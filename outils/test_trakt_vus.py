"""Tests hors réseau de la synchronisation Trakt des films, séries et épisodes vus."""
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_donnees = tempfile.TemporaryDirectory(prefix="kamcine-trakt-")
os.environ["KAMCINE_DIR"] = _donnees.name
sys.modules.setdefault("requests", types.SimpleNamespace())
import trakt


class Reponse:
    def __init__(self, donnees, pages=1, code=200):
        self._donnees = donnees
        self.headers = {"X-Pagination-Page-Count": str(pages)}
        self.status_code = code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("HTTP %d" % self.status_code)

    def json(self):
        return self._donnees


class SynchronisationVus(unittest.TestCase):
    def setUp(self):
        trakt._cache.update(t=0, data=None)

    def test_lit_toutes_les_pages_et_le_detail_episodes(self):
        films1 = [{"movie": {"ids": {"tmdb": 11}}}]
        films2 = [{"movie": {"ids": {"tmdb": 22}}}]
        series = [{"show": {"ids": {"tmdb": 33}}, "seasons": [
            {"number": 2, "episodes": [{"number": 4}, {"number": 5}]},
            {"number": 3, "episodes": [{"number": 1}]},
        ]}]
        reponses = {
            "/sync/watched/movies?page=1&limit=250": Reponse(films1, pages=2),
            "/sync/watched/movies?page=2&limit=250": Reponse(films2, pages=2),
            "/sync/watched/shows?extended=progress&page=1&limit=100": Reponse(series),
        }
        with patch.object(trakt, "connectee", return_value=True), \
                patch.object(trakt, "_appel", side_effect=lambda m, p: reponses[p]) as appel:
            trakt.rafraichir_vus()
        vus, pret = trakt.vus_rapide()
        self.assertTrue(pret)
        self.assertEqual(vus["films"], {11, 22})
        self.assertEqual(vus["series"], {33})
        self.assertEqual(vus["episodes"], {(33, 2, 4), (33, 2, 5), (33, 3, 1)})
        self.assertEqual([x.args[1] for x in appel.call_args_list], list(reponses))
        sauvegarde = json.loads(Path(trakt.VUS_FICHIER).read_text(encoding="utf-8"))
        self.assertEqual(sauvegarde["films"], [11, 22])

    def test_arrete_une_page_repetee_si_la_pagination_est_absente(self):
        reponse = Reponse([{"movie": {"ids": {"tmdb": 44}}}])
        reponse.headers = {}
        with patch.object(trakt, "_appel", return_value=reponse) as appel:
            items = trakt._pages_vus("/sync/watched/movies", 1)
        self.assertEqual(len(items), 1)
        self.assertEqual(appel.call_count, 2)

    def test_echec_ne_remplace_pas_le_cache_publie(self):
        anciens = {"films": {7}, "series": {8}, "episodes": {(8, 1, 2)}}
        trakt._cache.update(t=123, data=anciens)
        with patch.object(trakt, "connectee", return_value=True), \
                patch.object(trakt, "_appel", side_effect=RuntimeError("panne")), \
                self.assertRaises(RuntimeError):
            trakt.rafraichir_vus()
        self.assertIs(trakt._cache["data"], anciens)
        self.assertEqual(trakt._cache["t"], 123)

    def test_ecrit_plusieurs_episodes_en_une_requete_groupee(self):
        reponse = Reponse({"added": {"episodes": 3}})
        with patch.object(trakt, "_appel", return_value=reponse) as appel:
            trakt.ecrire_episodes_vus(77, [(2, 3), (1, 2), (1, 1), (2, 3)])
        appel.assert_called_once()
        methode, chemin, corps = appel.call_args.args[:3]
        self.assertEqual((methode, chemin), ("POST", "/sync/history"))
        self.assertEqual(corps["shows"][0]["ids"], {"tmdb": 77})
        saisons = corps["shows"][0]["seasons"]
        self.assertEqual([s["number"] for s in saisons], [1, 2])
        self.assertEqual([e["number"] for e in saisons[0]["episodes"]], [1, 2])
        self.assertTrue(all(e.get("watched_at") for s in saisons for e in s["episodes"]))

    def test_profil_persistant_ne_retourne_ni_jeton_ni_secret(self):
        class ProfilResponse:
            status_code = 200
            def __init__(self, payload): self.payload = payload
            def json(self): return self.payload

        secret_file = Path(_donnees.name) / "profil-secrets.json"
        profil_file = Path(_donnees.name) / "profil-vus.json"
        secret_file.write_text(json.dumps({"trakt": {"client_id": "id-public", "client_secret": "secret-prive",
                                                "access_token": "jeton-prive", "refresh_token": "refresh-prive"}}))
        réponses = [
            ProfilResponse({"user": {"username": "kamcine-user", "name": "Prénom", "ids": {"slug": "kamcine-user"}}}),
            ProfilResponse({"user": {"username": "kamcine-user", "name": "Prénom", "images": {"avatar": {"full": "https://images.trakt.tv/avatar.jpg"}}}}),
        ]
        with patch.object(trakt, "SECRETS", str(secret_file)), patch.object(trakt, "VUS_FICHIER", str(profil_file)), \
             patch.object(trakt, "connectee", return_value=True), patch.object(trakt, "_appel", side_effect=réponses) as appel:
            profil = trakt.profil_utilisateur()
            état = trakt.etat()
        self.assertEqual(profil, {"username": "kamcine-user", "name": "Prénom", "avatar": "https://images.trakt.tv/avatar.jpg"})
        self.assertEqual(état["profil"], profil)
        self.assertIsNone(état["client_id"])
        self.assertNotIn("secret-prive", json.dumps(état))
        self.assertNotIn("jeton-prive", json.dumps(état))
        self.assertNotIn("refresh-prive", json.dumps(état))
        self.assertEqual([call.args[1] for call in appel.call_args_list],
                         ["/users/settings", "/users/kamcine-user?extended=full"])

    def test_etat_deconnecte_expose_client_id_seulement(self):
        secret_file = Path(_donnees.name) / "deconnecte-secrets.json"
        secret_file.write_text(json.dumps({"trakt": {"client_id": "id-public", "client_secret": "secret-prive"}}))
        with patch.object(trakt, "SECRETS", str(secret_file)):
            état = trakt.etat()
        self.assertTrue(état["configuree"])
        self.assertFalse(état["connectee"])
        self.assertEqual(état["client_id"], "id-public")
        self.assertNotIn("secret-prive", json.dumps(état))

    def test_oauth_device_code_persiste_jetons_et_profil_sans_les_exposer(self):
        class TokenResponse(Reponse):
            status_code = 200
        secret_file = Path(_donnees.name) / "oauth-secrets.json"
        profile_file = Path(_donnees.name) / "oauth-vus.json"
        secret_file.write_text(json.dumps({"trakt": {"client_id": "id-public", "client_secret": "secret-prive",
                                                "device_code": "device-prive"}}))
        responses = [
            Reponse({"user": {"username": "cine-user", "name": "Ciné User", "ids": {"slug": "cine-user"}}}),
            Reponse({"user": {"username": "cine-user", "images": {"avatar": {"full": "https://images.trakt.tv/avatar.jpg"}}}}),
        ]
        token = TokenResponse({"access_token": "access-prive", "refresh_token": "refresh-prive", "created_at": 1, "expires_in": 3600})
        travailleurs = []
        class ThreadDiffere:
            def __init__(self, target, daemon=False): self.target = target
            def start(self): travailleurs.append(self)
        with patch.object(trakt, "SECRETS", str(secret_file)), patch.object(trakt, "VUS_FICHIER", str(profile_file)), \
             patch.object(trakt.requests, "post", return_value=token, create=True) as post, \
             patch.object(trakt, "_appel", side_effect=responses), \
             patch.object(trakt.threading, "Thread", ThreadDiffere):
            result = trakt.verifier_appareil()
            etat = trakt.etat()
            self.assertEqual(result["etat"], "connecte")
            self.assertNotIn("profil", result, "La confirmation OAuth ne dépend pas du chargement du profil")
            self.assertTrue(etat["connectee"])
            self.assertIsNone(etat["profil"], "La connexion est disponible avant le profil")
            travailleurs[0].target()
            etat = trakt.etat()
        self.assertEqual(etat["profil"]["avatar"], "https://images.trakt.tv/avatar.jpg")
        self.assertNotIn("secret-prive", json.dumps(etat))
        self.assertNotIn("access-prive", json.dumps(result))
        post.assert_called_once()


if __name__ == "__main__":
    unittest.main()
