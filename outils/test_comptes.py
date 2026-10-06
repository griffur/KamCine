"""Comptes, sessions et migrations SQLite (2.6.102), multi utilisateur (2.6.103).

Unitaires sur comptes.py (base temporaire), puis un vrai service (uvicorn, Apple TV neutralisée) pour la base refusée,
l'expiration d'une session, la persistance après redémarrage, le cookie Secure derrière HTTPS, puis le parcours à deux
comptes : création par l'administrateur, sessions simultanées, profil et photo de chacun, rôles, désactivation, routes
d'administration refusées à un utilisateur.
Il faut le Python du service : python -m unittest outils.test_comptes"""
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest

import requests

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RACINE)
sys.path.insert(0, os.path.join(RACINE, "outils"))
import comptes
from test_chemins import Service, jpeg


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ancien = os.environ.get("KAMCINE_DATA")
        os.environ["KAMCINE_DATA"] = self.tmp.name

    def tearDown(self):
        if self.ancien is None:
            os.environ.pop("KAMCINE_DATA", None)
        else:
            os.environ["KAMCINE_DATA"] = self.ancien
        self.tmp.cleanup()

    def sql(self, requete, *params):
        c = sqlite3.connect(comptes.chemin_base())
        try:
            r = c.execute(requete, params).fetchall()
            c.commit()
            return r
        finally:
            c.close()


class MotsDePasse(unittest.TestCase):
    def test_format_versionne_sel_unique_et_verification(self):
        a, b = comptes.hacher("popcorn-2026"), comptes.hacher("popcorn-2026")
        self.assertNotEqual(a, b)
        algo, iterations, sel, empreinte = a.split("$")
        self.assertEqual((algo, int(iterations), len(sel), len(empreinte)), ("pbkdf2_sha256", 600000, 32, 64))
        self.assertNotIn("popcorn", a)
        self.assertTrue(comptes.verifier("popcorn-2026", a))
        self.assertFalse(comptes.verifier("popcorn-2027", a))
        self.assertFalse(comptes.verifier("x", "md5$1$00$00"))
        self.assertFalse(comptes.verifier("x", "illisible"))
        self.assertTrue(comptes.verifier("abc", comptes.hacher("abc", iterations=1000)), "les itérations sont lues dans le format")


class Migrations(Base):
    def test_base_creee_privee_et_versionnee(self):
        self.assertEqual(comptes.migrer(), comptes.VERSION_BASE)
        self.assertEqual(comptes.migrer(), comptes.VERSION_BASE, "une seconde migration ne change rien")
        self.assertEqual(oct(os.stat(comptes.chemin_base()).st_mode & 0o777), "0o600")
        self.assertEqual(self.sql("SELECT version, nom FROM schema_migrations"), [(1, "comptes et sessions"), (2, "photo de chaque compte"),
                                                                                (3, "permissions, favoris et activité par compte"),
                                                                                (4, "préférences personnelles")])
        self.assertEqual(self.sql("PRAGMA user_version"), [(4,)])

    def test_preferences_isolees_par_compte_et_persistantes(self):
        comptes.migrer()
        a = comptes.creer_compte("Utilisateur A", "utilisateur-a", "motdepasse-test", role="admin", premier=True)
        b = comptes.creer_compte("Utilisateur B", "utilisateur-b", "motdepasse-test")
        comptes.ecrire_preference(a["id"], "appletv", "salon-id")
        comptes.ecrire_preference(b["id"], "appletv", "chambre-id")
        self.assertEqual(comptes.lire_preference(a["id"], "appletv"), "salon-id")
        self.assertEqual(comptes.lire_preference(b["id"], "appletv"), "chambre-id")
        self.assertEqual(comptes.authentifier("utilisateur-a", "motdepasse-test")["id"], a["id"], "la préférence survit à une nouvelle connexion")
        self.assertEqual(comptes.lire_preference(b["id"], "appletv"), "chambre-id", "la reconnexion A ne modifie pas le compte B")

    def test_base_2_6_102_migree_sans_perte(self):
        """Base créée par la 2.6.102 (schéma 1, un administrateur, une session) : sauvegardée, puis colonnes de photo ajoutées."""
        base = comptes.chemin_base()
        comptes.VERSION_BASE, anciennes = 1, list(comptes.MIGRATIONS)
        comptes.MIGRATIONS[:] = anciennes[:1]
        try:
            comptes.migrer()
            self.sql("INSERT INTO users (identifiant, nom, mot_de_passe, role, cree_a, maj_a) VALUES ('alex', 'Alex', ?, 'admin', 1, 1)",
                     comptes.hacher("motdepasse", iterations=1000))
        finally:
            comptes.MIGRATIONS[:] = anciennes
            comptes.VERSION_BASE = anciennes[-1][0]
        self.assertEqual(comptes.migrer(), comptes.VERSION_BASE)
        self.assertTrue(os.listdir(os.path.join(self.tmp.name, "backups"))[0].startswith("kamcine-schema1-"))
        u = comptes.authentifier("alex", "motdepasse")
        self.assertEqual((u["nom"], u["admin"], u["avatar_v"], u["bienvenue"]), ("Alex", True, None, False))
        self.assertTrue(os.path.exists(base))

    def test_base_plus_recente_refusee_sans_ecriture(self):
        comptes.migrer()
        self.sql("PRAGMA user_version = 99")
        avant = os.path.getmtime(comptes.chemin_base())
        with self.assertRaises(comptes.BaseTropRecente):
            comptes.migrer()
        self.assertEqual(self.sql("PRAGMA user_version"), [(99,)])
        self.assertEqual(os.path.getmtime(comptes.chemin_base()), avant)

    def test_migration_suivante_sauvegarde_puis_applique(self):
        comptes.migrer()
        comptes.creer_compte("Alex", "alex", "motdepasse", "admin", premier=True)
        actuelle = comptes.VERSION_BASE
        ajout = (actuelle + 1, "essai", lambda c: c.execute("CREATE TABLE essai (x INTEGER)"))
        comptes.MIGRATIONS.append(ajout)
        comptes.VERSION_BASE = actuelle + 1
        try:
            self.assertEqual(comptes.migrer(), actuelle + 1)
        finally:
            comptes.MIGRATIONS.remove(ajout)
            comptes.VERSION_BASE = actuelle
        sauvegardes = os.listdir(os.path.join(self.tmp.name, "backups"))
        self.assertEqual(len(sauvegardes), 1)
        self.assertTrue(sauvegardes[0].startswith("kamcine-schema%d-" % actuelle))
        copie = sqlite3.connect(os.path.join(self.tmp.name, "backups", sauvegardes[0]))
        self.assertEqual(copie.execute("SELECT identifiant FROM users").fetchall(), [("alex",)])
        copie.close()

    def test_migration_en_echec_annulee(self):
        comptes.migrer()
        actuelle = comptes.VERSION_BASE
        def casse(c):
            c.execute("CREATE TABLE a_moitie (x INTEGER)")
            raise RuntimeError("panne")
        ajout = (actuelle + 1, "cassée", casse)
        comptes.MIGRATIONS.append(ajout)
        comptes.VERSION_BASE = actuelle + 1
        try:
            with self.assertRaises(RuntimeError):
                comptes.migrer()
        finally:
            comptes.MIGRATIONS.remove(ajout)
            comptes.VERSION_BASE = actuelle
        self.assertEqual(self.sql("PRAGMA user_version"), [(actuelle,)])
        self.assertEqual(self.sql("SELECT name FROM sqlite_master WHERE name = 'a_moitie'"), [])


class Comptes(Base):
    def setUp(self):
        super().setUp()
        comptes.migrer()

    def test_premier_compte_administrateur_unique(self):
        admin = comptes.creer_compte("Alex", "Alex", "motdepasse", "admin", premier=True)
        self.assertEqual((admin["identifiant"], admin["role"], admin["admin"]), ("alex", "admin", True))
        self.assertNotIn("mot_de_passe", admin)
        with self.assertRaises(comptes.Refus):
            comptes.creer_compte("Autre", "autre", "motdepasse", "admin", premier=True)

    def test_deux_premiers_en_meme_temps_un_seul_gagne(self):
        resultats = []
        def creer(n):
            try:
                resultats.append(comptes.creer_compte("N%d" % n, "admin%d" % n, "motdepasse", "admin", premier=True))
            except comptes.Refus:
                resultats.append(None)
        fils = [threading.Thread(target=creer, args=(n,)) for n in range(4)]
        [f.start() for f in fils]
        [f.join() for f in fils]
        self.assertEqual(len([r for r in resultats if r]), 1)
        self.assertEqual(comptes.nombre_comptes(), 1)

    def test_plusieurs_comptes_identifiant_unique_sans_casse(self):
        comptes.creer_compte("Alex", "alex", "motdepasse", "admin", premier=True)
        amie = comptes.creer_compte("Léa", "lea", "autremotdepasse")
        self.assertEqual(amie["role"], "utilisateur")
        with self.assertRaises(comptes.Refus) as e:
            comptes.creer_compte("Autre", "ALEX", "motdepasse")
        self.assertEqual(e.exception.champ, "identifiant")
        self.assertEqual([c["identifiant"] for c in comptes.lister_comptes()], ["alex", "lea"])

    def test_saisies_refusees(self):
        for nom, ident, mdp, champ in (("", "alex", "motdepasse", "nom"), ("K", "ka", "motdepasse", "identifiant"),
                                       ("K", "ka mel", "motdepasse", "identifiant"), ("K", "alex", "court", "mot_de_passe"),
                                       ("K", "alex", "x" * 300, "mot_de_passe")):
            with self.assertRaises(comptes.Refus) as e:
                comptes.creer_compte(nom, ident, mdp)
            self.assertEqual(e.exception.champ, champ)

    def test_authentification(self):
        comptes.creer_compte("Alex", "alex", "motdepasse", "admin", premier=True)
        self.assertEqual(comptes.authentifier(" ALEX ", "motdepasse")["identifiant"], "alex")
        self.assertIsNone(comptes.authentifier("alex", "mauvais"))
        self.assertIsNone(comptes.authentifier("inconnu", "motdepasse"))
        self.assertIsNotNone(self.sql("SELECT derniere_connexion FROM users")[0][0])

    def test_sessions_multiples_independantes(self):
        a = comptes.creer_compte("Alex", "alex", "motdepasse", "admin", premier=True)
        b = comptes.creer_compte("Léa", "lea", "autremotdepasse")
        tel1, _ = comptes.ouvrir_session(a["id"], 30, "iPhone de Alex")
        tel2, _ = comptes.ouvrir_session(a["id"], 30, "Mac")
        tel3, _ = comptes.ouvrir_session(b["id"], 7, "iPhone de Léa")
        self.assertEqual({comptes.session(j)[0]["identifiant"] for j in (tel1, tel2)}, {"alex"})
        self.assertEqual(comptes.session(tel3)[0]["identifiant"], "lea")
        comptes.fermer_session(tel1)
        self.assertIsNone(comptes.session(tel1)[0])
        self.assertIsNotNone(comptes.session(tel2)[0])
        self.assertIsNotNone(comptes.session(tel3)[0])
        self.assertNotIn(tel2, json.dumps(self.sql("SELECT * FROM sessions")), "le jeton brut n'est jamais stocké")

    def test_expiration_et_prolongation(self):
        a = comptes.creer_compte("Alex", "alex", "motdepasse", "admin", premier=True)
        jeton, duree = comptes.ouvrir_session(a["id"], 30)
        self.assertEqual(duree, 30 * 86400)
        self.assertEqual(comptes.session(jeton)[1], 0, "session neuve : rien à prolonger")
        self.sql("UPDATE sessions SET expire_a = ?", time.time() + 86400)
        compte, prolongee = comptes.session(jeton)
        self.assertEqual(prolongee, 30 * 86400, "utilisée après la moitié de sa durée : prolongée")
        self.assertGreater(self.sql("SELECT expire_a FROM sessions")[0][0], time.time() + 29 * 86400)
        self.sql("UPDATE sessions SET expire_a = ?", time.time() - 1)
        self.assertEqual(comptes.session(jeton), (None, 0))

    def test_compte_desactive_sans_connexion_ni_session(self):
        a = comptes.creer_compte("Alex", "alex", "motdepasse", "admin", premier=True)
        jeton, _ = comptes.ouvrir_session(a["id"], 30)
        self.sql("UPDATE users SET desactive_a = ?", time.time())
        self.assertIsNone(comptes.session(jeton)[0])
        self.assertIsNone(comptes.authentifier("alex", "motdepasse"))

    def test_etat_installation(self):
        self.assertIsNone(comptes.lire_etat("presentation_terminee"))
        comptes.ecrire_etat("presentation_terminee", 1)
        comptes.ecrire_etat("presentation_terminee", 2)
        self.assertEqual(comptes.lire_etat("presentation_terminee"), "2")


class Administration(Base):
    """Rôles, activation, mots de passe (2.6.103)."""
    def setUp(self):
        super().setUp()
        comptes.migrer()
        self.admin = comptes.creer_compte("Alice", "alice", "motdepasse", "admin", premier=True)
        self.camille = comptes.creer_compte("Camille", "camille", "cinema-camille")

    def test_role_change_et_dernier_admin_protege(self):
        with self.assertRaises(comptes.Refus):
            comptes.changer_role(self.admin["id"], "utilisateur")
        with self.assertRaises(comptes.Refus):
            comptes.changer_role(self.camille["id"], "proprietaire")
        self.assertTrue(comptes.changer_role(self.camille["id"], "admin")["admin"])
        self.assertFalse(comptes.changer_role(self.admin["id"], "utilisateur")["admin"], "un autre admin actif existe")
        with self.assertRaises(comptes.Refus):
            comptes.changer_role(self.camille["id"], "utilisateur")

    def test_admin_desactive_ne_compte_pas(self):
        comptes.changer_role(self.camille["id"], "admin")
        comptes.changer_activation(self.camille["id"], False)
        with self.assertRaises(comptes.Refus, msg="Camille admin mais désactivée : Alice reste le seul actif"):
            comptes.changer_role(self.admin["id"], "utilisateur")
        with self.assertRaises(comptes.Refus):
            comptes.changer_activation(self.admin["id"], False)

    def test_desactivation_revoque_les_sessions(self):
        t1, _ = comptes.ouvrir_session(self.camille["id"], 30, "iPhone")
        t2, _ = comptes.ouvrir_session(self.camille["id"], 30, "iPad")
        tm, _ = comptes.ouvrir_session(self.admin["id"], 30)
        self.assertFalse(comptes.changer_activation(self.camille["id"], False)["actif"])
        self.assertEqual((comptes.session(t1)[0], comptes.session(t2)[0]), (None, None))
        self.assertIsNone(comptes.authentifier("camille", "cinema-camille"))
        self.assertIsNotNone(comptes.session(tm)[0], "les autres comptes ne sont pas touchés")
        self.assertTrue(comptes.changer_activation(self.camille["id"], True)["actif"])
        self.assertIsNone(comptes.session(t1)[0], "une réactivation ne ranime pas les anciennes sessions")
        self.assertIsNotNone(comptes.authentifier("camille", "cinema-camille"))

    def test_reinitialisation_par_admin(self):
        t, _ = comptes.ouvrir_session(self.camille["id"], 30)
        comptes.reinitialiser_mot_de_passe(self.camille["id"], "nouveau-mdp-1")
        self.assertIsNone(comptes.session(t)[0])
        self.assertIsNone(comptes.authentifier("camille", "cinema-camille"))
        self.assertIsNotNone(comptes.authentifier("camille", "nouveau-mdp-1"))
        with self.assertRaises(comptes.Refus):
            comptes.reinitialiser_mot_de_passe(self.camille["id"], "court")

    def test_changement_par_le_compte_garde_cet_appareil(self):
        ici, _ = comptes.ouvrir_session(self.camille["id"], 30)
        ailleurs, _ = comptes.ouvrir_session(self.camille["id"], 30)
        with self.assertRaises(comptes.Refus) as e:
            comptes.changer_mot_de_passe(self.camille["id"], "faux", "nouveau-mdp-1", ici)
        self.assertEqual(e.exception.champ, "actuel")
        comptes.changer_mot_de_passe(self.camille["id"], "cinema-camille", "nouveau-mdp-1", ici)
        self.assertIsNotNone(comptes.session(ici)[0])
        self.assertIsNone(comptes.session(ailleurs)[0])
        self.assertIsNotNone(comptes.authentifier("camille", "nouveau-mdp-1"))

    def test_nom_affiche(self):
        self.assertEqual(comptes.modifier_nom(self.camille["id"], "  Camille   M. ")["nom"], "Camille M.")
        with self.assertRaises(comptes.Refus):
            comptes.modifier_nom(self.camille["id"], " ")
        self.assertEqual(comptes.compte(self.camille["id"])["identifiant"], "camille", "l'identifiant ne change pas")

    def test_aucune_empreinte_dans_ce_qui_sort(self):
        tout = json.dumps([comptes.compte(self.admin["id"]), comptes.lister_comptes(), comptes.authentifier("camille", "cinema-camille")])
        self.assertNotIn("pbkdf2", tout)
        self.assertNotIn("mot_de_passe", tout)


class Photos(Base):
    def setUp(self):
        super().setUp()
        comptes.migrer()
        self.admin = comptes.creer_compte("Alice", "alice", "motdepasse", "admin", premier=True)

    def test_jpeg_valide(self):
        self.assertTrue(comptes.jpeg_valide(jpeg()))
        for mauvais in (b"", b"\xff\xd8" + b"\x00" * 1000, b"<svg onload=alert(1)>" + b" " * 600, b"\x89PNG" + b"\x00" * 800,
                        jpeg(5000, 5000), jpeg()[:-2] + b"\x00" * 2_000_001 + b"\xff\xd9"):
            self.assertFalse(comptes.jpeg_valide(mauvais))

    def test_photo_du_compte_remplacee_puis_retiree(self):
        self.assertIsNone(comptes.compte(self.admin["id"])["avatar_v"])
        self.assertIsNone(comptes.chemin_avatar(self.admin["id"]))
        c = comptes.definir_avatar(self.admin["id"], jpeg())
        chemin = os.path.join(self.tmp.name, "avatars", "%d.jpg" % self.admin["id"])
        self.assertEqual(comptes.chemin_avatar(self.admin["id"]), chemin)
        self.assertIsNotNone(c["avatar_v"])
        self.assertEqual(oct(os.stat(chemin).st_mode & 0o777), "0o600")
        comptes.definir_avatar(self.admin["id"], jpeg(256, 256))
        with open(chemin, "rb") as f:
            self.assertEqual(f.read(), jpeg(256, 256), "remplacée sur place")
        self.assertEqual(os.listdir(os.path.join(self.tmp.name, "avatars")), ["%d.jpg" % self.admin["id"]], "aucun fichier temporaire")
        with self.assertRaises(comptes.Refus):
            comptes.definir_avatar(self.admin["id"], b"pas une image" * 100)
        self.assertIsNone(comptes.retirer_avatar(self.admin["id"])["avatar_v"])
        self.assertFalse(os.path.exists(chemin))

    def test_nom_de_fichier_de_la_base_jamais_suivi_hors_forme(self):
        with open(os.path.join(self.tmp.name, "secrets.json"), "w") as f:
            f.write("{}")
        self.sql("UPDATE users SET avatar = '../secrets.json', avatar_maj = 1")
        self.assertIsNone(comptes.chemin_avatar(self.admin["id"]))

    def ancienne(self, contenu=None):
        chemin = os.path.join(self.tmp.name, "app", "profil.jpg")
        os.makedirs(os.path.dirname(chemin), exist_ok=True)
        with open(chemin, "wb") as f:
            f.write(contenu or jpeg(300, 300))
        return chemin

    def test_photo_ancienne_rattachee_au_seul_compte(self):
        chemin = self.ancienne()
        self.assertEqual(comptes.rattacher_photo_ancienne(chemin), self.admin["id"])
        self.assertTrue(os.path.exists(chemin), "l'ancien fichier reste pour un retour arrière")
        with open(comptes.chemin_avatar(self.admin["id"]), "rb") as f:
            self.assertEqual(f.read(), jpeg(300, 300))
        camille = comptes.creer_compte("Camille", "camille", "cinema-camille")
        self.assertIsNone(comptes.rattacher_photo_ancienne(chemin), "une seule fois")
        self.assertIsNone(comptes.compte(camille["id"])["avatar_v"])
        self.assertIsNone(comptes.chemin_avatar(camille["id"]))

    def test_photo_ancienne_ambigue_ignoree(self):
        comptes.creer_compte("Camille", "camille", "cinema-camille")
        self.assertIsNone(comptes.rattacher_photo_ancienne(self.ancienne()))
        self.assertEqual([c["avatar_v"] for c in comptes.lister_comptes()], [None, None])
        self.assertIn("plusieurs", comptes.lire_etat("photo_ancienne"))

    def test_photo_ancienne_ne_remplace_pas_une_photo_choisie(self):
        comptes.definir_avatar(self.admin["id"], jpeg(128, 128))
        comptes.rattacher_photo_ancienne(self.ancienne())
        with open(comptes.chemin_avatar(self.admin["id"]), "rb") as f:
            self.assertEqual(f.read(), jpeg(128, 128))


class Personnel(Base):
    """Permissions, bienvenue et activité par compte (2.6.104)."""
    def setUp(self):
        super().setUp()
        comptes.migrer()
        self.admin = comptes.creer_compte("Alice", "alice", "motdepasse", "admin", premier=True)
        self.camille = comptes.creer_compte("Camille", "camille", "cinema-camille")

    def test_permissions_par_defaut_et_admin_implicite(self):
        self.assertEqual(self.camille["permissions"], {"demander": True, "seances": True, "telechargements": True})
        self.assertTrue(all(self.admin["permissions"].values()))
        l = comptes.changer_permission(self.camille["id"], "seances", False)
        self.assertEqual(l["permissions"]["seances"], False)
        self.assertFalse(comptes.a_permission(l, "seances"))
        self.assertTrue(comptes.a_permission(l, "demander"))
        a = comptes.changer_permission(self.admin["id"], "seances", False)
        self.assertTrue(comptes.a_permission(a, "seances"), "un administrateur a toutes les permissions")
        self.assertFalse(a["permissions_compte"]["seances"])
        with self.assertRaises(comptes.Refus):
            comptes.changer_permission(self.camille["id"], "tout", True)
        jeton, _ = comptes.ouvrir_session(self.camille["id"], 30)
        self.assertFalse(comptes.session(jeton)[0]["permissions"]["seances"], "la session porte les permissions à jour")
        self.assertTrue(comptes.changer_permission(self.camille["id"], "seances", True)["permissions"]["seances"])

    def test_bienvenue_une_fois_par_compte(self):
        self.assertFalse(self.admin["bienvenue"], "le premier administrateur vient de voir la présentation")
        self.assertTrue(self.camille["bienvenue"])
        self.assertFalse(comptes.bienvenue_vue(self.camille["id"])["bienvenue"])
        self.assertFalse(comptes.authentifier("camille", "cinema-camille")["bienvenue"])

    def test_activite_propre_a_chaque_compte(self):
        comptes.noter_activite(self.camille["id"], "demande", "movie", 27205)
        comptes.noter_activite(self.camille["id"], "demande", "movie", 27205)
        comptes.noter_activite(self.camille["id"], "demande", "tv", 1399)
        comptes.noter_activite(self.admin["id"], "seance", "movie", 603)
        comptes.noter_activite(None, "demande", "movie", 1)
        comptes.noter_activite(self.admin["id"], "inconnu", "movie", 1)
        self.assertEqual(comptes.medias_actifs(self.camille["id"], "demande"), ([("tv", 1399), ("movie", 27205)], False))
        self.assertEqual(comptes.medias_actifs(self.admin["id"], "demande"), ([], False))
        self.assertEqual(comptes.compter_activite(self.camille["id"], "demande", distincts=True), 2)
        self.assertEqual(comptes.compter_activite(self.admin["id"], "seance"), 1)
        self.assertEqual(self.sql("SELECT COUNT(*) FROM user_activity"), [(4,)])
        page1, plus = comptes.medias_actifs(self.camille["id"], "demande", 1, 1)
        self.assertEqual((page1, plus), ([("tv", 1399)], True))


class MigrationPersonnel(Base):
    def test_base_2_6_103_migree(self):
        """Base de la 2.6.103 (schéma 2) avec Alice administrateur et Camille utilisatrice déjà connectée."""
        anciennes = list(comptes.MIGRATIONS)
        comptes.MIGRATIONS[:] = anciennes[:2]
        comptes.VERSION_BASE = 2
        try:
            comptes.migrer()
            for ident, role in (("alice", "admin"), ("camille", "utilisateur")):
                self.sql("INSERT INTO users (identifiant, nom, mot_de_passe, role, cree_a, maj_a, derniere_connexion) VALUES (?, ?, ?, ?, 1, 1, 5)",
                         ident, ident.title(), comptes.hacher("motdepasse", iterations=1000), role)
        finally:
            comptes.MIGRATIONS[:] = anciennes
            comptes.VERSION_BASE = anciennes[-1][0]
        self.assertEqual(comptes.migrer(), 4)
        self.assertTrue(any(n.startswith("kamcine-schema2-") for n in os.listdir(os.path.join(self.tmp.name, "backups"))))
        m, l = comptes.authentifier("alice", "motdepasse"), comptes.authentifier("camille", "motdepasse")
        self.assertFalse(m["bienvenue"], "l'administrateur qui utilisait déjà KamCiné ne revoit pas de bienvenue")
        self.assertTrue(l["bienvenue"])
        self.assertTrue(all(l["permissions"].values()), "permissions normales accordées aux comptes existants")


class ServiceReel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.service = None

    def demarrer(self):
        self.service = Service({"KAMCINE_APP": RACINE, "KAMCINE_DATA": self.tmp.name}, RACINE)
        return self.service

    def tearDown(self):
        if self.service:
            self.service.arreter()

    def creer_admin(self, s):
        r = s.http.post(s.url + "/auth/inscription", json={"nom": "Alex", "identifiant": "alex", "mot_de_passe": "popcorn-2026"}, timeout=10)
        self.assertEqual(r.status_code, 200, r.text)
        return r

    def test_etats_session_expiration_et_redemarrage(self):
        s = self.demarrer()
        etat = requests.get(s.url + "/auth/etat", timeout=5).json()
        self.assertEqual((etat["etat"], etat["presentation"], etat["version"]), ("nouvelle", True, "2.7.10"))
        self.assertTrue(requests.post(s.url + "/installation/presentation", timeout=5).json()["ok"])
        self.assertFalse(requests.get(s.url + "/auth/etat", timeout=5).json()["presentation"])
        self.assertEqual(requests.get(s.url + "/reglages", timeout=5).status_code, 401, "aucune donnée avant le premier compte")
        r = self.creer_admin(s)
        cookie = r.headers["set-cookie"].lower()
        for attendu in ("kc_compte=", "httponly", "samesite=lax", "max-age=2592000", "path=/"):
            self.assertIn(attendu, cookie)
        self.assertNotIn("secure", cookie, "pas de Secure en HTTP local")
        self.assertEqual(s.http.get(s.url + "/auth/etat", timeout=5).json()["etat"], "connecte")
        self.assertEqual(s.http.get(s.url + "/reglages", timeout=5).status_code, 200)
        self.assertEqual(requests.get(s.url + "/auth/etat", timeout=5).json()["etat"], "connexion")
        self.assertEqual(len(s.http.get(s.url + "/comptes", timeout=5).json()["comptes"]), 1)
        # Derrière un proxy HTTPS : cookie Secure.
        https = requests.post(s.url + "/auth/connexion", json={"identifiant": "alex", "mot_de_passe": "popcorn-2026"},
                              headers={"X-Forwarded-Proto": "https"}, timeout=10)
        self.assertIn("secure", https.headers["set-cookie"].lower())
        # Trop d'échecs de connexion : attente.
        codes = [requests.post(s.url + "/auth/connexion", json={"identifiant": "alex", "mot_de_passe": "faux"}, timeout=10).status_code
                 for _ in range(6)]
        self.assertEqual(codes[:5], [403] * 5)
        self.assertEqual(codes[5], 429)
        # Redémarrage : la base et la session survivent.
        jeton = s.http.cookies.get("kc_compte")
        s.arreter()
        s = self.demarrer()
        s.http.cookies.set("kc_compte", jeton)
        self.assertEqual(s.http.get(s.url + "/auth/etat", timeout=5).json()["compte"]["identifiant"], "alex")
        # Expiration : la session ne vaut plus rien, la route redevient protégée.
        c = sqlite3.connect(os.path.join(self.tmp.name, "kamcine.db"))
        c.execute("UPDATE sessions SET expire_a = ?", (time.time() - 1,))
        c.commit()
        c.close()
        self.assertEqual(s.http.get(s.url + "/reglages", timeout=5).status_code, 401)
        self.assertEqual(s.http.get(s.url + "/auth/etat", timeout=5).json()["etat"], "connexion")

    def connecter(self, s, identifiant, mdp):
        http = requests.Session()
        r = http.post(s.url + "/auth/connexion", json={"identifiant": identifiant, "mot_de_passe": mdp}, timeout=10)
        self.assertEqual(r.status_code, 200, r.text)
        return http

    def test_multi_utilisateur_complet(self):
        # Photo d'avant les comptes, dans l'emplacement historique des données (présente au démarrage, comme sur le NAS).
        os.makedirs(os.path.join(self.tmp.name, "app"), exist_ok=True)
        with open(os.path.join(self.tmp.name, "app", "profil.jpg"), "wb") as f:
            f.write(jpeg(300, 300))
        s = self.demarrer()
        self.creer_admin(s)
        alice = s.http
        moi = alice.get(s.url + "/auth/etat", timeout=5).json()["compte"]
        self.assertIsNotNone(moi["avatar_v"], "photo historique rattachée au premier administrateur")
        self.assertEqual(alice.get(s.url + "/comptes/%d/avatar" % moi["id"], timeout=5).content, jpeg(300, 300))
        # O. Identifiant dupliqué, saisies invalides.
        u = s.url + "/comptes"
        self.assertEqual(alice.post(u, json={"nom": "X", "identifiant": "ALEX", "mot_de_passe": "motdepasse"}, timeout=10).status_code, 409)
        r = alice.post(u, json={"nom": "X", "identifiant": "x", "mot_de_passe": "motdepasse"}, timeout=10)
        self.assertEqual((r.status_code, r.json()["champ"]), (400, "identifiant"))
        self.assertEqual(alice.post(u, json={"nom": "X", "identifiant": "xavier", "mot_de_passe": "motdepasse", "role": "root"},
                                      timeout=10).status_code, 400)
        # A. L'administrateur crée Camille (Utilisateur par défaut).
        r = alice.post(u, json={"nom": "Camille", "identifiant": "Camille", "mot_de_passe": "cinema-camille"}, timeout=10)
        self.assertEqual(r.status_code, 200, r.text)
        camille_c = r.json()["compte"]
        self.assertEqual((camille_c["identifiant"], camille_c["role"], camille_c["avatar_v"]), ("camille", "utilisateur", None))
        self.assertNotIn("pbkdf2", r.text)
        # B, C, D. Camille se connecte sur son téléphone ; Alice reste connecté ; sessions séparées.
        camille = self.connecter(s, "camille", "cinema-camille")
        self.assertNotIn("kc_compte", r.text)
        self.assertEqual(camille.get(s.url + "/auth/etat", timeout=5).json()["compte"]["identifiant"], "camille")
        self.assertEqual(alice.get(s.url + "/auth/etat", timeout=5).json()["compte"]["identifiant"], "alex")
        self.assertNotEqual(camille.cookies.get("kc_compte"), alice.cookies.get("kc_compte"))
        etat = camille.get(s.url + "/auth/etat", timeout=5).text
        self.assertNotIn("pbkdf2", etat)
        self.assertNotIn(camille.cookies.get("kc_compte"), etat, "le jeton ne sort jamais dans une réponse")
        # E, F, G. Camille : son nom, avatar neutre ; la photo de Alice ne lui est jamais servie.
        self.assertIsNone(camille.get(s.url + "/auth/etat", timeout=5).json()["compte"]["avatar_v"])
        self.assertEqual(camille.get(s.url + "/comptes/%d/avatar" % camille_c["id"], timeout=5).status_code, 404)
        self.assertEqual(camille.get(s.url + "/comptes/%d/avatar" % moi["id"], timeout=5).status_code, 404)
        self.assertEqual(camille.get(s.url + "/profil.jpg", timeout=5).status_code, 404, "l'ancienne photo globale n'est plus servie")
        # H, I. Camille change sa photo et son nom ; rien ne change pour Alice.
        self.assertEqual(camille.post(s.url + "/moi/avatar", data=b"<html>" * 200, timeout=5).status_code, 400)
        self.assertEqual(camille.post(s.url + "/moi/avatar", data=b"\xff" * 2_100_000, timeout=10).status_code, 413)
        c = camille.post(s.url + "/moi/avatar", data=jpeg(512, 512, b"L"), timeout=5).json()["compte"]
        self.assertIsNotNone(c["avatar_v"])
        self.assertEqual(camille.get(s.url + "/comptes/%d/avatar" % camille_c["id"], timeout=5).content, jpeg(512, 512, b"L"))
        self.assertEqual(camille.post(s.url + "/moi/profil", json={"nom": "Camille D."}, timeout=5).json()["compte"]["nom"], "Camille D.")
        self.assertEqual(alice.get(s.url + "/auth/etat", timeout=5).json()["compte"]["nom"], "Alex")
        self.assertEqual(alice.get(s.url + "/comptes/%d/avatar" % moi["id"], timeout=5).content, jpeg(300, 300))
        self.assertEqual(alice.get(s.url + "/comptes/%d/avatar" % camille_c["id"], timeout=5).status_code, 200, "visible par un admin")
        # K. Routes d'administration : 403 pour Camille, quel que soit le chemin ou la méthode.
        for methode, chemin, corps in (("GET", "/comptes", None), ("POST", "/comptes", {"nom": "Y", "identifiant": "yann", "mot_de_passe": "motdepasse"}),
                                       ("POST", "/comptes/%d/role" % camille_c["id"], {"role": "admin"}),
                                       ("POST", "/comptes/%d/activation" % moi["id"], {"actif": False}),
                                       ("POST", "/comptes/%d/mot-de-passe" % moi["id"], {"mot_de_passe": "piratage-123"}),
                                       ("POST", "/reglages", {"recul": 9}), ("POST", "/reglages/reset", None),
                                       ("GET", "/reglages/raccourci", None), ("HEAD", "/reglages/raccourci", None),
                                       ("POST", "/tmdb/cle", {"cle": "x" * 32}), ("POST", "/overseerr/retirer", None),
                                       ("POST", "/arr/config", {}), ("POST", "/transmission/config", {}), ("POST", "/trakt/oublier", None),
                                       ("POST", "/services/tester", None), ("POST", "/log/clear", None), ("GET", "/catalogue/diagnostic", None),
                                       ("POST", "/comptes/", None), ("GET", "/comptes/../reglages/raccourci", None)):
            r = camille.request(methode, s.url + chemin, json=corps, timeout=5)
            self.assertIn(r.status_code, (403, 404, 405), (methode, chemin, r.status_code))
            self.assertNotEqual(r.status_code, 200, (methode, chemin))
            if methode in ("GET", "POST") and ".." not in chemin:
                self.assertEqual(r.status_code, 403, (methode, chemin))
        self.assertEqual(alice.get(s.url + "/reglages", timeout=5).json()["recul"],
                         camille.get(s.url + "/reglages", timeout=5).json()["recul"], "lecture des réglages ouverte, aucune écriture")
        self.assertNotEqual(camille.get(s.url + "/reglages", timeout=5).json()["recul"], 9)
        self.assertEqual(len(alice.get(s.url + "/comptes", timeout=5).json()["comptes"]), 2)
        self.assertEqual(camille.post(s.url + "/moi/mot-de-passe", json={"actuel": "faux", "nouveau": "nouveau-mdp"}, timeout=10).status_code, 403)
        # J. L'administrateur change le rôle de Camille : ses droits suivent tout de suite, sans reconnexion.
        self.assertTrue(alice.post(s.url + "/comptes/%d/role" % camille_c["id"], json={"role": "admin"}, timeout=5).json()["compte"]["admin"])
        self.assertEqual(camille.get(s.url + "/comptes", timeout=5).status_code, 200)
        self.assertTrue(alice.post(s.url + "/comptes/%d/role" % camille_c["id"], json={"role": "utilisateur"}, timeout=5).json()["ok"])
        self.assertEqual(camille.get(s.url + "/comptes", timeout=5).status_code, 403)
        # N. Dernier administrateur actif protégé.
        r = alice.post(s.url + "/comptes/%d/role" % moi["id"], json={"role": "utilisateur"}, timeout=5)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(alice.post(s.url + "/comptes/%d/activation" % moi["id"], json={"actif": False}, timeout=5).status_code, 409)
        self.assertEqual(alice.get(s.url + "/comptes", timeout=5).status_code, 200)
        # P. Redémarrage : comptes, photos et sessions conservés.
        s.arreter()
        s = self.demarrer()
        # Les cookies sont liés à l'hôte, pas au port : les deux téléphones gardent leur session.
        self.assertEqual(camille.get(s.url + "/auth/etat", timeout=5).json()["compte"]["nom"], "Camille D.")
        self.assertEqual(camille.get(s.url + "/comptes/%d/avatar" % camille_c["id"], timeout=5).content, jpeg(512, 512, b"L"))
        self.assertEqual(alice.get(s.url + "/comptes/%d/avatar" % moi["id"], timeout=5).content, jpeg(300, 300))
        # Réinitialisation par l'administrateur : Camille est déconnectée, son nouveau mot de passe marche.
        self.assertTrue(alice.post(s.url + "/comptes/%d/mot-de-passe" % camille_c["id"], json={"mot_de_passe": "reinit-camille"}, timeout=10).json()["ok"])
        self.assertEqual(camille.get(s.url + "/reglages", timeout=5).status_code, 401)
        camille = self.connecter(s, "camille", "reinit-camille")
        telephone2 = self.connecter(s, "camille", "reinit-camille")
        # Changement de son propre mot de passe : cet appareil reste connecté, l'autre non.
        self.assertTrue(camille.post(s.url + "/moi/mot-de-passe", json={"actuel": "reinit-camille", "nouveau": "a-moi-seule"}, timeout=10).json()["ok"])
        self.assertEqual(camille.get(s.url + "/reglages", timeout=5).status_code, 200)
        self.assertEqual(telephone2.get(s.url + "/reglages", timeout=5).status_code, 401)
        # M. Désactivation : sessions invalidées, connexion refusée ; réactivation : connexion de nouveau possible.
        self.assertFalse(alice.post(s.url + "/comptes/%d/activation" % camille_c["id"], json={"actif": False}, timeout=5).json()["compte"]["actif"])
        r = camille.get(s.url + "/catalogue/recents", timeout=5)
        self.assertEqual((r.status_code, r.json().get("connexion")), (401, True))
        self.assertEqual(requests.post(s.url + "/auth/connexion", json={"identifiant": "camille", "mot_de_passe": "a-moi-seule"}, timeout=10).status_code, 403)
        self.assertEqual(alice.get(s.url + "/auth/etat", timeout=5).json()["etat"], "connecte")
        self.assertTrue(alice.post(s.url + "/comptes/%d/activation" % camille_c["id"], json={"actif": True}, timeout=5).json()["compte"]["actif"])
        self.connecter(s, "camille", "a-moi-seule")
        # L'onboarding reste lié à l'installation : un nouveau téléphone arrive à la connexion.
        self.assertEqual(requests.get(s.url + "/auth/etat", timeout=5).json()["etat"], "connexion")
        with open(os.path.join(self.tmp.name, "app", "profil.jpg"), "rb") as f:
            self.assertEqual(f.read(), jpeg(300, 300), "ancienne photo intacte pour un retour arrière")

    def test_personnel_permissions_et_appareils(self):
        # Favoris communs d'avant les comptes : ce sont ceux du premier administrateur.
        with open(os.path.join(self.tmp.name, "favoris.json"), "w") as f:
            json.dump([{"type": "movie", "id": 603, "titre": "Matrix", "t": 1}, {"type": "tv", "id": 1399, "titre": "GoT", "t": 2}], f)
        s = self.demarrer()
        self.creer_admin(s)
        alice = s.http
        self.assertEqual(alice.post(s.url + "/comptes", json={"nom": "Camille", "identifiant": "camille", "mot_de_passe": "cinema-camille"},
                                      timeout=10).status_code, 200)
        camille = self.connecter(s, "camille", "cinema-camille")
        moi_l = camille.get(s.url + "/auth/etat", timeout=5).json()["compte"]
        # H, E, F, G. Favoris.
        fav = lambda http: http.get(s.url + "/profil/resume", timeout=15).json()["favoris"]
        self.assertEqual([x["id"] for x in fav(alice)["films"] + fav(alice)["series"]], [603, 1399])
        self.assertEqual(fav(camille), {"films": [], "series": []})
        self.assertTrue(camille.post(s.url + "/favoris/basculer", json={"type": "movie", "id": 27205, "titre": "Inception"}, timeout=5).json()["favori"])
        self.assertEqual([x["id"] for x in fav(camille)["films"]], [27205])
        self.assertEqual([x["id"] for x in fav(alice)["films"]], [603], "le favori de Camille n'arrive pas chez Alice")
        # I, J, K. Activité : les demandes et séances de Camille sont à elle ; la salle reste commune.
        os.environ["KAMCINE_DATA"] = self.tmp.name
        self.addCleanup(os.environ.pop, "KAMCINE_DATA", None)
        comptes.noter_activite(moi_l["id"], "demande", "movie", 27205)
        rl, rm = camille.get(s.url + "/profil/resume", timeout=15).json(), alice.get(s.url + "/profil/resume", timeout=15).json()
        self.assertEqual((rl["personnel"]["demandes"], rm["personnel"]["demandes"]), (1, 0))
        self.assertEqual(rl["stats"], rm["stats"], "statistiques de la salle : communes, identiques pour tous")
        # O, P. Bienvenue une seule fois.
        self.assertTrue(moi_l["bienvenue"])
        self.assertFalse(camille.post(s.url + "/moi/bienvenue", timeout=5).json()["compte"]["bienvenue"])
        self.assertFalse(self.connecter(s, "camille", "cinema-camille").get(s.url + "/auth/etat", timeout=5).json()["compte"]["bienvenue"])
        self.assertFalse(alice.get(s.url + "/auth/etat", timeout=5).json()["compte"]["bienvenue"])
        # A, C. Appareils : page d'administration.
        for methode, chemin in (("GET", "/appareils/etat"), ("POST", "/appareils/lumieres?action=on"), ("POST", "/appareils/appletv?action=off"),
                                ("GET", "/appareils/denon"), ("GET", "/appareils/synology"), ("GET", "/appletv/rattachement")):
            self.assertEqual(camille.request(methode, s.url + chemin, timeout=10).status_code, 403, chemin)
        self.assertNotEqual(alice.get(s.url + "/appareils/denon", timeout=10).status_code, 403)
        # D, Q, R, S. Permissions : autorisées par défaut (la route répond, jamais 403), puis retirées une à une.
        essais = {"demander": ("POST", "/overseerr/demander?type=movie&id=0"), "seances": ("POST", "/planning/annuler"),
                  "telechargements": ("GET", "/telechargements")}
        for p, (methode, chemin) in essais.items():
            self.assertNotEqual(camille.request(methode, s.url + chemin, json={}, timeout=15).status_code, 403, p)
        self.assertNotEqual(camille.get(s.url + "/status", timeout=10).status_code, 403, "l'état de la séance reste lisible")
        for p, (methode, chemin) in essais.items():
            r = alice.post(s.url + "/comptes/%d/permission" % moi_l["id"], json={"permission": p, "accordee": False}, timeout=5)
            self.assertFalse(r.json()["compte"]["permissions"][p])
            r = camille.request(methode, s.url + chemin, json={}, timeout=15)
            self.assertEqual((r.status_code, r.json().get("permission")), (403, p))
        for chemin in ("/start", "/stop", "/telecommande?cmd=play", "/lancer?type=movie&id=1", "/attente/ajouter"):
            self.assertEqual(camille.post(s.url + chemin, json={}, timeout=10).status_code, 403, chemin)
        self.assertEqual(camille.post(s.url + "/catalogue/episodes/demander", json={}, timeout=10).status_code, 403,
                         "une demande ciblée d'épisodes utilise la permission demander")
        # T. L'administrateur garde tous les droits, même si ses permissions de compte sont retirées.
        alice.post(s.url + "/comptes/1/permission", json={"permission": "telechargements", "accordee": False}, timeout=5)
        self.assertNotEqual(alice.get(s.url + "/telechargements", timeout=15).status_code, 403)
        self.assertEqual(camille.post(s.url + "/comptes/%d/permission" % moi_l["id"], json={"permission": "seances", "accordee": True},
                                     timeout=5).status_code, 403, "un utilisateur ne se donne pas de permission")
        # U. Sessions simultanées inchangées.
        self.assertEqual(alice.get(s.url + "/auth/etat", timeout=5).json()["etat"], "connecte")
        self.assertEqual(camille.get(s.url + "/auth/etat", timeout=5).json()["etat"], "connecte")

    def test_inscriptions_et_installation(self):
        s = self.demarrer()
        # A, B. Installation neuve : le premier compte est Administrateur, même sans rôle demandé.
        self.assertEqual(requests.get(s.url + "/auth/etat", timeout=5).json()["etat"], "nouvelle")
        r = s.http.post(s.url + "/auth/inscription", json={"nom": "Alice", "identifiant": "alice", "mot_de_passe": "popcorn-2026",
                                                          "role": "utilisateur"}, timeout=10)
        self.assertTrue(r.json()["compte"]["admin"])
        alice = s.http
        # C, D. Installation existante : inscriptions fermées par défaut, refus propre, jamais un second premier administrateur.
        etat = requests.get(s.url + "/auth/etat", timeout=5).json()
        self.assertEqual((etat["etat"], etat["inscriptions"]), ("connexion", False))
        corps = {"nom": "Paul", "identifiant": "paul", "mot_de_passe": "cinema-paul", "role": "admin"}
        r = requests.post(s.url + "/auth/inscription", json=corps, timeout=10)
        self.assertEqual((r.status_code, r.json().get("fermees")), (403, True))
        self.assertEqual(len(alice.get(s.url + "/comptes", timeout=5).json()["comptes"]), 1)
        # E, F. Inscriptions ouvertes par l'administrateur : compte Utilisateur, même avec role=admin dans la requête.
        self.assertFalse(alice.get(s.url + "/comptes", timeout=5).json()["inscriptions"])
        self.assertTrue(alice.post(s.url + "/comptes/inscriptions", json={"ouvertes": True}, timeout=5).json()["inscriptions"])
        self.assertTrue(requests.get(s.url + "/auth/etat", timeout=5).json()["inscriptions"])
        paul = requests.Session()
        r = paul.post(s.url + "/auth/inscription", json=dict(corps, admin=True, premier=True), timeout=10)
        self.assertEqual(r.status_code, 200, r.text)
        c = r.json()["compte"]
        self.assertEqual((c["role"], c["admin"], c["bienvenue"]), ("utilisateur", False, True))
        self.assertEqual(paul.get(s.url + "/auth/etat", timeout=5).json()["compte"]["identifiant"], "paul", "session ouverte")
        self.assertEqual(paul.get(s.url + "/comptes", timeout=5).status_code, 403)
        self.assertEqual(paul.post(s.url + "/comptes/inscriptions", json={"ouvertes": False}, timeout=5).status_code, 403)
        r = requests.post(s.url + "/auth/inscription", json=dict(corps, nom="Autre"), timeout=10)
        self.assertEqual((r.status_code, r.json()["champ"]), (409, "identifiant"))
        self.assertTrue(alice.post(s.url + "/comptes/inscriptions", json={"ouvertes": False}, timeout=5).json() == {"ok": True, "inscriptions": False})
        self.assertEqual(requests.post(s.url + "/auth/inscription", json=dict(corps, identifiant="loan"), timeout=10).status_code, 403)
        # J, K, L, M, N. État réel de l'installation : rien de configuré ici.
        e = paul.get(s.url + "/installation/etat", timeout=10).json()
        self.assertEqual(e["pret"], {"seances": False, "catalogue": False, "telechargements": False, "telechargements_actifs": False, "appletvs": [], "appletv_pref": ""})
        self.assertNotIn("services", e, "le détail est réservé à l'administrateur")
        d = alice.get(s.url + "/installation/etat", timeout=10).json()["services"]
        self.assertEqual({k: v["configuree"] for k, v in d.items()},
                         {"appletv": False, "tmdb": False, "overseerr": False, "transmission": False, "hue": False,
                          "denon": False,      # 2.6.106 : plus aucune adresse d'ampli par défaut
                          "radarr_hd": False, "radarr_uhd": False, "sonarr_hd": False, "sonarr_uhd": False})
        # P, Q, R. Une seule source de vérité : la configuration réelle (mêmes routes que Réglages), relue à chaque fois.
        with open(os.path.join(self.tmp.name, "secrets.json"), "w") as f:
            json.dump({"tmdb": "x" * 32, "hue": {"adresse": "192.168.1.20", "cle": "cle-du-pont"}}, f)
        alice.post(s.url + "/reglages", json={"denon_actif": True, "denon_ip": "192.168.1.50"}, timeout=5)
        e = alice.get(s.url + "/installation/etat", timeout=10).json()
        self.assertTrue(e["pret"]["catalogue"])
        self.assertEqual((e["services"]["hue"]["configuree"], e["services"]["denon"]["configuree"]), (True, True))
        self.assertNotIn("cle-du-pont", json.dumps(e))
        self.assertNotIn("x" * 32, json.dumps(e))
        self.assertTrue(alice.post(s.url + "/tmdb/retirer", timeout=5).json().get("ok", True))
        alice.post(s.url + "/reglages", json={"denon_actif": False}, timeout=5)
        e = alice.get(s.url + "/installation/etat", timeout=10).json()
        self.assertEqual((e["pret"]["catalogue"], e["services"]["denon"]["configuree"]), (False, False))

    def test_base_plus_recente_refusee_par_le_service(self):
        os.environ["KAMCINE_DATA"] = self.tmp.name
        self.addCleanup(os.environ.pop, "KAMCINE_DATA", None)
        comptes.migrer()
        c = sqlite3.connect(comptes.chemin_base())
        c.execute("PRAGMA user_version = 99")
        c.close()
        s = self.demarrer()
        etat = requests.get(s.url + "/auth/etat", timeout=5).json()
        self.assertEqual(etat["etat"], "erreur")
        self.assertIn("plus récente", etat["message"])
        r = requests.get(s.url + "/reglages", timeout=5)
        self.assertEqual(r.status_code, 503)
        self.assertTrue(r.json()["base"])
        self.assertEqual(requests.post(s.url + "/auth/connexion", json={"identifiant": "a", "mot_de_passe": "b"}, timeout=5).status_code, 503)
        self.assertEqual(requests.get(s.url + "/", timeout=5).status_code, 200, "la page s'affiche pour expliquer le refus")

    def test_ancien_code_une_derniere_fois(self):
        import hashlib, secrets as sec
        sel = sec.token_hex(16)
        with open(os.path.join(self.tmp.name, "auth.json"), "w") as f:
            json.dump({"sel": sel, "hash": hashlib.pbkdf2_hmac("sha256", b"4321", bytes.fromhex(sel), 200000).hex(), "longueur": 4}, f)
        s = self.demarrer()
        etat = requests.get(s.url + "/auth/etat", timeout=5).json()
        self.assertEqual((etat["etat"], etat["longueur"]), ("migration", 4))
        corps = {"nom": "Alex", "identifiant": "alex", "mot_de_passe": "popcorn-2026"}
        self.assertEqual(s.http.post(s.url + "/auth/inscription", json=corps, timeout=10).status_code, 403)
        self.assertEqual(s.http.post(s.url + "/auth/inscription", json=dict(corps, jeton_migration="invente"), timeout=10).status_code, 403)
        self.assertEqual(s.http.post(s.url + "/auth/ancien-code", json={"code": "0000"}, timeout=10).status_code, 403)
        jeton = s.http.post(s.url + "/auth/ancien-code", json={"code": "4321"}, timeout=10).json()["jeton"]
        self.assertEqual(s.http.post(s.url + "/auth/inscription", json=dict(corps, jeton_migration=jeton), timeout=10).status_code, 200)
        self.assertEqual(s.http.post(s.url + "/auth/ancien-code", json={"code": "4321"}, timeout=10).status_code, 409,
                         "après la création, l'ancien code ne sert plus à rien")
        self.assertTrue(os.path.exists(os.path.join(self.tmp.name, "auth.json")), "conservé pour un retour arrière")
        self.assertEqual(requests.get(s.url + "/auth/etat", timeout=5).json()["etat"], "connexion")


if __name__ == "__main__":
    unittest.main()
