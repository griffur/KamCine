"""Séances en attente de téléchargement : un film absent de la bibliothèque est demandé, puis la séance part toute seule quand il est prêt.

Ce module ne connaît ni Overseerr, ni Radarr, ni l'Apple TV : main.py lui passe ce dont il a besoin (état du film, conflits, lancement). Il garde
les séances en attente dans attente.json, écrit de façon atomique et relu à chaque opération, donc elles survivent à un redémarrage
du service comme les séances programmées. La décision (evaluer) est une fonction pure, testée sans réseau."""
import datetime, json, os, re, threading, time, uuid
import config, planning

import chemins
BASE = chemins.data_dir()  # dossier des données (2.6.101)
FICHIER = os.path.join(BASE, "attente.json")
_verrou = threading.Lock()

# Statuts : demande (demandé), telechargement, pret, lancement (départ en cours), lancee, echec, annulee, lancement_echoue (prête, lancement
# échoué, avec un bouton Relancer), manquee (date choisie dépassée sans film prêt, ou conflit à l'heure choisie).
ACTIFS = ("demande", "telechargement", "pret", "lancement", "lancement_echoue")
LIBELLES = {"demande": "En recherche…", "telechargement": "En téléchargement", "pret": "Prêt", "lancement": "Lancement en cours", "lancee": "Lancée",
            "echec": "Échec", "annulee": "Annulée", "lancement_echoue": "Prête, lancement échoué", "manquee": "Manquée"}


def _lire():
    try:
        with open(FICHIER, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, list) else []
    except Exception:
        return []


def _ecrire(l):
    tmp = FICHIER + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(l[-100:], f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, FICHIER)


def liste(maintenant=None):
    """Les séances en attente, plus celles terminées ou ratées depuis moins de 24 h pour en garder la trace."""
    now = maintenant or time.time()
    with _verrou:
        return [e for e in _lire() if e.get("statut") in ACTIFS or now - (e.get("fin") or e.get("cree") or 0) < 86400]


def actives():
    with _verrou:
        return [e for e in _lire() if e.get("statut") in ACTIFS]


def trouver(pid):
    with _verrou:
        return next((dict(e) for e in _lire() if e.get("pid") == pid), None)


def active_pour(type_, id_):
    with _verrou:
        return next((dict(e) for e in _lire() if e.get("statut") in ACTIFS and e.get("type") == type_ and e.get("id") == id_), None)


def ajouter(entree, maintenant=None):
    """Enregistre une séance en attente. Renvoie (entrée, None), ou (None, entrée existante) s'il y en a déjà une active pour ce titre."""
    now = maintenant or time.time()
    with _verrou:
        l = _lire()
        ex = next((e for e in l if e.get("statut") in ACTIFS and e.get("type") == entree["type"] and e.get("id") == entree["id"]), None)
        if ex:
            return None, dict(ex)
        e = dict(entree, pid=uuid.uuid4().hex[:10], statut="demande", cree=now, message="", pret_t=None, progres=None, restant=None, depart=None)
        l.append(e)
        _ecrire(l)
    return e, None


def marquer(pid, **kw):
    with _verrou:
        l = _lire()
        for e in l:
            if e.get("pid") == pid:
                e.update(kw)
                if kw.get("statut") in ("lancee", "echec", "annulee", "manquee"):
                    e["fin"] = time.time()
        _ecrire(l)


def annuler(pid):
    """Annule une séance en attente (le téléchargement demandé n'est pas annulé). Faux si elle n'est plus active ou déjà en cours de lancement."""
    with _verrou:
        l = _lire()
        ok = False
        for e in l:
            if e.get("pid") == pid and e.get("statut") in ("demande", "telechargement", "pret", "lancement_echoue"):
                e.update(statut="annulee", fin=time.time(), message="Annulée")
                ok = True
        _ecrire(l)
    return ok


def apres_redemarrage():
    """Une séance dont le lancement était en cours quand le service s'est arrêté ne peut pas être dite lancée : elle passe en lancement échoué."""
    with _verrou:
        l = _lire()
        for e in l:
            if e.get("statut") == "lancement":
                e.update(statut="lancement_echoue", message="Le service a redémarré pendant le lancement. Relance la séance.")
        _ecrire(l)


_MSG_ABANDON_SUPPRIME = re.compile(r"^Aucun fichier arrivé après \d+ heures : le téléchargement n'a pas abouti\.$")


def reprendre_echecs_abandon():
    """2.6.64 : l'abandon automatique après un délai sans fichier est supprimé, une demande reste en attente sans
    limite de durée. Une entrée déjà passée en échec uniquement par cette ancienne règle (message reconnaissable
    ci dessus, jamais écrit ailleurs) retrouve un état actif ; le prochain passage la remet à jour avec le vrai état
    du téléchargement. Un échec pour une autre raison n'est pas touché."""
    with _verrou:
        l = _lire()
        for e in l:
            if e.get("statut") == "echec" and _MSG_ABANDON_SUPPRIME.match(e.get("message") or ""):
                e.update(statut="demande", message="", fin=None)
        _ecrire(l)


# ---------- Fenêtre horaire (fuseau du serveur) ----------
def _minutes(t):
    d = planning._local(t)
    return d.hour * 60 + d.minute


def dans_fenetre(t, debut_min, fin_min):
    m = _minutes(t)
    return debut_min <= m <= fin_min if debut_min <= fin_min else (m >= debut_min or m <= fin_min)


def prochaine_ouverture(t, debut_min, fin_min):
    """t s'il est dans la fenêtre, sinon le prochain instant où elle ouvre."""
    if dans_fenetre(t, debut_min, fin_min):
        return t
    jour = datetime.datetime.strptime(planning.local_iso(t)[:10], "%Y-%m-%d").date()
    for j in range(0, 3):
        d = jour + datetime.timedelta(days=j)
        # A daily opening may fall in a DST gap. Use the first existing minute still in the window.
        local = datetime.datetime.combine(d, datetime.time()) + datetime.timedelta(minutes=debut_min)
        for minute in range(181):
            try:
                c = planning.epoch_depuis_local((local + datetime.timedelta(minutes=minute)).strftime("%Y-%m-%dT%H:%M"))
            except ValueError:
                continue
            if c > t and dans_fenetre(c, debut_min, fin_min):
                return c
            break
    return t


def premier_depart(depart_min, cfg, conflit_fn, reserve_s):
    """Premier instant à partir de depart_min qui est dans la fenêtre horaire et ne chevauche aucune séance (règle de marge de la
    programmation). Renvoie (instant, raisons du décalage)."""
    t, raisons = depart_min, []
    for _ in range(8):
        o = prochaine_ouverture(t, cfg["attente_debut_min"], cfg["attente_fin_min"])
        if o > t + 1:
            raisons.append("fenêtre horaire, ouverture à %s" % planning.heure_txt(o))
            t = o
        c, libre = conflit_fn(t, reserve_s)
        if c:
            raisons.append("chevauche « %s » (%s)" % (c["titre"], planning.quand_txt(c["debut"], True)))
            t = libre
            continue
        return t, raisons
    return t, raisons


def evaluer(e, film, now, cfg, conflit_fn, reserve_s, occupe):
    """Un pas de décision pour une séance en attente. film vient de bibliotheque.film_pret. Renvoie (changements, lancer)."""
    tol = cfg.get("plan_tolerance_min", 10) * 60
    quand = e.get("quand")
    if not film["pret"]:
        ch = {"pret_t": None, "depart": None}
        if film["etat"] == "telechargement":
            ch.update(statut="telechargement", progres=film["progres"], restant=film["restant"], message="")
        else:
            ch.update(statut="demande", progres=None, restant=None,
                      message="Un téléchargement a échoué, Radarr va réessayer." if film["etat"] == "echec" else "")
        if quand and now > quand + tol:
            return {"statut": "manquee", "message": "Le film n'était pas prêt à l'heure choisie (%s). La séance n'a pas été lancée." % planning.quand_txt(quand)}, False
        return ch, False
    pret_t = e.get("pret_t") or now
    depart_min = pret_t + cfg["attente_delai_s"]
    ch = {"pret_t": pret_t, "progres": None, "restant": None}
    if quand:
        depart = max(depart_min, quand)
        if depart > quand + tol:
            return dict(ch, statut="manquee", message="Le film est arrivé trop tard pour l'heure choisie (%s). La séance n'a pas été lancée." % planning.quand_txt(quand)), False
        if now < depart:
            return dict(ch, statut="pret", depart=depart, message="Prêt. Lancement à %s, comme choisi." % planning.heure_txt(depart)), False
        c, _ = conflit_fn(now, reserve_s)
        if occupe() or c:
            raison = "une séance est en cours" if occupe() else "elle chevauche « %s »" % c["titre"]
            return dict(ch, statut="manquee", message="À l'heure choisie (%s), %s. La séance n'a pas été lancée." % (planning.heure_txt(quand), raison)), False
        return dict(ch, statut="pret", depart=depart, message=""), True
    depart, raisons = premier_depart(depart_min, cfg, conflit_fn, reserve_s)
    if occupe() and now >= depart:
        depart = max(depart, now + 60)
        raisons.append("une séance est en cours")
    if now < depart:
        msg = "Prêt. Lancement à %s" % planning.quand_txt(depart, True) if raisons else "Prêt. Lancement à %s" % planning.heure_txt(depart)
        if raisons:
            msg += " (décalé : %s)" % ", ".join(raisons)
        return dict(ch, statut="pret", depart=depart, message=msg + "."), False
    return dict(ch, statut="pret", depart=depart, message=""), True


def tick(etat_film, lancer, suivre, conflit, reserve, occupe, maintenant=None):
    """Un passage : suit les lancements en cours, décide pour chaque séance en attente et lance celles qui peuvent partir.
    etat_film(e) -> film_pret ; lancer(e) -> {"ok", "message", "code"} ; suivre(e, now) -> changements ; conflit(t, s) -> (bloc, libre)."""
    now = maintenant or time.time()
    cfg = config.charger()
    planning.regler(cfg)
    for e in actives():
        if e["statut"] == "lancement":
            ch = suivre(e, now)
            if ch:
                marquer(e["pid"], **ch)
            continue
        if e["statut"] == "lancement_echoue":
            continue
        rappel_min = max(0, int(e.get("rappel_min", 0) or 0))
        quand = e.get("quand")
        if quand and rappel_min and now >= quand - rappel_min * 60 and not e.get("rappel_envoye"):
            marquer(e["pid"], rappel_envoye=True)
            import notifications
            notifications.signaler("attente:rappel:%s:%s" % (e["pid"], quand), "seance_bientot",
                                  "La séance approche", "« %s » est prévue à %s." % (
                                      e.get("titre") or "La séance", planning.quand_txt(quand)),
                                  {"page": "fiche", "type": "movie", "id": e["id"]})
        ch, go = evaluer(e, etat_film(e), now, cfg, conflit, reserve(e), occupe)
        marquer(e["pid"], **ch)
        if ch.get("pret_t") and not e.get("pret_t"):
            import notifications
            notifications.signaler("attente:prete:" + e["pid"], "seance_bientot",
                                  "La séance est prête", e.get("titre") or "Le contenu est prêt pour la séance.",
                                  {"page": "fiche", "type": "movie", "id": e["id"]})
        if go:
            marquer(e["pid"], statut="lancement", t_lancement=now, message="Lancement en cours")     # écrit avant l'appel : jamais deux lancements
            try:
                r = lancer(e)
            except Exception as ex:
                r = {"ok": False, "message": "Erreur au lancement : %s" % str(ex)[:120], "code": 500}
            if not r.get("ok"):
                if r.get("code") == 409:
                    marquer(e["pid"], statut="pret", message="Une séance est en cours, la séance en attente patiente.")
                else:
                    marquer(e["pid"], statut="lancement_echoue", message=r.get("message") or "Le lancement a échoué.")


def boucle(rafraichir, etat_film, lancer, suivre, conflit, reserve, occupe, pas=5):
    """Boucle de fond. Le réseau (relecture d'un film dans Radarr) n'est touché que si au moins une séance attend un téléchargement,
    au rythme attente_intervalle_s ; sinon la boucle ne fait que lire attente.json."""
    dernier = 0
    apres_redemarrage()
    reprendre_echecs_abandon()
    while True:
        try:
            cfg = config.charger()
            attend = [e for e in actives() if e["statut"] in ("demande", "telechargement")]
            if attend and time.time() - dernier >= cfg["attente_intervalle_s"]:
                dernier = time.time()
                for e in attend:
                    rafraichir(e)
            if actives():
                tick(etat_film, lancer, suivre, conflit, reserve, occupe)
        except Exception as ex:
            print("Attente :", ex)
        time.sleep(pas)
