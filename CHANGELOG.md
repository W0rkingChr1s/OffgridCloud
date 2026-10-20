# Changelog

Alle nennenswerten Änderungen an OffgridCloud. Die neueste Version steht oben.
Ältere Versionen (bis v0.4.10) sind in den
[GitHub-Releases](https://github.com/W0rkingChr1s/OffgridCloud/releases) dokumentiert.

## [Unreleased]

### ✨ Neu: Fertiges Raspberry-Pi-Image mit Setup-Assistent

- **Flashbares Image** für Raspberry Pi 3/4/5 und Zero 2 W (Raspberry Pi OS Lite, arm64). Gebaut mit pi-gen auf einem nativen arm64-Runner und an jedes Release als `offgridcloud-<version>-rpi-arm64.img.xz` angehängt (`.github/workflows/image.yml`).
- **Keine Geheimnisse im Image:** Secret-Key und Hostname-Vorschlag entstehen beim ersten Boot. Den Admin legt der neue **Setup-Assistent** unter `/setup` an. Bis dahin antworten alle anderen APIs mit 503.
- **Setup-WLAN mit Captive Portal:** Ohne Kabel öffnet die Box das WLAN `OffgridCloud-Setup-XXXX`, und Handys zeigen den Assistenten von selbst an. Per Kabel ist er unter `http://offgridcloud-setup.local` erreichbar.
- **Optionaler Setup-Code** und Passwort fürs Setup-WLAN über `offgridcloud-setup.txt` auf der Boot-Partition. Falsche Codes werden gedrosselt.
- Der Assistent setzt Admin, Box-Namen, Ziel-WLAN und das Passwort fürs Rückfall-WLAN und übergibt dann an `deploy/setup/finish.sh`. Anleitung: [docs/IMAGE.md](docs/IMAGE.md).
- `install.sh` hat dafür einen Image-Build-Modus (`OGC_IMAGE_BUILD=1`), `deploy/https/install.sh` die Option `--skip-apply`.

### 🤖 Auto-Merge für Pull Requests

- PRs mit dem Label `automerge` werden automatisch per Squash gemerged, sobald CI (und ggf. der Image-Build) auf dem Head-Commit grün ist. Dependabot-Patch- und -Minor-Updates erhalten das Label automatisch (`.github/workflows/automerge.yml`, Details in [CONTRIBUTING.md](CONTRIBUTING.md)).

### 🧹 Branch-Cleanup

- Gemergte Branches werden automatisch gelöscht: sofort nach dem Merge und zusätzlich per wöchentlichem Durchlauf für Altlasten. Branches mit offenem PR oder neuen Commits bleiben stehen (`.github/workflows/branch-cleanup.yml`).

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
