"""Messages courts et journal technique indépendant des marqueurs de séance."""
import json
import os
import re
import threading
import traceback
from collections import deque
from datetime import datetime
from urllib.parse import quote, quote_plus

import chemins
BASE = chemins.data_dir()  # dossier des données (2.6.101)
CHEMIN = os.path.join(BASE, "erreurs.log")
_verrou = threading.Lock()


def masquer(texte, confidentiels=()):
    valeurs = list(confidentiels)

    def parcourir(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k.lower() not in ("url", "user", "utilisateur", "hue_ip", "nom"):
                    parcourir(v)
        elif isinstance(obj, str):
            valeurs.append(obj)

    try:
        with open(os.path.join(BASE, "secrets.json"), encoding="utf-8") as f:
            parcourir(json.load(f))
    except (OSError, ValueError):
        pass
    valeurs.extend(os.environ.get(k, "") for k in ("COMP", "AIR"))
    for v in sorted({str(v) for v in valeurs if v}, key=len, reverse=True):
        for variante in (v, quote(v, safe=""), quote_plus(v)):
            texte = texte.replace(variante, "[masqué]")
    texte = re.sub(r"(?i)(https?://)[^\s/@]+:[^\s/@]+@", r"\1[masqué]@", texte)
    texte = re.sub(r"(?i)([?&](?:api_?key|key|token|access_token|client_secret|password)=)[^&\s\"'<>]+", r"\1[masqué]", texte)
    return texte


def message(erreur, public="Action impossible.", confidentiels=()):
    """Garde la trace complète sans les secrets, jamais les variables locales."""
    detail = "".join(traceback.format_exception(type(erreur), erreur, erreur.__traceback__)) if isinstance(erreur, BaseException) else str(erreur)
    entree = "[%s] %s\n%s\n" % (datetime.now().astimezone().isoformat(timespec="seconds"), public, detail)
    entree = masquer(entree, confidentiels)
    try:
        with _verrou:
            if os.path.exists(CHEMIN) and os.path.getsize(CHEMIN) > 1024 * 1024:
                os.replace(CHEMIN, CHEMIN + ".1.log")
            fd = os.open(CHEMIN, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                os.fchmod(f.fileno(), 0o600)
                f.write(entree)
    except OSError:
        # Le journal Docker garde une copie si le volume est inaccessible.
        print(entree, flush=True)
    return public + " Consulte le journal pour plus d’informations."


def journaliser_action(source, message, genre="succes"):
    """Ajoute une action utilisateur horodatée au Journal, sans la classer comme erreur technique."""
    if genre not in ("info", "succes", "avertissement", "erreur"):
        genre = "info"
    source = re.sub(r"[^\wÀ-ÿ ._-]", "", str(source or "KamCiné"))[:40]
    texte = masquer(str(message or "").replace("\n", " ").strip())[:500]
    entree = "[%s] [action:%s:%s] %s\n" % (datetime.now().astimezone().isoformat(timespec="seconds"), source, genre, texte)
    try:
        with _verrou:
            if os.path.exists(CHEMIN) and os.path.getsize(CHEMIN) > 1024 * 1024:
                os.replace(CHEMIN, CHEMIN + ".1.log")
            fd = os.open(CHEMIN, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                os.fchmod(f.fileno(), 0o600)
                f.write(entree)
    except OSError:
        print(entree, flush=True)


def lire():
    with _verrou:
        try:
            with open(CHEMIN, encoding="utf-8") as f:
                return list(deque((l.rstrip("\n") for l in f), maxlen=400))
        except OSError:
            return []


def effacer():
    with _verrou:
        for chemin in (CHEMIN, CHEMIN + ".1.log"):
            try:
                os.remove(chemin)
            except FileNotFoundError:
                pass
