"""Persistance des étoiles personnelles, distinctes des notes de sources externes."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import notes_personnelles


class NotesPersonnellesTest(unittest.TestCase):
    def test_note_est_persistante_par_compte_et_remplacable(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(notes_personnelles, "_FICHIER", str(Path(tmp) / "notes.json")):
            self.assertIsNone(notes_personnelles.lire(7, "movie", 603))
            self.assertEqual(notes_personnelles.enregistrer(7, "movie", 603, 4), 4)
            self.assertEqual(notes_personnelles.lire(7, "movie", 603), 4)
            notes_personnelles.enregistrer(7, "movie", 603, 2)
            self.assertEqual(notes_personnelles.lire(7, "movie", 603), 2)
            self.assertIsNone(notes_personnelles.lire(8, "movie", 603))
            self.assertEqual(Path(notes_personnelles._FICHIER).stat().st_mode & 0o777, 0o600)

    def test_note_refuse_valeurs_hors_limites_ou_type_inconnu(self):
        with self.assertRaises(ValueError):
            notes_personnelles.enregistrer(1, "movie", 603, 0)
        with self.assertRaises(ValueError):
            notes_personnelles.enregistrer(1, "person", 603, 5)


if __name__ == "__main__":
    unittest.main()
