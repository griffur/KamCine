import json, os, threading, time

import json_atomique
import chemins
BASE = chemins.data_dir()  # dossier des données (2.6.101)
HIST = os.path.join(BASE, "historique_vus.json")
PROG = os.path.join(BASE, "progressions.json")
COMPTEURS = os.path.join(BASE, "compteurs.json")
_verrou = threading.Lock()


def _lire(chemin, defaut):
    try:
        with open(chemin, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return defaut


def _ecrire(chemin, data):
    with _verrou:
        json_atomique.ecrire(chemin, data)


def cle(type_, id_, saison=None, episode=None):
    return "%s:%s:%s:%s" % (type_, id_, saison or 0, episode or 0)


def _cle_entree(x):
    return cle(x.get("type"), x.get("id"), x.get("saison"), x.get("episode"))


def historique():
    return _lire(HIST, [])


def est_vu(type_, id_, saison=None, episode=None):
    k = cle(type_, id_, saison, episode)
    return any(_cle_entree(x) == k for x in historique())


def films_vus():
    return {x["id"] for x in historique() if x.get("type") == "movie"}


def series_vues():
    return {x["id"] for x in historique() if x.get("type") == "tv"}


def episodes_vus(id_, saison):
    return {x["episode"] for x in historique() if x.get("type") == "tv" and x.get("id") == id_ and x.get("saison") == saison}


def marquer_vu(entree):
    entree = dict(entree)
    entree.setdefault("t", time.time())
    k = _cle_entree(entree)
    h = [x for x in historique() if _cle_entree(x) != k]
    h.insert(0, entree)
    _ecrire(HIST, h[:5000])
    p = _lire(PROG, {})
    if p.pop(k, None) is not None:
        _ecrire(PROG, p)


def marquer_plusieurs(entrees):
    """Plusieurs entrées d'un coup (une série entière), une seule écriture. Les épisodes déjà vus gardent leur place, la série remonte."""
    entrees = [dict(e) for e in entrees]
    nouvelles = {_cle_entree(e) for e in entrees}
    maintenant = time.time()
    for e in entrees:
        e.setdefault("t", maintenant)
    h = [x for x in historique() if _cle_entree(x) not in nouvelles]
    _ecrire(HIST, (entrees + h)[:5000])
    p = _lire(PROG, {})
    if any(p.pop(k, None) is not None for k in nouvelles):
        _ecrire(PROG, p)


def retirer_serie(id_):
    """Retire tous les épisodes vus d'une série (la coche Marquer la série comme non vue)."""
    _ecrire(HIST, [x for x in historique() if not (x.get("type") == "tv" and x.get("id") == id_)])
    p = _lire(PROG, {})
    restes = {k: v for k, v in p.items() if not k.startswith("tv:%s:" % id_)}
    if len(restes) != len(p):
        _ecrire(PROG, restes)


def retirer_vu(type_, id_, saison=None, episode=None):
    k = cle(type_, id_, saison, episode)
    _ecrire(HIST, [x for x in historique() if _cle_entree(x) != k])


def enregistrer_progression(entree):
    p = _lire(PROG, {})
    entree = dict(entree, t=time.time())
    p[_cle_entree(entree)] = entree
    if len(p) > 60:
        for k in sorted(p, key=lambda k: p[k]["t"])[: len(p) - 60]:
            p.pop(k)
    _ecrire(PROG, p)


def progressions_catalogue(elements, vus_films=None, vus_episodes=None, maintenant=None):
    """Progressions locales de posters, sans réseau et en lot.

    Les films et séries ne sont renvoyés que pour une progression récente et inachevée.
    Pour une série, la progression la plus récente d'un épisode non vu représente la série ;
    un épisode déjà vu n'efface donc pas la progression de l'épisode suivant.
    """
    maintenant = time.time() if maintenant is None else maintenant
    vus_films = vus_films or set()
    vus_episodes = vus_episodes or set()
    demandes = set(elements)
    progression = _lire(PROG, {})
    resultat = {}
    for entree in progression.values():
        try:
            type_ = "tv" if entree.get("type") == "tv" else "movie"
            identifiant = int(entree.get("id") or 0)
            total, position = float(entree.get("total") or 0), float(entree.get("pos") or 0)
            date = float(entree.get("t") or 0)
            saison, episode = int(entree.get("saison") or 0), int(entree.get("episode") or 0)
        except (TypeError, ValueError, AttributeError):
            continue
        cle_media = "%s-%d" % (type_, identifiant)
        if not identifiant or cle_media not in demandes or total <= 0 or position <= 0:
            continue
        if maintenant - date > 60 * 86400 or date > maintenant + 60:
            continue
        pct = position / total
        if not 0.03 <= pct < 1.0:
            continue
        if type_ == "movie" and identifiant in vus_films:
            continue
        if type_ == "tv" and (identifiant, saison, episode) in vus_episodes:
            continue
        ancien = resultat.get(cle_media)
        if ancien is None or date > ancien[0]:
            resultat[cle_media] = (date, round(pct * 100))
    return {cle: valeur[1] for cle, valeur in resultat.items()}


MASQUES = os.path.join(BASE, "reprendre_masques.json")


def cle_reprise(type_, id_):
    """Un film ou une série n'a qu'une entrée dans Continuer la séance : la clé ne contient ni saison ni épisode."""
    return "%s:%s" % (type_, id_)


def a_reprendre():
    """Ce qui est à reprendre : une seule entrée par film et par série (celle de l'épisode le plus récemment regardé, avec sa saison et son épisode),
    sauf ce que l'utilisateur a retiré de la liste. Un retrait ne touche ni à l'historique vu ni aux statistiques : il est noté dans reprendre_masques.json
    avec l'heure, et l'entrée reparaît si une nouvelle progression est enregistrée plus tard.

    La position vient du dernier /film reçu par un navigateur ouvert (voir app/main.py, suivre()) : si personne n'a regardé l'écran pendant que
    le film continuait (téléphone verrouillé, app fermée, lecture jamais pilotée), elle ne bouge plus alors que le film, lui, a pu finir. Un film
    qui a eu largement le temps de se terminer depuis la dernière position connue (marge de 6 h, pour absorber une vraie pause) est retiré ici et
    nettoyé du fichier : mieux vaut ne rien proposer qu'une reprise à un endroit que le film a dépassé depuis longtemps."""
    p = _lire(PROG, {})
    v = {_cle_entree(x) for x in historique()}
    maintenant, perimes, groupe = time.time(), [], {}
    for k, x in p.items():
        total = x.get("total") or 0
        if not total or k in v:
            continue
        if maintenant - x.get("t", 0) > (total - x.get("pos", 0)) + 6 * 3600:
            perimes.append(k)
            continue
        pct = x.get("pos", 0) / total
        if 0.03 <= pct < 0.9 and maintenant - x.get("t", 0) < 60 * 86400:
            e = dict(x, pourcent=round(pct * 100))
            g = cle_reprise(e.get("type"), e.get("id"))
            if g not in groupe or e["t"] > groupe[g]["t"]:
                groupe[g] = e
    if perimes:
        _ecrire(PROG, {k: x for k, x in p.items() if k not in perimes})
    masques = _lire(MASQUES, {})
    out = [e for g, e in groupe.items() if e["t"] > masques.get(g, 0)]
    out.sort(key=lambda x: x["t"], reverse=True)
    return out[:10]


def retirer_reprise(type_, id_):
    """Masque un film ou une série de Continuer la séance. Renvoie la clé."""
    m = _lire(MASQUES, {})
    g = cle_reprise(type_, id_)
    m[g] = time.time()
    if len(m) > 200:
        for k in sorted(m, key=lambda k: m[k])[: len(m) - 200]:
            m.pop(k)
    _ecrire(MASQUES, m)
    return g


def annuler_retrait_reprise(type_, id_):
    m = _lire(MASQUES, {})
    ok = m.pop(cle_reprise(type_, id_), None) is not None
    if ok:
        _ecrire(MASQUES, m)
    return ok


def incrementer_seances():
    c = _lire(COMPTEURS, {"seances": 0})
    c["seances"] = c.get("seances", 0) + 1
    _ecrire(COMPTEURS, c)


def stats():
    h = historique()
    films = [x for x in h if x.get("type") == "movie"]
    eps = [x for x in h if x.get("type") == "tv"]
    minutes = sum((x.get("duree") or 0) for x in h)
    mois = []
    maintenant = time.localtime()
    for i in range(5, -1, -1):
        m = maintenant.tm_mon - i
        a = maintenant.tm_year
        while m <= 0:
            m += 12
            a -= 1
        n = sum(1 for x in h if time.localtime(x["t"]).tm_year == a and time.localtime(x["t"]).tm_mon == m)
        mois.append({"annee": a, "mois": m, "n": n})
    return {"films": len({x["id"] for x in films}), "episodes": len(eps), "series": len({x["id"] for x in eps}),
            "minutes": int(minutes), "seances": _lire(COMPTEURS, {}).get("seances", 0), "mois": mois}
