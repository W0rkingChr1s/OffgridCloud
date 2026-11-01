# Changelog

Alle nennenswerten Änderungen an OffgridCloud. Die neueste Version steht oben.
Ältere Versionen (bis v0.4.10) sind in den
[GitHub-Releases](https://github.com/W0rkingChr1s/OffgridCloud/releases) dokumentiert.

## [Unreleased]

## [0.11.1] – 2026-11-01

Bugfix-Release: Das One-Click-Update aus dem Portal lief nicht immer bis zum Ende durch. Danach fehlte unter anderem Bluetooth nach dem nächsten Neustart. **Wichtig:** Das Update auf 0.11.1 selbst läuft noch mit dem alten Update-Skript. Am sichersten per SSH: `sudo /opt/offgridcloud/src/deploy/update.sh`.

### 🐛 Behoben

- **One-Click-Update bricht nicht mehr mittendrin ab:** Das Update aus dem Portal lief in der cgroup des Webdienstes. Startete ein Unterskript (AP-Fallback, VPN) den Webdienst neu, beendete systemd das ganze Update gleich mit, nach dem Neuaufbau des Python-venv, aber vor dem Bluetooth-Schritt. Das Portal meldete trotzdem Erfolg, weil die Version schon gestempelt war. Folge: `dbus-fast` fehlte im neuen venv, und `offgridcloud-ble` fiel erst nach dem nächsten Neustart der Box mit `No module named 'dbus_fast'` aus, weil der alte Prozess bis dahin weiterlief. Auch Kiosk-, HTTPS- und Abschlussschritte fielen still weg. Jetzt läuft `update.sh` als eigene systemd-Unit weiter, die Unterskripte starten den Webdienst während eines Updates nicht mehr neu, und `dbus-fast` kommt direkt beim venv-Neubau mit.
  Betroffene Boxen: einmal `sudo /opt/offgridcloud/src/deploy/update.sh` per SSH ausführen (oder nur `sudo /opt/offgridcloud/src/deploy/ble/install.sh` für Bluetooth).

## [0.11.0] – 2026-10-31

Große Dateien kommen jetzt auch über Nextcloud-Freigabelinks ohne Chunked-Upload in die Cloud: Videos als abspielbare Clips, alles andere in Stücken. Dazu: kein sinnloses Wiederholen bei „zu groß“, kleine Dateien zuerst, eine Transferliste, die aufs Handy passt, und Hinweise direkt am Provider.

### ✨ Neu

- **Große Dateien werden für Freigabelinks automatisch geteilt:** Auf Nextcloud-Servern vor Version 32 nimmt ein Freigabelink nur Uploads am Stück an, und der Proxy davor (bei Hetzner Storage Share: Cloudflare) lehnt alles über rund 100 MB ab. Dateien über der neuen Einstellung „Max. Größe am Stück“ (Standard 90 MB) teilt OffgridCloud jetzt vor dem Upload und legt die Teile im Zielordner in einem Unterordner mit dem Namen der Datei ab.
  - **Videos** werden mit ffmpeg verlustfrei und ohne Neukodierung in abspielbare Clips geschnitten, z. B. `DJI_0005_W/DJI_0005_W_teil01.MP4`. Das geht auch auf dem Pi schnell.
  - **Flugdaten** in einer Untertitelspur (z. B. bei manchen DJI-Modellen) bleiben in den Clips erhalten und kommen zusätzlich als `DJI_0005_W.srt` daneben. Reine Datenspuren wie die DJI-Telemetrie `djmd` lassen sich nicht schneiden und fehlen in den Clips.
  - **Andere Dateien** werden in Stücke `name.ext.001`, `.002` … zerlegt, mit einer `ZUSAMMENFUEGEN.txt`, die das Zusammenfügen erklärt (7-Zip, `copy /b`, `cat`).
  - **Abgebrochene Uploads machen beim nächsten Versuch beim ersten fehlenden Teil weiter**, statt alles erneut zu senden.
  - **Vor dem Teilen wird der freie Platz im Puffer geprüft.** Es braucht kurz noch einmal so viel Platz, wie die Datei groß ist.
  - In der Transferliste steht „Teil x/n“ und am Ende „in N Teilen“. Das Löschen in der Cloud entfernt alle Teile.

  ffmpeg ist im Pi-Image enthalten und jetzt auch im Docker-Image.
- **Uploads, die an der Dateigröße scheitern, werden nicht mehr automatisch wiederholt:** Ein „413 Payload Too Large“ (oder 507, fehlendes ffmpeg) kommt beim nächsten Versuch genauso. Bisher lief eine 3,5-GB-Datei so bis zu fünfmal plus bei jedem Selbstheilungslauf erneut. Solche Jobs gehen sofort auf „fehlgeschlagen“, „Erneut“ startet sie bei Bedarf von Hand.
- **Kleine Dateien zuerst:** Bei gleicher Priorität starten Dateien bis 50 MB vor größeren. Ein Lagefoto wartet nicht mehr eine Stunde hinter einem 3,5-GB-Video, das eine Minute früher kam. Ein laufender Upload wird dafür nicht abgebrochen.
- **Hinweise am Provider:** Einschränkungen eines funktionierenden Ziels (z. B. „kein Chunked-Upload, Dateien über 90 MB werden geteilt“) stehen jetzt dauerhaft in der Provider-Liste. Sie werden beim Testen gesetzt und alle 6 Stunden im Hintergrund nachgeprüft. Ist der Server gerade nicht erreichbar, bleibt der alte Hinweis stehen. Aktualisiert Hetzner auf Nextcloud 32, verschwindet der Hinweis von selbst.
- **Verbindungstest einer Freigabe ohne Chunked-Upload** gilt jetzt als bestanden, mit Hinweis auf das Limit, statt als Fehler.
- **Rechtsklick-Menü auch auf der Speicher-Seite und in der Suche:** In der Galerie und der Ordner-Ansicht von Karten und USB-Laufwerken gibt es *Vorschau*, *Öffnen*, *Auswählen*, *Ganzen Tag auswählen*, *Nur diese Datei hochladen nach „…“* (die übrige Auswahl bleibt dabei stehen), *Auf Cache sichern*, *In „Ordner“ anzeigen* für Dateien, die schon auf der Box liegen, sowie *Dateinamen/Pfad kopieren*. Ein Rechtsklick in eine Mehrfachauswahl lädt die ganze Auswahl hoch oder sichert sie. Das Ziel ist der unten in der Leiste gewählte Ordner. In der Suche gibt es *Herunterladen*, *Ordner öffnen* und *Dateinamen kopieren*.

### 🐛 Fehlerbehebungen

- **Transferliste auf dem Handy:** Lange Kameradateinamen haben die Tabelle über den Bildschirmrand geschoben, Status und Größe waren abgeschnitten. Die Namen brechen jetzt um, die Größe steht unter dem Status und „Erneut“ darunter.
- **Fehlermeldungen ohne HTML:** Die HTML-Fehlerseite, die ein Proxy wie Cloudflare mitschickt, wird aus der Meldung entfernt.
- **Weboberfläche wird nach längerer Laufzeit langsam bis unerreichbar (möglicher Auslöser):** Jeder offene Tab hielt zwei Live-Verbindungen, und jede baute alle 2 Sekunden einen eigenen Zustandsbericht. Für Admins las dieser jedes Mal die Dateinamen aller Medien. Schlief ein iPad hinter Caddy ein, blieben seine Verbindungen oft stundenlang offen, und die Box rechnete für niemanden weiter. Jetzt hat jeder Tab nur noch eine Verbindung, die er schließt, solange er im Hintergrund ist. Der Bericht entsteht pro Nutzer höchstens einmal je Intervall und liest nur die Namen der laufenden Transfers. Außerdem beendet der Server jede Verbindung nach 5 Minuten, der Browser verbindet sich von selbst neu.

## [0.10.3] – 2026-10-31

Nextcloud-Freigabelinks auf Servern vor Nextcloud 32 nehmen Dateien über 10 MiB wieder an, und zu große Dateien melden, was hilft.

### 🐛 Fehlerbehebungen

- **Freigabelinks auf Nextcloud < 32: Dateien über 10 MiB scheiterten mit „Parent node does not exist“ (409):** Seit dem Chunked-Upload (0.10.2) zerlegt rclone größere Dateien in Stücke. Ältere Nextcloud-Versionen haben für Freigaben aber keinen Chunk-Endpunkt. OffgridCloud prüft das jetzt vor dem Upload und schickt die Datei auf solchen Servern wieder am Stück, wie vor 0.10.2. Dateien bis zum Limit des Proxys vor dem Server (bei Hetzner Storage Share rund 100 MB) gehen dann wieder durch. Was größer ist, scheitert mit 413, und die Fehlermeldung sagt dazu, was hilft: Nextcloud 32 oder der Provider „Nextcloud“ mit Konto und App-Passwort.

## [0.10.2] – 2026-10-31

Große Videos auf Nextcloud-Freigabelinks laden in Chunks hoch (rclone ≥ 1.63), und wartende Transfers zeigen ihren Fehlergrund.

### 🐛 Fehlerbehebungen

- **Große Videos auf Nextcloud-Freigabelinks blieben bei „wartet“ hängen:** Der Chunked-Upload aus #247 greift erst ab rclone 1.63. Das Docker-Image installierte rclone aus Debian (1.60.1), und `install.sh` behielt ein vorhandenes altes rclone. Dann gingen große Dateien weiter als ein einziger Request raus und scheiterten mit „413 Payload Too Large“.
  - Das Docker-Image bringt jetzt das offizielle rclone-Release mit.
  - `install.sh` ersetzt ein rclone älter als 1.63.
  - Der Verbindungstest einer Freigabe meldet ein zu altes rclone. Fehlt der Chunk-Endpunkt auf dem Server, nennt er die Nextcloud-Version (Chunked-Upload für Freigaben gibt es erst ab Nextcloud 32).
- **Transfers zeigten bei „wartet“ keinen Grund:** Die letzte Fehlermeldung steht jetzt auch bei Jobs, die auf den nächsten Versuch warten, unter dem Dateinamen, also auch auf dem Handy sichtbar.

## [0.10.1] – 2026-10-31

Die Web-Oberfläche lädt auf dem Handy deutlich schneller.

### ⚡ Performance

- **Web-Oberfläche lädt auf dem Handy deutlich schneller:** Bisher kam die ganze App als ein unkomprimiertes 660-KB-Skript, bei jedem Aufruf neu über das Netz, auch wenn der Service Worker es schon hatte. Jetzt:
  - Das Frontend wird beim Bauen vorkomprimiert (Brotli/gzip), die Box liefert diese Dateien direkt aus, ohne eigene CPU-Last. Der erste Aufruf überträgt rund 90 KB statt rund 750 KB.
  - Jede Seite ist ein eigenes Paket. Die Admin-Seiten werden erst geladen, wenn man sie öffnet.
  - Dateien unter `/assets/` (mit Hash im Namen) sind dauerhaft cachebar, und der Service Worker beantwortet sie aus dem Cache. Ein erneuter Aufruf lädt nur noch `index.html`.
  - Gemessen mit gedrosseltem Handy-Profil (6× langsamere CPU, 1,6 Mbit/s, 150 ms Latenz): Login-Seite 5,2 s → 1,4 s.
  - Öffnet ein Tab nach einem Update eine noch nicht geladene Seite, deren Paket es nicht mehr gibt, lädt die App einmal neu statt weiß zu bleiben.

## [0.10.0] – 2026-10-30

Klarere Fehlermeldungen beim Login.

### 🐛 Fehlerbehebungen

- **Login meldete nur „Passkey-Anmeldung fehlgeschlagen“:** Fehler, die der Browser selbst bei der Passkey- oder Passwort-Anmeldung wirft, landeten in einer Sammelmeldung ohne Ursache. Jetzt steht die konkrete Fehlermeldung im Login. Ist kein passender Passkey da, weist der Login darauf hin, dass Passkeys nur für die Adresse gelten, unter der sie angelegt wurden (z. B. `offgridcloud.local` vs. `<hostname>.local`). Verweigert der Browser Passkeys wegen eines nicht vertrauten Zertifikats, sagt er das ebenfalls.

## [0.9.6] – 2026-10-30

### 🐛 Fehlerbehebungen

- **Nextcloud-Freigabelinks: große Dateien scheiterten mit 413:** Dateien über 100 MB scheiterten beim Upload auf einen Freigabelink an Proxys wie Cloudflare (413 „Payload Too Large“). Jetzt wird in 10-MB-Stücken über den Chunked-Upload hochgeladen, den Nextcloud für Freigaben anbietet. Fehlt dieser Endpunkt bei älteren Nextcloud-Versionen, meldet das der Verbindungstest.

## [0.9.5] – 2026-10-29

Ein gemeinsamer Datei-Browser für Ordner, Karten und Suche, Nextcloud-Freigabelinks als Cloud-Ziel und ein Rechtsklick-Menü.

### ✨ Neu

- **Einheitlicher Datei-Browser:** Medien eines Ordners, die Ordner-Ansicht von Karten und USB-Laufwerken sowie die Suche nutzen jetzt denselben Browser. Er bietet Liste oder Kacheln mit Vorschaubildern (die Wahl wird je Ansicht gemerkt), einen Namensfilter, Sortierung per Spaltenkopf und Mehrfachauswahl: Shift-Klick für einen Bereich, Strg/⌘+A für alle, Esc hebt sie auf. Bedienbar ist er auch per Tastatur (Pfeiltasten, Leertaste, Enter öffnet). Lange Listen werden virtualisiert: Eine Karte mit Tausenden Dateien rendert nur, was gerade zu sehen ist, und fragt nur dafür Vorschaubilder an. Die Ordner-Ansicht auf der Speicher-Seite zeigt jetzt Vorschaubilder; sie teilt sich den Thumbnail-Cache mit der Galerie.
- **Nextcloud-Freigabelinks als Cloud-Ziel:** Neuer Provider-Typ „Nextcloud-Freigabelink“. Man fügt nur den öffentlichen Link ein (`https://…/s/<token>`, auch Hetzner Storage Share), bei Bedarf mit Freigabe-Passwort. Die Box baut daraus selbst den WebDAV-Zugang. Ein eigenes Nextcloud-Konto ist nicht nötig. Der Verbindungstest prüft zusätzlich die Rechte der Freigabe und meldet einen schreibgeschützten Link sofort, statt erst beim ersten Upload zu scheitern. Die Freigabe braucht „Hochladen und Bearbeiten erlauben“, „Dateiablage“ reicht nicht.
- **Rechtsklick-Menü:** In der Medienliste eines Ordners, in den Transfers und in der Ordnerverwaltung öffnet ein Rechtsklick ein Kontextmenü mit den passenden Aktionen: Herunterladen, Beschreiben, Auswählen, Dateinamen kopieren und Löschen bei Medien; Erneut versuchen, Zum Ordner und Fehlermeldung kopieren bei Transfers. Ist die angeklickte Datei Teil einer Mehrfachauswahl, gelten die Aktionen für die ganze Auswahl (ZIP-Download, Beschreiben, Löschen). Bedienbar mit Pfeiltasten, Enter und Esc. Wer das Browser-Menü braucht, hält Shift gedrückt; in Eingabefeldern und auf markiertem Text bleibt es ohnehin.

### 🐛 Fehlerbehebungen

- **Monitoring-Token ließ sich über HTTP nicht kopieren:** Der „Kopieren“-Button unter Zustand → Zugriff für Monitoring-Systeme nutzte nur die Clipboard-API des Browsers, die es ausschließlich über HTTPS gibt. Auf einer Box, die per `http://` erreicht wird, passierte beim Klick nichts. Jetzt greift derselbe Fallback wie im Telegram-Assistenten. Klappt auch der nicht, erscheint ein Hinweis, das Token von Hand zu kopieren.

## [0.9.3] – 2026-10-28

### 🐛 Fehlerbehebungen

- **Fehlendes `dbus-fast` wird im Portal angezeigt:** Scheiterte bei einem Update die Installation von `dbus-fast`, stürzte `offgridcloud-ble` schon beim Import ab, und das Portal meldete nur „Der Bluetooth-Dienst der Box meldet sich nicht“. Jetzt zeigt es Admins den Fehler und den Befehl zum Nachinstallieren (`sudo /opt/offgridcloud/src/deploy/ble/install.sh`).

## [0.9.2] – 2026-10-28

### 🐛 Fehlerbehebungen

- **Bluetooth-Box verschwindet nach einer Weile und meldet sich nicht mehr:** Lehnte BlueZ das Anmelden der Werbung ein einziges Mal ab (z. B. direkt nach dem Trennen eines Handys), blieb die Werbung halb registriert, und jeder weitere Versuch scheiterte, bis der Dienst von Hand neu gestartet wurde. Der Prozess lief weiter, schrieb aber keinen Herzschlag mehr: Das Portal zeigte „Der Bluetooth-Dienst der Box meldet sich nicht“ ohne Fehlermeldung. Jetzt räumt der Dienst eine gescheiterte Anmeldung auf, zeigt Admins nach 30 Sekunden den Fehler im Portal und startet sich nach einer Minute ohne Erfolg selbst neu. Auch ein Neustart von `bluetoothd` (z. B. durch ein BlueZ-Update), der alle Registrierungen verwirft, führt jetzt zu einem sauberen Neustart statt zu einer unsichtbaren Box.

## [0.9.1] – 2026-10-28

Updates laufen auf Boxen mit Leitungsbündelung wieder durch: net-merge ist umgezogen, die Box zieht mit und bekommt net-merge 0.3.6.

### 🐛 Fehlerbehebungen

- **Update bricht mit „Code 100“ ab, wenn Leitungsbündelung installiert ist:** net-merge ist umgezogen, sein apt-Repository liegt jetzt unter `pkg.net-merge.org` (gleicher Signaturschlüssel). Die alte Adresse `rohit267.github.io/net-merge` liefert 404, `apt-get update` scheiterte daran mit „no longer has a Release file“, und damit das ganze Update. Der Installer schreibt den Eintrag jetzt vor `apt-get update` auf die neue Adresse um. Außerdem bricht eine einzelne nicht erreichbare Paketquelle das Update nicht mehr ab (Warnung, weiter mit den vorhandenen Paketlisten). Links zeigen auf das neue GitHub-Repo `net-merge/net-merge`. Für Boxen, die gerade festhängen: `sudo sed -i 's#rohit267.github.io/net-merge#pkg.net-merge.org#' /etc/apt/sources.list.d/net-merge.list`, dann erneut aktualisieren.

### 🔀 net-merge 0.3.6

- **net-merge 0.3.3 → 0.3.6:** Neue Linien wählt net-merge jetzt danach, wie ausgelastet eine Leitung wirklich ist, statt nach der Zahl offener Verbindungen. Eine schnelle Leitung mit vielen untätigen Verbindungen wird also nicht mehr übergangen. Der SOCKS5-Proxy prüft Anfragen strenger. Dazu kommen das neue apt-Repo `pkg.net-merge.org` und die Wahl einer freien Adresse beim Start. Siehe [Changelog von net-merge](https://github.com/net-merge/net-merge/blob/main/CHANGELOG.md).

## [0.9.0] – 2026-10-28

Fotos aus der Companion-App kommen jetzt auch ohne gemeinsames Netz auf die Box: Die App handelt per Bluetooth ein eigenes Upload-WLAN aus. Dazu HTTPS auch per IP und eine Weboberfläche, die beim Kopieren auf den Cache nicht mehr hängt.

### ✨ Neu

- **Fotos aus der Companion-App per WLAN-Handover:** Die App fordert über Bluetooth ein WLAN zur Box an und lädt darüber hoch, auch wenn Handy und Box in keinem gemeinsamen Netz sind. Neu ist `GET/POST/DELETE /api/uplink/handover`, nur über die Bluetooth-Brücke erreichbar. Die Box nimmt das laufende Rückfall-WLAN, sonst ein eigenes Upload-WLAN `OGC-<Box>-up` auf Abruf: auf einem freien Adapter, oder auf `wlan0`, wobei sie dann für die Dauer des Uploads offline geht. Cloud-Transfers warten in dieser Zeit. Jedes Handy hat seinen eigenen WLAN-Schlüssel. Die App pinnt die Root-CA der Box, die sie über den verschlüsselten Bluetooth-Kanal bekommt. Das Token gilt nur für den Chunk-Upload. Das WLAN geht nach 5 min Leerlauf, spätestens nach 60 min wieder aus. Ob normale Benutzer die Box dafür offline nehmen dürfen, legt der Admin unter *Bluetooth* fest (Standard: nein). Läuft mit `hostapd` und dem Helfer `deploy/netfallback/handover.sh`; bestehende Boxen mit Rückfall-WLAN bekommen beides beim nächsten Update. Siehe [Netzwerk-Redundanz](docs/NETZWERK-REDUNDANZ.md#upload-wlan-für-die-companion-app).
- **HTTPS auch per IP:** `https://<IP der Box>` brach bisher mit einem TLS-Fehler ab, weil es für eine nackte IP kein Zertifikat gab. Caddy stellt jetzt beim ersten Aufruf ein Zertifikat für die aufgerufene IP aus, von derselben lokalen CA wie für `<name>.local`. Mit installiertem Root-Zertifikat (*System → Erweitert*) gibt es also keine Warnung. Das Backend gibt nur IPs der Box selbst frei (`/api/system/https/ask`). `http://<IP>` bleibt unverändert erreichbar. Bestehende Boxen bekommen die neue Caddy-Konfiguration beim nächsten Update.

### 🐛 Fehlerbehebungen

- **Upload-WLAN der Companion-App nimmt die Box nicht mehr offline:** Hing die Box nur per WLAN am Internet, pausierte sie für den Handover ihre Verbindung. Jetzt läuft das Upload-WLAN auf einem virtuellen Interface `ogcup0` neben der bestehenden WLAN-Verbindung, auf deren Kanal (auch 5 GHz außerhalb der Radar-Kanäle). Geht das nicht, lehnt die Box den Handover mit einer Erklärung ab, statt offline zu gehen. Bricht die WLAN-Verbindung während der Sitzung ab, beendet der Watchdog das Upload-WLAN. Der Admin-Schalter „Benutzer dürfen für Uploads aus der App das WLAN der Box übernehmen“ entfällt; `uplink_pause` ist immer `false`, `confirm_pause` wird ignoriert. Bestehende Boxen bekommen beim nächsten Update `iw` und die NetworkManager-Regel für `ogcup0`.
- **Weboberfläche hängt während einer Kopie auf den Cache:** Die Galerie fordert hunderte Vorschaubilder auf einmal an. Jede dieser Anfragen belegte einen Thread des gemeinsamen Server-Pools, während sie auf einen der zwei Render-Plätze wartete. Bremste ein laufender Kopiervorgang das Lesen von der Karte, waren alle 40 Threads belegt, und die ganze Website samt Login und statischen Dateien hing. Vorschaubilder warten jetzt ohne Thread in einer eigenen Warteschlange. Der Kopierjob selbst läuft mit niedrigster CPU- und I/O-Priorität, schreibt alle 16 MiB zurück, statt den Page-Cache (im 400-MB-Limit des Dienstes) vollzuschreiben, und es läuft nur noch ein Import- oder Cache-Job gleichzeitig. Weitere Jobs warten als „wartet“.
- **Safari zeigt statt einer Fehlermeldung eine Hilfeseite, wenn die Box nicht erreichbar ist:** Fand der Service Worker bei einem Seitenaufruf weder die Box noch die App-Seite im Cache, zeigte Safari eine eigene Fehlerseite ohne Ausweg. Jetzt kommt eine Seite „Box nicht erreichbar“ mit Erklärung zum Zertifikat und „Neu verbinden“-Knopf (setzt Service Worker und Cache zurück). Hinweis: Die Meldung „Returned response is null“ stammt von Service-Worker-Versionen vor 0.7.5. Safari kann so einen alten Worker bei abgelaufenem Zertifikat nicht selbst ersetzen, dann einmal die Websitedaten von `<name>.local` in Safari löschen.

## [0.8.5] – 2026-10-25

Der Zustands-Verlauf passt jetzt über Bluetooth: Die Companion-App zeigt den Health Monitor samt 24-h-Verlauf auch ohne Netz, direkt per Funk zur Box.

### 🐛 Fehlerbehebungen

- **Zustands-Verlauf passt wieder über Bluetooth:** `GET /api/monitor/history` lieferte immer alle Minutenwerte der letzten 24 h, als JSON rund 240 kB. Eine BLE-Nachricht fasst aber höchstens 64 KiB, deshalb scheiterte der Abruf über Bluetooth nach gut 6 Stunden Laufzeit mit `too_large`. Neu sind die Parameter `step` (Mittelwerte über 60–3600 s, Ausfälle bleiben sichtbar) und `hours` (nur die letzten 1–24 Stunden). Mit `?step=900` ist der ganze Tag rund 15 kB groß. Ohne Parameter bleibt die Antwort wie bisher. (#223)

## [0.8.4] – 2026-10-25

Health Monitor: eine neue Seite *Zustand* zeigt System, Speicher und Internet grafisch mit 24-h-Verlauf, und Monitoring-Systeme wie Prometheus, PRTG oder die Companion-App lesen dieselben Werte maschinenlesbar. Dazu ein Portal-Link im Telegram-Bot.

### ✨ Neu

- **Health Monitor:** Neue Admin-Seite *Zustand* mit animierten Anzeigen für CPU, Arbeitsspeicher, Temperatur und Speicher, Prüfungen mit Ampel, Internet-Details (Latenz, Ausfälle der letzten 24 h, WLAN-Signal, IPs, VPN) und einem 24-h-Verlauf als Diagramm oder Tabelle. Auf dem Raspberry Pi zeigt sie auch Unterspannung und Drosselung — jetzt und seit dem Start.
- **Maschinenlesbar:** `GET /api/monitor` liefert alle Werte als JSON, als Prometheus-Metriken (`/api/monitor/metrics`) und als erweiterten PRTG-Sensor (`?format=prtg`); `/api/monitor/history` den Verlauf. Zugriff mit Admin-Anmeldung oder einem **Monitoring-Token** (nur Lesen, im UI erzeugen und widerrufen). Die Companion-App erreicht `/api/monitor` und den Verlauf auch per Bluetooth. Details: [docs/BETRIEB.md](docs/BETRIEB.md) §8. (#221)
- **Portal-Link im Telegram-Bot:** `/status` zeigt einen anklickbaren Link aufs Portal, `/netz` alle Adressen — zuerst die öffentliche Domain (falls unter *System → HTTPS* eingetragen), dann `<hostname>.local`. Ohne HTTPS-Proxy zeigt der Link auf `http://<hostname>.local:<port>`. (#219)

## [0.8.3] – 2026-10-25

Ruhigere Verbindungsmeldungen: Kurze WLAN-Aussetzer lösen kein „Wieder online“ mehr aus, und echte Ausfälle kommen mit Dauer.

### 🐛 Fehlerbehebungen

- **Weniger falsche „Wieder online“-Meldungen:** Ein einzelner gescheiterter Verbindungstest (zwei TCP-Verbindungen à 3 s) galt bisher schon als Ausfall. Kurze WLAN-Aussetzer lösten so mehrmals täglich eine Meldung aus. Jetzt gilt die Box erst nach 3 gescheiterten Tests in Folge (≈ 1 Minute) als offline. Die Meldung nennt die Ausfalldauer („Verbindung nach 4 min wiederhergestellt“), der Webhook liefert sie als `outage_seconds`. Den Beginn eines Ausfalls loggt das Backend jetzt auch („Uplink down“). (#217)

### 🧰 Unter der Haube

- **Abhängigkeiten:** SQLAlchemy 2.1.6, Vite 8.3.5; für die Entwicklung ruff 0.17.2 und httpx2 2.16.0. (#215, #216)

## [0.8.2] – 2026-10-21

Auto-Import von Wechselmedien mit Status-LED, Warteschlange für Benachrichtigungen, die offline nicht zugestellt werden können, und robustere Updates: Bluetooth fehlt auf dem Pi 3 nicht mehr stillschweigend, und gescheiterte Zusatzfunktionen meldet das Update jetzt.

### ✨ Neu

- **Auto-Import von Wechselmedien:** Unter *Speicher → Auto-Import* lässt sich ein festes Ziel wählen (Ordner mit Cloud-Upload oder Cache-Laufwerk). Jedes neu angesteckte Medium wird dann automatisch eingehängt, kopiert (Duplikate übersprungen) und auf Wunsch ausgeworfen.
- **Status-LED für den Offline-Betrieb:** Die LED der Box zeigt den Auto-Import ohne Portal an: langsames Blinken = kopiert, dauerhaft an = fertig/ziehen, schnelles Flackern = Fehler. Standard ist die `ACT`-LED des Pi; eine externe GPIO-LED (`dtoverlay=gpio-led`) ist wählbar, jedes Muster lässt sich testen.
- **Benachrichtigung „Auto-Import fertig/fehlgeschlagen“** über Telegram, E-Mail und Webhook.
- **Warteschlange für Offline-Benachrichtigungen:** Meldungen, die ein Kanal nicht zustellen kann (Box offline, Dienst weg), bleiben in der Datenbank und werden nachgeliefert, sobald er wieder erreichbar ist — bei Telegram/E-Mail gebündelt als Sammelnachricht mit Originalzeiten, beim Webhook einzeln mit `delayed`/`queued_at`. Reihenfolge bleibt erhalten; max. 500 je Kanal und 7 Tage. Anzeige mit „Jetzt senden“/„Verwerfen“ unter *System → Benachrichtigungen*.

### 🐛 Fehlerbehebungen

- **Gescheiterte Zusatzfunktionen beim Update sichtbar:** Schlug die Einrichtung einer optionalen Funktion (Bluetooth, Kiosk, VPN, HTTPS, …) fehl, meldete das Update trotzdem schlicht „abgeschlossen“. Jetzt heißt es „Mit Warnungen, siehe Protokoll“, und der Installer listet die Probleme am Ende auf. Das Protokoll des vorherigen Updates bleibt als `data/update.log.1` erhalten, auch nach „Verstanden“.
- **Bluetooth-Einrichtung wird wiederholt:** Scheitert sie mittendrin (z. B. `pip install dbus-fast`), versucht das nächste Update es erneut, statt den Dienst stillschweigend wegzulassen.
- **Bluetooth nach Update auf dem Pi 3:** Das Update richtete `offgridcloud-ble` nur ein, wenn schon ein Adapter (`hci0`) sichtbar war. Ohne BlueZ bzw. laufendes `hciuart` gibt es den aber noch nicht — der Pi 3 bekam den Dienst nie, das Portal meldete „meldet sich nicht“. Installer und Update erkennen jetzt Pis mit eingebautem Bluetooth am Modell (3B/3B+/3A+, 4, 5, 400, 500, Zero W, Zero 2 W).
- **Klarerer Bluetooth-Hinweis:** Die Seite *Bluetooth* unterscheidet jetzt „Dienst nicht installiert“, „kein Adapter“ und „Dienst meldet sich nicht“ und zeigt Admins die Fehlermeldung, mit der der Dienst zuletzt beim Start gescheitert ist.
- **Update-Log ohne Steuerzeichen:** Das Live-Log unter *System → Update* zeigte die Terminal-Farbcodes der Deploy-Skripte roh an (`␛[1;36m>> …␛[0m`). Das Backend filtert ANSI-Escape-Sequenzen jetzt heraus, bevor es das Log ausliefert.

## [0.8.1] – 2026-10-21

Fehlerbehebungen für 0.8.0: Der Bluetooth-Dienst startet wieder, und die Companion-App erreicht per Bluetooth auch den Pool.

### 🔀 net-merge-Updates per PR

- **Workflow „net-merge update“:** Prüft montags, ob es ein neues net-merge-Release gibt, und öffnet dann einen PR, der den Pin in `deploy/netmerge/install.sh` anhebt (Version und SHA-256, berechnet von `scripts/netmerge-bump.sh`). Erst wenn das apt-Repository die Version für alle Pi-Architekturen hat. Gemergt wird von Hand; der Image-Build läuft auf dem PR mit.

### 🐛 Fehlerbehebungen

- **Bluetooth-Dienst startet wieder:** `offgridcloud-ble` wollte schon werben, bevor er mit BlueZ verbunden war, stürzte dabei ab und startete alle 10 Sekunden neu. Das Web-Portal meldete deshalb „Der Bluetooth-Dienst der Box meldet sich nicht“, obwohl ein Adapter da war.
- **Pool per Bluetooth:** Die Pool-Endpunkte fehlten in der Whitelist des Bluetooth-Dienstes; der Pool-Bildschirm der Companion-App bekam nur `not_allowed`. Zugangsverwaltung (Tokens, Peers per URL + Token) bleibt per Funk gesperrt. Ein Test gleicht die Whitelist jetzt mit der Tabelle in [docs/BLE-PROTOKOLL.md](docs/BLE-PROTOKOLL.md) ab.
- **Hinweise in der App:** Das `status`-Event schickte Hinweise als Objekt statt als Text, die App verwarf sie stillschweigend.
- **Pool-Seite:** Nach dem Pausieren von Uploads tauchte das Auftrags-Panel doppelt auf.

## [0.8.0] – 2026-10-21

Die Companion-App verwaltet die Box jetzt per Bluetooth, auch ohne gemeinsames Netz. Leitungsbündelung ist ab Werk installiert, und auf Wunsch hält sich die Box nachts in einem ruhigen Zeitfenster selbst aktuell.

### 🔀 Leitungsbündelung ab Werk installiert

- **net-merge kommt jetzt standardmäßig mit:** Installer, Update und das fertige Image richten den Dienst `offgridcloud-netmerge` ohne Zutun ein; kein `sudo …/netmerge/install.sh` mehr nötig. Abwählen per Installer-Frage oder `OGC_WITH_NETMERGE=0`.
- **Eingeschaltet wird weiterhin in der UI** (Bandbreite → Leitungsbündelung). Bis dahin läuft nur ein Proxy auf `127.0.0.1:1080`, über den nichts geht.
- **Feste Version:** net-merge ist auf 0.3.3 gepinnt. Das apt-Paket wird per `apt-mark hold` festgehalten, der Tarball-Weg prüft SHA-256 statt blind „latest“ zu laden. Neue net-merge-Versionen kommen nur mit einem OffgridCloud-Update.

### 📶 Bluetooth für die Companion-App

- **Neuer Dienst `offgridcloud-ble`:** Die [Companion-App](https://github.com/W0rkingChr1s/OffgridCloud-Companion) verwaltet die Box per Bluetooth Low Energy, auch ohne gemeinsames Netz. Protokoll: [docs/BLE-PROTOKOLL.md](docs/BLE-PROTOKOLL.md), Betrieb: [BETRIEB.md §9d](docs/BETRIEB.md).
- **Ende-zu-Ende verschlüsselt** mit dem Noise Protocol (`IK`, beim Koppeln `IKpsk2`). Gekoppelt wird nur mit einem Code aus dem Web-Portal (5 Minuten, einmal gültig); jeder Benutzer koppelt seine eigenen Handys und widerruft sie wieder.
- **Keine eigenen Rechte:** Der Dienst reicht Anfragen mit einem 5-Minuten-Token des gekoppelten Benutzers an die REST-API weiter. Eine Whitelist hält Downloads, Formatieren und Zugangsdaten vom Funk fern. Schreibende Aktionen stehen mit `[ble]` im Audit-Log.
- **Bleibt erreichbar, wenn das Backend hängt:** Minimal-Status und „Dienst neu starten“ beantwortet der Dienst selbst.
- **Eine Verbindung gleichzeitig.** Während ein Handy verbunden ist, wirbt die Box nicht.
- Installer-Option „Bluetooth für die Companion-App“ (vorausgewählt, wenn ein Adapter da ist), im fertigen Image aktiv. Neue Endpunkte `/api/ble/*`; Abschalten über `PUT /api/ble/settings` (Admin).
- **Neue Seite *Bluetooth* im Web-Portal** (für alle Benutzer): „Gerät koppeln“ zeigt QR-Code und Code mit Countdown und erkennt selbst, wenn das Handy fertig ist. Darunter die gekoppelten Geräte zum Umbenennen und Entfernen; Admins sehen alle und schalten Bluetooth hier ab. Meldet sich der Dienst nicht, sagt die Seite das.

### 🔄 Automatische Updates

- **Neu unter System → Version: „Automatisch aktualisieren“** (standardmäßig aus). Die Box installiert neue Versionen selbst, mit derselben `update.sh` wie der One-Click-Knopf und auf ihrem Kanal.
- **Nur wenn es nicht stört:** im wählbaren Zeitfenster (Standard 03–05 Uhr, Uhrzeit der Box), höchstens einmal am Tag und nur, wenn weder Übertragungen noch Uploads auf die Box noch Karten-Importe laufen. Über eine getaktete Verbindung (Handy per USB) erst nach ausdrücklicher Freigabe.
- **Meldet sich:** Erfolg oder Fehler kommt über Telegram, E-Mail und Webhook (Event `server.update`). Eine fehlgeschlagene Version wird übersprungen, bis eine neuere erscheint, statt jede Nacht neu zu scheitern.
- Neue Endpunkte `GET/PUT /api/updates/auto` (Admin), Änderungen stehen im Audit-Log. Details: [BETRIEB.md §9](docs/BETRIEB.md#9-updates).

## [0.7.7] – 2026-10-21

Boxen an einer gemeinsamen Leitung teilen sich die Bandbreite fair, und Limits greifen sofort auch bei laufenden Uploads. Wer per `https://<name>.local` arbeitet, installiert einmal das Root-Zertifikat der Box und hat danach Ruhe vor Zertifikatswarnungen.

### ⚖️ Pool: Bandbreite fair teilen

- **Eine Leitung, gerechte Anteile:** Unter **Pool → Gemeinsame Leitung** die Kapazität der Leitung (KB/s) eintragen und „Bandbreite fair teilen“ einschalten. Jede Minute bekommt jede sendende Box mit Rang den gleichen Anteil. Wer gerade nicht sendet, hat seinen Anteil schon bereit, damit ein Neuling nicht kurz alles belegt.
- **Gilt zusätzlich zum eigenen Limit** (das kleinere zählt) und **sofort, auch für laufende Uploads** (siehe unten).
- **Läuft von selbst ab:** Ohne Erneuerung durch den Hub fällt der Anteil nach 3 Minuten weg, keine Box bleibt dauerhaft gedrosselt. Ausschalten hebt ihn sofort auf.
- **Kombinierbar mit Vorrang:** Erst pausiert der Vorrang niedrigere Ränge, dann teilen sich die übrigen die Leitung.
- Braucht bei jeder Box mit Rang das Recht „steuern“. Die Box zeigt ihren Anteil unter **Bandbreite → Aktuelles Limit**, der Hub auf der Kachel. Neuer Endpunkt `POST /api/pool/limit`, Anteilsänderungen stehen im Audit-Log (die minütliche Erneuerung nicht).

### 🎚️ Limits greifen auch bei laufenden Uploads

- Bisher bekam rclone das Bandbreiten-Limit nur beim Start eines Uploads: Ein großes Drohnenvideo lief mit dem alten Tempo zu Ende, egal was sich änderte. Jetzt stellt die Box ein geändertes Limit **binnen ~5 Sekunden auch bei laufenden Uploads** um. Das gilt für den Pool-Anteil, eigene Limit-Änderungen und Zeitfenster aus dem Bandbreiten-Zeitplan.
- Technisch öffnet jeder Upload rclones Fernsteuerung (`rc`) nur auf `127.0.0.1`, mit einem zufälligen Einmal-Passwort, das über die Umgebung statt die Kommandozeile übergeben wird (nicht in `ps` sichtbar). Ist der Port im Moment des Starts belegt, läuft der Upload ohne Live-Limit statt fehlzuschlagen. Abschaltbar mit `OGC_RCLONE_LIVE_LIMITS=false`.
- Nebenbei: Harte rclone-Fehler (Level `critical`) erscheinen jetzt mit ihrem Text statt als „Upload fehlgeschlagen“.

### 🐛 Behoben

- **Anmeldung über `https://<name>.local` schlug plötzlich fehl, per IP ging es:** Das selbst ausgestellte Zertifikat der Box wird etwa alle 12 Stunden erneuert, eine einmal bestätigte Browser-Warnung gilt danach nicht mehr. Safari ließ dann jede Anfrage still scheitern, während der Service Worker die zwischengespeicherte Seite zeigte. Die Meldung „Anmeldung fehlgeschlagen“ wirkte wie ein falsches Passwort. Jetzt:
  - **Root-Zertifikat zum Download** unter **System → Erweitert → Zertifikat auf Geräten vertrauen**, als iOS/macOS-Profil oder `.crt`, mit SHA-256-Fingerabdruck. Einmal installiert, hält es 10 Jahre. Die Downloads sind auch ohne Anmeldung per `http://<name>.local` erreichbar.
  - **Gelber Hinweis „Box nicht erreichbar“**, sobald die Box nicht antwortet, mit Erklärung, Link zum Zertifikat und „Neu verbinden“ (setzt Service Worker und Cache zurück, damit der Browser die Warnung wieder zeigt).
  - **Login sagt, was los ist:** „Box nicht erreichbar – die Anmeldedaten wurden gar nicht geprüft“ statt „Anmeldung fehlgeschlagen“, auch bei Passkeys.

### 📄 Doku

- **BLE-Protokoll:** Die Whitelist gibt eine eng gefasste Pool-Auswahl für die Companion-App frei (Übersicht, Steuerung und Job-Liste der Peers, Kopplungscode, Discovery + Koppeln, gemeinsame Leitung). Die Zugangsverwaltung (Tokens, Peers per URL + Token) bleibt gesperrt.

## [0.7.6] – 2026-10-20

Uploads hängen nicht mehr stumm bei 0 %: Bei Verbindungsaussetzern macht der Browser dort weiter, wo die Box steht.

### 🐛 Behoben

- **Upload blieb stumm bei 0 % stehen:** Kam die Antwort der Box auf einen Block nicht an (WLAN-Aussetzer, hängende Verbindung), wartete der Browser ewig. Jede Anfrage hat jetzt ein Timeout (45 s ohne Fortschritt; ein langsamer, aber laufender Upload wird nie abgebrochen). Danach fragt der Browser die Box, wie weit sie ist, und macht genau dort weiter. Das passiert bis zu 5-mal mit wachsender Pause, der Ordner zeigt dabei „neuer Versuch 2/5 …“, erst danach erscheint eine Fehlermeldung. Der Fortschrittsbalken läuft jetzt auch innerhalb eines 4-MB-Blocks mit.
- **Abgebrochener Block konnte die Datei beschädigen:** Riss ein Block mittendrin ab, blieben die halb empfangenen Bytes in der Teildatei, und der wiederholte Block wurde dahinter angehängt. Die Box verwirft solche Reste jetzt vor jedem Block.

### 📄 Doku

- **BLE-Protokoll:** Ablehnungen sind an der Länge erkennbar, die Musterwahl der Box (IK vor IKpsk2) ist klargestellt.

## [0.7.5] – 2026-10-20

Pool-Boxen lassen sich vom Hub aus steuern, finden sich selbst und koppeln per Code. Dazu kommen ein Telegram-Assistent und ein Schaubild der Leitungen.

### 🛰️ Pool: Transfers aus der Ferne steuern

- **Vom Hub aus** lassen sich auf einer Peer-Box Uploads **pausieren/fortsetzen** und **alle fehlgeschlagenen Transfers neu anstoßen**, direkt auf der Kachel unter **Pool**.
- **Nur mit Recht „steuern“:** Das Token, das die Peer-Box dem Hub ausgestellt hat, muss dieses Recht haben (siehe unten). Standard ist „nur lesen“. Löschen oder Einstellungen ändern kann der Hub nicht.
- **Nachvollziehbar:** Jede Aktion steht im Audit-Log beider Boxen, auf dem Peer mit Token-Namen und Hostnamen des Hubs.
- **Auch lokal:** Die Seite **Transfers** hat jetzt „Uploads pausieren“ und „Alle fehlgeschlagenen erneut“. Ist die Box pausiert (von hier oder vom Hub), steht dort ein gelber Hinweis, **Bandbreite** nennt „Uploads manuell pausiert“ als Grund. Laufende Uploads werden beim Pausieren nicht abgebrochen.
- **Job-Liste eines Peers:** „Jobs ansehen“ auf der Kachel zeigt die Transfers der Box mit Datei, Ziel und **Fehlertext**, gefiltert nach Fehlgeschlagen/Wartend/Läuft/Alle. Mit Recht „steuern“ lässt sich jeder Job einzeln neu anstoßen. Für die eigene Box gibt es dieselbe Liste.
- Peers mit älterer Version zeigen keine Buttons bzw. melden „Update nötig“. Anleitung: [docs/MULTI-SERVER-POOL.md](docs/MULTI-SERVER-POOL.md#fernsteuerung-von-transfers).

### 🥇 Pool: Vorrang bei gemeinsamer Leitung

- **Reihenfolge für Boxen an einer Leitung:** Unter **Pool → Vorrang bei gemeinsamer Leitung** bekommt jede Box Rang 1 (hoch) bis 3 (niedrig) oder bleibt „nicht verwaltet“. Solange eine höherrangige Box senden will, pausiert der Hub die niedrigeren und setzt sie danach fort, z. B. ELW-Lagebilder vor Drohnen-Rohmaterial.
- **Nur für eine gemeinsame Leitung gedacht:** Boxen mit eigenem Uplink nicht eintragen, sonst verschenken sie Bandbreite.
- **Kein Gegeneinander:** Der Hub setzt nur fort, was er selbst pausiert hat, auch über einen Neustart hinweg. Von Hand pausierte Boxen bleiben pausiert. Setzt jemand eine Vorrang-Pause von Hand fort, lässt der Hub die Box in Ruhe, bis der Vorrang vorbei ist. Ausschalten gibt alles sofort frei.
- Braucht das Recht „steuern“ im Token des Peers (die Seite warnt, wo es fehlt). Reaktionszeit bis zu einer Minute, laufende Uploads werden nicht abgebrochen. Aktionen erscheinen in den Pool-Meldungen und auf den Kacheln („Rang 3“, „pausiert (Vorrang)“).

### 🛰️ Pool: gebündelte Meldungen vom Hub

- **Eine Nachricht statt vieler:** Der Hub prüft seine Peers jede Minute und schickt Änderungen gesammelt über Telegram, E-Mail oder Webhook: Box nicht erreichbar (erst nach 2 Fehlversuchen) bzw. wieder da, Speicher knapp, Uploads pausiert/fortgesetzt, neue fehlgeschlagene Transfers.
- **Nur Wechsel, kein Dauerfeuer:** Eine Box, die offline bleibt, wird einmal gemeldet. Fehlschläge melden sich erst bei einem neuen Höchststand oder nach einer Stunde Ruhe, sonst käme bei einem dauerhaft kaputten Ziel durch die automatische Wiederholung alle 15 Minuten dieselbe Nachricht. Ein Neustart des Hubs meldet keine alten Zustände nach.
- Schalter unter **System → Benachrichtigungen → „Pool-Änderungen (nur am Hub)“** (Standard: an) und in der Box-Konsole. Empfehlung: Kanäle nur am Hub einrichten.
- Verständlichere Fehlertexte für Peers: „Verbindung abgelehnt – läuft OffgridCloud dort, stimmt der Port?“ und „keine Antwort (Zeitüberschreitung)“ statt `[Errno 111] Connection refused`.

### 📡 Pool: Boxen finden sich selbst und koppeln per Code

- **Automatisch gefunden:** Boxen im selben Netz kündigen sich per mDNS an (`_offgridcloud._tcp`). Unter **Pool → In der Nähe gefunden** stehen sie mit Name, Version und Adresse, bereits gekoppelte sind markiert.
- **Koppeln per Code statt Token kopieren:** An der Peer-Box **„Kopplungscode erzeugen“** (oder an der Box-Konsole *Kopplungscode für einen Hub anzeigen*), am Hub den 6-stelligen Code eingeben, fertig. Der Code gilt 10 Minuten, einmal, und ist nach 5 Fehlversuchen verbrannt. Kopplung gibt nur Leserecht, „steuern“ vergibt die Peer-Box selbst.
- **Sicher trotz offenem mDNS:** Die Liste macht nur Vorschläge, Zugriff gibt es erst mit dem Code von der echten Box. Ein gespeichertes Token geht nie an eine nur per mDNS gelernte Adresse.
- Boxen nicht in der Liste (anderes Subnetz) lassen sich per Adresse + Code koppeln. Ankündigung abschaltbar unter Pool oder per `OGC_MDNS_ENABLED=false`.
- **Docker:** Finden klappt nur mit `network_mode: host` (Hinweis in `docker-compose.yml`), sonst bleibt die Liste leer und die Seite sagt warum.
- Neue Abhängigkeiten: `zeroconf` und `ifaddr` (fertige Pakete für Pi 3/4/5 und x86).

### 🔑 Pool: ein Token pro Hub

- **Jeder Hub bekommt sein eigenes Token** (**Pool → Zugriff für Hubs auf diese Box**), mit Namen und Recht *nur lesen* oder *lesen + steuern*. *Nur lesen* umfasst die Job-Liste, also auch Dateinamen und Fehlertexte. Einen Hub sperren heißt sein Token löschen, die anderen Hubs bleiben verbunden. Bisher teilten sich alle Hubs ein Token, und Sperren hieß: neu erzeugen und überall neu eintragen.
- **Sieht, wer abfragt:** Pro Token steht, wann und von welchem Hub es zuletzt benutzt wurde.
- **Nur noch als Hash gespeichert:** Die Box hält ausgestellte Tokens nur als SHA-256, der Klartext erscheint einmal beim Erzeugen. Das alte Token lag im Klartext in der Datenbank und war im Web-UI erneut abrufbar.
- **Umstieg ohne Ausfall:** Das bisherige gemeinsame Token wird beim Start als „Bisheriges Token“ (*nur lesen*) übernommen, Hubs damit laufen weiter. Empfehlung: jedem Hub ein eigenes Token ausstellen und das bisherige danach löschen.
- **API:** `/api/pool/token` und `/api/pool/self` entfallen, neu ist `/api/pool/tokens`. Die Konsole an der Box ist angepasst.

### 🧭 Telegram: Einrichtungsassistent

- **Geführte Einrichtung** unter **System → Benachrichtigungen → Telegram → Einrichtungsassistent**, in vier Schritten: Bot anlegen, Token, Chat, fertig.
- **Namensvorschläge für @BotFather:** Aus dem Namen der Box (oder einem eingegebenen Team-/Box-Namen wie „Boot Nord“) schlägt der Assistent Anzeigenamen und Benutzernamen vor, die Telegrams Regeln einhalten (5–32 Zeichen, a–z/0–9/_, endet auf `bot`, Umlaute werden umschrieben). Weil Benutzernamen weltweit eindeutig sind, ist immer eine Variante mit kurzem, box-stabilem Zusatz dabei (`ogc_boot_nord_d735_bot`). Nichtssagende Hostnamen (`raspberrypi`, Docker-Container-ID …) werden dafür nicht verwendet. Jeder Schritt bei @BotFather hat einen Kopieren-Knopf, funktioniert auch über einfaches HTTP.
- **Token-Prüfung** per `getMe`, bevor gespeichert wird: zeigt, zu welchem Bot der Token gehört, und erkennt Tippfehler, unbekannte Tokens und gesperrte Gruppen. Offline lässt er sich trotzdem ungeprüft speichern.
- **Chat per Klick statt Chat-ID abtippen:** Der Assistent verlinkt direkt auf den Bot (Privatchat) bzw. „zu Gruppe hinzufügen“ und listet live alle Chats, die dem Bot gerade geschrieben oder ihn hinzugefügt haben. Ein Klick auf „Übernehmen“ speichert den Chat, der Bot begrüßt ihn sofort. Die Chat-ID von Hand einzutragen geht weiterhin.
- Neue Token- oder Chat-Einstellungen gelten sofort: Der Bot wartet nicht mehr bis zu 30 s (bzw. 5 min nach einem falschen Token), bis er sie übernimmt.

### 🔀 Leitungen auf einen Blick

- **Schaubild „Box → Leitungen → Internet“** unter **Netzwerk** (sobald mehr als eine Leitung da ist) und unter **Bandbreite → Leitungsbündelung**: Jede Leitung (Kabel, WLAN, Handy) mit Status *aktiv*, *gebündelt*, *Reserve* oder *nicht gebündelt* samt Grund. Leitungen, über die gerade Uploads laufen, sind animiert.
- **Bündelung ausgesetzt?** Das Schaubild sagt jetzt, warum Uploads trotz eingeschalteter Bündelung direkt gehen, z. B. weil ein Handy steckt und getaktete Verbindungen auf „pausieren“ oder „Tagesvolumen“ stehen.
- **USB-Tethering ist nicht mehr zu übersehen:** Unter **Netzwerk** ein deutlicher Hinweis, ob das Handy die Box gerade online hält, mitgebündelt wird oder nur als Reserve steckt, inklusive der geltenden Upload-Regel. Im Dashboard erscheint ein Hinweis, solange Uploads über eine getaktete Verbindung laufen.

### 🐛 Behoben

- **Safari: „FetchEvent.respondWith received an error: Returned response is null“ auf `https://<name>.local`:** Der Service Worker hat bei einer fehlgeschlagenen Anfrage ohne Cache-Treffer eine leere Antwort geliefert. Ausgelöst wird das, weil Safari die Ausnahme für das selbstsignierte Zertifikat nicht auf Anfragen des Service Workers anwendet. Der Service Worker prüft jetzt beim Installieren, ob er die Box erreicht, und meldet sich sonst selbst ab (auch eine bereits aktive ältere Version). Er liefert nie mehr eine leere Antwort und legt die App-Shell vorab in den Cache. Per IP (HTTP) war die Box nicht betroffen.
- **`sudo /opt/offgridcloud/deploy/netmerge/install.sh`: command not found:** Die Bandbreiten-Seite verweist auf dieses Skript, der Installer hat es aber nur kopiert, wenn die Bündelung schon beim Installieren gewählt war. Es liegt jetzt immer dort (ausgeführt wird es weiterhin nur mit der Option).
- **„Fehler“ nach „Als Puffer nutzen“:** Die Speicher-Seite meldete ein nacktes „Fehler“, obwohl das Laufwerk korrekt als Puffer eingerichtet war (leere Antwort der Box wurde als JSON gelesen).

### 📄 Doku

- **BLE-Protokoll v1** für die Companion-App spezifiziert, mit Testvektoren: [docs/BLE-PROTOKOLL.md](docs/BLE-PROTOKOLL.md).

## [0.7.1] – 2026-10-20

Mehrere Boxen mit Telegram vertragen sich jetzt: Die Box erkennt einen geteilten Bot, und Befehle lassen sich gezielt an eine Box richten.

### 🤖 Telegram: mehrere Boxen sauber trennen

- **Zwei Boxen, ein Bot-Token** gehen nicht: Telegram lässt pro Bot nur einen Empfänger Befehle abholen, die Boxen nehmen sich gegenseitig die Befehle weg. Die Box erkennt das jetzt (`409 Conflict` von einem anderen `getUpdates`), zeigt unter **System → Benachrichtigungen → Telegram** einen Hinweis und wartet 2 min statt 30 s, bevor sie es erneut versucht. Abhilfe: für jede Box bei @BotFather einen eigenen Bot anlegen.
- Bisher hat die Box in diesem Fall fälschlich einen Webhook vermutet und `deleteWebhook` aufgerufen. Das passiert jetzt nur noch, wenn tatsächlich ein Webhook gesetzt ist.
- **Mehrere Boxen in einer Gruppe:** Jede Box hat ihren eigenen Bot. Ein Befehl mit Bot-Namen (`/status@Box2_bot`) wird nur noch von diesem Bot beantwortet, die anderen schweigen. Ohne Namen (`/status`) antworten weiterhin alle.

### 🐛 Behoben

- **VPN in Docker:** Der Hinweis auf fehlende Rechte zeigte `docker run … offgridcloud` mit Auslassungspunkten, wörtlich kopiert scheiterte das mit „invalid reference format“. Jetzt stehen zuerst die zwei VPN-Zeilen für `docker-compose.yml` und `docker compose up -d --force-recreate` zum Kopieren da. Ohne compose nennt der Hinweis die Flags `--cap-add=NET_ADMIN --device=/dev/net/tun`, die zu den bisherigen Optionen dazukommen. (#178)

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
