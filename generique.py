import os
import requests

API = os.environ.get("INTRODB_BASE", "https://api.theintrodb.org/v3")


def credits_debut(type_, tmdb_id, saison=None, episode=None, duree_s=None):
    """Début du générique de fin, en secondes, d'après TheIntroDB. None si introuvable."""
    params = {"tmdb_id": int(tmdb_id)}
    if type_ == "tv":
        if not (saison and episode):
            return None
        params.update(season=int(saison), episode=int(episode))
    if duree_s:
        params["duration_ms"] = int(duree_s * 1000)
    r = requests.get(API + "/media", params=params, timeout=6, headers={"Accept": "application/json"})
    if r.status_code != 200:
        return None
    candidats = []
    for seg in (r.json().get("credits") or []):
        debut = seg.get("start_ms")
        if debut is None:
            continue
        sec = debut / 1000.0
        if duree_s and not (duree_s * 0.5 <= sec < duree_s):
            continue
        candidats.append(sec)
    return min(candidats) if candidats else None


def seuil(total, serie, debut, reglages):
    """Seuil unique du générique, partagé par la séance et son relais.
    Un timecode TheIntroDB fiable prime. En Auto, le repli est proportionnel à la durée et plafonné par le réglage utilisateur.
    En Fixe, le délai utilisateur est appliqué tel quel. La cible reste toujours avant les vingt dernières secondes."""
    if debut:
        return min(int(debut + reglages.get("generique_delai", 0)), max(total - 20, 0)), "timecode connu"
    minutes = int(reglages["generique_minutes_serie"] if serie else reglages["generique_minutes"])
    marge = minutes * 60
    mode = reglages.get("generique_source", "auto")
    if mode == "auto" and total > 0:
        proportion = 0.06 if serie else 0.08
        minimum = 45 if serie else 120
        marge = min(marge, max(minimum, round(total * proportion)))
    return max(total - marge, 0), ("repli fixe" if mode == "fixe" else "repli intelligent") + (" série" if serie else " film")
