"""Notifications KamCiné persistées localement sur le NAS, avec déduplication logique."""
import hashlib
import json
import os
import threading
import time

import chemins
BASE = chemins.data_dir()  # dossier des données (2.6.101)
FICHIER = os.path.join(BASE, "notifications.json")
MAX_NOTIFICATIONS = 200
_verrou = threading.Lock()


def _lire():
    try:
        with open(FICHIER, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return {"items": data, "torrents": {}, "attentes": {}}
        if isinstance(data, dict):
            items = data.get("items", [])
            torrents = data.get("torrents", {})
            attentes = data.get("attentes", {})
            return {"items": items if isinstance(items, list) else [],
                    "torrents": torrents if isinstance(torrents, dict) else {},
                    "attentes": attentes if isinstance(attentes, dict) else {},
                    "effacees": data.get("effacees", [])[-1000:], "medias": data.get("medias", {})}
    except (OSError, ValueError):
        pass
    return {"items": [], "torrents": {}, "attentes": {}}


def _ecrire(data):
    anciens = set()
    try:
        with open(FICHIER, encoding="utf-8") as f:
            precedent = json.load(f)
        anciens_objets = precedent.get("items", []) if isinstance(precedent, dict) else precedent
        anciens = {x.get("id") for x in anciens_objets if isinstance(x, dict)} if isinstance(anciens_objets, list) else set()
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    nouvelles = [x for x in data.get("items", []) if isinstance(x, dict) and x.get("id") not in anciens]
    os.makedirs(BASE, exist_ok=True)
    temporaire = FICHIER + ".tmp"
    with open(temporaire, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    try:
        os.chmod(temporaire, 0o600)
    except OSError:
        pass
    os.replace(temporaire, FICHIER)
    if nouvelles:
        try:
            import push
            push.notifier_creation(nouvelles)
        except Exception:
            pass


def _id(cle):
    return hashlib.sha256(str(cle).encode("utf-8")).hexdigest()[:24]


def _ajouter(data, cle, genre, titre, description, cible=None):
    ident = _id(cle)
    if ident in data.get("effacees", []) or any(x.get("id") == ident for x in data["items"]):
        return False
    data["items"].insert(0, {"id": ident, "genre": genre, "titre": str(titre)[:100],
                             "description": str(description)[:240], "cree": time.time(), "lue": False,
                             "cible": cible if isinstance(cible, dict) else None})
    data["items"] = data["items"][:MAX_NOTIFICATIONS]
    return True


def ajouter(cle, genre, titre, description, cible=None):
    """Ajoute un événement unique. La clé doit identifier l'événement, pas le rafraîchissement."""
    with _verrou:
        data = _lire()
        nouveau = _ajouter(data, cle, genre, titre, description, cible)
        if nouveau:
            _ecrire(data)
        return sum(not x.get("lue") for x in data["items"])


def lister():
    with _verrou:
        data = _lire()
    items = [x for x in data["items"] if isinstance(x, dict)]
    items.sort(key=lambda x: x.get("cree", 0), reverse=True)
    return {"notifications": items, "non_lues": sum(not x.get("lue") for x in items)}


def marquer_lue(ident):
    with _verrou:
        data = _lire()
        for item in data["items"]:
            if item.get("id") == ident:
                item["lue"] = True
                _ecrire(data)
                break
        return sum(not x.get("lue") for x in data["items"])


def marquer_tout_lu():
    with _verrou:
        data = _lire()
        for item in data["items"]:
            item["lue"] = True
        _ecrire(data)
        return 0


def texte_termine(torrent, nom, cible):
    """Point 85 : titre et description orientés séance, seulement quand le média est identifié avec certitude (même règle
    que la destination vers la fiche). Un torrent non rattaché garde un texte neutre : rien ne dit qu'il se regarde en séance.
    Une série peut arriver en épisode ou en saison entière, d'où un titre qui ne présume pas du nombre."""
    if cible.get("page") != "fiche":
        return "Téléchargement terminé", nom
    return str(nom)[:90] + " est prêt 🎬", "Disponible. Vous pouvez lancer votre séance."


def observer_transmission(torrents):
    """Détecte les transitions connues dans le polling déjà effectué par la page Téléchargements."""
    with _verrou:
        data = _lire()
        etats = data["torrents"]
        avant = dict(etats)
        changements = 0
        items = [x for groupe in (torrents or {}).values() for x in (groupe or []) if x.get("hash")]
        for torrent in items:
            h = str(torrent["hash"]).lower()
            statut = torrent.get("etat", "")
            signature = "erreur:" + str(torrent.get("erreur") or "") if statut == "erreur" else (
                "termine" if statut in ("terminé", "seed") else "actif")
            precedent = etats.get(h)
            if precedent is not None and signature != precedent:
                # Identité certaine (media_type/media_id, posés par /telechargements seulement quand la correspondance
                # est fiable, point 47) : direction vers la fiche plutôt que la liste générale des téléchargements.
                if torrent.get("media_type") in ("movie", "tv") and torrent.get("media_id"):
                    cible = {"page": "fiche", "type": torrent["media_type"], "id": torrent["media_id"]}
                else:
                    cible = {"page": "telechargements"}
                nom = torrent.get("titre_media") or torrent.get("nom") or "Téléchargement"
                if signature.startswith("erreur:") and not str(precedent).startswith("erreur:"):
                    changements += _ajouter(data, "transmission:%s:%s" % (h, signature), "telechargement_erreur",
                                            "Téléchargement en erreur", nom, cible)
                elif signature == "termine" and precedent == "actif":
                    changements += _ajouter(data, "transmission:%s:termine" % h, "telechargement_termine",
                                            *texte_termine(torrent, nom, cible), cible)
            etats[h] = signature
        if items:
            # Les hashs récemment retirés ne doivent pas rendre le registre de snapshots illimité.
            data["torrents"] = dict(list(etats.items())[-500:])
            if changements or data["torrents"] != avant:
                _ecrire(data)
        return sum(not x.get("lue") for x in data["items"])


def observer_attentes(attentes):
    """Signale une seule fois chaque séance en attente qui passe en échec de lancement."""
    with _verrou:
        data = _lire()
        deja = data["attentes"]
        avant = dict(deja)
        for e in attentes or []:
            if e.get("statut") != "lancement_echoue":
                continue
            pid = str(e.get("pid") or e.get("id_attente") or "")
            if not pid or deja.get(pid):
                continue
            titre = e.get("titre") or "Séance"
            _ajouter(data, "seance:echec:" + pid, "seance_echec", "Échec du lancement", titre,
                     {"page": "seance"})
            deja[pid] = True
        data["attentes"] = dict(list(deja.items())[-500:])
        if data["attentes"] != avant:
            _ecrire(data)
        return sum(not x.get("lue") for x in data["items"])


def ouvrir():
    """Marque le snapshot d'ouverture ; les créations suivantes restent non lues."""
    with _verrou:
        data = _lire()
        for item in data["items"]:
            item["lue"] = True
        _ecrire(data)
        return {"notifications": data["items"], "non_lues": 0}


def supprimer_tout():
    with _verrou:
        data = _lire()
        data["effacees"] = (data.get("effacees", []) + [x["id"] for x in data["items"]])[-1000:]
        data["items"] = []
        _ecrire(data)
        return {"notifications": [], "non_lues": 0}


def observer_medias(medias):
    """Seuls les contenus demandés dont une version devient disponible émettent un événement."""
    with _verrou:
        data = _lire()
        avant = data.get("medias", {})
        apres = dict(avant)
        for (type_, ident), m in medias.items():
            if not m.get("requests"):
                continue
            for version, champ in (("HD", "status"), ("4K", "status4k")):
                if not any(bool(r.get("is4k")) == (version == "4K") for r in m["requests"]):
                    continue
                cle = "%s:%s:%s" % (type_, ident, version)
                valeur = m.get(champ)
                if cle in avant and avant[cle] in (2, 3, 4) and valeur == 5:
                    _ajouter(data, "disponible:" + cle, "contenu_disponible", "Contenu demandé disponible",
                             (m.get("titre") or ("Film" if type_ == "movie" else "Série")) + " · " + version,
                             {"page": "fiche", "type": type_, "id": ident})
                apres[cle] = valeur
        data["medias"] = dict(list(apres.items())[-2400:])
        if apres != avant:
            _ecrire(data)


def signaler(*args, **kwargs):
    """Une panne du journal de notifications ne doit pas interrompre une séance."""
    try:
        return ajouter(*args, **kwargs)
    except OSError:
        return 0
