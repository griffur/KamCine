import os, re, json, html, random, subprocess, time, asyncio, threading, requests
import pyatv
from pyatv.const import Protocol
from lumieres import Hue
import lecture_infuse
import json_atomique
import chemins
from etat_seance import FinFilm, FIN_APRES_GENERIQUE_S

MODE_TEST = os.environ.get("SEANCE_MODE", "test") != "reel"

ATV_ID = os.environ["ATV_ID"]         # 2.6.106 : donné par le service (integrations.env_seance), plus écrit ici
ATV = ["atvremote", "--id", ATV_ID,
       "--companion-credentials", os.environ["COMP"],
       "--airplay-credentials", os.environ["AIR"]]

INFUSE = "com.firecore.infuse"
MARGE_FIN = 26
TOLERANCE = 3
# Point 90 : une avance manuelle à la télécommande qui dépasse d'un coup l'entracte prévue ne doit pas la déclencher
# en retard une fois remarquée, mais l'annuler (elle reste sautée pour le reste de la séance). Un bond de position
# supérieur au temps réel écoulé par plus que ce seuil est considéré manuel, jamais une lecture normale qui rattrape
# une pause.
SAUT_MANUEL_S = 5
LECTURE_TEST = 5 if MODE_TEST else None
DELAI_TEST = 20
ENTRE = (0.45, 0.60)
ENTRACTE_ID = "ga_66cp4Jrc"
ANNONCE_ID = None
ANNONCE_DUREE = 12
DELAI_LANCEMENT = 0.6
DUREE_RECUL = 5
DELAI_RECUL = 0.2
DELAI_TEST_GENERIQUE = 15
HISTORIQUE = chemins.donnee("historique.json")   # 2.6.101 : dossier des données, plus /cinema en dur
CHAINE = "https://www.youtube.com/feeds/videos.xml?channel_id=UC_i8X3p8oZNaik8X513Zn1Q"

from config import charger
REG = charger()
DUREE_RECUL = int(REG["recul"])
ENTRE = (REG["entracte_debut"] / 100, REG["entracte_fin"] / 100)
ENTRACTE_MIN = min(int(REG["entracte_min"]), int(REG["entracte_max"])) * 60
ENTRACTE_MAX = max(int(REG["entracte_min"]), int(REG["entracte_max"])) * 60
# 2.4 : le choix de cette séance (SEANCE_ENTRACTE, posé par le service à chaque lancement) passe avant le réglage
ENTRACTE_ACTIF = bool(REG["entracte_actif"]) if os.environ.get("SEANCE_ENTRACTE") is None else os.environ["SEANCE_ENTRACTE"] == "1"
MARGE_FIN = int(REG["trailer_marge_fin_s"])
TOLERANCE = int(REG["trailer_tolerance_s"])
TRAILER_ATTENTE = int(REG["trailer_attente_s"])
FILM_ATTENTE = int(REG["film_attente_s"])
ATV_DELAI = int(REG["atv_delai_s"])
# Un média tout juste disponible peut ne pas être encore indexé par Infuse : la fiche ouvre alors sur une recherche
# plutôt que sur la lecture. On retente plusieurs fois (même lien, FILM_ATTENTE à chaque essai) avant de conclure à
# un échec ; app/main.py connaît le même chiffre (NB_ESSAIS_INFUSE) pour afficher une attente plutôt qu'un échec.
NB_ESSAIS_INFUSE = 4

def choix_nb_trailers():
    v = REG["nb_trailers"]
    return random.choice((2, 3)) if v == "auto" else int(v)

def seance_serie():
    print("\nAmbiance douce")
    lumiere("debut", 3)
    print("\nSérie : pas de bandes annonces ni d'entracte")
    if not demarrer_contenu():
        atv("select")
        time.sleep(2)
    lumiere("noir", 4)
    total = 0
    for _ in range(10):
        etat, titre, pos, total = playing()
        print(f"  {etat} | {titre[:30]} | {pos}/{total}s")
        if etat in ("Playing", "Paused") and total > 0:
            break
        time.sleep(2)
    if total > 0:
        # Lot 2.6.89 : le service suit l'épisode lui même (seuil du générique propre aux séries, retour avant le seuil, épisode
        # suivant enchaîné par Infuse) avec le relais, la même boucle que Piloter la séance. Ligne lue par main.py (lecteur).
        print("Épisode confié au service")
        return
    print("\nSéance terminée" if MODE_TEST else "\nSéance suspendue")

def cible_generique(total):
    # Lot 2.6.89 : même règle que le relais du service (generique.seuil), repli séparé pour les films et les séries.
    from generique import seuil
    serie = os.environ.get("SEANCE_TYPE") == "serie"
    genre, ident = os.environ.get("SEANCE_TMDB_TYPE"), os.environ.get("SEANCE_TMDB_ID")
    debut, raison = None, "réglage fixe"
    if REG.get("generique_source") != "fixe":
        raison = "non identifié"
        if ident:
            try:
                from generique import credits_debut
                saison = int(os.environ.get("SEANCE_SAISON") or 0) or None
                episode = int(os.environ.get("SEANCE_EPISODE") or 0) or None
                debut = credits_debut(genre, int(ident), saison, episode, total)
            except Exception:
                debut = None
            raison = "minutage introuvable"
    cible, source = seuil(total, serie, debut, REG)
    if debut is not None:
        return cible, f" (source : TheIntroDB, début du générique à {int(debut)}s)"
    minutes = int(REG["generique_minutes_serie"] if serie else REG["generique_minutes"])
    return cible, f" ({source} : {minutes} min avant la fin, {raison})"

def demarrer_contenu():
    url = os.environ.get("SEANCE_INFUSE_URL")
    if not url:
        return False
    cible = json.loads(os.environ.get("SEANCE_META") or "{}")
    for essai in range(NB_ESSAIS_INFUSE):
        if essai:
            p = lecture_observee()
            if (p.get("app") in (None, "", lecture_infuse.INFUSE_ID) and p.get("etat") in ("Playing", "Paused")
                    and p.get("total") and not (p.get("titre") or "").strip() and not (p.get("serie_nom") or "").strip()):
                # Lot 2.6.90 : Infuse lit déjà un flux dont le titre n'est pas publié. Un nouveau lien rouvrirait la fiche et
                # relancerait la lecture par dessus : on attend l'identité au lieu de renvoyer.
                print("  Infuse lit déjà sans titre publié : pas de nouvel envoi du lien")
            else:
                print("\nLancement du film via Infuse : " + url)
                enchainer([("launch", url)])
        else:
            print("\nLancement du film via Infuse : " + url)
            enchainer([("launch", url)])
        debut = time.time()
        while time.time() - debut < FILM_ATTENTE:
            p = lecture_observee()
            etat, titre, pos, total = p["etat"], p["titre"], p["pos"], p["total"]
            print(f"  {etat} | {titre[:30]} | {pos}/{total}s")
            if lecture_infuse.correspond(p, cible):
                print("Lecture confirmée : %s:%s:%s:%s" % (os.environ.get("SEANCE_TMDB_TYPE"),
                      os.environ.get("SEANCE_TMDB_ID"), os.environ.get("SEANCE_SAISON") or 0,
                      os.environ.get("SEANCE_EPISODE") or 0))
                return True
            time.sleep(0.5)
    print("Infuse n'a pas démarré la lecture : ce titre est il dans ta bibliothèque Infuse ?")
    lumiere("entracte", 3)
    liaison.fermer()
    raise SystemExit

GENERIQUE = {"atteint": False, "fin": False, "t": None}

def marquer_termine(total):
    if MODE_TEST or not GENERIQUE["atteint"]:
        return
    genre, ident = os.environ.get("SEANCE_TMDB_TYPE"), os.environ.get("SEANCE_TMDB_ID")
    if not ident:
        return
    try:
        requests.post("http://127.0.0.1:8765/interne/vu", timeout=15, params={
            "type": genre, "id": ident, "saison": os.environ.get("SEANCE_SAISON") or 0,
            "episode": os.environ.get("SEANCE_EPISODE") or 0, "vu": 1})
        print("Titre marqué comme vu")
    except Exception as e:
        print("Titre non marqué comme vu :", e)

def etapes_retour():
    e = [("launch", INFUSE), ("attendre", DELAI_RECUL)]
    if DUREE_RECUL > 0:
        e.append(("skip_backward", DUREE_RECUL))
    return e

def atv(*args):
    try:
        return subprocess.run(ATV + list(args), capture_output=True, text=True, timeout=ATV_DELAI).stdout
    except subprocess.TimeoutExpired:
        return ""

class Liaison:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self.tv = None
        self.progres = 0

    def _run(self, coro, delai=30):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(delai)

    async def _connecter(self):
        confs = await pyatv.scan(self.loop, identifier=ATV_ID, timeout=5)
        if not confs:
            raise RuntimeError("Apple TV introuvable")
        conf = confs[0]
        conf.set_credentials(Protocol.Companion, os.environ["COMP"])
        conf.set_credentials(Protocol.AirPlay, os.environ["AIR"])
        self.tv = await pyatv.connect(conf, self.loop)

    async def _fermer(self):
        if self.tv is not None:
            await asyncio.gather(*self.tv.close())

    async def _lecture(self):
        p = await asyncio.wait_for(self.tv.metadata.playing(), min(ATV_DELAI, 4))
        try:
            app = self.tv.metadata.app
        except Exception:
            app = None
        try:
            veille = self.tv.power.power_state.name.lower() == "off"
        except Exception:
            veille = False
        return {"etat": p.device_state.name, "titre": p.title or "", "media": p.media_type.name,
                "pos": int(p.position or 0), "total": int(p.total_time or 0),
                "app": app.identifier if app else None, "veille": veille,
                "serie_nom": p.series_name or "", "saison_n": p.season_number or 0,
                "episode_n": p.episode_number or 0}

    async def _etapes(self, etapes):
        rc = self.tv.remote_control
        while self.progres < len(etapes):
            e = etapes[self.progres]
            if e[0] == "select":
                await rc.select()
            elif e[0] == "skip_backward":
                try:
                    await rc.skip_backward(e[1])
                except TypeError:
                    await rc.skip_backward()
            elif e[0] == "launch":
                await self.tv.apps.launch_app(e[1])
            elif e[0] == "attendre":
                await asyncio.sleep(e[1])
            self.progres += 1

    def connecter(self):
        self._run(self._connecter(), 30)

    def fermer(self):
        try:
            self._run(self._fermer(), 10)
        except Exception:
            pass
        self.tv = None

    def reconnecter(self):
        self.fermer()
        try:
            self.connecter()
        except Exception as e:
            print(f"  (reconnexion impossible : {e})")

    def envoyer(self, etapes):
        self.progres = 0
        for _ in range(2):
            try:
                if self.tv is None:
                    self.connecter()
                self._run(self._etapes(etapes), 30)
                return True
            except Exception as e:
                print(f"  (liaison Apple TV : {e}, nouvelle connexion)")
                self.fermer()
        return False

liaison = Liaison()

def enchainer(etapes):
    if liaison.envoyer(etapes):
        return
    print("  (mode secours, une commande après l'autre)")
    for etape in etapes[liaison.progres:]:
        if etape[0] == "select":
            atv("select")
        elif etape[0] == "skip_backward":
            # Même durée que la voie directe (réglage recul), syntaxe atvremote commande=argument (2.6.98).
            atv("skip_backward=%d" % int(etape[1]))
        elif etape[0] == "launch":
            atv("launch_app=" + etape[1])
        elif etape[0] == "attendre":
            time.sleep(etape[1])

try:
    hue = Hue()
except Exception as e:
    hue = None
    print(f"  (lumières Hue indisponibles : {e})")

def lumiere(nom, *args):
    if hue is None:
        return
    def tache():
        try:
            getattr(hue, nom)(*args)
        except Exception as e:
            print(f"  (erreur lumières : {e})")
    threading.Thread(target=tache, daemon=True).start()

def norm(s):
    return re.sub(r"\W+", "", s.lower())

def lecture_observee():
    if liaison.tv is not None:
        try:
            return liaison._run(liaison._lecture(), min(ATV_DELAI, 5))
        except Exception:
            pass
    return lecture_infuse.depuis_cli(atv("playing", "app", "power_state"))


def playing():
    p = lecture_observee()
    return p["etat"], p["titre"], p["pos"], p["total"]

def lecture_valide(essais=5, pause=2):
    """Relit l'état jusqu'à avoir une durée totale. Une lecture ratée renvoie 0 : elle ne doit pas piloter l'entracte ni le générique."""
    for _ in range(essais):
        etat, titre, pos, total = playing()
        if total > 0 and etat in ("Playing", "Paused"):
            break
        time.sleep(pause)
    return etat, titre, pos, total


def duree_youtube(vid):
    try:
        h = requests.get("https://www.youtube.com/watch?v=" + vid,
                         headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "fr-FR,fr;q=0.9"},
                         cookies={"CONSENT": "YES+1"}, timeout=10).text
        m = re.search(r'"lengthSeconds":"(\d+)"', h)
        return int(m.group(1)) if m else None
    except Exception:
        return None

def charger_historique():
    try:
        with open(HISTORIQUE) as f:
            return json.load(f)
    except Exception:
        return []

def sauver_historique(liste):
    try:
        json_atomique.ecrire(HISTORIQUE, liste)
    except Exception as e:
        print("Historique non sauvegardé :", e)

def choisir_trailers():
    x = requests.get(CHAINE, timeout=10).text
    items = re.findall(r"<yt:videoId>(.*?)</yt:videoId>.*?<title>(.*?)</title>", x, re.S)
    trailers = [(v, html.unescape(t)) for v, t in items if "bande annonce" in t.lower()]
    n = choix_nb_trailers()
    vus = [] if MODE_TEST else charger_historique()
    nouveaux = [t for t in trailers if t[0] not in vus]
    choisis = nouveaux[:n]
    if len(choisis) < n:
        deja = [t for t in trailers if t[0] in vus]
        choisis += deja[:n - len(choisis)]
    print(f"{n} bandes annonces (les plus récentes non vues)")
    if not MODE_TEST:
        sauver_historique((vus + [v for v, _ in choisis])[-40:])
    return choisis

def jouer_trailers(choisis, durees, premier_lance=False):
    cles = []
    for i, (vid, titre) in enumerate(choisis, 1):
        attendu = durees.get(vid)
        print(f"\nTrailer {i}/{len(choisis)} : {titre}")
        print(f"  Durée attendue : {attendu if attendu else 'inconnue'}")
        if not (i == 1 and premier_lance):
            atv("launch_app=youtube://www.youtube.com/watch?v=" + vid)
        cle = norm(titre)[:15]
        cles.append(cle)
        debut = time.time()
        demarre = False
        while time.time() - debut < 900:
            etat, t, pos, total = playing()
            titre_ok = cle in norm(t)
            if attendu:
                est_trailer = titre_ok and abs(total - attendu) <= TOLERANCE
                fin = attendu
            else:
                est_trailer = titre_ok and total >= 60
                fin = total
            print(f"  {etat} | {t[:30]} | {pos}/{total}s | {'trailer' if est_trailer else 'pub ou chargement'}")
            if est_trailer:
                demarre = True
                if etat == "Paused":
                    atv("play")
                if pos >= (LECTURE_TEST or fin - MARGE_FIN):
                    if i == len(choisis):
                        lumiere("noir", 5)
                    break
            elif not demarre and time.time() - debut > TRAILER_ATTENTE:
                print("  Trailer non démarré, on passe")
                break
            time.sleep(1)
    return cles

def attendre_lecture(exclus, delai=25, respecter_pause=False):
    debut = time.time()
    secours = False
    while time.time() - debut < delai:
        etat, t, pos, total = playing()
        print(f"  {etat} | {t[:30]} | {pos}/{total}s")
        if (etat == "Playing" or respecter_pause and etat == "Paused") and total > 0 and not any(c in norm(t) for c in exclus):
            return pos
        if time.time() - debut > 6 and not secours:
            secours = True
            if etat == "Idle":
                print("  Le film ne repart pas, on appuie sur Lecture")
                atv("select")
            elif etat == "Paused" and not respecter_pause:
                print("  Le film est en pause, on envoie lecture")
                atv("play")
        time.sleep(1)
    return None

def attendre_confirmation(cible, delai=20):
    """Filet de sécurité pour attendre_lecture() : le film a déjà été confirmé une fois avant les bandes annonces
    (demarrer_contenu, position posée à titre indicatif), une reprise un peu lente après une longue coupure
    (rebufferisation, Infuse au premier plan qui met un instant à répondre) ne doit pas faire perdre le pilotage
    de toute la séance restante. Continue à redemander la lecture tant que le bon film reste en pause ; n'accepte
    que la vraie reprise (Playing), pas une simple pause reconnue, pour ne jamais laisser croire que la séance
    est confirmée alors qu'elle reste bloquée."""
    debut = time.time()
    while time.time() - debut < delai:
        p = lecture_observee()
        print(f"  {p['etat']} | {p['titre'][:30]} | {p['pos']}/{p['total']}s")
        if not lecture_infuse.correspond(p, cible):
            time.sleep(1)
            continue
        if p["etat"] == "Playing":
            return True
        print("  Toujours en pause après les bandes annonces, on envoie lecture")
        atv("play")
        time.sleep(1)
    return False

def lancer_video(vid, mot_cle, delai=30):
    atv("launch_app=youtube://www.youtube.com/watch?v=" + vid)
    debut = time.time()
    while time.time() - debut < delai:
        etat, t, pos, total = playing()
        print(f"  {etat} | {t[:30]} | {pos}/{total}s")
        if mot_cle in norm(t):
            return True
        time.sleep(1)
    return False

def faire_entracte(titre, pos, total, cible=None):
    if cible is not None:
        print("Entracte réarmée après retour avant le seuil")
    elif MODE_TEST:
        cible = pos + DELAI_TEST
    else:
        cible = int(total * random.uniform(*ENTRE))
        if cible < pos + 60:
            cible = int(pos + (total - pos) * random.uniform(0.35, 0.65))
    print(f"\nFilm : {titre} | position {pos}s sur {total}s")
    print(f"Entracte prévue à {cible}s ({cible * 100 // max(total, 1)} %)")
    suivi_fin = FinFilm(json.loads(os.environ.get("SEANCE_META") or "null"))
    dernier_p, dernier_t = pos, time.time()
    while True:
        observe = lecture_observee()
        resultat = suivi_fin.observer(observe)
        etat, t, p, tot = observe["etat"], observe["titre"], observe["pos"], observe["total"]
        print(f"  {etat} | {p}/{tot}s")
        if resultat in ("terminee", "suspendue"):
            print("Le film s'est arrêté, on annule l'entracte")
            return
        if resultat in ("lecture", "pause"):
            maintenant = time.time()
            saut = (p - dernier_p) - (maintenant - dernier_t)
            dernier_p, dernier_t = p, maintenant
            if p >= cible:
                if saut > SAUT_MANUEL_S:
                    print(f"Avance manuelle au delà de l'entracte prévue ({p}s) : entracte annulée")
                    return cible
                if resultat == "lecture":
                    break
        time.sleep(2)
    avant = p
    duree = 15 if MODE_TEST else random.randint(ENTRACTE_MIN, ENTRACTE_MAX)
    print(f"\nENTRACTE : écran entracte pour {duree} secondes (le film se met en pause tout seul)")
    lumiere("entracte", 8 if MODE_TEST else 25)
    debut_ecran = time.time()
    if not lancer_video(ENTRACTE_ID, "entracte"):
        print("La vidéo entracte ne s'est pas lancée")
    liaison.reconnecter()
    reste = duree - (time.time() - debut_ecran)
    if reste > 0:
        time.sleep(reste)
    if ANNONCE_ID:
        print("Annonce de reprise")
        atv("launch_app=youtube://www.youtube.com/watch?v=" + ANNONCE_ID)
        time.sleep(ANNONCE_DUREE + 3)
    print("\nReprise du film : retour à Infuse et recul immédiat")
    enchainer(etapes_retour())
    lumiere("noir", 4)
    reprise = attendre_lecture(["entracte"])
    if reprise is None:
        print("Le film n'a pas repris, regarde l'écran")
    else:
        print(f"  Position avant l'entracte : {avant}s | reprise à : {reprise}s")

def attendre_generique(total, entracte_prevu=None):
    if MODE_TEST:
        cible_t, infos_t = cible_generique(total)
        print(f"Minutage du générique : vers {cible_t}s{infos_t}")
        print(f"\nMode test : générique simulé dans {DELAI_TEST_GENERIQUE} secondes")
        time.sleep(DELAI_TEST_GENERIQUE)
        print("Générique : TV gauche et droite")
        lumiere("generique", 8)
        time.sleep(10)
        print("Fin du test des lumières")
        lumiere("noir", 3)
        return
    if total <= 0:
        total = lecture_valide(10, 3)[3]
    if total <= 0:
        return
    cible, infos = cible_generique(total)
    print(f"\nGénérique attendu vers {cible}s sur {total}s{infos}")
    suivi_fin = FinFilm(json.loads(os.environ.get("SEANCE_META") or "null"))
    while True:
        p = lecture_observee()
        resultat = suivi_fin.observer(p)
        if resultat in ("terminee", "suspendue"):
            # Film quitté après le générique : la séance est finie, pas interrompue.
            GENERIQUE["fin"] = resultat == "terminee" or GENERIQUE["atteint"]
            print("Fin naturelle du film" if resultat == "terminee" else (
                "Fin de séance au générique" if GENERIQUE["fin"] else "Le film s'est arrêté, séance conservée"))
            return
        if resultat in ("lecture", "pause") and entracte_prevu is not None and p.get("pos", 0) < entracte_prevu:
            entracte_prevu = faire_entracte(p.get("titre", ""), p["pos"], total, cible=entracte_prevu)
            suivi_fin.patienter()
            continue
        if resultat == "lecture" and p.get("pos", 0) >= cible and not GENERIQUE["atteint"]:
            GENERIQUE["atteint"], GENERIQUE["t"] = True, time.time()
            print("\nGénérique : TV gauche et droite")
            lumiere("generique", 20)
        if GENERIQUE["atteint"] and time.time() - GENERIQUE["t"] >= FIN_APRES_GENERIQUE_S:
            GENERIQUE["fin"] = True
            print("Fin de séance au générique")
            return
        time.sleep(3)

if os.environ.get("SEANCE_TYPE") == "serie":
    seance_serie()
    raise SystemExit
print("\nAmbiance douce")
lumiere("debut", 3)
class Desactivees(Exception):
    pass
try:
    if os.environ.get("SEANCE_BANDES_ANNONCES") == "0":     # 2.4 : décochées à la fenêtre Lancer la séance
        raise Desactivees()
    choisis = choisir_trailers()
except Exception as e:
    choisis = []
    print(f"Aucune bande annonce disponible ({type(e).__name__}), la séance continue sans")
else:
    if not choisis:
        print("Aucune bande annonce trouvée, la séance continue sans")
if choisis:
    durees = {vid: duree_youtube(vid) for vid, _ in choisis}
    premier = choisis[0][0]
try:
    liaison.connecter()
except Exception as e:
    print(f"  (connexion directe impossible : {e})")
if choisis:
    print("\nLancement du film puis ouverture immédiate des trailers")
    if demarrer_contenu():
        enchainer([("launch", "youtube://www.youtube.com/watch?v=" + premier)])
    else:
        enchainer([("select",), ("attendre", DELAI_LANCEMENT),
               ("launch", "youtube://www.youtube.com/watch?v=" + premier)])
    lumiere("trailers", 5)
    cles = jouer_trailers(choisis, durees, premier_lance=True)
    print("\nRetour au film : retour à Infuse et recul immédiat")
    enchainer(etapes_retour())
else:
    print("\nDémarrage du film, sans bande annonce")
    if not demarrer_contenu():
        enchainer([("select",)])
    lumiere("noir", 5)
    cles = []
if attendre_lecture(cles, respecter_pause=not choisis) is None:
    cible = json.loads(os.environ.get("SEANCE_META") or "null")
    if not (cible and attendre_confirmation(cible)):
        print("Le film n'a pas repris, on arrête")
        lumiere("entracte", 2)
        time.sleep(1)
        liaison.fermer()
        raise SystemExit
etat, titre, pos, total = lecture_valide()
entracte_prevu = None
if ENTRACTE_ACTIF and total > 0:
    entracte_prevu = faire_entracte(titre, pos, total)
attendre_generique(total, entracte_prevu)
marquer_termine(total)
time.sleep(2)
liaison.fermer()
print("\nSéance terminée" if MODE_TEST or GENERIQUE["fin"] else "\nSéance suspendue")
