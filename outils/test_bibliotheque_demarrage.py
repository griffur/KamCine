"""Le cache des services démarre avec l'application, pas lors du healthcheck."""
import sys
import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("KAMCINE_DATA", "/private/tmp/kc-startup-test-data")
from app import main


class DemarrageBibliotheque(unittest.TestCase):
    def test_demarrage_lance_le_rafraichisseur_des_badges(self):
        with patch.object(main.threading, "Thread") as thread, \
             patch.object(main.filmsactu, "boucle"), \
             patch.object(main.atvlive.LIVE, "demarrer"), \
             patch.object(main.bibliotheque, "demarrer") as demarrer:
            main.demarrage()
        self.assertEqual(thread.call_count, 3)
        demarrer.assert_called_once()
        taches = demarrer.call_args.args[0]
        self.assertEqual(len(taches), 1)
        self.assertTrue(callable(taches[0]))


if __name__ == "__main__":
    unittest.main()
