"""Régressions du feedback et du journal, sans réseau ni matériel.

Exécuter avec les dépendances du service : python outils/test_feedback.py.
"""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
donnees = tempfile.TemporaryDirectory(prefix="kamcine-feedback-")
os.environ["KAMCINE_DIR"] = donnees.name

import erreurs
from app import main


class Feedback(unittest.TestCase):
    def setUp(self):
        erreurs.effacer()
        Path(main.SECRETS).write_text("{}")
        main.etat["lignes"] = []
        self.reseau = patch("requests.sessions.Session.request", side_effect=AssertionError("Réseau interdit pendant ces tests"))
        self.reseau.start()
        self.addCleanup(self.reseau.stop)

    def reponse(self, reponse):
        return json.loads(reponse.body) if hasattr(reponse, "body") else reponse

    def test_trace_complete_et_secrets_masques(self):
        secret = "cle confidentielle/avec+symboles"
        Path(main.SECRETS).write_text(json.dumps({"tmdb": secret, "transmission": {"url": "http://nas:9091", "pass": "autre_secret"}}))
        try:
            raise RuntimeError("HTTPConnectionPool http://nas:9091 " + secret + " autre_secret " + "x" * 600)
        except RuntimeError as e:
            public = erreurs.message(e, "Connexion impossible à Transmission.")
        journal = "\n".join(erreurs.lire())
        self.assertNotIn("HTTPConnectionPool", public)
        self.assertIn("Traceback", journal)
        self.assertIn("x" * 600, journal)
        self.assertIn("http://nas:9091", journal)
        self.assertNotIn(secret, journal)
        self.assertNotIn("autre_secret", journal)
        self.assertEqual(os.stat(erreurs.CHEMIN).st_mode & 0o777, 0o600)

    def test_cle_non_enregistree_et_url(self):
        texte = erreurs.masquer("http://user:secret@nas/?api_key=inconnue&x=1 nouveau%2Fsecret", ("nouveau/secret",))
        for secret in ("user:secret", "inconnue", "nouveau%2Fsecret"):
            self.assertNotIn(secret, texte)
        self.assertIn("&x=1", texte)

    def test_transmission_panne_test_et_enregistrement(self):
        for route in (main.transmission_tester, main.transmission_config):
            with self.subTest(route=route.__name__), patch.object(main, "corps_json", AsyncMock(return_value={"url": "http://nas:9091", "mot_de_passe": "secret_saisi"})), patch.object(main.transmission, "tester", side_effect=RuntimeError("HTTPConnectionPool secret_saisi " + "detail" * 100)):
                r = self.reponse(asyncio.run(route(None)))
            self.assertFalse(r["ok"])
            self.assertEqual(r["message"], "Connexion impossible à Transmission. Consulte le journal pour plus d’informations.")
            self.assertNotIn("secret_saisi", "\n".join(erreurs.lire()))
        self.assertEqual(json.loads(Path(main.SECRETS).read_text()), {})

    def test_transmission_validation_et_refus(self):
        for erreur,attendu in ((ValueError("L'adresse doit ressembler à http://nas:9091"), "L'adresse"), (PermissionError("détail refus"), "Transmission refuse")):
            with patch.object(main, "corps_json", AsyncMock(return_value={})), patch.object(main.transmission, "tester", side_effect=erreur):
                r = self.reponse(asyncio.run(main.transmission_tester(None)))
            self.assertTrue(r["message"].startswith(attendu))

    def test_tous_les_tests_services_en_panne(self):
        secrets = {"tmdb": "cle_tmdb", "omdb": "abcdef12", "youtube": "cle_youtube", "overseerr": "cle_overseerr", "arr": {n: {"cle": "cle_arr", "url": "http://nas:7878"} for n in main.bibliotheque.NOMS}, "transmission": {"url": "http://nas:9091"}, "trakt": {"access_token": "jeton_trakt"}}
        Path(main.SECRETS).write_text(json.dumps(secrets))
        panne = RuntimeError("HTTPConnectionPool : " + "cause complète " * 40)
        for nom in ["tmdb", "omdb", "youtube", "overseerr", "transmission", "trakt"] + main.bibliotheque.NOMS:
            with self.subTest(service=nom), patch.object(main, "tmdb_get", side_effect=panne), patch.object(main.bibliotheque, "ov_appel", side_effect=panne), patch.object(main.bibliotheque, "tester", side_effect=panne), patch.object(main.transmission, "tester", side_effect=panne), patch.object(main.filmsactu, "tester_cle", side_effect=panne), patch.object(main.trakt, "_appel", side_effect=panne), patch.object(main.requests, "get", side_effect=panne):
                r = self.reponse(main.services_tester(nom))
            self.assertFalse(r["ok"], nom)
            self.assertNotIn("HTTPConnectionPool", r["message"])
            self.assertLess(len(r["message"]), 130)

    def test_journal_ne_modifie_pas_la_seance(self):
        main.etat["lignes"] = [(1, "Film : exemple")]
        erreurs.message(RuntimeError("ENTRACTE puis Séance terminée"))
        with patch.object(main, "en_cours", return_value=True), patch.object(main, "lignes_courantes", return_value=main.etat["lignes"]):
            self.assertIn("Erreurs techniques", main.journal()["log"])
            self.assertEqual(main.log_effacer().status_code, 409)
        self.assertEqual(main.etat["lignes"], [(1, "Film : exemple")])
        with patch.object(main, "en_cours", return_value=False):
            self.assertTrue(main.log_effacer()["ok"])
        self.assertEqual(erreurs.lire(), [])

    def test_rotation(self):
        Path(erreurs.CHEMIN).write_text("x" * (1024 * 1024 + 1))
        erreurs.message(RuntimeError("Nouvelle erreur"))
        self.assertTrue(Path(erreurs.CHEMIN + ".1.log").exists())
        self.assertIn("Nouvelle erreur", "\n".join(erreurs.lire()))

    def test_relance_radarr_journalisee_comme_succes(self):
        with patch.object(main, "corps_json", AsyncMock(return_value={"type": "movie", "id": 42, "qualite": "hd"})), \
             patch.object(main.bibliotheque, "relancer_recherche", return_value={"service": "Radarr HD", "titre": "Dune"}) as relance:
            r = self.reponse(asyncio.run(main.arr_recherche_relancer(None)))
        self.assertTrue(r["ok"])
        self.assertIn("Radarr HD", r["message"])
        relance.assert_called_once_with("movie", 42, "hd", None, None)
        evenement = main.journal()["evenements"][0]
        self.assertEqual((evenement["source"], evenement["type"]), ("Radarr", "succes"))
        self.assertIn("Dune", evenement["message"])

    def test_exception_non_geree(self):
        request = type("Request", (), {"url": type("URL", (), {"path": "/test"})(), "cookies": {"kc_compte": "jeton"},
                                       "state": type("State", (), {})()})()
        with patch.object(main.comptes, "session", return_value=({"id": 1, "admin": True}, 0)):
            r = asyncio.run(main.verrou(request, AsyncMock(side_effect=RuntimeError("panne technique"))))
        self.assertEqual(r.status_code, 500)
        self.assertNotIn("panne technique", self.reponse(r)["message"])
        self.assertIn("panne technique", "\n".join(erreurs.lire()))


if __name__ == "__main__":
    try:
        unittest.main()
    finally:
        donnees.cleanup()
