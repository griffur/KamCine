"""Le verrou des comptes (2.6.102) protège les routes Appareils et accepte la session du navigateur ; depuis la 2.6.104,
les routes Appareils sont réservées aux administrateurs et les permissions d'un Utilisateur sont vérifiées au même endroit."""
import ast
import asyncio
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))
SOURCE = RACINE / "app" / "main.py"
ARBRE = ast.parse(SOURCE.read_text(encoding="utf-8"))
NOMS = {"ui_path", "route_libre", "verrou", "en_https", "pose_cookie", "appareils_synology", "route_admin", "permission_refusee"}
CONSTANTES = {"UI_SCREEN_PATHS", "ROUTES_ADMIN", "ROUTES_ADMIN_ECRITURE", "PREFIXES_ADMIN", "AVATAR_COMPTE", "PERMISSION_ROUTES", "MESSAGES_PERMISSION"}
FUNCS = []
for node in ARBRE.body:
    if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in CONSTANTES for t in node.targets):
        FUNCS.append(node)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in NOMS:
        node.decorator_list = []
        node.returns = None
        for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
            arg.annotation = None
        FUNCS.append(node)


class JSONResponse:
    def __init__(self, content, status_code=200):
        self.content = content
        self.status_code = status_code


class Request:
    def __init__(self, path, cookies=None):
        self.url = SimpleNamespace(path=path, scheme="http")
        self.method = "POST"
        self.cookies = cookies or {}
        self.headers = {}
        self.state = SimpleNamespace()


class Synology:
    @staticmethod
    def lire():
        return {"en_ligne": True, "modele": "DS de test", "uptime": 1234, "total": 1000}


class AuthAppareilsTests(unittest.TestCase):
    def test_routes_appareils_refusent_sans_session_et_acceptent_session_valide(self):
        with tempfile.TemporaryDirectory(prefix="kamcine-auth-appareils-") as dossier:
            os.environ["KAMCINE_DATA"] = dossier
            self.addCleanup(os.environ.pop, "KAMCINE_DATA", None)
            import comptes
            comptes.migrer()
            compte = comptes.creer_compte("Test", "test", "motdepasse", "admin", premier=True)
            jeton, _ = comptes.ouvrir_session(compte["id"], 30)
            camille = comptes.creer_compte("Camille", "camille", "motdepasse")
            jeton_camille, _ = comptes.ouvrir_session(camille["id"], 30)
            import re
            env = {"re": re, "COOKIE": "kc_compte", "ETAT_BASE": {"erreur": ""}, "comptes": comptes, "JSONResponse": JSONResponse,
                   "erreurs": SimpleNamespace(message=str), "synology": Synology, "run_in_threadpool": self.run_inline}
            exec(compile(ast.Module(body=FUNCS, type_ignores=[]), str(SOURCE), "exec"), env)

            async def endpoint(request):
                if request.url.path == "/appareils/synology":
                    return await env["appareils_synology"]()
                return {"ok": True, "route": request.url.path}

            for path in ("/appareils/synology", "/appareils/etat", "/planning/confirmer", "/lancement/reprendre", "/notifications/ouvrir", "/notifications/supprimer", "/catalogue/fichiers"):
                refuse = asyncio.run(env["verrou"](Request(path), endpoint))
                self.assertEqual(refuse.status_code, 401)
                self.assertTrue(refuse.content["connexion"])
                faux = asyncio.run(env["verrou"](Request(path, {"kc_compte": "jeton-inconnu"}), endpoint))
                self.assertEqual(faux.status_code, 401)
                accepte = asyncio.run(env["verrou"](Request(path, {"kc_compte": jeton}), endpoint))
                if path.endswith("synology"):
                    self.assertEqual(accepte["modele"], "DS de test")
                    self.assertEqual(accepte["uptime"], 1234)
                else:
                    self.assertEqual(accepte, {"ok": True, "route": path})
                utilisatrice = asyncio.run(env["verrou"](Request(path, {"kc_compte": jeton_camille}), endpoint))
                if path.startswith("/appareils/"):
                    self.assertEqual((utilisatrice.status_code, utilisatrice.content["admin"]), (403, True), path)
                else:
                    self.assertEqual(utilisatrice, {"ok": True, "route": path}, "permission accordée par défaut : " + path)
            comptes.changer_permission(camille["id"], "seances", False)
            refus = asyncio.run(env["verrou"](Request("/lancement/reprendre", {"kc_compte": jeton_camille}), endpoint))
            self.assertEqual((refus.status_code, refus.content["permission"]), (403, "seances"))
            self.assertEqual(asyncio.run(env["verrou"](Request("/notifications/ouvrir", {"kc_compte": jeton_camille}), endpoint))["ok"], True)

    @staticmethod
    async def run_inline(function, *args):
        return function(*args)


if __name__ == "__main__":
    unittest.main()
