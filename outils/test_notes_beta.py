"""Vérifie que les scores tiers restent inactifs même avec une configuration existante."""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from app import main


class NotesBeta(unittest.TestCase):
    def test_aucun_appel_fournisseur_avec_ancienne_cle(self):
        with patch.object(main, "lire_secrets", return_value={"omdb": "ancienne"}), \
             patch.object(main.requests, "get", side_effect=AssertionError("requête externe")), \
             patch.object(main, "overseerr_appel", side_effect=AssertionError("requête Overseerr")):
            self.assertEqual(main.ov_notes("movie", 42), {})
            self.assertEqual(main.omdb_notes({"liens": {"imdb": "https://www.imdb.com/title/tt1234567/"}}), {})
            self.assertIsNone(main.notes_titre("movie", 42, {}))
            self.assertFalse(main.omdb_tester("ancienne")[0])
            self.assertEqual(main.omdb_etat(), {"configuree": False, "disponible": False})

    def test_configuration_refusee_sans_lecture_ni_ecriture(self):
        with patch.object(main, "corps_json", side_effect=AssertionError("lecture")), \
             patch.object(main, "ecrire_prive", side_effect=AssertionError("écriture")):
            self.assertEqual(asyncio.run(main.omdb_cle(None)).status_code, 410)

    def test_les_notes_tmdb_restent_sur_les_cartes(self):
        self.assertEqual(main.carte({"id": 42, "vote_average": 7.4})["note"], 7.4)
