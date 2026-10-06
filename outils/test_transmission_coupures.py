"""Point 83 : coupures passagères de Transmission sur la page Téléchargements, sans réseau réel."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
donnees = tempfile.TemporaryDirectory(prefix="kamcine-transmission-")
os.environ["KAMCINE_DIR"] = donnees.name

import requests
import config
import transmission

MAINTENANT = time.time()
TORRENTS = [{"hashString": "a" * 40, "name": "Film actif", "status": 4, "percentDone": .5, "addedDate": MAINTENANT - 10}]


class Horloge:
    def __init__(self):
        self.t = 1000.0

    def time(self):
        return self.t


def reponse(code=200, corps=None, entetes=None):
    r = Mock(status_code=code, headers=entetes or {})
    r.json.return_value = corps if corps is not None else {"result": "success", "arguments": {"torrents": TORRENTS}}
    r.raise_for_status = Mock()
    return r


class Coupures(unittest.TestCase):
    def setUp(self):
        Path(transmission.SECRETS).write_text(json.dumps({"transmission": {"url": "http://nas:9091/transmission/rpc", "user": "", "pass": ""}}))
        Path(config.CHEMIN).write_text(json.dumps({"transmission_actif": True}))
        transmission._cache_recents.clear()
        transmission._incident.clear()
        transmission._par_hash.clear()
        transmission._jeton["id"] = None
        self.horloge = Horloge()
        p = patch.object(transmission, "time", self.horloge);p.start();self.addCleanup(p.stop)

    def envois(self, *effets):
        post = Mock(side_effect=list(effets))
        p = patch.object(transmission.requests, "post", post);p.start();self.addCleanup(p.stop)
        return post

    def test_jeton_renouvele_puis_delais_separes(self):
        post = self.envois(reponse(409, entetes={"X-Transmission-Session-Id": "abc"}), reponse())
        self.assertEqual(transmission.recents()["en_cours"][0]["nom"], "Film actif")
        self.assertEqual(post.call_args.kwargs["headers"], {"X-Transmission-Session-Id": "abc"})
        self.assertEqual(post.call_args.kwargs["timeout"], (3, 20))

    def test_delai_passager_garde_la_derniere_lecture_puis_recupere(self):
        self.envois(reponse(), requests.exceptions.ReadTimeout("lecture"), reponse(corps={"result": "success", "arguments": {"torrents": []}}))
        premier = transmission.recents()
        self.horloge.t += 7
        ancien = transmission.recents()
        self.assertTrue(ancien["perime"])
        self.assertEqual((ancien["cause"], ancien["age_s"], ancien["echecs"]), ("delai", 7, 1))
        self.assertEqual(ancien["en_cours"], premier["en_cours"])
        self.horloge.t += 7
        frais = transmission.recents()
        self.assertNotIn("perime", frais)
        self.assertEqual(frais["en_cours"], [], "La récupération remplace bien les anciennes données")
        self.assertEqual(transmission._incident, {})

    def test_panne_durable_n_est_plus_masquee(self):
        self.envois(reponse(), *[requests.exceptions.ConnectionError("refusée")] * 20)
        transmission.recents()
        for _ in range(8):
            self.horloge.t += 7
            self.assertEqual(transmission.recents()["cause"], "hors_ligne")
        self.horloge.t += 7
        self.assertGreater(self.horloge.t - 1000, transmission.TOLERANCE_RECENTS_S)
        with self.assertRaises(requests.exceptions.ConnectionError) as e:
            transmission.recents()
        self.assertEqual(e.exception.cause, "hors_ligne")
        self.assertGreater(e.exception.echecs, 1)

    def test_premiere_lecture_en_echec_remonte_tout_de_suite(self):
        self.envois(requests.exceptions.ConnectTimeout("connexion"))
        with self.assertRaises(requests.exceptions.ConnectTimeout) as e:
            transmission.recents()
        self.assertEqual(e.exception.cause, "hors_ligne")

    def test_identifiants_et_reponse_invalide_jamais_masques(self):
        self.envois(reponse(), reponse(401), reponse(corps={"result": "erreur interne"}))
        transmission.recents()
        self.horloge.t += 7
        with self.assertRaises(PermissionError):
            transmission.recents()
        self.horloge.t += 7
        with self.assertRaises(RuntimeError) as e:
            transmission.recents()
        self.assertEqual(e.exception.cause, "reponse")

    def test_un_echec_tout_recent_n_est_pas_retente_aussitot(self):
        post = self.envois(reponse(), requests.exceptions.ReadTimeout("lecture"))
        transmission.recents()
        self.horloge.t += 7
        self.assertTrue(transmission.recents()["nouvel_echec"])
        self.horloge.t += 2
        suivant = transmission.recents()
        self.assertFalse(suivant["nouvel_echec"])
        self.assertEqual(post.call_count, 2)

    def test_la_fiche_reutilise_la_lecture_de_la_page_sans_nouvel_appel(self):
        # Lot 2.6.81, point 7 : même source de vérité que la page Téléchargements, par hash, sans torrent-get de plus.
        post = self.envois(reponse(corps={"result": "success", "arguments": {"torrents": [
            {"hashString": "A" * 40, "name": "13 Hours HD", "status": 4, "percentDone": .713, "sizeWhenDone": 100, "leftUntilDone": 29, "eta": 131},
            {"hashString": "b" * 40, "name": "13 Hours 4K", "status": 4, "percentDone": .015, "sizeWhenDone": 100, "leftUntilDone": 98, "eta": 4860}]}}))
        transmission.recents()
        with patch.object(transmission, "_relire_en_fond") as fond:
            infos = transmission.suivi(["a" * 40])
        fond.assert_not_called()
        self.assertEqual(post.call_count, 1)
        self.assertEqual(list(infos), ["a" * 40], "Seul le torrent demandé, jamais celui de l'autre qualité")
        self.assertEqual(infos["a" * 40]["progres"], 71.3)
        self.assertEqual(transmission.resume(infos)["progres"], 71)

    def test_suivi_relit_en_fond_sans_bloquer_et_oublie_une_lecture_trop_vieille(self):
        self.envois(reponse())
        transmission.recents()
        self.horloge.t += 7
        with patch.object(transmission, "_relire_en_fond") as fond:
            self.assertIn("a" * 40, transmission.suivi(["a" * 40]), "La dernière lecture sert pendant la relecture")
            fond.assert_called_once()
            self.horloge.t += transmission.TOLERANCE_RECENTS_S
            self.assertIsNone(transmission.suivi(["a" * 40]), "Trop vieille : Radarr et Sonarr reprennent la main")

    def test_suivi_sans_lecture_ni_transmission_actif(self):
        with patch.object(transmission, "_relire_en_fond") as fond:
            self.assertIsNone(transmission.suivi(["a" * 40]))
        fond.assert_called_once()
        Path(config.CHEMIN).write_text(json.dumps({"transmission_actif": False}))
        self.assertIsNone(transmission.suivi(["a" * 40]))

    def test_page_repond_tout_de_suite_avec_son_dernier_etat_pendant_une_relecture_lente(self):
        # Lot 2.6.82 : la page ne paie plus un torrent-get complet à chaque actualisation.
        post = self.envois(reponse())
        premier = transmission.recents_rapide()
        self.assertEqual(post.call_count, 1, "Sans lecture en mémoire, la première attend Transmission")
        self.horloge.t += 7
        with patch.object(transmission, "_relire_en_fond") as fond:
            vite = transmission.recents_rapide()
        fond.assert_called_once()
        self.assertEqual(post.call_count, 1, "Aucun appel bloquant : la relecture part en arrière plan")
        self.assertEqual(vite["en_cours"], premier["en_cours"])
        self.assertEqual(vite["age_s"], 7)
        self.assertNotIn("perime", vite, "Une lecture de quelques secondes n'est pas une coupure")

    def test_coupure_en_arriere_plan_signalee_puis_panne_revelee(self):
        self.envois(reponse(), requests.exceptions.ReadTimeout("lecture"), requests.exceptions.ConnectionError("refusée"))
        transmission.recents_rapide()
        self.horloge.t += 7
        self.assertTrue(transmission.recents()["perime"], "La relecture de fond échoue : coupure enregistrée")
        with patch.object(transmission, "_relire_en_fond"):
            ancien = transmission.recents_rapide()
        self.assertTrue(ancien["perime"])
        self.assertEqual((ancien["cause"], ancien["incident_debut"]), ("delai", 1007.0))
        self.horloge.t += transmission.TOLERANCE_RECENTS_S
        with self.assertRaises(requests.exceptions.ConnectionError):
            transmission.recents_rapide()

    def test_appels_simultanes_un_seul_torrent_get(self):
        transmission.time = time
        lent = threading.Event()

        def post(*a, **k):
            lent.wait(1)
            return reponse()
        with patch.object(transmission.requests, "post", side_effect=post) as envoi:
            fils = [threading.Thread(target=transmission.recents) for _ in range(4)]
            [f.start() for f in fils]
            time.sleep(.2)
            lent.set()
            [f.join(3) for f in fils]
        self.assertEqual(envoi.call_count, 1)


class Route(unittest.TestCase):
    """La route /telechargements : cause précise, données anciennes signalées, enrichissements séparés de Transmission."""
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
        from app import main
        cls.m = main

    def setUp(self):
        m = self.m
        Path(transmission.SECRETS).write_text(json.dumps({"transmission": {"url": "http://nas:9091/transmission/rpc"}}))
        Path(config.CHEMIN).write_text(json.dumps({"transmission_actif": True}))
        for objet, nom, valeur in [(m.bibliotheque, "medias_par_hash", Mock(return_value={})),
                                   (m.bibliotheque, "medias_par_nom_torrent", Mock(return_value={})),
                                   (m.notifications, "observer_transmission", Mock(return_value=2)),
                                   (m.notifications, "lister", Mock(return_value={"non_lues": 2})),
                                   (m.erreurs, "message", Mock(return_value="")),
                                   (m.erreurs, "journaliser_action", Mock())]:
            p = patch.object(objet, nom, valeur);p.start();self.addCleanup(p.stop)

    def recents(self, effet):
        p = patch.object(self.m.transmission, "recents_rapide", Mock(side_effect=[effet] if isinstance(effet, Exception) else None,
                                                              return_value=effet))
        p.start();self.addCleanup(p.stop)

    def test_donnees_anciennes_signalees_sans_erreur_ni_notification(self):
        self.recents({"en_cours": [{"nom": "Film", "hash": "a" * 40}], "termines": [], "perime": True, "age_s": 12,
                      "cause": "delai", "echecs": 1, "nouvel_echec": True, "incident_debut": 500.0})
        self.m.JOURNAL_COUPURE.clear()
        r = self.m.telechargements()
        self.assertTrue(r["ok"])
        self.assertEqual((r["perime"], r["age_s"], r["cause"]), (True, 12, "delai"))
        self.assertEqual(r["en_cours"][0]["nom"], "Film")
        self.m.notifications.observer_transmission.assert_not_called()
        self.m.erreurs.journaliser_action.assert_called_once()

    def test_coupure_deja_journalisee_ne_se_repete_pas(self):
        self.recents({"en_cours": [], "termines": [], "perime": True, "age_s": 20, "cause": "delai", "echecs": 3,
                      "nouvel_echec": False, "incident_debut": 600.0})
        self.m.JOURNAL_COUPURE.clear()
        self.m.telechargements()
        self.m.telechargements()
        self.m.erreurs.journaliser_action.assert_called_once()

    def test_vraie_panne_avec_cause_precise(self):
        for erreur, cause, texte in [(requests.exceptions.ConnectionError("refusée"), "hors_ligne", "injoignable"),
                                     (requests.exceptions.ReadTimeout("lecture"), "delai", "ne répond pas"),
                                     (PermissionError("refus"), "identifiants", "identifiant"),
                                     (RuntimeError("bizarre"), "reponse", "inattendue")]:
            with self.subTest(cause=cause):
                self.recents(erreur)
                r = self.m.telechargements()
                corps = json.loads(r.body)
                self.assertEqual(r.status_code, 502)
                self.assertEqual(corps["cause"], cause)
                self.assertIn(texte, corps["message"])

    def test_panne_d_enrichissement_n_est_pas_une_panne_transmission(self):
        self.recents({"en_cours": [{"nom": "Film", "hash": "a" * 40}], "termines": []})
        self.m.notifications.observer_transmission.side_effect = RuntimeError("fichier illisible")
        self.m.bibliotheque.medias_par_hash.side_effect = RuntimeError("Radarr absent")
        r = self.m.telechargements()
        self.assertTrue(r["ok"])
        self.assertNotIn("perime", r)
        self.assertEqual(r["en_cours"][0]["nom"], "Film")


if __name__ == "__main__":
    unittest.main()
