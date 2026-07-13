# OffgridCloud — Installations- & Betriebshandbuch

Dieses Handbuch beschreibt Installation, Absicherung und Betrieb auf einem
Mini-Server (z. B. Raspberry Pi 3).

## 1. Installation

### Variante A′ — One-Liner (empfohlen, frischer Server)

Ein Befehl auf einem frischen Debian/Raspberry Pi OS/Fedora/Arch installiert
alle Abhängigkeiten (git, Node, Python, rclone), klont das Repo nach
`/opt/offgridcloud/src`, baut, richtet den Dienst ein, startet ihn und prüft den
Health-Endpoint:

```bash
curl -fsSL https://raw.githubusercontent.com/W0rkingChr1s/OffgridCloud/main/deploy/bootstrap.sh | sudo bash
# mit Optionen:
curl -fsSL https://raw.githubusercontent.com/W0rkingChr1s/OffgridCloud/main/deploy/bootstrap.sh \
  | sudo bash -s -- --with-ffmpeg --port 8080 --admin-email admin@example.com
```

Überschreibbar per Env: `OGC_REPO`, `OGC_BRANCH`, `OGC_SRC`. **Update:** den
One-Liner erneut ausführen (Daten & `.env` bleiben erhalten).

### Variante 0 — lokal ausprobieren (ohne Installation)

```bash
git clone <repo> && cd OffgridCloud
./quickstart.sh                         # http://localhost:8000, Ctrl-C beendet
```

Baut das Frontend, legt eine venv an, schreibt eine lokale `.env` (mit zufälligem
Admin-Passwort, das einmalig angezeigt wird) und startet den Server im Vordergrund.
Kein root, kein systemd. Für den echten Betrieb Variante A verwenden.

### Variante A — nativer Service (empfohlen für RPi 3)

```bash
git clone <repo> && cd OffgridCloud
sudo ./deploy/install.sh --start        # baut, installiert, startet, prüft Health
```

Der Installer erzeugt `/opt/offgridcloud/.env` mit zufälligem `OGC_SECRET_KEY`
**und zufälligem Admin-Passwort** — das Passwort wird am Ende **einmalig**
angezeigt, also notieren. rclone wird über den offiziellen Installer in aktueller
Version bereitgestellt.

Optionen: `--admin-email EMAIL`, `--port PORT`, `--prefix DIR`, `--with-ffmpeg`
(Video-Thumbnails), `--no-service`, `--start`. Ohne `--start` danach:

```bash
sudo nano /opt/offgridcloud/.env        # z. B. OGC_BUFFER_DIR auf USB-SSD
sudo systemctl enable --now offgridcloud
```

Entfernen: `sudo ./deploy/uninstall.sh` (behält Daten/`.env`; `--purge` löscht alles).

### Variante B — Windows (PowerShell)

```powershell
# Im Repo-Verzeichnis. Fehlende Tools (Python, Node, rclone) werden – sofern
# winget vorhanden ist – automatisch installiert.
powershell -ExecutionPolicy Bypass -File deploy\install.ps1

# Server starten:
powershell -ExecutionPolicy Bypass -File deploy\run.ps1          # http://localhost:8000

# Optional als Autostart-Dienst registrieren (Admin-PowerShell):
powershell -ExecutionPolicy Bypass -File deploy\install.ps1 -InstallService
```

Der Installer legt eine `.env` im Repo-Stamm an (mit zufälligem `OGC_SECRET_KEY`).
`run.ps1` lädt diese `.env` und startet uvicorn aus der mitgelieferten venv.
Den Dienst stoppen/starten: `Stop-ScheduledTask -TaskName OffgridCloud` /
`Start-ScheduledTask -TaskName OffgridCloud`.

### Variante C — ein Docker-Image (plattformübergreifend)

```bash
docker build -f deploy/Dockerfile -t offgridcloud .
docker run -d --name offgridcloud \
  -p 8000:8000 \
  -v /mnt/ssd/offgrid:/data \
  --env-file .env \
  --restart unless-stopped \
  offgridcloud
```

## 2. Konfiguration (.env)

| Variable | Bedeutung |
|----------|-----------|
| `OGC_SECRET_KEY` | **Kritisch.** Signiert JWTs und verschlüsselt Provider-Credentials. Lang & zufällig wählen, sicher sichern. Ändern macht gespeicherte Provider-Secrets unbrauchbar. |
| `OGC_INITIAL_ADMIN_EMAIL` / `OGC_INITIAL_ADMIN_PASSWORD` | Initial-Admin beim ersten Start. **Passwort nach erstem Login ändern.** |
| `OGC_DATA_DIR` | DB & App-Status. |
| `OGC_BUFFER_DIR` | Medien-Puffer — **auf externe USB-SSD** legen, nicht auf die SD-Karte. |
| `OGC_WORKER_*` | Worker-Schalter, Poll-Intervall, max. Versuche. |

Secret-Key erzeugen: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`

## 3. HTTPS / Reverse-Proxy

OffgridCloud lauscht intern auf Port 8000 (HTTP). Im Betrieb davor einen
Reverse-Proxy mit TLS setzen:

- **Caddy** (einfachstes Auto-TLS): `deploy/Caddyfile` anpassen und starten.
  Für den Offline-Feldeinsatz `tls internal` (self-signed) verwenden.
- **nginx**: `deploy/nginx.conf.example` als Vorlage; enthält bereits die
  SSE-freundliche `/api/events`-Location (Buffering aus, langer Timeout) und ein
  Self-signed-Cert-Rezept.

## 4. Bandbreiten-Steuerung (Betrieb)

Im Admin-Bereich unter **Bandbreite**:
- **Standard-Limit** (`--bwlimit`) global drosseln.
- **Zeitfenster** definieren (z. B. nachts volle Last, tags 50 %). Fenster dürfen
  über Mitternacht laufen.
- **Mindest-Bandbreite** aktivieren: Uploads pausieren, solange der gemessene
  Durchsatz darunter liegt (Messung aus realen Transfers, mit Cooldown).
- **Prioritäten** je Ordner↔Provider setzen (Eilmaterial zuerst).

**Messung ohne Konfiguration:** Der Knopf **„Jetzt messen"** funktioniert sofort
— es muss nichts eingetragen werden.

- Ist die **Ookla Speedtest CLI** (`speedtest`) installiert, wird sie bevorzugt:
  Sie misst den Upload gegen einen nahen Speedtest-Server und ist unabhängig von
  Bot-Sperren fremder CDNs. `deploy/install.sh` installiert sie automatisch
  (abschaltbar mit `--no-speedtest`); manuell: <https://www.speedtest.net/apps/cli>.
- Ohne die CLI lädt die Messung als Fallback eine öffentliche Test-Datei
  (Standard: Cloudflare) und misst den Durchsatz. Manche Netze/Regionen
  beantworten diese Anfrage mit **HTTP 403** — dann hilft die Speedtest CLI oder
  ein eigenes Testziel unter **System** (Probe-URL bzw. `OGC_DEFAULT_PROBE_URL`).
- Schlägt eine Messung fehl, zeigt die Oberfläche den konkreten Grund an
  (z. B. `403 Forbidden`, `NoServers`, Timeout).

## 5. Speicher-Management

Unter **System**:
- **Disk-Auslastung** des Puffers wird angezeigt; bei wenig freiem Platz erscheint
  eine Warnung (< 10 % oder < 500 MB frei).
- **„Lokale Kopie nach Upload löschen"**: ist diese Option aktiv, wird die
  Pufferdatei entfernt, sobald **alle** Zielprovider eines Mediums erfolgreich
  bestätigt sind (rclone verifiziert Größe/Hash). Spart Platz auf dem Mini-Server.

## 6. Audit-Log

Unter **System → Aktivität** sind sicherheitsrelevante Admin-Aktionen
protokolliert (Benutzer/Provider/Ordner anlegen & löschen, Verknüpfungen,
Bandbreiten- und System-Änderungen).

## 7. Backup & Restore

```bash
# Sichert DB + .env (NICHT den Medien-Puffer):
sudo ./deploy/backup.sh /opt/offgridcloud/data /opt/offgridcloud/.env /pfad/zum/backup
```

Wiederherstellen:
1. Service stoppen: `sudo systemctl stop offgridcloud`
2. `offgridcloud.db` (und ggf. `.env`) aus dem Archiv zurück nach `OGC_DATA_DIR` kopieren.
3. Service starten: `sudo systemctl start offgridcloud`

> Der `OGC_SECRET_KEY` (in `.env`) ist zum Entschlüsseln der Provider-Credentials
> nötig — Backup sicher und getrennt aufbewahren.

## 8. Monitoring & Health

- `GET /api/health` — unauthentifizierter Liveness-Check (für Uptime-Monitore).
- Admin **System**-Seite — Disk-Auslastung, rclone-Verfügbarkeit, Einstellungen.
- Logs:
  - nativ: `journalctl -u offgridcloud -f`
  - Docker: `docker logs -f offgridcloud`

## 9. Updates

Instanzen prüfen automatisch gegen die **GitHub-Releases** des Projekts
(System-Seite zeigt aktuelle vs. neueste Version). Releases entstehen durch
einen Versions-Tag (`git tag v0.2.0 && git push origin v0.2.0` → Workflow
`release.yml` baut & veröffentlicht).

- **nativ, ein Befehl** (empfohlen): aktualisiert auf das neueste Release,
  baut neu, startet den Dienst neu — Daten, `.env` und Port bleiben erhalten:
  ```bash
  sudo /opt/offgridcloud/src/deploy/update.sh          # oder --check nur prüfen
  ```
- **One-Click im Web-UI:** beim Installieren mit `--self-update` aktivieren
  (`sudo ./deploy/install.sh --self-update`). Dann erscheint unter **System**
  ein „Jetzt aktualisieren"-Knopf, sobald ein neueres Release vorliegt. Ohne
  diese Option zeigt die UI stattdessen den obigen Befehl an.
- **Docker:** Image neu bauen/ziehen, Container ersetzen (Volume `/data` bleibt erhalten).

Unterbrochene Transfers werden beim Start automatisch wieder eingereiht
(Resume-Recovery); laufende Uploads gehen nicht verloren.

## 10. Troubleshooting

| Symptom | Ursache / Lösung |
|---------|------------------|
| Provider-Test schlägt fehl: „rclone nicht installiert" | rclone fehlt im PATH; Image/Install-Skript bringt es mit. |
| Uploads bleiben „wartend" | Bandbreiten-Gate aktiv (Mindest-Bandbreite) oder kein Provider verknüpft. |
| Login schlägt nach Key-Wechsel fehl / Provider-Test rot | `OGC_SECRET_KEY` wurde geändert → Provider-Credentials neu eingeben. |
| Wenig Speicher | Externe SSD für `OGC_BUFFER_DIR`; „Lokale Kopie nach Upload löschen" aktivieren. |
