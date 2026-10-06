"""Identifiants des intégrations de l'installation (2.6.106) : Apple TV, pont Hue, NAS Synology.

Source unique à l'exécution : secrets.json du dossier des données (KAMCINE_DATA), le même fichier que les clés TMDB,
Radarr, Sonarr, Overseerr, Transmission, Trakt, YouTube et OMDb. Sections :
  appletvs : Apple TV appairées (2.6.108, plusieurs possibles) : identifiant (pyatv), companion, airplay (identifiants
             d'appairage), nom_detecte (nom annoncé par l'appareil), nom (nom KamCiné, Salon, Chambre…)
  appletv_active : identifiant de l'Apple TV des séances ; appletv : copie de l'Apple TV active, format d'avant la 2.6.108
             (lu par les versions précédentes en cas de retour arrière, et source des identifiants repris sans appareil)
  hue      : adresse, cle, roles (ecran, panneau, salle : identifiants de lumières, voir appairage_hue.py)
  synology : url, utilisateur, mot_de_passe, nom, verifier_tls
Aucune valeur par défaut personnelle : sans section, l'intégration n'est pas configurée. Rien de tout cela n'est jamais
renvoyé à l'interface (seulement « configurée » ou non, GET /installation/etat).

Migration des installations d'avant la 2.6.106 (importer_ancien) : ces identifiants venaient de creds.env (variables
COMP, AIR, HUE_IP, HUE_KEY, SYNOLOGY_*, lues par Docker à la création du conteneur, et relues en fichier par
lumieres.py). Depuis la 2.6.107, KamCiné sait appairer l'Apple TV et le pont Hue lui même (appairage_atv.py,
appairage_hue.py) : creds.env n'est plus qu'une source de migration. Au démarrage du service, ce qui manque dans
secrets.json est importé une seule fois depuis creds.env et l'environnement, puis la date est notée (_import_ancien) :
une relance n'écrase jamais une valeur déjà présente, et une intégration retirée plus tard n'est pas réimportée.
creds.env n'est ni modifié ni supprimé (retour arrière)."""
import json
import os
import secrets as _alea
import threading
import time

import chemins

_verrou = threading.Lock()


def chemin():
    return chemins.donnee("secrets.json")


def lire():
    try:
        with open(chemin(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def ecrire(d):
    """Écriture atomique et privée (0600 dès la création) : jamais un secrets.json à moitié écrit."""
    final = chemin()
    os.makedirs(os.path.dirname(final), exist_ok=True)
    tmp = final + ".%s.tmp" % _alea.token_hex(4)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        os.replace(tmp, final)
        os.chmod(final, 0o600)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def _texte(v):
    return str(v or "").strip()


def _brutes(d):
    """Entrées d'Apple TV telles qu'enregistrées : la liste (2.6.108), sinon l'ancienne section unique."""
    liste = d.get("appletvs")
    if isinstance(liste, list):
        return [x for x in liste if isinstance(x, dict)]
    a = d.get("appletv")
    return [a] if isinstance(a, dict) and (_texte(a.get("companion")) or _texte(a.get("identifiant"))) else []


def _complete(a):
    return bool(_texte(a.get("identifiant")) and _texte(a.get("companion")))


def _nom(a):
    return _texte(a.get("nom")) or _texte(a.get("nom_detecte")) or "Apple TV"


def _active(d):
    completes = [a for a in _brutes(d) if _complete(a)]
    voulu = _texte(d.get("appletv_active")) or _texte((d.get("appletv") or {}).get("identifiant"))
    return next((a for a in completes if _texte(a["identifiant"]) == voulu), completes[0] if completes else None)


def appletv(identifiant=None):
    """L'Apple TV des séances : {identifiant, companion, airplay}, ou None si aucune n'est configurée."""
    d = lire()
    a = _active(d) if not identifiant else next((x for x in _brutes(d) if _complete(x) and _texte(x.get("identifiant")) == _texte(identifiant)), None)
    if not a:
        return None
    return {"identifiant": _texte(a["identifiant"]), "companion": _texte(a["companion"]), "airplay": _texte(a.get("airplay"))}


def appletvs():
    """Apple TV configurées, sans aucun secret : identifiant, nom KamCiné, nom détecté, active."""
    d = lire()
    active = _active(d)
    return [{"identifiant": _texte(a["identifiant"]), "nom": _nom(a), "nom_detecte": _texte(a.get("nom_detecte")) or _texte(a.get("nom")),
             "active": active is not None and a["identifiant"] == active["identifiant"]} for a in _brutes(d) if _complete(a)]


def hue():
    """{adresse, cle} si le pont Hue est configuré, sinon None."""
    h = lire().get("hue") or {}
    adresse, cle = _texte(h.get("adresse")), _texte(h.get("cle"))
    return {"adresse": adresse, "cle": cle} if adresse and cle else None


def hue_roles():
    """Rôles des lumières : {ecran: [ids], panneau: [ids], salle: [ids]}, listes vides par défaut."""
    r = (lire().get("hue") or {}).get("roles") or {}
    return {k: [str(i) for i in (r.get(k) or []) if i] for k in ("ecran", "panneau", "salle")}


def _modifier(fonction):
    with _verrou:
        d = lire()
        fonction(d)
        ecrire(d)


def _ranger(d, liste, active_id):
    """Écrit la liste, l'Apple TV active, et la copie au format d'avant (retour arrière)."""
    liste = [a for a in liste if _complete(a) or _texte(a.get("companion"))]
    d["appletvs"] = liste
    completes = [a for a in liste if _complete(a)]
    active = next((a for a in completes if a["identifiant"] == active_id), completes[0] if completes else None)
    if active:
        d["appletv_active"] = active["identifiant"]
        d["appletv"] = dict(active)
    else:
        d.pop("appletv_active", None)
        if liste:
            d["appletv"] = dict(liste[0])
        else:
            d.pop("appletv", None)


def enregistrer_appletv(identifiant, companion, airplay, nom_detecte=""):
    """Apple TV appairée (ou choisie) : ajoutée, ou mise à jour si elle l'était déjà (son nom KamCiné est gardé). Les
    identifiants repris sans appareil sont alors remplacés. Elle devient active s'il n'y en avait pas encore."""
    def f(d):
        liste = [a for a in _brutes(d) if _complete(a)]
        ident = _texte(identifiant)
        ancienne = next((a for a in liste if a["identifiant"] == ident), {})
        nouvelle = {"identifiant": ident, "companion": _texte(companion), "airplay": _texte(airplay),
                    "nom_detecte": _texte(nom_detecte)[:80] or _texte(ancienne.get("nom_detecte")),
                    "nom": _texte(ancienne.get("nom"))[:40]}
        liste = [a for a in liste if a["identifiant"] != ident] + [nouvelle]
        active = _active(d)
        _ranger(d, liste, active["identifiant"] if active else ident)
    _modifier(f)


def enregistrer_nom_appletv(nom_detecte, identifiant=None):
    """Nom annoncé par l'appareil (test de connexion)."""
    def f(d):
        active = _active(d)
        cible = _texte(identifiant) or (active["identifiant"] if active else "")
        liste = _brutes(d)
        for a in liste:
            if _texte(a.get("identifiant")) == cible:
                a["nom_detecte"] = _texte(nom_detecte)[:80]
        _ranger(d, liste, active["identifiant"] if active else "")
    _modifier(f)


def renommer_appletv(identifiant, nom):
    """Nom KamCiné (Salon, Chambre…) ; vide : le nom détecté."""
    def f(d):
        liste, active = _brutes(d), _active(d)
        if not any(_texte(a.get("identifiant")) == _texte(identifiant) for a in liste):
            raise KeyError("Apple TV inconnue")
        for a in liste:
            if _texte(a.get("identifiant")) == _texte(identifiant):
                a["nom"] = " ".join(str(nom or "").split())[:40]
        _ranger(d, liste, active["identifiant"] if active else "")
    _modifier(f)


def activer_appletv(identifiant):
    """L'Apple TV utilisée par les séances. Renvoie True si elle a changé."""
    change = []
    def f(d):
        liste, active = _brutes(d), _active(d)
        if not any(_complete(a) and a["identifiant"] == _texte(identifiant) for a in liste):
            raise KeyError("Apple TV inconnue")
        change.append(not active or active["identifiant"] != _texte(identifiant))
        _ranger(d, liste, _texte(identifiant))
    _modifier(f)
    return change[0]


def retirer_appletv(identifiant):
    """Oublie une Apple TV (sur confirmation de l'administrateur). Si c'était l'active, la suivante le devient."""
    def f(d):
        liste, active = _brutes(d), _active(d)
        reste = [a for a in liste if _texte(a.get("identifiant")) != _texte(identifiant)]
        garde = active["identifiant"] if active and active["identifiant"] != _texte(identifiant) else ""
        _ranger(d, reste, garde)
    _modifier(f)


def oublier_appletv():
    _modifier(lambda d: [d.pop(k, None) for k in ("appletv", "appletvs", "appletv_active")])


def enregistrer_hue(adresse, cle):
    """Nouvelle clé du pont ; les rôles sont gardés si l'adresse ne change pas."""
    def f(d):
        ancien = d.get("hue") or {}
        roles = ancien.get("roles") if _texte(ancien.get("adresse")) == _texte(adresse) else {}
        d["hue"] = {"adresse": _texte(adresse), "cle": _texte(cle), "roles": roles or {}}
    _modifier(f)


def enregistrer_roles_hue(roles):
    def f(d):
        if d.get("hue"):
            d["hue"]["roles"] = {k: list(roles.get(k) or []) for k in ("ecran", "panneau", "salle")}
    _modifier(f)


def oublier_hue():
    _modifier(lambda d: d.pop("hue", None))


def etat_public():
    """Ce que l'interface peut savoir, sans aucun secret : Apple TV (configurée, identifiants repris sans appareil
    choisi, nom) et Hue (configuré, lumières associées ou non)."""
    d = lire()
    h = d.get("hue") or {}
    roles, active, liste = hue_roles(), _active(d), appletvs()
    a_choisir = any(_texte(a.get("companion")) and not _texte(a.get("identifiant")) for a in _brutes(d))
    return {"appletv": {"configuree": active is not None, "a_choisir": a_choisir and active is None,
                        "nom": _nom(active) if active else "", "appareils": liste},
            "hue": {"configuree": hue() is not None, "roles_associes": any(roles.values()),
                    "adresse": _texte(h.get("adresse")) if hue() else ""}}


def synology():
    """Section synology de secrets.json, sinon les variables SYNOLOGY_* (optionnel, informations du NAS seulement)."""
    s = lire().get("synology") or {}
    if _texte(s.get("url")):
        return {"url": _texte(s.get("url")), "utilisateur": _texte(s.get("utilisateur")), "mot_de_passe": str(s.get("mot_de_passe") or ""),
                "nom": _texte(s.get("nom")) or "NAS Synology", "verifier_tls": s.get("verifier_tls", True) is not False}
    e = os.environ
    return {"url": _texte(e.get("SYNOLOGY_URL")), "utilisateur": _texte(e.get("SYNOLOGY_USER")),
            "mot_de_passe": e.get("SYNOLOGY_PASSWORD", ""), "nom": _texte(e.get("SYNOLOGY_NAME")) or "NAS Synology",
            "verifier_tls": _texte(e.get("SYNOLOGY_VERIFY_TLS") or "1").lower() not in ("0", "false", "no")}


def enregistrer_synology(url, utilisateur, mot_de_passe, nom="", verifier_tls=True):
    """Enregistre la configuration DSM dans secrets.json, sans toucher aux autres intégrations."""
    def f(d):
        ancien = d.get("synology") if isinstance(d.get("synology"), dict) else {}
        mdp = str(mot_de_passe or "") or str(ancien.get("mot_de_passe") or "")
        d["synology"] = {"url": _texte(url).rstrip("/"), "utilisateur": _texte(utilisateur),
                          "mot_de_passe": mdp, "nom": _texte(nom) or "NAS Synology",
                          "verifier_tls": bool(verifier_tls)}
    _modifier(f)


def retirer_synology():
    _modifier(lambda d: d.pop("synology", None))


def env_seance(env, identifiant=None):
    """Variables données à seance.py : une cible explicite de séance est prioritaire au choix global historique.
    Cette sélection n'écrit jamais dans secrets.json ; elle appartient à l'appelant (compte ou séance)."""
    a = appletv(identifiant)
    if a:
        env.update(ATV_ID=a["identifiant"], COMP=a["companion"], AIR=a["airplay"])
    return env


def _creds_env():
    """creds.env d'une installation historique (dossier des données), en lecture seule. Lignes CLE=VALEUR."""
    out = {}
    try:
        with open(chemins.donnee("creds.env"), encoding="utf-8") as f:
            for ligne in f:
                ligne = ligne.strip()
                if not ligne or ligne.startswith("#") or "=" not in ligne:
                    continue
                k, v = ligne.split("=", 1)
                k = k.strip()
                if k.startswith("export "):
                    k = k[7:].strip()
                out[k] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def importer_ancien(environ=None):
    """Importe une seule fois, dans secrets.json, les identifiants d'une installation d'avant la 2.6.106 (creds.env et
    environnement du conteneur). Ne remplit que ce qui manque ; ne fait rien si l'import a déjà eu lieu. Renvoie la liste
    des sections importées (vide si rien à faire). Aucune valeur n'est journalisée."""
    environ = os.environ if environ is None else environ
    with _verrou:
        d = lire()
        if d.get("_import_ancien"):
            return []
        fichier = _creds_env()
        source = lambda k: _texte(fichier.get(k) or environ.get(k))
        importees = []
        if not d.get("appletv") and source("COMP"):
            # Sans ATV_ID (le cas des anciennes installations, où l'identifiant était dans le code), les identifiants
            # d'appairage sont gardés et l'administrateur choisit son Apple TV dans KamCiné (appairage_atv.choisir).
            d["appletv"] = {"identifiant": source("ATV_ID"), "companion": source("COMP"), "airplay": source("AIR")}
            importees.append("appletv")
        if not d.get("hue") and source("HUE_IP") and source("HUE_KEY"):
            d["hue"] = {"adresse": source("HUE_IP"), "cle": source("HUE_KEY")}
            importees.append("hue")
        if not d.get("synology") and source("SYNOLOGY_URL"):
            d["synology"] = {"url": source("SYNOLOGY_URL"), "utilisateur": source("SYNOLOGY_USER"),
                             "mot_de_passe": fichier.get("SYNOLOGY_PASSWORD") or environ.get("SYNOLOGY_PASSWORD", ""),
                             "nom": source("SYNOLOGY_NAME") or "NAS Synology",
                             "verifier_tls": (source("SYNOLOGY_VERIFY_TLS") or "1").lower() not in ("0", "false", "no")}
            importees.append("synology")
        anciennes = any(source(k) for k in ("COMP", "AIR", "HUE_IP", "HUE_KEY", "SYNOLOGY_URL"))
        if not anciennes:
            return []                  # installation vierge : rien à importer, aucun fichier écrit
        d["_import_ancien"] = {"fait_a": time.time(), "sections": importees}
        ecrire(d)
        return importees
