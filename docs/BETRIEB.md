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
sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/W0rkingChr1s/OffgridCloud/main/deploy/bootstrap.sh)"
```

> **Warum nicht `… | sudo bash`?** Beim Pipe-in-sudo ist die Standardeingabe des
> Installers die heruntergeladene Datei, nicht deine Tastatur — modernes `sudo`
> läuft in einem eigenen Pseudo-Terminal und reicht Tastendrücke dann nicht
> durch, sodass das interaktive Menü nicht auf Eingaben reagiert. Die Form oben
> übergibt `sudo` deine Tastatur. Alternativ: erst herunterladen, dann
> ausführen — `curl -fsSL …/bootstrap.sh -o ogc.sh && sudo bash ogc.sh`.

Der Installer **fragt danach interaktiv** ab, was eingerichtet werden soll (Port,
Admin-E-Mail, Video-Thumbnails, VPN, Kiosk-Menü usw.). Ist `whiptail` vorhanden
(auf Debian/Raspberry Pi OS meist vorinstalliert, sonst versucht der Installer es
still nachzuinstallieren), erscheint ein **grafisches Terminal-Menü** mit Eingabe­feldern
und einer Funktions-Checkliste; ohne `whiptail` greifen die einfachen Text-Abfragen —
in beiden Fällen ist der eingetragene Wert die Vorgabe. Für eine **unbeaufsichtigte**
Installation die Fragen per Umgebungs­variablen vorbeantworten (durch `sudo` durchreichen):

```bash
curl -fsSL https://raw.githubusercontent.com/W0rkingChr1s/OffgridCloud/main/deploy/bootstrap.sh \
  | sudo OGC_NONINTERACTIVE=1 OGC_PORT=8080 OGC_ADMIN_EMAIL=admin@example.com OGC_WITH_FFMPEG=1 bash
```

Überschreibbar per Env: `OGC_REPO`, `OGC_BRANCH`, `OGC_SRC` sowie alle `OGC_*`
des Installers (siehe `deploy/install.sh --help`). **Update:** den One-Liner
erneut ausführen (Daten & `.env` bleiben erhalten) — der Installer **erkennt die
bestehende Installation** und wählt jede bereits aktive Funktion (Dienst, Kiosk,
Rückfall-WLAN, VPN, Video-Thumbnails, Chromium) sowie Admin-E-Mail und Port als
Vorgabe vor. Ein Re-Run ist damit ein vollwertiges Update: einfach **mit Enter
durchbestätigen**, dann werden App und alle vorhandenen Funktionen aktualisiert —
ohne Flags oder `OGC_*`-Variablen. Details unter [§9 Updates](#9-updates).

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
sudo ./deploy/install.sh                 # fragt interaktiv, baut, installiert, startet
```

Der Installer stellt eine kurze Liste Fragen (Enter = Vorgabe in eckigen Klammern):
Verzeichnis, Admin-E-Mail, Port, Video-Thumbnails, Speedtest-CLI, Netzwerk-Redundanz,
VPN, Kiosk-Menü, ob der Dienst gleich starten soll. Danach läuft alles allein.

Er erzeugt `/opt/offgridcloud/.env` mit zufälligem `OGC_SECRET_KEY` **und
zufälligem Admin-Passwort** — das Passwort wird am Ende **einmalig** angezeigt,
also notieren. rclone wird über den offiziellen Installer in aktueller Version
bereitgestellt.

Für Skripte/Automatisierung gibt es **keine Flags**, sondern `OGC_*`-Variablen
(`sudo ./deploy/install.sh --help` listet sie). Beispiel — ohne Rückfragen, ohne
Autostart, damit man vorher noch `.env` anpassen kann:

```bash
sudo OGC_NONINTERACTIVE=1 OGC_START=0 ./deploy/install.sh
sudo nano /opt/offgridcloud/.env        # z. B. OGC_BUFFER_DIR auf USB-SSD
sudo systemctl enable --now offgridcloud
```

Entfernen: `sudo ./deploy/uninstall.sh` (behält Daten/`.env`; `--purge` löscht
alles inkl. der `ogc-wifi-*`-WLAN-Profile). Räumt auch alle Zusatzfunktionen ab:
Kiosk-Konsole, HTTPS (Caddy-Konfiguration weg, ursprünglicher Hostname zurück),
Netzwerk-Redundanz (Watchdog + Fallback-AP), VPN-Drop-in und sämtliche
sudoers-Regeln. Installierte Pakete (caddy, avahi, rclone, ffmpeg, …) bleiben.

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

Der Installer richtet HTTPS automatisch ein (Frage „HTTPS aktivieren? Ja",
Standard). Er installiert **Caddy** als Reverse-Proxy und **avahi** für den
mDNS-Namen und erzeugt:

- einen **lokalen Zugang** `https://<hostname>.local` mit selbstsigniertem
  Zertifikat — funktioniert offline im Feld, ohne Domain und ohne Internet. Die
  Vorgabe ist `offgridcloud-XXXXX` (`XXXXX` = 5 Zufallszeichen aus Klein­buchstaben
  und Ziffern), damit mehrere Boxen im selben Netz nicht auf denselben Namen
  hören. Im Installer lässt sich der Name frei überschreiben, z. B. auf
  `offgridcloud`;
- optional zusätzlich einen **öffentlichen Zugang** über eine echte Domain mit
  automatischem Let's-Encrypt-Zertifikat, sobald eine Domain hinterlegt ist.

**Domain später ändern:** im Portal unter **System → HTTPS** die Domain
eintragen oder entfernen — kein Neu-Installieren nötig. Der lokale
`.local`-Zugang bleibt immer erhalten. Damit ein echtes Zertifikat ausgestellt
werden kann, muss die Domain per DNS auf die Box zeigen und die Ports 80/443
müssen von außen erreichbar sein (Portweiterleitung am Router).

**Manuelle Alternative:** Wer statt Caddy lieber nginx nutzt, findet in
`deploy/nginx.conf.example` eine Vorlage (inkl. Self-signed-Cert-Rezept und der
SSE-freundlichen `/api/events`-Location).

**HTTPS wieder zurückbauen:** entweder beim erneuten Installer-Lauf die Frage
„HTTPS aktivieren?" mit **nein** beantworten (eine bestehende Einrichtung wird
dann automatisch entfernt) oder direkt:

```bash
sudo ./deploy/https/uninstall.sh
```

Beides entfernt die verwaltete Caddy-Konfiguration, deaktiviert caddy, löscht
die sudoers-Regel samt `.env`-Verdrahtung und stellt den ursprünglichen
Hostnamen wieder her (sofern er beim Einrichten aufgezeichnet wurde). Die
Pakete caddy/avahi bleiben installiert.

### 3.1 Passkeys (WebAuthn)

Nutzer können sich zusätzlich zum Passwort per **Passkey** anmelden (Fingerabdruck,
Gesichtserkennung, Sicherheitsschlüssel). Voraussetzung ist HTTPS — also der per
Installer eingerichtete lokale Zugang `https://<hostname>.local` (Vorgabe
`offgridcloud-XXXXX.local`, siehe oben) oder eine echte Domain (siehe oben). Über
eine nackte LAN-IP ohne HTTPS funktionieren Passkeys nicht.

- **Einrichten:** eingeloggt unter **Passkeys** → „Passkey hinzufügen".
- **Anmelden:** auf der Login-Seite „Mit Passkey anmelden" (mit ausgefüllter
  E-Mail gezielt, ohne E-Mail als Ein-Klick).
- **Zwei Zugänge:** Ein Passkey gilt nur für die Adresse, unter der er angelegt
  wurde. Wer sowohl lokal (`https://<hostname>.local`) als auch über eine Domain
  zugreift, legt pro Adresse einen eigenen Passkey an.
- **Fallback:** Das Passwort bleibt immer gültig. Geht ein Gerät verloren, per
  Passwort anmelden und den Passkey unter **Passkeys** löschen; ein Admin kann
  zudem das Passwort zurücksetzen.

## 4. Bandbreiten-Steuerung (Betrieb)

Im Admin-Bereich unter **Bandbreite**:
- **Standard-Limit** (`--bwlimit`) global drosseln.
- **Zeitfenster** definieren (z. B. nachts volle Last, tags 50 %). Fenster dürfen
  über Mitternacht laufen.
- **Mindest-Bandbreite** aktivieren: Uploads pausieren, solange der gemessene
  Durchsatz darunter liegt (Messung aus realen Transfers, mit Cooldown).
- **Prioritäten** je Ordner↔Provider setzen (Eilmaterial zuerst).

**Messung ohne Konfiguration:** Der Knopf **„Jetzt messen"** funktioniert sofort
— es muss nichts eingetragen werden. Die Messung probiert der Reihe nach mehrere
Wege und nimmt das erste Ergebnis:

1. **HTTP-Probe** (schnell): lädt eine öffentliche Test-Datei (Standard:
   Cloudflare) über **mehrere parallele Verbindungen** und summiert den
   Durchsatz — ein einzelner TCP-Stream unterschätzt die Bandbreite auf Leitungen
   mit Latenz stark. Schlägt das primäre Ziel fehl, wird automatisch ein zweites
   Ziel versucht. Ein eigenes Ziel lässt sich unter **System** eintragen
   (Probe-URL bzw. `OGC_DEFAULT_PROBE_URL`). Der Wert ist eine Schätzung des
   Downloads und kann von dedizierten Speedtests (mehr Streams, näherer Server)
   abweichen.
2. **Ookla Speedtest CLI** (`speedtest`, letzter Ausweg): greift, wenn die
   HTTP-Ziele z. B. mit **HTTP 403** (Bot-Sperre eines CDN) blocken. Misst den
   Upload gegen einen nahen Speedtest-Server. Der Installer bietet sie
   standardmäßig an (Frage „Ookla-Speedtest-CLI …?" bzw. `OGC_WITH_SPEEDTEST=0`
   zum Überspringen); manuell:
   <https://www.speedtest.net/apps/cli>.
   Hinweis: Speedtest-Server nutzen oft Port 8080 — auf stark eingeschränkten
   Uplinks (nur 80/443 erlaubt) kann das mit `Cannot open socket` scheitern; dann
   trägt die HTTP-Probe die Messung.
- Schlägt jeder Weg fehl, zeigt die Oberfläche die gesammelten Gründe an
  (z. B. `403 Forbidden`, `NoServers`, `Cannot open socket`, Timeout).

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
(System-Seite zeigt aktuelle vs. neueste Version).

**Release schneiden — ohne Terminal (empfohlen):** auf GitHub unter
**Actions → „Release" → „Run workflow"** die Version eintippen (z. B. `0.2.0`)
und starten. Der Workflow `release.yml` setzt den Tag, baut die UI und
veröffentlicht das Release automatisch. Alternativ per Terminal über einen
Versions-Tag: `git tag v0.2.0 && git push origin v0.2.0`.

- **nativ, ein Befehl** (empfohlen): aktualisiert auf das neueste Release,
  baut neu, startet den Dienst neu — Daten, `.env` und Port bleiben erhalten:
  ```bash
  sudo /opt/offgridcloud/src/deploy/update.sh          # oder --check nur prüfen
  ```
  Ist das **On-Box-Menü (Kiosk)** installiert, wird auch die Konsole aus der
  neuen Quelle **mit aktualisiert** (Dashboard, SSH-Start …) — vorher blieb sie
  bei einem Selbst-Update still zurück. PIN und Boot-Zustand bleiben erhalten.
- **One-Click im Web-UI:** standardmäßig aktiv. Der Installer richtet die
  nötige NOPASSWD-`sudoers`-Regel automatisch ein, sodass unter **System** ein
  „Jetzt aktualisieren"-Knopf erscheint, sobald ein neueres Release vorliegt.
  Der Knopf ruft dieselbe `update.sh` auf, aktualisiert also auch das Kiosk-Menü.
  Abschalten (z. B. bei Docker) mit `OGC_SELF_UPDATE=false` in der `.env` — dann
  zeigt die UI stattdessen den obigen Befehl an.
- **Installer erneut ausführen** (One-Liner/`install.sh`): ebenfalls ein
  vollwertiges Update. Der Installer erkennt die bestehende Installation, wählt
  jede aktive Funktion als Vorgabe vor und aktualisiert sie beim Durchbestätigen
  (siehe [§1](#1-installation)). Nützlich, wenn eine **neue** Funktion (z. B. das
  Kiosk-Menü) nachträglich dazukommen soll — die entsprechende Frage dann auf ja.
- **Docker:** Image neu bauen/ziehen, Container ersetzen (Volume `/data` bleibt erhalten).

Unterbrochene Transfers werden beim Start automatisch wieder eingereiht
(Resume-Recovery); laufende Uploads gehen nicht verloren.

### System steuern (Neustart & Herunterfahren)

Unter **System → „System steuern"** lassen sich drei Aktionen direkt aus dem
Web-UI auslösen:

- **OffgridCloud neustarten** — startet nur den Dienst neu
  (`systemctl restart offgridcloud`); das Betriebssystem läuft weiter.
- **System neustarten** — startet die ganze Box neu (`systemctl reboot`).
- **System herunterfahren** — fährt die Box komplett herunter
  (`systemctl poweroff`); sie muss danach vor Ort wieder eingeschaltet werden.

Diese Aktionen brauchen Root-Rechte und sind **standardmäßig aktiv**: Der
Installer richtet die passenden NOPASSWD-`sudoers`-Regeln automatisch ein, sodass
der Dienstnutzer `systemctl restart` / `reboot` / `poweroff` ohne Passwort
ausführen darf. Eine einzelne Aktion lässt sich abschalten, indem der zugehörige
Befehl in der `.env` geleert wird (`OGC_RESTART_SERVICE_COMMAND=`,
`OGC_REBOOT_COMMAND=`, `OGC_SHUTDOWN_COMMAND=`); der Knopf ist dann deaktiviert.
Neustart und Herunterfahren wirken sofort; unterbrochene Transfers werden nach
einem Neustart automatisch fortgesetzt.

## 9a. Netzwerk-Redundanz (Rückfall-WLAN)

Damit das Feld-Team auch bei Router-Ausfall weiter hochladen kann, hostet die
Box optional ihr **eigenes WLAN** als Rückfallebene und verbindet sich wieder
als Client, sobald ein hinterlegtes Netz erreichbar ist.

```bash
# bei der Installation: die Frage „Netzwerk-Redundanz …?" mit ja beantworten
sudo ./deploy/install.sh
# oder nachträglich:
sudo /opt/offgridcloud/deploy/netfallback/install.sh
```

Danach im Web-UI unter **Netzwerk**: Fallback-AP (Name/Passwort) setzen, die
bevorzugten WLAN-Uplinks hinterlegen, **„Anwenden“**. Der Watchdog schaltet
automatisch um; beobachten mit `journalctl -u offgridcloud-netwatch -f`.
Vollständige Beschreibung (Architektur, Sicherheit, Grenzen):
**[docs/NETZWERK-REDUNDANZ.md](NETZWERK-REDUNDANZ.md)**.

## 9b. Multi-Server-Pool (mehrere Boxen)

Sind mehrere OffgridCloud-Boxen im Einsatz (z. B. je Fahrzeug eine), lässt sich
eine als **Hub** einrichten, die alle anderen abfragt und eine gemeinsame
Flottenübersicht zeigt — Knoten online, Medien, aktive Transfers, Durchsatz,
Speicher. Es ist reine **read-only-Aggregation** (keine verteilte Koordination),
also sparsam genug für den Pi.

1. Auf jeder **Peer-Box** unter **Pool** ein Pool-Token erzeugen.
2. Auf der **Hub-Box** unter **Pool** jeden Peer mit *Name*, *URL*
   (`https://box2.local:8000`) und dessen *Token* eintragen.

Vollständige Beschreibung: **[docs/MULTI-SERVER-POOL.md](MULTI-SERVER-POOL.md)**.

## 9c. Tags & Suche

Jedes Medium kann **frei mit Tags versehen** werden (z. B. `interview`, `drohne`,
`eilig`) — direkt in der Ordner- oder Suchansicht. Unter **Suche** findet man
Medien **ordnerübergreifend** nach Dateiname, Tag, Status und Ordner. Die Suche
ist zugriffsgeschützt: Benutzer sehen nur Ordner, für die sie freigegeben sind;
Admins sehen alles.

## 10. Troubleshooting

| Symptom | Ursache / Lösung |
|---------|------------------|
| Provider-Test schlägt fehl: „rclone nicht installiert" | rclone fehlt im PATH; Image/Install-Skript bringt es mit. |
| Uploads bleiben „wartend" | Bandbreiten-Gate aktiv (Mindest-Bandbreite) oder kein Provider verknüpft. |
| Login schlägt nach Key-Wechsel fehl / Provider-Test rot | `OGC_SECRET_KEY` wurde geändert → Provider-Credentials neu eingeben. |
| Wenig Speicher | Externe SSD für `OGC_BUFFER_DIR`; „Lokale Kopie nach Upload löschen" aktivieren. |
