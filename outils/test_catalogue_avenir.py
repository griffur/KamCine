import ast
import datetime
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "app" / "main.py"
ARBRE = ast.parse(SOURCE.read_text(encoding="utf-8"))
FONCTION = next(x for x in ARBRE.body if isinstance(x, ast.FunctionDef) and x.name == "catalogue_a_venir")
FONCTION.decorator_list = []


class Reponse:
    def __init__(self, contenu, status_code=200):
        self.contenu = contenu
        self.status_code = status_code


class CatalogueAvenirTests(unittest.TestCase):
    def setUp(self):
        self.appels = []
        self.env = {
            "time": __import__("time"),
            "planning": __import__("planning"),
            "cache_listes": {},
            "tmdb_get": self.tmdb_get,
            "carte": lambda x: {"id": x["id"], "type": x["media_type"], "titre": x.get("title") or x.get("name")},
            "erreurs": type("Erreurs", (), {"message": staticmethod(lambda e, m: m)})(),
            "JSONResponse": Reponse,
        }
        module = ast.Module(body=[FONCTION], type_ignores=[])
        exec(compile(module, str(SOURCE), "exec"), self.env)

    def tmdb_get(self, chemin, **params):
        self.appels.append((chemin, params))
        contenu = [{"id": i, "title": "Film annoncé"} for i in range(1, 19)]
        if chemin.endswith("tv"):
            contenu = [{"id": i, "name": "Série annoncée"} for i in range(1, 19)]
        return {"results": contenu, "total_pages": 7}

    def test_films_cibles_sur_date_suisse_sans_minimum_de_votes(self):
        resultat = self.env["catalogue_a_venir"]("movie", 2)
        chemin, params = self.appels[0]
        self.assertEqual(chemin, "/discover/movie")
        self.assertEqual(params["page"], 2)
        self.assertEqual(params["region"], "CH")
        self.assertEqual(params["release_date.gte"], self.env["planning"].local_iso(self.env["time"].time())[:10])
        self.assertNotIn("vote_count.gte", params)
        self.assertEqual(resultat["pages"], 7)
        self.assertEqual(len(resultat["resultats"]), 18)

    def test_series_gardent_les_dates_annoncees_sans_seuil_de_votes(self):
        self.env["catalogue_a_venir"]("tv", 1)
        chemin, params = self.appels[0]
        self.assertEqual(chemin, "/discover/tv")
        self.assertIn("first_air_date.gte", params)
        self.assertEqual(params["timezone"], self.env["planning"].TIMEZONE_NAME)
        self.assertNotIn("vote_count.gte", params)

    def test_server_date_and_timezone_differ_from_host(self):
        stamp = datetime.datetime(2026, 1, 15, 20, tzinfo=datetime.timezone.utc).timestamp()
        planning = self.env["planning"]
        with patch.object(planning, "FUSEAU", ZoneInfo("Asia/Kathmandu")), \
             patch.object(planning, "TIMEZONE_NAME", "Asia/Kathmandu"), \
             patch.object(self.env["time"], "time", return_value=stamp):
            self.env["catalogue_a_venir"]("tv", 1)
        params = self.appels[0][1]
        self.assertEqual(params["timezone"], "Asia/Kathmandu")
        self.assertEqual(params["first_air_date.gte"], "2026-01-16")


if __name__ == "__main__":
    unittest.main()
