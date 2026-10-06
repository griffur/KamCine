"""Rafraîchissement final ARR borné et priorité de l'import réel."""
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from app import main
import bibliotheque


class ImportFinal(unittest.TestCase):
    def test_fichier_radarr_importe_prioritaire_sur_une_file_transmission_a_100(self):
        film = {"id": 7, "tmdbId": 42, "hasFile": True, "movieFile": {"size": 1000}}
        idx = {"par_tmdb": {42: film}, "queue": {7: [{"status": "completed", "size": 1000, "sizeleft": 0}]}}
        etat = bibliotheque._v_radarr(idx, 42)
        self.assertEqual(etat["etat"], "disponible")

    def test_queue_terminee_sans_fichier_ne_reste_pas_a_100_pourcent(self):
        film = {"id": 7, "tmdbId": 42, "hasFile": False, "monitored": True, "isAvailable": True}
        idx = {"par_tmdb": {42: film}, "queue": {7: [{"status": "completed", "size": 1000, "sizeleft": 0}]}}
        self.assertEqual(bibliotheque._v_radarr(idx, 42)["etat"], "recherche")

    def test_telechargement_actif_reste_en_cours(self):
        film = {"id": 7, "tmdbId": 42, "hasFile": False, "monitored": True, "isAvailable": True}
        idx = {"par_tmdb": {42: film}, "queue": {7: [{"status": "downloading", "size": 1000, "sizeleft": 200}]}}
        etat = bibliotheque._v_radarr(idx, 42)
        self.assertEqual(etat["etat"], "telechargement")
        self.assertEqual(etat["progres"], 80)

    def test_overseerr_100_sans_import_revient_a_en_recherche(self):
        media = {"status4k": 3, "requests": [{"is4k": True, "status": 2}],
                 "downloadStatus4k": [{"size": 1000, "sizeLeft": 0}]}
        etat = bibliotheque._v_overseerr(media, True)
        self.assertEqual(etat["etat"], "recherche")
        self.assertNotIn("progres", etat)

    def test_overseerr_sous_100_reste_un_telechargement(self):
        media = {"status": 3, "requests": [{"is4k": False, "status": 2}],
                 "downloadStatus": [{"size": 1000, "sizeLeft": 20}]}
        etat = bibliotheque._v_overseerr(media, False)
        self.assertEqual(etat["etat"], "telechargement")
        self.assertEqual(etat["progres"], 98)

    def test_file_sonarr_ne_reclasse_pas_une_autre_serie(self):
        serie = {"id": 10, "tmdbId": 42, "statistics": {"episodeFileCount": 0, "episodeCount": 5},
                 "monitored": True}
        queue = [{"seriesId": 99, "seasonNumber": 1, "status": "downloading", "size": 100, "sizeleft": 0}]
        etat = bibliotheque._v_sonarr({"par_tmdb": {42: serie}, "par_tvdb": {}, "queue": {10: queue}}, 42, None)
        self.assertEqual(etat["etat"], "recherche")

    def test_statuts_episodes_separent_qualites_et_excluent_le_futur(self):
        hd = {"par_tmdb": {42: {"id": 10}}, "queue": {10: [{"seriesId": 10, "episodeId": 3,
               "status": "downloading", "size": 100, "sizeleft": 50}]}}
        uhd = {"par_tmdb": {42: {"id": 20}}, "queue": {20: []}}
        episodes = [
            {"id": 1, "seasonNumber": 1, "episodeNumber": 1, "hasFile": True, "monitored": True},
            {"id": 2, "seasonNumber": 1, "episodeNumber": 2, "airDateUtc": "2099-01-01T00:00:00Z", "monitored": True},
            {"id": 3, "seasonNumber": 1, "episodeNumber": 3, "monitored": True},
        ]
        def get(_nom, _path, _params=None, delai=8): return episodes
        bibliotheque._episodes_cache.clear()
        with patch.object(bibliotheque, "config", return_value={"sonarr_hd": {"cle": "x"}, "sonarr_uhd": {"cle": "y"}}), \
             patch.object(bibliotheque, "_idx", side_effect=lambda nom: hd if nom == "sonarr_hd" else uhd), \
             patch.object(bibliotheque, "_get", side_effect=get):
            result = bibliotheque.disponibilite_episodes(42, 1)
        self.assertEqual(result[1]["hd"]["etat"], "disponible")
        self.assertEqual(result[2]["hd"]["etat"], "a_venir")
        self.assertEqual(result[3]["hd"]["etat"], "telechargement")
        self.assertEqual(result[3]["uhd"]["etat"], "recherche")

    def test_relecture_finale_non_bloquante_pour_films_et_series(self):
        for type_ in ("movie", "tv"):
            with self.subTest(type=type_), patch.object(main, "RELECTURE_FILM", {}), \
                 patch.object(main, "RELECTURE_MEDIA_EN_COURS", set()), \
                 patch.object(main.threading, "Thread") as thread:
                self.assertTrue(main.programmer_relecture_media(type_, 42, 0))
                thread.assert_called_once()
                self.assertTrue(thread.call_args.kwargs["daemon"])

    def test_progression_a_99_declenche_une_verification_acceleree(self):
        statut = {"erreur": None, "hd": {"etat": "telechargement", "progres": 100},
                  "uhd": {"etat": "absent"}}
        with patch.object(main.bibliotheque, "statut", return_value=statut), \
             patch.object(main, "statut_avec_transmission", side_effect=lambda x: x), \
             patch.object(main, "programmer_relecture_media") as programmer:
            resultat = main.telechargement("movie", 42)
        self.assertTrue(resultat["en_cours"])
        programmer.assert_called_once_with("movie", 42, 10)

    def test_relecture_sonarr_cible_met_a_jour_index_et_file(self):
        initiale = {"id": 3, "tmdbId": 42, "tvdbId": 84, "title": "Série", "monitored": True,
                    "statistics": {"episodeFileCount": 0, "episodeCount": 8}, "seasons": []}
        actualisee = dict(initiale, statistics={"episodeFileCount": 8, "episodeCount": 8})
        index = {"sonarr_hd": {"data": {"par_tmdb": {42: initiale}, "par_tvdb": {84: initiale}, "queue": {}}}}

        def get(_nom, chemin, params=None, delai=8):
            if chemin == "/series":
                return [actualisee]
            return {"records": []}

        with patch.object(bibliotheque, "_index", index), \
             patch.object(bibliotheque, "config", return_value={"sonarr_hd": {"cle": "x"}}), \
             patch.object(bibliotheque, "_get", side_effect=get), \
             patch.object(bibliotheque, "vider"):
            self.assertEqual(bibliotheque.relire_serie(42), 1)
        self.assertEqual(index["sonarr_hd"]["data"]["par_tmdb"][42]["statistics"]["episodeFileCount"], 8)


if __name__ == "__main__":
    unittest.main()
