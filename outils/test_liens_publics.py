"""Validation et conservation des adresses publiques, sans réseau."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config


class LiensPublics(unittest.TestCase):
    def test_adresses_https(self):
        for entree, attendu in (
            ("https://radarr.example.org/", "https://radarr.example.org"),
            (" https://SONARR.example.org:443/base/ ", "https://sonarr.example.org/base"),
            ("", ""),
        ):
            with self.subTest(url=entree):
                self.assertEqual(config.url_publique(entree), attendu)

    def test_aucune_ip_ni_adresse_locale(self):
        for entree in (
            "http://radarr.example.org", "https://192.168.1.48", "https://8.8.8.8",
            "https://127.1", "https://[::1]", "https://localhost", "https://nas.local",
            "https://nas.lan", "https://nas.internal", "https://nas.home", "https://nas",
            "https://radarr.example.org:7878", "https://radarr.example.org:abc",
        ):
            with self.subTest(url=entree), self.assertRaises(ValueError):
                config.url_publique(entree)

    def test_aucun_secret_ni_schema_actif(self):
        for entree in (
            "javascript:alert(1)", "//radarr.example.org", "https://user:pass@radarr.example.org",
            "https://radarr.example.org/?apikey=secret", "https://radarr.example.org/#secret",
            "https://radarr.example.org\\evil", "https://radarr.example.org/avec un espace",
            "https://radarr.example.org/\nchemin", "https://radarr.example.org/" + "x" * 500,
        ):
            with self.subTest(url=entree), self.assertRaises(ValueError):
                config.url_publique(entree)

    def test_correspondance_et_persistance(self):
        services = ("radarr_hd", "radarr_uhd", "sonarr_hd", "sonarr_uhd", "overseerr")
        with tempfile.TemporaryDirectory() as dossier, patch.object(config, "CHEMIN", str(Path(dossier) / "reglages.json")):
            r = config.charger()
            r["overseerr_url"] = "http://127.0.0.1:5055"
            for service in services:
                cle = "url_publique_" + service
                r[cle] = config.nettoyer(cle, "https://" + service.replace("_", "-") + ".example.org/")
            config.sauver(r)
            relu = config.charger()
            for service in services:
                self.assertEqual(relu["url_publique_" + service], "https://" + service.replace("_", "-") + ".example.org")
            self.assertEqual(relu["overseerr_url"], "http://127.0.0.1:5055")

    def test_ancienne_configuration_reste_compatible(self):
        with tempfile.TemporaryDirectory() as dossier, patch.object(config, "CHEMIN", str(Path(dossier) / "reglages.json")):
            Path(config.CHEMIN).write_text(json.dumps({"home_decouvrir": False, "home_sec_reprendre": True,
                                                       "home_tuile_catalogue": False}))
            r = config.charger()
            self.assertFalse(r["home_decouvrir"])
            self.assertTrue(r["home_sec_reprendre"])
            self.assertFalse(r["home_tuile_catalogue"])
            self.assertIn("url_publique_radarr_hd", r)

    def test_ancien_rappel_desactive_reste_desactive_apres_migration(self):
        with tempfile.TemporaryDirectory() as dossier, patch.object(config, "CHEMIN", str(Path(dossier) / "reglages.json")):
            Path(config.CHEMIN).write_text(json.dumps({"rappel_min": 0}))
            r = config.charger()
            self.assertFalse(r["rappel_actif"])
            Path(config.CHEMIN).write_text(json.dumps({"rappel_min": 30}))
            self.assertTrue(config.charger()["rappel_actif"])


if __name__ == "__main__":
    unittest.main()
