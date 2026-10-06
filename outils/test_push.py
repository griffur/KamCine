"""Tests des diagnostics Web Push à destination d'un appareil."""
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("KAMCINE_DIR", tempfile.mkdtemp(prefix="kamcine-push-test-"))
import push


class TestPush(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="kamcine-push-test-")
        push.BASE = self.temp.name
        push.FILE = os.path.join(self.temp.name, "push.json")
        with open(push.FILE, "w", encoding="utf-8") as f:
            json.dump({"private": "private-test-key", "public": "public-test-key", "subject": "https://kamcine.example/", "subscriptions": [
                {"endpoint": "https://push.example/device-a", "keys": {"p256dh": "a", "auth": "b"}},
                {"endpoint": "https://push.example/device-b", "keys": {"p256dh": "c", "auth": "d"}},
            ]}, f)

    def tearDown(self):
        self.temp.cleanup()

    def test_test_push_cible_seulement_le_device_demande(self):
        with patch.object(push, "_envoyer_un") as envoyer:
            result = push.envoyer_test("https://push.example/device-a")
        self.assertEqual(result, {"ok": True})
        sub, payload, private, subject = envoyer.call_args.args
        self.assertEqual(sub["endpoint"], "https://push.example/device-a")
        self.assertEqual(private, "private-test-key")
        self.assertEqual(subject, "https://kamcine.example/")
        self.assertEqual(payload["titre"], "Notification de test")
        self.assertIsNone(payload["cible"])

    def test_test_push_ne_retourne_aucun_endpoint_ou_secret(self):
        class ErreurPush(Exception):
            response = None

        with patch.object(push, "_envoyer_un", side_effect=ErreurPush("détail privé")):
            result = push.envoyer_test("https://push.example/device-a")
        self.assertEqual(result, {"ok": False, "code": "service_indisponible"})
        self.assertNotIn("endpoint", json.dumps(result))
        self.assertNotIn("private-test-key", json.dumps(result))

    def test_abonnement_expire_est_retire(self):
        class Reponse:
            status_code = 410

        class ErreurPush(Exception):
            response = Reponse()

        with patch.object(push, "_envoyer_un", side_effect=ErreurPush()):
            result = push.envoyer_test("https://push.example/device-a")
        self.assertEqual(result, {"ok": False, "code": "abonnement_expire"})
        with open(push.FILE, encoding="utf-8") as f:
            self.assertEqual([x["endpoint"] for x in json.load(f)["subscriptions"]], ["https://push.example/device-b"])

    def test_cle_d_abonnement_refusee_est_identifiee_sans_exposer_la_reponse(self):
        class Reponse:
            status_code = 403
            def json(self):
                return {"reason": "VapidPkHashMismatch", "message": "private provider detail"}

        class ErreurPush(Exception):
            response = Reponse()

        with patch.object(push, "_envoyer_un", side_effect=ErreurPush()):
            result = push.envoyer_test("https://push.example/device-a")
        self.assertEqual(result, {"ok": False, "code": "abonnement_cle"})
        self.assertNotIn("private provider detail", json.dumps(result))

    def test_jwt_vapid_refuse_est_identifie_sans_exposer_le_corps(self):
        class Reponse:
            status_code = 403
            def json(self):
                return {"reason": "BadJwtToken", "message": "private provider detail"}

        class ErreurPush(Exception):
            response = Reponse()

        with patch.object(push, "_envoyer_un", side_effect=ErreurPush()):
            result = push.envoyer_test("https://push.example/device-a")
        self.assertEqual(result, {"ok": False, "code": "vapid_jwt"})
        self.assertNotIn("private provider detail", json.dumps(result))

    def test_abonnement_inconnu_ou_invalide_n_est_pas_envoye(self):
        with patch.object(push, "_envoyer_un") as envoyer:
            self.assertEqual(push.envoyer_test("https://push.example/unknown"), {"ok": False, "code": "abonnement_absent"})
            self.assertEqual(push.envoyer_test("http://push.example/device-a"), {"ok": False, "code": "abonnement_invalide"})
        envoyer.assert_not_called()

    def test_sujet_https_initialise_et_persiste_sans_etre_retourne(self):
        with open(push.FILE, "w", encoding="utf-8") as f:
            json.dump({"private": "private-test-key", "public": "public-test-key", "subject": "", "subscriptions": []}, f)
        def generer(data):
            data.update(private="private-test-key", public="public-test-key")

        with patch.dict(os.environ, {"KAMCINE_VAPID_SUBJECT": ""}), patch.object(push, "_ensure_keys", side_effect=generer):
            cle = push.cle_publique("https://kamcine-dev.example")
        self.assertEqual(cle, "public-test-key")
        with open(push.FILE, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["subject"], "https://kamcine-dev.example")
        self.assertEqual(len(data["subscriptions"]), 0)
        self.assertEqual(stat.S_IMODE(os.stat(push.FILE).st_mode), 0o600)

    def test_sujet_localhost_est_remplace_par_origine_https(self):
        with open(push.FILE, "w", encoding="utf-8") as f:
            json.dump({"private": "private-test-key", "public": "public-test-key",
                       "subject": "mailto:kamcine@localhost", "subscriptions": []}, f)
        with patch.dict(os.environ, {"KAMCINE_VAPID_SUBJECT": ""}):
            push.cle_publique("https://kamcine-dev.example")
        with open(push.FILE, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["subject"], "https://kamcine-dev.example")

    def test_sujet_vapid_absent_ou_invalide_refuse_sans_destruire_cles(self):
        with open(push.FILE, "w", encoding="utf-8") as f:
            json.dump({"private": "private-test-key", "public": "public-test-key", "subject": "", "subscriptions": []}, f)
        with patch.dict(os.environ, {"KAMCINE_VAPID_SUBJECT": ""}):
            with self.assertRaises(ValueError):
                push.cle_publique()
            with self.assertRaises(ValueError):
                push.cle_publique("http://kamcine.example")
        with open(push.FILE, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["private"], "private-test-key")


if __name__ == "__main__":
    unittest.main()
