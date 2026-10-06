"""Transitions réelles des contrôleurs, sans commande matérielle ni réseau."""
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import etat_seance
import lecture_infuse

META = {"type": "movie", "id": 1, "titre": "Film de test", "annee": "2026"}
P = {"titre": META["titre"], "etat": "Playing", "app": "com.firecore.infuse", "media": "Video", "pos": 100, "total": 7200}


def types_simple(**k):
    import types
    return types.SimpleNamespace(**k)


class Horloge:
    """Remplace etat_seance.time pour contrôler ABSENCE_TOLERANCE_S sans attendre en réel (point 90). pas=0 :
    contrôle explicite par avancer() ; pas>0 : avance seule à chaque lecture, pour une boucle réelle (relais.py,
    seance.py) qui ne donne pas la main entre deux observations."""
    def __init__(self, debut=1000.0, pas=0.0):
        self.t = debut
        self.pas = pas

    def time(self):
        v = self.t
        self.t += self.pas
        return v

    def sleep(self, n):
        """Pour remplacer le module time en entier (relais.py appelle time.sleep) : avance sans attendre en réel."""
        self.avancer(n)

    def avancer(self, n):
        self.t += n


class Transitions(unittest.TestCase):
    def test_lecture_pause_reprise_generique_fin(self):
        horloge = Horloge()
        with patch.object(etat_seance, "time", horloge):
            suivi = etat_seance.FinFilm(META)
            for p, attendu in [(P, "lecture"), (dict(P, etat="Paused"), "pause"),
                               (None, "inconnu"), (dict(P, pos=7000), "lecture"),
                               (dict(P, pos=7192), "lecture")]:
                self.assertEqual(suivi.observer(p), attendu)
            self.assertEqual(suivi.observer({"etat": "Idle"}), "inconnu")
            horloge.avancer(etat_seance.ABSENCE_TOLERANCE_S + 1)
            self.assertEqual(suivi.observer({"etat": "Idle"}), "terminee")
            # Infuse peut aussi publier sa dernière position dans son état arrêté.
            self.assertEqual(etat_seance.FinFilm(META).observer(dict(P, etat="Stopped", pos=7190)), "terminee")
            # Quitter pendant le générique, plus de quinze secondes avant la fin, garde la séance.
            suivi = etat_seance.FinFilm(META)
            self.assertEqual(suivi.observer(dict(P, pos=7000)), "lecture")
            self.assertEqual(suivi.observer({"etat": "Idle"}), "inconnu")
            horloge.avancer(etat_seance.ABSENCE_TOLERANCE_S + 1)
            self.assertEqual(suivi.observer({"etat": "Idle"}), "suspendue")
            self.assertNotEqual(etat_seance.FinFilm(META).observer(dict(P, etat="Stopped", pos=7000)), "terminee")

    # ---------- Lot 2.6.82 : preuve forte d'une autre lecture ----------
    YT = {"app": "com.google.ios.youtube", "titre": "Vidéo", "etat": "Playing", "media": "Video", "pos": 50}

    def test_autre_app_qui_progresse_interrompt_tout_de_suite(self):
        horloge = Horloge()
        with patch.object(etat_seance, "time", horloge):
            suivi = etat_seance.FinFilm(META)
            self.assertEqual(suivi.observer(P), "lecture")
            horloge.avancer(etat_seance.GARDE_APRES_YOUTUBE_KAMCINE_S + 1)
            self.assertEqual(suivi.observer(dict(self.YT, progression=True, depuis=2)), "suspendue",
                             "Pas besoin d'attendre ABSENCE_TOLERANCE_S")

    def test_vieil_etat_youtube_sans_progression_ne_compte_pas(self):
        horloge = Horloge()
        with patch.object(etat_seance, "time", horloge):
            suivi = etat_seance.FinFilm(META)
            suivi.observer(P)
            horloge.avancer(etat_seance.GARDE_APRES_YOUTUBE_KAMCINE_S + 1)
            for variante in ({"progression": False, "depuis": 1}, {"progression": True, "depuis": 60},
                             {"progression": True, "depuis": 1, "stale": True}, {"progression": True, "depuis": 1, "etat": "Paused"}):
                self.assertEqual(etat_seance.FinFilm(META).observer(dict(self.YT, **variante)), "inconnu", variante)

    def test_youtube_de_kamcine_juste_apres_entracte_ne_compte_pas(self):
        horloge = Horloge()
        with patch.object(etat_seance, "time", horloge):
            suivi = etat_seance.FinFilm(META)
            suivi.observer(P)
            horloge.avancer(etat_seance.GARDE_APRES_YOUTUBE_KAMCINE_S + 1)
            suivi.patienter()
            self.assertEqual(suivi.observer(dict(self.YT, progression=True, depuis=1)), "inconnu")

    def test_simple_pause_infuse_n_est_pas_une_interruption(self):
        horloge = Horloge()
        with patch.object(etat_seance, "time", horloge):
            suivi = etat_seance.FinFilm(META)
            horloge.avancer(etat_seance.GARDE_APRES_YOUTUBE_KAMCINE_S + 1)
            for _ in range(10):
                horloge.avancer(10)
                self.assertEqual(suivi.observer(dict(P, etat="Paused")), "pause")

    def test_registre_ne_prouve_la_progression_qu_apres_un_vrai_mouvement(self):
        from observation import ObservationLedger
        t = [1000.0]
        registre = ObservationLedger(clock=lambda: t[0])
        premier = registre.observe(dict(self.YT), "test", 120)
        self.assertFalse(premier["progression"], "Une première apparition ne prouve rien")
        t[0] += 3
        suite = registre.observe(dict(self.YT, pos=53), "test", 120)
        self.assertTrue(suite["progression"])
        self.assertTrue(etat_seance.autre_lecture_active(suite))

    def test_quitter_avant_fin_suspend_et_pause_ne_termine_jamais(self):
        horloge = Horloge()
        with patch.object(etat_seance, "time", horloge):
            suivi = etat_seance.FinFilm(META)
            suivi.observer(P)
            for _ in range(100):
                self.assertEqual(suivi.observer(dict(P, etat="Paused")), "pause")
            self.assertEqual(suivi.observer({"etat": "Idle"}), "inconnu")
            horloge.avancer(etat_seance.ABSENCE_TOLERANCE_S + 1)
            self.assertEqual(suivi.observer({"etat": "Idle"}), "suspendue")
            self.assertEqual(suivi.observer(P), "lecture")

    def test_un_autre_titre_ne_declenche_pas_le_generique(self):
        horloge = Horloge()
        with patch.object(etat_seance, "time", horloge):
            suivi = etat_seance.FinFilm(META)
            suivi.observer(P)
            self.assertEqual(suivi.observer(dict(P, titre="Autre film", pos=7199)), "inconnu")
            horloge.avancer(etat_seance.ABSENCE_TOLERANCE_S + 1)
            r = suivi.observer(dict(P, titre="Autre film", pos=7199))
            self.assertEqual(r, "suspendue")
            self.assertEqual(suivi.position, 100)

    def test_absence_breve_ne_fait_pas_perdre_l_identite(self):
        # Point 90 : une pause/avance/recul manuels avec la télécommande peuvent publier un instant un état Idle
        # (rebufferisation), sans jamais dépasser ABSENCE_TOLERANCE_S : l'identité ne doit pas se perdre pour ça.
        horloge = Horloge()
        with patch.object(etat_seance, "time", horloge):
            suivi = etat_seance.FinFilm(META)
            suivi.observer(P)
            self.assertEqual(suivi.observer({"etat": "Idle"}), "inconnu")
            horloge.avancer(etat_seance.ABSENCE_TOLERANCE_S - 2)
            self.assertEqual(suivi.observer({"etat": "Idle"}), "inconnu")
            self.assertEqual(suivi.observer(dict(P, pos=150)), "lecture")
            self.assertEqual(suivi.position, 150)

    def test_vue_separe_pilotage_lecture_et_entracte(self):
        f = {"actif": True, "etat": "Playing"}
        self.assertEqual(etat_seance.vue(f, None, False, False, {})["nature"], "detectee")
        self.assertEqual(etat_seance.vue(f, {"confirme": True}, False, False, {})["nature"], "liee")
        for phase, detail, film in [("film", {}, f), ("pause", {}, dict(f, etat="Paused")),
                                    ("entracte", {"entracte": {"etat": "actif"}}, {"actif": False}),
                                    ("generique", {"generique": {"etat": "fait"}}, f)]:
            vue = etat_seance.vue(film, {"confirme": True}, True, False, detail)
            self.assertEqual(vue["nature"], "pilotee")
            self.assertEqual(vue["phase"], phase)
            self.assertEqual(vue["commandes"], phase != "entracte")
        self.assertTrue(etat_seance.vue(f, {"confirme": True}, True, False, {"entracte": {"etat": "fait"}})["commandes"])

    def test_youtube_idle_inconnu_veille_et_autre_app_ne_sont_pas_une_lecture(self):
        p = dict(P, app="com.google.ios.youtube")
        for variante in ({"etat": "Idle"}, {"veille": True}, {"etat": "Unknown"}, {"app_active": "com.firecore.infuse"}):
            self.assertFalse(etat_seance.media_reel(dict(p, **variante)))
        self.assertTrue(etat_seance.media_reel(dict(p, etat="Paused")))


class Service(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["KAMCINE_DIR"] = cls.tmp.name
        from app import main
        cls.m = main

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        m = self.m
        self.film_original = m.film
        m.etat.update(proc=None, preparation=False, lignes=[], mode="reel", type="film", debut=0, options=None)
        m.relais.actif = False
        m.LANCEMENT["d"] = None
        m.SUSPENDUES["l"] = []
        m.cache_film.update(t=0, data=None)
        m.BA_LANCEE["d"] = None
        from observation import ObservationLedger
        m.atvlive.LIVE.observations = ObservationLedger()
        self.p = dict(P, lu_a=m.time.time())
        for objet, nom, valeur in [(m.relais, "demarrer", Mock(return_value=True)),
                                   (m, "lire_playing", Mock(side_effect=lambda **kw: self.p)),
                                   (m, "lire_playing_cli", Mock(side_effect=lambda **kw: self.p)),
                                   (m, "atv", Mock(return_value=(True, ""))),
                                   (m, "film_choisi", Mock(return_value=None)),
                                   (m, "meta_par_id", Mock(return_value=META)),
                                   (m, "film", Mock(return_value={"actif": True, "etat": "Playing", "meta": META, "type": "movie"})),
                                   (m.notifications, "ajouter", Mock()),
                                   (m.suivi, "incrementer_seances", Mock())]:
            p = patch.object(objet, nom, valeur);p.start();self.addCleanup(p.stop)

    # ---------- Lot 2.6.81 : séance conservée, série, fin au générique, carte de fin ----------
    YOUTUBE = {"app": "com.google.ios.youtube", "app_nom": "YouTube", "titre": "Une vidéo", "etat": "Playing", "pos": 40, "total": 300}

    def seance_interrompue(self):
        m = self.m
        m.oublier_fin_seance()
        d = m.lancement_definir("movie", 1, None, None, "seance", meta=META)
        d.update(position=3100, total=7200, mode="reel")
        self.p = {"etat": "Idle", "lu_a": m.time.time()}
        m.film.return_value = {"actif": False, "ailleurs": dict(self.YOUTUBE)}
        return d

    def test_seance_interrompue_et_youtube_existent_ensemble(self):
        m = self.m
        d = self.seance_interrompue()
        status = m.status()
        self.assertFalse(status["en_cours"])
        self.assertEqual(status["seance_conservee"]["id"], 1)
        self.assertEqual(status["seance_conservee"]["position"], 3100)
        self.assertEqual(status["seance"]["nature"], "ailleurs", "YouTube reste visible sous la séance conservée")
        self.assertIs(m.LANCEMENT["d"], d, "La séance n'est pas perdue pour autant")
        # Lot 2.6.91 : un autre départ ne l'efface plus, il la met en suspens.
        self.assertFalse(m.conflit_depart("movie", 2))
        m.lancement_definir("movie", 2, None, None, "seance", meta=dict(META, id=2, titre="Autre"))
        self.assertEqual([x["id"] for x in m.SUSPENDUES["l"]], [1])
        m.SUSPENDUES["l"] = []

    def test_autre_film_infuse_n_est_pas_lie_a_la_seance_conservee(self):
        m = self.m
        self.seance_interrompue()
        m.film.return_value = {"actif": True, "etat": "Playing", "meta": dict(META, id=2, titre="Autre"), "type": "movie"}
        status = m.status()
        self.assertEqual(status["seance"]["nature"], "detectee")
        self.assertEqual(status["seance_conservee"]["id"], 1)

    def test_reprendre_la_seance_conservee_sans_nouvelle_seance(self):
        m = self.m
        d = self.seance_interrompue()
        self.p = dict(self.YOUTUBE, media="Video", lu_a=m.time.time())
        refus = m.lancement_reprendre(d["t"])
        self.assertEqual(refus.status_code, 409, "Sans confirmation, une autre lecture n'est pas remplacée")
        with patch.object(m.atvlive.LIVE, "pause", return_value=True) as pause:
            self.assertTrue(m.lancement_reprendre(d["t"], remplacer=1)["ok"])
        pause.assert_called_once()
        m.atv.assert_called_with("launch_app=infuse://movie/1?play", delai=25)
        self.assertIs(m.LANCEMENT["d"], d, "Même séance, même jeton : rien n'est recréé")
        self.assertTrue(d["lumieres_reprise"])
        # Le film rejoue : le rattachement reprend le scénario et remet l'ambiance, sans rejouer les bandes annonces.
        self.p = dict(P, pos=3110, lu_a=m.time.time())
        m.lancement_courant()
        ctx = m.relais.demarrer.call_args.args[0]
        self.assertTrue(ctx["silencieux"])
        self.assertTrue(ctx["lumieres_reprise"])
        self.assertNotIn("lumieres_reprise", d)

    def test_reprendre_masque_la_carte_tout_de_suite_puis_rattache(self):
        # Lot 2.6.82 : la carte ne reste pas affichée pendant qu'Infuse rouvre le média ; le rattachement prend ensuite la main.
        m = self.m
        d = self.seance_interrompue()
        self.assertTrue(m.lancement_reprendre(d["t"], remplacer=1)["ok"])
        self.p = {"etat": "Idle", "lu_a": m.time.time()}
        m.film.return_value = {"actif": False}
        status = m.status()
        self.assertIsNone(status["seance_conservee"], "Plus de carte Séance en attente après Reprendre")
        self.assertTrue(status["en_cours"])
        self.assertEqual(status["seance"]["phase"], "reprise")
        self.p = dict(P, pos=3110, lu_a=m.time.time())
        m.lancement_courant()
        m.relais.demarrer.assert_called()
        self.assertNotIn("reprise_demandee", d, "Rattachée : la reprise demandée est soldée")

    def test_reprise_sans_retour_du_media_redevient_en_attente(self):
        m = self.m
        d = self.seance_interrompue()
        m.lancement_reprendre(d["t"], remplacer=1)
        m.film.return_value = {"actif": False}
        d["reprise_demandee"] -= m.REPRISE_ATTENTE_S + 1
        self.assertIsNotNone(m.status()["seance_conservee"])

    def test_reprise_refusee_par_l_apple_tv_ne_masque_pas_la_carte(self):
        m = self.m
        d = self.seance_interrompue()
        m.atv.return_value = (False, "Apple TV injoignable")
        self.assertEqual(m.lancement_reprendre(d["t"], remplacer=1).status_code, 502)
        self.assertNotIn("reprise_demandee", d)
        self.assertIsNotNone(m.status()["seance_conservee"])

    def test_carte_de_fin_exclusive_meme_si_infuse_montre_encore_le_film(self):
        m = self.m
        d = m.lancement_definir("movie", 1, None, None, "seance", meta=META)
        d["mode"] = "test"
        m.ligne("Séance terminée")
        m.film.return_value = {"actif": True, "etat": "Playing", "meta": META, "type": "movie"}
        self.p = dict(P, lu_a=m.time.time())
        status = m.status()
        self.assertEqual(status["fin_seance"]["id"], 1)
        self.assertFalse(status["en_cours"])
        self.assertEqual(status["seance"]["nature"], "vide", "Le film qui vient de finir n'est pas présenté comme une nouvelle lecture")
        m.film.return_value = {"actif": True, "etat": "Playing", "meta": dict(META, id=2, titre="Autre"), "type": "movie"}
        self.assertEqual(m.status()["seance"]["nature"], "detectee", "Un autre film reste visible sous la carte de fin")

    # ---------- Lot 2.6.83 : identité unique du média observé ----------
    REACHER = {"type": "tv", "id": 7, "titre": "Reacher", "annee": "2022", "saison": 1, "episode": 3}

    def reacher_en_attente(self):
        m = self.m
        m.oublier_fin_seance()
        d = m.lancement_definir("tv", 7, 1, 2, "seance", meta=dict(self.REACHER, episode=2))
        d.update(episode=3, meta=dict(self.REACHER), mode="reel")
        self.p = dict(self.YOUTUBE, media="Video", lu_a=m.time.time())
        m.film.return_value = {"actif": False, "ailleurs": dict(self.YOUTUBE)}
        self.assertIsNotNone(m.status()["seance_conservee"])
        return d

    def episode_observe(self, **champs):
        """Infuse publie d'abord le titre de l'épisode, sous un nom de série localisé : les règles de titre seules échouent."""
        m = self.m
        p = dict({"titre": "Cuillère en argent", "serie_nom": "Jack Reacher", "saison_n": 1, "episode_n": 3, "etat": "Playing",
                  "app": "com.firecore.infuse", "media": "Video", "pos": 900, "total": 2800, "lu_a": m.time.time()}, **champs)
        return m.annoter_identite(p)

    def test_meme_episode_relance_a_la_main_se_rattache_des_qu_il_est_identifie(self):
        m = self.m
        d = self.reacher_en_attente()
        m.cache_tmdb.pop(("titre", m.norm("Jack Reacher"), 1, 3), None)
        self.p = self.episode_observe()
        m.film.return_value = {"actif": True, "etat": "Playing", "type": "tv", "meta": None, "titre": "Cuillère en argent"}
        status = m.status()
        self.assertIsNotNone(status["seance_conservee"], "Pas encore identifié : pas de rattachement deviné")
        m.relais.demarrer.assert_not_called()
        # La recherche TMDB de /film aboutit : même série, même épisode. Rattachement au relevé suivant, sans second lancement.
        m.cache_tmdb[("titre", m.norm("Jack Reacher"), 1, 3)] = dict(self.REACHER)
        self.p = self.episode_observe()
        self.assertEqual(self.p["identite"], {"type": "tv", "id": 7, "saison": 1, "episode": 3})
        m.film.return_value = {"actif": True, "etat": "Playing", "type": "tv", "meta": dict(self.REACHER), "titre": "Cuillère en argent"}
        status = m.status()
        self.assertIsNone(status["seance_conservee"], "Jamais « Reprendre S1E3 » et « S1E3 en lecture » ensemble")
        self.assertTrue(status["en_cours"])
        m.relais.demarrer.assert_called_once()
        self.assertIs(m.LANCEMENT["d"], d)

    def test_autre_episode_de_la_meme_serie_ne_se_rattache_pas(self):
        m = self.m
        self.reacher_en_attente()
        m.cache_tmdb[("titre", m.norm("Jack Reacher"), 1, 4)] = dict(self.REACHER, episode=4)
        self.p = self.episode_observe(episode_n=4)
        m.film.return_value = {"actif": True, "etat": "Playing", "type": "tv", "meta": dict(self.REACHER, episode=4)}
        status = m.status()
        self.assertEqual(status["seance_conservee"]["episode"], 3)
        self.assertFalse(status["en_cours"])
        m.relais.demarrer.assert_not_called()

    def test_autre_film_ne_se_rattache_pas(self):
        m = self.m
        self.seance_interrompue()
        m.cache_tmdb[("titre", m.norm("Film B"), None, None)] = dict(META, id=2, titre="Film B")
        self.p = m.annoter_identite(dict(P, titre="Film B", lu_a=m.time.time()))
        m.film.return_value = {"actif": True, "etat": "Playing", "type": "movie", "meta": dict(META, id=2, titre="Film B")}
        status = m.status()
        self.assertEqual(status["seance_conservee"]["id"], 1)
        m.relais.demarrer.assert_not_called()

    def test_titre_ressemblant_sans_identite_ne_se_rattache_pas(self):
        m = self.m
        self.seance_interrompue()
        self.p = dict(P, titre="Film de test 2", lu_a=m.time.time())
        m.film.return_value = {"actif": True, "etat": "Playing", "type": None, "meta": None}
        self.assertIsNotNone(m.status()["seance_conservee"])
        m.relais.demarrer.assert_not_called()

    def test_jamais_le_meme_media_en_attente_et_en_lecture(self):
        # Filet central : même si l'observation brute ne prouve rien, une lecture identifiée comme le même épisode l'emporte.
        m = self.m
        for lecture, conservee in [({"actif": True, "etat": "Playing", "meta": dict(self.REACHER)}, False),
                                   ({"actif": True, "etat": "Paused", "meta": dict(self.REACHER)}, False),
                                   ({"actif": True, "etat": "Playing", "meta": dict(self.REACHER, episode=4)}, True),
                                   ({"actif": False, "ailleurs": dict(self.YOUTUBE)}, True),
                                   ({"actif": False}, True)]:
            with self.subTest(lecture=lecture):
                m.relais.demarrer.reset_mock()
                self.reacher_en_attente()
                self.p = {"etat": "Idle", "lu_a": m.time.time()}
                m.film.return_value = lecture
                status = m.status()
                self.assertEqual(status["seance_conservee"] is not None, conservee)
                if status["seance_conservee"] and lecture.get("actif"):
                    self.assertFalse(m.meme_media(lecture.get("meta"), status["seance_conservee"]))

    # ---------- Lot 2.6.84 : métadonnées réellement publiées par Infuse ----------
    def reacher_infuse(self, **champs):
        m = self.m
        return dict({"titre": "Reacher", "etat": "Playing", "app": "com.firecore.infuse", "media": "Video",
                     "pos": 1500, "total": 2789, "lu_a": m.time.time()}, **champs)

    def test_infuse_ne_publie_que_le_nom_de_la_serie_rattache_par_le_meme_fichier(self):
        m = self.m
        d = self.reacher_en_attente()
        m.etat["lignes"] = [(1, "Série : pas de bandes annonces ni d'entracte"), (2, "Générique attendu vers 2600s sur 2789s (repli)")]
        m.save_session_scenario()
        self.assertEqual(d["total_fichier"], 2789, "La durée du fichier est relevée dans le minutage du contrôleur")
        m.RATTACHEMENTS["lignes"].clear(); m.RATTACHEMENTS["sig"] = None
        # 1. Infuse démarre sans titre ni durée : rien de certain, pas de rattachement, pas de film supposé.
        self.p = self.reacher_infuse(titre="", total=0, pos=0)
        m.film.return_value = {"actif": True, "etat": "Playing", "type": None, "meta": None, "titre": ""}
        self.assertIsNotNone(m.status()["seance_conservee"])
        m.relais.demarrer.assert_not_called()
        # 2. Puis seulement « Reacher » avec la durée du fichier : même fichier, rattachement immédiat, sans second lancement.
        self.p = self.reacher_infuse()
        m.film.return_value = {"actif": True, "etat": "Playing", "type": None, "meta": None, "titre": "Reacher"}
        status = m.status()
        self.assertIsNone(status["seance_conservee"])
        self.assertTrue(status["en_cours"])
        self.assertEqual(status["type"], "serie")
        m.relais.demarrer.assert_called_once()
        ctx = m.relais.demarrer.call_args.args[0]
        self.assertEqual(ctx["meta"]["total_fichier"], 2789, "Le relais rattaché connaît le fichier")
        raisons = [l["raison"] for l in m.RATTACHEMENTS["lignes"] if l.get("etape") == "rattachement"]
        self.assertIn("même fichier : nom de la série et durée identique", raisons)
        self.assertTrue(any("durée" in r and "pas encore" in r for r in raisons) or any("nom différent" in r for r in raisons))
        ligne = [l for l in m.RATTACHEMENTS["lignes"] if l.get("etape") == "rattachement"][-1]
        self.assertEqual((ligne["titre"], ligne["duree"], ligne["seance"]["episode"], ligne["rattache"]), ("Reacher", 2789, 3, True))
        self.assertIs(m.LANCEMENT["d"], d)

    def test_episode_suivant_du_meme_nom_jamais_rattache(self):
        m = self.m
        d = self.reacher_en_attente()
        d["total_fichier"] = 2789
        for p, raison in [(self.reacher_infuse(total=2650), "autre durée"),
                          (self.reacher_infuse(titre="Reacher S01E04"), "autre épisode publié"),
                          (self.reacher_infuse(serie_nom="Reacher", saison_n=1, episode_n=4), "autre épisode publié")]:
            with self.subTest(p=p):
                self.p = p
                m.film.return_value = {"actif": True, "etat": "Playing", "type": None, "meta": None}
                self.assertIsNotNone(m.status()["seance_conservee"])
                self.assertIn(raison, lecture_infuse.explique_correspondance(p, d)[1])
        m.relais.demarrer.assert_not_called()

    def test_sans_duree_de_fichier_connue_pas_de_rattachement_devine(self):
        m = self.m
        d = self.reacher_en_attente()
        d.pop("total_fichier", None)
        self.p = self.reacher_infuse()
        m.film.return_value = {"actif": True, "etat": "Playing", "type": None, "meta": None}
        self.assertIsNotNone(m.status()["seance_conservee"])
        self.assertIn("durée du fichier de la séance inconnue", lecture_infuse.explique_correspondance(self.p, d)[1])
        # Dès qu'Infuse publie l'épisode (ou que TMDB l'identifie), le rattachement se fait au relevé suivant.
        self.p = self.reacher_infuse(titre="Reacher S01E03")
        self.assertIsNone(m.status()["seance_conservee"])

    def test_tmdb_ne_transforme_pas_un_titre_voisin_en_film(self):
        m = self.m
        m.cache_tmdb.pop(("titre", m.norm("Reacher"), None, None), None)
        m.cache_neg.clear()
        resultats = {"results": [{"id": 75780, "title": "Jack Reacher", "original_title": "Jack Reacher", "release_date": "2012-12-20", "popularity": 90}]}
        with patch.object(m, "tmdb_get", return_value=resultats), patch.object(m, "meta_par_id") as meta:
            self.assertIsNone(m.meta_par_titre({"type": None, "nom": "Reacher", "saison": None, "episode": None}, 2789))
        meta.assert_not_called()

    # ---------- Lot 2.6.85 : format réel d'Infuse « Série - S1 ∙ E4 - Titre » ----------
    REEL = "Reacher - S1 \u2219 E4 - Dommages collatéraux"

    def test_format_reel_s1e4_jamais_rattache_a_la_seance_s1e3(self):
        m = self.m
        d = self.reacher_en_attente()
        d["total_fichier"] = 2716
        m.RATTACHEMENTS["lignes"].clear(); m.RATTACHEMENTS["sig"] = None
        self.p = self.reacher_infuse(titre=self.REEL, serie_nom="", saison_n=0, episode_n=0, total=2716)
        m.film.return_value = {"actif": True, "etat": "Playing", "type": "tv", "meta": dict(self.REACHER, episode=4)}
        status = m.status()
        self.assertEqual(status["seance_conservee"]["episode"], 3)
        m.relais.demarrer.assert_not_called()
        ligne = [l for l in m.RATTACHEMENTS["lignes"] if l.get("etape") == "rattachement"][-1]
        self.assertEqual((ligne["type_observe"], ligne["rattache"], ligne["raison"]), ("tv", False, "autre épisode publié : S1E4"))

    def test_format_reel_s1e3_rattache_immediatement(self):
        m = self.m
        d = self.reacher_en_attente()
        self.p = self.reacher_infuse(titre=self.REEL.replace("E4", "E3"), serie_nom="", saison_n=0, episode_n=0)
        m.film.return_value = {"actif": True, "etat": "Playing", "type": "tv", "meta": dict(self.REACHER)}
        status = m.status()
        self.assertIsNone(status["seance_conservee"])
        self.assertTrue(status["en_cours"])
        self.assertEqual(status["type"], "serie")
        self.assertEqual([e["nom"] for e in status["etapes"]], ["Préparation", "Épisode", "Générique"])
        m.relais.demarrer.assert_called_once()
        self.assertIs(m.LANCEMENT["d"], d)

    def test_format_reel_resolu_comme_serie_jamais_comme_film(self):
        m = self.m
        p = {"titre": self.REEL, "serie_nom": "", "saison_n": 0, "episode_n": 0, "etat": "Playing",
             "app": "com.firecore.infuse", "media": "Video", "pos": 900, "total": 2716, "lu_a": m.time.time()}
        m.cache_film.update(t=0, data=None)
        recherche = Mock(return_value=(None, True))
        with patch.object(m, "lire_playing", Mock(return_value=p)), patch.object(m, "meta_titre_rapide", recherche), \
                patch.object(m, "film_choisi", Mock(return_value=None)):
            data = self.film_original()
        self.assertEqual(data["type"], "tv")
        self.assertEqual((data["serie"]["nom"], data["serie"]["saison"], data["serie"]["episode"]), ("Reacher", 1, 4))
        info = recherche.call_args.args[0]
        self.assertEqual((info["type"], info["nom"], info["saison"], info["episode"]), ("tv", "Reacher", 1, 4),
                         "Recherche TMDB de la série Reacher, jamais un film sur la chaîne complète")
        # Une ancienne identité « film » mise en cache pour la chaîne complète n'est plus jamais utilisée.
        m.cache_tmdb[("titre", m.norm(self.REEL), None, None)] = {"type": "movie", "id": 1767261, "titre": self.REEL}
        self.assertNotEqual((m.annoter_identite(dict(p)).get("identite") or {}).get("type"), "movie")

    # ---------- Lot 2.6.86 : cycle de vie des observations, cas A à E ----------
    S1E5 = {"type": "tv", "id": 108978, "titre": "Reacher", "annee": "2022", "saison": 1, "episode": 5}

    def chaine_reelle(self, cli=None):
        """Observation réelle : connexion continue (self.p) complétée par atvremote (cli) puis annotée ; /film réel ; TMDB simulé."""
        m = self.m
        self.cli = cli
        class Immediat:
            def __init__(self, target=None, daemon=None, args=(), **k): self.f, self.a = target, args
            def start(self): self.f(*self.a)
        for objet, nom, valeur in [
                (m, "lire_playing", Mock(side_effect=lambda **kw: m.annoter_identite(m.completer_metadonnees(dict(self.p))))),
                (m, "lire_playing_cli", Mock(side_effect=lambda **kw: dict(self.cli) if self.cli else None)),
                (m, "film", Mock(side_effect=lambda: (m.cache_film.update(t=0, data=None), self.film_original())[1])),
                (m, "film_choisi", Mock(return_value=None)),
                (m, "meta_titre_rapide", Mock(side_effect=lambda info, total: (dict(self.S1E5, episode=info["episode"]), False)
                                              if info["type"] == "tv" and info["nom"] == "Reacher" else (None, True))),
                (m.threading, "Thread", Immediat)]:
            p = patch.object(objet, nom, valeur);p.start();self.addCleanup(p.stop)
        m.COMPLEMENT.update(t=0, p=None, busy=False)

    def infuse(self, titre, pos=900, **champs):
        return dict({"titre": titre, "etat": "Playing", "app": "com.firecore.infuse", "media": "Video", "pos": pos,
                     "total": 2869, "lu_a": self.m.time.time()}, **champs)

    def seance_s1e5_puis_youtube(self):
        m = self.m
        m.oublier_fin_seance()
        d = m.lancement_definir("tv", 108978, 1, 5, "seance", meta=dict(self.S1E5))
        d["mode"] = "reel"
        m.RATTACHEMENTS["lignes"].clear(); m.CHAINE_SIG.clear(); m.RATTACHEMENTS["sig"] = None
        self.chaine_reelle()
        self.p = dict(self.YOUTUBE, media="Video", lu_a=m.time.time())
        status = m.status()
        return d, status

    def test_cas_a_youtube_ne_fait_jamais_perdre_la_seance(self):
        m = self.m
        d, status = self.seance_s1e5_puis_youtube()
        self.assertIs(m.LANCEMENT["d"], d)
        self.assertEqual(status["seance_conservee"]["episode"], 5, "Carte Séance en attente")
        self.assertEqual(status["seance"]["nature"], "ailleurs", "YouTube dessous")
        self.assertFalse(any(l.get("evenement") == "séance effacée" for l in m.RATTACHEMENTS["lignes"]))

    def test_cas_b_titre_publie_quelques_releves_plus_tard(self):
        m = self.m
        d, _ = self.seance_s1e5_puis_youtube()
        self.p = self.infuse("")
        status = m.status()
        self.assertIsNotNone(status["seance_conservee"], "Relevé 1 sans titre : rien de deviné")
        self.assertIsNone(status["lecture"]["type"], "Jamais un film supposé")
        self.p = self.infuse("Reacher - S1 \u2219 E5 - Aucune excuse", pos=903)
        status = m.status()
        self.assertIsNone(status["seance_conservee"])
        self.assertTrue(status["en_cours"])
        self.assertEqual([e["nom"] for e in status["etapes"]], ["Préparation", "Épisode", "Générique"])
        m.relais.demarrer.assert_called_once()

    def test_cas_b_bis_titre_jamais_publie_par_la_connexion_continue(self):
        # Constat matériel : la connexion continue garde un titre vide ; une connexion neuve rapporte l'état complet.
        m = self.m
        d, _ = self.seance_s1e5_puis_youtube()
        self.cli = self.infuse("Reacher - S1 \u2219 E5 - Aucune excuse")
        self.p = self.infuse("")
        m.status()
        status = m.status()
        self.assertIsNone(status["seance_conservee"], "Complément par connexion neuve, sans second lancement")
        self.assertEqual(status["lecture"]["meta"]["episode"], 5)
        etapes = {l["etape"] for l in m.RATTACHEMENTS["lignes"]}
        self.assertTrue({"complement", "identification", "rattachement", "home"} <= etapes, etapes)
        m.relais.demarrer.assert_called()

    def test_cas_c_titre_absent_plusieurs_releves_puis_present(self):
        m = self.m
        self.seance_s1e5_puis_youtube()
        self.p = self.infuse("")
        for i in range(4):
            m.COMPLEMENT["t"] = 0     # chaque relevé peut relancer une lecture de complément, qui ne rapporte rien
            self.assertIsNotNone(m.status()["seance_conservee"])
        self.assertGreaterEqual(m.lire_playing_cli.call_count, 2, "La réévaluation continue tant que le titre manque")
        self.cli = self.infuse("Reacher - S1 \u2219 E5 - Aucune excuse")
        m.COMPLEMENT["t"] = 0
        m.status()
        self.assertIsNone(m.status()["seance_conservee"])

    def test_cas_d_titre_jamais_disponible_reste_neutre(self):
        m = self.m
        self.seance_s1e5_puis_youtube()
        self.p = self.infuse("")
        for _ in range(3):
            m.COMPLEMENT["t"] = 0
            status = m.status()
        self.assertIsNotNone(status["seance_conservee"])
        self.assertIsNone(status["lecture"]["type"])
        self.assertIsNone(status["lecture"]["meta"])
        ident = [l for l in m.RATTACHEMENTS["lignes"] if l.get("etape") == "identification"][-1]
        self.assertIn("titre absent", ident["raison"])

    def test_cas_e_autre_episode_garde_la_seance_en_attente(self):
        m = self.m
        self.seance_s1e5_puis_youtube()
        self.p = self.infuse("Reacher - S1 \u2219 E6 - Autre")
        status = m.status()
        self.assertEqual(status["seance_conservee"]["episode"], 5)
        m.relais.demarrer.assert_not_called()

    def test_seance_lancee_sans_identite_confirmee_par_le_complement(self):
        # « Lancer la séance » crée une séance à confirmer par la lecture : un titre vide ne doit pas l'empêcher.
        m = self.m
        m.oublier_fin_seance()
        d = m.lancement_definir("tv", 108978, 1, 5, "seance")
        d["meta"] = dict(self.S1E5)
        self.chaine_reelle(cli=self.infuse("Reacher - S1 \u2219 E5 - Aucune excuse"))
        self.p = self.infuse("")
        self.assertFalse(d.get("confirme"))
        m.status()
        m.status()
        self.assertTrue(d.get("confirme"), "Confirmée dès que la connexion neuve rapporte le bon épisode")
        self.assertTrue(any(l.get("evenement") == "séance confirmée" for l in m.RATTACHEMENTS["lignes"]))

    def test_complement_jamais_applique_a_un_autre_fichier(self):
        m = self.m
        m.COMPLEMENT.update(t=m.time.time(), p=dict(self.infuse("Reacher - S1 \u2219 E5 - X"), total=2500, recu=m.time.time()), busy=False)
        self.assertEqual(m.completer_metadonnees(self.infuse(""))["titre"], "", "Autre durée : autre fichier")
        self.assertEqual(m.completer_metadonnees(self.infuse("Titre publié"))["titre"], "Titre publié", "Jamais remplacer un titre publié")

    # ---------- Lot 2.6.87 : fraîcheur des lectures externes, de bout en bout ----------
    def chaine_avec_registre(self):
        m = self.m
        from observation import ObservationLedger
        horloge = [m.time.time()]
        m.atvlive.LIVE.observations = ObservationLedger(None, lambda: horloge[0])
        self.chaine_reelle()
        m.lire_playing.side_effect = lambda **kw: m.annoter_identite(m.completer_metadonnees(
            m.atvlive.LIVE.observations.observe(dict(self.p, lu_a=m.time.time()), "test", 120)))
        return horloge

    def releve(self, horloge, p, avance=3):
        horloge[0] += avance
        self.p = p
        return self.m.status()

    def test_sortie_d_infuse_vers_l_accueil_sans_ancien_youtube(self):
        m = self.m
        m.oublier_lancement(raison="test")
        h = self.chaine_avec_registre()
        yt = dict(self.YOUTUBE, media="Video", total=7340)
        self.releve(h, dict(yt, etat="Playing", pos=690)); self.releve(h, dict(yt, etat="Playing", pos=693))
        self.assertEqual(self.releve(h, dict(yt, etat="Paused", pos=699))["seance"]["nature"], "ailleurs")
        ep = "Reacher - S1 \u2219 E5 - Aucune excuse"
        self.releve(h, self.infuse(ep, pos=100), 10)
        self.assertEqual(self.releve(h, self.infuse(ep, pos=103))["seance"]["nature"], "detectee")
        self.assertEqual(self.releve(h, dict(yt, etat="Paused", pos=699), 5)["seance"]["nature"], "vide",
                         "L'ancienne pause YouTube ne réapparaît pas : directement Rien en lecture")
        ligne = [l for l in m.RATTACHEMENTS["lignes"] if l.get("etape") == "externe"][-1]
        self.assertEqual((ligne["affichee"], ligne["raison"]), (False, "pause antérieure à une lecture plus récente"))

    def test_nouvelle_video_youtube_apres_infuse_apparait_vite(self):
        m = self.m
        m.oublier_lancement(raison="test")
        h = self.chaine_avec_registre()
        ep = "Reacher - S1 \u2219 E5 - Aucune excuse"
        self.releve(h, self.infuse(ep, pos=100)); self.releve(h, self.infuse(ep, pos=103))
        nouvelle = dict(self.YOUTUBE, media="Video", titre="Nouvelle vidéo", total=300)
        self.assertEqual(self.releve(h, dict(nouvelle, etat="Playing", pos=1))["seance"]["nature"], "ailleurs")

    def test_pause_youtube_recente_sans_infuse_reste_affichee(self):
        m = self.m
        m.oublier_lancement(raison="test")
        h = self.chaine_avec_registre()
        yt = dict(self.YOUTUBE, media="Video", total=7340)
        self.releve(h, dict(yt, etat="Playing", pos=10)); self.releve(h, dict(yt, etat="Playing", pos=13))
        for _ in range(5):
            status = self.releve(h, dict(yt, etat="Paused", pos=15), 10)
        self.assertEqual(status["seance"]["nature"], "ailleurs")
        self.assertEqual(status["lecture"]["ailleurs"]["etat"], "Paused")

    def test_pause_infuse_reste_infuse_et_seance_rattachee_au_retour(self):
        m = self.m
        m.oublier_fin_seance()
        d = m.lancement_definir("tv", 108978, 1, 5, "seance", meta=dict(self.S1E5))
        d["mode"] = "reel"
        h = self.chaine_avec_registre()
        ep = "Reacher - S1 \u2219 E5 - Aucune excuse"
        self.releve(h, self.infuse(ep, pos=100)); self.releve(h, self.infuse(ep, pos=103))
        status = self.releve(h, self.infuse(ep, pos=103, etat="Paused"), 30)
        self.assertIsNone(status["seance_conservee"], "Une pause Infuse n'est pas une sortie")
        self.assertEqual(status["lecture"]["etat"], "Paused")
        status = self.releve(h, {"etat": "Idle", "app": "com.firecore.infuse"}, 5)
        self.assertIsNotNone(status["seance_conservee"], "Sortie : séance en attente")
        status = self.releve(h, self.infuse(ep, pos=110), 5)
        self.assertIsNone(status["seance_conservee"], "Retour au même épisode : rattachée")
        self.assertIs(m.LANCEMENT["d"], d)

    # ---------- Lot 2.6.88 : lecture Infuse actuelle, mémoire d'identité, cas A à J (fixture du journal matériel) ----------
    EP5 = "Reacher - S1 \u2219 E5 - Aucune excuse"
    CMD = {"play": True, "pause": True, "play_pause": True, "back": True, "forward": True, "position": True}
    FERME = {"play": False, "pause": False, "play_pause": False, "back": False, "forward": False, "position": False}

    def chaine_complete(self):
        """Toute la chaîne réelle : registre, pause Infuse qualifiée, mémoire, complément, identité, /film réel."""
        m = self.m
        from observation import ObservationLedger
        horloge = [m.time.time()]
        m.atvlive.LIVE.observations = ObservationLedger(None, lambda: horloge[0])
        m.MEMOIRE_INFUSE["p"] = None
        self.chaine_reelle()
        m.lire_playing.side_effect = lambda **kw: m.memoriser_identite_infuse(m.annoter_identite(m.completer_metadonnees(
            m.qualifier_infuse(m.atvlive.LIVE.observations.observe(dict(self.p, lu_a=m.time.time()), "test", 120)))))
        return horloge

    def reacher(self, titre=None, pos=2467, etat="Playing", cmd=None):
        return dict(self.infuse(self.EP5 if titre is None else titre, pos=pos, total=2869, etat=etat), fonctions=cmd or self.CMD)

    def reacher_identifie(self, h):
        self.releve(h, self.reacher(pos=2460)); return self.releve(h, self.reacher(pos=2463))

    def test_a_titre_vide_du_meme_flux_garde_reacher(self):
        m = self.m; m.oublier_lancement(raison="test")
        h = self.chaine_complete()
        self.reacher_identifie(h)
        status = self.releve(h, self.reacher(titre="", pos=2466))
        self.assertEqual(status["lecture"]["meta"]["id"], 108978, "Jamais « Lecture en cours » pour un trou de métadonnées")
        self.assertEqual((status["lecture"]["type"], status["lecture"]["meta"]["episode"]), ("tv", 5))
        status = self.releve(h, self.reacher(titre="", pos=2466, etat="Paused"), 10)
        self.assertEqual((status["lecture"]["meta"]["episode"], status["lecture"]["etat"]), (5, "Paused"))
        m.lire_playing_cli.assert_not_called()   # la mémoire suffit : aucun appel réseau

    def test_b_plusieurs_releves_vides_puis_retour_du_titre(self):
        m = self.m; m.oublier_lancement(raison="test")
        h = self.chaine_complete()
        self.reacher_identifie(h)
        for i in range(4):
            self.assertEqual(self.releve(h, self.reacher(titre="", pos=2466 + 3 * i))["lecture"]["meta"]["episode"], 5)
        self.assertEqual(self.releve(h, self.reacher(pos=2480))["lecture"]["meta"]["episode"], 5)

    def test_c_autre_episode_explicite_jamais_s1e5(self):
        m = self.m; m.oublier_lancement(raison="test")
        h = self.chaine_complete()
        self.reacher_identifie(h)
        status = self.releve(h, self.reacher(titre="Reacher - S1 \u2219 E6 - Autre", pos=10))
        self.assertEqual(status["lecture"]["meta"]["episode"], 6)
        # Et un titre vide ensuite relève du flux S1E6 : autre durée que S1E5 dans la réalité ; ici même durée, même flux.
        self.assertEqual(m.MEMOIRE_INFUSE["p"]["titre"], "Reacher - S1 \u2219 E6 - Autre")

    def test_c_bis_duree_incompatible_ne_reprend_pas_la_memoire(self):
        m = self.m; m.oublier_lancement(raison="test")
        h = self.chaine_complete()
        self.reacher_identifie(h)
        status = self.releve(h, dict(self.reacher(titre="", pos=100), total=3100))
        self.assertIsNone(status["lecture"]["meta"])

    def test_d_vraie_pause_dans_infuse_reste_reacher(self):
        m = self.m; m.oublier_lancement(raison="test")
        h = self.chaine_complete()
        self.reacher_identifie(h)
        for _ in range(5):
            status = self.releve(h, self.reacher(pos=2463, etat="Paused"), 20)
        self.assertEqual(status["seance"]["nature"], "detectee")
        self.assertEqual(status["lecture"]["etat"], "Paused")
        self.assertEqual(status["lecture"]["meta"]["episode"], 5)

    def test_e_seance_puis_accueil_tvos_carte_et_rien_en_lecture(self):
        m = self.m
        m.oublier_fin_seance()
        d = m.lancement_definir("tv", 108978, 1, 5, "seance", meta=dict(self.S1E5)); d["mode"] = "reel"
        h = self.chaine_complete()
        self.reacher_identifie(h)
        status = self.releve(h, self.reacher(pos=2463, etat="Paused", cmd=self.FERME), 3)
        self.assertEqual(status["seance"]["nature"], "vide", "Lecteur fermé : plus de grande fiche Reacher en pause")
        self.assertEqual(status["seance_conservee"]["episode"], 5, "Séance en suspens")
        ligne = [l for l in m.RATTACHEMENTS["lignes"] if l.get("etape") == "identification"][-1]
        self.assertIn("lecteur Infuse fermé", ligne["raison"])

    def test_f_seance_puis_youtube_carte_et_youtube(self):
        m = self.m
        m.oublier_fin_seance()
        d = m.lancement_definir("tv", 108978, 1, 5, "seance", meta=dict(self.S1E5)); d["mode"] = "reel"
        h = self.chaine_complete()
        self.reacher_identifie(h)
        yt = dict(self.YOUTUBE, media="Video", titre="Directeur du CNRS", total=6)
        self.releve(h, dict(yt, etat="Playing", pos=1))
        status = self.releve(h, dict(yt, etat="Playing", pos=4))
        self.assertEqual(status["seance"]["nature"], "ailleurs")
        self.assertEqual(status["seance_conservee"]["episode"], 5)

    def test_g_retour_au_meme_episode_carte_disparait(self):
        m = self.m
        m.oublier_fin_seance()
        d = m.lancement_definir("tv", 108978, 1, 5, "seance", meta=dict(self.S1E5)); d["mode"] = "reel"
        h = self.chaine_complete()
        self.reacher_identifie(h)
        self.assertIsNotNone(self.releve(h, self.reacher(pos=2463, etat="Paused", cmd=self.FERME))["seance_conservee"])
        status = self.releve(h, self.reacher(titre="", pos=2465), 5)
        self.assertIsNone(status["seance_conservee"], "Même flux, même épisode : la séance reprend")
        self.assertTrue(status["en_cours"])

    def test_h_lecture_manuelle_sans_seance_aucune_fausse_carte(self):
        m = self.m; m.oublier_lancement(raison="test")
        h = self.chaine_complete()
        self.reacher_identifie(h)
        self.assertIsNone(self.releve(h, self.reacher(pos=2463, etat="Paused", cmd=self.FERME))["seance_conservee"])
        yt = dict(self.YOUTUBE, media="Video", total=6)
        self.releve(h, dict(yt, etat="Playing", pos=1))
        self.assertIsNone(self.releve(h, dict(yt, etat="Playing", pos=4))["seance_conservee"])

    def test_i_ancien_youtube_puis_reacher_puis_accueil(self):
        m = self.m; m.oublier_lancement(raison="test")
        h = self.chaine_complete()
        yt = dict(self.YOUTUBE, media="Video", total=7340)
        self.releve(h, dict(yt, etat="Playing", pos=690)); self.releve(h, dict(yt, etat="Playing", pos=693))
        self.releve(h, dict(yt, etat="Paused", pos=699))
        self.reacher_identifie(h)
        self.assertEqual(self.releve(h, self.reacher(pos=2463, etat="Paused", cmd=self.FERME))["seance"]["nature"], "vide")
        self.assertEqual(self.releve(h, dict(yt, etat="Paused", pos=699))["seance"]["nature"], "vide")

    def test_i_bis_reacher_en_pause_depasse_par_une_autre_lecture(self):
        m = self.m; m.oublier_lancement(raison="test")
        h = self.chaine_complete()
        self.reacher_identifie(h)
        yt = dict(self.YOUTUBE, media="Video", total=300)
        self.releve(h, dict(yt, etat="Playing", pos=1)); self.releve(h, dict(yt, etat="Playing", pos=4))
        status = self.releve(h, self.reacher(pos=2463, etat="Paused"))
        self.assertEqual(status["seance"]["nature"], "vide", "Pause Infuse antérieure à la lecture YouTube : pas la lecture actuelle")

    def test_j_lecture_en_cours_seulement_si_jamais_identifiee(self):
        m = self.m; m.oublier_lancement(raison="test")
        h = self.chaine_complete()
        status = self.releve(h, self.reacher(titre="", pos=100))
        self.assertIsNone(status["lecture"]["type"])
        self.assertIsNone(status["lecture"]["meta"])

    def test_lecture_non_identifiee_n_est_jamais_un_film(self):
        m = self.m
        p = {"titre": "Cuillère en argent", "etat": "Playing", "app": "com.firecore.infuse", "media": "Video",
             "pos": 900, "total": 2800, "lu_a": m.time.time()}
        m.cache_film.update(t=0, data=None)
        with patch.object(m, "lire_playing", Mock(return_value=p)), patch.object(m, "meta_titre_rapide", Mock(return_value=(None, True))), \
                patch.object(m, "film_choisi", Mock(return_value=None)):
            data = self.film_original()
        self.assertTrue(data["actif"])
        self.assertIsNone(data["type"], "Type inconnu tant que TMDB n'a pas répondu, jamais « movie » par défaut")
        self.assertIsNone(data["serie"])
        m.cache_film.update(t=0, data=None)
        with patch.object(m, "lire_playing", Mock(return_value=dict(p, serie_nom="Reacher", saison_n=1, episode_n=3))), \
                patch.object(m, "meta_titre_rapide", Mock(return_value=(None, True))), patch.object(m, "film_choisi", Mock(return_value=None)):
            data = self.film_original()
        self.assertEqual(data["type"], "tv")
        self.assertEqual((data["serie"]["saison"], data["serie"]["episode"]), (1, 3))

    def test_fermer_la_seance_conservee(self):
        m = self.m
        self.seance_interrompue()
        self.assertTrue(m.lancement_fermer()["ok"])
        self.assertIsNone(m.LANCEMENT["d"])
        self.assertIsNone(m.lire_memoire_seance())
        self.assertIsNone(m.status()["seance_conservee"])
        m.atv.assert_not_called()

    def test_serie_a_son_propre_scenario(self):
        m = self.m
        meta = {"type": "tv", "id": 7, "titre": "Reacher", "saison": 1, "episode": 2}
        d = m.lancement_definir("tv", 7, 1, 2, "reprise", meta=meta)
        m.etat.update(type=None, mode="reel", lignes=[(1, "Film : Reacher"), (2, "Générique attendu vers 3000s sur 3300s (repli)")])
        with patch.object(m, "en_cours", return_value=True):
            status = m.status()
        self.assertEqual(status["type"], "serie")
        self.assertEqual([e["cle"] for e in status["etapes"]], ["preparation", "film", "generique"])
        self.assertEqual([e["nom"] for e in status["etapes"]], ["Préparation", "Épisode", "Générique"])
        self.assertEqual(status["etapes"][0]["detail"], "Épisode déjà lancé")
        self.assertIs(m.LANCEMENT["d"], d)

    def test_piloter_une_serie_identifiee_prend_le_scenario_serie(self):
        m = self.m
        meta = {"type": "tv", "id": 7, "titre": "Reacher", "saison": 1, "episode": 2}
        m.film.return_value = {"actif": True, "etat": "Playing", "meta": meta, "type": None}
        self.assertEqual(m.piloter_film("reel")["type"], "serie")
        self.assertEqual(m.etat["type"], "serie")

    def test_fin_au_generique_termine_marque_vu_et_laisse_la_carte_de_fin(self):
        m = self.m
        m.oublier_fin_seance()
        d = m.lancement_definir("movie", 1, None, None, "seance", meta=META)
        d["mode"] = "reel"
        with patch.object(m, "deja_vu", return_value=False), patch.object(m, "suivi_vu") as vu:
            m.ligne("Fin de séance au générique")
            m.ligne("Séance terminée")
        vu.assert_called_once_with(type="movie", id=1, saison=0, episode=0, vu=1)
        self.assertIsNone(m.LANCEMENT["d"])
        self.assertIsNone(m.lire_memoire_seance())
        self.p = {"etat": "Idle", "lu_a": m.time.time()}
        m.film.return_value = {"actif": False}
        status = m.status()
        self.assertEqual(status["fin_seance"]["id"], 1)
        self.assertIn("favori", status["fin_seance"])
        self.assertIsNone(status["seance_conservee"])
        self.assertFalse(m.conflit_depart("movie", 2), "La carte de fin ne bloque aucune nouvelle séance")
        m.lancement_definir("movie", 2, None, None, "lire", meta=dict(META, id=2))
        self.assertEqual(m.fin_seance_courante()["id"], 1, "Une nouvelle lecture ne ferme pas implicitement la carte de fin")

    def test_mode_test_ne_marque_pas_vu(self):
        m = self.m
        d = m.lancement_definir("movie", 1, None, None, "seance", meta=META)
        d["mode"] = "test"
        with patch.object(m, "suivi_vu") as vu:
            m.ligne("Séance terminée")
        vu.assert_not_called()
        self.assertEqual(m.fin_seance_courante()["mode"], "test")

    def test_fermer_la_carte_de_fin_et_persistance(self):
        m = self.m
        d = m.lancement_definir("movie", 1, None, None, "seance", meta=META)
        d["mode"] = "test"
        m.ligne("Séance terminée")
        self.assertTrue(m.seance_fin_fermer()["ok"])
        self.assertTrue(m.fin_seance_courante()["fermee"])
        d = m.lancement_definir("movie", 1, None, None, "seance", meta=META)
        d["mode"] = "test"
        m.ligne("Séance terminée")
        with patch.object(m.time, "time", return_value=m.time.time() + 30 * 86400):
            self.assertIsNotNone(m.fin_seance_courante(), "La fermeture explicite, pas une expiration, efface l'encart")

    def test_seance_bloquee_apres_le_generique_se_termine(self):
        m = self.m
        m.oublier_fin_seance()
        d = m.lancement_definir("movie", 1, None, None, "seance", meta=META)
        d.update(mode="reel", scenario={"credits": {"etat": "fait"}, "lines": []})
        self.p = {"etat": "Idle", "lu_a": m.time.time()}
        with patch.object(m, "deja_vu", return_value=True):
            self.assertIsNone(m.lancement_courant())
        self.assertIsNone(m.LANCEMENT["d"])
        self.assertEqual(m.fin_seance_courante()["id"], 1)

    def test_interruption_avant_le_generique_reste_une_seance_conservee(self):
        m = self.m
        d = self.seance_interrompue()
        d["scenario"] = {"credits": {"etat": "attente"}, "lines": []}
        m.film.return_value = {"actif": False}
        self.assertIsNotNone(m.lancement_courant())
        self.assertIsNotNone(m.status()["seance_conservee"])
        self.assertIsNone(m.fin_seance_courante())

    def test_suite_de_saga_prioritaire_et_jamais_un_volet_precedent(self):
        m = self.m
        parties = {10: {"id": 10, "title": "Volet 1", "release_date": "2001-01-01"},
                   1: {"id": 1, "title": "Volet 2", "release_date": "2004-01-01"},
                   12: {"id": 12, "title": "Volet 3", "release_date": "2008-01-01"},
                   13: {"id": 13, "title": "Volet 4", "release_date": "2999-01-01"}}
        fin = {"type": "movie", "id": 1, "saison": None, "episode": None}
        with patch.object(m, "details", return_value={"belongs_to_collection": {"id": 99}}), \
                patch.object(m, "_collection_membres", return_value=("Saga", parties)), \
                patch.object(m.bibliotheque, "ajoutes", return_value=([("movie", 10), ("movie", 20), ("tv", 1), ("movie", 1)], False)), \
                patch.object(m, "tmdb_get", return_value={"results": [{"id": 1, "media_type": "tv", "name": "Série"}]}), \
                patch.object(m, "vus_source", return_value={"films": set(), "series": set()}), \
                patch.object(m, "carte_par_id", side_effect=lambda t, i: {"type": t, "id": i, "titre": str(i)}):
            res = m.suggestions_apres(fin)
        self.assertEqual((res[0]["id"], res[0]["suite"]), (12, True), "La suite sortie passe en premier, pas le volet à venir")
        ids = [(x["type"], x["id"]) for x in res]
        self.assertNotIn(("movie", 10), ids, "Jamais un volet précédent")
        self.assertNotIn(("movie", 1), ids, "Ni le film qui vient d'être vu")
        self.assertIn(("tv", 1), ids, "Une série de même identifiant n'est pas confondue avec le film")
        self.assertLessEqual(len(res), m.SUGGESTIONS_MAX)

    def test_suite_d_une_serie_est_l_episode_suivant(self):
        m = self.m
        fin = {"type": "tv", "id": 7, "saison": 1, "episode": 8}
        with patch.object(m, "episodes_diffusees", return_value=[(1, 7), (1, 8), (2, 1), (2, 2)]), \
                patch.object(m, "carte_par_id", return_value={"type": "tv", "id": 7, "titre": "Reacher"}):
            suite, _ = m.suite_directe(fin)
        self.assertEqual((suite["saison"], suite["episode"]), (2, 1))

    def test_progression_transmission_par_qualite_sans_melange(self):
        m = self.m
        st = {"hd": {"etat": "telechargement", "progres": 0, "source": "radarr", "hashes": ["a" * 40]},
              "uhd": {"etat": "telechargement", "progres": 0, "source": "radarr", "hashes": ["b" * 40]},
              "saisons": {}, "erreur": None}
        infos = {"a" * 40: {"progres": 71.3, "taille": 100, "reste": 29, "restant_s": 131, "vitesse": 9, "etat": "téléchargement",
                            "pairs": 3, "bloque": False, "erreur": ""},
                 "b" * 40: {"progres": 1.5, "taille": 100, "reste": 98, "restant_s": 4860, "vitesse": 1, "etat": "téléchargement",
                            "pairs": 1, "bloque": False, "erreur": ""}}
        with patch.object(m.transmission, "suivi", side_effect=lambda hs: {h: infos[h] for h in hs if h in infos}):
            out = m.statut_avec_transmission(st)
        self.assertEqual((out["hd"]["progres"], out["hd"]["source"]), (71, "transmission"))
        self.assertEqual((out["uhd"]["progres"], out["uhd"]["source"]), (2, "transmission"))
        self.assertEqual(st["hd"]["progres"], 0, "Le statut en cache n'est pas modifié")
        with patch.object(m.transmission, "suivi", return_value=None):
            self.assertEqual(m.statut_avec_transmission(st)["hd"]["source"], "radarr", "Sans Transmission, Radarr reste la base")

    def test_route_telechargement_rend_chaque_qualite_et_relit_la_file(self):
        m = self.m
        m.RELECTURE_FILM.clear()
        st = {"hd": {"etat": "telechargement", "progres": 0, "source": "radarr", "hashes": ["a" * 40]},
              "uhd": {"etat": "recherche", "source": "radarr"}, "saisons": {}, "erreur": None}
        infos = {"a" * 40: {"progres": 71.3, "taille": 100, "reste": 29, "restant_s": 131, "vitesse": 9, "etat": "téléchargement",
                            "pairs": 3, "bloque": False, "erreur": ""}}
        with patch.object(m.bibliotheque, "statut", return_value=st), patch.object(m.bibliotheque, "relire_film") as relire, \
                patch.object(m.transmission, "suivi", return_value=infos):
            r = m.telechargement("movie", 5)
            m.telechargement("movie", 5)
        relire.assert_called_once_with(5, delai=5)
        self.assertEqual(r["source"], "transmission")
        self.assertEqual(r["versions"]["hd"]["progres"], 71)
        self.assertEqual(r["versions"]["uhd"]["etat"], "recherche")

    def test_owned_session_survives_remote_pause_seek_and_absence(self):
        m = self.m
        d = m.lancement_definir("movie", 1, None, None, "seance", meta=META)
        for state, position in (("Paused", 100), ("Playing", 160), ("Paused", 90), ("Idle", 0)):
            self.p = dict(P, etat=state, pos=position, lu_a=m.time.time())
            m.lancement_courant()
            self.assertIs(m.LANCEMENT["d"], d)
            # Lot 2.6.91 : conflit tant que le média de la séance joue ; sinon un départ la met en suspens.
            self.assertEqual(m.conflit_depart("movie", 1), state != "Idle")
            self.assertEqual(m.conflit_depart("movie", 2), state != "Idle")
        m.film.return_value = {"actif": False}
        status = m.status()
        # Lot 2.6.81 : la séance reste possédée et protégée, mais n'est plus « en cours » quand son film ne joue pas :
        # elle est exposée à part (seance_conservee) et la vue décrit la lecture réelle (ici, rien).
        self.assertFalse(status["en_cours"])
        self.assertEqual(status["seance_conservee"]["id"], 1)
        self.assertEqual(status["seance"]["nature"], "vide")
        self.assertIsNone(status["fin_seance"])

    def test_status_ne_attend_pas_un_depart_en_cours(self):
        m = self.m
        m.lancement_definir("movie", 1, None, None, "seance", meta=META)
        self.p = dict(P, pos=100, lu_a=m.time.time())
        tenu, liberer = threading.Event(), threading.Event()

        def depart_long():
            with m.VERROU_DEPART:
                tenu.set()
                liberer.wait(5)
        fil = threading.Thread(target=depart_long)
        fil.start()
        try:
            tenu.wait(5)
            debut = m.time.time()
            self.assertIsNotNone(m.lancement_courant())
            self.assertLess(m.time.time() - debut, 1)
            m.relais.demarrer.assert_not_called()
        finally:
            liberer.set()
            fil.join()
        m.lancement_courant()
        m.relais.demarrer.assert_called_once()

    def test_same_media_reattaches_saved_scenario_without_relaunch(self):
        m = self.m
        d = m.lancement_definir("movie", 1, None, None, "seance", meta=META)
        d.update(mode="reel", scenario={"threshold": 150, "intermission": "annulee",
                 "options": {"ba": True, "entracte": True}, "credits": {"etat": "attente"}})
        m.memoriser_lancement(d, force=True)
        m.LANCEMENT["d"] = m.lire_memoire_seance()
        restored = m.LANCEMENT["d"]
        self.p = dict(P, pos=100, lu_a=m.time.time())
        m.lancement_courant()
        self.assertIs(m.LANCEMENT["d"], restored)
        ctx = m.relais.demarrer.call_args.args[0]
        self.assertEqual(ctx["entracte_prevu"], 150)
        self.assertTrue(ctx["intermission_skipped"])
        self.assertFalse(ctx["entracte_deja"])
        m.atv.assert_not_called()

    def test_different_media_never_reattaches_and_keeps_owner(self):
        m = self.m
        d = m.lancement_definir("movie", 1, None, None, "seance", meta=META)
        self.p = dict(P, titre="Autre film", lu_a=m.time.time())
        m.lancement_courant()
        m.relais.demarrer.assert_not_called()
        self.assertIs(m.LANCEMENT["d"], d)

    def test_explicit_stop_during_absence_abandons_and_allows_replacement(self):
        m = self.m
        old = m.lancement_definir("movie", 1, None, None, "seance", meta=META)
        self.p = {"etat": "Idle", "lu_a": m.time.time()}
        self.assertFalse(m.conflit_depart("movie", 2), "Lot 2.6.91 : elle partirait en suspens, plus de conflit")
        with patch.object(m, "lire_film", return_value={"actif": False}):
            self.assertTrue(m.stop(lumieres=0, film=1)["ok"])
        self.assertIsNone(m.lire_memoire_seance())
        self.assertFalse(m.conflit_depart("movie", 2))
        new = m.lancement_definir("movie", 2, None, None, "seance", meta=dict(META, id=2))
        m.reattach_session(old, dict(P, lu_a=m.time.time()))
        self.assertIs(m.LANCEMENT["d"], new)
        m.relais.demarrer.assert_not_called()

    def test_direct_playback_does_not_gain_automatic_ownership(self):
        m = self.m
        m.lancement_definir("movie", 1, None, None, "lire", meta=META)
        self.p = dict(P, lu_a=m.time.time())
        m.lancement_courant()
        m.relais.demarrer.assert_not_called()

    def test_rearmed_intermission_is_persisted_not_consumed(self):
        m = self.m
        m.lancement_definir("movie", 1, None, None, "seance", meta=META)
        for line in ("Entracte prévue à 150s (50 %)",
                     "Avance manuelle au delà de l'entracte prévue (160s) : entracte annulée",
                     "Entracte réarmée après retour avant le seuil"):
            m.ligne(line)
        saved = m.lire_memoire_seance()
        self.assertFalse(saved["entracte_faite"])
        self.assertEqual(saved["scenario"]["threshold"], 150)
        self.assertEqual(saved["scenario"]["intermission"], "attente")
        m.ligne("ENTRACTE : écran entracte pour 60 secondes")
        self.assertTrue(m.lire_memoire_seance()["entracte_faite"])

    def test_decompte_trailer_suit_la_marge_configuree(self):
        m = self.m
        lignes = [(100, "Trailer 1/1 : Exemple"), (100, "  Durée attendue : 100"),
                  (100, "  Playing | Exemple | 70/100s | trailer")]
        for marge in (5, 26):
            with patch.object(m, "charger", return_value={"trailer_marge_fin_s": marge}), \
                    patch.object(m.time, "time", return_value=102):
                self.assertEqual(m.analyser(lignes, "reel")["trailers"][0]["reste"], 100 - marge - 72)

    def test_entracte_annulee_reste_sautee_et_absente_du_relais(self):
        m = self.m
        lignes = [(100, "Film : Film de test"), (100, "Entracte prévue à 150s (50 %)"),
                  (110, "Avance manuelle au delà de l'entracte prévue (160s) : entracte annulée")]
        detail = m.analyser(lignes, "reel")
        self.assertEqual(detail["entracte"]["etat"], "annulee")
        self.assertEqual(detail["entracte"]["prevu_s"], 150)
        etapes = m.etapes_seance(lignes, detail, False)
        self.assertEqual(next(e for e in etapes if e["cle"] == "entracte")["etat"], "saute")
        with patch.object(m, "en_cours", return_value=True), \
                patch.object(m, "processus_vivant", return_value=True), \
                patch.object(m, "lignes_courantes", return_value=lignes), \
                patch.object(m, "tuer_seance"), patch.object(m.time, "sleep"), \
                patch.object(m.relais, "demarrer", return_value=True) as demarrer:
            self.assertTrue(m.entracte_manuel(duree=60)["ok"])
        ctx = demarrer.call_args.args[0]
        self.assertFalse(ctx["entracte_deja"])
        self.assertEqual(ctx["entracte_prevu"], 150)

    def test_entracte_annulee_memorisee_pour_la_reprise(self):
        m = self.m
        m.lancement_definir("movie", 1, None, None, "seance", meta=META)
        self.p["lu_a"] = m.time.time()
        m.etat["lignes"] = [(100, "Avance manuelle au delà de l'entracte prévue (160s) : entracte annulée")]
        with patch.object(m, "en_cours", return_value=True):
            m.lancement_courant()
        self.assertFalse(m.lire_memoire_seance()["entracte_faite"])

    def test_prendre_la_main_persiste_options_et_change_le_statut(self):
        m = self.m
        def demarrer(ctx):
            m.relais.actif = True
            self.ctx = ctx
            return True
        with patch.object(m.relais, "demarrer", side_effect=demarrer):
            self.assertTrue(m.reprendre(entracte="0")["ok"])
        self.assertTrue(self.ctx["annuler_classique"])
        self.assertFalse(m.etat["options"]["entracte"])
        self.assertFalse(m.etat["options"]["ba"])
        self.assertEqual(m.status()["seance"]["nature"], "pilotee")
        self.assertEqual(m.lire_memoire_seance()["relance"]["entracte"], "0")

    def test_depart_refuse_lecture_directe_et_seance_memorisee(self):
        m = self.m
        self.assertEqual(m.lancer("movie", 2).status_code, 409)
        self.assertEqual(m.demarrer_seance(tmdb_type="movie", tmdb_id=2)["code"], 409)
        m.atv.assert_not_called()
        m.LANCEMENT["d"] = {"confirme": True, "id": 1, "type": "movie", "t": 1}
        self.p = {"etat": "Idle"}
        self.assertTrue(m.conflit_depart("movie", 2))

    def test_remplacement_refuse_une_confirmation_devenue_obsolete(self):
        m = self.m
        ancien = m.occupation_courante()
        self.p = dict(P, titre="Nouvelle lecture")
        self.assertEqual(m.stop(film=1, attendu=ancien).status_code, 409)
        m.atv.assert_not_called()

    def test_fin_naturelle_efface_la_memoire_persistante(self):
        m = self.m
        d = m.lancement_definir("movie", 1, None, None, "seance", meta=META)
        self.assertIsNotNone(m.lire_memoire_seance())
        m.ligne("Fin naturelle du film")
        self.assertIsNone(m.LANCEMENT["d"])
        self.assertIsNone(m.lire_memoire_seance())

    def test_dernier_point_et_redemarrage_ne_resuscitent_pas_la_seance(self):
        m = self.m
        d = m.lancement_definir("movie", 1, None, None, "seance", meta=META)
        self.p = dict(P, pos=7195, lu_a=m.time.time())
        m.lancement_courant()
        m.memoriser_lancement(d, force=True)
        m.LANCEMENT["d"] = m.lire_memoire_seance()
        horloge = Horloge()
        with patch.object(m.etat_seance, "time", horloge):
            for i in range(3):
                self.p = {"etat": "Idle", "lu_a": m.time.time() + i / 1000}
                horloge.avancer(m.etat_seance.ABSENCE_TOLERANCE_S)
                resultat = m.lancement_courant()
        self.assertIsNone(resultat)
        self.assertIsNone(m.lire_memoire_seance())

    def test_episode_different_exige_confirmation_meme_seance_suspendue(self):
        m = self.m
        m.LANCEMENT["d"] = {"confirme": True, "id": 1, "type": "tv", "saison": 1, "episode": 1}
        self.p = {"etat": "Idle"}
        self.assertTrue(m.conflit_depart("tv", 1, 1, 2))
        self.assertFalse(m.conflit_depart("tv", 1, 1, 1))

    def test_conflit_depart_ignore_ailleurs_seulement_pour_un_depart_sans_personne(self):
        m = self.m
        # Une séance programmée confirmée par « Je suis là » n'a personne pour confirmer un remplacement : une
        # lecture ailleurs (YouTube ici) ne doit plus bloquer silencieusement ce départ.
        self.p = dict(P, app="com.google.ios.youtube", etat="Playing", lu_a=m.time.time())
        self.assertTrue(m.conflit_depart("movie", 2))
        self.assertFalse(m.conflit_depart("movie", 2, ignorer_ailleurs=True))
        # Une vraie lecture Infuse reste un conflit, même pour un départ programmé.
        self.p = dict(P, app=m.INFUSE_ID, etat="Playing", lu_a=m.time.time())
        self.assertTrue(m.conflit_depart("movie", 2, ignorer_ailleurs=True))

    def test_lancer_planifiee_ignore_une_lecture_ailleurs(self):
        m = self.m
        with patch.object(m, "demarrer_seance", return_value={"ok": True}) as dep:
            ok, msg = m.lancer_planifiee({"mode": "reel", "type": "movie", "id": 5, "saison": 0, "episode": 0,
                                          "bandes_annonces": None, "entracte": None})
        self.assertTrue(ok)
        dep.assert_called_once_with("reel", "auto", "movie", 5, 0, 0, None, None, ignorer_ailleurs=True)

    def test_ancienne_bande_annonce_ne_revient_pas_sur_la_home(self):
        m = self.m
        m.BA_LANCEE["d"] = {"t": m.time.time(), "vu": m.time.time()}
        for f in ({"app": "com.google.ios.youtube", "connecte": True}, {"actif": True, "app": "com.firecore.infuse"}):
            m.cache_film["data"] = f
            self.assertIsNone(m.ba_courante())

    def test_ancienne_observation_youtube_est_rejetee(self):
        m = self.m
        self.assertIsNone(m.contenu_ailleurs(dict(P, app="com.google.ios.youtube", lu_a=0)))

    def test_fin_publiee_dans_etat_arrete_nettoie_la_memoire(self):
        m = self.m
        m.lancement_definir("movie", 1, None, None, "lire", meta=META)
        m.lancement_courant()
        self.p = dict(P, etat="Stopped", pos=7200, lu_a=m.time.time())
        self.assertIsNone(m.lancement_courant())
        self.assertIsNone(m.lire_memoire_seance())

    def test_reseau_absent_ne_prouve_pas_une_interruption(self):
        m = self.m
        d = m.lancement_definir("movie", 1, None, None, "lire", meta=META)
        self.p["lu_a"] = m.time.time()
        m.lancement_courant()
        self.p = None
        r = m.lancement_courant()
        self.assertTrue(r["incertaine"])
        self.assertFalse(r["suspendue"])
        horloge = Horloge()
        with patch.object(m.etat_seance, "time", horloge):
            for i in range(3):
                self.p = {"etat": "Idle", "lu_a": m.time.time() + i / 1000}
                horloge.avancer(m.etat_seance.ABSENCE_TOLERANCE_S)
                r = m.lancement_courant()
        self.assertTrue(r["suspendue"])
        self.assertFalse(r["incertaine"])
        m.LANCEMENT["d"] = m.lire_memoire_seance()
        self.p = None
        self.assertTrue(m.lancement_courant()["suspendue"])
        self.assertEqual(d["position"], 100)

    def test_ancienne_memoire_sans_preuve_ne_devient_pas_a_reprendre(self):
        m = self.m
        d = m.lancement_definir("movie", 1, None, None, "lire", meta=META)
        d.update(position=100, total=7200, derniere_lecture=m.time.time() - 86400,
                 suspendue=True)
        m.memoriser_lancement(d, force=True)
        m.LANCEMENT["d"] = m.lire_memoire_seance()
        for i in range(4):
            self.p = {"etat": "Idle", "lu_a": m.time.time() + i / 1000}
            r = m.lancement_courant()
        self.assertFalse(r["suspendue"])
        self.assertTrue(r["incertaine"])

    def test_fin_d_un_autre_media_ne_termine_pas_la_seance(self):
        m = self.m
        m.lancement_definir("movie", 1, None, None, "lire", meta=META)
        self.p = dict(P, etat="Stopped", titre="Autre film", pos=7200, lu_a=m.time.time())
        self.assertIsNotNone(m.lancement_courant())
        self.assertIsNotNone(m.lire_memoire_seance())

    def test_observation_identique_ne_compte_pas_trois_absences(self):
        m = self.m
        m.lancement_definir("movie", 1, None, None, "lire", meta=META)
        m.lancement_courant()
        self.p = {"etat": "Idle", "lu_a": m.time.time()}
        for _ in range(4):
            self.assertFalse(m.lancement_courant()["suspendue"])

    def test_youtube_type_inconnu_et_disparition_des_metadonnees(self):
        m = self.m
        self.p = dict(P, app="com.google.ios.youtube", media="Unknown", lu_a=m.time.time(),
                      content_identifier="dQw4w9WgXcQ", commandes={"play_pause": True, "back": False})
        data = self.film_original()
        self.assertEqual(data["ailleurs"]["titre"], P["titre"])
        self.assertEqual(data["ailleurs"]["url"], "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertFalse(data["ailleurs"]["commandes"]["back"])
        self.p["hash"] = "contenu"
        h = m.atvlive.cle_image(self.p)
        with patch.object(m.atvlive.LIVE, "jaquette", {"h": h, "octets": b"image"}), \
                patch.object(m.atvlive.LIVE, "lire", side_effect=lambda: self.p):
            self.assertEqual(m.appletv_jaquette(h).status_code, 200)
            self.assertEqual(m.appletv_jaquette("ancienne").status_code, 404)
            self.p["etat"] = "Idle"
            self.assertEqual(m.appletv_jaquette(h).status_code, 404)
            self.p["etat"] = "Playing"
        self.p["commandes"] = {"back": True, "forward": True}
        for cmd, action in (("back", "skip_backward=10"), ("forward", "skip_forward=10")):
            self.assertTrue(m.telecommande(cmd)["ok"])
            m.atv.assert_called_with(action, delai=10)
        for variation in ({"etat": "Idle"}, {"etat": "Stopped"}, {"veille": True},
                          {"app_active": "com.firecore.infuse"}, {"lu_a": 0}):
            self.assertIsNone(m.contenu_ailleurs(dict(self.p, **variation)))
        self.assertIsNotNone(m.contenu_ailleurs(dict(self.p, titre="", etat="Paused")))

    def test_infuse_derniere_app_seule_ne_desactive_pas_ouvrir(self):
        m = self.m
        for variante, attendu in [({"etat": "Idle"}, False), ({"etat": "Playing"}, True),
                                  ({"etat": "Idle", "app_active": m.INFUSE_ID}, True),
                                  ({"veille": True}, False)]:
            self.p = dict(P, lu_a=m.time.time(), **variante)
            m.cache_film["t"] = 0
            self.assertEqual(self.film_original()["infuse_ouvert"], attendu)

    def test_commandes_service_connexion_live_et_aucun_rejeu_sur_erreur(self):
        m = self.m
        for ok in (True, False):
            with patch.object(m.atvlive.LIVE, "commander", return_value={"ok": ok}) as commande:
                self.assertEqual(m.telecommande("play_pause", "jeton")["ok"], ok)
                commande.assert_called_once_with("play_pause", "jeton")
        m.atv.assert_not_called()

    def test_commandes_service_refuse_concurrence_et_media_change(self):
        m = self.m
        with m.VERROU_LECTEUR:
            self.assertEqual(m.telecommande("back").status_code, 409)
        with patch.object(m.atvlive.LIVE, "commander", return_value=None):
            self.assertEqual(m.telecommande("back", "autre media").status_code, 409)
            self.p["etat"] = "Idle"
            self.assertEqual(m.telecommande("play_pause").status_code, 409)
        m.atv.assert_not_called()

    def test_film_direct_sans_duree_ou_court_garde_les_commandes(self):
        m = self.m
        with patch.object(m, "meta_titre_rapide", return_value=(None, None)):
            for duree in (0, 300, 7200):
                for etat in ("Playing", "Paused"):
                    self.p = dict(P, total=duree, etat=etat, lu_a=m.time.time())
                    m.cache_film["t"] = 0
                    f = self.film_original()
                    self.assertTrue(f["actif"])
                    for detail in ({}, {"entracte": {"etat": "actif"}}, {"entracte": {"etat": "fait"}}):
                        self.assertTrue(m.etat_seance.vue(f, None, True, False, detail)["commandes"])

    def test_identification_rapide_ignore_les_variations_de_duree_observee(self):
        m = self.m
        info = {"type": "movie", "nom": "Un Film Unique 2.6.63", "saison": None, "episode": None}
        cle = ("titre", m.norm(info["nom"]), None, None)
        m.cache_tmdb.pop(cle, None)
        m.cache_neg.pop(cle, None)
        resultats = {"results": [{"id": 999, "title": info["nom"], "original_title": info["nom"],
                                  "release_date": "", "popularity": 10, "vote_count": 5}]}
        with patch.object(m, "tmdb_get", return_value=resultats) as recherche:
            self.assertEqual(m.meta_par_titre(info, 0), META)
            self.assertEqual(recherche.call_count, 1)
            # La durée observée a changé (Infuse ne la connaît pas tout de suite) : la recherche déjà résolue
            # ne doit pas repartir pour autant, sinon l'affichage attend ce changement au lieu du titre détecté.
            self.assertEqual(m.meta_par_titre(info, 7200), META)
            self.assertEqual(recherche.call_count, 1)

    def test_recherche_infructueuse_reste_negative_malgre_la_duree_qui_change(self):
        m = self.m
        info = {"type": "movie", "nom": "Un Echec Bien Distinctif 2.6.63", "saison": None, "episode": None}
        cle = ("titre", m.norm(info["nom"]), None, None)
        m.cache_tmdb.pop(cle, None)
        m.cache_neg.pop(cle, None)
        with patch.object(m, "tmdb_get", return_value={"results": []}) as recherche:
            self.assertIsNone(m.meta_par_titre(info, 0))
            self.assertEqual(recherche.call_count, 1)
            self.assertIsNone(m.meta_par_titre(info, 5400))
            self.assertEqual(recherche.call_count, 1)

    def test_lecture_externe_series_ambigue_reste_sans_fausse_identification(self):
        m = self.m
        info = {"type": "tv", "nom": "Une Série Ambigue Test 2.7.3", "saison": 1, "episode": 2}
        cle = ("titre", m.norm(info["nom"]), 1, 2)
        m.cache_tmdb.pop(cle, None)
        m.cache_neg.pop(cle, None)
        resultats = {"results": [
            {"id": 77301, "name": info["nom"], "original_name": info["nom"], "first_air_date": "2025-01-01", "popularity": 10},
            {"id": 77302, "name": info["nom"], "original_name": info["nom"], "first_air_date": "2025-01-01", "popularity": 10},
        ]}
        with patch.object(m, "tmdb_get", return_value=resultats) as recherche, patch.object(m, "meta_par_id") as details:
            self.assertIsNone(m.meta_par_titre(info, 0))
            self.assertEqual(recherche.call_count, 1)
            details.assert_not_called()
        m.cache_neg.pop(cle, None)

    def test_meta_titre_rapide_arrive_au_prochain_passage_sans_relancer_sur_la_duree(self):
        m = self.m
        info = {"type": "movie", "nom": "Un Film Asynchrone 2.6.63", "saison": None, "episode": None}
        cle = ("titre", m.norm(info["nom"]), None, None)
        m.cache_tmdb.pop(cle, None)
        m.cache_neg.pop(cle, None)
        m.ident_en_cours.discard(cle)
        resultats = {"results": [{"id": 4242, "title": info["nom"], "original_title": info["nom"],
                                  "release_date": "", "popularity": 10, "vote_count": 5}]}
        with patch.object(m, "tmdb_get", return_value=resultats) as recherche:
            premier, en_cours = m.meta_titre_rapide(info, 0)
            self.assertIsNone(premier)
            self.assertTrue(en_cours)
            fin = m.time.time() + 2
            while cle in m.ident_en_cours and m.time.time() < fin:
                m.time.sleep(0.01)
            self.assertNotIn(cle, m.ident_en_cours)
            # Le passage suivant arrive avec une durée différente (pyatv l'a maintenant précisée) : le résultat
            # déjà trouvé doit rester, pas une nouvelle recherche qui retarderait encore l'affichage.
            deuxieme, en_cours2 = m.meta_titre_rapide(info, 7200)
            self.assertEqual(deuxieme, META)
            self.assertFalse(en_cours2)
            self.assertEqual(recherche.call_count, 1)

    def test_quitter_film_deja_dans_infuse_utilise_la_connexion_continue(self):
        m = self.m
        self.p = dict(P, app="com.firecore.infuse", etat="Playing", lu_a=m.time.time())
        with patch.object(m.atvlive.LIVE, "menu", return_value=True) as menu, \
                patch.object(m.atvlive.LIVE, "ouvrir_app", return_value=True) as ouvrir:
            m.quitter_film()
            menu.assert_called_once()
            ouvrir.assert_not_called()     # déjà dans Infuse : pas besoin de changer d'app
        m.atv.assert_not_called()

    def test_quitter_film_change_d_app_puis_repli_cli_si_la_connexion_echoue(self):
        m = self.m
        self.p = dict(P, app="com.google.ios.youtube", etat="Playing", lu_a=m.time.time())
        with patch.object(m.atvlive.LIVE, "menu", return_value=False), \
                patch.object(m.atvlive.LIVE, "ouvrir_app", return_value=False), \
                patch.object(m.time, "sleep"):
            m.quitter_film()
        m.atv.assert_any_call("launch_app=" + m.INFUSE_ID, delai=15)
        m.atv.assert_any_call("menu", delai=10)

    def test_quitter_film_suspend_ailleurs_brievement_apres_coup(self):
        # Point 90 : après avoir fait quitter un film (fin de séance, Arrêter), on sait que rien ne joue plus tout
        # de suite ; pyatv peut republier un instant une ancienne lecture (YouTube...) qui n'a plus rien à voir.
        m = self.m
        self.p = dict(P, app="com.firecore.infuse", etat="Playing", lu_a=m.time.time())
        with patch.object(m.atvlive.LIVE, "menu", return_value=True):
            m.quitter_film()
        ancienne = dict(P, app="com.google.ios.youtube", titre="Vieille vidéo", etat="Paused", lu_a=m.time.time())
        self.assertIsNone(m.contenu_ailleurs(m.atvlive.LIVE.observe(ancienne, "test")))
        m.atvlive.LIVE.observe(dict(ancienne, etat="Playing"), "test")
        active = m.atvlive.LIVE.observe(dict(ancienne, etat="Playing", pos=110), "test")
        self.assertIsNotNone(m.contenu_ailleurs(active))

    def test_stop_ailleurs_pause_rapide_sans_attendre_le_repli_cli(self):
        m = self.m
        self.p = dict(app="com.google.ios.youtube", etat="Playing", titre="Vidéo", media="Video",
                      pos=10, total=120, lu_a=m.time.time())
        with patch.object(m.atvlive.LIVE, "pause", return_value=True) as pause, \
                patch.object(m.atvlive.LIVE, "menu", return_value=True) as menu:
            self.assertTrue(m.stop(film=1)["ok"])
            pause.assert_called_once()
            menu.assert_called_once()
        m.atv.assert_not_called()

    def test_stop_ailleurs_repli_cli_seulement_pour_la_pause(self):
        m = self.m
        self.p = dict(app="com.google.ios.youtube", etat="Playing", titre="Vidéo", media="Video",
                      pos=10, total=120, lu_a=m.time.time())
        with patch.object(m.atvlive.LIVE, "pause", return_value=False), \
                patch.object(m.atvlive.LIVE, "menu", return_value=True) as menu:
            self.assertTrue(m.stop(film=1)["ok"])
            menu.assert_called_once()
        m.atv.assert_called_once_with("pause", delai=10)


    # ---------- Lot 2.6.91 : une séance active, plusieurs séances en suspens ----------
    JASON = {"type": "movie", "id": 2, "titre": "Jason Bourne", "annee": "2016", "duree": 123}
    AVATAR = {"type": "movie", "id": 3, "titre": "Avatar", "annee": "2009", "duree": 162}

    def seance(self, meta, origine="reprise"):
        d = self.m.lancement_definir(meta["type"], meta["id"], meta.get("saison"), meta.get("episode"), origine, meta=dict(meta))
        d["mode"] = "reel"
        return d

    def ids_suspendus(self):
        return [x["id"] if x["type"] == "movie" else (x["id"], x["saison"], x["episode"]) for x in self.m.SUSPENDUES["l"]]

    # ---------- Lot 2.6.92 : « Suspendre et lancer », transition de remplacement ----------
    def jason_joue(self, pos=2000, etat="Playing", **champs):
        return dict(P, titre="Jason Bourne", etat=etat, pos=pos, lu_a=self.m.time.time() + 1, **champs)

    def suspendre_et_lancer_jason(self):
        m = self.m
        jason = self.seance(self.JASON)
        self.p = self.jason_joue()
        m.film.return_value = {"actif": True, "etat": "Playing", "meta": dict(self.JASON), "type": "movie", "total": 7200}
        with patch.object(m, "en_cours", return_value=True), patch.object(m, "tuer_seance"), patch.object(m, "quitter_film"):
            self.assertTrue(m.stop(lumieres=0, film=1, suspendre=1)["ok"])
        return jason

    def test_lot92_a_observation_residuelle_ne_reprend_pas_la_seance_suspendue(self):
        m = self.m
        jason = self.suspendre_et_lancer_jason()
        # Juste après l'arrêt, Infuse publie encore Jason en lecture (Menu pas encore traité) : c'était le bug du premier essai.
        for pos in (2001, 2002):
            self.p = self.jason_joue(pos)
            status = m.status()
            self.assertIsNone(m.LANCEMENT["d"], "Jason n'est pas repris par une observation résiduelle")
            self.assertFalse(status["en_cours"], "L'interface voit l'ancienne séance arrêtée dès le premier essai")
        m.relais.demarrer.assert_not_called()
        self.assertEqual([x["t"] for x in m.SUSPENDUES["l"]], [jason["t"]])
        ligne = [l for l in m.RATTACHEMENTS["lignes"] if l.get("evenement") == "rattachement ignoré"][-1]
        self.assertIn("observation résiduelle", ligne["raison"])
        # Infuse revient sur la fiche : la transition se termine sur un fait observé, pas sur un délai.
        self.p = {"etat": "Idle", "app": "com.firecore.infuse", "titre": "", "pos": 0, "total": 0, "lu_a": m.time.time() + 1}
        m.film.return_value = {"actif": False}
        m.status()
        self.assertIsNone(m.TRANSITION["cle"])
        # B démarre au premier essai et Jason reste en suspens.
        unabomber = dict(META, id=5, titre="Unabomber")
        m.meta_par_id.return_value = unabomber
        def lien(*args, **kw):
            self.p = dict(P, titre="Unabomber", lu_a=m.time.time() + 5)
            return True, ""
        m.atv.side_effect = lien
        self.assertTrue(m.lancer("movie", 5)["ok"], "B démarre au premier essai")
        self.assertEqual(len([c for c in m.atv.call_args_list if c.args and c.args[0].startswith("launch_app=")]), 1)
        self.assertEqual(m.LANCEMENT["d"]["id"], 5)
        self.assertEqual([x["id"] for x in m.SUSPENDUES["l"]], [2])

    def test_lot92_b_apres_la_transition_le_retour_reel_sur_jason_le_rattache(self):
        m = self.m
        jason = self.suspendre_et_lancer_jason()
        self.p = {"etat": "Idle", "app": "com.firecore.infuse", "titre": "", "pos": 0, "total": 0, "lu_a": m.time.time() + 1}
        m.film.return_value = {"actif": False}
        m.status()
        self.assertIsNone(m.TRANSITION["cle"])
        # Alex relance lui même Jason Bourne dans Infuse : rattachement automatique, comme en 2.6.91.
        self.p = self.jason_joue(2100)
        m.film.return_value = {"actif": True, "etat": "Playing", "meta": dict(self.JASON), "type": "movie", "total": 7200}
        status = m.status()
        self.assertEqual(m.LANCEMENT["d"]["t"], jason["t"])
        self.assertEqual(status["seances_suspendues"], [])
        self.assertTrue(status["en_cours"])

    def test_lot92_c_echec_du_nouveau_lancement_jason_reste_recuperable(self):
        m = self.m
        jason = self.suspendre_et_lancer_jason()
        d = m.lancement_definir("movie", 5, None, None, "seance")
        m._lancement_echec(d, "Infuse n'a pas démarré la lecture.")
        self.assertEqual(m.LANCEMENT["d"]["etat"], "echec")
        self.assertEqual([x["t"] for x in m.SUSPENDUES["l"]], [jason["t"]], "Aucune séance perdue")
        self.assertTrue(m.charger_suspendues()[0]["confirme"])
        self.assertTrue(m.lancement_reprendre(jason["t"], remplacer=1)["ok"])
        self.assertEqual((m.LANCEMENT["d"]["t"], m.SUSPENDUES["l"]), (jason["t"], []), "Le lancement raté n'est pas une séance")

    def test_lot92_d_reprendre_depuis_la_liste_sans_doublon(self):
        m = self.m
        avatar = self.seance(self.AVATAR)
        jason = self.seance(self.JASON)
        self.assertTrue(m.lancement_reprendre(avatar["t"], remplacer=1)["ok"])
        self.assertIsNone(m.TRANSITION["cle"] if m.TRANSITION["cle"] == m.cle_media(avatar) else None)
        self.p = dict(P, titre="Avatar", lu_a=m.time.time() + 1)
        m.film.return_value = {"actif": True, "etat": "Playing", "meta": dict(self.AVATAR), "type": "movie"}
        status = m.status()
        self.assertEqual(m.LANCEMENT["d"]["t"], avatar["t"])
        self.assertEqual([x["t"] for x in status["seances_suspendues"]], [jason["t"]])
        self.assertIsNone(status["seance_conservee"])

    def test_lot91_a_piloter_reacher_met_jason_en_suspens(self):
        m = self.m
        jason = self.seance(self.JASON)
        m.film.return_value = {"actif": True, "etat": "Playing", "meta": dict(self.S1E5), "type": "tv", "total": 2869}
        self.assertEqual(m.reprendre()["type"], "serie")
        self.assertEqual((m.LANCEMENT["d"]["type"], m.LANCEMENT["d"]["episode"]), ("tv", 5))
        self.assertEqual(self.ids_suspendus(), [2], "Jason Bourne n'est plus effacé : il est en suspens")
        self.assertEqual(m.SUSPENDUES["l"][0]["t"], jason["t"], "Même jeton, même séance")
        ligne = [l for l in m.RATTACHEMENTS["lignes"] if l.get("evenement") == "séance mise en suspens"][-1]
        self.assertEqual(ligne["suspendues"], ["movie 2"])

    def test_lot91_b_c_trois_seances_puis_reprendre_avatar(self):
        m = self.m
        avatar = self.seance(self.AVATAR)
        self.seance(dict(self.S1E5))
        self.seance(self.JASON)
        self.assertEqual(self.ids_suspendus(), [3, (108978, 1, 5)])
        # Jason joue : il est la séance active, Avatar et Reacher restent en suspens.
        self.p = dict(P, titre="Jason Bourne", lu_a=m.time.time())
        m.film.return_value = {"actif": True, "etat": "Playing", "meta": dict(self.JASON), "type": "movie"}
        status = m.status()
        self.assertTrue(status["en_cours"])
        self.assertIsNone(status["seance_conservee"])
        self.assertEqual([x["id"] for x in status["seances_suspendues"]], [3, 108978])
        # Reprendre Avatar : Avatar active, Jason et Reacher en suspens.
        self.assertTrue(m.lancement_reprendre(avatar["t"], remplacer=1)["ok"])
        self.assertEqual(m.LANCEMENT["d"]["id"], 3)
        self.assertEqual(m.LANCEMENT["d"]["t"], avatar["t"])
        self.assertEqual(sorted(map(str, self.ids_suspendus())), sorted(map(str, [(108978, 1, 5), 2])))
        m.atv.assert_called_with("launch_app=infuse://movie/3?play", delai=25)

    def test_lot91_d_meme_media_qu_une_seance_en_suspens_la_rattache(self):
        m = self.m
        self.seance(dict(self.S1E5))
        self.seance(self.JASON)
        self.p = self.infuse(self.EP5, pos=1500, total=2869)
        m.film.return_value = {"actif": True, "etat": "Playing", "meta": dict(self.S1E5), "type": "tv", "total": 2869}
        status = m.status()
        self.assertEqual((m.LANCEMENT["d"]["type"], m.LANCEMENT["d"]["episode"]), ("tv", 5), "Reacher rattaché automatiquement")
        self.assertTrue(status["en_cours"])
        self.assertIsNone(status["seance_conservee"])
        self.assertEqual([x["id"] for x in status["seances_suspendues"]], [2], "Jason en suspens, Reacher jamais en double")
        ligne = [l for l in m.RATTACHEMENTS["lignes"] if l.get("evenement") == "séance en suspens reprise"][-1]
        self.assertEqual(ligne["raison"], "média de la séance en suspens de nouveau lu")

    def test_lot91_e_s1e6_lu_ne_rattache_jamais_s1e5_en_suspens(self):
        m = self.m
        self.seance(dict(self.S1E5))
        jason = self.seance(self.JASON)
        self.p = self.infuse("Reacher - S1 \u2219 E6 - Papier", pos=100, total=2861)
        m.film.return_value = {"actif": True, "etat": "Playing", "meta": dict(self.S1E6), "type": "tv", "total": 2861}
        status = m.status()
        self.assertIs(m.LANCEMENT["d"], jason)
        self.assertEqual([(x["id"], x["episode"]) for x in status["seances_suspendues"]], [(108978, 5)])

    def test_lot91_f_lecture_manuelle_jamais_en_suspens(self):
        m = self.m
        self.seance(dict(self.S1E5), origine="lire")      # « Lire » depuis la fiche : pas une séance KamCiné
        self.seance(self.JASON)
        self.assertEqual(self.ids_suspendus(), [], "Une lecture non pilotée ne devient jamais une séance en suspens")
        m.oublier_lancement(raison="test")
        self.p = dict(self.YOUTUBE, media="Video", lu_a=m.time.time())
        m.film.return_value = {"actif": False, "ailleurs": dict(self.YOUTUBE)}
        status = m.status()
        self.assertEqual((status["seance_conservee"], status["seances_suspendues"]), (None, []))

    def test_lot91_g_seance_pilotee_puis_youtube(self):
        m = self.m
        self.seance_interrompue()
        status = m.status()
        self.assertEqual(status["seance"]["nature"], "ailleurs")
        self.assertEqual(status["seance_conservee"]["id"], 1)

    def test_lot91_j_limite_sans_suppression_silencieuse(self):
        m = self.m
        for meta in (self.AVATAR, dict(self.S1E5), dict(self.S1E6), self.JASON):
            self.seance(meta)
        self.assertEqual(len(m.SUSPENDUES["l"]), 3)
        m.film.return_value = {"actif": True, "etat": "Playing", "meta": dict(META, id=9, titre="Nouveau"), "type": "movie", "total": 7200}
        r = m.reprendre()
        self.assertEqual(r.status_code, 409)
        corps = __import__("json").loads(r.body)
        self.assertEqual(corps["code"], "suspens_plein")
        self.assertEqual(len(corps["suspendues"]), 3)
        self.assertEqual((len(m.SUSPENDUES["l"]), m.LANCEMENT["d"]["id"]), (3, 2), "Rien n'est effacé, Jason reste actif")
        self.assertEqual(m.start(tmdb_type="movie", tmdb_id=9).status_code, 409)
        self.assertEqual(m.lancer("movie", 9).status_code, 409)
        # Alex choisit d'en quitter une : l'action passe.
        self.assertTrue(m.lancement_fermer(t=m.SUSPENDUES["l"][0]["t"])["ok"])
        self.assertTrue(m.reprendre()["ok"])
        self.assertEqual(len(m.SUSPENDUES["l"]), 3)
        self.assertEqual(m.LANCEMENT["d"]["id"], 9)

    def test_lot91_k_seances_en_suspens_survivent_au_redemarrage(self):
        m = self.m
        self.seance(self.AVATAR); self.seance(dict(self.S1E5)); self.seance(self.JASON)
        relues = m.charger_suspendues()
        self.assertEqual([(x["type"], x["id"]) for x in relues], [("movie", 3), ("tv", 108978)])
        self.assertTrue(all(x["suspendue"] and x["confirme"] for x in relues))
        self.assertEqual(m.lire_memoire_seance()["id"], 2, "La séance active est toujours dans sa propre mémoire")

    def test_lot91_quitter_une_seance_en_suspens_ne_touche_pas_l_active(self):
        m = self.m
        avatar = self.seance(self.AVATAR)
        jason = self.seance(self.JASON)
        with patch.object(m.relais, "arreter") as arreter:
            self.assertTrue(m.lancement_fermer(t=avatar["t"])["ok"])
        arreter.assert_not_called()
        self.assertEqual((self.ids_suspendus(), m.LANCEMENT["d"]), ([], jason))

    def test_lot91_lire_un_media_en_suspens_reprend_sa_seance(self):
        m = self.m
        reacher = self.seance(dict(self.S1E5))
        self.seance(self.JASON)
        d = m.lancement_definir("tv", 108978, 1, 5, "lire")
        self.assertEqual((d["t"], d["origine"]), (reacher["t"], "reprise"), "La séance Reacher reprend, pas une simple lecture")
        self.assertEqual(self.ids_suspendus(), [2])

    def test_lot91_arreter_une_lecture_non_pilotee_garde_la_seance_en_suspens(self):
        m = self.m
        d = self.seance(self.JASON)
        m.film.return_value = {"actif": True}
        with patch.object(m, "lire_film", return_value={"actif": True}), patch.object(m, "quitter_film"):
            self.assertTrue(m.stop(lumieres=0, film=0)["ok"])
        self.assertIs(m.LANCEMENT["d"], d)

    def test_lot91_remplacer_met_en_suspens(self):
        m = self.m
        d = self.seance(self.JASON)
        with patch.object(m, "en_cours", return_value=True), patch.object(m, "tuer_seance"), patch.object(m, "quitter_film"):
            self.assertTrue(m.stop(lumieres=0, film=1, suspendre=1)["ok"])
        self.assertIsNone(m.LANCEMENT["d"])
        self.assertEqual(m.SUSPENDUES["l"][0]["t"], d["t"])

    def test_lot91_h_i_netflix_deja_connu_avant_un_arret(self):
        # Netflix (titre vide) déjà vu avant un arrêt KamCiné : même clé, marquée invalide. Il n'a jamais de position.
        m = self.m; m.oublier_lancement(raison="test")
        h = self.chaine_complete()
        self.releve(h, self.netflix("Idle"))
        m.atvlive.LIVE.observations.invalidate()
        status = self.releve(h, self.netflix("Idle"))
        self.assertEqual((status["seance"]["nature"], status["lecture"]["contexte"]["classe"]), ("vide", "app_sans_lecture"))
        self.assertEqual(status["lecture"]["contexte"]["app_nom"], "Netflix")
        for _ in range(3):
            status = self.releve(h, self.netflix("Playing"))
        self.assertEqual(status["seance"]["nature"], "ailleurs", "Le passage observé à Playing prouve le démarrage")
        self.assertEqual(status["lecture"]["ailleurs"]["titre"], "")
        # Un Playing republié sans passage observé (déjà Playing avant l'arrêt) ne prouve rien.
        m.atvlive.LIVE.observations.invalidate()
        self.assertEqual(self.releve(h, self.netflix("Playing"))["lecture"]["contexte"]["classe"], "ancienne")

    # ---------- Lot 2.6.90 : faits du journal matériel de la 2.6.89 ----------
    EP6 = "Reacher - S1 \u2219 E6 - Papier"
    NETFLIX = {"app": "com.netflix.Netflix", "app_nom": "Netflix", "titre": "", "media": "Unknown", "pos": 0, "total": 0}

    def s1e6(self, titre=None, pos=483, etat="Paused"):
        return dict(self.infuse(self.EP6 if titre is None else titre, pos=pos, total=2861, etat=etat), fonctions=self.CMD)

    def netflix(self, etat):
        return dict(self.NETFLIX, etat=etat, lu_a=self.m.time.time())

    def test_lot90_1_titre_vide_du_meme_flux_apres_une_avance_garde_s1e6(self):
        m = self.m; m.oublier_lancement(raison="test")
        h = self.chaine_complete()
        self.releve(h, self.s1e6(pos=480, etat="Playing")); self.releve(h, self.s1e6(pos=483))
        # Journal : même flux (2861 s) publié sans titre vers 2710 s, après une avance à la télécommande.
        for pos, etat in [(2710, "Paused"), (2712, "Playing"), (2716, "Playing")]:
            status = self.releve(h, self.s1e6(titre="", pos=pos, etat=etat))
            self.assertEqual((status["lecture"]["meta"] or {}).get("episode"), 6, "Jamais « Lecture en cours » : %s" % pos)
        self.assertEqual(status["lecture"]["contexte"]["classe"], "lecture")
        ligne = [l for l in m.RATTACHEMENTS["lignes"] if l.get("etape") == "classement"][-1]
        self.assertEqual(ligne["origine_identite"], "mémoire d'identité")

    def test_lot90_2_titre_vide_n_est_pas_un_nom_different(self):
        seance = {"type": "tv", "id": 108978, "saison": 1, "episode": 6, "total_fichier": 2861,
                  "meta": dict(self.S1E6, episode=6)}
        vide = self.s1e6(titre="", pos=2710)
        self.assertEqual(lecture_infuse.explique_correspondance(vide, seance), (True, "même fichier : titre absent, durée identique"))
        ok, raison = lecture_infuse.explique_correspondance(dict(vide, total=2869), seance)
        self.assertFalse(ok); self.assertIn("titre absent et autre durée", raison)
        ok, raison = lecture_infuse.explique_correspondance(vide, dict(seance, total_fichier=None, meta=dict(self.S1E6)))
        self.assertFalse(ok); self.assertNotIn("nom différent", raison)
        ok, raison = lecture_infuse.explique_correspondance(self.s1e6(titre="Jack Ryan S01E06", pos=10), seance)
        self.assertFalse(ok, "Un titre explicite d'un autre média reste refusé : " + raison)

    def test_lot90_3_s1e5_explicite_n_est_jamais_la_seance_s1e6(self):
        m = self.m
        m.oublier_fin_seance()
        d = m.lancement_definir("tv", 108978, 1, 6, "reprise", meta=dict(self.S1E6))
        d.update(mode="reel", total_fichier=2861)
        h = self.chaine_complete()
        self.releve(h, self.s1e6(pos=480, etat="Playing"))
        status = self.releve(h, dict(self.reacher(pos=860), total=2869))
        self.assertEqual(status["lecture"]["meta"]["episode"], 5)
        self.assertEqual(status["seance_conservee"]["episode"], 6, "S1E5 lu : la séance S1E6 reste en suspens")
        self.assertFalse(lecture_infuse.correspond(dict(self.reacher(pos=860), total=2869), d))
        # Et un titre vide ensuite relève du flux S1E5 (2869 s), jamais de S1E6.
        status = self.releve(h, dict(self.reacher(titre="", pos=863), total=2869))
        self.assertEqual(status["lecture"]["meta"]["episode"], 5)
        self.assertIsNotNone(status["seance_conservee"])

    def seance_s1e6_suivie(self):
        m = self.m
        m.oublier_fin_seance()
        d = m.lancement_definir("tv", 108978, 1, 6, "reprise", meta=dict(self.S1E6))
        d.update(mode="reel", total_fichier=2861)
        h = self.chaine_complete()
        self.releve(h, self.s1e6(pos=480, etat="Playing")); self.releve(h, self.s1e6(pos=483, etat="Playing"))
        self.assertTrue(self.releve(h, self.s1e6(pos=486, etat="Paused"))["en_cours"])
        return d, h

    def test_lot90_4_seance_puis_netflix_ouvert_suspens_et_rien_en_lecture(self):
        m = self.m
        d, h = self.seance_s1e6_suivie()
        status = self.releve(h, self.netflix("Idle"))
        self.assertEqual(status["seance"]["nature"], "vide")
        self.assertEqual(status["seance_conservee"]["episode"], 6)
        self.assertEqual((status["lecture"]["contexte"]["classe"], status["lecture"]["contexte"]["app_nom"]), ("app_sans_lecture", "Netflix"))
        # pyatv republie ensuite l'ancienne pause Reacher : elle ne redevient jamais la grande fiche.
        status = self.releve(h, self.s1e6(pos=486, etat="Paused"))
        self.assertFalse(status["lecture"]["actif"])
        self.assertEqual(status["lecture"]["contexte"]["classe"], "ancienne")
        self.assertIn("Netflix", status["lecture"]["contexte"]["raison"])
        self.assertEqual(status["seance_conservee"]["episode"], 6)
        self.assertIs(m.LANCEMENT["d"], d, "La séance persistante n'est pas perdue")
        # Retour réel sur Reacher : la lecture reprend, la séance est rattachée, plus de carte en suspens.
        status = self.releve(h, self.s1e6(pos=489, etat="Playing"))
        status = self.releve(h, self.s1e6(pos=492, etat="Playing"))
        self.assertTrue(status["lecture"]["actif"])
        self.assertIsNone(status["seance_conservee"])
        self.assertTrue(status["en_cours"])

    def test_lot90_4_bis_accueil_signale_sans_application(self):
        # Si l'Apple TV signale l'accueil (aucune application, Idle), la pause Reacher republiée ensuite est ancienne.
        d, h = self.seance_s1e6_suivie()
        status = self.releve(h, {"etat": "Idle", "titre": "", "app": None, "pos": 0, "total": 0, "lu_a": self.m.time.time()})
        self.assertEqual(status["seance"]["nature"], "vide")
        status = self.releve(h, self.s1e6(pos=486, etat="Paused"))
        self.assertFalse(status["lecture"]["actif"])
        self.assertEqual(status["seance_conservee"]["episode"], 6)

    def test_lot90_5_seance_puis_youtube_en_lecture(self):
        d, h = self.seance_s1e6_suivie()
        yt = dict(self.YOUTUBE, media="Video", titre="Une vidéo", total=600)
        self.releve(h, dict(yt, etat="Playing", pos=10))
        status = self.releve(h, dict(yt, etat="Playing", pos=13))
        self.assertEqual(status["seance"]["nature"], "ailleurs")
        self.assertEqual(status["lecture"]["ailleurs"]["app_nom"], "YouTube")
        self.assertEqual(status["seance_conservee"]["episode"], 6)

    def test_lot90_6_meme_reacher_sans_titre_jamais_en_double(self):
        d, h = self.seance_s1e6_suivie()
        for pos in (2710, 2713, 2716):
            status = self.releve(h, self.s1e6(titre="", pos=pos, etat="Playing"))
            self.assertIsNone(status["seance_conservee"], "Même épisode, même fichier : aucune séance en suspens")
            self.assertTrue(status["en_cours"])
            self.assertEqual(status["lecture"]["meta"]["episode"], 6)

    def test_lot90_7_ancien_youtube_en_pause_puis_netflix(self):
        m = self.m; m.oublier_lancement(raison="test")
        h = self.chaine_complete()
        yt = dict(self.YOUTUBE, media="Video", titre="DIRECTEUR DU CNRS", total=3707)
        self.releve(h, dict(yt, etat="Playing", pos=3280)); self.releve(h, dict(yt, etat="Playing", pos=3283))
        self.assertEqual(self.releve(h, dict(yt, etat="Paused", pos=3285))["seance"]["nature"], "ailleurs", "Vraie pause YouTube")
        self.releve(h, self.netflix("Idle"))
        status = self.releve(h, dict(yt, etat="Paused", pos=3285))
        self.assertEqual(status["seance"]["nature"], "vide", "L'ancien YouTube ne redevient jamais principal après Netflix")
        self.assertEqual(status["lecture"]["contexte"]["classe"], "ancienne")
        ligne = [l for l in m.RATTACHEMENTS["lignes"] if l.get("etape") == "classement"][-1]
        self.assertEqual((ligne["app"], ligne["classe"]), ("com.google.ios.youtube", "ancienne"))
        self.assertIn("passage à une autre application", ligne["raison"])
        # YouTube relancé pour de vrai : il redevient la lecture actuelle.
        self.releve(h, dict(yt, etat="Playing", pos=3290))
        self.assertEqual(self.releve(h, dict(yt, etat="Playing", pos=3293))["seance"]["nature"], "ailleurs")

    def test_lot90_8_9_netflix_idle_puis_lecture_sans_titre_apres_un_arret(self):
        m = self.m; m.oublier_lancement(raison="test")
        h = self.chaine_complete()
        m.atvlive.LIVE.observations.invalidate()      # un arrêt KamCiné antérieur pose la barrière
        status = self.releve(h, self.netflix("Idle"))
        self.assertEqual(status["seance"]["nature"], "vide", "Netflix ouvert n'est pas une lecture")
        self.assertIsNone(status["lecture"]["ailleurs"])
        for _ in range(3):
            status = self.releve(h, self.netflix("Playing"))
        self.assertEqual(status["seance"]["nature"], "ailleurs", "Netflix lit : lecture externe honnête")
        self.assertEqual((status["lecture"]["ailleurs"]["app_nom"], status["lecture"]["ailleurs"]["titre"]), ("Netflix", ""))
        self.assertEqual(status["lecture"]["contexte"]["classe"], "lecture")

    # ---------- Lot 2.6.89 : Piloter, générique film et série, épisode suivant, carte de fin de saison ----------
    S1E6 = {"type": "tv", "id": 108978, "titre": "Reacher", "annee": "2022", "saison": 1, "episode": 6, "ep_titre": "Pas de bol"}

    def test_lot89_a_piloter_une_serie_cree_une_seance_tv_sans_entracte(self):
        m = self.m
        m.film.return_value = {"actif": True, "etat": "Playing", "meta": dict(self.S1E5), "type": "tv", "total": 2869}
        with patch.object(m.relais, "demarrer", return_value=True) as demarrer:
            r = m.reprendre(entracte="1")
        self.assertEqual(r["type"], "serie")
        ctx = demarrer.call_args[0][0]
        self.assertEqual(ctx["type"], "serie")
        self.assertFalse(ctx["entracte_actif"], "Jamais d'entracte pour un épisode, même demandée")
        self.assertEqual(ctx["meta"]["total_fichier"], 2869)
        d = m.LANCEMENT["d"]
        self.assertEqual((d["type"], d["saison"], d["episode"], d["origine"], d["confirme"]), ("tv", 1, 5, "reprise", True))
        self.assertEqual(d["total_fichier"], 2869)
        self.assertFalse(m.etat["options"]["entracte"])

    def test_lot89_a_piloter_sans_identite_ne_cree_jamais_de_seance_film(self):
        m = self.m
        m.oublier_lancement(raison="test")
        for film in ({"actif": True, "etat": "Playing", "meta": None, "type": None, "titre": "Reacher"},
                     {"actif": True, "etat": "Playing", "meta": None, "type": "tv", "titre": "Reacher"},
                     {"actif": True, "etat": "Playing", "meta": {"type": "tv", "id": 108978, "titre": "Reacher"}, "type": "tv"}):
            m.film.return_value = film
            with patch.object(m.relais, "demarrer", return_value=True) as demarrer:
                r = m.reprendre()
            self.assertEqual(r.status_code, 409, film)
            demarrer.assert_not_called()
            self.assertIsNone(m.LANCEMENT["d"])

    def test_lot89_d_e_regle_du_generique_film_et_serie(self):
        import generique
        cfg = dict(self.m.charger(), generique_minutes=7, generique_minutes_serie=2, generique_delai=15)
        # D : épisode de 47 min, 4 min avant la fin : toujours l'épisode (le repli film de 7 min l'aurait déjà déclenché).
        self.assertEqual(generique.seuil(2820, True, None, cfg), (2700, "repli intelligent série"))
        self.assertLess(2820 - 240, generique.seuil(2820, True, None, cfg)[0])
        self.assertEqual(generique.seuil(7200, False, None, cfg), (6780, "repli intelligent film"))
        self.assertEqual(generique.seuil(4680, False, None, cfg), (4306, "repli intelligent film"))
        self.assertEqual(generique.seuil(4680, False, None, dict(cfg, generique_source="fixe")), (4260, "repli fixe film"))
        # A : un minutage connu passe avant tout repli, décalé du délai réglé, jamais dans les vingt dernières secondes.
        self.assertEqual(generique.seuil(2820, True, 2750, cfg), (2765, "timecode connu"))
        self.assertEqual(generique.seuil(2820, True, 2810, cfg), (2800, "timecode connu"))
        h = self.m.Hote()
        with patch.object(self.m.generique, "credits_debut", return_value=None):
            self.assertEqual(h.cible_generique(dict(self.S1E5), 2820, dict(cfg, generique_source="auto"), False, 900, True), (2700, "repli intelligent série"))
        with patch.object(self.m.generique, "credits_debut", return_value=2750):
            self.assertEqual(h.cible_generique(dict(self.S1E5), 2820, dict(cfg, generique_source="auto"), False, 900, True), (2765, "TheIntroDB"))
        ligne = [l for l in self.m.RATTACHEMENTS["lignes"] if l.get("etape") == "generique"][-1]
        self.assertEqual((ligne["source"], ligne["timecode"], ligne["duree"], ligne["seuil"]), ("timecode connu (TheIntroDB)", 2750, 2820, 2765))

    def test_lot89_f_g_retour_avant_le_seuil_et_une_seule_etape_active(self):
        m = self.m
        m.lancement_definir("tv", 108978, 1, 5, "reprise", meta=dict(self.S1E5))
        base = [(1, "Séance reprise en cours d'épisode"), (2, "Générique attendu vers 2700s sur 2820s (repli série)")]
        m.etat.update(type="serie", mode="reel", lignes=base + [(3, "Générique : TV gauche et droite")])
        with patch.object(m, "en_cours", return_value=True):
            etapes = {e["cle"]: e["etat"] for e in m.status()["etapes"]}
        self.assertEqual(etapes, {"preparation": "saute", "film": "fait", "generique": "en_cours"})
        m.etat["lignes"].append((4, "Retour avant le générique : l'épisode continue"))
        with patch.object(m, "en_cours", return_value=True):
            status = m.status()
        self.assertEqual({e["cle"]: e["etat"] for e in status["etapes"]}, {"preparation": "saute", "film": "en_cours", "generique": "a_venir"})
        self.assertEqual(status["detail"]["generique"]["source"], "repli série")
        self.assertNotEqual(m.analyser(m.etat["lignes"], "reel")["generique"]["etat"], "fait")

    def test_lot89_h_i_seance_pilotee_reste_confirmee_titre_vide_pause_et_saut(self):
        m = self.m
        m.oublier_lancement(raison="test")
        h = self.chaine_complete()
        self.reacher_identifie(h)
        with patch.object(m.relais, "demarrer", return_value=True):
            self.assertEqual(m.reprendre()["type"], "serie")
        d = m.LANCEMENT["d"]
        for p, avance in [(self.reacher(titre="", pos=2466), 3), (self.reacher(titre="", pos=2466, etat="Paused"), 10),
                          (self.reacher(titre="", pos=2466, etat="Paused"), 10), (self.reacher(pos=2470), 3),
                          (self.reacher(pos=1200), 3), (self.reacher(titre="", pos=1203), 3), (self.reacher(pos=1206), 3)]:
            status = self.releve(h, p, avance)
            self.assertIs(m.LANCEMENT["d"], d)
            self.assertTrue(status["en_cours"], p)
            self.assertIsNone(status["seance_conservee"], p)
            self.assertEqual(status["type"], "serie")

    def seance_s1e5_suivie(self):
        m = self.m
        m.oublier_fin_seance()
        d = m.lancement_definir("tv", 108978, 1, 5, "reprise", meta=dict(self.S1E5))
        d.update(mode="reel", total_fichier=2869, total=2869, position=2860)
        m.etat.update(type="serie", mode="reel", lignes=[(1, "Séance reprise en cours d'épisode"),
                                                          (2, "Générique attendu vers 2749s sur 2869s (repli série)"),
                                                          (3, "Générique : TV gauche et droite")])
        return d

    def test_lot89_j_episode_suivant_met_a_jour_la_meme_seance(self):
        m = self.m
        d = self.seance_s1e5_suivie()
        m.meta_par_id.side_effect = lambda t, i, s=None, e=None: dict(self.S1E6, saison=s, episode=e)
        p = self.infuse("Reacher - S1 \u2219 E6 - Pas de bol", pos=5, total=2950)
        self.assertIsNone(m.seance_episode_suivant(dict(self.S1E5), dict(p, titre="Reacher - S1 \u2219 E4 - Avant")),
                          "Jamais un épisode précédent")
        suivant = m.seance_episode_suivant(dict(self.S1E5), p)
        self.assertEqual((suivant["saison"], suivant["episode"], suivant["total_fichier"]), (1, 6, 2950))
        self.assertIs(m.LANCEMENT["d"], d, "Même séance, même jeton")
        self.assertEqual((d["saison"], d["episode"], d["total_fichier"], d["meta"]["episode"]), (1, 6, 2950, 6))
        self.assertTrue(d["confirme"])
        lignes = [l for _, l in m.etat["lignes"]]
        self.assertFalse(any(l.startswith("Générique") for l in lignes), "Les étapes de S1E5 ne restent pas")
        self.assertTrue(lignes[-1].startswith("Épisode suivant : Reacher, saison 1, épisode 6"))
        self.p = p
        with patch.object(m, "en_cours", return_value=True):
            status = m.status()
        self.assertEqual({e["cle"]: e["etat"] for e in status["etapes"]}, {"preparation": "saute", "film": "en_cours", "generique": "a_venir"})
        self.assertEqual(status["type"], "serie")
        self.assertEqual(m.lire_memoire_seance()["episode"], 6)
        ligne = [l for l in m.RATTACHEMENTS["lignes"] if l.get("evenement") == "épisode suivant"][-1]
        self.assertEqual((ligne["avant"], ligne["apres"]), ("S1E5", "S1E6"))

    def test_lot89_j_le_service_ne_termine_pas_une_serie_suivie_par_son_controleur(self):
        m = self.m
        d = self.seance_s1e5_suivie()
        self.p = {"etat": "Idle", "lu_a": m.time.time()}
        with patch.object(m, "en_cours", return_value=True), patch.object(m, "terminer_seance_normalement") as fin:
            m.lancement_courant()
        fin.assert_not_called()
        self.assertIs(m.LANCEMENT["d"], d)

    def test_fin_episode_affiche_une_carte_a_chaque_episode(self):
        m = self.m
        self.seance_s1e5_suivie()
        m.terminer_seance_normalement()
        fin = m.fin_seance_courante()
        self.assertEqual(fin["episode"], 5, "Une carte de fin est disponible pour chaque épisode terminé")
        self.assertIsNone(m.LANCEMENT["d"])
        d = self.seance_s1e5_suivie()
        d.update(episode=8, meta=dict(self.S1E5, episode=8))
        m.terminer_seance_normalement()
        fin = m.fin_seance_courante()
        self.assertEqual((fin["saison"], fin["episode"]), (1, 8))
        self.assertNotIn("fin_saison", fin)

    def test_lot89_l_film_garde_sa_carte_de_fin(self):
        m = self.m
        m.oublier_fin_seance()
        m.lancement_definir("movie", 1, None, None, "seance", meta=META)["mode"] = "reel"
        m.terminer_seance_normalement()
        self.assertEqual(m.fin_seance_courante()["id"], 1)

    def test_lot89_seance_serie_confie_l_episode_au_relais(self):
        m = self.m
        d = m.lancement_definir("tv", 108978, 1, 5, "seance", meta=dict(self.S1E5))
        d["total_fichier"] = 2869
        m.etat["mode"] = "test"
        with patch.object(m.relais, "demarrer", return_value=True) as demarrer:
            m.confier_episode()
        ctx = demarrer.call_args[0][0]
        self.assertEqual((ctx["type"], ctx["test"], ctx["silencieux"], ctx["meta"]["total_fichier"]), ("serie", True, True, 2869))


class Controleurs(unittest.TestCase):
    def test_relais_rearms_skipped_threshold_but_never_executed_break(self):
        import relais
        h = Mock()
        h.charger.return_value = {"entracte_actif": False, "entracte_min": 1, "entracte_max": 1}
        h.cible_generique.return_value = (7000, "fixe")
        clock = Horloge()
        points = iter([(100, 0), (160, 4), (90, 4), (120, 30), (150, 30),
                       (100, 4), (155, 55), (7000, 6845), (7200, 200)])
        def playing():
            position, elapsed = next(points)
            clock.avancer(elapsed)
            return dict(P, pos=position)
        h.playing.side_effect = playing
        r = relais.Relais(h)
        r.actif, r.jeton = True, 1
        with patch.object(r, "_entracte") as intermission, patch.object(relais, "time", clock),                 patch.object(etat_seance, "time", clock):
            r._boucle({"test": False, "meta": META, "entracte_prevu": 150}, 1)
        intermission.assert_called_once()
        h.log.assert_any_call("Entracte réarmée après retour avant le seuil")
        h.log.assert_any_call("Fin naturelle du film")
        self.assertFalse(any("Erreur du relais" in str(c) for c in h.log.call_args_list))

    def relais_scenario(self, points):
        import relais
        h = Mock()
        h.charger.return_value = {"entracte_actif": False, "entracte_min": 1, "entracte_max": 1}
        h.cible_generique.return_value = (7000, "fixe")
        clock = Horloge()
        suite = iter(points)
        def playing():
            p, elapsed = next(suite)
            clock.avancer(elapsed)
            return p
        h.playing.side_effect = playing
        r = relais.Relais(h)
        r.actif, r.jeton = True, 1
        with patch.object(relais, "time", clock), patch.object(etat_seance, "time", clock):
            r._boucle({"test": False, "meta": META}, 1)
        return [c.args[0] for c in h.log.call_args_list], h

    def test_relais_termine_la_seance_peu_apres_le_generique(self):
        pas = [(dict(P, pos=6990), 4), (dict(P, pos=7000), 4)] + [(dict(P, pos=7000 + 4 * i), 4) for i in range(1, 10)]
        logs, h = self.relais_scenario(pas)
        self.assertIn("Générique : TV gauche et droite", logs)
        self.assertEqual(logs[-2:], ["Fin de séance au générique", "Séance terminée"])
        h.marquer_vu.assert_called_once()
        self.assertNotIn("Le film s'est arrêté, séance conservée", logs)

    def test_relais_film_quitte_pendant_le_generique_est_une_fin(self):
        pas = [(dict(P, pos=7000), 4), (dict(P, pos=7004), 4)] + [({"etat": "Idle"}, 5)] * 5
        logs, _ = self.relais_scenario(pas)
        self.assertEqual(logs[-2:], ["Fin de séance au générique", "Séance terminée"])

    def test_relais_rend_la_main_des_qu_une_autre_app_progresse(self):
        yt = {"app": "com.google.ios.youtube", "titre": "Vidéo", "etat": "Playing", "media": "Video", "pos": 60,
              "progression": True, "depuis": 2}
        pas = [(dict(P, pos=5000), 0), (dict(P, pos=5035), etat_seance.GARDE_APRES_YOUTUBE_KAMCINE_S + 5), (yt, 4)]
        logs, _ = self.relais_scenario(pas)
        self.assertEqual(logs[-1], "Le film s'est arrêté, séance conservée")
        self.assertEqual(len([l for l in logs if "arrêté" in l]), 1)

    def test_relais_rattache_reste_quand_infuse_ne_publie_plus_que_la_serie(self):
        meta = {"type": "tv", "id": 7, "titre": "Reacher", "saison": 1, "episode": 3, "total_fichier": 2789}
        suivi = etat_seance.FinFilm(meta)
        p = {"titre": "Reacher", "etat": "Playing", "app": "com.firecore.infuse", "media": "Video", "pos": 1500, "total": 2789}
        self.assertEqual(suivi.observer(p), "lecture")
        self.assertEqual(etat_seance.FinFilm(meta).observer(dict(p, titre="Reacher S01E04")), "inconnu")

    def test_relais_film_quitte_avant_le_generique_reste_interrompu(self):
        pas = [(dict(P, pos=5000), 4)] + [({"etat": "Idle"}, 5)] * 5
        logs, h = self.relais_scenario(pas)
        self.assertEqual(logs[-1], "Le film s'est arrêté, séance conservée")
        self.assertNotIn("Séance terminée", logs)
        h.marquer_vu.assert_not_called()

    def script_generique(self, positions, pas):
        import ast, json
        from io import StringIO
        from contextlib import redirect_stdout
        source = Path(__file__).resolve().parents[1] / "seance.py"
        node = next(n for n in ast.parse(source.read_text()).body if isinstance(n, ast.FunctionDef) and n.name == "attendre_generique")
        clock = Horloge(pas=pas)
        suite = iter(positions)
        env = {"MODE_TEST": False, "os": os, "json": json, "FinFilm": etat_seance.FinFilm,
               "GENERIQUE": {"atteint": False, "fin": False, "t": None}, "cible_generique": lambda _: (7000, ""),
               "FIN_APRES_GENERIQUE_S": etat_seance.FIN_APRES_GENERIQUE_S,
               "lecture_observee": lambda: next(suite), "time": clock, "lumiere": Mock(), "faire_entracte": Mock()}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), env)
        sortie = StringIO()
        with patch.object(etat_seance, "time", clock), patch.dict(os.environ, SEANCE_META=json.dumps(META)), redirect_stdout(sortie):
            env["attendre_generique"](7200)
        return sortie.getvalue(), env

    def test_script_termine_la_seance_peu_apres_le_generique(self):
        sortie, env = self.script_generique([dict(P, pos=7000 + i) for i in range(0, 400, 3)], pas=3)
        self.assertIn("Fin de séance au générique", sortie)
        self.assertTrue(env["GENERIQUE"]["fin"])
        env["lumiere"].assert_called_once_with("generique", 20)

    def test_script_film_quitte_pendant_le_generique_est_une_fin(self):
        sortie, env = self.script_generique([dict(P, pos=7000)] + [{"etat": "Idle"}] * 20, pas=5)
        self.assertIn("Fin de séance au générique", sortie)
        self.assertTrue(env["GENERIQUE"]["fin"])

    def test_script_film_quitte_avant_le_generique_reste_interrompu(self):
        sortie, env = self.script_generique([dict(P, pos=5000)] + [{"etat": "Idle"}] * 20, pas=5)
        self.assertIn("Le film s'est arrêté, séance conservée", sortie)
        self.assertFalse(env["GENERIQUE"]["fin"])

    def test_script_rearms_once_then_preserves_credits(self):
        import ast, json
        from io import StringIO
        from contextlib import redirect_stdout
        source = Path(__file__).resolve().parents[1] / "seance.py"
        node = next(n for n in ast.parse(source.read_text()).body if isinstance(n, ast.FunctionDef) and n.name == "attendre_generique")
        points = iter([dict(P, pos=160), dict(P, pos=90), dict(P, pos=80),
                       dict(P, pos=7000), dict(P, pos=7200)])
        clock = Horloge()
        intermission = Mock(return_value=None)
        env = {"MODE_TEST": False, "os": os, "json": json, "FinFilm": etat_seance.FinFilm,
               "GENERIQUE": {"atteint": False}, "cible_generique": lambda _: (7000, ""), "FIN_APRES_GENERIQUE_S": etat_seance.FIN_APRES_GENERIQUE_S,
               "lecture_observee": lambda: next(points), "time": clock, "lumiere": Mock(),
               "faire_entracte": intermission}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), env)
        with patch.dict(os.environ, SEANCE_META=json.dumps(META)), redirect_stdout(StringIO()):
            env["attendre_generique"](7200, 150)
        intermission.assert_called_once_with(META["titre"], 90, 7200, cible=150)
        self.assertTrue(env["GENERIQUE"]["fin"])
        env["lumiere"].assert_called_once()

    def test_relais_annule_entracte_sur_seek_pendant_pause_sans_tuer_seance(self):
        import relais
        h = Mock()
        h.charger.return_value = {"entracte_actif": False}
        sequence = iter([P, dict(P, etat="Paused", pos=100), dict(P, etat="Paused", pos=160),
                         dict(P, pos=160), dict(P, pos=7000)])
        h.playing.side_effect = lambda: next(sequence)
        h.cible_generique.return_value = (7000, "fixe")
        horloge = Horloge()
        r = relais.Relais(h)
        r.actif, r.jeton = True, 1
        with patch.object(r, "_entracte") as entracte, patch.object(relais, "time", horloge), \
                patch.object(etat_seance, "time", horloge):
            r._boucle({"test": False, "meta": META, "entracte_prevu": 150}, 1)
        entracte.assert_not_called()
        h.log.assert_any_call("Avance manuelle au delà de l'entracte prévue (160s) : entracte annulée")
        h.marquer_vu.assert_called_once_with(META)

    def test_relais_pause_entracte_reprise_generique_fin(self):
        import relais
        cfg = {"entracte_actif": False, "entracte_debut": 45, "entracte_fin": 60,
               "entracte_min": 1, "entracte_max": 1}
        sequence = iter([P, dict(P, etat="Paused"), dict(P, pos=200), dict(P, pos=500),
                         dict(P, pos=7000), dict(P, pos=7200), {"etat": "Idle"}, {"etat": "Idle"}, {"etat": "Idle"}])
        horloge = Horloge()

        def playing():
            p = next(sequence)
            # La position suit le temps réel écoulé (1x, comme une lecture normale) : le saut manuel qui annule
            # l'entracte (point 90) ne doit pas se confondre avec cette progression scriptée mais réaliste.
            if "pos" in p:
                horloge.t = max(horloge.t, 1000.0 + p["pos"])
            else:
                horloge.avancer(7.0)
            return p

        h = Mock()
        h.charger.return_value = cfg
        h.playing.side_effect = playing
        h.cible_generique.return_value = (7000, "fixe")
        r = relais.Relais(h)
        r.actif, r.jeton = True, 1
        with patch.object(r, "_entracte") as entracte, patch.object(relais, "time", horloge), \
                patch.object(etat_seance, "time", horloge):
            r._boucle({"test": False, "meta": META, "entracte_prevu": 150}, 1)
        entracte.assert_called_once()
        h.marquer_vu.assert_called_once_with(META)
        h.log.assert_any_call("Fin naturelle du film")
        self.assertFalse(r.actif)
        self.assertFalse(any("Erreur du relais" in str(c) for c in h.log.call_args_list))

    def test_relais_attend_la_duree_sans_perdre_le_pilotage(self):
        import relais
        h = Mock()
        h.charger.return_value = {"entracte_actif": False}
        h.playing.side_effect = [dict(P, total=0), dict(P, total=0, etat="Paused"), P, {"etat": "Idle"}, {"etat": "Idle"}, {"etat": "Idle"}]
        h.cible_generique.return_value = (7000, "fixe")
        r = relais.Relais(h)
        r.actif, r.jeton = True, 1
        with patch.object(r, "_attendre"), patch.object(relais.time, "sleep"), \
                patch.object(etat_seance, "time", Horloge(pas=7.0)):
            r._boucle({"test": False, "meta": META}, 1)
        self.assertGreaterEqual(h.playing.call_count, 6)
        h.log.assert_any_call("Le film s'est arrêté, séance conservée")
        h.marquer_vu.assert_not_called()

    def test_script_reel_attend_la_fin_et_ne_rallume_pas_sur_arret_precoce(self):
        import ast
        import json
        import time
        from io import StringIO
        from contextlib import redirect_stdout
        source = Path(__file__).resolve().parents[1] / "seance.py"
        n = next(n for n in ast.parse(source.read_text()).body if isinstance(n, ast.FunctionDef) and n.name == "attendre_generique")
        for positions, fin in [([P, dict(P, pos=7000), dict(P, pos=7200)], True), ([P], False)]:
            observations = iter(positions + [{"etat": "Idle"}] * 3)
            env = {"MODE_TEST": False, "REG": {}, "os": os, "json": json, "FinFilm": etat_seance.FinFilm,
                   "GENERIQUE": {"atteint": False}, "cible_generique": lambda _: (7000, ""), "FIN_APRES_GENERIQUE_S": etat_seance.FIN_APRES_GENERIQUE_S,
                   "lecture_observee": lambda: next(observations), "time": time, "lumiere": Mock()}
            exec(compile(ast.Module(body=[n], type_ignores=[]), str(source), "exec"), env)
            log = StringIO()
            with patch.dict(os.environ, {"SEANCE_META": json.dumps(META)}), patch.object(time, "sleep"), \
                    patch.object(etat_seance, "time", Horloge(pas=7.0)), redirect_stdout(log):
                env["attendre_generique"](7200)
            self.assertEqual("Fin naturelle du film" in log.getvalue(), fin)
            self.assertEqual(env["lumiere"].call_count, int(fin))


    # ---------- 2.6.98 : durée du recul identique par la voie directe et par le repli atvremote ----------
    def faux_pyatv(self, appels):
        import types
        class RC:
            async def skip_backward(self, t=0.0):
                appels.append(("skip_backward", t))
        class Apps:
            async def launch_app(self, u):
                appels.append(("launch", u))
        class ATV:
            remote_control, apps = RC(), Apps()
            def close(self):
                return []
        class Conf:
            def set_credentials(self, *a):
                pass
        pyatv = types.ModuleType("pyatv")
        async def scan(loop, identifier=None, timeout=5):
            return [Conf()]
        async def connect(conf, loop):
            return ATV()
        pyatv.scan, pyatv.connect = scan, connect
        const = types.ModuleType("pyatv.const")
        const.Protocol = types.SimpleNamespace(Companion="c", AirPlay="a")
        pyatv.const = const
        # 2.6.106 : l'Apple TV vient de la configuration de l'installation (integrations.py), plus de l'environnement.
        import contextlib, integrations
        pile = contextlib.ExitStack()
        pile.enter_context(patch.dict(sys.modules, {"pyatv": pyatv, "pyatv.const": const}))
        pile.enter_context(patch.object(integrations, "appletv", return_value={"identifiant": "AA:BB:CC:DD:EE:FF", "companion": "c", "airplay": "a"}))
        return pile

    def test_recul_relais_voie_directe_et_repli_meme_duree(self):
        import relais
        etapes = [("launch", relais.INFUSE), ("attendre", 0), ("skip_backward", 7)]
        appels = []
        with self.faux_pyatv(appels):
            relais.Relais(Mock())._sequence(etapes)
        self.assertIn(("skip_backward", 7), appels, "Voie directe : recul du réglage")
        h = Mock()
        with patch.object(relais, "_sequence_async", side_effect=RuntimeError("Apple TV introuvable")):
            relais.Relais(h)._sequence(etapes)
        self.assertEqual([c.args[0] for c in h.atv.call_args_list], ["launch_app=" + relais.INFUSE, "skip_backward=7"],
                         "Repli atvremote : même durée, syntaxe commande=argument")

    def test_recul_zero_relais_aucun_skip_backward(self):
        import relais
        for recul, attendu in ((0, []), (4, [("skip_backward", 4)])):
            h = Mock()
            h.playing_frais.return_value = None
            r = relais.Relais(h)
            r.actif, r.jeton = True, 1
            appels = []
            with self.faux_pyatv(appels), patch.object(r, "_attendre"):
                r._retour({"recul": recul}, 1)
            self.assertEqual([x for x in appels if x[0] == "skip_backward"], attendu, "Voie directe, recul %d" % recul)
            h = Mock()
            h.playing_frais.return_value = None
            r = relais.Relais(h)
            r.actif, r.jeton = True, 1
            with patch.object(relais, "_sequence_async", side_effect=RuntimeError("Apple TV introuvable")), patch.object(r, "_attendre"):
                r._retour({"recul": recul}, 1)
            reculs = [c.args[0] for c in h.atv.call_args_list if c.args and str(c.args[0]).startswith("skip_backward")]
            self.assertEqual(reculs, ["skip_backward=%d" % recul] if recul else [], "Repli atvremote, recul %d" % recul)

    def seance_fonction(self, nom, env):
        import ast
        source = Path(__file__).resolve().parents[1] / "seance.py"
        node = next(n for n in ast.parse(source.read_text()).body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name == nom)
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), env)
        return env[nom]

    def test_recul_seance_voie_directe_et_repli_meme_duree(self):
        import asyncio
        from io import StringIO
        from contextlib import redirect_stdout
        etapes = [("launch", "com.firecore.infuse"), ("attendre", 0), ("skip_backward", 5)]
        # Voie directe : Liaison._etapes passe la durée à pyatv.
        appels = []
        class RC:
            async def select(self):
                pass
            async def skip_backward(self, t=0.0):
                appels.append(("skip_backward", t))
        class Apps:
            async def launch_app(self, u):
                appels.append(("launch", u))
        Liaison = self.seance_fonction("Liaison", {"asyncio": asyncio, "time": __import__("time")})
        l = Liaison.__new__(Liaison)
        l.tv, l.progres = types_simple(remote_control=RC(), apps=Apps()), 0
        asyncio.run(l._etapes(etapes))
        self.assertIn(("skip_backward", 5), appels)
        # Repli : enchainer passe par atvremote avec la même durée.
        atv = Mock(return_value="")
        enchainer = self.seance_fonction("enchainer", {"liaison": types_simple(envoyer=lambda e: False, progres=0), "atv": atv,
                                                        "time": types_simple(sleep=lambda s: None)})
        with redirect_stdout(StringIO()):
            enchainer(etapes)
        self.assertEqual([c.args[0] for c in atv.call_args_list], ["launch_app=com.firecore.infuse", "skip_backward=5"])

    # ---------- Lot 2.6.89 ----------
    SERIE = {"type": "tv", "id": 108978, "titre": "Reacher", "annee": "2022", "saison": 1, "episode": 5}

    def ep(self, n, pos, total, etat="Playing", titre=None):
        return {"titre": titre if titre is not None else "Reacher - S1 \u2219 E%d - Titre" % n, "etat": etat,
                "app": "com.firecore.infuse", "media": "Video", "pos": pos, "total": total}

    def relais_serie(self, points, suivant=None):
        import generique
        import relais
        h = Mock()
        h.charger.return_value = {"entracte_actif": True, "entracte_debut": 45, "entracte_fin": 60, "entracte_min": 1,
                                  "entracte_max": 1, "generique_minutes": 7, "generique_minutes_serie": 2}
        h.cible_generique.side_effect = lambda meta, total, cfg, test, pos, serie=False: generique.seuil(total, serie, None, cfg)
        h.episode_suivant.side_effect = suivant or (lambda meta, p: None)
        clock = Horloge()
        suite = iter(points)
        def playing():
            p, elapsed = next(suite)
            clock.avancer(elapsed)
            return p
        h.playing.side_effect = playing
        r = relais.Relais(h)
        r.actif, r.jeton = True, 1
        with patch.object(relais, "time", clock), patch.object(etat_seance, "time", clock):
            r._boucle({"test": False, "meta": dict(self.SERIE, total_fichier=2820), "type": "serie"}, 1)
        return [c.args[0] for c in h.log.call_args_list], h

    def test_lot89_d_serie_quatre_minutes_avant_la_fin_reste_l_episode(self):
        pas = [(self.ep(5, 2400, 2820), 4), (self.ep(5, 2580, 2820), 4), (self.ep(5, 2600, 2820), 4)] + [({"etat": "Idle"}, 5)] * 5
        logs, h = self.relais_serie(pas)
        self.assertIn("Générique attendu vers 2700s sur 2820s (repli intelligent série)", logs)
        self.assertNotIn("Générique : TV gauche et droite", logs)
        self.assertFalse(any("Entracte" in l for l in logs), "Jamais d'entracte pour un épisode")
        self.assertNotIn("generique", [c.args[0] for c in h.lumiere.call_args_list])

    def test_lot89_e_f_seuil_puis_recul_puis_seuil_sans_boucle_de_lumieres(self):
        pas = ([(self.ep(5, 2690, 2820), 4), (self.ep(5, 2702, 2820), 4), (self.ep(5, 2706, 2820), 4),
                (self.ep(5, 2690, 2820), 4), (self.ep(5, 2400, 2820), 4), (self.ep(5, 2404, 2820), 4),
                (self.ep(5, 2702, 2820), 4)] + [(self.ep(5, 2702 + 4 * i, 2820), 4) for i in range(1, 12)]
               + [({"etat": "Idle"}, 5)] * 20)
        logs, h = self.relais_serie(pas)
        self.assertEqual(logs.count("Générique : TV gauche et droite"), 2)
        self.assertEqual(logs.count("Retour avant le générique : l'épisode continue"), 1,
                         "Un petit recul (2690 après 2706) ne ramène pas l'épisode : pas de va et vient")
        self.assertEqual([c.args[0] for c in h.lumiere.call_args_list], ["noir", "generique", "noir", "generique"],
                         "Ambiance du film à la prise en main, puis une seule action par franchissement réel")
        h.marquer_vu.assert_called_once()
        self.assertEqual(logs[-2:], ["Fin de séance au générique", "Séance terminée"])
        self.assertLess(logs.index("Retour avant le générique : l'épisode continue"), len(logs) - 3)

    def test_lot89_serie_ne_se_termine_pas_vingt_secondes_apres_le_generique(self):
        pas = [(self.ep(5, 2702 + 4 * i, 2820), 4) for i in range(0, 25)] + [({"etat": "Idle"}, 5)] * 20
        logs, _ = self.relais_serie(pas)
        i = logs.index("Générique : TV gauche et droite")
        self.assertEqual(logs[i + 1:], ["Fin de séance au générique", "Séance terminée"],
                         "La série reste en générique tant qu'Infuse joue, puis finit quand la lecture s'arrête")

    def test_lot89_j_autoplay_episode_suivant_meme_seance_seuil_recalcule(self):
        suivants = []
        def suivant(meta, p):
            if "E6" in p.get("titre", ""):
                suivants.append(meta["episode"])
                return dict(meta, episode=6, total_fichier=p["total"])
            return None
        pas = ([(self.ep(5, 2702, 2820), 4), (self.ep(5, 2760, 2820), 4)] + [({"etat": "Idle"}, 5)] * 4
               + [(self.ep(6, 0, 0, titre="Reacher"), 3), (self.ep(6, 3, 2950), 3), (self.ep(6, 8, 2950), 4),
                  (self.ep(6, 1200, 2950), 4)] + [({"etat": "Idle"}, 5)] * 5)
        logs, h = self.relais_serie(pas, suivant)
        self.assertEqual(suivants, [5])
        self.assertEqual([c.args[1] for c in h.cible_generique.call_args_list], [2820, 2950], "Seuil recalculé pour S1E6")
        self.assertIn("Générique attendu vers 2830s sur 2950s (repli intelligent série)", logs)
        self.assertEqual(logs.count("Générique : TV gauche et droite"), 1, "Pas de générique au début de S1E6")
        self.assertNotIn("Séance terminée", logs)
        self.assertEqual(logs[-1], "Le film s'est arrêté, séance conservée", "S1E6 quitté avant son générique : conservée")
        self.assertEqual(h.marquer_vu.call_count, 1)

    def test_lot89_film_garde_sa_fin_vingt_secondes_apres_le_generique(self):
        logs, _ = self.relais_scenario([(dict(P, pos=7000 + 4 * i), 4) for i in range(0, 12)])
        self.assertEqual(logs[-2:], ["Fin de séance au générique", "Séance terminée"])

    def test_lot89_script_serie_confie_l_episode_au_service(self):
        import ast
        from io import StringIO
        from contextlib import redirect_stdout
        source = Path(__file__).resolve().parents[1] / "seance.py"
        node = next(n for n in ast.parse(source.read_text()).body if isinstance(n, ast.FunctionDef) and n.name == "seance_serie")
        env = {"lumiere": Mock(), "demarrer_contenu": Mock(return_value=True), "atv": Mock(), "time": Mock(),
               "playing": Mock(return_value=("Playing", "Reacher - S1 \u2219 E5", 30, 2869)), "MODE_TEST": False,
               "attendre_generique": Mock(), "marquer_termine": Mock(), "GENERIQUE": {"fin": False}}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), env)
        sortie = StringIO()
        with redirect_stdout(sortie):
            env["seance_serie"]()
        self.assertIn("\nÉpisode confié au service\n", sortie.getvalue())
        self.assertNotIn("Séance terminée", sortie.getvalue())
        env["attendre_generique"].assert_not_called()
        self.assertIn("Épisode confié au service", Path(source.parent / "app" / "main.py").read_text(),
                      "Même texte des deux côtés (contrat du journal)")

    def test_lot89_meme_serie_autre_episode(self):
        meta = dict(self.SERIE, total_fichier=2820)
        self.assertFalse(lecture_infuse.meme_serie_autre_episode(self.ep(5, 100, 2820), meta))
        self.assertFalse(lecture_infuse.meme_serie_autre_episode(self.ep(5, 100, 2820, titre="Reacher"), meta), "Même fichier")
        self.assertTrue(lecture_infuse.meme_serie_autre_episode(self.ep(6, 100, 2950), meta))
        self.assertTrue(lecture_infuse.meme_serie_autre_episode(self.ep(6, 100, 2950, titre="Reacher"), meta))
        self.assertFalse(lecture_infuse.meme_serie_autre_episode(self.ep(6, 100, 2950, titre="Jack Ryan - S1E6"), meta))
        self.assertFalse(lecture_infuse.meme_serie_autre_episode(dict(self.ep(6, 100, 2950), app="com.google.ios.youtube"), meta))
        self.assertFalse(lecture_infuse.meme_serie_autre_episode(dict(self.ep(6, 100, 2950), stale=True), meta))


if __name__ == "__main__":
    unittest.main()
