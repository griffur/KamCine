"""Configuration de l'Apple TV depuis KamCiné (2.6.107) : découverte, choix, appairage, test.

KamCiné pilote l'Apple TV avec deux protocoles pyatv et garde leurs deux identifiants d'appairage : Companion (lancement
d'apps, télécommande) et AirPlay (lecture, métadonnées). Les deux sont nécessaires : l'Apple TV affiche donc un premier code
(Companion), puis, une fois celui ci accepté, un second (AirPlay). Plusieurs Apple TV peuvent être appairées (2.6.108) ;
une seule est active, celle des séances. L'appairage suit l'API officielle de pyatv : pyatv.scan, puis
pour chaque protocole pyatv.pair, begin, pin (code affiché par l'Apple TV), finish. Les identifiants obtenus ne sont
enregistrés (integrations.enregistrer_appletv) qu'une fois les deux protocoles appairés : une Apple TV déjà configurée
reste en place jusque là. Rien de secret n'est renvoyé : seulement nom, identifiant, adresse, modèle et étape.

Les objets pyatv sont liés à leur boucle asyncio : une boucle dédiée (fil à part) garde la session d'appairage d'une
requête HTTP à l'autre. Une seule session à la fois, abandonnée après DUREE_SESSION."""
import asyncio
import secrets
import threading
import time

import integrations

DELAI_SCAN, DELAI_ETAPE, DUREE_SESSION = 6, 20, 300
PROTOCOLES = ("Companion", "AirPlay")          # ordre de l'appairage ; les deux sont nécessaires
_boucle = None
_verrou = threading.Lock()
TROUVEES = {}                                  # identifiant -> configuration pyatv de la dernière recherche
SESSION = {}


class Echec(Exception):
    """Situation normale à expliquer à l'administrateur (aucun appareil, mauvais code, délai…), jamais une panne."""


def _executer(coro, delai):
    global _boucle
    with _verrou:
        if _boucle is None:
            _boucle = asyncio.new_event_loop()
            threading.Thread(target=_boucle.run_forever, daemon=True, name="appairage-atv").start()
    try:
        return asyncio.run_coroutine_threadsafe(coro, _boucle).result(delai)
    except asyncio.TimeoutError:
        raise Echec("L’Apple TV ne répond pas dans le délai. Vérifiez qu’elle est allumée, puis réessayez.")
    except Echec:
        raise
    except Exception as e:
        texte = str(e) or e.__class__.__name__
        raise Echec("Échec avec l’Apple TV : " + texte[:120])


def _modele(conf):
    try:
        return str(conf.device_info.model_str if hasattr(conf.device_info, "model_str") else conf.device_info.model)
    except Exception:
        return ""


def _publique(conf):
    return {"identifiant": str(conf.identifier), "nom": str(conf.name or "Apple TV"), "adresse": str(conf.address or ""), "modele": _modele(conf)}


def rechercher():
    """Apple TV visibles sur le réseau local (mDNS, pyatv.scan). Liste vide si rien n'est trouvé."""
    import pyatv
    from pyatv.const import Protocol

    async def scan():
        return await pyatv.scan(asyncio.get_running_loop(), timeout=DELAI_SCAN)
    confs = _executer(scan(), DELAI_SCAN + 10)
    TROUVEES.clear()
    deja = {a["identifiant"] for a in integrations.appletvs()}
    out = []
    for c in confs:
        if c.get_service(Protocol.Companion) is None:
            continue                            # KamCiné a besoin de Companion : pas un appareil pilotable
        TROUVEES[str(c.identifier)] = c
        out.append(dict(_publique(c), configuree=str(c.identifier) in deja))
    return out


def _conf(identifiant):
    c = TROUVEES.get(str(identifiant or ""))
    if c is None:
        raise Echec("Cette Apple TV n’est plus dans la liste. Lancez une nouvelle recherche.")
    return c


def annuler():
    s = dict(SESSION)
    SESSION.clear()
    if s.get("handler") is not None:
        async def fermer():
            await s["handler"].close()
        try:
            _executer(fermer(), 5)
        except Echec:
            pass
    return {"etape": "annule"}


def _commencer(protocole):
    import pyatv
    from pyatv.const import Protocol

    async def debut():
        h = await pyatv.pair(SESSION["conf"], getattr(Protocol, protocole), asyncio.get_running_loop(), name="KamCiné")
        await h.begin()
        return h
    SESSION.update(handler=_executer(debut(), DELAI_ETAPE), protocole=protocole)
    if not SESSION["handler"].device_provides_pin:
        return _terminer_protocole(None)
    numero = PROTOCOLES.index(protocole) + 1
    return {"etape": "pin", "session": SESSION["id"], "protocole": protocole, "numero": numero, "total": len(PROTOCOLES),
            "precedent_accepte": numero > 1, "appareil": _publique(SESSION["conf"])}


def appairer(identifiant):
    """Commence l'appairage de l'Apple TV choisie (Companion, puis AirPlay). Remplace toute session en cours."""
    annuler()
    SESSION.update(id=secrets.token_urlsafe(12), conf=_conf(identifiant), identifiants={}, debut=time.time())
    try:
        return _commencer(PROTOCOLES[0])
    except Echec:
        annuler()
        raise


def _terminer_protocole(pin):
    h = SESSION["handler"]

    async def fin():
        if pin is not None:
            h.pin(int(pin))
        await h.finish()
        return h.has_paired, (h.service.credentials if h.has_paired else None)
    ok, cred = _executer(fin(), DELAI_ETAPE)
    if not ok or not cred:
        raise Echec("Code refusé par l’Apple TV. Recommencez l’appairage et saisissez le nouveau code affiché.")
    SESSION["identifiants"][SESSION["protocole"]] = cred
    suivant = PROTOCOLES.index(SESSION["protocole"]) + 1
    try:
        _executer(h.close(), 5)
    except Echec:
        pass
    if suivant < len(PROTOCOLES):
        return _commencer(PROTOCOLES[suivant])
    conf, creds = SESSION["conf"], SESSION["identifiants"]
    integrations.enregistrer_appletv(str(conf.identifier), creds["Companion"], creds["AirPlay"], str(conf.name or ""))
    SESSION.clear()
    return {"etape": "fini", "appareil": _publique(conf)}


def saisir_pin(session, pin):
    if not SESSION or SESSION.get("id") != session or time.time() - SESSION.get("debut", 0) > DUREE_SESSION:
        annuler()
        raise Echec("L’appairage a expiré. Recommencez depuis la recherche.")
    pin = str(pin or "").strip()
    if not pin.isdigit() or not 4 <= len(pin) <= 8:
        raise Echec("Saisissez le code à 4 chiffres affiché par l’Apple TV.")
    try:
        return _terminer_protocole(pin)
    except Echec:
        annuler()
        raise


async def _connecter(identifiant, companion, airplay):
    import pyatv
    from pyatv.const import Protocol
    loop = asyncio.get_running_loop()
    confs = await pyatv.scan(loop, identifier=identifiant, timeout=DELAI_SCAN)
    if not confs:
        raise Echec("Apple TV introuvable. Vérifiez qu’elle est allumée et connectée au même réseau.")
    conf = confs[0]
    conf.set_credentials(Protocol.Companion, companion)
    if airplay:
        conf.set_credentials(Protocol.AirPlay, airplay)
    atv = await pyatv.connect(conf, loop)
    try:
        return str(conf.name or "Apple TV")
    finally:
        taches = atv.close()
        if taches:
            await asyncio.gather(*taches)


def tester():
    """Connexion réelle avec l'Apple TV configurée. Le nom détecté est gardé pour l'affichage."""
    a = integrations.appletv()
    if not a:
        raise Echec("Aucune Apple TV n’est configurée.")
    nom = _executer(_connecter(a["identifiant"], a["companion"], a["airplay"]), DELAI_SCAN + DELAI_ETAPE)
    integrations.enregistrer_nom_appletv(nom)
    return {"ok": True, "nom": nom}


def choisir(identifiant):
    """Installation reprise de l'ancienne configuration (identifiants d'appairage sans identifiant d'appareil) : l'Apple TV
    choisie est gardée si elle accepte les identifiants existants. Aucun nouvel appairage."""
    a = integrations.lire().get("appletv") or {}
    if not a.get("companion"):
        raise Echec("Aucun identifiant d’appairage existant : appairez cette Apple TV.")
    conf = _conf(identifiant)
    nom = _executer(_connecter(str(conf.identifier), a["companion"], a.get("airplay", "")), DELAI_SCAN + DELAI_ETAPE)
    integrations.enregistrer_appletv(str(conf.identifier), a["companion"], a.get("airplay", ""), nom)
    return {"ok": True, "nom": nom}
