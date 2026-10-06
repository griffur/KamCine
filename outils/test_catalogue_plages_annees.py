"""Bornes temporelles du filtre Année du catalogue."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from app import main
import bibliotheque


class PlagesAnneeCatalogue(unittest.TestCase):
    def params(self, annee, type_="movie"):
        main.cache_listes.clear()
        with patch.object(main, "charger", return_value={"catalogue_cache_min": 10}), \
             patch.object(main, "tmdb_get", return_value={"results": [], "total_pages": 1}) as tmdb:
            resultat = main.catalogue_parcourir(type=type_, annee=annee)
        self.assertTrue(resultat["ok"])
        return tmdb.call_args.kwargs

    def test_annee_exacte_conserve_le_comportement_historique(self):
        self.assertEqual(self.params("exact:2022")["primary_release_year"], "2022")
        self.assertEqual(self.params("2022")["primary_release_year"], "2022")

    def test_depuis_avant_et_entre(self):
        self.assertEqual(self.params("since:2022")["primary_release_date.gte"], "2022-01-01")
        self.assertEqual(self.params("before:2022")["primary_release_date.lte"], "2021-12-31")
        self.assertEqual(self.params("between:2015:2022")["primary_release_date.gte"], "2015-01-01")
        self.assertEqual(self.params("between:2015:2022")["primary_release_date.lte"], "2022-12-31")

    def test_champ_de_date_adapte_aux_series(self):
        self.assertEqual(self.params("since:2020", "tv")["first_air_date.gte"], "2020-01-01")

    def test_plage_inversee_est_refusee(self):
        resultat = main.catalogue_parcourir(annee="between:2024:2020")
        self.assertEqual(resultat.status_code, 400)

    def test_qualite_visee_ne_compte_que_le_fichier_reel(self):
        self.assertEqual(bibliotheque._v_radarr({"par_tmdb": {7: {"hasFile": False, "monitored": True,
                                                                   "isAvailable": True}}, "queue": {}}, 7)["etat"], "recherche")
        self.assertEqual(bibliotheque._v_radarr({"par_tmdb": {7: {"hasFile": True, "movieFile": {"size": 10}}},
                                                  "queue": {}}, 7)["etat"], "disponible")
        choisie = bibliotheque.qualite_visee("disponible", "disponible")
        self.assertEqual(choisie["visee"], "uhd")

    def test_selection_4k_depuis_les_etats_reels(self):
        cas = (("disponible", "absent", "hd"), ("absent", "disponible", "uhd"),
               ("disponible", "disponible", "uhd"), ("disponible", "recherche", "hd"))
        for hd, uhd, attendu in cas:
            with self.subTest(hd=hd, uhd=uhd):
                self.assertEqual(bibliotheque.qualite_visee(hd, uhd)["visee"], attendu)

    def test_release_transmission_reste_associee_a_sa_qualite(self):
        statut = {"hd": {"etat": "telechargement", "hashes": ["a" * 40], "progres": 20},
                  "uhd": {"etat": "recherche"}, "sources": {}}
        with patch.object(main.transmission, "suivi", return_value={"a" * 40: {"nom": "Film.2160p.REMUX"}}), \
             patch.object(main.transmission, "resume", return_value={"progres": 20, "restant_s": 30, "vitesse": 5,
                                                                       "pairs": 2, "bloque": False, "erreur": "",
                                                                       "etat": "téléchargement"}), \
             patch.object(main.transmission, "duree_txt", return_value="30 s"):
            resultat = main.statut_avec_transmission(statut)
        self.assertEqual(resultat["hd"]["release"], "Film.2160p.REMUX")
        self.assertNotIn("release", resultat["uhd"])


if __name__ == "__main__":
    unittest.main()
