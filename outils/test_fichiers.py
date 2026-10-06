"""Les noms de fichiers proviennent des identifiants exacts Radarr et Sonarr."""
import sys
import unittest
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bibliotheque


class Fichiers(unittest.TestCase):
    def test_radarr_conserve_le_nom_complet_sans_dossier(self):
        film = bibliotheque._reduire_film({"id": 2, "tmdbId": 42, "hasFile": True,
                    "movieFile": {"relativePath": "Film/Film.2026.2160p.DV.HDR.mkv"}})
        resultat = bibliotheque._v_radarr({"par_tmdb": {42: film}}, 42)
        self.assertEqual(resultat["fichier"], "Film.2026.2160p.DV.HDR.mkv")

    def test_sonarr_associe_episode_et_fichier_par_identifiant(self):
        bibliotheque._cache.clear()
        def get(nom, chemin, params=None, delai=None):
            if chemin == "/episode":
                return [{"seasonNumber": 1, "episodeNumber": 1, "hasFile": True, "episodeFileId": 10},
                        {"seasonNumber": 1, "episodeNumber": 2, "hasFile": True, "episodeFileId": 20}]
            self.assertEqual(chemin, "/episodefile/20")
            return {"path": "/volume1/Series/S01E02." + nom + ".mkv"}
        with patch.object(bibliotheque, "_idx", return_value={"par_tmdb": {42: {"id": 8}}}), patch.object(bibliotheque, "_get", side_effect=get) as api:
            noms = bibliotheque.fichiers_episode(42, 1, 2)
            self.assertEqual([x["v"] for x in noms], ["S01E02.sonarr_uhd.mkv", "S01E02.sonarr_hd.mkv"])
            self.assertEqual(bibliotheque.fichiers_episode(42, 1, 2), noms)
            self.assertEqual(api.call_count, 4)

    def test_statut_cible_interroge_radarr_en_parallele_sans_lire_les_bibliotheques(self):
        def get(nom, chemin, params=None, delai=None):
            self.assertLessEqual(delai, 4)
            time.sleep(.10)
            if chemin == "/queue":
                return {"records": []}
            self.assertEqual(chemin, "/movie")
            self.assertEqual(params, {"tmdbId": 42})
            return ([{"id": 5, "tmdbId": 42, "hasFile": True, "movieFile": {"quality": {"quality": {"name": "Bluray-1080p"}}}}]
                    if nom == "radarr_hd" else [])

        initial = {"hd": {"etat": "inconnu"}, "uhd": {"etat": "inconnu"}, "sources": {}, "incomplet": True}
        bibliotheque._cible_cache.clear()
        with patch.object(bibliotheque, "_reglages", return_value={"badges_source": "arr"}), \
             patch.object(bibliotheque, "config", return_value={"radarr_hd": {"cle": "x"}, "radarr_uhd": {"cle": "y"}}), \
             patch.object(bibliotheque, "_idx", return_value=None), patch.object(bibliotheque, "_get", side_effect=get) as api:
            debut = time.perf_counter()
            resultat = bibliotheque.statut_cible("movie", 42, statut_initial=initial, delai=4)
            duree = time.perf_counter() - debut
            appels = api.call_count
            again = bibliotheque.statut_cible("movie", 42, statut_initial=initial, delai=4)
        self.assertLess(duree, .30, "Les deux qualités sont cherchées en parallèle")
        self.assertEqual(resultat["hd"]["etat"], "disponible")
        self.assertEqual(resultat["uhd"]["etat"], "absent")
        self.assertFalse(resultat["incomplet"])
        self.assertIn("radarr_hd_titre_ms", resultat["profilage_ms"])
        self.assertIn("radarr_hd_queue_ms", resultat["profilage_ms"])
        self.assertIn("radarr_uhd_total_ms", resultat["profilage_ms"])
        self.assertEqual(api.call_count, appels, "Le statut du même titre est réutilisé pendant son cache court")
        self.assertEqual(again["global"], resultat["global"])

    def test_statut_cible_sonarr_utilise_tvdb_et_conserve_les_saisons(self):
        def get(nom, chemin, params=None, delai=None):
            if chemin == "/queue":
                return {"records": []}
            self.assertEqual(chemin, "/series")
            self.assertEqual(params, {"tvdbId": 77})
            return [{"id": 8, "tmdbId": 420, "tvdbId": 77, "monitored": True,
                     "statistics": {"episodeFileCount": 8, "episodeCount": 8}, "seasons": []}]

        initial = {"hd": {"etat": "inconnu"}, "uhd": {"etat": "inconnu"}, "sources": {}, "incomplet": True}
        with patch.object(bibliotheque, "_reglages", return_value={"badges_source": "arr"}), \
             patch.object(bibliotheque, "config", return_value={"sonarr_hd": {"cle": "x"}}), \
             patch.object(bibliotheque, "_idx", return_value=None), patch.object(bibliotheque, "_get", side_effect=get):
            resultat = bibliotheque.statut_cible("tv", 420, "77", statut_initial=initial)
        self.assertEqual(resultat["hd"]["etat"], "disponible")
        self.assertFalse(resultat["incomplet"])

    def test_rafraichissement_global_ne_serie_plus_les_services(self):
        def attendre(*args):
            time.sleep(.10)
            return True
        with patch.object(bibliotheque, "_reglages", return_value={"badges_source": "auto", "badges_delai_liste_s": 20, "badges_delai_overseerr_s": 15}), \
             patch.object(bibliotheque, "config", return_value={n: {"cle": "x"} for n in bibliotheque.NOMS}), \
             patch.object(bibliotheque, "ov_cle", return_value="x"), \
             patch.object(bibliotheque, "_rafraichir_instance", side_effect=attendre), \
             patch.object(bibliotheque, "_rafraichir_overseerr", side_effect=attendre), \
             patch.object(bibliotheque, "_sauver_disque"), \
             patch.object(bibliotheque, "vider") as vider:
            debut = time.perf_counter()
            self.assertTrue(bibliotheque.rafraichir_tout())
            duree = time.perf_counter() - debut
        self.assertLess(duree, .30, "Les quatre ARR et Overseerr avancent simultanément")
        vider.assert_called_once_with()

    def test_rafraichissement_film_de_saga_parallellise_radarr_et_overseerr(self):
        def attendre(*args):
            time.sleep(.10)
            return True
        with patch.object(bibliotheque, "_reglages", return_value={"badges_source": "auto", "badges_delai_liste_s": 20, "badges_delai_overseerr_s": 15}), \
             patch.object(bibliotheque, "config", return_value={"radarr_hd": {"cle": "x"}, "radarr_uhd": {"cle": "y"}}), \
             patch.object(bibliotheque, "ov_cle", return_value="x"), \
             patch.object(bibliotheque, "_rafraichir_instance", side_effect=attendre), \
             patch.object(bibliotheque, "_rafraichir_overseerr", side_effect=attendre), \
             patch.object(bibliotheque, "_sauver_disque"):
            debut = time.perf_counter()
            self.assertTrue(bibliotheque.rafraichir_films())
            duree = time.perf_counter() - debut
        self.assertLess(duree, .25, "Les deux Radarr et Overseerr sont lus en parallèle, sans Sonarr")


if __name__ == "__main__":
    unittest.main()
