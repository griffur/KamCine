"""Contrôle de cohérence de l'interface, sans rien lancer.

1. Chaque classe CSS utilisée dans app/index.html (attributs class, classList, className) a une règle dans le CSS.
2. Chaque chemin d'API appelé par l'interface existe dans app/main.py.

Usage : python3 outils/verifier_interface.py
Code de sortie 1 s'il reste des écarts. Les classes construites dynamiquement (concaténation de texte)
ne sont vues que si elles apparaissent en clair quelque part dans le fichier."""
import os, re, sys

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = open(os.path.join(RACINE, "app", "index.html"), encoding="utf-8").read()
MAIN = open(os.path.join(RACINE, "app", "main.py"), encoding="utf-8").read()

# Classes posées par le navigateur ou par des bibliothèques, sans règle chez nous.
IGNOREES = {"on", "off"}


def css_et_reste():
    m = re.search(r"<style[^>]*>(.*?)</style>", HTML, re.S)
    return m.group(1), HTML.replace(m.group(0), "")


def classes_definies(css):
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return set(re.findall(r"\.(-?[A-Za-z_][\w-]*)", css))


def classes_utilisees(reste):
    out = {}

    def noter(texte, contexte):
        for c in texte.split():
            if re.fullmatch(r"-?[A-Za-z_][\w-]*", c) and not c.startswith("__"):
                out.setdefault(c, contexte)

    for m in re.finditer(r"""class=\\?["']([^"'<>{}$]*?)\\?["']""", reste):
        noter(m.group(1), "class=")
    for m in re.finditer(r"""classList\.(?:add|remove|toggle|contains)\(([^)]*)\)""", reste):
        for lit in re.findall(r"""['"]([\w-]+)['"]""", m.group(1)):
            noter(lit, "classList")
    for m in re.finditer(r"""className\s*=\s*['"]([^'"]*)['"]""", reste):
        noter(m.group(1), "className")
    return out


def routes_appelees(reste):
    return {m.group(1).rstrip("/") or "/" for m in re.finditer(r"""(?:api|fetch)\(\s*['"`](/[A-Za-z0-9_\-/]*)""", reste)}


def routes_definies():
    brut = set(re.findall(r"""@app\.(?:get|post|api_route)\("([^"]+)\"""", MAIN))
    return {re.sub(r"\{[^}]+\}", "*", r) for r in brut}


def main():
    css, reste = css_et_reste()
    defs = classes_definies(css)
    util = classes_utilisees(reste)
    sans_regle = sorted(c for c in util if c not in defs and c not in IGNOREES)
    print("Classes CSS : %d utilisées, %d règles." % (len(util), len(defs)))
    if sans_regle:
        print("Classes utilisées sans aucune règle CSS :")
        for c in sans_regle:
            print("  " + c + "  (vue dans " + util[c] + ")")
    else:
        print("Toutes les classes utilisées ont une règle.")
    defs_r, app_r = routes_definies(), routes_appelees(reste)
    absentes = sorted(r for r in app_r if r not in defs_r and not any("*" in d and r.startswith(d.split("*")[0]) for d in defs_r))
    print("Routes : %d appelées par l'interface, %d définies dans main.py." % (len(app_r), len(defs_r)))
    if absentes:
        print("Routes appelées mais absentes de main.py :")
        for r in absentes:
            print("  " + r)
    else:
        print("Toutes les routes appelées existent.")
    return 1 if (sans_regle or absentes) else 0


if __name__ == "__main__":
    sys.exit(main())
