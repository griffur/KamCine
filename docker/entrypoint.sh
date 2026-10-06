#!/bin/sh
# Démarrage de KamCiné dans l'image (2.6.108).
# Lancé en root (cas habituel) : le dossier /config est donné à PUID:PGID s'il ne leur appartient pas encore (premier
# démarrage, ou PUID changé), puis KamCiné tourne sous cet utilisateur, jamais en root. Lancé directement avec un
# utilisateur (user: dans le compose), rien n'est modifié : /config doit alors déjà lui être accessible en écriture.
set -eu

DONNEES="${KAMCINE_DATA:-/config}"
PORT="${KAMCINE_PORT:-8765}"
HOTE="${KAMCINE_HOST:-0.0.0.0}"
umask 027
mkdir -p "$DONNEES"

set -- python -m uvicorn main:app --app-dir /app/app --host "$HOTE" --port "$PORT" --timeout-graceful-shutdown 15

if [ "$(id -u)" = "0" ]; then
    PUID="${PUID:-1000}"
    PGID="${PGID:-1000}"
    case "$PUID$PGID" in *[!0-9]*|"") echo "PUID et PGID doivent être des nombres." >&2; exit 1 ;; esac
    if [ "$PUID" = "0" ]; then
        echo "KamCiné refuse de tourner en root : choisissez un PUID non nul." >&2
        exit 1
    fi
    if [ "$(stat -c %u "$DONNEES")" != "$PUID" ] || [ "$(stat -c %g "$DONNEES")" != "$PGID" ]; then
        echo "Attribution de $DONNEES à $PUID:$PGID"
        chown -R "$PUID:$PGID" "$DONNEES"
    fi
    # Dossier personnel temporaire (atvremote y garde ses réglages) : dans le conteneur, jamais dans /config.
    export HOME=/tmp/kamcine
    mkdir -p "$HOME" && chown "$PUID:$PGID" "$HOME"
    exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups -- "$@"
fi

export HOME="${HOME:-/tmp/kamcine}"
[ -w "$HOME" ] || export HOME=/tmp
exec "$@"
