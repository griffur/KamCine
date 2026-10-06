"""Point 82 : envoi d'une demande Overseerr, délai propre et expiration sans doublon, sans réseau."""
import json
import asyncio
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import requests
from app import main


def reponse(code=201, corps=None):
    r = Mock(status_code=code, text=json.dumps(corps or {}))
    r.json.return_value = corps or {}
    r.raise_for_status = Mock()
    return r


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t))


class Demande(unittest.TestCase):
    def setUp(self):
        for nom in ("invalider_demandes_recentes",):
            p = patch.object(main, nom, Mock());p.start();self.addCleanup(p.stop)
        p = patch.object(main.bibliotheque, "vider", Mock());p.start();self.addCleanup(p.stop)
        p = patch.object(main.erreurs, "message", Mock(return_value="Demande impossible à Overseerr."));p.start();self.addCleanup(p.stop)

    def appels(self, *effets):
        appel = Mock(side_effect=list(effets))
        p = patch.object(main, "overseerr_appel", appel);p.start();self.addCleanup(p.stop)
        return appel

    def test_envoi_utilise_son_propre_delai(self):
        appel = self.appels(reponse(201))
        with patch.object(main, "charger", return_value={"overseerr_demande_delai_s": 75}):
            self.assertTrue(main.envoyer_demande("movie", 1, "hd")["hd"]["ok"])
        self.assertEqual(appel.call_args.kwargs["delai"], 75)
        self.assertEqual(main.charger()["overseerr_demande_delai_s"], 60)

    def test_expiration_puis_demande_trouvee_vaut_reussite_sans_renvoi(self):
        appel = self.appels(requests.exceptions.ReadTimeout("lecture"),
                            reponse(200, {"mediaInfo": {"requests": [{"is4k": False, "status": 1, "createdAt": iso(time.time())}]}}))
        r = main.envoyer_demande("movie", 1, "hd")["hd"]
        self.assertTrue(r["ok"])
        self.assertEqual([c.args[0] for c in appel.call_args_list], ["POST", "GET"], "Jamais un second POST")

    def test_expiration_sans_trace_reste_incertaine_sans_renvoi(self):
        ancienne = {"is4k": False, "status": 2, "createdAt": iso(time.time() - 86400)}
        autre_qualite = {"is4k": True, "status": 1, "createdAt": iso(time.time())}
        appel = self.appels(requests.exceptions.ReadTimeout("lecture"),
                            reponse(200, {"mediaInfo": {"requests": [ancienne, autre_qualite]}}))
        r = main.envoyer_demande("movie", 1, "hd")["hd"]
        self.assertFalse(r["ok"])
        self.assertTrue(r["incertaine"])
        self.assertIn("vérifie dans Overseerr", r["message"])
        self.assertEqual([c.args[0] for c in appel.call_args_list], ["POST", "GET"])

    def test_relecture_impossible_reste_incertaine(self):
        self.appels(requests.exceptions.ReadTimeout("lecture"), requests.exceptions.ConnectionError("hors ligne"))
        self.assertTrue(main.envoyer_demande("tv", 5, "hd")["hd"]["incertaine"])

    def test_date_absente_accepte_une_demande_non_refusee(self):
        self.appels(requests.exceptions.ReadTimeout("lecture"),
                    reponse(200, {"mediaInfo": {"requests": [{"is4k": True, "status": 3}]}}),
                    )
        self.assertFalse(main.envoyer_demande("movie", 1, "uhd")["uhd"]["ok"], "Une demande refusée ne compte pas")
        self.appels(requests.exceptions.ReadTimeout("lecture"),
                    reponse(200, {"mediaInfo": {"requests": [{"is4k": True, "status": 1}]}}))
        self.assertTrue(main.envoyer_demande("movie", 1, "uhd")["uhd"]["ok"])

    def test_connexion_impossible_garde_le_message_habituel(self):
        appel = self.appels(requests.exceptions.ConnectTimeout("connexion"))
        r = main.envoyer_demande("movie", 1, "hd")["hd"]
        self.assertFalse(r["ok"])
        self.assertNotIn("incertaine", r)
        self.assertEqual(appel.call_count, 1)

    def test_route_explique_l_incertitude(self):
        self.appels(requests.exceptions.ReadTimeout("lecture"), reponse(200, {"mediaInfo": {}}))
        with patch.object(main, "noter_demande") as noter:
            r = main.overseerr_demander(type="movie", id=1, versions="hd")
        corps = json.loads(r.body)
        self.assertFalse(corps["ok"])
        self.assertIn("pas répondu à temps", corps["message"])
        noter.assert_not_called()

    def test_seance_demande_4k_meme_si_hd_est_deja_disponible(self):
        st = {"global": "partiel", "hd": {"etat": "disponible", "source": "radarr"},
              "uhd": {"etat": "absent", "source": "radarr"}}
        envoi = Mock(return_value={"uhd": {"ok": True, "message": ""}})
        attente_ajoutee = Mock(return_value=({"id": "attente-1"}, None))
        with patch.object(main, "corps_json", new=AsyncMock(return_value={"id": 42, "versions": "uhd"})), \
             patch.object(main.bibliotheque, "ov_cle", return_value="cle"), \
             patch.object(main, "meta_par_id", return_value={"titre": "Film", "affiche": None}), \
             patch.object(main.bibliotheque, "statut", return_value=st), \
             patch.object(main.attente, "active_pour", return_value=None), \
             patch.object(main, "charger", return_value={"rappel_min": 15, "rappel_actif": False, "qualite_preferee": "hd"}), \
             patch.object(main, "mode_effectif", return_value="reel"), \
             patch.object(main, "envoyer_demande", envoi), \
             patch.object(main, "noter_demande"), patch.object(main, "noter_activite"), \
             patch.object(main.attente, "ajouter", attente_ajoutee), \
             patch.object(main, "attente_json", return_value={"id": "attente-1"}), \
             patch.object(main.threading, "Thread"):
            resultat = asyncio.run(main.attente_ajouter(None))
        self.assertTrue(resultat["ok"])
        envoi.assert_called_once_with("movie", 42, "uhd")
        self.assertEqual(attente_ajoutee.call_args.args[0]["versions"], "uhd")

    def test_options_4k_ne_sont_pas_bloquees_par_le_statut_hd_global(self):
        st = {"global": "partiel", "hd": {"etat": "disponible", "source": "radarr"},
              "uhd": {"etat": "absent", "source": "radarr"}}
        cfg = {"qualite_preferee": "hd", "attente_debut_min": 0, "attente_fin_min": 1440,
               "attente_delai_s": 0}
        with patch.object(main, "charger", return_value=cfg), \
             patch.object(main.bibliotheque, "statut", return_value=st), \
             patch.object(main.bibliotheque, "ov_cle", return_value="cle"), \
             patch.object(main.attente, "active_pour", return_value=None), \
             patch.object(main.planning, "regler"), patch.object(main.planning, "arrondi_propose", return_value=1), \
             patch.object(main.planning, "plus_tot", return_value=1), \
             patch.object(main.planning, "local_iso", return_value="date"), \
             patch.object(main, "mode_effectif", return_value="reel"):
            resultat = main.attente_options(id=42, versions="uhd")
        self.assertEqual(resultat["statut"], "absent")
        self.assertEqual(resultat["versions"], "uhd")

    def test_options_reconnait_une_demande_locale_4k_pas_encore_indexee(self):
        st = {"global": "disponible", "hd": {"etat": "disponible", "source": "radarr"},
              "uhd": {"etat": "recherche", "source": "radarr"}}
        cfg = {"qualite_preferee": "hd", "attente_debut_min": 0, "attente_fin_min": 1440,
               "attente_delai_s": 0}
        with tempfile.TemporaryDirectory() as dossier, \
             patch.object(main, "DEMANDES_AUTO", str(Path(dossier) / "demandes.json")), \
             patch.object(main, "charger", return_value=cfg), \
             patch.object(main.bibliotheque, "statut", return_value=st), \
             patch.object(main.bibliotheque, "ov_cle", return_value="cle"), \
             patch.object(main.attente, "active_pour", return_value=None), \
             patch.object(main.planning, "regler"), patch.object(main.planning, "arrondi_propose", return_value=1), \
             patch.object(main.planning, "plus_tot", return_value=1), \
             patch.object(main.planning, "local_iso", return_value="date"), \
             patch.object(main, "mode_effectif", return_value="reel"):
            main.noter_demande("movie", 42, "uhd")
            resultat = main.attente_options(id=42, versions="uhd")
        self.assertTrue(resultat["deja_demande"])
        self.assertEqual(resultat["statut"], "recherche")


if __name__ == "__main__":
    unittest.main()
