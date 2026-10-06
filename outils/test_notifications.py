"""Tests locaux du registre de notifications et de ses transitions observées."""
import os
import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
donnees = tempfile.TemporaryDirectory(prefix="kamcine-notifications-")
os.environ["KAMCINE_DIR"] = donnees.name
import notifications


class Notifications(unittest.TestCase):
    def setUp(self):
        notifications.FICHIER = os.path.join(donnees.name, "notifications.json")
        if os.path.exists(notifications.FICHIER):
            os.unlink(notifications.FICHIER)

    def test_dedoublonne_et_persiste_une_notification(self):
        cle = "overseerr:movie:42:hd"
        self.assertEqual(notifications.ajouter(cle, "demande_envoyee", "Demande envoyée", "Dune · HD",
                                               {"page": "fiche", "type": "movie", "id": 42}), 1)
        self.assertEqual(notifications.ajouter(cle, "demande_envoyee", "Demande envoyée", "Dune · HD"), 1)
        resultat = notifications.lister()
        self.assertEqual(len(resultat["notifications"]), 1)
        self.assertEqual(resultat["notifications"][0]["cible"]["id"], 42)

    def test_transmission_emet_seulement_sur_transition_active_vers_termine(self):
        h = "a" * 40
        actifs = {"en_cours": [{"hash": h, "nom": "Dune", "etat": "téléchargement"}], "termines": []}
        termines = {"en_cours": [], "termines": [{"hash": h, "nom": "Dune", "etat": "terminé"}]}
        self.assertEqual(notifications.observer_transmission(actifs), 0)  # premier relevé = référence
        self.assertEqual(notifications.observer_transmission(termines), 1)
        self.assertEqual(notifications.observer_transmission(termines), 1)
        evenement = notifications.lister()["notifications"][0]
        self.assertEqual(evenement["genre"], "telechargement_termine")
        self.assertEqual(evenement["cible"], {"page": "telechargements"})

    def test_transmission_ouvre_la_fiche_quand_l_identite_est_certaine(self):
        h = "d" * 40
        actif = {"en_cours": [{"hash": h, "nom": "Dune.2160p.mkv", "etat": "téléchargement"}], "termines": []}
        termine = {"en_cours": [], "termines": [{"hash": h, "nom": "Dune.2160p.mkv", "etat": "terminé",
                                                 "titre_media": "Dune", "media_type": "movie", "media_id": 438631}]}
        notifications.observer_transmission(actif)
        notifications.observer_transmission(termine)
        evenement = notifications.lister()["notifications"][0]
        self.assertEqual(evenement["cible"], {"page": "fiche", "type": "movie", "id": 438631})
        self.assertEqual(evenement["genre"], "telechargement_termine")
        self.assertEqual((evenement["titre"], evenement["description"]), ("Dune est prêt 🎬", "Disponible. Vous pouvez lancer votre séance."))

    def test_texte_oriente_seance_pour_une_serie_identifiee(self):
        h = "e" * 40
        notifications.observer_transmission({"en_cours": [{"hash": h, "nom": "Severance.S02", "etat": "téléchargement"}]})
        notifications.observer_transmission({"termines": [{"hash": h, "nom": "Severance.S02", "etat": "seed",
                                                           "titre_media": "Severance", "media_type": "tv", "media_id": 95396}]})
        evenement = notifications.lister()["notifications"][0]
        self.assertEqual((evenement["titre"], evenement["description"]), ("Severance est prêt 🎬", "Disponible. Vous pouvez lancer votre séance."))
        self.assertEqual(evenement["cible"], {"page": "fiche", "type": "tv", "id": 95396})

    def test_torrent_non_identifie_garde_un_texte_neutre(self):
        h = "f" * 40
        notifications.observer_transmission({"en_cours": [{"hash": h, "nom": "Linux.iso", "etat": "téléchargement"}]})
        notifications.observer_transmission({"termines": [{"hash": h, "nom": "Linux.iso", "etat": "terminé"}]})
        evenement = notifications.lister()["notifications"][0]
        self.assertEqual((evenement["titre"], evenement["description"]), ("Téléchargement terminé", "Linux.iso"))
        self.assertNotIn("séance", evenement["description"])

    def test_etat_identique_ne_recrit_pas_le_fichier_a_chaque_poll(self):
        courant = {"en_cours": [{"hash": "c" * 40, "nom": "Film", "etat": "téléchargement"}]}
        notifications.observer_transmission(courant)
        with patch.object(notifications, "_ecrire", wraps=notifications._ecrire) as ecrire:
            notifications.observer_transmission(courant)
        ecrire.assert_not_called()

    def test_erreur_de_torrent_et_echec_de_seance_sont_uniques(self):
        h = "b" * 40
        notifications.observer_transmission({"en_cours": [{"hash": h, "nom": "Film", "etat": "téléchargement"}]})
        erreur = {"en_cours": [{"hash": h, "nom": "Film", "etat": "erreur", "erreur": "Tracker arrêté"}]}
        self.assertEqual(notifications.observer_transmission(erreur), 1)
        seance = {"pid": "abc123", "statut": "lancement_echoue", "titre": "Alien"}
        self.assertEqual(notifications.observer_attentes([seance]), 2)
        self.assertEqual(notifications.observer_attentes([seance]), 2)
        self.assertEqual(notifications.marquer_lue(notifications.lister()["notifications"][0]["id"]), 1)
        self.assertEqual(notifications.marquer_tout_lu(), 0)
        self.assertEqual(notifications.lister()["non_lues"], 0)

    def test_webpush_declenche_seulement_a_la_creation_dedoublonnee(self):
        with patch("push.notifier_creation") as envoyer:
            notifications.ajouter("critique:unique", "telechargement_termine", "Terminé", "Dune")
            notifications.ajouter("critique:unique", "telechargement_termine", "Terminé", "Dune")
        envoyer.assert_called_once()
        self.assertEqual(envoyer.call_args.args[0][0]["genre"], "telechargement_termine")

    def test_ouverture_lit_snapshot_nouvelle_notification_reste_non_lue(self):
        notifications.ajouter("avant", "info", "Avant", "")
        self.assertEqual(notifications.ouvrir()["non_lues"], 0)
        notifications.ajouter("apres", "info", "Après", "")
        self.assertEqual(notifications.lister()["non_lues"], 1)

    def test_supprimer_ne_recree_pas_un_evenement_deja_signale(self):
        notifications.ajouter("une", "info", "Titre", "")
        self.assertEqual(notifications.supprimer_tout()["notifications"], [])
        notifications.ajouter("une", "info", "Titre", "")
        self.assertEqual(notifications.lister()["notifications"], [])
        notifications.ajouter("deux", "info", "Autre", "")
        self.assertEqual(notifications.lister()["non_lues"], 1)

    def test_disponibilite_uniquement_sur_transition_demande_connue(self):
        m = {("movie", 1): {"requests": [{"is4k": False}], "status": 3, "titre": "Film"}}
        notifications.observer_medias(m)
        self.assertEqual(notifications.lister()["non_lues"], 0)
        m[("movie", 1)]["status"] = 5
        notifications.observer_medias(m)
        notifications.observer_medias(m)
        self.assertEqual(notifications.lister()["non_lues"], 1)
        notifications.supprimer_tout()
        notifications.observer_medias(m)
        self.assertEqual(notifications.lister()["non_lues"], 0)


if __name__ == "__main__":
    try:
        unittest.main()
    finally:
        donnees.cleanup()
