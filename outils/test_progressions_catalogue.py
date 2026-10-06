"""Progressions de posters issues du cache local, sans appels par affiche."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import suivi


class ProgressionsCatalogue(unittest.TestCase):
    def lire(self, donnees, elements, vus_films=None, vus_episodes=None, maintenant=2_000_000):
        with tempfile.TemporaryDirectory() as dossier:
            fichier = Path(dossier) / "progressions.json"
            fichier.write_text(json.dumps(donnees), encoding="utf-8")
            with patch.object(suivi, "PROG", str(fichier)):
                return suivi.progressions_catalogue(elements, vus_films, vus_episodes, maintenant=maintenant)

    def test_film_en_cours_est_expose_en_pourcentage_entier(self):
        resultat = self.lire({"movie:42:0:0": {"type": "movie", "id": 42, "pos": 420, "total": 1000, "t": 1_999_999}}, ["movie-42"])
        self.assertEqual(resultat, {"movie-42": 42})

    def test_progression_presque_terminee_reste_visible_tant_que_non_vue(self):
        resultat = self.lire({"movie:42:0:0": {"type": "movie", "id": 42, "pos": 950, "total": 1000, "t": 1_999_999}}, ["movie-42"])
        self.assertEqual(resultat, {"movie-42": 95})

    def test_serie_reprend_la_progression_la_plus_recente_non_vue(self):
        donnees = {
            "tv:9:1:1": {"type": "tv", "id": 9, "saison": 1, "episode": 1, "pos": 500, "total": 1000, "t": 1_999_990},
            "tv:9:1:2": {"type": "tv", "id": 9, "saison": 1, "episode": 2, "pos": 250, "total": 1000, "t": 1_999_999},
        }
        resultat = self.lire(donnees, ["tv-9"], vus_episodes={(9, 1, 1)})
        self.assertEqual(resultat, {"tv-9": 25})

    def test_pas_de_barre_si_termine_vu_inconnu_ou_perime(self):
        donnees = {
            "movie:1:0:0": {"type": "movie", "id": 1, "pos": 1000, "total": 1000, "t": 6_999_999},
            "movie:2:0:0": {"type": "movie", "id": 2, "pos": 500, "total": 1000, "t": 6_999_999},
            "movie:3:0:0": {"type": "movie", "id": 3, "pos": 500, "total": 1000, "t": 0},
        }
        resultat = self.lire(donnees, ["movie-1", "movie-2", "movie-3", "movie-4"], vus_films={2}, maintenant=7_000_000)
        self.assertEqual(resultat, {})


if __name__ == "__main__":
    unittest.main()
