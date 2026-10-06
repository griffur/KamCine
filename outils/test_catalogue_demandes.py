"""Contrat des demandes récentes et confidentialité par compte."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from app import main


class Reponse:
    status_code = 200

    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class DemandesRecentes(unittest.TestCase):
    def setUp(self):
        main.cache_listes.clear()
        self.request = SimpleNamespace(state=SimpleNamespace(compte={"id": 1, "admin": True}))

    def test_admin_lit_les_demandes_overseerr_et_dedoublonne(self):
        data = {"results": [
            {"media": {"tmdbId": 11, "mediaType": "movie"}},
            {"media": {"tmdbId": 11, "mediaType": "movie"}},
            {"media": {"tmdbId": 22, "mediaType": "tv"}},
        ], "pageInfo": {"pages": 2}}
        with patch.object(main.bibliotheque, "ov_cle", return_value="secret"), \
             patch.object(main.bibliotheque, "ov_appel", return_value=Reponse(data)) as appel, \
             patch.object(main, "carte_par_id", side_effect=lambda kind, ident: {"type": kind, "id": ident}):
            out = main.catalogue_demandes(self.request)
        self.assertEqual(out["resultats"], [{"type": "movie", "id": 11}, {"type": "tv", "id": 22}])
        self.assertTrue(out["plus"])
        self.assertEqual(appel.call_args.args[:2], ("GET", "/request?take=18&skip=0&filter=all"))

    def test_echec_overseerr_est_visible(self):
        with patch.object(main.bibliotheque, "ov_cle", return_value="secret"), \
             patch.object(main.bibliotheque, "ov_appel", side_effect=RuntimeError("API inaccessible")):
            out = main.catalogue_demandes(self.request)
        self.assertEqual(out.status_code, 502)
        self.assertTrue(out.body)

    def test_compte_non_admin_reste_limite_a_son_activite(self):
        self.request.state.compte = {"id": 2, "admin": False}
        with patch.object(main.bibliotheque, "ov_cle", return_value="secret"), \
             patch.object(main.comptes, "medias_actifs", return_value=([( "movie", 33)], False)) as activite, \
             patch.object(main, "carte_par_id", return_value={"id": 33}):
            out = main.catalogue_demandes(self.request)
        activite.assert_called_once_with(2, "demande", 1, 18)
        self.assertEqual(out["resultats"], [{"id": 33}])


if __name__ == "__main__":
    unittest.main()
