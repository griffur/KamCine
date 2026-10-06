import json, os
import re
from urllib.parse import urlsplit, urlunsplit

import json_atomique
import chemins
BASE = chemins.data_dir()  # dossier des données (2.6.101)
CHEMIN = os.path.join(BASE, "reglages.json")

DEFAUTS = {
    "nom": "KamCiné",
    "icone": "sombre",
    "mode": "reel",
    "nb_trailers": "auto",
    "recul": 5,
    "confirm_delai": 5,
    "journal_visible": True,
    "entracte_actif": True,
    "entracte_debut": 45,
    "entracte_fin": 60,
    "entracte_min": 3,
    "entracte_max": 6,
    "lumieres_actives": True,
    "niv_debut": 40,
    "niv_trailers": 10,
    "niv_entracte": 40,
    "niv_generique": 35,
    "generique_minutes": 7,
    # Lot 2.6.89 : repli propre aux séries (un épisode de 45 min n'a qu'une ou deux minutes de générique).
    "generique_minutes_serie": 2,
    "verrou_duree": "30",
    "type_contenu": "auto",
    "overseerr_actif": True,
    "overseerr_url": "",
    # Adresses ouvertes depuis l’accueil, indépendantes des connexions API du NAS.
    "url_publique_radarr_hd": "",
    "url_publique_radarr_uhd": "",
    "url_publique_sonarr_hd": "",
    "url_publique_sonarr_uhd": "",
    "url_publique_overseerr": "",
    "generique_source": "auto",
    "generique_delai": 15,
    "denon_actif": False,
    "denon_ip": "",            # 2.6.106 : aucune adresse personnelle par défaut ; l'installation la garde dans reglages.json
    "denon_entracte_baisse": 0,
    "denon_allumer": False,
    "entracte_delai_lumiere": 8,
    "entracte_montee": 45,
    "demande_auto_ba": True,
    "demande_auto_versions": "both",
    "rappel_min": 15,
    "rappel_actif": True,
    "denon_limite": 82,
    # Badges de disponibilité du catalogue
    "badges_source": "auto",
    "badges_cache_min": 5,
    "badges_demarrage": True,
    "badges_delai_liste_s": 60,
    "badges_delai_overseerr_s": 15,
    # Envoi d'une demande : Overseerr peut attendre Radarr ou Sonarr avant de répondre (point 82)
    "overseerr_demande_delai_s": 60,
    "badges_delai_trakt_s": 15,
    "badges_vu_actif": True,
    # Séances programmées : durée réservée et tampon
    "plan_preparation_min": 3,
    "plan_bandes_annonces_min": 9,
    "plan_securite_min": 10,
    "plan_tampon_min": 10,
    "plan_tolerance_min": 10,
    "plan_confirmation_requise": True,
    "plan_confirmation_min": 2,
    "plan_avance_min": 2,
    "plan_imminente_h": 2,
    "plan_decompte_min": 60,
    "qualite_preferee": "meilleure",
    "ba_cache_min": 30,
    "ba_home_min": 30,
    # Accueil quand rien ne joue : rangées du catalogue
    "home_decouvrir": True,
    "home_sec_reprendre": True,
    "home_sec_vus": True,
    "home_sec_ajoutes": True,
    "home_sec_tendances": True,
    "home_sec_nouveautes": True,
    "home_nb_genres": 3,
    "home_cache_min": 10,
    # Anciennes tuiles, conservées pour compatibilité depuis la 2.6.14. Les clés home_sec_vus, home_sec_ajoutes,
    # home_sec_tendances, home_sec_nouveautes, home_nb_genres et home_cache_min restent également tolérées.
    "home_tuile_catalogue": True,
    "home_tuile_programmer": True,
    "home_tuile_overseerr": True,
    "home_tuile_journal": True,
    # Séances en attente de téléchargement
    "attente_intervalle_s": 60,
    "attente_debut_min": 720,
    "attente_fin_min": 1350,
    "attente_delai_s": 120,
    # Transmission (2.3), en lecture seule : vitesse et temps restant d'un téléchargement. Adresse, identifiant et mot de passe sont dans secrets.json
    "transmission_actif": False,
    # Bandes annonces de la fiche (2.4) : relecture du flux de la chaîne, durée de mémoire d'un refus
    "ba_flux_min": 60,
    "ba_cache_neg_min": 5,
    # Délais et seuils
    "trailer_marge_fin_s": 5,
    "trailer_tolerance_s": 3,
    "trailer_attente_s": 180,
    "film_attente_s": 20,
    "lancer_attente_s": 14,
    "lancement_confirmation_s": 240,
    "atv_delai_s": 30,
    "atv_delai_lecture_s": 15,
    "atv_etat_max_age_s": 8,
    "atv_pas_actif_s": 2,
    "atv_ailleurs_pause_max_s": 120,
    "seuil_vu_pct": 90,
    "film_choisi_heures": 4,
    "catalogue_cache_min": 10,
}

SPEC = {
    "nom": ("str", 1, 24),
    "icone": ("choix", ("sombre", "clair")),
    "mode": ("choix", ("reel", "test")),
    "nb_trailers": ("choix", ("auto", "2", "3")),
    "recul": ("int", 0, 15),
    "confirm_delai": ("int", 3, 15),
    "journal_visible": ("bool",),
    "entracte_actif": ("bool",),
    "entracte_debut": ("int", 20, 80),
    "entracte_fin": ("int", 20, 80),
    "entracte_min": ("int", 1, 30),
    "entracte_max": ("int", 1, 30),
    "lumieres_actives": ("bool",),
    "niv_debut": ("int", 0, 100),
    "niv_trailers": ("int", 0, 100),
    "niv_entracte": ("int", 0, 100),
    "niv_generique": ("int", 0, 100),
    "generique_minutes": ("int", 1, 20),
    "generique_minutes_serie": ("int", 1, 20),
    "verrou_duree": ("choix", ("1", "7", "30")),
    "type_contenu": ("choix", ("auto", "film", "serie")),
    "overseerr_actif": ("bool",),
    "overseerr_url": ("str", 0, 120),
    "url_publique_radarr_hd": ("url_publique",),
    "url_publique_radarr_uhd": ("url_publique",),
    "url_publique_sonarr_hd": ("url_publique",),
    "url_publique_sonarr_uhd": ("url_publique",),
    "url_publique_overseerr": ("url_publique",),
    "generique_source": ("choix", ("auto", "fixe")),
    "generique_delai": ("int", 0, 300),
    "denon_actif": ("bool",),
    "denon_ip": ("str", 0, 60),
    "denon_entracte_baisse": ("int", 0, 30),
    "denon_allumer": ("bool",),
    "entracte_delai_lumiere": ("int", 0, 60),
    "entracte_montee": ("int", 5, 180),
    "demande_auto_ba": ("bool",),
    "demande_auto_versions": ("choix", ("both", "hd", "uhd")),
    "rappel_min": ("int", 0, 120),
    "rappel_actif": ("bool",),
    "denon_limite": ("int", 20, 100),
    "badges_source": ("choix", ("auto", "arr", "overseerr")),
    "badges_cache_min": ("int", 1, 120),
    "badges_demarrage": ("bool",),
    "badges_delai_liste_s": ("int", 5, 180),
    "badges_delai_overseerr_s": ("int", 3, 60),
    "overseerr_demande_delai_s": ("int", 15, 180),
    "badges_delai_trakt_s": ("int", 3, 60),
    "badges_vu_actif": ("bool",),
    "plan_preparation_min": ("int", 0, 30),
    "plan_bandes_annonces_min": ("int", 0, 30),
    "plan_securite_min": ("int", 0, 60),
    "plan_tampon_min": ("int", 0, 60),
    "plan_tolerance_min": ("int", 0, 180),
    "plan_confirmation_requise": ("bool",),
    "plan_confirmation_min": ("int", 1, 15),
    "plan_avance_min": ("int", 0, 30),
    "plan_imminente_h": ("int", 1, 48),
    "plan_decompte_min": ("int", 5, 600),
    "qualite_preferee": ("choix", ("meilleure", "hd")),
    "ba_cache_min": ("int", 1, 720),
    "ba_home_min": ("int", 1, 240),
    "home_decouvrir": ("bool",),
    "home_sec_reprendre": ("bool",),
    "home_sec_vus": ("bool",),
    "home_sec_ajoutes": ("bool",),
    "home_sec_tendances": ("bool",),
    "home_sec_nouveautes": ("bool",),
    "home_nb_genres": ("int", 0, 8),
    "home_cache_min": ("int", 1, 120),
    "home_tuile_catalogue": ("bool",),
    "home_tuile_programmer": ("bool",),
    "home_tuile_overseerr": ("bool",),
    "home_tuile_journal": ("bool",),
    "attente_intervalle_s": ("int", 15, 900),
    "attente_debut_min": ("int", 0, 1439),
    "attente_fin_min": ("int", 0, 1439),
    "attente_delai_s": ("int", 0, 3600),
    "transmission_actif": ("bool",),
    "ba_flux_min": ("int", 10, 1440),
    "ba_cache_neg_min": ("int", 1, 60),
    "trailer_marge_fin_s": ("int", 5, 120),
    "trailer_tolerance_s": ("int", 1, 15),
    "trailer_attente_s": ("int", 30, 600),
    "film_attente_s": ("int", 10, 120),
    "lancer_attente_s": ("int", 5, 60),
    "lancement_confirmation_s": ("int", 60, 900),
    "atv_delai_s": ("int", 10, 120),
    "atv_delai_lecture_s": ("int", 5, 60),
    "atv_etat_max_age_s": ("int", 2, 60),
    "atv_pas_actif_s": ("int", 1, 10),
    "atv_ailleurs_pause_max_s": ("int", 15, 1800),
    "seuil_vu_pct": ("int", 50, 100),
    "film_choisi_heures": ("int", 1, 24),
    "catalogue_cache_min": ("int", 1, 120),
}


def url_publique(valeur):
    """Adresse HTTPS de navigation : un nom de domaine, jamais une IP ou un secret."""
    v = str(valeur or "").strip()
    if not v:
        return ""
    if len(v) > 500 or re.search(r"[\s\\]", v):
        raise ValueError("Adresse publique invalide")
    u = urlsplit(v)
    h = (u.hostname or "").lower()
    if (u.scheme != "https" or u.username is not None or u.password is not None
            or u.port not in (None, 443) or u.query or u.fragment
            or not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", h)
            or h.endswith((".local", ".lan", ".home", ".internal", ".localhost"))):
        raise ValueError("Utilise une adresse HTTPS publique avec un nom de domaine")
    return urlunsplit(("https", h, u.path.rstrip("/"), "", ""))


def nettoyer(cle, valeur):
    s = SPEC[cle]
    t = s[0]
    if t == "url_publique":
        return url_publique(valeur)
    if t == "bool":
        return bool(valeur)
    if t == "int":
        return max(s[1], min(s[2], int(valeur)))
    if t == "choix":
        v = str(valeur)
        if v not in s[1]:
            raise ValueError(cle)
        return v
    v = str(valeur).strip()[: s[2]]
    if len(v) < s[1]:
        raise ValueError(cle)
    return v


def charger():
    r = dict(DEFAUTS)
    try:
        with open(CHEMIN, encoding="utf-8") as f:
            brut = json.load(f)
        for k, v in brut.items():
            if k in SPEC:
                try:
                    r[k] = nettoyer(k, v)
                except Exception:
                    pass
        if "rappel_actif" not in brut:
            # Avant 2.6.109, rappel_min=0 désactivait les rappels. Préserver ce choix lors de la migration.
            r["rappel_actif"] = int(r.get("rappel_min", 15)) > 0
    except Exception:
        pass
    return r


def sauver(r):
    json_atomique.ecrire(CHEMIN, r, indent=2)
