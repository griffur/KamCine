"""Classement local TMDB de la recherche Catalogue, sans réseau ni dépendances du service."""
import ast
import difflib
import math
import re
from pathlib import Path
import unicodedata
import unittest


source = Path(__file__).resolve().parents[1].joinpath("app/main.py").read_text()
arbre = ast.parse(source)
fonctions = [n for n in arbre.body if isinstance(n, ast.FunctionDef) and n.name in {
    "normaliser_recherche_catalogue", "score_texte_recherche_catalogue", "classer_recherche_catalogue"
}]
ns = {"difflib": difflib, "math": math, "re": re, "unicodedata": unicodedata}
exec(compile(ast.Module(body=fonctions, type_ignores=[]), "app/main.py", "exec"), ns)


class RechercheCatalogue(unittest.TestCase):
    def test_personne_connue_devant_profils_secondaires(self):
        resultats = [
            {"media_type": "person", "id": 4, "name": "Jim Beaver", "popularity": 0.2, "profile_path": None, "known_for": []},
            {"media_type": "person", "id": 8, "name": "Jim Carrey", "popularity": 35, "profile_path": "/jim.jpg", "known_for": [{"title": "The Truman Show"}]},
            {"media_type": "person", "id": 9, "name": "Jimmy Secondary", "popularity": 0.5, "profile_path": "/secondary.jpg", "known_for": []},
            {"media_type": "person", "id": 10, "name": "James Carrey", "popularity": 42, "profile_path": "/james.jpg", "known_for": [{"title": "Film"}]},
        ]
        personnes, medias = ns["classer_recherche_catalogue"]("Jim", resultats)
        self.assertEqual([x["id"] for x in personnes][0], 8)
        self.assertNotIn(4, [x["id"] for x in personnes])
        self.assertEqual(medias, [])
        tronquees, _ = ns["classer_recherche_catalogue"]("Jim...", resultats)
        self.assertEqual(tronquees[0]["id"], 8)

    def test_nom_exact_insensible_aux_accents(self):
        self.assertEqual(ns["normaliser_recherche_catalogue"]("Beyoncé"), "beyonce")
        self.assertEqual(ns["normaliser_recherche_catalogue"]("东京"), "东京")
        resultats = [
            {"media_type": "person", "id": 1, "name": "Beyoncé", "popularity": 0.1, "profile_path": None, "known_for": []},
            {"media_type": "person", "id": 2, "name": "Beyonce Knowles", "popularity": 30, "profile_path": "/b.jpg", "known_for": [{"title": "Film"}]},
        ]
        personnes, _ = ns["classer_recherche_catalogue"]("Beyonce", resultats)
        self.assertEqual(personnes[0]["id"], 1)

    def test_medias_exact_puis_proches_et_populaires_sans_perte(self):
        resultats = [
            {"media_type": "movie", "id": 1, "title": "Avatar: The Way of Water", "popularity": 1000},
            {"media_type": "tv", "id": 2, "name": "The Avatar Chronicles", "popularity": 600},
            {"media_type": "movie", "id": 3, "title": "Avatar", "popularity": 4},
        ]
        personnes, medias = ns["classer_recherche_catalogue"]("Avatar", resultats)
        self.assertEqual(personnes, [])
        self.assertEqual([x["id"] for x in medias], [3, 1, 2])


if __name__ == "__main__":
    unittest.main()
