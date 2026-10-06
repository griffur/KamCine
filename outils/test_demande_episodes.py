"""Demandes Sonarr ciblées par épisode/saison, avec qualité et données d'épisode contrôlées."""
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bibliotheque


class DemandeEpisodes(unittest.TestCase):
    def setUp(self):
        bibliotheque._episodes_cache.clear()
        self.cfg = {
            "sonarr_hd": {"url": "http://sonarr-hd", "cle": "h" * 24},
            "sonarr_uhd": {"url": "http://sonarr-uhd", "cle": "u" * 24},
        }
        self.hd_idx = {"par_tmdb": {42: {"id": 10, "title": "Paolo"}}, "par_tvdb": {}, "queue": {10: []}}
        self.uhd_idx = {"par_tmdb": {42: {"id": 20, "title": "Paolo"}}, "par_tvdb": {}, "queue": {20: []}}
        self.response = Mock(status_code=200)
        self.response.json.return_value = {"id": 100}

    def appeler(self, episodes_hd, episodes_uhd, qualites, episode=None):
        def lire(nom, chemin, params=None, delai=8):
            if chemin == "/episode":
                return episodes_hd if nom == "sonarr_hd" else episodes_uhd
            self.fail("appel inattendu %s %s" % (nom, chemin))

        with patch.object(bibliotheque, "config", return_value=self.cfg), \
             patch.object(bibliotheque, "_idx", side_effect=lambda nom: self.hd_idx if nom == "sonarr_hd" else self.uhd_idx), \
             patch.object(bibliotheque, "_get", side_effect=lire), \
             patch.object(bibliotheque.requests, "put", return_value=self.response) as put, \
             patch.object(bibliotheque.requests, "post", return_value=self.response) as post, \
             patch.object(bibliotheque, "vider") as vider, \
             patch.object(bibliotheque, "rafraichir_bientot") as refresh:
            resultat = bibliotheque.demander_episodes(42, 84, 1, qualites, episode)
        return resultat, put, post, vider, refresh

    @staticmethod
    def ep(id_, number, **kw):
        return {"id": id_, "seriesId": 10, "seasonNumber": 1, "episodeNumber": number,
                "hasFile": False, "monitored": False, **kw}

    def test_episode_hd_manquant_demande_seulement_sonarr_hd(self):
        hd = [self.ep(1, 1)]
        uhd = [self.ep(9, 1, hasFile=True, episodeFileId=99)]
        resultat, _, post, _, _ = self.appeler(hd, uhd, ["hd"], 1)
        self.assertEqual(resultat["resultats"][0]["episodes"], [1])
        post.assert_called_once_with("http://sonarr-hd/api/v3/command", json={"name": "EpisodeSearch", "episodeIds": [1]},
                                     headers=unittest.mock.ANY, timeout=12)

    def test_demande_saison_ignore_fichier_futur_et_download_actif(self):
        hd = [self.ep(1, 1), self.ep(2, 2, airDateUtc="2099-01-01T00:00:00Z"),
              self.ep(3, 3)]
        uhd = [self.ep(10, 1, hasFile=True, episodeFileId=10), self.ep(20, 2), self.ep(30, 3)]
        self.hd_idx["queue"][10] = [{"status": "downloading", "episodeId": 3, "size": 10, "sizeleft": 5}]
        resultat, _, post, vider, refresh = self.appeler(hd, uhd, ["hd", "uhd"])
        self.assertEqual(resultat["resultats"][0]["episodes"], [1])
        self.assertEqual(resultat["resultats"][1]["episodes"], [2, 3])
        self.assertEqual(post.call_count, 2)
        commandes = {appel.args[0]: appel.kwargs["json"]["episodeIds"] for appel in post.call_args_list}
        self.assertEqual(commandes, {"http://sonarr-hd/api/v3/command": [1],
                                    "http://sonarr-uhd/api/v3/command": [20, 30]},
                         "les identifiants d'épisodes restent séparés par instance/qualité")
        vider.assert_called_once_with()
        refresh.assert_called_once_with()

    def test_episode_non_suivi_est_cible_avant_episode_search(self):
        episodes = [self.ep(7, 4, monitored=False)]
        resultat, put, post, _, _ = self.appeler(episodes, [], ["hd"], 4)
        self.assertEqual(resultat["resultats"][0]["episodes"], [4])
        put.assert_called_once()
        self.assertTrue(put.call_args.kwargs["json"]["monitored"])
        post.assert_called_once()

    def test_episode_futur_ne_declenche_aucune_demande(self):
        futur = [self.ep(8, 5, airDateUtc="2099-01-01T00:00:00Z")]
        resultat, put, post, vider, refresh = self.appeler(futur, futur, ["hd", "uhd"])
        self.assertEqual([x["episodes"] for x in resultat["resultats"]], [[], []])
        put.assert_not_called()
        post.assert_not_called()
        vider.assert_called_once_with()
        refresh.assert_not_called()

    def test_episode_deja_monitore_en_recherche_ne_recoit_pas_une_seconde_demande(self):
        episodes = [self.ep(11, 6, monitored=True)]
        resultat, put, post, vider, refresh = self.appeler(episodes, [], ["hd"], 6)
        self.assertEqual(resultat["resultats"][0]["episodes"], [])
        put.assert_not_called()
        post.assert_not_called()
        vider.assert_called_once_with()
        refresh.assert_not_called()

    def test_qualite_non_configuree_ne_peut_pas_emprunter_l_autre_instance(self):
        with patch.object(bibliotheque, "config", return_value={"sonarr_hd": self.cfg["sonarr_hd"]}), \
             patch.object(bibliotheque, "_idx", return_value=self.hd_idx), \
             patch.object(bibliotheque, "_get", return_value=[self.ep(1, 1)]), \
             patch.object(bibliotheque.requests, "post") as post:
            with self.assertRaisesRegex(RuntimeError, "Sonarr 4K"):
                bibliotheque.demander_episodes(42, 84, 1, ["uhd"], 1)
            post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
