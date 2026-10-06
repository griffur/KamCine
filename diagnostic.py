"""Mesure des sources de la bibliothèque : chronomètre chaque appel séparément.

Lancé en arrière plan (la mesure peut durer plusieurs minutes si une source est lente), le résultat
se lit en rechargeant la route. Rien n'est mis en cache par ce module et aucun secret n'est renvoyé."""
import threading, time
import requests
import bibliotheque, trakt
from config import charger

DELAI_MAX = 90


def _delais():
    """Délais de production (réglages), pour dire si une source les dépasse : liste Radarr et Sonarr, Trakt, Overseerr.
    Ce sont des délais d'attente de lecture (temps avant le premier octet), pas des durées totales."""
    c = charger()
    return c["badges_delai_liste_s"], c["badges_delai_trakt_s"], c["badges_delai_overseerr_s"]

_etat = {"en_cours": False, "debut": None, "fin": None, "etapes": []}
_verrou = threading.Lock()


def _ajouter(etape):
    with _verrou:
        _etat["etapes"].append(etape)


def _chrono(nom, fn):
    t0 = time.time()
    e = {"nom": nom}
    try:
        e.update(fn())
        e["ok"] = True
    except Exception as ex:
        e.update(ok=False, erreur="%s : %s" % (type(ex).__name__, str(ex)[:100]))
    e["duree_s"] = round(time.time() - t0, 2)
    _ajouter(e)


def _liste_arr(nom):
    c = bibliotheque.config().get(nom) or {}
    if not c.get("cle"):
        return {"configuree": False}
    radarr = nom.startswith("radarr")
    r = requests.get(c["url"] + "/api/v3" + ("/movie" if radarr else "/series"),
                     headers={"X-Api-Key": c["cle"]}, timeout=DELAI_MAX)
    r.raise_for_status()
    t1 = time.time()
    items = r.json()
    if radarr:
        avec = sum(1 for x in items if x.get("hasFile"))
    else:
        avec = sum(1 for x in items if (x.get("statistics") or {}).get("episodeFileCount"))
    attente = round(r.elapsed.total_seconds(), 2)
    return {"configuree": True, "attente_premier_octet_s": attente, "octets": len(r.content),
            "lecture_json_s": round(time.time() - t1, 2), "elements": len(items),
            "avec_fichier": avec, "depasse_delai_actuel": attente > _delais()[0]}


def _file_arr(nom):
    c = bibliotheque.config().get(nom) or {}
    if not c.get("cle"):
        return {"configuree": False}
    r = requests.get(c["url"] + "/api/v3/queue", params={"page": 1, "pageSize": 200},
                     headers={"X-Api-Key": c["cle"]}, timeout=DELAI_MAX)
    r.raise_for_status()
    return {"configuree": True, "elements": len(r.json().get("records", [])), "octets": len(r.content)}


def _overseerr_pages():
    if not bibliotheque.ov_cle():
        return {"configuree": False}
    pages, total = [], 0
    for i in range(6):
        t0 = time.time()
        r = bibliotheque.ov_appel("GET", "/media?take=100&skip=%d&sort=added" % (i * 100), delai=DELAI_MAX)
        r.raise_for_status()
        res = r.json().get("results") or []
        duree = round(time.time() - t0, 2)
        pages.append({"page": i + 1, "elements": len(res), "duree_s": duree, "octets": len(r.content),
                      "depasse_delai_actuel": duree > _delais()[2]})
        total += len(res)
        if len(res) < 100:
            break
    return {"configuree": True, "pages": pages, "elements": total}


def _overseerr_titre():
    if not bibliotheque.ov_cle():
        return {"configuree": False}
    t0 = time.time()
    r = bibliotheque.ov_appel("GET", "/movie/603", delai=DELAI_MAX)
    return {"configuree": True, "code": r.status_code, "duree_s": round(time.time() - t0, 2)}


def _trakt(chemin):
    if not trakt.connectee():
        return {"connectee": False}
    r = requests.get(trakt.API + chemin, headers=trakt._entetes(), timeout=DELAI_MAX)
    r.raise_for_status()
    attente = round(r.elapsed.total_seconds(), 2)
    return {"connectee": True, "attente_premier_octet_s": attente, "octets": len(r.content),
            "elements": len(r.json()), "depasse_delai_actuel": attente > _delais()[1]}


def _mesure():
    for nom in bibliotheque.NOMS:
        _chrono("liste " + nom, lambda nom=nom: _liste_arr(nom))
    for nom in bibliotheque.NOMS:
        _chrono("file d'attente " + nom, lambda nom=nom: _file_arr(nom))
    _chrono("overseerr, pages de la liste complète", _overseerr_pages)
    _chrono("overseerr, une fiche de titre", _overseerr_titre)
    _chrono("trakt, films vus", lambda: _trakt("/sync/watched/movies"))
    _chrono("trakt, séries vues", lambda: _trakt("/sync/watched/shows"))


def _run():
    try:
        _mesure()
    finally:
        with _verrou:
            _etat.update(en_cours=False, fin=time.time())


def _ligne(e):
    """Une ligne lisible par étape, pour pouvoir la recopier telle quelle."""
    if not e.get("ok"):
        return "%s : ERREUR après %s s (%s)" % (e["nom"], e["duree_s"], e.get("erreur"))
    if e.get("configuree") is False:
        return "%s : non configuré" % e["nom"]
    if e.get("connectee") is False:
        return "%s : Trakt non connecté" % e["nom"]
    txt = "%s : %s s au total" % (e["nom"], e["duree_s"])
    if "attente_premier_octet_s" in e:
        txt += ", %s s avant le premier octet" % e["attente_premier_octet_s"]
    if "elements" in e:
        txt += ", %s éléments" % e["elements"]
    if "avec_fichier" in e:
        txt += " dont %s avec fichier" % e["avec_fichier"]
    if e.get("octets"):
        txt += ", %.1f Mo" % (e["octets"] / 1048576.0)
    if e.get("depasse_delai_actuel") or any(p.get("depasse_delai_actuel") for p in e.get("pages", [])):
        txt += " (DÉPASSE le délai réglé, à augmenter dans Réglages)"
    return txt


def etat(relancer=False):
    """Résultat de la dernière mesure. Lance une mesure s'il n'y en a pas encore ou si relancer est vrai."""
    with _verrou:
        demarrer = not _etat["en_cours"] and (relancer or _etat["debut"] is None)
        if demarrer:
            _etat.update(en_cours=True, debut=time.time(), fin=None, etapes=[])
    if demarrer:
        threading.Thread(target=_run, daemon=True).start()
    with _verrou:
        out = {"ok": True, "en_cours": _etat["en_cours"], "resume": [_ligne(e) for e in _etat["etapes"]],
               "etapes": list(_etat["etapes"]),
               "delais_actuels_s": dict(zip(("liste_radarr_sonarr", "trakt", "overseerr"), _delais())),
               "source_des_badges": charger()["badges_source"]}
        if _etat["debut"]:
            out["depuis_s"] = round((_etat["fin"] or time.time()) - _etat["debut"])
    out["cache_actuel"] = bibliotheque.etat_cache()
    out["message"] = ("Mesure en cours, recharge cette page dans une minute." if out["en_cours"]
                      else "Mesure terminée. Ajoute ?relancer=1 à l'adresse pour recommencer.")
    return out
