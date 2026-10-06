"""Configuration du pont Philips Hue depuis KamCiné (2.6.107) : recherche, bouton du pont, clé, lumières, rôles.

Recherche : le service de découverte de Philips (discovery.meethue.com, adresse réglable par HUE_DISCOVERY_URL pour les
tests) donne les ponts du réseau ; sinon l'administrateur saisit l'adresse. Association : l'API du pont (POST /api avec
devicetype) répond « bouton non appuyé » (erreur 101) tant que le bouton rond du pont n'a pas été pressé, puis une clé
d'application (username), la même que la clé CLIP v2 utilisée par lumieres.py. Adresse, clé et rôles des lumières vont
dans secrets.json (integrations.py) ; la clé ne sort jamais vers le navigateur.

Rôles des lumières (ceux dont lumieres.py a réellement besoin) : ecran, les lumières autour de l'écran (niveaux de
départ, bandes annonces, entracte, générique) ; panneau, l'enseigne ou le panneau lumineux (mêmes niveaux, éteint au
générique) ; salle, la lumière de la pièce, éteinte pendant la séance."""
import os
import re

import requests
import urllib3

import integrations

urllib3.disable_warnings()
DECOUVERTE = os.environ.get("HUE_DISCOVERY_URL", "https://discovery.meethue.com/")
SCHEMA = os.environ.get("HUE_SCHEME", "https")      # http seulement pour les tests (faux pont)
ROLES = ("ecran", "panneau", "salle")
ADRESSE = re.compile(r"^[A-Za-z0-9.\-]{1,60}(:\d{1,5})?$")
IDENTIFIANT = re.compile(r"^[A-Za-z0-9\-]{1,64}$")


class Echec(Exception):
    """Situation normale à expliquer (pont absent, bouton non pressé, délai…), jamais une panne serveur."""


def adresse_valide(adresse):
    adresse = str(adresse or "").strip()
    if not ADRESSE.match(adresse):
        raise Echec("Adresse du pont invalide : une adresse IP ou un nom, par exemple 192.168.1.20.")
    return adresse


def rechercher():
    """Ponts annoncés par le service de découverte de Philips. Liste vide si rien n'est trouvé ou sans Internet."""
    try:
        r = requests.get(DECOUVERTE, timeout=6)
        brut = r.json() if r.ok else []
    except (requests.RequestException, ValueError):
        return []
    out = []
    for p in brut if isinstance(brut, list) else []:
        ip = str((p or {}).get("internalipaddress") or "")
        if ADRESSE.match(ip):
            out.append({"adresse": ip, "identifiant": str(p.get("id") or "")[:40]})
    return out


def associer(adresse):
    """Demande une clé au pont. {etat: "bouton"} tant que le bouton n'est pas pressé, {etat: "fini"} une fois la clé
    enregistrée. Les anciennes associations de rôles sont gardées si le pont est le même."""
    adresse = adresse_valide(adresse)
    try:
        r = requests.post("%s://%s/api" % (SCHEMA, adresse), json={"devicetype": "kamcine#serveur", "generateclientkey": True},
                          verify=False, timeout=6)
        rep = r.json()
    except requests.Timeout:
        raise Echec("Le pont Hue ne répond pas à cette adresse. Vérifiez qu’il est allumé et sur le même réseau.")
    except (requests.RequestException, ValueError):
        raise Echec("Aucun pont Hue ne répond à cette adresse.")
    rep = rep[0] if isinstance(rep, list) and rep else {}
    if "success" in rep and rep["success"].get("username"):
        integrations.enregistrer_hue(adresse, rep["success"]["username"])
        return {"etat": "fini", "lumieres": lumieres()}
    erreur = rep.get("error") or {}
    if erreur.get("type") == 101:
        return {"etat": "bouton"}
    raise Echec("Le pont Hue a refusé l’association : " + str(erreur.get("description") or "réponse inattendue")[:100])


def _appel(chemin):
    h = integrations.hue()
    if not h:
        raise Echec("Aucun pont Hue n’est configuré.")
    try:
        r = requests.get("%s://%s/clip/v2/resource/%s" % (SCHEMA, h["adresse"], chemin), headers={"hue-application-key": h["cle"]},
                         verify=False, timeout=6)
    except requests.RequestException:
        raise Echec("Le pont Hue ne répond pas. Vérifiez qu’il est allumé et sur le même réseau.")
    if r.status_code in (401, 403):
        raise Echec("Le pont Hue refuse la clé enregistrée : associez de nouveau le pont.")
    try:
        return r.json().get("data") or []
    except ValueError:
        raise Echec("Réponse inattendue du pont Hue.")


def lumieres():
    """Lumières du pont (identifiant et nom), triées par nom, avec leur rôle KamCiné actuel."""
    roles = integrations.hue_roles()
    out = []
    for l in _appel("light"):
        ident, nom = str(l.get("id") or ""), str((l.get("metadata") or {}).get("name") or "Lumière")
        if IDENTIFIANT.match(ident):
            out.append({"id": ident, "nom": nom, "role": next((r for r in ROLES if ident in roles.get(r, [])), "")})
    return sorted(out, key=lambda x: x["nom"].casefold())


def tester():
    return {"ok": True, "lumieres": len(lumieres())}


def enregistrer_roles(roles):
    """roles : {ecran: [id…], panneau: [id…], salle: [id…]} ; une lumière n'a qu'un rôle, les inconnues sont refusées."""
    connues = {l["id"] for l in lumieres()}
    propre, vues = {}, set()
    for role in ROLES:
        ids = [str(i) for i in (roles or {}).get(role) or []]
        for i in ids:
            if i not in connues:
                raise Echec("Lumière inconnue du pont : relancez la liste des lumières.")
            if i in vues:
                raise Echec("Une lumière ne peut avoir qu’un seul rôle.")
            vues.add(i)
        propre[role] = ids
    integrations.enregistrer_roles_hue(propre)
    return {"ok": True, "lumieres": lumieres()}
