"""Notes personnelles KamCiné (distinctes des notes des fournisseurs externes)."""
import json
import os
import threading

import chemins

_FICHIER = os.path.join(chemins.data_dir(), "notes_personnelles.json")
_VERROU = threading.Lock()


def _lire():
    try:
        with open(_FICHIER, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def lire(user_id, media_type, tmdb_id):
    cle = "%s:%s:%s" % (int(user_id), media_type, int(tmdb_id))
    with _VERROU:
        return _lire().get(cle)


def enregistrer(user_id, media_type, tmdb_id, note):
    if media_type not in ("movie", "tv") or not 1 <= int(tmdb_id) or not 1 <= int(note) <= 5:
        raise ValueError("Note invalide.")
    cle = "%s:%s:%s" % (int(user_id), media_type, int(tmdb_id))
    with _VERROU:
        d = _lire()
        d[cle] = int(note)
        os.makedirs(os.path.dirname(_FICHIER), exist_ok=True)
        tmp = _FICHIER + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, _FICHIER)
    return int(note)
