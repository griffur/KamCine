# Licences, crédits et marques

Audit du 29 septembre 2026 pour KamCiné 2.6.108. Le code original et la documentation de KamCiné sont sous
GNU GPL version 3 uniquement (`GPL-3.0-only`), voir LICENSE. Les éléments tiers gardent leurs droits propres.
KamCiné est un projet indépendant, non affilié, non sponsorisé et non approuvé par les services cités.
Apple TV est une marque d’Apple Inc.

## Dépendances Python

Inventaire extrait des images Linux Python 3.12.14 construites pour amd64 et arm64. Leurs contraintes communes
se trouvent dans docker/constraints.txt. Les versions et textes de licence sont lus dans chaque distribution
installée. Les liens mènent aux sources exactes ; outils/exporter_sources_image.py les récupère et vérifie leur SHA256.
L’image conserve ses notices dans /usr/share/kamcine/licenses, avec les textes de chaque paquet Python, les avis
natifs vendored et les notices Debian correspondantes.

| Dépendance | Version Linux | Licence annoncée par la distribution |
| :--- | :--- | :--- |
| [aiohappyeyeballs](https://pypi.org/project/aiohappyeyeballs/2.7.1/) | 2.7.1 | PSF-2.0 |
| [aiohttp](https://pypi.org/project/aiohttp/3.14.3/) | 3.14.3 | Apache-2.0 AND MIT |
| [aiosignal](https://pypi.org/project/aiosignal/1.4.0/) | 1.4.0 | Apache 2.0 |
| [annotated-doc](https://pypi.org/project/annotated-doc/0.0.5/) | 0.0.5 | MIT |
| [annotated-types](https://pypi.org/project/annotated-types/0.8.0/) | 0.8.0 | MIT |
| [anyio](https://pypi.org/project/anyio/4.15.1/) | 4.15.1 | MIT |
| [attrs](https://pypi.org/project/attrs/26.1.0/) | 26.1.0 | MIT |
| [certifi](https://pypi.org/project/certifi/2026.7.22/) | 2026.7.22 | MPL-2.0 |
| [cffi](https://pypi.org/project/cffi/2.1.1/) | 2.1.1 | MIT-0 |
| [chacha20poly1305-reuseable](https://pypi.org/project/chacha20poly1305-reuseable/0.13.2/) | 0.13.2 | Apache-2.0 OR BSD-3-Clause |
| [charset-normalizer](https://pypi.org/project/charset-normalizer/3.5.1/) | 3.5.1 | MIT |
| [click](https://pypi.org/project/click/8.5.0/) | 8.5.0 | BSD-3-Clause |
| [cryptography](https://pypi.org/project/cryptography/50.0.2/) | 50.0.2 | Apache-2.0 OR BSD-3-Clause |
| [fastapi](https://pypi.org/project/fastapi/0.128.8/) | 0.128.8 | MIT |
| [frozenlist](https://pypi.org/project/frozenlist/1.8.0/) | 1.8.0 | Apache-2.0 |
| [h11](https://pypi.org/project/h11/0.16.0/) | 0.16.0 | MIT |
| [http_ece](https://pypi.org/project/http_ece/1.2.1/) | 1.2.1 | MIT |
| [idna](https://pypi.org/project/idna/3.20/) | 3.20 | BSD-3-Clause |
| [ifaddr](https://pypi.org/project/ifaddr/0.2.0/) | 0.2.0 | MIT |
| [miniaudio](https://pypi.org/project/miniaudio/1.71/) | 1.71 | MIT |
| [multidict](https://pypi.org/project/multidict/6.9.1/) | 6.9.1 | Apache License 2.0 |
| [pip](https://pypi.org/project/pip/25.0.1/) | 25.0.1 | MIT |
| [propcache](https://pypi.org/project/propcache/0.5.4/) | 0.5.4 | Apache-2.0 |
| [protobuf](https://pypi.org/project/protobuf/7.36.2/) | 7.36.2 | 3-Clause BSD License |
| [py-vapid](https://pypi.org/project/py-vapid/1.9.4/) | 1.9.4 | MPL-2.0 |
| [pyatv](https://pypi.org/project/pyatv/0.18.0/) | 0.18.0 | MIT |
| [pycparser](https://pypi.org/project/pycparser/3.0/) | 3.0 | BSD-3-Clause |
| [pydantic](https://pypi.org/project/pydantic/2.13.5/) | 2.13.5 | MIT |
| [pydantic_core](https://pypi.org/project/pydantic_core/2.46.5/) | 2.46.5 | MIT |
| [pywebpush](https://pypi.org/project/pywebpush/2.1.2/) | 2.1.2 | MPL-2.0 |
| [requests](https://pypi.org/project/requests/2.32.5/) | 2.32.5 | Apache-2.0 |
| [six](https://pypi.org/project/six/1.17.0/) | 1.17.0 | MIT |
| [srptools](https://pypi.org/project/srptools/1.0.1/) | 1.0.1 | BSD 3-Clause License |
| [starlette](https://pypi.org/project/starlette/0.52.1/) | 0.52.1 | BSD-3-Clause |
| [tabulate](https://pypi.org/project/tabulate/0.10.0/) | 0.10.0 | MIT |
| [tinytag](https://pypi.org/project/tinytag/2.3.2/) | 2.3.2 | MIT License |
| [typing-inspection](https://pypi.org/project/typing-inspection/0.4.4/) | 0.4.4 | MIT |
| [typing_extensions](https://pypi.org/project/typing_extensions/4.16.0/) | 4.16.0 | PSF-2.0 |
| [urllib3](https://pypi.org/project/urllib3/2.8.0/) | 2.8.0 | MIT |
| [uvicorn](https://pypi.org/project/uvicorn/0.39.0/) | 0.39.0 | BSD-3-Clause |
| [yarl](https://pypi.org/project/yarl/1.25.1/) | 1.25.1 | Apache-2.0 |
| [zeroconf](https://pypi.org/project/zeroconf/0.151.5/) | 0.151.5 | LGPL-2.1-or-later |

Conclusion : aucune incompatibilité GPLv3 identifiée. Les licences permissives MIT, BSD, Apache 2.0 et PSF sont
compatibles selon la [FSF](https://www.gnu.org/licenses/license-list.html). zeroconf utilise LGPL-2.1-or-later.
pywebpush, py-vapid et certifi restent MPL-2.0 ; leurs avis, sources et possibilité de combinaison GPLv3 sont
consignés ci-dessus. Le classifier de chacha20poly1305-reuseable est trompeur, mais son texte amont annonce
Apache-2.0 OR BSD-3-Clause ; son texte est conservé.

Les roues natives incluent des composants compilés : cryptography 50.0.2 embarque OpenSSL 4.0.3 ; pydantic-core et
cryptography embarquent aussi du Rust. docker/licenses/native-notices.txt reprend les 319 fichiers de notice
extraits des sources Python et des arbres Rust. Leurs 134 dépendances de registre figurent dans les Cargo.lock
amont ; les expressions de licence annoncées se trouvent dans docker/licenses/rust-crates.json. Les sources
sdist Python, CPython 3.12.14, crates Rust et OpenSSL ont été récupérées et ont leurs SHA256. Les sources Debian
correspondantes (73 paquets source, aucune manquante) ont été récupérées par APT après vérification des index signés.
Les sources collectées lors de la préparation restent locales et ne constituent pas encore un artefact distribué.
Chaque release doit les associer à son inventaire exact et fournir les empreintes ainsi que les instructions de reconstruction.
Voir la section Sources accompagnant les images du README public et les outils de collecte livrés.
Une distribution d’image devra être accompagnée de l’offre de sources et des instructions requises par les
licences applicables.

## TMDB

[TMDB](https://www.themoviedb.org) fournit les fiches, images et recherches.

This product uses the TMDB API but is not endorsed or certified by TMDB.

La [FAQ officielle](https://developer.themoviedb.org/docs/faq) demande le logo et cette phrase dans À propos ou
Crédits, sans laisser penser à une approbation. Ils sont maintenant dans Réglages, À propos et le README public.
Le SVG existant est conservé sans changement de couleurs ni de proportions ; object-fit:contain évite l’étirement.
La page [logos officiels](https://www.themoviedb.org/about/logos-attribution) a été consultée. Le SVG livré est identique octet pour octet au fichier officiel Primary short (blue), SHA256 `5bdc75aaebeb75dc7ae79426ddd9be3b2be1e342510f8202baf6bffa71d7f5c4`.
La clé appartient à chaque installation. Un usage commercial nécessite un accord TMDB ; la GPL ne concède
aucun droit supplémentaire sur l’API, ses images ou les données des ayants droit.

## Inventaire des logos et marques réellement utilisés

Audit de app/logos, des SVG intégrés dans app/index.html et de leurs appels. Les licences des logiciels
accessibles par API ne sont pas automatiquement les licences de leurs marques ou de leurs logos.
Les fichiers graphiques tiers incertains ont été retirés des interfaces et des artefacts livrés. Le seul visuel de marque conservé est le logo TMDB, explicitement requis et récupéré depuis son kit officiel.

| Service | Utilisation réelle | Résultat et action avant publication |
| :--- | :--- | :--- |
| TMDB | tmdb.svg officiel, notes, liens et crédits | Provenance authentifiée par SHA256 ; attribution dans À propos et le README. |
| IMDb | Notes et liens | Nom complet en typographie neutre, aucun logo. Le SVG local n’est pas redistribué ; l’endpoint ne le sert pas. IMDb réserve l’emploi de son logo propriétaire et demande une permission écrite pour son usage dans un lien (voir ses [Conditions d’utilisation](https://www.imdb.com/conditions/)). |
| Trakt | Notes et lien de synchronisation | Nom en texte neutre, aucun pictogramme de marque. |
| Overseerr | Demandes et accès au service | Pictogramme générique de recherche, nom complet affiché ; aucun logo de marque. |
| Radarr | Services HD et 4K | Pictogramme générique de film, nom complet et format affichés ; aucun logo de marque. |
| Sonarr | Services HD et 4K | Pictogramme générique de télévision, nom complet et format affichés ; aucun logo de marque. |
| Infuse | Ouvrir la fiche | Pictogramme générique de lecture, nom complet affiché ; aucun logo de marque. |
| Sofa Time | Ouvrir dans l’app | Pictogramme générique de télévision, nom complet affiché ; aucun logo de marque. |
| Transmission | Nom et pictogrammes génériques de téléchargement | Aucun logo de Transmission trouvé ; [service officiel](https://transmissionbt.com/). Aucun droit sur son logo revendiqué. |
| Apple TV | Nom et pictogrammes génériques de télévision | Aucun logo Apple trouvé. Usage de compatibilité et mention Apple Inc. selon les [directives Apple](https://www.apple.com/legal/intellectual-property/guidelinesfor3rdparties.html). |
| Philips Hue | Nom et pictogrammes génériques de lumière | Aucun logo Hue trouvé. Usage descriptif d’intégration ; aucune affiliation revendiquée. |
| Denon | Nom et pictogrammes génériques d’ampli | Aucun logo Denon trouvé. Usage descriptif d’intégration ; aucune affiliation revendiquée. |
| Rotten Tomatoes | Notes critiques et public via un fournisseur tiers | Nom complet en typographie neutre, aucun fruit ni logo de marque. Ses règles demandent une demande de licence pour l’intégration des notes et logos, ainsi qu’une attribution et un lien vers la fiche correspondante ([licence officielle](https://www.rottentomatoes.com/help_desk/licensing)). |
| Metacritic | Note via OMDb | Nom complet en typographie neutre, aucun logo de marque. |

Les affiches et photographies reçues des fournisseurs restent soumises à leurs conditions et droits propres ; la GPL de KamCiné ne les couvre pas. Les notes reçues d’OMDb ou d’Overseerr devront aussi être vérifiées pour tout usage public.

## Candidate 2.7.6

La préparation 2.7.6 avait remplacé les bitmaps officiels KamCiné par des dessins géométriques.
Cette substitution est annulée en 2.7.9 sur instruction explicite du porteur du projet : les fichiers officiels
popcorn sombre et clair, ainsi que le splash, sont restaurés sans modification. Le générateur de remplacement
est retiré. Cette correction ne donne aucun droit nouveau sur les autres marques citées.
Les notices natives sont recalculées depuis
les sources exactes de la candidate : 134 crates couvrant les Cargo.lock, avec les versions PyO3 retrouvées
dans les binaires livrés. Les anciennes entrées supplémentaires PyO3 0.29.0, absentes des binaires, sont retirées.
Les sources correspondantes et leurs empreintes sont préparées localement avec les inventaires des deux architectures.

## Décision de distribution 2.7.10

Les scores IMDb, Rotten Tomatoes et Metacritic via OMDb et Overseerr sont désactivés, sans requête à ces
fournisseurs de notes, même si une ancienne clé OMDb est présente dans /config. Cette clé n’est pas supprimée.
La configuration OMDb n’est plus proposée. TMDB et les notes personnelles restent disponibles. Les logos tiers
incertains restent exclus ; seul le logo TMDB approuvé et son attribution sont livrés. Aucune collection d’affiches,
photographies ou données de catalogue n’est embarquée. Les liens descriptifs vers les services ne revendiquent
aucune affiliation. Les anciennes lignes du tableau décrivent l’audit antérieur, pas des scores livrés dans cette bêta.

Références relues : [licence des données Rotten Tomatoes](https://www.rottentomatoes.com/help_desk),
[conditions OMDb](https://www.omdbapi.com/legal.htm), [attribution TMDB](https://developer.themoviedb.org/docs/faq).
