# Bluetooth auf echter Hardware prüfen

Checkliste für den ersten Test des Dienstes `offgridcloud-ble` mit einem echten
Pi und einem echten Handy. Alles oberhalb der Funkstrecke ist automatisch
getestet: Protokoll gegen Testvektoren, GATT-Schicht gegen einen echten
`dbus-daemon` mit nachgebautem BlueZ, die Companion-App Ende-zu-Ende gegen den
echten Dienst. **Offen ist nur, was echtes BlueZ und echter Funk anders machen.**
Die Schritte bauen aufeinander auf. Wo einer scheitert, aufhören, die Diagnose
(Abschnitt 6) einsammeln und nicht im nächsten Schritt weiterraten.

Protokoll: [BLE-PROTOKOLL.md](BLE-PROTOKOLL.md) · Betrieb: [BETRIEB.md §9d](BETRIEB.md)

## 0. Was du brauchst

- **Pi mit Bluetooth:** 3B/3B+, 4, 5 oder Zero 2 W (sonst USB-Dongle), Raspberry
  Pi OS Bookworm, OffgridCloud aktueller Stand von `main`, mit der
  Installer-Option *Bluetooth für die Companion-App*. Nachrüsten:
  `sudo /opt/offgridcloud/deploy/ble/install.sh`.
- **Handy mit der Companion-App:**
  - Android 10+: `app-debug.apk` aus dem CI-Lauf von `main` im Companion-Repo
    (Actions → letzter Lauf → Artefakt `app-debug`), per Datei installieren.
  - iOS 16+: Build über Xcode Cloud bzw. TestFlight.
- **[nRF Connect](https://www.nordicsemi.com/Products/Development-tools/nRF-Connect-for-mobile)**
  (Nordic, kostenlos, Android und iOS) auf demselben oder einem zweiten Handy.
  Damit prüfst du Werbung und GATT-Tabelle unabhängig von unserer App.
- Ein Browser fürs Web-Portal und eine SSH-Sitzung auf dem Pi.

> **Nur eine Verbindung gleichzeitig.** Solange nRF Connect oder die App mit der
> Box verbunden ist, wirbt sie nicht und alle anderen finden sie nicht. Vor jedem
> Schritt die vorige Verbindung trennen.

## 1. Dienst und Adapter (auf dem Pi)

```bash
systemctl status offgridcloud-ble --no-pager
journalctl -u offgridcloud-ble -b --no-pager | tail -20
bluetoothctl --version; bluetoothctl show | grep -E 'Powered|Name|Roles'
rfkill list bluetooth
id offgrid | tr ',' '\n' | grep bluetooth
cat /opt/offgridcloud/data/ble/service.json; echo; date +%s
systemctl show offgridcloud-ble -p MemoryCurrent
```

**Erwartet:**
- Der Dienst ist `active (running)`.
- Im Journal steht `GATT-Dienst auf /org/bluez/hci0 registriert`, ohne Traceback danach.
- `Powered: yes`, und `rfkill` meldet weder Soft- noch Hard-Block.
- `offgrid` ist in der Gruppe `bluetooth`.
- `updated` in `service.json` liegt höchstens wenige Sekunden hinter `date +%s`.
- Im Portal unter *Bluetooth* gibt es keinen gelben Hinweis „meldet sich nicht“.
- `MemoryCurrent` notieren (Ziel laut Plan < 40 MB, harte Grenze 150 MB).

| Meldung im Journal | Ursache und Abhilfe |
|---|---|
| `Kein Bluetooth-Adapter mit GATT-Unterstützung gefunden` | Adapter aus oder fehlt: `rfkill unblock bluetooth`, `sudo systemctl restart bluetooth`, bei Dongle `lsusb`. Bei mehreren Adaptern `OGC_BLE_ADAPTER=hci0` in `/opt/offgridcloud/.env` |
| `org.freedesktop.DBus.Error.AccessDenied` oder `org.bluez.Error.NotPermitted` | D-Bus erlaubt der Gruppe `bluetooth` den Zugriff auf BlueZ nicht. `grep -A3 'group="bluetooth"' /etc/dbus-1/system.d/bluetooth.conf` muss `allow send_destination="org.bluez"` zeigen; Gruppe prüfen (siehe oben), danach `sudo systemctl restart offgridcloud-ble` |
| `ModuleNotFoundError: No module named 'dbus_fast'` | `install.sh` Schritt 3 lief nicht: `sudo /opt/offgridcloud/deploy/ble/install.sh` |
| `org.bluez.Error.Failed` / `Invalid Length` bei `RegisterAdvertisement` | Werbung passt nicht in 31 Byte. **Genau das ist einer der offenen Punkte** → Diagnose (Abschnitt 6) und melden |

## 2. Werbung (nRF Connect, Reiter *Scanner*)

Scannen, Filter auf „OGC“ oder nach Signalstärke sortieren.

**Erwartet:**
- Gerät **`OGC-<Box-Name>`** (der Box-Name aus dem Portal).
- *Complete/Incomplete List of 128-bit Service UUIDs*: `90c00001-59bd-434e-b548-6113f2f006f9`.
- *Manufacturer data*, Company `0xFFFF`, Daten `0x01 0x04`. Das erste Byte ist
  die Protokollversion, das zweite die Flags:
  - `0x04`: Backend läuft;
  - `0x06`: zusätzlich gerade ein Kopplungscode offen;
  - `+0x01`: Setup-Assistent noch nicht durchlaufen.

**Prüfpunkte:**
- **Steht der Name da?** Flags, UUID und Herstellerdaten belegen schon 27 der
  31 Byte. Der Name muss also in die *Scan Response*. Ob BlueZ ihn dort
  ablegt, ist der wichtigste offene Punkt. Fehlt er, findet die App die Box
  trotzdem (sie sucht nach der UUID und liest den Namen aus `info`). Bitte
  trotzdem melden.
- Im Portal *Gerät koppeln* drücken: Das Flag-Byte muss binnen ~5 s auf `0x06` springen.

## 3. GATT-Tabelle (nRF Connect, *Connect*)

Verbinden, dann den Dienst `90c00001-…` aufklappen.

| Characteristic | Eigenschaften | Probe |
|---|---|---|
| `90c00002-…` (`info`) | Read | Lesen. Der Wert beginnt mit `A4 61 76 01` (CBOR-Map, `v: 1`) und enthält den Box-Namen und 32 Byte Schlüssel |
| `90c00003-…` (`rx`) | Write Without Response | nicht schreiben |
| `90c00004-…` (`tx`) | Notify | — |

**Prüfpunkte:**
- In nRF Connect steht oben die ausgehandelte MTU (*MTU: …*). Bitte notieren.
- Lesen darf **kein** Pairing-Dialog des Betriebssystems auslösen. Die
  Verschlüsselung macht das Protokoll selbst (Noise), nicht Bluetooth.
- Danach **trennen** und prüfen, dass die Box im Scanner wieder auftaucht
  (die Werbung läuft wieder an).

## 4. Koppeln mit der App

1. Im Portal unter **Bluetooth → Gerät koppeln** den QR-Code anzeigen.
2. Mit der normalen Kamera-App des Handys scannen und den Link in der
   OffgridCloud-App öffnen. Alternativ in der App *Mit Code koppeln* wählen
   und den Code abtippen.
3. Beim ersten Start fragt die App nach Bluetooth- bzw. „Geräte in der
   Nähe“-Berechtigung. Unter Android 10/11 braucht der Scan außerdem den
   **Standort**, und der muss eingeschaltet sein.

**Erwartet:**
- In der App: Kopplung erfolgreich, danach das Dashboard mit Live-Status.
- Im Portal: Der Code verschwindet, es kommt die Meldung „… ist gekoppelt“,
  und das Gerät steht in der Liste.
- Im Journal: `BLE gekoppelt: <Gerät> (<E-Mail>)`.

Dauert die Kopplung länger als ~10 s oder bricht sie ab, die Diagnose aus
Abschnitt 6 mit `btmon` **während eines neuen Versuchs** aufnehmen.

## 5. Funktionsproben

| # | Probe | Erwartet |
|---|---|---|
| 5.1 | App beenden und neu öffnen, Box antippen | Anmeldung ohne Code (`BLE-Sitzung: …` im Journal) |
| 5.2 | Im Portal eine Datei hochladen | Die Zähler im App-Dashboard ändern sich binnen ~3 s |
| 5.3 | Mit dem Handy langsam weggehen | Notieren, ab welcher Entfernung (durch Wände?) die Verbindung abreißt; danach Wiederverbinden in Reichweite |
| 5.4 | `sudo systemctl stop offgridcloud` (Web-Dienst aus) | Die App verbindet trotzdem und zeigt „🔴 Backend antwortet nicht“. *Backend neu starten* in der App bringt ihn zurück (`systemctl is-active offgridcloud`) |
| 5.5 | Im Portal das Gerät **entfernen**, während die App verbunden ist | Die Verbindung bricht binnen ~5 s ab. Neu verbinden meldet „Die Box kennt dieses Gerät nicht mehr“ |
| 5.6 | Im Portal (Admin) *Bluetooth aktiv* ausschalten | Die Box verschwindet binnen ~5 s aus dem Scan; nach dem Einschalten ist sie wieder da |
| 5.7 | Pi neu starten | Die Box ist ohne Zutun wieder per Bluetooth erreichbar |
| 5.8 | Nur mit WLAN-/Rückfall-AP-Problem: In der App das WLAN ändern | Das ist der Ernstfall, für den es den Dienst gibt |

Nach 5.5 das Handy neu koppeln, bevor 5.6 bis 5.8 laufen.

## 6. Diagnose einsammeln

Den Fehler so aufnehmen, dann ist er ohne Rätselraten eingrenzbar:

```bash
# Terminal 1: Funkverkehr mitschneiden (Strg+C nach dem Versuch)
sudo btmon -w ~/ble-$(date +%H%M).btsnoop

# Terminal 2: den Fehler auslösen (z. B. Dienst neu starten oder in der App koppeln)
sudo systemctl restart offgridcloud-ble

# Danach das Journal sichern
journalctl -u offgridcloud-ble --since "15 min ago" --no-pager > ~/ble-journal.txt
```

Für die Werbung (Schritt 2) reicht oft schon die Textausgabe:
`sudo btmon | grep -A12 -E 'Set (Extended )?(Advertising|Scan Response) Data'`.

Mitschicken: die `.btsnoop`-Datei und `ble-journal.txt`, dazu was du in der App
bzw. in nRF Connect gesehen hast (Screenshot genügt).

## 7. Ergebnis

Ausfüllen und zurückmelden:

| | |
|---|---|
| Pi-Modell / OS / `bluetoothctl --version` | |
| Handy / OS-Version / App-Build | |
| 1 Dienst, Adapter, Speicher (`MemoryCurrent`) | |
| 2 Werbung: Name sichtbar? Flags korrekt? | |
| 3 GATT: Tabelle korrekt? MTU? Kein OS-Pairing? | |
| 4 Kopplung per QR / per Code | |
| 5.1–5.8 | |
| Auffälligkeiten | |
