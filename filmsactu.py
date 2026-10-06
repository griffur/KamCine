"""Bandes annonces : trouver la vidéo de la chaîne FilmsActu pour un titre, et seulement celle de cette chaîne (2.4).

Pourquoi ce module. Depuis la 2.2 rien ne part sur la TV sans une vérification de chaîne, mais la recherche ne voyait que deux choses :
le flux RSS de la chaîne (les 15 dernières vidéos, environ 8 jours) et les vidéos que TMDB connaît pour le titre (des chaînes de studios).
Un film sorti il y a plus d'une semaine n'était donc jamais trouvé. Trois sources, de la plus sûre à la moins sûre :
1. l'archive locale (filmsactu_index.json) : chaque vidéo du flux vue par le service y est gardée, le flux est relu régulièrement et la
   couverture grandit avec le temps. L'identifiant de chaîne de chaque entrée du flux est contrôlé avant de la garder.
2. la recherche officielle de l'API YouTube Data v3 restreinte à la chaîne (search.list avec channelId), si l'administrateur a mis une clé
   dans Réglages. Le champ channelId de chaque résultat est contrôlé aussi. Une vidéo trouvée est ajoutée à l'archive.
3. les vidéos TMDB du titre, gardées seulement si l'oEmbed de YouTube dit qu'elles sont de la chaîne FilmsActu.
Rien de tout cela ne contourne YouTube : ce sont un flux RSS public, l'API officielle avec une clé, et l'oEmbed officiel.

Chaque étape laisse une trace (requête, statut, résultats, chaîne et verdict de chaque candidat) : la route de diagnostic et le message
d'erreur de l'interface disent la vraie raison, jamais un simple non disponible."""
import difflib, html, json, os, re, threading, time, unicodedata
import requests

import chemins
BASE = chemins.data_dir()  # dossier des données (2.6.101)
SECRETS = os.path.join(BASE, "secrets.json")
INDEX_FICHIER = os.path.join(BASE, "filmsactu_index.json")

CHAINE_ID = "UC_i8X3p8oZNaik8X513Zn1Q"
CHAINE_NOM = "FilmsActu"
CHAINE_ADRESSE = "https://www.youtube.com/@FilmsActu"
RSS = os.environ.get("YOUTUBE_RSS", "https://www.youtube.com/feeds/videos.xml?channel_id=" + CHAINE_ID)
OEMBED = os.environ.get("OEMBED_BASE", "https://www.youtube.com/oembed")
API = os.environ.get("YOUTUBE_API_BASE", "https://www.googleapis.com/youtube/v3")
UA = {"User-Agent": "Mozilla/5.0 (KamCine)"}
INDEX_MAX = 3000

_verrou = threading.Lock()
_cache_flux = {"t": 0, "items": [], "info": None}
_cache_api = {}                 # requête -> (heure, résultats, info) : 24 h, la recherche coûte 100 unités de quota
_vie = {"dernier": 0, "fil": None}


# ---------- Comparaison de titres et de chaînes ----------
def mots(t):
    """Mots d'un titre en minuscules, sans accents ni ponctuation."""
    t = unicodedata.normalize("NFD", str(t or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.findall(r"[a-z0-9]+", t)


def norm(t):
    """Forme comparable d'un nom : sans casse, accents, espaces ni ponctuation (Films Actu, filmsactu et FilmsActu se valent)."""
    return "".join(mots(t))


def nom_est_filmsactu(nom, adresse=""):
    """Chaîne FilmsActu d'après ce que dit l'oEmbed (nom, adresse). L'adresse l'emporte sur le nom, pour qu'un compte qui se contente de
    s'appeler FilmsActu ne passe pas : l'identifiant canonique de la chaîne d'abord ; sinon le pseudo @ (ou user, ou c) de l'adresse ;
    un autre identifiant de chaîne ou un autre pseudo refuse ; sans adresse exploitable, le nom normalisé (sans casse, accents, espaces
    ni ponctuation : Films Actu, filmsactu, FilmsActu se valent)."""
    a = str(adresse or "")
    if CHAINE_ID.lower() in a.lower():
        return True
    if re.search(r"/channel/UC[\w-]{20,}", a):
        return False                                    # un autre identifiant de chaîne
    m = re.search(r"youtube\.com/(?:@|user/|c/)([^/?#]+)", a, re.I)
    if m:
        return norm(m.group(1)) == norm(CHAINE_NOM)
    return norm(nom) == norm(CHAINE_NOM)


def titre_du_film(t):
    """Le titre du film dans un titre de vidéo : ce qui précède Bande Annonce, Teaser, Trailer, Extrait, sans année ni VF ni VOST."""
    t = html.unescape(str(t or ""))
    t = re.split(r"\b(?:bande[- ]annonce|ba\b|teaser|trailer|extrait|featurette|clip)\b", t, flags=re.I)[0]
    t = re.sub(r"\([^)]*\d{4}[^)]*\)", " ", t)
    t = re.sub(r"\b(?:saison|season)\s*\d+\b", " ", t, flags=re.I)
    t = re.sub(r"\bV(?:F|OSTFR|OST|O)\b", " ", t, flags=re.I)
    return re.sub(r"\s+", " ", t).strip(" :-–—|")


def annee_de(titre_video):
    m = re.search(r"\([^)]*?(\d{4})\)", str(titre_video or ""))
    return int(m.group(1)) if m else None


ARTICLES = {"le", "la", "les", "l", "un", "une", "des", "du", "de", "the", "a", "an"}


def _sans_article(m):
    return m[1:] if len(m) > 1 and m[0] in ARTICLES else m


def correspond(titre_video, cibles, annee):
    """(vrai ou faux, raison). Le titre de la vidéo est celui du film. Strict, pour ne jamais confondre Film 2 et Film 4 ni Rocky II
    et Rocky III : les mêmes mots (sans article, casse ni accents), ou 95 % de ressemblance avec les mêmes chiffres et le même dernier
    mot, ou le titre principal avant le deux points quand l'année est la même (Tenzing, puis Tenzing : À la conquête de l'Everest)."""
    q = titre_du_film(titre_video)
    if not q:
        return False, "titre vide"
    an = annee_de(titre_video)
    if an and annee and str(annee).isdigit() and abs(an - int(annee)) > 1:
        return False, "année différente (%d contre %s)" % (an, annee)
    mq = mots(q)
    if not mq:
        return False, "titre vide"
    tete_q = mots(re.split(r"\s[:\-–—]\s|:", q)[0])
    for c in cibles:
        mc = mots(c)
        if not mc:
            continue
        if mq == mc or _sans_article(mq) == _sans_article(mc):
            return True, "même titre"
        if (len(" ".join(mc)) > 8 and mq[-1] == mc[-1] and [m for m in mq if m.isdigit()] == [m for m in mc if m.isdigit()]
                and difflib.SequenceMatcher(None, " ".join(mq), " ".join(mc)).ratio() >= 0.95):
            return True, "titre à 95 % près"
        tete_c = mots(re.split(r"\s[:\-–—]\s|:", str(c))[0])
        if an and annee and str(annee).isdigit() and abs(an - int(annee)) <= 1 and len(" ".join(tete_c)) > 3 and (
                _sans_article(tete_q) == _sans_article(mc) or _sans_article(tete_c) == _sans_article(mq)):
            if [m for m in mq if m.isdigit()] == [m for m in mc if m.isdigit()]:
                return True, "titre principal identique, même année"
    return False, "titre différent"


_MOTS_VIDEO_A_ECARTER = re.compile(r"(?:#\s*shorts?|\bshorts?\b|\breaction\b|\banalyse\b|\breview\b|\bcritique\b|\bfan[ -]?made\b|\bfan trailer\b|\bparodie\b)", re.I)


def candidate_ba(titre):
    """Écarte les formats et sujets qui ne sont pas une bande-annonce exploitable."""
    return not _MOTS_VIDEO_A_ECARTER.search(str(titre or ""))


def score_ba(titre, trailer=True, official=False):
    """Classe les bandes-annonces normales avant teasers et contenus dérivés."""
    t = str(titre or "").lower()
    return (int(trailer and bool(re.search(r"\b(trailer|bande[- ]annonce|ba officielle)\b", t))) * 4
            + int(official) * 2 - int("teaser" in t))


def ressemblance(titre_video, cibles):
    q = " ".join(mots(titre_du_film(titre_video)))
    return max([difflib.SequenceMatcher(None, q, " ".join(mots(c))).ratio() for c in cibles if mots(c)] or [0])


# ---------- Archive locale ----------
def _lire_index():
    try:
        with open(INDEX_FICHIER, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, list) else []
    except Exception:
        return []


def _ecrire_index(liste):
    tmp = INDEX_FICHIER + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(liste, f, ensure_ascii=False)
    os.replace(tmp, INDEX_FICHIER)


def index():
    with _verrou:
        return _lire_index()


def maj_index(nouveaux):
    """Ajoute les vidéos vérifiées de la chaîne (id, titre, pub) à l'archive, sans doublon, les plus récentes d'abord, 3000 au plus.
    Renvoie le nombre d'ajouts."""
    if not nouveaux:
        return 0
    with _verrou:
        cur = _lire_index()
        connus = {x["id"] for x in cur}
        ajout = [x for x in nouveaux if x.get("id") and x["id"] not in connus]
        if not ajout:
            return 0
        cur = sorted(ajout + cur, key=lambda x: x.get("pub") or "", reverse=True)[:INDEX_MAX]
        try:
            _ecrire_index(cur)
        except Exception:
            return 0
        return len(ajout)


# ---------- Sources ----------
def lire_flux(delai=8):
    """(vidéos, info). Le flux RSS de la chaîne, avec l'identifiant de chaîne de chaque entrée contrôlé. info dit ce qui s'est passé :
    adresse, statut HTTP, durée, nombre d'entrées, erreur (réseau, statut, page de consentement ou format inattendu, flux vide)."""
    info = {"source": "flux", "requete": RSS, "statut": None, "ms": None, "erreur": None, "nb": 0, "autres_chaines": 0}
    items = []
    t0 = time.time()
    try:
        r = requests.get(RSS, timeout=delai, headers=UA)
        info["statut"] = r.status_code
        info["ms"] = round((time.time() - t0) * 1000)
        if r.status_code != 200:
            info["erreur"] = "YouTube répond %d" % r.status_code
        elif "<feed" not in r.text[:400] and "<entry>" not in r.text:
            info["erreur"] = "réponse inattendue (page de consentement ou autre format que le flux)"
        else:
            for e in re.findall(r"<entry>(.*?)</entry>", r.text, re.S):
                v = re.search(r"<yt:videoId>(.+?)</yt:videoId>", e)
                c = re.search(r"<yt:channelId>(.+?)</yt:channelId>", e)
                t = re.search(r"<title>(.+?)</title>", e, re.S)
                p = re.search(r"<published>(.+?)</published>", e)
                if not (v and t and c):
                    continue
                if c.group(1) != CHAINE_ID:
                    info["autres_chaines"] += 1
                    continue
                items.append({"id": v.group(1), "titre": html.unescape(t.group(1)).strip(), "pub": p.group(1) if p else ""})
            if not items:
                info["erreur"] = "flux vide"
    except Exception as e:
        info["ms"] = round((time.time() - t0) * 1000)
        info["erreur"] = "%s : %s" % (type(e).__name__, str(e)[:90])
    info["nb"] = len(items)
    return items, info


def flux(delai=8, ttl=600):
    """Le flux, relu au plus toutes les ttl secondes, et ajouté à l'archive. (vidéos, info)."""
    with _verrou:
        c = _cache_flux
        age = time.time() - c["t"]
        # une réponse en erreur n'est gardée qu'une minute : on ne veut ni marteler YouTube ni garder une panne passagère
        if c["info"] and age < (60 if c["info"]["erreur"] else ttl):
            return c["items"], dict(c["info"], cache=True)
    items, info = lire_flux(delai)
    if items:
        info["ajouts_index"] = maj_index(items)
    with _verrou:
        _cache_flux.update(t=time.time(), items=items, info=info)
    return items, info


def cle_api():
    try:
        with open(SECRETS, encoding="utf-8") as f:
            return (json.load(f) or {}).get("youtube") or None
    except Exception:
        return None


def _erreur_api(r):
    try:
        e = (r.json().get("error") or {})
        raison = ((e.get("errors") or [{}])[0].get("reason") or e.get("status") or "")
        return "API YouTube %d : %s" % (r.status_code, raison or str(e.get("message", ""))[:80])
    except Exception:
        return "API YouTube %d" % r.status_code


def recherche_api(q, cle, delai=8):
    """(vidéos, info). Recherche officielle restreinte à la chaîne (100 unités de quota, 10 000 par jour), gardée 24 h."""
    k = norm(q)
    with _verrou:
        c = _cache_api.get(k)
    if c and time.time() - c[0] < 86400:
        return c[1], dict(c[2], cache=True)
    info = {"source": "api", "requete": "%s/search?part=snippet&channelId=%s&type=video&maxResults=10&key=***&q=%s" % (API, CHAINE_ID, q),
            "statut": None, "ms": None, "erreur": None, "nb": 0}
    items = []
    t0 = time.time()
    try:
        r = requests.get(API + "/search", params={"part": "snippet", "channelId": CHAINE_ID, "type": "video", "maxResults": 10, "q": q, "key": cle},
                         timeout=delai, headers=UA)
        info["statut"], info["ms"] = r.status_code, round((time.time() - t0) * 1000)
        if r.status_code != 200:
            info["erreur"] = _erreur_api(r)
        else:
            for it in r.json().get("items") or []:
                sn = it.get("snippet") or {}
                vid = (it.get("id") or {}).get("videoId")
                if vid:
                    items.append({"id": vid, "titre": html.unescape(sn.get("title") or ""), "chaine_id": sn.get("channelId"), "pub": sn.get("publishedAt") or ""})
    except Exception as e:
        info["ms"] = round((time.time() - t0) * 1000)
        info["erreur"] = "%s : %s" % (type(e).__name__, str(e)[:90])
    info["nb"] = len(items)
    if not info["erreur"]:
        with _verrou:
            _cache_api[k] = (time.time(), items, info)
    return items, info


def tester_cle(cle, delai=8):
    """Vérifie une clé d'API : channels.list sur la chaîne (1 unité de quota). Renvoie le titre de la chaîne, lève une erreur claire sinon."""
    r = requests.get(API + "/channels", params={"part": "snippet", "id": CHAINE_ID, "key": cle}, timeout=delai, headers=UA)
    if r.status_code != 200:
        raise PermissionError(_erreur_api(r))
    its = r.json().get("items") or []
    if not its:
        raise RuntimeError("la clé marche mais la chaîne FilmsActu n'est pas trouvée")
    return (its[0].get("snippet") or {}).get("title") or CHAINE_NOM


def chaine_de_video(cle, delai=5):
    """(nom, adresse) de la chaîne d'une vidéo d'après l'oEmbed officiel, ou None si YouTube ne répond pas."""
    try:
        r = requests.get(OEMBED, params={"url": "https://www.youtube.com/watch?v=" + cle, "format": "json"}, timeout=delai, headers=UA)
        if r.status_code != 200:
            return None
        j = r.json()
        return j.get("author_name") or "", (j.get("author_url") or "").rstrip("/")
    except Exception:
        return None


# ---------- Recherche complète, avec trace ----------
def _proches(items, cibles, n=4):
    """Les n vidéos dont le titre ressemble le plus (pour la trace : on ne liste pas 3000 titres)."""
    return sorted(items, key=lambda it: -ressemblance(it["titre"], cibles))[:n]


def chercher(cibles, annee, tmdb_videos=None, est_serie=False, ecrire_index=True):
    """Cherche la bande annonce FilmsActu d'un titre. cibles : titres possibles (français, original). tmdb_videos : fonction sans argument qui
    renvoie les vidéos TMDB [{key, type, official}]. Renvoie un dict : dispo, cle, titre_video, verification, raison, message, detail et
    trace (la liste des étapes avec candidats et verdicts). Ne met jamais en cache : c'est l'appelant qui décide."""
    an = None if est_serie else annee
    trace = []
    res = {"dispo": False, "trace": trace}

    def trouve(cle, titre_video, verification, origine):
        res.update(dispo=True, cle=cle, titre_video=titre_video, verification=verification, raison="trouvee", origine=origine,
                   message="", detail="")
        return res

    # 1. le flux (relu si besoin) et l'archive
    items_flux, info_flux = flux()
    arch = index()
    tous = {x["id"]: x for x in arch}
    tous.update({x["id"]: x for x in items_flux})
    tous = list(tous.values())
    etape = {"source": "archive et flux", "requete": info_flux["requete"], "statut": info_flux["statut"], "erreur": info_flux["erreur"],
             "nb_flux": info_flux["nb"], "nb_archive": len(tous), "candidats": []}
    trace.append(etape)
    gagnant = None
    matches = []
    verdicts = {}
    for it in tous:                                  # tout est évalué : un titre long peut correspondre à un titre court
        verdicts[it["id"]] = (correspond(it["titre"], cibles, an) if candidate_ba(it["titre"])
                               else (False, "format court/réaction non retenu"))
        if verdicts[it["id"]][0]:
            matches.append(it)
    if matches:
        # L'archive est déjà limitée à la chaîne préférée ; classer le format avant
        # de prendre le premier résultat (souvent simplement le plus récent).
        gagnant = max(matches, key=lambda x: score_ba(x["titre"], official=True))
    for it in ([gagnant] if gagnant else []) + [x for x in _proches(tous, cibles) if x is not gagnant]:
        ok, raison = verdicts[it["id"]]
        etape["candidats"].append({"id": it["id"], "titre": it["titre"], "chaine": "%s (identifiant de chaîne contrôlé)" % CHAINE_NOM,
                                   "verdict": "accepté" if ok else "refusé", "raison": raison})
    if gagnant:
        return trouve(gagnant["id"], gagnant["titre"], "Vidéo de la chaîne FilmsActu (identifiant de chaîne %s contrôlé)" % CHAINE_ID, "flux")

    # 2. la recherche officielle de l'API, si une clé est enregistrée
    cle = cle_api()
    api_ok = False
    if cle:
        # FilmsActu peut titrer une vidéo avec le titre français ou le titre original. Jusqu'ici
        # seule la première cible était recherchée : les vidéos au seul titre original échappaient
        # donc à search.list, malgré le fait que le comparateur savait déjà les reconnaître.
        requetes, requetes_vues = [], set()
        for cible in cibles or [""]:
            q = "%s bande annonce" % cible
            if norm(q) not in requetes_vues:
                requetes_vues.add(norm(q))
                requetes.append(q)
        for q in requetes:
            vids, info = recherche_api(q, cle)
            etape2 = {"source": "recherche YouTube (API officielle, restreinte à la chaîne)", "requete": info["requete"].replace(cle, "***"),
                      "statut": info["statut"], "erreur": info["erreur"], "nb": info["nb"], "candidats": []}
            trace.append(etape2)
            if info["erreur"]:
                continue
            api_ok = True
            verifies = [v for v in vids if v.get("chaine_id") == CHAINE_ID]
            etape2["autres_chaines"] = len(vids) - len(verifies)
            for it in vids:
                if it.get("chaine_id") != CHAINE_ID:
                    etape2["candidats"].append({"id": it["id"], "titre": it["titre"], "chaine": it.get("chaine_id"), "verdict": "refusé", "raison": "autre chaîne"})
                    continue
                ok, raison = (correspond(it["titre"], cibles, an) if candidate_ba(it["titre"])
                              else (False, "format court/réaction non retenu"))
                etape2["candidats"].append({"id": it["id"], "titre": it["titre"], "chaine": "%s (identifiant de chaîne contrôlé)" % CHAINE_NOM,
                                            "verdict": "accepté" if ok else "refusé", "raison": raison})
                if ok and (gagnant is None or score_ba(it["titre"], official=True) > score_ba(gagnant["titre"], official=True)):
                    gagnant = it
            if ecrire_index and verifies:
                maj_index(verifies)
            if gagnant:
                return trouve(gagnant["id"], gagnant["titre"], "Vidéo de la chaîne FilmsActu trouvée par la recherche YouTube (identifiant de chaîne %s contrôlé)" % CHAINE_ID, "api")
    else:
        trace.append({"source": "recherche YouTube (API officielle)", "erreur": "aucune clé YouTube enregistrée dans Réglages", "candidats": []})

    # 3. TMDB fournit aussi des bandes annonces pertinentes d'autres chaînes.
    # FilmsActu reste prioritaire, puis on retient le premier résultat exploitable
    # présent sur la fiche TMDB et confirmé accessible par l'oEmbed YouTube.
    autres, injoignable, essais, repli = [], False, 0, None
    etape3 = {"source": "vidéos TMDB du titre, chaîne vérifiée par l'oEmbed de YouTube", "requete": OEMBED, "candidats": []}
    trace.append(etape3)
    vids = []
    for langue in ("fr-FR", "en-US"):
        try:
            vids += (tmdb_videos(langue) if tmdb_videos else [])
        except Exception as e:
            etape3["erreur"] = "TMDB : %s" % str(e)[:80]
    vus = set()
    vids = [v for v in vids if v.get("site", "YouTube") == "YouTube" and v.get("type") in ("Trailer", "Teaser") and re.match(r"^[\w-]{6,20}$", v.get("key") or "") and candidate_ba(v.get("name"))]
    # Le tri Python est stable : conserver l'ordre éditorial TMDB entre candidats
    # de même score, sinon l'ordre lexical des clés YouTube peut perturber les vérifications.
    vids.sort(key=lambda v: -score_ba(v.get("name"), v.get("type") == "Trailer", v.get("official")))
    for v in vids:
        if v["key"] in vus:
            continue
        vus.add(v["key"])
        if essais >= 6:
            break
        essais += 1
        c = chaine_de_video(v["key"])
        if c is None:
            injoignable = True
            etape3["candidats"].append({"id": v["key"], "titre": None, "chaine": "inconnue (YouTube n'a pas répondu)", "verdict": "non vérifié", "raison": "oEmbed injoignable"})
        elif nom_est_filmsactu(c[0], c[1]):
            etape3["candidats"].append({"id": v["key"], "titre": None, "chaine": "%s (%s)" % c, "verdict": "accepté", "raison": "chaîne FilmsActu"})
            return trouve(v["key"], None, "Vidéo TMDB dont la chaîne, contrôlée par l'oEmbed de YouTube, est FilmsActu (%s)" % (c[1] or c[0]), "tmdb")
        else:
            autres.append(c[0] or c[1])
            etape3["candidats"].append({"id": v["key"], "titre": None, "chaine": "%s (%s)" % c, "verdict": "retenu en solution de repli", "raison": "vidéo TMDB accessible"})
            if repli is None:
                repli = (v, c)

    # Ne pas s'arrêter au premier studio renvoyé par TMDB : parcourir les candidats
    # vérifiables permet de préférer FilmsActu s'il apparaît après un autre résultat.
    if repli:
        v, c = repli
        return trouve(v["key"], None, "Bande annonce proposée par TMDB, vidéo YouTube accessible (%s)" % (c[1] or c[0]), "tmdb_autre")

    # verdict : la vraie raison
    flux_ko = bool(info_flux["erreur"])
    res["raison"] = "youtube_injoignable" if (flux_ko and not items_flux and not arch) or (injoignable and not autres and not tous) else ("autre_chaine" if autres else "aucun_resultat")
    derniere = max([x.get("pub") or "" for x in tous] or [""])[:10]
    if res["raison"] == "youtube_injoignable":
        res["message"] = "YouTube n'a pas répondu"
        res["detail"] = "Flux de la chaîne : %s. Réessaie dans un moment." % (info_flux["erreur"] or "aucune réponse")
    elif res["raison"] == "autre_chaine":
        res["message"] = "Trouvée seulement sur une autre chaîne"
        res["detail"] = "%s : refusée, la règle est FilmsActu uniquement." % ", ".join(sorted(set(autres))[:3])
    else:
        res["message"] = "Pas de bande annonce FilmsActu pour ce titre"
        res["detail"] = ("Parmi %d vidéos connues de la chaîne%s, aucune ne porte ce titre. " % (len(tous), (" (jusqu'au %s)" % derniere) if derniere else "")) + \
             ("La recherche YouTube sur la chaîne n'a rien trouvé." if api_ok else
             ("La recherche YouTube a échoué (%s)." % "; ".join(e["erreur"] for e in trace if e.get("source", "").startswith("recherche YouTube") and e.get("erreur")) if cle else "Ajoute une clé YouTube dans Réglages pour chercher dans toute la chaîne."))
    if flux_ko and res["raison"] != "youtube_injoignable":
        res["detail"] += " Le flux de la chaîne n'a pas répondu (%s), seule l'archive a servi." % info_flux["erreur"]
    res["injoignable"] = injoignable or flux_ko
    return res


def transitoire(res):
    """Un résultat qui ne doit pas rester en mémoire : YouTube ne répondait pas, ce n'est pas la réponse à la question."""
    return not res.get("dispo") and (res.get("raison") == "youtube_injoignable" or res.get("injoignable"))


# ---------- Rafraîchissement en arrière plan ----------
def boucle(intervalle_min, en_cours):
    """Relit le flux de la chaîne toutes les intervalle_min() minutes et l'ajoute à l'archive : c'est ce qui fait grandir la couverture sans
    clé d'API. Une requête par heure par défaut, jamais pendant une séance."""
    def travail():
        time.sleep(20)
        while True:
            try:
                if not en_cours():
                    items, info = lire_flux()
                    if items:
                        maj_index(items)
                        with _verrou:
                            _cache_flux.update(t=time.time(), items=items, info=info)
                    _vie["dernier"] = time.time()
            except Exception:
                pass
            time.sleep(max(600, int(intervalle_min()) * 60))
    if _vie["fil"] is None:
        _vie["fil"] = threading.Thread(target=travail, daemon=True)
        _vie["fil"].start()
