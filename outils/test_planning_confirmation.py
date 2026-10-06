"""Vérifie la présence avant un lancement réel, sans réseau ni appareil."""
import ast
import concurrent.futures
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config
import planning


class ConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fichier = patch.object(planning, "FICHIER", str(Path(self.tmp.name) / "planning.json"))
        self.fichier.start()
        self.addCleanup(self.fichier.stop)
        self.horloge = patch.object(planning.time, "time", return_value=10000)
        self.temps = self.horloge.start()
        self.addCleanup(self.horloge.stop)
        self.journal = patch.object(planning.erreurs, "journaliser_action")
        self.log = self.journal.start()
        self.addCleanup(self.journal.stop)
        planning.regler(config.DEFAUTS)
        self.addCleanup(planning.regler, config.DEFAUTS)
        notif = patch("notifications.ajouter");notif.start();self.addCleanup(notif.stop)
        self.lancer = Mock(return_value=(True, ""))
        self.libre = Mock(return_value=False)

    def ajouter(self, **kw):
        return planning.ajouter(dict({"t": 10060, "type": "movie", "id": 1,
                                      "titre": "Film", "mode": "reel"}, **kw), 15)

    def test_rappel_ne_vaut_pas_confirmation(self):
        e = self.ajouter()
        planning.tick(self.lancer, self.libre)
        self.assertEqual(len(planning.rappels()), 1)
        planning.marquer(e["pid"], rappel_lu=True)
        self.temps.return_value = e["t"]
        planning.tick(self.lancer, self.libre)
        self.lancer.assert_not_called()
        self.assertEqual(planning.trouver(e["pid"])["etat"], "prevue")

    def test_absence_annule_une_seule_fois_sans_appareil(self):
        e = self.ajouter()
        self.temps.return_value = e["t"] + 120
        planning.tick(self.lancer, self.libre)
        planning.tick(self.lancer, self.libre)
        self.lancer.assert_not_called()
        fin = planning.trouver(e["pid"])
        self.assertEqual(fin["etat"], "annulee")
        self.assertTrue(fin["annulation_securite"])
        self.log.assert_called_once()
        self.assertFalse(planning.confirmer(e["pid"])[0])

    def test_confirmation_trop_tot_ou_a_la_limite_refusee(self):
        e = self.ajouter()
        for t in (e["t"] - 1, e["t"] + 120, e["t"] + 10000):
            self.temps.return_value = t
            self.assertFalse(planning.confirmer(e["pid"])[0])
        self.assertFalse(planning.confirmer("absente")[0])

    def test_double_confirmation_et_ticks_ne_lancent_qu_une_fois(self):
        e = self.ajouter()
        self.temps.return_value = e["t"]
        self.assertTrue(planning.confirmer(e["pid"])[0])
        self.assertTrue(planning.confirmer(e["pid"])[0])
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: planning.tick(self.lancer, self.libre), range(2)))
        self.lancer.assert_called_once()
        self.log.assert_called_once()
        self.assertEqual(planning.trouver(e["pid"])["etat"], "lancee")

    def test_confirmation_en_fin_de_delai_supporte_le_tick_suivant(self):
        planning.GARDES.update(confirmation_s=900, tolerance_s=0)
        e = self.ajouter()
        self.temps.return_value = e["t"] + 899
        self.assertTrue(planning.confirmer(e["pid"])[0])
        self.temps.return_value += 15
        planning.tick(self.lancer, self.libre)
        self.lancer.assert_called_once()

    def test_redemarrage_ne_rouvre_pas_le_delai(self):
        e = self.ajouter()
        planning.GARDES["confirmation_s"] = 900
        self.temps.return_value = e["t"] + 121
        planning.tick(self.lancer, self.libre)
        self.assertEqual(planning.trouver(e["pid"])["etat"], "annulee")
        self.lancer.assert_not_called()

    def test_confirmation_persiste_mais_ne_lance_pas_apres_long_arret(self):
        e = self.ajouter()
        self.temps.return_value = e["t"] + 1
        planning.confirmer(e["pid"])
        self.assertEqual(planning.trouver(e["pid"])["confirmation_acceptee"], e["t"] + 1)
        self.temps.return_value = e["t"] + 601
        planning.tick(self.lancer, self.libre)
        self.assertEqual(planning.trouver(e["pid"])["etat"], "manquee")
        self.lancer.assert_not_called()

    def test_deplacement_efface_la_presence(self):
        e = self.ajouter()
        self.temps.return_value = e["t"]
        planning.confirmer(e["pid"])
        neuve, refus = planning.deplacer_si_libre(e["pid"], 20000, 1000, 15)
        self.assertIsNone(refus)
        self.assertIsNone(neuve["confirmation_acceptee"])
        self.temps.return_value = 20000
        planning.tick(self.lancer, self.libre)
        self.lancer.assert_not_called()

    def test_annulation_apres_confirmation_empeche_le_lancement(self):
        e = self.ajouter()
        self.temps.return_value = e["t"]
        planning.confirmer(e["pid"])
        self.assertTrue(planning.annuler(e["pid"]))
        planning.tick(self.lancer, self.libre)
        self.lancer.assert_not_called()

    def test_confirmation_facultative_lance_directement_sans_confirmer(self):
        planning.GARDES["confirmation_requise"] = False
        self.addCleanup(planning.GARDES.update, confirmation_requise=True)
        e = self.ajouter()
        self.assertIsNone(planning.confirmation_fin(e))
        self.temps.return_value = e["t"]
        planning.tick(self.lancer, self.libre)
        self.lancer.assert_called_once()
        self.assertEqual(planning.trouver(e["pid"])["etat"], "lancee")

    def test_confirmation_facultative_respecte_toujours_l_occupation(self):
        planning.GARDES["confirmation_requise"] = False
        self.addCleanup(planning.GARDES.update, confirmation_requise=True)
        e = self.ajouter()
        self.temps.return_value = e["t"]
        planning.tick(self.lancer, lambda: True)
        self.assertEqual(planning.trouver(e["pid"])["etat"], "manquee")
        self.lancer.assert_not_called()

    def test_ancienne_seance_sans_champ_garde_la_confirmation_requise(self):
        e = self.ajouter()
        with planning._verrou:
            l = planning._lire()
            l[0].pop("confirmation_requise")
            planning._ecrire(l)
        self.assertIsNotNone(planning.confirmation_fin(planning.trouver(e["pid"])))

    def test_reglage_confirmation_requise_transmis_par_regler(self):
        planning.regler(dict(config.DEFAUTS, plan_confirmation_requise=False))
        self.assertFalse(planning.GARDES["confirmation_requise"])
        planning.regler(config.DEFAUTS)
        self.assertTrue(planning.GARDES["confirmation_requise"])

    def test_seance_manquee_reste_reprogrammable_et_retirable(self):
        e = self.ajouter()
        self.temps.return_value = e["t"]
        planning.confirmer(e["pid"])
        planning.tick(self.lancer, lambda: True)      # salon occupé au moment prévu : manquée
        manquee = planning.trouver(e["pid"])
        self.assertEqual(manquee["etat"], "manquee")
        self.assertTrue(planning.modifiable(manquee))
        neuve, refus = planning.deplacer_si_libre(e["pid"], 20000, 1000, 15)
        self.assertIsNone(refus)
        self.assertEqual(neuve["etat"], "prevue")
        self.assertIsNone(neuve["confirmation_acceptee"])
        self.temps.return_value = 20000
        self.assertTrue(planning.confirmer(e["pid"])[0])      # une reprogrammation redemande la présence
        planning.tick(self.lancer, self.libre)
        self.lancer.assert_called_once()

    def test_echec_retirable_et_idempotent(self):
        e = self.ajouter()
        self.temps.return_value = e["t"]
        planning.confirmer(e["pid"])
        self.lancer.side_effect = RuntimeError("Échec simulé")
        planning.tick(self.lancer, self.libre)
        self.assertEqual(planning.trouver(e["pid"])["etat"], "echec")
        self.assertTrue(planning.annuler(e["pid"]))
        self.assertEqual(planning.trouver(e["pid"])["etat"], "annulee")
        self.assertTrue(planning.annuler(e["pid"]))     # un second clic ne renvoie jamais un faux échec

    def test_annulation_securite_reste_reprogrammable(self):
        e = self.ajouter()
        self.temps.return_value = e["t"] + 120
        planning.tick(self.lancer, self.libre)
        ratee = planning.trouver(e["pid"])
        self.assertTrue(ratee["annulation_securite"])
        self.assertTrue(planning.modifiable(ratee))
        neuve, refus = planning.deplacer_si_libre(e["pid"], 20000, 1000, 15)
        self.assertIsNone(refus)
        self.assertEqual(neuve["etat"], "prevue")
        self.assertFalse(neuve["annulation_securite"])

    def test_lancee_n_est_plus_modifiable_ni_annulable(self):
        e = self.ajouter()
        self.temps.return_value = e["t"]
        planning.confirmer(e["pid"])
        planning.tick(self.lancer, self.libre)
        self.assertEqual(planning.trouver(e["pid"])["etat"], "lancee")
        self.assertFalse(planning.annuler(e["pid"]))
        neuve, refus = planning.deplacer_si_libre(e["pid"], 20000, 1000, 15)
        self.assertIsNone(neuve)
        self.assertIn("erreur", refus)

    def test_salon_occupe_avec_ou_sans_confirmation(self):
        for confirme in (False, True):
            e = self.ajouter()
            self.temps.return_value = e["t"]
            if confirme:
                planning.confirmer(e["pid"])
            planning.tick(self.lancer, lambda: True)
            self.assertEqual(planning.trouver(e["pid"])["etat"], "manquee")
        self.lancer.assert_not_called()

    def test_mode_test_garde_le_lancement_automatique(self):
        e = self.ajouter(mode="test")
        self.assertIsNone(planning.confirmation_fin(e))
        self.temps.return_value = e["t"]
        self.assertFalse(planning.confirmer(e["pid"])[0])
        planning.tick(self.lancer, self.libre)
        self.lancer.assert_called_once()

    def test_ancien_planning_sans_mode_est_protege(self):
        e = self.ajouter()
        with planning._verrou:
            l = planning._lire()
            l[0].pop("mode")
            l[0].pop("confirmation_delai_s")
            planning._ecrire(l)
        self.temps.return_value = e["t"]
        planning.tick(self.lancer, self.libre)
        self.lancer.assert_not_called()
        self.assertEqual(planning.trouver(e["pid"])["confirmation_delai_s"], 120)

    def test_echec_ne_relance_pas_automatiquement(self):
        e = self.ajouter()
        self.temps.return_value = e["t"]
        planning.confirmer(e["pid"])
        self.lancer.side_effect = RuntimeError("Échec simulé")
        planning.tick(self.lancer, self.libre)
        planning.tick(self.lancer, self.libre)
        self.lancer.assert_called_once()
        self.assertEqual(planning.trouver(e["pid"])["etat"], "echec")

    def test_reservation_inclut_le_delai_de_confirmation(self):
        avant = planning.duree_reservee("movie", 120)
        planning.GARDES["confirmation_s"] += 60
        self.assertEqual(planning.duree_reservee("movie", 120), avant + 60)

    def test_route_refus_et_confirmation(self):
        class JSONResponse:
            def __init__(self, content, status_code):
                self.content, self.status_code = content, status_code

        source = Path(__file__).resolve().parents[1] / "app" / "main.py"
        arbre = ast.parse(source.read_text())
        fonction = next(n for n in arbre.body if isinstance(n, ast.FunctionDef) and n.name == "planning_confirmer")
        fonction.decorator_list = []
        env = {"planning": planning, "charger": lambda: config.DEFAUTS, "JSONResponse": JSONResponse}
        exec(compile(ast.Module(body=[fonction], type_ignores=[]), str(source), "exec"), env)
        e = self.ajouter()
        self.assertEqual(env["planning_confirmer"](e["pid"]).status_code, 409)
        self.temps.return_value = e["t"]
        self.assertEqual(env["planning_confirmer"](e["pid"]).status_code, 200)
        self.lancer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
