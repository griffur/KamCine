"""Tests locaux du parsing de la liste des demandes Overseerr."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import demandes


class DemandesRecentes(unittest.TestCase):
    def test_extrait_et_dedoublonne_les_films_et_series_par_identifiant_tmdb(self):
        reponse = {
            "pageInfo": {"results": 30, "pages": 2},
            "results": [
                {"media": {"mediaType": "movie", "tmdbId": 550}},
                {"media": {"mediaType": "movie", "tmdbId": 550}},
                {"media": {"mediaType": "tv", "tmdbId": 550}},
                {"media": {"mediaType": "tv", "tmdbId": None}},
                {"media": {"mediaType": "movie", "tmdbId": "bad"}},
            ],
        }
        medias, plus = demandes.page_overseerr(reponse, 1, 18)
        self.assertEqual(medias, [("movie", 550), ("tv", 550)])
        self.assertTrue(plus)

    def test_supporte_la_reponse_historique_et_la_pagination_sans_metadonnees(self):
        reponse = {"requests": [{"type": "movie", "mediaId": 12}], "totalPages": 1}
        medias, plus = demandes.page_overseerr(reponse, 1, 18)
        self.assertEqual(medias, [("movie", 12)])
        self.assertFalse(plus)

    def test_page_suivante_est_basee_sur_les_demandes_brutes_y_compris_doublons(self):
        reponse = {"pageInfo": {"results": 37, "pages": 3}, "results": [{"media": {"mediaType": "tv", "tmdbId": 3}}] * 18}
        _, plus = demandes.page_overseerr(reponse, 2, 18)
        self.assertTrue(plus)


if __name__ == "__main__":
    unittest.main()
