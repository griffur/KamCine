"""État de l'Apple TV en continu : une seule connexion ouverte, plus un processus lancé à chaque question."""
import asyncio, os, threading, time
import re
import hashlib
import concurrent.futures
from types import SimpleNamespace
import chemins
import etat_seance
from observation import ObservationLedger
from urllib.parse import urlparse, parse_qs


def cle_image(p):
    """Sépare les images de lecteurs ou de contenus différents même à titre identique."""
    if not p.get("hash"):
        return None
    cle = repr((p.get("app"), p["hash"], p.get("content_identifier")))
    return hashlib.sha256(cle.encode("utf-8")).hexdigest()


def lien_youtube(p):
    """N'utilise que l'identifiant de contenu fourni par le lecteur, jamais son hash."""
    if p.get("app") not in ("com.google.ios.youtube", "com.google.youtube"):
        return None
    identifiant = p.get("content_identifier") or ""
    if not isinstance(identifiant, str):
        return None
    video = identifiant if re.fullmatch(r"[A-Za-z0-9_-]{11}", identifiant) else None
    try:
        u = urlparse(identifiant)
        if u.scheme in ("http", "https") and not u.username and not u.password:
            if u.hostname == "youtu.be":
                video = u.path.strip("/")
            elif u.hostname in ("youtube.com", "www.youtube.com", "m.youtube.com") and u.path == "/watch":
                video = parse_qs(u.query).get("v", [None])[0]
    except ValueError:
        return None
    return "https://www.youtube.com/watch?v=" + video if video and re.fullmatch(r"[A-Za-z0-9_-]{11}", video) else None


def _delai_lecture():
    """Temps maximal d'une lecture d'état (réglage atv_delai_lecture_s). Au delà, la connexion est refaite."""
    try:
        from config import charger
        return charger()["atv_delai_lecture_s"]
    except Exception:
        return 15


def _pas_actif():
    """Intervalle entre deux lectures quand quelqu'un regarde l'écran (réglage atv_pas_actif_s, 2 s par défaut, 3 s avant la
    2.6 : lot J, point 3, pour que l'app en premier plan et ce qui joue soient à jour plus vite)."""
    try:
        from config import charger
        return charger()["atv_pas_actif_s"]
    except Exception:
        return 2

# 2.6.106 : identifiant et identifiants d'appairage de l'Apple TV lus dans integrations.py à chaque connexion.


def jeton_lecture(p):
    cle = tuple(p.get(k) for k in ("app", "titre", "hash", "content_identifier", "serie_nom", "saison_n", "episode_n"))
    return hashlib.sha256(repr(cle).encode("utf-8")).hexdigest()


def plan_commande(p, cmd):
    if not etat_seance.media_reel(p):
        return None
    f = p.get("fonctions", {})
    if cmd == "play_pause":
        explicite = "pause" if p["etat"] == "Playing" else "play"
        if f.get(explicite):
            return explicite, ()
        return ("play_pause", ()) if f.get("play_pause") else None
    if cmd in ("back", "forward"):
        if f.get(cmd):
            return ("skip_backward" if cmd == "back" else "skip_forward"), (10,)
        if f.get("position") and p.get("position_connue"):
            pos = max(0, p["pos"] + (-10 if cmd == "back" else 10))
            if p.get("total", 0) > 0:
                pos = min(pos, p["total"])
            return "set_position", (pos,)
    return None


def vue_lecteur(p):
    actif = etat_seance.media_reel(p)
    p = p or {}
    # Le repli CLI ne publie pas les capacités : ses commandes restent essayables.
    commandes = p.get("commandes", {k: True for k in ("play_pause", "back", "forward")})
    return {"actif": actif, "etat": p.get("etat"), "app": p.get("app"), "titre": p.get("titre") or "",
            "pos": p.get("pos", 0), "total": p.get("total", 0), "observe_a": p.get("lu_a", 0),
            "jeton": jeton_lecture(p) if actif else "",
            "commandes": {k: bool(actif and commandes.get(k)) for k in ("play_pause", "back", "forward")}}


class Live:
    def __init__(self):
        self.observations = ObservationLedger(chemins.donnee("observations.json"))
        self.etat = None
        self.t = 0
        self.pas = 3
        self.demande = time.time()
        self.erreur = ""
        self.erreurs_consecutives = 0
        self.fil = None
        self.boucle = None
        self.reveil = None
        self._atv = None
        self._target_id = None
        self._lecture_lock = None
        self._commande_lock = threading.Lock()
        self.jaquette = None        # {h, octets, mime} : l'image du contenu en lecture, quand pyatv la fournit
        self._combo = (None, None)  # (app, etat) : pour dater depuis quand ça n'a pas changé (lot J, point 3)
        self._combo_t = time.time()

    def demarrer(self):
        if self.fil:
            return
        self.fil = threading.Thread(target=self._run, daemon=True)
        self.fil.start()

    def age(self):
        """Âge en secondes du dernier état lu, ou None s'il n'y en a pas."""
        return (time.time() - self.t) if self.etat is not None else None

    def _maj_combo(self, nouveau, t):
        """Ajoute nouveau['depuis'] : depuis combien de secondes la paire (app, état) n'a pas changé. Lot J, point 3 : sert à
        repérer une app restée affichée en pause sans preuve qu'elle est encore réellement à l'écran (pyatv ne dit rien de
        fiable quand on quitte une app vers l'accueil de l'Apple TV sans la fermer, voir contenu_ailleurs dans app/main.py)."""
        combo = tuple(nouveau.get(k) for k in ("app", "etat", "titre", "pos", "total"))
        if combo != self._combo:
            self._combo, self._combo_t = combo, t
        nouveau["depuis"] = t - self._combo_t

    def reconnecter(self):
        """Réveille la boucle : elle constate le changement d'Apple TV active et se reconnecte (2.6.108)."""
        if self.boucle and self.reveil:
            try:
                self.boucle.call_soon_threadsafe(self.reveil.set)
            except Exception:
                pass

    @property
    def target_id(self):
        return self._target_id

    def cibler(self, identifiant=None):
        """Cible de lecture en mémoire pour le suivi d'une séance ; le défaut reste l'Apple TV globale configurée."""
        identifiant = str(identifiant or "").strip() or None
        if identifiant == self._target_id:
            return
        self._target_id = identifiant
        self.reconnecter()

    def lire(self):
        """Dernier état connu, ou None si la connexion n'est pas établie ou trop ancienne."""
        self.demande = time.time()
        age = time.time() - self.t
        if self.etat is not None and age > _pas_actif() and self.boucle and self.reveil:
            try:
                self.boucle.call_soon_threadsafe(self.reveil.set)
            except Exception:
                pass
        # Une lecture vide/une coupure ponctuelle ne doit pas effacer immédiatement le
        # dernier média connu. On tolère trois échecs et au plus 20 s depuis la dernière
        # observation : le délai est borné même si l'intervalle de polling est élevé.
        grace = min(max(self.pas * 2 + 8, 20 if self.erreurs_consecutives else 0), 20)
        if self.etat is not None and age < grace and self.erreurs_consecutives < 3:
            return self.observe(dict(self.etat, lu_a=self.t), "live_cache")
        return None

    def frais(self, delai=3.0):
        """Force une lecture tout de suite et attend qu'elle arrive."""
        t0 = self.t
        if self.boucle and self.reveil and self.etat is not None:
            try:
                self.boucle.call_soon_threadsafe(self.reveil.set)
            except Exception:
                pass
            fin = time.time() + delai
            while time.time() < fin and self.t == t0:
                time.sleep(0.1)
        return self.lire()

    def _run(self):
        try:
            asyncio.run(self._boucle())
        except Exception as e:
            self.erreur = str(e)[:120]

    def commander(self, cmd, attendu=""):
        """None signifie aucune connexion, avant tout envoi. Aucun rejeu après un délai dépassé."""
        if not self._atv or not self.boucle or not self.boucle.is_running():
            return None
        if not self._commande_lock.acquire(blocking=False):
            return {"ok": False, "message": "Une commande est déjà en cours."}
        futur = None
        try:
            futur = asyncio.run_coroutine_threadsafe(self._commander(cmd, attendu), self.boucle)
            return futur.result(timeout=8)
        except concurrent.futures.TimeoutError:
            if futur:
                futur.cancel()
            return {"ok": False, "message": "L’Apple TV n’a pas confirmé la commande. Vérifie le lecteur avant de réessayer."}
        except Exception:
            return {"ok": False, "message": "La connexion au lecteur a été interrompue."}
        finally:
            self._commande_lock.release()

    def _rapide(self, fn, delai=4):
        """Envoie une commande simple sur la connexion continue (menu, pause, ouverture d'une app), sans vérifier le
        média : pour les besoins d'administration (quitter un film, arrêter une lecture ailleurs), pas les commandes
        de lecture exposées à l'utilisateur (voir commander()). False si la connexion n'est pas là ou si ça échoue : le
        code appelant garde alors son repli habituel en ligne de commande, plus lent mais déjà éprouvé."""
        if not self._atv or not self.boucle or not self.boucle.is_running():
            return False
        try:
            futur = asyncio.run_coroutine_threadsafe(self._rapide_async(fn), self.boucle)
            return bool(futur.result(timeout=delai))
        except Exception:
            return False

    async def _rapide_async(self, fn):
        async with self._lecture_lock:
            atv = self._atv
            if atv is None:
                return False
            await asyncio.wait_for(fn(atv), 3)
            self.reveil.set()
            return True

    def menu(self):
        return self._rapide(lambda atv: atv.remote_control.menu())

    def pause(self):
        return self._rapide(lambda atv: atv.remote_control.pause())

    def ouvrir_app(self, identifiant):
        return self._rapide(lambda atv: atv.apps.launch_app(identifiant))

    def observe(self, p, source):
        from config import charger
        return self.observations.observe(p, source, charger()["atv_ailleurs_pause_max_s"])

    def _publier(self, p):
        t = time.time()
        self.erreurs_consecutives = 0
        self.erreur = ""
        self._maj_combo(p, t)
        p = self.observe(dict(p, lu_a=t), "live_poll")
        self.etat, self.t = p, t
        return dict(p)

    async def _commander(self, cmd, attendu):
        async with self._lecture_lock:
            atv = self._atv
            if atv is None:
                return {"ok": False, "message": "Le lecteur est déconnecté."}
            # Une observation récente suffit à valider l'identité et les capacités. Faire un aller retour
            # metadata avant chaque clic ajoutait souvent plusieurs secondes et bloquait le polling.
            p = dict(self.etat or {})
            max_age = 8
            try:
                from config import charger
                max_age = int(charger().get("atv_etat_max_age_s", 8))
            except Exception:
                pass
            if not p or time.time() - self.t > max_age:
                p = self._publier(await asyncio.wait_for(self._lire(atv), 2))
            if attendu and jeton_lecture(p) != attendu:
                return {"ok": False, "message": "Le média a changé. Les commandes ont été actualisées.", "lecteur": vue_lecteur(p)}
            plan = plan_commande(p, cmd)
            if not plan:
                return {"ok": False, "message": "Cette commande n’est pas disponible pour le média actuel.", "lecteur": vue_lecteur(p)}
            try:
                await asyncio.wait_for(getattr(atv.remote_control, plan[0])(*plan[1]), 3)
            finally:
                # Une erreur peut survenir après l'envoi : jamais de seconde commande automatique.
                self.reveil.set()
            # Retour immédiat et optimiste après confirmation pyatv. Le prochain polling rectifie un état
            # inattendu. Cela évite d'attendre une seconde lecture réseau pour actualiser l'interface.
            p = dict(p)
            if cmd == "play_pause":
                p["etat"] = "Paused" if p.get("etat") == "Playing" else "Playing"
            elif cmd in ("back", "forward"):
                p["pos"] = max(0, min(int(p.get("total") or 0) or 2**31, int(p.get("pos") or 0) + (-10 if cmd == "back" else 10)))
            self._publier(p)
            return {"ok": True, "lecteur": vue_lecteur(p)}

    async def _boucle(self):
        import pyatv
        from pyatv.const import Protocol
        self.boucle = asyncio.get_running_loop()
        self.reveil = asyncio.Event()
        self._lecture_lock = asyncio.Lock()
        attente = 5
        while True:
            atv = None
            try:
                import integrations
                appareil = integrations.appletv(self._target_id)
                if not appareil:
                    raise RuntimeError("Apple TV non configurée")
                confs = await pyatv.scan(self.boucle, identifier=appareil["identifiant"], timeout=5)
                if not confs:
                    raise RuntimeError("Apple TV introuvable")
                conf = confs[0]
                conf.set_credentials(Protocol.Companion, appareil["companion"])
                if appareil["airplay"]:
                    conf.set_credentials(Protocol.AirPlay, appareil["airplay"])
                atv = await pyatv.connect(conf, self.boucle)
                self._atv = atv
                attente = 5
                # Point 78 : ne pas remettre _combo à zéro ici. Une coupure (Wifi, veille de l'Apple TV) déclenche une
                # reconnexion sans que rien n'ait forcément changé pendant l'interruption ; _maj_combo() compare déjà
                # la lecture retrouvée à la dernière connue et ne date que les vrais changements. Remettre à zéro à
                # chaque reconnexion prolongeait sans fin la durée perçue d'une pause déjà ancienne (atv_ailleurs_pause_max_s
                # ne se déclenchait jamais si des reconnexions se répètent), et un simple redémarrage du service perdait
                # tout l'historique de toute façon (Live() est recréé, _combo_t reparties de maintenant).
                while True:
                    async with self._lecture_lock:
                        nouveau = await asyncio.wait_for(self._lire(atv), _delai_lecture())
                        self._publier(nouveau)
                    await self._image(atv)
                    inactif = time.time() - self.demande
                    base = _pas_actif()
                    self.pas = base if inactif < 60 else (base * 3 if inactif < 600 else base * 10)
                    try:
                        await asyncio.wait_for(self.reveil.wait(), self.pas)
                    except asyncio.TimeoutError:
                        pass
                    self.reveil.clear()
                    # 2.6.108 : autre Apple TV active (choix de séance, appairage) : on se reconnecte à la nouvelle.
                    actuel = integrations.appletv(self._target_id)
                    if not actuel or actuel["identifiant"] != appareil["identifiant"] or actuel["companion"] != appareil["companion"]:
                        raise RuntimeError("Apple TV changée")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.erreur = str(e)[:120]
                self.erreurs_consecutives += 1
                await asyncio.sleep(attente)
                attente = min(60, attente * 2)
            finally:
                self._atv = None
                if atv is not None:
                    try:
                        taches = atv.close()
                        if taches:
                            await asyncio.gather(*taches)
                    except Exception:
                        pass

    async def _image(self, atv):
        """L'image du contenu en lecture, sur la même connexion (aucune de plus), une fois par contenu : seulement quand ce n'est pas Infuse (Infuse a
        déjà ses jaquettes TMDB) et que quelque chose joue. Une erreur ou une app sans image laisse simplement l'emplacement vide."""
        e = self.etat or {}
        h = cle_image(e) if e.get("etat") in ("Playing", "Paused") and e.get("app") != "com.firecore.infuse" else None
        if not h:
            self.jaquette = None
            return
        if self.jaquette and self.jaquette.get("h") == h and (self.jaquette.get("octets") or
                time.time() - self.jaquette.get("tentative", 0) < 10):
            return
        self.jaquette = {"h": h, "octets": None, "mime": None, "tentative": time.time()}
        try:
            art = await asyncio.wait_for(atv.metadata.artwork(width=400, height=None), 5)
            if art and art.bytes:
                self.jaquette = {"h": h, "octets": art.bytes, "mime": art.mimetype or "image/jpeg"}
        except Exception:
            pass

    async def _lire(self, atv):
        from pyatv.const import FeatureName, FeatureState
        p = await atv.metadata.playing()
        app, power = None, ""
        try:
            app = atv.metadata.app
        except Exception:
            pass
        try:
            power = atv.power.power_state.name
        except Exception:
            pass
        nom = lambda x: getattr(x, "name", str(x))
        fonctions = {}
        for cle, fonction in (("play_pause", "PlayPause"), ("play", "Play"), ("pause", "Pause"),
                              ("back", "SkipBackward"), ("forward", "SkipForward"), ("position", "SetPosition")):
            try:
                fonctions[cle] = atv.features.get_feature(getattr(FeatureName, fonction)).state == FeatureState.Available
            except Exception:
                fonctions[cle] = False
        try:
            identifiant = p.content_identifier
        except Exception:
            identifiant = None
        # tvOS/pyatv peut momentanément omettre metadata.app pendant une lecture YouTube.
        # N'inférer l'app que depuis une URL YouTube exacte, jamais depuis le titre ni un identifiant opaque.
        if app is None:
            try:
                u = urlparse(str(identifiant or ""))
                if u.scheme in ("http", "https") and u.hostname in ("youtu.be", "youtube.com", "www.youtube.com", "m.youtube.com"):
                    video = u.path.strip("/") if u.hostname == "youtu.be" else parse_qs(u.query).get("v", [""])[0]
                    if re.fullmatch(r"[A-Za-z0-9_-]{11}", video or ""):
                        app, app_nom = SimpleNamespace(identifier="com.google.ios.youtube", name="YouTube"), "YouTube"
            except (ValueError, TypeError):
                pass
        else:
            app_nom = app.name
        data = {"etat": nom(p.device_state), "titre": p.title or "", "media": nom(p.media_type),
                "fonctions": fonctions, "position_connue": p.position is not None, "content_identifier": identifiant,
                "pos": int(p.position or 0), "total": int(p.total_time or 0),
                "app": app.identifier if app else None, "app_nom": app_nom if app else None,
                "veille": power.lower() == "off",
                # 2.4 : ce que pyatv dit d'un contenu quelconque (Netflix, YouTube, musique...), quand l'app le donne
                "artiste": p.artist or "", "album": p.album or "", "genre": p.genre or "", "serie_nom": p.series_name or "",
                "saison_n": p.season_number or 0, "episode_n": p.episode_number or 0, "hash": p.hash or ""}
        data["commandes"] = {k: plan_commande(data, k) is not None for k in ("play_pause", "back", "forward")}
        return data


LIVE = Live()
