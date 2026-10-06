"""Tests réseau localisés sur la recherche FilmsActu (aucun appel YouTube/TMDB)."""
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.setdefault("requests", types.SimpleNamespace())
import filmsactu


class RechercheFilmsActu(unittest.TestCase):
    def setUp(self):
        filmsactu._cache_api.clear()

    def _sources_vides(self):
        return (
            patch.object(filmsactu, "flux", return_value=([], {"requete": "rss", "statut": 200, "erreur": None, "nb": 0})),
            patch.object(filmsactu, "index", return_value=[]),
            patch.object(filmsactu, "cle_api", return_value="cle-test"),
            patch.object(filmsactu, "chaine_de_video", return_value=None),
        )

    def test_recherche_api_essaie_titre_original_apres_le_titre_francais(self):
        fr = {"source": "api", "requete": "q=Le Voyage", "statut": 200, "erreur": None, "nb": 0}
        original = {"source": "api", "requete": "q=The Journey", "statut": 200, "erreur": None, "nb": 1}
        video = {"id": "video123", "titre": "The Journey - Bande-annonce officielle (2025)",
                 "chaine_id": filmsactu.CHAINE_ID, "pub": "2025-01-01"}
        with self._sources_vides()[0], self._sources_vides()[1], self._sources_vides()[2], self._sources_vides()[3], \
                patch.object(filmsactu, "recherche_api", side_effect=[([], fr), ([video], original)]) as recherche, \
                patch.object(filmsactu, "maj_index", return_value=1):
            resultat = filmsactu.chercher(["Le Voyage", "The Journey"], 2025, ecrire_index=False)
        self.assertTrue(resultat["dispo"])
        self.assertEqual(resultat["cle"], "video123")
        self.assertEqual(resultat["origine"], "api")
        self.assertEqual([appel.args[0] for appel in recherche.call_args_list],
                         ["Le Voyage bande annonce", "The Journey bande annonce"])
        self.assertEqual(len([x for x in resultat["trace"] if x["source"].startswith("recherche YouTube")]), 2)

    def test_cibles_normalisees_identiques_ne_font_pas_de_requete_en_double(self):
        info = {"source": "api", "requete": "q=Amelie", "statut": 200, "erreur": None, "nb": 0}
        patches = self._sources_vides()
        with patches[0], patches[1], patches[2], patches[3], \
                patch.object(filmsactu, "recherche_api", return_value=([], info)) as recherche:
            resultat = filmsactu.chercher(["Amélie", "Amelie"], 2001, est_serie=False)
        recherche.assert_called_once()
        self.assertEqual(resultat["raison"], "aucun_resultat")

    def test_annee_incompatible_et_series_sans_annee(self):
        video = {"id": "video123", "titre": "Le Voyage - Bande annonce (2020)", "pub": "2020-01-01"}
        patches = self._sources_vides()
        with patches[0], patches[1], patches[2], patches[3], \
                patch.object(filmsactu, "index", return_value=[video]), \
                patch.object(filmsactu, "recherche_api", return_value=([], {"source": "api", "requete": "q", "statut": 200, "erreur": None, "nb": 0})) as recherche:
            film = filmsactu.chercher(["Le Voyage"], 2025, est_serie=False)
            serie = filmsactu.chercher(["Le Voyage"], 2025, est_serie=True)
        self.assertFalse(film["dispo"])
        self.assertTrue(serie["dispo"])
        recherche.assert_called_once()
        self.assertTrue(filmsactu.correspond("Amelie - Bande-annonce VF (2001)", ["Amélie"], 2001)[0])

    def test_tmdb_est_solution_de_repli_apres_filmsactu(self):
        info = {"source": "api", "requete": "q", "statut": 200, "erreur": None, "nb": 0}
        video = {"site": "YouTube", "type": "Trailer", "official": True, "key": "fallback123"}
        patches = self._sources_vides()
        with patches[0], patches[1], patches[2], \
                patch.object(filmsactu, "recherche_api", return_value=([], info)), \
                patch.object(filmsactu, "chaine_de_video", return_value=("Studio officiel", "@studio")):
            resultat = filmsactu.chercher(["Le Voyage"], 2025, lambda langue: [video], ecrire_index=False)
        self.assertTrue(resultat["dispo"])
        self.assertEqual(resultat["cle"], "fallback123")
        self.assertEqual(resultat["origine"], "tmdb_autre")
        self.assertIn("TMDB", resultat["verification"])

    def test_recherche_parcourt_les_sources_tmdb_avant_le_repli_et_prefere_filmsactu(self):
        info = {"source": "api", "requete": "q", "statut": 200, "erreur": None, "nb": 0}
        videos = [
            {"site": "YouTube", "type": "Trailer", "official": True, "key": "studio123", "name": "Le Voyage Official Trailer"},
            {"site": "YouTube", "type": "Trailer", "official": True, "key": "fa_video1", "name": "Le Voyage bande annonce officielle"},
        ]
        patches = self._sources_vides()
        with patches[0], patches[1], patches[2], \
                patch.object(filmsactu, "recherche_api", return_value=([], info)), \
                patch.object(filmsactu, "chaine_de_video", side_effect=[("Studio", "@studio"), ("FilmsActu", "https://www.youtube.com/@FilmsActu")]):
            resultat = filmsactu.chercher(["Le Voyage"], 2025, lambda langue: videos, ecrire_index=False)
        self.assertEqual(resultat["cle"], "fa_video1")
        self.assertEqual(resultat["origine"], "tmdb")

    def test_rejette_un_short_nommee_comme_tel(self):
        self.assertFalse(filmsactu.candidate_ba("Le Voyage #Shorts"))
        self.assertFalse(filmsactu.candidate_ba("Le Voyage - Fan trailer"))
        self.assertTrue(filmsactu.candidate_ba("Le Voyage bande-annonce officielle"))

    def test_archive_filmsactu_prefere_bande_annonce_complete_au_teaser(self):
        infos = {"requete": "rss", "statut": 200, "erreur": None, "nb": 2}
        index = [
            {"id": "teaser123", "titre": "Le Voyage - Teaser officiel", "pub": "2025-02-02"},
            {"id": "trailer123", "titre": "Le Voyage - Bande annonce officielle", "pub": "2025-01-01"},
        ]
        with patch.object(filmsactu, "flux", return_value=([], infos)), \
             patch.object(filmsactu, "index", return_value=index):
            resultat = filmsactu.chercher(["Le Voyage"], 2025, ecrire_index=False)
        self.assertEqual(resultat["cle"], "trailer123")


if __name__ == "__main__":
    unittest.main()
