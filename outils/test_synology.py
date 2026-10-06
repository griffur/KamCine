import os
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import synology


class Reponse:
    def __init__(self, contenu):
        self.contenu = contenu

    def raise_for_status(self):
        pass

    def json(self):
        return self.contenu


class SessionFausse:
    def __init__(self):
        self.appels = 0

    def get(self, url, **kwargs):
        self.appels += 1
        if url.endswith("query.cgi"):
            return Reponse({"success": True, "data": {
                "SYNO.API.Auth": {"path": "auth.cgi", "maxVersion": 6},
                "SYNO.Core.System": {"path": "entry.cgi", "maxVersion": 3},
                "SYNO.Core.Storage.Volume": {"path": "entry.cgi", "maxVersion": 1}}})
        if url.endswith("auth.cgi"):
            return Reponse({"success": True, "data": {"sid": "private-session"}})
        params = kwargs["params"]
        if params["api"] == "SYNO.Core.System":
            return Reponse({"success": True, "data": {"model": "DS Mock", "uptime": 86400, "version_string": "DSM Mock"}})
        return Reponse({"success": True, "data": {"volumes": [{"volume_path": "/volume1",
            "size_total": 1000, "size_used": 640}, {"volume_path": "/volume2",
            "size_total": 2000, "size_used": 500}]}})

    def close(self):
        pass


class SynologyTests(unittest.TestCase):
    def setUp(self):
        synology._cache.update(t=0, etat=None)

    def test_non_configure_ne_retourne_aucun_secret(self):
        with patch.dict(os.environ, {}, clear=True):
            resultat = synology.lire(force=True)
        self.assertFalse(resultat["configure"])
        self.assertNotIn("password", resultat)
        self.assertNotIn("user", resultat)

    def test_infos_normalisees_et_cachees(self):
        session = SessionFausse()
        with patch.dict(os.environ, {"SYNOLOGY_URL": "https://nas.local:5001",
                                     "SYNOLOGY_USER": "kc", "SYNOLOGY_PASSWORD": "secret"}, clear=True), \
             patch.dict(sys.modules, {"requests": SimpleNamespace(Session=lambda: session)}):
            resultat = synology.lire(force=True)
            appels = session.appels
            seconde_lecture = synology.lire()
        self.assertTrue(resultat["en_ligne"])
        self.assertEqual(resultat["modele"], "DS Mock")
        self.assertEqual(resultat["uptime"], 86400)
        self.assertEqual(resultat["pourcentage"], 64.0)
        self.assertEqual(resultat["disponible"], 360)
        self.assertEqual(resultat["adresse"], "nas.local")
        self.assertEqual(len(resultat["volumes"]), 2)
        self.assertEqual(resultat["volumes"][1]["disponible"], 1500)
        self.assertEqual(session.appels, appels)
        self.assertEqual(seconde_lecture["nom"], "NAS Synology")
        self.assertNotIn("secret", str(resultat))
        self.assertEqual(resultat["version_dsm"], "DSM Mock")

    def test_configuration_en_test_ne_remplit_pas_le_cache_et_ne_renvoie_pas_les_identifiants(self):
        session = SessionFausse()
        conf = {"url": "https://nas.local:5001", "utilisateur": "kc", "mot_de_passe": "secret",
                "nom": "Salon", "verifier_tls": True}
        with patch.dict(os.environ, {}, clear=True), \
             patch("integrations.synology", return_value={"url": "", "utilisateur": "", "mot_de_passe": ""}), \
             patch.dict(sys.modules, {"requests": SimpleNamespace(Session=lambda: session)}):
            resultat = synology.lire(force=True, configuration=conf)
        self.assertTrue(resultat["en_ligne"])
        self.assertEqual(resultat["nom"], "Salon")
        self.assertIsNone(synology._cache["etat"], "un test de formulaire ne doit pas remplacer le cache de l'installation")
        self.assertNotIn("utilisateur", resultat)
        self.assertNotIn("mot_de_passe", resultat)
        self.assertNotIn("secret", str(resultat))

    def test_echec_dsm_conserve_le_cache_recent_sans_dire_kamcine_injoignable(self):
        synology._cache.update(t=time.time(), etat={"configure": True, "en_ligne": True, "nom": "Mon NAS",
                                                    "volumes": [{"nom": "volume1", "total": 100, "utilise": 50}]})
        with patch.dict(os.environ, {"SYNOLOGY_URL": "https://nas.local:5001", "SYNOLOGY_USER": "kc",
                                     "SYNOLOGY_PASSWORD": "secret"}, clear=True), \
             patch.dict(sys.modules, {"requests": SimpleNamespace(Session=lambda: SimpleNamespace(
                 get=lambda *a, **k: (_ for _ in ()).throw(TimeoutError("timeout")), close=lambda: None))}):
            resultat = synology.lire(force=True)
        self.assertTrue(resultat["en_ligne"], "le dernier état DSM récent reste consultable")
        self.assertFalse(resultat["actualise"])
        self.assertEqual(resultat["nom"], "Mon NAS")
        self.assertNotIn("secret", str(resultat))


if __name__ == "__main__":
    unittest.main()
