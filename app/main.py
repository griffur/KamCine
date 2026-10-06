import difflib, hashlib, hmac, html, json, math, os, re, secrets, sys, signal, subprocess, threading, time, unicodedata

# 2.6.101 : chemins.py, à la racine du code (un dossier au dessus de app/main.py), sépare le code (chemins.app_dir()) des données
# (chemins.data_dir()). Sans KAMCINE_APP ni KAMCINE_DATA, les deux valent KAMCINE_DIR, comme avant.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import json_atomique
import chemins
sys.path.insert(0, chemins.app_dir())
BASE = chemins.data_dir()  # dossier des données

import requests
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, Response
from starlette.concurrency import run_in_threadpool
from config import charger, sauver, nettoyer, SPEC, DEFAUTS
import erreurs
from functools import wraps
import etat_seance
import lecture_infuse
import integrations, appairage_atv, appairage_hue
import suivi, trakt, denon, bibliotheque, relais, planning, generique, atvlive, diagnostic, attente, transmission, filmsactu, favoris, journal_ui, demandes, notifications, synology, push, comptes, notes_personnelles
from fastapi.middleware.gzip import GZipMiddleware

DEMARRAGE = time.time()       # heure de démarrage du service, pour Réglages, À propos
app = FastAPI()
app.add_middleware(GZipMiddleware, minimum_size=1024)

DOSSIER = chemins.code("app")  # index.html, icônes, logos : le code
LOG = os.path.join(BASE, "seance.log")
VERSION_SERVICE = "2.7.10"  # à changer avec la version affichée dans app/index.html : l'interface s'en sert pour dire que le service n'a pas été redémarré
# Photo de profil d'avant les comptes, une seule pour toute l'installation (app/profil.jpg, ou profil.jpg des données).
# Depuis la 2.6.103, chaque compte a sa photo (comptes.py, avatars/) : ce fichier n'est plus servi ni écrit, il est copié
# une fois vers le compte si l'installation n'en a qu'un (comptes.rattacher_photo_ancienne) et reste en place pour un
# retour arrière.
PHOTO_ANCIENNE = os.path.join(BASE, "app", "profil.jpg")
PHOTO = PHOTO_ANCIENNE if chemins.app_dir() == BASE or os.path.exists(PHOTO_ANCIENNE) else os.path.join(BASE, "profil.jpg")
AUTH_FILE = os.path.join(BASE, "auth.json")
SECRETS = os.path.join(BASE, "secrets.json")
FILM_CHOISI = os.path.join(BASE, "film_choisi.json")
ICONES = {"sombre": "icon_sombre.png", "clair": "icon_clair.png"}
REPLI = ["icon2.png", "icon.png"]
TMDB = os.environ.get("TMDB_BASE", "https://api.themoviedb.org/3")
IMG = os.environ.get("TMDB_IMG", "https://image.tmdb.org/t/p")
def atv_commande(identifiant=None):
    """atvremote avec l'Apple TV configurée (integrations.py, 2.6.106), ou None si elle ne l'est pas. Plus aucun
    identifiant ni identifiant d'appairage écrit dans le code."""
    if identifiant is None and en_cours():
        identifiant = etat.get("appletv_id") or atvlive.LIVE.target_id
    a = integrations.appletv(identifiant)
    if not a:
        return None
    return ["atvremote", "--id", a["identifiant"], "--companion-credentials", a["companion"], "--airplay-credentials", a["airplay"]]
COMMANDES = {"play_pause": "play_pause", "back": "skip_backward=10", "forward": "skip_forward=10"}
VERROU_LECTEUR = threading.Lock()

etat = {"proc": None, "mode": None, "type": None, "debut": None, "lignes": [], "preparation": False, "appletv_id": None}
# Les bandes annonces viennent toujours de la chaîne FilmsActu, jamais d'une autre. L'identifiant de chaîne est celui du flux officiel
# (chaque entrée du flux porte yt:channelId, vérifié en réel). YOUTUBE_RSS et OEMBED_BASE se règlent pour tester avec de faux serveurs.
CHAINE_ID, CHAINE_NOM, CHAINE_ADRESSE = filmsactu.CHAINE_ID, filmsactu.CHAINE_NOM, filmsactu.CHAINE_ADRESSE   # la recherche vit dans filmsactu.py (2.4)
DEMANDES_AUTO = os.path.join(BASE, "demandes_auto.json")
APP_OK = {"ok": None, "t": 0}
CLI = {"t": 0, "derniere": None}
CLI_PAS = float(os.environ.get("KAMCINE_CLI_PAS", "6"))
ident_en_cours = set()
cache_ba, cache_neg = {}, {}
cache_film = {"t": 0, "data": None}
verrou_film = threading.Lock()
cache_tmdb = {}
cache_episodes_diffusees = {}
cache_gen, cache_tendances = {}, {"t": 0, "data": None}
cache_listes, cache_omdb, cache_details, cache_ovnotes, _cartes = {}, {}, {}, {}, {}
verrous_details = {}
RECENTS = os.path.join(BASE, "recents.json")
OVERSEERR_LOCAL = os.environ.get("OVERSEERR_LOCAL", "http://127.0.0.1:5055")
OMDB = os.environ.get("OMDB_BASE", "https://www.omdbapi.com/")

MARQUEURS = [
    ("Réveil de l'Apple TV", 0),
    ("Séance reprise", 2),
    ("Ambiance douce", 0),
    ("Lancement du film", 1),
    ("Trailer ", 1),
    ("Retour au film", 2),
    ("Film :", 2),
    ("Épisode :", 6),
    ("Épisode suivant", 6),
    ("Retour avant le générique", 2),
    ("ENTRACTE", 3),
    ("Reprise du film", 4),
    ("Le film s'est arrêté", 4),
    ("Générique", 4),
    ("Mode test : générique", 4),
    ("Série :", 6),
    ("Séance terminée", 5),
    ("Séance suspendue", None),
]


def norm(s):
    return re.sub(r"\W+", "", str(s).lower())


def en_cours():
    p = etat["proc"]
    return etat["preparation"] or relais.actif or (p is not None and p.poll() is None)


def processus_vivant():
    p = etat["proc"]
    return p is not None and p.poll() is None


# ---------- Journal et analyse de la séance ----------
def lignes_courantes():
    if etat["lignes"]:
        return list(etat["lignes"])
    try:
        with open(LOG, encoding="utf-8", errors="replace") as f:
            return [(None, l) for l in f.read().splitlines()]
    except Exception:
        return []


def phase_depuis(lignes):
    phase = None
    for _, ligne in lignes:
        for debut, p in MARQUEURS:
            if ligne.startswith(debut):
                phase = p
    return phase


def analyser(lignes, mode):
    maintenant = time.time()
    test = mode != "reel"
    trailers = []
    e = {"etat": "attente", "prevu_s": None, "prevu_pct": None, "duree": None, "reste": None, "t0": None}
    g = {"etat": "attente", "cible": None, "source": None}
    d = {"n_trailers": None, "trailers": trailers, "entracte": e, "generique": g}
    for t, l in lignes:
        m = re.match(r"Trailer (\d+)/(\d+) : (.*)$", l)
        if m:
            for tr in trailers:
                tr["etat"] = "fait"
            d["n_trailers"] = int(m.group(2))
            trailers.append({"titre": m.group(3), "duree": None, "pos": None, "total": None,
                             "t": None, "etat": "actif", "pub": False, "reste": None})
            continue
        if trailers and trailers[-1]["etat"] == "actif":
            tr = trailers[-1]
            m = re.match(r"  Durée attendue : (\d+)$", l)
            if m:
                tr["duree"] = int(m.group(1))
                continue
            m = re.match(r"  \w+ \| (.*) \| (\d+)/(\d+)s \| (trailer|pub ou chargement)$", l)
            if m:
                if m.group(4) == "trailer":
                    tr.update(pos=int(m.group(2)), total=int(m.group(3)), t=t, pub=False)
                else:
                    tr["pub"] = True
                continue
        if l.startswith("Retour au film"):
            for tr in trailers:
                tr["etat"] = "fait"
        m = re.match(r"Entracte prévue à (\d+)s \((\d+) %\)", l)
        if m:
            e["prevu_s"], e["prevu_pct"] = int(m.group(1)), int(m.group(2))
        if l.startswith("Avance manuelle au delà de l'entracte prévue") and l.endswith("entracte annulée"):
            e.update(etat="annulee")
        if l.startswith("Entracte réarmée"):
            e["etat"] = "attente"
        m = re.match(r"ENTRACTE : écran entracte pour (\d+) secondes", l)
        if m:
            e.update(etat="actif", duree=int(m.group(1)), t0=t)
        if l.startswith("Reprise du film"):
            e["etat"] = "fait"
        m = re.match(r"(?:Générique attendu vers|Minutage du générique : vers) (\d+)s(?: sur \d+s)?\s*(.*)$", l)
        if m:
            g["cible"] = int(m.group(1))
            reste = m.group(2)
            g["source"] = "TheIntroDB" if "TheIntroDB" in reste else (
                "repli série" if "série" in reste else ("repli film" if "film" in reste else ("repli" if reste else None)))
        if l.startswith("Générique :") or l.startswith("Fin du test des lumières"):
            g["etat"] = "fait"
        # Lot 2.6.89 : un recul sous le seuil rend la main au film ou à l'épisode ; un épisode enchaîné repart de zéro.
        if l.startswith("Retour avant le générique"):
            g["etat"] = "attente"
        if l.startswith("Épisode suivant"):
            g.update(etat="attente", cible=None, source=None)
    for tr in trailers:
        if tr["etat"] == "actif" and tr["pos"] is not None and tr["t"]:
            longueur = tr["duree"] or tr["total"] or 0
            cible = 5 if test else max(0, longueur - charger()["trailer_marge_fin_s"])
            tr["reste"] = max(0, int(cible - (tr["pos"] + (maintenant - tr["t"]))))
        tr.pop("t", None)
    if e["etat"] == "actif" and e["t0"] and e["duree"]:
        e["reste"] = max(0, int(e["duree"] - (maintenant - e["t0"])))
    e.pop("t0", None)
    return d


def hms(s):
    s = max(0, int(s))
    return "%d:%02d:%02d" % (s // 3600, s % 3600 // 60, s % 60) if s >= 3600 else "%d:%02d" % (s // 60, s % 60)


ETAPES_FILM = (("preparation", "Préparation"), ("bandes_annonces", "Bandes annonces"), ("film", "Film"), ("entracte", "Entracte"), ("generique", "Générique"))
ETAPES_SERIE = (("preparation", "Préparation"), ("film", "Épisode"), ("generique", "Générique"))    # la clé film est un contrat, le nom dit ce que c'est


def etapes_seance(lignes, detail, serie, entracte_actif=True, ba_actif=True, explicites=()):
    """Scénario de la séance en cours sous forme d'étapes : fait, en_cours, pause, a_venir ou saute, avec un court détail.
    Tout est déduit du journal que le service lit déjà (aucune ligne de seance.py ou de relais.py n'est modifiée) : chaque
    étape n'est marquée que par une ligne qui prouve qu'elle a commencé ou fini. Ce qui n'a pas de ligne reste à venir.
    L'étape Film reste en cours pendant tout le film, y compris après l'entracte, et passe en pause pendant l'entracte."""
    L = [l for _, l in lignes]

    def a(*debuts):
        return any(l.startswith(d) for l in L for d in debuts)

    e, g, trailers = detail["entracte"], detail["generique"], detail["trailers"]
    fin = a("Séance terminée")
    gen = g["etat"] == "fait"
    film_lance = a("Retour au film", "Démarrage du film", "Séance reprise", "Film :", "Série :", "Épisode :", "Épisode suivant")
    prep_faite = film_lance or a("Lancement du film", "Trailer ")
    prep_vue = a("Réveil de l'Apple TV", "Ambiance douce")
    out = {}
    # Préparation
    if prep_faite:
        out["preparation"] = ("fait", "") if prep_vue or not film_lance else ("saute", "Épisode déjà lancé" if serie else "Film déjà lancé")
    else:
        out["preparation"] = ("en_cours", "Réveil de l'Apple TV, lumières, choix des bandes annonces")
    # Bandes annonces
    if not ba_actif and not a("Trailer "):
        out["bandes_annonces"] = ("saute", "Désactivées pour cette séance" if "ba" in explicites else "Désactivées")
    elif a("Séance reprise") and not a("Trailer "):
        out["bandes_annonces"] = ("saute", "Séance reprise en cours de film")
    elif a("Aucune bande annonce", "Démarrage du film, sans bande annonce"):
        out["bandes_annonces"] = ("saute", "Aucune bande annonce trouvée")
    elif a("Retour au film") or film_lance:
        out["bandes_annonces"] = ("fait", "%d sur %d" % (len(trailers), detail["n_trailers"] or len(trailers)) if trailers else "")
    elif trailers:
        actif = next((t for t in reversed(trailers) if t["etat"] == "actif"), trailers[-1])
        txt = "Bande annonce %d sur %s" % (len(trailers), detail["n_trailers"] or "?")
        if actif.get("pub"):
            txt += ", publicité en cours"
        elif actif.get("reste") is not None:
            txt += ", reste " + hms(actif["reste"])
        out["bandes_annonces"] = ("en_cours", txt)
    elif a("Lancement du film"):
        out["bandes_annonces"] = ("en_cours", "Ouverture de la première bande annonce")
    else:
        out["bandes_annonces"] = ("a_venir", "")
    # Film
    if not film_lance:
        out["film"] = ("a_venir", "")
    elif gen or fin:
        out["film"] = ("fait", "")
    elif e["etat"] == "actif":
        out["film"] = ("pause", "En pause pendant l'entracte")
    else:
        out["film"] = ("en_cours", "")
    # Entracte
    if e["etat"] == "fait":
        out["entracte"] = ("fait", "")
    elif e["etat"] == "annulee":
        out["entracte"] = ("saute", "Annulée après une avance manuelle")
    elif e["etat"] == "actif":
        out["entracte"] = ("en_cours", "reste " + hms(e["reste"]) if e["reste"] is not None else "")
    elif gen or fin:
        out["entracte"] = ("saute", "Pas d'entracte cette fois")
    elif e["prevu_s"] is not None:
        out["entracte"] = ("a_venir", "Prévu à %s (%d %%)" % (hms(e["prevu_s"]), e["prevu_pct"]))
    elif not entracte_actif:
        out["entracte"] = ("saute", "Désactivé pour cette séance" if "entracte" in explicites else "Désactivé dans les réglages")
    else:
        out["entracte"] = ("a_venir", "")
    # Générique
    if fin:
        out["generique"] = ("fait", "")
    elif gen:
        out["generique"] = ("en_cours", "Lumières rallumées")
    else:
        out["generique"] = ("a_venir", "Vers " + hms(g["cible"]) if g["cible"] else "")
    liste = ETAPES_SERIE if serie else ETAPES_FILM
    return [{"cle": cle, "nom": nom, "etat": out[cle][0], "detail": out[cle][1]} for cle, nom in liste]


# Lot 2.6.89 : seance.py confie l'épisode d'une série au relais du service dès qu'il joue (contrat de journal, voir seance_serie).
LIGNE_EPISODE_CONFIE = "Épisode confié au service"


def confier_episode():
    meta = meta_de_la_seance()
    d = LANCEMENT.get("d") or {}
    if meta and d.get("total_fichier"):
        meta = dict(meta, total_fichier=d["total_fichier"])
    relais.demarrer({"test": etat["mode"] != "reel", "meta": meta, "type": "serie", "silencieux": True})


def lecteur(proc, fichier):
    for ligne in proc.stdout:
        ligne = ligne.rstrip("\n")
        if etat["proc"] is not proc:
            continue
        etat["lignes"].append((time.time(), ligne))
        save_session_scenario()
        if ligne in LIGNES_FIN and etat["proc"] is proc:
            terminer_seance_normalement()
        if ligne == LIGNE_EPISODE_CONFIE and etat["proc"] is proc:
            confier_episode()
        try:
            fichier.write(ligne + "\n")
            fichier.flush()
        except Exception:
            pass
    try:
        fichier.close()
    except Exception:
        pass


# ---------- Apple TV, Hue ----------
def atv(*args, delai=25, identifiant=None):
    commande = atv_commande(identifiant)
    if not commande:
        return False, "Apple TV non configurée."
    try:
        r = subprocess.run(commande + list(args), capture_output=True, text=True, timeout=delai)
        sortie = (r.stdout + r.stderr).strip()
        if r.returncode:
            return False, erreurs.message(sortie, "Commande Apple TV impossible.")
        return True, sortie
    except Exception as e:
        return False, erreurs.message(e, 'L’Apple TV ne répond pas.')


def lumieres_tv(action):
    if not integrations.hue():
        raise RuntimeError("Pont Hue non configuré.")
    from lumieres import Hue
    h = Hue()
    if action == "on":
        h.tv_allumees()
    else:
        h.tv_eteintes()


def _essayer(fn, *args):
    try:
        fn(*args)
    except Exception as e:
        if integrations.hue():            # sans pont Hue configuré, rien à signaler (2.6.107)
            print("Lumières :", e)


INFUSE_ID = "com.firecore.infuse"
NOMS_APPS = {"com.netflix.Netflix": "Netflix", "com.google.ios.youtube": "YouTube", "com.disney.disneyplus": "Disney+", "com.amazon.aiv.AIVApp": "Prime Video",
             "com.apple.TVWatchList": "Apple TV", "com.apple.TVMusic": "Musique", "com.apple.TVPodcasts": "Podcasts", "com.spotify.client": "Spotify",
             "com.canal.canalplus": "Canal+", "fr.francetv.france.tv": "france.tv", "tv.molotov.app": "Molotov", "com.plexapp.plex": "Plex", "tv.twitch": "Twitch"}


def contenu_ailleurs(p):
    """Le contenu qui joue sur l'Apple TV dans une app autre qu'Infuse (ou un contenu court), d'après pyatv. Rien si rien ne joue ou ne fait pause.
    Certaines apps ne donnent presque rien : il reste au moins le nom de l'app et l'état. pyatv donne l'app qui a joué en dernier, pas celle qui est
    au premier plan : on ne l'affiche donc que pendant une lecture ou une pause."""
    # 2.6, lot J, point 5 : un film Infuse qui vient de démarrer (total encore à 0, le temps que pyatv connaisse sa durée) tombait
    # ici, faute des plus de 600 s exigées par "actif" : il s'affichait un instant comme "ailleurs" (bloc réduit, aucun pilotage
    # possible) avant de basculer sur le bloc complet dès que la durée arrivait. Une vidéo Infuse n'est jamais "ailleurs", quelle
    # que soit sa durée : au pire, elle n'est pas encore assez longue pour être une séance (rien ne s'affiche, plutôt qu'à tort).
    if not etat_seance.media_reel(p) or time.time() - p.get("lu_a", 0) > charger()["atv_etat_max_age_s"] or (p.get("app") in (None, "", INFUSE_ID) and p.get("media") == "Video"):
        return None
    # 2.6, lot J, point 3 (suite, constat matériel) : quitter une app vers l'accueil de l'Apple TV sans la fermer ne fait pas
    # forcément changer metadata.app ni l'état de lecture côté pyatv (limite documentée dans docs/architecture.md, section 9,
    # et docs/audit_seance.md) : YouTube en pause peut rester affiché indéfiniment après l'avoir quitté ainsi. Impossible de
    # distinguer une vraie pause prolongée d'une app qu'on a quittée sans preuve supplémentaire : au delà de
    # atv_ailleurs_pause_max_s de pause sans le moindre changement (app, état confondus), on considère que ce n'est plus une
    # lecture réelle. "depuis" vient de la connexion continue (atvlive.py) ; absent (repli en ligne de commande), la
    # vérification ne s'applique simplement pas.
    if p.get("etat") == "Paused" and p.get("depuis", 0) > charger()["atv_ailleurs_pause_max_s"]:
        return None
    app = p.get("app")
    art = atvlive.LIVE.jaquette
    return {"app": app, "app_nom": p.get("app_nom") or NOMS_APPS.get(app) or (app.split(".")[-1] if app else "Apple TV"), "titre": p.get("titre") or "",
            "artiste": p.get("artiste") or "", "album": p.get("album") or "", "serie_nom": p.get("serie_nom") or "", "saison": p.get("saison_n") or 0,
            "episode": p.get("episode_n") or 0, "media": p.get("media") or "", "etat": p["etat"], "pos": p.get("pos") or 0, "total": p.get("total") or 0,
            "commandes": p.get("commandes") or {}, "url": atvlive.lien_youtube(p),
            "jaquette": art["h"] if art and art.get("octets") and art.get("h") == atvlive.cle_image(p) else None}


@app.get("/appletv/jaquette")
def appletv_jaquette(h: str = ""):
    """L'image du contenu en lecture ailleurs, quand pyatv l'a fournie (h : identifiant du contenu, pour que le navigateur ne garde pas une ancienne)."""
    art = atvlive.LIVE.jaquette
    p = atvlive.LIVE.lire()
    if (not art or not art.get("octets") or art.get("h") != h or not p
            or atvlive.cle_image(p) != h or not contenu_ailleurs(p)):
        return Response(status_code=404)
    return Response(art["octets"], media_type=art.get("mime") or "image/jpeg", headers={"Cache-Control": "no-store"})


def lire_playing_cli(delai=14):
    if APP_OK["ok"] is not False or time.time() - APP_OK["t"] > 300:
        ok, sortie = atv("playing", "app", "power_state", delai=delai)
        if ok:
            APP_OK["ok"] = True
        else:
            ok, sortie = atv("playing", delai=min(10, delai))
            if ok:
                APP_OK.update(ok=False, t=time.time())
    else:
        ok, sortie = atv("playing", delai=min(10, delai))
    if not ok:
        return None

    def champ(motif):
        m = re.search(motif, sortie)
        return m.group(1).strip() if m else ""

    return atvlive.LIVE.observe(dict(lecture_infuse.depuis_cli(sortie), lu_a=time.time(),
                artiste=champ(r"Artist: (.*)"), album=champ(r"Album: (.*)"), genre=champ(r"Genre: (.*)"), hash=""), "cli")


PLAYBACK_SNAPSHOT = threading.local()


def lire_playing(frais=False):
    """État de l'Apple TV, annoté de l'identité TMDB déjà résolue (lot 2.6.83) : rattachement, Home et contrôleurs partagent la
    même identité du média observé."""
    if hasattr(PLAYBACK_SNAPSHOT, "value"):
        return PLAYBACK_SNAPSHOT.value
    return memoriser_identite_infuse(annoter_identite(completer_metadonnees(qualifier_infuse(_lire_playing_brut(frais)))))


# ---------- Lecture Infuse actuelle ou non, mémoire d'identité (lot 2.6.88) ----------
# pyatv garde l'état Paused d'Infuse, avec titre et position, quand on quitte le lecteur : metadata.app n'est pas une preuve
# du premier plan. Deux signaux observables écartent une pause Infuse : une autre lecture a réellement progressé depuis la
# dernière progression de ce flux (registre, même règle que pour YouTube en 2.6.87), ou l'Apple TV ne propose plus aucune
# commande de lecture (lecteur fermé). Une pause réelle dans Infuse garde ses commandes et reste affichée.
MEMOIRE_INFUSE = {"p": None}
MEMOIRE_INFUSE_VALIDITE_S = 30 * 60


def qualifier_infuse(p):
    if not p or p.get("app") not in (None, "", INFUSE_ID) or p.get("etat") != "Paused":
        return p
    raison = None
    try:
        if atvlive.LIVE.observations.infuse_depassee(p):
            raison = "pause Infuse antérieure à une lecture plus récente"
    except Exception:
        pass
    if not raison and p.get("contexte_depasse") is not None:
        # Lot 2.6.90 : l'Apple TV a signalé une autre application depuis la dernière progression de ce flux (Netflix ouvert,
        # YouTube relancé) : cette pause n'est plus que republiée.
        raison = "pause Infuse antérieure au passage à %s" % (NOMS_APPS.get(p["contexte_depasse"]) or p["contexte_depasse"] or "l'accueil")
    f = p.get("fonctions")
    if not raison and isinstance(f, dict) and f and not any(f.get(k) for k in ("play", "play_pause", "pause")):
        raison = "lecteur Infuse fermé : aucune commande de lecture disponible"
    return dict(p, stale=True, stale_reason=raison, infuse_ecartee=raison) if raison else p


def memoriser_identite_infuse(p):
    """Retient les métadonnées publiées pour le flux Infuse actuel, pour combler un relevé du même flux arrivé sans titre. Toute
    nouvelle identité explicite remplace la mémoire ; un relevé sans titre d'une autre durée l'efface (autre flux)."""
    if not (p and p.get("app") in (None, "", INFUSE_ID) and not p.get("stale") and p.get("etat") in ("Playing", "Paused")
            and (p.get("total") or 0) > 0):
        return p
    m = MEMOIRE_INFUSE["p"]
    if (p.get("titre") or "").strip() and p.get("complement") != "mémoire d'identité":
        MEMOIRE_INFUSE["p"] = {"titre": p.get("titre"), "serie_nom": p.get("serie_nom"), "saison_n": p.get("saison_n"),
                               "episode_n": p.get("episode_n"), "total": p["total"], "pos": p.get("pos") or 0, "t": time.time()}
    elif m and abs(m["total"] - p["total"]) <= 2:
        # Lot 2.6.90 : chaque relevé du même flux prolonge la mémoire (un épisode peut rester des dizaines de minutes sans titre).
        m.update(pos=p.get("pos") or 0, t=time.time())
    elif m and not (p.get("titre") or "").strip():
        MEMOIRE_INFUSE["p"] = None
    return p


def _memoire_du_meme_flux(p):
    """Lot 2.6.90 (journal matériel : Reacher S1E6, durée 2861, publié avec son titre à 483 s puis sans titre vers 2710 s) : le même
    flux, c'est la même durée à 2 s près, sans autre flux Infuse observé entre temps (memoriser_identite_infuse efface la mémoire
    sinon). La position n'est plus exigée : une avance ou un recul dans la télécommande reste le même fichier. Un titre vide
    signifie « identité non fournie dans ce relevé », jamais « autre média »."""
    m = MEMOIRE_INFUSE["p"]
    if not m or time.time() - m["t"] > MEMOIRE_INFUSE_VALIDITE_S or abs(m["total"] - p["total"]) > 2:
        return None
    return m


# ---------- Réévaluation des métadonnées Infuse (lot 2.6.86) ----------
# Constat matériel : après YouTube, Infuse peut lire (Playing, durée, position) alors que la connexion continue publie un titre
# vide, sans jamais le compléter ; un second lancement le rendait complet. Tant que c'est le cas, une lecture ponctuelle par
# une connexion neuve (atvremote, lire_playing_cli) rapporte l'état complet. Elle complète l'observation seulement pour le même
# fichier (même app, même durée à 2 s près) et ne remplace jamais un titre déjà publié.
COMPLEMENT = {"t": 0.0, "p": None, "busy": False}
COMPLEMENT_PAS_S = 12
COMPLEMENT_VALIDITE_S = 90


def _titre_absent(p):
    return (p and p.get("app") in (None, "", INFUSE_ID) and p.get("etat") in ("Playing", "Paused")
            and not (p.get("titre") or "").strip() and not (p.get("serie_nom") or "").strip() and (p.get("total") or 0) > 0)


def lecture_sans_identite_infuse(p):
    """Infuse lit (ou tient en pause) un flux réel dont ni le titre ni la série ne sont encore publiés."""
    return bool(_titre_absent(p) and not p.get("stale") and not p.get("veille"))


def _sonder_metadonnees():
    try:
        q = lire_playing_cli(delai=10)
        if q and (q.get("titre") or q.get("serie_nom")) and q.get("app") in (None, "", INFUSE_ID):
            COMPLEMENT["p"] = dict(q, recu=time.time())
        journal_chaine("complement", {"titre_cli": (q or {}).get("titre"), "duree_cli": (q or {}).get("total"),
                                      "app_cli": (q or {}).get("app"), "utile": bool(COMPLEMENT["p"] and COMPLEMENT["p"].get("recu", 0) >= time.time() - 1)})
    except Exception as e:
        journal_chaine("complement", {"erreur": str(e)[:120]})
    finally:
        COMPLEMENT["busy"] = False


def completer_metadonnees(p):
    if not _titre_absent(p) or p.get("stale"):
        return p
    m = _memoire_du_meme_flux(p)
    if m:
        # Lot 2.6.88 : même flux déjà identifié (même durée, position cohérente) : l'identité n'est pas oubliée pour un
        # relevé passager sans titre. Aucun appel réseau.
        return dict(p, **{k: m.get(k) for k in ("titre", "serie_nom", "saison_n", "episode_n") if m.get(k)},
                    complement="mémoire d'identité")
    c = COMPLEMENT["p"]
    if c and time.time() - c.get("recu", 0) < COMPLEMENT_VALIDITE_S and abs((c.get("total") or 0) - p["total"]) <= 2:
        return dict(p, **{k: c.get(k) for k in ("titre", "serie_nom", "saison_n", "episode_n") if c.get(k)},
                    complement="connexion neuve")
    if not COMPLEMENT["busy"] and time.time() - COMPLEMENT["t"] >= COMPLEMENT_PAS_S:
        COMPLEMENT.update(busy=True, t=time.time())
        threading.Thread(target=_sonder_metadonnees, daemon=True).start()
    return p


# ---------- Journal de la chaîne d'identification (lot 2.6.86) ----------
# De l'observation Apple TV à l'état de la Home : une ligne par étape, seulement quand son contenu change. Aucun secret.
CHAINE_SIG = {}


def journal_chaine(etape, donnees, hors_signature=("position",)):
    sig = json.dumps({k: v for k, v in donnees.items() if k not in hors_signature}, sort_keys=True, ensure_ascii=False, default=str)
    if CHAINE_SIG.get(etape) == sig:
        return
    CHAINE_SIG[etape] = sig
    entree = dict(donnees, etape=etape, t=time.strftime("%Y-%m-%d %H:%M:%S"))
    RATTACHEMENTS["lignes"].append(entree)
    try:
        if os.path.exists(RATTACHEMENT_LOG) and os.path.getsize(RATTACHEMENT_LOG) > 256 * 1024:
            os.replace(RATTACHEMENT_LOG, RATTACHEMENT_LOG + ".1")
        with open(RATTACHEMENT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entree, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass


def _media_court(meta):
    return {k: (meta or {}).get(k) for k in ("type", "id", "saison", "episode", "titre")} if meta else None


def resume_seance():
    d = LANCEMENT.get("d") or {}
    if not d:
        return None
    return {"type": d.get("type"), "id": d.get("id"), "saison": d.get("saison"), "episode": d.get("episode"),
            "origine": d.get("origine"), "confirmee": bool(d.get("confirme")), "etat": d.get("etat")}


def annoter_identite(p):
    """Ajoute p["identite"] (type, id, saison, épisode) quand la recherche TMDB de /film a déjà résolu CETTE observation : même
    clé que meta_titre_rapide, lue dans le cache sans réseau. Une série n'est identifiée qu'avec un épisode certain."""
    if not p or p.get("identite") or p.get("app") not in (None, "", INFUSE_ID) or p.get("etat") not in ("Playing", "Paused"):
        return p
    info = lecture_infuse.identite_observee(p)
    if not info["nom"] or (info["type"] == "tv" and not info["episode_certain"]):
        return p
    meta = cache_tmdb.get(("titre", norm(info["nom"]), info["saison"], info["episode"]))
    if not meta or not meta.get("id"):
        return p
    ident = {"type": meta["type"], "id": meta["id"]}
    if meta["type"] == "tv":
        ident.update(saison=info["saison"], episode=info["episode"])
    return dict(p, identite=ident)


def _lire_playing_brut(frais=False):
    """État de l'Apple TV. La connexion continue répond sans lancer de processus ; sinon on interroge en ligne de commande, rarement.
    frais=True : pendant une séance, on veut la vérité tout de suite, sans attendre."""
    l = atvlive.LIVE.frais() if frais else atvlive.LIVE.lire()
    if l is not None:
        if atvlive.LIVE.age() is not None and atvlive.LIVE.age() <= charger()["atv_etat_max_age_s"]:
            return l
        l = atvlive.LIVE.frais()        # trop vieux pour agir dessus : on force une relecture
        if l is not None and atvlive.LIVE.age() <= charger()["atv_etat_max_age_s"]:
            return l
    if frais:
        return lire_playing_cli()
    if time.time() - CLI["t"] < CLI_PAS:
        p = CLI["derniere"]
        return atvlive.LIVE.observe(p, "cli_cache") if p and time.time() - p.get("lu_a", 0) <= charger()["atv_etat_max_age_s"] else None
    CLI["derniere"] = lire_playing_cli()
    CLI["t"] = time.time()
    return CLI["derniere"]


# ---------- TMDB (fiche du film ou de la série) ----------


def lire_secrets():
    try:
        with open(SECRETS, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def ecrire_prive(chemin, data):
    json_atomique.ecrire(chemin, data)
    try:
        os.chmod(chemin, 0o600)
    except Exception:
        pass


def tmdb_get(chemin, langue="fr-FR", **params):
    cle = lire_secrets().get("tmdb")
    if not cle:
        raise RuntimeError("Clé TMDB absente")
    entetes, p = {}, dict(params, language=langue)
    if cle.startswith("eyJ"):
        entetes["Authorization"] = "Bearer " + cle
    else:
        p["api_key"] = cle
    r = requests.get(TMDB + chemin, params=p, headers=entetes, timeout=6)
    if r.status_code == 401:
        raise PermissionError("Clé TMDB refusée")
    r.raise_for_status()
    return r.json()


def details(type_, id_):
    return tmdb_get("/%s/%d" % ("tv" if type_ == "tv" else "movie", id_), append_to_response="external_ids")


def details_complets(type_, id_):
    """Fiche TMDB avec distribution, classification et identifiants externes. Gardée 10 minutes."""
    k = (type_, id_)
    lock = verrous_details.setdefault(k, threading.Lock())
    with lock:
        c = cache_details.get(k)
        if c and time.time() - c[0] < charger()["catalogue_cache_min"] * 60:
            return c[1]
        extra = "external_ids,credits,content_ratings" if type_ == "tv" else "external_ids,credits,release_dates"
        f = tmdb_get("/%s/%d" % ("tv" if type_ == "tv" else "movie", id_), append_to_response=extra)
        cache_details[k] = (time.time(), f)
        return f


def liens_de(type_, f):
    imdb = f.get("imdb_id") or (f.get("external_ids") or {}).get("imdb_id")
    return {"tmdb": "https://www.themoviedb.org/%s/%s" % ("tv" if type_ == "tv" else "movie", f.get("id")),
            "imdb": "https://www.imdb.com/title/%s/" % imdb if imdb else None}


def image(chemin, taille):
    return IMG + "/" + taille + chemin if chemin else None


def meta_film(f):
    return {"type": "movie", "id": f.get("id"), "titre": f.get("title") or "",
            "titre_original": f.get("original_title") or "",
            "annee": (f.get("release_date") or "")[:4], "date_sortie": f.get("release_date") or "", "synopsis": f.get("overview") or "",
            "affiche": image(f.get("poster_path"), "w342"), "fond": image(f.get("backdrop_path"), "w780"),
            "note": round(f.get("vote_average") or 0, 1), "votes": f.get("vote_count") or 0,
            "duree": f.get("runtime"), "liens": liens_de("movie", f)}


def meta_serie(f, ep=None, saison=None, episode=None):
    synopsis, fond = f.get("overview") or "", image(f.get("backdrop_path"), "w780")
    note, votes, duree, ep_titre = round(f.get("vote_average") or 0, 1), f.get("vote_count") or 0, None, ""
    if ep:
        synopsis = ep.get("overview") or synopsis
        fond = image(ep.get("still_path"), "w780") or fond
        ep_titre = ep.get("name") or ""
        if ep.get("vote_average"):
            note, votes = round(ep["vote_average"], 1), ep.get("vote_count") or 0
        duree = ep.get("runtime")
    if not duree:
        rt = f.get("episode_run_time") or []
        duree = rt[0] if rt else None
    return {"type": "tv", "id": f.get("id"), "titre": f.get("name") or "",
            "titre_original": f.get("original_name") or "",
            "annee": (f.get("first_air_date") or "")[:4], "date_sortie": (ep.get("air_date") if ep else None) or f.get("first_air_date") or "", "synopsis": synopsis,
            "synopsis_serie": f.get("overview") or "",
            "affiche": image(f.get("poster_path"), "w342"), "fond": fond,
            "note": note, "votes": votes, "duree": duree, "saisons": f.get("number_of_seasons"),
            "saison": saison, "episode": episode, "ep_titre": ep_titre, "tvdb_id": (f.get("external_ids") or {}).get("tvdb_id"), "liens": liens_de("tv", f)}


def meta_par_id(type_, id_, saison=None, episode=None):
    cle = (type_, id_, saison, episode)
    if cle in cache_tmdb:
        return cache_tmdb[cle]
    meta = None
    try:
        f = details(type_, id_)
        if type_ == "tv":
            ep = None
            if saison and episode:
                try:
                    ep = tmdb_get("/tv/%d/season/%d/episode/%d" % (id_, saison, episode))
                except Exception:
                    ep = None
            meta = meta_serie(f, ep, saison, episode)
        else:
            meta = meta_film(f)
    except Exception:
        meta = None
    cache_tmdb[cle] = meta
    return meta


def variantes_titre(nom):
    n = re.sub(r"\[[^\]]*\]|\{[^}]*\}", " ", nom or "")
    n = re.sub(r"\b(?:2160p|1080p|720p|4k|hdr10?|dv|atmos|blu-?ray|web-?dl|x26[45]|h\.?26[45]|remux|vff?|multi|truefrench)\b", " ", n, flags=re.I)
    if n.count(".") + n.count("_") >= 3:
        n = re.sub(r"[._]+", " ", n)
    n = re.sub(r"\s+", " ", n).strip(" -–—.")
    annee = None
    m = re.search(r"\((\d{4})\)\s*$", n) or re.search(r"\b((?:19|20)\d{2})\s*$", n)
    if m and m.start() > 0:
        annee = int(m.group(1))
        n = n[:m.start()].strip(" -–—.(")
    base = n
    v = [base]
    for sep in (":", " - ", " – "):
        if sep in base:
            v.append(base.split(sep)[0].strip())
    sans = "".join(c for c in unicodedata.normalize("NFKD", base) if not unicodedata.combining(c))
    v.append(sans)
    return list(dict.fromkeys(x for x in v if x)), annee


RESSEMBLANCE_MIN = 0.8


def meta_par_titre(info, total):
    # La clé ne porte pas la durée observée : elle change dans les premières secondes (pyatv ne la connaît pas tout de
    # suite), ce qui relançait une recherche déjà résolue et retardait l'affichage sans raison, jusqu'au prochain
    # changement d'état qui la stabilisait. La durée ne sert plus qu'au départage ci dessous, sur cet appel précis.
    cle = ("titre", norm(info["nom"]), info["saison"], info["episode"])
    if cle in cache_tmdb and cache_tmdb[cle]:
        return cache_tmdb[cle]
    if time.time() - cache_neg.get(cle, 0) < 25:
        return None
    meta = None
    try:
        tv = info["type"] == "tv"
        variantes, annee = variantes_titre(info["nom"])
        candidats = {}
        for q in variantes[:3]:
            params = {"query": q}
            if annee:
                params["year" if not tv else "first_air_date_year"] = annee
            for r in tmdb_get("/search/tv" if tv else "/search/movie", **params)["results"][:8]:
                nom = r.get("name") if tv else r.get("title")
                orig = r.get("original_name") if tv else r.get("original_title")
                ratio = max(difflib.SequenceMatcher(None, norm(q), norm(nom or "")).ratio(),
                            difflib.SequenceMatcher(None, norm(q), norm(orig or "")).ratio())
                if ratio < RESSEMBLANCE_MIN:
                    # Lot 2.6.84 : « Reacher » ne devient jamais « Jack Reacher » ; mieux vaut une lecture non identifiée.
                    continue
                date = (r.get("first_air_date") if tv else r.get("release_date")) or ""
                score = ratio + (0.25 if annee and date[:4] == str(annee) else 0) + min(0.15, (r.get("popularity") or 0) / 800)
                if r["id"] not in candidats or score > candidats[r["id"]][0]:
                    candidats[r["id"]] = (score, r)
            if candidats and max(v[0] for v in candidats.values()) >= 1.0:
                break
        classes = sorted(candidats.values(), key=lambda x: x[0], reverse=True)
        if classes:
            if tv and len(classes) > 1 and classes[0][0] - classes[1][0] < 0.08:
                # Pour une lecture externe, un titre de série ambigu reste brut plutôt que d'afficher
                # silencieusement une fiche TMDB plausible mais possiblement fausse.
                cache_neg[cle] = time.time()
                return None
            choix = classes[0][1]
            if not tv and total and len(classes) > 1 and classes[0][0] - classes[1][0] < 0.08:
                def duree(r):
                    try:
                        return abs((details_complets("movie", r["id"]).get("runtime") or 0) * 60 - total)
                    except Exception:
                        return 10 ** 9
                choix = min([classes[0][1], classes[1][1]], key=duree)
            meta = meta_par_id("tv" if tv else "movie", choix["id"], info["saison"], info["episode"])
    except Exception:
        meta = None
    if meta:
        cache_tmdb[cle] = meta
    else:
        cache_neg[cle] = time.time()
    return meta


def meta_titre_rapide(info, total):
    """Ne fait jamais attendre : la recherche TMDB tourne en arrière plan et le résultat arrive au passage suivant."""
    cle = ("titre", norm(info["nom"]), info["saison"], info["episode"])
    if cache_tmdb.get(cle):
        return cache_tmdb[cle], False
    if time.time() - cache_neg.get(cle, 0) < 25:
        return None, False
    if cle not in ident_en_cours:
        ident_en_cours.add(cle)

        def go():
            try:
                meta_par_titre(info, total)
            finally:
                ident_en_cours.discard(cle)
        threading.Thread(target=go, daemon=True).start()
    return None, True


def film_choisi():
    try:
        with open(FILM_CHOISI, encoding="utf-8") as f:
            c = json.load(f)
        if time.time() - c["t"] < charger()["film_choisi_heures"] * 3600 and c.get("id"):
            return c
    except Exception:
        pass
    return None


def oublier_film_choisi():
    try:
        os.remove(FILM_CHOISI)
    except Exception:
        pass


def resoudre_type(demande):
    if demande in ("film", "serie"):
        return demande
    p = lire_playing()
    if p and p["media"] in ("Video", "TV") and lecture_infuse.identite_observee(p)["type"] == "tv":
        return "serie"
    return "film"


# ---------- Overseerr, catalogue, lancement dans Infuse ----------
base_overseerr = bibliotheque.ov_base
overseerr_appel = bibliotheque.ov_appel


def ov_notes(type_, id_):
    """Scores tiers désactivés dans la bêta publique, sans requête fournisseur."""
    return {}


def carte(r):
    return {"id": r["id"], "type": r.get("media_type"), "titre": r.get("title") or r.get("name") or "",
            "annee": (r.get("release_date") or r.get("first_air_date") or "")[:4],
            "affiche": image(r.get("poster_path"), "w185"), "note": round(r.get("vote_average") or 0, 1),
            "genres": r.get("genre_ids") or []}


def url_infuse(type_, id_, saison=None, episode=None):
    if type_ == "tv":
        if not (saison and episode):
            return None
        return "infuse://series/%d-%d-%d?play" % (id_, saison, episode)
    return "infuse://movie/%d?play" % id_


def reveiller(attente=25):
    """Réveille l'Apple TV si elle est en veille et attend qu'elle réponde. Renvoie vrai si elle dormait."""
    ok, sortie = atv("power_state", delai=10)
    if not (ok and "off" in sortie.lower()):
        return False
    atv("turn_on", delai=10)
    debut = time.time()
    while time.time() - debut < attente:
        time.sleep(2)
        ok, sortie = atv("power_state", delai=8)
        if ok and "off" not in sortie.lower():
            break
    time.sleep(4)
    return True


def memoriser_recent(type_, id_, saison=None, episode=None):
    try:
        meta = meta_par_id(type_, id_, saison, episode)
        if not meta:
            return
        try:
            with open(RECENTS, encoding="utf-8") as f:
                liste = json.load(f)
        except Exception:
            liste = []
        liste = [x for x in liste if not (x["type"] == type_ and x["id"] == id_)]
        liste.insert(0, {"type": type_, "id": id_, "titre": meta["titre"], "annee": meta["annee"],
                         "affiche": meta["affiche"], "note": meta["note"], "saison": saison,
                         "episode": episode, "t": time.time()})
        ecrire_prive(RECENTS, liste[:12])
    except Exception:
        pass


# ---------- Comptes et sessions (2.6.102) ----------
# Chaque compte a son identifiant et son mot de passe, chaque navigateur sa session (comptes.py, base kamcine.db). Le code
# PIN (auth.json) ne sert plus à se connecter : il sert une dernière fois à autoriser la création du premier administrateur
# d'une installation qui en avait un. auth.json et sessions.json restent en place pour un retour arrière.
COOKIE = "kc_compte"
ETAT_BASE = {"erreur": ""}
try:
    comptes.migrer()
except comptes.BaseTropRecente as e:
    ETAT_BASE["erreur"] = str(e)
except Exception as e:
    ETAT_BASE["erreur"] = erreurs.message(e, "Base des comptes kamcine.db illisible : aucune connexion possible.")


def importer_integrations_anciennes():
    """Installation d'avant la 2.6.106 : identifiants Apple TV, Hue et Synology importés une fois de creds.env et de
    l'environnement vers secrets.json (integrations.importer_ancien). Jamais bloquant ; aucune valeur journalisée."""
    try:
        sections = integrations.importer_ancien()
        if sections:
            erreurs.journaliser_action("Configuration", "Identifiants repris de l'ancienne configuration : %s." % ", ".join(sections))
    except Exception as e:
        erreurs.message(e, "Ancienne configuration des appareils non reprise.")


importer_integrations_anciennes()


def rattacher_donnees_anciennes():
    """Données d'avant les comptes, communes, rattachées une seule fois au premier administrateur : la photo (2.6.103) si
    l'installation n'a qu'un compte, les favoris de favoris.json (2.6.104). Les anciens fichiers restent en place."""
    if ETAT_BASE["erreur"]:
        return
    try:
        comptes.rattacher_photo_ancienne(PHOTO)
    except Exception as e:
        erreurs.message(e, "Photo de profil d'avant les comptes non rattachée.")
    try:
        comptes.rattacher_favoris_anciens(favoris.anciens())
    except Exception as e:
        erreurs.message(e, "Favoris d'avant les comptes non rattachés.")


rattacher_donnees_anciennes()
JETONS_MIGRATION = {}          # jeton remis après l'ancien code valide : autorise la création du premier administrateur
DUREE_MIGRATION = 900
ECHECS = {}                    # échecs récents par adresse et par nature (connexion, ancien code)


def lire_auth():
    """Ancien verrou par code (auth.json), lu seulement pour la création du premier administrateur."""
    try:
        with open(AUTH_FILE, encoding="utf-8") as f:
            a = json.load(f)
        return a if a.get("hash") and a.get("sel") else None
    except Exception:
        return None


def hacher(code, sel):
    return hashlib.pbkdf2_hmac("sha256", code.encode(), bytes.fromhex(sel), 200000).hex()


def code_ok(code):
    a = lire_auth()
    return bool(a) and hmac.compare_digest(hacher(str(code), a["sel"]), a["hash"])


def attente_restante(request, nature):
    """Secondes à patienter après 5 échecs en une minute depuis la même adresse (0 sinon)."""
    cle = (nature, request.client.host if request.client else "")
    maintenant = time.time()
    liste = [t for t in ECHECS.get(cle, []) if maintenant - t < 60]
    ECHECS[cle] = liste
    return int(60 - (maintenant - liste[0])) + 1 if len(liste) >= 5 else 0


def noter_echec(request, nature):
    ECHECS.setdefault((nature, request.client.host if request.client else ""), []).append(time.time())


async def corps_json(request):
    try:
        c = await request.json()
        return c if isinstance(c, dict) else {}
    except Exception:
        return {}


def en_https(request):
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https"


def pose_cookie(reponse, request, jeton, duree_s):
    """HttpOnly, SameSite Lax ; Secure seulement derrière HTTPS, pour ne pas casser l'accès local en HTTP."""
    reponse.set_cookie(COOKIE, jeton, max_age=int(duree_s), httponly=True, samesite="lax", secure=en_https(request), path="/")


UI_SCREEN_PATHS = {"/home", "/catalogue", "/downloads", "/devices", "/profile", "/settings", "/alerts", "/logs", "/suggestions"}


def ui_path(path):
    return path in UI_SCREEN_PATHS or bool(re.fullmatch(r"/(films|series|people)/[0-9]+", path))


def route_libre(chemin):
    """Routes accessibles sans session : la page et ses ressources, l'entrée (état, connexion, premier compte), les routes
    techniques qui ont leur propre contrôle (raccourci Siri par jeton, /interne/ réservé à la machine elle même)."""
    return (ui_path(chemin) or chemin in ("/", "/health", "/manifest.json", "/sw.js", "/splash.jpg", "/auth/etat", "/auth/connexion", "/auth/inscription",
                       "/auth/ancien-code", "/installation/presentation")
            or chemin.startswith("/icon.png") or chemin.startswith("/raccourci/") or chemin.startswith("/interne/"))


# Routes réservées aux administrateurs (2.6.103) : réglages de l'installation, services connectés et leurs clés, raccourci
# Siri, diagnostics techniques, gestion des comptes. Vérifiées ici, au verrou, pour toute méthode : un utilisateur reçoit
# 403 quoi que montre l'interface. Le reste (catalogue, séances, appareils, téléchargements, son profil) est ouvert à
# tout compte connecté.
ROUTES_ADMIN = {
    "/reglages/reset", "/reglages/raccourci", "/reglages/raccourci/retirer",
    "/tmdb/cle", "/tmdb/retirer", "/omdb/cle", "/omdb/retirer", "/youtube/cle", "/youtube/retirer",
    "/overseerr/cle", "/overseerr/retirer", "/overseerr/synchroniser", "/arr/config", "/arr/retirer",
    "/transmission/tester", "/transmission/config", "/transmission/retirer", "/services/tester",
    "/trakt/config", "/trakt/connecter", "/trakt/verifier", "/trakt/deconnecter", "/trakt/oublier",
    "/log/clear", "/catalogue/diagnostic", "/catalogue/bande-annonce/diagnostic",
    "/appletv/rattachement", "/appletv/observations",
    "/comptes",
}
# 2.6.104 : la page Appareils est une page d'administration de l'installation. Toutes ses routes (/appareils/*) sont
# réservées ; une séance n'en utilise aucune (elle passe par /start, /stop, /telecommande…, voir PERMISSION_ROUTES).
PREFIXES_ADMIN = ("/appareils/", "/comptes/")
ROUTES_ADMIN_ECRITURE = {"/reglages"}          # lecture ouverte (l'interface en a besoin), écriture réservée
AVATAR_COMPTE = re.compile(r"^/comptes/(\d+)/avatar$")


def route_admin(methode, chemin):
    chemin = chemin.rstrip("/") or "/"
    if chemin in ROUTES_ADMIN or (chemin in ROUTES_ADMIN_ECRITURE and methode not in ("GET", "HEAD")) or chemin == "/appareils":
        return True
    return chemin.startswith(PREFIXES_ADMIN) and not AVATAR_COMPTE.match(chemin)


# Permissions d'un Utilisateur (2.6.104, comptes.PERMISSIONS), vérifiées au verrou comme les routes d'administration.
# Un administrateur les a toutes. Les lectures (état, planning, fiches) restent ouvertes : seules les actions sont gardées.
PERMISSION_ROUTES = {
    "demander": {"/overseerr/demander", "/catalogue/collection/demander", "/catalogue/episodes/demander", "/attente/ajouter"},
    "seances": {"/start", "/seance/preparation/annuler", "/stop", "/reprendre", "/entracte/manuel", "/entracte/reprendre", "/lancer", "/lancer/bande-annonce",
                "/lancer/youtube", "/lancement/reessayer", "/lancement/fermer", "/lancement/reprendre", "/telecommande",
                "/appletv/infuse", "/planning/ajouter", "/planning/modifier", "/planning/annuler", "/planning/confirmer",
                "/attente/ajouter", "/attente/annuler", "/attente/relancer", "/film/choisir", "/film/dissocier"},
    "telechargements": {"/telechargements", "/telechargement", "/telechargements/retirer", "/telechargements/termines/retirer",
                        "/arr/recherche/relancer"},
}
MESSAGES_PERMISSION = {
    "demander": "Ton compte ne peut pas faire de demande. Un administrateur de l'installation peut te l'autoriser.",
    "seances": "Ton compte ne peut pas lancer ni piloter de séance. Un administrateur de l'installation peut te l'autoriser.",
    "telechargements": "Ton compte n'a pas accès aux téléchargements. Un administrateur de l'installation peut te l'autoriser.",
}


def permission_refusee(compte, chemin):
    """La première permission qui manque à ce compte pour cette route, ou None."""
    chemin = chemin.rstrip("/") or "/"
    for permission, routes in PERMISSION_ROUTES.items():
        if chemin in routes and not comptes.a_permission(compte, permission):
            return permission
    return None


@app.middleware("http")
async def verrou(request: Request, call_next):
    chemin = request.url.path
    request.state.compte, jeton, prolongee = None, request.cookies.get(COOKIE), 0
    if jeton and not ETAT_BASE["erreur"]:
        try:
            request.state.compte, prolongee = await run_in_threadpool(comptes.session, jeton)
        except Exception:
            request.state.compte = None
    if request.state.compte is None and not route_libre(chemin):
        if ETAT_BASE["erreur"]:
            return JSONResponse({"ok": False, "message": ETAT_BASE["erreur"], "base": True}, status_code=503)
        return JSONResponse({"ok": False, "message": "Connexion requise", "connexion": True}, status_code=401)
    if request.state.compte is not None and not request.state.compte["admin"] and route_admin(request.method, chemin):
        return JSONResponse({"ok": False, "message": "Réservé à un administrateur.", "admin": True}, status_code=403)
    manque = permission_refusee(request.state.compte, chemin) if request.state.compte is not None else None
    if manque:
        return JSONResponse({"ok": False, "message": MESSAGES_PERMISSION[manque], "permission": manque}, status_code=403)
    try:
        reponse = await call_next(request)
    except Exception as e:
        return JSONResponse({"ok": False, "message": erreurs.message(e)}, status_code=500)
    if prolongee and chemin not in ("/auth/deconnexion",):
        pose_cookie(reponse, request, jeton, prolongee)
    return reponse


def compte_courant(request):
    return getattr(request.state, "compte", None)


@app.get("/apropos")
def apropos():
    """Réglages, À propos : ce que le service sait de lui même. Ni clé ni secret."""
    import platform
    from importlib import metadata
    def version(nom):
        try:
            return metadata.version(nom)
        except Exception:
            return None
    return {"ok": True, "version": VERSION_SERVICE, "demarre": DEMARRAGE, "python": platform.python_version(),
            "fuseau": planning.TIMEZONE_NAME, "pyatv": version("pyatv"), "fastapi": version("fastapi")}


@app.get("/auth/etat")
def auth_etat(request: Request):
    """Où l'interface doit mener au démarrage :
    nouvelle : aucun compte, pas d'ancien code, présentation puis création de l'administrateur ;
    migration : aucun compte mais un ancien code, présentation puis ancien code puis création de l'administrateur ;
    connexion : des comptes existent, ce navigateur n'a pas de session ;
    connecte : session valable ;
    erreur : base refusée ou illisible."""
    base = {"ok": True, "version": VERSION_SERVICE}
    if ETAT_BASE["erreur"]:
        return dict(base, etat="erreur", message=ETAT_BASE["erreur"])
    compte = compte_courant(request)
    if compte:
        return dict(base, etat="connecte", compte=compte)
    if comptes.nombre_comptes():
        return dict(base, etat="connexion", inscriptions=comptes.inscriptions_ouvertes())
    ancien = lire_auth()
    return dict(base, etat="migration" if ancien else "nouvelle", presentation=not comptes.lire_etat("presentation_terminee"),
                longueur=ancien.get("longueur", 4) if ancien else None)


def hue_configuree():
    """Le pont Hue est configuré si secrets.json (section hue) donne son adresse et sa clé (2.6.106). Aucune valeur ne sort."""
    return integrations.hue() is not None


def etat_appletv():
    """Configurée : secrets.json (section appletv) donne son identifiant et ses identifiants Companion (2.6.106).
    Connectée : la connexion continue (atvlive) a un état récent. Aucune tentative de connexion ici."""
    live = atvlive.LIVE
    try:
        connectee = (getattr(live, "etat", None) is not None
                     and time.time() - float(getattr(live, "t", 0) or 0) < 120
                     and int(getattr(live, "erreurs_consecutives", 0) or 0) < 3)
    except Exception:
        connectee = False
    return {"configuree": integrations.appletv() is not None, "connectee": bool(connectee)}


@app.get("/installation/etat")
def installation_etat(request: Request):
    """Ce qui est réellement configuré dans cette installation (2.6.105), relu à chaque fois dans la configuration elle même
    (secrets.json et reglages.json du dossier des données) : jamais un drapeau « configuration terminée », jamais une
    valeur personnelle écrite dans le code (2.6.106).
    Retirer une clé fait donc redevenir le service non configuré. pret, pour tout compte : ce qui peut marcher (séances :
    Apple TV ; catalogue : TMDB ; téléchargements : Transmission). services, pour un administrateur : le détail, sans clé."""
    sec, tr = lire_secrets(), transmission.etat()
    appletv = etat_appletv()
    uid = id_connecte(request)
    prefere = comptes.lire_preference(uid, "appletv") if uid else None
    appareils = integrations.appletvs()
    ids = {a["identifiant"] for a in appareils}
    prefere = prefere if prefere in ids else next((a["identifiant"] for a in appareils if a["active"]), "")
    appareils_session = [{k: a[k] for k in ("identifiant", "nom")} | {"active": a["identifiant"] == prefere} for a in appareils]
    out = {"ok": True, "pret": {"seances": appletv["configuree"], "catalogue": bool(sec.get("tmdb")),
                                "telechargements": bool(tr.get("configuree")), "telechargements_actifs": bool(tr.get("actif")),
                                "appletvs": appareils_session, "appletv_pref": prefere}}
    compte = compte_courant(request)
    if compte and compte["admin"]:
        arr = {a["nom"]: {"configuree": a["configuree"], "titre": a["titre"]} for a in bibliotheque.etat_config()}
        pub = integrations.etat_public()
        appletv = dict(appletv, a_choisir=pub["appletv"]["a_choisir"], nom=pub["appletv"]["nom"])
        out["services"] = dict({"appletv": appletv, "tmdb": {"configuree": bool(sec.get("tmdb"))},
                                "overseerr": {"configuree": bool(sec.get("overseerr"))},
                                "transmission": {"configuree": bool(tr.get("configuree")), "actif": bool(tr.get("actif"))},
                                "hue": {"configuree": hue_configuree(), "roles_associes": pub["hue"]["roles_associes"]},
                                "denon": {"configuree": bool(denon.actif())}}, **arr)
    return out


@app.post("/installation/presentation")
def installation_presentation():
    """La présentation a été vue jusqu'au bout (ou passée) : elle ne revient plus d'elle même."""
    if ETAT_BASE["erreur"]:
        return JSONResponse({"ok": False, "message": ETAT_BASE["erreur"]}, status_code=503)
    if not comptes.lire_etat("presentation_terminee"):
        comptes.ecrire_etat("presentation_terminee", time.time())
    return {"ok": True}


@app.post("/auth/ancien-code")
async def auth_ancien_code(request: Request):
    """Installation existante : l'ancien code KamCiné, valide une dernière fois, autorise la création de l'administrateur."""
    if ETAT_BASE["erreur"] or comptes.nombre_comptes() or lire_auth() is None:
        return JSONResponse({"ok": False, "message": "Aucun ancien code à confirmer."}, status_code=409)
    attente = attente_restante(request, "ancien")
    if attente:
        return JSONResponse({"ok": False, "message": "Trop d'essais, réessayez dans %d s." % attente}, status_code=429)
    corps = await corps_json(request)
    if not await run_in_threadpool(code_ok, str(corps.get("code", ""))[:12]):
        noter_echec(request, "ancien")
        return JSONResponse({"ok": False, "message": "Code incorrect."}, status_code=403)
    maintenant = time.time()
    for j in [j for j, t in JETONS_MIGRATION.items() if t < maintenant]:
        JETONS_MIGRATION.pop(j, None)
    jeton = secrets.token_urlsafe(24)
    JETONS_MIGRATION[jeton] = maintenant + DUREE_MIGRATION
    return {"ok": True, "jeton": jeton}


@app.post("/auth/inscription")
async def auth_inscription(request: Request):
    """Créer un compte depuis l'entrée, puis ouvrir sa session.
    Aucun compte (installation neuve) : le premier compte, toujours Administrateur de l'installation (l'ancien code est
    demandé s'il en existait un). Création atomique : jamais deux premiers administrateurs.
    Des comptes existent : inscription publique, seulement si un administrateur l'a ouverte (Autoriser les inscriptions),
    et toujours Utilisateur : un rôle envoyé dans la requête est ignoré. Sinon 403 avec fermees."""
    if ETAT_BASE["erreur"]:
        return JSONResponse({"ok": False, "message": ETAT_BASE["erreur"]}, status_code=503)
    corps = await corps_json(request)
    if comptes.nombre_comptes():
        if not comptes.inscriptions_ouvertes():
            return JSONResponse({"ok": False, "fermees": True, "message": "Les inscriptions sont fermées. Demandez à un administrateur "
                                 "KamCiné de vous créer un compte."}, status_code=403)
        attente = attente_restante(request, "inscription")
        if attente:
            return JSONResponse({"ok": False, "message": "Trop d'essais, réessayez dans %d s." % attente}, status_code=429)
        noter_echec(request, "inscription")          # au plus 5 créations par minute depuis une même adresse
        try:
            compte = await run_in_threadpool(comptes.creer_compte, corps.get("nom"), corps.get("identifiant"),
                                             corps.get("mot_de_passe"), "utilisateur")
        except comptes.Refus as e:
            return JSONResponse({"ok": False, "message": str(e), "champ": e.champ}, status_code=409 if e.champ == "identifiant" and "pris" in str(e) else 400)
        return ouvrir_session_reponse(request, compte)
    if lire_auth() is not None:
        jeton = str(corps.get("jeton_migration", ""))
        if not jeton or JETONS_MIGRATION.get(jeton, 0) < time.time():
            return JSONResponse({"ok": False, "message": "Confirmez d'abord votre ancien code KamCiné.", "ancien_code": True}, status_code=403)
    try:
        compte = await run_in_threadpool(comptes.creer_compte, corps.get("nom"), corps.get("identifiant"),
                                         corps.get("mot_de_passe"), "admin", True)
    except comptes.Refus as e:
        # Sans champ : un autre appareil vient de créer le premier administrateur.
        return JSONResponse({"ok": False, "message": str(e), "champ": e.champ}, status_code=400 if e.champ else 409)
    JETONS_MIGRATION.clear()
    if not comptes.lire_etat("presentation_terminee"):
        comptes.ecrire_etat("presentation_terminee", time.time())
    await run_in_threadpool(rattacher_donnees_anciennes)
    compte = comptes.compte(compte["id"]) or compte
    return ouvrir_session_reponse(request, compte)


def ouvrir_session_reponse(request, compte):
    jeton, duree = comptes.ouvrir_session(compte["id"], int(charger()["verrou_duree"]), request.headers.get("user-agent", ""))
    rep = JSONResponse({"ok": True, "compte": compte})
    pose_cookie(rep, request, jeton, duree)
    return rep


@app.post("/auth/connexion")
async def auth_connexion(request: Request):
    if ETAT_BASE["erreur"]:
        return JSONResponse({"ok": False, "message": ETAT_BASE["erreur"]}, status_code=503)
    attente = attente_restante(request, "connexion")
    if attente:
        return JSONResponse({"ok": False, "message": "Trop d'essais, réessayez dans %d s." % attente}, status_code=429)
    corps = await corps_json(request)
    compte = await run_in_threadpool(comptes.authentifier, corps.get("identifiant"), corps.get("mot_de_passe"))
    if not compte:
        noter_echec(request, "connexion")
        return JSONResponse({"ok": False, "message": "Identifiant ou mot de passe incorrect."}, status_code=403)
    return ouvrir_session_reponse(request, compte)


@app.post("/auth/deconnexion")
def auth_deconnexion(request: Request):
    """Ferme la session de ce navigateur seulement : les autres téléphones restent connectés."""
    comptes.fermer_session(request.cookies.get(COOKIE))
    rep = JSONResponse({"ok": True})
    rep.delete_cookie(COOKIE, path="/")
    return rep


def refus_admin(request):
    """Seconde vérification dans chaque route d'administration, en plus du verrou (route_admin)."""
    compte = compte_courant(request)
    if not compte or not compte["admin"]:
        return JSONResponse({"ok": False, "message": "Réservé à un administrateur.", "admin": True}, status_code=403)
    return None


def reponse_refus(e, statut=400):
    return JSONResponse({"ok": False, "message": str(e), "champ": e.champ}, status_code=statut)


@app.get("/comptes")
def comptes_liste(request: Request):
    """Réglages, Comptes utilisateurs : la liste, pour un administrateur."""
    refus = refus_admin(request)
    if refus:
        return refus
    return {"ok": True, "comptes": comptes.lister_comptes(), "moi": compte_courant(request)["id"],
            "inscriptions": comptes.inscriptions_ouvertes()}


@app.post("/comptes/inscriptions")
async def comptes_inscriptions(request: Request):
    """Autoriser les inscriptions (2.6.105) : les personnes qui ont accès à cette installation créent leur compte Utilisateur."""
    refus = refus_admin(request)
    if refus:
        return refus
    corps = await corps_json(request)
    return {"ok": True, "inscriptions": comptes.regler_inscriptions(corps.get("ouvertes") is True)}


@app.post("/comptes")
async def comptes_creer(request: Request):
    """Un administrateur ajoute un compte (Utilisateur par défaut, ou Administrateur)."""
    refus = refus_admin(request)
    if refus:
        return refus
    corps = await corps_json(request)
    role = corps.get("role") or "utilisateur"
    if role not in comptes.ROLES:
        return JSONResponse({"ok": False, "message": "Rôle inconnu.", "champ": "role"}, status_code=400)
    try:
        cree = await run_in_threadpool(comptes.creer_compte, corps.get("nom"), corps.get("identifiant"), corps.get("mot_de_passe"), role)
    except comptes.Refus as e:
        return reponse_refus(e, 409 if e.champ == "identifiant" and "pris" in str(e) else 400)
    return {"ok": True, "compte": cree}


def id_compte(texte):
    try:
        return int(texte)
    except (TypeError, ValueError):
        return 0


@app.post("/comptes/{user_id}/role")
async def comptes_role(user_id: str, request: Request):
    refus = refus_admin(request)
    if refus:
        return refus
    corps = await corps_json(request)
    try:
        return {"ok": True, "compte": await run_in_threadpool(comptes.changer_role, id_compte(user_id), str(corps.get("role", "")))}
    except comptes.Refus as e:
        return reponse_refus(e, 409)


@app.post("/comptes/{user_id}/activation")
async def comptes_activation(user_id: str, request: Request):
    """Désactiver : plus de connexion, sessions révoquées sur tous ses appareils. Jamais le dernier administrateur actif."""
    refus = refus_admin(request)
    if refus:
        return refus
    corps = await corps_json(request)
    try:
        return {"ok": True, "compte": await run_in_threadpool(comptes.changer_activation, id_compte(user_id), corps.get("actif") is True)}
    except comptes.Refus as e:
        return reponse_refus(e, 409)


@app.post("/comptes/{user_id}/mot-de-passe")
async def comptes_reinitialiser(user_id: str, request: Request):
    """Un administrateur choisit un nouveau mot de passe pour un compte : ses appareils sont déconnectés."""
    refus = refus_admin(request)
    if refus:
        return refus
    corps = await corps_json(request)
    try:
        return {"ok": True, "compte": await run_in_threadpool(comptes.reinitialiser_mot_de_passe, id_compte(user_id), corps.get("mot_de_passe"))}
    except comptes.Refus as e:
        return reponse_refus(e)


@app.post("/comptes/{user_id}/permission")
async def comptes_permission(user_id: str, request: Request):
    """Accorde ou retire une permission d'Utilisateur. Sans effet visible pour un administrateur, qui les a toutes."""
    refus = refus_admin(request)
    if refus:
        return refus
    corps = await corps_json(request)
    try:
        return {"ok": True, "compte": await run_in_threadpool(comptes.changer_permission, id_compte(user_id),
                                                             str(corps.get("permission", "")), corps.get("accordee") is True)}
    except comptes.Refus as e:
        return reponse_refus(e)


@app.get("/comptes/{user_id}/avatar")
def comptes_avatar(user_id: str, request: Request):
    """La photo d'un compte : pour lui même ou pour un administrateur. 404 sans photo (l'interface montre l'initiale)."""
    moi, cible = compte_courant(request), id_compte(user_id)
    if not moi or (moi["id"] != cible and not moi["admin"]):
        return Response(status_code=404)
    chemin = comptes.chemin_avatar(cible)
    if not chemin:
        return Response(status_code=404)
    return FileResponse(chemin, media_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=604800", "X-Content-Type-Options": "nosniff"})


# ---------- Mon profil (chaque compte, 2.6.103) ----------
@app.post("/moi/bienvenue")
def moi_bienvenue(request: Request):
    """La courte bienvenue de première connexion a été vue : elle ne revient plus pour ce compte (2.6.104)."""
    return {"ok": True, "compte": comptes.bienvenue_vue(compte_courant(request)["id"])}


@app.post("/moi/appletv")
async def moi_appletv(request: Request):
    """Préférence de lecteur par compte ; la configuration physique des Apple TV reste globale."""
    uid = id_connecte(request)
    if not uid:
        return JSONResponse({"ok": False, "message": "Connecte-toi pour enregistrer ce choix."}, status_code=401)
    c = await corps_json(request)
    identifiant = str(c.get("identifiant") or "")
    if not any(a.get("identifiant") == identifiant for a in integrations.appletvs()):
        return JSONResponse({"ok": False, "message": "Cette Apple TV n’est pas configurée."}, status_code=400)
    comptes.ecrire_preference(uid, "appletv", identifiant)
    return {"ok": True, "identifiant": identifiant}


def id_connecte(request):
    """Le compte de la requête, None sans requête (appel interne ou test) : l'action n'est alors attribuée à personne."""
    compte = compte_courant(request) if request is not None else None
    return compte["id"] if compte else None


def noter_activite(request, genre, media_type, tmdb_id, details=""):
    """Auteur d'une action personnelle (demande, séance). Une panne ici ne fait jamais échouer l'action elle même."""
    try:
        comptes.noter_activite(id_connecte(request), genre, media_type, tmdb_id, details)
        invalider_demandes_recentes()
    except Exception as e:
        erreurs.message(e, "Activité du compte non enregistrée.")

@app.post("/moi/profil")
async def moi_profil(request: Request):
    corps = await corps_json(request)
    try:
        return {"ok": True, "compte": await run_in_threadpool(comptes.modifier_nom, compte_courant(request)["id"], corps.get("nom"))}
    except comptes.Refus as e:
        return reponse_refus(e)


@app.post("/moi/avatar")
async def moi_avatar(request: Request):
    """Photo du compte connecté : JPEG recadré par le navigateur, jamais un chemin ni un nom de fichier fourni par lui."""
    longueur = request.headers.get("content-length", "")
    if longueur.isdigit() and int(longueur) > comptes.AVATAR_MAX:
        return JSONResponse({"ok": False, "message": "Image trop lourde (2 Mo au plus)."}, status_code=413)
    data = await request.body()
    try:
        return {"ok": True, "compte": await run_in_threadpool(comptes.definir_avatar, compte_courant(request)["id"], data)}
    except comptes.Refus as e:
        return reponse_refus(e)


@app.post("/moi/avatar/retirer")
def moi_avatar_retirer(request: Request):
    return {"ok": True, "compte": comptes.retirer_avatar(compte_courant(request)["id"])}


@app.post("/moi/mot-de-passe")
async def moi_mot_de_passe(request: Request):
    """Changer son mot de passe : l'actuel est exigé ; cet appareil reste connecté, les autres sont déconnectés."""
    attente = attente_restante(request, "mot_de_passe")
    if attente:
        return JSONResponse({"ok": False, "message": "Trop d'essais, réessayez dans %d s." % attente}, status_code=429)
    corps = await corps_json(request)
    try:
        await run_in_threadpool(comptes.changer_mot_de_passe, compte_courant(request)["id"], corps.get("actuel"),
                                corps.get("nouveau"), request.cookies.get(COOKIE))
    except comptes.Refus as e:
        if e.champ == "actuel":
            noter_echec(request, "mot_de_passe")
        return reponse_refus(e, 403 if e.champ == "actuel" else 400)
    return {"ok": True}


# ---------- Pages, icône, profil ----------
@app.get("/home", response_class=HTMLResponse)
@app.get("/catalogue", response_class=HTMLResponse)
@app.get("/downloads", response_class=HTMLResponse)
@app.get("/devices", response_class=HTMLResponse)
@app.get("/profile", response_class=HTMLResponse)
@app.get("/settings", response_class=HTMLResponse)
@app.get("/alerts", response_class=HTMLResponse)
@app.get("/logs", response_class=HTMLResponse)
@app.get("/suggestions", response_class=HTMLResponse)
@app.get("/films/{media_id:int}", response_class=HTMLResponse)
@app.get("/series/{media_id:int}", response_class=HTMLResponse)
@app.get("/people/{media_id:int}", response_class=HTMLResponse)
@app.get("/", response_class=HTMLResponse)
def accueil():
    with open(os.path.join(DOSSIER, "index.html"), encoding="utf-8") as f:
        return HTMLResponse(f.read().replace('content="__KAMCINE_TIMEZONE__"', 'content="' + html.escape(planning.TIMEZONE_NAME, quote=True) + '"'), headers={"Cache-Control": "no-store"})


@app.get("/icon.png")
def icone(style: str = ""):
    if style not in ICONES:
        style = charger()["icone"]
    for nom in [ICONES.get(style, "")] + REPLI:
        chemin = os.path.join(DOSSIER, nom)
        if nom and os.path.exists(chemin):
            return FileResponse(chemin, media_type="image/png", headers={"Cache-Control": "no-store"})
    return JSONResponse({"ok": False, "message": "Aucune icône trouvée"}, status_code=404)


@app.get("/splash.jpg")
def splash():
    """Logo de l'écran de démarrage (2.6.102) : version légère du logo sombre, gardée en cache par le téléphone."""
    return FileResponse(os.path.join(DOSSIER, "splash.jpg"), media_type="image/jpeg", headers={"Cache-Control": "no-cache"})


@app.get("/manifest.json")
def manifeste():
    return FileResponse(os.path.join(DOSSIER, "manifest.webmanifest"), media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    return FileResponse(os.path.join(DOSSIER, "sw.js"), media_type="application/javascript",
                        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


"""Logos tiers optionnels : ils ne sont pas embarqués dans l'image publique.

Une installation peut déposer localement les assets autorisés dans /config/logos.
Le logo TMDB reste le seul asset inclus dans le code distribué.
"""
LOGOS_OK = {
    "tmdb.svg": "image/svg+xml", "imdb.svg": "image/svg+xml",
    "metacritic.svg": "image/svg+xml", "rt_fresh.svg": "image/svg+xml",
    "rt_rotten.svg": "image/svg+xml", "rt_public_fresh.svg": "image/svg+xml",
    "rt_public_rotten.svg": "image/svg+xml", "overseerr.svg": "image/svg+xml",
    "radarr.svg": "image/svg+xml", "sonarr.svg": "image/svg+xml",
    "infuse.png": "image/png", "sofatime.png": "image/png",
}


@app.get("/logos/{nom}")
def logo(nom: str):
    """Asset local optionnel sous /config/logos, puis asset public intégré si disponible.

    La liste blanche empêche le parcours de chemins. Les marques tierces optionnelles
    restent hors dépôt/image publique et peuvent être fournies par l'installation.
    """
    if nom not in LOGOS_OK:
        return Response(status_code=404)
    prive = os.path.join(BASE, "logos", nom)
    public = os.path.join(DOSSIER, "logos", nom)
    chemin = prive if os.path.isfile(prive) else public
    if not os.path.isfile(chemin):
        return Response(status_code=404)
    cache = "private, max-age=300" if chemin == prive else "public, max-age=86400"
    return FileResponse(chemin, media_type=LOGOS_OK[nom], headers={"Cache-Control": cache})


# ---------- Réglages ----------
@app.get("/reglages")
def get_reglages():
    return charger()


@app.get("/reglages/defauts")
def get_reglages_defauts():
    """Les valeurs d'origine de chaque réglage, pour le retour à la valeur par défaut d'un seul réglage (Réglages, bouton Par défaut)."""
    return {k: v for k, v in DEFAUTS.items() if k in SPEC}


@app.post("/reglages")
async def set_reglages(request: Request):
    try:
        corps = await request.json()
        assert isinstance(corps, dict)
    except Exception:
        return JSONResponse({"ok": False, "message": "Requête invalide"}, status_code=400)
    r = charger()
    for k, v in corps.items():
        if k not in SPEC:
            continue
        try:
            r[k] = nettoyer(k, v)
        except Exception:
            if k.startswith("url_publique_"):
                return JSONResponse({"ok": False, "message": "Utilise une adresse HTTPS publique avec un nom de domaine, sans identifiant ni paramètres."}, status_code=400)
            return JSONResponse({"ok": False, "message": "Valeur invalide : " + k}, status_code=400)
    url = r.get("overseerr_url", "")
    if url and not re.match(r"^https?://\S+$", url):
        return JSONResponse({"ok": False, "message": "L'adresse doit commencer par http:// ou https://"}, status_code=400)
    r["overseerr_url"] = url.rstrip("/")
    if r["entracte_max"] < r["entracte_min"]:
        r["entracte_max"] = r["entracte_min"]
    if r["entracte_fin"] < r["entracte_debut"]:
        r["entracte_fin"] = r["entracte_debut"]
    sauver(r)
    return r


@app.post("/reglages/reset")
def reset_reglages():
    from config import DEFAUTS
    actuel = charger()
    neuf = dict(DEFAUTS)
    neuf["nom"] = actuel["nom"]
    neuf["icone"] = actuel["icone"]
    sauver(neuf)
    return neuf


# ---------- Séance ----------
def ligne(texte):
    if texte in LIGNES_FIN:
        terminer_seance_normalement()
    etat["lignes"].append((time.time(), texte))
    save_session_scenario()
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(texte + "\n")
    except Exception:
        pass


class Hote:
    def log(self, texte):
        ligne(texte)

    def atv(self, *args, delai=25):
        return atv(*args, delai=delai)

    def appletv_id(self):
        return etat.get("appletv_id") or atvlive.LIVE.target_id or (integrations.appletv() or {}).get("identifiant")

    def playing(self):
        return lire_playing(frais=True)

    def playing_frais(self):
        l = atvlive.LIVE.frais()
        return l if l is not None else lire_playing(frais=True)

    def lumiere(self, nom, d):
        _essayer(lambda: getattr(__import__("lumieres").Hue(), nom)(d))

    def charger(self):
        return charger()

    def cible_generique(self, meta, total, cfg, test, pos, serie=False):
        """Seuil du générique du média en cours (generique.seuil, lot 2.6.89) et sa source, consignés dans le journal de la chaîne."""
        if test:
            return pos + 45, "test"
        debut = None
        if cfg["generique_source"] == "auto" and meta:
            try:
                debut = generique.credits_debut(meta["type"], meta["id"], meta.get("saison"), meta.get("episode"), total)
            except Exception:
                debut = None
        cible, source = generique.seuil(total, serie, debut, cfg)
        self.journal_generique("seuil calculé", pos, source=source + (" (TheIntroDB)" if debut else ""),
                               timecode=int(debut) if debut else None, duree=total, seuil=cible,
                               media=_media_court(meta))
        return cible, "TheIntroDB" if debut else source

    def journal_generique(self, evenement, pos, **donnees):
        journal_chaine("generique", dict(donnees, evenement=evenement, position=pos))

    def episode_suivant(self, meta, p):
        return seance_episode_suivant(meta, p)

    def marquer_vu(self, meta):
        if meta:
            try:
                suivi.marquer_vu({"type": meta["type"], "id": meta["id"], "saison": meta.get("saison"), "episode": meta.get("episode"),
                                  "titre": meta["titre"], "affiche": meta.get("affiche"), "fond": meta.get("fond"),
                                  "duree": meta.get("duree") or 0})
            except Exception:
                pass


relais = relais.Relais(Hote())


# ---------- Lancement en cours : ce que la home affiche tout de suite ----------
# On sait ce qu'on vient de lancer (titre, jaquette) avant que l'Apple TV ou Infuse répondent. L'état vit ici, dans le service,
# pour rester cohérent entre les appareils. La preuve vient du média observé ou du marqueur explicite du script.
MEMOIRE_SEANCE = os.path.join(BASE, "seance_memorisee.json")
VERROU_MEMOIRE = threading.RLock()


def lire_memoire_seance():
    try:
        with open(MEMOIRE_SEANCE, encoding="utf-8") as f:
            d = json.load(f)
        if (isinstance(d, dict) and d.get("confirme") and d.get("type") in ("movie", "tv")
                and isinstance(d.get("id"), int) and d["id"] > 0 and isinstance(d.get("meta"), dict)
                and isinstance(d.get("t"), (int, float)) and d.get("origine") in ("lire", "seance", "reprise")):
            return dict(d, etat="ok", sauvegarde=0, phase="", message="",
                        suspendue=bool(d.get("interruption_confirmee")),
                        saison=d.get("saison"), episode=d.get("episode"))
    except (OSError, ValueError, TypeError):
        pass
    return None


LANCEMENT = {"d": lire_memoire_seance()}


# ---------- Séances en suspens (lot 2.6.91) ----------
# Une séance active au plus (LANCEMENT), plusieurs séances KamCiné en suspens. Seule une vraie séance (lancée par KamCiné ou
# pilotée, origine seance ou reprise, confirmée) peut être mise en suspens : une lecture Infuse manuelle ne l'est jamais.
# Une nouvelle séance met l'ancienne en suspens au lieu de l'effacer ; MAX_SUSPENDUES n'est jamais dépassé en silence par une
# action de l'utilisateur (réponse suspens_plein, il choisit laquelle quitter). Persistées dans seances_suspendues.json.
SUSPENS_FICHIER = os.path.join(BASE, "seances_suspendues.json")
MAX_SUSPENDUES = 3


def est_seance(d):
    return bool(d and d.get("confirme") and d.get("origine") in ("seance", "reprise"))


def cle_media(d):
    if not d:
        return None
    return (d.get("type"), d.get("id")) + ((d.get("saison"), d.get("episode")) if d.get("type") == "tv" else ())


def charger_suspendues():
    try:
        with open(SUSPENS_FICHIER, encoding="utf-8") as f:
            brut = json.load(f)
    except (OSError, ValueError):
        return []
    out = []
    for d in brut if isinstance(brut, list) else []:
        if (isinstance(d, dict) and d.get("confirme") and d.get("type") in ("movie", "tv") and isinstance(d.get("id"), int)
                and isinstance(d.get("meta"), dict) and isinstance(d.get("t"), (int, float)) and d.get("origine") in ("seance", "reprise")):
            out.append(dict(d, etat="ok", phase="", message="", suspendue=True))
    return out


SUSPENDUES = {"l": charger_suspendues()}


def sauver_suspendues():
    try:
        ecrire_prive(SUSPENS_FICHIER + ".tmp", [dict(x) for x in SUSPENDUES["l"]])
        os.replace(SUSPENS_FICHIER + ".tmp", SUSPENS_FICHIER)
    except OSError:
        pass


def resume_suspendues():
    return ["%s %s%s" % (x.get("type"), x.get("id"), " S%sE%s" % (x.get("saison"), x.get("episode")) if x.get("type") == "tv" else "")
            for x in SUSPENDUES["l"]]


def suspendre_seance(d, raison):
    """Met une vraie séance en suspens (une seule entrée par média), sans rien effacer."""
    if not est_seance(d):
        return False
    with VERROU_MEMOIRE:
        copie = {k: v for k, v in d.items() if k not in ("reprise_demandee", "lumieres_reprise", "sauvegarde")}
        copie.update(suspendue=True, interruption_confirmee=True, suspendue_a=time.time())
        SUSPENDUES["l"] = [x for x in SUSPENDUES["l"] if cle_media(x) != cle_media(d)] + [copie]
        sauver_suspendues()
    journal_chaine("seance", {"evenement": "séance mise en suspens", "raison": raison, "seance": resume_seance(),
                              "suspendues": resume_suspendues()})
    return True


def retirer_suspendue(t=None, cle=None):
    with VERROU_MEMOIRE:
        avant = len(SUSPENDUES["l"])
        SUSPENDUES["l"] = [x for x in SUSPENDUES["l"] if not ((t is not None and x.get("t") == t) or (cle is not None and cle_media(x) == cle))]
        if len(SUSPENDUES["l"]) != avant:
            sauver_suspendues()
            return True
    return False


def suspens_plein(type_=None, id_=None, saison=None, episode=None):
    """Vrai si lancer ce média mettrait la séance active en suspens alors que MAX_SUSPENDUES sont déjà en suspens."""
    d = LANCEMENT.get("d")
    if not est_seance(d):
        return False
    nouveau = (type_, id_) + ((saison or None, episode or None) if type_ == "tv" else ())
    if type_ is not None and cle_media(d) == nouveau:
        return False
    restantes = [x for x in SUSPENDUES["l"] if cle_media(x) != nouveau]
    return len(restantes) >= MAX_SUSPENDUES


def reponse_suspens_plein():
    journal_chaine("seance", {"evenement": "limite des séances en suspens", "suspendues": resume_suspendues()})
    return JSONResponse({"ok": False, "code": "suspens_plein",
                         "message": "%d séances sont déjà en suspens : choisis celle à quitter." % MAX_SUSPENDUES,
                         "suspendues": [seance_conservee(x) for x in SUSPENDUES["l"]]}, status_code=409)


# Lot 2.6.92 : transition de remplacement. KamCiné vient de mettre une séance en suspens pour lancer autre chose ; tant que son
# média n'a pas cessé d'être lu (ou que le nouveau n'est pas confirmé), une observation résiduelle de l'ancien média n'est pas un
# retour de l'utilisateur : elle ne doit pas relancer le rattachement automatique de CETTE séance. Un état explicite, pas un délai.
TRANSITION = {"cle": None, "t": 0}


def ouvrir_transition(d, raison):
    TRANSITION.update(cle=cle_media(d), t=time.time())
    journal_chaine("transition", {"evenement": "transition de remplacement ouverte", "raison": raison, "suspendue": cle_media(d)})


def fermer_transition(raison):
    if TRANSITION["cle"] is not None:
        journal_chaine("transition", {"evenement": "transition de remplacement terminée", "raison": raison,
                                      "suspendue": TRANSITION["cle"], "duree_s": round(time.time() - TRANSITION["t"], 1)})
        TRANSITION.update(cle=None, t=0)


def ecarter_lancement(nouveau_cle, raison):
    """Libère LANCEMENT pour un nouveau départ : une vraie séance d'un autre média part en suspens, le reste est oublié."""
    d = LANCEMENT.get("d")
    if est_seance(d) and cle_media(d) != nouveau_cle:
        suspendre_seance(d, raison)
        ouvrir_transition(d, raison)
        with VERROU_MEMOIRE:
            LANCEMENT["d"] = None
            try:
                ecrire_prive(MEMOIRE_SEANCE + ".tmp", None)
                os.replace(MEMOIRE_SEANCE + ".tmp", MEMOIRE_SEANCE)
            except OSError:
                pass
    else:
        oublier_lancement(raison=raison)


def restaurer_suspendue(s, raison):
    """La séance en suspens s redevient la séance active ; l'active actuelle, si c'est une autre vraie séance, part en suspens."""
    if TRANSITION["cle"] == cle_media(s):
        fermer_transition("séance reprise explicitement")
    ecarter_lancement(cle_media(s), raison)
    retirer_suspendue(t=s.get("t"))
    d = dict(s, etat="ok", suspendue=False, interruption_confirmee=False, incertaine=False, sauvegarde=0)
    d.pop("suspendue_a", None)
    LANCEMENT["d"] = d
    OBSERVATION_FIN["d"] = None
    memoriser_lancement(d, force=True)
    journal_chaine("seance", {"evenement": "séance en suspens reprise", "raison": raison, "seance": resume_seance(),
                              "suspendues": resume_suspendues()})
    return d
OBSERVATION_FIN = {"d": None, "suivi": None, "lu_a": None, "resultat": "inconnu"}
MSG_INFUSE = "La lecture du titre demandé n’a pas encore été confirmée dans Infuse."
# Un média tout juste disponible peut ne pas être encore indexé par Infuse : la fiche ouvre alors sur une recherche
# plutôt que sur la lecture. On retente plusieurs fois avant de conclure à un échec (voir lancer() et MSG_INDEXATION).
# seance.py a sa propre constante du même nom pour le lancement piloté par le script (demarrer_contenu) ; les deux
# nombres n'ont pas besoin d'être égaux, mais changer l'un sans l'autre laisse une incohérence de patience.
NB_ESSAIS_INFUSE = 4
MSG_INDEXATION = "Toujours pas confirmé : si le média vient d’être ajouté, Infuse peut avoir besoin d’un instant pour l’indexer."


def memoriser_lancement(d, force=False):
    with VERROU_MEMOIRE:
        if LANCEMENT["d"] is not d or not d.get("confirme"):
            return
        if not force and time.time() - d.get("sauvegarde", 0) < 5:
            return
        try:
            ecrire_prive(MEMOIRE_SEANCE + ".tmp", dict(d))
            os.replace(MEMOIRE_SEANCE + ".tmp", MEMOIRE_SEANCE)
            d["sauvegarde"] = time.time()
        except OSError:
            pass


def oublier_lancement(attendu=None, raison="non précisée"):
    with VERROU_MEMOIRE:
        if attendu is not None and LANCEMENT["d"] is not attendu:
            return
        if LANCEMENT["d"]:
            journal_chaine("seance", {"evenement": "séance effacée", "raison": raison, "seance": resume_seance()})
        LANCEMENT["d"] = None
        try:
            ecrire_prive(MEMOIRE_SEANCE + ".tmp", None)
            os.replace(MEMOIRE_SEANCE + ".tmp", MEMOIRE_SEANCE)
        except OSError:
            pass


def confirmer_lancement(d, p=None):
    premiere = not d.get("confirme")
    if TRANSITION["cle"] is not None and cle_media(d) != TRANSITION["cle"]:
        fermer_transition("nouveau média confirmé")
    if premiere:
        journal_chaine("seance", {"evenement": "séance confirmée", "seance": resume_seance(),
                                  "par": "lecture observée : %s" % (p.get("titre") or "") if p else "identité connue au lancement"})
    d.update(etat="ok", confirme=True, message="")
    transition = bool(p and p.get("etat") != d.get("dernier_etat"))
    if p:
        d.update(derniere_lecture=p.get("lu_a", time.time()), position=p.get("pos", d.get("position", 0)),
                 total=p.get("total") or d.get("total", 0), dernier_etat=p.get("etat"),
                 suspendue=False, incertaine=False, interruption_confirmee=False)
        if p.get("total"):
            # Lot 2.6.84 : durée du fichier de la séance, preuve du « même fichier » quand Infuse ne publie plus l'épisode.
            d["total_fichier"] = p["total"]
    if premiere and d.get("origine") != "lire":
        notifications.signaler("seance:demarree:" + str(d["t"]), "seance_demarree", "Séance démarrée",
                              (d.get("meta") or {}).get("titre") or "Bonne séance", {"page": "seance"})
    memoriser_lancement(d, force=premiere or transition or bool(p and d.get("total", 0) > 0 and d.get("position", 0) >= d["total"] - 20))


def lancement_definir(type_, id_, saison, episode, origine, phase="", meta=None):
    d = {"type": type_, "id": id_, "saison": saison or None, "episode": episode or None, "origine": origine,
         "etat": "ok" if meta else "en_cours", "phase": phase, "message": "", "t": time.time(), "meta": meta}
    nouveau = (type_, id_) + ((saison or None, episode or None) if type_ == "tv" else ())
    reprise = next((x for x in SUSPENDUES["l"] if cle_media(x) == nouveau), None) if origine == "lire" else None
    if reprise:
        # Lire un média qui a une séance en suspens reprend cette séance (même jeton, même scénario), sans en créer une autre.
        return restaurer_suspendue(reprise, "lecture du média d'une séance en suspens")
    ecarter_lancement(nouveau, "nouveau lancement %s %s (%s)" % (type_, id_, origine))
    retirer_suspendue(cle=nouveau)    # le même média relancé redevient la séance active, jamais aussi en suspens
    LANCEMENT["d"] = d
    journal_chaine("seance", {"evenement": "séance créée", "seance": resume_seance(), "avec_identite": bool(meta)})
    if meta:
        confirmer_lancement(d)
    if not meta:
        def enrichir():
            m = meta_par_id(type_, id_, saison or None, episode or None)
            if LANCEMENT["d"] is d:
                d["meta"] = m
        threading.Thread(target=enrichir, daemon=True).start()
    return d


def _lancement_echec(d, message, cause=None):
    """Expose un message court et consigne une seule fois le contexte technique de l'échec."""
    if d.get("confirme"):
        return
    d.update(etat="echec", message=message)
    if d.get("echec_journalise"):
        return
    meta = d.get("meta") or {}
    titre = meta.get("titre") or "%s %s" % (d.get("type") or "contenu", d.get("id") or "")
    if d.get("origine") != "lire":
        # "lire" est un clic direct sur "Lire" depuis la fiche : l'échec est déjà visible tout de suite dans
        # l'interface, sans intérêt à notifier. "seance" et "reprise" peuvent se confirmer bien plus tard
        # (bandes annonces, retour au salon), une notification a du sens si l'échec arrive après ce moment là.
        notifications.signaler("seance:echec:" + str(d["t"]), "seance_echec", "Échec du lancement", titre, {"page": "seance"})
    lignes = [l for _, l in lignes_courantes()[-12:]]
    detail = [
        "Échec du lancement de %r." % titre,
        "Type=%s, TMDB=%s, saison=%s, épisode=%s, origine=%s." % (
            d.get("type"), d.get("id"), d.get("saison"), d.get("episode"), d.get("origine")),
        "Cause: %s" % (cause or message),
    ]
    if lignes:
        detail.append("Dernières lignes du journal de séance:\n" + "\n".join(lignes))
    p = lire_playing() or {}
    detail.append("Observation Apple TV : état=%s, app=%s, titre=%r, position=%s, durée=%s, lecture=%s." % (
        p.get("etat"), p.get("app"), p.get("titre"), p.get("pos"), p.get("total"), p.get("lu_a")))
    erreurs.message("\n".join(detail), "Échec du lancement de séance.")
    erreurs.journaliser_action("KamCiné", "Échec du lancement de %r. %s" % (titre, cause or message), "erreur")
    d["echec_journalise"] = True


def _resoudre_lancement(d):
    """Le journal ne confirme que le média attendu, jamais une durée isolée."""
    lignes = [(t, l) for t, l in lignes_courantes() if t is not None and t >= d["t"]]
    preuve = "Lecture confirmée : %s:%s:%s:%s" % (d["type"], d["id"], d.get("saison") or 0, d.get("episode") or 0)
    if any(l == preuve for _, l in lignes):
        confirmer_lancement(d)
        return
    for t, l in lignes:
        if l.startswith("Lancement du film via Infuse"):
            d.setdefault("attente_depuis", t or time.time())
        if l.startswith(("Infuse n'a pas démarré la lecture", "Erreur au départ")):
            _lancement_echec(d, MSG_INFUSE, l)
            return
    if not en_cours():
        _lancement_echec(d, "La séance s'est arrêtée avant confirmation de la lecture.")
        return
    if not d.get("attente_depuis"):
        return
    attente = time.time() - d["attente_depuis"]
    if attente > charger()["lancement_confirmation_s"]:
        _lancement_echec(d, "La lecture n'a pas été confirmée dans le délai réglé.")
    elif attente > charger()["film_attente_s"]:
        # seance.py retente de son côté (NB_ESSAIS_INFUSE, demarrer_contenu) : ce n'est pas encore un échec.
        d["phase"] = MSG_INDEXATION


def _film_toujours_en_lecture(d=None, p=None):
    p = lire_playing() if p is None else p
    if not p or not d or not lecture_infuse.correspond(p, d):
        return False
    lu = p.get("lu_a", 0)
    return lu >= d.get("lecture_apres", d["t"]) and time.time() - lu <= charger()["atv_etat_max_age_s"]


def observer_fin_lancement(d, p):
    """Même décision que les contrôleurs, restaurée depuis la dernière position persistée."""
    if d.get("total", 0) > 0 and d.get("position", 0) >= d["total"]:
        return "terminee"
    o = OBSERVATION_FIN
    if o["d"] is not d:
        suivi_fin = etat_seance.FinFilm(d)
        suivi_fin.position, suivi_fin.total = d.get("position", 0), d.get("total", 0)
        o.update(d=d, suivi=suivi_fin, lu_a=None, resultat="inconnu")
    if (not p or p.get("lu_a", 0) < d.get("lecture_apres", d["t"])
            or time.time() - p.get("lu_a", 0) > charger()["atv_etat_max_age_s"]):
        return "inconnu"
    if p.get("lu_a") != o["lu_a"]:
        o.update(lu_a=p.get("lu_a"), resultat=o["suivi"].observer(p))
    return o["resultat"]


def save_session_scenario():
    """Mémorise l'avancée du scénario (entracte, générique, lignes utiles), indépendamment de la lecture observée."""
    d = LANCEMENT.get("d")
    if not d or not d.get("confirme") or d.get("origine") not in ("seance", "reprise"):
        return
    detail = analyser(lignes_courantes(), d.get("mode", etat["mode"]))
    e = detail["entracte"]
    scenario = {"threshold": e["prevu_s"], "intermission": e["etat"],
                "credits": detail["generique"], "options": etat.get("options"),
                "lines": [(t, line) for t, line in lignes_courantes() if line.startswith((
                    "Réveil", "Ambiance", "Lancement du film", "Lecture confirmée", "Trailer ",
                    "Retour au film", "Démarrage du film", "Séance reprise", "Film :", "Série :", "Épisode", "Retour avant",
                    "Entracte prévue", "Avance manuelle", "Entracte réarmée", "ENTRACTE :",
                    "Reprise du film", "Générique", "Minutage du générique"))]}
    if not d.get("total_fichier"):
        # Les contrôleurs écrivent la durée du fichier dans leur minutage (« ... vers Ns sur Ts ») : elle suffit à reconnaître
        # plus tard le même fichier, même si Infuse ne publie alors que le nom de la série.
        for _, l in reversed(lignes_courantes()):
            m = re.search(r"vers \d+s sur (\d+)s", l) or re.search(r"\| position \d+s sur (\d+)s", l)
            if m and int(m.group(1)) > 0:
                d["total_fichier"] = int(m.group(1))
                memoriser_lancement(d, force=True)
                break
    if d.get("scenario") != scenario:
        d["scenario"] = scenario
        # La 2.6.74 notait une entracte sautée comme faite : le journal fait foi pour corriger une ancienne mémoire.
        d["entracte_faite"] = e["etat"] in ("actif", "fait")
        memoriser_lancement(d, force=True)


def reattach_session(d, p):
    """Reprend l'automatisation du média possédé, sans le relancer ni le déplacer."""
    # Sans attente : un départ ou un arrêt en cours tient le verrou, le prochain sondage réessaiera.
    if not VERROU_DEPART.acquire(blocking=False):
        return
    try:
        if LANCEMENT.get("d") is not d or en_cours() or d.get("origine") not in ("seance", "reprise"):
            return
        if not d.get("confirme") or not _film_toujours_en_lecture(d, p):
            return
        if not d.get("scenario") and lignes_courantes():
            save_session_scenario()
        scenario = d.get("scenario") or {}
        etat.update(mode=d.get("mode", "reel"), type="serie" if d["type"] == "tv" else "film",
                    debut=d["t"], preparation=False)
        if scenario.get("lines"):
            etat["lignes"] = [tuple(x) for x in scenario["lines"]]
        options = scenario.get("options") or options_seance(False, lire_option((d.get("relance") or {}).get("entracte")), d["type"] == "tv")
        etat["options"] = options
        meta_relais = dict(d.get("meta") or {}, total_fichier=d.get("total_fichier")) if d.get("meta") else d.get("meta")
        ctx = {"test": etat["mode"] != "reel", "meta": meta_relais, "type": etat["type"],
               "silencieux": True, "entracte_actif": options["entracte"],
               "entracte_prevu": scenario.get("threshold"),
               "entracte_deja": d.get("entracte_faite", False),
               "intermission_skipped": scenario.get("intermission") == "annulee",
               "credits_done": (scenario.get("credits") or {}).get("etat") == "fait"}
        if ctx["entracte_deja"]:
            ctx["entracte_prevu"] = None
        ctx["lumieres_reprise"] = bool(d.pop("lumieres_reprise", False))
        d.pop("reprise_demandee", None)
        if relais.demarrer(ctx):
            erreurs.journaliser_action("KamCiné", "Lecture rattachée à la séance mémorisée.", "info")
    finally:
        VERROU_DEPART.release()


def seance_episode_suivant(meta, p):
    """Lot 2.6.89 : Infuse enchaîne l'épisode suivant de la série de la séance. La même séance le suit : nouvel épisode, durée du
    nouveau fichier, scénario de l'épisode repris de zéro (préparation gardée). Seulement vers l'avant, jamais un épisode précédent.
    Renvoie la fiche du nouvel épisode (avec total_fichier), ou None si l'observation ne prouve pas encore quel épisode joue."""
    if not meta or meta.get("type") != "tv" or not p or not p.get("total"):
        return None
    ident = p.get("identite") or {}
    if ident.get("type") == "tv" and ident.get("id") == meta.get("id") and ident.get("saison") and ident.get("episode"):
        saison, episode = int(ident["saison"]), int(ident["episode"])
    else:
        info = lecture_infuse.identite_observee(p)
        if not info["episode_certain"] or not lecture_infuse.meme_serie_autre_episode(p, meta):
            return None
        saison, episode = int(info["saison"]), int(info["episode"])
    if (saison, episode) <= (int(meta.get("saison") or 0), int(meta.get("episode") or 0)):
        return None
    m = meta_par_id("tv", meta["id"], saison, episode)
    if not m:
        return None
    with VERROU_MEMOIRE:
        d = LANCEMENT.get("d")
        if d and d.get("type") == "tv" and d.get("id") == meta.get("id") and d.get("origine") in ("seance", "reprise"):
            avant = "S%sE%s" % (d.get("saison"), d.get("episode"))
            d.update(saison=saison, episode=episode, meta=m, total_fichier=p["total"], total=p["total"], position=p.get("pos") or 0,
                     derniere_lecture=p.get("lu_a", time.time()), dernier_etat=p.get("etat"), entracte_faite=False, scenario=None,
                     suspendue=False, incertaine=False, interruption_confirmee=False)
            OBSERVATION_FIN["d"] = None
            journal_chaine("seance", {"evenement": "épisode suivant", "avant": avant, "apres": "S%dE%d" % (saison, episode),
                                      "duree": p["total"], "seance": resume_seance()})
    # Le scénario affiché repart de zéro pour ce nouvel épisode : seules les lignes de préparation restent.
    etat["lignes"] = [x for x in etat["lignes"] if x[1].startswith(("Réveil", "Ambiance", "Série :", "Séance reprise"))]
    ligne("Épisode suivant : %s, saison %d, épisode %d%s" % (m.get("titre") or "", saison, episode,
                                                          (" : " + m["ep_titre"]) if m.get("ep_titre") else ""))
    d = LANCEMENT.get("d")
    if d:
        memoriser_lancement(d, force=True)
    return dict(m, total_fichier=p["total"])


# ---------- Journal du rattachement (lot 2.6.84) ----------
# Pour chaque relevé Infuse pendant une séance possédée : ce que publie l'Apple TV, l'identité résolue, la séance et la décision
# de correspond avec sa raison. Une ligne seulement quand quelque chose change. Aucun secret : titres et numéros seulement.
RATTACHEMENT_LOG = os.path.join(BASE, "rattachement.log")
RATTACHEMENTS = {"sig": None, "lignes": deque(maxlen=60)}


def journaliser_rattachement(d, p, joue):
    if not p or p.get("app") not in (None, "", INFUSE_ID) or p.get("etat") not in ("Playing", "Paused"):
        return
    accepte, raison = lecture_infuse.explique_correspondance(p, dict(d, total_fichier=d.get("total_fichier")))
    info = lecture_infuse.identite_observee(p)
    entree = {"app": p.get("app"), "titre": p.get("titre"), "serie": p.get("serie_nom"), "saison": p.get("saison_n"),
              "episode": p.get("episode_n"), "duree": p.get("total"), "type_observe": info["type"],
              "identite": p.get("identite"), "seance": {"type": d.get("type"), "id": d.get("id"), "saison": d.get("saison"),
              "episode": d.get("episode"), "duree_fichier": d.get("total_fichier"), "confirmee": bool(d.get("confirme"))},
              "rattache": bool(joue), "raison": raison if d.get("confirme") else "séance pas encore confirmée : " + raison,
              "complement": p.get("complement"), "etape": "rattachement"}
    sig = json.dumps(dict(entree, duree=(p.get("total") or 0) // 5), sort_keys=True, ensure_ascii=False)
    if sig == RATTACHEMENTS["sig"]:
        return
    RATTACHEMENTS["sig"] = sig
    entree = dict(entree, t=time.strftime("%Y-%m-%d %H:%M:%S"), position=p.get("pos"))
    RATTACHEMENTS["lignes"].append(entree)
    try:
        if os.path.exists(RATTACHEMENT_LOG) and os.path.getsize(RATTACHEMENT_LOG) > 256 * 1024:
            os.replace(RATTACHEMENT_LOG, RATTACHEMENT_LOG + ".1")
        with open(RATTACHEMENT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entree, ensure_ascii=False) + "\n")
    except OSError:
        pass


@app.get("/appletv/rattachement")
def rattachement_diagnostic():
    """Les derniers relevés du journal de rattachement, pour diagnostiquer une séance non reconnue."""
    return {"ok": True, "lignes": list(RATTACHEMENTS["lignes"])}


def rattacher_suspendue(p):
    """Lot 2.6.91 : le média lu est celui d'une séance en suspens (même type, même TMDB, même épisode, même fichier) : elle
    redevient la séance active, jamais affichée à la fois en suspens et en lecture."""
    d = LANCEMENT["d"]
    if TRANSITION["cle"] is not None and p:
        ancienne = next((x for x in SUSPENDUES["l"] if cle_media(x) == TRANSITION["cle"]), None)
        if not ancienne:
            fermer_transition("séance quittée pendant la transition")
        elif not _film_toujours_en_lecture(ancienne, p):
            fermer_transition("l'ancien média n'est plus lu")
    if not SUSPENDUES["l"] or en_cours() or not p or (est_seance(d) and _film_toujours_en_lecture(d, p)):
        return
    s = next((x for x in SUSPENDUES["l"] if _film_toujours_en_lecture(x, p)), None)
    if s and cle_media(s) == TRANSITION["cle"]:
        journal_chaine("transition", {"evenement": "rattachement ignoré", "raison": "observation résiduelle de la séance que KamCiné vient de mettre en suspens",
                                      "suspendue": TRANSITION["cle"], "observe": {k: p.get(k) for k in ("app", "etat", "titre", "pos", "total")}})
        return
    if s:
        restaurer_suspendue(s, "média de la séance en suspens de nouveau lu")


def lancement_courant():
    p = lire_playing()
    rattacher_suspendue(p)
    d = LANCEMENT["d"]
    if not d:
        return None
    joue = _film_toujours_en_lecture(d, p)
    if d.get("origine") in ("seance", "reprise"):
        journaliser_rattachement(d, p, joue)
    resultat = observer_fin_lancement(d, p) if d.get("confirme") else "inconnu"
    # Lot 2.6.89 : pendant qu'un contrôleur suit une série, c'est lui qui décide de la fin (Infuse peut enchaîner l'épisode suivant).
    if resultat == "terminee" and d.get("type") == "tv" and en_cours():
        resultat = "inconnu"
    if resultat == "terminee":
        terminer_seance_normalement()
        return None
    if joue:
        confirmer_lancement(d, p)
        if d.get("origine") in ("seance", "reprise"):
            reattach_session(d, p)
    elif d["etat"] == "en_cours" and d["origine"] == "seance":
        _resoudre_lancement(d)
    # Séance restée bloquée après son générique (contrôleur arrêté, par exemple une 2.6.80 quittée pendant le générique) :
    # le scénario prouve qu'elle est arrivée à son terme, elle se termine au lieu de rester interrompue.
    if (d.get("confirme") and d.get("origine") in ("seance", "reprise") and not en_cours() and not joue
            and ((d.get("scenario") or {}).get("credits") or {}).get("etat") == "fait"):
        terminer_seance_normalement()
        return None
    if d.get("confirme") and en_cours():
        save_session_scenario()
        detail = analyser(lignes_courantes(), etat["mode"])
        if (detail.get("entracte") or {}).get("etat") in ("actif", "fait") and not d.get("entracte_faite"):
            d["entracte_faite"] = True
            memoriser_lancement(d, force=True)
        memoriser_lancement(d)
    age = time.time() - d["t"]
    if d["etat"] == "echec" and age > 600:
        oublier_lancement(d, raison="échec de lancement depuis plus de 10 minutes")
        return None
    # Un état Idle aujourd'hui ne prouve pas ce qui est arrivé hier pendant une coupure.
    interrompue = d.get("interruption_confirmee", False) or (
        resultat == "suspendue" and time.time() - d.get("derniere_lecture", 0) <= 30)
    suspendue = bool(d.get("confirme") and not en_cours() and not joue and interrompue)
    incertaine = bool(d.get("confirme") and not en_cours() and not joue and not suspendue)
    transition = (d.get("suspendue"), d.get("incertaine")) != (suspendue, incertaine)
    d["suspendue"] = suspendue
    d["incertaine"] = incertaine
    d["interruption_confirmee"] = suspendue
    if transition:
        memoriser_lancement(d, force=True)
    phase = d["phase"]
    if d["etat"] == "en_cours" and d["origine"] == "seance":
        if etat["preparation"]:
            phase = "Réveil de l'Apple TV et préparation…"
        elif not d.get("attente_depuis") or time.time() - d["attente_depuis"] <= charger()["film_attente_s"]:
            phase = "Ouverture du film dans Infuse…"
        # sinon : garder la phase posée par _resoudre_lancement (indexation Infuse en cours, MSG_INDEXATION)
    return {"type": d["type"], "id": d["id"], "saison": d["saison"], "episode": d["episode"], "origine": d["origine"],
            "etat": d["etat"], "phase": phase, "message": d["message"], "depuis": int(age), "meta": d["meta"],
            "relance": d.get("relance"), "confirme": bool(d.get("confirme")), "suspendue": suspendue, "incertaine": incertaine, "jeton": d["t"]}


def suivre_lancement():
    """Conserve la séance même lorsque le téléphone ne consulte plus la home."""
    while True:
        try:
            if LANCEMENT["d"]:
                lancement_courant()
        except Exception as ex:
            erreurs.message(ex, "Suivi de la séance indisponible.")
        time.sleep(2)


def contenu_seance():
    """Le titre de la séance en cours (type, id, et le reste de la fiche quand il est connu : titre, affiche, fond, durée, note,
    année), pour que le catalogue sache quelle fiche est en séance, et pour que la home affiche jaquette et titre même à un
    rechargement de page pendant la préparation, les bandes annonces ou l'entracte (2.6, lot J, point 5), quand /film ne donne
    encore rien (le film n'est pas confirmé en lecture à ces étapes). Le lancement donne le type et l'identifiant sans attendre
    TMDB ; sinon le film choisi à la main, puis le film que l'Apple TV lit."""
    d = LANCEMENT["d"]
    if d:
        m = d.get("meta") or {}
        return {"type": d["type"], "id": d["id"], "titre": m.get("titre"), "affiche": m.get("affiche"),
                "fond": m.get("fond"), "duree": m.get("duree"), "note": m.get("note"), "annee": m.get("annee"),
                "saison": d.get("saison"), "episode": d.get("episode")}
    m = film_choisi()
    if not m:
        f = cache_film["data"] or {}
        m = f.get("meta") if f.get("actif") else None
    if not m:
        return None
    return {"type": m["type"], "id": m["id"], "titre": m.get("titre"), "affiche": m.get("affiche"), "fond": m.get("fond"),
            "duree": m.get("duree"), "note": m.get("note"), "annee": m.get("annee"),
            "saison": m.get("saison"), "episode": m.get("episode")}


def mode_effectif(demande=None):
    """Le seul endroit qui décide du mode d'une séance, réel ou test. Un mode demandé explicitement (paramètre de l'API, séance
    programmée déjà enregistrée) passe avant le réglage global, qui est réel par défaut. Tout autre valeur donne le réglage."""
    if demande in ("reel", "test"):
        return demande
    return "test" if charger()["mode"] == "test" else "reel"


def options_seance(ba=None, entracte=None, serie=False):
    """Les deux choix indépendants d'une séance de film : avec les bandes annonces, avec l'entracte. None : la valeur des réglages (bandes annonces
    activées par défaut, entracte selon entracte_actif). Une série n'a ni l'un ni l'autre. explicites dit ce qui a été choisi et non hérité des
    réglages, pour que le scénario dise pourquoi une étape est sautée."""
    cfg = charger()
    if serie:
        return {"ba": False, "entracte": False, "explicites": []}
    expl = [k for k, v in (("ba", ba), ("entracte", entracte)) if v is not None]
    return {"ba": True if ba is None else bool(ba), "entracte": bool(cfg["entracte_actif"]) if entracte is None else bool(entracte), "explicites": expl}


def lire_option(v):
    """Une option reçue de l'interface : vrai, faux, ou None pour valeur des réglages."""
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return v
    return str(v).lower() in ("1", "true", "oui", "on")


VERROU_DEPART = threading.RLock()


def depart_exclusif(fn):
    @wraps(fn)
    def lancer_unique(*args, **kwargs):
        with VERROU_DEPART:
            return fn(*args, **kwargs)
    return lancer_unique


def occupation_courante(frais=False):
    d = LANCEMENT.get("d") or {}
    jeton = "seance:" + str(d.get("t") or etat.get("debut")) if en_cours() or d.get("confirme") or d.get("etat") == "en_cours" else ""
    p = lire_playing(frais=frais)
    if etat_seance.media_reel(p):
        jeton += "|lecture:" + hashlib.sha256((str(p.get("app")) + str(p.get("titre"))).encode()).hexdigest()[:24]
    return jeton


def conflit_depart(type_, id_, saison=0, episode=0, ignorer_ailleurs=False):
    """ignorer_ailleurs : pour un départ sans personne pour confirmer un remplacement (séance programmée, présence déjà
    confirmée avant l'heure, ou lancement interactif qui vient de confirmer "Lancer quand même"). Une vraie séance ou
    un autre lancement confirmé restent un conflit dans tous les cas. Une lecture ailleurs (YouTube...) ne bloque plus
    dès qu'elle est trop ancienne pour être encore affichée quelque part dans l'app (même limite que contenu_ailleurs,
    point 78 : sinon un lancement peut rester bloqué par un état que la Home ne montre déjà plus)."""
    d = LANCEMENT.get("d") or {}
    if en_cours() or d.get("etat") == "en_cours":
        return True
    # Lot 2.6.91 : une vraie séance qui ne tourne plus ne bloque plus un départ : elle partira en suspens (ecarter_lancement).
    # Elle reste un conflit tant que son média joue (plus bas).
    if d.get("confirme") and not est_seance(d) and (d.get("id") != id_ or d.get("type") != type_ or
                             type_ == "tv" and (d.get("saison") != saison or d.get("episode") != episode)):
        return True
    p = lire_playing(frais=True)
    if d.get("confirme") and p and lecture_infuse.correspond(p, d):
        return True
    if not etat_seance.media_reel(p):
        return False
    if p.get("app") not in (None, "", INFUSE_ID):
        if not contenu_ailleurs(p):
            return False
        if ignorer_ailleurs:
            return False
    return True


@depart_exclusif
def demarrer_seance(mode=None, type="auto", tmdb_type="", tmdb_id=0, saison=0, episode=0, ba=None, entracte=None, ignorer_ailleurs=False, appletv_id=None):
    if conflit_depart(tmdb_type, tmdb_id, saison, episode, ignorer_ailleurs):
        return {"ok": False, "message": "Séance déjà en cours. Confirme son remplacement avant de lancer ce titre.", "code": 409}
    contenu = None
    if tmdb_id and tmdb_type in ("movie", "tv"):
        contenu = {"type": tmdb_type, "id": tmdb_id, "saison": saison or None, "episode": episode or None, "lien": True}
        genre = "serie" if tmdb_type == "tv" else "film"
    else:
        genre = resoudre_type(type)
        m = film_choisi()
        if m:
            contenu = {"type": m["type"], "id": m["id"], "saison": None, "episode": None, "lien": False}
    appareil = integrations.appletv(appletv_id)
    if not appareil:
        return {"ok": False, "message": "L’Apple TV n’est pas encore configurée : un administrateur doit terminer la configuration de KamCiné.", "code": 409}
    # Une séance utilise la préférence du compte, sans modifier l'Apple TV globale de l'installation.
    env = integrations.env_seance(dict(os.environ, SEANCE_MODE=mode_effectif(mode), SEANCE_TYPE=genre, KAMCINE_SEANCE="1"), appareil["identifiant"])
    atvlive.LIVE.cibler(appareil["identifiant"])
    opts = options_seance(lire_option(ba), lire_option(entracte), genre == "serie")
    env.update(SEANCE_BANDES_ANNONCES="1" if opts["ba"] else "0", SEANCE_ENTRACTE="1" if opts["entracte"] else "0")
    etat["options"] = opts
    if contenu:
        env.update(SEANCE_TMDB_TYPE=contenu["type"], SEANCE_TMDB_ID=str(contenu["id"]))
        if contenu["saison"] and contenu["episode"]:
            env.update(SEANCE_SAISON=str(contenu["saison"]), SEANCE_EPISODE=str(contenu["episode"]))
        if contenu["lien"]:
            url = url_infuse(contenu["type"], contenu["id"], contenu["saison"], contenu["episode"])
            if not url:
                return {"ok": False, "message": "Choisis une saison et un épisode", "code": 400}
            env["SEANCE_INFUSE_URL"] = url
    etat["lignes"] = []
    etat["preparation"] = True
    BA_LANCEE["d"] = None           # une séance commence : le bloc Bande annonce lancée de l'accueil n'a plus lieu d'être
    etat["mode"], etat["type"], etat["debut"] = env["SEANCE_MODE"], genre, time.time()
    etat["appletv_id"] = appareil["identifiant"]
    if contenu:
        d = lancement_definir(contenu["type"], contenu["id"], contenu["saison"], contenu["episode"], "seance")
        d["mode"] = env["SEANCE_MODE"]
        d["relance"] = {"mode": env["SEANCE_MODE"], "type": genre,
                         "tmdb_type": contenu["type"], "tmdb_id": contenu["id"],
                         "saison": contenu["saison"] or 0, "episode": contenu["episode"] or 0,
                         "ba": "1" if opts["ba"] else "0", "entracte": "1" if opts["entracte"] else "0",
                         "appletv_id": appareil["identifiant"]}
    else:
        oublier_lancement(raison="séance lancée sans contenu identifié")
    try:
        open(LOG, "w").close()
    except Exception:
        pass
    suivi.incrementer_seances()

    depart = etat["debut"]

    def go():
        try:
            if reveiller():
                ligne("Réveil de l'Apple TV")
            if not etat["preparation"] or etat["debut"] != depart:
                return
            if contenu and contenu["lien"]:
                memoriser_recent(contenu["type"], contenu["id"], contenu["saison"], contenu["episode"])
                meta = meta_par_id(contenu["type"], contenu["id"], contenu["saison"], contenu["episode"])
                env["SEANCE_META"] = json.dumps(meta or {}, ensure_ascii=False)
            if not etat["preparation"] or etat["debut"] != depart:
                return          # arrêt demandé pendant la préparation (memoriser_recent peut durer plusieurs secondes)
            compte_pret.set()
            for seconde in (3, 2, 1):
                if not etat["preparation"] or etat["debut"] != depart:
                    return
                ligne("La séance démarre dans %s…" % seconde)
                time.sleep(1)
            with VERROU_PREPARATION:
                if not etat["preparation"] or etat["debut"] != depart:
                    return
                proc = subprocess.Popen(["python", "-u", chemins.code("seance.py")],
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                                        cwd=BASE, start_new_session=True, text=True, bufsize=1)
                etat["proc"] = proc
                etat["preparation"] = False
            threading.Thread(target=lecteur, args=(proc, open(LOG, "a", encoding="utf-8")), daemon=True).start()
        except Exception as e:
            ligne("Erreur au départ : %s" % str(e)[:100])
        finally:
            compte_pret.set()
            if etat["debut"] == depart:
                etat["preparation"] = False

    compte_pret = threading.Event()
    threading.Thread(target=go, daemon=True).start()
    compte_pret.wait(40)
    return {"ok": True, "type": genre}


VERROU_PREPARATION = threading.Lock()


@app.post("/seance/preparation/annuler")
def seance_preparation_annuler():
    """Annulation atomique avant la création du processus de séance."""
    with VERROU_PREPARATION:
        if not etat.get("preparation") or processus_vivant():
            return JSONResponse({"ok": False, "message": "Le démarrage a déjà commencé."}, status_code=409)
        depart = etat.get("debut")
        etat["preparation"] = False
        etat["debut"] = 0
        relais.arreter()
        oublier_lancement(raison="Démarrage annulé avant le lancement")
    BA_LANCEE["d"] = None
    journal_chaine("seance", {"evenement": "démarrage annulé", "raison": "annulation demandée pendant la préparation", "debut": depart})
    return {"ok": True}


def reponse(r):
    if r.get("ok"):
        return r
    return JSONResponse({"ok": False, "message": r.get("message", "")}, status_code=r.get("code", 400))


def choisir_appletv(identifiant):
    """Valide une cible de séance sans toucher au choix global de l'installation."""
    if not identifiant:
        return None
    try:
        if not integrations.appletv(identifiant):
            raise KeyError(identifiant)
    except KeyError:
        return JSONResponse({"ok": False, "message": "Cette Apple TV n’est pas configurée."}, status_code=400)
    return None


def cible_appletv_compte(user_id, demandee=""):
    """Préférence valide du compte, sinon défaut d'installation. Une cible explicite reste validée par choisir_appletv."""
    if demandee:
        return str(demandee)
    disponibles = {a["identifiant"] for a in integrations.appletvs()}
    preference = comptes.lire_preference(user_id, "appletv") if user_id else None
    if preference in disponibles:
        return preference
    return (integrations.appletv() or {}).get("identifiant", "")


@app.post("/start")
def start(mode: str = "", type: str = "auto", tmdb_type: str = "", tmdb_id: int = 0,
          saison: int = 0, episode: int = 0, ba: str = "", entracte: str = "", ignorer_ailleurs: int = 0, appletv: str = "",
          request: Request = None):
    if suspens_plein(tmdb_type or None, tmdb_id or None, saison, episode):
        return reponse_suspens_plein()
    uid = id_connecte(request)
    cible = cible_appletv_compte(uid, appletv)
    refus = choisir_appletv(cible)
    if refus:
        return refus
    if uid and appletv and appletv != comptes.lire_preference(uid, "appletv"):
        comptes.ecrire_preference(uid, "appletv", appletv)
    resultat = demarrer_seance(mode, type, tmdb_type, tmdb_id, saison, episode, ba, entracte,
                               bool(ignorer_ailleurs), appletv_id=cible)
    if isinstance(resultat, dict) and resultat.get("ok") and tmdb_id:
        noter_activite(request, "seance", tmdb_type, tmdb_id)
    return reponse(resultat)


@app.post("/lancement/reessayer")
def lancement_reessayer():
    d = LANCEMENT.get("d") or {}
    relance = d.get("relance")
    if d.get("etat") != "echec" or d.get("origine") != "seance" or not relance or time.time() - d.get("t", 0) > 600:
        return JSONResponse({"ok": False, "message": "Cette séance ne peut plus être relancée. Relance-la depuis sa fiche."}, status_code=409)
    if en_cours() or _film_toujours_en_lecture(d):
        return JSONResponse({"ok": False, "message": "Une séance semble toujours active. Vérifie son état avant de réessayer."}, status_code=409)
    erreurs.journaliser_action("KamCiné", "Nouvelle tentative de lancement pour %r." % ((d.get("meta") or {}).get("titre") or d.get("id")), "info")
    return reponse(demarrer_seance(**relance))


def tuer_seance():
    if processus_vivant():
        try:
            os.killpg(os.getpgid(etat["proc"].pid), signal.SIGTERM)
        except Exception:
            pass


def quitter_film():
    """Sort du film et revient sur sa fiche dans Infuse. La connexion continue (atvlive) répond en général en moins
    d'une seconde ; le repli en ligne de commande (atvremote, plusieurs secondes à chaque appel) ne sert que si elle
    n'est pas disponible."""
    p = lire_playing(frais=True)
    if p and p.get("app") and p["app"] != "com.firecore.infuse":
        if not atvlive.LIVE.ouvrir_app(INFUSE_ID):
            atv("launch_app=" + INFUSE_ID, delai=15)
        time.sleep(1.5)
    if not atvlive.LIVE.menu():
        atv("menu", delai=10)
    atvlive.LIVE.observations.invalidate()
    cache_film["t"] = 0


@app.post("/stop")
@depart_exclusif
def stop(lumieres: int = 1, film: int = 0, attendu: str = "", suspendre: int = 0):
    """suspendre : la séance active part en suspens au lieu d'être terminée (lancer autre chose sans la perdre, lot 2.6.91)."""
    if attendu and occupation_courante(frais=True) != attendu:
        return JSONResponse({"ok": False, "message": "La lecture a changé. Vérifie le média actuel avant de confirmer."}, status_code=409)
    if suspendre and suspens_plein():
        return reponse_suspens_plein()
    if not en_cours() and film and contenu_ailleurs(lire_playing(frais=True) or {}):
        # Une lecture ailleurs (YouTube...) n'a pas de fiche à laquelle revenir comme Infuse : la mettre en pause
        # suffit, le menu est juste une tentative en plus. Pas d'attente sur le résultat : le prochain lancement
        # dans Infuse changera l'app de toute façon.
        if not atvlive.LIVE.pause():
            atv("pause", delai=10)
        atvlive.LIVE.menu()
        atvlive.LIVE.observations.invalidate()
        oublier_hors_seance("Arrêter sur une lecture ailleurs")
        cache_film["t"] = 0
        return {"ok": True}
    if not en_cours():
        if not lire_film().get("actif"):
            if (LANCEMENT["d"] or {}).get("confirme"):
                atvlive.LIVE.observations.invalidate()
                if suspendre:
                    ecarter_lancement(None, "séance mise en suspens pour un autre lancement")
                else:
                    oublier_lancement(raison="Terminer la séance sans lecture")
                return {"ok": True, "pilotee": False}
            return {"ok": False, "message": "Aucune séance en cours"}
        # film lancé depuis Infuse, pas piloté par KamCiné : mêmes options d'arrêt (lumières, quitter le film)
        if film:
            threading.Thread(target=quitter_film, daemon=True).start()
        if lumieres:
            threading.Thread(target=lambda: _essayer(lumieres_tv, "on"), daemon=True).start()
        threading.Thread(target=denon.restaurer, daemon=True).start()
        oublier_hors_seance("Arrêter une lecture non pilotée")
        return {"ok": True, "pilotee": False}
    atvlive.LIVE.observations.invalidate()
    etat["preparation"] = False
    if suspendre and est_seance(LANCEMENT.get("d")):
        ecarter_lancement(None, "séance mise en suspens pour un autre lancement")
    else:
        oublier_lancement(raison="Terminer la séance")
    relais.arreter()
    tuer_seance()
    if film:
        threading.Thread(target=quitter_film, daemon=True).start()
    if lumieres:
        threading.Thread(target=lambda: _essayer(lumieres_tv, "on"), daemon=True).start()
    threading.Thread(target=denon.restaurer, daemon=True).start()
    return {"ok": True}


def oublier_hors_seance(raison):
    """Arrêter une lecture qui n'est pas la séance : une vraie séance en suspens n'est jamais effacée par ce biais."""
    if not est_seance(LANCEMENT.get("d")):
        oublier_lancement(raison=raison)


@app.post("/reprendre")
def reprendre(mode: str = "", entracte: str = ""):
    """Reprend une séance quand le film est déjà lancé : plus de bandes annonces, l'entracte et le générique suivent."""
    return piloter_film(mode, entracte_actif=lire_option(entracte), annuler_classique=lire_option(entracte) is False)


@depart_exclusif
def piloter_film(mode="", **ctx):
    """Prend la main sur le film en lecture (lancé depuis Infuse) avec relais.py : c'est la seule façon de piloter un film que
    KamCiné n'a pas lancé. ctx complète le contexte du relais (entracte envoyée tout de suite, par exemple)."""
    if en_cours():
        return JSONResponse({"ok": False, "message": "Une séance est déjà en cours"}, status_code=409)
    f = film()
    if not f.get("actif"):
        return JSONResponse({"ok": False, "message": "Rien n'est en lecture sur l'Apple TV"}, status_code=400)
    # Lot 2.6.89 : jamais de séance sur un média pas encore identifié. Sans fiche, le type n'est pas prouvé et le repli « film »
    # transformait un épisode en film (entracte, textes de film, séance non mémorisée). L'interface attend l'identification.
    if not f.get("meta") or f["meta"].get("type") not in ("movie", "tv"):
        journal_chaine("seance", {"evenement": "Piloter refusé", "raison": "média pas encore identifié", "titre": f.get("titre")})
        return JSONResponse({"ok": False, "message": "KamCiné identifie encore ce média : Piloter sera possible dans un instant."},
                            status_code=409)
    # Le média identifié fait foi pour le type (lot 2.6.81) : pyatv ne dit pas toujours qu'un épisode est une série.
    genre = "serie" if f["meta"]["type"] == "tv" or f.get("type") == "tv" else "film"
    if suspens_plein(f["meta"]["type"], f["meta"]["id"], f["meta"].get("saison"), f["meta"].get("episode")):
        return reponse_suspens_plein()
    if genre == "serie" and not (f["meta"].get("saison") and f["meta"].get("episode")):
        journal_chaine("seance", {"evenement": "Piloter refusé", "raison": "épisode pas encore identifié", "titre": f.get("titre")})
        return JSONResponse({"ok": False, "message": "KamCiné identifie encore l’épisode : Piloter sera possible dans un instant."},
                            status_code=409)
    etat["lignes"] = []
    mode = mode_effectif(mode)
    etat["mode"], etat["type"], etat["debut"] = mode, genre, time.time()
    try:
        open(LOG, "w").close()
    except Exception:
        pass
    ent = ctx.get("entracte_actif")
    if ent is None:
        ent = charger()["entracte_actif"] and not ctx.get("annuler_classique", False)
    ent = bool(ent) and genre != "serie"    # jamais d'entracte pour un épisode
    ctx["entracte_actif"] = ent
    etat["options"] = options_seance(False, ent, genre == "serie")
    suivi.incrementer_seances()
    m = f.get("meta")
    ancien = LANCEMENT["d"] or {}
    deja = bool(m and ancien.get("type") == m.get("type") and ancien.get("id") == m.get("id")
                and ancien.get("saison") == m.get("saison") and ancien.get("episode") == m.get("episode")
                and ancien.get("entracte_faite"))
    if deja:
        ctx["entracte_deja"] = True
    d = lancement_definir(m["type"], m["id"], m.get("saison"), m.get("episode"), "reprise", meta=m)
    d["mode"] = mode
    d["entracte_faite"] = deja
    d["relance"] = {"entracte": "1" if ent and genre != "serie" else "0"}
    if f.get("total"):
        d["total_fichier"] = f["total"]
    memoriser_lancement(d, force=True)
    meta_relais = dict(m, total_fichier=f.get("total") or None)
    if not relais.demarrer(dict({"test": mode != "reel", "meta": meta_relais, "type": genre}, **ctx)):
        return JSONResponse({"ok": False, "message": "La prise en main est déjà en cours."}, status_code=409)
    return {"ok": True, "type": genre}


@app.post("/entracte/manuel")
def entracte_manuel(duree: int = 300, annuler: int = 0):
    duree = max(10, min(int(duree), 1800))
    if relais.actif:
        relais.entracte(duree, annuler)
        return {"ok": True}
    if not en_cours():
        # film lancé depuis Infuse, pas encore piloté : envoyer l'entracte prend la main, puis le générique suit
        return piloter_film("", entracte_deja=True, entracte_manuelle=duree)
    if not processus_vivant():
        return JSONResponse({"ok": False, "message": "La séance démarre : l'entracte manuelle sera possible pendant le film"}, status_code=400)
    lignes = lignes_courantes()
    if phase_depuis(lignes) not in (2, 4):
        return JSONResponse({"ok": False, "message": "L'entracte manuelle est possible pendant le film"}, status_code=400)
    e = analyser(lignes, etat["mode"])["entracte"]
    if e["etat"] == "actif":
        return JSONResponse({"ok": False, "message": "Une entracte est déjà en cours"}, status_code=409)
    deja = e["etat"] in ("actif", "fait")
    f = film()
    tuer_seance()
    time.sleep(1)
    relais.demarrer({"test": etat["mode"] != "reel", "meta": f.get("meta"), "type": "serie" if etat["type"] == "serie" else "film",
                     "silencieux": True, "entracte_deja": deja or bool(annuler),
                     "entracte_prevu": None if (annuler or deja) else e["prevu_s"],
                     "annuler_classique": bool(annuler), "entracte_manuelle": duree})
    return {"ok": True}


def meta_de_la_seance():
    """Le film de la séance en cours. Pendant l'entracte l'Apple TV joue une vidéo YouTube, /film ne le reconnaît donc pas :
    on prend ce que le lancement a retenu, puis le film choisi à la main."""
    d = LANCEMENT["d"]
    if d and d.get("meta"):
        return d["meta"]
    m = film_choisi()
    if m:
        try:
            return meta_par_id(m["type"], m["id"], m.get("saison"), m.get("episode"))
        except Exception:
            return None
    return None


@app.post("/entracte/reprendre")
def entracte_reprendre():
    """Point 7 de la 2.0 : écourte l'entracte en cours, le film reprend tout de suite (retour à Infuse et recul, comme à la fin normale)."""
    lignes = lignes_courantes()
    if analyser(lignes, etat["mode"])["entracte"]["etat"] != "actif":
        return JSONResponse({"ok": False, "message": "Aucune entracte en cours"}, status_code=409)
    if relais.actif:
        relais.reprendre_entracte()
        return {"ok": True}
    if not processus_vivant():
        return JSONResponse({"ok": False, "message": "Aucune séance en cours"}, status_code=400)
    meta = meta_de_la_seance()
    tuer_seance()
    time.sleep(1)
    relais.demarrer({"test": etat["mode"] != "reel", "meta": meta, "type": "serie" if etat["type"] == "serie" else "film",
                     "silencieux": True, "entracte_deja": True, "entracte_prevu": None, "reprendre_maintenant": True})
    return {"ok": True}


# ---------- Séances programmées ----------
def lancer_planifiee(e):
    """La présence a déjà été confirmée avant l'appel (planning.tick) : une lecture ailleurs (YouTube...) ne doit plus
    faire échouer silencieusement ce départ, personne n'est là pour confirmer un remplacement. Une vraie séance ou un
    autre lancement confirmé restent bloquants (conflit_depart, ignorer_ailleurs)."""
    r = demarrer_seance(e.get("mode"), "auto", e["type"], e["id"], e.get("saison") or 0, e.get("episode") or 0,
                        e.get("bandes_annonces"), e.get("entracte"), ignorer_ailleurs=True)
    return bool(r.get("ok")), r.get("message", "")


@app.on_event("startup")
def demarrage():
    threading.Thread(target=suivre_lancement, daemon=True).start()
    threading.Thread(target=planning.boucle, args=(lancer_planifiee, en_cours), daemon=True).start()
    threading.Thread(target=attente.boucle, args=(rafraichir_attente, etat_film_attente, lancer_attente, suivre_attente, conflit_attente,
                                                  reserve_attente, en_cours), daemon=True).start()
    filmsactu.boucle(lambda: charger()["ba_flux_min"], en_cours)
    atvlive.LIVE.demarrer()
    def vus_trakt():
        if charger()["badges_vu_actif"] and trakt.connectee():
            trakt.rafraichir_vus()
    bibliotheque.demarrer([vus_trakt])


@app.on_event("shutdown")
def arret():
    """Arrêt du service (SIGTERM de Docker, 2.6.108) : la séance en cours, lancée dans son propre groupe de processus,
    reçoit SIGTERM et quelques secondes pour finir plutôt que d'être tuée brutalement avec le conteneur."""
    if processus_vivant():
        tuer_seance()
        try:
            etat["proc"].wait(timeout=5)
        except Exception:
            pass


@app.get("/health")
def sante():
    """Santé pour Docker (HEALTHCHECK) : le processus répond. Libre, sans session ni donnée ; ne dépend d'aucun appareil,
    service externe ni d'Internet."""
    return {"ok": True, "version": VERSION_SERVICE}


def seance_json(e):
    """Une séance programmée, avec sa date et son heure du serveur en clair et au format des champs de l'interface."""
    return dict(e, quand=planning.local_iso(e["t"]), txt=planning.quand_txt(e["t"]), txt_court=planning.quand_txt(e["t"], True),
                txt_mini=planning.quand_mini(e["t"]), heure=planning.heure_txt(e["t"]),
                confirmation_fin=planning.confirmation_fin(e))


# ---------- Séances en attente de téléchargement (2.2, lot C) ----------
def versions_attente(cfg, choix=None):
    """Renvoie le choix explicite HD/4K ou, sans choix, la qualité configurée pour les séances en attente."""
    choix_labels = {"hd": ("HD", "Qualité choisie : HD"), "uhd": ("4K", "Qualité choisie : 4K"), "both": ("HD et 4K", "Qualité choisie : HD et 4K")}
    if choix in choix_labels:
        libelle, raison = choix_labels[choix]
        return choix, libelle, raison
    if cfg["qualite_preferee"] == "hd":
        return "hd", "HD", "Qualité visée : HD"
    if (bibliotheque.config().get("radarr_uhd") or {}).get("cle"):
        return "uhd", "4K", "Qualité visée : la meilleure, la 4K est possible (Radarr 4K configuré)"
    return "hd", "HD", "Qualité visée : la meilleure, mais aucun Radarr 4K n'est configuré, la demande se fera en HD"


def _qualites_selectionnees(versions):
    return ("hd", "uhd") if versions == "both" else (versions,)


def _demande_qualite_en_cours(version):
    version = version or {}
    etat = version.get("etat")
    if etat in ("attente", "telechargement"):
        return True
    return etat in ("recherche", "a_venir") and (version.get("source") == "overseerr" or bool(version.get("demande")))


def _etat_demande_cible(st, versions):
    """État synthétique des seules qualités choisies, sans bloquer une demande 4K par le HD déjà présent."""
    if not st:
        return None
    valeurs = [(st.get(q) or {}).get("etat") for q in _qualites_selectionnees(versions)]
    if valeurs and all(v in ("disponible", "partiel") for v in valeurs):
        return "disponible"
    if any(v in ("telechargement",) for v in valeurs):
        return "telechargement"
    if any(_demande_qualite_en_cours((st or {}).get(q)) for q in _qualites_selectionnees(versions)):
        return "recherche"
    return "absent"


def _qualites_a_demander(st, versions):
    """Retire seulement les qualités déjà présentes ou déjà en attente; les autres restent explicitement ciblées."""
    return tuple(q for q in _qualites_selectionnees(versions)
                 if ((st or {}).get(q) or {}).get("etat") not in ("disponible", "partiel")
                 and not _demande_qualite_en_cours((st or {}).get(q)))


def attente_json(e):
    return dict(e, libelle=attente.LIBELLES.get(e.get("statut"), e.get("statut")), cree_txt=planning.quand_txt(e["cree"], True),
                quand_txt=planning.quand_txt(e["quand"]) if e.get("quand") else None,
                depart_txt=planning.quand_txt(e["depart"], True) if e.get("depart") else None)


def rafraichir_attente(e):
    bibliotheque.relire_film(e["id"])


def etat_film_attente(e):
    return bibliotheque.film_pret(e["id"], e.get("versions") or "both")


def conflit_attente(t, reserve_s):
    planning.regler(charger())
    return planning.verifier(t, reserve_s, reserves_anciennes(), [b for b in [bloc_seance_en_cours()] if b])


def reserve_attente(e):
    return reserve_de("movie", e.get("duree_min"), e.get("entracte"))


def lancer_attente(e):
    r = demarrer_seance(e.get("mode"), "film", "movie", e["id"], 0, 0, e.get("bandes_annonces"), e.get("entracte"))
    return {"ok": bool(r.get("ok")), "message": r.get("message", ""), "code": r.get("code")}


def suivre_attente(e, now):
    """Suit un lancement : lancée quand la lecture est confirmée, lancement échoué avec la raison sinon."""
    d = lancement_courant()
    if d and d["type"] == "movie" and d["id"] == e["id"]:
        if d["etat"] == "ok":
            return {"statut": "lancee", "message": ""}
        if d["etat"] == "echec":
            return {"statut": "lancement_echoue", "message": d["message"] or "Le lancement a échoué."}
    elif not en_cours() and now - (e.get("t_lancement") or now) > 20:
        return {"statut": "lancement_echoue", "message": "La séance s'est arrêtée avant le film. Le journal (Réglages) dit pourquoi."}
    if now - (e.get("t_lancement") or now) > charger()["lancement_confirmation_s"] + 30:
        return {"statut": "lancement_echoue", "message": "Aucune confirmation de la lecture dans le délai réglé."}
    return {}


@app.get("/attente")
def attente_liste():
    return {"ok": True, "seances": [attente_json(e) for e in attente.liste()]}


@app.get("/attente/options")
def attente_options(id: int = 0, versions: str = ""):
    """Options du dialogue de demande combinée : qualité, état Overseerr et séance déjà en attente."""
    if versions and versions not in ("hd", "uhd", "both"):
        return JSONResponse({"ok": False, "message": "Version demandée invalide."}, status_code=400)
    cfg = charger()
    versions, libelle, raison = versions_attente(cfg, versions or None)
    st = None
    try:
        st = bibliotheque.statut("movie", id, None, False)
    except Exception:
        pass
    st = appliquer_demandes_locales("movie", id, st)
    ex = attente.active_pour("movie", id)
    planning.regler(cfg)
    tot = planning.arrondi_propose(planning.plus_tot())
    etat_cible = _etat_demande_cible(st, versions)
    deja_cible = any(_demande_qualite_en_cours((st or {}).get(q)) for q in _qualites_selectionnees(versions))
    return {"ok": True, "overseerr": bool(bibliotheque.ov_cle()), "versions": versions, "libelle": libelle, "raison": raison,
            "statut": etat_cible, "deja_demande": deja_cible, "active": attente_json(ex) if ex else None,
            "mode": mode_effectif(), "fenetre": "%s à %s" % ("%02d:%02d" % divmod(cfg["attente_debut_min"], 60), "%02d:%02d" % divmod(cfg["attente_fin_min"], 60)),
            "fuseau": planning.TIMEZONE_NAME, "delai_s": cfg["attente_delai_s"], "plus_tot_local": planning.local_iso(tot)}


@app.post("/attente/ajouter")
async def attente_ajouter(request: Request):
    """Demande le film à Overseerr dans la qualité choisie ou configurée, puis enregistre la séance en attente."""
    c = await corps_json(request)
    if c.get("type") == "tv":
        return JSONResponse({"ok": False, "message": "Cette fonction ne marche que pour les films dans cette version."}, status_code=400)
    try:
        id_ = int(c["id"])
    except Exception:
        return JSONResponse({"ok": False, "message": "Film invalide"}, status_code=400)
    if not bibliotheque.ov_cle():
        return JSONResponse({"ok": False, "message": "Overseerr n'est pas configuré : la demande de téléchargement est impossible."}, status_code=400)
    meta = meta_par_id("movie", id_)
    if not meta:
        return JSONResponse({"ok": False, "message": "Film introuvable"}, status_code=404)
    try:
        st = bibliotheque.statut("movie", id_, None, False)
    except Exception:
        st = None
    st = appliquer_demandes_locales("movie", id_, st)
    quand = None
    if c.get("quand") or c.get("t"):
        try:
            quand = planning.epoch_depuis_local(str(c["quand"])) if c.get("quand") else float(c["t"])
        except Exception:
            return JSONResponse({"ok": False, "message": "Date et heure invalides"}, status_code=400)
        refus = refus_passe(quand)
        if refus:
            return refus
    cfg = charger()
    try:
        rappel_valeur = int(c.get("rappel_min", cfg.get("rappel_min", 15)))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "message": "Délai de rappel invalide."}, status_code=400)
    if rappel_valeur not in (0, 15, 30, 60):
        return JSONResponse({"ok": False, "message": "Choisis un rappel de 15 min, 30 min, 1 h, ou aucun."}, status_code=400)
    rappel_actif = lire_option(c.get("rappel_actif", cfg.get("rappel_actif", True)))
    choix_versions = c.get("versions")
    if choix_versions is not None and choix_versions not in ("hd", "uhd", "both"):
        return JSONResponse({"ok": False, "message": "Version demandée invalide."}, status_code=400)
    versions, libelle, raison = versions_attente(cfg, choix_versions)
    if _etat_demande_cible(st, versions) == "disponible":
        return JSONResponse({"ok": False, "message": "La qualité choisie est déjà disponible : lance la séance directement."}, status_code=409)
    if attente.active_pour("movie", id_):
        return JSONResponse({"ok": False, "message": "Une séance est déjà en attente pour ce film."}, status_code=409)
    note = ""
    cibles = _qualites_a_demander(st, versions)
    if cibles:
        versions_envoi = "both" if len(cibles) == 2 else cibles[0]
        rs = envoyer_demande("movie", id_, versions_envoi)
        if not any(v["ok"] for v in rs.values()):
            texte = "; ".join(v["message"] for v in rs.values())
            return JSONResponse({"ok": False, "message": "Overseerr : " + (texte or "demande refusée")}, status_code=400)
        noter_demande("movie", id_, versions_envoi)
        noter_activite(request, "demande", "movie", id_, "séance en attente")
        deja = set(_qualites_selectionnees(versions)) - set(cibles)
        if deja:
            noms = " et ".join("4K" if q == "uhd" else "HD" for q in sorted(deja))
            note = noms + " déjà disponible ou en cours ; seules les autres qualités ont été demandées."
    else:
        note = "La qualité choisie est déjà disponible ou demandée ; la séance attendra son arrivée."
    e, ex = attente.ajouter({"type": "movie", "id": id_, "titre": meta["titre"], "affiche": meta.get("affiche"), "annee": meta.get("annee"),
                             "duree_min": meta.get("duree"), "mode": mode_effectif(c.get("mode")), "versions": versions, "quand": quand,
                             "rappel_min": rappel_valeur if quand and rappel_actif else 0, "rappel_envoye": False,
                             "bandes_annonces": lire_option(c.get("bandes_annonces")), "entracte": lire_option(c.get("entracte")),
                             "note": note})
    if ex:
        return JSONResponse({"ok": False, "message": "Une séance est déjà en attente pour ce film."}, status_code=409)
    threading.Thread(target=rafraichir_attente, args=(e,), daemon=True).start()
    return {"ok": True, "seance": attente_json(e)}


@app.post("/attente/annuler")
def attente_annuler(id: str = ""):
    return {"ok": attente.annuler(id)}


@app.post("/attente/relancer")
def attente_relancer(id: str = ""):
    e = attente.trouver(id)
    if not e or e["statut"] != "lancement_echoue":
        return JSONResponse({"ok": False, "message": "Cette séance n'a pas de lancement à relancer."}, status_code=409)
    if en_cours():
        return JSONResponse({"ok": False, "message": "Une séance est déjà en cours."}, status_code=409)
    attente.marquer(id, statut="lancement", t_lancement=time.time(), message="Lancement en cours")
    r = lancer_attente(e)
    if not r["ok"]:
        attente.marquer(id, statut="lancement_echoue", message=r["message"] or "Le lancement a échoué.")
        return JSONResponse({"ok": False, "message": r["message"] or "Le lancement a échoué."}, status_code=400)
    return {"ok": True}


@app.get("/planning")
def planning_liste():
    cfg = charger()
    planning.regler(cfg)
    now = time.time()
    tot = planning.arrondi_propose(planning.plus_tot(now))
    attentes = [attente_json(e) for e in attente.liste()]
    non_lues = notifications.observer_attentes(attentes)
    return {"ok": True, "seances": [seance_json(e) for e in planning.liste()], "rappels": [seance_json(e) for e in planning.rappels()],
            "attentes": attentes,
            "maintenant": now, "maintenant_local": planning.local_iso(now), "fuseau": planning.TIMEZONE_NAME, "plus_tot": tot, "plus_tot_local": planning.local_iso(tot),
            "imminente_s": cfg["plan_imminente_h"] * 3600, "decompte_s": cfg["plan_decompte_min"] * 60,
            "notifications_non_lues": non_lues}


def reserve_de(type_, duree_min, entracte=None):
    """Durée réservée pour une séance (règle de marge de planning.py, réglages d'entracte compris). entracte : le choix de cette séance, sinon le réglage."""
    cfg = charger()
    planning.regler(cfg)
    ent = cfg["entracte_actif"] if entracte is None else bool(entracte)
    return planning.duree_reservee(type_, duree_min, ent, max(cfg["entracte_min"], cfg["entracte_max"]))


def reserves_anciennes():
    """Séances programmées avant la 2.0 : elles n'ont pas de durée réservée, on la calcule d'après la fiche TMDB."""
    out = {}
    for e in planning.liste():
        if e.get("etat") in ("prevue", "lancee") and not e.get("reserve_s"):
            m = meta_par_id(e["type"], e["id"], e.get("saison"), e.get("episode"))
            out[e["pid"]] = reserve_de(e["type"], (m or {}).get("duree"), e.get("entracte"))
    return out


def bloc_seance_en_cours():
    """Créneau de la séance qui tourne en ce moment, pour qu'on ne programme rien par dessus."""
    if not en_cours():
        return None
    now = time.time()
    debut = etat["debut"] or now
    f = cache_film["data"] or {}
    if f.get("actif") and f.get("total"):
        fin = now + max(0, f["total"] - f["pos"]) + planning.REGLES["securite_s"]
        titre = (f.get("meta") or {}).get("titre") or f.get("titre")
    else:
        d = LANCEMENT["d"] or {}
        m = d.get("meta") or {}
        fin = max(debut + reserve_de(m.get("type") or "movie", m.get("duree")), now + planning.REGLES["securite_s"])
        titre = m.get("titre")
    return {"debut": debut, "fin": fin, "titre": titre or "la séance en cours", "pid": None}


def refus_passe(t):
    """Point 9 : pas de séance dans le passé, ni programmée ni déplacée. Le service est la référence, à l'heure du serveur."""
    planning.regler(charger())
    message = planning.verifier_futur(t)
    if not message:
        return None
    tot = planning.arrondi_propose(planning.plus_tot())
    return JSONResponse({"ok": False, "message": message, "passe": True, "plus_tot": tot, "plus_tot_local": planning.local_iso(tot)}, status_code=400)


def lire_creneau(c):
    """Lit et vérifie les champs d'une demande de programmation. Renvoie (t, type, id, saison, episode) ou une réponse d'erreur."""
    try:
        t = planning.epoch_depuis_local(str(c["quand"])) if c.get("quand") else float(c["t"])
        type_, id_ = c["type"], int(c["id"])
        saison, episode = int(c.get("saison") or 0), int(c.get("episode") or 0)
    except Exception:
        return JSONResponse({"ok": False, "message": "Séance invalide"}, status_code=400)
    if type_ not in ("movie", "tv") or (type_ == "tv" and not (saison and episode)):
        return JSONResponse({"ok": False, "message": "Choisis un film, ou une série avec saison et épisode"}, status_code=400)
    refus = refus_passe(t)
    if refus:
        return refus
    return t, type_, id_, saison, episode


def _bloc_json(b):
    return {"titre": b["titre"], "debut": b["debut"], "fin": b["fin"], "debut_txt": planning.quand_txt(b["debut"]),
            "fin_txt": planning.heure_txt(b["fin"])}


def _libre_json(t):
    return {"premier_libre": t, "premier_libre_local": planning.local_iso(t), "premier_libre_txt": planning.quand_txt(t)}


@app.get("/planning/verifier")
def planning_verifier(t: float = 0, quand: str = "", type: str = "movie", id: int = 0, saison: int = 0, episode: int = 0, exclure: str = ""):
    """Dit si ce créneau est libre, sans rien enregistrer. L'interface s'en sert pour prévenir avant l'envoi. quand : date et heure
    du serveur (2026-09-21T20:30), sinon t en secondes. exclure : la séance qu'on déplace, qui ne se gêne pas elle même."""
    lu = lire_creneau({"t": t, "quand": quand, "type": type, "id": id, "saison": saison, "episode": episode})
    if isinstance(lu, JSONResponse):
        return lu
    t, type_, id_, saison, episode = lu
    meta = meta_par_id(type_, id_, saison or None, episode or None)
    if not meta:
        return JSONResponse({"ok": False, "message": "Titre introuvable"}, status_code=404)
    reserve = reserve_de(type_, meta.get("duree"))
    externes = [b for b in [bloc_seance_en_cours()] if b]
    conflit, libre = planning.verifier(t, reserve, reserves_anciennes(), externes, exclure=exclure or None)
    return dict({"ok": True, "libre": conflit is None, "debut": t, "fin": t + reserve, "reserve_s": reserve,
                 "duree_min": meta.get("duree"), "conflit": _bloc_json(conflit) if conflit else None,
                 "tampon_min": planning.REGLES["tampon_s"] // 60, "debut_txt": planning.quand_txt(t), "fin_txt": planning.heure_txt(t + reserve)},
                **_libre_json(libre))


ETATS_OK_PROG = ("disponible", "partiel", "inconnu")


def refus_indisponible(type_, id_, saison, titre):
    """Point 5 de la 2.3 : une séance programmée exige un titre présent dans la bibliothèque. Renvoie le message de refus, ou None.
    Même règle que l'interface (fiche) : on ne refuse que si le statut est connu et complet ET qu'aucune version n'est disponible.
    Statut absent, en erreur ou incomplet (listes pas encore lues) : on laisse passer, un faux refus serait pire qu'une séance de trop.
    Pour une série, c'est l'état de la saison choisie s'il existe."""
    try:
        st = bibliotheque.statut(type_, id_, None, True)
    except Exception:
        return None
    if not st or st.get("erreur") or st.get("incomplet"):
        return None
    hd, uhd = (st.get("hd") or {}), (st.get("uhd") or {})
    if type_ == "tv" and saison:
        sa = (st.get("saisons") or {}).get(str(saison)) or {}
        hd, uhd = (sa.get("hd") or hd), (sa.get("uhd") or uhd)
    etat = bibliotheque.meilleur(hd.get("etat", "inconnu"), uhd.get("etat", "inconnu"))
    if etat in ETATS_OK_PROG:
        return None
    if type_ == "tv":
        return ("La saison %s de « %s » n'est pas dans ta bibliothèque : impossible de la programmer. "
                "Fais la demande depuis sa fiche KamCiné, puis programme la séance quand elle sera disponible." % (saison or "", titre))
    return ("« %s » n'est pas dans ta bibliothèque : impossible de le programmer. Utilise « Télécharger puis lancer la séance » "
            "sur sa fiche, qui peut aussi attendre la date et l'heure de ton choix." % titre)


@app.post("/planning/ajouter")
async def planning_ajouter(request: Request):
    c = await corps_json(request)
    lu = lire_creneau(c)
    if isinstance(lu, JSONResponse):
        return lu
    t, type_, id_, saison, episode = lu
    meta = meta_par_id(type_, id_, saison or None, episode or None)
    if not meta:
        return JSONResponse({"ok": False, "message": "Titre introuvable"}, status_code=404)
    indispo = refus_indisponible(type_, id_, saison or None, meta["titre"])
    if indispo:
        return JSONResponse({"ok": False, "message": indispo, "indisponible": True}, status_code=409)
    opt_ba, opt_ent = lire_option(c.get("bandes_annonces")), lire_option(c.get("entracte"))
    reserve = reserve_de(type_, meta.get("duree"), None if type_ == "tv" else opt_ent)
    externes = [b for b in [bloc_seance_en_cours()] if b]
    cfg = charger()
    try:
        rappel_valeur = int(c.get("rappel_min", cfg.get("rappel_min", 15)))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "message": "Délai de rappel invalide."}, status_code=400)
    if rappel_valeur not in (0, 15, 30, 60):
        return JSONResponse({"ok": False, "message": "Choisis un rappel de 15 min, 30 min, 1 h, ou aucun."}, status_code=400)
    rappel_min = rappel_valeur if c.get("rappel_actif", cfg.get("rappel_actif", True)) else 0
    e, refus = planning.ajouter_si_libre(
        {"t": t, "type": type_, "id": id_, "saison": saison or None, "episode": episode or None,
         "mode": mode_effectif(c.get("mode")), "titre": meta["titre"], "bandes_annonces": opt_ba, "entracte": opt_ent,
         "affiche": meta.get("affiche"), "annee": meta.get("annee"), "duree_min": meta.get("duree")},
        rappel_min, reserve, reserves_anciennes(), externes)
    if refus:
        return JSONResponse(dict({"ok": False, "message": "Cette séance chevauche « %s »." % refus["conflit"]["titre"],
                                  "conflit": _bloc_json(refus["conflit"]), "tampon_min": planning.REGLES["tampon_s"] // 60},
                                 **_libre_json(refus["libre"])), status_code=409)
    return {"ok": True, "seance": seance_json(e)}


@app.post("/planning/modifier")
async def planning_modifier(request: Request):
    """Point 7 : change la date et l'heure d'une séance encore prévue, ou reprogramme une séance ratée (manquée, en
    échec, ou annulée faute de confirmation), sans la supprimer. Mêmes vérifications qu'à la création : pas dans le
    passé, pas de chevauchement (la séance déplacée ne se gêne pas elle même), règle de marge. Refus clair sinon."""
    c = await corps_json(request)
    pid = str(c.get("id") or "")
    e = planning.trouver(pid)
    if not e:
        return JSONResponse({"ok": False, "message": "Cette séance programmée n'existe plus."}, status_code=404)
    if not planning.modifiable(e):
        return JSONResponse({"ok": False, "message": "Cette séance n'est plus modifiable (%s)." % planning.ETATS_TXT.get(e.get("etat"), e.get("etat"))}, status_code=409)
    lu = lire_creneau({"t": c.get("t"), "quand": c.get("quand"), "type": e["type"], "id": e["id"], "saison": e.get("saison"), "episode": e.get("episode")})
    if isinstance(lu, JSONResponse):
        return lu
    t = lu[0]
    meta = meta_par_id(e["type"], e["id"], e.get("saison"), e.get("episode"))
    indispo = refus_indisponible(e["type"], e["id"], e.get("saison"), (meta or {}).get("titre") or e.get("titre") or "")
    if indispo:
        return JSONResponse({"ok": False, "message": indispo, "indisponible": True}, status_code=409)
    reserve = reserve_de(e["type"], (meta or {}).get("duree") or e.get("duree_min"), e.get("entracte"))
    externes = [b for b in [bloc_seance_en_cours()] if b]
    cfg = charger()
    try:
        rappel_valeur = int(c.get("rappel_min", cfg.get("rappel_min", 15)))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "message": "Délai de rappel invalide."}, status_code=400)
    if rappel_valeur not in (0, 15, 30, 60):
        return JSONResponse({"ok": False, "message": "Choisis un rappel de 15 min, 30 min, 1 h, ou aucun."}, status_code=400)
    rappel_min = rappel_valeur if c.get("rappel_actif", cfg.get("rappel_actif", True)) else 0
    neuve, refus = planning.deplacer_si_libre(pid, t, reserve, rappel_min, reserves_anciennes(), externes)
    if refus and refus.get("erreur"):
        return JSONResponse({"ok": False, "message": refus["erreur"]}, status_code=409)
    if refus:
        return JSONResponse(dict({"ok": False, "message": "Ce nouveau créneau chevauche « %s »." % refus["conflit"]["titre"],
                                  "conflit": _bloc_json(refus["conflit"]), "tampon_min": planning.REGLES["tampon_s"] // 60},
                                 **_libre_json(refus["libre"])), status_code=409)
    return {"ok": True, "seance": seance_json(neuve)}


@app.post("/planning/annuler")
def planning_annuler(id: str = ""):
    return {"ok": planning.annuler(id)}


@app.post("/planning/rappel_lu")
def planning_rappel_lu(id: str = ""):
    planning.marquer(id, rappel_lu=True)
    return {"ok": True}


@app.post("/planning/confirmer")
def planning_confirmer(id: str = ""):
    planning.regler(charger())
    ok, message = planning.confirmer(id)
    return JSONResponse({"ok": ok, "message": message}, status_code=200 if ok else 409)


# ---------- Bandes annonces de la séance : de quel film s'agit-il ? ----------
def rss_chaine():
    """Les vidéos récentes de la chaîne, [{id, titre}] : le flux RSS (relu au plus toutes les 10 minutes), dont l'identifiant de chaîne de
    chaque entrée est contrôlé, et qui alimente aussi l'archive locale (filmsactu.py)."""
    return filmsactu.flux(ttl=600)[0]


titre_du_film = filmsactu.titre_du_film


def cle_demande(type_, id_):
    """Clé d'un titre dans demandes_auto.json : l'identifiant nu pour un film (format d'avant la 2.2), tv:ID pour une série, car les
    identifiants TMDB des films et des séries se recoupent."""
    return id_ if type_ != "tv" else "tv:%d" % id_


def lire_demandes():
    try:
        with open(DEMANDES_AUTO, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, list) else []
    except Exception:
        return []


def cle_demande_qualite(type_, id_, qualite):
    return "qualite:%s:%d:%s" % ("tv" if type_ == "tv" else "movie", int(id_), qualite)


def deja_demande(type_, id_, qualite=None, demandes=None):
    demandes = lire_demandes() if demandes is None else demandes
    globale = cle_demande(type_, id_)
    if globale in demandes:
        return True
    prefixe = "qualite:%s:%d:" % ("tv" if type_ == "tv" else "movie", int(id_))
    if qualite in ("hd", "uhd"):
        return cle_demande_qualite(type_, id_, qualite) in demandes
    return any(isinstance(x, str) and x.startswith(prefixe) for x in demandes)


def noter_demande(type_, id_, versions=None):
    d = lire_demandes()
    if versions in ("hd", "uhd", "both"):
        qualites = ("hd", "uhd") if versions == "both" else (versions,)
        cles = [cle_demande_qualite(type_, id_, q) for q in qualites]
    else:
        cles = [cle_demande(type_, id_)]
    for k in cles:
        if k not in d:
            d.append(k)
    ecrire_prive(DEMANDES_AUTO, d[-200:])


def appliquer_demandes_locales(type_, id_, statut, demandes=None):
    """Affiche tout de suite les qualités réellement demandées, sans attendre le prochain index Overseerr."""
    if not isinstance(statut, dict):
        return statut
    out = dict(statut)
    if isinstance(out.get("saisons"), dict):
        out["saisons"] = {k: dict(v) if isinstance(v, dict) else v for k, v in out["saisons"].items()}
    cibles = [out] + [s for s in (out.get("saisons") or {}).values() if isinstance(s, dict)]
    for qualite in ("hd", "uhd"):
        if not deja_demande(type_, id_, qualite, demandes):
            continue
        for cible in cibles:
            version = cible.get(qualite)
            if not isinstance(version, dict):
                continue
            etat = version.get("etat")
            if etat in ("disponible", "partiel", "telechargement", "refuse", "echec", "bloque"):
                continue
            version = dict(version, demande=True)
            if etat != "a_venir":
                version.update(etat="attente", source="demande_locale")
            cible[qualite] = version
    if out.get("hd") and out.get("uhd"):
        out["global"] = bibliotheque.meilleur(out["hd"].get("etat", "inconnu"), out["uhd"].get("etat", "inconnu"))
    return out


def invalider_demandes_recentes():
    for cle in list(cache_listes):
        if isinstance(cle, tuple) and cle and cle[0] == "demandes":
            cache_listes.pop(cle, None)


MSG_DEMANDE_INCERTAINE = ("Overseerr n'a pas répondu à temps. La demande est peut être quand même partie : "
                          "vérifie dans Overseerr avant de la refaire.")


def _date_overseerr(texte):
    try:
        return datetime.fromisoformat(str(texte).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def demande_apres_expiration(type_, id_, is4k, debut, erreur):
    """Point 82 : la demande est partie mais Overseerr n'a pas répondu dans le délai. Elle a pu être enregistrée malgré
    tout : on relit une fois ce titre au lieu de la renvoyer, pour ne jamais la doubler. Une demande de la même qualité,
    créée depuis l'envoi (ou non refusée si Overseerr ne donne pas sa date), vaut réussite ; sinon l'issue reste incertaine."""
    erreurs.message(erreur, "Overseerr n'a pas répondu à temps à une demande.")
    try:
        r = overseerr_appel("GET", "/%s/%d" % ("tv" if type_ == "tv" else "movie", id_))
        r.raise_for_status()
        demandes = (r.json().get("mediaInfo") or {}).get("requests") or []
    except Exception:
        demandes = []
    for d in demandes:
        if bool(d.get("is4k")) != is4k:
            continue
        cree = _date_overseerr(d.get("createdAt"))
        # Marge de deux minutes : les horloges du NAS et d'Overseerr peuvent différer un peu.
        if (cree is not None and cree >= debut - 120) or (cree is None and d.get("status") != 3):
            return {"ok": True, "message": "", "apres_delai": True}
    return {"ok": False, "incertaine": True, "message": MSG_DEMANDE_INCERTAINE}


def envoyer_demande(type_, id_, versions):
    resultats = {}
    for is4k in {"hd": [False], "uhd": [True]}.get(versions, [False, True]):
        nom = "uhd" if is4k else "hd"
        corps = {"mediaType": type_, "mediaId": id_, "is4k": is4k}
        if type_ == "tv":
            corps["seasons"] = "all"
        debut = time.time()
        try:
            r = overseerr_appel("POST", "/request", corps, delai=charger()["overseerr_demande_delai_s"])
        except requests.exceptions.ReadTimeout as e:
            resultats[nom] = demande_apres_expiration(type_, id_, is4k, debut, e)
            continue
        except Exception as e:
            resultats[nom] = {"ok": False, "message": erreurs.message(e, 'Demande impossible à Overseerr.')}
            continue
        if r.status_code in (200, 201):
            resultats[nom] = {"ok": True, "message": ""}
        else:
            try:
                msg = r.json().get("message") or ""
            except Exception:
                msg = ""
            resultats[nom] = {"ok": False, "message": erreurs.message("HTTP %d : %s" % (r.status_code, r.text), "Demande refusée par Overseerr.")}
    bibliotheque.vider()
    if any(x.get("ok") for x in resultats.values()):
        invalider_demandes_recentes()
    return resultats


def identifier_ba(titre):
    q = titre_du_film(titre)
    an = re.search(r"\((\d{4})\)", titre)
    if not q or not lire_secrets().get("tmdb"):
        return None
    res = [r for r in tmdb_get("/search/multi", query=q, include_adult="false")["results"] if r.get("media_type") in ("movie", "tv")]
    meilleur, score = None, 0
    for r in res[:8]:
        nom = r.get("title") or r.get("name") or ""
        date = r.get("release_date") or r.get("first_air_date") or ""
        sc = difflib.SequenceMatcher(None, norm(q), norm(nom)).ratio() + (0.2 if an and date[:4] == an.group(1) else 0) + min(0.1, (r.get("popularity") or 0) / 1000)
        if sc > score:
            meilleur, score = r, sc
    if not meilleur or score < 0.5:
        return None
    type_ = meilleur["media_type"]
    f = details_complets(type_, meilleur["id"])
    meta = meta_film(f) if type_ == "movie" else meta_serie(f)
    yt = next((x["id"] for x in rss_chaine() if norm(x["titre"]) == norm(titre)), None)
    base_ov = base_overseerr()
    liens = {"youtube": "https://www.youtube.com/watch?v=" + yt if yt else "https://www.youtube.com/results?search_query=" + requests.utils.quote(titre),
             "google": "https://www.google.com/search?q=" + requests.utils.quote(meta["titre"] + " " + (meta.get("annee") or "") + (" série" if type_ == "tv" else " film")),
             "imdb": meta["liens"].get("imdb"), "tmdb": meta["liens"].get("tmdb")}
    if charger()["overseerr_actif"] and bibliotheque.ov_cle():
        liens["overseerr"] = "%s/%s/%d" % (base_ov, "tv" if type_ == "tv" else "movie", meilleur["id"])
    out = {"type": type_, "id": meilleur["id"], "titre": meta["titre"], "annee": meta.get("annee"), "affiche": meta.get("affiche"),
           "note": meta.get("note"), "notes": notes_titre(type_, meilleur["id"], meta), "liens": liens, "statut": None, "demande": None}
    try:
        st = bibliotheque.statut(type_, meilleur["id"], (f.get("external_ids") or {}).get("tvdb_id"), False)
        out["statut"] = st["global"] if st else None
    except Exception:
        st = None
    cfg = charger()
    if (cfg["demande_auto_ba"] and etat["mode"] == "reel" and type_ == "movie" and st and st["global"] == "absent"
            and not st.get("incomplet") and bibliotheque.ov_cle()):
        if deja_demande("movie", meilleur["id"]):
            out["demande"] = {"ok": True, "message": "déjà demandé", "existante": True}
        else:
            versions = cfg["demande_auto_versions"]
            rs = envoyer_demande(type_, meilleur["id"], versions)
            ok = any(v["ok"] for v in rs.values())
            out["demande"] = {"ok": ok, "message": "" if ok else "; ".join(v["message"] for v in rs.values())[:120]}
            if ok:
                noter_demande("movie", meilleur["id"], versions)
    return out


def film_de_trailer(titre):
    c = cache_ba.get(titre, "inconnu")
    if c == "inconnu":
        cache_ba[titre] = False

        def go():
            try:
                cache_ba[titre] = identifier_ba(titre)
            except Exception:
                cache_ba[titre] = None
        threading.Thread(target=go, daemon=True).start()
        return None
    return c or None


def ba_courante():
    """La bande annonce lancée depuis KamCiné, tant que le bloc doit rester sur l'accueil : pendant qu'elle joue (l'Apple TV est sur YouTube)
    puis ba_home_min minutes après la dernière fois où on l'y a vue."""
    d = BA_LANCEE["d"]
    if not d:
        return None
    now = time.time()
    f = cache_film["data"] or {}
    a = f.get("ailleurs") or {}
    joue = "youtube" in (a.get("app") or "").lower() and a.get("etat") in ("Playing", "Paused")
    if not joue:
        return None
    if joue:
        d["vu"] = now
    if now - max(d["vu"], d["t"]) > charger()["ba_home_min"] * 60:
        BA_LANCEE["d"] = None
        return None
    statut = None
    try:
        st = bibliotheque.statut(d["type"], d["id"], None, False)
        statut = st["global"] if st else None
    except Exception:
        pass
    demandee = deja_demande(d["type"], d["id"]) or statut in ("attente", "recherche", "telechargement", "a_venir")
    return {"type": d["type"], "id": d["id"], "titre": d["titre"], "affiche": d.get("affiche"), "chaine": CHAINE_NOM, "en_lecture": joue,
            "depuis": int(now - d["t"]), "statut": statut, "demandee": bool(demandee), "overseerr": bool(bibliotheque.ov_cle())}


@app.post("/bande-annonce/fermer")
def bande_annonce_fermer():
    BA_LANCEE["d"] = None
    return {"ok": True}


@app.get("/status")
def status(request: Request = None):
    PLAYBACK_SNAPSHOT.value = lire_playing()
    try:
        etat_ = status_snapshot()
        fin = etat_.get("fin_seance") if isinstance(etat_, dict) else None
        if fin:   # le cœur de la carte de fin est celui du compte qui regarde (2.6.104)
            uid = id_connecte(request)
            try:
                etat_["fin_seance"] = dict(fin, favori=bool(uid) and favoris.est_favori(uid, fin["type"], fin["id"]))
            except Exception:
                pass
        return etat_
    finally:
        del PLAYBACK_SNAPSHOT.value


def status_snapshot():
    observe_a = time.time()
    lancement = lancement_courant()
    lecture = film()
    lignes = lignes_courantes()
    controller_running = en_cours()
    d = LANCEMENT["d"] or {}
    owned = bool(lancement and lancement.get("confirme") and lancement.get("origine") in ("seance", "reprise"))
    # Lot 2.6.81 : séance KamCiné persistante et lecture observée sont deux choses distinctes. La séance n'occupe la Home comme
    # séance en cours que si son contrôleur tourne ou si son propre média joue. Sinon elle est conservée (seance_conservee) et
    # la Home montre en même temps ce que l'Apple TV lit réellement (YouTube, un autre film, ou rien).
    # Lot 2.6.83 : même média observé et identifié (même épisode pour une série) = séance en lecture, jamais à reprendre en
    # même temps. Le rattachement du contrôleur suit au relevé suivant (lancement_courant, même identité annotée).
    owned_playback = bool(owned and (_film_toujours_en_lecture(d) or (lecture.get("actif") and meme_media(lecture.get("meta"), d))))
    # Lot 2.6.82 : après Reprendre, la séance est de nouveau en cours tout de suite (étape « reprise »), le temps qu'Infuse
    # rouvre le média et que le rattachement reprenne la main. Sans retour du média dans REPRISE_ATTENTE_S, elle redevient conservée.
    reprise = bool(owned and not controller_running and not owned_playback
                   and time.time() - d.get("reprise_demandee", 0) < REPRISE_ATTENTE_S)
    run = controller_running or owned_playback or reprise
    conservee = seance_conservee(d) if owned and not run else None
    # Lot 2.6.91 : les autres séances en suspens ; jamais celle dont le média est en lecture (règle absolue du doublon).
    suspendues = [seance_conservee(x) for x in SUSPENDUES["l"]
                  if not (lecture.get("actif") and meme_media(lecture.get("meta"), x))]
    fin_lue = None if run or conservee else fin_seance_courante()
    fin_fermee = bool(fin_lue and fin_lue.get("fermee"))
    fin = None if fin_fermee else fin_lue
    if owned and not controller_running:
        lignes = (d.get("scenario") or {}).get("lines") or lignes
    depuis = int(time.time() - etat["debut"]) if etat["debut"] and run else None
    # Le type vient de la séance elle même quand elle existe : le relais écrit « Film : » même pour un épisode, et etat["type"]
    # ne survit pas à un redémarrage du service. Sans séance mémorisée, la déduction du journal reste le repli.
    if owned:
        genre = "serie" if d.get("type") == "tv" else "film"
    else:
        genre = etat["type"] or ("serie" if any(l.startswith("Série :") for _, l in lignes) else None)
    detail = analyser(lignes, etat["mode"])
    if run:
        for tr in detail["trailers"]:
            tr["film"] = film_de_trailer(tr["titre"])
    opts = etat.get("options") or options_seance(None, None, genre == "serie")
    etapes = etapes_seance(lignes, detail, genre == "serie", opts["entracte"], opts["ba"], opts.get("explicites")) if run else None
    # Séance conservée : la vue décrit la lecture réelle, sans la rattacher à la séance (un autre film Infuse n'est pas « lié »).
    # Séance terminée : la carte de fin remplace entièrement la séance. Si Infuse montre encore ce même média (fin du générique),
    # il n'est pas présenté comme une nouvelle lecture sous la carte de fin (états exclusifs).
    vue_lecture = dict(lecture, actif=False) if fin_lue and meme_media(lecture.get("meta"), fin_lue) and (fin or fin_fermee) else lecture
    vue = etat_seance.vue(vue_lecture, None if conservee or fin else lancement, run, etat["preparation"], detail)
    if reprise:
        vue["phase"] = vue["etape"] = "reprise"
    journal_chaine("home", {"nature": vue["nature"], "phase": vue["phase"], "en_cours": run, "controleur": controller_running,
                            "seance_conservee": bool(conservee), "fin_seance": bool(fin), "type": genre,
                            "suspendues": resume_suspendues()})
    if etapes and vue["phase"] == "pause":
        for e in etapes:
            if e["cle"] == ("generique" if vue["etape"] == "generique" else "film"):
                e.update(etat="pause", detail="En pause dans Infuse")
    return {"observe_a": observe_a, "lecteur": atvlive.vue_lecteur(lire_playing()), "occupation": occupation_courante(), "lecture": lecture, "seance": vue, "en_cours": run, "controller_running": controller_running, "etapes": etapes, "options": opts if run else None, "contenu": contenu_seance() if run else None, "bande_annonce": ba_courante(), "mode": etat["mode"], "mode_reglage": mode_effectif(), "type": genre, "depuis": depuis, "reprise": relais.actif,
            "preparation": etat["preparation"], "phase": phase_depuis(lignes) if lignes else (0 if etat["preparation"] else None),
            "detail": detail, "lancement": lancement, "seance_conservee": conservee,
            "seances_suspendues": suspendues, "max_suspendues": MAX_SUSPENDUES, "fin_seance": fin,
            "media_termine_ferme": fin_lue if fin_fermee else None}


REPRISE_ATTENTE_S = 60


def meme_media(meta, x):
    """Même film, ou même épisode d'une série."""
    if not meta or not x or meta.get("type") != x.get("type") or meta.get("id") != x.get("id"):
        return False
    return x.get("type") != "tv" or (meta.get("saison"), meta.get("episode")) == (x.get("saison"), x.get("episode"))


def seance_conservee(d):
    """Ce que la carte Séance interrompue affiche : le média de la séance, sa dernière position connue et son jeton de reprise."""
    meta = d.get("meta") or {}
    return {"t": d["t"], "type": d["type"], "id": d["id"], "saison": d.get("saison"), "episode": d.get("episode"),
            "mode": d.get("mode"), "incertaine": bool(d.get("incertaine")), "position": d.get("position") or 0,
            "total": d.get("total") or d.get("total_fichier") or 0, "debut": d["t"],
            "meta": {k: meta.get(k) for k in ("titre", "annee", "affiche", "fond", "saison", "episode", "ep_titre", "duree")}}


# ---------- Fin de séance (lot 2.6.81) ----------
# Une séance finie normalement laisse une carte « Vous avez aimé votre séance ? » sur la Home : ce n'est pas une séance active,
# elle ne bloque aucun lancement. Gardée FIN_SEANCE_DUREE_S, fermable, effacée dès qu'une nouvelle séance ou lecture est lancée.
FIN_SEANCE = os.path.join(BASE, "fin_seance.json")
LIGNES_FIN = ("Fin naturelle du film", "Fin de séance au générique", "Séance terminée")
SUGGESTIONS_FIN = {}    # (type, id, saison, episode) -> (heure, résultat)


def fin_seance_courante():
    try:
        with open(FIN_SEANCE, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or not d.get("t"):
        return None
    return d


def oublier_fin_seance():
    try:
        ecrire_prive(FIN_SEANCE + ".tmp", None)
        os.replace(FIN_SEANCE + ".tmp", FIN_SEANCE)
    except OSError:
        pass


def fermer_fin_seance():
    """Garde un tombstone court pour qu'un lecteur resté sur le média terminé ne réapparaisse pas après fermeture."""
    fin = fin_seance_courante()
    if not fin:
        return
    fin["fermee"] = True
    try:
        ecrire_prive(FIN_SEANCE + ".tmp", fin)
        os.replace(FIN_SEANCE + ".tmp", FIN_SEANCE)
    except OSError:
        pass


def terminer_seance_normalement():
    """Fin normale (vraie fin du fichier, ou générique atteint puis FIN_APRES_GENERIQUE_S) : carte de fin, titre vu, séance oubliée.
    Appelée pour chaque ligne de fin : la première fait tout, les suivantes ne trouvent plus de séance et ne font rien de plus."""
    d = LANCEMENT.get("d")
    if d and d.get("confirme") and d.get("origine") in ("seance", "reprise"):
        meta = d.get("meta") or {}
        fin = {"t": time.time(), "type": d["type"], "id": d["id"], "saison": d.get("saison"), "episode": d.get("episode"),
               "mode": d.get("mode") or etat["mode"],
               "meta": {k: meta.get(k) for k in ("titre", "annee", "affiche", "fond", "saison", "episode", "ep_titre")}}
        try:
            ecrire_prive(FIN_SEANCE + ".tmp", fin)
            os.replace(FIN_SEANCE + ".tmp", FIN_SEANCE)
        except OSError:
            pass
        titre_fin = str(meta.get("titre") or "ce contenu")
        notification_fin = ("Épisode terminé 🎬", "Qu’avez-vous pensé de " + titre_fin + " ?") if d["type"] == "tv" and d.get("episode") else (
            "Séance terminée 🎬", "Qu’avez-vous pensé de " + titre_fin + " ?")
        notifications.signaler("seance:fin:" + str(d.get("t")), "seance_terminee", notification_fin[0], notification_fin[1],
                                {"page": "fiche", "type": d["type"], "id": d["id"], "saison": d.get("saison"), "episode": d.get("episode")})
        journal_chaine("seance", {"evenement": "fin normale", "carte_de_fin": True,
                                  "seance": resume_seance()})
        if fin["mode"] == "reel":
            marquer_fin_vue(d)
    if d:
        oublier_lancement(d, raison="fin normale de la séance")


def marquer_fin_vue(d):
    """Même source de vérité que la coche de la fiche (historique local de suivi.py) ; les contrôleurs l'ont en général déjà fait
    au générique. Trakt n'est pas écrit ici : Infuse y déclare lui même la lecture, une seconde écriture doublerait l'historique."""
    try:
        if not deja_vu(d["type"], d["id"], d.get("saison"), d.get("episode")):
            suivi_vu(type=d["type"], id=d["id"], saison=d.get("saison") or 0, episode=d.get("episode") or 0, vu=1)
    except Exception as e:
        erreurs.message(e, "Marquage vu de fin de séance impossible.")


@app.post("/lancement/fermer")
@depart_exclusif
def lancement_fermer(t: float = 0):
    """t : quitter une séance en suspens précise (bouton Quitter de sa carte) sans toucher à la séance active."""
    if t and retirer_suspendue(t=t):
        journal_chaine("seance", {"evenement": "séance en suspens quittée", "suspendues": resume_suspendues()})
        return {"ok": True}
    d = LANCEMENT.get("d")
    if t and (not d or d.get("t") != t):
        return JSONResponse({"ok": False, "message": "Cette séance en suspens n’existe plus."}, status_code=409)
    relais.arreter()
    tuer_seance()
    etat["preparation"] = False
    oublier_lancement(raison="Fermer la séance")
    return {"ok": True}


@app.post("/lancement/reprendre")
@depart_exclusif
def lancement_reprendre(t: float = 0, remplacer: int = 0):
    """remplacer : confirmé depuis la carte Séance interrompue, la lecture actuelle (YouTube, autre film) cède la place à la séance."""
    s_ = next((x for x in SUSPENDUES["l"] if x.get("t") == t), None) if t else None
    d = s_ or LANCEMENT["d"]
    if not d or not d.get("confirme") or d["t"] != t:
        return JSONResponse({"ok": False, "message": "Aucune séance mémorisée à reprendre."}, status_code=409)
    if en_cours() and not s_:
        return JSONResponse({"ok": False, "message": "Une séance est déjà pilotée."}, status_code=409)
    p = lire_playing(frais=True)
    autre = bool(p and not p.get("veille") and p.get("etat") in ("Playing", "Paused") and not _film_toujours_en_lecture(d, p))
    if autre and not remplacer:
        return JSONResponse({"ok": False, "message": "Un autre contenu semble en lecture. Arrête sa lecture avant de reprendre cette séance."}, status_code=409)
    if s_:
        # Lot 2.6.91 : la séance active (pilotée ou non), si c'en est une autre, part en suspens ; la séance choisie redevient active.
        if en_cours():
            relais.arreter()
            tuer_seance()
            etat["preparation"] = False
        d = restaurer_suspendue(s_, "Reprendre une séance en suspens")
    if autre and p.get("app") not in (None, "", INFUSE_ID):
        atvlive.LIVE.pause()    # l'app externe est mise en pause avant qu'Infuse reprenne l'écran
    if d["origine"] == "lire":
        return lancer(d["type"], d["id"], d.get("saison") or 0, d.get("episode") or 0)
    d["lumieres_reprise"] = True    # le rattachement remettra l'ambiance du film, sans rejouer le scénario
    d["reprise_demandee"] = time.time()    # la Home montre la séance qui reprend, plus la carte Séance en attente
    # Rouvre la lecture en gardant la même séance, le même jeton et le reste du scénario : le rattachement fait la suite.
    url = url_infuse(d["type"], d["id"], d.get("saison"), d.get("episode"))
    ok, message = atv("launch_app=" + url, delai=25)
    if not ok:
        d.pop("reprise_demandee", None)
        d.pop("lumieres_reprise", None)
        return JSONResponse({"ok": False, "message": message}, status_code=502)
    return {"ok": True}



@app.post("/seance/fin/fermer")
def seance_fin_fermer():
    fermer_fin_seance()
    return {"ok": True}


@app.get("/seance/fin/suggestions")
def seance_fin_suggestions(etendu: bool = False):
    """Suite directe en tête, puis recommandations TMDB pertinentes, avec cache séparé pour la vue étendue."""
    fin = fin_seance_courante()
    if not fin:
        return {"ok": True, "resultats": []}
    cle = (fin["type"], fin["id"], fin.get("saison"), fin.get("episode"), bool(etendu))
    c = SUGGESTIONS_FIN.get(cle)
    if c and time.time() - c[0] < 600:
        return c[1]
    out = {"ok": True, "resultats": suggestions_apres(fin, 30 if etendu else 8)}
    if len(SUGGESTIONS_FIN) > 20:
        SUGGESTIONS_FIN.clear()
    SUGGESTIONS_FIN[cle] = (time.time(), out)
    return out


@app.get("/notes-personnelles/{type_}/{tmdb_id}")
def note_personnelle_lire(type_: str, tmdb_id: int, request: Request):
    if type_ not in ("movie", "tv") or tmdb_id <= 0:
        return JSONResponse({"ok": False, "message": "Média invalide."}, status_code=400)
    return {"ok": True, "note": notes_personnelles.lire(id_connecte(request), type_, tmdb_id)}


@app.post("/notes-personnelles")
async def note_personnelle_ecrire(request: Request):
    c = await corps_json(request)
    try:
        valeur = notes_personnelles.enregistrer(id_connecte(request), str(c.get("type") or ""),
                                                 int(c.get("id") or 0), int(c.get("note") or 0))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "message": "Choisis une note entre 1 et 5 étoiles."}, status_code=400)
    except OSError as e:
        erreurs.message(e, "Enregistrement de la note personnelle impossible.")
        return JSONResponse({"ok": False, "message": "La note n’a pas pu être enregistrée."}, status_code=500)
    return {"ok": True, "note": valeur}


def suite_directe(fin):
    """La suite logique : volet suivant déjà sorti de la saga TMDB pour un film, épisode diffusé suivant pour une série.
    Renvoie (carte, exclus) : exclus liste les volets précédents, jamais proposés ensuite comme contenu récent non plus."""
    aujourdhui = planning.local_iso(time.time())[:10]
    if fin["type"] == "movie":
        f = details("movie", fin["id"])
        collection = (f.get("belongs_to_collection") or {}).get("id")
        if not isinstance(collection, int) or collection <= 0:
            return None, set()
        _, membres = _collection_membres(collection)
        ordre = sorted(membres.values(), key=lambda x: (x.get("release_date") or "9999", x["id"]))
        ids = [x["id"] for x in ordre]
        if fin["id"] not in ids:
            return None, set()
        i = ids.index(fin["id"])
        exclus = {("movie", x) for x in ids[:i + 1]}
        suivant = next((x for x in ordre[i + 1:] if (x.get("release_date") or "9999") <= aujourdhui), None)
        if not suivant:
            return None, exclus
        return dict(carte(dict(suivant, media_type="movie")), suite=True, raison="Suite de la saga"), exclus
    if fin["type"] == "tv" and fin.get("saison") and fin.get("episode"):
        episodes = sorted(episodes_diffusees(fin["id"]))
        courant = (fin["saison"], fin["episode"])
        suivant = next((e for e in episodes if e > courant), None)
        if not suivant:
            return None, set()
        c = carte_par_id("tv", fin["id"])
        if not c:
            return None, set()
        return dict(c, saison=suivant[0], episode=suivant[1], suite=True,
                    raison="Épisode suivant : saison %d, épisode %d" % suivant), {("tv", fin["id"])}
    return None, set()


def suggestions_apres(fin, limite=8):
    resultats, exclus = [], {(fin["type"], fin["id"])}
    try:
        suite, avant = suite_directe(fin)
        exclus |= avant
        if suite:
            resultats.append(suite)
            exclus.add((suite["type"], suite["id"]))
    except Exception as e:
        erreurs.message(e, "Suite de la séance introuvable.")
    try:
        # Un lot TMDB cohérent avec le contenu terminé ; aucun appel par affiche. Pour un épisode, les recommandations
        # portent sur sa série après avoir placé l'épisode suivant en tête.
        t = "tv" if fin["type"] == "tv" else "movie"
        recommandations = tmdb_get("/%s/%d/recommendations" % (t, fin["id"]))
        for item in recommandations.get("results") or []:
            typ = item.get("media_type") or t
            ident = int(item.get("id") or 0)
            if typ not in ("movie", "tv") or ident <= 0 or (typ, ident) in exclus:
                continue
            resultats.append(dict(carte(dict(item, media_type=typ)), raison="Recommandé pour vous"))
            exclus.add((typ, ident))
            if len(resultats) >= limite:
                break
    except Exception as e:
        erreurs.message(e, "Recommandations de fin de séance indisponibles.")
    if len(resultats) < limite:
        try:
            t = "tv" if fin["type"] == "tv" else "movie"
            similaires = tmdb_get("/%s/%d/similar" % (t, fin["id"]))
            for item in similaires.get("results") or []:
                typ = item.get("media_type") or t
                ident = int(item.get("id") or 0)
                if typ not in ("movie", "tv") or ident <= 0 or (typ, ident) in exclus:
                    continue
                resultats.append(dict(carte(dict(item, media_type=typ)), raison="Contenu similaire"))
                exclus.add((typ, ident))
                if len(resultats) >= limite:
                    break
        except Exception as e:
            erreurs.message(e, "Contenus similaires indisponibles.")
    return resultats[:limite]


SUGGESTIONS_MAX = 8


@app.get("/appletv/observations")
def observation_diagnostic():
    return atvlive.LIVE.observations.diagnostic()


@app.get("/journal")
def journal():
    technique = erreurs.lire()
    lignes = lignes_courantes()[-400:]
    contenu = [l for _, l in lignes]
    if technique:
        contenu += ["", "Erreurs techniques", ""] + technique
    evenements, sources = journal_ui.evenements(lignes, technique)
    return {"ok": True, "en_cours": en_cours(), "log": contenu, "evenements": evenements, "sources": sources}


@app.post("/log/clear")
def log_effacer():
    if en_cours():
        return JSONResponse({"ok": False, "message": "Une séance est en cours"}, status_code=409)
    open(LOG, "w").close()
    erreurs.effacer()
    etat["lignes"] = []
    return {"ok": True}


# ---------- Film ou série en cours, télécommande ----------
def suivre(data):
    m = data["meta"]
    if m["type"] == "tv" and not (m.get("saison") and m.get("episode")):
        return
    entree = {"type": m["type"], "id": m["id"], "saison": m.get("saison"), "episode": m.get("episode"),
              "titre": m["titre"], "affiche": m.get("affiche"), "fond": m.get("fond"),
              "duree": m.get("duree") or round(data["total"] / 60)}
    if data["pos"] / data["total"] >= charger()["seuil_vu_pct"] / 100.0:
        if not suivi.est_vu(entree["type"], entree["id"], entree["saison"], entree["episode"]):
            suivi.marquer_vu(entree)
    else:
        suivi.enregistrer_progression(dict(entree, pos=data["pos"], total=data["total"]))


@app.get("/film")
def film():
    maintenant = time.time()
    if maintenant - cache_film["t"] < 2 and cache_film["data"] is not None:     # 2.6, lot J, point 3 : détection plus vive (2,5 s avant)
        return cache_film["data"]
    if not verrou_film.acquire(blocking=False):
        return cache_film["data"] or {"actif": False, "meta": None}
    try:
        p = lire_playing()
        data = {"actif": False, "connecte": p is not None, "etat": None, "titre": "", "pos": 0, "total": 0,
                "type": None, "serie": None, "meta": None, "manuel": False,
                "app": p.get("app") if p else None, "app_nom": p.get("app_nom") if p else None,
                "infuse_ouvert": bool(p and not p.get("veille") and (p.get("app_active") == INFUSE_ID or
                                      (p.get("app") == INFUSE_ID and etat_seance.media_reel(p)))),
                "veille": bool(p and p.get("veille"))}
        est_infuse = not p or p.get("app") in (None, "", INFUSE_ID)
        # 2.6, lot J, point 4 : l'état de lecture (Playing, Paused...) peut rester un instant sur son ancienne valeur juste après
        # que l'Apple TV s'est mise en veille. Rien ne joue vraiment TV éteinte, quoi que dise cet état.
        lie = bool(p and _film_toujours_en_lecture(LANCEMENT["d"], p))
        if p and etat_seance.media_reel(p) and est_infuse and not (p.get("total", 0) > 0 and p.get("pos", 0) >= p["total"]) and (p.get("media") == "Video" or p.get("app") == INFUSE_ID or lie):
            data.update(actif=True, etat=p["etat"], titre=p["titre"], pos=p["pos"], total=p["total"])
        # 2.4 : tout ce qui joue ailleurs (Netflix, YouTube, Disney+, musique...) avec ce que pyatv en dit. Jamais suivi, jamais associé à un titre TMDB.
        data["ailleurs"] = contenu_ailleurs(p) if p and not data["actif"] and not data["veille"] else None
        # Les lectures des apps de streaming reprennent le résolveur TMDB asynchrone
        # déjà utilisé par Infuse : clé stable par titre/épisode, cache positif/négatif,
        # une recherche en arrière-plan et aucun appel ajouté à chaque cycle pyatv.
        if data["ailleurs"] and p.get("media") in ("Video", "TV") and data["ailleurs"].get("titre"):
            info_externe = lecture_infuse.identite_observee(p)
            if info_externe.get("nom"):
                meta_externe, identification = meta_titre_rapide(info_externe, data["ailleurs"].get("total") or 0)
                if meta_externe:
                    data["ailleurs"].update(meta=meta_externe, type=meta_externe.get("type"),
                                             progression=(data["ailleurs"].get("pos", 0) / data["ailleurs"]["total"]
                                                          if data["ailleurs"].get("total") else None))
                elif identification:
                    data["ailleurs"]["identification"] = True
        # Lot 2.6.90 : le contexte de la Home (règle unique etat_seance.classer), et la dernière application signalée.
        classe, raison_classe = etat_seance.classer(p)
        try:
            app_contexte = atvlive.LIVE.observations.contexte.get("app")
        except Exception:
            app_contexte = None
        app_contexte = app_contexte if app_contexte is not None else (p.get("app") if p else None)
        data["contexte"] = {"classe": classe, "raison": raison_classe, "app": app_contexte or None,
                            "app_nom": (NOMS_APPS.get(app_contexte) or ("Infuse" if app_contexte == INFUSE_ID else None)
                                        or (app_contexte.split(".")[-1] if app_contexte else None))}
        if p:
            journal_chaine("classement", {
                "app": p.get("app"), "etat": p.get("etat"), "titre": p.get("titre"), "duree": p.get("total"),
                "position": p.get("pos"), "media": p.get("media"),
                "commandes": {k: (p.get("fonctions") or {}).get(k) for k in ("play", "pause", "play_pause", "back", "forward")},
                "classe": classe, "raison": raison_classe, "contexte_app": app_contexte,
                "affichee": "lecture Infuse" if data["actif"] else ("lecture externe" if data["ailleurs"] else "rien en lecture"),
                "origine_identite": p.get("complement") or ("identité TMDB" if p.get("identite") else ("titre publié" if (p.get("titre") or "").strip() else None)),
                "seance": resume_seance(), "suspendues": resume_suspendues()})
        if p and p.get("app") not in (None, "", INFUSE_ID):
            # Lot 2.6.87 : pourquoi une lecture hors Infuse est affichée ou écartée (fraîcheur et génération du registre).
            generation = p.get("generation") or 0
            journal_chaine("externe", {
                "app": p.get("app"), "etat": p.get("etat"), "titre": p.get("titre"), "affichee": bool(data["ailleurs"]),
                "perimee": bool(p.get("stale")), "raison": {"superseded": "pause antérieure à une lecture plus récente",
                                                            "unchanged_pause": "pause sans progression depuis trop longtemps",
                                                            "invalidated": "antérieure au dernier arrêt"}.get(p.get("stale_reason") or "", "fraîche" if data["ailleurs"] else "pas une lecture réelle"),
                "progression_vue": bool(p.get("progression")), "depuis_progression_s": round(p.get("depuis") or 0),
                "derniere_lecture_il_y_a_s": round(time.time() - generation) if generation else None},
                hors_signature=("depuis_progression_s", "derniere_lecture_il_y_a_s"))
        # Lot 2.6.83 : même analyse que le rattachement de la séance. Le type n'est posé que s'il est prouvé (série) ou résolu
        # par TMDB plus bas ; tant qu'il reste inconnu, la Home montre une lecture neutre, jamais un film supposé.
        info = lecture_infuse.identite_observee(p) if data["actif"] and data["titre"] else None
        if info and info["type"] == "tv":
            data["type"] = "tv"
            if info["episode_certain"]:
                data["serie"] = {"nom": info["nom"], "saison": info["saison"], "episode": info["episode"],
                                 "ep_titre": info["ep_titre"]}
        manuel = film_choisi()
        if manuel:
            cle_live = info["nom"] if info else ""
            if manuel.get("live") and cle_live and norm(manuel["live"]) != norm(cle_live):
                oublier_film_choisi()
                manuel = None
            elif cle_live and not manuel.get("live"):
                manuel["live"] = cle_live
                ecrire_prive(FILM_CHOISI, manuel)
        if manuel and not lie:
            meta = meta_par_id(manuel["type"], manuel["id"], info and info["saison"], info and info["episode"])
            data.update(meta=meta, manuel=True)
            if meta:
                data["type"] = meta["type"]
        elif data["actif"] and LANCEMENT["d"] and LANCEMENT["d"].get("meta") and lie:
            # La correspondance du média a été vérifiée : conserver sa fiche plutôt que refaire une recherche TMDB.
            dl = LANCEMENT["d"]
            data.update(meta=dl["meta"], type=dl["meta"]["type"])
            if dl["meta"]["type"] == "tv" and dl.get("saison") and dl.get("episode"):
                data["serie"] = {"nom": dl["meta"].get("titre") or "", "saison": dl["saison"], "episode": dl["episode"],
                                 "ep_titre": dl["meta"].get("ep_titre") or ""}
        elif data["actif"] and info:
            data["meta"], data["identification"] = meta_titre_rapide(info, data["total"])
        if data["meta"] and data["meta"].get("type"):
            data["type"] = data["meta"]["type"]
        if p and (data["actif"] or p.get("app") in (None, "", INFUSE_ID)):
            if p.get("infuse_ecartee"):
                raison = "pause écartée : " + p["infuse_ecartee"]
            elif not data["actif"]:
                raison = "pas de lecture Infuse active"
            elif not data["titre"]:
                raison = "titre absent : identification en attente, lecture de complément par connexion neuve"
            elif lie:
                raison = "séance reconnue : identité de la séance"
            elif data["meta"]:
                raison = "identité résolue"
            elif data.get("identification"):
                raison = "recherche TMDB en cours"
            else:
                raison = "recherche TMDB sans résultat récent, nouvel essai au prochain passage"
            journal_chaine("identification", {
                "app": p.get("app"), "etat": p.get("etat"), "titre": p.get("titre"), "duree": p.get("total"),
                "position": p.get("pos"), "complement": p.get("complement"),
                "commandes": {k: (p.get("fonctions") or {}).get(k) for k in ("play", "pause", "play_pause")},
                "position_connue": p.get("position_connue"),
                "serie": (info or {}).get("nom") if info and info.get("type") == "tv" else None,
                "saison": (info or {}).get("saison"), "episode": (info or {}).get("episode"),
                "type": data["type"], "identite": p.get("identite"),
                "media": {"type": data["meta"]["type"], "id": data["meta"]["id"], "saison": data["meta"].get("saison"),
                          "episode": data["meta"].get("episode")} if data["meta"] else None,
                "seance": resume_seance(), "raison": raison})
        if data["actif"] and data["meta"] and data["total"]:
            try:
                suivre(data)
            except Exception:
                pass
        cache_film.update(t=time.time(), data=data)
        return data
    finally:
        verrou_film.release()


lire_film = film       # stop() a un paramètre nommé film : on passe par cet alias pour appeler la fonction


@app.get("/film/chercher")
def film_chercher(q: str = ""):
    if not q.strip():
        return {"ok": True, "resultats": []}
    try:
        res = tmdb_get("/search/multi", query=q.strip(), include_adult="false")["results"]
    except PermissionError as e:
        return JSONResponse({"ok": False, "message": erreurs.message(e, 'Recherche impossible.')}, status_code=400)
    except Exception as e:
        return JSONResponse({"ok": False, "message": erreurs.message(e, 'Recherche impossible.')}, status_code=400)
    res = [r for r in res if r.get("media_type") in ("movie", "tv")]
    res.sort(key=lambda r: r.get("popularity") or 0, reverse=True)
    return {"ok": True, "resultats": [
        {"id": r["id"], "type": r["media_type"], "titre": r.get("title") or r.get("name") or "",
         "annee": (r.get("release_date") or r.get("first_air_date") or "")[:4],
         "affiche": image(r.get("poster_path"), "w92")} for r in res[:8]]}


@app.post("/film/choisir")
def film_choisir(id: int = 0, type: str = "movie"):
    type = "tv" if type == "tv" else "movie"
    try:
        details(type, id)
    except Exception as e:
        return JSONResponse({"ok": False, "message": erreurs.message(e, 'Fiche indisponible.')}, status_code=400)
    live = ""
    p = lire_playing()
    if p and p["titre"]:
        live = lecture_infuse.identite_observee(p)["nom"]
    ecrire_prive(FILM_CHOISI, {"t": time.time(), "type": type, "id": id, "live": live})
    cache_film["t"] = 0
    return {"ok": True}


@app.post("/film/dissocier")
def film_dissocier():
    oublier_film_choisi()
    cache_film["t"] = 0
    return {"ok": True}


@app.get("/tmdb/etat")
def tmdb_etat():
    return {"configuree": bool(lire_secrets().get("tmdb"))}


@app.post("/tmdb/cle")
async def tmdb_cle(request: Request):
    cle = str((await corps_json(request)).get("cle", "")).strip()
    if len(cle) < 20:
        return JSONResponse({"ok": False, "message": "Cette clé semble incomplète"}, status_code=400)
    ancienne = lire_secrets()
    ecrire_prive(SECRETS, dict(ancienne, tmdb=cle))
    try:
        tmdb_get("/configuration")
    except PermissionError as e:
        erreurs.message(e, "Identifiants refusés par TMDB.", confidentiels=(cle,))
        ecrire_prive(SECRETS, ancienne)
        return JSONResponse({"ok": False, "message": "TMDB refuse cette clé"}, status_code=400)
    except Exception as e:
        ecrire_prive(SECRETS, ancienne)
        return JSONResponse({"ok": False, "message": erreurs.message(e, 'Connexion impossible à TMDB.', confidentiels=(cle,))}, status_code=400)
    cache_tmdb.clear()
    return {"ok": True}


@app.post("/tmdb/retirer")
def tmdb_retirer():
    s = lire_secrets()
    s.pop("tmdb", None)
    ecrire_prive(SECRETS, s)
    cache_tmdb.clear()
    return {"ok": True}


@app.post("/telecommande")
def telecommande(cmd: str = "", attendu: str = ""):
    if cmd not in COMMANDES:
        return JSONResponse({"ok": False, "message": "Commande inconnue"}, status_code=400)
    if not VERROU_LECTEUR.acquire(blocking=False):
        return JSONResponse({"ok": False, "message": "Une commande est déjà en cours."}, status_code=409)
    try:
        resultat = atvlive.LIVE.commander(cmd, attendu)
        if resultat is not None:
            return resultat
        # Repli seulement si aucune commande n’a pu être envoyée par la connexion continue.
        p = lire_playing_cli(delai=5)
        lecteur = atvlive.vue_lecteur(p)
        if not lecteur["commandes"].get(cmd) or (attendu and attendu != lecteur["jeton"]):
            return JSONResponse({"ok": False, "message": "Le média n’est plus disponible. Vérifie le lecteur.", "lecteur": lecteur}, status_code=409)
        commande = COMMANDES[cmd]
        if cmd == "play_pause":
            commande = "pause" if p["etat"] == "Playing" else "play"
        ok, sortie = atv(commande, delai=10)
        CLI.update(t=0, derniere=None)
        return {"ok": ok, "message": "" if ok else (sortie[-120:] or "Commande refusée")}
    finally:
        cache_film["t"] = 0
        VERROU_LECTEUR.release()


# ---------- Appareils ----------
@app.get("/appareils/etat")
def appareils_etat():
    res = {"appletv": {"etat": "injoignable"}, "hue": {"ok": False, "lumieres": 0, "message": ""}}
    if en_cours():
        res["appletv"] = {"etat": "seance"}
    else:
        l = atvlive.LIVE.lire()
        if l is not None:
            res["appletv"] = {"etat": "veille" if l.get("veille") else "allumee"}
        else:
            ok, sortie = atv("power_state", delai=10)
            if ok:
                res["appletv"] = {"etat": "veille" if "off" in sortie.lower() else "allumee"}
    if not integrations.hue():
        res["hue"] = {"ok": False, "lumieres": 0, "message": "Pont Hue non configuré.", "configure": False}
        return res
    try:
        from lumieres import Hue
        res["hue"] = {"ok": True, "lumieres": len(Hue().ids), "message": ""}
    except Exception as e:
        res["hue"]["message"] = erreurs.message(e, "Le pont Hue ne répond pas.")
    return res


@app.get("/appareils/synology")
async def appareils_synology():
    """État résumé du NAS, obtenu côté serveur depuis les WebAPI DSM. Aucun secret n'est renvoyé."""
    return await run_in_threadpool(synology.lire)


@app.get("/appareils/synology/configuration")
def appareils_synology_configuration():
    """Valeurs non secrètes du formulaire DSM ; le mot de passe n'est jamais renvoyé."""
    c = integrations.synology()
    return {"ok": True, "configure": bool(c.get("url") and c.get("utilisateur") and c.get("mot_de_passe")),
            "url": c.get("url", ""), "nom": c.get("nom", "NAS Synology"),
            "utilisateur": c.get("utilisateur", ""), "verifier_tls": c.get("verifier_tls", True),
            "mot_de_passe_configure": bool(c.get("mot_de_passe"))}


async def _configuration_synology_depuis_request(request: Request):
    corps = await corps_json(request)
    url = str(corps.get("url", "")).strip().rstrip("/")
    utilisateur = str(corps.get("utilisateur", "")).strip()
    nom = str(corps.get("nom", "")).strip() or "NAS Synology"
    mot_de_passe = str(corps.get("mot_de_passe", ""))
    verifier_tls = corps.get("verifier_tls") is not False
    ancienne = integrations.synology()
    if not mot_de_passe:
        mot_de_passe = ancienne.get("mot_de_passe", "")
    from urllib.parse import urlsplit
    u = urlsplit(url)
    if (u.scheme not in ("http", "https") or not u.hostname or u.username or u.password
            or u.query or u.fragment or not utilisateur or not mot_de_passe):
        raise ValueError("Adresse DSM, identifiant et mot de passe sont requis.")
    if len(url) > 300 or len(nom) > 80 or len(utilisateur) > 120:
        raise ValueError("Une valeur de configuration est trop longue.")
    return {"url": url, "utilisateur": utilisateur, "mot_de_passe": mot_de_passe,
            "nom": nom, "verifier_tls": verifier_tls}


@app.post("/appareils/synology/tester")
async def appareils_synology_tester(request: Request):
    try:
        conf = await _configuration_synology_depuis_request(request)
    except ValueError as e:
        return JSONResponse({"ok": False, "message": str(e)}, status_code=400)
    resultat = await run_in_threadpool(synology.lire, True, conf)
    if not resultat.get("en_ligne"):
        return JSONResponse({"ok": False, "message": "Connexion DSM impossible. Vérifie l’adresse, les identifiants et l’accès HTTPS."}, status_code=400)
    return {"ok": True, "message": "Connexion DSM réussie.", "etat": resultat}


@app.post("/appareils/synology/configuration")
async def appareils_synology_enregistrer(request: Request):
    """Teste DSM avant d'enregistrer les identifiants dans le stockage privé /config/secrets.json."""
    try:
        conf = await _configuration_synology_depuis_request(request)
    except ValueError as e:
        return JSONResponse({"ok": False, "message": str(e)}, status_code=400)
    resultat = await run_in_threadpool(synology.lire, True, conf)
    if not resultat.get("en_ligne"):
        return JSONResponse({"ok": False, "message": "Connexion DSM impossible. Vérifie l’adresse, les identifiants et l’accès HTTPS."}, status_code=400)
    integrations.enregistrer_synology(conf["url"], conf["utilisateur"], conf["mot_de_passe"], conf["nom"], conf["verifier_tls"])
    synology.invalider()
    resultat = await run_in_threadpool(synology.lire, True)
    return {"ok": True, "message": "NAS connecté et configuration enregistrée.", "etat": resultat}


@app.post("/appletv/infuse")
def appletv_infuse():
    """Ouvre Infuse sur la TV (réveille l'Apple TV si besoin). Ne choisit aucun titre : pyatv ne sait ni lire ni fixer la page affichée."""
    if en_cours():
        return JSONResponse({"ok": False, "message": "Une séance est en cours"}, status_code=409)
    reveiller()
    ok, sortie = atv("launch_app=" + INFUSE_ID, delai=15)
    cache_film["t"] = 0
    return {"ok": ok, "message": "" if ok else "L'Apple TV refuse la commande : " + sortie[-100:]}


@app.post("/appareils/appletv")
def appletv(action: str = "off", identifiant: str = ""):
    if action not in ("on", "off"):
        return JSONResponse({"ok": False, "message": "Commande Apple TV invalide."}, status_code=400)
    if identifiant and not any(x.get("identifiant") == identifiant for x in integrations.appletvs()):
        return JSONResponse({"ok": False, "message": "Apple TV inconnue."}, status_code=404)
    commande = "turn_on" if action == "on" else "turn_off"
    ok, sortie = atv(commande, identifiant=identifiant) if identifiant else atv(commande)
    return {"ok": ok, "message": "" if ok else (sortie[-160:] or "Commande refusée")}


@app.post("/appareils/lumieres")
def lumieres(action: str = "on"):
    try:
        lumieres_tv("on" if action == "on" else "off")
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "message": erreurs.message(e, 'Le pont Hue ne répond pas.')}


@app.post("/appareils/lumieres/scene")
def lumieres_scene(nom: str = ""):
    if nom not in ("debut", "trailers", "entracte", "generique", "noir"):
        return JSONResponse({"ok": False, "message": "Scène inconnue"}, status_code=400)
    if not charger()["lumieres_actives"]:
        return {"ok": False, "message": "Active d'abord « Lumières pilotées »"}
    if not integrations.hue():
        return {"ok": False, "message": "Pont Hue non configuré."}
    if not any(integrations.hue_roles().values()):
        return {"ok": False, "message": "Associez d’abord vos lumières aux rôles KamCiné (Appareils, Philips Hue)."}
    try:
        from lumieres import Hue
        getattr(Hue(), nom)(2)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "message": erreurs.message(e, 'Le pont Hue ne répond pas.')}


# ---------- Configuration des appareils depuis KamCiné (2.6.107, administrateurs : préfixe /appareils/) ----------
# Apple TV (appairage_atv.py) et pont Hue (appairage_hue.py). Une situation normale (aucun appareil, mauvais code, délai,
# bouton non pressé) répond ok faux avec un message, jamais une erreur serveur. Aucun secret ne sort.
def resultat_appareil(fonction, *args):
    try:
        r = fonction(*args)
        return dict({"ok": True}, **r) if isinstance(r, dict) else {"ok": True, "resultat": r}
    except (appairage_atv.Echec, appairage_hue.Echec) as e:
        return {"ok": False, "message": str(e)}
    except Exception as e:
        return {"ok": False, "message": erreurs.message(e, "Opération impossible pour le moment.")}


@app.get("/appareils/configuration")
def appareils_configuration(request: Request):
    """État public des appareils : configuré, nom, lumières associées ; ampli : adresse et activation (non secrets)."""
    e = integrations.etat_public()
    uid = id_connecte(request)
    prefere = comptes.lire_preference(uid, "appletv") if uid else None
    ids = {a["identifiant"] for a in e["appletv"]["appareils"]}
    prefere = prefere if prefere in ids else next((a["identifiant"] for a in e["appletv"]["appareils"] if a["active"]), "")
    e["appletv"]["preference"] = prefere
    e["appletv"]["nom"] = next((a["nom"] for a in e["appletv"]["appareils"] if a["identifiant"] == prefere), e["appletv"].get("nom", ""))
    for appareil in e["appletv"]["appareils"]:
        appareil["active"] = appareil["identifiant"] == prefere
    e["appletv"]["connectee"] = etat_appletv()["connectee"]
    cfg = charger()
    e["denon"] = {"configuree": bool(denon.actif()), "actif": bool(cfg["denon_actif"]), "ip": denon.ip()}
    return dict({"ok": True}, **e)


@app.post("/appareils/appletv/rechercher")
def appletv_rechercher():
    r = resultat_appareil(appairage_atv.rechercher)
    if r["ok"]:
        r = {"ok": True, "appareils": r["resultat"]}
    return r


@app.post("/appareils/appletv/appairer")
async def appletv_appairer(request: Request):
    corps = await corps_json(request)
    return await run_in_threadpool(resultat_appareil, appairage_atv.appairer, str(corps.get("identifiant", "")))


@app.post("/appareils/appletv/pin")
async def appletv_pin(request: Request):
    corps = await corps_json(request)
    r = await run_in_threadpool(resultat_appareil, appairage_atv.saisir_pin, str(corps.get("session", "")), str(corps.get("pin", "")))
    if r.get("etape") == "fini":
        atvlive.LIVE.reconnecter()
    return r


@app.post("/appareils/appletv/annuler")
def appletv_annuler():
    return resultat_appareil(appairage_atv.annuler)


@app.post("/appareils/appletv/choisir")
async def appletv_choisir(request: Request):
    corps = await corps_json(request)
    r = await run_in_threadpool(resultat_appareil, appairage_atv.choisir, str(corps.get("identifiant", "")))
    if r.get("ok"):
        atvlive.LIVE.reconnecter()
    return r


@app.post("/appareils/appletv/tester")
def appletv_tester():
    return resultat_appareil(appairage_atv.tester)


@app.post("/appareils/appletv/activer")
async def appletv_activer(request: Request):
    corps = await corps_json(request)
    identifiant = str(corps.get("identifiant", ""))
    uid = id_connecte(request)
    if not uid:
        return JSONResponse({"ok": False, "message": "Connecte-toi pour enregistrer cette préférence."}, status_code=401)
    if not any(a.get("identifiant") == identifiant for a in integrations.appletvs()):
        return JSONResponse({"ok": False, "message": "Cette Apple TV n’est pas configurée."}, status_code=400)
    comptes.ecrire_preference(uid, "appletv", identifiant)
    return {"ok": True, "preference": identifiant}


@app.post("/appareils/appletv/renommer")
async def appletv_renommer(request: Request):
    corps = await corps_json(request)
    try:
        integrations.renommer_appletv(str(corps.get("identifiant", "")), str(corps.get("nom", "")))
    except KeyError:
        return {"ok": False, "message": "Cette Apple TV n’est pas configurée."}
    return {"ok": True, "appareils": integrations.appletvs()}


@app.post("/appareils/appletv/retirer")
async def appletv_retirer(request: Request):
    """Oublie une Apple TV, sur confirmation explicite dans l'interface. La suivante devient active si besoin."""
    corps = await corps_json(request)
    integrations.retirer_appletv(str(corps.get("identifiant", "")))
    atvlive.LIVE.reconnecter()
    return {"ok": True, "appareils": integrations.appletvs()}


@app.post("/appareils/hue/rechercher")
def hue_rechercher():
    return {"ok": True, "ponts": appairage_hue.rechercher()}


@app.post("/appareils/hue/associer")
async def hue_associer(request: Request):
    corps = await corps_json(request)
    return await run_in_threadpool(resultat_appareil, appairage_hue.associer, str(corps.get("adresse", "")))


@app.get("/appareils/hue/lumieres")
def hue_lumieres():
    r = resultat_appareil(appairage_hue.lumieres)
    return {"ok": True, "lumieres": r["resultat"]} if r["ok"] else r


@app.post("/appareils/hue/roles")
async def hue_roles(request: Request):
    corps = await corps_json(request)
    return await run_in_threadpool(resultat_appareil, appairage_hue.enregistrer_roles, corps.get("roles") if isinstance(corps.get("roles"), dict) else {})


@app.post("/appareils/hue/tester")
def hue_tester():
    return resultat_appareil(appairage_hue.tester)


@app.post("/appareils/denon/tester")
def denon_tester():
    """Test de l'ampli configuré : « Connexion réussie » ou une erreur compréhensible."""
    if not denon.ip():
        return {"ok": False, "message": "Indiquez d’abord l’adresse de l’ampli."}
    try:
        e = denon.etat()
    except Exception as ex:
        return {"ok": False, "message": erreurs.message(ex, "L’ampli ne répond pas.")}
    if e.get("joignable"):
        return {"ok": True, "message": "Connexion réussie" + (" : ampli allumé." if e.get("allume") else " : ampli en veille.")}
    return {"ok": False, "message": "L’ampli ne répond pas à cette adresse. Vérifiez qu’il est branché et sur le même réseau."}


@app.get("/appareils/denon")
def denon_etat():
    c = charger()
    return {"ok": True, "actif": bool(c["denon_actif"] and denon.ip()), "ip": c["denon_ip"],
            "etat": denon.etat() if denon.actif() else {"joignable": False, "message": "Ampli désactivé"}}


@app.post("/appareils/denon")
def denon_action(action: str = "", db: float = -40, valeur: float = 0):
    if not denon.actif():
        return JSONResponse({"ok": False, "message": "Ampli désactivé ou adresse absente"}, status_code=400)
    try:
        if action == "on":
            denon.allumer()
            time.sleep(1.5)
        elif action == "off":
            denon.veille()
        elif action == "mute":
            denon.muet(True)
        elif action == "unmute":
            denon.muet(False)
        elif action == "niveau":
            return {"ok": True, "niveau": denon.regler_niveau(valeur), "limite": denon.limite()}
        elif action == "volume":
            denon.regler_volume(db)
            return {"ok": True, "db": max(denon.DB_MIN, min(denon.DB_MAX, round(db * 2) / 2))}
        else:
            return JSONResponse({"ok": False, "message": "Action inconnue"}, status_code=400)
    except Exception as e:
        return {"ok": False, "message": erreurs.message(e, 'L’ampli ne répond pas.')}
    return {"ok": True, "etat": denon.etat()}


@app.get("/appareils/denon/diagnostic")
def denon_diagnostic():
    return {"ok": True, "ip": denon.ip(), "essais": denon.diagnostic()}


# ---------- Catalogue ----------
def normaliser_recherche_catalogue(texte):
    texte = unicodedata.normalize("NFKD", str(texte or "").lower())
    texte = "".join(c for c in texte if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^\w]+", " ", texte)).strip()


def score_texte_recherche_catalogue(requete, texte):
    q = normaliser_recherche_catalogue(requete)
    t = normaliser_recherche_catalogue(texte)
    if not q or not t:
        return 0
    if q == t:
        return 1000
    if t.startswith(q):
        return 850 - min(40, len(t) - len(q))
    qt, tt = q.split(), t.split()
    if all(any(n.startswith(x) for n in tt) for x in qt):
        return 780
    if all(x in tt for x in qt):
        return 740
    return round(difflib.SequenceMatcher(None, q.replace(" ", ""), t.replace(" ", "")).ratio() * 500)


def classer_recherche_catalogue(requete, resultats):
    personnes, medias = [], []
    for entree in resultats:
        type_ = entree.get("media_type")
        if type_ == "person":
            nom = entree.get("name") or ""
            pertinence = score_texte_recherche_catalogue(requete, nom)
            if pertinence < 300:
                continue
            popularite = max(0, float(entree.get("popularity") or 0))
            connus = entree.get("known_for") or []
            photo = entree.get("profile_path")
            if not photo and pertinence < 850 and popularite < 12:
                continue
            if popularite < 1 and not connus and pertinence < 1000:
                continue
            score = pertinence + min(45, math.log1p(popularite) * 10) + (12 if photo else 0) + (8 if connus else 0)
            personnes.append((score, entree))
        elif type_ in ("movie", "tv"):
            titre = entree.get("title") or entree.get("name") or ""
            original = entree.get("original_title") or entree.get("original_name") or ""
            pertinence = max(score_texte_recherche_catalogue(requete, titre), score_texte_recherche_catalogue(requete, original))
            popularite = max(0, float(entree.get("popularity") or 0))
            medias.append((pertinence * 100 + min(4500, math.log1p(popularite) * 100), entree))
    personnes.sort(key=lambda x: x[0], reverse=True)
    medias.sort(key=lambda x: x[0], reverse=True)
    return [x[1] for x in personnes], [x[1] for x in medias]


@app.get("/catalogue/chercher")
def catalogue_chercher(q: str = "", page: int = 1):
    if not q.strip():
        return {"ok": True, "resultats": [], "personnes": [], "page": 1, "pages": 1, "plus": False}
    page = max(1, min(page, 500))
    try:
        data = tmdb_get("/search/multi", query=q.strip(), include_adult="false", page=page)
    except PermissionError as e:
        return JSONResponse({"ok": False, "message": erreurs.message(e, 'Recherche impossible.')}, status_code=400)
    except Exception as e:
        return JSONResponse({"ok": False, "message": erreurs.message(e, 'Recherche impossible.')}, status_code=400)
    personnes_brutes, medias_bruts = classer_recherche_catalogue(q.strip(), data.get("results") or [])
    personnes = [{"id": r["id"], "nom": r.get("name") or "", "photo": image(r.get("profile_path"), "w185"),
                  "metier": metier_personne(r.get("known_for_department"), r.get("gender") or 0),
                  "role": next((x.get("title") or x.get("name") for x in (r.get("known_for") or []) if x.get("title") or x.get("name")), "")}
                 for r in personnes_brutes if r.get("id")]
    medias = [carte(r) for r in medias_bruts]
    return {"ok": True, "resultats": medias, "personnes": personnes, "page": page,
            "pages": min(500, data.get("total_pages") or page), "plus": page < min(500, data.get("total_pages") or page)}


GENRES = {
    "movie": [(28, "Action"), (35, "Comédie"), (18, "Drame"), (878, "Science-fiction"), (27, "Horreur"), (53, "Thriller"),
              (16, "Animation"), (12, "Aventure"), (14, "Fantastique"), (10749, "Romance"), (80, "Crime"), (10751, "Famille")],
    "tv": [(10759, "Action et aventure"), (18, "Drame"), (35, "Comédie"), (10765, "Science-fiction et fantastique"),
           (80, "Crime"), (9648, "Mystère"), (16, "Animation"), (99, "Documentaire"), (10751, "Famille")],
}


def liste_tmdb(cle, chemin, type_defaut, duree, **params):
    c = cache_listes.get(cle)
    if c and time.time() - c[0] < duree:
        return c[1]
    res = tmdb_get(chemin, **params)["results"]
    out = {"ok": True, "resultats": [carte(dict(r, media_type=r.get("media_type") or type_defaut))
                                     for r in res if (r.get("media_type") or type_defaut) in ("movie", "tv")][:18]}
    cache_listes[cle] = (time.time(), out)
    return out


@app.get("/catalogue/tendances")
def catalogue_tendances(type: str = "all", page: int = 1):
    type = type if type in ("movie", "tv") else "all"
    page = max(1, min(page, 500))
    cle = ("tendances", type, page)
    c = cache_listes.get(cle)
    if c and time.time() - c[0] < 3600:
        return c[1]
    try:
        d = tmdb_get("/trending/%s/week" % type, page=page)
        resultats = []
        for x in d.get("results", []):
            media = x.get("media_type") or (type if type != "all" else "")
            if media in ("movie", "tv") and (type == "all" or media == type):
                resultats.append(carte(dict(x, media_type=media)))
        out = {"ok": True, "resultats": resultats[:18],
               "page": page, "pages": min(d.get("total_pages", 1), 500)}
        cache_listes[cle] = (time.time(), out)
        return out
    except Exception as e:
        return JSONResponse({"ok": False, "message": erreurs.message(e, 'Action impossible.')}, status_code=400)


@app.get("/catalogue/a-venir")
def catalogue_a_venir(type: str = "movie", page: int = 1):
    type = "tv" if type == "tv" else "movie"
    page = max(1, min(page, 500))
    cle = ("a-venir", type, page)
    c = cache_listes.get(cle)
    if c and time.time() - c[0] < 3600:
        return c[1]
    try:
        aujourdhui = planning.local_iso(time.time())[:10]
        params = {"page": page, "sort_by": "popularity.desc", "include_adult": "false"}
        if type == "movie":
            # La date principale peut être déjà passée alors que la sortie suisse reste à venir.
            params.update(region="CH", **{"release_date.gte": aujourdhui})
        else:
            # Ne pas écarter les séries annoncées qui n'ont pas encore reçu de votes.
            params.update(timezone=planning.TIMEZONE_NAME, **{"first_air_date.gte": aujourdhui})
        d = tmdb_get("/discover/" + type, **params)
        out = {"ok": True, "resultats": [carte(dict(x, media_type=type)) for x in d.get("results", [])],
               "page": page, "pages": min(d.get("total_pages", 1), 500)}
        cache_listes[cle] = (time.time(), out)
        return out
    except Exception as e:
        return JSONResponse({"ok": False, "message": erreurs.message(e, 'Action impossible.')}, status_code=400)


TRIS = {"popularite": "popularity.desc", "note": "vote_average.desc", "recents": "date.desc"}


@app.get("/catalogue/parcourir")
def catalogue_parcourir(type: str = "movie", genre: int = 0, annee: str = "", tri: str = "popularite", page: int = 1):
    type = "tv" if type == "tv" else "movie"
    page = max(1, min(page, 500))
    plage = re.fullmatch(r"(exact|since|before|between):(\d{4})(?::(\d{4}))?", annee)
    if annee and not (re.fullmatch(r"\d{4}(?:-\d{4})?", annee) or plage):
        return JSONResponse({"ok": False, "message": "Année invalide"}, status_code=400)
    if plage and plage.group(1) == "between" and (not plage.group(3) or int(plage.group(3)) < int(plage.group(2))):
        return JSONResponse({"ok": False, "message": "Plage d’années invalide"}, status_code=400)
    tri = tri if tri in TRIS else "popularite"
    champ = "first_air_date" if type == "tv" else "primary_release_date"
    p = {"include_adult": "false", "page": page, "sort_by": TRIS[tri] if tri != "recents" else champ + ".desc"}
    if genre in [i for i, _ in GENRES[type]]:
        p["with_genres"] = str(genre)
    if annee:
        if plage:
            mode, debut, fin = plage.group(1), plage.group(2), plage.group(3)
            if mode == "exact":
                p["first_air_date_year" if type == "tv" else "primary_release_year"] = debut
            elif mode == "since":
                p[champ + ".gte"] = debut + "-01-01"
            elif mode == "before":
                p[champ + ".lte"] = str(int(debut) - 1) + "-12-31"
            else:
                p[champ + ".gte"], p[champ + ".lte"] = debut + "-01-01", fin + "-12-31"
        elif "-" in annee:
            a, b = annee.split("-")
            p[champ + ".gte"], p[champ + ".lte"] = a + "-01-01", b + "-12-31"
        else:
            p["first_air_date_year" if type == "tv" else "primary_release_year"] = annee
    if tri == "note":
        p["vote_count.gte"] = 300 if type == "movie" else 150
    elif tri == "recents":
        p["vote_count.gte"] = 10
        if not annee:
            p[champ + ".lte"] = time.strftime("%Y-%m-%d")
    cle = ("p", type, tuple(sorted(p.items())))
    c = cache_listes.get(cle)
    if c and time.time() - c[0] < charger()["catalogue_cache_min"] * 60:
        return c[1]
    try:
        d = tmdb_get("/discover/" + type, **p)
    except Exception as e:
        return JSONResponse({"ok": False, "message": erreurs.message(e, 'Action impossible.')}, status_code=400)
    out = {"ok": True, "resultats": [carte(dict(r, media_type=type)) for r in d.get("results", [])],
           "page": d.get("page", page), "pages": min(d.get("total_pages", 1), 500)}
    cache_listes[cle] = (time.time(), out)
    return out


@app.get("/catalogue/ajoutes")
def catalogue_ajoutes(page: int = 1):
    page = max(1, min(page, 500))
    debut, taille = (page - 1) * 18, page * 18 + 1
    cle = ("ajoutes", page)
    # Point 84 : la rangée suit les listes de Radarr et Sonarr. Sa mémoire vaut tant que ces listes n'ont pas été relues,
    # au plus 5 minutes : un nouveau fichier apparaît dès le rafraîchissement suivant, sans attendre une seconde expiration.
    bibliotheque.demander()
    version = bibliotheque.version_listes()
    c = cache_listes.get(cle)
    if c and time.time() - c[0] < 300 and c[2:] == (version,):
        return c[1]
    items, manque = [], False
    try:
        items, manque = bibliotheque.ajoutes(taille)
    except Exception:
        items = []
    deja_dans_bibliotheque = bool(items)
    plus = len(items) > debut + 18
    items = items[debut:debut + 18]
    if not deja_dans_bibliotheque and not manque and bibliotheque.ov_cle():
        try:
            r = overseerr_appel("GET", "/media?filter=available&sort=mediaAdded&take=19&skip=%d" % debut).json()
            resultats = [x for x in r.get("results", []) if x.get("tmdbId") and x.get("mediaType") in ("movie", "tv")]
            plus = len(resultats) > 18
            items = [(x["mediaType"], x["tmdbId"]) for x in resultats[:18]]
        except Exception:
            items = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        cartes = [c for c in ex.map(lambda x: carte_par_id(*x), items) if c]
    out = {"ok": True, "resultats": cartes, "pret": not manque, "plus": plus}
    if cartes and not manque:
        cache_listes[cle] = (time.time(), out, version)
    return out


@app.get("/catalogue/demandes")
def catalogue_demandes(request: Request, page: int = 1):
    """Pour l'admin, demandes récentes de l'installation Overseerr. Pour les autres comptes, activité privée KamCiné."""
    page = max(1, min(page, 500))
    taille, uid = 18, id_connecte(request)
    compte = compte_courant(request)
    admin = bool(compte and compte.get("admin"))
    cle = ("demandes", "overseerr" if admin else uid, page)
    cache = cache_listes.get(cle)
    if cache and time.time() - cache[0] < 90:
        return cache[1]
    try:
        if admin and bibliotheque.ov_cle():
            # Overseerr renvoie 400 si sortDirection est fourni (même "desc") ; son tri par défaut
            # est le plus récent en premier. take/skip/filter=all sont acceptés et pageInfo pagine.
            response = bibliotheque.ov_appel("GET", "/request?take=%d&skip=%d&filter=all" % (taille, (page - 1) * taille), delai=8)
            response.raise_for_status()
            payload = response.json()
            lignes = payload.get("results", []) if isinstance(payload, dict) else []
            identifiants, vus = [], set()
            for ligne in lignes:
                media = ligne.get("media") or {}
                ident = media.get("tmdbId") or ligne.get("tmdbId")
                type_media = media.get("mediaType") or ligne.get("mediaType") or ligne.get("type")
                if type_media in (1, "1", "movie", "film"):
                    type_media = "movie"
                elif type_media in (2, "2", "tv", "series", "show"):
                    type_media = "tv"
                else:
                    continue
                try:
                    ident = int(ident)
                except (TypeError, ValueError):
                    continue
                cle_media = (type_media, ident)
                if ident > 0 and cle_media not in vus:
                    vus.add(cle_media)
                    identifiants.append(cle_media)
                if len(identifiants) >= page * taille + 1:
                    break
            items = identifiants[:taille]
            plus = bool((payload.get("pageInfo") or {}).get("pages", 1) > page)
        else:
            items, plus = comptes.medias_actifs(uid, "demande", page, taille)
        identifiants = items
        with ThreadPoolExecutor(max_workers=6) as ex:
            resultats = [carte for carte in ex.map(lambda x: carte_par_id(*x), identifiants) if carte]
        message = None
        if admin and bibliotheque.ov_cle() and identifiants and not resultats:
            message = "Overseerr a des demandes, mais TMDB n’a pas permis de charger leurs fiches. Réessaie dans un instant."
        elif admin and bibliotheque.ov_cle() and len(resultats) < len(identifiants):
            message = "%d demande(s) n’ont pas pu être résolue(s) par TMDB." % (len(identifiants) - len(resultats))
        elif not resultats:
            message = "Aucune demande récente dans Overseerr." if admin and bibliotheque.ov_cle() else "Les demandes effectuées depuis KamCiné apparaîtront ici."
        out = {"ok": True, "resultats": resultats, "page": page, "plus": plus, "message": message}
        cache_listes[cle] = (time.time(), out)
        return out
    except Exception as e:
        return JSONResponse({"ok": False, "message": erreurs.message(e, "Overseerr ne peut pas charger les demandes récentes.")}, status_code=502)


@app.get("/catalogue/non-vues")
def catalogue_non_vues(page: int = 1):
    """Première rangée du catalogue : les titres présents dans la bibliothèque (Radarr ou Sonarr, avec un fichier) que tu n'as pas vus, le plus
    récemment ajouté d'abord. Vu : ce que dit vus_source (Trakt quand il est prêt, plus ce que KamCiné a noté), un film vu ou une série dont au
    moins un épisode est vu. Rien ne bloque : la bibliothèque et les vus viennent de la mémoire, et la réponse dit pret faux tant qu'une des
    deux sources n'est pas arrivée (l'interface réessaie et complète). Sans Radarr ni Sonarr, la rangée le dit au lieu de rester vide."""
    page = max(1, min(page, 500))
    debut, taille = (page - 1) * 18, page * 18 + 1
    if not any((bibliotheque.config().get(n) or {}).get("cle") for n in bibliotheque.NOMS):
        return {"ok": True, "resultats": [], "pret": True, "plus": False, "message": "Ajoute Radarr ou Sonarr dans Réglages pour voir ce que tu n'as pas encore vu."}
    v = vus_source()
    cle = ("nonvues", page, v["source"])
    c = cache_listes.get(cle)
    if c and time.time() - c[0] < 30:
        return c[1]
    def vu(t, i):
        return (i in v["films"]) if t == "movie" else (i in v["series"])
    items, manque = [], False
    try:
        items, manque = bibliotheque.ajoutes(taille, vu)
    except Exception:
        items = []
    plus = len(items) > debut + 18
    items = items[debut:debut + 18]
    with ThreadPoolExecutor(max_workers=6) as ex:
        cartes = [c_ for c_ in ex.map(lambda x: carte_par_id(*x), items) if c_]
    note = ""
    if v["source"] == "local":
        note = "D'après ce que KamCiné a noté : connecte Trakt pour compter aussi ce que tu regardes dans Infuse." if not trakt.connectee() else ""
    out = {"ok": True, "resultats": cartes, "pret": (not manque) and v["pret"], "source": v["source"], "note": note, "plus": plus}
    if out["pret"]:
        cache_listes[cle] = (time.time(), out)
    return out


@app.get("/catalogue/nouveautes")
def catalogue_nouveautes(page: int = 1):
    page = max(1, min(page, 500))
    cle = ("nouveautes", page)
    c = cache_listes.get(cle)
    if c and time.time() - c[0] < 3600:
        return c[1]
    aujourdhui, debut = time.strftime("%Y-%m-%d"), time.strftime("%Y-%m-%d", time.localtime(time.time() - 60 * 86400))
    try:
        rf = tmdb_get("/discover/movie", page=page, sort_by="popularity.desc", region="FR", include_adult="false",
                      **{"primary_release_date.gte": debut, "primary_release_date.lte": aujourdhui, "vote_count.gte": 5})
        rt = tmdb_get("/discover/tv", page=page, sort_by="popularity.desc", include_adult="false",
                      **{"first_air_date.gte": debut, "first_air_date.lte": aujourdhui, "vote_count.gte": 2})
    except Exception as e:
        return JSONResponse({"ok": False, "message": erreurs.message(e, 'Action impossible.')}, status_code=400)
    mix = []
    films, series = rf.get("results", []), rt.get("results", [])
    for i in range(max(len(films), len(series))):
        if i < len(films):
            mix.append(carte(dict(films[i], media_type="movie")))
        if i < len(series):
            mix.append(carte(dict(series[i], media_type="tv")))
    out = {"ok": True, "resultats": mix[:18], "page": page,
           "pages": min(max(rf.get("total_pages", 1), rt.get("total_pages", 1)), 500)}
    cache_listes[cle] = (time.time(), out)
    return out


@app.get("/catalogue/genres")
def catalogue_genres(type: str = "movie"):
    type = "tv" if type == "tv" else "movie"
    return {"ok": True, "genres": [{"id": i, "nom": n} for i, n in GENRES[type]]}


@app.get("/catalogue/genres/apercus")
def catalogue_genres_apercus():
    """Une jaquette TMDB distincte par genre, calculée une fois puis gardée en cache."""
    key = ("genre_apercus",)
    cached = cache_listes.get(key)
    if cached and time.time() - cached[0] < 86400:
        return cached[1]
    genres = GENRES["movie"]
    def chercher(g):
        ident, nom = g
        try:
            data = tmdb_get("/discover/movie", with_genres=str(ident), sort_by="popularity.desc", include_adult="false",
                            **{"vote_count.gte": 30, "vote_average.gte": 4})
            return ident, [x for x in data.get("results", []) if x.get("poster_path")]
        except Exception:
            return ident, []
    with ThreadPoolExecutor(max_workers=5) as ex:
        resultats = list(ex.map(chercher, genres))
    pool = []
    vus = set()
    for _, films in resultats:
        for film in films:
            path = film.get("poster_path")
            if path and path not in vus:
                vus.add(path)
                pool.append(path)
    utilises, cartes = set(), {}
    for ident, films in resultats:
        films.sort(key=lambda x: (-(x.get("popularity") or 0), x.get("id") or 0))
        candidats = films or []
        poster = next((x.get("poster_path") for x in candidats if x.get("poster_path") not in utilises), None)
        if not poster:
            poster = next((x for x in pool if x not in utilises), None)
        if poster:
            utilises.add(poster)
            cartes[ident] = image(poster, "w780")
    out = {"ok": True, "genres": [{"id": i, "nom": n, "affiche": cartes.get(i)} for i, n in genres]}
    cache_listes[key] = (time.time(), out)
    return out


@app.get("/catalogue/genre")
def catalogue_genre(type: str = "movie", id: int = 0, page: int = 1):
    type = "tv" if type == "tv" else "movie"
    page = max(1, min(page, 500))
    if id not in [i for i, _ in GENRES[type]]:
        return JSONResponse({"ok": False, "message": "Genre inconnu"}, status_code=400)
    try:
        cle = ("genre", type, id, page)
        c = cache_listes.get(cle)
        if c and time.time() - c[0] < 1800:
            return c[1]
        d = tmdb_get("/discover/" + type, page=page, with_genres=str(id), sort_by="popularity.desc",
                     include_adult="false", **{"vote_count.gte": 150})
        out = {"ok": True, "resultats": [carte(dict(x, media_type=type)) for x in d.get("results", [])][:18],
               "page": page, "pages": min(d.get("total_pages", 1), 500)}
        cache_listes[cle] = (time.time(), out)
        return out
    except Exception as e:
        return JSONResponse({"ok": False, "message": erreurs.message(e, 'Action impossible.')}, status_code=400)


@app.get("/catalogue/recents")
def catalogue_recents():
    try:
        with open(RECENTS, encoding="utf-8") as f:
            return {"ok": True, "resultats": json.load(f)[:9]}
    except Exception:
        return {"ok": True, "resultats": []}


def vus_source():
    """Ce qui est vu : Trakt quand il est connecté (Infuse lui envoie tout), plus ce que KamCiné a noté lui même (séances terminées et
    coches manuelles), pour que la coche réagisse tout de suite même si Trakt est en retard ou refuse l'écriture. Sans Trakt, la détection
    locale seule. Ne touche jamais au réseau : les vus de Trakt viennent de la mémoire, remplie en arrière plan. Tant qu'ils ne sont
    pas arrivés, on répond avec la détection locale et pret est faux."""
    h = suivi.historique()
    local = {"films": {x["id"] for x in h if x.get("type") == "movie"},
             "series": {x["id"] for x in h if x.get("type") == "tv"},
             "episodes": {(x["id"], x.get("saison"), x.get("episode")) for x in h if x.get("type") == "tv"}}
    if trakt.connectee():
        t, pret = trakt.vus_rapide()
        if pret:
            return {"films": t["films"] | local["films"], "series": t["series"] | local["series"],
                    "episodes": t["episodes"] | local["episodes"], "source": "trakt", "pret": True}
    return dict(local, source="local", pret=not trakt.connectee())


def deja_vu(type_, id_, saison=None, episode=None):
    v = vus_source()
    if type_ == "movie":
        return id_ in v["films"]
    if saison and episode:
        return (id_, saison, episode) in v["episodes"]
    return id_ in v["series"]


def omdb_notes(meta):
    """Scores tiers désactivés, même avec une clé déjà enregistrée."""
    return {}


def omdb_tester(cle, delai=6):
    """Aucun appel OMDb tant que les droits des scores tiers ne sont pas établis."""
    return False, "Les notes OMDb sont désactivées dans cette bêta."


@app.get("/omdb/etat")
def omdb_etat():
    return {"configuree": False, "disponible": False}


@app.post("/omdb/cle")
async def omdb_cle(request: Request):
    """Configuration indisponible tant que les droits des scores ne sont pas établis."""
    return JSONResponse({"ok": False, "message": "Les notes OMDb sont désactivées dans cette bêta."}, status_code=410)


@app.post("/omdb/retirer")
def omdb_retirer():
    s_ = lire_secrets()
    s_.pop("omdb", None)
    ecrire_prive(SECRETS, s_)
    cache_omdb.clear()
    return {"ok": True}


def notes_titre(type_, id_, meta):
    """Seules les notes TMDB et personnelles restent disponibles dans cette bêta."""
    return None


MOIS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
LANGUES = {"fr": "Français", "en": "Anglais", "es": "Espagnol", "de": "Allemand", "it": "Italien", "ja": "Japonais",
           "ko": "Coréen", "zh": "Chinois", "cn": "Cantonais", "pt": "Portugais", "ru": "Russe", "hi": "Hindi",
           "sv": "Suédois", "da": "Danois", "no": "Norvégien", "nl": "Néerlandais", "pl": "Polonais", "tr": "Turc",
           "ar": "Arabe", "th": "Thaï", "fi": "Finnois", "he": "Hébreu", "cs": "Tchèque", "el": "Grec", "uk": "Ukrainien"}
STATUTS_FILM = {"Released": "Sorti", "Post Production": "En post production", "In Production": "En production",
                "Planned": "Prévu", "Rumored": "Rumeur", "Canceled": "Annulé"}
STATUTS_TV = {"Returning Series": "En cours", "Ended": "Terminée", "Canceled": "Annulée", "In Production": "En production",
              "Planned": "Prévue", "Pilot": "Pilote"}


def date_fr(d):
    try:
        a, m, j = d.split("-")
        return "%d %s %s" % (int(j), MOIS_FR[int(m) - 1], a)
    except Exception:
        return d or ""


def argent(n):
    if not n:
        return ""
    return ("%s M$" % ("%.1f" % (n / 1e6)).rstrip("0").rstrip(".").replace(".", ",")) if n >= 1e6 else "%d k$" % round(n / 1e3)


def classification(type_, f):
    try:
        if type_ == "tv":
            res = {r["iso_3166_1"]: r.get("rating") for r in (f.get("content_ratings") or {}).get("results", [])}
            return res.get("FR") or res.get("US") or ""
        for pays in ("FR", "US"):
            for r in (f.get("release_dates") or {}).get("results", []):
                if r.get("iso_3166_1") == pays:
                    for d in r.get("release_dates", []):
                        if d.get("certification"):
                            return d["certification"]
    except Exception:
        pass
    return ""


def noms(liste, n=3):
    return ", ".join(x.get("name") for x in liste[:n] if x.get("name"))


def fiche_infos(type_, f):
    cr = f.get("credits") or {}
    equipe = cr.get("crew") or []
    infos = []

    def ajout(k, v):
        if v:
            infos.append({"k": k, "v": str(v)})

    titre_o = f.get("original_title") if type_ != "tv" else f.get("original_name")
    if titre_o and titre_o != (f.get("title") or f.get("name")):
        ajout("Titre original", titre_o)
    if type_ == "tv":
        ajout("Créée par", noms(f.get("created_by") or []))
        ajout("Première diffusion", date_fr(f.get("first_air_date")))
        nx = f.get("next_episode_to_air") or {}
        ajout("Prochain épisode", date_fr(nx.get("air_date")))
        ajout("Statut", STATUTS_TV.get(f.get("status"), f.get("status")))
        if f.get("number_of_seasons"):
            ajout("Saisons et épisodes", "%d saison%s, %d épisodes" % (f["number_of_seasons"], "s" if f["number_of_seasons"] > 1 else "",
                                                                    f.get("number_of_episodes") or 0))
        ajout("Diffusée sur", noms(f.get("networks") or []))
    else:
        ajout("Sortie", date_fr(f.get("release_date")))
        ajout("Réalisation", ", ".join(x["name"] for x in equipe if x.get("job") == "Director")[:120])
        ajout("Scénario", ", ".join(dict.fromkeys(x["name"] for x in equipe if x.get("job") in ("Screenplay", "Writer")))[:120])
        ajout("Musique", ", ".join(x["name"] for x in equipe if x.get("job") == "Original Music Composer")[:80])
        ajout("Statut", STATUTS_FILM.get(f.get("status"), f.get("status")))
        ajout("Budget", argent(f.get("budget")))
        ajout("Recettes", argent(f.get("revenue")))
    ajout("Classification", classification(type_, f))
    ajout("Pays", noms(f.get("production_countries") or [], 4))
    ajout("Langue d'origine", LANGUES.get(f.get("original_language"), (f.get("original_language") or "").upper()))
    ajout("Production", noms(f.get("production_companies") or [], 3))
    return infos


def casting(f):
    out = []
    for c in (f.get("credits") or {}).get("cast", [])[:16]:
        out.append({"id": c.get("id"), "nom": c.get("name") or "", "role": c.get("character") or "",
                    "photo": image(c.get("profile_path"), "w185")})
    return out


def annoter_saisons_vues(out, id_):
    episodes_vus = vus_source()["episodes"]
    for saison in out.get("saisons", []):
        numero, total = saison.get("numero", 0), saison.get("episodes", 0)
        numeros_vus = {episode for sid, saison_, episode in episodes_vus if sid == id_ and saison_ == numero}
        saison["terminee"] = bool(total and set(range(1, total + 1)) <= numeros_vus)
    return out


@app.get("/catalogue/fiche")
def catalogue_fiche(type: str = "movie", id: int = 0, saison: int = 0, episode: int = 0):
    """Premier temps : tout ce qui vient de TMDB, pour afficher la page tout de suite."""
    requete_debut = time.perf_counter()
    type = "tv" if type == "tv" else "movie"
    cle = ("fiche", type, id, saison or None, episode or None)
    if cle in cache_tmdb and cache_tmdb[cle]:
        out = cache_tmdb[cle]
        resultat = annoter_saisons_vues(dict(out), id) if type == "tv" else dict(out)
        resultat["profilage_ms"] = {"cache_tmdb": round((time.perf_counter() - requete_debut) * 1000),
                                     "total_requete": round((time.perf_counter() - requete_debut) * 1000)}
        return resultat
    profilage = {}
    tmdb_debut = time.perf_counter()
    try:
        f = details_complets(type, id)
    except Exception:
        return JSONResponse({"ok": False, "message": "Fiche introuvable"}, status_code=404)
    profilage["tmdb_fiche_distribution_identifiants"] = round((time.perf_counter() - tmdb_debut) * 1000)
    if type == "tv":
        ep = None
        if saison and episode:
            episode_debut = time.perf_counter()
            try:
                ep = tmdb_get("/tv/%d/season/%d/episode/%d" % (id, saison, episode))
            except Exception:
                ep = None
            profilage["tmdb_episode"] = round((time.perf_counter() - episode_debut) * 1000)
        meta = meta_serie(f, ep, saison or None, episode or None)
    else:
        meta = meta_film(f)
    out = {"ok": True, "meta": meta, "saisons": [], "casting": casting(f), "infos": fiche_infos(type, f),
           "genres": [g["name"] for g in f.get("genres", [])], "slogan": f.get("tagline") or ""}
    if type == "movie" and (collection := f.get("belongs_to_collection")):
        collection_id = collection.get("id")
        if isinstance(collection_id, int) and collection_id > 0:
            collection_debut = time.perf_counter()
            try:
                saga = tmdb_get("/collection/%d" % collection_id)
                parties = {partie.get("id"): partie for partie in saga.get("parts", [])
                           if isinstance(partie.get("id"), int) and partie.get("id") > 0 and partie.get("title")}
                films = [carte(dict(partie, media_type="movie")) for identifiant, partie in parties.items() if identifiant != id]
                if films:
                    out["collection"] = {"id": collection_id, "titre": saga.get("name") or collection.get("name") or "Collection",
                                          "ids": list(parties), "films": films}
            except Exception as e:
                erreurs.message(e, "Chargement de la collection TMDB impossible.")
            profilage["tmdb_collection"] = round((time.perf_counter() - collection_debut) * 1000)
    if type == "tv":
        out["saisons"] = [{"numero": x["season_number"], "episodes": x.get("episode_count", 0)}
                          for x in f.get("seasons", []) if x.get("season_number", 0) > 0]
        annoter_saisons_vues(out, id)
    cache_tmdb[cle] = out
    profilage["total_requete"] = round((time.perf_counter() - requete_debut) * 1000)
    return {**out, "profilage_ms": profilage}



@app.get("/catalogue/fichiers")
def catalogue_fichiers(id: int = 0, saison: int = 0, episode: int = 0):
    return {"ok": True, "fichiers": bibliotheque.fichiers_episode(id, saison, episode) if id > 0 and saison > 0 and episode > 0 else []}


@app.get("/catalogue/fiche/etat")
def catalogue_fiche_etat(request: Request, type: str = "movie", id: int = 0, saison: int = 0, episode: int = 0, rapide: int = 0):
    """Second temps : bibliothèque (Radarr, Sonarr, Overseerr), notes, vu, suggestions."""
    requete_debut = time.perf_counter()
    type = "tv" if type == "tv" else "movie"
    if rapide:
        # Première passe locale, puis lookup ciblé (jamais la bibliothèque complète) si le cache est froid.
        debut = time.perf_counter()
        try:
            st = statut_avec_transmission(bibliotheque.statut(type, id, None, False))
        except Exception:
            st = None
        st = appliquer_demandes_locales(type, id, st)
        local_ms = round((time.perf_counter() - debut) * 1000)
        if st and st.get("incomplet"):
            try:
                st = statut_avec_transmission(bibliotheque.statut_cible(type, id, request.query_params.get("tvdb_id"), st, delai=4))
            except Exception:
                pass
        st = appliquer_demandes_locales(type, id, st)
        qualite = None
        try:
            qualite = bibliotheque.qualites_titre(st, charger()["qualite_preferee"])
        except Exception:
            pass
        profilage = {"cache_etat_local": local_ms, **((st or {}).get("profilage_ms") or {})}
        profilage["total_requete"] = round((time.perf_counter() - requete_debut) * 1000)
        return {"ok": True, "statut": st, "qualite": qualite, "liens_arr": [], "notes": {}, "similaires": [],
                "vu": None, "vu_serie": None, "vu_source": None, "trakt_connectee": None, "favori": None, "rapide": True,
                "profilage_ms": profilage}
    profilage = {}
    debut = time.perf_counter()
    try:
        f = details_complets(type, id)
    except Exception:
        f = {}
    profilage["tmdb_fiche_et_identifiants"] = round((time.perf_counter() - debut) * 1000)
    meta = {"liens": liens_de(type, f)} if f else {"liens": {}}
    tvdb = (f.get("external_ids") or {}).get("tvdb_id")

    def similaires():
        debut_sim = time.perf_counter()
        try:
            valeur = [carte(dict(r, media_type=type)) for r in tmdb_get("/%s/%d/recommendations" % (type, id))["results"][:10]]
        except Exception:
            valeur = []
        return valeur, round((time.perf_counter() - debut_sim) * 1000)

    def disponibilite():
        debut_dispo = time.perf_counter()
        st_ = statut_avec_transmission(bibliotheque.statut(type, id, tvdb, False))
        if st_ and st_.get("incomplet"):
            st_ = statut_avec_transmission(bibliotheque.statut_cible(type, id, tvdb, st_, delai=4))
        st_ = appliquer_demandes_locales(type, id, st_)
        return st_, round((time.perf_counter() - debut_dispo) * 1000)

    def notes():
        debut_notes = time.perf_counter()
        return notes_titre(type, id, meta), round((time.perf_counter() - debut_notes) * 1000)

    with ThreadPoolExecutor(max_workers=4) as ex:
        fs = ex.submit(disponibilite)
        fn = ex.submit(notes)
        fr = ex.submit(similaires)
        try:
            notes_result, profilage["notes_overseerr_omdb"] = fn.result()
            sim, profilage["recommandations_tmdb"] = fr.result()
            st, profilage["disponibilite_arr_overseerr_cache"] = fs.result()
        except Exception as e:
            st = {"erreur": erreurs.message(e, 'Action impossible.'), "hd": {"etat": "inconnu"}, "uhd": {"etat": "inconnu"}, "saisons": {}, "global": "inconnu", "sources": {}}
            notes_result, sim = {}, []
    qualite = None
    try:
        qualite = bibliotheque.qualites_titre(st, charger()["qualite_preferee"])
    except Exception:
        pass
    liens = []
    try:
        liens = bibliotheque.liens_arr(type, id, tvdb)
    except Exception:
        pass
    profilage["total_requete"] = round((time.perf_counter() - requete_debut) * 1000)
    return {"ok": True, "statut": st, "qualite": qualite, "liens_arr": liens, "notes": notes_result, "similaires": sim,
            "vu": deja_vu(type, id, saison or None, episode or None), "vu_serie": (deja_vu("tv", id) if type == "tv" else None),
            "vu_source": vus_source()["source"], "trakt_connectee": trakt.connectee(), "favori": favoris.est_favori(id_connecte(request), type, id),
            "profilage_ms": profilage}


METIERS_TMDB = {"Acting": "Acteur / actrice", "Directing": "Réalisation", "Writing": "Scénariste", "Production": "Production",
                "Sound": "Son et musique", "Camera": "Directeur / directrice de la photographie", "Editing": "Montage",
                "Creator": "Création", "Art": "Direction artistique"}


def metier_personne(departement, genre=0):
    """Libellé lisible du département professionnel TMDB, accordé si le genre est connu."""
    accord = {"Acting": ("Actrice", "Acteur"), "Directing": ("Réalisatrice", "Réalisateur"),
              "Production": ("Productrice", "Producteur"), "Creator": ("Créatrice", "Créateur"),
              "Editing": ("Monteuse", "Monteur"),
              "Camera": ("Directrice de la photographie", "Directeur de la photographie")}
    if departement in accord:
        feminin, masculin = accord[departement]
        return feminin if genre == 1 else masculin if genre == 2 else METIERS_TMDB[departement]
    return METIERS_TMDB.get(departement, "")
GENRES_SANS_FICTION = {10763, 10764, 10767}    # journal télévisé, télé réalité, talk show : la personne y passe, elle n'y joue pas
METIERS_CREDITS = {"Director": "Réalisation", "Screenplay": "Scénario", "Writer": "Scénario", "Creator": "Création"}


def filmographie(cr, acteur):
    """Films et séries d'une personne, dédoublonnés, du plus connu au moins connu. Les apparitions en tant que soi même
    (talk shows, actualités) et les titres sans jaquette sont écartés."""
    vus = {}
    lignes = list(cr.get("cast") or [])
    if not acteur:
        lignes += [dict(x, character=METIERS_CREDITS[x["job"]]) for x in (cr.get("crew") or []) if x.get("job") in METIERS_CREDITS]
    for r in lignes:
        if r.get("media_type") not in ("movie", "tv") or not r.get("poster_path"):
            continue
        if GENRES_SANS_FICTION & set(r.get("genre_ids") or []):
            continue
        role = (r.get("character") or "").strip()
        if role.lower().startswith("self") or role.lower().startswith("himself") or role.lower().startswith("herself"):
            continue
        cle = (r["media_type"], r["id"])
        if cle in vus:
            continue
        vus[cle] = dict(carte(r), role=role, pop=r.get("popularity") or 0)
    tri = sorted(vus.values(), key=lambda x: x["pop"], reverse=True)
    for x in tri:
        x.pop("pop")
    return ([x for x in tri if x["type"] == "movie"][:60], [x for x in tri if x["type"] == "tv"][:30])


@app.get("/catalogue/personne")
def catalogue_personne(id: int = 0):
    """Point 3 de la 2.0 : vue d'une personne de la distribution, avec ses films et séries (chacun ouvre sa fiche)."""
    cle = ("personne", id)
    if cache_tmdb.get(cle):
        return cache_tmdb[cle]
    try:
        with ThreadPoolExecutor(max_workers=2) as ex:
            fp = ex.submit(tmdb_get, "/person/%d" % id)
            fc = ex.submit(tmdb_get, "/person/%d/combined_credits" % id)
            p, cr = fp.result(), fc.result()
    except PermissionError as e:
        return JSONResponse({"ok": False, "message": erreurs.message(e, 'Vérifie la clé TMDB dans les réglages.')}, status_code=400)
    except Exception as e:
        return JSONResponse({"ok": False, "message": erreurs.message(e, "Personne introuvable.")}, status_code=404)
    bio = (p.get("biography") or "").strip()
    if not bio:
        try:
            bio = (tmdb_get("/person/%d" % id, langue="en-US").get("biography") or "").strip()
        except Exception:
            bio = ""
    departement = p.get("known_for_department") or ""
    films, series = filmographie(cr, departement in ("", "Acting"))
    out = {"ok": True, "personne": {"id": p.get("id") or id, "nom": p.get("name") or "", "photo": image(p.get("profile_path"), "w342"),
                                    "metier": metier_personne(departement, p.get("gender") or 0), "naissance": date_fr(p.get("birthday")) if p.get("birthday") else "",
                                    "deces": date_fr(p.get("deathday")) if p.get("deathday") else "", "lieu": p.get("place_of_birth") or "",
                                    "bio": bio[:1500]},
           "films": films, "series": series}
    cache_tmdb[cle] = out
    return out


@app.get("/catalogue/episodes")
def catalogue_episodes(id: int = 0, saison: int = 1):
    try:
        d = tmdb_get("/tv/%d/season/%d" % (id, saison))
    except Exception as e:
        return JSONResponse({"ok": False, "message": erreurs.message(e, 'Action impossible.')}, status_code=400)
    v = vus_source()["episodes"]
    disponibilites = bibliotheque.disponibilite_episodes(id, saison)
    return {"ok": True, "episodes": [{"numero": e["episode_number"], "titre": e.get("name") or "",
                                      "duree": e.get("runtime"), "affiche": image(e.get("still_path"), "w300"),
                                      "note": round(e.get("vote_average") or 0, 1) if e.get("vote_count") else None,
                                      "date": e.get("air_date") or "",
                                      "resume": e.get("overview") or "",
                                      "vu": (id, saison, e["episode_number"]) in v,
                                      "versions": disponibilites.get(e["episode_number"], {})}
                                     for e in d.get("episodes", [])]}


@app.post("/catalogue/episodes/demander")
async def catalogue_episodes_demander(request: Request):
    """Demande ciblée d'un épisode ou des épisodes manquants d'une saison, qualité par qualité."""
    c = await corps_json(request)
    if c.get("type") != "tv":
        return JSONResponse({"ok": False, "message": "Cette demande concerne une série."}, status_code=400)
    try:
        identifiant = int(c.get("id") or 0)
        tvdb = int(c.get("tvdb") or 0) or None
        saison = int(c.get("saison") or 0)
        episode = int(c["episode"]) if c.get("episode") is not None else None
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "message": "Identifiants de série, saison ou épisode invalides."}, status_code=400)
    qualites = c.get("qualites")
    if not isinstance(qualites, list) or not qualites or any(q not in ("hd", "uhd") for q in qualites):
        return JSONResponse({"ok": False, "message": "Choisis au moins une qualité valide."}, status_code=400)
    if identifiant <= 0 or saison <= 0 or (episode is not None and episode <= 0):
        return JSONResponse({"ok": False, "message": "Série, saison ou épisode invalide."}, status_code=400)
    try:
        resultat = bibliotheque.demander_episodes(identifiant, tvdb, saison, qualites, episode)
    except Exception as e:
        return JSONResponse({"ok": False, "message": erreurs.message(e, "Impossible de demander les épisodes manquants." )}, status_code=400)
    demandee = [("4K" if x["qualite"] == "uhd" else "HD", x["episodes"]) for x in resultat["resultats"] if x["episodes"]]
    if demandee:
        portee = "l’épisode %d" % episode if episode else "la saison %d" % saison
        erreurs.journaliser_action("Sonarr", "Recherche demandée pour %s : %s." % (
            portee, " ; ".join("%s (%d épisode(s))" % (q, len(eps)) for q, eps in demandee)))
    return {"ok": True, "resultats": resultat["resultats"], "message": "Recherche lancée pour les épisodes manquants." if demandee else "Aucun épisode diffusé ne manque dans ces qualités."}


@app.get("/generique")
def generique_info(type: str = "movie", id: int = 0, saison: int = 0, episode: int = 0, duree: int = 0):
    cle = (type, id, saison, episode, duree // 60)
    if cle not in cache_gen:
        debut = None
        try:
            import generique
            debut = generique.credits_debut(type, id, saison or None, episode or None, duree or None)
        except Exception:
            debut = None
        cache_gen[cle] = debut
    debut = cache_gen[cle]
    if debut is None:
        return {"debut": None, "cible": None, "source": None}
    return {"debut": int(debut), "cible": int(debut) + int(charger()["generique_delai"]), "source": "TheIntroDB"}


@app.post("/lancer")
@depart_exclusif
def lancer(type: str = "movie", id: int = 0, saison: int = 0, episode: int = 0, ignorer_ailleurs: int = 0):
    if (not any(cle_media(x) == (("tv", id, saison or None, episode or None) if type == "tv" else ("movie", id)) for x in SUSPENDUES["l"])
            and suspens_plein("tv" if type == "tv" else "movie", id, saison, episode)):
        return reponse_suspens_plein()
    if conflit_depart(type, id, saison, episode, bool(ignorer_ailleurs)):
        return JSONResponse({"ok": False, "message": "Une séance est en cours"}, status_code=409)
    type = "tv" if type == "tv" else "movie"
    url = url_infuse(type, id, saison or None, episode or None)
    if not url:
        return JSONResponse({"ok": False, "message": "Choisis une saison et un épisode"}, status_code=400)
    d = lancement_definir(type, id, saison, episode, "lire", "Réveil de l'Apple TV si besoin…")
    reveiller()
    if LANCEMENT["d"] is not d:
        return {"ok": False, "message": "Lancement annulé."}
    d["lecture_apres"] = time.time()
    attente_lecture = charger()["lancer_attente_s"]
    for essai in range(NB_ESSAIS_INFUSE):
        if LANCEMENT["d"] is not d:
            return {"ok": False, "message": "Lancement annulé."}
        avant = lire_playing(frais=True) if essai else None
        if essai and lecture_sans_identite_infuse(avant):
            # Lot 2.6.90 : Infuse lit déjà un flux dont le titre n'est pas encore publié. Renvoyer le lien profond rouvrirait la
            # fiche et relancerait la lecture par dessus (piste pour l'interface Infuse bloquée après un lancement KamCiné).
            journal_chaine("lancement", {"evenement": "renvoi du lien évité", "essai": essai + 1, "lien": url, "origine": "lire",
                                         "observe": {k: avant.get(k) for k in ("app", "etat", "titre", "total")}})
            ok = True
        else:
            d["phase"] = "Envoi de la commande à l'Apple TV…" if essai == 0 else "Nouvel envoi à l'Apple TV…"
            journal_chaine("lancement", {"evenement": "lien profond envoyé", "essai": essai + 1, "lien": url, "origine": "lire",
                                         "observe": {k: (avant or {}).get(k) for k in ("app", "etat", "titre", "total")} if avant else None})
            ok, sortie = atv("launch_app=" + url, delai=15)
        if not ok:
            msg = "L'Apple TV refuse la commande : " + sortie[-100:]
            d.update(etat="echec", message=msg)
            return {"ok": False, "message": msg}
        d["phase"] = "Ouverture dans Infuse, attente de la lecture…" if essai == 0 else MSG_INDEXATION
        debut = time.time()
        while time.time() - debut < attente_lecture:
            if LANCEMENT["d"] is not d:
                return {"ok": False, "message": "Lancement annulé."}
            p = lire_playing(frais=True)
            if _film_toujours_en_lecture(d, p):
                memoriser_recent(type, id, saison or None, episode or None)
                cache_film["t"] = 0
                confirmer_lancement(d, p)
                return {"ok": True}
            time.sleep(1)
    if d.get("confirme"):
        return {"ok": True}
    _lancement_echec(d, MSG_INFUSE)
    return {"ok": False, "message": MSG_INFUSE}


# ---------- Overseerr ----------
@app.get("/overseerr/etat")
def overseerr_etat():
    """url : l'adresse réellement utilisée par le service (2.6.108). Sans adresse enregistrée, c'est l'adresse par défaut
    OVERSEERR_LOCAL (127.0.0.1:5055, vue depuis le serveur KamCiné) : l'interface le dit au lieu d'afficher un exemple."""
    return {"configuree": bool(lire_secrets().get("overseerr")), "url": bibliotheque.ov_base(),
            "par_defaut": not charger().get("overseerr_url")}


@app.post("/overseerr/cle")
async def overseerr_cle(request: Request):
    cle = str((await corps_json(request)).get("cle", "")).strip()
    if len(cle) < 20:
        return JSONResponse({"ok": False, "message": "Cette clé semble incomplète"}, status_code=400)
    ancienne = lire_secrets()
    ecrire_prive(SECRETS, dict(ancienne, overseerr=cle))
    try:
        r = overseerr_appel("GET", "/auth/me")
        if r.status_code != 200:
            raise RuntimeError("réponse %d" % r.status_code)
    except PermissionError as e:
        erreurs.message(e, "Identifiants refusés par Overseerr.", confidentiels=(cle,))
        ecrire_prive(SECRETS, ancienne)
        return JSONResponse({"ok": False, "message": "Overseerr refuse cette clé"}, status_code=400)
    except Exception as e:
        ecrire_prive(SECRETS, ancienne)
        return JSONResponse({"ok": False, "message": erreurs.message(e, 'Connexion impossible à Overseerr.', confidentiels=(cle,))}, status_code=400)
    bibliotheque.vider()
    invalider_demandes_recentes()
    return {"ok": True}


@app.post("/overseerr/retirer")
def overseerr_retirer():
    s = lire_secrets()
    s.pop("overseerr", None)
    ecrire_prive(SECRETS, s)
    bibliotheque.vider()
    invalider_demandes_recentes()
    return {"ok": True}


@app.post("/overseerr/demander")
def overseerr_demander(type: str = "movie", id: int = 0, versions: str = "both", sans_doublon: int = 0, request: Request = None):
    """sans_doublon : la demande automatique des bandes annonces et le bloc Bande annonce lancée de l'accueil ne redemandent pas un titre
    déjà demandé (demandes_auto.json). La fiche, elle, peut redemander une autre version."""
    type = "tv" if type == "tv" else "movie"
    if sans_doublon and deja_demande(type, id):
        return {"ok": True, "existante": True, "resultats": {}}
    resultats = envoyer_demande(type, id, versions)
    if any(v["ok"] for v in resultats.values()):
        noter_demande(type, id, versions)
        noter_activite(request, "demande", type, id, versions)
        meta = cache_tmdb.get(("fiche", type, id, None, None)) or {}
        titre = meta.get("titre") or ("Série" if type == "tv" else "Film")
        qualites = ["4K" if k == "uhd" else "HD" for k, v in resultats.items() if v.get("ok")]
        if qualites:
            cle_qualites = "-".join(sorted(qualites))
            notifications.ajouter("overseerr:%s:%d:%s" % (type, id, cle_qualites), "demande_envoyee",
                                  "Demande envoyée", "%s · %s" % (titre, " et ".join(qualites)),
                                  {"page": "fiche", "type": type, "id": id})
        return {"ok": True, "resultats": resultats, "notifications_non_lues": notifications.lister()["non_lues"]}
    texte = "; ".join("%s : %s" % (k.upper().replace("UHD", "4K"), v["message"]) for k, v in resultats.items())
    return JSONResponse({"ok": False, "message": "Overseerr, " + texte, "resultats": resultats}, status_code=400)


@app.post("/overseerr/synchroniser")
def overseerr_synchroniser():
    try:
        lances = bibliotheque.synchroniser_overseerr()
    except Exception as e:
        return JSONResponse({"ok": False, "message": erreurs.message(e, 'Synchronisation impossible avec Overseerr.')}, status_code=400)
    return {"ok": True, "taches": lances}


@app.get("/arr/etat")
def arr_etat():
    return {"ok": True, "instances": bibliotheque.etat_config()}


@app.get("/services/etat")
def services_etat():
    """État lisible des Radarr/Sonarr configurés, sans exposer clés ni adresses internes."""
    cfg = charger()
    public = {"radarr_hd": cfg.get("url_publique_radarr_hd", ""), "radarr_uhd": cfg.get("url_publique_radarr_uhd", ""),
              "sonarr_hd": cfg.get("url_publique_sonarr_hd", ""), "sonarr_uhd": cfg.get("url_publique_sonarr_uhd", "")}
    instances = []
    for inst in bibliotheque.etat_config():
        item = {"nom": inst["nom"], "titre": inst["titre"], "configuree": inst["configuree"],
                "etat": "inconnu", "resume": "Non configuré", "details": [], "lien": public.get(inst["nom"], "")}
        if inst["configuree"]:
            item["resume"] = "Vérification impossible"
            item["etat"] = "erreur"
            try:
                status = bibliotheque._get(inst["nom"], "/system/status", delai=5)
                health = bibliotheque._get(inst["nom"], "/health", delai=5)
                problemes = []
                for h in health if isinstance(health, list) else []:
                    if not isinstance(h, dict):
                        continue
                    message = str(h.get("message") or "").strip()
                    kind = str(h.get("type") or "").lower()
                    if message and kind in ("warning", "error", "notice"):
                        problemes.append({"type": kind, "message": message_sante(message)})
                errors = [x for x in problemes if x["type"] == "error"]
                warnings = [x for x in problemes if x["type"] in ("warning", "notice")]
                item["etat"] = "erreur" if errors else "avertissement" if warnings or status.get("updateAvailable") else "ok"
                item["resume"] = resume_sante(errors[0]["message"]) if errors else resume_sante(warnings[0]["message"]) if warnings else (
                    "Mise à jour disponible" if status.get("updateAvailable") else "En ligne · version " + str(status.get("version", "inconnue")))
                item["details"] = (problemes + ([{"type": "info", "message": "Une mise à jour du service est disponible."}]
                                                 if status.get("updateAvailable") else []))[:8]
            except PermissionError:
                item.update(etat="erreur", resume="Clé API refusée")
            except Exception:
                item.update(etat="erreur", resume="Service injoignable ou erreur API")
        instances.append(item)
    sec = lire_secrets()
    atv = etat_appletv()
    tr = transmission.etat()
    autres = [
        {"titre": "TMDB", "etat": "avertissement" if sec.get("tmdb") else "inconnu", "resume": "Clé configurée, connexion non vérifiée" if sec.get("tmdb") else "Non configuré"},
        {"titre": "Apple TV", "etat": "ok" if atv["connectee"] else "avertissement" if atv["configuree"] else "inconnu",
         "resume": "Connectée" if atv["connectee"] else "Pas d’observation récente" if atv["configuree"] else "Non configurée"},
        {"titre": "Overseerr", "etat": "avertissement" if sec.get("overseerr") else "inconnu", "resume": "Configuré, connexion non vérifiée" if sec.get("overseerr") else "Non configuré"},
        trakt_service_etat(),
        {"titre": "Transmission", "etat": "avertissement" if tr.get("configuree") and tr.get("actif") else "inconnu",
         "resume": "Activé, connexion non vérifiée" if tr.get("configuree") and tr.get("actif") else "Désactivé" if tr.get("configuree") else "Non configuré"},
        {"titre": "Hue", "etat": "avertissement" if integrations.hue() else "inconnu", "resume": "Pont configuré, connexion non vérifiée" if integrations.hue() else "Optionnel, non configuré"},
        {"titre": "Denon", "etat": "avertissement" if denon.actif() else "inconnu", "resume": "Ampli activé, connexion non vérifiée" if denon.actif() else "Optionnel, non configuré"},
    ]
    return {"ok": True, "instances": instances, "autres": autres}


def trakt_service_etat():
    """État synthétique de Trakt pour la page services; aucune réponse brute ni secret ne sort."""
    if not trakt.configuree():
        return {"titre": "Trakt", "etat": "inconnu", "resume": "Non configuré"}
    if not trakt.connectee():
        return {"titre": "Trakt", "etat": "avertissement", "resume": "Connexion requise"}
    try:
        response = trakt._appel("GET", "/users/settings", delai=5)
        if response.status_code in (401, 403):
            return {"titre": "Trakt", "etat": "avertissement", "resume": "Authentification à renouveler"}
        if response.status_code == 429:
            return {"titre": "Trakt", "etat": "avertissement", "resume": "Limite API temporaire"}
        if 200 <= response.status_code < 300:
            return {"titre": "Trakt", "etat": "ok", "resume": "Connecté · API accessible"}
        return {"titre": "Trakt", "etat": "erreur", "resume": "Erreur de réponse API"}
    except Exception:
        return {"titre": "Trakt", "etat": "erreur", "resume": "API inaccessible"}


def resume_sante(message):
    """Réduit les messages de healthchecks Radarr/Sonarr à un état lisible."""
    texte = message_sante(message)
    normalise = texte.casefold()
    if "indexer" in normalise or "indexeur" in normalise:
        return "Un indexeur est temporairement indisponible"
    if "rss" in normalise or "feed" in normalise:
        return "La mise à jour RSS est indisponible"
    if "search" in normalise or "recherche" in normalise:
        return "La recherche est indisponible"
    if "connect" in normalise or "connexion" in normalise or "unable to communicate" in normalise:
        return "Problème de connexion à un service"
    if "update" in normalise or "mise à jour" in normalise:
        return "Une mise à jour du service est disponible"
    if any(mot in normalise for mot in ("disk", "space", "espace disque", "storage")):
        return "Espace de stockage insuffisant"
    if any(mot in normalise for mot in ("permission", "access denied", "unauthorized", "forbidden")):
        return "Accès refusé au service ou au dossier"
    if any(mot in normalise for mot in ("certificate", "ssl", "tls", "https")):
        return "La connexion sécurisée doit être vérifiée"
    if any(mot in normalise for mot in ("database", "sqlite", "migration")):
        return "La base de données du service nécessite une vérification"
    if any(mot in normalise for mot in ("path", "folder", "directory", "chemin", "dossier")):
        return "Un chemin ou dossier configuré est inaccessible"
    return texte if len(texte) <= 74 else "Le service signale un problème à vérifier"


def message_sante(message):
    """Garde un détail utile sans exposer adresses privées présentes dans une réponse d'API."""
    texte = re.sub(r"https?://\S+", "adresse masquée", str(message or ""))
    texte = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b", "adresse masquée", texte)
    return re.sub(r"\s+", " ", texte).strip()[:240]


@app.post("/arr/config")
async def arr_config(request: Request):
    c = await corps_json(request)
    nom, url, cle = str(c.get("nom", "")), str(c.get("url", "")).strip(), str(c.get("cle", "")).strip()
    if nom not in bibliotheque.NOMS:
        return JSONResponse({"ok": False, "message": "Instance inconnue"}, status_code=400)
    if not re.match(r"^https?://[\w.\-]+(:\d+)?(/[\w.\-/]*)?$", url):
        return JSONResponse({"ok": False, "message": "L'adresse doit ressembler à http://192.168.1.10:7878"}, status_code=400)
    if len(cle) < 16:
        return JSONResponse({"ok": False, "message": "Cette clé semble incomplète"}, status_code=400)
    try:
        version = bibliotheque.tester(url, cle)
    except PermissionError as e:
        erreurs.message(e, "Identifiants refusés par %s." % bibliotheque.TITRES[nom], confidentiels=(cle,))
        return JSONResponse({"ok": False, "message": "%s refuse cette clé" % bibliotheque.TITRES[nom]}, status_code=400)
    except Exception as e:
        return JSONResponse({"ok": False, "message": erreurs.message(e, 'Connexion impossible à %s.' % bibliotheque.TITRES[nom], confidentiels=(cle,))}, status_code=400)
    bibliotheque.sauver(nom, url, cle)
    # Les clés des rangées sont des tuples (nom, page...) : pop("ajoutes") n'effaçait rien.
    for k in [k for k in cache_listes if isinstance(k, tuple) and k[:1] in (("ajoutes",), ("nonvues",))]:
        cache_listes.pop(k, None)
    return {"ok": True, "version": version}


@app.post("/arr/retirer")
def arr_retirer(nom: str = ""):
    if nom not in bibliotheque.NOMS:
        return JSONResponse({"ok": False, "message": "Instance inconnue"}, status_code=400)
    bibliotheque.retirer(nom)
    return {"ok": True}


@app.post("/arr/recherche/relancer")
async def arr_recherche_relancer(request: Request):
    c = await corps_json(request)
    type_ = str(c.get("type", ""))
    try:
        identifiant = int(c.get("id") or 0)
        tvdb = int(c.get("tvdb") or 0) or None
        saison = int(c.get("saison") or 0) or None
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "message": "Identifiant média invalide"}, status_code=400)
    qualite = str(c.get("qualite", ""))
    if type_ not in ("movie", "tv") or identifiant <= 0 or (saison is not None and saison < 1):
        return JSONResponse({"ok": False, "message": "Titre invalide"}, status_code=400)
    if qualite not in ("hd", "uhd"):
        return JSONResponse({"ok": False, "message": "Qualité invalide"}, status_code=400)
    service = ("Sonarr" if type_ == "tv" else "Radarr") + (" 4K" if qualite == "uhd" else " HD")
    try:
        resultat = bibliotheque.relancer_recherche(type_, identifiant, qualite, tvdb, saison)
    except Exception as e:
        message = erreurs.message(e, "Impossible de relancer la recherche dans %s." % service)
        return JSONResponse({"ok": False, "message": message}, status_code=400)
    libelle = "Recherche relancée via %s pour « %s »%s." % (
        resultat["service"], resultat["titre"], " (saison %d)" % saison if saison else "")
    erreurs.journaliser_action("Sonarr" if type_ == "tv" else "Radarr", libelle)
    return {"ok": True, "message": "Recherche relancée dans %s." % resultat["service"]}


# ---------- Suivi d'un téléchargement (2.3) : Radarr et Sonarr, et Transmission s'il est activé ----------
def statut_avec_transmission(st):
    """Lot 2.6.81, point 7 : une qualité en téléchargement prend la progression réelle de Transmission quand son torrent est relié
    avec certitude, par le hash que la file de SA propre instance Radarr ou Sonarr donne (HD et 4K ne se mélangent donc jamais).
    Lecture du cache de la page Téléchargements (transmission.suivi), sans appel bloquant. Copie : le statut en cache reste intact."""
    if not st or st.get("erreur"):
        return st
    out = dict(st)
    for k in ("hd", "uhd"):
        v = out.get(k) or {}
        if v.get("etat") != "telechargement" or not v.get("hashes"):
            continue
        infos = transmission.suivi(v["hashes"])
        if infos:
            r = transmission.resume(infos)
            # Le hash est la seule association fiable entre la qualité ARR et Transmission. Le nom de torrent
            # permet d'identifier la release en cours, sans inférer ni fabriquer une adresse de tracker.
            releases = list(dict.fromkeys(str(x.get("nom") or "").strip() for x in infos.values() if x.get("nom")))
            out[k] = dict(v, progres=r["progres"], restant=transmission.duree_txt(r["restant_s"]) or v.get("restant"),
                          vitesse=r["vitesse"], pairs=r["pairs"], bloque=r["bloque"], erreur_client=r["erreur"],
                          etat_client=r["etat"], source="transmission", release=" · ".join(releases) if releases else None)
    return out


RELECTURE_FILM = {}    # tmdb -> heure de la dernière relecture ciblée
RELECTURE_FILM_LOCK = threading.Lock()
RELECTURE_MEDIA_EN_COURS = set()


def programmer_relecture_media(type_, identifiant, delai_min=30):
    """Rafraîchit Radarr/Sonarr en arrière-plan; la fiche sert le statut actuel sans attendre le NAS."""
    maintenant = time.time()
    cle = (type_, identifiant)
    with RELECTURE_FILM_LOCK:
        if cle in RELECTURE_MEDIA_EN_COURS or maintenant - RELECTURE_FILM.get(cle, 0) < delai_min:
            return False
        RELECTURE_FILM[cle] = maintenant
        RELECTURE_MEDIA_EN_COURS.add(cle)

    def relire():
        try:
            if type_ == "tv":
                bibliotheque.relire_serie(identifiant, delai=5)
            else:
                bibliotheque.relire_film(identifiant, delai=5)
        except Exception:
            pass
        finally:
            with RELECTURE_FILM_LOCK:
                RELECTURE_MEDIA_EN_COURS.discard(cle)

    threading.Thread(target=relire, name="arr-final-%s-%s" % (type_, identifiant), daemon=True).start()
    return True


@app.get("/telechargement")
def telechargement(type: str = "movie", id: int = 0):
    """Progression du téléchargement d'un titre, pour la fiche. La base est la file de Radarr et de Sonarr (progression et temps restant
    déjà lus pour les badges). Si Transmission est activé, le hash du torrent que la file donne permet de lire dans Transmission la
    vitesse, la progression exacte et le temps restant, sans deviner par le nom. Transmission injoignable : on garde Radarr et Sonarr."""
    type_ = "tv" if type == "tv" else "movie"
    try:
        st = statut_avec_transmission(bibliotheque.statut(type_, id, None, True))
    except Exception:
        st = None
    if not st or st.get("erreur"):
        return {"ok": True, "en_cours": False}
    versions = {k: st.get(k) or {} for k in ("hd", "uhd")}
    dl = [v for v in versions.values() if v.get("etat") == "telechargement"]
    if not dl:
        return {"ok": True, "en_cours": False}
    presque_fini = any(v.get("progres") is not None and v["progres"] >= 99 for v in dl)
    programmer_relecture_media(type_, id, 10 if presque_fini else 30)
    v0 = max(dl, key=lambda v: v.get("progres") if v.get("progres") is not None else -1)
    out = {"ok": True, "en_cours": True, "source": v0.get("source"), "progres": v0.get("progres"), "restant": v0.get("restant"),
           "vitesse": v0.get("vitesse"), "pairs": v0.get("pairs"), "bloque": bool(v0.get("bloque")), "erreur": v0.get("erreur_client") or "",
           # Chaque qualité garde sa propre progression : la fiche met ses pastilles HD et 4K à jour séparément.
           "versions": {k: {"etat": v.get("etat"), "progres": v.get("progres"), "restant": v.get("restant"), "source": v.get("source")}
                        for k, v in versions.items()}}
    if v0.get("etat_client"):
        out["etat_client"] = v0["etat_client"]
    return out


@app.get("/transmission/etat")
def transmission_etat():
    return dict({"ok": True}, **transmission.etat())


# Point 83 : message du journal technique, puis message affiché, pour chaque cause d'échec de la lecture Transmission.
MSG_TRANSMISSION = {
    "identifiants": ("Identifiants refusés par Transmission.", "Transmission refuse l’identifiant ou le mot de passe enregistré."),
    "hors_ligne": ("Transmission injoignable.", "Transmission est injoignable à l’adresse enregistrée. Vérifie qu’il tourne sur le NAS."),
    "delai": ("Transmission ne répond pas dans le délai.", "Transmission ne répond pas pour l’instant. Il est peut être très occupé ; nouvel essai automatique."),
    "reponse": ("Réponse inattendue de Transmission.", "Réponse inattendue de Transmission. Consulte le journal pour plus d’informations."),
}


@app.get("/telechargements")
def telechargements():
    """Vue informative limitée, lue directement depuis le RPC Transmission configuré."""
    etat = transmission.etat()
    if not etat.get("configuree"):
        return {"ok": True, "configuree": False, "active": False, "en_cours": [], "termines": [],
                "notifications_non_lues": notifications.lister()["non_lues"]}
    if not etat.get("actif"):
        return {"ok": True, "configuree": True, "active": False, "en_cours": [], "termines": [],
                "notifications_non_lues": notifications.lister()["non_lues"]}
    mdp = (transmission.config().get("pass", ""),)
    debut = time.time()
    try:
        # Lot 2.6.82 : dernière lecture valide tout de suite, relecture de la file en arrière plan (transmission.recents_rapide).
        listes = transmission.recents_rapide(20)
    except Exception as e:
        # Point 83 : une vraie panne s'affiche, avec sa cause ; les coupures passagères ont déjà été absorbées par recents().
        nature = getattr(e, "cause", None) or transmission.cause(e)
        erreurs.message(e, MSG_TRANSMISSION[nature][0], confidentiels=mdp)
        return JSONResponse({"ok": False, "cause": nature, "message": MSG_TRANSMISSION[nature][1]}, status_code=502)
    t_transmission = time.time() - debut
    # Une ligne de Journal par coupure : la relecture se fait maintenant en arrière plan, la route repère donc le début de
    # chaque coupure par sa date plutôt que par le premier échec qu'elle aurait vu elle même.
    if listes and listes.get("perime") and listes.get("incident_debut") and JOURNAL_COUPURE.get("debut") != listes["incident_debut"]:
        JOURNAL_COUPURE["debut"] = listes["incident_debut"]
        erreurs.journaliser_action("Transmission", "Réponse lente ou coupure passagère (%s) : dernières données conservées." % listes["cause"],
                                   "avertissement")
    # Les enrichissements ne concernent pas Transmission : leur panne n'est jamais présentée comme une panne de Transmission.
    try:
        medias = bibliotheque.medias_par_hash()
    except Exception as e:
        erreurs.message(e, "Correspondance média des téléchargements indisponible.")
        medias = {}
    try:
        releases = bibliotheque.historique_releases_par_hash(
            [str(t.get("hash") or "").lower() for cle in ("en_cours", "termines")
             for t in ((listes or {}).get(cle) or [])])
    except Exception as e:
        erreurs.message(e, "Historique des releases indisponible.")
        releases = {}
    noms = [t.get("nom") for cle in ("en_cours", "termines") for t in ((listes or {}).get(cle) or [])]
    try:
        par_nom = bibliotheque.medias_par_nom_torrent(noms)
    except Exception as e:
        erreurs.message(e, "Correspondance par titre des téléchargements indisponible.")
        par_nom = {}
    listes = listes or {"en_cours": [], "termines": []}
    enrichies = {}
    for cle in ("en_cours", "termines"):
        enrichis = []
        for torrent in listes.get(cle) or []:
            item = dict(torrent)
            media = medias.get(str(item.get("hash") or "").lower()) or par_nom.get(item.get("nom"))
            if media:
                affiche = media.get("affiche")
                # Repli sans réseau : une fiche TMDB déjà consultée dans ce processus peut fournir son poster.
                if not affiche and media.get("id") and media.get("type") in ("movie", "tv"):
                    fiche_cache = cache_details.get((media["type"], media["id"]))
                    if fiche_cache and fiche_cache[1].get("poster_path"):
                        affiche = image(fiche_cache[1]["poster_path"], "w185")
                item.update(titre_media=media["titre"], affiche=affiche)
                if media.get("id") and media.get("type") in ("movie", "tv"):
                    item.update(media_type=media["type"], media_id=media["id"])
                hash_ = str(item.get("hash") or "").lower()
                historique = releases.get(hash_) or {}
                # L'historique ARR n'est attribué que si l'instance source de la file correspond sans ambiguïté.
                sources = media.get("instances") or []
                infos = [historique[n] for n in sources if n in historique]
                if len(infos) == 1:
                    info = infos[0]
                    if info.get("release"):
                        item["release"] = info["release"]
                    if info.get("tracker"):
                        item["tracker"] = info["tracker"]
            enrichis.append(item)
        enrichies[cle] = enrichis
    try:
        # Des données resservies pendant une coupure ne sont pas une nouvelle observation : pas de transition à notifier.
        non_lues = notifications.lister()["non_lues"] if listes.get("perime") else notifications.observer_transmission(enrichies)
    except Exception as e:
        erreurs.message(e, "Notifications des téléchargements indisponibles.")
        non_lues = None
    out = {"ok": True, "configuree": True, "active": True, **enrichies, "notifications_non_lues": non_lues,
           "age_s": listes.get("age_s", 0),
           # Mesures de la réponse, lisibles dans l'outil réseau du navigateur : Transmission (ou son cache) et le reste.
           "mesures_ms": {"transmission": round(t_transmission * 1000), "total": round((time.time() - debut) * 1000)}}
    if listes.get("perime"):
        out.update(perime=True, cause=listes.get("cause"))
    if time.time() - debut > 3 and time.time() - JOURNAL_LENT.get("t", 0) > 600:
        JOURNAL_LENT["t"] = time.time()
        erreurs.journaliser_action("Transmission", "Page Téléchargements lente : %s ms, dont %s ms pour Transmission." % (
            out["mesures_ms"]["total"], out["mesures_ms"]["transmission"]), "avertissement")
    return out


JOURNAL_COUPURE, JOURNAL_LENT = {}, {}


@app.get("/notifications")
def notifications_liste():
    return dict({"ok": True}, **notifications.lister())


@app.post("/notifications/ouvrir")
def notifications_ouvrir():
    return dict(ok=True, **notifications.ouvrir())


@app.post("/notifications/supprimer")
def notifications_supprimer():
    return dict(ok=True, **notifications.supprimer_tout())


@app.get("/notifications/push/cle")
def notifications_push_cle(request: Request):
    try:
        return {"ok": True, "cle": push.cle_publique(request.headers.get("x-kamcine-push-subject", ""))}
    except Exception as e:
        erreurs.message(e, "Initialisation Web Push impossible.")
        return JSONResponse({"ok": False, "message": "Notifications système indisponibles sur ce serveur."}, status_code=503)


@app.post("/notifications/push")
async def notifications_push_enregistrer(request: Request):
    corps = await corps_json(request)
    try:
        total = push.enregistrer(corps)
        return {"ok": True, "abonnements": total}
    except ValueError as e:
        return JSONResponse({"ok": False, "message": str(e)}, status_code=400)
    except Exception as e:
        erreurs.message(e, "Enregistrement Web Push impossible.")
        return JSONResponse({"ok": False, "message": "L'abonnement aux notifications n'a pas pu être enregistré."}, status_code=503)


@app.post("/notifications/push/retirer")
async def notifications_push_retirer(request: Request):
    corps = await corps_json(request)
    endpoint = str(corps.get("endpoint") or "")
    if not endpoint.startswith("https://"):
        return JSONResponse({"ok": False, "message": "Abonnement Web Push invalide."}, status_code=400)
    return {"ok": True, "abonnements": push.retirer(endpoint)}


@app.post("/notifications/push/test")
async def notifications_push_test(request: Request):
    corps = await corps_json(request)
    endpoint = str(corps.get("endpoint") or "")
    resultat = await run_in_threadpool(push.envoyer_test, endpoint)
    if resultat.get("ok"):
        return {"ok": True, "message": "Le service Push a accepté l'envoi."}
    codes = {
        "abonnement_invalide": "Abonnement invalide. Réactive les notifications sur cet appareil.",
        "abonnement_absent": "Cet appareil n'est plus enregistré. Réactive les notifications.",
        "abonnement_expire": "L'abonnement de cet appareil a expiré. Réactive les notifications.",
        "abonnement_cle": "Cet abonnement ne correspond plus à la clé Push de l’installation. Réactive les notifications sur cet appareil.",
        "vapid_jwt": "Le service Push a rejeté l’identification VAPID. Vérifie le sujet HTTPS et la clé privée de l’installation.",
        "configuration": "Le service Push a refusé l’identification VAPID. Vérifie le sujet de contact HTTPS et les clés de l’installation.",
        "service_indisponible": "Le service Push ne répond pas. Réessaie dans quelques instants.",
    }
    code = resultat.get("code", "service_indisponible")
    status = 404 if code in ("abonnement_absent", "abonnement_expire") else 409 if code == "abonnement_cle" else 503
    return JSONResponse({"ok": False, "code": code, "message": codes.get(code, codes["service_indisponible"])}, status_code=status)


@app.post("/notifications/lue")
async def notification_lue(request: Request):
    corps = await corps_json(request)
    ident = str(corps.get("id") or "")[:64]
    if not ident:
        return JSONResponse({"ok": False, "message": "Notification invalide."}, status_code=400)
    return {"ok": True, "non_lues": notifications.marquer_lue(ident)}


@app.post("/notifications/tout-lire")
def notifications_tout_lire():
    return {"ok": True, "non_lues": notifications.marquer_tout_lu()}


@app.post("/telechargements/retirer")
async def telechargements_retirer(request: Request):
    """Retire un torrent de Transmission après confirmation, sans toucher aux fichiers locaux."""
    c = await corps_json(request)
    hash_torrent = str(c.get("hash") or "").strip().lower()
    nom = re.sub(r"[\x00-\x1f\x7f]+", " ", str(c.get("nom") or "Téléchargement")).strip()[:160] or "Téléchargement"
    try:
        transmission.retirer_torrent(hash_torrent)
    except ValueError:
        return JSONResponse({"ok": False, "message": "Identifiant de téléchargement invalide."}, status_code=400)
    except Exception as e:
        message = erreurs.message(e, "Impossible de retirer ce téléchargement.",
                                  confidentiels=(transmission.config().get("pass", ""),))
        return JSONResponse({"ok": False, "message": message}, status_code=502)
    ligne("Transmission : téléchargement retiré de la liste, fichiers conservés : " + nom)
    return {"ok": True}


@app.post("/telechargements/termines/retirer")
async def telechargements_termines_retirer(request: Request):
    """Retire les téléchargements terminés récemment sélectionnés, en conservant toujours les fichiers."""
    c = await corps_json(request)
    try:
        noms = transmission.retirer_termines(c.get("hashes"))
    except ValueError:
        return JSONResponse({"ok": False, "message": "Sélection de téléchargements terminés invalide."}, status_code=400)
    except Exception as e:
        message = erreurs.message(e, "Impossible de retirer les téléchargements terminés.",
                                  confidentiels=(transmission.config().get("pass", ""),))
        return JSONResponse({"ok": False, "message": message}, status_code=502)
    if not noms:
        return JSONResponse({"ok": False, "message": "Aucun téléchargement terminé ne peut être retiré."}, status_code=409)
    ligne("Transmission : " + str(len(noms)) + " téléchargement(s) terminé(s) retiré(s), fichiers conservés : " + ", ".join(noms))
    return {"ok": True, "retire": len(noms)}


@app.post("/transmission/tester")
async def transmission_tester(request: Request):
    """Teste la connexion avec ce qui est dans le formulaire ; un mot de passe laissé vide reprend celui qui est enregistré."""
    c = await corps_json(request)
    url, user, mdp = str(c.get("url") or "").strip(), str(c.get("utilisateur") or "").strip(), str(c.get("mot_de_passe") or "")
    st = transmission.config()
    url = url or st.get("url") or ""
    if not mdp and user == (st.get("user") or ""):
        mdp = st.get("pass") or ""
    try:
        version = transmission.tester(url, user, mdp)
    except ValueError as e:
        return JSONResponse({"ok": False, "message": str(e) if type(e) is ValueError and str(e).startswith("L'adresse") else erreurs.message(e, 'Connexion impossible à Transmission.', confidentiels=(mdp,))}, status_code=400)
    except PermissionError as e:
        erreurs.message(e, "Identifiants refusés par Transmission.", confidentiels=(mdp,))
        return JSONResponse({"ok": False, "message": "Transmission refuse cet identifiant ou ce mot de passe"}, status_code=400)
    except Exception as e:
        return JSONResponse({"ok": False, "message": erreurs.message(e, 'Connexion impossible à Transmission.', confidentiels=(mdp,))}, status_code=400)
    return {"ok": True, "version": version}


@app.post("/transmission/config")
async def transmission_config(request: Request):
    """Enregistre l'adresse et l'identifiant (secrets.json) après un test réussi. Ne l'active pas : l'activation est le réglage
    transmission_actif. Le mot de passe n'est jamais renvoyé à l'interface."""
    c = await corps_json(request)
    url, user, mdp = str(c.get("url") or "").strip(), str(c.get("utilisateur") or "").strip(), str(c.get("mot_de_passe") or "")
    st = transmission.config()
    if not mdp and user == (st.get("user") or ""):
        mdp = st.get("pass") or ""
    try:
        version = transmission.tester(url, user, mdp)
        transmission.sauver(url, user, mdp)
    except ValueError as e:
        return JSONResponse({"ok": False, "message": str(e) if type(e) is ValueError and str(e).startswith("L'adresse") else erreurs.message(e, 'Connexion impossible à Transmission.', confidentiels=(mdp,))}, status_code=400)
    except PermissionError as e:
        erreurs.message(e, "Identifiants refusés par Transmission.", confidentiels=(mdp,))
        return JSONResponse({"ok": False, "message": "Transmission refuse cet identifiant ou ce mot de passe"}, status_code=400)
    except Exception as e:
        return JSONResponse({"ok": False, "message": erreurs.message(e, 'Connexion impossible à Transmission.', confidentiels=(mdp,))}, status_code=400)
    return {"ok": True, "version": version}


@app.post("/transmission/retirer")
def transmission_retirer():
    transmission.retirer()
    c = charger()
    if c["transmission_actif"]:
        c["transmission_actif"] = False
        sauver(c)
    return {"ok": True}


# ---------- Test de connexion des services connectés (Réglages) ----------
@app.post("/services/tester")
def services_tester(nom: str = ""):
    """Teste un service avec ce qui est déjà enregistré (aucun secret n'arrive ni ne repart) : ok et une phrase claire. Un service non configuré le dit."""
    titre = dict(tmdb="TMDB", omdb="OMDb", overseerr="Overseerr", youtube="YouTube", transmission="Transmission", trakt="Trakt", **bibliotheque.TITRES).get(nom, "ce service")

    def non():
        return {"ok": False, "configure": False, "message": "Pas encore configuré"}
    try:
        if nom == "tmdb":
            if not lire_secrets().get("tmdb"):
                return non()
            tmdb_get("/configuration")
            return {"ok": True, "configure": True, "message": "TMDB répond"}
        if nom == "overseerr":
            if not bibliotheque.ov_cle():
                return non()
            r = bibliotheque.ov_appel("GET", "/auth/me")
            if r.status_code != 200:
                return {"ok": False, "configure": True, "message": erreurs.message("HTTP %d : %s" % (r.status_code, r.text), "Connexion refusée par Overseerr.")}
            return {"ok": True, "configure": True, "message": "Overseerr répond"}
        if nom in bibliotheque.NOMS:
            c = (bibliotheque.config().get(nom) or {})
            if not c.get("cle"):
                return non()
            if not c.get("url"):
                return {"ok": False, "configure": True, "message": "Adresse de %s absente : configurez de nouveau l'instance." % bibliotheque.TITRES[nom]}
            v = bibliotheque.tester(c["url"], c["cle"])
            return {"ok": True, "configure": True, "message": "%s répond%s" % (bibliotheque.TITRES[nom], (" (version %s)" % v) if v else "")}
        if nom == "transmission":
            c = transmission.config()
            if not c.get("url"):
                return non()
            v = transmission.tester(c["url"], c.get("user", ""), c.get("pass", ""))
            return {"ok": True, "configure": True, "message": "Transmission répond (%s)" % v}
        if nom == "youtube":
            cle = filmsactu.cle_api()
            if not cle:
                return non()
            filmsactu.tester_cle(cle)
            return {"ok": True, "configure": True, "message": "L'API YouTube répond"}
        if nom == "trakt":
            if not trakt.connectee():
                return {"ok": False, "configure": trakt.configuree(), "message": "Pas connecté à Trakt"}
            r = trakt._appel("GET", "/users/settings")
            if r.status_code != 200:
                return {"ok": False, "configure": True, "message": erreurs.message("HTTP %d : %s" % (r.status_code, r.text), "Connexion refusée par Trakt.")}
            return {"ok": True, "configure": True, "message": "Trakt répond"}
        if nom == "omdb":
            cle = lire_secrets().get("omdb")
            if not cle:
                return non()
            ok, msg = omdb_tester(cle)
            return {"ok": ok, "configure": True, "message": msg}
    except PermissionError as e:
        return {"ok": False, "configure": True, "message": erreurs.message(e, "Connexion impossible à %s." % titre)}
    except Exception as e:
        return {"ok": False, "configure": True, "message": erreurs.message(e, "Connexion impossible à %s." % titre)}
    return JSONResponse({"ok": False, "message": "Service inconnu"}, status_code=400)


# ---------- Raccourcis Siri ----------
@app.get("/reglages/raccourci")
def raccourci_etat():
    return {"token": lire_secrets().get("raccourci")}


@app.post("/reglages/raccourci")
def raccourci_creer():
    s = lire_secrets()
    s["raccourci"] = secrets.token_urlsafe(16)
    ecrire_prive(SECRETS, s)
    return {"token": s["raccourci"]}


@app.post("/reglages/raccourci/retirer")
def raccourci_retirer():
    s = lire_secrets()
    s.pop("raccourci", None)
    ecrire_prive(SECRETS, s)
    return {"token": None}


@app.api_route("/raccourci/{token}/{action}", methods=["GET", "POST"])
def raccourci(token: str, action: str, mode: str = "", type: str = "auto"):
    t = lire_secrets().get("raccourci")
    if not t or not hmac.compare_digest(token, t):
        return JSONResponse({"ok": False, "message": "Adresse invalide"}, status_code=403)
    if action == "start":
        return start(mode=mode, type=type)
    if action == "stop":
        return stop()
    if action == "playpause":
        return telecommande("play_pause")
    if action == "veille":
        return appletv("off")
    if action == "reveil":
        return appletv("on")
    return JSONResponse({"ok": False, "message": "Action inconnue"}, status_code=404)


# ---------- Catalogue : états, récents, liste, reprise ----------
def _cles_items(items):
    out = []
    for x in items.split(",")[:60]:
        if "-" in x:
            t, i = x.split("-", 1)
            if t in ("movie", "tv") and i.isdigit():
                out.append((t, int(i)))
    return out


def _etat_collection_fiable(st, complet=False):
    """Une absence est exploitable seulement si toutes les sources configurées ont répondu récemment."""
    if not st or st.get("incomplet") or st.get("erreur") or not st.get("sources"):
        return False
    if any(valeur != "ok" for valeur in st["sources"].values()):
        return False
    cfg = charger()
    mode = cfg.get("badges_source", "auto")
    etat_cache = bibliotheque.etat_cache().get("listes", {})
    limite = max(60, int(cfg.get("badges_cache_min", 5)) * 60)
    attendues = []
    if mode in ("auto", "arr"):
        arr = bibliotheque.config()
        attendues = [n for n in bibliotheque.NOMS if n.startswith(("radarr_", "sonarr_"))
                     and n in st["sources"] and (arr.get(n) or {}).get("cle")]
        if mode == "arr" and not attendues:
            return False
        for nom in attendues:
            source = etat_cache.get(nom) or {}
            age = source.get("age_s")
            if (st["sources"].get(nom) != "ok" or source.get("origine") != "reseau"
                    or age is None or age > limite or source.get("derniere_erreur")):
                return False
    if mode in ("auto", "overseerr") and bibliotheque.ov_cle():
        if st["sources"].get("overseerr") != "ok":
            return False
        if not complet:
            source = etat_cache.get("overseerr") or {}
            age = source.get("age_s")
            if (source.get("origine") != "reseau" or age is None or age > limite
                    or source.get("derniere_erreur")):
                return False
    return bool(attendues or (mode in ("auto", "overseerr") and bibliotheque.ov_cle()))


def etat_poster_public(version, demande_locale=False):
    """Ramène l'état Radarr/Sonarr/Overseerr au vocabulaire partagé des posters.

    La recherche/ surveillance ARR seule n'est pas une demande utilisateur. Un état orange
    n'est émis que pour une entrée réelle de file (déjà normalisée en telechargement).
    """
    if not isinstance(version, dict):
        return None
    brut = version.get("etat")
    source = version.get("source")
    if brut in ("disponible", "partiel"):
        return "available"
    if brut == "telechargement":
        return "downloading"
    demande = bool(demande_locale or version.get("demande"))
    # Le vocabulaire « requested » reste interne aux intégrations. Côté KamCiné,
    # une demande future est tout de même « En recherche… » ; « À venir » est
    # réservé à l'action avant demande.
    if demande:
        return "requested"
    if brut == "attente":
        return "requested"
    if brut == "recherche":
        return "requested" if source == "overseerr" or demande else None
    if brut == "a_venir":
        return "upcoming"
    return None


def _collection_membres(collection_id):
    saga = tmdb_get("/collection/%d" % collection_id)
    membres = {x["id"]: x for x in saga.get("parts", [])
               if isinstance(x.get("id"), int) and x["id"] > 0 and x.get("title")}
    if not membres or len(membres) > 60:
        raise ValueError("Collection vide ou trop grande pour une demande sûre")
    return saga.get("name") or "Collection", membres


def _statuts_collection(collection_id, complet=False):
    titre, membres = _collection_membres(collection_id)
    identifiants = list(membres)

    def lire(identifiant):
        try:
            st = bibliotheque.statut("movie", identifiant, None, complet)
        except Exception:
            st = None
        return identifiant, st

    with ThreadPoolExecutor(max_workers=4) as ex:
        lus = dict(ex.map(lire, identifiants))
    items, candidats, fiables = {}, {"hd": [], "uhd": []}, {"hd": True, "uhd": True}
    manquants_locaux = set()
    demandes_locales = lire_demandes()
    for identifiant in identifiants:
        st = appliquer_demandes_locales("movie", identifiant, lus[identifiant], demandes_locales)
        fiable = _etat_collection_fiable(st, complet)
        qualites = {}
        for qualite, cle in (("hd", "hd"), ("uhd", "uhd")):
            val = ((st or {}).get(cle) or {}).get("etat", "inconnu")
            manquant_local = deja_demande("movie", identifiant, qualite, demandes_locales)
            if manquant_local and val in ("absent", "non_suivi"):
                # Une demande réussie mais pas encore visible dans l'index ne doit jamais être répétée.
                val = "attente"
                manquants_locaux.add(identifiant)
            connu = fiable and val in ("disponible", "partiel", "attente", "recherche", "telechargement", "a_venir", "absent", "non_suivi")
            qualites[qualite] = val if connu else "inconnu"
            if not connu:
                fiables[qualite] = False
            elif val in ("absent", "non_suivi"):
                candidats[qualite].append(identifiant)
        items[str(identifiant)] = {"titre": membres[identifiant].get("title") or "", "hd": qualites["hd"], "uhd": qualites["uhd"]}
    return {"titre": titre, "ids": identifiants, "items": items, "candidats": candidats,
            "fiables": fiables, "demandes_locales": sorted(manquants_locaux)}


@app.get("/catalogue/etats")
def catalogue_etats(items: str = "", actualiser: int = 0):
    """Disponibilité des jaquettes, renvoyée tout de suite depuis la mémoire. pret est faux tant que des listes n'ont pas
    fini d'arriver : l'interface réessaie et complète les jaquettes au fur et à mesure. Le statut vu est servi à part."""
    if actualiser:
        # Réservé aux rafraîchissements explicites de la fiche/saga : remplace les index ET les statuts calculés.
        bibliotheque.rafraichir_films()
        bibliotheque.vider()
    sources = bool(lire_secrets().get("overseerr") or bibliotheque.config())
    etats, pret, age = {}, True, None
    demandes_locales = lire_demandes()
    for t, i in _cles_items(items):
        try:
            d = statut_avec_transmission(bibliotheque.statut(t, i, None, False)) if sources else None
        except Exception:
            d = None
        d = appliquer_demandes_locales(t, i, d, demandes_locales)
        dispo = None
        if d:
            if d.get("incomplet"):
                pret = False
                # une réponse positive reste vraie même si une autre liste n'est pas encore arrivée
                dispo = d["global"] if d["global"] in ("disponible", "partiel") else None
            else:
                dispo = d["global"]
            if d.get("age_s") is not None:
                age = max(age or 0, d["age_s"])
        progres = None
        if d and dispo == "telechargement":
            # même mémoire que la disponibilité, aucun appel de plus : sert à la fine barre de progression de la jaquette
            ps = [v.get("progres") for v in (d.get("hd") or {}, d.get("uhd") or {}) if v.get("etat") == "telechargement" and v.get("progres") is not None]
            progres = max(ps) if ps else None
        hd, uhd = (d or {}).get("hd") or {}, (d or {}).get("uhd") or {}
        etats["%s-%d" % (t, i)] = {"cle": "%s-%d" % (t, i), "dispo": dispo, "progres": progres,
                                   "hd": hd.get("etat", "inconnu"), "uhd": uhd.get("etat", "inconnu"),
                                   "hd_poster": etat_poster_public(hd, deja_demande(t, i, "hd", demandes_locales)),
                                   "uhd_poster": etat_poster_public(uhd, deja_demande(t, i, "uhd", demandes_locales)),
                                   "fiable": _etat_collection_fiable(d, False),
                                   "deja_demande": deja_demande(t, i, demandes=demandes_locales)}
    return {"ok": True, "etats": etats, "pret": pret, "age_s": age}


@app.post("/catalogue/collection/demander")
async def catalogue_collection_demander(request: Request):
    c = await corps_json(request)
    try:
        collection_id = int(c.get("collection_id", 0))
    except (TypeError, ValueError):
        collection_id = 0
    if collection_id <= 0:
        return JSONResponse({"ok": False, "message": "Collection invalide"}, status_code=400)
    if not bibliotheque.ov_cle():
        return JSONResponse({"ok": False, "message": "Overseerr n'est pas configuré."}, status_code=400)
    action_ = c.get("action", "preview")
    if action_ not in ("preview", "confirmer"):
        return JSONResponse({"ok": False, "message": "Action de collection invalide"}, status_code=400)
    versions = c.get("versions", "both")
    qualites = {"hd": ["hd"], "uhd": ["uhd"], "both": ["hd", "uhd"]}.get(versions) if isinstance(versions, str) else None
    if action_ == "confirmer" and not qualites:
        return JSONResponse({"ok": False, "message": "Qualité invalide"}, status_code=400)
    try:
        if action_ == "preview":
            # La fiche précharge cet aperçu sur les index mémoire/disque. statut() demande déjà au
            # rafraîchisseur partagé de compléter les listes si elles sont périmées, sans bloquer l'API.
            etat_collection = _statuts_collection(collection_id, complet=False)
        else:
            # Une confirmation explicite relit les sources puis revalide exactement les candidats.
            await run_in_threadpool(bibliotheque.rafraichir_films)
            bibliotheque.vider()
            etat_collection = _statuts_collection(collection_id, complet=False)
    except Exception as e:
        message = erreurs.message(e, "Impossible de vérifier la collection avant la demande.")
        return JSONResponse({"ok": False, "message": message}, status_code=400)
    if action_ == "preview":
        return {"ok": True, **etat_collection}
    attendus = c.get("candidats") or {}
    if not isinstance(attendus, dict):
        return JSONResponse({"ok": False, "message": "Prévisualisation invalide"}, status_code=400)
    for qualite in qualites:
        nouveaux = etat_collection["candidats"][qualite]
        if not etat_collection["fiables"][qualite]:
            return JSONResponse({"ok": False, "message": "État de la collection incertain. Aucune demande n'a été envoyée."}, status_code=409)
        liste_attendue = attendus.get(qualite)
        if (not isinstance(liste_attendue, list)
                or any(not str(x).isdigit() for x in liste_attendue)
                or sorted(set(int(x) for x in liste_attendue)) != sorted(nouveaux)):
            return JSONResponse({"ok": False, "message": "L'état de la saga a changé. Vérifie à nouveau avant de confirmer."}, status_code=409)
    if not any(etat_collection["candidats"][q] for q in qualites):
        return JSONResponse({"ok": False, "message": "Aucun film manquant dans les qualités choisies."}, status_code=409)

    resultats, reussis, echoues = [], [], []
    par_id = {int(k): v["titre"] for k, v in etat_collection["items"].items()}
    for qualite in qualites:
        for identifiant in etat_collection["candidats"][qualite]:
            nom_qualite = "4K" if qualite == "uhd" else "HD"
            try:
                resultat = envoyer_demande("movie", identifiant, qualite).get(qualite, {"ok": False, "message": "Demande impossible."})
            except Exception as e:
                # Une anomalie isolée ne doit pas interrompre les autres demandes de la saga.
                resultat = {"ok": False, "message": erreurs.message(e, "Demande impossible à Overseerr.")}
            entree = {"id": identifiant, "titre": par_id.get(identifiant, "Film"), "qualite": nom_qualite,
                      "ok": bool(resultat.get("ok")), "message": resultat.get("message", "")}
            resultats.append(entree)
            if entree["ok"]:
                noter_demande("movie", identifiant, qualite)
                noter_activite(request, "demande", "movie", identifiant, "collection %s" % collection_id)
                reussis.append(entree)
                erreurs.journaliser_action("Overseerr", "Demande %s envoyée pour « %s » (collection %s)." % (nom_qualite, entree["titre"], collection_id))
            else:
                echoues.append(entree)
                erreurs.journaliser_action("Overseerr", "Échec demande %s pour « %s » (collection %s)." % (nom_qualite, entree["titre"], collection_id), "erreur")
    erreurs.journaliser_action("Overseerr", "%d demande(s) réussie(s), %d échec(s) pour la collection %s." % (len(reussis), len(echoues), collection_id), "erreur" if echoues else "succes")
    invalider_demandes_recentes()
    return {"ok": True, "envoyees": len(reussis), "echecs": echoues, "resultats": resultats}


@app.get("/catalogue/vus")
def catalogue_vus(items: str = ""):
    """Statuts de visionnage et progression locale des jaquettes, renvoyés par lot sans appel par carte."""
    elements = _cles_items(items)
    v = vus_source()
    progressions = suivi.progressions_catalogue(
        ["%s-%d" % (t, i) for t, i in elements], v["films"], v["episodes"])
    if not charger()["badges_vu_actif"]:
        return {"ok": True, "actif": False, "vus": {}, "progressions": progressions, "pret": True}
    bibliotheque.demander()
    out = {}
    for t, i in elements:
        out["%s-%d" % (t, i)] = (i in v["films"]) if t == "movie" else (i in v["series"])
    return {"ok": True, "actif": True, "vus": out, "progressions": progressions, "pret": v["pret"], "source": v["source"]}


@app.get("/catalogue/diagnostic")
def catalogue_diagnostic(relancer: int = 0):
    """Chronomètre chaque source de la bibliothèque (Radarr, Sonarr, Overseerr, Trakt). À ouvrir dans le navigateur."""
    return diagnostic.etat(bool(relancer))


@app.post("/catalogue/recents/supprimer")
def recents_supprimer(type: str = "movie", id: int = 0):
    try:
        with open(RECENTS, encoding="utf-8") as f:
            liste = json.load(f)
    except Exception:
        liste = []
    ecrire_prive(RECENTS, [x for x in liste if not (x["type"] == type and x["id"] == id)])
    return {"ok": True}


@app.post("/catalogue/recents/vider")
def recents_vider():
    ecrire_prive(RECENTS, [])
    return {"ok": True}


@app.get("/catalogue/reprendre")
def catalogue_reprendre():
    return {"ok": True, "resultats": suivi.a_reprendre()}


@app.post("/catalogue/reprendre/retirer")
def catalogue_reprendre_retirer(type: str = "movie", id: int = 0):
    """Retire un film ou une série de Continuer la séance (masque noté sur le NAS, l'historique vu et les statistiques ne bougent pas)."""
    return {"ok": True, "cle": suivi.retirer_reprise("tv" if type == "tv" else "movie", id)}


@app.post("/catalogue/reprendre/annuler")
def catalogue_reprendre_annuler(type: str = "movie", id: int = 0):
    """Annule un retrait de Continuer la séance fait il y a quelques secondes."""
    return {"ok": suivi.annuler_retrait_reprise("tv" if type == "tv" else "movie", id)}


@app.get("/catalogue/vus-recemment")
def catalogue_vus_recemment():
    """Les derniers titres vus d'après l'historique local (un fichier, aucun appel réseau). Un titre n'apparaît qu'une fois, un épisode
    représente sa série. Vide si rien n'a encore été vu : l'accueil masque alors la rangée."""
    vus, out = set(), []
    for x in suivi.historique():
        k = (x.get("type"), x.get("id"))
        if k in vus or not x.get("id") or not x.get("titre"):
            continue
        vus.add(k)
        out.append({"id": x["id"], "type": x.get("type"), "titre": x["titre"], "affiche": x.get("affiche"), "annee": x.get("annee") or "",
                    "saison": x.get("saison"), "episode": x.get("episode")})
        if len(out) >= 14:
            break
    return {"ok": True, "resultats": out}


def suivi_vu(type: str = "movie", id: int = 0, saison: int = 0, episode: int = 0, vu: int = 1):
    type = "tv" if type == "tv" else "movie"
    s_, e_ = saison or None, episode or None
    if type == "tv" and not (s_ and e_):
        return JSONResponse({"ok": False, "message": "Choisis un épisode"}, status_code=400)
    if vu:
        meta = meta_par_id(type, id, s_, e_)
        if not meta:
            return JSONResponse({"ok": False, "message": "Titre introuvable"}, status_code=404)
        suivi.marquer_vu({"type": type, "id": id, "saison": s_, "episode": e_, "titre": meta["titre"],
                          "affiche": meta["affiche"], "fond": meta["fond"], "duree": meta.get("duree") or 0})
    else:
        suivi.retirer_vu(type, id, s_, e_)
    cache_listes.pop("nonvues", None)
    if type == "tv":
        for k in [k for k in cache_tmdb if isinstance(k, tuple) and len(k) >= 3
                  and k[0] == "fiche" and k[1] == "tv" and k[2] == id]:
            cache_tmdb.pop(k, None)
    return {"ok": True}


def episodes_diffusees(id_):
    """Tous les épisodes déjà diffusés d'une série (saisons 1 et suivantes, pas les épisodes spéciaux), d'après TMDB. Une saison qui ne répond
    pas fait échouer l'ensemble : mieux vaut ne rien marquer qu'une série à moitié."""
    c = cache_episodes_diffusees.get(id_)
    if c and time.time() - c[0] < 900:
        return c[1]
    f = details("tv", id_)
    aujourdhui = planning.local_iso(time.time())[:10]
    numeros = [x["season_number"] for x in f.get("seasons") or [] if (x.get("season_number") or 0) >= 1]
    def une(n):
        d = tmdb_get("/tv/%d/season/%d" % (id_, n))
        return [(n, e["episode_number"]) for e in d.get("episodes", []) if (e.get("air_date") or "9999") <= aujourdhui]
    with ThreadPoolExecutor(4) as ex:
        resultat = [x for r in ex.map(une, numeros) for x in r]
    cache_episodes_diffusees[id_] = (time.time(), resultat)
    return resultat


def suivi_serie(id: int = 0, vu: int = 1):
    """Marquer une série entière comme vue ou non vue : même règle que pour un épisode (Trakt d'abord pour un retrait), sur tous les
    épisodes diffusés."""
    meta = meta_par_id("tv", id)
    if not meta:
        return JSONResponse({"ok": False, "message": "Série introuvable"}, status_code=404)
    trakt_etat, trakt_msg = "non_connecte", ""
    if trakt.connectee():
        try:
            trakt.ecrire_vu("tv", id, vu=bool(vu))
            trakt.memoire_vu("tv", id, vu=bool(vu))
            trakt_etat = "ecrit"
        except Exception as e:
            trakt_etat, trakt_msg = "echec", erreurs.message(e, 'Synchronisation impossible.')
            if not vu:
                return JSONResponse({"ok": False, "message": "Non modifié : " + trakt_msg}, status_code=502)
    if vu:
        try:
            eps = episodes_diffusees(id)
        except Exception as e:
            return JSONResponse({"ok": False, "message": erreurs.message(e, 'Synchronisation impossible.')}, status_code=502)
        suivi.marquer_plusieurs([{"type": "tv", "id": id, "saison": s_, "episode": e_, "titre": meta["titre"], "affiche": meta.get("affiche"),
                                  "fond": meta.get("fond"), "duree": meta.get("duree") or 0} for s_, e_ in eps])
        if trakt_etat == "ecrit":
            for s_, e_ in eps:
                trakt.memoire_vu("tv", id, s_, e_, True)
    else:
        suivi.retirer_serie(id)
    cache_listes.pop("nonvues", None)
    return {"ok": True, "vu": bool(vu), "trakt": trakt_etat, "trakt_message": trakt_msg}


# ---------- Bandes annonces sur la TV : toujours FilmsActu ----------
# Avant la 2.2, la fiche envoyait la première vidéo YouTube de TMDB, de n'importe quelle chaîne. Maintenant rien ne part sans une
# vérification de la chaîne. Deux sources, la plus sûre d'abord : 1. le flux de la chaîne FilmsActu (l'identifiant de chaîne de chaque
# entrée est contrôlé, voir rss_chaine) ; 2. les vidéos que TMDB connaît pour ce titre, gardées seulement si l'oEmbed officiel de YouTube
# dit qu'elles sont de la chaîne FilmsActu (nom et adresse @FilmsActu, l'oEmbed ne donne pas l'identifiant de chaîne).
cache_ba_fa = {}
BA_LANCEE = {"d": None}
_mots = filmsactu.mots
chaine_de_video = filmsactu.chaine_de_video


def correspond_ba(titre_video, cibles, annee):
    return filmsactu.correspond(titre_video, cibles, annee)[0]


def _cibles_ba(type_, id_):
    """(meta, titres possibles, année) d'un titre : titre français, titre original."""
    meta = meta_par_id(type_, id_)
    if not meta:
        return None, [], None
    cibles = [meta["titre"]]
    try:
        f = details_complets(type_, id_)
        cibles.append(f.get("original_title") or f.get("original_name"))
    except Exception:
        pass
    return meta, [c for c in cibles if c], (None if type_ == "tv" else meta.get("annee"))     # la vidéo d'une série porte l'année de la saison


def _videos_tmdb(type_, id_):
    return lambda langue: tmdb_get("/%s/%d/videos" % (type_, id_), langue=langue)["results"]


def _chercher_ba(type_, id_, avec_trace=False):
    meta, cibles, annee = _cibles_ba(type_, id_)
    if not meta:
        return {"dispo": False, "message": "Titre introuvable", "raison": "titre_introuvable", "chaine": CHAINE_NOM}
    base = {"type": type_, "id": id_, "titre": meta["titre"], "annee": meta.get("annee"), "affiche": meta.get("affiche"),
            "chaine": CHAINE_NOM, "chaine_id": CHAINE_ID, "duree": None}
    res = filmsactu.chercher(cibles, annee, _videos_tmdb(type_, id_), est_serie=(type_ == "tv"))
    if not avec_trace:
        res.pop("trace", None)
    return dict(base, **res)


def bande_annonce_filmsactu(type_, id_):
    """Résultat mis en cache : dispo, cle, chaine, verification, ou dispo faux avec la vraie raison (message, detail, raison). Un résultat
    trouvé reste ba_cache_min minutes ; un refus ba_cache_neg_min minutes (une vidéo peut arriver, une clé peut être ajoutée) ; une réponse
    due à YouTube injoignable n'est jamais gardée."""
    k = (type_, id_)
    c = cache_ba_fa.get(k)
    cfg = charger()
    if c and time.time() - c[0] < (cfg["ba_cache_min"] if c[1].get("dispo") else cfg["ba_cache_neg_min"]) * 60:
        return c[1]
    res = _chercher_ba(type_, id_)
    if not filmsactu.transitoire(res):
        cache_ba_fa[k] = (time.time(), res)
    return res


@app.get("/catalogue/bande-annonce")
def catalogue_bande_annonce(type: str = "movie", id: int = 0):
    """Vérification avant d'envoyer une bande annonce sur la TV : rien ne part sans elle. Toujours en 200, dispo dit le résultat."""
    type = "tv" if type == "tv" else "movie"
    f = cache_film["data"] or {}
    return dict(bande_annonce_filmsactu(type, id), ok=True, seance_en_cours=en_cours(), film_en_lecture=bool(f.get("actif")))


@app.get("/catalogue/bande-annonce/diagnostic")
def bande_annonce_diagnostic(type: str = "movie", id: int = 0, titre: str = "", annee: str = ""):
    """Ce que fait la recherche pour un titre, étape par étape : requêtes envoyées, résultats reçus, chaîne de chaque candidat, verdict et
    raison. Donner titre (et annee) pour tester un titre sans passer par TMDB. Ne met rien en cache. À ouvrir dans le navigateur."""
    type_ = "tv" if type == "tv" else "movie"
    if titre.strip():
        cibles, an, meta, videos = [titre.strip()], (annee or None), {"titre": titre.strip(), "annee": annee or None}, None
    else:
        meta, cibles, an = _cibles_ba(type_, id)
        videos = _videos_tmdb(type_, id) if meta else None
        if not meta:
            return JSONResponse({"ok": False, "message": "Titre introuvable (TMDB)"}, status_code=404)
    res = filmsactu.chercher(cibles, an, videos, est_serie=(type_ == "tv"))
    arch = filmsactu.index()
    dates = sorted(x.get("pub") or "" for x in arch if x.get("pub"))
    return {"ok": True, "titre_cherche": cibles, "annee": an, "type": type_, "verdict": "trouvée" if res["dispo"] else "non disponible",
            "raison": res.get("raison"), "message": res.get("message"), "detail": res.get("detail"), "cle": res.get("cle"),
            "verification": res.get("verification"), "trace": res["trace"],
            "archive": {"videos": len(arch), "plus_ancienne": dates[0][:10] if dates else None, "plus_recente": dates[-1][:10] if dates else None},
            "cle_youtube": bool(filmsactu.cle_api()), "regle": "Uniquement la chaîne FilmsActu (%s), contrôlée par identifiant de chaîne" % CHAINE_ID}


@app.get("/youtube/etat")
def youtube_etat():
    arch = filmsactu.index()
    return {"ok": True, "configuree": bool(filmsactu.cle_api()), "archive": len(arch)}


@app.post("/youtube/cle")
async def youtube_cle(request: Request):
    """Clé de l'API YouTube Data v3, pour chercher une bande annonce dans toute la chaîne FilmsActu (100 unités de quota par recherche, 10 000
    par jour, résultats gardés 24 h). Testée avant d'être enregistrée (secrets.json, jamais renvoyée)."""
    cle = str((await corps_json(request)).get("cle", "")).strip()
    if not re.match(r"^[\w-]{20,60}$", cle):
        return JSONResponse({"ok": False, "message": "Cette clé semble incomplète ou mal copiée"}, status_code=400)
    try:
        nom = filmsactu.tester_cle(cle)
    except PermissionError as e:
        return JSONResponse({"ok": False, "message": erreurs.message(e, 'Connexion impossible à YouTube.', confidentiels=(cle,))}, status_code=400)
    except Exception as e:
        return JSONResponse({"ok": False, "message": erreurs.message(e, 'Connexion impossible à YouTube.', confidentiels=(cle,))}, status_code=400)
    ecrire_prive(SECRETS, dict(lire_secrets(), youtube=cle))
    cache_ba_fa.clear()
    return {"ok": True, "chaine": nom}


@app.post("/youtube/retirer")
def youtube_retirer():
    s_ = lire_secrets()
    s_.pop("youtube", None)
    ecrire_prive(SECRETS, s_)
    cache_ba_fa.clear()
    return {"ok": True}


def ba_enregistrer(res, origine):
    BA_LANCEE["d"] = {"type": res["type"], "id": res["id"], "titre": res["titre"], "affiche": res.get("affiche"), "cle": res["cle"],
                      "t": time.time(), "vu": 0, "origine": origine}


@app.post("/lancer/bande-annonce")
def lancer_bande_annonce(type: str = "movie", id: int = 0):
    type = "tv" if type == "tv" else "movie"
    res = bande_annonce_filmsactu(type, id)
    if not res.get("dispo"):
        return JSONResponse({"ok": False, "message": res.get("message") or "Bande annonce FilmsActu non disponible"}, status_code=404)
    if en_cours():
        return JSONResponse({"ok": False, "message": "Une séance est en cours"}, status_code=409)
    reveiller()
    ok, sortie = atv("launch_app=youtube://www.youtube.com/watch?v=" + res["cle"], delai=15)
    if ok:
        ba_enregistrer(res, "hors_seance")
    return {"ok": ok, "message": "" if ok else "L'Apple TV refuse la commande : " + sortie[-100:], "cle": res["cle"]}


@app.post("/lancer/youtube")
def lancer_youtube(cle: str = ""):
    """Ancienne route, gardée mais verrouillée : elle n'envoie qu'une vidéo dont la chaîne FilmsActu a été vérifiée."""
    if en_cours():
        return JSONResponse({"ok": False, "message": "Une séance est en cours"}, status_code=409)
    if not re.match(r"^[\w-]{6,20}$", cle):
        return JSONResponse({"ok": False, "message": "Vidéo invalide"}, status_code=400)
    connues = {x["id"] for x in rss_chaine()} | {x["id"] for x in filmsactu.index()} | {r[1].get("cle") for r in cache_ba_fa.values() if r[1].get("dispo")}
    if cle not in connues:
        return JSONResponse({"ok": False, "message": "Cette vidéo n'est pas vérifiée comme venant de FilmsActu"}, status_code=403)
    reveiller()
    ok, sortie = atv("launch_app=youtube://www.youtube.com/watch?v=" + cle, delai=15)
    return {"ok": ok, "message": "" if ok else "L'Apple TV refuse la commande : " + sortie[-100:]}


# ---------- Profil : statistiques et vus récemment ----------
def carte_par_id(type_, id_):
    k = (type_, id_)
    if k not in _cartes:
        try:
            f = tmdb_get("/%s/%d" % (type_, id_))
        except Exception:
            return None
        c = carte(dict(f, media_type=type_))
        c["fond"] = image(f.get("backdrop_path"), "w780")
        _cartes[k] = c
    return _cartes[k]


@app.get("/profil/resume")
def profil_resume(request: Request):
    """Profil (2.6.104). personnel : ce compte seulement (favoris, nombre de demandes et de séances lancées depuis KamCiné).
    stats, films, series : la salle, commune à l'installation (ce qu'a lu l'Apple TV, ou le compte Trakt de l'installation),
    présentée comme telle par l'interface, jamais comme l'activité de la personne connectée."""
    uid = id_connecte(request)
    personnel = {"favoris": len(comptes.favoris(uid)), "demandes": comptes.compter_activite(uid, "demande", distincts=True),
                 "seances": comptes.compter_activite(uid, "seance")}
    st, source = suivi.stats(), "KamCiné, détection locale"
    films, series = [], []
    t = trakt.stats() if trakt.connectee() else None
    if t:
        source = "Infuse, via Trakt"
        st.update(films=t["films"], episodes=t["episodes"], series=t["series"], minutes=t["minutes"])
        mois = trakt.par_mois(6)
        if mois:
            st["mois"] = mois
        r = trakt.recents()
        tous = r["films"] + r["series"]
        with ThreadPoolExecutor(max_workers=6) as ex:
            for x, c in zip(tous, ex.map(lambda x: carte_par_id(x["type"], x["id"]), tous)):
                if c:
                    liste = films if x["type"] == "movie" else series
                    if not any(y.get("id") == c.get("id") for y in liste):       # une seule affiche par série
                        liste.append(dict(c, saison=x.get("saison"), episode=x.get("episode")))
    else:
        h = suivi.historique()
        films = [x for x in h if x.get("type") == "movie"][:12]
        deja = set()
        for x in h:
            if x.get("type") == "tv" and x["id"] not in deja:
                deja.add(x["id"])
                series.append(x)
    fond = next((x.get("fond") for x in films + series if x.get("fond")), None)
    return {"ok": True, "stats": st, "source": source, "films": films[:12], "series": series[:12], "fond": fond,
            "favoris": {"films": favoris.liste(uid, "movie"), "series": favoris.liste(uid, "tv")}, "personnel": personnel}


@app.post("/favoris/basculer")
async def favoris_basculer(request: Request):
    c = await corps_json(request)
    try:
        type_ = str(c.get("type") or "")
        id_ = int(c.get("id") or 0)
        etat_ = favoris.basculer(id_connecte(request), type_, id_, c.get("titre"), c.get("affiche"), c.get("annee"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "message": "Film ou série invalide."}, status_code=400)
    except (OSError, comptes.sqlite3.Error) as e:
        message = erreurs.message(e, "Impossible d'enregistrer le favori.")
        return JSONResponse({"ok": False, "message": message}, status_code=500)
    return {"ok": True, "favori": etat_}


# ---------- Trakt (synchronisation avec Sofa Time) ----------
@app.get("/trakt/etat")
def trakt_etat():
    return trakt.etat()


@app.post("/trakt/config")
async def trakt_config(request: Request):
    c = await corps_json(request)
    cid, sec = str(c.get("client_id", "")).strip(), str(c.get("client_secret", "")).strip()
    if len(cid) < 20 or len(sec) < 20:
        return JSONResponse({"ok": False, "message": "Identifiant ou secret incomplet"}, status_code=400)
    ancienne = trakt.config()
    if (ancienne.get("client_id"), ancienne.get("client_secret")) != (cid, sec):
        # Un jeton OAuth appartient à l'application qui l'a émis : ne pas le
        # conserver si les identifiants de l'application changent.
        trakt.deconnecter()
    trakt.sauver(client_id=cid, client_secret=sec)
    return {"ok": True}


@app.post("/trakt/connecter")
def trakt_connecter():
    if not trakt.configuree():
        return JSONResponse({"ok": False, "message": "Enregistre d'abord l'identifiant et le secret"}, status_code=400)
    try:
        return dict(trakt.demarrer_appareil(), ok=True)
    except Exception as e:
        return JSONResponse({"ok": False, "message": erreurs.message(e, 'Connexion impossible à Trakt.')}, status_code=400)


@app.post("/trakt/verifier")
def trakt_verifier():
    try:
        return dict(trakt.verifier_appareil(), ok=True)
    except Exception as e:
        return JSONResponse({"ok": False, "message": erreurs.message(e, 'Connexion impossible à Trakt.')}, status_code=400)


@app.post("/trakt/deconnecter")
def trakt_deconnecter():
    trakt.deconnecter()
    return {"ok": True}


@app.post("/trakt/oublier")
def trakt_oublier():
    trakt.oublier()
    return {"ok": True}


@app.post("/suivi/vu")
def suivi_vu_utilisateur(type: str = "movie", id: int = 0, saison: int = 0, episode: int = 0, vu: int = 1):
    """La coche de la fiche. Trakt est le point commun (Infuse et Sofa Time s'y branchent) : on y écrit d'abord, et l'état local suit toujours.
    Réponse honnête : trakt vaut ecrit, non_connecte ou echec. Un retrait que Trakt refuse n'est pas fait, pour que les deux ne divergent
    pas ; une marque que Trakt refuse est gardée dans KamCiné, avec l'avertissement."""
    type = "tv" if type == "tv" else "movie"
    if not id:
        return JSONResponse({"ok": False, "message": "Titre inconnu"}, status_code=400)
    trakt_etat, trakt_msg = "non_connecte", ""
    if trakt.connectee():
        try:
            trakt.ecrire_vu(type, id, saison or None, episode or None, bool(vu))
            trakt.memoire_vu(type, id, saison or None, episode or None, bool(vu))
            trakt_etat = "ecrit"
        except Exception as e:
            trakt_etat, trakt_msg = "echec", erreurs.message(e, 'Synchronisation impossible avec Trakt.')
            if not vu:
                return JSONResponse({"ok": False, "message": "Non modifié : " + trakt_msg}, status_code=502)
    r = suivi_vu(type=type, id=id, saison=saison, episode=episode, vu=vu)
    if isinstance(r, JSONResponse):
        return r
    r.update(vu=bool(vu), trakt=trakt_etat, trakt_message=trakt_msg)
    return r


@app.get("/suivi/episodes/precedents")
def suivi_episodes_precedents(id: int = 0, saison: int = 0, episode: int = 0):
    """Compte les épisodes déjà diffusés et non vus qui précèdent l'épisode choisi."""
    if not id or not saison or not episode:
        return JSONResponse({"ok": False, "message": "Choisis un épisode"}, status_code=400)
    try:
        precedents = [(s, e) for s, e in episodes_diffusees(id)
                      if (s < saison or (s == saison and e < episode))]
    except Exception as e:
        return JSONResponse({"ok": False, "message": erreurs.message(e, "Épisodes précédents indisponibles.")}, status_code=502)
    vus = vus_source()["episodes"]
    manquants = [(s, e) for s, e in precedents if (id, s, e) not in vus]
    return {"ok": True, "nombre": len(manquants)}


@app.post("/suivi/episodes/precedents")
def suivi_marquer_episodes_precedents(id: int = 0, saison: int = 0, episode: int = 0):
    """Marque l'épisode choisi et tous ses épisodes antérieurs diffusés, après accord explicite."""
    if not id or not saison or not episode:
        return JSONResponse({"ok": False, "message": "Choisis un épisode"}, status_code=400)
    try:
        precedents = [(s, e) for s, e in episodes_diffusees(id)
                      if (s < saison or (s == saison and e < episode))]
        vus = vus_source()["episodes"]
        manquants = [(s, e) for s, e in precedents if (id, s, e) not in vus]
        episodes = sorted(set(manquants + [(saison, episode)]))
        meta = meta_par_id("tv", id)
        if not meta:
            return JSONResponse({"ok": False, "message": "Série introuvable"}, status_code=404)
        trakt_etat, trakt_msg = "non_connecte", ""
        if trakt.connectee():
            try:
                trakt.ecrire_episodes_vus(id, episodes, vu=True)
                for s, e in episodes:
                    trakt.memoire_vu("tv", id, s, e, True)
                trakt_etat = "ecrit"
            except Exception as e:
                trakt_etat, trakt_msg = "echec", erreurs.message(e, "Synchronisation impossible avec Trakt.")
        suivi.marquer_plusieurs([{"type": "tv", "id": id, "saison": s, "episode": e,
                                  "titre": meta["titre"], "affiche": meta.get("affiche"),
                                  "fond": meta.get("fond"), "duree": meta.get("duree") or 0}
                                 for s, e in episodes])
        cache_listes.pop("nonvues", None)
        for k in [k for k in cache_tmdb if isinstance(k, tuple) and len(k) >= 3
                  and k[0] == "fiche" and k[1] == "tv" and k[2] == id]:
            cache_tmdb.pop(k, None)
        return {"ok": True, "vu": True, "trakt": trakt_etat, "trakt_message": trakt_msg,
                "nombre": len(manquants)}
    except Exception as e:
        return JSONResponse({"ok": False, "message": erreurs.message(e, "Marquage des épisodes impossible.")}, status_code=502)


@app.post("/suivi/serie")
def suivi_serie_route(id: int = 0, vu: int = 1):
    return suivi_serie(id=id, vu=vu)


@app.post("/interne/vu")
def interne_vu(request: Request, type: str = "movie", id: int = 0, saison: int = 0, episode: int = 0, vu: int = 1):
    if not request.client or request.client.host not in ("127.0.0.1", "::1"):
        return JSONResponse({"ok": False, "message": "Accès refusé"}, status_code=403)
    return suivi_vu(type=type, id=id, saison=saison, episode=episode, vu=vu)
