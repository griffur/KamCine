"""Une écriture interrompue conserve le JSON précédent et les droits privés."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import json_atomique


class JsonAtomique(unittest.TestCase):
    def test_remplacement_prive(self):
        with tempfile.TemporaryDirectory() as dossier:
            cible = Path(dossier) / "etat.json"
            json_atomique.ecrire(cible, {"nom": "été"})
            self.assertEqual(json.loads(cible.read_text()), {"nom": "été"})
            self.assertEqual(cible.stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(Path(dossier).iterdir()), [cible])

    def test_erreurs_conservent_le_fichier(self):
        for panne in ("serialisation", "replace", "fsync"):
            with self.subTest(panne=panne), tempfile.TemporaryDirectory() as dossier:
                cible = Path(dossier) / "secrets.json"
                cible.write_text('{"ancien": true}')
                before = cible.read_bytes()
                if panne == "serialisation":
                    with self.assertRaises(TypeError):
                        json_atomique.ecrire(cible, {"nouveau": object()})
                else:
                    with patch("json_atomique.os." + panne, side_effect=OSError("interruption")):
                        with self.assertRaises(OSError):
                            json_atomique.ecrire(cible, {"nouveau": True})
                self.assertEqual(cible.read_bytes(), before)
                self.assertEqual(list(Path(dossier).iterdir()), [cible])
