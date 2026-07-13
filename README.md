<p align="center">
  <img src="assets/logo/offgridcloud-logo.svg" alt="OffgridCloud" width="520">
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-GPL--3.0-blue.svg" alt="License: GPL-3.0"></a>
  <img src="https://img.shields.io/badge/status-production--ready-brightgreen.svg" alt="Status: production-ready">
  <img src="https://img.shields.io/badge/Raspberry%20Pi%203-supported-c51a4a.svg" alt="Raspberry Pi 3 supported">
</p>

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

---

## Installation

### 🚀 One-Liner (empfohlen — Linux / Raspberry Pi OS)

Auf einem frischen Raspberry Pi (oder Debian/Ubuntu/Fedora/Arch) reicht **ein
Befehl**. Er installiert alle Abhängigkeiten (git, Node, Python, rclone), baut
das Frontend, richtet einen systemd-Dienst ein, **startet ihn und prüft den
Health-Endpoint**:

```bash
curl -fsSL https://raw.githubusercontent.com/W0rkingChr1s/OffgridCloud/main/deploy/bootstrap.sh | sudo bash
```

Optionen werden nach `--` durchgereicht — z. B. ffmpeg für Video-Thumbnails,
eigener Port und Admin-Login:

```bash
curl -fsSL https://raw.githubusercontent.com/W0rkingChr1s/OffgridCloud/main/deploy/bootstrap.sh \
  | sudo bash -s -- --with-ffmpeg --port 8080 --admin-email admin@example.com
```

Am Ende zeigt der Installer **einmalig** ein zufällig generiertes Admin-Passwort
— unbedingt notieren. Danach:

```
http://<host-ip>:8000     →  Login mit admin@offgrid.local / <angezeigtes Passwort>
```

> Der Installer legt Repo + App unter `/opt/offgridcloud` ab. **Updates:** den
> One-Liner erneut ausführen (Daten & `.env` bleiben erhalten).

### 🧪 Lokal ausprobieren (ohne Installation)

Repo klonen und in einem Befehl im Vordergrund starten — kein root, kein systemd:

```bash
git clone https://github.com/W0rkingChr1s/OffgridCloud.git && cd OffgridCloud
./quickstart.sh                       # http://localhost:8000, Ctrl-C beendet
```

Braucht nur `python3` und (fürs UI) `npm`. Das generierte Admin-Passwort wird
angezeigt und in `.env` abgelegt.

### 🐳 Docker (ein Image, plattformübergreifend)

```bash
docker build -f deploy/Dockerfile -t offgridcloud .
docker run -d --name offgridcloud -p 8000:8000 \
  -v /mnt/ssd/offgrid:/data --env-file .env --restart unless-stopped offgridcloud
```

> **VPN-Client (optional):** Läuft OffgridCloud außerhalb des Heimnetzes und soll
> ein internes Ziel (z. B. NAS per SMB) erreichen, kann unter **VPN** ein
> WireGuard- oder OpenVPN-Profil hinterlegt werden. Der Tunnel-Aufbau braucht
> erhöhte Container-Rechte — dann zusätzlich starten mit:
> ```bash
> docker run … --cap-add=NET_ADMIN --device=/dev/net/tun offgridcloud
> ```
> (docker-compose: `cap_add: [NET_ADMIN]` und `devices: ["/dev/net/tun"]`). Ohne
> diese Rechte bleibt die Oberfläche nutzbar und zeigt einen klaren Hinweis.

### 🪟 Windows (PowerShell)

```powershell
# fehlende Tools (Python/Node/rclone) werden via winget nachgezogen
powershell -ExecutionPolicy Bypass -File deploy\install.ps1            # Setup (erzeugt Admin-Passwort)
powershell -ExecutionPolicy Bypass -File deploy\run.ps1                # starten
powershell -ExecutionPolicy Bypass -File deploy\install.ps1 -InstallService   # optional Autostart (Admin)
```

### Manuell / Optionen / Deinstallieren

```bash
sudo ./deploy/install.sh --help       # alle Flags: --start --admin-email --port --with-ffmpeg --no-service ...
sudo ./deploy/uninstall.sh            # entfernen (behält Daten; --purge löscht alles)
```

---

## Produktiv betreiben — Checkliste

Nach der Installation für den echten Einsatz:

1. **TLS/HTTPS davor setzen.** OffgridCloud lauscht intern auf HTTP (Port 8000).
   Einen Reverse-Proxy mit Zertifikat vorschalten — Vorlagen liegen bei:
   `deploy/Caddyfile` (Auto-TLS, `tls internal` fürs Offline-Feld) oder
   `deploy/nginx.conf.example` (inkl. SSE-tauglicher `/api/events`-Location).
2. **Admin-Passwort ändern** nach dem ersten Login.
3. **Puffer auf externe SSD.** `OGC_BUFFER_DIR` in `/opt/offgridcloud/.env` auf eine
   USB-SSD legen — **nicht** auf die microSD-Karte (Schreibverschleiß, Kapazität).
4. **`OGC_SECRET_KEY` sichern.** Er entschlüsselt die Provider-Credentials — geht
   er verloren, müssen alle Provider neu eingerichtet werden. Getrennt aufbewahren.
5. **Backups.** `deploy/backup.sh` sichert DB + `.env` (nicht den Medien-Puffer).
6. **Monitoring.** `GET /api/health` (ohne Auth) für Uptime-Checks; Logs via
   `journalctl -u offgridcloud -f`.
7. **Updates.** Instanzen zeigen unter **System** die aktuelle vs. neueste
   Version (aus den GitHub-Releases). Aktualisieren mit einem Befehl:
   `sudo /opt/offgridcloud/src/deploy/update.sh` — Daten, `.env` und Port bleiben
   erhalten. Für einen **One-Click-Knopf im Web-UI** bei der Installation
   `--self-update` mitgeben.

Alles im Detail im **[Betriebshandbuch](docs/BETRIEB.md)** — Reverse-Proxy,
Bandbreiten-Steuerung, Speicher-Management, Audit-Log, Backup/Restore,
Updates, Troubleshooting.

## Konfiguration (`.env`)

Die Installer erzeugen `.env` mit zufälligem Key & Passwort. Wichtigste Variablen:

| Variable | Bedeutung |
|----------|-----------|
| `OGC_SECRET_KEY` | **Kritisch.** Signiert JWTs & verschlüsselt Provider-Credentials. Sichern, nicht ändern. |
| `OGC_INITIAL_ADMIN_EMAIL` / `OGC_INITIAL_ADMIN_PASSWORD` | Initial-Admin beim ersten Start. Passwort nach Login ändern. |
| `OGC_DATA_DIR` | SQLite-DB & App-Status. |
| `OGC_BUFFER_DIR` | Medien-Puffer — **auf externe USB-SSD** legen. |
| `OGC_RCLONE_BINARY` | Pfad/Name des rclone-Binaries (Default `rclone`). |

Vollständige Vorlage: [`.env.example`](.env.example).

## Architektur in einem Satz

FastAPI-Backend + **rclone** als universelle Transfer-Engine (deckt alle Provider ab)
+ bandbreiten-gesteuerter In-Process-Worker + React/Vite-Kachel-UI (als statische Dateien
ausgeliefert). Läuft als **ein Prozess** — sparsam genug für einen **Raspberry Pi 3**
(nativer systemd-Service oder ein einziges Docker-Image, ~150–250 MB RAM).
Details im [Konzept](docs/KONZEPT.md).

## Entwicklung

```bash
# Backend (Terminal 1) — Live-Reload
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload          # http://localhost:8000

# Frontend (Terminal 2)
cd frontend && npm install && npm run dev   # http://localhost:5173
```

Tests & Lint: `cd backend && pytest -q && ruff check .`. Beiträge:
[CONTRIBUTING.md](CONTRIBUTING.md).

## Status

✅ **Produktionsreif.** Die Phasen 0–8 sind umgesetzt; alle Meilensteine
(**M1** Walking Skeleton → **M4** Feldtauglich) sind erreicht:

- **Phase 0–2** — Grundgerüst, Auth/User-Management, Ordner & **chunked/resumable** lokale Uploads.
- **Phase 3–4** — Cloud-Provider (11 Typen via rclone, Verbindungstest, verschlüsselte Credentials) & automatische **Transfer-Engine** (Retry/Backoff, Resume, Integritäts-Check).
- **Phase 5–6** — **Bandbreiten-Steuerung** (`--bwlimit`, Zeitfenster, Mindest-Bandbreite-Gate, Prioritäten) & **Live-Dashboard** per SSE.
- **Phase 7** — Härtung: Audit-Log, Speicher-Management, Disk-Monitoring, Backup, Reverse-Proxy-Configs, [Betriebshandbuch](docs/BETRIEB.md).
- **Phase 8** — Teams/Gruppen, Thumbnails (Pillow/ffmpeg), PWA fürs Feld, Fertig-Webhook, aktive Bandbreiten-Probe.

Optionaler Backlog: Multi-Server-Pooling, Metadaten/Tagging & Suche — siehe
[Entwicklungsplan](docs/ENTWICKLUNGSPLAN.md).

## Dokumentation

- 📗 [Betriebshandbuch](docs/BETRIEB.md) — Installation, Absicherung, Betrieb
- 📘 [Konzept](docs/KONZEPT.md) — Vision, Architektur, Datenmodell, Tech-Stack
- 🗺️ [Entwicklungsplan](docs/ENTWICKLUNGSPLAN.md) — Roadmap in Phasen & Meilensteinen

<!--
  There is more here than meets the eye. The old machines never really left.
  If you know the sacred sequence of the gamers of old, the screen remembers:
  up, up, down, down, left, right, left, right, B, A.
-->

## Lizenz

[GPL-3.0](LICENSE)
