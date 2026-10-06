# KamCiné

KamCiné transforme votre salon en salle de cinéma. Depuis votre téléphone, vous choisissez un film ou une série ;
KamCiné lance une vraie séance sur votre Apple TV : lumières Philips Hue, bandes annonces, film dans Infuse, entracte,
lumières au générique, volume de l'ampli Denon.

Première bêta 2.7.10, tag prévu `v2.7.10-beta.1`. Interface en français.
Dépôt : [griffur/KamCine](https://github.com/griffur/KamCine).
Image : `ghcr.io/griffur/kamcine:2.7.10-beta.1`, pour Linux amd64 et arm64.
Ces coordonnées sont finalisées ; les artefacts seront disponibles après publication de la release.

## Ce qu'il faut

* Un serveur Docker toujours allumé sur votre réseau local : un NAS (Synology ou autre) ou un petit ordinateur, en
  x86_64 (amd64) ou ARM64.
* Une Apple TV avec l'application Infuse, sur le même réseau.
* Une clé d'API TMDB, gratuite (compte sur themoviedb.org, puis Réglages, API).
* Facultatif : Radarr, Sonarr, Overseerr, Transmission, un pont Philips Hue, un ampli Denon.

## Installation avec Docker Compose

1. Créez un dossier pour KamCiné, par exemple `kamcine/`, et dedans un dossier vide `config/`.
2. Dans `kamcine/`, créez un fichier `docker-compose.yml` :

```yaml
version: "2.4"
services:
  kamcine:
    image: ${KAMCINE_IMAGE:?Définissez KAMCINE_IMAGE avec une image versionnée}
    container_name: kamcine
    network_mode: host
    init: true
    environment:
      KAMCINE_PORT: "8765"
      KAMCINE_TIMEZONE: "Europe/Paris"
      PUID: "1000"
      PGID: "1000"
    volumes:
      - ./config:/config
    restart: unless-stopped
    stop_grace_period: 20s
```

3. Créez un fichier `.env` à côté du Compose avec `KAMCINE_IMAGE=ghcr.io/griffur/kamcine:2.7.10-beta.1`. Cette image sera disponible à la publication
   de [la release](https://github.com/griffur/KamCine/releases/tag/v2.7.10-beta.1).
4. Démarrez : `docker compose up -d` (ou `docker-compose up -d` avec l'ancienne commande, celle de Synology).
5. Ouvrez `http://adresse-du-serveur:8765` dans un navigateur du même réseau.

Pour construire l'image vous même depuis ce dépôt : `cd docker && docker compose up -d --build` (fichier
`docker/docker-compose.yml`).

## Réglages du conteneur

* `KAMCINE_PORT` : port de l'interface web, 8765 par défaut.
* `PUID` et `PGID` : utilisateur et groupe propriétaires du dossier `config/`. KamCiné ne tourne jamais en root : au
  démarrage, le conteneur donne `config/` à cet utilisateur s'il ne lui appartient pas encore, puis s'exécute sous son
  identité. Pour les connaître : `id` dans un terminal du serveur (sur Synology, souvent 1026 et 100).
* `/config` : le seul dossier persistant, monté depuis votre serveur.

## Pourquoi le réseau de l'hôte

`network_mode: host` est nécessaire : KamCiné découvre votre Apple TV (mDNS) et votre pont Hue sur le réseau local,
et le réseau Docker habituel ne laisse pas passer cette découverte. Conséquence : aucun port n'est publié ; KamCiné
écoute directement sur le port choisi du serveur. Choisissez un port libre.

## Première ouverture

1. Une courte présentation de KamCiné.
2. Créez votre compte : le premier compte est l'Administrateur de cette installation.
3. « Configurons KamCiné » : Apple TV (recherche sur le réseau, puis deux codes affichés tour à tour par l'Apple TV),
   catalogue (clé TMDB), services média (Radarr, Sonarr, Overseerr), téléchargements (Transmission), lumières Hue et
   ampli Denon. Chaque étape peut être faite plus tard : Réglages, Configuration de KamCiné.
4. La connexion DSM facultative se règle dans Appareils, NAS. YouTube et Trakt se configurent dans
   Réglages, Services connectés ; aucune clé partagée n’est fournie.
5. D'autres personnes peuvent avoir leur compte : Réglages, Compte et sécurité (création par l'administrateur, ou
   inscriptions ouvertes).

## Données et image

* L'image contient seulement l'application. Elle peut être supprimée et remplacée à tout moment.
* Tout ce qui vous appartient est dans `config/` : comptes (`kamcine.db`), réglages, clés d'API et identifiants
  d'appairage (`secrets.json`, lisible seulement par KamCiné), photos des comptes, favoris, historique, journaux.
  Une copie de la base est faite dans `config/backups/` avant chaque évolution de son format.

## Mise à jour

1. Hors séance, notez l’image versionnée actuelle et arrêtez le conteneur : `docker compose stop`.
2. Copiez tout `config/` vers un dossier de sauvegarde distinct. Conservez aussi le Compose et `.env` actuels.
3. Changez `KAMCINE_IMAGE` dans `.env` vers la nouvelle version annoncée, puis exécutez :

```sh
docker compose pull
docker compose up -d
```

4. Vérifiez `/health`, la connexion, les réglages et les appareils. Ne supprimez pas la sauvegarde.

Un tag de version est immuable : `pull` seul ne sélectionne donc pas une nouvelle version.
Le même montage `config/` est conservé. Les migrations SQLite sont sauvegardées et transactionnelles ;
les autres données restent en JSON et ne sont pas couvertes par la seule sauvegarde automatique de la base.
Les écritures directes à risque utilisent désormais un fichier privé synchronisé puis un remplacement atomique ;
cela ne transforme pas les modifications de plusieurs fichiers en une transaction unique.

## Retour à la version précédente

1. Arrêtez le conteneur hors séance : `docker compose stop`.
2. Mettez le `config/` actuel de côté, puis remettez la copie complète sauvegardée avant la mise à jour.
3. Rétablissez le Compose et `.env` précédents, puis `docker compose up -d`.
4. Vérifiez `/health`, connexion et réglages. Les actions effectuées depuis la sauvegarde ne sont pas restaurées.

Ne forcez jamais un ancien code à ouvrir une base plus récente : KamCiné refuse cette situation.
Une copie de `kamcine.db` seule ne remplace pas une sauvegarde complète de `config/`.

## Sauvegarde

Arrêtez le conteneur (`docker compose stop`), copiez tout le dossier `config/`, puis redémarrez
(`docker compose start`). Pour restaurer : remettez ce dossier à la place de `config/` avant de démarrer.
Le dossier contient vos clés d'API et vos identifiants d'appairage : gardez la copie en lieu sûr.

## Arrêt et désinstallation

* Arrêter : `docker compose stop`. Redémarrer : `docker compose start`.
* Désinstaller : `docker compose down`, puis supprimez l'image (`docker image rm` suivi du nom de l'image).
* Vos données restent dans `config/` tant que vous ne supprimez pas ce dossier.

## Réseau, HTTPS et périmètre de la bêta

Installation recommandée : hôte Docker Linux avec réseau local accessible. Un Docker Desktop sur Mac ou Windows
ne constitue pas une validation de la découverte multicast ni du pilotage réel. ARM64 signifie ARM 64 bits,
pas ARM 32 bits ; la compatibilité dépend aussi des modèles de NAS et d’Apple TV.

HTTP convient au premier accès local. Pour installer la PWA sur iPhone et recevoir les notifications Web Push,
utilisez une origine HTTPS avec certificat reconnu via un proxy inverse. Le proxy doit transmettre les chemins
complets et leurs paramètres, notamment `/catalogue`, `/films/…` et `/series/…`. Ne publiez pas directement
le port HTTP sur Internet. Le premier administrateur doit être créé depuis un réseau de confiance.

Cette première bêta prévoit plusieurs comptes pour les membres d’un même foyer. Tous partagent les appareils de
l’installation selon leurs permissions. Un accès par VPN ou HTTPS ne crée pas un compte distant isolé : ne donnez
pas de compte à une personne qui ne doit pas voir les appareils ou l’activité du foyer. Le type d’accès distant
et son petit Agent local sont prévus pour une version future, avec le même serveur et sans conteneur KamCiné
complet par utilisateur.

Une seule séance est pilotée à la fois par installation. Les comptes, favoris et notes sont personnels,
mais les appareils, la salle et certaines données de suivi sont partagés. Sans TMDB et Apple TV configurés,
le Catalogue ou le lancement de séance restent indisponibles. Infuse doit être installée et sa bibliothèque prête ;
KamCiné ne fournit aucun film ni série. Les fournisseurs externes restent soumis à leurs conditions.

## Sources accompagnant les images

Chaque release doit indiquer le commit public exact, les digests des images amd64 et arm64 et les archives
correspondantes de sources avec empreintes SHA256. Les notices installées se trouvent dans
`/usr/share/kamcine/licenses/inventory.json`. Les scripts `outils/exporter_sources_image.py` et
`outils/exporter_sources_natives.py` permettent de collecter les sources Python et les composants natifs identifiés.
La [release v2.7.10-beta.1](https://github.com/griffur/KamCine/releases/tag/v2.7.10-beta.1) accompagne les images avec
`KamCine-2.7.10-sources.tar.gz`, `KamCine-2.7.10-sources-tierces.tar`, les inventaires, `source-verification.json`
et `SHA256SUMS`. L’archive tierce contient les sources Debian et `RECONSTRUIRE.md`. Ces fichiers seront
accessibles lors de la publication. Le rapport associe le commit public et les fichiers runtime aux deux images ;
les digests du registre figureront dans la release. Aucune reproductibilité binaire absolue n’est revendiquée.

## Aide

Projet créé par **Michael Gerber**. Pour signaler un problème ou proposer une amélioration :
[Issues GitHub](https://github.com/griffur/KamCine/issues). Indiquez la version, le modèle de serveur et les étapes
pour reproduire ; ne joignez jamais de clés d’API, mots de passe ou fichiers de configuration privés.


* Santé du service : `http://adresse-du-serveur:8765/health` répond `{"ok": true, ...}` ; Docker l'indique aussi
  (état « healthy »).
* Journaux : `docker logs kamcine`.
* Synology : voir [docs/installation-synology.md](docs/installation-synology.md).

## Licence et services tiers

Le code original et la documentation de KamCiné sont distribués sous GNU GPL version 3 uniquement
(`GPL-3.0-only`). Voir [LICENSE](LICENSE). Le logiciel est fourni sans garantie.
Les dépendances, marques, logos, affiches et données de services tiers conservent leurs propres droits :
ils ne deviennent pas GPL du fait de leur utilisation par KamCiné. Voir [les notices et limites de distribution](THIRD_PARTY_NOTICES.md).

KamCiné est un projet indépendant, non affilié, non sponsorisé et non approuvé par les services tiers cités.
Apple TV est une marque d’Apple Inc.

[![TMDB](app/logos/tmdb.svg)](https://www.themoviedb.org)

This product uses the TMDB API but is not endorsed or certified by TMDB.

Chaque installation utilise sa propre clé TMDB. La gratuité de son API concerne les usages non commerciaux
avec attribution ; un usage commercial demande un accord de TMDB. La GPL du code ne change pas ces conditions.

## Fuseau horaire

Définissez `KAMCINE_TIMEZONE` dans l’environnement du service, par exemple `Europe/Paris`, `America/Montreal`,
`Asia/Kathmandu` ou `UTC`. Le fichier `docker/docker-compose.yml` transmet cette variable.
Sans cette variable, le service utilise `TZ`, puis `Europe/Zurich` pour conserver le comportement des installations existantes.
Avec Compose, sa valeur par défaut explicite est `Europe/Zurich` : définissez `KAMCINE_TIMEZONE` pour la changer.
Recréez le conteneur après changement de son environnement, hors séance en cours.

Le planning, les fenêtres de lancement et les dates affichées suivent le fuseau du serveur, même si le téléphone
est ailleurs. Le fuseau apparaît dans Réglages, À propos et dans le sélecteur de séance.
Un nom invalide empêche le démarrage avec une erreur explicite. La base IANA `tzdata` est installée dans l’image.
Une heure inexistante au passage à l’heure d’été est refusée ; une heure répétée à l’automne désigne la seconde occurrence.
Les séances déjà enregistrées conservent leur instant absolu ; changer le fuseau change leur heure affichée.

## Visuels livrés

Les visuels officiels KamCiné sont livrés sans modification : logo popcorn sombre et clair dans
`app/icon_sombre.png` et `app/icon_clair.png`, splash dans `app/splash.jpg`. Ils conservent l’identité du projet,
Leur auteur confirme ses droits et autorise leur distribution avec KamCiné. Le seul logo tiers livré est TMDB ; attribution et provenance dans THIRD_PARTY_NOTICES.md.

### Configurer HTTPS pour la PWA

Le [guide Synology et proxy inverse](docs/installation-synology.md#https-pwa-et-notifications) décrit DNS, certificat reconnu, règle HTTPS vers le port privé du service, en têtes, installation iPhone et notification de test. Il comprend aussi un exemple Nginx. Utilisez une origine dédiée à la racine, accessible sur réseau local ou VPN ; la bêta ne nécessite pas d’ouverture publique du NAS. HTTP garde honnêtement le message « HTTPS requis ».

## Notes des fournisseurs

Cette bêta affiche les notes TMDB et vos notes personnelles. Les scores IMDb, Rotten Tomatoes et Metacritic
via OMDb ou Overseerr sont désactivés ; aucune clé OMDb n’est nécessaire. Les demandes et le suivi de disponibilité
Overseerr restent disponibles. Aucune affiche ni donnée de catalogue préchargée n’est distribuée dans l’image.
