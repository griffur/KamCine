"""Faux pyatv pour les tests de configuration de l'Apple TV (2.6.107) : découverte, appairage à code, connexion.

installer() remplace pyatv et pyatv.const dans sys.modules (à appeler avant la première recherche). Comportement réglé
par ETAT : appareils visibles, codes attendus par protocole, connexion possible ou non. Aucune Apple TV réelle."""
import sys
import types

CODES = {"Companion": "1234", "AirPlay": "5678"}
ETAT = {"appareils": [], "connexion": True, "appels": []}


class Service:
    def __init__(self):
        self.credentials = None


class Conf:
    def __init__(self, identifier, name, address="10.0.0.40", companion=True):
        self.identifier, self.name, self.address, self.companion = identifier, name, address, companion
        self.device_info = types.SimpleNamespace(model_str="Apple TV 4K")
        self.credentials = {}

    def get_service(self, protocole):
        return Service() if (protocole != "Companion" or self.companion) else None

    def set_credentials(self, protocole, valeur):
        self.credentials[protocole] = valeur


class Handler:
    def __init__(self, conf, protocole):
        self.conf, self.protocole, self._pin, self.has_paired, self.service = conf, protocole, None, False, Service()

    async def begin(self):
        ETAT["appels"].append(("begin", self.protocole))

    @property
    def device_provides_pin(self):
        return True

    def pin(self, code):
        self._pin = "%04d" % int(code)

    async def finish(self):
        self.has_paired = self._pin == CODES[self.protocole]
        if self.has_paired:
            self.service.credentials = "faux-identifiant-%s-%s" % (self.protocole.lower(), self.conf.identifier)

    async def close(self):
        ETAT["appels"].append(("close", self.protocole))


class Atv:
    def close(self):
        return []


def installer():
    pyatv = types.ModuleType("pyatv")
    const = types.ModuleType("pyatv.const")
    const.Protocol = types.SimpleNamespace(Companion="Companion", AirPlay="AirPlay")
    pyatv.const = const

    async def scan(loop, timeout=5, identifier=None, **k):
        confs = [Conf(*a) for a in ETAT["appareils"]]
        return [c for c in confs if identifier is None or c.identifier == identifier]

    async def pair(conf, protocole, loop, **k):
        return Handler(conf, protocole)

    async def connect(conf, loop, **k):
        ETAT["appels"].append(("connect", dict(conf.credentials)))
        if not ETAT["connexion"]:
            raise RuntimeError("connexion refusée")
        return Atv()

    pyatv.scan, pyatv.pair, pyatv.connect = scan, pair, connect
    sys.modules["pyatv"], sys.modules["pyatv.const"] = pyatv, const
    return pyatv
