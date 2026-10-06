"""Normalisation publique des statuts portés par les affiches."""
import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from app import main
import bibliotheque
from app.main import etat_poster_public


class EtatsPoster(unittest.TestCase):
    def test_demande_uhd_reste_possible_si_hd_est_disponible(self):
        st = {"hd": {"etat": "disponible", "source": "radarr"},
              "uhd": {"etat": "absent", "source": "radarr"}}
        self.assertEqual(main._etat_demande_cible(st, "uhd"), "absent")
        self.assertEqual(main._qualites_a_demander(st, "uhd"), ("uhd",))
        self.assertEqual(main._qualites_a_demander(st, "both"), ("uhd",))

    def test_demande_uhd_en_recherche_ne_redevient_pas_une_action(self):
        st = {"hd": {"etat": "disponible", "source": "radarr"},
              "uhd": {"etat": "recherche", "source": "overseerr"}}
        self.assertEqual(main._etat_demande_cible(st, "uhd"), "recherche")
        self.assertEqual(main._qualites_a_demander(st, "uhd"), ())

    def test_recherche_arr_seule_ne_bloque_pas_une_demande_utilisateur(self):
        st = {"hd": {"etat": "disponible", "source": "radarr"},
              "uhd": {"etat": "recherche", "source": "radarr"}}
        self.assertEqual(main._etat_demande_cible(st, "uhd"), "absent")
        self.assertEqual(main._qualites_a_demander(st, "uhd"), ("uhd",))

    def test_demande_locale_est_memorisee_par_qualite(self):
        with tempfile.TemporaryDirectory() as dossier, patch.object(main, "DEMANDES_AUTO", str(Path(dossier) / "demandes.json")):
            main.noter_demande("movie", 42, "uhd")
            self.assertTrue(main.deja_demande("movie", 42, "uhd"))
            self.assertFalse(main.deja_demande("movie", 42, "hd"))
            self.assertTrue(main.deja_demande("movie", 42))
            self.assertEqual(json.loads((Path(dossier) / "demandes.json").read_text()), ["qualite:movie:42:uhd"])

    def test_demande_locale_uhd_est_visible_sans_changer_le_statut_hd(self):
        statut = {"hd": {"etat": "disponible", "source": "radarr"},
                  "uhd": {"etat": "absent", "source": "radarr"}, "global": "disponible"}
        resultat = main.appliquer_demandes_locales("movie", 42, statut,
                                                   ["qualite:movie:42:uhd"])
        self.assertEqual(resultat["hd"]["etat"], "disponible")
        self.assertEqual(resultat["uhd"]["etat"], "attente")
        self.assertEqual(resultat["uhd"]["source"], "demande_locale")
        self.assertEqual(resultat["global"], "disponible")
        self.assertEqual(statut["uhd"]["etat"], "absent")  # La source de cache n'est pas mutée.

    def test_demande_globale_locale_couvre_hd_et_uhd(self):
        statut = {"hd": {"etat": "absent"}, "uhd": {"etat": "absent"}}
        resultat = main.appliquer_demandes_locales("movie", 42, statut, [42])
        self.assertEqual(resultat["hd"]["etat"], "attente")
        self.assertEqual(resultat["uhd"]["etat"], "attente")

    def test_demande_locale_de_serie_copie_les_saisons_sans_mutation_du_cache(self):
        saison = {"hd": {"etat": "absent"}, "uhd": {"etat": "absent"}}
        statut = {"hd": {"etat": "absent"}, "uhd": {"etat": "absent"}, "saisons": {"1": saison}}
        resultat = main.appliquer_demandes_locales("tv", 42, statut, ["qualite:tv:42:uhd"])
        self.assertEqual(resultat["saisons"]["1"]["uhd"]["etat"], "attente")
        self.assertEqual(saison["uhd"]["etat"], "absent")

    def test_disponibilite_et_telechargement_reel(self):
        self.assertEqual(etat_poster_public({"etat": "disponible", "source": "radarr"}), "available")
        self.assertEqual(etat_poster_public({"etat": "partiel", "source": "sonarr"}), "available")
        self.assertEqual(etat_poster_public({"etat": "telechargement", "source": "radarr"}), "downloading")

    def test_recherche_arr_seule_ne_devient_pas_un_etat_visible(self):
        self.assertIsNone(etat_poster_public({"etat": "recherche", "source": "radarr"}))
        self.assertEqual(etat_poster_public({"etat": "recherche", "source": "overseerr"}), "requested")
        self.assertEqual(etat_poster_public({"etat": "recherche", "source": "radarr"}, True), "requested")

    def test_demande_en_attente_et_a_venir(self):
        self.assertEqual(etat_poster_public({"etat": "attente", "source": "overseerr"}), "requested")
        self.assertEqual(etat_poster_public({"etat": "a_venir", "source": "radarr"}), "upcoming")
        self.assertEqual(etat_poster_public({"etat": "a_venir", "source": "radarr"}, True), "requested")
        self.assertEqual(etat_poster_public({"etat": "a_venir", "source": "radarr"}), "upcoming")
        self.assertEqual(etat_poster_public({"etat": "absent"}, True), "requested")

    def test_etats_non_demandes_ne_produisent_pas_de_badge(self):
        for etat in (None, "absent", "non_suivi", "inconnu", "echec", "refuse", "bloque"):
            self.assertIsNone(etat_poster_public({"etat": etat, "source": "radarr"}))

    def test_fusion_ne_perd_pas_une_demande_overseerr_face_a_la_recherche_arr(self):
        resultat = bibliotheque._fusion(
            {"etat": "recherche", "source": "overseerr", "demande": "approuvee"},
            {"etat": "recherche", "source": "radarr"},
        )
        self.assertEqual(resultat["source"], "overseerr")
        self.assertEqual(etat_poster_public(resultat), "requested")

    def test_fusion_reconnait_un_telechargement_overseerr_reel(self):
        resultat = bibliotheque._fusion(
            {"etat": "telechargement", "source": "overseerr", "progres": 37},
            {"etat": "recherche", "source": "radarr"},
        )
        self.assertEqual(etat_poster_public(resultat), "downloading")

    def test_fusion_conserve_le_caractere_a_venir_dune_demande(self):
        resultat = bibliotheque._fusion(
            {"etat": "attente", "source": "overseerr", "demande": "approbation"},
            {"etat": "a_venir", "source": "radarr"},
        )
        self.assertTrue(resultat["a_venir"])
        self.assertEqual(etat_poster_public(resultat), "requested")

    def test_lexique_utilisateur_ne_presente_plus_demandé(self):
        page = (Path(__file__).resolve().parents[1] / "app" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("Demandé", page)
        self.assertIn("requested:'En recherche…'", page)
        self.assertIn("v.demande&&['a_venir','absent','non_suivi','inconnu'].includes(v.etat)", page)
        self.assertIn("&&!v.demande&&OVOK", page)
        self.assertIn("Demande enregistrée", page)
        self.assertIn("fi-ep-number", page)
        self.assertIn("fi-season-state.rech", page)

    def test_fiabilite_series_verifie_sonarr_et_non_radarr(self):
        st = {"sources": {"sonarr_hd": "ok", "sonarr_uhd": "ok"}}
        cache = {"listes": {n: {"origine": "reseau", "age_s": 3} for n in st["sources"]}}
        with patch.object(main, "charger", return_value={"badges_source": "arr", "badges_cache_min": 5}), \
             patch.object(bibliotheque, "config", return_value={n: {"cle": "x"} for n in st["sources"]}), \
             patch.object(bibliotheque, "etat_cache", return_value=cache):
            self.assertTrue(main._etat_collection_fiable(st))


if __name__ == "__main__":
    unittest.main()
