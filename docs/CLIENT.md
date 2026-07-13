# OffgridCloud — Desktop-Client (Auto-Upload-Agent)

> **Status:** Plan / Konzept. Noch keine Implementierung — dieses Dokument legt
> Architektur, Tech-Stack und einen phasierten Umsetzungsplan fest.

Ein schlanker Hintergrund-Agent für **macOS, Linux und Windows**, der lokale
Ordner überwacht und neue Dateien **automatisch** in die OffgridCloud-Box lädt —
resilient gegen instabile Verbindungen, mit Live-Statusmeldungen vom Server.

Das Feld-Team kopiert Medien einfach in einen überwachten Ordner (oder die Kamera
sichert dorthin). Der Client übernimmt den Rest: erkennen, in die Warteschlange
legen, hochladen (resumable), Server-Status durchreichen — ohne dass jemand ein
Web-UI öffnen muss.

---

## 1. Ziele & Nicht-Ziele

**Ziele**

- 📂 **Ordner-Watchdog** — ein oder mehrere lokale Ordner beobachten und neue/
  geänderte Dateien automatisch erfassen.
- ⬆️ **Auto-Upload** — Dateien über das **bestehende** resumable Chunk-Protokoll
  der Box hochladen (kein neues Server-Protokoll nötig).
- 🔁 **Resilient** — überlebt Verbindungsabbrüche, Neustarts und Standby:
  persistente Warteschlange, Wiederaufnahme genau am Server-Offset.
- 🛰️ **Server-Watchdog** — Erreichbarkeit der Box überwachen, bei Ausfall
  pausieren und automatisch wieder aufnehmen (Backoff).
- 📡 **Statusmeldungen durchreichen** — den `/api/events`-SSE-Stream der Box
  konsumieren und lokal anzeigen (Tray-Tooltip, Benachrichtigungen, CLI/Log).
- 🖥️ **Drei Plattformen, ein Binary je OS** — nativ, klein, ohne schwere Runtime.
- 🧩 **Zwei Betriebsarten** — Headless-Dienst (systemd/launchd/Windows-Service)
  **und** optionales System-Tray mit Mini-UI.

**Nicht-Ziele (vorerst)**

- Kein Zwei-Wege-Sync und kein Download/Restore (der Client schiebt nur nach oben).
- Keine Konflikt-/Merge-Logik — die Box ist die Senke, nicht die Quelle.
- Keine Änderung an der Cloud-Provider-Logik: die Box übernimmt wie bisher den
  Weitertransport in die Public Cloud.

---

## 2. Wie es in OffgridCloud passt

Der Client ist die **dritte Eingangsart** neben Web-UI und PWA — er benutzt
exakt dieselbe öffentliche API und braucht darum am Server fast nichts Neues:

```
  Kamera / Feld-Laptop                    OffgridCloud-Box                Public Cloud
 ┌─────────────────────┐            ┌───────────────────────────┐      ┌────────────┐
 │  Überwachter Ordner │            │  FastAPI + Chunk-Upload    │      │  S3 / SFTP │
 │  ┌───────────────┐  │  HTTPS     │  ┌─────────────────────┐  │ rclone│  Nextcloud │
 │  │  neue Dateien │──┼───────────▶│  │ /api/folders/../    │  │─────▶│  WebDAV …  │
 │  └───────────────┘  │  resumable │  │   uploads (chunked) │  │      └────────────┘
 │        ▲            │            │  └─────────────────────┘  │
 │  ┌─────┴────────┐   │   SSE      │  ┌─────────────────────┐  │
 │  │ Client-Agent │◀──┼────────────┼──│ /api/events (Status)│  │
 │  └──────────────┘   │            │  └─────────────────────┘  │
 └─────────────────────┘            └───────────────────────────┘
```

Wiederverwendetes Server-Protokoll (siehe `backend/app/routers/uploads.py`,
`auth.py`, `events.py`):

| Schritt | Endpunkt | Zweck |
|--------|----------|-------|
| Login | `POST /api/auth/login` → JWT | Anmeldung, Token für alle Aufrufe |
| Ordner | `GET /api/folders` | verfügbare Ziel-Ordner ermitteln |
| Session öffnen | `POST /api/folders/{id}/uploads` `{filename,size}` | liefert `upload_id` + `received` |
| Chunk senden | `PUT /api/uploads/{id}` Header `X-Offset` | append ab aktuellem Offset; `409` mit `X-Received` → resync |
| Abschließen | `POST /api/uploads/{id}/complete` | Hash, `MediaItem`, Cloud-Transfer wird angestoßen |
| Wiederaufnahme | `GET /api/uploads/{id}` | Server-Offset nach Neustart abfragen |
| Abbrechen | `DELETE /api/uploads/{id}` | angebrochene Session verwerfen |
| Status | `GET /api/events?token=…` (SSE) | Live-Snapshot: Ordner-Zähler, Transfers, Bandbreite |

Die Chunk-Größe und der Ablauf entsprechen der Referenz im Frontend
(`frontend/src/upload.ts`, 4 MiB Chunks) — der Client ist damit ein
Server-kompatibler „headless Uploader".

---

## 3. Tech-Stack-Empfehlung: **Go**

Passt zur Projekt-DNA (sparsam, ein Binary, feldtauglich):

- **Ein statisches Binary je OS/Arch** — kein Runtime-Zoo, kein Node/Python
  auf dem Feld-Laptop. Cross-Compile in CI (analog `deploy/` & `release.yml`).
- Kleiner Speicher-Fußabdruck, robuste Std-Lib für HTTP/Streaming.
- Reife Bausteine: `fsnotify` (Datei-Events), `getlantern/systray` (Tray),
  OS-Keychain-Anbindung, `kardianos/service` (systemd/launchd/Win-Service aus
  einem Code-Pfad).
- Gut testbar; keine GUI-Toolchain nötig für den Kern (Tray ist optional/additiv).

**Alternativen & warum nicht (jetzt):**

- *Tauri/Rust* — schönere GUI, aber mehr Build-Komplexität; für einen
  Hintergrund-Agenten Overkill.
- *Electron* — zu schwer, widerspricht dem „sparsam"-Prinzip.
- *Python (wie Backend)* — Code-Nähe, aber plattformübergreifendes Packaging
  (PyInstaller je OS, Signaturen) ist mühsamer als ein Go-Binary.

> Tech-Stack wird vor Phase C1 final bestätigt (wie im Entwicklungsplan üblich).

---

## 4. Komponenten-Architektur

```
                 ┌──────────────────────────────────────────────┐
                 │                 Client-Agent                  │
                 │                                               │
  Dateisystem ──▶│  Watcher ──▶ Quiescence ──▶ Scanner/Dedup     │
                 │   (fsnotify + periodischer Rescan)            │
                 │        │                                      │
                 │        ▼                                      │
                 │  Persistente Queue (SQLite/bbolt)             │
                 │        │                                      │
                 │        ▼                                      │
                 │  Uploader (resumable, N parallel) ───────────┼──▶ Box  /api/uploads
                 │        │            ▲                         │
                 │        │            │ 409/Offset-Resync       │
                 │        ▼            │                         │
                 │  Server-Watchdog (Health-Poll, Backoff) ◀─────┼──▶ Box  /api/health
                 │        │                                      │
                 │        ▼                                      │
                 │  Status-Client (SSE) ────────────────────────┼──▶ Box  /api/events
                 │        │                                      │
                 │        ▼                                      │
                 │  Presenter: Tray · Notifications · CLI · Log  │
                 └──────────────────────────────────────────────┘
```

**Watcher + Quiescence.** `fsnotify` meldet Dateiänderungen; da eine noch
schreibende Kamera/Kopie unvollständig wäre, wird eine Datei erst als „stabil"
markiert, wenn Größe **und** mtime für ein Fenster (z. B. 5 s) unverändert
bleiben. Zusätzlich ein periodischer Rescan (fängt Events, die das OS verschluckt,
und den Zustand nach Client-Neustart).

**Persistente Queue.** Jede erfasste Datei bekommt einen Eintrag
(Pfad, Größe, mtime, SHA-256 optional, Ziel-Ordner-ID, `upload_id`, Offset,
Status). In einer lokalen DB (bbolt oder SQLite), damit Neustart/Standby nichts
verliert. Idempotenz per Pfad+Größe+mtime (bereits hochgeladene Dateien werden
nicht doppelt gesendet).

**Uploader.** Setzt exakt das Server-Protokoll um: Session öffnen, ab
`received` chunk-weise `PUT` mit `X-Offset`, bei `409` das autoritative
`X-Received` übernehmen und weiterlaufen, dann `complete`. Konfigurierbare
Parallelität (Default 1–2, Pi-schonend). Retry mit exponentiellem Backoff.

**Server-Watchdog.** Periodischer `GET /api/health` (bzw. `/api/auth/me`).
Bei Ausfall: Uploads pausieren, Queue behalten, mit Backoff neu verbinden,
danach automatisch fortsetzen. Optional Reaktion auf die Netzwerk-Redundanz der
Box (Rückfall-WLAN, siehe `docs/NETZWERK-REDUNDANZ.md`).

**Status-Client (Durchreichen).** Hält den SSE-Stream `/api/events` offen
(Token als Query-Param, da EventSource keine Header kann) und übersetzt die
Snapshots (Ordner-Zähler, laufende Transfers, Bandbreiten-Gate) in:
Tray-Tooltip/-Icon, Desktop-Benachrichtigungen bei „fertig"/„fehlgeschlagen",
`offgridcloud-client status` (CLI) und strukturiertes Log.

---

## 5. Betriebsarten & Plattform-Integration

| OS | Dienst (headless) | Tray/UI | Autostart |
|----|-------------------|---------|-----------|
| **Linux** | systemd **user**-Service | optionales `systray` (X11/Wayland) | `systemctl --user enable` |
| **macOS** | `launchd` LaunchAgent | Menüleisten-Icon | LaunchAgent in `~/Library/LaunchAgents` |
| **Windows** | Windows-Service (`kardianos/service`) | Tray-Icon (Notification Area) | Dienst „Automatisch" |

Ein Binary, zwei Modi: `--daemon` (headless) oder Tray. Der Tray zeigt: verbunden/
getrennt, Queue-Länge, aktueller Upload + %, letzte Fehler, „Ordner öffnen",
„Pause/Fortsetzen", „Box im Browser öffnen".

---

## 6. Konfiguration

Menschlich lesbare Datei am OS-üblichen Ort (`~/.config/offgridcloud/config.yaml`,
`~/Library/Application Support/…`, `%APPDATA%\OffgridCloud\…`). **Keine Secrets im
Klartext**: JWT und (optional gespeicherte) Zugangsdaten in die **OS-Keychain**
(macOS Keychain, GNOME Keyring/libsecret, Windows Credential Manager).

```yaml
server:
  url: https://box.local:8000
  verify_tls: true          # self-signed im Feld: Fingerprint pinnen statt abschalten
watches:
  - path: /home/team/Dropzone/Kamera-A
    folder_id: 3            # Ziel-Ordner auf der Box (per Name auflösbar)
    delete_after_upload: false
upload:
  chunk_size_mib: 4
  parallel: 1
  quiescence_seconds: 5
bandwidth:
  respect_server_gate: true # der Client bremst; die Box steuert per --bwlimit
notifications:
  on_done: true
  on_failed: true
```

Ersteinrichtung per `offgridcloud-client login` (interaktiv: URL, E-Mail,
Passwort → Token in Keychain) und `offgridcloud-client add-watch <pfad>`.

**TLS im Feld:** Die Box läuft oft mit self-signed Zertifikat. Statt Prüfung
abzuschalten, wird der Zertifikats-Fingerprint beim ersten Login angezeigt und
gepinnt (TOFU) — sichere und praktikable Lösung ohne CA-Infrastruktur.

---

## 7. Server-seitige Ergänzungen (klein, optional)

Der Client kommt fast ohne Server-Änderungen aus. Sinnvoll wären:

1. **Token-Refresh / lange Lebensdauer.** Heute ist Logout rein client-seitig,
   ohne Refresh (`auth.py`). Ein Agent, der wochenlang läuft, sollte bei `401`
   sauber neu anmelden (Credentials aus Keychain) **oder** ein optionaler
   Refresh-/Long-Lived-Token für Geräte wird ergänzt. → als Server-Issue führen.
2. **Optionaler Client-Health-/Version-Ping.** Ein `POST /api/clients/heartbeat`
   (Hostname, Version, Queue-Länge), damit Admins im Dashboard sehen, welche
   Agenten aktiv sind. Rein additiv; MVP kommt ohne aus.
3. **Nichts weiter** — Upload- und SSE-Endpunkte werden unverändert genutzt.

---

## 8. Phasenplan

Analog zum Server-Entwicklungsplan: jede Phase ist lauffähig.

### Phase C0 — Fundament
- Repo-Struktur `client/` (Go-Modul), CI-Cross-Build (linux/amd64+arm64,
  darwin/amd64+arm64, windows/amd64), Lint/Tests.
- Config-Loader + Keychain-Anbindung, `login`/`me`-Flow gegen die Box.

### Phase C1 — Uploader-Kern (Walking Skeleton)
- Resumable Chunk-Upload gegen die echte API (open → PUT/X-Offset → complete),
  inkl. `409`-Resync und `GET`-Wiederaufnahme.
- CLI `upload <datei> --folder <id>` als erster nutzbarer Durchstich.

### Phase C2 — Watchdog & Queue
- `fsnotify`-Watcher + Quiescence + periodischer Rescan.
- Persistente Queue (bbolt/SQLite), Idempotenz, Retry/Backoff.
- `--daemon`-Modus: Ordner rein → landet automatisch auf der Box.

### Phase C3 — Server-Watchdog & Statusmeldungen
- Erreichbarkeits-Watchdog (Health-Poll, Pause/Resume, Backoff).
- SSE-Consumer für `/api/events`, Status im Log und über `status`-CLI.

### Phase C4 — Desktop-Integration
- System-Tray (drei OS), Autostart als Dienst (systemd/launchd/Windows-Service).
- Desktop-Benachrichtigungen bei „fertig"/„fehlgeschlagen".

### Phase C5 — Härtung & Auslieferung
- TLS-Pinning (TOFU), signierte Builds soweit möglich, Auto-Update-Check.
- Installer/Pakete (`.pkg`/`.dmg`, `.deb`/AppImage, `.msi`), Doku.
- Optionaler Server-Heartbeat + Admin-Ansicht (falls in Phase 7 ergänzt).

### Meilensteine
| Meilenstein | Inhalt | Phasen |
|-------------|--------|--------|
| **C-M1 — Uploader** | CLI lädt resumable in die Box | C0–C1 |
| **C-M2 — Auto-Agent** | Ordner-Watchdog + persistente Queue, headless | C2 |
| **C-M3 — Feld-Client** | Server-Watchdog, Status-Durchreichung, Tray | C3–C4 |
| **C-M4 — Auslieferbar** | signierte Pakete, TLS-Pinning, Doku | C5 |

---

## 9. Offene Fragen (vor C1 zu klären)

- **Tech-Stack final:** Go (Empfehlung) bestätigen — oder Tauri, falls eine
  reichere GUI gewünscht ist?
- **Token-Lebensdauer:** Re-Login bei `401` (kein Server-Change) genügt für MVP,
  oder soll gleich ein Geräte-/Refresh-Token am Server ergänzt werden?
- **Ordner-Zuordnung:** feste `folder_id` in der Config, oder Auswahl per
  Ordner-**Name** (robuster gegenüber DB-Wechsel)?
- **Löschen nach Upload:** lokale Datei nach verifiziertem Upload entfernen
  (spiegelt die Server-Option `delete_local_after_upload`) — Default aus.
- **Repo:** Client im selben Monorepo unter `client/` (empfohlen, geteilte CI/
  Doku) oder separates Repository?
