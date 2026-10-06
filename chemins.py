"""Emplacements du code et des données (2.6.101).

Code : l'application (scripts Python, seance.py, app/index.html, logos, icônes). Fourni par l'image plus tard.
Données : tout ce que le service écrit (réglages, secrets, séances, historiques, notifications, caches, journaux),
destiné à vivre plus tard dans /config.

Ordre de lecture : KAMCINE_APP ou KAMCINE_DATA s'ils existent, sinon KAMCINE_DIR (l'installation actuelle, code et données
dans le même dossier monté), sinon /cinema. Sans nouvelle variable, les deux valent donc exactement l'ancien dossier.
L'environnement est relu à chaque appel, comme chaque module le lisait avant à son importation."""
import os


def _ancien():
    return os.environ.get("KAMCINE_DIR") or "/cinema"


def app_dir():
    """Dossier du code."""
    return os.environ.get("KAMCINE_APP") or _ancien()


def data_dir():
    """Dossier des données."""
    return os.environ.get("KAMCINE_DATA") or _ancien()


def donnee(nom):
    """Chemin d'un fichier de données."""
    return os.path.join(data_dir(), nom)


def code(*parties):
    """Chemin d'un fichier de l'application."""
    return os.path.join(app_dir(), *parties)
