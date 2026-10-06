"""Relance ciblée Radarr/Sonarr et garde-fous, sans accès réseau."""
import ast
import asyncio
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
donnees = tempfile.TemporaryDirectory(prefix="kamcine-recherche-arr-")
os.environ["KAMCINE_DIR"] = donnees.name
sys.modules.setdefault("requests", types.SimpleNamespace())
import bibliotheque


class RecherchesArr(unittest.TestCase):
    def setUp(self):
        self.cfg = {"radarr_hd": {"url": "http://radarr:7878", "cle": "a" * 24},
                    "sonarr_uhd": {"url": "http://sonarr:8989", "cle": "b" * 24}}
        self.reponse = types.SimpleNamespace(status_code=201, raise_for_status=lambda: None, json=lambda: {"id": 7})

    def lancer(self, type_, tmdb, qualite, donnees, saison=None, tvdb=None):
        with patch.object(bibliotheque, "config", return_value=self.cfg), \
             patch.object(bibliotheque, "_get", side_effect=donnees) as get, \
             patch.object(bibliotheque.requests, "post", return_value=self.reponse, create=True) as post, \
             patch.object(bibliotheque, "rafraichir_bientot"):
            resultat = bibliotheque.relancer_recherche(type_, tmdb, qualite, tvdb, saison)
        return resultat, get, post

    def test_radarr_lance_une_recherche_du_film_exact_et_invalide_ses_etats(self):
        film = {"id": 123, "tmdbId": 42, "title": "Dune", "monitored": True, "isAvailable": True, "hasFile": False}
        with patch.object(bibliotheque, "config", return_value=self.cfg), \
             patch.object(bibliotheque, "_get", side_effect=[[film], {"records": []}]) as get, \
             patch.object(bibliotheque.requests, "post", return_value=self.reponse, create=True) as post, \
             patch.object(bibliotheque, "rafraichir_bientot") as refresh:
            resultat = bibliotheque.relancer_recherche("movie", 42, "hd")
        self.assertEqual(resultat["service"], "Radarr HD")
        self.assertEqual(get.call_args_list[0].args[1:3], ("/movie", {"tmdbId": 42}))
        self.assertEqual(post.call_args.kwargs["json"], {"name": "MoviesSearch", "movieIds": [123]})
        refresh.assert_called_once()

    def test_sonarr_limite_la_recherche_a_la_saison_demande_e(self):
        serie = {"id": 9, "tmdbId": 42, "title": "Série", "monitored": True,
                 "statistics": {"episodeCount": 20, "episodeFileCount": 0},
                 "seasons": [{"seasonNumber": 2, "monitored": True,
                              "statistics": {"episodeCount": 8, "episodeFileCount": 0}}]}
        _, _, post = self.lancer("tv", 42, "uhd", [[serie], {"records": []}], saison=2)
        self.assertEqual(post.call_args.kwargs["json"], {"name": "SeasonSearch", "seriesId": 9, "seasonNumber": 2})
        self.assertEqual(post.call_args.args[0], "http://sonarr:8989/api/v3/command")

    def test_refuse_titre_absent_deja_en_telechargement_ou_non_surveille(self):
        films = {"id": 123, "tmdbId": 42, "monitored": True, "isAvailable": True, "hasFile": False}
        for entree in (([], {"records": []}), [[films], {"records": [{"movieId": 123, "status": "downloading"}]}],
                       [[{**films, "monitored": False}], {"records": []}]):
            with self.subTest(entree=entree), patch.object(bibliotheque, "config", return_value=self.cfg), \
                 patch.object(bibliotheque, "_get", side_effect=entree), \
                 patch.object(bibliotheque.requests, "post", create=True) as post:
                with self.assertRaises((LookupError, RuntimeError)):
                    bibliotheque.relancer_recherche("movie", 42, "hd")
                post.assert_not_called()

    def test_ne_relance_pas_une_saison_deja_partiellement_presente(self):
        serie = {"id": 9, "tmdbId": 42, "monitored": True,
                 "seasons": [{"seasonNumber": 2, "monitored": True,
                              "statistics": {"episodeCount": 8, "episodeFileCount": 1}}]}
        with patch.object(bibliotheque, "config", return_value=self.cfg), \
             patch.object(bibliotheque, "_get", side_effect=[[serie], {"records": []}]), \
             patch.object(bibliotheque.requests, "post", create=True) as post:
            with self.assertRaises(RuntimeError):
                bibliotheque.relancer_recherche("tv", 42, "uhd", saison=2)
            post.assert_not_called()

    def test_route_transmet_les_identifiants_et_journalise_la_reussite(self):
        arbre = ast.parse(Path(__file__).resolve().parents[1].joinpath("app/main.py").read_text())
        fonction = next(n for n in arbre.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "arr_recherche_relancer")
        fonction.decorator_list = []
        payload = {"type": "tv", "id": 77, "tvdb": 99, "saison": 2, "qualite": "uhd"}

        async def corps_json(_):
            return payload

        class Reponse(dict):
            def __init__(self, contenu, status_code):
                super().__init__(contenu)
                self.status_code = status_code

        relancer = Mock(return_value={"service": "Sonarr 4K", "titre": "Série"})
        journal = Mock()
        namespace = {"corps_json": corps_json, "bibliotheque": types.SimpleNamespace(relancer_recherche=relancer),
                     "erreurs": types.SimpleNamespace(journaliser_action=journal, message=Mock()), "JSONResponse": Reponse,
                     "Request": object}
        exec(compile(ast.Module(body=[fonction], type_ignores=[]), "app/main.py", "exec"), namespace)
        resultat = asyncio.run(namespace["arr_recherche_relancer"](None))
        self.assertTrue(resultat["ok"])
        relancer.assert_called_once_with("tv", 77, "uhd", 99, 2)
        journal.assert_called_once_with("Sonarr", "Recherche relancée via Sonarr 4K pour « Série » (saison 2).")
        payload["qualite"] = "autre"
        invalide = asyncio.run(namespace["arr_recherche_relancer"](None))
        self.assertEqual(invalide.status_code, 400)
        self.assertEqual(invalide["message"], "Qualité invalide")
        relancer.assert_called_once()


if __name__ == "__main__":
    try:
        unittest.main()
    finally:
        donnees.cleanup()
