"""Banc d'essai de seance.py : le vrai script tourne en mode test contre un faux Apple TV (le faux atvremote de ce dossier)
et de faux appels YouTube. Rien ne touche au matériel ni au réseau.

Usage : python3 outils/banc_seance/lancer.py [normal|sans_ba|reseau|indexation|reprise_lente|reprise_jamais]
  normal          : 2 ou 3 bandes annonces, film, entracte, générique.
  sans_ba         : le flux YouTube ne contient aucune bande annonce.
  reseau          : le flux YouTube est injoignable.
  indexation      : le lien Infuse ouvre d'abord une recherche (média pas encore indexé), puis Infuse le retrouve
                    au deuxième essai : vérifie que demarrer_contenu() retente au lieu d'abandonner tout de suite.
  reprise_lente   : la commande "play" envoyée au retour des bandes annonces ne relance rien pendant quelques
                    essais (rebufferisation), comme une reprise anormalement lente : vérifie qu'attendre_confirmation()
                    rattrape la séance dès la vraie reprise (Playing) au lieu de l'interrompre (raise SystemExit).
  reprise_jamais  : la commande "play" ne relance jamais rien après les bandes annonces (reprise qui ne revient
                    vraiment pas) : vérifie que la séance échoue proprement (SystemExit, message clair) au lieu de
                    rester bloquée en silence dans l'attente de l'entracte.
Il faut un Python avec requests et pyatv (celui de l'image Docker, ou un venv). Compter 1 à 2 minutes (délais du mode test).
Le journal s'affiche à l'écran : c'est celui que le service lit avec ses regex."""
import json, os, runpy, sys, tempfile

ICI = os.path.dirname(os.path.abspath(__file__))
RACINE = os.path.dirname(os.path.dirname(ICI))
scenario = sys.argv[1] if len(sys.argv) > 1 else "normal"
tmp = tempfile.mkdtemp(prefix="banc_seance_")
os.environ.update(ATV_ID="AA:BB:CC:DD:EE:FF", COMP="x", AIR="y", SEANCE_MODE="test", KAMCINE_DIR=tmp, KAMCINE_SEANCE="1",
                  SEANCE_INFUSE_URL="infuse://movie/603?play", SEANCE_TMDB_TYPE="movie", SEANCE_TMDB_ID="603",
                  FAKE_ATV_STATE=os.path.join(tmp, "apple_tv.json"))
os.environ["PATH"] = ICI + os.pathsep + os.environ["PATH"]
if scenario == "indexation":
    # Essais courts pour que le banc reste rapide ; le délai d'indexation (4 s) tombe entre
    # le premier et le deuxième essai, comme un média disponible pendant l'attente du premier essai.
    with open(os.path.join(tmp, "reglages.json"), "w") as f:
        json.dump({"film_attente_s": 3, "nb_trailers": "2", "trailer_attente_s": 30}, f)
    os.environ["FAKE_ATV_INDEX_DELAI"] = "4"
if scenario == "reprise_lente":
    os.environ["FAKE_ATV_JOUER_BLOQUE"] = "3"    # ignore les 3 premières commandes "play", puis reprend vraiment
elif scenario == "reprise_jamais":
    os.environ["FAKE_ATV_JOUER_BLOQUE"] = "-1"   # ne reprend jamais
videos = {"AAA1": ["Film Un Bande annonce VF", 40], "BBB2": ["Film Deux Bande annonce", 40],
          "CCC3": ["Film Trois Bande annonce", 40], "ga_66cp4Jrc": ["Entracte", 300]}
os.environ["FAKE_ATV_VIDEOS"] = json.dumps(videos)
os.environ["SEANCE_META"] = json.dumps({"type": "movie", "id": 603, "titre": "99 Francs"})

import requests, pyatv
flux = "".join("<entry><yt:videoId>%s</yt:videoId><title>%s</title></entry>" % (k, v[0]) for k, v in videos.items() if k != "ga_66cp4Jrc")
flux_vide = "".join("<entry><yt:videoId>ID%d</yt:videoId><title>Interview numero %d</title></entry>" % (i, i) for i in range(15))


class Rep:
    def __init__(self, t):
        self.text = t


def faux_get(url, **k):
    if "feeds/videos.xml" in url:
        if scenario == "reseau":
            raise requests.exceptions.ConnectionError("réseau coupé")
        return Rep(flux_vide if scenario == "sans_ba" else flux)
    if "watch?v=" in url:
        return Rep('..."lengthSeconds":"%d"...' % videos.get(url.split("v=")[-1], ["", 60])[1])
    raise RuntimeError("appel réseau inattendu : " + url)


async def scan(*a, **k):
    return []          # pas d'Apple TV sur le réseau : seance.py passe en mode secours et utilise le faux atvremote


requests.get = faux_get
pyatv.scan = scan
sys.path.insert(0, RACINE)
os.chdir(tmp)
runpy.run_path(os.path.join(RACINE, "seance.py"), run_name="__main__")
