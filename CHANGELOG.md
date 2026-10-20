# Changelog

Alle nennenswerten Änderungen an OffgridCloud. Die neueste Version steht oben.
Ältere Versionen (bis v0.4.10) sind in den
[GitHub-Releases](https://github.com/W0rkingChr1s/OffgridCloud/releases) dokumentiert.

## [Unreleased]

## [0.7.0] – 2026-10-20

Die Box kann Uploads jetzt über **alle Uplinks gleichzeitig** schicken (Kabel, WLAN, Handy) und **mehrere Dateien parallel** hochladen. Beides ist standardmäßig aus.

### 🔀 Neu: Leitungsbündelung mit net-merge und parallele Uploads

- **Alle Uplinks gleichzeitig:** Mit [net-merge](https://github.com/rohit267/net-merge) verteilt die Box ihre Cloud-Uploads auf Kabel, WLAN und ein angestecktes Handy zugleich. Das bringt vor allem bei großen Dateien zu S3, Azure und B2 etwas, deren Teile rclone parallel hochlädt.
- **Nur für rclone:** net-merge läuft als SOCKS5-Proxy auf `127.0.0.1:1080` (Dienst `offgridcloud-netmerge`). Routing, Rückfall-WLAN und VPN der Box bleiben unberührt. Antwortet der Proxy nicht, gehen Uploads direkt.
- **Vorsichtig:** Ziele im LAN oder hinter dem VPN, ein aktiver OpenVPN-Tunnel und ein getaktetes Handy bei Regel „Tagesvolumen“/„pausieren“ umgehen die Bündelung.
- Einrichten mit `sudo deploy/netmerge/install.sh` (oder „Leitungsbündelung“ im Installer), einschalten unter **Bandbreite → Leitungsbündelung**. Standard: aus.
- **Parallele Uploads:** Unter Bandbreite lassen sich 1–4 Uploads gleichzeitig einstellen (Standard 1). Mit Bündelung nimmt jeder Upload eine eigene Leitung, das bringt auch bei WebDAV und SFTP mehr Durchsatz. Das KB/s-Limit gilt für alle zusammen, laufende Uploads zählen beim Tagesvolumen schon mit.
- Die Konsole an der Box kann beides einstellen, Telegram `/netz` zeigt die gebündelten Leitungen.
- Anleitung und Grenzen: [docs/NETZWERK-REDUNDANZ.md](docs/NETZWERK-REDUNDANZ.md#leitungsbündelung-net-merge).

### 🐛 Behoben

- **Pool:** Eine Peer-URL ohne Schema (`192.168.178.128:8000`) führte zu „unknown url type“. Jetzt wird `http://` ergänzt, auch bei schon gespeicherten Peers. `https://` auf den Klartext-Port und selbstsignierte Zertifikate geben einen verständlichen Hinweis statt `WRONG_VERSION_NUMBER`. (#179)

## [0.6.0] – 2026-10-20

Ein **Handy per USB** wird zum Uplink, **USB-Sticks und Karten** dienen als zusätzlicher Puffer, und die **Konsole an der Box** kann jetzt alles, was die Speicher-Seite kann.

### 📱 Neu: USB-Tethering mit iPhone oder Android

- **Handy per Kabel als Uplink:** Ein per USB angestecktes iPhone (`ipheth`) oder Android-Handy (`rndis_host`/`cdc_ncm`) wird erkannt. Die Netzwerk-Seite zeigt den Modus „USB-Tethering“ mit dem Gerät, Telegram `/netz` und die Konsole an der Box ebenso. Fürs iPhone installiert `install.sh` jetzt `usbmuxd` mit, ohne das kommt der Link nicht hoch.
- **Handy nur als Reserve:** Ein neues NetworkManager-Drop-in (`offgridcloud-tether.conf`) gibt Handys die Routen-Metrik 700. Kabel und bekanntes WLAN haben damit Vorrang, das Handy trägt den Upload nur, wenn sonst nichts da ist.
- **Getaktete Verbindung:** Unter Bandbreite einstellbar, was Uploads über das Handy (oder einen als getaktet erkannten WLAN-Hotspot) tun: normal senden, bis zu einem **Tagesvolumen** senden (Dateien, die nicht mehr passen, warten) oder **pausieren**. Der Worker misst dabei nicht nach, um kein Volumen zu verbrennen. Standard bleibt „normal senden“.
- Anleitung und Einschränkungen: [docs/NETZWERK-REDUNDANZ.md](docs/NETZWERK-REDUNDANZ.md#usb-tethering-iphoneandroid).
### 💾 Neu: Wechselmedien als Puffer

- Auf der Seite **Speicher** gibt es im ⋯-Menü eines Laufwerks **„Als Puffer nutzen“**. Solange es eingesteckt ist und Platz hat, landen neue Uploads, Importe und Beschreibungen zuerst dort (`OffgridCloud-Puffer/`), sonst wie bisher im Puffer auf der Box. Die Puffer-Anzeige zählt Box und Laufwerke zusammen.
- Mit Dateien, die noch auf den Upload warten, lässt sich das Laufwerk nicht aushängen, auswerfen, formatieren oder entmarkieren. Wird es trotzdem gezogen, warten die Transfers, statt fehlzuschlagen, und die Integritätsprüfung beim Start lässt die Dateien in Ruhe.
- Wieder eingesteckt, wird es automatisch eingehängt. Ein geänderter Einhängepunkt wird in den gespeicherten Pfaden nachgezogen.
- Root-Helfer: neue Aktion `storage.sh prepare-buffer`. Ein Update (bzw. `install.sh`) spielt den Helfer neu ein. Ohne Helfer (Docker) wird der Ordner direkt angelegt.

### 🖥️ Konsole an der Box überarbeitet

- **Neuer Tab „Speicher“:** Laufwerke und Speicherkarten einhängen, aushängen, auswerfen und formatieren, als Cache-Laufwerk markieren, Karteninhalte in einen Ordner hochladen oder ohne Netz aufs Cache-Laufwerk sichern. Laufende Vorgänge erscheinen mit Live-Fortschritt, Kartensicherungen lassen sich ansehen und löschen. Die Konsole kann damit dasselbe wie die Speicher-Seite der Web-UI.
- **Updates:** Mit „Nach Updates suchen“ fragt die Konsole sofort neu bei GitHub nach, statt den Cache zu nutzen. Nach „Jetzt aktualisieren“ zeigt sie den **Fortschritt live** an: Status, Versionen, Dauer und das Ende des Protokolls. Den Neustart mitten im Update überbrückt sie und blendet danach das Ergebnis ein. Ist One-Click aus, steht der passende Befehl da (auch für Docker).
- **Behoben: Dienst-Neustart per SSH ging nicht.** Ohne root rief die Konsole `systemctl` direkt auf, und das schlug fehl. Jetzt versucht sie `sudo -n` und nimmt sonst die API (wie der Knopf in der Web-UI). Außerdem blockiert der Neustart die Konsole nicht mehr (`--no-block`), und eine Warte-Anzeige läuft, bis der Dienst wieder antwortet.
- Die Tab-Leiste passt jetzt auch auf 80 Spalten (dann mit Kurznamen). Mehrzeilige Hinweise in Menüs. Ein abgelaufenes Login führt beim nächsten Schritt zu einer neuen Anmeldung statt zu Fehlermeldungen.

## [0.5.3] – 2026-10-20

**Fertiges Raspberry-Pi-Image** mit Setup-Assistent, **Docker-Image** auf GHCR, **Hell-Modus**, und die Box ist unter `offgridcloud.local` erreichbar. Dazu viele aufgeräumte Seiten (Tailwind 4).

### 🌗 Neu: Hell-Modus

- Im Menü unter **Darstellung**: Dunkel (Standard), Hell oder System (folgt der Einstellung des Geräts und wechselt live mit). Die Wahl wird pro Browser gespeichert und vor dem ersten Rendern angewendet, deshalb blitzt nichts dunkel auf.
- Umgesetzt über die Farbvariablen von Tailwind 4 (`html.light` in `index.css`), ohne `dark:`-Varianten im Markup. Ältere Browser ohne `color-mix()` (Chrome < 111, Safari < 16.2) bekommen die Option nicht angezeigt, dort bleibt alles dunkel.
- Behoben: Im Dunkel-Modus schimmerte der Hintergrund rechts unten hell, wenn das Gerät auf hell eingestellt war (`color-scheme` ist jetzt fest `dark`).

### 🌐 Box unter `offgridcloud.local` und per IP erreichbar

- Neben dem eindeutigen `https://offgridcloud-XXXXX.local` hört die Box jetzt auch auf den festen Alias **`https://offgridcloud.local`**. Den veröffentlicht der neue Dienst `offgridcloud-mdns-alias` per avahi, je Netzwerk-Schnittstelle mit der passenden IP (LAN und Rückfall-WLAN). Bei mehreren Boxen im selben Netz bekommt nur die erste den Alias.
- **Zugriff per IP:** Caddy beantwortet `http://<IP der Box>` jetzt direkt (ohne Zertifikat, weil es für nackte IPs keins gibt). Die `.local`-Namen leiten weiter auf HTTPS um.
- Passkeys sind auch unter `offgridcloud.local` erlaubt (eigener Passkey pro Adresse).

### ✨ Neu: Fertiges Raspberry-Pi-Image mit Setup-Assistent

- **Flashbares Image** für Raspberry Pi 3/4/5 und Zero 2 W (Raspberry Pi OS Lite, arm64). Gebaut mit pi-gen auf einem nativen arm64-Runner und an jedes Release als `offgridcloud-<version>-rpi-arm64.img.xz` angehängt (`.github/workflows/image.yml`).
- **Keine Geheimnisse im Image:** Secret-Key und Hostname-Vorschlag entstehen beim ersten Boot. Den Admin legt der neue **Setup-Assistent** unter `/setup` an. Bis dahin antworten alle anderen APIs mit 503.
- **Setup-WLAN mit Captive Portal:** Ohne Kabel öffnet die Box das WLAN `OffgridCloud-Setup-XXXX`, und Handys zeigen den Assistenten von selbst an. Per Kabel ist er unter `http://offgridcloud-setup.local` erreichbar.
- **Optionaler Setup-Code** und Passwort fürs Setup-WLAN über `offgridcloud-setup.txt` auf der Boot-Partition. Falsche Codes werden gedrosselt.
- Der Assistent setzt Admin, Box-Namen, Ziel-WLAN und das Passwort fürs Rückfall-WLAN und übergibt dann an `deploy/setup/finish.sh`. Anleitung: [docs/IMAGE.md](docs/IMAGE.md).
- `install.sh` hat dafür einen Image-Build-Modus (`OGC_IMAGE_BUILD=1`), `deploy/https/install.sh` die Option `--skip-apply`.

### 🐳 Docker: fertiges Image auf GHCR

- **Multi-Arch-Image** (`linux/amd64`, `linux/arm64`) unter `ghcr.io/w0rkingchr1s/offgridcloud`: `latest` und `<major>.<minor>`/`<version>` bei jedem Release, `main` bei jedem Push auf `main`. arm64 wird nativ gebaut, ohne QEMU (`.github/workflows/docker.yml`).
- **`docker-compose.yml`** im Repo-Stamm: `docker compose up -d` startet die Box, `docker compose pull && docker compose up -d` aktualisiert sie. VPN-Rechte sind auskommentiert vorbereitet.
- **Image kennt seine Version:** Version, Commit und Update-Kanal werden beim Build gestempelt, die System-Seite zeigt also die echte Version statt `0.1.0`.
- **Docker-taugliche Vorgaben:** Im Container sind One-Click-Update, Neustart/Reboot/Shutdown und die Laufwerks-Aktionen abgeschaltet (kein sudo/systemd). Die System-Seite zeigt als Update-Befehl `docker compose pull …` statt `update.sh`. Healthcheck auf `/api/health` eingebaut.

### 🤖 Auto-Merge für Pull Requests

- PRs mit dem Label `automerge` werden automatisch per Squash gemerged, sobald CI (und ggf. der Image-Build) auf dem Head-Commit grün ist. Dependabot-Patch- und -Minor-Updates erhalten das Label automatisch (`.github/workflows/automerge.yml`, Details in [CONTRIBUTING.md](CONTRIBUTING.md)).

### 🧹 Branch-Cleanup

- Gemergte Branches werden automatisch gelöscht: sofort nach dem Merge und zusätzlich per wöchentlichem Durchlauf für Altlasten. Branches mit offenem PR oder neuen Commits bleiben stehen (`.github/workflows/branch-cleanup.yml`).

## [0.5.0] – [0.5.2] – 2026-10-19

- **Speicher-Verwaltung** für angeschlossene Medien mit Import für den Upload (#133), dazu **Galerie für Kamerakarten**, boxweite Duplikatprüfung und Cache-Laufwerk (#138).
- Frontend auf Vite 8, React 19 (#136).
- Behoben: Code, den `sudo` als root ausführt, liegt nicht mehr beim Dienstkonto (#135). Ein Update brach mit „No such file or directory“ bei `pip` ab (#137).

## [0.4.11] – 2026-10-19

Die Box lässt sich jetzt **komplett per Telegram steuern** (Status, Neustart, Speedtest, Update). Außerdem bleibt eine Installation zuverlässig auf ihrem **Update-Kanal**, und **viele Sicherheitslücken in Abhängigkeiten** sind geschlossen.

### ✨ Neu: Telegram-Bot mit Befehlen

- **Verbindungstest:** Beim Speichern von Bot-Token und Chat-ID meldet sich die Box mit „OffgridCloud verbunden“ und richtet Befehlsmenü, Beschreibungen und Menü-Button ein. Im Portal gibt es dazu den Button **„Testnachricht“**. (#123)
- **Abfragen:** `/status`, `/queue`, `/speicher`, `/fehler`, `/retry`, `/netz`, `/ping`, `/hilfe`. Der Bot antwortet nur im konfigurierten Chat. Andere Chats bekommen per `/id` ihre Chat-ID angezeigt. Befehle, die während einer Offline-Phase länger als 15 min gewartet haben, werden verworfen. (#123)
- **Push-Meldungen** kommen jetzt mit Icon und fettem Titel. (#123)
- **Gruppen-Branding:** Ist der Bot in einer Gruppe oder einem Kanal Admin, setzt er dort Titel („OffgridCloud · &lt;host&gt;“), Beschreibung und Logo und pinnt die Begrüßung an. (#125)
- **Steuerung mit Bestätigung:** `/reboot` (#125), `/shutdown` (#127) und `/restart` (nur der Dienst, #129) fragen per „Ja / Abbrechen“-Button nach. Bestätigen darf nur, wer den Befehl ausgelöst hat, und nur innerhalb von 60 s. Jede Aktion landet im Audit-Log. Ist der zugehörige Befehl in der `.env` leer, ist die Aktion deaktiviert.
- **`/speedtest`** misst die Bandbreite mit derselben Probe wie „Jetzt messen“ im Portal. Weil jede Messung auf einem LTE-Uplink schnell Hunderte MB kosten kann, gibt es eine Sperrzeit von 5 min zwischen zwei Messungen (`OGC_TELEGRAM_SPEEDTEST_COOLDOWN`, `0` = aus). (#130)
- **`/update`** prüft auf eine neue Version, zeigt die Änderungen und startet nach Bestätigung das One-Click-Update. Das Ergebnis meldet der Bot nach dem Neustart in den Chat. Dafür muss `OGC_SELF_UPDATE` aktiv sein. (#131)

### 🔄 Updates: Kanal wird erkannt

- **main-Kanal:** Installationen vom `main`-Branch (Version wie `0.4.10-44-g97b8eb0`) prüfen jetzt Commit für Commit gegen `main` statt gegen Releases. Die System-Seite zeigt „main @ &lt;commit&gt;“ bzw. „N neue Commits auf main“. (#124)
- **Kein stiller Downgrade mehr:** Bisher konnte das One-Click-Update eine `main`-Installation auf das letzte, ältere Release zurücksetzen. Das ist behoben: `update.sh` bleibt auf dem installierten Kanal. Gewechselt wird nur explizit mit `--channel release|main`. (#124)
- Der Retro-Modus zeigt die echte installierte Version statt `v0.0.1`. (#126)

### 🔒 Sicherheit

Gepatchte Advisories in Abhängigkeiten, unter anderem:

- **Backend:** `cryptography`, `pyjwt` (u. a. GHSA-42vr-xj54-vc7v), `python-multipart`
- **Frontend:** `react-router-dom` (Open Redirect), `nanoid` (DoS, GHSA-2v37-7h3g-55p8), `postcss` (XSS/Path Traversal), `postcss-selector-parser` (DoS)

Dazu kommt Dependabot für laufende Updates. (#69–#122)

### 🧰 Unter der Haube

- **Docker-Image:** Python 3.14 und Node 26 als Basis (vorher 3.11 / 22). (#102, #103)
- **Abhängigkeiten:** SQLAlchemy 2.1, FastAPI 0.143, Pydantic 2.14, webauthn 3.0, TypeScript 7, Vite 6.4.4.
- **Tests & CI:** Den wackeligen Bandbreiten-Test (Parallel-Streams) deterministisch gemacht (#128). GitHub Actions auf aktuelle Major-Versionen gehoben (#89).

### ⬆️ Aktualisieren

- **Release-Kanal:** System → „Jetzt aktualisieren“, oder
  `sudo /opt/offgridcloud/src/deploy/update.sh`
- **Von einem `main`-Stand vor #124** (Version wie `0.4.10-NN-g…`): Den ersten Lauf **einmal von Hand mit Kanal** starten, weil noch das alte `update.sh` aktiv ist:
  `sudo /opt/offgridcloud/src/deploy/update.sh --channel release` (oder `--channel main`)

Daten, `.env` und Port bleiben erhalten.

**Alle Änderungen:** https://github.com/W0rkingChr1s/OffgridCloud/compare/v0.4.10...v0.4.11
