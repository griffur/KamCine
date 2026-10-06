"""Normalisation locale des entrées du Journal, sans FastAPI ni réseau."""
import os
import sys
import tempfile
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
donnees = tempfile.TemporaryDirectory(prefix="kamcine-journal-actions-")
os.environ["KAMCINE_DIR"] = donnees.name
import journal_ui
import erreurs


class JournalUI(unittest.TestCase):
    def test_trie_les_evenements_dates_et_garde_le_contexte(self):
        lignes = [
            (100, "Film : Dune (2021) | position 0s sur 100s"),
            (102, "Transmission : téléchargement retiré de la liste, fichiers conservés : Dune.2021.2160p"),
            (101, "Réveil de l'Apple TV"),
        ]
        erreurs = ["[1970-01-01T00:02:00+00:00] Connexion impossible à Transmission.", "Traceback (most recent call last):", "RuntimeError: RPC timeout"]
        events, sources = journal_ui.evenements(lignes, erreurs)
        self.assertEqual(events[0]["message"], "Connexion impossible à Transmission.")
        self.assertEqual(events[0]["type"], "erreur")
        self.assertIn("RPC timeout", events[0]["detail"])
        self.assertEqual(events[1]["source"], "Transmission")
        self.assertEqual(events[1]["type"], "succes")
        self.assertEqual(events[1]["torrent"], "Dune.2021.2160p")
        self.assertEqual(events[1]["titre"], "Dune (2021)")
        self.assertEqual(events[2]["source"], "Apple TV")
        self.assertIn("Transmission", sources)

    def test_anciennes_lignes_sans_date_restent_compatibles_et_recentes_dabord(self):
        events, _ = journal_ui.evenements([(None, "Ancienne ligne"), (None, "Ligne la plus récente")], [])
        self.assertEqual([e["message"] for e in events], ["Ligne la plus récente", "Ancienne ligne"])
        self.assertIsNone(events[0]["date"])
        self.assertEqual(events[0]["type"], "info")

    def test_details_techniques_orphelins_apres_rotation_de_l_historique_restent_visibles(self):
        events, sources = journal_ui.evenements([], ["Traceback (most recent call last):", "Transmission RPC timeout"])
        self.assertIn("Transmission RPC timeout", events[0]["detail"])
        self.assertEqual(events[0]["type"], "erreur")
        self.assertIn("Transmission", sources)

    def test_classifie_avertissement_succes_et_sources(self):
        events, _ = journal_ui.evenements([
            (20, "Apple TV : connexion réussie"),
            (21, "Attention : séance manquée"),
            (22, "FilmsActu : aucune bande annonce trouvée"),
        ], [])
        par_message = {e["message"]: e for e in events}
        self.assertEqual(par_message["Apple TV : connexion réussie"]["type"], "succes")
        self.assertEqual(par_message["Attention : séance manquée"]["type"], "avertissement")
        self.assertEqual(par_message["FilmsActu : aucune bande annonce trouvée"]["source"], "FilmsActu")

    def test_action_radarr_structuree_est_un_succes_et_non_une_erreur(self):
        events, sources = journal_ui.evenements([], [
            "[2026-09-24T12:00:00+02:00] [action:Radarr:succes] Recherche relancée via Radarr HD pour « Dune »."
        ])
        self.assertEqual(events[0]["source"], "Radarr")
        self.assertEqual(events[0]["type"], "succes")
        self.assertEqual(events[0]["message"], "Recherche relancée via Radarr HD pour « Dune ».")
        self.assertIn("Radarr", sources)

    def test_ecriture_action_persistante_et_permissions_privees(self):
        erreurs.journaliser_action("Radarr", "Recherche relancée via Radarr HD pour Dune.")
        events, _ = journal_ui.evenements([], erreurs.lire())
        self.assertEqual(events[0]["type"], "succes")
        self.assertEqual(events[0]["source"], "Radarr")
        self.assertEqual(Path(erreurs.CHEMIN).stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    try:
        unittest.main()
    finally:
        donnees.cleanup()
