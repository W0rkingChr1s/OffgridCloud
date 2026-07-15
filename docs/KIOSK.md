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
| Web-Oberfläche im Browser öffnen | Nur wenn Chromium installiert ist (siehe unten) |
| OffgridCloud-Dienst neu starten | `systemctl restart offgridcloud` (mit Rückfrage) |
| Box neu starten / herunterfahren | `reboot` / `poweroff` (mit Rückfrage) |
| Zur Raspberry-Pi-Shell (PIN) | Nach korrekter PIN eine Root-Shell; `exit` führt zurück ins Menü |

Bedienung: **Pfeiltasten** (oder `j`/`k`) wählen, **Enter** bestätigt.

## Warum eine Text-Konsole (und nicht sofort ein Browser)

OffgridCloud zielt auf den **Raspberry Pi 3** und läuft dort mit ~150–250 MB RAM.
Ein Vollbild-Chromium bräuchte X und noch einmal mehrere Hundert MB — auf einem
Pi 3 zäh. Die Konsole ist **reines Python 3 (curses), ohne Fremd-Abhängigkeiten**
und ohne Desktop; sie fragt nur `systemctl`/`ip`/`hostname` und den lokalen
`/api/health` ab. Der Browser-Kiosk ist deshalb **optional** und eher für Pi 4/5.

## Installation

Am einfachsten direkt beim Einrichten der Box mitinstallieren:

```bash
# nur die leichte Text-Konsole (empfohlen, auch für den Pi 3)
sudo ./deploy/install.sh --with-kiosk

# zusätzlich den optionalen Vollbild-Browser (Pi 4/5)
sudo ./deploy/install.sh --with-chromium-kiosk

# eigene PIN vorgeben (sonst wird eine zufällige 6-stellige einmalig angezeigt)
sudo ./deploy/install.sh --with-kiosk --kiosk-pin 4242
```

Beim One-Liner werden die Flags nach `--` durchgereicht:

```bash
curl -fsSL https://raw.githubusercontent.com/W0rkingChr1s/OffgridCloud/main/deploy/bootstrap.sh \
  | sudo bash -s -- --with-kiosk
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
3. setzt die **Admin-PIN** (vorgegeben oder zufällig, einmalig angezeigt) und
4. installiert bei `--with-chromium` einen minimalen **X + Chromium**-Stack.

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

## Deinstallation

`deploy/uninstall.sh` räumt die Konsole automatisch mit ab: der Dienst wird
gestoppt und entfernt, `getty@tty1` wieder **entmaskiert** und gestartet — die
Box zeigt danach wieder den normalen Login. Nur die Konsole entfernen:

```bash
sudo systemctl disable --now offgrid-kiosk.service
sudo rm -f /etc/systemd/system/offgrid-kiosk.service
sudo systemctl unmask getty@tty1.service
sudo systemctl daemon-reload && sudo systemctl start getty@tty1.service
```

## Fehlersuche

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
