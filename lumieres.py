import os, threading, time, unicodedata, requests, urllib3
from config import charger
import denon
import chemins

urllib3.disable_warnings()

# 2.6.107 : plus aucun nom de lumière dans le code. L'administrateur associe les lumières de son pont à trois rôles
# (appairage_hue.py, secrets.json) : ecran, autour de l'écran ; panneau, l'enseigne ; salle, la lumière de la pièce.


SEANCE = {"actif": False, "jeton": 0}


def en_seance():
    return os.environ.get("KAMCINE_SEANCE") == "1" or SEANCE["actif"]


def cle(s):
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.casefold().split())


class Hue:
    """Ambiances de la séance. Le pont Hue est facultatif : sans lui (ou sans lumière associée), les lumières sont
    simplement ignorées, sans erreur, et l'ampli Denon suit toujours les scènes."""
    def __init__(self):
        import integrations
        cfg = integrations.hue()          # secrets.json (section hue), configuré depuis KamCiné (2.6.107)
        self.configure = cfg is not None
        self.noms, self.ids, self.base, self.h = {}, {}, None, {}
        roles = integrations.hue_roles()
        self.ecran, self.panneau, self.salle = roles["ecran"], roles["panneau"], roles["salle"]
        if not self.configure:
            return
        self.base = os.environ.get("HUE_SCHEME", "https") + "://" + cfg["adresse"] + "/clip/v2/resource"
        self.h = {"hue-application-key": cfg["cle"]}
        r = requests.get(self.base + "/light", headers=self.h, verify=False, timeout=5)
        self.noms = {l["id"]: l["metadata"]["name"] for l in r.json()["data"]}
        self.ids = dict(self.noms)

    @property
    def tv(self):
        return self.ecran + self.panneau

    def regler(self, ids, luminosite, duree=0, force=False):
        if not self.configure or (not force and not charger()["lumieres_actives"]):
            return
        for ident in ids:
            nom = self.noms.get(ident)
            if nom is None:
                print("  Lumière associée absente du pont :", ident)
                continue
            corps = {"dynamics": {"duration": int(duree * 1000)}}
            if luminosite <= 0:
                corps["on"] = {"on": False}
            else:
                corps["on"] = {"on": True}
                corps["dimming"] = {"brightness": luminosite}
            try:
                requests.put(self.base + "/light/" + ident, headers=self.h,
                             json=corps, verify=False, timeout=5)
            except Exception as e:
                print("  Erreur Hue :", nom, e)

    def salon_eteint(self, d=2):
        self.regler(self.salle, 0, d)

    def debut(self, d=3):
        if en_seance():
            denon.en_arriere_plan(denon.demarrer)
        self.salon_eteint(d)
        self.regler(self.tv, charger()["niv_debut"], d)

    def ambiance(self, d=2):
        self.debut(d)

    def trailers(self, d=5):
        self.salon_eteint(d)
        self.regler(self.tv, charger()["niv_trailers"], d)

    def noir(self, d=4):
        if en_seance():
            SEANCE["jeton"] += 1
            denon.en_arriere_plan(denon.restaurer)
        self.salon_eteint(d)
        self.regler(self.tv, 0, d)

    def entracte(self, d=25):
        """En séance : la vidéo démarre d'abord, puis les lumières montent doucement."""
        if en_seance():
            denon.en_arriere_plan(denon.baisser, charger()["denon_entracte_baisse"])
            cfg = charger()
            SEANCE["jeton"] += 1
            jeton = SEANCE["jeton"]
            threading.Thread(target=self._entracte_doux, args=(cfg["entracte_delai_lumiere"], max(d, cfg["entracte_montee"]), jeton)).start()
            return
        self.salon_eteint(d)
        self.regler(self.tv, charger()["niv_entracte"], d)

    def _entracte_doux(self, delai, montee, jeton):
        time.sleep(delai)
        if jeton != SEANCE["jeton"]:
            return
        self.salon_eteint(2)
        self.regler(self.tv, charger()["niv_entracte"], montee)

    def generique(self, d=20):
        if en_seance():
            denon.en_arriere_plan(denon.restaurer)
        self.salon_eteint(d)
        self.regler(self.ecran, charger()["niv_generique"], d)

    def tv_allumees(self, d=2):
        self.regler(self.tv, 60, d, force=True)

    def tv_eteintes(self, d=2):
        self.regler(self.tv, 0, d, force=True)
