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
+ bandbreiten-gesteuerter Worker + React/Vite-Kachel-UI, deploybar per Docker.
Details und Begründung im [Konzept](docs/KONZEPT.md).

## Status

🚧 Frühe Planungsphase — siehe [Entwicklungsplan](docs/ENTWICKLUNGSPLAN.md).

## Lizenz

[GPL-3.0](LICENSE)
