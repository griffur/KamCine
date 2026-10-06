"""Favoris de films et séries, propres à chaque compte depuis la 2.6.104 (table user_favorites de kamcine.db, comptes.py).

favoris.json, le fichier commun d'avant les comptes, n'est plus écrit : il est lu une seule fois pour donner ses favoris au
premier administrateur (comptes.rattacher_favoris_anciens) et reste en place pour un retour arrière."""
import json
import os
from urllib.parse import urlsplit

import chemins
import comptes
BASE = chemins.data_dir()  # dossier des données (2.6.101)
FICHIER = os.path.join(BASE, "favoris.json")


def anciens():
    """Favoris communs d'avant la 2.6.104 (favoris.json), en lecture seule."""
    try:
        with open(FICHIER, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return []
        return [x for x in data if isinstance(x, dict) and x.get("type") in ("movie", "tv") and isinstance(x.get("id"), int) and x["id"] > 0]
    except (OSError, ValueError):
        return []


def liste(user_id, type_=None):
    return comptes.favoris(user_id, type_ if type_ in ("movie", "tv") else None)


def est_favori(user_id, type_, id_):
    return comptes.est_favori(user_id, type_, id_)


def affiche_sure(affiche):
    """Seule une affiche https de image.tmdb.org est gardée."""
    try:
        u = urlsplit(str(affiche or ""))
        if u.scheme == "https" and u.hostname == "image.tmdb.org" and u.username is None and u.password is None and u.path.startswith("/t/p/"):
            return str(affiche)
    except ValueError:
        pass
    return None


def basculer(user_id, type_, id_, titre="", affiche=None, annee=""):
    if type_ not in ("movie", "tv") or not isinstance(id_, int) or id_ <= 0:
        raise ValueError("Film ou série invalide")
    return comptes.basculer_favori(user_id, type_, id_, titre, affiche_sure(affiche), annee)
