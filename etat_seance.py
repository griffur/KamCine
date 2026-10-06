"""Décisions de séance dérivées d'une observation, sans commande d'appareil."""
import time
import lecture_infuse


def media_reel(p):
    return bool(p and not p.get("stale") and not p.get("veille") and p.get("etat") in ("Playing", "Paused")
                and (p.get("media") in ("Video", "Music", "TV") or
                     (p.get("media") in (None, "", "Unknown") and p.get("app")))
                and not (p.get("app_active") is not None and p.get("app_active") != p.get("app")))


# Une pause, un recul ou une avance manuels avec la télécommande Apple TV, ou simplement une rebufferisation après
# un saut, peuvent faire publier un instant un état inexploitable (Idle, Stopped, titre ou position incohérents) sans
# qu'il n'y ait la moindre vraie coupure. Le comptage précédent (3 lectures consécutives sans correspondance) dépendait
# du rythme de sondage de chaque appelant (2 à 4 s selon l'endroit) : une rebufferisation d'une poignée de secondes
# suffisait à déclencher une fausse "suspendue", qui fait sortir seance.py/relais.py de leur boucle de pilotage. Cette
# tolérance se mesure maintenant en temps réel écoulé, pas en nombre d'appels : robuste quel que soit le rythme de
# sondage de l'appelant.
ABSENCE_TOLERANCE_S = 12
# Le lecteur est sondé toutes les 3 à 4 s : la position exactement égale à la durée n'est presque jamais observée.
# Une disparition du bon titre dans ses quinze dernières secondes vaut donc fin naturelle ; plus tôt, même pendant
# le générique, la séance est seulement suspendue et reste conservée.
FIN_TOLERANCE_S = 15
# Lot 2.6.81 (retour matériel de la 2.6.80) : quand le scénario KamCiné atteint le générique et rallume les lumières, la séance
# arrive fonctionnellement à son terme, même s'il reste plusieurs minutes de générique dans le fichier. L'étape Générique reste
# visible ce court moment, puis la séance se termine d'elle même. Avant le générique, une disparition du film reste une
# interruption (séance conservée), jamais une fin.
FIN_APRES_GENERIQUE_S = 20
# Lot 2.6.82 (retour matériel de la 2.6.81) : passer directement d'Infuse à YouTube faisait attendre ABSENCE_TOLERANCE_S
# avant l'interruption. Une autre application dont le registre d'observations a vu la position progresser récemment est une
# preuve forte : l'interruption est immédiate. Un vieil état republié par tvOS ne progresse pas et ne compte jamais.
AUTRE_LECTURE_RECENTE_S = 8
# Juste après les bandes annonces ou l'entracte (vidéos YouTube lancées par KamCiné lui même), la preuve forte est ignorée
# ce délai : la lecture YouTube de KamCiné peut encore être annoncée le temps qu'Infuse reprenne l'écran.
GARDE_APRES_YOUTUBE_KAMCINE_S = 30


# Lot 2.6.90 : pourquoi une observation n'est plus la lecture actuelle (registre d'observations et qualification Infuse).
RAISONS_ANCIENNE = {"invalidated": "antérieure au dernier arrêt KamCiné",
                    "superseded": "pause antérieure à une lecture plus récente",
                    "context_changed": "pause antérieure au passage à une autre application",
                    "unchanged_pause": "pause sans progression depuis trop longtemps"}


def classer(p):
    """Lot 2.6.90 : la règle unique de ce que l'Apple TV lit maintenant, partagée par la Home et le journal. (classe, raison) :
    lecture (Playing réel), pause (Paused réel, lecteur toujours le contexte actif), ancienne (pause ou état republié par pyatv
    après un changement de contexte, un arrêt ou une lecture plus récente), app_sans_lecture (une application signalée sans rien
    lire, par exemple Netflix ouvert), veille, rien. metadata.app n'est jamais pris pour l'application au premier plan : une
    application n'est citée que parce que l'Apple TV l'a signalée en dernier."""
    if not p:
        return "rien", "aucune observation de l'Apple TV"
    if p.get("veille"):
        return "veille", "Apple TV en veille"
    etat = p.get("etat")
    if etat in ("Playing", "Paused"):
        if p.get("stale"):
            return "ancienne", p.get("infuse_ecartee") or RAISONS_ANCIENNE.get(p.get("stale_reason") or "", "observation écartée")
        if not media_reel(p):
            return "ignoree", "état %s sans média exploitable (type %s)" % (etat, p.get("media") or "inconnu")
        return ("lecture", "lecture réelle") if etat == "Playing" else ("pause", "pause dans le lecteur, contexte inchangé")
    if p.get("app"):
        return "app_sans_lecture", "application signalée sans lecture (état %s)" % (etat or "inconnu")
    return "rien", "aucune lecture (état %s)" % (etat or "inconnu")


def autre_lecture_active(p):
    """Vrai si une app autre qu'Infuse lit réellement quelque chose, prouvé par une progression récente dans le registre."""
    return bool(media_reel(p) and p.get("app") not in (None, "", lecture_infuse.INFUSE_ID) and p.get("etat") == "Playing"
                and p.get("progression") and p.get("depuis", AUTRE_LECTURE_RECENTE_S + 1) <= AUTRE_LECTURE_RECENTE_S)


class FinFilm:
    """Une pause et une perte réseau ne sont jamais une fin de film."""
    def __init__(self, cible=None):
        self.cible = cible
        self.position = 0
        self.total = 0
        self.absence_depuis = None
        self.garde_jusqua = time.time() + GARDE_APRES_YOUTUBE_KAMCINE_S

    def patienter(self, secondes=GARDE_APRES_YOUTUBE_KAMCINE_S):
        """Après une entracte : la vidéo YouTube de KamCiné peut encore être annoncée, pas de preuve forte pendant ce délai."""
        self.garde_jusqua = time.time() + secondes

    def observer(self, p):
        if not p or p.get("etat") in (None, "", "Unknown", "Loading", "Seeking"):
            return "inconnu"
        # Infuse peut publier la dernière position seulement dans son état arrêté.
        if (self.cible and p.get("etat") in ("Idle", "Stopped")
                and lecture_infuse.correspond(dict(p, etat="Playing"), dict(self.cible, total_fichier=self.cible.get("total_fichier") or self.total))
                and p.get("total", 0) > 0 and p.get("pos", 0) >= p["total"] - FIN_TOLERANCE_S):
            return "terminee"
        # Lot 2.6.84 : une fois la durée du fichier connue, un épisode dont Infuse ne publie plus que le nom de la série reste
        # reconnu (même fichier) ; un autre épisode publié explicitement reste refusé.
        cible = dict(self.cible, total_fichier=self.cible.get("total_fichier") or self.total) if self.cible and self.total else self.cible
        meme = lecture_infuse.correspond(p, cible) if self.cible else (
            media_reel(p) and p.get("app") in (None, "", lecture_infuse.INFUSE_ID))
        if meme:
            self.absence_depuis = None
            self.position = p.get("pos", self.position)
            self.total = p.get("total") or self.total
            if self.total > 0 and self.position >= self.total:
                return "terminee"
            return "pause" if p["etat"] == "Paused" else "lecture"
        if autre_lecture_active(p) and time.time() >= self.garde_jusqua:
            return "suspendue"
        if self.absence_depuis is None:
            self.absence_depuis = time.time()
            return "inconnu"
        if time.time() - self.absence_depuis < ABSENCE_TOLERANCE_S:
            return "inconnu"
        return "terminee" if self.total > 0 and self.position >= self.total - FIN_TOLERANCE_S else "suspendue"


def vue(film, lancement, pilotee, preparation, detail):
    f, d, detail = film or {}, lancement or {}, detail or {}
    actif = bool(f.get("actif"))
    ent = (detail.get("entracte") or {}).get("etat") == "actif"
    gen = (detail.get("generique") or {}).get("etat") == "fait"
    ba = any(x.get("etat") == "actif" for x in detail.get("trailers", []))
    if pilotee:
        phase = "preparation" if preparation else ("entracte" if ent else (
            "pause" if actif and f.get("etat") == "Paused" else (
                "generique" if actif and gen else "film" if actif else "bandes_annonces" if ba else "attente")))
        nature = "pilotee"
    elif (d.get("suspendue") or d.get("incertaine")) and not actif and not f.get("ailleurs"):
        nature, phase = "memorisee", "inconnue" if d.get("incertaine") else "suspendue"
    elif actif:
        nature, phase = "liee" if d.get("confirme") and not d.get("suspendue") else "detectee", "pause" if f.get("etat") == "Paused" else "film"
    elif d and not f.get("ailleurs"):
        nature, phase = "lancement", d.get("etat", "en_cours")
    elif f.get("ailleurs"):
        nature, phase = "ailleurs", "pause" if f["ailleurs"].get("etat") == "Paused" else "lecture"
    else:
        nature, phase = "vide", "vide"
    return {"nature": nature, "phase": phase, "etape": "generique" if pilotee and gen and not ent else phase, "commandes": actif}
