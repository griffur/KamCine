"""Point 84 : la rangée Ajoutés récemment suit les listes de Radarr et Sonarr sans seconde attente, sans réseau."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
donnees = tempfile.TemporaryDirectory(prefix="kamcine-ajoutes-")
os.environ["KAMCINE_DIR"] = donnees.name
from app import main

bib = main.bibliotheque


def film(tmdb, date):
    return {"id": tmdb, "tmdbId": tmdb, "title": "Film %d" % tmdb, "hasFile": True,
            "added": date, "movieFile": {"dateAdded": date}}


class Ajoutes(unittest.TestCase):
    def setUp(self):
        self.t = [1000.0]
        main.cache_listes.clear()
        for objet, nom, valeur in [(bib, "config", Mock(return_value={"radarr_hd": {"cle": "k"}})),
                                   (bib, "demander", Mock()),
                                   (main, "carte_par_id", Mock(side_effect=lambda t, i: {"type": t, "id": i})),
                                   (main.time, "time", Mock(side_effect=lambda: self.t[0]))]:
            p = patch.object(objet, nom, valeur);p.start();self.addCleanup(p.stop)
        self.index = {}
        p = patch.dict(bib._index, {}, clear=True);p.start();self.addCleanup(p.stop)
        self.lire([film(1, "2026-09-20T10:00:00Z")])

    def lire(self, films):
        """Ce que fait un rafraîchissement réussi de Radarr : nouvelle liste, nouvelle date."""
        bib._index["radarr_hd"] = {"t": self.t[0], "data": bib._construire(films, {}), "origine": "reseau"}

    def ids(self):
        return [c["id"] for c in main.catalogue_ajoutes()["resultats"]]

    def test_nouveau_fichier_visible_des_la_relecture_des_listes(self):
        self.assertEqual(self.ids(), [1])
        self.t[0] += 60
        self.lire([film(1, "2026-09-20T10:00:00Z"), film(2, "2026-09-26T18:00:00Z")])
        self.assertEqual(self.ids(), [2, 1], "Pas de seconde attente de 5 minutes après la relecture")

    def test_sans_relecture_la_memoire_sert_sans_recalcul(self):
        self.assertEqual(self.ids(), [1])
        main.carte_par_id.reset_mock()
        self.t[0] += 60
        self.assertEqual(self.ids(), [1])
        main.carte_par_id.assert_not_called()

    def test_memoire_bornee_a_cinq_minutes(self):
        self.ids()
        main.carte_par_id.reset_mock()
        self.t[0] += 301
        self.ids()
        main.carte_par_id.assert_called()

    def test_la_rangee_reveille_les_listes_perimees(self):
        self.ids()
        bib.demander.assert_called()

    def test_changer_la_configuration_oublie_la_rangee(self):
        import asyncio
        self.ids()
        main.cache_listes[("nonvues", 1, "local")] = (self.t[0], {})
        main.cache_listes[("tendances", 1)] = (self.t[0], {})
        corps = {"nom": "radarr_hd", "url": "http://nas:7878", "cle": "x" * 32}
        with patch.object(main, "corps_json", Mock(side_effect=lambda r: asyncio.sleep(0, corps))), \
                patch.object(bib, "tester", return_value="5"), patch.object(bib, "sauver"):
            self.assertTrue(asyncio.run(main.arr_config(None))["ok"])
        self.assertEqual([k[0] for k in main.cache_listes], ["tendances"])

if __name__ == "__main__":
    unittest.main()
