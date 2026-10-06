"""Contenu de l'image Docker de KamCiné sans Docker (2.6.108).

Reproduit ce que `docker build` enverrait puis copierait dans /app : les fichiers du dossier courant filtrés par
.dockerignore, avec la règle de Docker (le dernier motif qui correspond au chemin ou à l'un de ses dossiers parents
l'emporte, `!` réintègre). Vérifie :
1. que tout ce dont l'exécution a besoin y est (modules importés par app/main.py et seance.py, fichiers servis) ;
2. que rien de personnel ou de secret n'y est (données, journaux, documentation privée, deploy.sh, photo, secrets) ;
3. qu'aucun fichier retenu ne contient une adresse, un identifiant d'appareil ou une clé.
`preparer(dossier)` recopie ce contenu, comme /app dans l'image, pour faire tourner le service à l'identique.
Usage : python3 outils/verifier_image.py"""
import ast
import fnmatch
import os
import re
import shutil
import sys

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FICHIERS_SERVIS = ["app/index.html", "app/main.py", "app/manifest.webmanifest", "app/sw.js", "app/splash.jpg",
                   "app/icon_clair.png", "app/icon_sombre.png", "requirements.txt", "docker/entrypoint.sh", "docker/collect_licenses.py", "docker/constraints.txt", "docker/licenses/http-ece-1.2.1.txt", "docker/licenses/native-notices.txt", "docker/licenses/rust-crates.json", "LICENSE", "THIRD_PARTY_NOTICES.md"]
INTERDITS = [r"^\.git/", r"^docs/", r"^outils/", r"^CLAUDE\.md$", r"^deploy\.sh$", r"^docker-compose\.yml$", r"^Dockerfile$",
             r"\.json$", r"\.db(-wal|-shm)?$", r"\.log$", r"(^|/)creds\.env$", r"profil\.jpg$", r"^sauvegardes/", r"^backups/",
             r"^avatars/", r"^config/", r"__pycache__", r"\.pyc$", r"^entracte\.py$", r"^trailers\.py$", r"^test_lumieres\.py$"]
# Règles génériques (identifiant d'appareil, chemin de volume, clé privée), plus les valeurs propres à l'installation de
# développement si le fichier privé outils/marqueurs_prives.py est présent (il n'est pas publié).
MARQUEURS = [re.compile(r"\b[0-9A-F]{2}(?::[0-9A-F]{2}){5}\b"), re.compile(r"/volume\d"), re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")]
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import marqueurs_prives
    MARQUEURS += [re.compile(m) for m in marqueurs_prives.MARQUEURS]
except ImportError:
    pass


def motifs(chemin=None):
    lignes = open(chemin or os.path.join(RACINE, ".dockerignore"), encoding="utf-8").read().splitlines()
    out = []
    for l in lignes:
        l = l.strip()
        if not l or l.startswith("#"):
            continue
        exception = l.startswith("!")
        out.append((exception, l[1:].strip("/") if exception else l.strip("/")))
    return out


def _correspond(motif, chemin):
    """Motif Docker : `**` traverse les dossiers, `*` reste dans un dossier ; un motif vaut aussi pour les parents."""
    parties = chemin.split("/")
    candidats = ["/".join(parties[:i]) for i in range(1, len(parties) + 1)]
    if "**" in motif:
        rx = re.compile("^" + re.escape(motif).replace(r"\*\*/", "(?:.*/)?").replace(r"\*\*", ".*").replace(r"\*", "[^/]*").replace(r"\?", "[^/]") + "$")
        return any(rx.match(c) for c in candidats)
    n = motif.count("/") + 1
    return any(c.count("/") + 1 == n and fnmatch.fnmatchcase(c, motif) for c in candidats)


def retenu(chemin, regles):
    garde = True
    for exception, motif in regles:
        if _correspond(motif, chemin):
            garde = exception
    return garde


def contexte(racine=RACINE, regles=None):
    regles = regles if regles is not None else motifs(os.path.join(racine, ".dockerignore"))
    out = []
    for dossier, sous, fichiers in os.walk(racine):
        rel = os.path.relpath(dossier, racine)
        if rel.split(os.sep)[0] == ".git":
            sous[:] = []
            continue
        for f in fichiers:
            chemin = f if rel == "." else os.path.join(rel, f).replace(os.sep, "/")
            if retenu(chemin, regles):
                out.append(chemin)
    return sorted(out)


def necessaires(racine=RACINE):
    """Modules locaux importés, de proche en proche, depuis app/main.py et seance.py, plus les fichiers servis."""
    locaux = {f[:-3] for f in os.listdir(racine) if f.endswith(".py")}
    vus, pile = set(), ["app/main.py", "seance.py"]
    while pile:
        f = pile.pop()
        if f in vus:
            continue
        vus.add(f)
        for n in ast.walk(ast.parse(open(os.path.join(racine, f), encoding="utf-8").read())):
            noms = []
            if isinstance(n, ast.Import):
                noms = [a.name.split(".")[0] for a in n.names]
            elif isinstance(n, ast.ImportFrom) and n.module:
                noms = [n.module.split(".")[0]]
            elif isinstance(n, ast.Call) and getattr(n.func, "id", "") == "__import__" and n.args and isinstance(n.args[0], ast.Constant):
                noms = [n.args[0].value]
            pile += [m + ".py" for m in noms if m in locaux and m + ".py" not in vus]
    logos = ["app/logos/tmdb.svg"]
    return sorted(vus | set(FICHIERS_SERVIS) | set(logos))


def verifier(racine=RACINE):
    fichiers = contexte(racine)
    manquants = [f for f in necessaires(racine) if f not in fichiers]
    interdits = [f for f in fichiers if f != "docker/licenses/rust-crates.json" and any(re.search(r, f) for r in INTERDITS)]
    marques = []
    for f in fichiers:
        try:
            texte = open(os.path.join(racine, f), encoding="utf-8").read()
        except UnicodeDecodeError:
            continue
        # Preserve upstream copyright notices verbatim; their named author matches one private-name marker.
        marque_texte = re.sub(r"Seth M[a-z]+ Larson", "", texte) if f == "docker/licenses/native-notices.txt" else texte
        try:
            from marqueurs_prives import texte_public
            marque_texte = texte_public(f, marque_texte)
        except ImportError:
            pass
        marques += ["%s : %s" % (f, m.pattern) for m in MARQUEURS if m.search(marque_texte)]
    return {"fichiers": fichiers, "manquants": manquants, "interdits": interdits, "marques": marques}


def preparer(dest, racine=RACINE):
    """Recopie le contenu de /app de l'image dans dest."""
    for f in contexte(racine):
        cible = os.path.join(dest, f)
        os.makedirs(os.path.dirname(cible), exist_ok=True)
        shutil.copy2(os.path.join(racine, f), cible)
    return dest


if __name__ == "__main__":
    r = verifier()
    print("Fichiers dans l'image (/app) : %d" % len(r["fichiers"]))
    for cle, titre in (("manquants", "Nécessaires mais absents"), ("interdits", "Présents mais interdits"), ("marques", "Valeurs personnelles ou secrètes")):
        print("%s : %s" % (titre, ", ".join(r[cle]) if r[cle] else "aucun"))
    sys.exit(1 if r["manquants"] or r["interdits"] or r["marques"] else 0)
