<p align="center">
  <img src="assets/logo/offgridcloud-logo.svg" alt="OffgridCloud" width="520">
</p>

<p align="center"><strong>Upload when the signal is right.</strong></p>

# OffgridCloud

Ein selbst-gehosteter Mini-Server, der Medien-Uploads aus dem Feld von instabilen
Verbindungen **entkoppelt** und sie zuverlässig in Public-Cloud-Speicher überträgt —
**sobald ausreichend Bandbreite vorhanden ist.**

## Das Szenario

Ein Team ist mit Auto, Boot oder LKW unterwegs und hat mal besseren, mal
schlechteren Empfang. Ein Social-Media-Team im Office übernimmt die
Berichterstattung — braucht dafür aber verlässlich die Bilder und Videos aus
dem Feld. Direkter Upload in die Cloud scheitert an Verbindungsabbrüchen und
Timeouts.

**OffgridCloud löst die Blockade:** Das Feld-Team lädt alle Medien **lokal und
schnell** auf den Mini-Server. Dieser übernimmt den Upload in die Cloud
**autonom, bandbreiten-bewusst und resilient** (resumable, mit Retry) und hält
den Status für alle transparent.

## Kernfunktionen

- 📥 **Lokal annehmen, später senden** — Annahme und Cloud-Upload sind entkoppelt
- 📶 **Bandbreiten-bewusst** — Upload startet/drosselt je nach gemessener Leitung
- 🔁 **Resilient** — resumable Transfers, automatische Wiederholung, kein Datenverlust
- 👥 **User-Management** — Admin (Einstellungen, Provider, Ordner) & Benutzer (Upload in freigegebene Ordner)
- ☁️ **Viele Cloud-Ziele** — Amazon S3, MinIO, Azure Blob, OneDrive/SharePoint, Nextcloud, ownCloud, WebDAV, SFTP, SCP/SSH, FTP/FTPS, Hetzner Storage Box, Synology/QNAP/TrueNAS
- 🗂️ **Ordner ↔ Provider** — ein Ordner kann an mehrere Cloud-Ziele gespiegelt werden
- 🧩 **Modernes Kachel-Dashboard** — Live-Status, Fortschritt, Dark-Mode

## Dokumentation

- 📘 [Konzept](docs/KONZEPT.md) — Vision, Architektur, Datenmodell, Provider-Strategie, Tech-Stack
- 🗺️ [Entwicklungsplan](docs/ENTWICKLUNGSPLAN.md) — Roadmap in Phasen & Meilensteinen

## Architektur in einem Satz

FastAPI-Backend + **rclone** als universelle Transfer-Engine (deckt alle Provider ab)
+ bandbreiten-gesteuerter In-Process-Worker + React/Vite-Kachel-UI (als statische Dateien
ausgeliefert). Läuft als **ein Prozess** — sparsam genug für einen **Raspberry Pi 3**
(nativer systemd-Service oder ein einziges Docker-Image, ~150–250 MB RAM).
Details und Begründung im [Konzept](docs/KONZEPT.md).

## Schnellstart (Entwicklung)

```bash
# Backend (Terminal 1)
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload          # http://localhost:8000

# Frontend (Terminal 2)
cd frontend
npm install
npm run dev                            # http://localhost:5173
```

## Deployment auf dem Raspberry Pi 3

**Variante A — nativer Service (empfohlen):**
```bash
sudo ./deploy/install.sh
sudo systemctl enable --now offgridcloud   # http://<pi-ip>:8000
```

**Variante B — ein einziges Docker-Image:**
```bash
docker build -f deploy/Dockerfile -t offgridcloud .
docker run -d -p 8000:8000 -v /mnt/ssd/offgrid:/data --env-file .env offgridcloud
```

Details in [CONTRIBUTING.md](CONTRIBUTING.md) und dem [Konzept](docs/KONZEPT.md).

## Status

🚧 **Phasen 0–4 erledigt** (Meilensteine **M1 – Walking Skeleton** und **M2 – Cloud-Upload** erreicht):
- **Phase 0** — Grundgerüst (FastAPI + statisches React-Kachel-UI + rclone-Wrapper + systemd/Docker + CI)
- **Phase 1** — Auth & User-Management (JWT-Login, bcrypt, Rollen Admin/Benutzer, Initial-Admin, Admin-UI, geschützte Routen)
- **Phase 2** — Ordner & lokale Datei-Annahme: Admin legt Ordner an und gibt sie Benutzern frei; Benutzer laden per **chunked/resumable Upload** (Drag & Drop, Fortschritt) große Videos hoch — chunk-weise auf Platte gestreamt (Pi-3-schonend), SHA-256 beim Abschluss.
- **Phase 3** — Cloud-Provider-Anbindung: Admin verknüpft Ziel-Speicher (11 Typen: S3, MinIO, Azure Blob, OneDrive/SharePoint, Nextcloud, ownCloud, WebDAV, SFTP/SCP, FTP/FTPS, Hetzner Storage Box, SMB/NAS) über **rclone**, mit dynamischem Formular, **Verbindungstest** und verschlüsselter Credential-Ablage (Secrets im UI maskiert).
- **Phase 4** — Ordner ↔ Provider & Transfer-Engine: Admin verknüpft Ordner mit einem/mehreren Cloud-Zielen; ein **Hintergrund-Worker** lädt Medien per rclone automatisch hoch — mit Status-Lebenszyklus (queued → uploading → done), **Retry mit Backoff**, Resume-Recovery und Transfers-Übersicht mit manuellem Retry. **Damit ist die Upload-Blockade aufgelöst.**
- **Phase 5** — Bandbreiten-Steuerung: **Drosselung (`--bwlimit`) mit Zeitfenstern** (z. B. nachts volle Last), **Mindest-Bandbreite-Gate** (pausiert Uploads bei schwacher Leitung, Messung aus realem Durchsatz) und **Prioritäten** je Ordner↔Provider (Eilmaterial zuerst) — alles über eine Admin-Oberfläche.

> Beim ersten Start wird ein Admin aus `OGC_INITIAL_ADMIN_EMAIL` / `OGC_INITIAL_ADMIN_PASSWORD`
> angelegt — **Passwort nach dem ersten Login ändern.**

Als Nächstes: **Phase 6** (Dashboard & Realtime — Live-Fortschritt per WebSocket/SSE) → erreicht **Meilenstein M3**. Siehe [Entwicklungsplan](docs/ENTWICKLUNGSPLAN.md).

## Lizenz

[GPL-3.0](LICENSE)
