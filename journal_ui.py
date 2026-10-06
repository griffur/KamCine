"""Normalisation non destructive des journaux historiques et techniques pour l'interface."""
import re
from datetime import datetime


_DATE = re.compile(r"^\[(\d{4}-\d\d-\d\dT[^\]]+)\]\s*(.*)$")
_ACTION = re.compile(r"^\[action:([^:\]]+):(info|succes|avertissement|erreur)\]\s*(.*)$", re.I)
_TYPE_SOURCE = (
    ("Transmission", ("transmission", "torrent")),
    ("Radarr", ("radarr",)),
    ("Sonarr", ("sonarr",)),
    ("Overseerr", ("overseerr",)),
    ("Trakt", ("trakt",)),
    ("FilmsActu", ("filmsactu",)),
    ("Apple TV", ("apple tv", "appletv", "infuse", "pyatv")),
    ("Hue", ("hue", "lumière", "lumières")),
    ("Denon", ("denon",)),
    ("TMDB", ("tmdb",)),
)
_RELEASE = re.compile(r"^(?:connexion impossible|identifiants refusés|erreur|échec|impossible|refusé|refusée|indisponible|injoignable)", re.I)
_SUCCES = re.compile(r"(?:séance terminée|connecté|connexion réussie|répond\b|retiré(?:e|s)?\b|envoyé(?:e|s)?\b|mis à jour|réussi(?:e|s)?\b)", re.I)
_AVERTISSEMENT = re.compile(r"(?:attention|avertissement|annulé(?:e|s)?\b|manqué(?:e|s)?\b|ignoré(?:e|s)?\b)", re.I)


def _timestamp(valeur):
    if isinstance(valeur, (int, float)):
        return float(valeur)
    if not valeur:
        return None
    try:
        return datetime.fromisoformat(str(valeur)).timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


def _source(texte):
    bas = str(texte or "").casefold()
    return next((nom for nom, mots in _TYPE_SOURCE if any(mot in bas for mot in mots)), "KamCiné")


def _type(message, detail="", force_erreur=False):
    texte = str(message or "") + " " + str(detail or "")
    if force_erreur or _RELEASE.search(str(message or "")) or "traceback" in str(detail or "").casefold():
        return "erreur"
    if _AVERTISSEMENT.search(texte):
        return "avertissement"
    if _SUCCES.search(texte):
        return "succes"
    return "info"


def _evenement(message, horodatage=None, detail="", seance=None, source=None, genre=None, index=0):
    message = str(message or "").strip()
    texte = message + " " + str(detail or "")
    match = re.match(r"^(?:Film|Série)\s*:\s*(.+)$", message, re.I)
    titre = seance
    if match:
        titre = match.group(1).split(" | ", 1)[0].strip()
    torrent = None
    m = re.search(r"fichiers conservés\s*:\s*(.+)$", message, re.I)
    if m:
        torrent = m.group(1).strip()
    return {"date": _timestamp(horodatage), "message": message, "detail": str(detail or "").strip(),
            "source": source or _source(texte), "type": genre or _type(message, detail, source is not None and genre == "erreur"),
            "titre": titre or None, "torrent": torrent, "contexte": "Séance" if titre else None, "_ordre": index}


def _techniques(lignes, debut_ordre):
    resultats, courant = [], None
    for ligne in lignes or []:
        ligne = str(ligne)
        m = _DATE.match(ligne)
        if m:
            action = _ACTION.match(m.group(2))
            if action:
                courant = _evenement(action.group(3), m.group(1), source=action.group(1), genre=action.group(2).lower(), index=debut_ordre + len(resultats))
            else:
                courant = _evenement(m.group(2), m.group(1), source=_source(m.group(2)), genre="erreur", index=debut_ordre + len(resultats))
            resultats.append(courant)
        elif courant:
            courant["detail"] = (courant["detail"] + "\n" + ligne).strip()
        else:
            courant = _evenement("Détail technique d'une entrée historique", detail=ligne, source=_source(ligne), genre="erreur", index=debut_ordre + len(resultats))
            resultats.append(courant)
    for evenement in resultats:
        if evenement["source"] == "KamCiné":
            evenement["source"] = _source(evenement["message"] + " " + evenement["detail"])
    return resultats


def evenements(seance, erreurs):
    """Convertit les lignes en objets UI, sans modifier ni réécrire les sources texte historiques."""
    resultats, titre, ordre = [], None, 0
    for horodatage, ligne in (seance or [])[-400:]:
        ligne = str(ligne or "").strip()
        if not ligne:
            continue
        if ligne.startswith("Film :") or ligne.startswith("Série :"):
            titre = ligne.split(":", 1)[1].split(" | ", 1)[0].strip()
        resultats.append(_evenement(ligne, horodatage, seance=titre, index=ordre))
        ordre += 1
    resultats.extend(_techniques(erreurs, ordre))
    resultats.sort(key=lambda e: (e["date"] is not None, e["date"] if e["date"] is not None else e["_ordre"]), reverse=True)
    sources = sorted({e["source"] for e in resultats}, key=str.casefold)
    for e in resultats:
        e.pop("_ordre", None)
    return resultats, sources
