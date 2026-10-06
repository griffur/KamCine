"""Remplacement atomique et privé des fichiers JSON persistants."""
import json
import os
import tempfile


def ecrire(chemin, donnees, **options):
    dossier = os.path.dirname(os.path.abspath(chemin))
    os.makedirs(dossier, exist_ok=True)
    fd, temporaire = tempfile.mkstemp(prefix=".kamcine-json-", dir=dossier)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fichier:
            json.dump(donnees, fichier, ensure_ascii=False, **options)
            fichier.flush()
            os.fsync(fichier.fileno())
        os.replace(temporaire, chemin)
        fd_dossier = os.open(dossier, os.O_RDONLY)
        try:
            os.fsync(fd_dossier)
        finally:
            os.close(fd_dossier)
    finally:
        if os.path.exists(temporaire):
            os.unlink(temporaire)
