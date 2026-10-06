"""Identification prudente du média en lecture dans Infuse."""
import re
import unicodedata

INFUSE_ID = "com.firecore.infuse"
# Types de média pyatv d'une vidéo Infuse : un épisode peut être publié comme TV plutôt que Video.
MEDIAS_VIDEO = (None, "", "Unknown", "Video", "TV")
# Titre au format série : « Reacher S01E03 », « Reacher - S1E3 - Cuillère en argent », « Reacher.S01.E03 », et le format
# réellement publié par Infuse (lot 2.6.85, relevé par /appletv/rattachement) : « Reacher - S1 ∙ E4 - Dommages collatéraux ».
# Entre la saison et l'épisode, seuls des séparateurs sûrs sont admis : espace, point, tiret bas, deux points, tirets, et les
# points médians ∙ (U+2219), · (U+00B7), • (U+2022), ⋅ (U+22C5). Rien d'autre (pas de lettre, pas de chiffre).
SEPARATEURS_EPISODE = "\\s._:–—\\-\u2219\u00b7\u2022\u22c5"
SERIE_RE = re.compile(r"^(?P<nom>.+?)[\s._–—-]*\bS(?P<s>\d{1,3})[" + SEPARATEURS_EPISODE + r"]*E(?P<e>\d{1,3})(?!\d)(?:[\s._:–—-]+(?P<ep>.*))?$", re.I)


def identite_observee(p):
    """Lot 2.6.83 : l'unique lecture de l'identité d'une observation Infuse, partagée par /film, le rattachement de la séance
    et les contrôleurs. Les champs série de pyatv (serie_nom, saison_n, episode_n) et un titre au format série donnent une
    série ; l'épisode n'est certain qu'avec sa saison et son numéro. Sans aucun indice de série, le type reste inconnu (None) :
    un titre seul n'est jamais supposé être un film, c'est la recherche TMDB qui le dira."""
    titre = (p.get("titre") or "").strip() if p else ""
    nom, saison, episode, ep = (p.get("serie_nom") or "").strip() if p else "", None, None, ""
    if p:
        saison, episode = p.get("saison_n") or None, p.get("episode_n") or None
    m = SERIE_RE.match(titre)
    if m:
        nom = nom or m.group("nom").strip(" -–—._")
        saison, episode = saison or int(m.group("s")), episode or int(m.group("e"))
        ep = (m.group("ep") or "").strip(" -–—._")
    elif nom and titre and normaliser(titre) != normaliser(nom):
        ep = titre
    if nom:
        return {"type": "tv", "nom": nom, "saison": saison, "episode": episode, "ep_titre": ep,
                "episode_certain": bool(saison and episode)}
    return {"type": None, "nom": titre, "saison": None, "episode": None, "ep_titre": "", "episode_certain": False}


def normaliser(titre):
    texte = unicodedata.normalize("NFKD", str(titre or ""))
    return "".join(c.lower() for c in texte if c.isalnum() and not unicodedata.combining(c))


def titre_normalise(titre, annee=None):
    titre = re.sub(r"\.(?:mkv|mp4|m4v|avi)$", "", titre or "", flags=re.I)
    titre = re.sub(r"\b(?:2160p|1080p|720p|4k|hdr10?|dv)\b", "", titre, flags=re.I)
    if annee:
        titre = re.sub(r"[\s._(\[]*" + re.escape(str(annee)) + r"[\s._)\]]*$", "", titre)
    return normaliser(titre)


# Même fichier (lot 2.6.84) : durée publiée égale à celle du fichier de la séance, à cette tolérance près.
TOLERANCE_FICHIER_S = 2


def correspond(p, cible):
    """Un titre exact normalisé, et le bon épisode pour une série. La durée seule ne prouve rien."""
    return explique_correspondance(p, cible)[0]


def explique_correspondance(p, cible):
    """(accepté, raison) : la décision de correspond et sa raison exacte, pour le journal de rattachement (lot 2.6.84).
    Preuves acceptées, de la plus forte à la plus faible : identité TMDB de l'observation (même média, même épisode) ;
    titre au format série ou champs pyatv avec le bon épisode ; pour un épisode dont Infuse ne publie que le nom de la série,
    le même fichier (même nom et durée égale à celle déjà relevée pour la séance, cible["total_fichier"]). Un épisode publié
    avec un autre numéro est toujours refusé, même si la durée coïncide."""
    if not p or not cible:
        return False, "aucune observation ou aucune séance"
    if p.get("veille"):
        return False, "Apple TV en veille"
    if p.get("app") not in (None, "", INFUSE_ID):
        return False, "autre application : %s" % p.get("app")
    if p.get("stale"):
        return False, "état écarté : %s" % (p.get("infuse_ecartee") or p.get("stale_reason") or "périmé")
    if p.get("etat") not in ("Playing", "Paused"):
        return False, "état %s" % p.get("etat")
    if p.get("media") not in MEDIAS_VIDEO:
        return False, "type de média %s" % p.get("media")
    if p.get("etat") == "Paused" and not (p.get("pos") or p.get("total")):
        return False, "pause sans position ni durée"
    meta = cible.get("meta") or cible
    type_cible = cible.get("type", meta.get("type"))
    attendus = (cible.get("saison") or meta.get("saison"), cible.get("episode") or meta.get("episode"))
    ident = p.get("identite")
    if ident and ident.get("id") and ident.get("id") == (cible.get("id") or meta.get("id")) and ident.get("type") == type_cible:
        if type_cible != "tv":
            return True, "identité TMDB : même film"
        if all(attendus) and (ident.get("saison"), ident.get("episode")) == attendus:
            return True, "identité TMDB : même épisode"
    noms = {titre_normalise(meta.get(k), meta.get("annee")) for k in ("titre", "titre_original")}
    noms.discard("")
    if not noms:
        return False, "séance sans titre"
    info = identite_observee(p)
    if not info["nom"] and not (p.get("titre") or "").strip():
        # Lot 2.6.90 (journal matériel : « nom différent de la série : (vide) ») : un relevé sans titre ni série ne fournit pas
        # d'identité, il ne contredit rien. Seul le même fichier (durée égale à celle relevée pour la séance) le rattache.
        total_fichier = cible.get("total_fichier") or meta.get("total_fichier") or 0
        if not total_fichier:
            return False, "titre absent : identité non fournie, durée du fichier de la séance inconnue"
        if not p.get("total"):
            return False, "titre absent : identité non fournie, durée pas encore publiée"
        if abs(p["total"] - total_fichier) > TOLERANCE_FICHIER_S:
            return False, "titre absent et autre durée : %ss au lieu de %ss" % (p["total"], total_fichier)
        return True, "même fichier : titre absent, durée identique"
    if type_cible == "tv":
        if not all(attendus):
            return False, "séance sans épisode"
        if info["episode_certain"]:
            if (info["saison"], info["episode"]) != attendus:
                return False, "autre épisode publié : S%sE%s" % (info["saison"], info["episode"])
            if titre_normalise(info["nom"], meta.get("annee")) in noms:
                return True, "titre ou champs pyatv : même épisode"
            return False, "même épisode mais autre nom de série : %s" % info["nom"]
        if titre_normalise(info["nom"], meta.get("annee")) not in noms:
            return False, "nom différent de la série : %s" % (info["nom"] or "(vide)")
        total_fichier = cible.get("total_fichier") or meta.get("total_fichier") or 0
        if not total_fichier:
            return False, "épisode non publié et durée du fichier de la séance inconnue"
        if not p.get("total"):
            return False, "épisode non publié et durée pas encore publiée"
        if abs(p["total"] - total_fichier) > TOLERANCE_FICHIER_S:
            return False, "épisode non publié et autre durée : %ss au lieu de %ss" % (p["total"], total_fichier)
        return True, "même fichier : nom de la série et durée identique"
    if info["type"] == "tv":
        return False, "épisode de série, la séance est un film"
    if titre_normalise(p.get("titre") or "", meta.get("annee")) in noms:
        return True, "titre du film"
    return False, "titre différent : %s" % (p.get("titre") or "(vide)")

def meme_serie_autre_episode(p, meta):
    """Lot 2.6.89 : une lecture Infuse réelle de la même série que la séance, mais pas son épisode (ou pas encore prouvé comme
    tel) : l'épisode suivant qu'Infuse enchaîne, jamais une interruption. Preuve de la série : l'identité TMDB de l'observation,
    ou le nom publié par Infuse égal au titre de la série."""
    if not p or not meta or meta.get("type") != "tv" or p.get("stale") or p.get("veille"):
        return False
    if p.get("app") not in (None, "", INFUSE_ID) or p.get("etat") not in ("Playing", "Paused"):
        return False
    if correspond(p, meta):
        return False
    ident = p.get("identite") or {}
    if ident.get("id"):
        return ident.get("type") == "tv" and ident.get("id") == meta.get("id")
    # Comme correspond : un titre égal au nom de la série suffit (Infuse ne publie parfois que lui), sans champ série.
    info = identite_observee(p)
    noms = {titre_normalise(meta.get(k), meta.get("annee")) for k in ("titre", "titre_original")}
    noms.discard("")
    return bool(info["nom"]) and titre_normalise(info["nom"], meta.get("annee")) in noms


def depuis_cli(sortie):
    """Accepte une position seule, une durée seule ou la paire affichée par atvremote."""
    def champ(motif):
        m = re.search(motif, sortie, re.I | re.M)
        return m.group(1).strip() if m else ""
    position = re.search(r"Position:\s*(\d+)(?:/(\d+))?s", sortie, re.I)
    app = re.search(r"App:\s*(.*?)\s*\(([^)]+)\)", sortie)
    return {"etat": champ(r"Device state:\s*(\w+)"), "titre": champ(r"^\s*Title:\s*(.*)$"),
            "media": champ(r"Media type:\s*(\w+)"),
            "pos": int(position.group(1)) if position else 0,
            "total": int((position.group(2) if position else None) or champ(r"Total time:\s*(\d+)") or 0),
            "app": app.group(2) if app else None, "app_nom": app.group(1) if app else None,
            "veille": bool(re.search(r"PowerState\.Off|Power state: Off", sortie, re.I)),
            "serie_nom": champ(r"Series(?: Name)?:\s*(.*)"),
            "saison_n": int(champ(r"Season(?: number)?:\s*(\d+)") or 0),
            "episode_n": int(champ(r"Episode(?: number)?:\s*(\d+)") or 0)}
