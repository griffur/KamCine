"""Le vrai seance.py face à un média pas encore indexé par Infuse (banc d'essai, faux Apple TV, aucun réseau réel).

Lent (délais du mode test, comme les autres scénarios de outils/banc_seance/) : compter une minute et demie."""
import subprocess
import sys
import os
import signal
import unittest
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]


class IndexationInfuse(unittest.TestCase):
    def test_retente_puis_confirme_sans_echouer_tout_de_suite(self):
        # Le banc simule volontairement une attente d'indexation et plusieurs lectures.
        # Il tourne dans son propre groupe pour qu'un timeout termine aussi ses enfants.
        p = subprocess.Popen([sys.executable, str(RACINE / "outils" / "banc_seance" / "lancer.py"), "indexation"],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
        try:
            sortie, _ = p.communicate(timeout=110)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            sortie, _ = p.communicate()
            self.fail("Le scénario Infuse dépasse 110 s; groupe de processus arrêté proprement.\n" + sortie[-2000:])
        self.assertEqual(p.returncode, 0, sortie[-2000:])
        self.assertEqual(sortie.count("Lancement du film via Infuse"), 2, sortie[-2000:])
        self.assertIn("Lecture confirmée : movie:603:0:0", sortie)
        self.assertNotIn("Infuse n'a pas démarré la lecture", sortie)
        self.assertIn("Séance terminée", sortie)


if __name__ == "__main__":
    unittest.main()
