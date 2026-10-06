"""Transmission : suivi des téléchargements et retrait sans effacement des données.

Optionnel et désactivé par défaut (réglage transmission_actif). L'adresse, l'identifiant et le mot de passe sont dans secrets.json,
comme les clés des autres services. Sans Transmission, la progression vient de la file de Radarr et de Sonarr et tout marche.

Le lien entre un titre et son torrent se fait par le hash : la file de Radarr et de Sonarr donne downloadId, qui est le hash du
torrent, et Transmission le connaît sous hashString. Aucune devinette par le nom. Le suivi est en lecture seule ; la page dédiée peut
retirer un torrent de la liste Transmission par hash sans jamais supprimer ses données locales.

L'interface RPC de Transmission demande un jeton de session : la première requête reçoit une erreur 409 avec l'en tête
X-Transmission-Session-Id, qu'il faut renvoyer dans la requête suivante. Le jeton est gardé en mémoire et renouvelé quand
Transmission le change."""
import json, os, re, threading, time
import requests
from config import charger

import json_atomique
import chemins
BASE = chemins.data_dir()  # dossier des données (2.6.101)
SECRETS = os.path.join(BASE, "secrets.json")
DELAI = 3                     # secondes : ce module ne doit jamais retarder une page
CHAMPS = ["hashString", "name", "status", "percentDone", "rateDownload", "eta", "totalSize", "sizeWhenDone", "leftUntilDone",
          "peersConnected", "isStalled", "error", "errorString"]
ETATS = {0: "en pause", 1: "en attente de vérification", 2: "vérification", 3: "en file d'attente", 4: "téléchargement",
         5: "en attente de partage", 6: "partage"}
CHAMPS_RECENTS = ["hashString", "name", "status", "percentDone", "rateDownload", "eta", "totalSize", "sizeWhenDone",
                  "leftUntilDone", "error", "errorString", "addedDate", "doneDate", "uploadRatio", "peersConnected", "isStalled"]

# Point 83 : la page dédiée lit toute la file. Connexion courte pour reconnaître vite un Transmission éteint, lecture longue
# pour une file volumineuse qui répond lentement sans être en panne.
DELAI_RECENTS = (3, 20)
# Après une lecture réussie, une coupure passagère (délai dépassé, connexion refusée pendant un redémarrage) ressert cette
# lecture en la signalant comme ancienne, au plus pendant cette durée. Au delà, c'est une vraie panne et elle s'affiche.
TOLERANCE_RECENTS_S = 60

_verrou = threading.Lock()
_verrou_recents = threading.Lock()   # un seul torrent-get complet à la fois, les autres appels attendent son résultat
_jeton = {"id": None}
_cache = {}                   # (url, hashes) -> (heure, résultat), 4 secondes : la fiche interroge toutes les 5 secondes
_cache_recents = {}            # URL -> (heure, résultat), 6 secondes pour la page dédiée
_incident = {}                 # URL -> {"debut", "cause", "echecs"} : coupure en cours, pour ne journaliser qu'à son début
_par_hash = {}                 # URL -> (heure, {hash: infos}) : toute la file de la dernière lecture réussie, pour la fiche et le catalogue


def cause(e):
    """Nature d'une erreur Transmission, pour un message et un journal précis."""
    if isinstance(e, PermissionError):
        return "identifiants"
    if isinstance(e, (requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError)):
        return "hors_ligne"
    if isinstance(e, requests.exceptions.Timeout):
        return "delai"
    return "reponse"


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
    return _secrets().get("transmission") or {}


def actif():
    return bool(charger().get("transmission_actif")) and bool(config().get("url"))


def etat():
    """Pour l'interface : jamais le mot de passe."""
    c = config()
    return {"configuree": bool(c.get("url")), "actif": bool(charger().get("transmission_actif")), "url": c.get("url") or "",
            "utilisateur": c.get("user") or "", "mot_de_passe": bool(c.get("pass"))}


def normaliser(url):
    """http://hote:9091 devient http://hote:9091/transmission/rpc. Une adresse qui contient déjà /transmission/rpc reste telle quelle."""
    url = (url or "").strip().rstrip("/")
    if not re.match(r"^https?://[\w.\-]+(:\d+)?(/[\w.\-/]*)?$", url):
        raise ValueError("L'adresse doit ressembler à http://192.168.1.10:9091")
    if not url.endswith("/rpc"):
        url += "/rpc" if url.endswith("/transmission") else "/transmission/rpc"
    return url


def _rpc(url, user, mdp, methode, arguments=None, delai=DELAI):
    """Un appel RPC, avec l'échange du jeton de session. Lève PermissionError si l'identifiant est refusé."""
    corps = {"method": methode}
    if arguments:
        corps["arguments"] = arguments
    auth = (user, mdp) if user else None
    for _ in range(2):
        with _verrou:
            jeton = _jeton["id"]
        h = {"X-Transmission-Session-Id": jeton} if jeton else {}
        r = requests.post(url, json=corps, headers=h, auth=auth, timeout=delai)
        if r.status_code == 409:
            with _verrou:
                _jeton["id"] = r.headers.get("X-Transmission-Session-Id")
            continue
        if r.status_code in (401, 403):
            raise PermissionError("identifiant ou mot de passe refusé")
        r.raise_for_status()
        j = r.json()
        if j.get("result") != "success":
            raise RuntimeError(str(j.get("result"))[:80])
        return j.get("arguments") or {}
    raise RuntimeError("jeton de session refusé par Transmission")


def tester(url, user="", mdp=""):
    """Bouton Tester la connexion : demande la version à Transmission. Renvoie le texte de la version."""
    a = _rpc(normaliser(url), user, mdp, "session-get", {"fields": ["version", "rpc-version"]}, delai=6)
    return a.get("version") or ("RPC " + str(a.get("rpc-version", "")))


def sauver(url, user, mdp):
    s = _secrets()
    s["transmission"] = {"url": normaliser(url), "user": user or "", "pass": mdp or ""}
    _ecrire(s)
    _cache.clear()
    _cache_recents.clear()
    _par_hash.clear()


def retirer():
    s = _secrets()
    s.pop("transmission", None)
    _ecrire(s)
    _cache.clear()
    _cache_recents.clear()
    _par_hash.clear()
    with _verrou:
        _jeton["id"] = None


def torrents(hashes):
    """{hash: infos} pour les hashs donnés, ou None si Transmission est désactivé ou injoignable (l'appelant retombe alors sur la file
    de Radarr et de Sonarr). Un hash inconnu de Transmission est simplement absent du résultat."""
    hashes = sorted({h.lower() for h in hashes or [] if h})
    if not hashes or not actif():
        return None
    c = config()
    cle = (c.get("url"), tuple(hashes))
    with _verrou:
        p = _cache.get(cle)
    if p and time.time() - p[0] < 4:
        return p[1]
    try:
        a = _rpc(c["url"], c.get("user"), c.get("pass"), "torrent-get", {"fields": CHAMPS, "ids": hashes})
    except Exception:
        return None
    out = {}
    for t in a.get("torrents") or []:
        h = str(t.get("hashString") or "").lower()
        if not h:
            continue
        eta = t.get("eta")
        out[h] = {"nom": t.get("name"), "etat": ETATS.get(t.get("status"), str(t.get("status"))),
                  "progres": round((t.get("percentDone") or 0) * 100, 1), "vitesse": t.get("rateDownload") or 0,
                  "restant_s": eta if isinstance(eta, (int, float)) and eta >= 0 else None,
                  "taille": t.get("sizeWhenDone") or t.get("totalSize") or 0, "reste": t.get("leftUntilDone") or 0,
                  "pairs": t.get("peersConnected") or 0, "bloque": bool(t.get("isStalled")),
                  "erreur": (t.get("errorString") or "")[:120] if t.get("error") else ""}
    with _verrou:
        _cache[cle] = (time.time(), out)
    return out


def recents(limite=20):
    """Téléchargements actifs et terminés récemment, en lecture seule, à partir de torrent-get.

    Une coupure réseau passagère ressert la dernière lecture réussie de moins de TOLERANCE_RECENTS_S, marquée perime avec
    son âge, sa cause et le nombre d'échecs de la coupure. Identifiants refusés, réponse invalide, aucune lecture récente ou
    coupure plus longue : l'erreur remonte telle quelle, une vraie panne n'est jamais masquée."""
    if not actif():
        return None
    c = config()
    cle = c.get("url")
    with _verrou_recents:
        with _verrou:
            p = _cache_recents.get(cle)
        if p and time.time() - p[0] < 6:
            return p[1]
        with _verrou:
            recent = dict(_incident.get(cle) or {})
        # Un autre appel vient d'échouer pendant qu'on attendait : pas de nouvel essai avant le rythme du cache.
        if recent and time.time() - recent["dernier"] < 6 and p and time.time() - p[0] <= TOLERANCE_RECENTS_S:
            return dict(p[1], perime=True, age_s=int(time.time() - p[0]), cause=recent["cause"], echecs=recent["echecs"],
                        incident_debut=recent["debut"], nouvel_echec=False)
        try:
            a = _rpc(cle, c.get("user"), c.get("pass"), "torrent-get", {"fields": CHAMPS_RECENTS}, delai=DELAI_RECENTS)
        except Exception as e:
            nature = cause(e)
            with _verrou:
                i = _incident.setdefault(cle, {"debut": time.time(), "cause": nature, "echecs": 0})
                i.update(cause=nature, echecs=i["echecs"] + 1, dernier=time.time())
                incident = dict(i)
            e.cause, e.echecs = nature, incident["echecs"]
            if p and nature in ("hors_ligne", "delai") and time.time() - p[0] <= TOLERANCE_RECENTS_S:
                return dict(p[1], perime=True, age_s=int(time.time() - p[0]), cause=nature, echecs=incident["echecs"],
                            incident_debut=incident["debut"], nouvel_echec=True)
            raise
        with _verrou:
            _incident.pop(cle, None)
        out = _lire_recents(a, limite)
        with _verrou:
            _cache_recents[cle] = (time.time(), out)
            _par_hash[cle] = (time.time(), {str(t.get("hashString") or "").lower(): _infos(t)
                                            for t in a.get("torrents") or [] if t.get("hashString")})
        return out


def _infos(t):
    eta = t.get("eta")
    return {"nom": t.get("name"), "etat": ETATS.get(t.get("status"), str(t.get("status"))),
            "progres": round((t.get("percentDone") or 0) * 100, 1), "vitesse": t.get("rateDownload") or 0,
            "restant_s": eta if isinstance(eta, (int, float)) and eta >= 0 else None,
            "taille": t.get("sizeWhenDone") or t.get("totalSize") or 0, "reste": t.get("leftUntilDone") or 0,
            "pairs": t.get("peersConnected") or 0, "bloque": bool(t.get("isStalled")),
            "erreur": (t.get("errorString") or "")[:120] if t.get("error") else ""}


def recents_rapide(limite=20):
    """Lot 2.6.82 : la page Téléchargements répond tout de suite avec la dernière lecture réussie de moins de
    TOLERANCE_RECENTS_S et relance la lecture de la file en arrière plan (un seul torrent-get à la fois, délais inchangés).
    Pendant une coupure, la réponse est marquée perime avec sa cause, comme en 2.6.78. Sans lecture récente, l'appel attend
    Transmission (recents) : une vraie panne remonte toujours, au plus tard après la même tolérance."""
    if not actif():
        return None
    cle = config().get("url")
    with _verrou:
        p = _cache_recents.get(cle)
        incident = dict(_incident.get(cle) or {})
    if not p or time.time() - p[0] > TOLERANCE_RECENTS_S:
        return recents(limite)
    age = time.time() - p[0]
    if age >= 6:
        _relire_en_fond()
    if incident:
        return dict(p[1], perime=True, age_s=int(age), cause=incident["cause"], echecs=incident["echecs"],
                    incident_debut=incident["debut"], nouvel_echec=False)
    return dict(p[1], age_s=int(age))


def _relire_en_fond():
    def relire():
        try:
            recents()
        except Exception:
            pass     # la cause est déjà classée et journalisée par la page Téléchargements ; ici on garde la dernière lecture
    if not _verrou_recents.locked():
        threading.Thread(target=relire, daemon=True).start()


def suivi(hashes):
    """Point 7 du lot 2.6.81 : progression des torrents donnés, lue dans la même lecture de la file que la page Téléchargements
    (une seule source de vérité, un seul torrent-get à la fois). Ne bloque jamais : la dernière lecture sert si elle a moins de
    TOLERANCE_RECENTS_S, et une relecture part en arrière plan dès qu'elle a plus de 6 s. None si Transmission est désactivé ou si
    aucune lecture récente n'existe (l'appelant garde alors Radarr et Sonarr). Un hash absent de Transmission est absent du résultat."""
    hashes = [str(h).lower() for h in hashes or [] if h]
    if not hashes or not actif():
        return None
    cle = config().get("url")
    with _verrou:
        e = _par_hash.get(cle)
    if e is None or time.time() - e[0] >= 6:
        _relire_en_fond()
    if e is None or time.time() - e[0] > TOLERANCE_RECENTS_S:
        return None
    return {h: e[1][h] for h in hashes if h in e[1]}


def _lire_recents(a, limite):
    ts = a.get("torrents") or []
    maintenant = time.time()
    en_cours, termines = [], []
    for t in ts:
        statut, pct = t.get("status"), round((t.get("percentDone") or 0) * 100, 1)
        erreur = (t.get("errorString") or "")[:120] if t.get("error") else ""
        complet = pct >= 100 or statut in (5, 6)
        if erreur:
            libelle = "erreur"
        elif statut == 4:
            libelle = "téléchargement"
        elif statut in (1, 2):
            libelle = "vérification"
        elif statut == 3:
            libelle = "en attente"
        elif statut in (5, 6):
            libelle = "seed"
        elif complet:
            libelle = "terminé"
        else:
            libelle = "en pause"
        item = {"hash": t.get("hashString") or "", "nom": t.get("name") or "Téléchargement sans nom", "etat": libelle, "progres": pct,
                "vitesse": t.get("rateDownload") or 0, "restant_s": t.get("eta") if isinstance(t.get("eta"), (int, float)) and t.get("eta") >= 0 else None,
                "taille": t.get("sizeWhenDone") or t.get("totalSize") or 0, "ratio": t.get("uploadRatio"),
                "ajoute": t.get("addedDate") or 0, "termine": t.get("doneDate") or 0, "erreur": erreur}
        if complet and not erreur:
            date_fin = item["termine"] or item["ajoute"]
            if date_fin and maintenant - date_fin <= 30 * 86400:
                termines.append(item)
        else:
            en_cours.append(item)
    en_cours.sort(key=lambda x: (x["etat"] != "erreur", -x["ajoute"]))
    termines.sort(key=lambda x: -(x["termine"] or x["ajoute"]))
    return {"en_cours": en_cours[:max(1, min(int(limite), 20))], "termines": termines[:15]}


def retirer_torrent(hash_torrent):
    """Retire un torrent de Transmission en conservant toujours ses fichiers téléchargés."""
    h = str(hash_torrent or "").strip().lower()
    if not re.fullmatch(r"[a-f0-9]{40}", h):
        raise ValueError("Identifiant de torrent invalide")
    if not actif():
        raise RuntimeError("Transmission est désactivé ou non configuré")
    c = config()
    _rpc(c["url"], c.get("user"), c.get("pass"), "torrent-remove",
         {"ids": [h], "delete-local-data": False})
    with _verrou:
        _cache.clear()
        _cache_recents.clear()
    return True


def retirer_termines(hashes):
    """Retire en lot les torrents terminés demandés, après revalidation RPC, sans effacer leurs fichiers."""
    if not isinstance(hashes, (list, tuple)) or not hashes or len(hashes) > 15:
        raise ValueError("Sélection de téléchargements terminés invalide")
    hs = list(dict.fromkeys(str(h or "").strip().lower() for h in hashes))
    if any(not re.fullmatch(r"[a-f0-9]{40}", h) for h in hs):
        raise ValueError("Identifiant de torrent invalide")
    if not actif():
        raise RuntimeError("Transmission est désactivé ou non configuré")
    c = config()
    a = _rpc(c["url"], c.get("user"), c.get("pass"), "torrent-get",
             {"fields": ["hashString", "name", "status", "percentDone", "error"], "ids": hs})
    eligibles = []
    for t in a.get("torrents") or []:
        h = str(t.get("hashString") or "").lower()
        if h in hs and not t.get("error") and (t.get("percentDone", 0) >= 1 or t.get("status") in (5, 6)):
            nom = re.sub(r"[\x00-\x1f\x7f]+", " ", str(t.get("name") or "Téléchargement sans nom")).strip()[:160]
            eligibles.append((h, nom or "Téléchargement sans nom"))
    if eligibles:
        _rpc(c["url"], c.get("user"), c.get("pass"), "torrent-remove",
             {"ids": [h for h, _ in eligibles], "delete-local-data": False})
    with _verrou:
        _cache.clear()
        _cache_recents.clear()
    return [nom for _, nom in eligibles]


def duree_txt(s):
    """Temps restant dans le même style que celui de Radarr : 12 min, 2 h 05, 3 j."""
    if s is None:
        return None
    s = int(s)
    if s >= 86400:
        return "%d j" % (s // 86400)
    h, m = s // 3600, (s % 3600) // 60
    return "%d h %02d" % (h, m) if h else "%d min" % max(m, 1)


def resume(infos):
    """Une ligne pour tout le titre (plusieurs torrents possibles, un pack de saison par exemple) : progression pondérée par la taille,
    vitesse totale, temps restant le plus long, et l'état le plus parlant."""
    if not infos:
        return None
    tot = sum(i["taille"] for i in infos.values())
    reste = sum(i["reste"] for i in infos.values())
    p = round((tot - reste) / tot * 100) if tot else round(sum(i["progres"] for i in infos.values()) / len(infos))
    etas = [i["restant_s"] for i in infos.values() if i["restant_s"] is not None]
    return {"progres": max(0, min(100, p)), "vitesse": sum(i["vitesse"] for i in infos.values()),
            "restant_s": max(etas) if etas else None, "etat": next(iter(infos.values()))["etat"],
            "pairs": sum(i["pairs"] for i in infos.values()), "bloque": all(i["bloque"] for i in infos.values()),
            "erreur": next((i["erreur"] for i in infos.values() if i["erreur"]), "")}
