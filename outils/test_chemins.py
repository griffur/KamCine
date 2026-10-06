"""Séparation du code et des données (2.6.101).

1. chemins.py : KAMCINE_APP et KAMCINE_DATA priment, sinon KAMCINE_DIR pour les deux, sinon /cinema.
2. Un vrai service (uvicorn) lancé avec le code copié dans un dossier et les données dans un autre : l'interface, les
   logos et les icônes viennent du code ; réglages, base des comptes et photos des comptes (avatars/, 2.6.108)
   s'écrivent dans les données ; le dossier du code ne reçoit aucun fichier.
3. Compatibilité : avec KAMCINE_DIR seul, tout est écrit comme avant dans ce dossier.

La connexion continue à l'Apple TV est neutralisée (aucun appareil touché) et les adresses externes pointent vers un port
fermé. Il faut le Python du service (fastapi, uvicorn, pyatv, requests) : python -m unittest outils.test_chemins"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest

import requests

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RACINE)
import chemins

FERME = "http://127.0.0.1:9"
LANCEUR = """
import os, sys
sys.path.insert(0, sys.argv[1])
import main
main.atvlive.LIVE.demarrer = lambda: None
import uvicorn
uvicorn.run(main.app, host="127.0.0.1", port=int(sys.argv[2]), log_level="warning")
"""
def jpeg(largeur=512, hauteur=512, remplissage=b"K"):
    """Petite image JPEG structurellement valide (début, commentaire, en tête de trame avec dimensions, fin) : ce que
    comptes.jpeg_valide exige d'une photo de compte."""
    commentaire = remplissage * 700
    sof = b"\x08" + hauteur.to_bytes(2, "big") + largeur.to_bytes(2, "big") + b"\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01"
    return (b"\xff\xd8" + b"\xff\xfe" + (len(commentaire) + 2).to_bytes(2, "big") + commentaire
            + b"\xff\xc0" + (len(sof) + 2).to_bytes(2, "big") + sof + b"\x00" * 16 + b"\xff\xd9")


JPEG = jpeg()


def copier_code(dest):
    """Copie les fichiers suivis par Git, comme deploy.sh ou une future image."""
    fichiers = subprocess.run(["git", "ls-files"], cwd=RACINE, capture_output=True, text=True, check=True).stdout.split()
    for nom in fichiers:
        cible = os.path.join(dest, nom)
        os.makedirs(os.path.dirname(cible), exist_ok=True)
        shutil.copy2(os.path.join(RACINE, nom), cible)
    return set(fichiers)


def contenu(dossier):
    return {os.path.relpath(os.path.join(d, f), dossier) for d, _, fs in os.walk(dossier) for f in fs
            if "__pycache__" not in d}


class Service:
    def __init__(self, env, app_dir):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        self.port = s.getsockname()[1]
        s.close()
        self.url = "http://127.0.0.1:%d" % self.port
        base = {k: v for k, v in os.environ.items() if not k.startswith("KAMCINE_")}
        base.update(TMDB_BASE=FERME, OMDB_BASE=FERME, TRAKT_BASE=FERME, INTRODB_BASE=FERME, YOUTUBE_RSS=FERME,
                    OEMBED_BASE=FERME, YOUTUBE_API_BASE=FERME, OVERSEERR_LOCAL=FERME, PYTHONWARNINGS="ignore")
        base.update(env)
        # Lancé depuis un dossier neutre : aucun chemin ne doit dépendre du dossier courant.
        self.neutre = tempfile.mkdtemp(prefix="kamcine_cwd_")
        self.proc = subprocess.Popen([sys.executable, "-c", LANCEUR, os.path.join(app_dir, "app"), str(self.port)],
                                     env=base, cwd=self.neutre, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.http = requests.Session()
        for _ in range(100):
            try:
                self.http.get(self.url + "/apropos", timeout=1)
                return
            except requests.RequestException:
                if self.proc.poll() is not None:
                    raise AssertionError(self.proc.stdout.read().decode(errors="replace"))
                time.sleep(0.2)
        self.arreter()
        raise AssertionError("service injoignable")

    def arreter(self):
        self.proc.terminate()
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        shutil.rmtree(self.neutre, ignore_errors=True)


class Chemins(unittest.TestCase):
    def env(self, **valeurs):
        noms = ("KAMCINE_DIR", "KAMCINE_APP", "KAMCINE_DATA")
        anciens = {k: os.environ.pop(k) for k in noms if k in os.environ}

        def restaurer():
            for k in noms:
                os.environ.pop(k, None)
            os.environ.update(anciens)
        self.addCleanup(restaurer)
        os.environ.update(valeurs)

    def test_sans_variable_tout_reste_dans_cinema(self):
        self.env()
        self.assertEqual((chemins.app_dir(), chemins.data_dir()), ("/cinema", "/cinema"))
        self.assertEqual(chemins.donnee("historique.json"), "/cinema/historique.json")

    def test_kamcine_dir_seul_garde_un_seul_dossier(self):
        self.env(KAMCINE_DIR="/volume/ancien")
        self.assertEqual((chemins.app_dir(), chemins.data_dir()), ("/volume/ancien", "/volume/ancien"))

    def test_nouvelles_variables_priment(self):
        self.env(KAMCINE_DIR="/ancien", KAMCINE_APP="/app", KAMCINE_DATA="/config")
        self.assertEqual(chemins.code("seance.py"), "/app/seance.py")
        self.assertEqual(chemins.donnee("reglages.json"), "/config/reglages.json")

    def test_une_seule_variable_neuve_laisse_l_autre_a_l_ancien_dossier(self):
        self.env(KAMCINE_DIR="/ancien", KAMCINE_DATA="/config")
        self.assertEqual((chemins.app_dir(), chemins.data_dir()), ("/ancien", "/config"))

    def test_historique_de_seance_dans_les_donnees(self):
        """seance.py ne s'importe pas (piège 2 de CLAUDE.md) : on évalue sa vraie ligne HISTORIQUE."""
        import ast
        with open(os.path.join(RACINE, "seance.py"), encoding="utf-8") as f:
            arbre = ast.parse(f.read())
        ligne = next(n for n in arbre.body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "HISTORIQUE")
        self.env(KAMCINE_APP="/app", KAMCINE_DATA="/config")
        valeur = eval(compile(ast.Expression(ligne.value), "seance.py", "eval"), {"chemins": chemins})
        self.assertEqual(valeur, "/config/historique.json")

    def test_plus_aucun_cinema_en_dur_dans_le_service(self):
        for nom in subprocess.run(["git", "ls-files", "*.py"], cwd=RACINE, capture_output=True, text=True).stdout.split():
            if nom.startswith("outils/") or nom in ("test_lumieres.py", "app/faire_icone.py"):
                continue          # tests et scripts hérités, hors service
            with open(os.path.join(RACINE, nom), encoding="utf-8") as f:
                texte = f.read()
            self.assertNotIn('"/cinema/', texte, nom)
            if nom != "chemins.py":
                self.assertNotIn('environ.get("KAMCINE_DIR"', texte, nom)


class ServiceSepare(unittest.TestCase):
    def test_code_et_donnees_dans_deux_dossiers(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        code, donnees = os.path.join(tmp.name, "code"), os.path.join(tmp.name, "donnees")
        os.makedirs(donnees)
        copier_code(code)
        os.makedirs(os.path.join(donnees, "logos"))
        logo_prive = b"<svg xmlns='http://www.w3.org/2000/svg'><title>asset prive</title></svg>"
        with open(os.path.join(donnees, "logos", "imdb.svg"), "wb") as f:
            f.write(logo_prive)
        avant = contenu(code)
        s = Service({"KAMCINE_APP": code, "KAMCINE_DATA": donnees}, code)
        try:
            page = s.http.get(s.url + "/", timeout=5)
            self.assertEqual(page.status_code, 200)
            self.assertIn("const VERSION='2.7.10'", page.text)
            for chemin in ("/manifest.json", "/sw.js", "/icon.png?style=sombre", "/splash.jpg"):
                self.assertEqual(s.http.get(s.url + chemin, timeout=5).status_code, 200, chemin)
            self.assertEqual(s.http.get(s.url + "/reglages", timeout=5).status_code, 401, "verrou actif avant tout compte")
            self.assertTrue(s.http.post(s.url + "/auth/inscription", json={"nom": "Test", "identifiant": "test", "mot_de_passe": "motdepasse"},
                                        timeout=10).json()["ok"])
            self.assertEqual(s.http.get(s.url + "/apropos", timeout=5).json()["version"], "2.7.10")
            self.assertEqual(s.http.get(s.url + "/logos/tmdb.svg", timeout=5).status_code, 200)
            prive = s.http.get(s.url + "/logos/imdb.svg", timeout=5)
            self.assertEqual(prive.status_code, 200)
            self.assertEqual(prive.content, logo_prive)
            self.assertIn("private", prive.headers.get("Cache-Control", ""))
            self.assertEqual(s.http.get(s.url + "/logos/../../secrets.json", timeout=5).status_code, 404)
            self.assertEqual(s.http.get(s.url + "/logos/inconnu.svg", timeout=5).status_code, 404)
            self.assertEqual(s.http.post(s.url + "/reglages", json={"recul": 7}, timeout=5).json()["recul"], 7)
            moi = s.http.post(s.url + "/moi/avatar", data=JPEG, timeout=5).json()["compte"]
            self.assertEqual(s.http.get(s.url + "/comptes/%d/avatar" % moi["id"], timeout=5).content, JPEG)
            self.assertEqual(requests.get(s.url + "/reglages", timeout=5).status_code, 401)   # verrou actif
        finally:
            s.arreter()
        with open(os.path.join(donnees, "reglages.json")) as f:
            self.assertEqual(json.load(f)["recul"], 7)
        for nom in ("kamcine.db", os.path.join("avatars", "1.jpg")):
            self.assertTrue(os.path.exists(os.path.join(donnees, nom)), nom)
        self.assertFalse(os.path.exists(os.path.join(donnees, "app")))
        self.assertEqual(contenu(code), avant, "le dossier du code a été modifié")

    def test_installation_actuelle_kamcine_dir_seul(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        copier_code(tmp.name)
        s = Service({"KAMCINE_DIR": tmp.name}, tmp.name)
        try:
            self.assertEqual(s.http.get(s.url + "/", timeout=5).status_code, 200)
            s.http.post(s.url + "/auth/inscription", json={"nom": "Test", "identifiant": "test", "mot_de_passe": "motdepasse"}, timeout=10)
            self.assertEqual(s.http.post(s.url + "/reglages", json={"recul": 3}, timeout=5).json()["recul"], 3)
            self.assertEqual(s.http.post(s.url + "/moi/avatar", data=JPEG, timeout=5).status_code, 200)
        finally:
            s.arreter()
        self.assertTrue(os.path.exists(os.path.join(tmp.name, "reglages.json")))
        self.assertTrue(os.path.exists(os.path.join(tmp.name, "kamcine.db")))
        self.assertTrue(os.path.exists(os.path.join(tmp.name, "avatars", "1.jpg")))     # photo du compte, dans les données
        self.assertFalse(os.path.exists(os.path.join(tmp.name, "profil.jpg")))


if __name__ == "__main__":
    unittest.main()
