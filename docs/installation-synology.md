# Installer KamCiné sur un NAS Synology

Bêta 2.7.10, image prévue `ghcr.io/griffur/kamcine:2.7.10-beta.1`, disponible après publication. Il faut Container Manager (paquet Docker de Synology).

## 1. Dossier de configuration

Dans File Station, créez par exemple `docker/kamcine/config`. Tout ce que KamCiné garde y sera écrit.

Relevez l'utilisateur et le groupe qui possèdent ce dossier : en SSH, `id votre-utilisateur` donne `uid` (PUID) et
`gid` (PGID). Souvent 1026 et 100.

## 2. Fichier docker-compose.yml

Dans `docker/kamcine`, créez `docker-compose.yml` avec le contenu du README, en adaptant `PUID`, `PGID` et
`KAMCINE_PORT` et `KAMCINE_TIMEZONE`. Ajoutez le fichier `.env` avec `KAMCINE_IMAGE` décrit dans le README.

## 3. Démarrage

Deux possibilités :

* Container Manager, Projet, Créer : choisissez le dossier `docker/kamcine` et son fichier `docker-compose.yml`.
* En SSH, depuis `docker/kamcine` : `sudo docker-compose up -d`.

Le conteneur utilise le réseau de l'hôte : c'est normal et nécessaire pour trouver l'Apple TV et le pont Hue.

## 4. Première ouverture

Ouvrez `http://adresse-du-nas:8765`, créez le premier compte (Administrateur), puis suivez « Configurons KamCiné ».

## Adresses des services

Dans la configuration, indiquez pour Radarr, Sonarr, Overseerr et Transmission l'adresse que le NAS peut joindre, par
exemple `http://192.168.1.10:7878`, ou une adresse HTTPS si vous passez par un proxy. Le bouton « Ouvrir » mène à la
page où se trouve la clé d'API de chaque service.

## Mise à jour et sauvegarde

Suivez le README : notez la version actuelle, arrêtez hors séance, sauvegardez tout `config/` et le Compose,
changez l’image versionnée dans `.env`, puis recréez le conteneur. Un rollback restaure l’image précédente
et le dossier complet sauvegardé, jamais la seule base SQLite.

## Fuseau des séances

Avant le premier démarrage, définissez `KAMCINE_TIMEZONE` dans l’environnement Compose (par exemple
`Europe/Paris` ou `America/Montreal`). Le défaut est `Europe/Zurich`. Le fuseau choisi apparaît dans
Réglages, À propos ; toutes les heures de programmation le suivent, même sur un téléphone situé ailleurs.
Changer cette variable nécessite de recréer le conteneur hors séance en cours, sans supprimer le dossier config.

## HTTPS, PWA et notifications

Utilisez un nom dédié, par exemple `cine.example.net`, à la racine de son origine. KamCiné ne prend pas en charge une installation dans un sous dossier `/kamcine`. Le nom doit résoudre vers le NAS depuis les appareils qui utilisent l’application. Pour une bêta locale, DNS local ou VPN suffit : HTTPS ne nécessite pas de rendre le NAS accessible publiquement.

1. Obtenez un certificat couvrant ce nom et reconnu par vos téléphones et ordinateurs. Dans DSM, Panneau de configuration, Sécurité, Certificat, importez ou demandez le certificat, puis affectez le à la règle du proxy. Le certificat doit être valide sans contourner un avertissement du navigateur. Son renouvellement doit être prévu ; la méthode de validation du domaine dépend du fournisseur et du DNS choisis.
2. Dans DSM 7, Panneau de configuration, Portail de connexion, Avancé, Proxy inversé, créez une règle dédiée. Source : HTTPS, nom `cine.example.net`, port 443. Destination : HTTP, nom `127.0.0.1`, port du conteneur KamCiné. Pour une installation standard : 8765 ; pour la candidate indépendante de test : 8767. Le proxy sur un autre hôte doit joindre l’adresse privée du NAS au lieu de 127.0.0.1.
3. Transmettez le nom d’origine dans `Host` et remplacez `X-Forwarded-Proto` par `https`. Le proxy doit fixer cette valeur, jamais recopier celle fournie par un client. Les chemins et paramètres d’URL doivent être transmis sans réécriture. Ne cachez pas les réponses d’authentification, API, manifest ou service worker. Le port HTTP du service reste réservé au réseau privé et au proxy.
4. Ouvrez `https://cine.example.net` : vérifiez le certificat, connectez vous, rechargez une fiche et testez précédent/retour. Cette origine a ses propres cookies : reconnectez vous même si la connexion HTTP fonctionnait. Les données du serveur restent les mêmes. Utilisez ensuite cette adresse de façon stable : changer de domaine implique une nouvelle installation PWA et un nouvel abonnement Push.
5. Sur iPhone ou iPad compatible, ouvrez cette adresse dans Safari, Partager, Ajouter à l’écran d’accueil, puis ouvrez KamCiné depuis son icône. Dans Réglages, Apparence et sons, activez les notifications à la demande et envoyez une notification de test. Sur desktop, utilisez un navigateur compatible et autorisez les notifications. Internet sortant est nécessaire pour joindre les fournisseurs Push ; KamCiné ne nécessite pas de port entrant supplémentaire pour envoyer les notifications.

En HTTP sur une adresse du NAS, « HTTPS requis » est attendu. Aucun contournement Web Push n’est fourni. Un simple certificat autosigné non reconnu ne suffit pas. KamCiné ne fournit pas un mode hors connexion : le NAS doit rester joignable.

Pour un autre proxy, l’équivalent Nginx est le suivant, dans un serveur HTTPS muni de son certificat. Remplacez le port par celui de votre installation et gardez ce serveur sur un réseau privé ou un VPN pour cette bêta.

```nginx
location / {
    proxy_pass http://127.0.0.1:8765;
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_http_version 1.1;
    proxy_read_timeout 90s;
    proxy_cache off;
}
```

Sources : [portail et proxy DSM](https://kb.synology.com/DSM/tutorial/Quick_Start_Synology_SSO), [certificats DSM](https://kb.synology.com/DSM/tutorial/Why_did_I_see_a_not_secure_warning_in_the_browser_when_connecting_to_my_Synology_product), [Web Push Apple](https://developer.apple.com/documentation/usernotifications/sending-web-push-notifications-in-web-apps-and-browsers), [contextes sécurisés](https://developer.mozilla.org/en-US/docs/Web/Security/Defenses/Secure_Contexts).
