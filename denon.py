import json, os, re, threading, time
import requests
from config import charger

import json_atomique
import chemins
BASE = chemins.data_dir()  # dossier des données (2.6.101)
FICHIER = os.path.join(BASE, "denon_etat.json")
PORTS = [int(p) for p in os.environ.get("DENON_PORTS", "8080,80").split(",")]
DB_MIN, DB_MAX = -80.0, 0.0
MVMAX = 98.0
ENTREES = {"TV": "TV audio", "MPLAY": "Lecteur média", "GAME": "Jeu", "SAT/CBL": "Câble ou satellite", "CBL/SAT": "Câble ou satellite",
           "DVD": "DVD", "BD": "Blu-ray", "AUX1": "AUX 1", "AUX2": "AUX 2", "NET": "Réseau", "BT": "Bluetooth",
           "TUNER": "Radio", "PHONO": "Platine", "CD": "CD", "MEDIA PLAYER": "Lecteur média", "8K": "8K"}
_memoire = {"port": None}


def ip():
    v = str(charger().get("denon_ip") or "").strip()
    return v if re.match(r"^[A-Za-z0-9.\-]{1,60}$", v) else ""


def actif():
    return bool(charger().get("denon_actif") and ip())


def _get(chemin, delai=2):
    h = ip()
    if not h:
        raise RuntimeError("Adresse de l'ampli absente")
    ports = ([_memoire["port"]] if _memoire["port"] else []) + [p for p in PORTS if p != _memoire["port"]]
    derniere = None
    for p in ports:
        try:
            r = requests.get("http://%s:%d%s" % (h, p, chemin), timeout=delai)
            if r.status_code == 200:
                _memoire["port"] = p
                return r.text
            derniere = RuntimeError("réponse %d" % r.status_code)
        except Exception as e:
            derniere = e
    raise derniere or RuntimeError("Ampli injoignable")


def commande(cmd):
    return _get("/goform/formiPhoneAppDirect.xml?" + cmd)


def _valeur(xml, balise):
    m = re.search(r"<%s>\s*<value>(.*?)</value>" % balise, xml, re.S)
    return m.group(1).strip() if m else None


def etat():
    try:
        xml = _get("/goform/formMainZone_MainZoneXmlStatusLite.xml")
    except requests.exceptions.RequestException:
        # hors tension : l'ampli s'éteint avec l'Apple TV (HDMI CEC), ce n'est pas une panne
        return {"joignable": False, "eteint": True}
    except Exception as e:
        return {"joignable": False, "eteint": False, "message": str(e)[:100]}
    code = (_valeur(xml, "InputFuncSelect") or "").strip()
    try:
        volume = float(_valeur(xml, "MasterVolume"))
    except (TypeError, ValueError):
        volume = None
    return {"joignable": True, "allume": (_valeur(xml, "Power") or "").upper() == "ON",
            "entree": ENTREES.get(code.upper(), code), "volume": volume,
            "niveau": niveau_de_db(volume) if volume is not None else None, "limite": limite(),
            "muet": (_valeur(xml, "Mute") or "").lower() == "on"}


def niveau_de_db(db):
    """Volume de l'ampli en dB relatifs (-80 à +18) ramené à une échelle de 0 à 100."""
    return round(max(0.0, min(MVMAX, db + 80)) / MVMAX * 100, 1)


def mv_de_niveau(n):
    return round(max(0.0, min(100.0, float(n))) / 100 * MVMAX * 2) / 2


def limite():
    try:
        return int(charger().get("denon_limite", 82))
    except Exception:
        return 82


def _cmd_mv(mv):
    entier = int(mv)
    return "MV%02d%s" % (entier, "5" if mv - entier >= 0.5 else "")


def regler_niveau(n):
    """Règle le volume de 0 (aucun son) à 100, sans dépasser la limite de sécurité."""
    n = min(float(n), float(limite()))
    commande(_cmd_mv(mv_de_niveau(n)))
    return round(n, 1)


def _mv(db):
    db = max(DB_MIN, min(DB_MAX, round(float(db) * 2) / 2))
    mv = db + 80
    entier = int(mv)
    return "MV%02d%s" % (entier, "5" if mv - entier >= 0.5 else "")


def regler_volume(db):
    commande(_mv(db))


def allumer():
    commande("PWON")


def veille():
    commande("PWSTANDBY")


def muet(actif_):
    commande("MUON" if actif_ else "MUOFF")


def demarrer():
    """Au lancement d'une séance : allume l'ampli s'il est en veille."""
    if not (actif() and charger().get("denon_allumer")):
        return
    try:
        e = etat()
        if e.get("joignable") and not e.get("allume"):
            allumer()
            time.sleep(2)
    except Exception as ex:
        print("  Ampli :", ex)


def baisser(delta):
    """À l'entracte : baisse le volume de delta dB et retient le niveau d'avant."""
    if not delta or delta <= 0 or not actif():
        return
    try:
        e = etat()
        if not (e.get("joignable") and e.get("allume") and e.get("volume") is not None):
            return
        try:
            with open(FICHIER) as f:
                if time.time() - json.load(f)["t"] < 4 * 3600:
                    return
        except Exception:
            pass
        json_atomique.ecrire(FICHIER, {"avant": e["volume"], "t": time.time()})
        regler_volume(e["volume"] - delta)
        print("  Ampli : volume baissé de %s dB" % delta)
    except Exception as ex:
        print("  Ampli :", ex)


def restaurer():
    """Remet le volume d'avant l'entracte, une seule fois."""
    try:
        with open(FICHIER) as f:
            s = json.load(f)
    except Exception:
        return
    try:
        if actif():
            regler_volume(s["avant"])
            print("  Ampli : volume remis")
        os.remove(FICHIER)
    except Exception as ex:
        print("  Ampli :", ex)
        if time.time() - s.get("t", 0) > 4 * 3600:
            try:
                os.remove(FICHIER)
            except Exception:
                pass


def en_arriere_plan(fn, *args):
    threading.Thread(target=fn, args=args, daemon=False).start()


def diagnostic():
    """Teste chaque port et dit ce qui répond, pour comprendre une panne."""
    h = ip()
    res = []
    for p in PORTS:
        t = time.time()
        try:
            r = requests.get("http://%s:%d/goform/formMainZone_MainZoneXmlStatusLite.xml" % (h, p), timeout=2)
            res.append({"port": p, "ok": r.status_code == 200, "ms": round((time.time() - t) * 1000),
                        "volume": _valeur(r.text, "MasterVolume") if r.status_code == 200 else None,
                        "erreur": None if r.status_code == 200 else "réponse %d" % r.status_code})
        except Exception as e:
            res.append({"port": p, "ok": False, "ms": round((time.time() - t) * 1000), "volume": None, "erreur": type(e).__name__})
    return res
