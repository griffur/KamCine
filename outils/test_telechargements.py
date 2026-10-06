"""Tests hors réseau du suivi et du retrait des téléchargements Transmission."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
donnees = tempfile.TemporaryDirectory(prefix="kamcine-telechargements-")
os.environ["KAMCINE_DIR"] = donnees.name

import config
sys.modules.setdefault("requests", types.SimpleNamespace())
import bibliotheque
import transmission


class Telechargements(unittest.TestCase):
    def setUp(self):
        Path(transmission.SECRETS).write_text(json.dumps({"transmission": {"url": "http://nas:9091/transmission/rpc", "user": "kam", "pass": "secret"}}))
        Path(config.CHEMIN).write_text(json.dumps({"transmission_actif": True}))
        transmission._cache_recents.clear()

    def test_torrent_get_recent_normalise_limite_et_cache(self):
        maintenant = time.time()
        torrents = [
            {"hashString": "a" * 40, "name": "Film actif", "status": 4, "percentDone": .42, "rateDownload": 2048, "eta": 90,
             "sizeWhenDone": 1000, "addedDate": maintenant - 100, "uploadRatio": .2},
            {"name": "Série en pause", "status": 0, "percentDone": .2, "addedDate": maintenant - 200},
            {"name": "Film terminé", "status": 6, "percentDone": 1, "doneDate": maintenant - 3600,
             "addedDate": maintenant - 5000, "uploadRatio": 1.25},
            {"name": "Ancien terminé", "status": 0, "percentDone": 1, "doneDate": maintenant - 60 * 86400},
            {"hashString": "c" * 40, "name": "Erreur tracker", "status": 4, "percentDone": .5, "error": 1, "errorString": "échec"},
        ]
        with patch.object(transmission, "_rpc", return_value={"torrents": torrents}) as rpc:
            resultat = transmission.recents(1)
            self.assertEqual(transmission.recents(1), resultat)
        rpc.assert_called_once()
        self.assertEqual(rpc.call_args.args[3], "torrent-get")
        self.assertEqual(rpc.call_args.args[4]["fields"], transmission.CHAMPS_RECENTS)
        self.assertEqual(rpc.call_args.kwargs["delai"], transmission.DELAI_RECENTS)
        self.assertNotIn("ids", rpc.call_args.args[4])
        self.assertEqual([x["nom"] for x in resultat["en_cours"]], ["Erreur tracker"])
        self.assertEqual(resultat["en_cours"][0]["etat"], "erreur")
        self.assertEqual(resultat["termines"][0]["nom"], "Film terminé")
        self.assertEqual(resultat["termines"][0]["ratio"], 1.25)
        self.assertEqual(resultat["en_cours"][0]["hash"], "c" * 40)

    def test_service_inactif_sans_requete(self):
        Path(config.CHEMIN).write_text(json.dumps({"transmission_actif": False}))
        with patch.object(transmission, "_rpc") as appel:
            self.assertIsNone(transmission.recents())
        appel.assert_not_called()

    def test_cache_expire_apres_six_secondes(self):
        horloge = [1000]
        with patch.object(transmission, "_rpc", return_value={"torrents": []}) as rpc, \
             patch.object(transmission.time, "time", side_effect=lambda: horloge[0]):
            transmission.recents()
            horloge[0] += 5.9
            transmission.recents()
            self.assertEqual(rpc.call_count, 1)
            horloge[0] += 0.2
            transmission.recents()
        self.assertEqual(rpc.call_count, 2)

    def test_correspondance_par_titre_avec_annee_et_collision_sure(self):
        index = {
            "radarr_hd": {"data": {"par_tmdb": {
                1: {"tmdbId": 1, "title": "Blade Runner", "year": 1982, "affiche": "https://image.tmdb.org/t/p/w342/a.jpg"},
                2: {"tmdbId": 2, "title": "Blade Runner", "year": 2049, "affiche": "https://image.tmdb.org/t/p/w342/b.jpg"},
                3: {"tmdbId": 3, "title": "Dune", "year": 2021, "affiche": "https://image.tmdb.org/t/p/w342/d.jpg"}}, "par_tvdb": {}, "queue": {}}}
        }
        with patch.object(bibliotheque, "config", return_value={"radarr_hd": {"cle": "ok"}}), patch.object(bibliotheque, "_index", index):
            result = bibliotheque.medias_par_nom_torrent([
                "Blade.Runner.2049.2160p.WEB-DL.mkv", "Blade Runner 1982 1080p BluRay", "Dune.2021.S01E01", "Blade Runner 2050 1080p"])
        self.assertEqual(result["Blade.Runner.2049.2160p.WEB-DL.mkv"]["id"], 2)
        self.assertEqual(result["Blade Runner 1982 1080p BluRay"]["id"], 1)
        self.assertNotIn("Dune.2021.S01E01", result)
        self.assertNotIn("Blade Runner 2050 1080p", result)

    def test_retrait_rpc_conserve_les_fichiers_et_invalide_le_cache(self):
        hash_torrent = "b" * 40
        transmission._cache[("url", ())] = (time.time(), {})
        transmission._cache_recents["url"] = (time.time(), {})
        with patch.object(transmission, "_rpc", return_value={}) as rpc:
            self.assertTrue(transmission.retirer_torrent(hash_torrent))
        rpc.assert_called_once()
        self.assertEqual(rpc.call_args.args[3], "torrent-remove")
        self.assertEqual(rpc.call_args.args[4], {"ids": [hash_torrent], "delete-local-data": False})
        self.assertEqual(transmission._cache, {})
        self.assertEqual(transmission._cache_recents, {})

    def test_retrait_refuse_un_hash_invalide_sans_appel_rpc(self):
        with patch.object(transmission, "_rpc") as rpc:
            with self.assertRaises(ValueError):
                transmission.retirer_torrent("hash-invalide")
        rpc.assert_not_called()

    def test_retrait_en_lot_revalide_termines_et_conserve_les_fichiers(self):
        term = "d" * 40
        actif = "e" * 40
        erreur = "f" * 40
        with patch.object(transmission, "_rpc", side_effect=[
            {"torrents": [
                {"hashString": term, "name": "Film terminé", "status": 6, "percentDone": 1},
                {"hashString": actif, "name": "Film actif", "status": 4, "percentDone": .8},
                {"hashString": erreur, "name": "Torrent erroné", "status": 6, "percentDone": 1, "error": 1},
            ]},
            {},
        ]) as rpc:
            noms = transmission.retirer_termines([term, actif, erreur])
        self.assertEqual(noms, ["Film terminé"])
        self.assertEqual(rpc.call_count, 2)
        self.assertEqual(rpc.call_args_list[0].args[3], "torrent-get")
        self.assertEqual(rpc.call_args_list[1].args[3], "torrent-remove")
        self.assertEqual(rpc.call_args_list[1].args[4], {"ids": [term], "delete-local-data": False})

    def test_retrait_en_lot_refuse_selection_trop_large_avant_rpc(self):
        with patch.object(transmission, "_rpc") as rpc:
            with self.assertRaises(ValueError):
                transmission.retirer_termines([f"{i:040x}" for i in range(16)])
        rpc.assert_not_called()

    def test_correspondance_media_par_hash_unique_et_sans_ambiguite(self):
        hash_fiable = "1" * 40
        hash_ambigu = "2" * 40
        poster = "https://image.tmdb.org/t/p/original/affiche.jpg"
        film = {"id": 10, "tmdbId": 603, "title": "Matrix", "affiche": poster}
        film_4k = {"id": 20, "tmdbId": 603, "title": "Matrix", "affiche": poster}
        autre = {"id": 30, "tmdbId": 999, "title": "Autre film", "affiche": None}
        data_hd = {"par_tmdb": {603: film, 999: autre}, "par_tvdb": {}, "queue": {
            10: [{"downloadId": hash_fiable, "protocol": "torrent"}],
            30: [{"downloadId": hash_ambigu, "protocol": "torrent"}],
        }}
        data_4k = {"par_tmdb": {603: film_4k, 1000: {"id": 40, "tmdbId": 1000, "title": "Ambigu", "affiche": None}}, "par_tvdb": {}, "queue": {
            20: [{"downloadId": hash_fiable, "protocol": "torrent"}],
            40: [{"downloadId": hash_ambigu, "protocol": "torrent"}],
        }}
        arr = {n: {"cle": "x"} for n in ("radarr_hd", "radarr_uhd")}
        indexes = {"radarr_hd": {"data": data_hd}, "radarr_uhd": {"data": data_4k}}
        with patch.object(bibliotheque, "config", return_value=arr), patch.object(bibliotheque, "_index", indexes):
            medias = bibliotheque.medias_par_hash()
        self.assertEqual(medias[hash_fiable], {"type": "movie", "id": 603, "titre": "Matrix", "affiche": poster,
                                               "instances": ["radarr_hd", "radarr_uhd"]})
        self.assertNotIn(hash_ambigu, medias)

    def test_historique_arr_relie_release_tracker_par_hash_sans_exposer_url(self):
        h = "a" * 40
        reponse = {"records": [
            {"downloadId": h, "eventType": "grabbed", "sourceTitle": "Film.2024.WEB-DL", "data": {
                "indexer": "C411", "nzbInfoUrl": "https://c411.example/torrent/123?passkey=secret",
                "downloadUrl": "https://arr.local/download?apikey=secret"}},
            {"downloadId": "b" * 40, "sourceTitle": "Autre.2024", "data": {"indexer": "Autre"}},
        ]}
        with patch.object(bibliotheque.requests, "get", create=True) as get:
            get.return_value.json.return_value = reponse
            get.return_value.raise_for_status.return_value = None
            result = bibliotheque._lire_historique_instance("radarr_hd", {"url": "https://arr.example", "cle": "secret"})
        self.assertEqual(result[h], {"source": "radarr_hd", "release": "Film.2024.WEB-DL", "tracker": "C411"})
        self.assertNotIn("downloadUrl", str(result))
        self.assertNotIn("passkey", str(result))

    def test_affiche_arr_accepte_seulement_url_tmdb_https(self):
        self.assertEqual(bibliotheque._affiche_arr([{"coverType": "poster", "remoteUrl": "https://image.tmdb.org/t/p/w500/a.jpg"}]),
                         "https://image.tmdb.org/t/p/w500/a.jpg")
        self.assertIsNone(bibliotheque._affiche_arr([{"coverType": "poster", "remoteUrl": "http://image.tmdb.org/t/p/w500/a.jpg"}]))

    def test_correspondance_sonarr_par_hash_et_tmdb(self):
        h = "3" * 40
        serie = {"id": 8, "tmdbId": 1399, "tvdbId": 12345, "title": "Game of Thrones", "affiche": None}
        data = {"par_tmdb": {1399: serie}, "par_tvdb": {12345: serie},
                "queue": {8: [{"downloadId": h.upper(), "protocol": "torrent", "seasonNumber": 1}]}}
        with patch.object(bibliotheque, "config", return_value={"sonarr_hd": {"cle": "x"}}), \
             patch.object(bibliotheque, "_index", {"sonarr_hd": {"data": data}}):
            medias = bibliotheque.medias_par_hash()
        self.assertEqual(medias[h]["type"], "tv")
        self.assertEqual(medias[h]["titre"], "Game of Thrones")


if __name__ == "__main__":
    unittest.main()
