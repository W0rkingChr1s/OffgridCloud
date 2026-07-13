# OffgridCloud — Konzept

> **Upload when the signal is right.**
> Ein selbst-gehosteter Mini-Server, der Medien-Uploads aus dem Feld entkoppelt
> von instabilen Verbindungen und zuverlässig in Public-Cloud-Speicher überträgt.

---

## 1. Problemstellung

Ein mobiles Team (Auto, Boot, LKW) produziert unterwegs Foto- und Videomaterial
und hat dabei stark schwankenden Empfang. Ein Social-Media-/Redaktionsteam im
Office übernimmt die Weiterverarbeitung und Veröffentlichung — ist also auf einen
**verlässlichen Zustrom der Medien** angewiesen.

Der direkte Upload vom Feld in die Public Cloud scheitert heute regelmäßig an:

- **Verbindungsabbrüchen** mitten im Upload großer Videodateien
- **Timeouts** durch hohe Latenz / schwache Funkzellen
- **Wechselnden Netzen** (LTE → 3G → kein Netz → WLAN im Hafen)
- **Doppel-Uploads / Chaos**, weil niemand den Überblick behält, was schon oben ist
- **Blockierten Geräten**, weil Smartphones/Laptops während des Uploads gebunden sind

## 2. Lösungsidee

OffgridCloud ist eine **Puffer- und Steuerungs-Schicht zwischen Feld und Cloud**.

```
 ┌──────────────┐        lokal/schnell        ┌────────────────────┐      wenn Bandbreite da ist     ┌─────────────────┐
 │  Feld-Team   │  ──────────────────────►    │   OffgridCloud      │  ───────────────────────────►   │  Public Cloud    │
 │ (Handy/Cam)  │   WLAN/LAN am Mini-Server   │   (Mini-Server)     │   resumable, retry, gedrosselt  │  (S3, OneDrive…) │
 └──────────────┘                             └────────────────────┘                                 └─────────────────┘
                                                       │
                                                       ▼
                                              ┌────────────────────┐
                                              │  Social-Media-Team  │  Status, Fortschritt, Freigaben
                                              │   (Office / Web)    │
                                              └────────────────────┘
```

Das Team lädt **lokal** (schnell, stabil) auf den Mini-Server. Der Server
übernimmt danach **autonom** den Upload in die Cloud — wartet auf ausreichende
Bandbreite, setzt unterbrochene Transfers fort, wiederholt fehlgeschlagene und
hält den Status für alle Beteiligten transparent.

### Kernprinzipien

1. **Lokal annehmen, später senden** — Annahme und Cloud-Upload sind entkoppelt.
2. **Bandbreiten-bewusst** — Uploads starten/drosseln je nach gemessener Leitung.
3. **Resilient** — resumable Transfers, automatische Wiederholung, kein Datenverlust.
4. **Transparent** — jeder sieht, was angenommen, in Übertragung und fertig ist.
5. **Self-hosted & offline-fähig** — läuft auf günstiger Hardware ohne Cloud-Zwang.

## 3. Rollen & Rechte (rudimentäres User-Management)

| Rolle        | Rechte |
|--------------|--------|
| **Admin**    | Alle Einstellungen; Benutzer anlegen/sperren; Cloud-Provider verknüpfen; Upload-Ordner anlegen und Providern + Benutzern zuordnen; globale Bandbreiten-/Zeitfenster-Regeln; Logs & Systemstatus einsehen. |
| **Benutzer** | Dateien in die **für ihn freigegebenen Ordner** hochladen; eigene Uploads und deren Upload-Status sehen. Keine Einstellungen. |

> MVP bewusst schlank: zwei feste Rollen, keine feingranularen ACLs. Erweiterbar
> (siehe Roadmap: Gruppen/Teams).

## 4. Datenmodell (Kernobjekte)

- **User** — `id, name, email, passwort_hash, rolle (admin|user), aktiv`
- **CloudProvider** — verknüpfte Ziel-Anbindung
  `id, name, typ, config (verschlüsselt), status`
- **UploadFolder** — logischer Ordner im UI
  `id, name, lokaler_pfad, beschreibung`
- **FolderProviderLink** — *m:n* Ordner ↔ Provider (+ Ziel-Pfad/Bucket je Provider)
- **FolderUserAccess** — *m:n* Ordner ↔ Benutzer (Upload-Freigabe)
- **MediaItem** — angenommene Datei
  `id, folder_id, dateiname, größe, hash, status, hochgeladen_von, erstellt_am`
- **TransferJob** — ein Upload-Vorgang je MediaItem × Zielprovider
  `id, media_id, provider_id, status (queued|running|paused|done|failed),
   fortschritt, versuche, letzter_fehler, bytes_übertragen`

**Status-Lebenszyklus eines MediaItem:**
`received → queued → uploading → verified → done` (mit `failed`/`retrying` als Seitenzweige)

Ein Ordner kann an **mehrere Provider** gespiegelt werden → pro Datei und Ziel
entsteht ein eigener `TransferJob`. Erst wenn alle Pflicht-Ziele `done` sind,
gilt das `MediaItem` als fertig.

## 5. Unterstützte Cloud-Provider

Geforderte Anbindungen — gebündelt nach technischem Protokoll:

| Kategorie            | Anbieter |
|----------------------|----------|
| **S3-kompatibel**    | Amazon S3, MinIO, (Hetzner Object Storage), QNAP/TrueNAS S3-Gateways |
| **Microsoft**        | Azure Blob Storage, OneDrive / SharePoint |
| **WebDAV-basiert**   | Nextcloud, ownCloud, generisches WebDAV, Hetzner Storage Box (WebDAV) |
| **SSH/Datei**        | SFTP, SCP/SSH, Hetzner Storage Box (SFTP), Synology/QNAP/TrueNAS via SFTP |
| **FTP**              | FTP, FTPS |
| **NAS**              | Synology, QNAP, TrueNAS (über SMB/WebDAV/SFTP/S3 je nach Gerät) |

### Architekturentscheidung: rclone als Transfer-Engine

**Empfehlung:** Die eigentlichen Übertragungen laufen über **[rclone](https://rclone.org)**
statt über N einzeln gepflegte SDKs.

**Warum:**
- rclone deckt **praktisch alle geforderten Provider out-of-the-box** ab
  (S3, Azure Blob, OneDrive/SharePoint, WebDAV/Nextcloud/ownCloud, SFTP, FTP/FTPS, …).
- Liefert von Haus aus: **resumable Transfers, Retry, Bandbreitenlimit (`--bwlimit`),
  Integritäts-Check (Hash/Size), Parallelität, `--check-first`**.
- Eine einheitliche Schnittstelle (rclone-Remotes) → wir bauen nur **Konfig-Mapping +
  Orchestrierung**, nicht zehn Integrationen.

OffgridCloud generiert pro `CloudProvider` ein rclone-Remote und ruft rclone
(via `rclone rcd`-API oder Subprozess) für jeden `TransferJob` auf. SCP/SSH ohne
WebDAV/SFTP-Fähigkeit wird über das `sftp`-Backend bzw. ein dünnes Fallback abgedeckt.

## 6. Bandbreiten-Steuerung (Herzstück)

Der Scheduler entscheidet **ob und wie schnell** hochgeladen wird:

1. **Messung** — periodischer, leichter Durchsatz-/Latenz-Check zum Ziel.
2. **Schwellwert** — Upload startet erst über einer konfigurierbaren Mindest-Bandbreite.
3. **Drosselung** — `--bwlimit` dynamisch; optional Zeitpläne
   (z. B. „nachts volle Last, tagsüber 50 %").
4. **Pause/Resume** — bei Einbruch unter Schwellwert wird pausiert, nicht abgebrochen.
5. **Priorität** — Admin kann Ordner/Provider priorisieren (Eilmaterial zuerst).

## 7. Oberfläche (modernes Kachel-Design)

- **Dashboard** — Kacheln je Ordner: Anzahl Dateien, % hochgeladen, aktive Transfers,
  aktuelle Bandbreite, Zielprovider-Badges. Live-Aktualisierung (WebSocket/SSE).
- **Upload-Ansicht (Benutzer)** — Drag & Drop in freigegebene Ordner, chunked Upload,
  Fortschritt pro Datei.
- **Transfers** — laufende/wartende/fertige Jobs, Retry-/Pause-Buttons.
- **Admin → Provider** — Anbieter hinzufügen (Typ wählen → Felder → Verbindungstest).
- **Admin → Ordner** — Ordner anlegen, Providern zuordnen, Benutzern freigeben.
- **Admin → Benutzer** — anlegen, Rolle, sperren, Passwort-Reset.
- **Admin → System** — Bandbreiten-Regeln, Zeitpläne, Logs, Speicherauslastung.

Design-Sprache: Kacheln mit Status-Farbcodierung, Logo-Palette
(Teal `#0EA5A4` → Blau `#1D4ED8` → Indigo `#312E81`), Dark-Mode-fähig.

## 8. Technologie-Stack (Empfehlung)

| Schicht        | Wahl | Begründung |
|----------------|------|------------|
| **Backend**    | Python + **FastAPI** | Async, schnelle API, gute rclone-/Subprozess-Anbindung, einfache Wartung. |
| **Transfer**   | **rclone** | Deckt alle Provider ab (s. o.). |
| **Queue/Worker** | **SQLite-Queue + In-Process-Worker** (MVP) → Redis nur bei echtem Bedarf | Spart einen Dienst → wichtig auf 1 GB RAM. |
| **DB**         | **SQLite** (MVP) → optional PostgreSQL | Appliance-tauglich, keine Extra-Dienste. |
| **Auth**       | JWT + bcrypt, Rollen-Middleware | Schlankes Rollenmodell. |
| **Frontend**   | **React + Vite + TypeScript**, Tailwind CSS | Modernes Kachel-UI; wird zu **statischen Dateien** gebaut und vom Backend ausgeliefert → **kein Node zur Laufzeit**. |
| **Realtime**   | WebSocket / SSE | Live-Fortschritt im Dashboard. |
| **Deployment** | **Nativer systemd-Service** (empfohlen für RPi 3) **oder ein einziges Docker-Image** (multi-arch arm64) | Ein Prozess statt compose-Stack — s. Abschnitt 11. |
| **Secrets**    | Verschlüsselung der Provider-Credentials at rest | Sicherheit der Cloud-Zugänge. |

> Stack ist eine **begründete Empfehlung**, kein Dogma. Falls das Team Node/Go
> bevorzugt, ist die Architektur (rclone-Engine + REST + Kachel-UI) übertragbar.

## 8a. Betrieb auf dem Raspberry Pi 3 (Ressourcen-Budget)

Zielplattform ist u. a. ein **Raspberry Pi 3** — sparsam und lautlos, aber mit
harten Grenzen. Die Architektur ist explizit darauf ausgelegt.

**Die Grenzen:**
- **1 GB RAM** (LPDDR2) — der bestimmende Faktor.
- **microSD** als Systemmedium — langsam und verschleißanfällig bei vielen Schreibvorgängen.
- CPU (Quad-Core A53) reicht problemlos, da **rclone** die Transferarbeit übernimmt.

**Konsequenzen für das Design (statt 3-Container-compose):**

1. **Ein Prozess** — FastAPI liefert API **und** das statische React-UI aus und ruft
   `rclone` als Subprozess. Kein separater Frontend-Dienst, kein Node zur Laufzeit.
2. **Keine zusätzlichen Dienste** — SQLite statt PostgreSQL, In-Process-Worker statt Redis.
3. **Realistisches RAM-Budget:** ~150–250 MB im Betrieb (FastAPI + rclone-Subprozess),
   passt mit Puffer in 1 GB.

**Deployment-Optionen (beide unterstützt):**

| Variante | Für wen | Hinweise |
|----------|---------|----------|
| **Nativer systemd-Service** *(empfohlen für RPi 3)* | Maximale Sparsamkeit | Python-venv + rclone-Binary + `.service`-Unit. Kein Docker-Overhead. |
| **Ein einziges Docker-Image** (multi-arch arm64) | Reproduzierbarkeit, einfache Updates | **Ein** Container, kein compose-Stack. ~50–80 MB extra für den Daemon. |

**Pi-spezifische Pflicht-Regeln:**
- **Medien-Puffer auf externe USB-SSD** legen, nicht auf die SD-Karte (Verschleiß + Platz).
- **Chunked Uploads streamen direkt auf Platte** — niemals ganze Videos im RAM puffern (OOM-Schutz).
- **rclone gedrosselt:** `--transfers 1–2`, moderates `--buffer-size`, `--bwlimit`.
- **64-bit Raspberry Pi OS** empfohlen (arm64-Wheels, bessere Performance).
- **Builds nicht auf dem Pi** — Docker-Image und Frontend in CI bauen, nur fertige Artefakte ausliefern.

## 9. Sicherheit

- Provider-Credentials **verschlüsselt** gespeichert (Key aus Env/Secret).
- HTTPS (Reverse-Proxy / self-signed im Feld).
- Rollenbasierte Zugriffskontrolle auf jeden Endpunkt.
- Audit-Log für Admin-Aktionen und Transfers.
- Optional: Integritäts-Verifikation (Hash-Vergleich lokal ↔ Cloud) vor dem Löschen
  lokaler Kopien.

## 10. Nicht-Ziele (MVP)

- Keine Medien-Bearbeitung/Transcoding im Server.
- Kein bidirektionaler Sync (nur Upload Feld → Cloud).

## 11. Umgesetzte Erweiterungen (nach dem MVP)

Der ursprüngliche Backlog ist vollständig abgearbeitet — der Vollständigkeit halber:

- **Teams/Gruppen** — Ordner-Freigabe an ganze Teams statt nur Einzelbenutzer.
- **Thumbnails**, **PWA fürs Feld**, **Fertig-Webhook**, **aktive Bandbreiten-Probe**.
- **Netzwerk-Redundanz** — Rückfall-WLAN bei Router-Ausfall (`docs/NETZWERK-REDUNDANZ.md`).
- **VPN-Client** — ins Heimnetz einwählen, um interne Ziele zu erreichen (`docs/VPN.md`).
- **Tags & Suche** — freie Tags je Medium + ordnerübergreifende, zugriffsgeschützte Suche.
- **Multi-Server-Pool** — mehrere Boxen als Flotte in einer Übersicht (read-only-
  Aggregation über ein Shared-Token, keine verteilte Koordination) —
  `docs/MULTI-SERVER-POOL.md`.

Details in der Roadmap (`docs/ENTWICKLUNGSPLAN.md`).
