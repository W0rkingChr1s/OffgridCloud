# OffgridCloud OS — die Konsole an der Box

Normalerweise wird OffgridCloud übers Netzwerk bedient: Handy oder Laptop öffnen
`http://<box-ip>:8000`. Hängt aber ein **Bildschirm direkt an der Box** (HDMI),
soll er nicht in einem nackten Linux-Login landen, sondern **nur das
OffgridCloud-Menü** zeigen — die Box fühlt sich wie ein fertiges Gerät an, nicht
wie ein Bastel-Pi.

Genau das ist die On-Box-Konsole (das „OffgridCloud OS"):

1. Beim Start übernimmt ein **Vollbild-Menü die primäre Konsole (tty1)**. Wer auf
   den Bildschirm schaut, sieht Live-Status und ein paar Geräte-Aktionen — sonst
   nichts.
2. Das darunterliegende **Raspberry Pi OS** ist weiterhin erreichbar, aber
   **geschützt**: über den Menüpunkt „Zur Raspberry-Pi-Shell" nur mit **Admin-PIN**.
3. Als Sicherheitsnetz bleiben die übrigen Text-Konsolen (**Strg+Alt+F2** …
   **F6**) mit dem normalen Login bestehen — so sperrt man sich nie aus.

Optional kann das Menü auch die **volle Web-Oberfläche im Vollbild-Chromium**
öffnen (für einen Pi 4/5 mit Desktop-Leistung).

## Was das Menü zeigt und kann

**Live-Status** (alles lokal ermittelt, ohne Login):

- Gerätename und IP-Adresse(n), die **Web-Oberflächen-URL** zum Abtippen
- **Dienst**-Status (`active`) und ob der lokale Health-Endpoint antwortet
- Version, ob **rclone** (die Cloud-Engine) verfügbar ist
- **Puffer-Speicher** (belegt/frei) — dieselbe Platte, auf die das Feld lädt
- **VPN** (aktives `wg`/`tun`-Interface) und **WLAN-Rückfallebene** (Watchdog)

**Aktionen:**

| Menüpunkt | Wirkung |
|---|---|
| Status aktualisieren | Werte neu einlesen (passiert auch alle 5 s automatisch) |
| Admin-Zugang anzeigen | URL + Admin-E-Mail einblenden (Passwort steht im Installations-Protokoll) |
| **Einstellungen (Admin)** | **Alle Einstellungen direkt an der Box** — siehe unten |
| Web-Oberfläche im Browser öffnen | Nur wenn Chromium installiert ist (siehe unten) |
| OffgridCloud-Dienst neu starten | `systemctl restart offgridcloud` (mit Rückfrage) |
| Box neu starten / herunterfahren | `reboot` / `poweroff` (mit Rückfrage) |
| Zur Raspberry-Pi-Shell (PIN) | Nach korrekter PIN eine Root-Shell; `exit` führt zurück ins Menü |

Bedienung: **Pfeiltasten** (oder `j`/`k`) wählen, **Enter** bestätigt.

## Einstellungen (Admin) — ohne Web-UI

Unter **„Einstellungen (Admin)"** lässt sich die Box **komplett lokal** einrichten,
ohne ein zweites Gerät. Beim ersten Aufruf fragt die Konsole einmal pro Sitzung
nach **Admin-E-Mail und Passwort** (dieselben Zugangsdaten wie in der Web-UI) —
denn intern steuert die Konsole **dieselbe lokale API** wie die Weboberfläche.
So gibt es keine doppelte Logik, und Konsole und Web-UI bleiben immer synchron.

Verfügbare Bereiche:

- **System & Benachrichtigungen** — Lösch-/Sync-Regeln, Mess-URL der Bandbreiten-
  Probe, sowie alle Alert-Kanäle (Webhook, **Telegram**, **E-Mail/SMTP**) und die
  Ereignis-Schalter (Empfang, fertig, fehlgeschlagen, wenig Speicher, Start,
  Wieder-online, Bandbreite). Jede Zeile mit **Enter** umschalten/ändern.
- **Cloud-Ziele (Provider)** — vorhandene Ziele anzeigen, **Verbindung testen**,
  **löschen** und **neu anlegen**. Das Formular wird dynamisch aus dem Provider-
  Katalog gebaut (S3, MinIO, Azure, WebDAV, SFTP, Nextcloud … — alle Typen der
  Web-UI, inkl. der jeweils passenden Felder und Passwort-Eingaben).
- **VPN** — WireGuard/OpenVPN-Profile **verbinden/trennen/löschen** und ein neues
  Profil **aus einer Datei** (`.conf`/`.ovpn`) anlegen.
- **Netzwerk (WLAN / Fallback-AP)** — Fallback-AP + Watchdog einstellen, bekannte
  **WLANs hinzufügen/löschen**, einen **WLAN-Scan** ausführen und die Konfiguration
  **anwenden** (`Jetzt anwenden`).

Sicherheit: Passwörter/Tokens sind reine Schreibfelder — die Konsole zeigt nur
„gesetzt/leer" an, nie den Klartext (genau wie die API). Die Anmeldung gilt nur
für die laufende Sitzung; „Abmelden" verwirft das Token sofort.

## Warum eine Text-Konsole (und nicht sofort ein Browser)

OffgridCloud zielt auf den **Raspberry Pi 3** und läuft dort mit ~150–250 MB RAM.
Ein Vollbild-Chromium bräuchte X und noch einmal mehrere Hundert MB — auf einem
Pi 3 zäh. Die Konsole ist **reines Python 3 (curses), ohne Fremd-Abhängigkeiten**
und ohne Desktop; sie fragt nur `systemctl`/`ip`/`hostname` und den lokalen
`/api/health` ab. Der Browser-Kiosk ist deshalb **optional** und eher für Pi 4/5.

## Installation

Am einfachsten direkt beim Einrichten der Box: Der Installer **fragt danach**.

```bash
sudo ./deploy/install.sh
```

Auf die Frage „**OffgridCloud-OS-Menü am Bildschirm der Box (Kiosk)?**" mit **ja**
antworten. Danach folgen zwei Unterfragen: ob zusätzlich der **Vollbild-Browser**
(Chromium, eher Pi 4/5) installiert werden soll, und die **Admin-PIN** (leer
lassen → es wird eine zufällige 6-stellige PIN erzeugt und einmalig angezeigt).

Unbeaufsichtigt (z. B. über den One-Liner) geht es per Umgebungs­variablen:

```bash
curl -fsSL https://raw.githubusercontent.com/W0rkingChr1s/OffgridCloud/main/deploy/bootstrap.sh \
  | sudo OGC_NONINTERACTIVE=1 OGC_WITH_KIOSK=1 bash
# zusätzlich Browser + eigene PIN:
#   ... | sudo OGC_NONINTERACTIVE=1 OGC_WITH_KIOSK=1 OGC_WITH_CHROMIUM_KIOSK=1 OGC_KIOSK_PIN=4242 bash
```

Nachträglich auf einer bereits installierten Box:

```bash
sudo /opt/offgridcloud/deploy/kiosk/install.sh            # bzw. --with-chromium
```

Der Installer

1. legt das Konsolen-Programm + die **systemd-Unit `offgrid-kiosk.service`** ab,
   die tty1 übernimmt,
2. **maskiert `getty@tty1`**, damit Login-Prompt und Menü sich nicht um den
   Bildschirm streiten,
3. stellt die Box auf **Konsolen-Boot** um (`systemctl set-default
   multi-user.target`) und **deaktiviert den Display-Manager** (LightDM/GDM/…) —
   sonst würde auf einem Pi-OS-*mit-Desktop* der Desktop den Bildschirm greifen,
   und das Menü liefe unsichtbar dahinter,
4. setzt die **Admin-PIN** (vorgegeben oder zufällig, einmalig angezeigt) und
5. installiert bei Bedarf einen minimalen **X + Chromium**-Stack.

> **Nach der Installation einmal neu starten** (`sudo reboot`) — die Umstellung
> weg vom Desktop greift erst beim nächsten Boot. Der vorherige Zustand (Boot-Ziel
> + Display-Manager) wird gesichert und von `uninstall.sh` wiederhergestellt.

## Die Admin-PIN

Die PIN schützt **nur den Sprung in die OS-Shell** aus dem Menü — Neustart und
Herunterfahren sind normale Geräte-Funktionen und brauchen keine PIN (nur eine
Rückfrage). Gespeichert wird ausschließlich ein **gesalzener PBKDF2-Hash** unter
`/opt/offgridcloud/data/kiosk.pin` (Modus `600`), nie die PIN selbst.

Ändern (fragt zweimal, verdeckt):

```bash
sudo /opt/offgridcloud/deploy/kiosk/set-pin.sh
```

**PIN vergessen?** Kein Problem — über **Strg+Alt+F2** an einer normalen Konsole
anmelden und die PIN mit dem Befehl oben neu setzen (oder die Datei
`kiosk.pin` löschen; dann ist der Shell-Sprung gesperrt, bis eine neue PIN
gesetzt ist).

## Der optionale Browser-Kiosk

Mit `--with-chromium` erscheint im Menü „**Web-Oberfläche im Browser öffnen**".
Der Punkt startet Chromium im Kiosk-Modus (`--kiosk`, Inkognito) per `xinit` auf
derselben Konsole; **das Fenster zu schließen führt zurück ins Menü**. Chromium
wird **auf Abruf** gestartet, nicht beim Booten — so bleibt die leichte Konsole
das Gesicht der Box.

## Der Weg ins Raspberry Pi OS

Es gibt bewusst **zwei** Wege — einer bequem, einer als Netz:

- **Aus dem Menü:** „Zur Raspberry-Pi-Shell (PIN)" → PIN eingeben → Root-Shell.
  `exit` (oder `logout`) bringt das Menü zurück. tty1 bleibt die ganze Zeit unter
  Kontrolle des Dienstes, deshalb landet man nach dem Shell-Ende **immer** wieder
  im Menü.
- **Direkt:** **Strg+Alt+F2** wechselt auf tty2 mit dem normalen Login (Benutzer
  + Passwort des Raspberry Pi OS). Zurück zum Menü mit **Strg+Alt+F1**.

> Wer die Box maximal abschotten will, kann zusätzlich die Gettys auf tty2–tty6
> deaktivieren (`sudo systemctl mask getty@tty2.service` …). Dann bleibt nur noch
> der PIN-Weg **und SSH** — vorher sicherstellen, dass SSH funktioniert, sonst
> ist die Box ohne Bildschirm-PIN nicht mehr erreichbar.

## Fernzugriff (SSH / Raspberry Pi Connect)

Das Menü ist eine **lokale** Konsole auf tty1. Es lässt sich aber auch aus der
Ferne öffnen — der Installer legt dafür den Befehl **`offgrid-console`** nach
`/usr/local/bin`:

```bash
sudo offgrid-console            # das komplette Menü in der aktuellen Sitzung
sudo offgrid-console --set-pin  # PIN ändern
```

Weil das Programm seinen echten Pfad selbst auflöst, funktioniert der Befehl auch
über den Symlink korrekt (findet `.env` und die PIN-Datei). Er startet eine
**eigene** Menü-Instanz — nicht die tty1-Sitzung gespiegelt —, alle Aktionen
(Status, Neustart, Herunterfahren, PIN→Shell) wirken aber identisch.

### Raspberry Pi Connect

- **Bildschirmfreigabe / Remote-Desktop:** funktioniert **nicht** mit dem Kiosk.
  Pi Connects Screen-Sharing braucht einen **Wayland-Desktop** (nicht Pi OS Lite /
  Konsole) und erfasst nur den Desktop, **nicht** die Text-Konsole tty1 — genau
  die haben wir für den Kiosk abgeschaltet.
- **Remote-Shell (Terminal im Browser):** funktioniert. Sie öffnet eine frische
  Shell (nicht die tty1-Ansicht) — dort einfach `sudo offgrid-console` eintippen.
- **Headless-Hinweis:** Da die Box jetzt ohne Desktop bootet, die Shell-Variante
  **`rpi-connect-lite`** verwenden, und den Connect-User-Dienst so einrichten,
  dass er ohne interaktiven Login startet:

  ```bash
  loginctl enable-linger "$USER"
  systemctl --user enable rpi-connect
  ```

  Ohne „Linger" ist die Box über Connect erst erreichbar, nachdem sich der
  Benutzer einmal (z. B. auf tty2) angemeldet hat.

Über **SSH** gilt dasselbe: nach dem Login `sudo offgrid-console` öffnet das Menü.

## Deinstallation

`deploy/uninstall.sh` räumt die Konsole automatisch mit ab: der Dienst wird
gestoppt und entfernt, `getty@tty1` wieder **entmaskiert** und gestartet, und der
vorherige Boot-Zustand (Desktop-Ziel + Display-Manager) aus
`data/kiosk-boot.state` **wiederhergestellt** — die Box bootet danach wieder wie
zuvor. Nur die Konsole von Hand entfernen:

```bash
sudo systemctl disable --now offgrid-kiosk.service
sudo rm -f /etc/systemd/system/offgrid-kiosk.service
sudo systemctl unmask getty@tty1.service
# Desktop wieder aktivieren (falls vorher ein Desktop lief):
sudo systemctl set-default graphical.target
sudo systemctl enable display-manager.service      # bzw. lightdm/gdm3
sudo systemctl daemon-reload && sudo reboot
```

## Fehlersuche

- **Der Pi bootet trotzdem in den Desktop:** Die Box startet noch in
  `graphical.target`, und ein Display-Manager greift sich den Bildschirm. Prüfen
  und umstellen:

  ```bash
  systemctl get-default                     # sollte multi-user.target sein
  sudo systemctl set-default multi-user.target
  sudo systemctl disable --now display-manager.service   # bzw. lightdm/gdm3
  sudo reboot
  ```

  Der Installer macht das eigentlich selbst (Schritt 3) — nach der Erst­installation
  ist aber **ein Neustart nötig**, damit es greift.
- **Bildschirm bleibt schwarz / kein Menü:** Läuft der Dienst?
  `journalctl -u offgrid-kiosk -e`. Häufig fehlt `TERM=linux` (setzt die Unit) —
  oder tty1 gehört noch dem alten Getty: `systemctl is-enabled getty@tty1`
  sollte `masked` sein.
- **„Kein Browser-Kiosk installiert":** Chromium/X fehlen — mit
  `--with-chromium` nachinstallieren.
- **PIN akzeptiert nicht:** neue PIN setzen (`set-pin.sh`) — der Hash-Speicher
  liegt in `data/kiosk.pin`.
- **Aussperren unmöglich?** Solange die Gettys auf tty2–tty6 laufen, kommt man
  per **Strg+Alt+F2** immer noch ins System.
