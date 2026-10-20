# Fertiges Image für den Raspberry Pi

Statt Raspberry Pi OS zu installieren und danach den Installer laufen zu lassen,
kann man ein **fertiges OffgridCloud-Image** auf die SD-Karte schreiben. Es
basiert auf Raspberry Pi OS Lite (64 Bit, Debian Trixie) und hat OffgridCloud
schon vorinstalliert, mit HTTPS, Video-Thumbnails, Speedtest-CLI und
WLAN-Rückfallebene.

Unterstützt: **Raspberry Pi 3B/3B+, 4, 5, Zero 2 W** (alles mit 64-Bit-CPU).

> Für x86-Server, VMs und andere ARM-Boards gibt es kein Image. Dort nimmst du
> den [One-Liner-Installer](BETRIEB.md) oder Docker.

## 1. Flashen

1. Das Image `offgridcloud-<version>-rpi-arm64.img.xz` aus dem neuesten
   [Release](https://github.com/W0rkingChr1s/OffgridCloud/releases) laden.
2. Im **Raspberry Pi Imager** „Eigenes Image verwenden“ wählen und es auf die
   SD-Karte schreiben. Optional lassen sich im Imager WLAN, SSH und ein
   Benutzer für den Shell-Zugang hinterlegen. OffgridCloud selbst braucht
   nichts davon.
3. **Optional, aber empfohlen:** Danach die Datei
   [`offgridcloud-setup.txt`](../deploy/setup/offgridcloud-setup.txt.example)
   auf die Boot-Partition der Karte legen (das FAT-Laufwerk, das dein Rechner
   anzeigt):

   ```ini
   SETUP_CODE=7391-kaffee          # der Assistent verlangt diesen Code
   SETUP_WIFI_PASSWORD=            # Setup-WLAN mit Passwort statt offen (8–63 Zeichen)
   COUNTRY=DE                      # WLAN-Land
   ```

   Die Box liest die Datei beim ersten Start und **löscht sie danach** von der Karte.

## 2. Einrichten

Beim ersten Start erzeugt die Box ihre eigenen Geheimnisse (`OGC_SECRET_KEY`)
und geht in den **Setup-Modus**. Bis die Einrichtung abgeschlossen ist, ist
außer dem Assistenten nichts erreichbar. Es gibt kein Standard-Passwort.

| Wie ist die Box verbunden? | So erreichst du den Assistenten |
|---|---|
| Netzwerkkabel (oder im Imager hinterlegtes WLAN) | `http://offgridcloud-setup.local` (oder die IP der Box) |
| Gar nicht | Mit dem WLAN **`OffgridCloud-Setup-XXXX`** verbinden. Handy und Laptop öffnen den Assistenten von selbst (Captive Portal), sonst `http://10.42.0.1` aufrufen. |

Das Setup-WLAN startet etwa 30 s nach dem Boot, wenn die Box kein anderes Netz
hat. Der Assistent fragt ab:

- **Admin-Zugang** (E-Mail und Passwort),
- **Name der Box**: danach `https://<name>.local`; vorgeschlagen wird ein
  zufälliger, damit mehrere Boxen im selben Netz nicht kollidieren. Einfacher
  geht's über den festen Alias `https://offgridcloud.local` oder per IP
  (`http://<IP>`),
- **Netzwerk**: ein WLAN, in das sich die Box einbucht, oder „Kabel / so lassen“,
- **Passwort fürs Rückfall-WLAN**: findet die Box später kein bekanntes Netz,
  öffnet sie ihr eigenes WLAN (Name wie die Box). Dieses Rückfall-WLAN lässt
  sich im Assistenten nicht abschalten, weil es bei einem falschen WLAN-Passwort
  der einzige Weg zurück auf die Box ist. Ändern oder abschalten lässt es sich
  später unter *Admin → Netzwerk*.

Nach dem Absenden schaltet die Box das Setup-WLAN ab, bucht sich ins gewählte
WLAN ein und richtet Hostname und HTTPS ein. Dann `https://<name>.local`
öffnen, die Zertifikatswarnung einmal bestätigen (das Zertifikat ist
selbstsigniert) und anmelden.

**Klappt das WLAN nicht** (z. B. Tippfehler im Passwort), öffnet die Box nach
etwa einer Minute ihr Rückfall-WLAN. Darüber ist sie unter
`http://10.42.0.1:8000` erreichbar, und das WLAN lässt sich unter
*Admin → Netzwerk* korrigieren.

## Sicherheit: „Wer zuerst kommt, wird Admin“

Ohne `offgridcloud-setup.txt` ist das Setup-WLAN **offen** und der Assistent
**ohne Code** nutzbar. Bis zur Einrichtung kann ihn also jeder in Funkreichweite
(oder im selben LAN) als Erster ausfüllen und die Box übernehmen. Wer die Box
nicht in geschützter Umgebung einrichtet, setzt einen `SETUP_CODE`. Falsche
Codes sperren den Assistenten nach fünf Versuchen jeweils für 60 s.

## Unter der Haube

| Teil | Aufgabe |
|---|---|
| `deploy/setup/firstboot.sh` | einmalig beim ersten Boot: Secret, `offgridcloud-setup.txt` einlesen, Hostname `offgridcloud-setup`, WLAN-Land, Caddy auf reines HTTP |
| `deploy/setup/setup-ap.sh` | solange Setup aussteht: ohne Uplink das Setup-WLAN `10.42.0.1` hosten und jede DNS-Anfrage auf die Box lenken (Captive Portal) |
| `app/setup_wizard.py`, `/api/setup/*` | der Assistent; sperrt alle anderen APIs mit 503, leitet Captive-Portal-Prüfungen auf `/setup` um |
| `deploy/setup/finish.sh` | nach dem Absenden (per sudo): Setup-WLAN weg, Netzwerk und Rückfall-WLAN anwenden, Hostname und HTTPS setzen |
| `.github/workflows/image.yml` | baut das Image mit pi-gen auf einem arm64-Runner und hängt es an jedes Release |

Status und Fehler auf der Box: `journalctl -u offgridcloud-firstboot -u offgridcloud-setup-ap`
sowie `/var/lib/offgridcloud/finish.log`.

Ein Image für einen beliebigen Branch baut *Actions → „Raspberry Pi image“ →
Run workflow*. Das Ergebnis liegt dann 7 Tage als Workflow-Artefakt bereit.
