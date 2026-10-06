"""Image Docker de KamCiné (2.7.10), vérifiée sans Docker : contenu de /app, fichiers Docker, et le service lancé
comme dans le conteneur (code recopié dans un /app simulé par outils/verifier_image.py, /config vide à part).

Couvre : contenu de l'image (nécessaires présents, rien de personnel), Dockerfile, script de démarrage et compose,
démarrage vierge (/health, aucun compte, premier administrateur, base dans /config, /app jamais modifié), persistance
après « recréation » (nouveau /app, même /config), mise à jour depuis une ancienne base, arrêt propre sur SIGTERM.
Il faut le Python du service : python -m unittest outils.test_image"""
import hashlib
import json
import os
import re
import signal
import sqlite3
import sys
import tempfile
import time
import unittest

import requests

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RACINE)
sys.path.insert(0, os.path.join(RACINE, "outils"))
import verifier_image
from test_chemins import Service


def empreinte(dossier):
    h = hashlib.sha256()
    for d, _, fichiers in sorted(os.walk(dossier)):
        for f in sorted(fichiers):
            chemin = os.path.join(d, f)
            h.update(os.path.relpath(chemin, dossier).encode())
            h.update(open(chemin, "rb").read())
    return h.hexdigest()


def lire(nom):
    return open(os.path.join(RACINE, nom), encoding="utf-8").read()


class ContenuImage(unittest.TestCase):
    def test_contenu_de_app(self):
        r = verifier_image.verifier()
        self.assertEqual((r["manquants"], r["interdits"], r["marques"]), ([], [], []))
        for present in ("app/main.py", "seance.py", "integrations.py", "appairage_atv.py", "app/index.html", "app/logos/tmdb.svg", "app/icon_sombre.png", "app/icon_clair.png", "app/splash.jpg", "requirements.txt"):
            self.assertIn(present, r["fichiers"])
        self.assertIn("docker/licenses/rust-crates.json", r["fichiers"])

    def test_regles_du_dockerignore(self):
        regles = verifier_image.motifs()
        for chemin, garde in (("app/main.py", True), ("app/profil.jpg", False), ("app/__pycache__/main.cpython-312.pyc", False),
                              ("docs/architecture.md", False), ("outils/test_image.py", False), ("secrets.json", False),
                              ("creds.env", False), ("kamcine.db", False), ("seance.log", False), ("deploy.sh", False),
                              ("CLAUDE.md", False), (".git/config", False), ("docker/entrypoint.sh", True), ("config/secrets.json", False),
                              ("sauvegardes/x.json", False), ("entracte.py", False), ("comptes.py", True)):
            self.assertEqual(verifier_image.retenu(chemin, regles), garde, chemin)


class FichiersDocker(unittest.TestCase):
    def test_dockerfile(self):
        d = lire("Dockerfile")
        for attendu in ("KAMCINE_APP=/app", "KAMCINE_DATA=/config", 'VOLUME ["/config"]', "HEALTHCHECK", "/health", "STOPSIGNAL SIGTERM",
                        "org.opencontainers.image.version", "COPY . /app", "--no-index", "EXPOSE 8765"):
            self.assertIn(attendu, d)
        self.assertEqual(d.count("FROM python:3.12-slim-bookworm"), 2, "construction puis image finale, même base")
        self.assertIn("tzdata=2026c-0+deb12u1", d, "version Debian installée épinglée")
        self.assertIn("cryptography==50.0.2", lire("docker/constraints.txt"), "OpenSSL embarqué corrigé")
        self.assertIn("build-essential", d.split("FROM python:3.12-slim-bookworm")[1], "compilateur seulement dans l'étape de construction")
        self.assertNotIn("build-essential", d.split("FROM python:3.12-slim-bookworm")[2])
        self.assertIsNone(re.search(r"(?i)(secret|password|token|api_?key)\s*=", d))
        self.assertNotIn("/cinema", d)
        self.assertNotIn("latest", d)

    def test_requirements_epingles(self):
        lignes = [l for l in lire("requirements.txt").splitlines() if l and not l.startswith("#")]
        self.assertEqual(sorted(l.split("==")[0] for l in lignes), ["fastapi", "pyatv", "pywebpush", "requests", "uvicorn"])
        self.assertTrue(all("==" in l for l in lignes))

    def test_script_de_demarrage(self):
        e = lire("docker/entrypoint.sh")
        self.assertIn("setpriv --reuid", e)
        self.assertIn("refuse de tourner en root", e)
        self.assertNotIn("777", e)
        self.assertIn('exec "$@"', e)
        self.assertIn("--timeout-graceful-shutdown", e)

    def test_compose(self):
        # docker-compose v1 (Synology) ne lit que docker-compose.yml et remonte sinon vers les dossiers parents, jusqu'au
        # docker-compose.yml de l'installation de Alex (env_file creds.env) : le fichier doit porter ce nom, dans docker/.
        self.assertFalse(os.path.exists(os.path.join(RACINE, "docker", "compose.yml")))
        c = lire("docker/docker-compose.yml")
        for attendu in ('version: "2.4"', "network_mode: host", "./config:/config", "init: true", "restart: unless-stopped", "PUID",
                        "KAMCINE_PORT", "KAMCINE_VAPID_SUBJECT", "container_name: ${KAMCINE_CONTAINER:-kamcine-test}", "context: ${KAMCINE_BUILD_CONTEXT:-..}", "dockerfile: Dockerfile"):
            self.assertIn(attendu, c)
        reglages = "\n".join(l.split("#")[0] for l in c.splitlines())        # hors commentaires
        for interdit in ("/cinema", "/volume", "env_file", "creds.env", "192.168."):
            self.assertNotIn(interdit, reglages)


class ServiceCommeDansLImage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = os.path.join(self.tmp.name, "config")
        os.makedirs(self.config)

    def app(self, nom="app"):
        return verifier_image.preparer(os.path.join(self.tmp.name, nom))

    def demarrer(self, app):
        s = Service({"KAMCINE_APP": app, "KAMCINE_DATA": self.config, "COMP": "", "AIR": ""}, app)
        self.addCleanup(s.arreter)
        return s

    def test_vierge_puis_recreation_avec_le_meme_config(self):
        app = self.app()
        avant = empreinte(app)
        s = self.demarrer(app)
        h = requests.get(s.url + "/health", timeout=5)
        self.assertEqual((h.status_code, h.json()), (200, {"ok": True, "version": "2.7.10"}), "sans session, sans donnée")
        self.assertEqual(requests.get(s.url + "/auth/etat", timeout=5).json()["etat"], "nouvelle", "aucun compte")
        self.assertIn("const VERSION='2.7.10'", requests.get(s.url + "/", timeout=5).text)
        for path in ("/home", "/catalogue", "/downloads", "/devices", "/profile", "/settings", "/films/12", "/series/34", "/people/56"):
            page = requests.get(s.url + path, timeout=5)
            self.assertEqual(page.status_code, 200, path)
            self.assertIn("const VERSION='2.7.10'", page.text, path)
        self.assertEqual(requests.get(s.url + "/catalogue/fiche?type=movie&id=12", timeout=5).status_code, 401)
        r = s.http.post(s.url + "/auth/inscription", json={"nom": "Admin", "identifiant": "admin", "mot_de_passe": "motdepasse-image"}, timeout=10)
        self.assertTrue(r.json()["compte"]["admin"])
        e = s.http.get(s.url + "/installation/etat", timeout=10).json()
        self.assertFalse(any(v["configuree"] for v in e["services"].values()), "aucune configuration personnelle")
        self.assertEqual(s.http.post(s.url + "/reglages", json={"denon_ip": "10.1.2.3"}, timeout=5).json()["denon_ip"], "10.1.2.3")
        contenu = set(os.listdir(self.config))
        self.assertIn("kamcine.db", contenu)
        self.assertNotIn("secrets.json", contenu, "secrets.json seulement quand une clé est enregistrée")
        self.assertNotIn("creds.env", contenu)
        self.assertEqual(empreinte(app), avant, "le code (/app) n'est jamais modifié")
        s.arreter()
        # Recréation : image neuve (nouveau /app), même /config.
        s = self.demarrer(self.app("app2"))
        self.assertEqual(requests.get(s.url + "/health", timeout=5).status_code, 200)
        self.assertEqual(requests.get(s.url + "/auth/etat", timeout=5).json()["etat"], "connexion", "le compte a survécu")
        http = requests.Session()
        self.assertEqual(http.post(s.url + "/auth/connexion", json={"identifiant": "admin", "mot_de_passe": "motdepasse-image"}, timeout=10).status_code, 200)
        self.assertEqual(http.get(s.url + "/reglages", timeout=5).json()["denon_ip"], "10.1.2.3", "la configuration a survécu")

    def test_mise_a_jour_depuis_une_ancienne_base(self):
        """/config d'une version antérieure (base de schéma 1, 2.6.102) repris par l'image : migration et sauvegarde."""
        import comptes
        ancien = os.environ.get("KAMCINE_DATA")
        os.environ["KAMCINE_DATA"] = self.config
        migrations, version = list(comptes.MIGRATIONS), comptes.VERSION_BASE
        try:
            comptes.MIGRATIONS[:] = migrations[:1]
            comptes.VERSION_BASE = 1
            comptes.migrer()
            c = sqlite3.connect(comptes.chemin_base())
            c.execute("INSERT INTO users (identifiant, nom, mot_de_passe, role, cree_a, maj_a) VALUES ('ancien', 'Ancien', ?, 'admin', 1, 1)",
                      (comptes.hacher("motdepasse-ancien", iterations=1000),))
            c.commit()
            c.close()
        finally:
            comptes.MIGRATIONS[:] = migrations
            comptes.VERSION_BASE = version
            if ancien is None:
                os.environ.pop("KAMCINE_DATA", None)
            else:
                os.environ["KAMCINE_DATA"] = ancien
        with open(os.path.join(self.config, "favoris.json"), "w") as f:
            json.dump([{"type": "movie", "id": 603, "titre": "Matrix", "t": 1}], f)
        s = self.demarrer(self.app())
        http = requests.Session()
        r = http.post(s.url + "/auth/connexion", json={"identifiant": "ancien", "mot_de_passe": "motdepasse-ancien"}, timeout=10)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual([x["id"] for x in http.get(s.url + "/profil/resume", timeout=15).json()["favoris"]["films"]], [603])
        c = sqlite3.connect(os.path.join(self.config, "kamcine.db"))
        self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], version)
        c.close()
        self.assertTrue(any(n.startswith("kamcine-schema1-") for n in os.listdir(os.path.join(self.config, "backups"))))

    def test_arret_propre_sur_sigterm(self):
        s = self.demarrer(self.app())
        self.assertEqual(requests.get(s.url + "/health", timeout=5).status_code, 200)
        debut = time.time()
        s.proc.send_signal(signal.SIGTERM)
        code = s.proc.wait(20)
        self.assertLess(time.time() - debut, 16, "arrêt dans le délai de grâce")
        self.assertIn(code, (0, -signal.SIGTERM), "arrêt demandé, pas un plantage")
        with self.assertRaises(requests.ConnectionError):
            requests.get(s.url + "/health", timeout=2)


if __name__ == "__main__":
    unittest.main()
