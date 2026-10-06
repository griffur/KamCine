"""Une prévisualisation de saga lit l'index local et n'attend pas un rafraîchissement réseau."""
import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from app import main


class PreparationCollection(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.request = SimpleNamespace()
        self.apercu = {"titre": "Saga", "ids": [11, 22], "items": {"22": {"titre": "Film test"}},
                       "candidats": {"hd": [22], "uhd": []},
                       "fiables": {"hd": True, "uhd": True}}

    async def test_apercu_utilise_l_index_sans_attendre_un_refresh_complet(self):
        with patch.object(main, "corps_json", new=AsyncMock(return_value={"action": "preview", "collection_id": 99})), \
             patch.object(main.bibliotheque, "ov_cle", return_value="configured"), \
             patch.object(main.bibliotheque, "rafraichir_films") as refresh, \
             patch.object(main, "_statuts_collection", return_value=self.apercu) as statuts:
            out = await main.catalogue_collection_demander(self.request)
        self.assertEqual(out["candidats"]["hd"], [22])
        refresh.assert_not_called()
        statuts.assert_called_once_with(99, complet=False)

    async def test_confirmation_relit_les_sources_avant_validation(self):
        with patch.object(main, "corps_json", new=AsyncMock(return_value={
                 "action": "confirmer", "collection_id": 99, "versions": "hd",
                 "candidats": {"hd": [22]}})), \
             patch.object(main.bibliotheque, "ov_cle", return_value="configured"), \
             patch.object(main.bibliotheque, "rafraichir_films", return_value=True) as refresh, \
             patch.object(main.bibliotheque, "vider") as vider, \
             patch.object(main, "_statuts_collection", return_value=self.apercu), \
             patch.object(main, "noter_demande"), \
             patch.object(main, "noter_activite"), \
             patch.object(main.erreurs, "journaliser_action"), \
             patch.object(main, "envoyer_demande", return_value={"hd": {"ok": True, "message": "ok"}}):
            out = await main.catalogue_collection_demander(self.request)
        refresh.assert_called_once_with()
        vider.assert_called_once_with()
        self.assertEqual(out["envoyees"], 1)


if __name__ == "__main__":
    unittest.main()
