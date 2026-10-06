"""Aperçus de genres sans doublons."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from app import main


class ApercusGenres(unittest.TestCase):
    def test_selection_unique_stable_et_cachee(self):
        genres = main.GENRES["movie"]
        def discover(_route, with_genres, **_params):
            ident = int(with_genres)
            return {"results": [{"id": 1, "poster_path": "/commun.jpg", "popularity": 100},
                                {"id": ident, "poster_path": "/genre-%s.jpg" % ident, "popularity": 10}]}
        with patch.object(main, "tmdb_get", side_effect=discover) as api, \
             patch.object(main, "image", side_effect=lambda p, size: "https://image.tmdb.org/t/p/%s%s" % (size, p)), \
             patch.dict(main.cache_listes, {}, clear=True):
            resultat = main.catalogue_genres_apercus()
            autre = main.catalogue_genres_apercus()
        images = [x["affiche"] for x in resultat["genres"]]
        self.assertEqual(len(images), len(genres))
        self.assertEqual(len(images), len(set(images)))
        self.assertEqual(resultat, autre)
        self.assertEqual(api.call_count, len(genres))


if __name__ == "__main__":
    unittest.main()
