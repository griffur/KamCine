"""Mesure les phases de chargement d'une fiche sans réseau externe."""
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from starlette.requests import Request
from app import main


def request_test():
    return Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""})


class PerformanceFiche(unittest.TestCase):
    def test_premiere_passe_utilise_uniquement_les_index_en_memoire(self):
        etat = {"hd": {"etat": "disponible"}, "uhd": {"etat": "recherche"}, "global": "disponible"}
        with patch.object(main.bibliotheque, "statut", return_value=etat) as statut, \
             patch.object(main, "statut_avec_transmission", side_effect=lambda x: x), \
             patch.object(main.bibliotheque, "qualites_titre", return_value={"global": {"visee": "hd"}}):
            debut = time.perf_counter()
            resultat = main.catalogue_fiche_etat(request_test(), "movie", 42, rapide=1)
            duree = time.perf_counter() - debut
        self.assertLess(duree, 0.15)
        statut.assert_called_once_with("movie", 42, None, False)
        self.assertTrue(resultat["rapide"])
        self.assertEqual(resultat["statut"], etat)

    def test_enrichissements_tmdb_et_statut_sont_paralleles(self):
        def lent(*_args, **_kwargs):
            time.sleep(0.12)
            return {"hd": {"etat": "disponible"}, "uhd": {"etat": "absent"}, "global": "disponible"}

        def similaires(*_args, **_kwargs):
            time.sleep(0.12)
            return {"results": []}

        def notes(*_args, **_kwargs):
            time.sleep(0.12)
            return {}

        with patch.object(main, "details_complets", return_value={"external_ids": {}, "title": "Film"}), \
             patch.object(main.bibliotheque, "statut", side_effect=lent), \
             patch.object(main, "statut_avec_transmission", side_effect=lambda x: x), \
             patch.object(main, "notes_titre", side_effect=notes), \
             patch.object(main, "tmdb_get", side_effect=similaires), \
             patch.object(main.bibliotheque, "qualites_titre", return_value=None), \
             patch.object(main.bibliotheque, "liens_arr", return_value=[]), \
             patch.object(main, "deja_vu", return_value=False), \
             patch.object(main, "vus_source", return_value={"source": "local"}), \
             patch.object(main.trakt, "connectee", return_value=False), \
             patch.object(main, "id_connecte", return_value=1), \
             patch.object(main.favoris, "est_favori", return_value=False):
            debut = time.perf_counter()
            resultat = main.catalogue_fiche_etat(request_test(), "movie", 42)
            duree = time.perf_counter() - debut
        self.assertLess(duree, 0.23, "Les trois appels indépendants doivent rester concurrents")
        self.assertTrue(resultat["ok"])
        self.assertEqual(resultat["statut"]["global"], "disponible")


if __name__ == "__main__":
    unittest.main()
