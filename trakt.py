import json, os, threading, time
from urllib.parse import quote
import requests

import json_atomique
import chemins
BASE = chemins.data_dir()  # dossier des données (2.6.101)
SECRETS = os.path.join(BASE, "secrets.json")
API = os.environ.get("TRAKT_BASE", "https://api.trakt.tv")
VUS_FICHIER = os.path.join(BASE, "trakt_vus.json")
_cache = {"t": 0, "data": None}
_verrou = threading.Lock()


def _delai():
    """Délai d'attente des appels, un réglage (badges_delai_trakt_s)."""
    try:
        from config import charger
        return charger()["badges_delai_trakt_s"]
    except Exception:
        return 8


def _lire():
    try:
        with open(SECRETS, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _ecrire(d):
    json_atomique.ecrire(SECRETS, d)
    try:
        os.chmod(SECRETS, 0o600)
    except Exception:
        pass


def config():
    return _lire().get("trakt", {})


def sauver(**kw):
    s = _lire()
    t = s.get("trakt", {})
    t.update(kw)
    s["trakt"] = {k: v for k, v in t.items() if v is not None}
    _ecrire(s)


def configuree():
    c = config()
    return bool(c.get("client_id") and c.get("client_secret"))


def connectee():
    return bool(config().get("access_token"))


def etat():
    c = config()
    active = bool(c.get("access_token"))
    return {"configuree": bool(c.get("client_id") and c.get("client_secret")),
            "connectee": active, "client_id": c.get("client_id") if not active else None,
            "profil": c.get("profil") if active and isinstance(c.get("profil"), dict) else None}


def profil_utilisateur():
    """Lit le profil public authentifié, en ne conservant que les champs d'affichage."""
    jeton_initial = config().get("access_token")
    if not jeton_initial:
        return None
    response = _appel("GET", "/users/settings", delai=5)
    if response.status_code != 200:
        return None
    payload = response.json() if hasattr(response, "json") else {}
    user = payload.get("user") if isinstance(payload, dict) else None
    if not isinstance(user, dict):
        return None
    ids = user.get("ids") if isinstance(user.get("ids"), dict) else {}
    username = str(user.get("username") or ids.get("slug") or "").strip()[:80]
    profile = {"username": username, "name": str(user.get("name") or "").strip()[:100], "avatar": ""}
    slug = str(ids.get("slug") or username).strip()
    if slug:
        try:
            detail = _appel("GET", "/users/%s?extended=full" % quote(slug, safe=""), delai=5)
            if detail.status_code == 200:
                extra = detail.json() or {}
                extra = extra.get("user") if isinstance(extra, dict) else None
                images = extra.get("images") if isinstance(extra, dict) else None
                avatar = ((images or {}).get("avatar") or {}).get("full") if isinstance(images, dict) else ""
                if isinstance(avatar, str) and avatar.startswith("https://"):
                    profile["avatar"] = avatar[:1000]
                if isinstance(extra, dict):
                    profile["name"] = str(extra.get("name") or profile["name"]).strip()[:100]
                    profile["username"] = str(extra.get("username") or profile["username"]).strip()[:80]
        except Exception:
            pass
    if config().get("access_token") == jeton_initial:
        sauver(profil=profile)
    return profile


def _entetes(auth=True):
    c = config()
    h = {"Content-Type": "application/json", "trakt-api-version": "2", "trakt-api-key": c.get("client_id", "")}
    if auth:
        h["Authorization"] = "Bearer " + jeton()
    return h


def _appel(methode, chemin, corps=None, auth=True, delai=None):
    return requests.request(methode, API + chemin, json=corps, headers=_entetes(auth), timeout=delai or _delai())


def demarrer_appareil():
    c = config()
    r = requests.post(API + "/oauth/device/code", json={"client_id": c.get("client_id")}, timeout=8)
    if r.status_code != 200:
        raise RuntimeError("Trakt refuse l'identifiant (%d)" % r.status_code)
    d = r.json()
    sauver(device_code=d["device_code"], device_expire=time.time() + d.get("expires_in", 600))
    return {"code": d["user_code"], "url": d.get("verification_url", "https://trakt.tv/activate"),
            "intervalle": d.get("interval", 5), "duree": d.get("expires_in", 600)}


def verifier_appareil():
    c = config()
    if not c.get("device_code"):
        return {"etat": "echec", "message": "Aucune connexion en cours"}
    r = requests.post(API + "/oauth/device/token", timeout=8, json={
        "code": c["device_code"], "client_id": c.get("client_id"), "client_secret": c.get("client_secret")})
    if r.status_code == 200:
        d = r.json()
        sauver(access_token=d["access_token"], refresh_token=d.get("refresh_token"),
               expires_at=d.get("created_at", time.time()) + d.get("expires_in", 7776000), device_code=None)
        # Ne pas retarder la confirmation OAuth par les appels de profil : l'interface
        # peut afficher immédiatement l'état connecté, puis récupérer le profil à part.
        threading.Thread(target=_charger_profil_apres_connexion, daemon=True).start()
        return {"etat": "connecte"}
    if r.status_code in (400, 429):
        return {"etat": "attente"}
    messages = {404: "Code inconnu", 409: "Code déjà utilisé", 410: "Le code a expiré", 418: "Connexion refusée"}
    return {"etat": "echec", "message": messages.get(r.status_code, "Erreur Trakt %d" % r.status_code)}


def _charger_profil_apres_connexion():
    try:
        profil_utilisateur()
    except Exception:
        # Le profil est secondaire : la connexion reste valide même si Trakt est indisponible.
        pass


def jeton():
    c = config()
    if not c.get("access_token"):
        raise RuntimeError("Trakt n'est pas connecté")
    if c.get("expires_at", 0) - time.time() < 3600 and c.get("refresh_token"):
        r = requests.post(API + "/oauth/token", timeout=8, json={
            "refresh_token": c["refresh_token"], "client_id": c.get("client_id"), "client_secret": c.get("client_secret"),
            "redirect_uri": "urn:ietf:wg:oauth:2.0:oob", "grant_type": "refresh_token"})
        if r.status_code == 200:
            d = r.json()
            sauver(access_token=d["access_token"], refresh_token=d.get("refresh_token"),
                   expires_at=d.get("created_at", time.time()) + d.get("expires_in", 7776000))
            return d["access_token"]
    return config()["access_token"]


def deconnecter():
    s = _lire()
    t = s.get("trakt", {})
    for k in ("access_token", "refresh_token", "expires_at", "device_code", "device_expire", "profil"):
        t.pop(k, None)
    s["trakt"] = t
    _ecrire(s)
    _oublier_vus()


def oublier():
    s = _lire()
    s.pop("trakt", None)
    _ecrire(s)
    _oublier_vus()


def _oublier_vus():
    _cache.update(t=0, data=None)
    try:
        os.remove(VUS_FICHIER)
    except OSError:
        pass


def _sauver_vus(data):
    tmp = VUS_FICHIER + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"t": _cache["t"], "films": sorted(data["films"]), "series": sorted(data["series"]),
                       "episodes": sorted(list(x) for x in data["episodes"])}, f)
        os.replace(tmp, VUS_FICHIER)
    except Exception:
        pass


def _charger_vus():
    try:
        with open(VUS_FICHIER, encoding="utf-8") as f:
            brut = json.load(f)
        data = {"films": set(brut["films"]), "series": set(brut["series"]), "episodes": {tuple(x) for x in brut["episodes"]}}
    except Exception:
        return
    with _verrou:
        if _cache["data"] is None:
            _cache.update(t=brut.get("t", 0), data=data)


def vus_rapide():
    """Ce qui est vu d'après Trakt, sans jamais attendre le réseau : (ensembles, prêt). Mémoire d'abord, puis le disque.
    Tant que rien n'a été lu, prêt est faux et les ensembles sont vides."""
    if _cache["data"] is None:
        _charger_vus()
    d = _cache["data"]
    if d is None:
        return {"films": set(), "series": set(), "episodes": set()}, False
    return d, True


def _pages_vus(chemin, limite):
    """Lit toutes les pages d'un endpoint watched, sans supposer que Trakt renvoie encore tout d'un coup."""
    page, pages = 1, None
    resultat, signatures = [], set()
    while pages is None or page <= pages:
        separateur = "&" if "?" in chemin else "?"
        r = _appel("GET", "%s%spage=%d&limit=%d" % (chemin, separateur, page, limite))
        r.raise_for_status()
        lot = r.json()
        if not isinstance(lot, list):
            raise RuntimeError("Réponse inattendue de Trakt pour l'historique vu")
        entetes = getattr(r, "headers", {}) or {}
        nb_pages = next((v for k, v in entetes.items() if k.lower() == "x-pagination-page-count"), None)
        try:
            pages = max(1, int(nb_pages)) if nb_pages is not None else None
        except (TypeError, ValueError):
            pages = None
        if not lot:
            break
        # Certaines versions de l'API ont déjà renvoyé la dernière page répétée hors limites.
        # Cette garde empêche une boucle si les en-têtes de pagination sont absents ou erronés.
        signature = json.dumps(lot, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        if signature in signatures:
            if pages is not None:
                raise RuntimeError("Trakt a répété une page de l'historique vu")
            break
        signatures.add(signature)
        resultat.extend(lot)
        if pages is None and len(lot) < limite:
            break
        page += 1
    return resultat


def rafraichir_vus():
    """Relit les titres vus sur Trakt (appelé en arrière plan). En cas d'échec, l'ancienne version est gardée."""
    if not connectee():
        return
    films, series, episodes = set(), set(), set()
    for x in _pages_vus("/sync/watched/movies", 250):
        t = (x.get("movie") or {}).get("ids", {}).get("tmdb")
        if t:
            films.add(int(t))
    # Le détail des saisons/épisodes est désormais opt-in. Trakt plafonne cette réponse plus
    # lourde à 100 éléments par page ; le endpoint conserve les numéros TMDB/saison/épisode utiles.
    for x in _pages_vus("/sync/watched/shows?extended=progress", 100):
        t = (x.get("show") or {}).get("ids", {}).get("tmdb")
        if not t:
            continue
        series.add(int(t))
        for s in x.get("seasons", []):
            for e in s.get("episodes", []):
                episodes.add((int(t), int(s["number"]), int(e["number"])))
    data = {"films": films, "series": series, "episodes": episodes}
    with _verrou:
        _cache.update(t=time.time(), data=data)
    _sauver_vus(data)


def vus():
    """Version qui attend, gardée pour d'éventuels appels directs : mémoire si elle est récente, sinon relecture."""
    d, pret = vus_rapide()
    if pret and time.time() - _cache["t"] < 600:
        return d
    try:
        rafraichir_vus()
    except Exception:
        pass
    return vus_rapide()[0]


def stats():
    if not connectee():
        return None
    try:
        r = _appel("GET", "/users/me/stats")
        if r.status_code != 200:
            return None
        d = r.json()
        return {"films": (d.get("movies") or {}).get("watched", 0),
                "episodes": (d.get("episodes") or {}).get("watched", 0),
                "series": (d.get("shows") or {}).get("watched", 0),
                "minutes": ((d.get("movies") or {}).get("minutes", 0) or 0) + ((d.get("episodes") or {}).get("minutes", 0) or 0)}
    except Exception:
        return None


def recents():
    """Derniers films et dernières séries vus, tels que Trakt les connaît."""
    if not connectee():
        return {"films": [], "series": []}
    films, series, deja = [], [], set()
    try:
        for x in _appel("GET", "/sync/history/movies?limit=12").json():
            m = x.get("movie") or {}
            t = (m.get("ids") or {}).get("tmdb")
            if t:
                films.append({"type": "movie", "id": int(t), "titre": m.get("title") or "", "t": x.get("watched_at")})
        for x in _appel("GET", "/sync/history/episodes?limit=60").json():
            sh, ep = x.get("show") or {}, x.get("episode") or {}
            t = (sh.get("ids") or {}).get("tmdb")
            if t and int(t) not in deja and len(series) < 12:
                deja.add(int(t))
                series.append({"type": "tv", "id": int(t), "titre": sh.get("title") or "", "saison": ep.get("season"),
                               "episode": ep.get("number"), "t": x.get("watched_at")})
    except Exception:
        pass
    return {"films": films, "series": series}


def par_mois(n=6):
    """Nombre de titres vus par mois, sur les n derniers mois."""
    if not connectee():
        return None
    maintenant = time.localtime()
    dm, da = maintenant.tm_mon - (n - 1), maintenant.tm_year
    while dm <= 0:
        dm += 12
        da -= 1
    try:
        r = _appel("GET", "/sync/history?limit=1000&start_at=%04d-%02d-01T00:00:00.000Z" % (da, dm))
        items = r.json() if r.status_code == 200 else []
    except Exception:
        return None
    mois = []
    for i in range(n - 1, -1, -1):
        m, a = maintenant.tm_mon - i, maintenant.tm_year
        while m <= 0:
            m += 12
            a -= 1
        mois.append({"annee": a, "mois": m, "n": 0})
    index = {(x["annee"], x["mois"]): x for x in mois}
    for x in items:
        t = (x.get("watched_at") or "")[:7]
        if len(t) == 7:
            k = (int(t[:4]), int(t[5:7]))
            if k in index:
                index[k]["n"] += 1
    return mois


# ---------- Écriture dans l'historique (2.5) : marquer vu ou non vu depuis KamCiné ----------
# Trakt : POST /sync/history ajoute, POST /sync/history/remove retire, avec le même corps (films ou séries, identifiants TMDB).
# Un épisode s'écrit sous sa série et sa saison. Une série donnée sans saisons touche tous ses épisodes. Retirer une entrée
# enlève toutes ses lectures. Les jetons Trakt n'ont pas de portée à choisir : celui de la connexion appareil donne aussi
# l'écriture, ce que seul un vrai compte prouve, donc un refus (401, 403) est traduit en message clair.
def _corps_historique(type_, id_, saison=None, episode=None, ajout=True):
    maintenant = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    ids = {"tmdb": int(id_)}
    if type_ == "movie":
        f = {"ids": ids}
        if ajout:
            f["watched_at"] = maintenant
        return {"movies": [f]}
    s = {"ids": ids}
    if saison and episode:
        e = {"number": int(episode)}
        if ajout:
            e["watched_at"] = maintenant
        s["seasons"] = [{"number": int(saison), "episodes": [e]}]
    elif ajout:
        s["watched_at"] = maintenant
    return {"shows": [s]}


def ecrire_vu(type_, id_, saison=None, episode=None, vu=True):
    """Écrit ou retire une entrée dans l'historique Trakt. Ne renvoie rien si tout va bien, lève RuntimeError avec un message lisible
    sinon (Trakt ne répond pas, jeton refusé, titre inconnu de Trakt)."""
    chemin = "/sync/history" if vu else "/sync/history/remove"
    try:
        r = _appel("POST", chemin, _corps_historique(type_, id_, saison, episode, vu), delai=10)
    except requests.exceptions.Timeout:
        raise RuntimeError("Trakt ne répond pas")
    except requests.exceptions.RequestException:
        raise RuntimeError("Trakt est injoignable")
    if r.status_code in (401, 403):
        raise RuntimeError("Trakt a refusé l'écriture : reconnecte Trakt dans Réglages, Services connectés")
    if r.status_code == 429:
        raise RuntimeError("Trakt limite les demandes, réessaie dans une minute")
    if r.status_code >= 400:
        raise RuntimeError("Erreur Trakt %d" % r.status_code)
    try:
        j = r.json()
    except ValueError:
        return
    nf = j.get("not_found") or {}
    if any(nf.get(k) for k in ("movies", "shows", "seasons", "episodes", "ids")) and vu:
        raise RuntimeError("Trakt ne connaît pas ce titre")


def ecrire_episodes_vus(id_, episodes, vu=True):
    """Écrit plusieurs épisodes d'une même série en une seule requête Trakt."""
    maintenant = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    saisons = {}
    for saison, episode in sorted(set((int(s), int(e)) for s, e in episodes)):
        entree = {"number": episode}
        if vu:
            entree["watched_at"] = maintenant
        saisons.setdefault(saison, []).append(entree)
    if not saisons:
        return
    corps = {"shows": [{"ids": {"tmdb": int(id_)}, "seasons": [
        {"number": saison, "episodes": eps} for saison, eps in sorted(saisons.items())]}]}
    chemin = "/sync/history" if vu else "/sync/history/remove"
    try:
        r = _appel("POST", chemin, corps, delai=10)
    except requests.exceptions.Timeout:
        raise RuntimeError("Trakt ne répond pas")
    except requests.exceptions.RequestException:
        raise RuntimeError("Trakt est injoignable")
    if r.status_code in (401, 403):
        raise RuntimeError("Trakt a refusé l'écriture : reconnecte Trakt dans Réglages, Services connectés")
    if r.status_code == 429:
        raise RuntimeError("Trakt limite les demandes, réessaie dans une minute")
    if r.status_code >= 400:
        raise RuntimeError("Erreur Trakt %d" % r.status_code)
    try:
        nf = r.json().get("not_found") or {}
    except ValueError:
        return
    if any(nf.get(k) for k in ("movies", "shows", "seasons", "episodes", "ids")) and vu:
        raise RuntimeError("Trakt ne connaît pas ce titre")


def memoire_vu(type_, id_, saison=None, episode=None, vu=True):
    """Met à jour tout de suite les ensembles gardés en mémoire (et sur le disque), sans attendre la prochaine lecture de Trakt : la fiche
    et les jaquettes réagissent aussitôt. Une série sans saison ni épisode touche la série entière."""
    with _verrou:
        d = _cache["data"]
        if d is None:
            return
        id_ = int(id_)
        if type_ == "movie":
            (d["films"].add if vu else d["films"].discard)(id_)
        elif saison and episode:
            if vu:
                d["series"].add(id_)
                d["episodes"].add((id_, int(saison), int(episode)))
            else:
                d["episodes"].discard((id_, int(saison), int(episode)))
                if not any(x[0] == id_ for x in d["episodes"]):
                    d["series"].discard(id_)
        elif not vu:
            d["series"].discard(id_)
            d["episodes"].difference_update({x for x in d["episodes"] if x[0] == id_})
        else:
            d["series"].add(id_)
        copie = {"films": set(d["films"]), "series": set(d["series"]), "episodes": set(d["episodes"])}
    _sauver_vus(copie)
