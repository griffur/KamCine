"""Préférence Apple TV propre au compte, sans mutation de la configuration globale."""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import main


class Requete:
    def __init__(self, corps):
        self.corps = corps

    async def json(self):
        return self.corps


class PreferenceAppleTV(unittest.IsolatedAsyncioTestCase):
    async def test_choix_valide_est_enregistre_sur_le_compte(self):
        appareils = [{"identifiant": "salon", "nom": "Salon"}, {"identifiant": "chambre", "nom": "Chambre"}]
        with patch.object(main, "id_connecte", return_value=17), \
             patch.object(main.integrations, "appletvs", return_value=appareils), \
             patch.object(main.comptes, "ecrire_preference") as ecrire:
            resultat = await main.moi_appletv(Requete({"identifiant": "chambre"}))
        self.assertEqual(resultat, {"ok": True, "identifiant": "chambre"})
        ecrire.assert_called_once_with(17, "appletv", "chambre")

    async def test_appareil_inconnu_est_refuse_sans_ecriture(self):
        with patch.object(main, "id_connecte", return_value=17), \
             patch.object(main.integrations, "appletvs", return_value=[{"identifiant": "salon"}]), \
             patch.object(main.comptes, "ecrire_preference") as ecrire:
            resultat = await main.moi_appletv(Requete({"identifiant": "inconnu"}))
        self.assertEqual(resultat.status_code, 400)
        ecrire.assert_not_called()

    async def test_session_absente_est_refusee(self):
        with patch.object(main, "id_connecte", return_value=None), \
             patch.object(main.integrations, "appletvs") as appareils:
            resultat = await main.moi_appletv(Requete({"identifiant": "salon"}))
        self.assertEqual(resultat.status_code, 401)
        appareils.assert_not_called()


if __name__ == "__main__":
    unittest.main()
