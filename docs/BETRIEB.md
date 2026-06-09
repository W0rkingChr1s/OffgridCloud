# OffgridCloud — Installations- & Betriebshandbuch

Dieses Handbuch beschreibt Installation, Absicherung und Betrieb auf einem
Mini-Server (z. B. Raspberry Pi 3).

## 1. Installation

### Variante A — nativer Service (empfohlen für RPi 3)

```bash
git clone <repo> && cd OffgridCloud
sudo ./deploy/install.sh
sudo nano /opt/offgridcloud/.env        # Secrets setzen (s. u.)
sudo systemctl enable --now offgridcloud
```

### Variante B — ein Docker-Image

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

- **nativ:** `git pull` → `sudo ./deploy/install.sh` → `sudo systemctl restart offgridcloud`
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
