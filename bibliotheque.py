"""Ce qui est réellement dans la bibliothèque : Radarr et Sonarr (les fichiers sur le NAS) et Overseerr (les demandes).

Aucune demande d'un utilisateur n'attend le réseau. Les listes sont relues en arrière plan par un rafraîchisseur,
gardées en mémoire (index léger) et sauvegardées sur le disque (badges_index.json) : après un redémarrage, les badges
sont disponibles tout de suite avec la dernière version connue, le temps que la lecture en arrière plan se termine.
Les délais, la durée de vie des listes et la source (Radarr et Sonarr, Overseerr, ou les deux) sont des réglages."""
import datetime as dt
import json, os, re, threading, time, unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed, Future
from urllib.parse import urlsplit
import requests
from config import charger

import json_atomique
import chemins
BASE = chemins.data_dir()  # dossier des données (2.6.101)
SECRETS = os.path.join(BASE, "secrets.json")
INDEX_FICHIER = os.path.join(BASE, "badges_index.json")
OVERSEERR_LOCAL = os.environ.get("OVERSEERR_LOCAL", "http://127.0.0.1:5055")
NOMS = ["radarr_hd", "radarr_uhd", "sonarr_hd", "sonarr_uhd"]
TITRES = {"radarr_hd": "Radarr HD", "radarr_uhd": "Radarr 4K", "sonarr_hd": "Sonarr HD", "sonarr_uhd": "Sonarr 4K"}
# Aucune adresse par défaut (2.6.108) : 127.0.0.1 n'est pas une adresse universelle dans Docker (autre conteneur, adresse
# locale, proxy HTTPS). L'adresse saisie à la configuration est la seule source de vérité.
# du moins bon au meilleur : sert à résumer HD et 4K en un seul état
ORDRE = ["inconnu", "absent", "non_suivi", "refuse", "echec", "bloque", "a_venir", "attente", "recherche",
         "telechargement", "partiel", "disponible"]

# Ce qu'on garde de chaque ligne de la file de téléchargement de Radarr et de Sonarr. downloadId est l'identifiant chez le client de
# téléchargement : pour un torrent, c'est le hash du torrent, ce qui permet de relier un titre à son torrent sans deviner par le nom.
CLES_FILE = ("size", "sizeleft", "timeleft", "status", "seasonNumber", "downloadId", "protocol", "movieId", "seriesId",
             "episodeId", "episodeIds", "trackedDownloadState", "trackedDownloadStatus", "downloadState")

_cache = {}                # statuts calculés (20 s) et fiches Overseerr (30 s)
_index = {}                # nom d'instance -> {"t": heure de lecture, "data": index léger, "origine": "disque" ou "reseau"}
_ovbulk = {"t": 0, "data": None, "origine": None}    # tout ce qu'Overseerr suit : (type, id) -> infos réduites
_durees, _erreurs = {}, {}
_episodes_cache = {}
_episodes_cache_lock = threading.Lock()
_verrou = threading.Lock()
_verrou_sauvegarde = threading.Lock()
_verrou_cible = threading.Lock()
_cible_en_cours = {}
_cible_cache = {}
_reveils = []              # un événement par boucle d'arrière plan, pour les réveiller toutes d'un coup
_vie = {"fil": None, "cycles": 0, "en_cours": False, "debut": None, "fin": None, "demande": 0, "disque": False}
_historique_releases = {"t": 0.0, "data": {}}
_historique_releases_verrou = threading.Lock()
_historique_releases_fil = None
_historique_releases_epoch = 0


def _reglages():
    return charger()


def _secrets():
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
    return _secrets().get("arr", {})


def etat_config():
    c = config()
    return [{"nom": n, "titre": TITRES[n], "configuree": bool((c.get(n) or {}).get("cle")),
             "url": (c.get(n) or {}).get("url") or ""} for n in NOMS]


def tester(url, cle):
    r = requests.get(url.rstrip("/") + "/api/v3/system/status", headers={"X-Api-Key": cle}, timeout=6)
    if r.status_code in (401, 403):
        raise PermissionError("clé refusée")
    r.raise_for_status()
    return r.json().get("version", "")


def sauver(nom, url, cle):
    s = _secrets()
    a = s.get("arr", {})
    a[nom] = {"url": url.rstrip("/"), "cle": cle}
    s["arr"] = a
    _ecrire(s)
    vider(True)


def retirer(nom):
    s = _secrets()
    s.get("arr", {}).pop(nom, None)
    _ecrire(s)
    with _verrou:
        _index.pop(nom, None)
    vider(True)


def vider(listes=False):
    """Oublie les statuts calculés. Avec listes=True, on garde les listes mais on demande une relecture tout de suite,
    et on oublie celles d'une instance ou d'un service qui n'est plus configuré."""
    global _historique_releases_fil, _historique_releases_epoch
    with _verrou:
        _cache.clear()
        with _episodes_cache_lock:
            _episodes_cache.clear()
        if listes:
            configurees = {n for n in NOMS if (config().get(n) or {}).get("cle")}
            for nom in list(_index):
                if nom not in configurees:
                    _index.pop(nom)
            if not ov_cle():
                _ovbulk.update(t=0, data=None, origine=None)
    if listes:
        with _historique_releases_verrou:
            _historique_releases_epoch += 1
            _historique_releases.update(t=0.0, data={})
    if listes:
        rafraichir_bientot()


def _get(nom, chemin, params=None, delai=8):
    c = config().get(nom) or {}
    if not c.get("cle"):
        raise RuntimeError("non configuré")
    r = requests.get(c["url"] + "/api/v3" + chemin, params=params, headers={"X-Api-Key": c["cle"]}, timeout=delai)
    if r.status_code in (401, 403):
        raise PermissionError("clé refusée")
    r.raise_for_status()
    return r.json()


def _reduire_film(x):
    f = x.get("movieFile") or {}
    return {"id": x.get("id"), "tmdbId": x.get("tmdbId"), "title": x.get("title") or "", "year": x.get("year"), "affiche": _affiche_arr(x.get("images")),
            "titleSlug": x.get("titleSlug"), "hasFile": x.get("hasFile"), "monitored": x.get("monitored"),
            "isAvailable": x.get("isAvailable"), "added": x.get("added"), "sizeOnDisk": x.get("sizeOnDisk"),
            "movieFile": {"nom": os.path.basename(f.get("relativePath") or f.get("path") or ""), "dateAdded": f.get("dateAdded"), "size": f.get("size"), "quality": f.get("quality")} if f else None}


def _reduire_serie(x):
    return {"id": x.get("id"), "tmdbId": x.get("tmdbId"), "tvdbId": x.get("tvdbId"), "title": x.get("title") or "", "year": x.get("year"),
            "affiche": _affiche_arr(x.get("images")), "titleSlug": x.get("titleSlug"), "monitored": x.get("monitored"),
            "added": x.get("added"), "statistics": x.get("statistics"),
            "seasons": [{"seasonNumber": y.get("seasonNumber"), "monitored": y.get("monitored"), "statistics": y.get("statistics")}
                        for y in x.get("seasons") or []]}


def _affiche_arr(images):
    """Garde uniquement une affiche distante publique de TMDB fournie par Radarr/Sonarr."""
    for image in images or []:
        if not isinstance(image, dict):
            continue
        url = str(image.get("remoteUrl") or "")
        try:
            u = urlsplit(url)
            if (image.get("coverType") == "poster" and u.scheme == "https" and u.hostname == "image.tmdb.org"
                    and u.username is None and u.password is None and u.path.startswith("/t/p/")):
                return url
        except Exception:
            pass
    return None


def _construire(reduits, queue):
    return {"par_tmdb": {x["tmdbId"]: x for x in reduits if x.get("tmdbId")},
            "par_tvdb": {x["tvdbId"]: x for x in reduits if x.get("tvdbId")}, "queue": queue}


def _telecharger(nom, delai):
    radarr = nom.startswith("radarr")
    items = _get(nom, "/movie" if radarr else "/series", delai=delai)
    try:
        rec = _get(nom, "/queue", {"page": 1, "pageSize": 200}, delai=min(delai, 30)).get("records", [])
    except Exception:
        rec = []
    cle_id = "movieId" if radarr else "seriesId"
    queue = {}
    for r in rec:
        queue.setdefault(r.get(cle_id), []).append({k: r.get(k) for k in CLES_FILE})
    return _construire([(_reduire_film if radarr else _reduire_serie)(x) for x in items], queue)


# ---------- Index en mémoire et sur le disque ----------
def _idx(nom):
    """Liste d'une instance telle qu'elle est en mémoire, ou None. Ne touche jamais au réseau."""
    with _verrou:
        e = _index.get(nom)
        return e["data"] if e else None


def _sauver_disque():
    tmp = INDEX_FICHIER + ".tmp"
    try:
        with _verrou_sauvegarde:
            with _verrou:
                out = {"version": 1, "arr": {}, "ov": None}
                for nom, e in _index.items():
                    d = e["data"]
                    recs = {}
                    for r in list(d["par_tmdb"].values()) + list(d["par_tvdb"].values()):
                        recs[r["id"]] = r
                    out["arr"][nom] = {"t": e["t"], "items": list(recs.values()), "queue": {str(k): v for k, v in d["queue"].items()}}
                if _ovbulk["data"] is not None:
                    out["ov"] = {"t": _ovbulk["t"], "items": [[t, i, m] for (t, i), m in _ovbulk["data"].items()]}
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(out, f)
            os.replace(tmp, INDEX_FICHIER)
    except Exception as e:
        _erreurs["disque"] = {"t": time.time(), "message": "%s : %s" % (type(e).__name__, str(e)[:100])}


def _charger_disque():
    try:
        with open(INDEX_FICHIER, encoding="utf-8") as f:
            brut = json.load(f)
    except Exception:
        return
    with _verrou:
        for nom, e in (brut.get("arr") or {}).items():
            if nom in NOMS and nom not in _index:
                queue = {}
                for k, v in (e.get("queue") or {}).items():
                    try:
                        queue[int(k)] = v
                    except ValueError:
                        pass
                _index[nom] = {"t": e.get("t", 0), "data": _construire(e.get("items") or [], queue), "origine": "disque"}
        ov = brut.get("ov")
        if ov and _ovbulk["data"] is None:
            _ovbulk.update(t=ov.get("t", 0), data={(t, i): m for t, i, m in ov.get("items", [])}, origine="disque")
        _vie["disque"] = True


# ---------- Overseerr ----------
def ov_base():
    try:
        u = _reglages().get("overseerr_url")
    except Exception:
        u = ""
    return (u or OVERSEERR_LOCAL).rstrip("/")


def ov_cle():
    return _secrets().get("overseerr")


def ov_appel(methode, chemin, corps=None, delai=None):
    cle = ov_cle()
    if not cle:
        raise RuntimeError("Clé Overseerr absente")
    if delai is None:
        delai = _reglages()["badges_delai_overseerr_s"]
    r = requests.request(methode, ov_base() + "/api/v1" + chemin, json=corps, headers={"X-Api-Key": cle}, timeout=delai)
    if r.status_code in (401, 403):
        raise PermissionError("Overseerr refuse la clé")
    return r


def ov_media(type_, id_, delai=None):
    """Ce qu'Overseerr sait de ce titre (mediaInfo), ou {} s'il ne le connaît pas. Un seul appel, pour la fiche."""
    k = ("ov", type_, id_)
    with _verrou:
        c = _cache.get(k)
    if c and time.time() - c[0] < 30:
        return c[1]
    r = ov_appel("GET", "/%s/%d" % ("tv" if type_ == "tv" else "movie", id_), delai=delai)
    if r.status_code == 404:
        mi = {}
    else:
        r.raise_for_status()
        mi = r.json().get("mediaInfo") or {}
    with _verrou:
        _cache[k] = (time.time(), mi)
    return mi


def _reduire_media(m):
    """Ce dont on a besoin pour les badges, sans le reste : la liste complète d'Overseerr peut être longue."""
    def suivi(liste):
        return [{"size": x.get("size"), "sizeLeft": x.get("sizeLeft"), "timeLeft": x.get("timeLeft")} for x in liste or []]
    return {"titre": m.get("title") or m.get("name"), "tmdbId": m.get("tmdbId"), "mediaType": m.get("mediaType"), "status": m.get("status"), "status4k": m.get("status4k"),
            "requests": [{"is4k": r.get("is4k"), "status": r.get("status")} for r in m.get("requests") or []],
            "downloadStatus": suivi(m.get("downloadStatus")), "downloadStatus4k": suivi(m.get("downloadStatus4k")),
            "seasons": [{"seasonNumber": s.get("seasonNumber"), "status": s.get("status"), "status4k": s.get("status4k")}
                        for s in m.get("seasons") or []]}


def _ov_telecharger(delai):
    out, skip = {}, 0
    for _ in range(12):
        res = ov_appel("GET", "/media?take=100&skip=%d&sort=added" % skip, delai=delai).json().get("results") or []
        for m in res:
            if m.get("tmdbId") and m.get("mediaType") in ("movie", "tv"):
                out[(m["mediaType"], m["tmdbId"])] = _reduire_media(m)
        if len(res) < 100:
            break
        skip += 100
    return out


def _ov_bulk():
    with _verrou:
        return _ovbulk["data"]


def _v_overseerr(mi, is4k):
    if not mi:
        return {"etat": "absent", "source": "overseerr"}
    st = mi.get("status4k" if is4k else "status")
    etat = {2: "attente", 3: "recherche", 4: "partiel", 5: "disponible", 6: "bloque"}.get(st, "absent")
    reqs = [r for r in (mi.get("requests") or []) if bool(r.get("is4k")) == is4k]
    v = {"etat": etat, "source": "overseerr"}
    dl = mi.get("downloadStatus4k" if is4k else "downloadStatus") or []
    if dl and etat != "disponible":
        tot = sum(x.get("size") or 0 for x in dl)
        reste = sum(x.get("sizeLeft") or 0 for x in dl)
        progres = max(0, min(100, round((tot - reste) / tot * 100))) if tot else None
        v["restant"] = next((_restant(x.get("timeLeft")) for x in dl if x.get("timeLeft")), None)
        if etat != "partiel":
            # Overseerr conserve parfois un downloadStatus à 100 % après la fin du torrent,
            # alors que l'ARR n'a pas importé le fichier. 100 % n'est pas une preuve d'activité.
            if progres is not None and progres >= 100:
                v["etat"] = "recherche"
                v["restant"] = None
            else:
                v["etat"] = "telechargement"
                v["progres"] = progres
    codes = [r.get("status") for r in reqs]
    if codes:
        # état de la demande elle même : 1 en attente d'approbation, 2 approuvée, 3 refusée, 4 échec (5 : terminée)
        v["demande"] = "approbation" if 1 in codes else "approuvee" if (2 in codes or 5 in codes) else "refusee" if 3 in codes else "echec" if 4 in codes else None
    if v["etat"] == "absent" and reqs:
        if 1 in codes:
            v["etat"] = "attente"
        elif 2 in codes or 5 in codes:
            v["etat"] = "recherche"
        elif 4 in codes:
            v["etat"] = "echec"
        elif 3 in codes:
            v["etat"] = "refuse"
    return v


def _saisons_ov(mi):
    out = {}
    for s in mi.get("seasons") or []:
        n = s.get("seasonNumber")
        if n:
            out[str(n)] = {"hd": _v_overseerr({"status": s.get("status")}, False),
                           "uhd": _v_overseerr({"status4k": s.get("status4k")}, True)}
    return out


# ---------- Rafraîchissement en arrière plan ----------
def _rafraichir_instance(nom, delai):
    debut = time.time()
    try:
        data = _telecharger(nom, delai)
    except Exception as e:
        with _verrou:
            _erreurs[nom] = {"t": time.time(), "message": "%s : %s" % (type(e).__name__, str(e)[:100])}
        return False
    with _verrou:
        _index[nom] = {"t": time.time(), "data": data, "origine": "reseau"}
        _durees[nom] = round(time.time() - debut, 2)
        _erreurs.pop(nom, None)
    return True


def _rafraichir_overseerr(delai):
    debut = time.time()
    try:
        data = _ov_telecharger(delai)
    except Exception as e:
        with _verrou:
            _erreurs["overseerr"] = {"t": time.time(), "message": "%s : %s" % (type(e).__name__, str(e)[:100])}
        return False
    import notifications
    notifications.observer_medias(data)
    with _verrou:
        _ovbulk.update(t=time.time(), data=data, origine="reseau")
        _durees["overseerr"] = round(time.time() - debut, 2)
        _erreurs.pop("overseerr", None)
    return True


def rafraichir_tout():
    """Un cycle : les instances configurées sont lues en parallèle. Une source lente ne retarde plus les autres."""
    cfg = _reglages()
    src = cfg["badges_source"]
    travaux = []
    if src in ("auto", "arr"):
        for nom in NOMS:
            if (config().get(nom) or {}).get("cle"):
                travaux.append((nom, lambda nom=nom: _rafraichir_instance(nom, cfg["badges_delai_liste_s"])))
    if src in ("auto", "overseerr") and ov_cle():
        travaux.append(("overseerr", lambda: _rafraichir_overseerr(cfg["badges_delai_overseerr_s"])))
    if not travaux:
        return True
    ok = True
    actualise = False
    with ThreadPoolExecutor(max_workers=min(5, len(travaux))) as ex:
        futures = {ex.submit(travail): nom for nom, travail in travaux}
        for future in as_completed(futures):
            if future.result():
                _sauver_disque()
                actualise = True
            else:
                ok = False
    if actualise:
        # Les statuts d'un média sont dérivés de ces index et peuvent avoir été calculés
        # depuis le cache disque avant le démarrage du rafraîchisseur.
        vider()
    return ok


def rafraichir_films():
    """Relit en parallèle Radarr et Overseerr, sans toucher aux index Sonarr pour une collection de films."""
    cfg = _reglages()
    src, travaux = cfg["badges_source"], []
    if src in ("auto", "arr"):
        for nom in ("radarr_hd", "radarr_uhd"):
            if (config().get(nom) or {}).get("cle"):
                travaux.append((nom, lambda nom=nom: _rafraichir_instance(nom, cfg["badges_delai_liste_s"])))
    if src in ("auto", "overseerr") and ov_cle():
        travaux.append(("overseerr", lambda: _rafraichir_overseerr(cfg["badges_delai_overseerr_s"])))
    if not travaux:
        return False
    ok = True
    with ThreadPoolExecutor(max_workers=len(travaux)) as ex:
        futures = {ex.submit(travail): nom for nom, travail in travaux}
        for future in as_completed(futures):
            if future.result():
                _sauver_disque()
            else:
                ok = False
    return ok


def _boucle(travail, nom, principale):
    """Boucle d'arrière plan d'une source. Chaque source a la sienne : une source lente ne retarde pas les autres
    (par exemple les vus de Trakt n'attendent pas les listes de Radarr)."""
    reveil = threading.Event()
    _reveils.append(reveil)
    cycles = 0
    while True:
        cfg = _reglages()
        cadence = cfg["badges_cache_min"] * 60
        if cycles == 0 and not cfg["badges_demarrage"]:
            reveil.wait()
            reveil.clear()
        elif cycles > 0 and _vie["demande"] and time.time() - _vie["demande"] > 2 * cadence:
            # personne n'a regardé le catalogue depuis longtemps : on dort jusqu'à la prochaine visite pour ménager le NAS
            reveil.wait()
            reveil.clear()
            continue
        if principale:
            _vie.update(en_cours=True, debut=time.time())
        try:
            ok = travail() is not False        # une tâche qui ne renvoie rien a réussi
        except Exception as e:
            with _verrou:
                _erreurs[nom] = {"t": time.time(), "message": "%s : %s" % (type(e).__name__, str(e)[:100])}
            ok = False
        else:
            if ok:
                with _verrou:
                    _erreurs.pop(nom, None)
        cycles += 1
        if principale:
            _vie.update(en_cours=False, fin=time.time(), cycles=cycles)
        reveil.wait(cadence if ok else min(60, cadence))
        reveil.clear()


def demarrer(taches=()):
    """Lance les boucles d'arrière plan (une seule fois) : les listes de Radarr, Sonarr et Overseerr, et chaque tâche
    supplémentaire (par exemple les vus de Trakt) dans sa propre boucle."""
    if _vie["fil"]:
        return
    _charger_disque()
    fils = [threading.Thread(target=_boucle, args=(rafraichir_tout, "listes", True), daemon=True)]
    for t in taches:
        fils.append(threading.Thread(target=_boucle, args=(t, getattr(t, "__name__", "tache"), False), daemon=True))
    for f in fils:
        f.start()
    _vie["fil"] = fils[0]


def _reveiller():
    for r in list(_reveils):
        r.set()


def rafraichir_bientot():
    _vie["demande"] = time.time()
    _reveiller()


def version_listes():
    """Date de la dernière lecture de chaque liste en mémoire : change dès qu'un rafraîchissement aboutit. Sert aux rangées
    calculées à partir des listes (Ajoutés récemment) pour ne jamais garder une réponse plus ancienne que les listes elles mêmes."""
    with _verrou:
        return tuple(sorted((nom, e["t"]) for nom, e in _index.items())) + (("overseerr", _ovbulk["t"]),)


def demander():
    """Appelé quand quelqu'un regarde des badges : réveille les boucles si les listes sont périmées ou absentes."""
    _vie["demande"] = time.time()
    if _vie["en_cours"]:
        return
    cadence = _reglages()["badges_cache_min"] * 60
    if _vie["fin"] is None or time.time() - _vie["fin"] > cadence:
        _reveiller()


def etat_cache():
    """Ce que le rafraîchisseur a fait : âge, origine (disque ou réseau), durée du dernier téléchargement, dernière erreur."""
    maintenant = time.time()
    out = {"listes": {}, "rafraichisseur": {"cycles": _vie["cycles"], "en_cours": _vie["en_cours"],
                                            "debut_il_y_a_s": round(maintenant - _vie["debut"]) if _vie["debut"] else None,
                                            "fin_il_y_a_s": round(maintenant - _vie["fin"]) if _vie["fin"] else None,
                                            "index_disque_charge": _vie["disque"]}}
    with _verrou:
        for nom, e in _index.items():
            out["listes"][nom] = {"age_s": round(maintenant - e["t"]), "origine": e["origine"],
                                  "elements": len(e["data"]["par_tmdb"]) + len([1 for x in e["data"]["par_tvdb"].values() if not x.get("tmdbId")]),
                                  "dernier_telechargement_s": _durees.get(nom)}
        if _ovbulk["data"] is not None:
            out["listes"]["overseerr"] = {"age_s": round(maintenant - _ovbulk["t"]), "origine": _ovbulk["origine"],
                                          "elements": len(_ovbulk["data"]), "dernier_telechargement_s": _durees.get("overseerr")}
        for nom, e in _erreurs.items():
            out["listes"].setdefault(nom, {})["derniere_erreur"] = e["message"]
            out["listes"][nom]["erreur_il_y_a_s"] = round(maintenant - e["t"])
    try:
        out["rafraichisseur"]["fichier_disque_octets"] = os.path.getsize(INDEX_FICHIER)
    except OSError:
        out["rafraichisseur"]["fichier_disque_octets"] = None
    return out


# ---------- Lecture d'un statut ----------
def _restant(tl):
    if not tl:
        return None
    try:
        jours = 0
        if "." in tl.split(":")[0]:
            jours, tl = tl.split(".", 1)
            jours = int(jours)
        h, m, _ = [int(float(x)) for x in tl.split(":")]
        h += jours * 24
        if h >= 24:
            return "%d j" % (h // 24)
        return "%d h %02d" % (h, m) if h else "%d min" % max(m, 1)
    except Exception:
        return None


def hashes_torrent(recs):
    """Les hashs de torrent des lignes de la file (Radarr et Sonarr donnent le hash dans downloadId, en majuscules). Un téléchargement
    usenet a un autre identifiant : on ne garde que les torrents (ou les lignes dont le protocole n'est pas précisé, index ancien)."""
    out = []
    for r in recs:
        d = str(r.get("downloadId") or "").lower()
        if re.fullmatch(r"[0-9a-f]{40}", d) and (r.get("protocol") or "torrent") == "torrent" and d not in out:
            out.append(d)
    return out


def medias_par_hash():
    """Médias connus par hash de torrent ; n'utilise que l'index Radarr/Sonarr déjà en mémoire ou sur disque."""
    cfg = config()
    with _verrou:
        index = {n: e["data"] for n, e in _index.items() if (cfg.get(n) or {}).get("cle")}
    candidats = {}
    for nom, data in index.items():
        type_ = "tv" if nom.startswith("sonarr") else "movie"
        items = list(data["par_tmdb"].values()) + list(data["par_tvdb"].values()) if type_ == "tv" else list(data["par_tmdb"].values())
        par_id = {x.get("id"): x for x in items if x.get("id") is not None}
        for media_id, recs in data.get("queue", {}).items():
            media = par_id.get(media_id)
            tmdb = media.get("tmdbId") if media else None
            titre = media.get("title") if media else None
            tvdb = media.get("tvdbId") if media else None
            if not titre or not (tmdb or (type_ == "tv" and tvdb)):
                continue
            identite = (type_, "tmdb", tmdb) if tmdb else (type_, "tvdb", tvdb)
            infos = {"type": type_, "id": tmdb, "titre": titre, "affiche": media.get("affiche"), "_instances": set()}
            for h in hashes_torrent(recs):
                existant = candidats.setdefault(h, {}).setdefault(identite, dict(infos))
                existant["_instances"].add(nom)
    # Si plusieurs médias distincts revendiquent le même torrent, ne pas faire de supposition.
    result = {}
    for h, medias in candidats.items():
        if len(medias) != 1:
            continue
        media = dict(next(iter(medias.values())))
        media["instances"] = sorted(media.pop("_instances"))
        result[h] = media
    return result


def _lire_historique_instance(nom, conf):
    """Lit une page d'historique ARR sans jamais conserver downloadUrl/nzbInfoUrl (potentiellement privés)."""
    try:
        r = requests.get(conf["url"].rstrip("/") + "/api/v3/history",
                         params={"page": 1, "pageSize": 200, "sortKey": "date", "sortDirection": "descending"},
                         headers={"X-Api-Key": conf["cle"]}, timeout=6)
        r.raise_for_status()
        donnees = r.json()
        records = donnees.get("records", []) if isinstance(donnees, dict) else []
        result = {}
        # On joint strictement par hash et au sein de la même instance ARR. Aucun rapprochement par titre.
        for rec in reversed(records if isinstance(records, list) else []):
            data = rec.get("data") or {}
            h = str(rec.get("downloadId") or data.get("torrentInfoHash") or "").lower()
            if not re.fullmatch(r"[0-9a-f]{40}", h):
                continue
            courant = result.setdefault(h, {"source": nom})
            source = str(rec.get("sourceTitle") or "").strip()
            if source:
                courant["release"] = source[:500]
            indexer = str(data.get("indexer") or "").strip()
            if indexer and indexer.lower() not in ("unknown", "n/a"):
                courant["tracker"] = indexer[:100]
        return result
    except Exception:
        return {}


def _rafraichir_historique_releases(epoch, cfg):
    global _historique_releases_fil
    travaux = [(n, c) for n, c in cfg.items() if n in NOMS and c.get("url") and c.get("cle")]
    resultat = {}
    if travaux:
        with ThreadPoolExecutor(max_workers=min(4, len(travaux))) as ex:
            futurs = {ex.submit(_lire_historique_instance, n, c): n for n, c in travaux}
            for f in as_completed(futurs):
                nom = futurs[f]
                try:
                    for h, info in f.result().items():
                        resultat.setdefault((nom, h), {}).update(info)
                except Exception:
                    pass
    with _historique_releases_verrou:
        if epoch == _historique_releases_epoch:
            _historique_releases.update(t=time.time(), data=resultat)
            _historique_releases_fil = None


def historique_releases_par_hash(hashes):
    """Renvoie l'instantané disponible et programme au plus une actualisation lente par tranche de 15 minutes."""
    global _historique_releases_fil
    maintenant = time.time()
    with _historique_releases_verrou:
        if maintenant - _historique_releases["t"] > 900 and _historique_releases_fil is None:
            epoch = _historique_releases_epoch
            snapshot = dict(config())
            _historique_releases_fil = threading.Thread(target=_rafraichir_historique_releases,
                                                        args=(epoch, snapshot), daemon=True)
            _historique_releases_fil.start()
        instantane = dict(_historique_releases["data"])
    # Transmission/Radarr/Sonarr ne doivent jamais se faire attribuer l'historique d'une autre instance.
    voulus = {str(h or "").lower() for h in hashes}
    return {h: {instance: info for (instance, hash_), info in instantane.items() if hash_ == h}
            for h in voulus if any(hash_ == h for _, hash_ in instantane)}


def _normaliser_nom_media(texte):
    texte = unicodedata.normalize("NFKD", str(texte or "").casefold())
    return re.sub(r"[^a-z0-9]+", " ", "".join(c for c in texte if not unicodedata.combining(c))).strip()


def medias_par_nom_torrent(noms):
    """Association de secours uniquement si le titre Arr forme un préfixe complet et univoque du torrent.

    Un séparateur de titre seul ne suffit pas : il faut aussi une année ou un marqueur de release/épisode connu.
    Les collisions entre identifiants TMDB restent volontairement non associées.
    """
    cfg = config()
    with _verrou:
        index = {n: e["data"] for n, e in _index.items() if (cfg.get(n) or {}).get("cle")}
    par_titre = {}
    for nom, data in index.items():
        type_ = "tv" if nom.startswith("sonarr") else "movie"
        medias = list(data.get("par_tmdb", {}).values()) + (list(data.get("par_tvdb", {}).values()) if type_ == "tv" else [])
        for media in medias:
            identifiant, titre = media.get("tmdbId"), _normaliser_nom_media(media.get("title"))
            if not identifiant or not titre:
                continue
            cle = (type_, identifiant)
            infos = {"type": type_, "id": identifiant, "titre": media.get("title"), "affiche": media.get("affiche"), "annee": media.get("year")}
            par_titre.setdefault(titre, {})[cle] = infos
    marqueurs = {"1080p", "2160p", "720p", "480p", "4k", "uhd", "bluray", "brrip", "web", "webrip", "webdl", "hdtv", "dvdrip", "remux", "x264", "x265", "h264", "h265", "proper", "repack", "multi", "french", "vostfr"}
    resultat = {}
    for nom in noms or []:
        torrent = _normaliser_nom_media(nom)
        correspondances = {}
        for titre, medias in par_titre.items():
            if not torrent.startswith(titre + " "):
                continue
            suite = torrent[len(titre):].strip().split()
            if not suite:
                continue
            premier = suite[0]
            annee = int(premier) if re.fullmatch(r"(?:18|19|20|21)\d{2}", premier) else None
            marqueur_episode = bool(re.fullmatch(r"s\d{1,2}e\d{1,3}", premier))
            if annee is None and premier not in marqueurs and not marqueur_episode:
                continue
            for cle, media in medias.items():
                if cle[0] == "movie" and any(re.fullmatch(r"s\d{1,2}e\d{1,3}", mot) for mot in suite):
                    continue
                annee_media = str(media.get("annee") or "")[:4]
                if annee and annee_media.isdigit() and int(annee_media) != annee:
                    continue
                correspondances[cle] = media
        if len(correspondances) == 1:
            resultat[nom] = next(iter(correspondances.values()))
    return resultat


def _progres(recs):
    tot = sum(r.get("size") or 0 for r in recs)
    reste = sum(r.get("sizeleft") or 0 for r in recs)
    p = max(0, min(100, round((tot - reste) / tot * 100))) if tot else None
    return p, next((_restant(r.get("timeleft")) for r in recs if r.get("timeleft")), None)


def _file_actif(rec):
    """Une ligne terminée/en import ne prouve pas qu'un téléchargement est encore actif."""
    status = str(rec.get("status") or "").casefold()
    tracked = str(rec.get("trackedDownloadState") or "").casefold()
    download = str(rec.get("downloadState") or "").casefold()
    if status in ("completed", "failed", "removed", "imported") or tracked in ("imported", "failed", "downloadfailed", "ignored"):
        return False
    return status in ("downloading", "queued") or tracked == "downloading" or download == "downloading"


def _v_radarr(idx, tmdb):
    m = idx["par_tmdb"].get(tmdb)
    if not m:
        return None
    if m.get("hasFile"):
        f = m.get("movieFile") or {}
        return {"etat": "disponible", "source": "radarr", "taille": f.get("size") or m.get("sizeOnDisk"),
                "fichier": f.get("nom") or os.path.basename(f.get("relativePath") or f.get("path") or ""),
                "qualite": ((f.get("quality") or {}).get("quality") or {}).get("name")}
    recs = [r for r in idx["queue"].get(m.get("id"), [])
            if r.get("movieId") in (None, m.get("id"))]
    if recs:
        if all((r.get("status") or "").lower() == "failed" for r in recs):
            return {"etat": "echec", "source": "radarr"}
        actifs = [r for r in recs if _file_actif(r)]
        if actifs:
            p, r = _progres(actifs)
            return {"etat": "telechargement", "source": "radarr", "progres": p, "restant": r, "hashes": hashes_torrent(actifs)}
    if not m.get("monitored"):
        return {"etat": "non_suivi", "source": "radarr"}
    return {"etat": "recherche" if m.get("isAvailable") else "a_venir", "source": "radarr"}


def _v_sonarr(idx, tmdb, tvdb):
    s = idx["par_tmdb"].get(tmdb) or (idx["par_tvdb"].get(tvdb) if tvdb else None)
    if not s:
        return None
    st = s.get("statistics") or {}
    fich, tot = st.get("episodeFileCount", 0), st.get("episodeCount", 0)
    recs = [r for r in idx["queue"].get(s.get("id"), [])
            if r.get("seriesId") in (None, s.get("id"))]
    actifs = [r for r in recs if _file_actif(r)]
    saisons = {}
    for x in s.get("seasons") or []:
        n = x.get("seasonNumber")
        if not n:
            continue
        sx = x.get("statistics") or {}
        f, t = sx.get("episodeFileCount", 0), sx.get("episodeCount", 0)
        if t and f >= t:
            e = "disponible"
        elif f > 0:
            e = "partiel"
        elif any(r.get("seasonNumber") == n for r in actifs):
            e = "telechargement"
        elif x.get("monitored"):
            e = "recherche"
        else:
            e = "non_suivi"
        saisons[str(n)] = {"etat": e, "fichiers": f, "total": t, "source": "sonarr"}
    if fich and tot and fich >= tot:
        etat = "disponible"
    elif fich > 0:
        etat = "partiel"
    elif actifs:
        etat = "telechargement"
    elif s.get("monitored"):
        etat = "recherche" if tot else "a_venir"
    else:
        etat = "non_suivi"
    v = {"etat": etat, "source": "sonarr", "fichiers": fich, "total": tot, "saisons": saisons}
    if actifs:
        v["progres"], v["restant"] = _progres(actifs)
        v["en_cours"] = len(actifs)
        v["hashes"] = hashes_torrent(actifs)
    return v


def disponibilite_episodes(tmdb, saison):
    """Disponibilité des épisodes d'une saison, interrogée une fois par Sonarr puis brièvement mise en cache."""
    try:
        tmdb, saison = int(tmdb), int(saison)
    except (TypeError, ValueError):
        return {}
    if tmdb <= 0 or saison <= 0:
        return {}
    maintenant = time.time()

    def lire(nom):
        cfg = config().get(nom) or {}
        if not cfg.get("cle"):
            return nom, {}
        idx = _idx(nom)
        if not idx:
            return nom, {}
        serie = idx["par_tmdb"].get(tmdb)
        if not serie:
            return nom, {}
        sid = serie.get("id")
        cle = (nom, sid, saison)
        with _episodes_cache_lock:
            memo = _episodes_cache.get(cle)
            if memo and maintenant - memo[0] < 45:
                return nom, memo[1]
        try:
            episodes = _get(nom, "/episode", {"seriesId": sid, "seasonNumber": saison}, delai=5)
        except Exception:
            return nom, {}
        episodes = [e for e in episodes if int(e.get("seasonNumber") or 0) == saison]
        queue = idx["queue"].get(sid, [])
        queue_ep = {}
        for item in queue:
            if not _file_actif(item):
                continue
            ids = item.get("episodeIds") or ([item.get("episodeId")] if item.get("episodeId") else [])
            for eid in ids:
                queue_ep[eid] = item
        now_utc = dt.datetime.now(dt.timezone.utc)
        resultat = {}
        for ep in episodes:
            numero = ep.get("episodeNumber")
            if not isinstance(numero, int):
                continue
            date_txt = ep.get("airDateUtc") or ""
            try:
                date_air = dt.datetime.fromisoformat(date_txt.replace("Z", "+00:00")) if date_txt else None
            except ValueError:
                date_air = None
            if date_air and date_air.tzinfo is None:
                date_air = date_air.replace(tzinfo=dt.timezone.utc)
            fichier = bool(ep.get("hasFile") or ep.get("episodeFileId"))
            telechargement = queue_ep.get(ep.get("id"))
            if fichier:
                etat = "disponible"
            elif telechargement:
                etat = "telechargement"
            elif date_air and date_air > now_utc:
                etat = "a_venir"
            elif ep.get("monitored"):
                etat = "recherche"
            else:
                etat = "absent"
            resultat[numero] = {"etat": etat, "source": nom,
                                "progres": _progres([telechargement])[0] if telechargement else None,
                                "date_air": date_txt or None}
        with _episodes_cache_lock:
            _episodes_cache[cle] = (maintenant, resultat)
        return nom, resultat

    noms = ("sonarr_hd", "sonarr_uhd")
    with ThreadPoolExecutor(max_workers=2) as ex:
        par_instance = dict(ex.map(lire, noms))
    numeros = set().union(*(set(x) for x in par_instance.values()))
    return {n: {q: par_instance["sonarr_" + q].get(n, {"etat": "inconnu"})
                for q in ("hd", "uhd")} for n in numeros}


def demander_episodes(tmdb, tvdb, saison, qualites, episode=None):
    """Demande seulement les épisodes diffusés qui manquent, dans chaque Sonarr ciblé.

    Chaque qualité est inspectée dans sa propre instance : un épisode présent ou en file dans Sonarr HD
    ne peut donc jamais masquer ni déclencher une recherche dans Sonarr 4K. Les épisodes futurs sont ignorés.
    """
    try:
        tmdb, saison = int(tmdb), int(saison)
        tvdb = int(tvdb) if tvdb else None
        episode = int(episode) if episode is not None else None
    except (TypeError, ValueError):
        raise ValueError("Identifiants de série, saison ou épisode invalides")
    qualites = list(dict.fromkeys(str(q) for q in (qualites or [])))
    if tmdb <= 0 or saison <= 0 or (episode is not None and episode <= 0):
        raise ValueError("Série, saison ou épisode invalide")
    if not qualites or any(q not in ("hd", "uhd") for q in qualites):
        raise ValueError("Qualité invalide")
    maintenant = dt.datetime.now(dt.timezone.utc)

    def demander(q):
        nom = "sonarr_" + q
        cfg = config().get(nom) or {}
        if not cfg.get("cle"):
            raise RuntimeError("L’instance %s n’est pas configurée" % TITRES[nom])
        idx = _idx(nom) or {}
        serie = (idx.get("par_tmdb") or {}).get(tmdb) or ((idx.get("par_tvdb") or {}).get(tvdb) if tvdb else None)
        if not serie or not serie.get("id"):
            raise LookupError("Série absente de %s" % TITRES[nom])
        sid = serie["id"]
        episodes = _get(nom, "/episode", {"seriesId": sid, "seasonNumber": saison}, delai=8)
        file_actifs = (idx.get("queue") or {}).get(sid, [])
        en_cours = set()
        for item in file_actifs:
            if not _file_actif(item):
                continue
            ids = item.get("episodeIds") or ([item.get("episodeId")] if item.get("episodeId") else [])
            en_cours.update(ids)
        manquants = []
        for ep in episodes or []:
            if ep.get("seasonNumber") != saison or (episode is not None and ep.get("episodeNumber") != episode):
                continue
            # Sonarr monitored sans fichier = état déjà « En recherche… » dans KamCiné ; ne relance pas
            # une demande existante pendant une demande de saison.
            if ep.get("hasFile") or ep.get("episodeFileId") or ep.get("id") in en_cours or ep.get("monitored"):
                continue
            date_txt = ep.get("airDateUtc") or ""
            try:
                date_air = dt.datetime.fromisoformat(date_txt.replace("Z", "+00:00")) if date_txt else None
            except ValueError:
                date_air = None
            if date_air and date_air.tzinfo is None:
                date_air = date_air.replace(tzinfo=dt.timezone.utc)
            if date_air and date_air > maintenant:
                continue
            manquants.append(ep)
        if not manquants:
            return {"qualite": q, "service": TITRES[nom], "episodes": []}
        headers = {"X-Api-Key": cfg["cle"], "Content-Type": "application/json"}
        # Sonarr n'accepte une EpisodeSearch que pour des épisodes suivis. Active le suivi uniquement
        # sur les éléments réellement absents, sans toucher aux autres épisodes/saisons.
        for ep in manquants:
            if not ep.get("monitored"):
                ep_modifie = dict(ep, monitored=True)
                rep = requests.put(cfg["url"].rstrip("/") + "/api/v3/episode/%s" % ep["id"],
                                   json=ep_modifie, headers=headers, timeout=12)
                if rep.status_code in (401, 403):
                    raise PermissionError("clé refusée")
                rep.raise_for_status()
        ids = [ep["id"] for ep in manquants]
        rep = requests.post(cfg["url"].rstrip("/") + "/api/v3/command",
                            json={"name": "EpisodeSearch", "episodeIds": ids}, headers=headers, timeout=12)
        if rep.status_code in (401, 403):
            raise PermissionError("clé refusée")
        rep.raise_for_status()
        rep.json()
        return {"qualite": q, "service": TITRES[nom], "episodes": [ep.get("episodeNumber") for ep in manquants]}

    with ThreadPoolExecutor(max_workers=min(2, len(qualites))) as ex:
        resultats = list(ex.map(demander, qualites))
    vider()
    if any(r["episodes"] for r in resultats):
        rafraichir_bientot()
    return {"resultats": resultats}


def meilleur(a, b):
    return a if ORDRE.index(a) >= ORDRE.index(b) else b


def _fusion(ov, ar):
    """Radarr et Sonarr savent ce qui est sur le NAS : ils l'emportent. Overseerr complète pour les demandes en attente."""
    if ar is not None:
        if ar["etat"] in ("absent", "non_suivi", "recherche", "a_venir") and ov and ov["etat"] in ("attente", "recherche", "telechargement", "refuse", "echec", "bloque"):
            resultat = dict(ov)
            if ar["etat"] == "a_venir":
                resultat["a_venir"] = True
            return resultat
        return ar
    return ov or {"etat": "inconnu", "source": None}


def statut(type_, id_, tvdb=None, complet=True):
    """État du titre en HD et en 4K, plus le détail par saison. None si aucune source n'est configurée.
    Ne lit que la mémoire (les listes des rafraîchisseurs), sauf l'appel à Overseerr pour un seul titre quand complet est vrai
    (la fiche). incomplet est vrai tant qu'une liste nécessaire n'est pas encore arrivée : l'interface réessaie."""
    cfg = _reglages()
    src = cfg["badges_source"]
    k = ("st", type_, id_, tvdb, complet, src)
    with _verrou:
        c = _cache.get(k)
    if c and time.time() - c[0] < 20:
        return c[1]
    arr_cfg = config()
    genre = "radarr" if type_ != "tv" else "sonarr"
    noms = {"hd": genre + "_hd", "uhd": genre + "_uhd"}
    actifs = {v: n for v, n in noms.items() if (arr_cfg.get(n) or {}).get("cle")} if src in ("auto", "arr") else {}
    ov_ok = bool(ov_cle()) and src in ("auto", "overseerr")
    if not actifs and not ov_ok:
        return None
    demander()
    sources, arr_res, mi, incomplet, profilage = {}, {}, None, False, {}
    ages = []
    for v, n in actifs.items():
        debut_source = time.perf_counter()
        idx = _idx(n)
        if idx is None:
            incomplet, arr_res[v] = True, None
            profilage[n] = round((time.perf_counter() - debut_source) * 1000)
            continue
        try:
            arr_res[v] = _v_radarr(idx, id_) if genre == "radarr" else _v_sonarr(idx, id_, tvdb)
            sources[n] = "ok"
        except Exception as e:
            arr_res[v] = None
            sources[n] = "erreur : " + str(e)[:60]
        with _verrou:
            ages.append(time.time() - _index[n]["t"])
        profilage[n] = round((time.perf_counter() - debut_source) * 1000)
    if ov_ok:
        debut_overseerr = time.perf_counter()
        bulk = _ov_bulk()
        if complet:
            try:
                mi = ov_media(type_, id_)
                sources["overseerr"] = "ok"
            except Exception as e:
                sources["overseerr"] = "erreur : " + str(e)[:60]
                if bulk is not None:
                    mi = bulk.get((type_, id_)) or {}
        elif bulk is not None:
            mi = bulk.get((type_, id_)) or {}
            sources["overseerr"] = "ok"
        elif bulk is None:
            incomplet = True
        profilage["overseerr_cache"] = round((time.perf_counter() - debut_overseerr) * 1000)
    ov_v = {v: (_v_overseerr(mi, v == "uhd") if mi is not None else None) for v in ("hd", "uhd")}
    res = {"hd": _fusion(ov_v["hd"], arr_res.get("hd")), "uhd": _fusion(ov_v["uhd"], arr_res.get("uhd")),
           "sources": sources, "saisons": {}, "erreur": None, "incomplet": incomplet,
           "age_s": round(max(ages)) if ages else None, "profilage_ms": profilage}
    if type_ == "tv":
        for v in ("hd", "uhd"):
            for n, s_ in ((arr_res.get(v) or {}).get("saisons") or {}).items():
                res["saisons"].setdefault(n, {})[v] = s_
        if mi:
            for n, d in _saisons_ov(mi).items():
                for v in ("hd", "uhd"):
                    res["saisons"].setdefault(n, {}).setdefault(v, d[v])
    res["global"] = meilleur(res["hd"]["etat"], res["uhd"]["etat"])
    if res["global"] == "inconnu" and not incomplet:
        res["erreur"] = "Aucune source ne répond"
    if not incomplet:
        with _verrou:
            _cache[k] = (time.time(), res)
    return res


def _statut_cible(type_, id_, tvdb=None, statut_initial=None, delai=4):
    """Complète un cache froid avec les recherches ARR ciblées sur un seul titre, en parallèle.

    N'interroge jamais les listes complètes. Le délai par instance est borné, et une réponse lente d'une qualité
    ne retarde pas les autres. `profilage_ms` sert à isoler la latence réelle par dépendance sans révéler les URLs.
    """
    debut = time.perf_counter()
    type_ = "tv" if type_ == "tv" else "movie"
    try:
        id_ = int(id_)
        tvdb = int(tvdb) if tvdb else None
    except (TypeError, ValueError):
        tvdb = None
    st = statut_initial or statut(type_, id_, tvdb, False)
    mesures = {"cache_local": round((time.perf_counter() - debut) * 1000), **(st.get("profilage_ms") or {})}
    cfg = _reglages()
    src = cfg["badges_source"]
    genre = "sonarr" if type_ == "tv" else "radarr"
    noms = [genre + "_hd", genre + "_uhd"] if src in ("auto", "arr") else []
    arr_cfg = config()
    manquants = [n for n in noms if (arr_cfg.get(n) or {}).get("cle") and _idx(n) is None]

    def lire(nom):
        t0 = time.perf_counter()
        titre_ms = queue_ms = 0
        param = {"tvdbId" if type_ == "tv" else "tmdbId": tvdb if type_ == "tv" else id_}
        if type_ == "tv" and not tvdb:
            return nom, None, False, {"titre": 0, "queue": 0, "total": 0}
        try:
            items = _get(nom, "/series" if type_ == "tv" else "/movie", param, delai=delai)
            titre_ms = round((time.perf_counter() - t0) * 1000)
            item = next((x for x in items if (x.get("tvdbId") == tvdb if type_ == "tv" else x.get("tmdbId") == id_)), None)
            if item is None:
                valeur = {"etat": "absent", "source": nom}
                queue_ms = 0
            elif type_ == "tv":
                reduit = _reduire_serie(item)
                stats = reduit.get("statistics") or {}
                complet = bool(stats.get("episodeCount") and stats.get("episodeFileCount", 0) >= stats.get("episodeCount"))
                debut_queue = time.perf_counter()
                queue = [] if complet else _get(nom, "/queue", {"seriesId": reduit.get("id"), "page": 1, "pageSize": 50}, delai=min(delai, 2)).get("records", [])
                queue = [x for x in queue if x.get("seriesId") == reduit.get("id")]
                queue_ms = round((time.perf_counter() - debut_queue) * 1000)
                valeur = _v_sonarr({"par_tmdb": {}, "par_tvdb": {tvdb: reduit}, "queue": {reduit.get("id"): [{k: x.get(k) for k in CLES_FILE} for x in queue]}}, id_, tvdb)
            else:
                reduit = _reduire_film(item)
                debut_queue = time.perf_counter()
                queue = [] if reduit.get("hasFile") else _get(nom, "/queue", {"movieId": reduit.get("id"), "page": 1, "pageSize": 50}, delai=min(delai, 2)).get("records", [])
                queue_ms = round((time.perf_counter() - debut_queue) * 1000)
                valeur = _v_radarr({"par_tmdb": {id_: reduit}, "queue": {reduit.get("id"): [{k: x.get(k) for k in CLES_FILE} for x in queue]}}, id_)
            return nom, valeur, True, {"titre": titre_ms, "queue": queue_ms, "total": round((time.perf_counter() - t0) * 1000)}
        except Exception as exc:
            return nom, {"erreur": type(exc).__name__}, False, {"titre": titre_ms, "queue": queue_ms, "total": round((time.perf_counter() - t0) * 1000)}

    bulk = _ov_bulk()
    besoin_ov = bool(ov_cle()) and src in ("auto", "overseerr") and bulk is None

    def lire_ov():
        t0 = time.perf_counter()
        try:
            return ov_media(type_, id_, delai=min(delai, 4)), True, round((time.perf_counter() - t0) * 1000)
        except Exception as exc:
            return {"erreur": type(exc).__name__}, False, round((time.perf_counter() - t0) * 1000)

    lectures, ov_resultat = [], None
    travaux = {n: None for n in manquants}
    if besoin_ov:
        travaux["overseerr"] = None
    if travaux:
        with ThreadPoolExecutor(max_workers=min(5, len(travaux))) as ex:
            futures = {ex.submit(lire, n): n for n in manquants}
            if besoin_ov:
                futures[ex.submit(lire_ov)] = "overseerr"
            for future in as_completed(futures):
                nom = futures[future]
                if nom == "overseerr":
                    ov_resultat = future.result()
                else:
                    lectures.append(future.result())
    for nom, _, _, durees in lectures:
        mesures.update({nom + "_" + etape + "_ms": duree for etape, duree in durees.items()})
    if ov_resultat:
        mesures["overseerr_titre"] = ov_resultat[2]
    resultat = dict(st or {})
    resultat.setdefault("hd", {"etat": "inconnu"})
    resultat.setdefault("uhd", {"etat": "inconnu"})
    resultat["sources"] = dict(resultat.get("sources") or {})
    incomplet = bool(resultat.get("incomplet"))
    ov_valeurs = ({"hd": _v_overseerr(ov_resultat[0], False), "uhd": _v_overseerr(ov_resultat[0], True)}
                  if ov_resultat and ov_resultat[1] else None)
    for nom, valeur, ok, _ in lectures:
        cle = "uhd" if nom.endswith("_uhd") else "hd"
        if ok:
            resultat[cle] = _fusion((ov_valeurs or {}).get(cle), valeur)
            resultat["sources"][nom] = "ok (recherche ciblée)"
        else:
            resultat["sources"][nom] = "erreur : " + (valeur or {}).get("erreur", "inconnue")
            incomplet = True
    if lectures and all(ok for _, _, ok, _ in lectures):
        incomplet = False
    arr_configure = any((arr_cfg.get(n) or {}).get("cle") for n in noms)
    if ov_resultat:
        ov_media_resultat, ov_ok, _ = ov_resultat
        if ov_ok:
            if not arr_configure:
                resultat["hd"] = _fusion(_v_overseerr(ov_media_resultat, False), None)
                resultat["uhd"] = _fusion(_v_overseerr(ov_media_resultat, True), None)
                incomplet = False
            resultat["sources"]["overseerr"] = "ok (titre)"
        else:
            resultat["sources"]["overseerr"] = "erreur : " + ov_media_resultat.get("erreur", "inconnue")
            if not arr_configure:
                incomplet = True
    resultat["incomplet"] = incomplet
    resultat["global"] = meilleur(resultat["hd"].get("etat", "inconnu"), resultat["uhd"].get("etat", "inconnu"))
    resultat["profilage_ms"] = mesures
    return resultat


def statut_cible(type_, id_, tvdb=None, statut_initial=None, delai=4):
    """Mutualise la résolution ciblée lorsqu'une fiche lance sa passe rapide et sa passe détaillée ensemble."""
    cle = ("tv" if type_ == "tv" else "movie", int(id_), int(tvdb) if tvdb else None)
    maintenant = time.monotonic()
    proprietaire = False
    with _verrou_cible:
        cache = _cible_cache.get(cle)
        if cache and maintenant - cache[0] < 8:
            resultat = dict(cache[1])
            resultat["profilage_ms"] = dict(resultat.get("profilage_ms") or {}, cache_cible_ms=0)
            return resultat
        future = _cible_en_cours.get(cle)
        if future is None:
            future = Future()
            _cible_en_cours[cle] = future
            proprietaire = True
    if not proprietaire:
        return future.result()
    try:
        resultat = _statut_cible(type_, id_, tvdb, statut_initial, delai)
        with _verrou_cible:
            _cible_cache[cle] = (time.monotonic(), resultat)
            _cible_en_cours.pop(cle, None)
        future.set_result(resultat)
        return resultat
    except Exception as exc:
        with _verrou_cible:
            _cible_en_cours.pop(cle, None)
        future.set_exception(exc)
        raise


DISPO = ("disponible", "partiel")


def qualite_visee(hd_etat, uhd_etat, preference="meilleure"):
    """Version qu'une séance viserait d'après ce que Radarr et Sonarr savent : la 4K quand elle existe, sinon la HD, ou la HD
    d'abord si la préférence est hd. Une version compte si elle est disponible ou partielle (une saison à moitié là).
    incertaine est vrai quand les deux existent : le lien infuse://movie/ID?play ne dit pas quelle version lancer, c'est
    Infuse qui choisit (voir docs/journal.md), la version visée n'est alors qu'une intention, pas une garantie."""
    hd, uhd = hd_etat in DISPO, uhd_etat in DISPO
    if hd and uhd:
        visee, raison = ("hd", "Préférence HD") if preference == "hd" else ("uhd", "La meilleure version disponible")
    elif uhd:
        visee, raison = "uhd", "Seule la 4K est disponible"
    elif hd:
        visee, raison = "hd", "La 4K n'est pas disponible" if uhd_etat not in ("telechargement", "recherche", "attente") else "La 4K n'est pas encore là"
    else:
        visee, raison = None, "Aucune version disponible"
    return {"visee": visee, "hd": hd, "uhd": uhd, "preference": preference, "raison": raison, "incertaine": hd and uhd}


def qualites_titre(st, preference="meilleure"):
    """qualite_visee pour tout le titre et, pour une série, saison par saison. st vient de statut()."""
    if not st:
        return None
    out = {"global": qualite_visee(st["hd"]["etat"], st["uhd"]["etat"], preference), "saisons": {}}
    for n, d in (st.get("saisons") or {}).items():
        out["saisons"][n] = qualite_visee((d.get("hd") or {}).get("etat"), (d.get("uhd") or {}).get("etat"), preference)
    return out


def relire_film(tmdb, delai=10):
    """Relit UN film (et la file de téléchargement) dans chaque instance Radarr configurée, et met l'index en mémoire à jour. Beaucoup
    plus léger que de relire toute la liste : sert à suivre un film qu'on attend (séance en attente), sans alourdir le NAS."""
    faits = 0
    for nom in ("radarr_hd", "radarr_uhd"):
        if not (config().get(nom) or {}).get("cle"):
            continue
        try:
            items = _get(nom, "/movie", {"tmdbId": tmdb}, delai=delai)
            rec = _get(nom, "/queue", {"page": 1, "pageSize": 200}, delai=delai).get("records", [])
        except Exception as e:
            with _verrou:
                _erreurs[nom + "_film"] = {"t": time.time(), "message": "%s : %s" % (type(e).__name__, str(e)[:100])}
            continue
        with _verrou:
            e = _index.get(nom)
            if e is None:
                continue
            d = e["data"]
            if items:
                r = _reduire_film(items[0])
                d["par_tmdb"][tmdb] = r
                mid = r["id"]
                d["queue"][mid] = [{k: x.get(k) for k in CLES_FILE}
                                   for x in rec if x.get("movieId") == mid]
                if not d["queue"][mid]:
                    d["queue"].pop(mid)
        faits += 1
    if faits:
        vider()
    return faits


def relire_serie(tmdb, delai=5):
    """Relit une série connue dans chaque Sonarr et sa file ciblée pour confirmer rapidement un import."""
    faits = 0
    for nom in ("sonarr_hd", "sonarr_uhd"):
        if not (config().get(nom) or {}).get("cle"):
            continue
        with _verrou:
            entree = _index.get(nom)
            serie_connue = (entree or {}).get("data", {}).get("par_tmdb", {}).get(tmdb)
            tvdb = serie_connue.get("tvdbId") if serie_connue else None
        if not tvdb:
            continue
        try:
            items = _get(nom, "/series", {"tvdbId": tvdb}, delai=delai)
            if not items:
                continue
            serie = _reduire_serie(items[0])
            rec = _get(nom, "/queue", {"seriesId": serie["id"], "page": 1, "pageSize": 200}, delai=delai).get("records", [])
        except Exception as e:
            with _verrou:
                _erreurs[nom + "_serie"] = {"t": time.time(), "message": "%s : %s" % (type(e).__name__, str(e)[:100])}
            continue
        with _verrou:
            entree = _index.get(nom)
            if entree is None:
                continue
            data = entree["data"]
            ancien = data["par_tmdb"].get(tmdb)
            if ancien:
                data["par_tvdb"].pop(ancien.get("tvdbId"), None)
            data["par_tmdb"][tmdb] = serie
            if serie.get("tvdbId"):
                data["par_tvdb"][serie["tvdbId"]] = serie
            rec = [x for x in rec if x.get("seriesId") == serie["id"]]
            data["queue"][serie["id"]] = [{k: x.get(k) for k in CLES_FILE} for x in rec]
            if not data["queue"][serie["id"]]:
                data["queue"].pop(serie["id"], None)
        faits += 1
    if faits:
        vider()
    return faits


def film_pret(tmdb, versions="both"):
    """Ce qui est vrai maintenant pour un film qu'on attend, d'après l'index (à jour grâce à relire_film). pret : un fichier est présent
    dans une des versions demandées ET ce film n'est plus dans la file de téléchargement de cette instance (jamais un film à moitié
    téléchargé ni en cours de remplacement). Renvoie pret, version (hd ou uhd), etat (disponible, telechargement, echec, recherche,
    absent...), progres et restant du téléchargement le plus avancé."""
    voulues = {"hd": ("hd",), "uhd": ("uhd",)}.get(versions, ("hd", "uhd"))
    out = {"pret": False, "version": None, "etat": "absent", "progres": None, "restant": None, "instances": 0}
    meilleur_progres = -1
    for v in voulues:
        nom = "radarr_" + v
        if not (config().get(nom) or {}).get("cle"):
            continue
        idx = _idx(nom)
        if idx is None:
            continue
        out["instances"] += 1
        m = idx["par_tmdb"].get(tmdb)
        if not m:
            continue
        recs = idx["queue"].get(m.get("id"), [])
        f = m.get("movieFile") or {}
        if m.get("hasFile"):
            return dict(out, pret=True, version=v, etat="disponible")
        if recs:
            if all((r.get("status") or "").lower() == "failed" for r in recs):
                if out["etat"] not in ("telechargement",):
                    out["etat"] = "echec"
            else:
                actifs = [x for x in recs if _file_actif(x)]
                p, r = _progres(actifs) if actifs else (None, None)
                if actifs and (p if p is not None else 0) > meilleur_progres:
                    meilleur_progres = p if p is not None else 0
                    out.update(etat="telechargement", progres=p, restant=r, version=v)
                elif not actifs and out["etat"] == "absent":
                    out["etat"] = "recherche" if m.get("monitored") else "non_suivi"
        elif m.get("hasFile"):
            out["etat"] = "disponible_incertain"
        elif out["etat"] == "absent":
            out["etat"] = "recherche" if m.get("monitored") else "non_suivi"
    return out


def liens_arr(type_, id_, tvdb=None):
    """Liens vers la page du titre dans Radarr et Sonarr, pour les instances où il est présent. On ne donne que le port et le chemin :
    l'interface prend l'adresse par laquelle elle est elle même ouverte (le service voit Radarr en 127.0.0.1, le téléphone non).
    Radarr : /movie/ suivi de l'identifiant TMDB (c'est son titleSlug). Sonarr : /series/ suivi du titleSlug, absent d'un index écrit
    avant la 2.1 : pas de lien tant que la liste n'est pas relue."""
    out = []
    genre = "radarr" if type_ != "tv" else "sonarr"
    cfg = config()
    for v, titre in (("hd", "HD"), ("uhd", "4K")):
        nom = genre + "_" + v
        if not (cfg.get(nom) or {}).get("cle"):
            continue
        idx = _idx(nom)
        if idx is None:
            continue
        m = idx["par_tmdb"].get(id_) or (idx["par_tvdb"].get(tvdb) if tvdb and genre == "sonarr" else None)
        if not m:
            continue
        chemin = "/movie/%s" % (m.get("titleSlug") or id_) if genre == "radarr" else ("/series/%s" % m["titleSlug"] if m.get("titleSlug") else None)
        if not chemin:
            continue
        url = (cfg.get(nom) or {}).get("url") or ""
        mp = re.search(r":(\d+)", url.split("//", 1)[-1])
        out.append({"instance": nom, "genre": genre, "version": titre, "port": int(mp.group(1)) if mp else None, "chemin": chemin})
    return out


def relancer_recherche(type_, tmdb, qualite, tvdb=None, saison=None):
    """Lance une recherche ciblée uniquement si le titre/la saison est toujours en recherche."""
    if type_ not in ("movie", "tv") or qualite not in ("hd", "uhd"):
        raise ValueError("Titre ou qualité invalide")
    nom = ("sonarr" if type_ == "tv" else "radarr") + "_" + qualite
    c = config().get(nom) or {}
    if not c.get("cle"):
        raise RuntimeError("instance non configurée")
    radarr = type_ == "movie"
    params = {"tmdbId": tmdb} if tmdb else {"tvdbId": tvdb}
    if not next(iter(params.values()), None):
        raise ValueError("Identifiant média absent")
    medias = _get(nom, "/movie" if radarr else "/series", params, delai=10)
    media = next((x for x in medias or [] if (x.get("tmdbId") == tmdb if tmdb else x.get("tvdbId") == tvdb)), None)
    if not media:
        raise LookupError("Titre absent de l'instance")
    ident = media.get("id")
    if not media.get("monitored"):
        raise RuntimeError("titre non surveillé")
    queue = _get(nom, "/queue", {"page": 1, "pageSize": 200}, delai=10).get("records", [])
    en_cours = [x for x in queue if x.get("movieId" if radarr else "seriesId") == ident
                and (not saison or x.get("seasonNumber") == saison)
                and str(x.get("status") or "").lower() != "failed"]
    if en_cours:
        raise RuntimeError("un téléchargement est déjà en cours")
    if radarr:
        if media.get("hasFile") or not media.get("isAvailable"):
            raise RuntimeError("le film n'est plus en recherche")
        commande = {"name": "MoviesSearch", "movieIds": [ident]}
    elif saison:
        detail = next((x for x in media.get("seasons", []) if x.get("seasonNumber") == saison), None)
        stats = (detail or {}).get("statistics") or {}
        if not detail or not detail.get("monitored") or not stats.get("episodeCount") or stats.get("episodeFileCount", 0):
            raise RuntimeError("la saison n'est plus en recherche")
        commande = {"name": "SeasonSearch", "seriesId": ident, "seasonNumber": saison}
    else:
        stats = media.get("statistics") or {}
        if not stats.get("episodeCount") or stats.get("episodeFileCount", 0):
            raise RuntimeError("la série n'est plus en recherche")
        commande = {"name": "SeriesSearch", "seriesId": ident}
    r = requests.post(c["url"].rstrip("/") + "/api/v3/command", json=commande,
                      headers={"X-Api-Key": c["cle"]}, timeout=12)
    if r.status_code in (401, 403):
        raise PermissionError("clé refusée")
    r.raise_for_status()
    r.json()
    vider()
    rafraichir_bientot()
    return {"instance": nom, "service": TITRES[nom], "titre": media.get("title") or "", "saison": saison,
            "commande": commande["name"]}


def synchroniser_overseerr():
    """Demande à Overseerr de relire Radarr, Sonarr et les téléchargements tout de suite."""
    jobs = ov_appel("GET", "/settings/jobs").json()
    voulus = {"radarr-scan", "sonarr-scan", "availability-sync", "download-sync"}
    lances = []
    for j in jobs:
        if j.get("id") in voulus:
            ov_appel("POST", "/settings/jobs/%s/run" % j["id"])
            lances.append(j["id"])
    vider()
    return lances


def ajoutes(n=18, sans=None):
    """Titres dont un fichier est arrivé récemment sur le NAS, d'après Radarr et Sonarr (listes déjà en mémoire). sans : fonction (type, id) qui
    dit si un titre est à écarter (les titres déjà vus, pour la rangée Non vues), appliquée avant de couper à n."""
    out, manque = {}, False
    for nom in NOMS:
        if not (config().get(nom) or {}).get("cle"):
            continue
        idx = _idx(nom)
        if idx is None:
            manque = True
            continue
        radarr = nom.startswith("radarr")
        for tm, x in idx["par_tmdb"].items():
            if radarr:
                if not x.get("hasFile"):
                    continue
                d, k = ((x.get("movieFile") or {}).get("dateAdded")) or x.get("added") or "", ("movie", tm)
            else:
                if not (x.get("statistics") or {}).get("episodeFileCount"):
                    continue
                d, k = x.get("added") or "", ("tv", tm)
            if d > out.get(k, ""):
                out[k] = d
    tries = [k for k, _ in sorted(out.items(), key=lambda kv: kv[1], reverse=True)]
    if sans:
        tries = [k for k in tries if not sans(*k)]
    return tries[:n], manque


def fichiers_episode(id_, saison, episode):
    """Noms exacts de l'épisode sélectionné, sans exposer les chemins du NAS."""
    cle = ("fichiers_episode", id_, saison, episode)
    with _verrou:
        ancien = _cache.get(cle)
    if ancien and time.time() - ancien[0] < 60:
        return ancien[1]
    noms = []
    for version in ("uhd", "hd"):
        nom = "sonarr_" + version
        idx = _idx(nom)
        serie = (idx or {}).get("par_tmdb", {}).get(id_)
        if not serie:
            continue
        try:
            episodes = _get(nom, "/episode", {"seriesId": serie["id"], "seasonNumber": saison}, delai=5)
            ep = next((e for e in episodes if e.get("seasonNumber") == saison and e.get("episodeNumber") == episode), None)
            if not ep or not ep.get("hasFile"):
                continue
            fichier = ep.get("episodeFile") or _get(nom, "/episodefile/%d" % ep["episodeFileId"], delai=5)
            chemin = fichier.get("relativePath") or fichier.get("path")
            if chemin:
                noms.append({"k": "Fichier " + ("4K" if version == "uhd" else "HD"), "v": os.path.basename(chemin)})
        except Exception:
            continue
    with _verrou:
        _cache[cle] = (time.time(), noms)
    return noms
