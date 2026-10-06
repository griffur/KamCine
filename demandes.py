"""Extraction prudente des identifiants TMDB issus des demandes Overseerr."""


def page_overseerr(reponse, page, taille):
    """Renvoie les médias TMDB uniques d'une page et indique s'il reste des demandes brutes."""
    reponse = reponse if isinstance(reponse, dict) else {}
    lignes = reponse.get("results")
    if not isinstance(lignes, list):
        lignes = reponse.get("requests")
    if not isinstance(lignes, list):
        lignes = []

    identifiants, vus = [], set()
    for demande in lignes:
        if not isinstance(demande, dict):
            continue
        media = demande.get("media") if isinstance(demande.get("media"), dict) else {}
        type_ = media.get("mediaType") or demande.get("mediaType") or demande.get("type")
        if type_ not in ("movie", "tv"):
            continue
        identifiant = media.get("tmdbId") or media.get("tmdb_id") or demande.get("tmdbId") or demande.get("mediaId")
        try:
            identifiant = int(identifiant)
        except (TypeError, ValueError, OverflowError):
            continue
        if identifiant <= 0:
            continue
        cle = (type_, identifiant)
        if cle not in vus:
            vus.add(cle)
            identifiants.append(cle)

    info = reponse.get("pageInfo") if isinstance(reponse.get("pageInfo"), dict) else {}
    total = info.get("results")
    pages = info.get("pages") or reponse.get("totalPages")
    try:
        total = int(total)
    except (TypeError, ValueError, OverflowError):
        total = None
    try:
        pages = int(pages)
    except (TypeError, ValueError, OverflowError):
        pages = None
    if total is not None:
        plus = (page - 1) * taille + len(lignes) < total
    elif pages is not None:
        plus = page < pages
    else:
        plus = len(lignes) >= taille
    return identifiants, plus
