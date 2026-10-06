"""Portabilité de la configuration (2.6.106) : une installation vierge n'hérite d'aucune valeur personnelle, et une
installation historique (creds.env, identifiant d'Apple TV autrefois dans le code) est reprise sans rien reconfigurer.

Unitaires sur integrations.py, config.py et bibliotheque.py, puis deux vrais services (uvicorn, Apple TV neutralisée) :
un dossier de données vide, et un dossier au format d'avant la 2.6.106. Toutes les valeurs sont FAUSSES.
Il faut le Python du service : python -m unittest outils.test_portabilite"""
import json
import re
import os
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

import requests

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RACINE)
sys.path.insert(0, os.path.join(RACINE, "outils"))
import integrations
from test_chemins import Service

FAUX = {"COMP": "faux-companion-0123456789abcdef", "AIR": "faux-airplay-fedcba9876543210",
        "HUE_IP": "10.9.8.7", "HUE_KEY": "fausse-cle-hue-abcdef", "SYNOLOGY_URL": "https://nas.exemple.test:5001",
        "SYNOLOGY_USER": "lecteur", "SYNOLOGY_PASSWORD": "faux-mot-de-passe-nas"}
SANS_ENV = {k: "" for k in ("COMP", "AIR", "ATV_ID", "HUE_IP", "HUE_KEY", "SYNOLOGY_URL", "SYNOLOGY_USER", "SYNOLOGY_PASSWORD")}


def ecrire_creds(dossier, valeurs):
    with open(os.path.join(dossier, "creds.env"), "w") as f:
        f.write("# ancien fichier\n" + "".join("%s=%s\n" % kv for kv in valeurs.items()))


class Dossier(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, dict(SANS_ENV, KAMCINE_DATA=self.tmp.name))
        env.start()
        self.addCleanup(env.stop)

    def secrets(self):
        with open(os.path.join(self.tmp.name, "secrets.json")) as f:
            return json.load(f)


class InstallationVierge(Dossier):
    def test_rien_de_configure_et_rien_ecrit(self):
        self.assertIsNone(integrations.appletv())
        self.assertIsNone(integrations.hue())
        self.assertEqual(integrations.synology()["url"], "")
        self.assertEqual(integrations.importer_ancien(), [])
        self.assertEqual(os.listdir(self.tmp.name), [], "une installation vierge n'écrit aucun fichier de configuration")
        env = integrations.env_seance({"COMP": "venu-du-conteneur"})
        self.assertNotIn("ATV_ID", env)

    def test_aucune_valeur_personnelle_par_defaut(self):
        import config, bibliotheque, denon
        textes = json.dumps(config.DEFAUTS, ensure_ascii=False)
        self.assertNotIn("192.168.", textes, "aucune adresse locale par défaut")
        try:
            import marqueurs_prives            # fichier privé du dépôt de développement, non publié
            for m in marqueurs_prives.MARQUEURS:
                self.assertIsNone(re.search(m, textes), m)
        except ImportError:
            pass
        self.assertEqual((config.DEFAUTS["denon_ip"], config.DEFAUTS["denon_actif"]), ("", False))
        self.assertEqual(denon.ip(), "")
        self.assertFalse(denon.actif(), "Denon non configuré sans réglage")
        self.assertFalse(hasattr(bibliotheque, "URL_DEFAUT"), "2.6.108 : aucune adresse supposée pour Radarr et Sonarr")
        self.assertEqual({a["url"] for a in bibliotheque.etat_config()}, {""})

    def test_configuration_synology_persistante_sans_ecraser_les_autres_secrets(self):
        integrations.ecrire({"tmdb": "cle-existante"})
        integrations.enregistrer_synology("https://nas.local:5001", "kamcine", "secret", "Mon NAS", True)
        s = self.secrets()
        self.assertEqual(s["tmdb"], "cle-existante")
        self.assertEqual(s["synology"], {"url": "https://nas.local:5001", "utilisateur": "kamcine",
            "mot_de_passe": "secret", "nom": "Mon NAS", "verifier_tls": True})
        integrations.enregistrer_synology("https://nas.local:5001", "kamcine", "", "Mon NAS modifié", True)
        self.assertEqual(integrations.synology()["mot_de_passe"], "secret", "mot de passe vide = conserver la valeur actuelle")
        self.assertEqual(integrations.synology()["nom"], "Mon NAS modifié")
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.tmp.name, "secrets.json")).st_mode), 0o600)


class MigrationAncienne(Dossier):
    def test_creds_env_importe_une_seule_fois(self):
        with open(os.path.join(self.tmp.name, "secrets.json"), "w") as f:
            json.dump({"tmdb": "fausse-cle-tmdb"}, f)
        ecrire_creds(self.tmp.name, FAUX)
        avant = open(os.path.join(self.tmp.name, "creds.env")).read()
        self.assertEqual(integrations.importer_ancien(), ["appletv", "hue", "synology"])
        s = self.secrets()
        self.assertEqual(s["tmdb"], "fausse-cle-tmdb", "les autres clés sont conservées")
        # 2.6.107 : plus d'identifiant de repli dans le code ; les identifiants d'appairage sont gardés, l'Apple TV reste à choisir.
        self.assertEqual(s["appletv"], {"identifiant": "", "companion": FAUX["COMP"], "airplay": FAUX["AIR"]})
        self.assertIsNone(integrations.appletv())
        self.assertTrue(integrations.etat_public()["appletv"]["a_choisir"])
        self.assertFalse(hasattr(integrations, "ANCIEN_ATV_ID"))
        self.assertEqual(integrations.hue(), {"adresse": "10.9.8.7", "cle": "fausse-cle-hue-abcdef"})
        self.assertEqual(integrations.synology()["mot_de_passe"], "faux-mot-de-passe-nas")
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.tmp.name, "secrets.json")).st_mode), 0o600)
        self.assertEqual(open(os.path.join(self.tmp.name, "creds.env")).read(), avant, "creds.env intact (retour arrière)")
        self.assertEqual([n for n in os.listdir(self.tmp.name) if n.endswith(".tmp")], [], "écriture atomique")
        # Relance : rien n'est réécrit, même après une modification dans le nouveau stockage.
        s["hue"]["adresse"] = "10.1.1.1"
        del s["synology"]                                   # retrait volontaire
        integrations.ecrire(s)
        self.assertEqual(integrations.importer_ancien(), [])
        self.assertEqual(integrations.hue()["adresse"], "10.1.1.1", "la nouvelle valeur n'est pas écrasée")
        self.assertNotIn("synology", self.secrets(), "une intégration retirée n'est pas réimportée")

    def test_identifiant_donne_et_environnement_du_conteneur(self):
        environ = {"COMP": "env-companion", "AIR": "env-airplay", "ATV_ID": "AA:BB:CC:DD:EE:FF"}
        self.assertEqual(integrations.importer_ancien(environ), ["appletv"])
        self.assertEqual(integrations.appletv(), {"identifiant": "AA:BB:CC:DD:EE:FF", "companion": "env-companion", "airplay": "env-airplay"})
        env = integrations.env_seance({"COMP": "ancien-du-conteneur", "SEANCE_MODE": "test"})
        self.assertEqual((env["ATV_ID"], env["COMP"], env["AIR"], env["SEANCE_MODE"]),
                         ("AA:BB:CC:DD:EE:FF", "env-companion", "env-airplay", "test"), "la configuration KamCiné est prioritaire")

    def test_valeurs_existantes_jamais_remplacees(self):
        integrations.ecrire({"appletv": {"identifiant": "11:22:33:44:55:66", "companion": "deja-la", "airplay": ""}})
        ecrire_creds(self.tmp.name, FAUX)
        self.assertEqual(integrations.importer_ancien(), ["hue", "synology"])
        self.assertEqual(integrations.appletv()["companion"], "deja-la")

    def test_journaux_sans_secret(self):
        ecrire_creds(self.tmp.name, FAUX)
        integrations.importer_ancien()
        import importlib, erreurs
        importlib.reload(erreurs)
        texte = erreurs.masquer("échec avec %s et %s puis %s" % (FAUX["COMP"], FAUX["HUE_KEY"], FAUX["SYNOLOGY_PASSWORD"]))
        for v in (FAUX["COMP"], FAUX["HUE_KEY"], FAUX["SYNOLOGY_PASSWORD"]):
            self.assertNotIn(v, texte)


class ServicesReels(unittest.TestCase):
    def demarrer(self, dossier):
        s = Service(dict(SANS_ENV, KAMCINE_APP=RACINE, KAMCINE_DATA=dossier), RACINE)
        self.addCleanup(s.arreter)
        return s

    def admin(self, s):
        r = s.http.post(s.url + "/auth/inscription", json={"nom": "Admin", "identifiant": "admin", "mot_de_passe": "motdepasse-test"}, timeout=10)
        self.assertEqual(r.status_code, 200, r.text)

    def test_installation_vierge(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        s = self.demarrer(tmp.name)
        self.assertEqual(requests.get(s.url + "/auth/etat", timeout=5).json()["etat"], "nouvelle", "aucun compte")
        self.admin(s)
        e = s.http.get(s.url + "/installation/etat", timeout=10).json()
        self.assertEqual({k: v["configuree"] for k, v in e["services"].items()}, {k: False for k in e["services"]},
                         "aucune intégration configurée")
        self.assertEqual(e["pret"], {"seances": False, "catalogue": False, "telechargements": False, "telechargements_actifs": False, "appletvs": [], "appletv_pref": ""})
        r = s.http.get(s.url + "/reglages", timeout=5).json()
        self.assertEqual((r["denon_ip"], r["denon_actif"], r["nom"]), ("", False, "KamCiné"))
        d = s.http.post(s.url + "/start", timeout=10).json()
        self.assertFalse(d["ok"])
        self.assertIn("pas encore configurée", d["message"])
        hue = s.http.get(s.url + "/appareils/etat", timeout=30).json()["hue"]
        self.assertEqual((hue["ok"], hue["message"]), (False, "Pont Hue non configuré."))
        self.assertFalse(os.path.exists(os.path.join(tmp.name, "erreurs.log")), "une installation vierge ne journalise aucune erreur d'appareil")
        self.assertFalse(os.path.exists(os.path.join(tmp.name, "secrets.json")))
        self.assertFalse(os.path.exists(os.path.join(tmp.name, "creds.env")))

    def test_installation_historique_reprise(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ecrire_creds(tmp.name, FAUX)
        with open(os.path.join(tmp.name, "secrets.json"), "w") as f:
            json.dump({"tmdb": "fausse-cle-tmdb-0123456789", "arr": {"radarr_hd": {"url": "http://10.0.0.2:1414", "cle": "fausse-cle-radarr-123"}},
                       "transmission": {"url": "http://10.0.0.2:9091/transmission/rpc", "user": "t", "pass": "faux-pass-transmission"}}, f)
        with open(os.path.join(tmp.name, "reglages.json"), "w") as f:
            json.dump({"denon_ip": "10.0.0.85", "denon_actif": True, "transmission_actif": True}, f)
        s = self.demarrer(tmp.name)
        self.admin(s)
        e = s.http.get(s.url + "/installation/etat", timeout=10).json()
        attendu = {"appletv": False, "tmdb": True, "hue": True, "denon": True, "radarr_hd": True, "transmission": True,
                   "overseerr": False, "radarr_uhd": False, "sonarr_hd": False, "sonarr_uhd": False}
        self.assertEqual({k: v["configuree"] for k, v in e["services"].items()}, attendu)
        texte = json.dumps(e) + s.http.get(s.url + "/arr/etat", timeout=5).text + s.http.get(s.url + "/transmission/etat", timeout=5).text
        for secret in (FAUX["COMP"], FAUX["AIR"], FAUX["HUE_KEY"], FAUX["SYNOLOGY_PASSWORD"], "fausse-cle-radarr-123",
                       "faux-pass-transmission", "fausse-cle-tmdb-0123456789"):
            self.assertNotIn(secret, texte, "aucun secret envoyé au navigateur")
        self.assertEqual(s.http.get(s.url + "/reglages", timeout=5).json()["denon_ip"], "10.0.0.85", "adresse de l'ampli conservée")
        # Modification dans le nouveau stockage, puis redémarrage : la migration ne la réécrase pas.
        with open(os.path.join(tmp.name, "secrets.json")) as f:
            d = json.load(f)
        d["hue"]["adresse"] = "10.2.2.2"
        with open(os.path.join(tmp.name, "secrets.json"), "w") as f:
            json.dump(d, f)
        jeton = s.http.cookies.get("kc_compte")
        s.arreter()
        s = self.demarrer(tmp.name)
        with open(os.path.join(tmp.name, "secrets.json")) as f:
            self.assertEqual(json.load(f)["hue"]["adresse"], "10.2.2.2")
        s.http.cookies.set("kc_compte", jeton)
        self.assertTrue(s.http.get(s.url + "/installation/etat", timeout=10).json()["services"]["appletv"]["a_choisir"],
                        "identifiants repris : l'administrateur choisit son Apple TV dans KamCiné")
        for nom in ("erreurs.log", "seance.log"):
            chemin = os.path.join(tmp.name, nom)
            if os.path.exists(chemin):
                contenu = open(chemin, encoding="utf-8").read()
                for secret in (FAUX["COMP"], FAUX["HUE_KEY"], FAUX["SYNOLOGY_PASSWORD"]):
                    self.assertNotIn(secret, contenu, nom)


if __name__ == "__main__":
    unittest.main()
