"""Séances programmées : rappel puis présence confirmée avant le lancement réel."""
import datetime, json, math, os, threading, time, uuid
import config
import erreurs

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

TIMEZONE_NAME = (os.environ.get("KAMCINE_TIMEZONE") or os.environ.get("TZ") or "Europe/Zurich").strip()
try:
    FUSEAU = ZoneInfo(TIMEZONE_NAME)
except (ZoneInfoNotFoundError, ValueError) as exc:
    raise RuntimeError("Fuseau IANA invalide ou indisponible : %s. Vérifiez KAMCINE_TIMEZONE, TZ et tzdata." % TIMEZONE_NAME) from exc

import chemins
BASE = chemins.data_dir()  # dossier des données (2.6.101)
FICHIER = os.path.join(BASE, "planning.json")
_verrou = threading.Lock()

# Règle de marge : durée réservée = préparation + bandes annonces (films) + contenu + entracte au maximum (films)
# + sécurité. Deux séances entrent en conflit si leurs créneaux réservés se chevauchent, avec un tampon entre elles.
# Les quatre durées sont des réglages (plan_*_min) : main.py les recopie ici avant chaque calcul avec regler().
REGLES = {"preparation_s": 3 * 60, "bandes_annonces_s": 9 * 60, "securite_s": 10 * 60, "tampon_s": 10 * 60}
DUREE_DEFAUT_MIN = {"movie": 120, "tv": 45}
# Garde fous du lancement, réglables (plan_tolerance_min, plan_avance_min) : main.py les recopie avec regler().
GARDES = {"tolerance_s": 10 * 60, "avance_s": 2 * 60, "confirmation_s": 120, "confirmation_requise": True}
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS_COURTS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]


# All calendar operations use the installation timezone, independently of the host clock.
def decalage_s(t):
    return int(datetime.datetime.fromtimestamp(t, FUSEAU).utcoffset().total_seconds())


def _local(t):
    return datetime.datetime.fromtimestamp(t, FUSEAU)


def local_iso(t):
    return _local(t).strftime("%Y-%m-%dT%H:%M")


def epoch_depuis_local(texte):
    """Reject nonexistent local times; choose the later instant for repeated times."""
    naive = datetime.datetime.strptime(texte.strip()[:16], "%Y-%m-%dT%H:%M")
    candidates = []
    for fold in (0, 1):
        stamp = naive.replace(tzinfo=FUSEAU, fold=fold).timestamp()
        if _local(stamp).replace(tzinfo=None) == naive:
            candidates.append(stamp)
    if not candidates:
        raise ValueError("Cette heure n’existe pas dans le fuseau %s (changement d’heure)." % TIMEZONE_NAME)
    return max(candidates)


def heure_txt(t):
    return _local(t).strftime("%H:%M")


def quand_mini(t, maintenant=None):
    """Date très courte pour le petit badge d'une jaquette : auj 21h, dem 21h30, ven 21h dans la semaine, sinon 21 sept."""
    now = maintenant or time.time()
    d, j = _local(t), _local(now)
    ecart = (d.date() - j.date()).days
    heure = "%dh" % d.hour if d.minute == 0 else "%dh%02d" % (d.hour, d.minute)
    if ecart == 0:
        return "auj " + heure
    if ecart == 1:
        return "dem " + heure
    if 1 < ecart < 7:
        return "%s %s" % (JOURS[d.weekday()][:3], heure)
    return "%d %s" % (d.day, MOIS_COURTS[d.month - 1])


def quand_txt(t, court=False):
    """Date et heure du serveur en français : « lundi 21 septembre à 20:30 », ou « lun. 21 sept. 20:30 » en court."""
    d = _local(t)
    if court:
        return "%s. %d %s %s" % (JOURS[d.weekday()][:3], d.day, MOIS_COURTS[d.month - 1], d.strftime("%H:%M"))
    return "%s %d %s à %s" % (JOURS[d.weekday()], d.day, MOIS[d.month - 1], d.strftime("%H:%M"))


def regler(cfg):
    REGLES.update(preparation_s=cfg["plan_preparation_min"] * 60, bandes_annonces_s=cfg["plan_bandes_annonces_min"] * 60,
                  securite_s=cfg["plan_securite_min"] * 60, tampon_s=cfg["plan_tampon_min"] * 60)
    GARDES.update(tolerance_s=cfg.get("plan_tolerance_min", 10) * 60, avance_s=cfg.get("plan_avance_min", 2) * 60,
                  confirmation_s=cfg.get("plan_confirmation_min", 2) * 60,
                  confirmation_requise=cfg.get("plan_confirmation_requise", True))


def plus_tot(maintenant=None):
    """Heure la plus proche qu'on peut programmer : maintenant plus la marge plan_avance_min."""
    return (maintenant or time.time()) + GARDES["avance_s"]


def arrondi_propose(t):
    """Heure proposée à l'utilisateur : la suivante à la minute ronde, avec 30 s de jeu, pour que l'heure choisie ne soit pas déjà
    refusée quand la demande arrive (l'interface relit l'heure toutes les 20 s)."""
    return math.ceil((t + 30) / 60.0) * 60


def verifier_futur(t, maintenant=None):
    """None si t est programmable, sinon le message de refus. Une heure déjà passée et une heure trop proche sont distinguées."""
    now = maintenant or time.time()
    if t < now:
        return "Cette date et cette heure sont déjà passées (il est %s)." % heure_txt(now)
    if t < plus_tot(now):
        return "Choisis une heure au moins %d min plus loin, soit %s au plus tôt." % (GARDES["avance_s"] // 60, heure_txt(plus_tot(now)))
    return None


def duree_reservee(type_, duree_min, entracte_actif=True, entracte_max_min=6):
    """Secondes réservées pour une séance. Sans durée connue, on prend 120 min pour un film et 45 min pour un épisode."""
    duree_min = duree_min or DUREE_DEFAUT_MIN.get(type_, 120)
    s = GARDES["confirmation_s"] + REGLES["preparation_s"] + duree_min * 60 + REGLES["securite_s"]
    if type_ != "tv":
        s += REGLES["bandes_annonces_s"] + (entracte_max_min * 60 if entracte_actif else 0)
    return int(s)


def trouver_conflit(debut, fin, blocs):
    """Premier bloc qui gêne le créneau [debut, fin], tampon compris. Un écart d'exactement le tampon est permis."""
    tampon = REGLES["tampon_s"]
    for b in sorted(blocs, key=lambda b: b["debut"]):
        if debut < b["fin"] + tampon and b["debut"] < fin + tampon:
            return b
    return None


def premier_libre(debut, duree, blocs, apres=0):
    """Premier départ possible à partir de debut (et pas avant apres), arrondi à la minute suivante."""
    t = math.ceil(max(debut, apres) / 60.0) * 60
    for _ in range(len(blocs) + 2):
        b = trouver_conflit(t, t + duree, blocs)
        if not b:
            return t
        t = math.ceil((b["fin"] + REGLES["tampon_s"]) / 60.0) * 60
    return t


def _bloc(e, reserves):
    if e.get("etat") not in ("prevue", "lancee"):
        return None
    reserve = e.get("reserve_s") or reserves.get(e.get("pid")) or duree_reservee(e.get("type"), None)
    return {"debut": e["t"], "fin": e["t"] + reserve, "titre": e.get("titre") or "une autre séance", "pid": e.get("pid")}


def _blocs(l, reserves, externes, maintenant):
    out = list(externes)
    for e in l:
        b = _bloc(e, reserves)
        if b and b["fin"] + REGLES["tampon_s"] > maintenant:
            out.append(b)
    return out


def verifier(debut, reserve_s, reserves=None, externes=(), maintenant=None, exclure=None):
    """Sans rien enregistrer : (bloc en conflit ou None, premier départ libre). exclure : séance qu'on est en train de déplacer."""
    now = maintenant or time.time()
    with _verrou:
        blocs = _blocs([e for e in _lire() if e.get("pid") != exclure], reserves or {}, externes, now)
    c = trouver_conflit(debut, debut + reserve_s, blocs)
    return c, (premier_libre(debut, reserve_s, blocs, plus_tot(now)) if c else debut)


def ajouter_si_libre(entree, rappel_min, reserve_s, reserves=None, externes=(), maintenant=None):
    """Vérifie et enregistre d'un seul geste (sous verrou), pour que deux demandes simultanées ne passent pas toutes les deux.
    Renvoie (séance, None) ou (None, {"conflit": bloc, "libre": départ possible})."""
    now = maintenant or time.time()
    with _verrou:
        l = _lire()
        blocs = _blocs(l, reserves or {}, externes, now)
        c = trouver_conflit(entree["t"], entree["t"] + reserve_s, blocs)
        if c:
            return None, {"conflit": c, "libre": premier_libre(entree["t"], reserve_s, blocs, plus_tot(now))}
        e = dict(entree, pid=uuid.uuid4().hex[:10], etat="prevue", rappel_min=rappel_min, cree=now,
                 rappel_envoye=False, rappel_lu=False, message="",
                 reserve_s=int(reserve_s), confirmation_delai_s=GARDES["confirmation_s"],
                 confirmation_requise=GARDES["confirmation_requise"])
        l.append(e)
        _ecrire(l[-100:])
    return e, None


def modifiable(e):
    """Une séance encore prévue, ou une séance ratée (manquée, en échec, ou annulée par le filet de sécurité faute de
    confirmation) peut être reprogrammée : elle n'a pas eu lieu, rien n'empêche d'en refaire une à une nouvelle heure."""
    return e.get("etat") in ("prevue", "manquee", "echec") or bool(e.get("annulation_securite"))


def deplacer_si_libre(pid, t, reserve_s, rappel_min, reserves=None, externes=(), maintenant=None):
    """Change l'heure d'une séance encore prévue ou d'une séance ratée (qui redevient prévue), avec les mêmes
    vérifications qu'à la création (créneau libre en ne comptant pas la séance elle même). Renvoie (séance, None),
    (None, {"conflit", "libre"}) ou (None, {"erreur": message})."""
    now = maintenant or time.time()
    with _verrou:
        l = _lire()
        cible = next((e for e in l if e.get("pid") == pid), None)
        if not cible:
            return None, {"erreur": "Cette séance programmée n'existe plus."}
        if not modifiable(cible):
            return None, {"erreur": "Cette séance n'est plus modifiable (%s)." % ETATS_TXT.get(cible.get("etat"), cible.get("etat"))}
        blocs = _blocs([e for e in l if e.get("pid") != pid], reserves or {}, externes, now)
        c = trouver_conflit(t, t + reserve_s, blocs)
        if c:
            return None, {"conflit": c, "libre": premier_libre(t, reserve_s, blocs, plus_tot(now))}
        cible.update(t=t, reserve_s=int(reserve_s), rappel_min=rappel_min, rappel_envoye=False,
                     rappel_lu=False, message="", modifiee=now, confirmation_acceptee=None, etat="prevue",
                     annulation_securite=False, confirmation_delai_s=GARDES["confirmation_s"],
                     confirmation_requise=GARDES["confirmation_requise"])
        _ecrire(l)
        return dict(cible), None


ETATS_TXT = {"prevue": "prévue", "lancee": "déjà lancée", "annulee": "annulée", "manquee": "manquée", "echec": "en échec"}


def trouver(pid):
    with _verrou:
        return next((dict(e) for e in _lire() if e.get("pid") == pid), None)


def _lire():
    try:
        with open(FICHIER, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _ecrire(l):
    """Ecriture atomique : un redémarrage du service en plein enregistrement ne doit jamais laisser un planning.json coupé."""
    tmp = FICHIER + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(l, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, FICHIER)


def liste():
    """Séances à venir, plus celles des dernières 24 h pour en garder la trace."""
    with _verrou:
        return sorted([e for e in _lire() if e["t"] > time.time() - 86400], key=lambda e: e["t"])


def ajouter(entree, rappel_min):
    now = time.time()
    e = dict(entree, pid=uuid.uuid4().hex[:10], etat="prevue", rappel_min=rappel_min, cree=now,
             rappel_envoye=False, rappel_lu=False, message="",
             confirmation_delai_s=GARDES["confirmation_s"], confirmation_requise=GARDES["confirmation_requise"])
    with _verrou:
        l = _lire()
        l.append(e)
        _ecrire(l[-100:])
    return e


def marquer(pid, **kw):
    with _verrou:
        l = _lire()
        for e in l:
            if e["pid"] == pid:
                e.update(kw)
        _ecrire(l)


def annuler(pid):
    """Annule une séance encore prévue, ou retire une séance ratée de l'accueil (elle ne s'est pas lancée de toute
    façon) : idempotent si elle est déjà annulée, pour qu'un second clic ne renvoie jamais un faux échec."""
    with _verrou:
        l = _lire()
        trouve = False
        for e in l:
            if e["pid"] != pid:
                continue
            if e["etat"] == "annulee":
                trouve = True
            elif modifiable(e):
                e.update(etat="annulee", annulation_securite=False)
                trouve = True
        _ecrire(l)
    return trouve


def rappels():
    return [e for e in liste() if e["etat"] == "prevue" and e.get("rappel_envoye") and not e.get("rappel_lu")]


def confirmation_fin(e):
    """La date prévue ancre le délai, même après un redémarrage. e.get("confirmation_requise", True) : une ancienne
    séance sans ce champ garde le comportement d'avant (confirmation requise)."""
    if e.get("mode") == "test" or e.get("confirmation_requise", True) is False:
        return None
    return e["t"] + e.get("confirmation_delai_s", GARDES["confirmation_s"])


def confirmer(pid):
    """Enregistre uniquement une présence explicite à l'heure prévue, sous le verrou du planning."""
    with _verrou:
        now = time.time()
        l = _lire()
        e = next((e for e in l if e.get("pid") == pid), None)
        if not e or e.get("etat") != "prevue" or e.get("mode") == "test":
            return False, "Cette séance n'attend pas de confirmation."
        if not e["t"] <= now < confirmation_fin(e):
            return False, "La confirmation est disponible à l'heure prévue, pendant le délai indiqué."
        if not e.get("confirmation_acceptee"):
            e.setdefault("confirmation_delai_s", GARDES["confirmation_s"])
            e["confirmation_acceptee"] = now
            e["message"] = "Présence confirmée, lancement imminent"
            _ecrire(l)
            erreurs.journaliser_action("Planning", "Présence confirmée pour « %s »." % e.get("titre", "Séance"))
    return True, "Présence confirmée"


def tick(lancer, occupe):
    """Réserve chaque lancement sous verrou après confirmation en réel, puis agit hors du verrou."""
    with _verrou:
        ids = [e["pid"] for e in _lire() if e["etat"] == "prevue"]
    for pid in ids:
        with _verrou:
            now = time.time()
            l = _lire()
            e = next((e for e in l if e["pid"] == pid), None)
            if not e or e["etat"] != "prevue":
                continue
            avant = dict(e)
            if now >= e["t"] - e["rappel_min"] * 60:
                e["rappel_envoye"] = True
                if not avant.get("rappel_envoye") and e.get("rappel_min", 0) > 0:
                    import notifications
                    notifications.signaler("planning:rappel:" + pid + ":" + str(e["t"]), "seance_bientot",
                                          "La séance va bientôt commencer", e.get("titre") or "Confirme ta présence à l’heure prévue.", {"page": "seance"})
            if now < e["t"]:
                if e != avant:
                    _ecrire(l)
                continue
            fin = confirmation_fin(e)
            if fin and not e.get("confirmation_acceptee"):
                if now >= fin:
                    e.update(etat="annulee", annulation_securite=True,
                             message="Aucune confirmation reçue à temps. Séance annulée ; aucun appareil activé.")
                elif occupe():
                    e.update(etat="manquee", message="Une autre séance est déjà en cours. La séance n'a pas été lancée.")
                else:
                    e.setdefault("confirmation_delai_s", GARDES["confirmation_s"])
                    e["message"] = "Confirme ta présence pour lancer la séance."
            elif now > max(e["t"] + GARDES["tolerance_s"], (e.get("confirmation_acceptee") or 0) + 30):
                e.update(etat="manquee", message="Le délai de lancement est dépassé. La séance n'a pas été lancée.")
            elif occupe():
                e.update(etat="manquee", message="Une autre séance est déjà en cours. La séance n'a pas été lancée.")
            else:
                e.update(etat="lancee", message="Lancement en cours")
            if e != avant:
                _ecrire(l)
            if e.get("annulation_securite") or e["etat"] == "manquee":
                erreurs.journaliser_action("Planning", "« %s » : %s" % (e.get("titre", "Séance"), e["message"]), "avertissement")
            if e["etat"] != "lancee":
                continue
            entree = dict(e)
        try:
            ok, msg = lancer(entree)
        except Exception as ex:
            ok, msg = False, "Erreur au lancement : %s" % str(ex)[:120]
        marquer(pid, etat="lancee" if ok else "echec", message="" if ok else (msg or "Le lancement a échoué"))


def boucle(lancer, occupe, pas=15):
    while True:
        try:
            regler(config.charger())
            tick(lancer, occupe)
        except Exception as ex:
            print("Planning :", ex)
        time.sleep(pas)
