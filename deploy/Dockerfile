# syntax=docker/dockerfile:1

# --- Stage 1: build the React frontend (runs on the build host / CI, NOT the Pi)
FROM node:26-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# --- Stage 2: runtime — single process, multi-arch (amd64/arm64)
FROM python:3.14-slim AS runtime

# wireguard-tools/openvpn = optional built-in VPN client (needs the container to
# run with --cap-add=NET_ADMIN --device=/dev/net/tun to actually connect).
# openresolv lets wg-quick apply a config's DNS line without failing.
# ffmpeg = video thumbnails and cutting oversized videos into parts for
# targets without chunked upload (app.video_split).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        wireguard-tools \
        openvpn \
        iproute2 \
        iptables \
        openresolv \
    && rm -rf /var/lib/apt/lists/*

# rclone = universal transfer engine. Debian ships 1.60, which predates
# Nextcloud chunked uploads (1.63) — large files to Nextcloud share links then
# go out as one PUT and proxies answer 413. Take the official build instead.
ARG TARGETARCH
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends curl unzip ca-certificates; \
    case "${TARGETARCH:-$(dpkg --print-architecture)}" in \
        amd64) arch=amd64 ;; arm64) arch=arm64 ;; arm|armhf) arch=arm-v7 ;; \
        *) echo "unsupported arch ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/rclone.zip "https://downloads.rclone.org/rclone-current-linux-${arch}.zip"; \
    unzip -j /tmp/rclone.zip '*/rclone' -d /usr/local/bin; \
    chmod 755 /usr/local/bin/rclone; \
    rm /tmp/rclone.zip; \
    apt-get purge -y --auto-remove curl unzip; \
    rm -rf /var/lib/apt/lists/*; \
    rclone version | head -1

WORKDIR /app
# Container defaults. The box updates by pulling a new image and has no sudo,
# systemd or host block devices, so the host-level actions (one-click update,
# service restart/reboot/shutdown, mounting/formatting drives) are off. Every
# value can still be overridden via --env-file / compose `environment:`.
ENV PYTHONUNBUFFERED=1 \
    OGC_DATA_DIR=/data \
    OGC_BUFFER_DIR=/data/buffer \
    OGC_IN_DOCKER=1 \
    OGC_SELF_UPDATE=false \
    OGC_UPDATE_COMMAND="" \
    OGC_RESTART_SERVICE_COMMAND="" \
    OGC_REBOOT_COMMAND="" \
    OGC_SHUTDOWN_COMMAND="" \
    OGC_STORAGE_COMMAND=""

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./
# Drop the built UI next to the backend so FastAPI serves it from "/".
COPY --from=frontend /app/frontend/dist ./app/static

# Version stamps the app reads at runtime (System page, update check, Telegram).
# CI passes them; a plain local `docker build` falls back to the dev version.
ARG OGC_VERSION=""
ARG OGC_COMMIT=""
ARG OGC_CHANNEL="release"
RUN if [ -n "$OGC_VERSION" ]; then echo "$OGC_VERSION" > app/VERSION; fi \
    && if [ -n "$OGC_COMMIT" ]; then echo "$OGC_COMMIT" > app/COMMIT; fi \
    && echo "$OGC_CHANNEL" > app/CHANNEL

VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
