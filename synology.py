"""Lecture optionnelle de l'état Synology via les WebAPI DSM officielles."""
import os
import threading
import time
from urllib.parse import urlsplit


_cache = {"t": 0, "etat": None}
_lock = threading.Lock()
_TTL = 60


def _configuration(configuration=None):
    import integrations
    c = configuration or integrations.synology()  # secrets.json, sinon variables SYNOLOGY_*
    base, user, password, nom, verify = c["url"].rstrip("/"), c["utilisateur"], c["mot_de_passe"], c["nom"], c["verifier_tls"]
    if not base or not user or not password or not base.startswith(("http://", "https://")):
        return None
    url = urlsplit(base)
    if url.username or url.password or url.query or url.fragment:
        return None
    return base, user, password, nom, verify


def _api(session, base, path, api, version, method, params, sid, timeout, verify):
    query = dict(params, api=api, version=version, method=method, _sid=sid)
    response = session.get(base + "/webapi/" + str(path).lstrip("/"), params=query,
                           timeout=timeout, verify=verify, allow_redirects=False)
    response.raise_for_status()
    body = response.json()
    if not body.get("success"):
        code = (body.get("error") or {}).get("code", "inconnu")
        raise RuntimeError("DSM %s.%s a refusé la requête (code %s)" % (api, method, code))
    return body.get("data") or {}


def invalider():
    with _lock:
        _cache.update(t=0, etat=None)


def lire(force=False, configuration=None):
    now = time.time()
    if configuration is None:
        with _lock:
            if not force and _cache["etat"] is not None and now - _cache["t"] < _TTL:
                return dict(_cache["etat"])
    conf = _configuration(configuration)
    if not conf:
        return {"configure": False, "en_ligne": False, "message": "Configuration serveur absente."}
    base, user, password, nom, verify = conf
    import requests
    session = requests.Session()
    sid = None
    auth_path = "auth.cgi"
    try:
        q = session.get(base + "/webapi/query.cgi", params={"api": "SYNO.API.Info", "version": 1,
            "method": "query", "query": "SYNO.API.Auth,SYNO.Core.System,SYNO.Core.Storage.Volume"}, timeout=5,
            verify=verify, allow_redirects=False)
        q.raise_for_status()
        query_body = q.json()
        if not query_body.get("success"):
            code = (query_body.get("error") or {}).get("code", "inconnu")
            raise RuntimeError("DSM SYNO.API.Info.query a échoué (code %s)" % code)
        api_info = query_body.get("data", {})
        auth_info = api_info.get("SYNO.API.Auth", {})
        auth_path = auth_info.get("path", "auth.cgi")
        auth_version = int(auth_info.get("maxVersion", 3))
        login = session.get(base + "/webapi/" + auth_path.lstrip("/"), params={"api": "SYNO.API.Auth",
            "version": min(auth_version, 6), "method": "login", "account": user, "passwd": password,
            "session": "KamCine", "format": "sid"}, timeout=5, verify=verify, allow_redirects=False)
        login.raise_for_status()
        auth_body = login.json()
        if not auth_body.get("success"):
            code = (auth_body.get("error") or {}).get("code", "inconnu")
            raise RuntimeError("DSM SYNO.API.Auth.login a échoué (code %s)" % code)
        sid = (auth_body.get("data") or {}).get("sid")
        if not sid:
            raise RuntimeError("DSM session unavailable")
        system_info = api_info.get("SYNO.Core.System", {})
        storage_info = api_info.get("SYNO.Core.Storage.Volume", {})
        system = _api(session, base, system_info.get("path", "entry.cgi"), "SYNO.Core.System",
                      int(system_info.get("maxVersion", 3)), "info", {}, sid, 6, verify)
        storage = _api(session, base, storage_info.get("path", "entry.cgi"), "SYNO.Core.Storage.Volume",
                       int(storage_info.get("maxVersion", 1)), "list",
                       {"limit": -1, "offset": 0, "location": "internal"}, sid, 6, verify)
        volumes = storage.get("volumes", []) if isinstance(storage, dict) else []
        volumes = [v for v in volumes if isinstance(v, dict)]
        volumes.sort(key=lambda v: str(v.get("volume_path") or v.get("display_name") or ""))
        resume_volumes = []
        for v in volumes:
            try:
                total_v = int(v.get("size_total", v.get("total_size", v.get("total"))))
                used_v = int(v.get("size_used", v.get("used_size", v.get("used"))))
            except (TypeError, ValueError):
                continue
            resume_volumes.append({"nom": v.get("display_name") or v.get("volume_path") or "Volume",
                                   "total": total_v, "utilise": used_v, "disponible": max(0, total_v - used_v),
                                   "pourcentage": round(100 * used_v / total_v, 1) if total_v else None})
        volume = volumes[0] if volumes else {}
        total = volume.get("size_total", volume.get("total_size", volume.get("total")))
        used = volume.get("size_used", volume.get("used_size", volume.get("used")))
        try:
            total, used = int(total), int(used)
        except (TypeError, ValueError):
            total, used = None, None
        pct = round(100 * used / total, 1) if total and used is not None else None
        uptime = system.get("uptime", system.get("up_time", system.get("uptime_sec")))
        try:
            uptime = max(0, int(uptime))
        except (TypeError, ValueError):
            uptime = None
        etat = {"configure": True, "en_ligne": True, "nom": nom,
            "dsm_url": base, "adresse": urlsplit(base).hostname, "arret_disponible": False,
            "modele": system.get("model") or system.get("model_name"),
            "version_dsm": system.get("version_string") or system.get("version") or system.get("firmware_version"),
            "uptime": uptime,
            "volume": volume.get("display_name") or volume.get("volume_path") or "Volume principal",
            "total": total, "utilise": used, "disponible": total - used if total is not None and used is not None else None,
            "pourcentage": pct, "volumes": resume_volumes}
        if configuration is None:
            with _lock:
                _cache.update(t=time.time(), etat=etat)
        return dict(etat)
    except Exception as exc:
        try:
            import erreurs
            erreurs.message(exc, "Lecture des WebAPI Synology échouée.", confidentiels=(user, password))
        except Exception:
            pass
        with _lock:
            if configuration is None and _cache["etat"] is not None and time.time() - _cache["t"] < 300:
                stale = dict(_cache["etat"])
                stale["actualise"] = False
                return stale
        return {"configure": True, "en_ligne": False, "nom": nom,
                "message": "NAS injoignable ou authentification refusée."}
    finally:
        try:
            if sid:
                session.get(base + "/webapi/" + auth_path.lstrip("/"), params={"api": "SYNO.API.Auth",
                    "version": min(auth_version, 6), "method": "logout", "session": "KamCine", "_sid": sid},
                    timeout=2, verify=verify, allow_redirects=False)
            session.close()
        except Exception:
            pass
