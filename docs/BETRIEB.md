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

Entfernen: `sudo offgridcloud-uninstall` — der Installer legt den Uninstaller
gleich mit auf der Box ab. Er fragt interaktiv, was mit weg soll, behält
Daten/`.env` per Vorgabe und räumt alle Zusatzfunktionen ab. Details, Optionen
und der Weg ohne Box-Installation stehen in [§10 Deinstallation](#10-deinstallation).

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

### Variante C — Docker (fertiges Image, plattformübergreifend)

Jedes Release wird als Multi-Arch-Image (`linux/amd64`, `linux/arm64`) in die
GitHub Container Registry gepusht (`.github/workflows/docker.yml`):

| Tag | Inhalt |
|-----|--------|
| `latest` | neuestes Release |
| `0.5`, `0.5.0` | fest auf eine Minor- bzw. exakte Version |
| `main` | aktueller Stand von `main` (Update-Kanal „main“) |
| `sha-<commit>` | ein bestimmter `main`-Commit |

Auf dem Raspberry Pi braucht es ein **64-bit-OS** (arm64); 32-bit-Raspberry-Pi-OS
wird nicht unterstützt.

**Mit Docker Compose (empfohlen):**

```bash
mkdir offgridcloud && cd offgridcloud
curl -fsSLO https://raw.githubusercontent.com/W0rkingChr1s/OffgridCloud/main/docker-compose.yml
curl -fsSL https://raw.githubusercontent.com/W0rkingChr1s/OffgridCloud/main/.env.example -o .env
nano .env               # OGC_SECRET_KEY + OGC_INITIAL_ADMIN_PASSWORD setzen
docker compose up -d    # http://<host>:8000
```

Daten (SQLite, Status, Medien-Puffer) liegen in `./data` neben der
`docker-compose.yml` — auf dem Pi den Volume-Pfad auf die USB-SSD umbiegen
(`/mnt/ssd/offgrid:/data`). Port und Image-Tag lassen sich per `OGC_PORT` /
`OGC_IMAGE_TAG` in der `.env` setzen (z. B. `OGC_IMAGE_TAG=main`). Im
geklonten Repo baut `docker compose up -d --build` das Image stattdessen lokal.

**Ohne Compose:**

```bash
docker run -d --name offgridcloud \
  -p 8000:8000 \
  -v /mnt/ssd/offgrid:/data \
  --env-file .env -e OGC_DATA_DIR=/data -e OGC_BUFFER_DIR=/data/buffer \
  --restart unless-stopped \
  ghcr.io/w0rkingchr1s/offgridcloud:latest
```

**Unterschiede zur nativen Installation.** Im Container gibt es kein sudo,
systemd oder Zugriff auf Host-Laufwerke. Das Image schaltet deshalb ab:
One-Click-Update (Update = neues Image ziehen, die System-Seite zeigt den
passenden Befehl), Dienst-Neustart/Reboot/Shutdown im Portal und per Telegram
(Docker startet den Container per `restart: unless-stopped` selbst neu) sowie
Ein-/Aushängen und Formatieren unter „Speicher“. Rückfall-WLAN, HTTPS per
Caddy/mDNS und das Kiosk-Menü sind Funktionen der nativen Installation. Der
**VPN-Client** funktioniert, wenn der Container `NET_ADMIN` und `/dev/net/tun`
bekommt (in der `docker-compose.yml` auskommentiert vorbereitet, siehe
[VPN.md](VPN.md)). Ein Healthcheck auf `/api/health` ist im Image eingebaut
(`docker ps` zeigt `healthy`).

> **Für Maintainer:** GHCR legt das Paket beim ersten Push **privat** an. Einmal
> unter *GitHub → Packages → offgridcloud → Package settings* auf **Public**
> stellen, sonst scheitert `docker pull` ohne Login.

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
- den festen **Alias** `https://offgridcloud.local` (gleiches Zertifikat, per
  mDNS vom Dienst `offgridcloud-mdns-alias` veröffentlicht) — damit man sich
  den Zufallsnamen nicht merken muss. Laufen mehrere Boxen im selben Netz,
  bekommt nur die erste den Alias; die anderen sind weiter unter ihrem
  eindeutigen Namen erreichbar;
- **Zugriff per IP**: `http://<IP der Box>` und `https://<IP der Box>`. Für
  HTTPS stellt Caddy beim ersten Aufruf ein Zertifikat für genau die
  aufgerufene IP aus (`tls internal` mit `on_demand`, auch nach einem
  DHCP-Wechsel). Es stammt von derselben lokalen CA wie das `.local`-Zertifikat,
  ohne installiertes Root-Zertifikat (siehe unten) gibt es also auch hier eine
  Warnung. Vor dem Ausstellen fragt Caddy das Backend
  (`/api/system/https/ask`), das nur IPs der Box selbst freigibt, damit
  beliebige Namen keine Zertifikate erzeugen. `http://<IP>` leitet bewusst
  nicht auf HTTPS um: ein Gerät ohne Root-Zertifikat käme sonst gar nicht mehr
  auf die Box. Die `.local`-Namen leiten weiterhin auf HTTPS um;
- optional zusätzlich einen **öffentlichen Zugang** über eine echte Domain mit
  automatischem Let's-Encrypt-Zertifikat, sobald eine Domain hinterlegt ist.

**Zertifikat auf Geräten vertrauen (empfohlen):** Das Zertifikat für
`https://<hostname>.local` (und `https://<IP>`) stellt Caddy selbst aus (`tls internal`) und erneuert
es etwa alle 12 Stunden. Eine im Browser bestätigte Warnung gilt nur für das
alte Zertifikat. Danach schlagen Anmeldung und Datenabrufe still fehl („Box
nicht erreichbar“), Safari zeigt oft keine neue Warnung, weil der Service Worker
die zwischengespeicherte Seite ausliefert. Abhilfe ist, das **Root-Zertifikat**
der Box einmal pro Gerät zu installieren (10 Jahre gültig):

- Im Portal unter **System → Erweitert → Zertifikat auf Geräten vertrauen**:
  „Profil für iPhone/iPad/Mac“ (`.mobileconfig`) oder „Zertifikat (.crt)“.
  Ohne Anmeldung: `http://<hostname>.local/api/system/https/ca.mobileconfig`
  bzw. `…/ca.crt` (auch per `http://<IP>`). Diese beiden Pfade bleiben bewusst
  per HTTP erreichbar, alles andere leitet auf HTTPS um.
- **iOS/iPadOS:** Profil laden → Einstellungen → „Profil geladen“ →
  Installieren, danach Einstellungen → Allgemein → Info →
  Zertifikatsvertrauenseinstellungen → „Volles Vertrauen“ einschalten.
- **Android/Windows/Linux:** die `.crt` als vertrauenswürdige
  Stammzertifizierungsstelle importieren.
- Der Download läuft über unverschlüsseltes HTTP. Vor dem Vertrauen den
  SHA-256-Fingerabdruck aus dem Portal mit dem auf dem Gerät vergleichen.
- `deploy/https/apply.sh` kopiert die Root-CA nach `data/caddy-root.crt`. Bei
  bestehenden Boxen passiert das beim nächsten Update.

**Schnelle Hilfe ohne Zertifikat:** Erscheint oben der gelbe Hinweis „Box nicht
erreichbar“, setzt „Neu verbinden“ Service Worker und Cache zurück und lädt neu.
Dann zeigt der Browser die Zertifikatswarnung wieder und man kann sie erneut
bestätigen, bis zur nächsten Erneuerung.

**Domain später ändern:** im Portal unter **System → Erweitert → Öffentliche HTTPS-Domain** die Domain
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

Beides entfernt die verwaltete Caddy-Konfiguration und den Alias-Dienst, deaktiviert caddy, löscht
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
  wurde. Wer sowohl lokal (`https://<hostname>.local`), über den Alias
  `https://offgridcloud.local` als auch über eine Domain zugreift, legt pro
  Adresse einen eigenen Passkey an.
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
  Bei gleicher Priorität starten Dateien bis 50 MB vor größeren, ein Lagefoto
  wartet also nicht hinter einem Mehr-GB-Video (ein laufender Upload wird dafür
  nicht abgebrochen).

**Nextcloud-Freigabelinks ohne Chunked-Upload:** Nextcloud vor Version 32 nimmt
über Freigabelinks nur Uploads am Stück an, und ein Proxy davor (bei Hetzner
Storage Share: Cloudflare) lehnt alles über rund 100 MB ab. Dateien über
**„Max. Größe am Stück“** (Provider-Einstellung, Standard 90 MB) teilt
OffgridCloud deshalb vor dem Upload und legt die Teile in einem Unterordner mit
dem Dateinamen ab:
- **Videos** werden mit ffmpeg ohne Neukodierung in abspielbare Clips geschnitten
  (`DJI_0005_W/DJI_0005_W_teil01.MP4`); Flugdaten in einer Untertitelspur bleiben
  erhalten und liegen zusätzlich als `.srt` daneben. Reine Datenspuren (DJI-`djmd`)
  fehlen in den Clips.
- **Andere Dateien** werden in `name.ext.001`, `.002` … zerlegt, dazu eine
  `ZUSAMMENFUEGEN.txt` mit Anleitung (7-Zip, `copy /b`, `cat`).
- Abgebrochene Uploads setzen beim ersten fehlenden Teil fort; vorab wird der freie
  Pufferplatz geprüft (kurzzeitig noch einmal die Dateigröße). Löschen in der Cloud
  entfernt alle Teile. ffmpeg ist im Pi- und im Docker-Image enthalten.
- Uploads, die an der Größe scheitern (413, 507, ffmpeg fehlt), werden nicht
  automatisch wiederholt; „Erneut“ startet sie von Hand.
- Einschränkungen eines funktionierenden Ziels stehen als Hinweis in der
  Provider-Liste; sie werden beim Testen gesetzt und alle 6 Stunden nachgeprüft.

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

Unter **System → Uploads**:
- **Disk-Auslastung** des Puffers wird angezeigt; bei wenig freiem Platz erscheint
  eine Warnung (< 10 % oder < 500 MB frei).
- **„Lokale Kopie nach Upload löschen"**: ist diese Option aktiv, wird die
  Pufferdatei entfernt, sobald **alle** Zielprovider eines Mediums erfolgreich
  bestätigt sind (rclone verifiziert Größe/Hash). Spart Platz auf dem Mini-Server.

### 5.1 Angeschlossene Speichermedien (Admin → Speicher)

Die Seite **Speicher** listet alle angeschlossenen Laufwerke (USB-Sticks, SD-Karten,
externe Platten) mit Belegung. Die Liste aktualisiert sich alle paar Sekunden — Medium
einstecken genügt. Jede Karte zeigt nur die Hauptaktionen („Fotos & Dateien“,
„Auf Cache sichern“, „Auswerfen“); Seltenes und Zerstörerisches (Aushängen, Als Cache
nutzen, Formatieren) liegt im **⋯-Menü**. Gerätepfade (`/dev/…`) stehen im Tooltip des
Namens. Das System-/Puffer-Laufwerk erscheint nicht als eigene Karte, sondern
eingeklappt unter **„Puffer auf der Box“**.

- **Einhängen / Aushängen / Auswerfen** — Medien werden unter
  `/media/offgridcloud/<Bezeichnung>` eingehängt (`nosuid,nodev,noexec`; FAT/exFAT/NTFS
  gehören dem Dienstkonto). „Auswerfen“ hängt alle Partitionen aus und schaltet
  USB-Geräte ab — danach kann das Medium gefahrlos gezogen werden.
- **Formatieren** — eine Partition oder das **komplette Laufwerk** (neue
  Partitionstabelle, eine Partition) als **exFAT** (empfohlen, Kameras/Windows/macOS),
  **FAT32** (max. 4 GB je Datei) oder **ext4**. Zur Sicherheit muss der Gerätename
  (z. B. `sdb`) eingetippt werden.
- **Fotos & Dateien — Galerie für Kamerakarten** — öffnet ein eingehängtes Medium als
  Bildergalerie: alle Fotos, RAWs, Videos und Audiodateien über sämtliche
  `DCIM/100XXX`-Ordner hinweg, nach Aufnahmetag gruppiert, mit Vorschaubildern. Antippen
  wählt aus, **Shift-Klick** wählt einen Bereich, „Tag auswählen“ / „Alle neuen auswählen“
  nehmen gleich ganze Blöcke; die Lupe öffnet eine Großansicht (← → blättern,
  Leertaste wählt). Filter nach Fotos/RAW/Videos. Die Ansicht „Ordner“ zeigt den
  normalen Dateibaum für alles andere (Dokumente-Sticks …).
  Vorschaubilder entstehen auf der Box und werden zwischengespeichert: JPEGs im
  schnellen Draft-Modus, RAWs über das eingebettete Vorschau-JPEG, Videos per
  ffmpeg (falls installiert). Höchstens zwei werden gleichzeitig berechnet, damit
  Uploads auf dem Pi 3 nicht ausgebremst werden.
- **Für den Upload übernehmen** — Ziel-Ordner wählen, optional Tags, „N hochladen“.
  Die Dateien werden in den Puffer **kopiert** (gestreamt, SHA-256, fsync) und wie ein
  normaler Upload an alle verknüpften Cloud-Ziele eingereiht. Gleichnamige, aber
  verschiedene Dateien (z. B. `IMG_0001.JPG` aus `100CANON` und `101CANON`) bekommen
  ein Suffix ` (2)`. Vor dem Start wird geprüft, ob der Puffer genug Platz hat.
- **Duplikatprüfung** — jede Kachel zeigt, ob die Datei schon auf der Box ist
  („✓ übernommen“) oder bereits in der Cloud („☁ gesichert“), und in welchem Ordner.
  Geprüft wird **boxweit**, nicht nur im Ziel-Ordner. Erkennung: Name + Größe +
  Aufnahmezeit (mtime) aus einem früheren Import gelten als identisch und werden ohne
  Lesen übersprungen; alles andere wird kopiert und per **SHA-256** verglichen — der
  Hash entscheidet. Name + Größe allein reichen bewusst nicht: manche Kameras schreiben
  RAWs fester Größe, das würde neue Fotos verschlucken. Bei per Browser hochgeladenen
  Dateien ist die Aufnahmezeit unbekannt, sie erscheinen als „≈ evtl. vorhanden“ und
  der Hash entscheidet beim Import.

#### Cache — Karte schnell wieder freibekommen

Ein angeschlossenes Laufwerk (z. B. eine USB-SSD) lässt sich mit **„Als Cache nutzen“**
markieren (per Dateisystem-UUID — die Markierung überlebt Umstecken). Danach hat jede
Karte den Knopf **„Auf Cache sichern“**: die Karte wird samt Ordnerstruktur und
Aufnahmezeit nach `OffgridCloud-Cache/<Kartenname>_<Datum>/` auf den Cache kopiert,
optional nur Neues und optional mit **automatischem Auswerfen**, wenn alles fehlerfrei
kopiert wurde. Die Karte kann sofort zurück in die Kamera.
Im Bereich **„Cache — Kartensicherungen“** lassen sich die Sicherungen öffnen (gleiche
Galerie, gleiche Duplikat-Badges) und in Ruhe auswählen und hochladen; erledigte
Sicherungen werden dort gelöscht. Ein Cache-Laufwerk lässt sich nicht formatieren,
solange es markiert ist. Wiederholtes Sichern derselben Karte am selben Tag ergänzt die
Sicherung und kopiert nichts doppelt.

#### Puffer-Laufwerk — mehr Platz für den Upload-Puffer

Ein angeschlossenes Laufwerk lässt sich im ⋯-Menü mit **„Als Puffer nutzen“** als
zusätzlicher Puffer markieren (per Dateisystem-UUID, wie beim Cache). Solange es
eingesteckt ist und Platz hat, landen **neue Uploads, Importe und Beschreibungen zuerst
dort** (unter `OffgridCloud-Puffer/`), sonst wie bisher im Puffer auf der Box. Das
schont nebenbei die System-SD. Die Anzeige „Puffer“ zählt dann Box und Laufwerke
zusammen, auch für die Speicher-knapp-Meldung.

- **Abziehen:** Solange Dateien auf dem Laufwerk noch nicht überall hochgeladen sind,
  lässt es sich nicht aushängen, auswerfen, formatieren oder entmarkieren. Wird es
  trotzdem gezogen, **warten** die betroffenen Transfers („Puffer-Laufwerk nicht
  angeschlossen“), statt fehlzuschlagen. Auch die Integritätsprüfung beim Start stellt
  diese Dateien nicht unter Quarantäne.
- **Wieder einstecken:** Das Laufwerk wird automatisch eingehängt (alle ~10 s geprüft),
  danach laufen die Uploads weiter. Landet es unter einem anderen Einhängepunkt, werden
  die gespeicherten Pfade automatisch angepasst. Ein von Hand ausgehängtes
  Puffer-Laufwerk bleibt ausgehängt, bis es abgezogen oder wieder eingehängt wird.
- **Verlorenes Laufwerk:** Ist es nicht angeschlossen, kann die Markierung trotzdem
  entfernt werden. Die noch nicht hochgeladenen Dateien darauf gelten dann als verloren.
- **FAT32** wird als Puffer abgelehnt (max. 4 GB je Datei): vorher als exFAT oder ext4
  formatieren.

#### Auto-Import — einstecken, warten bis die LED leuchtet, ziehen

Im Kasten **„Auto-Import“** auf der Speicher-Seite wird ein festes Ziel gewählt:
ein **Ordner** (die Dateien gehen danach an dessen Cloud-Ziele) oder ein
**Cache-Laufwerk** (Kartensicherung). Ist der Auto-Import aktiv, wird **jedes neu
angesteckte Wechselmedium** innerhalb weniger Sekunden eingehängt und komplett auf das
Ziel kopiert, ohne dass jemand das Portal öffnet. Duplikate werden wie beim manuellen
Import übersprungen; eine Karte erneut einzustecken kopiert also nur Neues. Nach einem
fehlerfreien Durchlauf wird das Medium (optional) ausgeworfen.

Übergangen werden System- und Puffer-Laufwerke, als Cache/Puffer markierte Laufwerke,
fest verbaute (nicht-Wechsel-)Laufwerke und Laufwerke mit einem `OffgridCloud-Cache/`-
oder `OffgridCloud-Puffer/`-Ordner. **Achtung:** Ein *neues* Laufwerk, das gleich als
Cache oder Puffer markiert werden soll, vorher **nicht** bei aktivem Auto-Import
anstecken, sonst wird sein Inhalt erst einmal übernommen.

**Status ohne Netz — die Status-LED:**

| LED                         | Bedeutung                                             |
|-----------------------------|-------------------------------------------------------|
| normal (SD-Aktivität)       | nichts zu tun                                         |
| langsames Blinken (0,5 s)   | kopiert — Medium **stecken lassen**                   |
| dauerhaft an                | fertig (und ausgeworfen) — Medium **kann gezogen werden** |
| schnelles Flackern (0,1 s)  | Fehler (zu wenig Platz, kein Cache, Lesefehler …) — Details im Portal |

Stecken mehrere Medien, gewinnt „kopiert“ vor „Fehler“ vor „fertig“: solange irgendwo
noch kopiert wird, darf nichts gezogen werden. „Fertig“ bleibt stehen, bis das Medium
gezogen wird. USB-Kartenleser schalten sich beim Auswerfen ab und sind danach für die
Box unsichtbar; dann hält die LED „fertig“ 30 Minuten oder bis zum nächsten Medium.

Standard ist die grüne `ACT`-LED des Raspberry Pi (ältere Kernel: `led0`). Für eine
gut sichtbare LED vorn am Gehäuse eine LED (mit Vorwiderstand, ~330 Ω) an einen
GPIO-Pin hängen und in `/boot/firmware/config.txt` eintragen:

```
dtoverlay=gpio-led,gpio=17,label=ogc-status
```

Nach dem Neustart erscheint `ogc-status` in der LED-Auswahl. Mit **„Testen“** lässt
sich jedes Muster ein paar Sekunden lang vorführen. Die LED wird über den Root-Helfer
geschaltet (`storage.sh led …`), das Blinken übernimmt der Kernel-Trigger `timer`.

**Status mit Netz:** Zusätzlich geht pro Medium eine Nachricht über die eingerichteten
Kanäle (Telegram, E-Mail, Webhook) — Ereignis „Auto-Import fertig/fehlgeschlagen“
unter *System → Benachrichtigungen*, standardmäßig an. Ist gerade kein Netz da, wartet
die Nachricht in der Warteschlange und kommt nach, sobald die Box wieder online ist
(siehe unten).

#### Warteschlange für Offline-Benachrichtigungen

Kann ein Kanal (Telegram, E-Mail, Webhook) eine Meldung nicht zustellen, weil die Box
offline oder der Dienst nicht erreichbar ist, landet sie in einer Warteschlange in der
Datenbank — übersteht also auch einen Neustart oder eine leere Powerbank. Die Box
versucht es mit wachsendem Abstand erneut (30 s bis 15 min) und sofort, wenn die
Verbindung zurückkommt.

- **Telegram/E-Mail:** mehrere wartende Meldungen kommen als **eine Sammelnachricht**
  („📬 5 Meldungen aus der Offline-Zeit“) mit der jeweils ursprünglichen Uhrzeit; eine
  einzelne als „verzögert“ markiert. Lange Listen werden auf mehrere Telegram-Nachrichten
  verteilt.
- **Webhook:** jede Meldung einzeln, im JSON zusätzlich `"delayed": true` und
  `"queued_at"`.
- **Reihenfolge bleibt:** Solange ein Kanal einen Rückstand hat, reihen sich neue
  Meldungen hinten an, statt vorzudrängeln (und die Box wartet offline nicht bei jeder
  Meldung auf einen Netzwerk-Timeout).
- **Grenzen:** max. 500 Meldungen je Kanal und 7 Tage, ältere fallen raus. Dauerhafte
  Fehler (z. B. ungültiger Bot-Token, SMTP-Login abgelehnt) werden nicht gespeichert —
  das löst kein erneuter Versuch. Wird ein Kanal abgeschaltet, verfällt sein Rückstand.

Unter *System → Benachrichtigungen* zeigt ein Hinweis, wie viele Meldungen warten, mit
**„Jetzt senden“** und **„Verwerfen“**.

**Schutz:** Laufwerke mit einem Dateisystem außerhalb von `/media`, `/mnt` oder
`/run/media` (also die System-SD/-SSD) und das Laufwerk mit dem OffgridCloud-Puffer
werden als **System** bzw. **Puffer** markiert und lassen sich weder aushängen noch
formatieren noch durchsuchen. Diese Prüfung macht sowohl die API als auch — unabhängig
davon — der Root-Helfer `deploy/storage/storage.sh`.

**Einrichtung:** `install.sh` (auch beim Update) installiert `exfatprogs`,
`dosfstools`, `e2fsprogs` und `fdisk`, legt den Helfer root-eigen unter
`/opt/offgridcloud/deploy/storage/storage.sh` ab und erlaubt ihn per
`/etc/sudoers.d/offgridcloud-storage`. Ohne Helfer (z. B. Docker,
`OGC_STORAGE_COMMAND=` leer) ist die Seite schreibgeschützt: Anzeigen, Durchsuchen und
Importieren von anderweitig eingehängten Medien funktioniert weiterhin.

Importe und Cache-Sicherungen laufen im Hintergrund mit Fortschrittsanzeige und lassen
sich abbrechen.
Der Job-Status lebt nur im Speicher — nach einem Neustart den Import einfach erneut
starten, schon Übernommenes wird übersprungen.

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
- `GET /api/health?format=prtg` — dieselben Daten als **PRTG**-Sensor-JSON (siehe unten).
- `GET /api/monitor` — ausführlicher **Zustand** (Token oder Admin, siehe
  [Health Monitor](#health-monitor)), auch als Prometheus (`/api/monitor/metrics`)
  und erweiterter PRTG-Sensor (`?format=prtg`).
- Admin-Seite **Zustand** — dieselben Werte grafisch, mit 24-h-Verlauf.
- Admin **System**-Seite — Disk-Auslastung, rclone-Verfügbarkeit, Einstellungen.
- Logs:
  - nativ: `journalctl -u offgridcloud -f`
  - Docker: `docker logs -f offgridcloud`

### PRTG einbinden

Sensor **HTTP Data Advanced** anlegen und als URL eintragen:

```
http://<box>:8000/api/health?format=prtg
```

Die Antwort liefert das von PRTG erwartete Format inklusive Schwellwerten, der
Sensor ist also ohne Nachkonfiguration einsatzbereit:

```json
{
  "prtg": {
    "result": [
      { "channel": "Status", "value": "1", "limitmode": 1, "limitminerror": "1" },
      { "channel": "rclone verfügbar", "value": "1", "limitmode": 1, "limitminerror": "1" },
      { "channel": "Speicher belegt", "value": "42.0", "unit": "Percent", "float": 1,
        "limitmode": 1, "limitmaxwarning": "85", "limitmaxerror": "95" },
      { "channel": "Speicher frei", "value": "51539607552", "unit": "BytesDisk" }
    ],
    "text": "OffgridCloud 0.1.0 | rclone: rclone v1.66.0 | Speicher: 42.0% belegt"
  }
}
```

Kanäle:

| Kanal | Bedeutung | Fehler ab |
| --- | --- | --- |
| Status | Dienst antwortet mit `ok` | 0 (Dienst meldet Störung) |
| rclone verfügbar | rclone-Binary gefunden | 0 (Uploads schlagen fehl) |
| Speicher belegt | Auslastung des Puffer-Verzeichnisses | Warnung ab 85 %, Fehler ab 95 % |
| Speicher frei | freier Platz im Puffer-Verzeichnis | — |

Antwortet der Server gar nicht, geht der Sensor über den HTTP-Fehler auf *Down*.
Ist das Puffer-Verzeichnis nicht lesbar (z. B. ausgehängte USB-Platte), entfallen
die beiden Speicher-Kanäle — Status und rclone bleiben erhalten.

> **Hinweis:** `/api/health` ist bewusst unauthentifiziert, damit Monitoring ohne
> Zugangsdaten funktioniert. Mit `format=prtg` sind darüber auch die
> Speicher-Kennzahlen (Prozent belegt, freie Bytes) im Netz sichtbar. Soll das
> nicht sein, den Endpunkt im Reverse-Proxy auf das Monitoring-Netz beschränken
> (siehe [§3](#3-https--reverse-proxy)).

### Health Monitor

Die Seite **Zustand** (Admin-Menü) und `GET /api/monitor` zeigen dieselben Werte:

| Bereich | Werte | Warnung / Fehler ab |
| --- | --- | --- |
| CPU | Auslastung, Load 1/5/15 min, Kerne | — |
| Arbeitsspeicher | belegt, verfügbar, Swap | 90 % / 97 % |
| Temperatur | CPU/SoC in °C | 70 °C / 80 °C |
| Stromversorgung (Pi) | Unterspannung, Drosselung — jetzt und seit dem Start | Unterspannung jetzt = Fehler, alles andere Warnung |
| Speicher | Puffer belegt/frei | 85 % / 95 % |
| Internet | online, Latenz, Ausfälle und Ausfallzeit der letzten 24 h, letzter Ausfall | offline = Fehler; Latenz 300 / 1000 ms; ab 3 Ausfällen in 24 h Warnung |
| WLAN | Signal (dBm), Linkqualität je Schnittstelle | −75 / −85 dBm |
| Weiteres | IPs, VPN, Bandbreite, Warteschlange, Cloud-Ziele, rclone | Fehlgeschlagene Übertragungen, Cloud-Ziel mit Fehler = Warnung; rclone fehlt = Fehler |

Aus den Prüfungen ergibt sich ein Gesamtzustand `ok`, `warning` oder `error`.
Der Verlauf (ein Punkt pro Minute, 24 h) liegt bewusst nur im Arbeitsspeicher, um
die SD-Karte zu schonen — nach einem Neustart beginnt er neu. Langzeitdaten
gehören in Prometheus oder PRTG.

**Zugriff:** Unter *Zustand → Zugriff für Monitoring-Systeme* einen
**Monitoring-Token** erzeugen (nur Lesen, wird einmal angezeigt, jederzeit
widerrufbar). Er wird als `Authorization: Bearer <token>` übergeben oder, wo
kein Header möglich ist, als `?token=<token>`. Eine Admin-Anmeldung funktioniert
ebenfalls (so liest die Companion-App die Werte, auch per Bluetooth).

| Endpunkt | Format |
| --- | --- |
| `GET /api/monitor` | JSON-Snapshot |
| `GET /api/monitor?format=prometheus` bzw. `GET /api/monitor/metrics` | Prometheus-Textformat |
| `GET /api/monitor?format=prtg` | PRTG *HTTP Data Advanced* mit allen Kanälen und Schwellwerten |
| `GET /api/monitor/history` | Verlauf der letzten 24 h, ein Punkt pro Minute. `?step=900` fasst zu Mittelwerten über 15 min zusammen (60–3600 s; `online` nimmt den schlechtesten Wert), `?hours=6` kürzt auf die letzten Stunden (1–24) |

Prometheus:

```yaml
- job_name: offgridcloud
  metrics_path: /api/monitor/metrics
  authorization:
    credentials: ogcm_…
  static_configs:
    - targets: ["<box>:8000"]
```

Metriken tragen das Präfix `offgridcloud_`, z. B. `offgridcloud_health_status`
(0/1/2), `offgridcloud_check_status{check="…"}`, `offgridcloud_cpu_temperature_celsius`,
`offgridcloud_throttled{flag="under_voltage",scope="now"}`,
`offgridcloud_internet_up`, `offgridcloud_internet_latency_seconds`,
`offgridcloud_disk_free_bytes`, `offgridcloud_wifi_signal_dbm{interface="wlan0"}`.

PRTG: Sensor **HTTP Data Advanced** auf
`http://<box>:8000/api/monitor?format=prtg&token=ogcm_…`. Zusätzlich zu den
Kanälen oben kommen Gesamtzustand, CPU-Last, Arbeitsspeicher, CPU-Temperatur,
Unterspannung, Internet, Latenz, Ausfälle (24 h), WLAN-Signal, Warteschlange und
Fehlgeschlagen — Schwellwerte sind wieder eingebaut.

## 9. Updates

Instanzen prüfen automatisch gegen die **GitHub-Releases** des Projekts
(System-Seite zeigt aktuelle vs. neueste Version).

**Update-Kanal:** Jede Installation folgt einem Kanal, den der Installer neben
der Version ablegt (`backend/app/CHANNEL`):

- **`release`** — prüft gegen die GitHub-Releases, `update.sh` holt das neueste
  Release-Tag. Standard, wenn die Quelle genau auf einem Tag steht.
- **`main`** — prüft Commit für Commit gegen den `main`-Branch (System-Seite
  zeigt „main @ <commit>" und „N neue Commits auf main"). Gilt für Installationen
  vom `main`-Branch, etwa per One-Liner zwischen zwei Releases — erkennbar an
  einer Version wie `0.4.10-44-g97b8eb0` (44 Commits nach `v0.4.10`).

`update.sh` (und damit der One-Click-Knopf) bleibt auf dem installierten Kanal,
eine `main`-Installation fällt also nicht auf ein älteres Release zurück.
Wechseln mit `sudo …/update.sh --channel release` bzw. `--channel main`.

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
  **Rechte:** Weil `update.sh` per sudo als root läuft und dabei Git, npm und
  `install.sh` aus `/opt/offgridcloud/src` ausführt, gehören Quell-Checkout,
  `backend/` (inkl. venv) und `deploy/` **root** und sind für das Dienstkonto
  nur lesbar. Das Dienstkonto besitzt ausschließlich `data/` und `.env`. So kann
  ein kompromittierter Dienst keinen Code ablegen, den sudo später als root
  ausführt. Ältere Installationen werden beim nächsten Update automatisch
  umgestellt.
- **Automatisch:** unter **System → Version → „Automatisch aktualisieren"**
  (standardmäßig aus). Die Box schaut alle 5 Minuten nach und startet dieselbe
  `update.sh` wie der One-Click-Knopf, aber nur wenn
  - die Uhrzeit der Box im **Zeitfenster** liegt (Standard 03–05 Uhr; über
    Mitternacht wie 22–04 geht, gleiche Von-/Bis-Zeit heißt rund um die Uhr),
  - **nichts läuft**: keine Übertragung in die Cloud, kein Upload auf die Box
    mit Daten in den letzten 10 Minuten, kein Karten-Import,
  - die Leitung **nicht getaktet** ist (Handy per USB), außer das ist erlaubt,
  - heute noch **kein Versuch** war (höchstens einer pro Tag).

  Das Ergebnis geht an alle eingerichteten Benachrichtigungen (Telegram,
  E-Mail, Webhook-Event `server.update`) und in die Hinweise im Portal. Schlägt
  eine Version fehl, überspringt die Box sie, bis eine neuere erscheint;
  Aus- und wieder Einschalten versucht sie erneut. Unter dem Schalter steht,
  worauf die Box gerade wartet. Nicht verfügbar in Docker oder mit
  `OGC_SELF_UPDATE=false`. Auf dem `main`-Kanal heißt das: jeder neue Commit
  wird installiert — für Feldboxen besser beim Kanal `release` bleiben.
- **Installer erneut ausführen** (One-Liner/`install.sh`): ebenfalls ein
  vollwertiges Update. Der Installer erkennt die bestehende Installation, wählt
  jede aktive Funktion als Vorgabe vor und aktualisiert sie beim Durchbestätigen
  (siehe [§1](#1-installation)). Nützlich, wenn eine **neue** Funktion (z. B. das
  Kiosk-Menü) nachträglich dazukommen soll — die entsprechende Frage dann auf ja.
- **Docker:** `docker compose pull && docker compose up -d` zieht das neue
  Image und ersetzt den Container; `/data` bleibt erhalten. Ohne Compose:
  `docker pull ghcr.io/w0rkingchr1s/offgridcloud:latest`, Container entfernen
  und mit demselben `docker run` neu starten.

Unterbrochene Transfers werden beim Start automatisch wieder eingereiht
(Resume-Recovery); laufende Uploads gehen nicht verloren.

### System steuern (Neustart & Herunterfahren)

Unter **System → „Wartung"** lassen sich drei Aktionen direkt aus dem
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

## 9d. Bluetooth (Companion-App)

Mit der [Companion-App](https://github.com/W0rkingChr1s/OffgridCloud-Companion)
lässt sich die Box per Bluetooth Low Energy verwalten — auch ohne gemeinsames
Netz (falsches WLAN korrigieren, Status, Neustart). Das erledigt der eigene
Dienst `offgridcloud-ble`; Protokoll und Sicherheitsmodell:
**[docs/BLE-PROTOKOLL.md](BLE-PROTOKOLL.md)**.

- **Installation:** im Installer die Option *Bluetooth für die Companion-App*
  (vorausgewählt, wenn die Box einen Bluetooth-Adapter hat oder ein Pi mit
  eingebautem Bluetooth ist — das gilt auch für Updates), nachträglich
  `sudo /opt/offgridcloud/src/deploy/ble/install.sh`. Raspberry Pi 3B/3B+/3A+,
  4, 5 und Zero W / Zero 2 W haben Bluetooth eingebaut; sonst ein USB-Dongle.
  Docker wird nicht unterstützt.
- **Gelber Hinweis im Portal:** Die Seite *Bluetooth* sagt, ob der Dienst
  fehlt, kein Adapter gefunden wird oder der Dienst beim Start scheitert, und
  zeigt Admins die letzte Fehlermeldung.
- **Koppeln:** nur über das Web-Portal, unter *Bluetooth → Gerät koppeln*. Die
  Seite zeigt einen QR-Code und den Code zum Abtippen; er gilt 5 Minuten und
  einmal. Sobald das Handy fertig ist, verschwindet der Code und das Gerät steht
  in der Liste. Jeder Benutzer koppelt seine eigenen Handys und sieht in der App
  nur, was er auch im Web-UI darf.
- **Widerrufen:** in derselben Liste; offene Verbindungen des Geräts trennt der
  Dienst binnen Sekunden. Admins sehen und widerrufen alle Geräte.
- **Abschalten:** *Bluetooth* im Web-Portal (Admin). Standard ist an — sonst
  fehlt die Rettungsleine genau dann, wenn das Netz weg ist.
- **Eine Verbindung gleichzeitig:** Während ein Handy verbunden ist, wirbt die
  Box nicht und weitere Handys finden sie nicht.
- **Audit:** Kopplungen, Widerrufe und jede schreibende Aktion per Bluetooth
  stehen im Audit-Log mit `[ble]`.

Erster Test auf echter Hardware: Checkliste
**[docs/BLE-HARDWARETEST.md](BLE-HARDWARETEST.md)**.

Scheitert die Einrichtung während eines Updates (z. B. `pip`), meldet das
Update „Mit Warnungen“ und versucht es beim nächsten Update erneut (Marker
`data/.ble-setup-pending`). Das Protokoll des letzten Updates liegt in
`data/update.log`, das vorherige in `data/update.log.1`.

Scheitert der laufende Dienst eine Minute lang (z. B. BlueZ lehnt die Werbung
ab) oder wird `bluetoothd` neu gestartet, beendet sich `offgridcloud-ble`
selbst und systemd startet ihn neu. Der letzte Fehler steht dann im gelben
Hinweis. Von Hand: `sudo systemctl restart offgridcloud-ble`.

Fehlersuche: `journalctl -u offgridcloud-ble -f` · `bluetoothctl show`
(Adapter an?) · `rfkill list` (Bluetooth nicht blockiert?). Der Box-Schlüssel
liegt unter `data/ble/box_static.key`; wer ihn löscht, macht alle Kopplungen
ungültig.

## 10. Deinstallation

### Nativ (Linux / Raspberry Pi OS)

Der Installer legt den Uninstaller mit auf der Box ab — es braucht also weder
das Repo noch Internet:

```bash
sudo offgridcloud-uninstall            # fragt interaktiv, was weg soll
sudo offgridcloud-uninstall --dry-run  # zeigt nur an, was passieren würde
```

Ohne die Box-Installation (oder wenn der Befehl fehlt) geht auch:

```bash
sudo /opt/offgridcloud/deploy/uninstall.sh          # vom Installer abgelegt
sudo /opt/offgridcloud/src/deploy/uninstall.sh      # aus dem Checkout
sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/W0rkingChr1s/OffgridCloud/main/deploy/uninstall.sh)"
```

Der Uninstaller zeigt erst, was er auf der Box gefunden hat, fragt dann per
Menü (whiptail, sonst Text-Prompts) nach den Zusatz-Optionen und listet vor dem
Start noch einmal auf, was entfernt und was behalten wird.

**Immer entfernt:** systemd-Dienst, VPN-Drop-in und ein laufender Tunnel, alle
`sudoers`-Regeln, die Kiosk-Konsole (inkl. wiederhergestelltem Boot-Ziel und
Display-Manager), HTTPS (verwaltete Caddy-Konfiguration weg, ursprünglicher
Hostname zurück), Netzwerk-Redundanz (Watchdog + Fallback-AP) sowie die
App-Dateien unter `/opt/offgridcloud`.

**Nur auf Nachfrage:**

| Option | Flag | Wirkung |
|--------|------|---------|
| Daten löschen | `--purge` | Datenbank, Medien-Puffer und `.env` (mit `OGC_SECRET_KEY`). Vorher wird auf Wunsch eine Sicherung aus DB + `.env` nach `/root` geschrieben. |
| WLAN-Profile | `--wifi` | Die von der Box angelegten `ogc-wifi-*`-Profile. Vorsicht: kann die Verbindung kappen, über die du gerade arbeitest. |
| Dienst-Benutzer | `--remove-user` | Benutzer `offgrid` inkl. Home (und damit der rclone-Konfiguration). |
| Pakete | `--packages` | caddy, rclone, ffmpeg, wireguard-tools, openvpn und `/usr/local/bin/speedtest`. NetworkManager, avahi, X/Chromium, Node und Python bleiben **immer** — sie können anderswo gebraucht werden. |
| Trockenlauf | `--dry-run` | Zeigt jeden Schritt an, verändert nichts. |

Ohne `--purge` bleiben `/opt/offgridcloud/data` und `/opt/offgridcloud/.env`
liegen — eine spätere Neuinstallation findet Datenbank und Schlüssel wieder vor.
Für Skripte gibt es zusätzlich `-y` (keine Rückfragen) und dieselben
`OGC_*`-Variablen wie beim Installer (`sudo offgridcloud-uninstall --help`).

Nur eine einzelne Funktion zurückbauen? HTTPS geht separat mit
`sudo ./deploy/https/uninstall.sh` (siehe [§3](#3-https--reverse-proxy)), alle
anderen Funktionen lassen sich im Installer-Menü abwählen.

### Windows (PowerShell)

```powershell
powershell -ExecutionPolicy Bypass -File deploy\uninstall.ps1
powershell -ExecutionPolicy Bypass -File deploy\uninstall.ps1 -DryRun   # nur anzeigen
powershell -ExecutionPolicy Bypass -File deploy\uninstall.ps1 -Purge    # inkl. Daten
```

Entfernt den Autostart-Task „OffgridCloud", beendet einen noch laufenden
Server, löscht venv, gebautes Frontend und `node_modules` sowie die
Benutzer-Variable `OGC_PORT`. `.env` und `data\` bleiben ohne `-Purge` erhalten
(mit `-Purge` wird vorher eine Sicherung in *Dokumente* abgelegt). Das
Repo-Verzeichnis selbst kann danach gelöscht werden. Das Abmelden des Tasks
braucht eine Administrator-PowerShell.

### Docker

```bash
docker compose down            # bzw. docker rm -f offgridcloud
docker rmi ghcr.io/w0rkingchr1s/offgridcloud
rm -rf ./data                  # bzw. das gemountete Verzeichnis — DESTRUKTIV
```

## 11. Troubleshooting

| Symptom | Ursache / Lösung |
|---------|------------------|
| Provider-Test schlägt fehl: „rclone nicht installiert" | rclone fehlt im PATH; Image/Install-Skript bringt es mit. |
| Uploads bleiben „wartend" | Bandbreiten-Gate aktiv (Mindest-Bandbreite) oder kein Provider verknüpft. |
| Login schlägt nach Key-Wechsel fehl / Provider-Test rot | `OGC_SECRET_KEY` wurde geändert → Provider-Credentials neu eingeben. |
| Weboberfläche wird nach langer Laufzeit langsam | Seit 0.11.0 hält jeder Tab nur eine Live-Verbindung (im Hintergrund geschlossen), der Server beendet sie nach 5 Minuten, der Browser verbindet sich selbst neu. Ältere Versionen: Update einspielen. |
| Große Datei über Freigabelink scheitert mit 413 | Nextcloud < 32 ohne Chunk-Endpunkt: „Max. Größe am Stück“ senken (Datei wird geteilt), Nextcloud 32 oder Provider „Nextcloud“ mit App-Passwort nutzen. |
| Wenig Speicher | Externe SSD für `OGC_BUFFER_DIR`; „Lokale Kopie nach Upload löschen" aktivieren. |
