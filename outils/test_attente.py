"""Séances en attente de téléchargement : plus d'abandon automatique par délai, migration des anciens échecs, sans réseau."""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_tmp = tempfile.TemporaryDirectory()
os.environ["KAMCINE_DIR"] = _tmp.name
import attente
import config

FILM_TELECHARGEMENT = {"pret": False, "etat": "telechargement", "progres": 40, "restant": "10 min"}
FILM_ABSENT = {"pret": False, "etat": "absent", "progres": None, "restant": None}
FILM_ECHEC_RADARR = {"pret": False, "etat": "echec", "progres": None, "restant": None}
CFG = config.DEFAUTS
CONFLIT_LIBRE = lambda t, s: (None, t)
OCCUPE_NON = lambda: False


class Abandon(unittest.TestCase):
    def test_etat_demande_est_presentable_sans_confusion_avec_action_demander(self):
        self.assertEqual(attente.LIBELLES["demande"], "En recherche…")

    def test_reste_en_attente_sans_limite_de_duree(self):
        for film in (FILM_TELECHARGEMENT, FILM_ABSENT, FILM_ECHEC_RADARR):
            for age_h in (1, 48, 49, 1000, 10000):
                e = {"cree": time.time() - age_h * 3600, "quand": None}
                ch, go = attente.evaluer(e, film, time.time(), CFG, CONFLIT_LIBRE, 0, OCCUPE_NON)
                self.assertNotEqual(ch["statut"], "echec")
                self.assertFalse(go)

    def test_echec_radarr_reste_une_reprise_pas_un_echec_definitif(self):
        e = {"cree": time.time() - 100 * 3600, "quand": None}
        ch, go = attente.evaluer(e, FILM_ECHEC_RADARR, time.time(), CFG, CONFLIT_LIBRE, 0, OCCUPE_NON)
        self.assertEqual(ch["statut"], "demande")
        self.assertIn("va réessayer", ch["message"])

    def test_attente_abandon_h_retiree_de_la_configuration(self):
        self.assertNotIn("attente_abandon_h", config.DEFAUTS)
        self.assertNotIn("attente_abandon_h", config.SPEC)

    def test_date_choisie_manquee_reste_inchangee(self):
        e = {"cree": time.time() - 10, "quand": time.time() - 3600}
        ch, go = attente.evaluer(e, FILM_TELECHARGEMENT, time.time(), CFG, CONFLIT_LIBRE, 0, OCCUPE_NON)
        self.assertEqual(ch["statut"], "manquee")
        self.assertFalse(go)


class ReprendreEchecsAbandon(unittest.TestCase):
    def setUp(self):
        with open(attente.FICHIER, "w", encoding="utf-8") as f:
            json.dump([], f)

    def _ecrire(self, entree):
        with open(attente.FICHIER, "w", encoding="utf-8") as f:
            json.dump([entree], f)

    def test_ancien_echec_par_abandon_retrouve_un_etat_actif(self):
        self._ecrire({"pid": "a1", "statut": "echec", "fin": 1000.0,
                      "message": "Aucun fichier arrivé après 48 heures : le téléchargement n'a pas abouti."})
        attente.reprendre_echecs_abandon()
        e = attente.trouver("a1")
        self.assertEqual(e["statut"], "demande")
        self.assertEqual(e["message"], "")
        self.assertIsNone(e["fin"])

    def test_echec_pour_une_autre_raison_n_est_pas_touche(self):
        self._ecrire({"pid": "a2", "statut": "echec", "fin": 1000.0, "message": "Erreur au lancement : Infuse ne répond pas."})
        attente.reprendre_echecs_abandon()
        e = attente.trouver("a2")
        self.assertEqual(e["statut"], "echec")
        self.assertEqual(e["message"], "Erreur au lancement : Infuse ne répond pas.")

    def test_annulee_n_est_jamais_touchee(self):
        self._ecrire({"pid": "a3", "statut": "annulee", "fin": 1000.0, "message": "Annulée"})
        attente.reprendre_echecs_abandon()
        e = attente.trouver("a3")
        self.assertEqual(e["statut"], "annulee")


class RappelProgramme(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fichier = patch.object(attente, "FICHIER", str(Path(self.tmp.name) / "attente.json"))
        self.fichier.start()
        self.addCleanup(self.fichier.stop)

    def test_rappel_est_envoye_une_seule_fois_a_la_date_choisie(self):
        maintenant = 10000
        entree, conflit = attente.ajouter({"type": "movie", "id": 42, "titre": "Film",
                                           "quand": maintenant + 900, "rappel_min": 15}, maintenant)
        self.assertIsNone(conflit)
        with patch("notifications.signaler") as notifier:
            for _ in range(2):
                attente.tick(lambda e: FILM_TELECHARGEMENT, Mock(), Mock(return_value={}),
                             CONFLIT_LIBRE, lambda e: 0, OCCUPE_NON, maintenant=maintenant)
            notifier.assert_called_once()
        self.assertTrue(attente.trouver(entree["pid"])["rappel_envoye"])


if __name__ == "__main__":
    unittest.main()
