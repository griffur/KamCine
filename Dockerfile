# syntax=docker/dockerfile:1
# Image autonome de KamCiné (2.7.10) : le code dans /app, les données dans le volume /config.
# Deux étapes, pour une seule raison : sur ARM64 (certains NAS), miniaudio, dépendance de pyatv, n'a pas de paquet
# binaire et doit être compilé. L'étape de construction a le compilateur ; l'image finale n'a que les paquets prêts.

FROM python:3.12-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e AS construction
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/requirements.txt
COPY docker/constraints.txt /tmp/constraints.txt
RUN pip wheel --no-cache-dir --wheel-dir /roues -r /tmp/requirements.txt -c /tmp/constraints.txt

FROM python:3.12-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e
ARG VERSION=2.7.10
ARG REVISION=unknown
LABEL org.opencontainers.image.title="KamCiné" \
      org.opencontainers.image.description="Séances de cinéma à la maison : Apple TV, Infuse, Hue, Denon, Radarr, Sonarr" \
      org.opencontainers.image.source="https://github.com/griffur/KamCine" \
      org.opencontainers.image.url="https://github.com/griffur/KamCine" \
      org.opencontainers.image.authors="Michael Gerber" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.licenses="GPL-3.0-only"
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    KAMCINE_APP=/app \
    KAMCINE_DATA=/config \
    KAMCINE_PORT=8765 \
    KAMCINE_HOST=0.0.0.0 \
    PUID=1000 \
    PGID=1000
COPY --from=construction /roues /tmp/roues
COPY requirements.txt /tmp/requirements.txt
COPY docker/constraints.txt /tmp/constraints.txt
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata=2026c-0+deb12u1 \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-index --find-links /tmp/roues -r /tmp/requirements.txt -c /tmp/constraints.txt \
 && rm -rf /tmp/roues /tmp/requirements.txt /tmp/constraints.txt \
 && groupadd --gid 1000 kamcine \
 && useradd --uid 1000 --gid 1000 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin kamcine \
 && mkdir -p /config
COPY docker/entrypoint.sh /usr/local/bin/kamcine-entrypoint
COPY . /app
RUN chmod 0755 /usr/local/bin/kamcine-entrypoint \
 && python /app/docker/collect_licenses.py /usr/share/kamcine/licenses \
 && python -m compileall -q /app \
 && chmod -R a+rX,go-w /app
WORKDIR /app
VOLUME ["/config"]
EXPOSE 8765
STOPSIGNAL SIGTERM
# Santé : le processus KamCiné répond, sans dépendre d'aucun appareil ni service externe.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import os,sys,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('KAMCINE_PORT', '8765'), timeout=4); sys.exit(0)" || exit 1
ENTRYPOINT ["/usr/local/bin/kamcine-entrypoint"]
