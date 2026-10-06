"""Favoris propres à chaque compte (2.6.104, table user_favorites) et rattachement des favoris communs d'avant.
Hors réseau : python3 -m unittest outils.test_favoris"""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
donnees = tempfile.TemporaryDirectory(prefix="kamcine-favoris-")
import comptes
import favoris
favoris.FICHIER = str(Path(donnees.name, "favoris.json"))


class Favoris(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        environnement = patch.dict(os.environ, {"KAMCINE_DIR": donnees.name, "KAMCINE_DATA": ""})
        environnement.start()
        self.addCleanup(environnement.stop)
        for nom in ("kamcine.db", "kamcine.db-wal", "kamcine.db-shm", "favoris.json"):
            Path(donnees.name, nom).unlink(missing_ok=True)
        comptes.migrer()
        self.alice = comptes.creer_compte("Alice", "alice", "motdepasse", "admin", premier=True)["id"]
        self.camille = comptes.creer_compte("Camille", "camille", "motdepasse")["id"]

    def test_ajout_retrait_par_compte_sans_confondre_type(self):
        self.assertTrue(favoris.basculer(self.alice, "movie", 603, "Matrix", "https://image.tmdb.org/t/p/w342/matrix.jpg", "1999"))
        self.assertTrue(favoris.basculer(self.alice, "tv", 603, "Matrix série", None, ""))
        self.assertTrue(favoris.est_favori(self.alice, "movie", 603))
        self.assertEqual(favoris.liste(self.alice, "movie")[0]["affiche"], "https://image.tmdb.org/t/p/w342/matrix.jpg")
        self.assertEqual(favoris.liste(self.camille), [], "Camille commence sans favori")
        self.assertFalse(favoris.est_favori(self.camille, "movie", 603))
        self.assertTrue(favoris.basculer(self.camille, "movie", 27205, "Inception"))
        self.assertEqual([x["id"] for x in favoris.liste(self.alice, "movie")], [603], "le favori de Camille n'arrive pas chez Alice")
        self.assertFalse(favoris.basculer(self.alice, "movie", 603))
        self.assertFalse(favoris.est_favori(self.alice, "movie", 603))
        self.assertTrue(favoris.est_favori(self.alice, "tv", 603))
        self.assertFalse(Path(favoris.FICHIER).exists(), "favoris.json n'est plus écrit")

    def test_rejette_identifiant_invalide_et_affiche_non_https_tmdb(self):
        with self.assertRaises(ValueError):
            favoris.basculer(self.alice, "documentary", 1, "Titre")
        favoris.basculer(self.alice, "movie", 1, "Titre", "http://image.tmdb.org/t/p/w342/x.jpg")
        self.assertIsNone(favoris.liste(self.alice, "movie")[0]["affiche"])

    def test_favoris_anciens_au_premier_administrateur_seulement(self):
        anciens = [{"type": "movie", "id": 603, "titre": "Matrix", "affiche": None, "annee": "1999", "t": 1000},
                   {"type": "tv", "id": 1399, "titre": "Game of Thrones", "t": 2000}, {"type": "x", "id": 5}, "illisible"]
        with open(favoris.FICHIER, "w") as f:
            json.dump(anciens, f)
        self.assertEqual(comptes.rattacher_favoris_anciens(favoris.anciens()), self.alice)
        self.assertEqual([(x["type"], x["id"]) for x in favoris.liste(self.alice)], [("tv", 1399), ("movie", 603)])
        self.assertEqual(favoris.liste(self.camille), [])
        self.assertIsNone(comptes.rattacher_favoris_anciens(favoris.anciens()), "une seule fois")
        self.assertEqual(len(favoris.liste(self.alice)), 2)
        with open(favoris.FICHIER) as f:
            self.assertEqual(json.load(f), anciens, "favoris.json intact pour un retour arrière")

    def test_favoris_anciens_attendent_le_premier_compte(self):
        for nom in ("kamcine.db", "kamcine.db-wal", "kamcine.db-shm"):
            Path(donnees.name, nom).unlink(missing_ok=True)
        comptes.migrer()
        self.assertIsNone(comptes.rattacher_favoris_anciens([{"type": "movie", "id": 603}]))
        self.assertIsNone(comptes.lire_etat("favoris_anciens"))
        admin = comptes.creer_compte("Alice", "alice", "motdepasse", "admin", premier=True)["id"]
        self.assertEqual(comptes.rattacher_favoris_anciens([{"type": "movie", "id": 603}]), admin)


if __name__ == "__main__":
    unittest.main()
