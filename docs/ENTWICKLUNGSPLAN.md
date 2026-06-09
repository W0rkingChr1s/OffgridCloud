# OffgridCloud — Entwicklungsplan

Iterativer Aufbau in Phasen. Jede Phase ist lauffähig und liefert sichtbaren Mehrwert.
Ziel: früh ein funktionierendes Grundgerüst (lokal annehmen → Cloud-Upload), danach
Härtung und Komfort.

Legende: ☐ offen · ◐ in Arbeit · ☑ erledigt

---

## Phase 0 — Fundament & Setup ✅
*Ergebnis: lauffähiges Projektgerüst, das jeder starten kann — Pi-3-tauglich.*

- ☑ Repo-Struktur (`backend/`, `frontend/`, `docs/`, `assets/`, `deploy/`)
- ☑ Backend-Grundgerüst FastAPI: Healthcheck, Config-Loader, SQLite-Anbindung
- ☑ Frontend zu **statischen Dateien** bauen, vom FastAPI ausliefern (kein Node zur Laufzeit)
- ☑ Frontend-Grundgerüst React + Vite + TypeScript + Tailwind, Logo eingebunden
- ☑ rclone-Binary einbinden + Versions-Check (Subprozess-Wrapper)
- ☑ **Deployment-Variante A:** nativer **systemd-Service** + Install-Skript (empfohlen für RPi 3)
- ☑ **Deployment-Variante B:** ein einziges **Docker-Image** (multi-arch arm64), in CI gebaut — *kein* compose-Stack
- ☑ `.env.example` (Initial-Admin, Secret-Key, Puffer-Pfad auf USB-SSD)
- ☑ CI: Lint + Tests + Frontend-Build + Image-Build (GitHub Actions)
- ☑ `CONTRIBUTING.md`, Code-Style, `.editorconfig`

## Phase 1 — Auth & User-Management ✅
*Ergebnis: Login, Rollen, Admin kann Benutzer verwalten.*

- ☑ Datenmodell `User` (Migrationen via `create_all`; Alembic bei wachsendem Schema)
- ☑ Registrierung deaktiviert/Initial-Admin via Env beim ersten Start
- ☑ Login (JWT) + Passwort-Hashing (bcrypt); Logout client-seitig (Token verwerfen).
      *Refresh-Token später, falls nötig.*
- ☑ Rollen-Dependencies (`admin` / `user`): `get_current_user`, `require_admin`
- ☑ Admin-UI: Benutzer anlegen/sperren/Rolle ändern/Passwort-Reset/löschen
- ☑ Geschützte Routen im Frontend (Rollen-Guards, geschützte API-Aufrufe)

## Phase 2 — Ordner & lokale Datei-Annahme
*Ergebnis: Benutzer lädt Dateien lokal in freigegebene Ordner — schnell & stabil.*

- ☐ Datenmodell `UploadFolder`, `FolderUserAccess`, `MediaItem`
- ☐ Admin-UI: Ordner anlegen/bearbeiten, Benutzern freigeben
- ☐ Chunked / resumable Upload-Endpunkt (große Videos!) + Hash beim Empfang
- ☐ Benutzer-UI: Drag & Drop, Fortschritt, nur freigegebene Ordner sichtbar
- ☐ Dateiablage auf lokalem Storage, `MediaItem`-Status `received`

## Phase 3 — Cloud-Provider-Anbindung
*Ergebnis: Admin verknüpft Provider; Verbindungstest grün.*

- ☐ Datenmodell `CloudProvider` (Config verschlüsselt at rest)
- ☐ Provider-Typen + Feld-Schemata (S3, Azure, OneDrive/SharePoint, WebDAV/Nextcloud/
      ownCloud, SFTP, SCP/SSH, FTP/FTPS, NAS, Hetzner Storage Box, MinIO)
- ☐ Mapping `CloudProvider` → rclone-Remote (Konfig-Generator)
- ☐ Admin-UI: Provider hinzufügen (Typ → Felder → **Verbindungstest**)
- ☐ Sichere Credential-Speicherung + Maskierung im UI

## Phase 4 — Ordner ↔ Provider & Transfer-Engine
*Ergebnis: Dateien wandern automatisch in die Cloud.*

- ☐ Datenmodell `FolderProviderLink` (m:n, Ziel-Pfad/Bucket je Provider), `TransferJob`
- ☐ Admin-UI: Ordner einem/mehreren Providern zuordnen
- ☐ Transfer-Worker: rclone-Aufruf je `TransferJob`, Fortschritt parsen
- ☐ Status-Lebenszyklus `received → queued → uploading → verified → done`
- ☐ Retry mit Backoff, Resume nach Abbruch, Fehler-Logging
- ☐ Integritäts-Check (Hash/Size) nach Upload

## Phase 5 — Bandbreiten-Steuerung
*Ergebnis: Upload passt sich der Leitung an — Kern des Produkts.*

- ☐ Periodische Bandbreiten-/Latenz-Messung zum Ziel
- ☐ Mindest-Bandbreite-Schwelle → Start/Stop des Schedulers
- ☐ Dynamisches `--bwlimit` + Zeitpläne (z. B. nachts volle Last)
- ☐ Pause/Resume bei Bandbreiten-Einbruch (kein Abbruch)
- ☐ Prioritäten je Ordner/Provider (Eilmaterial zuerst)
- ☐ Admin-UI für Regeln & Zeitfenster

## Phase 6 — Dashboard & Realtime
*Ergebnis: modernes Kachel-Dashboard mit Live-Status.*

- ☐ WebSocket/SSE für Live-Fortschritt
- ☐ Kachel-Dashboard: Ordner-Kacheln (% hochgeladen, aktive Transfers, Bandbreite)
- ☐ Transfer-Ansicht: laufend/wartend/fertig, Pause/Retry-Aktionen
- ☐ Status-Farbcodierung, Dark-Mode, responsive

## Phase 7 — Härtung, Sicherheit & Betrieb
*Ergebnis: feldtauglich und sicher.*

- ☐ HTTPS / Reverse-Proxy-Setup, optional self-signed im Feld
- ☐ Audit-Log (Admin-Aktionen, Transfers)
- ☐ Speicher-Management: optionales Löschen lokaler Kopien nach verifiziertem Upload
- ☐ Backup/Restore der Konfig & DB
- ☐ Monitoring/Health, Disk-Voll-Warnungen
- ☐ Dokumentation: Installations- & Betriebshandbuch

## Phase 8 — Erweiterungen (Backlog)
*Nach MVP, nach Bedarf.*

- ☐ Teams/Gruppen statt nur Admin/User
- ☐ Thumbnails/Vorschau für Bilder & Videos
- ☐ Mobile-optimierter Upload / PWA für das Feld-Team
- ☐ Webhook/Benachrichtigung ans Social-Media-Team bei „fertig"
- ☐ Multi-Server / mehrere Mini-Server poolen
- ☐ Metadaten/Tagging, Such-/Filterfunktionen

---

## Meilensteine

| Meilenstein | Inhalt | Phasen |
|-------------|--------|--------|
| **M1 — Walking Skeleton** | Login + lokaler Upload, läuft per docker-compose | 0–2 |
| **M2 — Cloud-Upload** | Provider verknüpft, Dateien landen in der Cloud | 3–4 |
| **M3 — Das Kernversprechen** | Bandbreiten-gesteuerter, resilienter Upload + Live-Dashboard | 5–6 |
| **M4 — Feldtauglich** | Sicherheit, Betrieb, Doku — produktiv einsetzbar | 7 |

## Empfohlene Reihenfolge der nächsten Schritte

1. **Tech-Stack bestätigen** (Default-Empfehlung: FastAPI + rclone + React/Vite + SQLite + Docker).
2. **Phase 0** umsetzen → lauffähiges Gerüst + CI.
3. **M1 (Phasen 1–2)** → erstes nutzbares Produkt für das Team (lokale Annahme).
4. Danach iterativ M2 → M3 → M4.
