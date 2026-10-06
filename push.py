"""Web Push optionnel, avec clés et abonnements conservés sur le NAS."""
import base64
import json
import logging
import os
import secrets
import threading
from urllib.parse import urlsplit
from concurrent.futures import ThreadPoolExecutor


import chemins
BASE = chemins.data_dir()  # dossier des données (2.6.101)
FILE = os.path.join(BASE, "push.json")
_LOCK = threading.Lock()
_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="kamcine-push")
_TYPES = {"telechargement_termine", "telechargement_erreur", "seance_echec", "seance_demarree", "seance_bientot", "contenu_disponible"}
_LOG = logging.getLogger("kamcine.push")


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _read():
    try:
        with open(FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {"private": data.get("private", ""), "public": data.get("public", ""),
                    "subscriptions": data.get("subscriptions", []) if isinstance(data.get("subscriptions"), list) else [],
                    "subject": data.get("subject", "")}
    except (OSError, ValueError):
        pass
    return {"private": "", "public": "", "subscriptions": [], "subject": ""}


def _sujet_valide(subject):
    """Un sujet VAPID doit être un contact HTTPS ou mailto, jamais localhost."""
    try:
        parsed = urlsplit(str(subject or ""))
        if parsed.scheme == "https":
            return bool(parsed.hostname and parsed.hostname.lower() not in ("localhost", "localhost.localdomain") and
                        not parsed.hostname.lower().endswith(".localhost") and not parsed.username and not parsed.password and
                        not parsed.query and not parsed.fragment and parsed.path in ("", "/"))
        if parsed.scheme == "mailto":
            adresse = parsed.path
            domaine = adresse.rsplit("@", 1)[-1]
            return ("@" in adresse and bool(domaine) and "." in domaine and
                    domaine.lower() != "localhost" and not parsed.query and not parsed.fragment)
    except (TypeError, ValueError):
        pass
    return False


def _write(data):
    os.makedirs(BASE, exist_ok=True)
    temp = FILE + ".tmp"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    try:
        os.chmod(temp, 0o600)
    except OSError:
        pass
    os.replace(temp, FILE)


def _ensure_keys(data):
    if data["private"] and data["public"]:
        return
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    key = ec.generate_private_key(ec.SECP256R1())
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode("ascii")
    point = key.public_key().public_numbers()
    public = _b64(b"\x04" + point.x.to_bytes(32, "big") + point.y.to_bytes(32, "big"))
    data.update(private=private, public=public)


def cle_publique(subject=None):
    with _LOCK:
        data = _read()
        genere = not (data["private"] and data["public"])
        _ensure_keys(data)
        configured = os.environ.get("KAMCINE_VAPID_SUBJECT", "").strip()
        sujet = configured or data.get("subject", "")
        if configured and not _sujet_valide(configured):
            raise ValueError("KAMCINE_VAPID_SUBJECT doit être une URL HTTPS ou une adresse mailto valide.")
        # À la première activation dans un navigateur HTTPS, utiliser et conserver l'origine de
        # l'installation. Cela donne notamment à APNs un sujet VAPID valide sans encoder un
        # domaine personnel dans l'image ou le dépôt.
        if not configured and not _sujet_valide(sujet) and _sujet_valide(subject):
            sujet = subject
        if not _sujet_valide(sujet):
            raise ValueError("Sujet VAPID absent : ouvre KamCiné depuis son URL HTTPS pour initialiser Push.")
        change = data.get("subject") != sujet
        data["subject"] = sujet
        if genere or change:
            _write(data)
        return data["public"]


def enregistrer(abonnement):
    endpoint = str((abonnement or {}).get("endpoint") or "")
    keys = (abonnement or {}).get("keys") or {}
    if (not endpoint.startswith("https://") or len(endpoint) > 4096 or
            not keys.get("p256dh") or not keys.get("auth") or
            len(str(keys.get("p256dh"))) > 256 or len(str(keys.get("auth"))) > 256):
        raise ValueError("Abonnement Web Push invalide")
    with _LOCK:
        data = _read()
        _ensure_keys(data)
        data["subscriptions"] = [x for x in data["subscriptions"] if x.get("endpoint") != endpoint]
        data["subscriptions"].append({"endpoint": endpoint, "keys": {"p256dh": str(keys["p256dh"]), "auth": str(keys["auth"])}})
        _write(data)
        return len(data["subscriptions"])


def retirer(endpoint):
    with _LOCK:
        data = _read()
        data["subscriptions"] = [x for x in data["subscriptions"] if x.get("endpoint") != endpoint]
        _write(data)
        return len(data["subscriptions"])


def _envoyer_un(sub, payload, private, subject):
    """Envoie à un seul appareil et ne journalise jamais son endpoint."""
    from pywebpush import webpush
    from py_vapid import Vapid
    vapid = Vapid.from_pem(private.encode("ascii"))
    webpush(subscription_info=sub, data=json.dumps(payload, ensure_ascii=False), vapid_private_key=vapid,
            vapid_claims={"sub": subject},
            ttl=60, timeout=10)


def envoyer_test(endpoint):
    """Teste explicitement l'envoi Web Push vers l'abonnement de cet appareil uniquement."""
    endpoint = str(endpoint or "")
    if not endpoint.startswith("https://") or len(endpoint) > 4096:
        return {"ok": False, "code": "abonnement_invalide"}
    with _LOCK:
        data = _read()
    sub = next((x for x in data["subscriptions"] if x.get("endpoint") == endpoint), None)
    if not sub:
        return {"ok": False, "code": "abonnement_absent"}
    subject = os.environ.get("KAMCINE_VAPID_SUBJECT", "").strip() or data.get("subject", "")
    if not data["private"] or not data["public"] or not _sujet_valide(subject):
        return {"ok": False, "code": "configuration"}
    payload = {"id": "test-" + secrets.token_urlsafe(12), "titre": "Notification de test",
               "description": "KamCiné peut envoyer des notifications à cet appareil.", "cible": None}
    try:
        _envoyer_un(sub, payload, data["private"], subject)
        return {"ok": True}
    except Exception as exc:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        if status in (404, 410):
            retirer(endpoint)
            return {"ok": False, "code": "abonnement_expire"}
        if status in (401, 403):
            # Le corps APNs est optionnel ; ne conserver que les identifiants de raison prévus
            # par le protocole, jamais le corps libre qui pourrait contenir des données privées.
            try:
                reason = response.json().get("reason")
            except Exception:
                reason = None
            if reason == "VapidPkHashMismatch":
                return {"ok": False, "code": "abonnement_cle"}
            if reason == "BadJwtToken":
                return {"ok": False, "code": "vapid_jwt"}
            return {"ok": False, "code": "configuration"}
        try:
            import erreurs
            erreurs.message(exc, "Échec du test Web Push", confidentiels=(endpoint, data["private"]))
        except Exception:
            _LOG.warning("Échec du test Web Push, statut=%s, erreur=%s", status, type(exc).__name__)
        return {"ok": False, "code": "service_indisponible"}


def _envoyer(item):
    if item.get("genre") not in _TYPES:
        return
    with _LOCK:
        data = _read()
    subject = os.environ.get("KAMCINE_VAPID_SUBJECT", "").strip() or data.get("subject", "")
    if not data["private"] or not data["subscriptions"] or not _sujet_valide(subject):
        return
    payload = json.dumps({"id": item.get("id"), "titre": item.get("titre", "KamCiné"),
                          "description": item.get("description", ""), "cible": item.get("cible")}, ensure_ascii=False)
    invalides = []
    for sub in data["subscriptions"]:
        try:
            _envoyer_un(sub, json.loads(payload), data["private"], subject)
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            try:
                import erreurs
                erreurs.message(exc, "Échec d'envoi Web Push pour la notification " + str(item.get("id")),
                                confidentiels=(sub.get("endpoint", ""),))
            except Exception:
                _LOG.warning("Échec d'envoi Web Push pour notification %s, statut=%s, erreur=%s",
                             item.get("id"), status, type(exc).__name__)
            if status in (404, 410):
                invalides.append(sub.get("endpoint"))
    if invalides:
        with _LOCK:
            data = _read()
            data["subscriptions"] = [x for x in data["subscriptions"] if x.get("endpoint") not in invalides]
            _write(data)


def notifier_creation(items):
    """Planifie uniquement les notifications critiques, sans bloquer leur création."""
    for item in items:
        if item.get("genre") in _TYPES:
            _POOL.submit(_envoyer, dict(item))
