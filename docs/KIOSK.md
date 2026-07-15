# OffgridCloud OS — die Konsole an der Box

Normalerweise wird OffgridCloud übers Netzwerk bedient: Handy oder Laptop öffnen
`http://<box-ip>:8000`. Hängt aber ein **Bildschirm direkt an der Box** (HDMI),
soll er nicht in einem nackten Linux-Login landen, sondern **nur das
OffgridCloud-Menü** zeigen — die Box fühlt sich wie ein fertiges Gerät an, nicht
wie ein Bastel-Pi.

Genau das ist die On-Box-Konsole (das „OffgridCloud OS"):

1. Beim Start übernimmt eine **getabbte Dashboard-App die primäre Konsole (tty1)**.
   Wer auf den Bildschirm schaut, sieht zuerst das **Dashboard** mit Live-Status —
   und kann über **Tabs** die ganze Box verwalten. Sonst nichts.
2. Das darunterliegende **Raspberry Pi OS** ist weiterhin erreichbar, aber
   **geschützt**: über System → „Zur Raspberry-Pi-Shell" nur mit **Admin-PIN**.
3. Als Sicherheitsnetz bleiben die übrigen Text-Konsolen (**Strg+Alt+F2** …
   **F6**) mit dem normalen Login bestehen — so sperrt man sich nie aus.

Dieselbe App erscheint auch beim **SSH-Login** (siehe „Fernzugriff"), und optional
lässt sich die **volle Web-Oberfläche im Vollbild-Chromium** öffnen (Pi 4/5).

## Dashboard & Tabs

Oben läuft eine **Tab-Leiste**; gewechselt wird mit **←/→** oder den Zahlen **1–6**.
Innerhalb eines Tabs: **↑/↓** wählen, **Enter** öffnen, **Esc** zurück.

- **1 · Dashboard** — Live-Status (ohne Login): Gerät/IP, **Web-URL**, Dienst-Status,
  Version, rclone, **Puffer-Speicher**, VPN, WLAN-Rückfallebene. Auto-Refresh alle 5 s.
- **2 · Provider** — Cloud-Ziele anzeigen, **testen**, **löschen**, **neu anlegen**
  (Formular dynamisch aus dem Provider-Katalog: S3, MinIO, Azure, WebDAV, SFTP,
  Nextcloud … alle Typen der Web-UI, inkl. der passenden Felder & Passwörter).
- **3 · Netz & Verbindung** — **WLAN & Fallback-AP** (AP/Watchdog, bekannte WLANs
  hinzufügen/löschen, Scan, „Anwenden"), **Bandbreite** (Limit + Mindest-Gate) und
  **VPN** (Profile verbinden/trennen/löschen, neues Profil aus `.conf`/`.ovpn`).
- **4 · Pool** — Pool-Übersicht (dieser Knoten + Peers), diesen Knoten **teilbar**
  machen (Token erzeugen/anzeigen/entfernen), **Peers** (andere Boxen) verwalten.
- **5 · Benutzer & Gruppen** — Benutzer anlegen/löschen, Passwort zurücksetzen,
  aktivieren/deaktivieren, Rolle wechseln; Gruppen anlegen/löschen und **Mitglieder**
  per Häkchenliste verwalten.
- **6 · System** — **Version & Updates** (prüfen und, wenn eingerichtet, per
  One-Click aktualisieren), **Speicherbelegung**, **Systemeinstellungen &
  Benachrichtigungen** (Lösch-/Sync-Regeln, Bandbreiten-Mess-URL, Webhook/Telegram/
  SMTP + alle Ereignis-Schalter), **Dienst neu starten / Neustart / Herunterfahren**,
  optional **Browser**, und **Zur Raspberry-Pi-Shell (PIN)**.

## Admin-Login & Sicherheit

Das **Dashboard** und die Geräte-Aktionen (Neustart/Herunterfahren) brauchen keinen
Login. Sobald man einen **Verwaltungs-Bereich** öffnet (Provider, Netz, Pool,
Benutzer, System-Einstellungen/Updates), fragt die Konsole **einmal pro Sitzung**
nach **Admin-E-Mail und Passwort** — dieselben Zugangsdaten wie in der Web-UI.

Der Grund: die Konsole baut die Web-UI **nicht nach**, sondern steuert **dieselbe
lokale REST-API**. So gibt es keine doppelte Logik, und Konsole und Web-UI bleiben
immer synchron. Passwörter/Tokens sind reine **Schreibfelder** — angezeigt wird nur
„gesetzt/leer", nie Klartext (wie in der API).

> Voraussetzung für die Verwaltungs-Tabs ist ein **laufender Dienst** (die Konsole
> spricht `http://127.0.0.1:<port>`). Dashboard, Neustart und Herunterfahren gehen
> auch, wenn der Dienst aus ist.

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

- **Aus der App:** Tab **System** → „Zur Raspberry-Pi-Shell (PIN)" → PIN eingeben →
  Root-Shell. `exit` (oder `logout`) bringt die App zurück. tty1 bleibt die ganze
  Zeit unter Kontrolle des Dienstes, deshalb landet man nach dem Shell-Ende
  **immer** wieder im Dashboard.
- **Direkt:** **Strg+Alt+F2** wechselt auf tty2 mit dem normalen Login (Benutzer
  + Passwort des Raspberry Pi OS). Zurück zum Menü mit **Strg+Alt+F1**.

> Wer die Box maximal abschotten will, kann zusätzlich die Gettys auf tty2–tty6
> deaktivieren (`sudo systemctl mask getty@tty2.service` …). Dann bleibt nur noch
> der PIN-Weg **und SSH** — vorher sicherstellen, dass SSH funktioniert, sonst
> ist die Box ohne Bildschirm-PIN nicht mehr erreichbar.

## Fernzugriff (SSH / Raspberry Pi Connect)

Die Dashboard-App läuft auf tty1 — erscheint aber **auch beim SSH-Login**
automatisch: Der Installer legt ein `profile.d`-Snippet an, das interaktive
**SSH-Sitzungen** direkt auf dem OffgridCloud-Dashboard landen lässt (statt auf
einem nackten Prompt). **`q` beendet** die App und führt zur normalen Shell —
über SSH bist du ja bereits angemeldet, deshalb gibt es hier **keine Sperre**
(anders als am tty1-Kiosk, wo der Shell-Sprung PIN-geschützt ist). `scp`/`sftp`/
`rsync` sind nicht interaktiv und werden übersprungen; lokale tty-Logins behalten
ihre normale Shell.

Manuell öffnen geht immer über den Befehl **`offgrid-console`** (vom Installer nach
`/usr/local/bin` gelegt):

```bash
offgrid-console            # Dashboard in der aktuellen Sitzung (q beendet)
sudo offgrid-console       # als root — für Neustart/Herunterfahren/PIN-Shell
sudo offgrid-console --set-pin
```

Weil das Programm seinen echten Pfad selbst auflöst, funktioniert der Befehl auch
über den Symlink korrekt (findet `.env` und die PIN-Datei). Hinweis: Geräte-
Aktionen (Dienst-Neustart, Reboot, Shutdown) brauchen root — per SSH also
`sudo offgrid-console`. Die Verwaltung (Provider, Netz, Pool, Benutzer, System)
läuft über den Admin-Login der API und funktioniert auch ohne root.

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
