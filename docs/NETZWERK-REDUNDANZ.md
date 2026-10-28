# Netzwerk-Redundanz — die Rückfallebene

OffgridCloud hängt normalerweise an einem vorhandenen Netzwerk (WLAN-Client oder
Kabel), um in die Cloud zu übertragen. **Fällt dieses Upstream-Netz aus** — der
Router ist weg, das Feld ist zu weit entfernt, das WLAN ändert sich —, darf das
Feld-Team trotzdem nicht ausgesperrt werden: Die Box soll **ihr eigenes WLAN
hosten**, damit lokal weiter hochgeladen werden kann, bis wieder ein bekanntes
Netzwerk erreichbar ist.

Genau das ist die Rückfallebene:

1. Die Box bevorzugt immer die **hinterlegten Netzwerke** (WLAN-Uplinks nach
   Priorität, oder Kabel).
2. Ist **keines** erreichbar, öffnet sie nach ein paar Fehlversuchen automatisch
   ihren **Fallback-Access-Point** (`OffgridCloud`).
3. Sobald ein hinterlegtes Netzwerk zurückkommt, **verlässt** sie den AP wieder
   und verbindet sich als Client — voll automatisch.

Die Box behält ihre aktuelle Verbindung, bis ein Admin die Netzwerke und den AP
im Web-UI unter **Netzwerk** einrichtet und **„Anwenden“** klickt.

## Wie es funktioniert

Auf Raspberry Pi OS (Bookworm) verwaltet **NetworkManager** die Verbindungen. Er
kann sowohl Client- als auch AP-Modus und speichert bekannte WLANs — das ist die
Grundlage.

Die App läuft bewusst **unprivilegiert** (Service-User `offgrid`). Echte
Netzwerkänderungen brauchen root — deshalb ist das Feature, genau wie das
One-Click-Self-Update, **opt-in** und in drei Schichten aufgebaut:

| Schicht | Rechte | Aufgabe |
|---------|--------|---------|
| **App** (`app/network.py`, `/api/network`) | unprivilegiert | Zeigt den Live-Status (read-only via `nmcli`), verwaltet die gewünschte Konfiguration und **exportiert** sie nach `<data_dir>/network.json` (Datei `0600`). |
| **Apply-Helfer** (`deploy/netfallback/apply.sh` + `_apply.py`) | root (via sudoers) | Liest den Export und legt die NetworkManager-Verbindungen an: einen Client pro hinterlegtem WLAN + den Fallback-AP. |
| **Watchdog** (`deploy/netfallback/watchdog.sh`, systemd-Dienst) | root | Prüft periodisch die Konnektivität und schaltet den AP hoch/runter — die eigentliche Automatik. |

Alles degradiert sauber: Ohne NetworkManager und ohne eingerichteten Helfer
antwortet die API mit `supported=false` statt zu scheitern — die Einstellungen
werden trotzdem gespeichert und beim nächsten „Anwenden“ auf einem passenden
Gerät übernommen.

```
   ┌────────────┐  export network.json (0600)   ┌─────────────────┐
   │  App (UI)  │ ────────────────────────────▶ │ apply.sh (root) │──▶ nmcli-Verbindungen
   │ unpriv.    │        sudo apply.sh          │  _apply.py      │    (Clients + AP)
   └────────────┘                               └─────────────────┘
         ▲                                              │ try-restart
         │ Status (read-only nmcli)                     ▼
         │                                       ┌─────────────────┐
         └────────────── UI zeigt Modus ─────────│ watchdog (root) │──▶ AP hoch/runter
                                                 │ systemd-Dienst  │    je nach Uplink
                                                 └─────────────────┘
```

## Einrichtung

### Bei der Installation

```bash
sudo ./deploy/install.sh      # Frage „Netzwerk-Redundanz …?" mit ja beantworten
```

Wird die Frage bejaht (oder `OGC_WITH_AP_FALLBACK=1` für eine unbeaufsichtigte
Installation gesetzt), installiert der Installer NetworkManager (+ `dnsmasq-base`
für die AP-DHCP, `iw` für die Funkregulierung), richtet den Watchdog-Dienst ein,
legt eine eng gefasste sudoers-Regel für **nur** `apply.sh` an und verdrahtet
`OGC_NET_APPLY_COMMAND` in der `.env`.

### Nachträglich

```bash
sudo /opt/offgridcloud/deploy/netfallback/install.sh
# (im rohen Checkout: sudo ./deploy/netfallback/install.sh)
```

### Im Web-UI

Unter **Netzwerk** (nur Admin):

1. **Rückfall-WLAN** einschalten und unter **AP-Einstellungen** (aufklappen) AP-Name/-Passwort setzen (WPA2, 8–63
   Zeichen; leer = offenes Netz). Optional Ländercode (z. B. `DE`) für die
   korrekte Funkregulierung.
2. Unter **Hinterlegte Netzwerke** die WLANs eintragen, mit denen sich die Box
   verbinden soll — höhere Priorität wird bevorzugt.
3. **„Anwenden“** klicken. Die Box übernimmt die Verbindungen und konvergiert
   sofort auf den passenden Modus.

## Betrieb

- **Watchdog beobachten:** `journalctl -u offgridcloud-netwatch -f`
- **Verbindungen ansehen:** `nmcli connection show` (der AP heißt
  `offgridcloud-ap`, Clients `ogc-wifi-*`).
- **Manuell anwenden** (falls sudoers nicht eingerichtet):
  `sudo /opt/offgridcloud/deploy/netfallback/apply.sh`
- **Tuning:** Prüf-Intervall, die Zahl der Fehlversuche bis zum Umschalten und
  die beiden Such-Intervalle im AP-Modus sind im UI einstellbar. Ein „Anwenden“ startet den Watchdog neu, damit ein
  geändertes Intervall sofort greift.

## USB-Tethering (iPhone/Android)

Kein Router, kein bekanntes WLAN, aber ein Handy mit Datenvertrag dabei? Per
USB-Kabel an die Box gesteckt, wird das Handy zum Uplink. Für NetworkManager ist
es ein ganz normales Kabel-Gerät (`usb0`, `eth1` oder `enx…`), es bekommt per
DHCP eine Adresse vom Handy, und der Watchdog zählt es als Uplink. Die Box geht
also **nicht** in den AP-Modus, solange das Handy Netz liefert.

| | Android | iPhone |
|---|---|---|
| Treiber | `rndis_host` oder `cdc_ncm` (im Kernel) | `ipheth` (im Kernel) |
| Zusatzpaket | keins | `usbmuxd` (installiert `install.sh` mit) |
| Einmalig | – | Handy entsperren, „Diesem Computer vertrauen“ antippen |
| Jedes Mal | Einstellungen → Hotspot & Tethering → **USB-Tethering** einschalten | **Persönlicher Hotspot** an, Handy beim Einstecken entsperrt |

Android schaltet USB-Tethering beim Abstecken wieder aus. Wer das nicht jedes Mal
antippen will: Entwickleroptionen → „Standard-USB-Konfiguration“ →
„USB-Tethering“.

**Erkennung:** Die Netzwerk-Seite zeigt dann den Modus **„USB-Tethering“** mit
„iPhone“ bzw. „Android-Handy“ statt „Kabel“. Erkannt wird das am Treiber
(`ipheth` = iPhone, `rndis_host`/`cdc_ncm` = Android). `cdc_ether` nutzen auch
viele USB-Ethernet-Adapter. Es zählt deshalb nur dann als Handy, wenn
NetworkManager die Verbindung zusätzlich als getaktet meldet (Android sendet
dafür eine DHCP-Option).

**Rangfolge:** NetworkManager gibt jedem Kabel-Gerät die Routen-Metrik 100, ein
eingestecktes Handy würde also ein bekanntes WLAN (600) überstimmen und alle
Uploads still übers Datenvolumen schicken. `deploy/netfallback/install.sh` legt
deshalb `/etc/NetworkManager/conf.d/offgridcloud-tether.conf` an und setzt die
Metrik für Handys auf **700**. Ergebnis: Kabel vor WLAN vor Handy. Das Handy ist
die letzte Reserve, bevor die Box ihr eigenes WLAN aufmacht. Ein schon
eingestecktes Handy übernimmt die neue Metrik erst nach dem nächsten Einstecken.

**Datenvolumen:** Läuft der Upload über eine getaktete Verbindung (Handy per
USB, oder ein Handy-Hotspot per WLAN, den NetworkManager als getaktet erkennt),
greift die Regel unter **Bandbreite → Getaktete Verbindung**:

- **normal senden** (Standard): wie bisher, ohne Rücksicht aufs Volumen.
- **bis zum Tagesvolumen senden:** Uploads laufen, bis das eingestellte
  Tagesvolumen (MB, Standard 500) verbraucht ist. Dateien, die nicht mehr ins
  Restvolumen passen, werden übersprungen und warten auf Kabel oder WLAN. Der
  Zähler beginnt jeden Tag (Ortszeit der Box) bei null.
- **pausieren:** Über getaktete Verbindungen wird gar nicht gesendet. Hochladen
  auf die Box geht weiter, die Uploads in die Cloud starten, sobald Kabel oder
  WLAN wieder da ist.

Pausen und Wiederaufnahmen meldet der Info-Service wie bei der Bandbreiten-
Drosselung. `/netz` im Telegram-Bot nennt die aktive Verbindung und ob sie
getaktet ist. Ohne NetworkManager kann die Box getaktete Verbindungen nicht
erkennen, die Regel bleibt dann wirkungslos.

**Strom:** Das Handy lädt am USB-Port der Box mit. Ein Pi 3 an einer schwachen
Powerbank kann dabei in Unterspannung geraten (`vcgencmd get_throttled`). Im
Zweifel das Handy separat laden oder ein Netzteil mit genug Reserve nehmen.

## Leitungsbündelung (net-merge)

Normalerweise trägt genau ein Uplink die Uploads: der mit der besten Routen-
Metrik. Mit der Leitungsbündelung nutzt die Box **alle Uplinks gleichzeitig**,
z. B. WLAN und ein per USB angestecktes LTE-Handy. Dafür sorgt
[net-merge](https://github.com/rohit267/net-merge) (MIT-Lizenz) von Rohit Mahto.

**Wie es eingebaut ist:** net-merge läuft als eigener Dienst
`offgridcloud-netmerge` und stellt einen SOCKS5-Proxy auf `127.0.0.1:1080`
bereit. Nur rclone wird über diesen Proxy geschickt. Systemweit ändert sich
nichts: Kein virtueller Netzwerkadapter, keine neuen Routen. Web-UI,
Rückfall-WLAN, VPN und die Geräte im Feld laufen wie bisher. Fällt net-merge
aus, gehen die Uploads automatisch direkt raus, statt fehlzuschlagen.

**Einrichten:** Der Installer richtet net-merge standardmäßig mit ein (auch im
fertigen Image und beim nächsten Update bestehender Boxen); abwählen mit
`OGC_WITH_NETMERGE=0`. Eingeschaltet wird die Bündelung trotzdem erst in der UI.
Fehlt es (Seite zeigt „nicht installiert“), von Hand nachholen:

```bash
sudo /opt/offgridcloud/deploy/netmerge/install.sh
# „command not found“? Ältere Installationen haben das Skript dort nur, wenn die
# Bündelung schon beim Installieren gewählt wurde. Dann aus dem Checkout:
#   sudo ./deploy/netmerge/install.sh
# oder den Installer mit OGC_WITH_NETMERGE=1 erneut laufen lassen.
```

Das Skript installiert eine fest gepinnte net-merge-Version (`NM_VERSION` im
Skript, derzeit 0.3.3): auf Raspberry Pi OS/Debian aus dessen signiertem
apt-Repository (Schlüssel-Fingerabdruck geprüft, Paket per `apt-mark hold`
festgehalten), sonst aus dem Release-Tarball (SHA-256 geprüft). Neuere
net-merge-Versionen kommen also nur mit einem OffgridCloud-Update. Es startet den Dienst und trägt `OGC_NETMERGE_PROXY` in die `.env`
ein. Beim Installer `deploy/install.sh` ist es der Punkt „Leitungsbündelung“
(bzw. `OGC_WITH_NETMERGE=1`). Danach unter **Bandbreite → Leitungsbündelung**
den Schalter „Uploads über alle Leitungen bündeln“ setzen. Dort steht auch, welche
Leitungen net-merge sieht und warum andere übersprungen werden.

**Was es bringt und was nicht:**

- net-merge verteilt **Verbindungen**, keine einzelnen Pakete. Ein Upload über eine
  einzige Verbindung (WebDAV/Nextcloud, SFTP, kleine Dateien) bleibt auf einer
  Leitung. Schneller werden **große Dateien zu S3, Azure Blob und B2** (rclone
  schickt deren Teile über mehrere Verbindungen parallel) und **mehrere Dateien
  gleichzeitig**: Unter **Bandbreite → Parallele Uploads** 2 bis 4 einstellen,
  dann kann jeder Upload eine andere Leitung nehmen. Das gilt für jeden Zieltyp.
- Es hilft nur, wenn die Leitungen **unabhängig** ins Internet gehen (WLAN + LTE).
  Kabel und WLAN am selben Router teilen sich eine Leitung, da kommt nichts dazu.
- net-merge prüft jede Leitung auf echten Internetzugang. Ein Handy ohne Daten
  oder ein WLAN mit Captive Portal bekommt keine Verbindungen.

**Wann die Box trotz Schalter direkt sendet:**

- Das Ziel liegt im **LAN oder hinter dem VPN** (der Hostname löst auf eine
  private Adresse auf oder gar nicht). net-merge bindet seine Verbindungen an die
  physischen Leitungen, so ein NAS wäre darüber nicht erreichbar.
- Ein **OpenVPN**-Tunnel ist aktiv. Er kann die Standard-Route tragen, und die
  Bündelung würde die Uploads an ihm vorbeischicken. WireGuard ist bei
  OffgridCloud immer Split-Tunnel, damit klappt die Bündelung.
- Ein **getaktetes Handy** hängt an der Box und die Regel für getaktete
  Verbindungen steht auf „bis zum Tagesvolumen“ oder „pausieren“. net-merge würde
  sonst einen Teil jedes Uploads übers Handy schicken, und das Tagesvolumen ließe
  sich nicht mehr zählen. Mit „normal senden“ wird das Handy mitgebündelt.
- Der Zieltyp kann keinen Proxy (SMB).

Welche Entscheidung gerade gilt, steht im Log (`journalctl -u offgridcloud`,
„Leitungsbündelung: Uploads …“). Telegram `/netz` zeigt die gebündelten
Leitungen.

**Parallele Uploads** (Bandbreite, Standard 1) gehen auch ohne Bündelung. Dann
teilen sich die Uploads die eine Leitung, was bei vielen kleinen Dateien oder einer
schnellen Leitung trotzdem hilft. Ein gesetztes Limit (KB/s) gilt für alle
zusammen, jeder Upload bekommt seinen Anteil. Bei „bis zum Tagesvolumen“ zählen
laufende Uploads schon mit, damit zwei gleichzeitige Dateien das Volumen nicht
gemeinsam überziehen. Auf einem Pi 3 höchstens 2 nehmen: jeder Upload ist ein
eigener rclone-Prozess.

**Docker:** Nicht vorgesehen. Wer es trotzdem will, startet net-merge auf dem Host
und setzt `OGC_NETMERGE_PROXY` auf eine Adresse, die der Container erreicht.

**Entfernen:** `deploy/uninstall.sh` nimmt den Dienst mit. Das Paket selbst bleibt
(`sudo apt remove net-merge`).

## Upload-WLAN für die Companion-App

Die Companion-App lädt Fotos vom Handy auch dann hoch, wenn Handy und Box in
keinem gemeinsamen Netz sind: Über Bluetooth fordert sie ein WLAN an
(`/api/uplink/handover`, siehe [BLE-PROTOKOLL.md §7.8](BLE-PROTOKOLL.md)), tritt
bei (ein Systemdialog beim ersten Mal) und lädt per HTTPS hoch.

Welches WLAN die Box dafür nimmt:

| Lage | Upload-WLAN |
|---|---|
| Rückfall-WLAN läuft schon | genau dieses |
| Freier WLAN-Adapter (USB-Stick, oder `wlan0` bei Kabel/Tethering ohne WLAN-Verbindung) | `OGC-<Box>-up` auf dem freien Adapter |
| `wlan0` hängt an einem WLAN | `OGC-<Box>-up` auf dem virtuellen Interface `ogcup0` **neben** der Verbindung, auf deren Kanal |

**Die Box geht für einen Upload nie offline.** Der WLAN-Chip des Pi kann einen
Access Point neben einer Client-Verbindung betreiben, beide auf demselben Kanal.
Funkt das Heimnetz auf einem 5-GHz-Kanal mit Radarerkennung (DFS, 52–144), oder
lässt der Chip das virtuelle Interface nicht zu, lehnt die Box den Handover ab.
Die App sagt das dann: Handy ins selbe Netz wie die Box bringen, oder die Box
per Kabel bzw. zweitem WLAN-Stick anbinden. Reißt die WLAN-Verbindung während
der Sitzung ab, beendet der Watchdog das Upload-WLAN, damit Wiederverbinden und
Rückfall-WLAN wie gewohnt greifen. Wechselt der Router den Kanal, bricht das
Upload-WLAN ab. Die App kann dann eine neue Sitzung anfordern.

Technik: Das Upload-WLAN betreibt nicht NetworkManager, sondern `hostapd`
(nur so bekommt jedes gekoppelte Handy einen eigenen Schlüssel auf derselben,
festen SSID) und ein `dnsmasq` nur für DHCP, ohne Gateway und DNS – das Handy
behält sein Mobilfunk-Internet. Gestartet und beendet wird es vom Root-Helfer
`deploy/netfallback/handover.sh` (sudoers-Regel und `OGC_NET_HANDOVER_COMMAND`
richtet `install.sh` ein, `hostapd` und `iw` installiert er mit, `ogcup0` setzt
er für NetworkManager auf *unmanaged*). Einen freien Adapter nimmt der Helfer
NetworkManager für die Sitzung weg und gibt ihn danach zurück. Neben einer
WLAN-Verbindung legt er `ogcup0` per `iw … interface add … type __ap` an und
löscht es am Ende wieder; die Verbindung selbst fasst er nicht an.

Das Upload-WLAN geht aus, wenn die App fertig ist, nach **5 min ohne Upload**,
spätestens nach **60 min**, beim Widerruf des Geräts und beim Neustart des
Dienstes. Die 60 min setzt zusätzlich der Watchdog durch, falls das Backend
hängt. Start und Ende stehen im Audit-Log (`uplink.handover_start`/`_end`).

**Grenzen:** Auf einem freien Adapter 2,4 GHz, Kanal 6 (jeder Pi kann das;
realistisch 2–4 MB/s). Neben einer Verbindung teilen sich Client und AP Funk
und Kanal, also auch die Bandbreite: Cloud-Upload und Handy-Upload bremsen
sich gegenseitig. Nicht auf der Hardware geprüft ist bisher, wie stabil der
AP neben dem Client auf allen Pi-Modellen und Firmware-Ständen läuft.
Rückmeldungen dazu bitte als Issue.

## Sicherheit & Grenzen

- **Klartext-Passwörter:** `network.json` enthält die WLAN-Passwörter im Klartext
  (NetworkManager braucht sie so — er legt sie ohnehin unter
  `/etc/NetworkManager/system-connections/` im Klartext ab). Die Datei liegt im
  Daten-Verzeichnis mit `0600`. In der Datenbank sind die Passwörter mit dem
  gleichen Schlüssel wie die Provider-Credentials **verschlüsselt** (`OGC_SECRET_KEY`).
- **Minimale Rechte:** Die sudoers-Regel erlaubt dem Service-User ausschließlich
  `apply.sh` — kein allgemeines `nmcli`-Recht.
- **Hardware:** Ein einzelner WLAN-Chip kann nicht gleichzeitig Client *und* AP
  sein. Der AP ist deshalb eine echte Rückfallebene: Er ist an, wenn kein Uplink
  besteht, und aus, sobald ein hinterlegtes WLAN wieder da ist. Für „beides
  gleichzeitig“ braucht es einen zweiten WLAN-Adapter (USB) oder einen Kabel-Uplink.
- **Rückkehr aus dem AP:** Weil der Funkchip im AP-Modus belegt ist, sucht der
  Watchdog aktiv nach bekannten WLANs. Berücksichtigt werden alle WLAN-Profile mit
  Autoconnect, auch das aus dem Raspberry Pi Imager (`netplan-wlan0-*`).
  Solche Profile zeigt die Netzwerk-Seite in der Liste der bekannten WLANs mit dem
  Kennzeichen „System“ (und „verbunden“, wenn aktiv) an. Sie lassen sich dort nicht
  bearbeiten, sondern nur per `nmtui`/`nmcli` auf der Box. Der Status nennt die
  tatsächlich verbundene SSID.
  1. Alle **2 Minuten** scannt er, ohne den AP abzuschalten. Ist ein bekanntes
     WLAN sichtbar, verbindet er sich damit.
  2. Viele Treiber, darunter der des Pi (`brcmfmac`), liefern im AP-Modus keine
     oder nur veraltete Scan-Ergebnisse. Deshalb schaltet er den AP zusätzlich
     **alle 10 Minuten für wenige Sekunden ab**, sucht als Client und stellt den
     AP sofort wieder her, wenn nichts Bekanntes in Reichweite ist. Laufende
     Uploads übers AP-WLAN brechen dabei kurz ab und müssen neu verbinden.

  Beide Intervalle sind im UI einstellbar („Suche im AP-Modus“ und „AP kurz
  abschalten zum Suchen“, Standard 120/600 s). Kürzere Werte bringen die Box
  schneller zurück ins WLAN, unterbrechen den AP aber öfter. Versteckte SSIDs
  tauchen in Scans nicht auf und werden deshalb nur über einen Kabel-Uplink
  erkannt.
- **Kein NetworkManager?** Auf Systemen mit `dhcpcd`/`systemd-networkd` steuert
  die App nichts aktiv. Die Konfiguration wird trotzdem exportiert; die
  Umsetzung müsste dann ein eigenes Setup (hostapd/dnsmasq) übernehmen.
