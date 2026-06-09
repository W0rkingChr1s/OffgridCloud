# syntax=docker/dockerfile:1

# --- Stage 1: build the React frontend (runs on the build host / CI, NOT the Pi)
FROM node:22-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# --- Stage 2: runtime — single process, multi-arch (amd64/arm64)
FROM python:3.11-slim AS runtime

# rclone = universal transfer engine. Installed from Debian for multi-arch.
RUN apt-get update \
    && apt-get install -y --no-install-recommends rclone \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    OGC_DATA_DIR=/data \
    OGC_BUFFER_DIR=/data/buffer

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./
# Drop the built UI next to the backend so FastAPI serves it from "/".
COPY --from=frontend /app/frontend/dist ./app/static

VOLUME ["/data"]
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
