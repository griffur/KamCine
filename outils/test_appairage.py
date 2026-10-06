"""Configuration de l'Apple TV et du pont Hue depuis KamCiné (2.6.107), sans aucun matériel.

Apple TV : faux pyatv (outils/faux_pyatv.py), découverte, appairage Companion puis AirPlay avec leurs codes, mauvais code,
session expirée, remplacement d'une Apple TV existante, choix d'une Apple TV pour des identifiants repris, test.
Hue : faux pont en HTTP local (HUE_SCHEME=http), recherche, bouton non pressé puis pressé, clé, lumières, rôles, et
lumieres.py piloté par rôles, avec ou sans pont. Puis un vrai service : routes réservées, rien de secret en réponse.
Il faut le Python du service : python -m unittest outils.test_appairage"""
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import requests

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RACINE)
sys.path.insert(0, os.path.join(RACINE, "outils"))
import faux_pyatv
from test_chemins import Service


class FauxPont:
    """Pont Hue minimal : POST /api (bouton), GET /clip/v2/resource/light, PUT /clip/v2/resource/light/<id>."""
    def __init__(self):
        etat = self.etat = {"bouton_apres": 1, "demandes": 0, "cle": "fausse-cle-pont-0123456789", "puts": [],
                            "lumieres": [{"id": "l-1", "metadata": {"name": "Lampe gauche"}}, {"id": "l-2", "metadata": {"name": "Lampe droite"}},
                                         {"id": "l-3", "metadata": {"name": "Enseigne"}}, {"id": "l-4", "metadata": {"name": "Plafonnier"}}]}

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def repondre(self, code, corps):
                data = json.dumps(corps).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == "/decouverte":
                    return self.repondre(200, [{"id": "pont1", "internalipaddress": "127.0.0.1:%d" % serveur.server_port}])
                if self.headers.get("hue-application-key") != etat["cle"]:
                    return self.repondre(403, {"errors": [{"description": "unauthorized"}]})
                self.repondre(200, {"data": etat["lumieres"]})

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                etat["demandes"] += 1
                if etat["demandes"] <= etat["bouton_apres"]:
                    return self.repondre(200, [{"error": {"type": 101, "description": "link button not pressed"}}])
                self.repondre(200, [{"success": {"username": etat["cle"], "clientkey": "x"}}])

            def do_PUT(self):
                etat["puts"].append((self.path, json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))))
                self.repondre(200, {"data": []})

        serveur = self.serveur = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=serveur.serve_forever, daemon=True).start()
        self.adresse = "127.0.0.1:%d" % serveur.server_port

    def arreter(self):
        self.serveur.shutdown()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, {"KAMCINE_DATA": self.tmp.name, "HUE_SCHEME": "http"})
        env.start()
        self.addCleanup(env.stop)

    def secrets(self):
        with open(os.path.join(self.tmp.name, "secrets.json")) as f:
            return json.load(f)


class AppleTV(Base):
    def setUp(self):
        super().setUp()
        self.pyatv_modules = {nom: sys.modules.get(nom) for nom in ("pyatv", "pyatv.const")}
        self.addCleanup(self.restaurer_pyatv)
        faux_pyatv.installer()
        import appairage_atv, integrations
        self.atv, self.integ = appairage_atv, integrations
        faux_pyatv.ETAT.update(appareils=[("AA:BB:CC:DD:EE:01", "Salon"), ("AA:BB:CC:DD:EE:02", "Chambre"),
                                          ("AA:BB:CC:DD:EE:03", "HomePod", "10.0.0.9", False)], connexion=True, appels=[])

    def restaurer_pyatv(self):
        for nom, module in self.pyatv_modules.items():
            if module is None:
                sys.modules.pop(nom, None)
            else:
                sys.modules[nom] = module

    def test_decouverte(self):
        trouvees = self.atv.rechercher()
        self.assertEqual([a["nom"] for a in trouvees], ["Salon", "Chambre"], "seules les Apple TV pilotables (Companion)")
        self.assertEqual(set(trouvees[0]), {"identifiant", "nom", "adresse", "modele", "configuree"})
        faux_pyatv.ETAT["appareils"] = []
        self.assertEqual(self.atv.rechercher(), [])

    def test_appairage_complet_puis_test(self):
        self.atv.rechercher()
        r = self.atv.appairer("AA:BB:CC:DD:EE:01")
        self.assertEqual((r["etape"], r["protocole"], r["numero"], r["total"]), ("pin", "Companion", 1, 2))
        self.assertIsNone(self.integ.appletv(), "rien d'enregistré avant la fin")
        r = self.atv.saisir_pin(r["session"], "1234")
        self.assertEqual((r["etape"], r["protocole"]), ("pin", "AirPlay"))
        r = self.atv.saisir_pin(r["session"], "5678")
        self.assertEqual((r["etape"], r["appareil"]["nom"]), ("fini", "Salon"))
        a = self.integ.appletv()
        self.assertEqual(a, {"identifiant": "AA:BB:CC:DD:EE:01", "companion": "faux-identifiant-companion-AA:BB:CC:DD:EE:01",
                             "airplay": "faux-identifiant-airplay-AA:BB:CC:DD:EE:01"})
        self.assertEqual(self.atv.tester(), {"ok": True, "nom": "Salon"})
        self.assertEqual(faux_pyatv.ETAT["appels"][-1][1], {"Companion": a["companion"], "AirPlay": a["airplay"]})
        self.assertEqual(self.integ.etat_public()["appletv"], {"configuree": True, "a_choisir": False, "nom": "Salon",
                          "appareils": [{"identifiant": "AA:BB:CC:DD:EE:01", "nom": "Salon", "nom_detecte": "Salon", "active": True}]})
        faux_pyatv.ETAT["connexion"] = False
        with self.assertRaises(self.atv.Echec):
            self.atv.tester()
        faux_pyatv.ETAT.update(connexion=True, appareils=[])
        with self.assertRaises(self.atv.Echec) as e:
            self.atv.tester()
        self.assertIn("Apple TV introuvable", str(e.exception))

    def test_mauvais_code_et_remplacement_sans_perte(self):
        self.integ.enregistrer_appletv("AA:BB:CC:DD:EE:09", "ancien-companion", "ancien-airplay", "Ancienne")
        self.atv.rechercher()
        r = self.atv.appairer("AA:BB:CC:DD:EE:02")
        with self.assertRaises(self.atv.Echec) as e:
            self.atv.saisir_pin(r["session"], "0000")
        self.assertIn("Code refusé", str(e.exception))
        self.assertEqual(self.integ.appletv()["companion"], "ancien-companion", "l'ancienne Apple TV reste configurée")
        with self.assertRaises(self.atv.Echec):
            self.atv.saisir_pin(r["session"], "1234")          # session fermée après l'échec
        with self.assertRaises(self.atv.Echec):
            self.atv.saisir_pin("inventee", "1234")
        with self.assertRaises(self.atv.Echec):
            self.atv.appairer("AA:BB:CC:DD:EE:77")              # pas dans la dernière recherche
        r = self.atv.appairer("AA:BB:CC:DD:EE:02")
        with self.assertRaises(self.atv.Echec):
            self.atv.saisir_pin(r["session"], "12ab")
        r = self.atv.appairer("AA:BB:CC:DD:EE:02")
        r = self.atv.saisir_pin(self.atv.saisir_pin(r["session"], "1234")["session"], "5678")
        # 2.6.108 : la nouvelle s'ajoute ; l'ancienne reste active tant que l'administrateur ne choisit pas l'autre.
        self.assertEqual([a["identifiant"] for a in self.integ.appletvs()], ["AA:BB:CC:DD:EE:09", "AA:BB:CC:DD:EE:02"])
        self.assertEqual(self.integ.appletv()["identifiant"], "AA:BB:CC:DD:EE:09")

    def test_deux_codes_le_second_annonce_le_premier_accepte(self):
        self.atv.rechercher()
        r = self.atv.appairer("AA:BB:CC:DD:EE:01")
        self.assertFalse(r["precedent_accepte"])
        r = self.atv.saisir_pin(r["session"], "1234")
        self.assertEqual((r["etape"], r["numero"], r["precedent_accepte"]), ("pin", 2, True), "le premier code accepté n'est pas un échec")

    def test_plusieurs_apple_tv(self):
        self.atv.rechercher()
        for ident in ("AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02"):
            r = self.atv.appairer(ident)
            self.atv.saisir_pin(self.atv.saisir_pin(r["session"], "1234")["session"], "5678")
        self.assertEqual([(a["nom"], a["active"]) for a in self.integ.appletvs()], [("Salon", True), ("Chambre", False)],
                         "la seconde s'ajoute, la première reste utilisée pour les séances")
        self.assertEqual(self.integ.appletv()["identifiant"], "AA:BB:CC:DD:EE:01")
        self.assertEqual(self.integ.appletv("AA:BB:CC:DD:EE:02")["companion"], "faux-identifiant-companion-AA:BB:CC:DD:EE:02",
                         "une commande peut cibler les identifiants de l'appareil choisi sans changer l'Apple TV active")
        env = self.integ.env_seance({}, "AA:BB:CC:DD:EE:02")
        self.assertEqual(env["ATV_ID"], "AA:BB:CC:DD:EE:02")
        self.assertEqual(self.integ.appletv()["identifiant"], "AA:BB:CC:DD:EE:01",
                         "la cible d'une séance n'écrase pas le choix global historique")
        self.assertIsNone(self.integ.appletv("inconnue"), "un appareil non enregistré ne fournit jamais de credentials")
        self.assertTrue(self.atv.rechercher()[1]["configuree"], "déjà appairée")
        self.integ.renommer_appletv("AA:BB:CC:DD:EE:02", "  Chambre   des parents ")
        self.assertTrue(self.integ.activer_appletv("AA:BB:CC:DD:EE:02"))
        self.assertFalse(self.integ.activer_appletv("AA:BB:CC:DD:EE:02"), "déjà active : rien ne change")
        a = self.integ.appletv()
        self.assertEqual((a["identifiant"], a["companion"]), ("AA:BB:CC:DD:EE:02", "faux-identifiant-companion-AA:BB:CC:DD:EE:02"))
        self.assertEqual(self.integ.etat_public()["appletv"]["nom"], "Chambre des parents")
        s = self.secrets()
        self.assertEqual(s["appletv"]["identifiant"], "AA:BB:CC:DD:EE:02", "copie de l'active au format d'avant (retour arrière)")
        self.assertEqual(s["appletv_active"], "AA:BB:CC:DD:EE:02")
        with self.assertRaises(KeyError):
            self.integ.activer_appletv("inconnue")
        self.integ.retirer_appletv("AA:BB:CC:DD:EE:02")
        self.assertEqual([(a["nom"], a["active"]) for a in self.integ.appletvs()], [("Salon", True)], "la suivante devient active")
        # Nouvel appairage d'une Apple TV déjà connue : son nom KamCiné est gardé.
        self.integ.renommer_appletv("AA:BB:CC:DD:EE:01", "Salon KamCiné")
        r = self.atv.appairer("AA:BB:CC:DD:EE:01")
        self.atv.saisir_pin(self.atv.saisir_pin(r["session"], "1234")["session"], "5678")
        self.assertEqual([a["nom"] for a in self.integ.appletvs()], ["Salon KamCiné"])

    def test_ancien_format_mono_apple_tv_lu_tel_quel(self):
        """secrets.json de la 2.6.107 (une seule section appletv) : rien à migrer, tout fonctionne."""
        self.integ.ecrire({"appletv": {"identifiant": "AA:BB:CC:DD:EE:01", "companion": "c1", "airplay": "a1", "nom": "Salon"}})
        self.assertEqual(self.integ.appletv(), {"identifiant": "AA:BB:CC:DD:EE:01", "companion": "c1", "airplay": "a1"})
        self.assertEqual(self.integ.appletvs(), [{"identifiant": "AA:BB:CC:DD:EE:01", "nom": "Salon", "nom_detecte": "Salon", "active": True}])
        self.assertEqual(self.integ.etat_public()["appletv"]["nom"], "Salon")
        self.assertEqual(self.atv.tester(), {"ok": True, "nom": "Salon"})

    def test_identifiants_repris_puis_choix_de_l_apple_tv(self):
        self.integ.importer_ancien({"COMP": "comp-repris", "AIR": "air-repris"})
        self.assertIsNone(self.integ.appletv(), "sans identifiant d'appareil, pas encore configurée")
        self.assertTrue(self.integ.etat_public()["appletv"]["a_choisir"])
        self.atv.rechercher()
        self.assertEqual(self.atv.choisir("AA:BB:CC:DD:EE:01"), {"ok": True, "nom": "Salon"})
        self.assertEqual(self.integ.appletv(), {"identifiant": "AA:BB:CC:DD:EE:01", "companion": "comp-repris", "airplay": "air-repris"})

    def test_annulation(self):
        self.atv.rechercher()
        self.atv.appairer("AA:BB:CC:DD:EE:01")
        self.assertEqual(self.atv.annuler(), {"etape": "annule"})
        self.assertIn(("close", "Companion"), faux_pyatv.ETAT["appels"])
        self.assertIsNone(self.integ.appletv())


class PontHue(Base):
    def setUp(self):
        super().setUp()
        self.pont = FauxPont()
        self.addCleanup(self.pont.arreter)
        import appairage_hue, integrations, importlib
        self.hue = importlib.reload(appairage_hue)
        self.hue.DECOUVERTE = "http://%s/decouverte" % self.pont.adresse
        self.integ = integrations

    def test_recherche_bouton_cle_lumieres_roles(self):
        self.assertEqual(self.hue.rechercher(), [{"adresse": self.pont.adresse, "identifiant": "pont1"}])
        self.assertEqual(self.hue.associer(self.pont.adresse), {"etat": "bouton"}, "bouton pas encore pressé")
        self.assertIsNone(self.integ.hue())
        r = self.hue.associer(self.pont.adresse)
        self.assertEqual(r["etat"], "fini")
        self.assertEqual([l["nom"] for l in r["lumieres"]], ["Enseigne", "Lampe droite", "Lampe gauche", "Plafonnier"])
        self.assertNotIn(self.pont.etat["cle"], json.dumps(r), "la clé du pont ne sort pas")
        self.assertEqual(self.integ.hue(), {"adresse": self.pont.adresse, "cle": self.pont.etat["cle"]})
        self.assertEqual(self.hue.tester(), {"ok": True, "lumieres": 4})
        r = self.hue.enregistrer_roles({"ecran": ["l-1", "l-2"], "panneau": ["l-3"], "salle": ["l-4"]})
        self.assertEqual({l["id"]: l["role"] for l in r["lumieres"]}, {"l-1": "ecran", "l-2": "ecran", "l-3": "panneau", "l-4": "salle"})
        with self.assertRaises(self.hue.Echec):
            self.hue.enregistrer_roles({"ecran": ["l-1"], "salle": ["l-1"]})
        with self.assertRaises(self.hue.Echec):
            self.hue.enregistrer_roles({"ecran": ["inconnue"]})
        # lumieres.py suit les rôles : générique sur l'écran seul, noir sur l'écran et le panneau, pièce éteinte au départ.
        import lumieres, importlib
        importlib.reload(lumieres)
        with patch.object(lumieres, "charger", return_value={"lumieres_actives": True, "niv_generique": 30, "niv_debut": 40}), \
                patch.object(lumieres.denon, "en_arriere_plan"):
            h = lumieres.Hue()
            h.generique(1)
            self.assertEqual(sorted(p for p, c in self.pont.etat["puts"]), ["/clip/v2/resource/light/l-1", "/clip/v2/resource/light/l-2",
                                                                              "/clip/v2/resource/light/l-4"])
            self.pont.etat["puts"].clear()
            h.debut(1)
            self.assertEqual(len(self.pont.etat["puts"]), 4)
        # Nouvelle association au même pont : les rôles sont gardés.
        self.integ.enregistrer_hue(self.pont.adresse, "nouvelle-cle")
        self.assertEqual(self.integ.hue_roles()["panneau"], ["l-3"])

    def test_pont_absent_bouton_jamais_presse_adresse_invalide(self):
        self.hue.DECOUVERTE = "http://127.0.0.1:9/rien"
        self.assertEqual(self.hue.rechercher(), [])
        with self.assertRaises(self.hue.Echec):
            self.hue.associer("127.0.0.1:9")
        for mauvaise in ("", "http://x", "1.2.3.4/../../", "a b"):
            with self.assertRaises(self.hue.Echec):
                self.hue.associer(mauvaise)
        self.pont.etat["bouton_apres"] = 99
        for _ in range(3):
            self.assertEqual(self.hue.associer(self.pont.adresse)["etat"], "bouton")
        self.assertIsNone(self.integ.hue())

    def test_sans_hue_seance_et_ampli_continuent(self):
        import lumieres, importlib
        importlib.reload(lumieres)
        with patch.object(lumieres, "charger", return_value={"lumieres_actives": True, "niv_debut": 40}), \
                patch.object(lumieres.denon, "en_arriere_plan") as ampli, patch.dict(os.environ, {"KAMCINE_SEANCE": "1"}):
            h = lumieres.Hue()                                  # aucune erreur sans pont
            self.assertFalse(h.configure)
            h.debut(1)
            h.noir(1)
            self.assertEqual(self.pont.etat["puts"], [])
            self.assertEqual(ampli.call_count, 2, "l'ampli suit toujours les scènes")


LANCEUR = """
import sys
sys.path.insert(0, sys.argv[3])
import faux_pyatv, json, os
faux_pyatv.installer()
faux_pyatv.ETAT["appareils"] = json.loads(os.environ["FAUX_APPAREILS"])
sys.path.insert(0, sys.argv[1])
import main
main.atvlive.LIVE.demarrer = lambda: None
if os.environ.get("KAMCINE_TEST_CAPTURE_START") == "1":
    main.demarrer_seance = lambda *args, **kwargs: {"ok": True, "cible_test": kwargs.get("appletv_id"),
                                                      "defaut_global_test": (main.integrations.appletv() or {}).get("identifiant")}
import uvicorn
uvicorn.run(main.app, host="127.0.0.1", port=int(sys.argv[2]), log_level="warning")
"""


class ServiceReel(unittest.TestCase):
    def test_routes_appareils(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        pont = FauxPont()
        self.addCleanup(pont.arreter)
        import test_chemins
        with patch.object(test_chemins, "LANCEUR", LANCEUR.replace("sys.argv[3]", repr(os.path.join(RACINE, "outils")))):
            s = Service({"KAMCINE_APP": RACINE, "KAMCINE_DATA": tmp.name, "HUE_SCHEME": "http", "COMP": "", "AIR": "",
                         "HUE_DISCOVERY_URL": "http://%s/decouverte" % pont.adresse,
                         "FAUX_APPAREILS": json.dumps([["AA:BB:CC:DD:EE:01", "Salon"]]),
                         "KAMCINE_TEST_CAPTURE_START": "1"}, RACINE)
        self.addCleanup(s.arreter)
        s.http.post(s.url + "/auth/inscription", json={"nom": "Admin", "identifiant": "admin", "mot_de_passe": "motdepasse-test"}, timeout=10)
        admin = s.http
        c = admin.get(s.url + "/appareils/configuration", timeout=10).json()
        self.assertEqual((c["appletv"]["configuree"], c["hue"]["configuree"], c["denon"]["configuree"]), (False, False, False), "installation vierge")
        # Apple TV : recherche, appairage, codes.
        r = admin.post(s.url + "/appareils/appletv/rechercher", timeout=30).json()
        self.assertEqual([a["nom"] for a in r["appareils"]], ["Salon"])
        r = admin.post(s.url + "/appareils/appletv/appairer", json={"identifiant": "AA:BB:CC:DD:EE:01"}, timeout=30).json()
        self.assertEqual(r["etape"], "pin")
        mauvais = admin.post(s.url + "/appareils/appletv/pin", json={"session": r["session"], "pin": "9999"}, timeout=30)
        self.assertEqual((mauvais.status_code, mauvais.json()["ok"]), (200, False), "mauvais code : message, pas d'erreur serveur")
        r = admin.post(s.url + "/appareils/appletv/appairer", json={"identifiant": "AA:BB:CC:DD:EE:01"}, timeout=30).json()
        r = admin.post(s.url + "/appareils/appletv/pin", json={"session": r["session"], "pin": "1234"}, timeout=30).json()
        r = admin.post(s.url + "/appareils/appletv/pin", json={"session": r["session"], "pin": "5678"}, timeout=30).json()
        self.assertEqual(r["etape"], "fini")
        self.assertEqual(admin.post(s.url + "/appareils/appletv/tester", timeout=30).json(), {"ok": True, "nom": "Salon"})
        # Hue : bouton, clé, rôles.
        self.assertEqual(admin.post(s.url + "/appareils/hue/rechercher", timeout=20).json()["ponts"][0]["adresse"], pont.adresse)
        self.assertEqual(admin.post(s.url + "/appareils/hue/associer", json={"adresse": pont.adresse}, timeout=20).json()["etat"], "bouton")
        self.assertEqual(admin.post(s.url + "/appareils/hue/associer", json={"adresse": pont.adresse}, timeout=20).json()["etat"], "fini")
        r = admin.post(s.url + "/appareils/hue/roles", json={"roles": {"ecran": ["l-1"], "salle": ["l-4"]}}, timeout=20).json()
        self.assertTrue(r["ok"])
        e = admin.get(s.url + "/installation/etat", timeout=10).json()
        self.assertEqual((e["pret"]["seances"], e["services"]["appletv"]["nom"], e["services"]["hue"]["roles_associes"]), (True, "Salon", True))
        # Aucun secret envoyé au navigateur.
        with open(os.path.join(tmp.name, "secrets.json")) as f:
            sec = json.load(f)
        texte = "".join(admin.get(s.url + u, timeout=15).text for u in ("/appareils/configuration", "/installation/etat", "/appareils/hue/lumieres",
                                                                          "/appareils/etat"))
        for secret in (sec["appletv"]["companion"], sec["appletv"]["airplay"], sec["hue"]["cle"]):
            self.assertNotIn(secret, texte)
        # Un Utilisateur n'y a pas accès.
        admin.post(s.url + "/comptes", json={"nom": "Camille", "identifiant": "camille", "mot_de_passe": "motdepasse-l"}, timeout=10)
        camille = requests.Session()
        camille.post(s.url + "/auth/connexion", json={"identifiant": "camille", "mot_de_passe": "motdepasse-l"}, timeout=10)
        admin.post(s.url + "/comptes", json={"nom": "Compte B", "identifiant": "compte-b", "mot_de_passe": "motdepasse-l"}, timeout=10)
        compte_b = requests.Session()
        compte_b.post(s.url + "/auth/connexion", json={"identifiant": "compte-b", "mot_de_passe": "motdepasse-l"}, timeout=10)
        # L'installation conserve ses appareils et son défaut physique, mais les deux comptes choisissent indépendamment.
        chemin_secrets = os.path.join(tmp.name, "secrets.json")
        with open(chemin_secrets, encoding="utf-8") as f:
            secrets_installation = json.load(f)
        secrets_installation["appletvs"].append({"identifiant": "AA:BB:CC:DD:EE:02", "companion": "fake-companion-2",
                                                  "airplay": "fake-airplay-2", "nom": "Chambre", "nom_detecte": "Chambre"})
        with open(chemin_secrets, "w", encoding="utf-8") as f:
            json.dump(secrets_installation, f)
        self.assertEqual(compte_b.get(s.url + "/installation/etat", timeout=10).json()["pret"]["appletv_pref"],
                         "AA:BB:CC:DD:EE:01", "sans préférence, le compte récupère le fallback global")
        self.assertTrue(camille.post(s.url + "/moi/appletv", json={"identifiant": "AA:BB:CC:DD:EE:02"}, timeout=10).json()["ok"])
        self.assertTrue(compte_b.post(s.url + "/moi/appletv", json={"identifiant": "AA:BB:CC:DD:EE:01"}, timeout=10).json()["ok"])
        for client, attendu in ((camille, "AA:BB:CC:DD:EE:02"), (compte_b, "AA:BB:CC:DD:EE:01")):
            pret = client.get(s.url + "/installation/etat", timeout=10).json()["pret"]
            self.assertEqual(pret["appletv_pref"], attendu)
            self.assertEqual([x["identifiant"] for x in pret["appletvs"] if x["active"]], [attendu])
        with open(chemin_secrets, encoding="utf-8") as f:
            apres_preferences = json.load(f)
        self.assertEqual(apres_preferences["appletv_active"], "AA:BB:CC:DD:EE:01",
                         "les préférences privées ne réécrivent pas le défaut global")
        # Le démarrage réel résout la préférence de chaque compte vers la cible de séance.
        for client, attendu in ((camille, "AA:BB:CC:DD:EE:02"), (compte_b, "AA:BB:CC:DD:EE:01")):
            lancement = client.post(s.url + "/start", timeout=10).json()
            self.assertEqual(lancement["cible_test"], attendu)
            self.assertEqual(lancement["defaut_global_test"], "AA:BB:CC:DD:EE:01")
        for chemin in ("/appareils/appletv/rechercher", "/appareils/appletv/appairer", "/appareils/hue/associer", "/appareils/hue/roles",
                       "/appareils/denon/tester"):
            self.assertEqual(camille.post(s.url + chemin, json={}, timeout=10).status_code, 403, chemin)
        self.assertEqual(camille.get(s.url + "/appareils/configuration", timeout=10).status_code, 403)
        # La liste d'installation reflète la préférence du compte, pas un appareil actif global.
        pret = camille.get(s.url + "/installation/etat", timeout=10).json()["pret"]
        self.assertEqual([x["identifiant"] for x in pret["appletvs"] if x["active"]], ["AA:BB:CC:DD:EE:02"])
        self.assertEqual(len(pret["appletvs"]), 2)
        self.assertNotIn(sec["appletv"]["companion"], json.dumps(pret))
        r = camille.post(s.url + "/start?appletv=11:22:33:44:55:66", timeout=10)
        self.assertEqual((r.status_code, r.json()["ok"]), (400, False), "Apple TV inconnue refusée")
        self.assertEqual(admin.post(s.url + "/appareils/appletv/renommer", json={"identifiant": "AA:BB:CC:DD:EE:01", "nom": "Salon TV"},
                                    timeout=10).json()["appareils"][0]["nom"], "Salon TV")
        self.assertEqual(camille.post(s.url + "/appareils/appletv/activer", json={"identifiant": "AA:BB:CC:DD:EE:01"}, timeout=10).status_code, 403)
        # Adresses : aucune supposée ; Overseerr dit l'adresse réellement utilisée.
        self.assertEqual({i["url"] for i in admin.get(s.url + "/arr/etat", timeout=10).json()["instances"]}, {""})
        ov = admin.get(s.url + "/overseerr/etat", timeout=10).json()
        self.assertEqual((ov["par_defaut"], ov["url"].startswith("http://127.0.0.1:")), (True, True))
        admin.post(s.url + "/reglages", json={"overseerr_url": "https://overseerr.exemple.fr"}, timeout=10)
        ov = admin.get(s.url + "/overseerr/etat", timeout=10).json()
        self.assertEqual((ov["par_defaut"], ov["url"]), (False, "https://overseerr.exemple.fr"), "l'adresse enregistrée est celle utilisée")
        d = admin.post(s.url + "/appareils/denon/tester", timeout=10).json()
        self.assertEqual((d["ok"], d["message"]), (False, "Indiquez d’abord l’adresse de l’ampli."))


class CodeActif(unittest.TestCase):
    def test_aucun_identifiant_ni_nom_personnel(self):
        fichiers = [f for f in os.listdir(RACINE) if f.endswith(".py")] + ["app/" + f for f in os.listdir(os.path.join(RACINE, "app"))
                                                                            if f.endswith((".py", ".html", ".js"))]
        mac = re.compile(r"\b[0-9A-F]{2}(?::[0-9A-F]{2}){5}\b")
        try:
            import marqueurs_prives            # valeurs de l'installation de développement, fichier privé non publié
            prives = [re.compile(m) for m in marqueurs_prives.MARQUEURS]
        except ImportError:
            prives = []
        for f in fichiers:
            if f.startswith("outils/"):
                continue
            texte = open(os.path.join(RACINE, f), encoding="utf-8").read()
            self.assertEqual(mac.findall(texte), [], "identifiant d'appareil écrit dans " + f)
            for m in prives:
                self.assertIsNone(m.search(texte), "%s dans %s" % (m.pattern, f))


if __name__ == "__main__":
    unittest.main()
