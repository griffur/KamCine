"""Diagnostic synthétique des API Radarr/Sonarr, sans réseau ni fuite de configuration."""
import sys
import unittest
import asyncio
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from app import main


class DiagnosticServices(unittest.TestCase):
    def test_resume_les_messages_connus_et_ne_renvoie_pas_un_journal_long(self):
        self.assertEqual(main.resume_sante("Indexers are unavailable"), "Un indexeur est temporairement indisponible")
        self.assertEqual(main.resume_sante("Insufficient disk space on configured root"), "Espace de stockage insuffisant")
        self.assertEqual(main.resume_sante("Database error: " + " détail technique" * 30), "La base de données du service nécessite une vérification")

    def test_synthetise_health_et_ne_retourne_pas_les_secrets(self):
        instances = [{"nom": "radarr_hd", "titre": "Radarr HD", "configuree": True,
                      "url": "https://radarr.example.invalid", "cle": "cle-secrete"}]
        api = Mock(side_effect=[{"version": "5.2.1", "updateAvailable": False},
                                [{"type": "warning", "message": "Indexeur temporairement indisponible sur http://192.168.1.5:9696"},
                                 {"type": "info", "message": "Information ignorée"}]])
        with patch.object(main.bibliotheque, "etat_config", return_value=instances), \
             patch.object(main.bibliotheque, "_get", api), \
             patch.object(main, "charger", return_value={"url_publique_radarr_hd": ""}), \
             patch.object(main, "lire_secrets", return_value={"tmdb": "tmdb-secret"}), \
             patch.object(main.trakt, "configuree", return_value=False), \
             patch.object(main.trakt, "connectee", return_value=False), \
             patch.object(main, "etat_appletv", return_value={"configuree": False, "connectee": False}), \
             patch.object(main.transmission, "etat", return_value={"configuree": False, "actif": False}), \
             patch.object(main.integrations, "hue", return_value=None), \
             patch.object(main.denon, "actif", return_value=False):
            resultat = main.services_etat()
        radarr = resultat["instances"][0]
        self.assertEqual(radarr["etat"], "avertissement")
        self.assertEqual(radarr["resume"], "Un indexeur est temporairement indisponible")
        self.assertEqual(len(radarr["details"]), 1)
        self.assertNotIn("192.168.1.5", str(radarr["details"]))
        self.assertNotIn("url", str(resultat))
        self.assertNotIn("cle-secrete", str(resultat))
        self.assertNotIn("tmdb-secret", str(resultat))
        self.assertEqual([x["titre"] for x in resultat["autres"]],
                         ["TMDB", "Apple TV", "Overseerr", "Trakt", "Transmission", "Hue", "Denon"])

    def test_trakt_a_un_etat_api_sans_detail_secret(self):
        with patch.object(main.trakt, "configuree", return_value=False), patch.object(main.trakt, "connectee", return_value=False):
            self.assertEqual(main.trakt_service_etat(), {"titre": "Trakt", "etat": "inconnu", "resume": "Non configuré"})
        with patch.object(main.trakt, "configuree", return_value=True), patch.object(main.trakt, "connectee", return_value=False):
            self.assertEqual(main.trakt_service_etat(), {"titre": "Trakt", "etat": "avertissement", "resume": "Connexion requise"})
        response = Mock(status_code=200)
        with patch.object(main.trakt, "configuree", return_value=True), \
             patch.object(main.trakt, "connectee", return_value=True), \
             patch.object(main.trakt, "_appel", return_value=response) as appel:
            etat = main.trakt_service_etat()
        self.assertEqual(etat, {"titre": "Trakt", "etat": "ok", "resume": "Connecté · API accessible"})
        appel.assert_called_once_with("GET", "/users/settings", delai=5)
        self.assertNotIn("secret", str(etat).lower())

    def test_trakt_auth_refusee_est_a_surveiller_et_panne_est_rouge(self):
        for code, attendu in ((401, "avertissement"), (503, "erreur")):
            with patch.object(main.trakt, "configuree", return_value=True), \
                 patch.object(main.trakt, "connectee", return_value=True), \
                 patch.object(main.trakt, "_appel", return_value=Mock(status_code=code)):
                self.assertEqual(main.trakt_service_etat()["etat"], attendu)

    def test_changement_identifiants_trakt_revoque_ancienne_session_oauth(self):
        class Request:
            async def json(self):
                return {"client_id": "nouveau-client-id-123456", "client_secret": "nouveau-secret-123456"}
        with patch.object(main.trakt, "config", return_value={"client_id": "ancien-client-id-123456",
                                                               "client_secret": "ancien-secret-123456",
                                                               "access_token": "jeton-prive"}), \
             patch.object(main.trakt, "deconnecter") as deconnecter, \
             patch.object(main.trakt, "sauver") as sauver:
            resultat = asyncio.run(main.trakt_config(Request()))
        self.assertEqual(resultat, {"ok": True})
        deconnecter.assert_called_once_with()
        sauver.assert_called_once_with(client_id="nouveau-client-id-123456", client_secret="nouveau-secret-123456")
        self.assertNotIn("secret", str(resultat).lower())

    def test_cle_refusee_donne_un_message_court(self):
        with patch.object(main.bibliotheque, "etat_config", return_value=[{"nom": "sonarr_hd", "titre": "Sonarr HD", "configuree": True}]), \
             patch.object(main.bibliotheque, "_get", side_effect=PermissionError()), \
             patch.object(main, "charger", return_value={}), \
             patch.object(main, "lire_secrets", return_value={}), \
             patch.object(main.trakt, "configuree", return_value=False), \
             patch.object(main.trakt, "connectee", return_value=False), \
             patch.object(main, "etat_appletv", return_value={"configuree": False, "connectee": False}), \
             patch.object(main.transmission, "etat", return_value={"configuree": False, "actif": False}), \
             patch.object(main.integrations, "hue", return_value=None), \
             patch.object(main.denon, "actif", return_value=False):
            resultat = main.services_etat()
        self.assertEqual(resultat["instances"][0]["resume"], "Clé API refusée")


if __name__ == "__main__":
    unittest.main()
