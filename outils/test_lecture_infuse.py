"""Lecture, pause et reprise avec les vraies fonctions et des appareils simulés."""
import ast
import asyncio
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import json_atomique
import lecture_infuse

META = {"type": "movie", "id": 1, "titre": "Toy Story 5", "titre_original": "Toy Story 5", "annee": "2026", "duree": 102}
P = {"etat": "Playing", "titre": "Toy Story 5", "media": "Video", "total": 6120,
     "pos": 120, "app": lecture_infuse.INFUSE_ID, "veille": False}


def fonctions(fichier, noms, env):
    arbre = ast.parse((ROOT / fichier).read_text())
    nodes = [n for n in arbre.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in noms]
    for n in nodes:
        n.decorator_list = []
    exec(compile(ast.Module(body=nodes, type_ignores=[]), fichier, "exec"), env)
    return env


class Horloge:
    def __init__(self):
        self.t = 1000
    def time(self):
        return self.t
    def sleep(self, n):
        self.t += n


class CorrespondanceTests(unittest.TestCase):
    def test_lecture_et_pause_du_bon_titre(self):
        for etat in ("Playing", "Paused"):
            self.assertTrue(lecture_infuse.correspond(dict(P, etat=etat), META))

    def test_autre_titre_meme_duree_refuse(self):
        self.assertFalse(lecture_infuse.correspond(dict(P, titre="Autre film"), META))

    def test_autre_application_veille_idle_musique_refuses(self):
        for changement in ({"app": "com.google.youtube"}, {"veille": True}, {"etat": "Idle"}, {"media": "Music"}):
            self.assertFalse(lecture_infuse.correspond(dict(P, **changement), META))

    def test_duree_absente_ou_courte_ne_bloque_pas_le_bon_titre(self):
        for total in (0, 300, 7300):
            self.assertTrue(lecture_infuse.correspond(dict(P, total=total), META))
        self.assertFalse(lecture_infuse.correspond(dict(P, total=0, pos=0, etat="Paused"), META))

    def test_titre_original_accents_annee_qualite(self):
        m = dict(META, titre="L’Été", titre_original="Summer")
        for titre in ("L'ete (2026)", "Summer", "L’Été.2026.2160p.mkv"):
            self.assertTrue(lecture_infuse.correspond(dict(P, titre=titre), m), titre)
        self.assertFalse(lecture_infuse.correspond(dict(P, titre="Summer 2"), m))

    def test_serie_exige_le_bon_episode(self):
        m = {"type": "tv", "titre": "La Série", "saison": 2, "episode": 3}
        self.assertTrue(lecture_infuse.correspond(dict(P, titre="La Série S02E03 : Départ"), m))
        self.assertTrue(lecture_infuse.correspond(dict(P, titre="Départ", serie_nom="La Série", saison_n=2, episode_n=3), m))
        for titre in ("La Série", "La Série S02E04", "Autre Série S02E03"):
            self.assertFalse(lecture_infuse.correspond(dict(P, titre=titre), m))

    def test_cli_duree_et_position_independantes(self):
        for texte, total in (("Position: 12/6120s", 6120), ("Position: 12s\nTotal time: 6120", 6120), ("Position: 12s", 0)):
            p = lecture_infuse.depuis_cli("Media type: Video\nDevice state: Paused\nTitle: Toy Story 5\n" + texte)
            self.assertEqual(p["pos"], 12)
            self.assertEqual(p["total"], total)
            self.assertTrue(lecture_infuse.correspond(p, META))


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = Horloge()
        class Thread:
            def __init__(self, target, **kw):
                self.target = target
                self.args = kw.get("args", ())
            def start(self):
                self.target(*self.args)
        class JSONResponse:
            def __init__(self, content, status_code=200):
                self.content, self.status_code = content, status_code
        faux_atv = {"identifiant": "AA:BB:CC:DD:EE:FF", "companion": "c", "airplay": "a"}   # 2.6.106 : Apple TV configurée
        self.env = {"time": self.clock, "json": json, "json_atomique": json_atomique, "os": os, "re": re, "lecture_infuse": lecture_infuse,
                    "integrations": SimpleNamespace(appletv=lambda identifiant=None: faux_atv if identifiant in (None, faux_atv["identifiant"]) else None, env_seance=lambda e, identifiant=None: e),
                    "MEMOIRE_SEANCE": str(Path(self.tmp.name) / "memoire.json"), "VERROU_MEMOIRE": threading.RLock(),
                    "LANCEMENT": {"d": None}, "conflit_depart": Mock(return_value=False),
                    "notifications": SimpleNamespace(signaler=Mock()), "analyser": Mock(return_value={}), "threading": SimpleNamespace(Thread=Thread, Event=threading.Event),
                    "etat": {"preparation": False, "mode": "reel"}, "MSG_INFUSE": "Non confirmé", "JSONResponse": JSONResponse,
                    "charger": lambda: {"atv_etat_max_age_s": 6, "lancer_attente_s": 3, "lancement_confirmation_s": 120, "film_attente_s": 1},
                    "lire_playing": Mock(side_effect=lambda **kw: dict(P, lu_a=self.clock.t)),
                    "en_cours": Mock(return_value=False), "lignes_courantes": Mock(return_value=[]),
                    "erreurs": SimpleNamespace(message=Mock(), journaliser_action=Mock()),
                    "meta_par_id": Mock(return_value=dict(META)), "reveiller": Mock(),
                    "atv": Mock(return_value=(True, "")), "memoriser_recent": Mock(), "cache_film": {"t": 0},
                    "demarrer_seance": Mock(return_value={"ok": True}), "reponse": lambda x: x,
                    "NB_ESSAIS_INFUSE": 2, "MSG_INDEXATION": "Toujours pas confirmé (indexation)"}
        noms = {"ecrire_prive", "lire_memoire_seance", "memoriser_lancement", "oublier_lancement", "confirmer_lancement",
                "lancement_definir", "_lancement_echec", "_resoudre_lancement", "_film_toujours_en_lecture",
                "observer_fin_lancement", "lancement_courant", "lancement_reprendre", "url_infuse", "lancer", "lancement_fermer",
                "terminer_seance_normalement", "oublier_fin_seance", "marquer_fin_vue", "lecture_sans_identite_infuse", "_titre_absent",
                "fin_de_saison", "est_seance", "cle_media", "ecarter_lancement", "retirer_suspendue", "suspendre_seance",
                "restaurer_suspendue", "sauver_suspendues", "resume_suspendues", "suspens_plein", "reponse_suspens_plein",
                "rattacher_suspendue", "ouvrir_transition", "fermer_transition"}
        import etat_seance
        self.env.update(etat_seance=etat_seance, OBSERVATION_FIN={"d": None},
                        VERROU_DEPART=threading.RLock(), reattach_session=Mock(), save_session_scenario=Mock(),
                        relais=SimpleNamespace(arreter=Mock()), tuer_seance=Mock(),
                        FIN_SEANCE=str(Path(self.tmp.name) / "fin_seance.json"), deja_vu=Mock(return_value=True), suivi_vu=Mock(),
                        INFUSE_ID=lecture_infuse.INFUSE_ID, atvlive=SimpleNamespace(LIVE=SimpleNamespace(pause=Mock(), cibler=Mock())),
                        journaliser_rattachement=Mock(), journal_chaine=Mock(), resume_seance=Mock(),
                        SUSPENDUES={"l": []}, MAX_SUSPENDUES=3, TRANSITION={"cle": None, "t": 0}, SUSPENS_FICHIER=str(Path(self.tmp.name) / "seances_suspendues.json"),
                        seance_conservee=Mock(return_value={}))
        fonctions("app/main.py", noms, self.env)
        # FinFilm (etat_seance) mesure une absence en temps réel écoulé (point 90) : la même horloge de test que
        # les fonctions extraites, pour que les avances de self.clock comptent aussi pour lui.
        p = patch.object(etat_seance, "time", self.clock)
        p.start()
        self.addCleanup(p.stop)

    def lancer(self, origine="lire"):
        return self.env["lancement_definir"]("movie", 1, None, None, origine)

    def test_lecture_directe_pause_confirmee_sans_select(self):
        self.env["lire_playing"].side_effect = lambda **kw: dict(P, etat="Paused", lu_a=self.clock.t)
        self.assertTrue(self.env["lancer"]("movie", 1)["ok"])
        self.env["atv"].assert_called_once_with("launch_app=infuse://movie/1?play", delai=15)
        self.assertTrue(self.env["LANCEMENT"]["d"]["confirme"])

    def test_autre_film_ne_confirme_pas_et_echec_recuperable(self):
        self.env["lire_playing"].side_effect = lambda **kw: dict(P, titre="Autre film", lu_a=self.clock.t)
        self.assertFalse(self.env["lancer"]("movie", 1)["ok"])
        self.assertEqual(self.env["LANCEMENT"]["d"]["etat"], "echec")
        self.env["lire_playing"].side_effect = lambda **kw: dict(P, lu_a=self.clock.t)
        self.assertEqual(self.env["lancement_courant"]()["etat"], "ok")

    def test_echec_notifie_pour_une_seance_ou_une_reprise_pas_pour_un_lire_direct(self):
        # "lire" est un clic direct : l'échec est déjà visible tout de suite, pas d'intérêt à notifier.
        # "seance" et "reprise" peuvent se confirmer bien plus tard (bandes annonces, retour au salon).
        for origine, notifie in (("seance", True), ("reprise", True), ("lire", False)):
            self.env["notifications"].signaler.reset_mock()
            d = self.lancer(origine)
            self.env["_lancement_echec"](d, "Message court", cause="Cause technique")
            if notifie:
                self.env["notifications"].signaler.assert_called_once_with(
                    "seance:echec:" + str(d["t"]), "seance_echec", "Échec du lancement", META["titre"], {"page": "seance"})
            else:
                self.env["notifications"].signaler.assert_not_called()

    def test_echec_ne_notifie_qu_une_fois(self):
        d = self.lancer("seance")
        self.env["_lancement_echec"](d, "Message court")
        self.env["_lancement_echec"](d, "Message court")
        self.env["notifications"].signaler.assert_called_once()

    def test_lancer_retente_avant_d_echouer_media_jamais_indexe(self):
        # NB_ESSAIS_INFUSE=2 dans cet environnement de test (voir setUp) : deux envois avant l'échec.
        self.env["lire_playing"].side_effect = lambda **kw: dict(P, titre="Autre film", lu_a=self.clock.t)
        self.assertFalse(self.env["lancer"]("movie", 1)["ok"])
        self.assertEqual(self.env["atv"].call_count, 2)
        self.assertEqual(self.env["LANCEMENT"]["d"]["etat"], "echec")

    def test_lancer_indique_l_indexation_possible_puis_confirme_sans_echec(self):
        appels = {"n": 0}

        def lire(**kw):
            appels["n"] += 1
            d = self.env["LANCEMENT"]["d"]
            if appels["n"] == 1:
                self.assertNotEqual(d.get("phase"), self.env["MSG_INDEXATION"])
            confirme = appels["n"] > 4     # rate le premier essai (attente_lecture=3, une lecture par seconde)
            return dict(P, titre="" if not confirme else P["titre"], lu_a=self.clock.t)
        self.env["lire_playing"].side_effect = lire
        r = self.env["lancer"]("movie", 1)
        self.assertTrue(r["ok"])
        self.assertTrue(self.env["LANCEMENT"]["d"]["confirme"])
        # Lot 2.6.90 : Infuse lisait déjà (titre pas encore publié) : un seul envoi du lien, jamais un second par dessus.
        self.assertEqual(self.env["atv"].call_count, 1)
        evenements = [c.args[1].get("evenement") for c in self.env["journal_chaine"].call_args_list if c.args[0] == "lancement"]
        self.assertEqual(evenements, ["lien profond envoyé", "renvoi du lien évité"])

    def test_cache_ancien_ne_confirme_pas(self):
        d = self.lancer()
        for lu in (999, 900):
            self.assertFalse(self.env["_film_toujours_en_lecture"](d, dict(P, lu_a=lu)))
        self.clock.t += 30
        self.assertFalse(self.env["_film_toujours_en_lecture"](d, dict(P, lu_a=1001)))

    def test_preuve_live_prime_sur_erreur_du_journal(self):
        self.lancer("seance")
        self.env["lignes_courantes"].return_value = [(1000, "Infuse n'a pas démarré la lecture")]
        self.assertEqual(self.env["lancement_courant"]()["etat"], "ok")
        self.env["erreurs"].message.assert_not_called()

    def test_journal_exige_la_preuve_du_bon_media(self):
        d = self.lancer("seance")
        self.env["en_cours"].return_value = True
        self.env["lire_playing"].side_effect = lambda **kw: None
        self.env["lignes_courantes"].return_value = [(1000, "Lancement du film via Infuse : lien"), (1001, "  Playing | Autre film | 12/6120s")]
        self.assertEqual(self.env["lancement_courant"]()["etat"], "en_cours")
        self.env["lignes_courantes"].return_value.append((1002, "Lecture confirmée : movie:1:0:0"))
        self.assertEqual(self.env["lancement_courant"]()["etat"], "ok")
        self.assertTrue(d["confirme"])

    def test_seance_signale_l_indexation_avant_le_delai_de_confirmation(self):
        # film_attente_s=1 dans cet environnement : passé ce délai sans confirmation, ni échec (pas encore
        # lancement_confirmation_s=120), l'attente affiche l'indexation possible plutôt qu'un texte générique.
        d = self.lancer("seance")
        self.env["en_cours"].return_value = True
        self.env["lire_playing"].side_effect = lambda **kw: None
        self.env["lignes_courantes"].return_value = [(1000, "Lancement du film via Infuse : lien")]
        self.clock.t = 1000
        r = self.env["lancement_courant"]()
        self.assertEqual(r["etat"], "en_cours")
        self.assertNotEqual(r["phase"], self.env["MSG_INDEXATION"])
        self.clock.t = 1002
        r = self.env["lancement_courant"]()
        self.assertEqual(r["etat"], "en_cours")
        self.assertEqual(d["phase"], self.env["MSG_INDEXATION"])
        self.assertEqual(r["phase"], self.env["MSG_INDEXATION"])
        # Le journal confirme ensuite : l'indexation n'était qu'une attente, pas un échec.
        self.env["lire_playing"].side_effect = lambda **kw: dict(P, lu_a=self.clock.t)
        self.assertEqual(self.env["lancement_courant"]()["etat"], "ok")

    def test_preuve_d_un_ancien_lancement_ignoree(self):
        self.lancer("seance")
        self.env["en_cours"].return_value = True
        self.env["lire_playing"].side_effect = lambda **kw: None
        self.env["lignes_courantes"].return_value = [(999, "Lecture confirmée : movie:1:0:0"), (None, "Lecture confirmée : movie:1:0:0")]
        self.assertEqual(self.env["lancement_courant"]()["etat"], "en_cours")

    def test_pause_puis_sortie_conserve_et_restaure_la_seance(self):
        self.lancer("seance")
        self.env["lire_playing"].side_effect = lambda **kw: dict(P, etat="Paused", lu_a=self.clock.t)
        self.assertFalse(self.env["lancement_courant"]()["suspendue"])
        self.env["lire_playing"].side_effect = lambda **kw: dict(P, etat="Idle", lu_a=self.clock.t)
        self.clock.t += 2
        for _ in range(3):
            self.clock.t += 7
            s = self.env["lancement_courant"]()
        self.assertTrue(s["suspendue"])
        restauree = self.env["lire_memoire_seance"]()
        self.assertEqual(restauree["id"], 1)
        self.env["LANCEMENT"]["d"] = restauree
        self.assertTrue(self.env["lancement_courant"]()["suspendue"])

    def test_autre_film_ne_prend_pas_l_identite_memorisee(self):
        d = self.lancer()
        self.env["lancement_courant"]()
        p = dict(P, titre="Autre film", lu_a=self.clock.t)
        self.assertFalse(self.env["_film_toujours_en_lecture"](d, p))

    def test_entracte_ou_bandes_annonces_ne_suspendent_pas_la_seance(self):
        self.lancer("seance")
        self.env["lancement_courant"]()
        self.env["en_cours"].return_value = True
        self.env["lire_playing"].side_effect = lambda **kw: dict(P, app="youtube", lu_a=self.clock.t)
        self.assertFalse(self.env["lancement_courant"]()["suspendue"])

    def test_fermer_efface_la_memoire_et_observation_obsolete_ne_la_recree_pas(self):
        d = self.lancer()
        self.env["lancement_courant"]()
        self.env["lancement_fermer"]()
        self.env["confirmer_lancement"](d, P)
        self.assertIsNone(self.env["lire_memoire_seance"]())
        self.assertIsNone(self.env["LANCEMENT"]["d"])

    def test_ancienne_observation_ne_peut_pas_effacer_un_nouveau_lancement(self):
        d = self.lancer()
        autre = self.lancer()
        self.env["oublier_lancement"](d)
        self.assertIs(self.env["LANCEMENT"]["d"], autre)

    def test_fin_observee_libere_la_memoire(self):
        self.lancer()
        self.env["lire_playing"].side_effect = lambda **kw: dict(P, pos=6110, lu_a=self.clock.t)
        self.env["lancement_courant"]()
        self.env["lire_playing"].side_effect = lambda **kw: dict(P, etat="Idle", lu_a=self.clock.t)
        for _ in range(3):
            self.clock.t += 7
            resultat = self.env["lancement_courant"]()
        self.assertIsNone(resultat)

    def test_reprise_seance_sans_bandes_annonces_et_mode_conserve(self):
        d = self.lancer("seance")
        d.update(mode="test", relance={"entracte": "0"})
        self.env["lancement_courant"]()
        self.assertTrue(self.env["lancement_reprendre"](d["t"])["ok"])
        self.env["demarrer_seance"].assert_not_called()
        self.assertIs(self.env["LANCEMENT"]["d"], d)
        self.assertEqual(d["mode"], "test")
        self.env["atv"].assert_called_once_with("launch_app=infuse://movie/1?play", delai=25)

    def test_reprise_directe_reste_directe_et_jeton_ancien_refuse(self):
        d = self.lancer()
        self.env["lancement_courant"]()
        self.env["lancer"] = Mock(return_value={"ok": True})
        self.assertEqual(self.env["lancement_reprendre"](999).status_code, 409)
        self.assertTrue(self.env["lancement_reprendre"](d["t"])["ok"])
        self.env["lancer"].assert_called_once_with("movie", 1, 0, 0)
        self.env["en_cours"].return_value = True
        self.assertEqual(self.env["lancement_reprendre"](d["t"]).status_code, 409)

    def test_reprise_refuse_un_autre_contenu_en_lecture(self):
        d = self.lancer("seance")
        self.env["lancement_courant"]()
        self.env["lire_playing"].side_effect = lambda **kw: dict(P, titre="Autre film", lu_a=self.clock.t)
        self.assertEqual(self.env["lancement_reprendre"](d["t"]).status_code, 409)
        self.env["demarrer_seance"].assert_not_called()

    def test_demarrage_transmet_la_cible_et_garde_le_titre_associe(self):
        self.env.update({"BASE": self.tmp.name, "chemins": SimpleNamespace(code=lambda *p: str(Path(self.tmp.name, *p))), "LOG": str(Path(self.tmp.name) / "seance.log"),
                         "mode_effectif": lambda m: m or "reel", "resoudre_type": lambda t: "film",
                         "film_choisi": lambda: dict(META), "lire_option": lambda x: x,
                         "options_seance": lambda *a: {"ba": False, "entracte": True},
                         "BA_LANCEE": {"d": None}, "suivi": SimpleNamespace(incrementer_seances=Mock()),
                         "VERROU_PREPARATION": threading.Lock(),
                         "subprocess": SimpleNamespace(Popen=Mock(return_value=Mock()), PIPE=-1, STDOUT=-2),
                         "lecteur": lambda p, f: f.close(), "ligne": Mock()})
        self.env["reveiller"].return_value = False
        fonctions("app/main.py", {"demarrer_seance"}, self.env)
        self.assertTrue(self.env["demarrer_seance"]("test", tmdb_type="movie", tmdb_id=1)["ok"])
        envoi = self.env["subprocess"].Popen.call_args.kwargs["env"]
        self.assertEqual(json.loads(envoi["SEANCE_META"])["titre"], META["titre"])
        self.assertEqual(envoi["SEANCE_INFUSE_URL"], "infuse://movie/1?play")
        self.assertEqual(self.env["LANCEMENT"]["d"]["mode"], "test")
        lignes = [appel.args[0] for appel in self.env["ligne"].call_args_list]
        self.assertEqual([x for x in lignes if x.startswith("La séance démarre dans")],
                         ["La séance démarre dans 3…", "La séance démarre dans 2…", "La séance démarre dans 1…"])
        self.assertTrue(self.env["demarrer_seance"]()["ok"])
        self.assertEqual(self.env["LANCEMENT"]["d"]["id"], 1)
        self.assertNotIn("SEANCE_INFUSE_URL", self.env["subprocess"].Popen.call_args.kwargs["env"])

    def test_erreur_tardive_ne_remplace_pas_la_confirmation(self):
        d = self.lancer()
        self.env["lancement_courant"]()
        self.env["_lancement_echec"](d, "Ancienne erreur")
        self.assertEqual(d["etat"], "ok")

    def test_fichier_memoire_invalide_ignore(self):
        for contenu in ("{", 'null', '{"confirme":true}'):
            Path(self.env["MEMOIRE_SEANCE"]).write_text(contenu)
            self.assertIsNone(self.env["lire_memoire_seance"]())


class ConflitDepartTests(unittest.TestCase):
    """Points 77 et 78 : un remplacement confirmé (Lancer quand même, lecture ailleurs) ne doit pas être rebloqué par
    le même conflit ensuite, et un état YouTube trop ancien pour être encore affiché quelque part dans l'app (même
    limite que contenu_ailleurs, la Home) ne doit plus bloquer un lancement du tout, même sans confirmation explicite.
    Une vraie séance ou un autre lancement confirmé doivent rester protégés dans tous les cas."""
    def setUp(self):
        import etat_seance
        self.env = {"LANCEMENT": {"d": None}, "en_cours": Mock(return_value=False), "time": time,
                    "lire_playing": Mock(return_value=None), "lecture_infuse": lecture_infuse,
                    "etat_seance": etat_seance, "INFUSE_ID": lecture_infuse.INFUSE_ID,
                    "SUPPRIMER_AILLEURS": {"jusqua": 0},
                    "charger": lambda: {"atv_etat_max_age_s": 999, "atv_ailleurs_pause_max_s": 120},
                    "atvlive": SimpleNamespace(LIVE=SimpleNamespace(jaquette=None), lien_youtube=lambda p: None),
                    "NOMS_APPS": {}}
        fonctions("app/main.py", {"conflit_depart", "contenu_ailleurs", "est_seance"}, self.env)

    def conflit(self, ignorer_ailleurs=False):
        return self.env["conflit_depart"]("movie", 1, 0, 0, ignorer_ailleurs)

    def test_vraie_seance_bloque_meme_avec_ignorer_ailleurs(self):
        self.env["en_cours"].return_value = True
        self.assertTrue(self.conflit(ignorer_ailleurs=True))

    def test_autre_lancement_confirme_bloque_meme_avec_ignorer_ailleurs(self):
        self.env["LANCEMENT"]["d"] = {"confirme": True, "id": 2, "type": "movie"}
        self.assertTrue(self.conflit(ignorer_ailleurs=True))

    def test_seance_en_suspens_qui_ne_joue_plus_ne_bloque_plus(self):
        # Lot 2.6.91 : une vraie séance arrêtée partira en suspens au départ suivant, elle ne le bloque plus.
        self.env["LANCEMENT"]["d"] = {"confirme": True, "id": 2, "type": "movie", "origine": "reprise", "meta": {"titre": "Avatar"}}
        self.assertFalse(self.conflit())

    def test_lecture_ailleurs_bloque_sans_confirmation(self):
        self.env["lire_playing"].return_value = dict(P, app="com.google.ios.youtube", titre="Vidéo en cours", lu_a=time.time())
        self.assertTrue(self.conflit(ignorer_ailleurs=False))

    def test_lecture_ailleurs_en_pause_bloque_toujours_sans_confirmation(self):
        # Après /stop, l'ailleurs n'est mis qu'en pause : media_reel() accepte Playing et Paused, donc le conflit
        # revient identique si rien ne dit explicitement que ce remplacement a déjà été confirmé.
        self.env["lire_playing"].return_value = dict(P, app="com.google.ios.youtube", titre="Vidéo en cours", lu_a=time.time(), etat="Paused", depuis=5)
        self.assertTrue(self.conflit(ignorer_ailleurs=False))

    def test_lecture_ailleurs_confirmee_ne_bloque_plus(self):
        self.env["lire_playing"].return_value = dict(P, app="com.google.ios.youtube", titre="Vidéo en cours", lu_a=time.time(), etat="Paused", depuis=5)
        self.assertFalse(self.conflit(ignorer_ailleurs=True))

    def test_infuse_reste_protege_meme_avec_ignorer_ailleurs(self):
        # ignorer_ailleurs ne lève le conflit que pour une autre application ; une lecture Infuse non encore
        # associée à LANCEMENT reste bloquante, la confirmation ne portait pas sur ça.
        self.env["lire_playing"].return_value = dict(P, app=lecture_infuse.INFUSE_ID)
        self.assertTrue(self.conflit(ignorer_ailleurs=True))

    def test_rien_en_lecture_ne_bloque_pas(self):
        self.assertFalse(self.conflit(ignorer_ailleurs=False))

    def test_sequence_playing_puis_paused_puis_accueil_tvos_puis_lancement(self):
        # Scénario demandé (point 78) : YouTube Playing, puis Paused (retour à l'accueil tvOS sans fermer l'app,
        # pyatv ne le distingue pas d'une vraie pause), la Home affiche d'abord la carte "ailleurs" puis plus rien
        # une fois l'âge dépassé ; un lancement de séance doit alors être autorisé, sans aucun média actif détecté.
        self.env["lire_playing"].return_value = dict(P, app="com.google.ios.youtube", titre="Vidéo en cours", etat="Playing", lu_a=time.time())
        self.assertIsNotNone(self.env["contenu_ailleurs"](self.env["lire_playing"].return_value))
        self.assertTrue(self.conflit(ignorer_ailleurs=False), "YouTube en lecture doit bloquer un lancement sans confirmation")
        self.env["lire_playing"].return_value = dict(P, app="com.google.ios.youtube", titre="Vidéo en cours", etat="Paused", depuis=30, lu_a=time.time())
        self.assertIsNotNone(self.env["contenu_ailleurs"](self.env["lire_playing"].return_value), "encore récent : la Home doit encore le montrer")
        self.assertTrue(self.conflit(ignorer_ailleurs=False))
        self.env["lire_playing"].return_value = dict(P, app="com.google.ios.youtube", titre="Vidéo en cours", etat="Paused", depuis=121, lu_a=time.time())
        self.assertIsNone(self.env["contenu_ailleurs"](self.env["lire_playing"].return_value), "Rien en lecture actuellement, comme sur la Home")
        self.assertFalse(self.conflit(ignorer_ailleurs=False), "aucun média actif : le lancement doit être autorisé sans confirmation")

    def test_youtube_pause_trop_ancienne_ne_bloque_plus_meme_sans_confirmation(self):
        # Point 78 : YouTube Playing puis Paused puis retour à l'accueil tvOS sans fermer l'app, pyatv ne dit rien
        # de fiable là dessus (metadata.app reste le dernier lecteur, pas l'app au premier plan). Passé le même
        # délai que celui qui fait déjà disparaître la carte "ailleurs" de la Home (contenu_ailleurs), un lancement
        # ne doit plus être bloqué du tout, même sans "Lancer quand même" : la Home ne montre déjà plus rien.
        self.env["lire_playing"].return_value = dict(P, app="com.google.ios.youtube", titre="Vidéo en cours", lu_a=time.time(), etat="Paused", depuis=121)
        self.assertFalse(self.conflit(ignorer_ailleurs=False))
        self.assertIsNone(self.env["contenu_ailleurs"](self.env["lire_playing"].return_value))

    def test_lancement_autorise_sans_second_faux_conflit_apres_confirmation(self):
        # Lecture externe détectée (encore récente) -> Lancer quand même (ignorer_ailleurs=True) -> le lancement qui
        # suit ne doit pas retomber sur un second faux conflit tant que rien de nouveau ne s'est produit.
        self.env["lire_playing"].return_value = dict(P, app="com.google.ios.youtube", titre="Vidéo en cours", lu_a=time.time(), etat="Paused", depuis=5)
        self.assertTrue(self.conflit(ignorer_ailleurs=False), "Le conflit initial doit bien être détecté")
        self.assertFalse(self.conflit(ignorer_ailleurs=True), "Lancer quand même doit lever ce même conflit")
        self.assertFalse(self.conflit(ignorer_ailleurs=True), "Un second appel ne doit pas faire réapparaître le conflit")


class AtvLiveComboTests(unittest.TestCase):
    """Point 78 : atvlive.Live date depuis quand la lecture ailleurs (app, état, titre, position, durée) n'a pas
    changé, pour distinguer une vraie pause prolongée d'un retour silencieux à l'accueil de l'Apple TV. Une
    reconnexion (Wifi, veille de l'Apple TV, redémarrage du conteneur) ne doit pas remettre cette date à zéro
    quand rien n'a réellement changé, sinon atv_ailleurs_pause_max_s ne se déclenche jamais si des reconnexions
    se répètent, et un état déjà affiché comme "Rien en lecture actuellement" peut réapparaître sans raison."""
    def test_combo_persiste_a_travers_deux_reconnexions(self):
        import atvlive
        live = atvlive.Live()
        pause_youtube = {"app": "com.google.ios.youtube", "etat": "Paused", "titre": "Vidéo en cours", "pos": 5, "total": 180}
        appels = SimpleNamespace(n=0)
        combo_t_observes = []

        async def fausse_image(atv):
            appels.n += 1
            combo_t_observes.append(live._combo_t)
            if appels.n == 3:
                raise asyncio.CancelledError()
            raise RuntimeError("coupure simulée %d" % appels.n)   # force une reconnexion (except Exception, puis nouvelle boucle)

        live._lire = AsyncMock(return_value=pause_youtube)
        live._image = fausse_image
        conf = SimpleNamespace(set_credentials=Mock())
        pyatv_mod = types.ModuleType("pyatv")
        pyatv_mod.scan = AsyncMock(return_value=[conf])
        pyatv_mod.connect = AsyncMock(return_value=SimpleNamespace(close=Mock(return_value=[])))
        const_mod = types.ModuleType("pyatv.const")
        const_mod.Protocol = SimpleNamespace(Companion="Companion", AirPlay="AirPlay")
        pyatv_mod.const = const_mod

        import integrations   # 2.6.106 : l'Apple TV vient de la configuration de l'installation
        faux = {"identifiant": "AA:BB:CC:DD:EE:FF", "companion": "c", "airplay": "a"}
        with patch.dict(sys.modules, {"pyatv": pyatv_mod, "pyatv.const": const_mod}), patch("asyncio.sleep", AsyncMock()), \
                patch.object(integrations, "appletv", return_value=faux):
            try:
                asyncio.run(live._boucle())
            except asyncio.CancelledError:
                pass

        self.assertEqual(appels.n, 3, "trois publications attendues, deux reconnexions entre elles")
        self.assertEqual(combo_t_observes[0], combo_t_observes[1])
        self.assertEqual(combo_t_observes[1], combo_t_observes[2])


class ConnexionScriptTests(unittest.TestCase):
    def test_metadata_pyatv_reutilisee(self):
        arbre = ast.parse((ROOT / "seance.py").read_text())
        classe = next(n for n in arbre.body if isinstance(n, ast.ClassDef) and n.name == "Liaison")
        fonction = next(n for n in classe.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "_lecture")
        env = {"asyncio": asyncio, "ATV_DELAI": 15}
        exec(compile(ast.Module(body=[fonction], type_ignores=[]), "seance.py", "exec"), env)
        p = SimpleNamespace(device_state=SimpleNamespace(name="Paused"), title=META["titre"],
                            media_type=SimpleNamespace(name="Video"), position=100, total_time=6120,
                            series_name=None, season_number=None, episode_number=None)
        async def playing():
            return p
        tv = SimpleNamespace(metadata=SimpleNamespace(playing=playing, app=SimpleNamespace(identifier=lecture_infuse.INFUSE_ID)),
                             power=SimpleNamespace(power_state=SimpleNamespace(name="On")))
        resultat = asyncio.run(env["_lecture"](SimpleNamespace(tv=tv)))
        self.assertTrue(lecture_infuse.correspond(resultat, META))
        self.assertEqual(resultat["etat"], "Paused")

    def test_connexion_continue_evite_un_processus_cli(self):
        liaison = SimpleNamespace(tv=object(), _lecture=Mock(return_value="lecture"), _run=Mock(return_value=P))
        env = {"liaison": liaison, "ATV_DELAI": 15, "atv": Mock(), "lecture_infuse": lecture_infuse}
        fonctions("seance.py", {"lecture_observee"}, env)
        self.assertEqual(env["lecture_observee"](), P)
        env["atv"].assert_not_called()

    def test_repli_cli_si_connexion_continue_echoue(self):
        liaison = SimpleNamespace(tv=object(), _lecture=Mock(return_value="lecture"), _run=Mock(side_effect=TimeoutError))
        env = {"liaison": liaison, "ATV_DELAI": 15, "atv": Mock(return_value="Device state: Playing\nMedia type: Video\nTitle: Toy Story 5\nPosition: 20/6120s"), "lecture_infuse": lecture_infuse}
        fonctions("seance.py", {"lecture_observee"}, env)
        self.assertTrue(lecture_infuse.correspond(env["lecture_observee"](), META))
        env["atv"].assert_called_once_with("playing", "app", "power_state")


class ScriptSeanceTests(unittest.TestCase):
    def setUp(self):
        self.clock = Horloge()
        self.env = {"time": self.clock, "os": os, "json": json, "lecture_infuse": lecture_infuse,
                    "FILM_ATTENTE": 2, "NB_ESSAIS_INFUSE": 2, "enchainer": Mock(), "lecture_observee": Mock(return_value=dict(P, etat="Paused")),
                    "lumiere": Mock(), "liaison": SimpleNamespace(fermer=Mock()), "norm": lecture_infuse.normaliser,
                    "atv": Mock(), "playing": Mock(return_value=("Paused", P["titre"], 100, 6120))}
        fonctions("seance.py", {"demarrer_contenu", "attendre_lecture", "attendre_confirmation"}, self.env)
        self.vars = patch.dict(os.environ, SEANCE_META=json.dumps(META), SEANCE_INFUSE_URL="infuse://movie/1?play",
                               SEANCE_TMDB_TYPE="movie", SEANCE_TMDB_ID="1", SEANCE_SAISON="0", SEANCE_EPISODE="0")
        self.vars.start()
        self.addCleanup(self.vars.stop)

    def test_pause_confirmee_sans_commande_supplementaire(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertTrue(self.env["demarrer_contenu"]())
        self.assertIn("Lecture confirmée : movie:1:0:0", out.getvalue())
        self.env["atv"].assert_not_called()

    def test_autre_film_ne_confirme_pas_le_script(self):
        self.env["lecture_observee"].return_value = dict(P, titre="Autre film")
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            self.env["demarrer_contenu"]()
        self.env["atv"].assert_not_called()

    def test_pause_initiale_respectee(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.env["attendre_lecture"]([], respecter_pause=True), 100)
        self.env["atv"].assert_not_called()

    def test_retour_entracte_garde_la_reprise_explicite(self):
        self.env["atv"].side_effect = lambda *a: self.env["playing"].configure_mock(return_value=("Playing", P["titre"], 100, 6120))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.env["attendre_lecture"]([]), 100)
        self.env["atv"].assert_called_once_with("play")

    def test_confirmation_reussit_des_que_la_lecture_reprend_vraiment(self):
        # Une reprise un peu lente après les bandes annonces : le bon film reste en pause un instant, une
        # relance suffit à le faire vraiment repartir.
        self.env["lecture_observee"].side_effect = [dict(P, etat="Paused"), dict(P, etat="Playing")]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(self.env["attendre_confirmation"](META))
        self.env["atv"].assert_called_once_with("play")

    def test_confirmation_relance_tant_que_la_pause_persiste(self):
        self.env["lecture_observee"].side_effect = [dict(P, etat="Paused"), dict(P, etat="Paused"), dict(P, etat="Playing")]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(self.env["attendre_confirmation"](META))
        self.assertEqual(self.env["atv"].call_count, 2)

    def test_confirmation_une_pause_reconnue_ne_suffit_pas_seule(self):
        # Contrairement à demarrer_contenu() (position posée à titre indicatif avant les bandes annonces), ici le
        # film est censé avoir repris : une pause qui ne redevient jamais Playing doit rester un échec, pas une
        # fausse confirmation qui laisserait la séance bloquée en silence dans la suite (entracte, générique).
        self.env["lecture_observee"].return_value = dict(P, etat="Paused")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(self.env["attendre_confirmation"](META, delai=3))
        self.assertGreaterEqual(self.env["atv"].call_count, 1)

    def test_confirmation_echoue_si_jamais_le_bon_film(self):
        self.env["lecture_observee"].return_value = dict(P, titre="Autre film")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(self.env["attendre_confirmation"](META, delai=3))
        self.env["atv"].assert_not_called()


class FaireEntracteTests(unittest.TestCase):
    """Point 90 : une avance manuelle qui dépasse d'un coup l'entracte prévue doit l'annuler, pas la déclencher en
    retard une fois remarquée ; un simple recul ou une petite avance qui reste avant l'entracte ne doit rien changer."""
    def setUp(self):
        import etat_seance
        self.clock = Horloge()
        self.env = {"time": self.clock, "os": os, "json": json, "random": __import__("random"),
                    "FinFilm": etat_seance.FinFilm, "MODE_TEST": True, "DELAI_TEST": 20, "ENTRE": (0.5, 0.6),
                    "lecture_observee": Mock(), "lumiere": Mock(), "liaison": SimpleNamespace(reconnecter=Mock()),
                    "lancer_video": Mock(return_value=True), "ENTRACTE_ID": "ga_66cp4Jrc", "ANNONCE_ID": None,
                    "enchainer": Mock(), "etapes_retour": Mock(return_value=[]), "attendre_lecture": Mock(return_value=5),
                    "atv": Mock(), "SAUT_MANUEL_S": 5}
        fonctions("seance.py", {"faire_entracte"}, self.env)
        self.vars = patch.dict(os.environ, SEANCE_META=json.dumps(META))
        self.vars.start()
        self.addCleanup(self.vars.stop)

    def test_progression_normale_declenche_l_entracte_prevue(self):
        self.env["lecture_observee"].side_effect = [dict(P, etat="Playing", pos=p) for p in range(102, 122, 2)]
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.env["faire_entracte"](META["titre"], 100, 6120)
        self.env["lancer_video"].assert_called_once()
        self.assertNotIn("annulée", out.getvalue())

    def test_petit_seek_avant_l_entracte_la_conserve(self):
        # Un recul ou une petite avance avec la télécommande (dans la timeline, avant le point prévu) ne doit rien
        # changer : l'entracte reste prévue au même point, elle se déclenche normalement une fois atteint.
        self.env["lecture_observee"].side_effect = [dict(P, etat="Playing", pos=102), dict(P, etat="Playing", pos=95)] + \
            [dict(P, etat="Playing", pos=p) for p in range(105, 122, 2)]
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.env["faire_entracte"](META["titre"], 100, 6120)
        self.env["lancer_video"].assert_called_once()
        self.assertNotIn("annulée", out.getvalue())

    def test_avance_manuelle_au_dela_annule_l_entracte_une_seule_fois(self):
        self.env["lecture_observee"].side_effect = [dict(P, etat="Playing", pos=500)]
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.env["faire_entracte"](META["titre"], 100, 6120)
        self.env["lancer_video"].assert_not_called()
        self.assertIn("Avance manuelle", out.getvalue())

    def test_seek_court_au_dela_annule_l_entracte(self):
        # Seek Apple TV courant d'environ 10 s détecté au sondage suivant.
        self.env["lecture_observee"].side_effect = [dict(P, etat="Playing", pos=118), dict(P, etat="Playing", pos=128)]
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.env["faire_entracte"](META["titre"], 100, 6120)
        self.env["lancer_video"].assert_not_called()
        self.assertIn("Avance manuelle", out.getvalue())

    def test_seek_pendant_pause_au_dela_annule_l_entracte(self):
        self.env["lecture_observee"].side_effect = [dict(P, etat="Paused", pos=100),
                                                     dict(P, etat="Paused", pos=130)]
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.env["faire_entracte"](META["titre"], 100, 6120)
        self.env["lancer_video"].assert_not_called()
        self.assertIn("Avance manuelle", out.getvalue())

    def test_pause_prolongee_puis_reprise_avant_l_entracte_ne_l_annule_pas(self):
        # Une pause avec la télécommande, même longue, suivie d'une reprise à la même position : aucun vrai saut,
        # l'entracte reste prévue (le temps réel écoulé pendant la pause ne doit pas être compté comme une avance).
        self.env["lecture_observee"].side_effect = ([dict(P, etat="Paused", pos=102)] * 5
                                                     + [dict(P, etat="Playing", pos=102)]
                                                     + [dict(P, etat="Playing", pos=p) for p in range(104, 122, 2)])
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.env["faire_entracte"](META["titre"], 100, 6120)
        self.env["lancer_video"].assert_called_once()
        self.assertNotIn("annulée", out.getvalue())


REEL_S1E4 = "Reacher - S1 \u2219 E4 - Dommages collatéraux"     # chaîne exacte relevée par /appletv/rattachement (2.6.84)


class FormatInfuseReel(unittest.TestCase):
    """Lot 2.6.85 : le format réellement publié par Infuse, « Série - S1 ∙ E4 - Titre de l'épisode »."""
    SEANCE_S1E3 = {"type": "tv", "id": 108978, "saison": 1, "episode": 3,
                   "meta": {"type": "tv", "id": 108978, "titre": "Reacher", "annee": "2022"}}
    BASE = {"etat": "Playing", "app": lecture_infuse.INFUSE_ID, "media": "Video", "pos": 900, "total": 2716}

    def test_chaine_reelle_analysee_en_episode(self):
        info = lecture_infuse.identite_observee({"titre": REEL_S1E4, "serie_nom": "", "saison_n": 0, "episode_n": 0})
        self.assertEqual((info["nom"], info["saison"], info["episode"], info["type"]), ("Reacher", 1, 4, "tv"))
        self.assertEqual(info["ep_titre"], "Dommages collatéraux")
        self.assertTrue(info["episode_certain"])

    def test_variantes_sures_acceptees_et_autres_refusees(self):
        for titre in ("Reacher - S1 \u2219 E4 - X", "Reacher - S01 \u2219 E04 - X", "Reacher - S1 \u00b7 E4 - X",
                      "Reacher - S1 \u2022 E4 - X", "Reacher - S1 \u22c5 E4 - X", "Reacher - S1 E4 - X", "Reacher - S1\u2219E4"):
            info = lecture_infuse.identite_observee({"titre": titre})
            self.assertEqual((info["type"], info["nom"], info["saison"], info["episode"]), ("tv", "Reacher", 1, 4), titre)
        for titre in ("Reacher - S1 x E4 - X", "Reacher - S1 / E4 - X", "Reacher - S1 ∙ F4 - X"):
            self.assertIsNone(lecture_infuse.identite_observee({"titre": titre})["type"], titre)

    def test_s1e4_refuse_pour_une_seance_s1e3(self):
        ok, raison = lecture_infuse.explique_correspondance(dict(self.BASE, titre=REEL_S1E4), self.SEANCE_S1E3)
        self.assertFalse(ok)
        self.assertEqual(raison, "autre épisode publié : S1E4")
        # Même à durée égale : l'épisode publié explicitement prime sur le fallback « même fichier ».
        self.assertFalse(lecture_infuse.correspond(dict(self.BASE, titre=REEL_S1E4), dict(self.SEANCE_S1E3, total_fichier=2716)))

    def test_meme_chaine_en_s1e3_rattachee_tout_de_suite(self):
        titre = REEL_S1E4.replace("E4", "E3")
        ok, raison = lecture_infuse.explique_correspondance(dict(self.BASE, titre=titre), self.SEANCE_S1E3)
        self.assertTrue(ok)
        self.assertEqual(raison, "titre ou champs pyatv : même épisode")


class IdentiteUnique(unittest.TestCase):
    """Lot 2.6.83 : une seule analyse de l'identité, partagée par /film, le rattachement et les contrôleurs."""
    def test_formats_de_titre_et_champs_pyatv(self):
        cas = {"Reacher S01E03": ("tv", "Reacher", 1, 3), "Reacher - S1E3 - Cuillère en argent": ("tv", "Reacher", 1, 3),
               "Reacher.S01.E03": ("tv", "Reacher", 1, 3), "S.W.A.T. S02E05": ("tv", "S.W.A.T", 2, 5),
               "Barbare": (None, "Barbare", None, None), "Mission: Impossible 2": (None, "Mission: Impossible 2", None, None)}
        for titre, attendu in cas.items():
            info = lecture_infuse.identite_observee({"titre": titre})
            self.assertEqual((info["type"], info["nom"], info["saison"], info["episode"]), attendu, titre)
        info = lecture_infuse.identite_observee({"titre": "Cuillère en argent", "serie_nom": "Reacher", "saison_n": 1, "episode_n": 3})
        self.assertEqual((info["type"], info["episode_certain"], info["ep_titre"]), ("tv", True, "Cuillère en argent"))
        self.assertFalse(lecture_infuse.identite_observee({"titre": "Pilote", "serie_nom": "Reacher"})["episode_certain"])

    def test_identite_tmdb_rattache_le_meme_episode_seulement(self):
        cible = {"type": "tv", "id": 7, "saison": 1, "episode": 3, "meta": {"type": "tv", "id": 7, "titre": "Reacher"}}
        base = {"titre": "Cuillère en argent", "etat": "Playing", "app": lecture_infuse.INFUSE_ID, "media": "Video", "pos": 5}
        self.assertTrue(lecture_infuse.correspond(dict(base, identite={"type": "tv", "id": 7, "saison": 1, "episode": 3}), cible))
        self.assertFalse(lecture_infuse.correspond(dict(base, identite={"type": "tv", "id": 7, "saison": 1, "episode": 4}), cible))
        self.assertFalse(lecture_infuse.correspond(dict(base, identite={"type": "tv", "id": 8, "saison": 1, "episode": 3}), cible))
        self.assertFalse(lecture_infuse.correspond(base, cible), "Titre d'épisode seul : aucune preuve")

    def test_episode_publie_comme_tv_et_film_distinct_d_un_episode(self):
        film = {"type": "movie", "id": 1, "meta": {"type": "movie", "id": 1, "titre": "Reacher"}}
        base = {"etat": "Playing", "app": lecture_infuse.INFUSE_ID, "pos": 5}
        self.assertTrue(lecture_infuse.correspond(dict(base, titre="Reacher", media="Video"), film))
        self.assertFalse(lecture_infuse.correspond(dict(base, titre="Reacher S01E03", media="Video"), film), "Un épisode n'est jamais le film")
        serie = {"type": "tv", "id": 7, "saison": 1, "episode": 3, "meta": {"type": "tv", "id": 7, "titre": "Reacher"}}
        self.assertTrue(lecture_infuse.correspond(dict(base, titre="Reacher S01E03", media="TV"), serie))


if __name__ == "__main__":
    unittest.main()
