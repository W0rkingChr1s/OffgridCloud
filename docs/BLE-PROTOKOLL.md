# OffgridCloud — BLE-Protokoll (Version 1)

> **Status:** Spezifikation, Entwurf zur Umsetzung. Noch keine Implementierung im
> Box-Dienst. Gegenstück in der App:
> [OffgridCloud-Companion](https://github.com/W0rkingChr1s/OffgridCloud-Companion)
> (`src/protocol/`). Testvektoren: [`docs/ble/testvectors.json`](https://github.com/W0rkingChr1s/OffgridCloud/blob/main/docs/ble/testvectors.json),
> erzeugt mit `scripts/ble_testvectors.py`.

Dieses Dokument legt fest, wie die Companion-App (iOS/Android) und der Dienst
`offgridcloud-ble` auf der Box über **Bluetooth Low Energy** miteinander
sprechen: Werbung, GATT-Layout, Framing, Ende-zu-Ende-Verschlüsselung mit dem
**Noise Protocol Framework**, Kopplung und RPC-Brücke zur bestehenden REST-API.

Die Schlüsselwörter **MUSS**, **DARF NICHT**, **SOLL** und **KANN** sind im Sinne
von RFC 2119 zu lesen.

---

## 1. Überblick

```
 App                                             Box (offgridcloud-ble)
  │  Scan: Service-UUID, Name OGC-…, Flags          │
  │  read info  ───────────────────────────────────▶│  Version, Name, Flags, pk
  │  Noise-Handshake (2 Nachrichten) über rx/tx ───▶│  IKpsk2 (Kopplung) / IK (Sitzung)
  │◀──────────────────────────────────────────────── │
  │  verschlüsselte RPC-Anfragen (CBOR) ───────────▶│  Whitelist → HTTP 127.0.0.1:8000
  │◀────────────── Antworten + Status-Events ─────── │
```

- **Transport:** ein eigener GATT-Service mit drei Characteristics (§3).
- **Framing:** jede Nachricht wird in Frames zerlegt, die je in eine
  ATT-Operation passen (§4).
- **Sicherheit:** Noise mit X25519, ChaCha20-Poly1305 und SHA-256 (§5). Die
  Kopplung ist an einen Code aus dem Web-Portal gebunden (§6).
- **RPC:** CBOR-kodierte Anfragen an die REST-API, gefiltert durch eine
  Whitelist (§7). Rechte prüft weiterhin allein das Backend.

## 2. Versionierung

Diese Spezifikation beschreibt **Protokollversion 1**. Die Version steht in der
Werbung (§3.1), in `info` (§3.2) und im Noise-Prolog (§5.2). Eine App, die die
Version einer Box nicht kennt, DARF KEINE Verbindung aufbauen und SOLL zum
Update raten. Inkompatible Änderungen erhöhen die Version; neue, optionale
CBOR-Felder nicht (Empfänger MÜSSEN unbekannte Felder ignorieren).

## 3. Werbung und GATT-Layout

### 3.1 Werbung (Advertising)

Die Box wirbt, solange BLE aktiviert ist (*Admin → Bluetooth*, Standard: an).

**Advertising-Paket** (Legacy, max. 31 Byte):

| AD-Typ | Inhalt | Länge |
|---|---|---|
| `0x01` Flags | `0x06` (LE General Discoverable, kein BR/EDR) | 3 |
| `0x07` Complete List of 128-bit UUIDs | Service-UUID (§3.2) | 18 |
| `0xFF` Manufacturer Specific Data | Company-ID `0xFFFF` (LE) · Version (u8) · Flags (u8) | 6 |

**Scan-Response:** `0x09` Complete Local Name = `OGC-` + Boxname, UTF-8,
höchstens 29 Byte insgesamt. Längere Namen werden an einer Zeichengrenze
gekürzt (der volle Name steht in `info`). Der Name passt nicht mehr ins
Advertising-Paket; Apps MÜSSEN Name und Manufacturer-Data aus Advertising und
Scan-Response zusammenführen (Android und iOS tun das automatisch).

Company-ID `0xFFFF` ist von der Bluetooth SIG für Tests und interne Nutzung
reserviert. Für eine öffentliche Version SOLL eine eigene Company-ID beantragt
werden; die App MUSS dann beide akzeptieren.

**Flags** (gleiche Bits in Werbung und `info`):

| Bit | Name | Bedeutung |
|---|---|---|
| `0x01` | `setup_pending` | Box noch nicht eingerichtet |
| `0x02` | `pairing_open` | mindestens ein gültiger Kopplungscode offen |
| `0x04` | `backend_up` | FastAPI-Backend antwortet |
| `0xF8` | — | reserviert, MUSS 0 sein, Empfänger ignorieren |

### 3.2 Service und Characteristics

| Name | UUID | Eigenschaften | Inhalt |
|---|---|---|---|
| Service | `90c00001-59bd-434e-b548-6113f2f006f9` | primary | — |
| `info` | `90c00002-59bd-434e-b548-6113f2f006f9` | read, **unverschlüsselt** | CBOR-Map, siehe unten |
| `rx` | `90c00003-59bd-434e-b548-6113f2f006f9` | write without response | App → Box: Frames |
| `tx` | `90c00004-59bd-434e-b548-6113f2f006f9` | notify | Box → App: Frames |

**`info`** ist eine CBOR-Map (§7.1), höchstens 120 Byte:

```
{ "v": 1, "name": "Kiste-1", "flags": 6, "pk": h'23e86086…' }
```

| Feld | Typ | Bedeutung |
|---|---|---|
| `v` | uint | Protokollversion |
| `name` | tstr | Anzeigename der Box, höchstens 32 Byte UTF-8 |
| `flags` | uint | Flags wie in §3.1 |
| `pk` | bstr (32) | statischer X25519-Schlüssel der Box (öffentlich) |

`info` verrät bewusst nur, *dass* hier eine OffgridCloud steht — keine IPs,
Zähler oder Benutzer. `pk` ist ein stabiler Bezeichner der Box; das ist
hinnehmbar, weil Name und Werbung ohnehin stabil sind.

GATT-Pairing/Bonding auf Link-Ebene wird **nicht** verwendet; die Sicherheit
kommt vollständig aus §5 und §6.

### 3.3 Verbindung

- Die App verbindet sich, liest `info`, abonniert Notifications auf `tx` und
  startet den Handshake (§5).
- **MTU:** Android SOLL 517 anfordern; iOS handelt selbst aus (typisch 185).
  Beide Seiten MÜSSEN mit der Mindest-MTU 23 funktionieren.
- Die Box KANN mehrere Verbindungen gleichzeitig halten. Jede Verbindung hat ihre
  eigene Noise-Sitzung; der Zustand geht mit der Verbindung verloren.

## 4. Framing

Jede Nachricht (Handshake-Nachricht oder verschlüsselte Transport-Nachricht) wird
in **Frames** zerlegt. Ein Frame füllt genau einen ATT-Write (`rx`) bzw. eine
Notification (`tx`).

```
 0       2       4   5
 ├───────┼───────┼───┼──────────────── … ─┤
 │msg_id │  seq  │flg│  Nutzdaten          │
 └───────┴───────┴───┴──────────────── … ─┘
  u16 BE  u16 BE  u8   höchstens MTU − 3 − 5 Byte
```

| Flag | Name | Bedeutung |
|---|---|---|
| `0x01` | `FIRST` | erster Frame der Nachricht (`seq` = 0) |
| `0x02` | `LAST` | letzter Frame der Nachricht |
| `0x04` | `EVENT` | Box → App: Status-Event statt Antwort |
| `0x08` | `HANDSHAKE` | Noise-Handshake-Nachricht (§5) |
| `0xF0` | — | reserviert, MUSS 0 sein |

Regeln:

1. `seq` beginnt bei 0 und steigt je Frame um 1. Eine Nachricht aus einem Frame
   trägt `FIRST | LAST`. Eine leere Nachricht ist ein Frame ohne Nutzdaten.
2. **`msg_id`:** bei Anfragen gleich der RPC-`id` (§7.2), Antworten tragen die
   `msg_id` der Anfrage. Events tragen einen beliebigen Zähler der Box.
   Handshake-Nachrichten tragen `msg_id` 0.
3. **Keine Verschachtelung:** Ein Sender MUSS alle Frames einer Nachricht
   senden, bevor er mit der nächsten beginnt, und Nachrichten in genau der
   Reihenfolge senden, in der er sie verschlüsselt hat (die Noise-Nonce ist
   implizit, §5.4). Empfänger DÜRFEN dennoch nach `msg_id` getrennt
   zusammensetzen.
4. **Grenzen:** eine Nachricht hat höchstens **65 535 Byte** (Grenze einer
   Noise-Nachricht). Unvollständige Nachrichten verfallen nach **10 s**.
5. **Fehler:** Lücken, doppelte Frames, `seq` ≠ erwartet, Überlänge oder
   reservierte Flags verwerfen die betroffene Nachricht. In der Transportphase
   (§5.4) ist das gleichbedeutend mit einem Entschlüsselungsfehler, weil die
   Nonce-Folge reißt: der Empfänger MUSS die Verbindung trennen.

Testvektoren: `framing` in `testvectors.json`.

## 5. Verschlüsselung (Noise)

### 5.1 Schlüssel

- **Box:** ein statisches X25519-Schlüsselpaar, beim ersten Start von
  `offgridcloud-ble` erzeugt, abgelegt unter `/var/lib/offgridcloud/ble/`
  (Modus 0600, Eigentümer des Dienstes). Ein Wechsel macht alle Kopplungen
  ungültig und ist nur über *Admin → Bluetooth → Box-Schlüssel erneuern* möglich.
- **App:** ein statisches X25519-Schlüsselpaar **je gekoppelter Box**, bei der
  Kopplung erzeugt. Ablage im iOS-Keychain (`ThisDeviceOnly`, nicht in Backups)
  bzw. im Android-Keystore-gestützten verschlüsselten Speicher. Secure Enclave
  und StrongBox unterstützen kein X25519; der Schlüssel liegt deshalb
  verschlüsselt, aber nicht hardware-gebunden vor.

### 5.2 Suites und Prolog

| Zweck | Noise-Protokollname |
|---|---|
| Kopplung | `Noise_IKpsk2_25519_ChaChaPoly_SHA256` |
| Sitzung (gekoppeltes Gerät) | `Noise_IK_25519_ChaChaPoly_SHA256` |

- **Initiator ist immer die App**, Responder die Box.
- **Prolog:** die ASCII-Bytes `OGC-BLE/1` (bindet die Protokollversion).
- Der statische Schlüssel der Box (`rs` des Initiators) stammt aus dem
  Kopplungs-Link (§6.2) oder, bei abgetipptem Code, aus `info.pk`. Bei
  gespeicherten Kopplungen verwendet die App den bei der Kopplung gespeicherten
  Schlüssel und MUSS abbrechen, wenn `info.pk` davon abweicht.

Warum IK: Die App kennt den Box-Schlüssel vorab; ihr eigener statischer Schlüssel
geht verschlüsselt in Nachricht 1, die Box sucht ihn in der Liste gekoppelter
Geräte. Zwei Nachrichten genügen, beide Seiten sind danach gegenseitig
authentisiert, und die Sitzung hat Forward Secrecy.

### 5.3 Handshake

Beide Handshake-Nachrichten laufen als Frames mit `HANDSHAKE`-Flag und
`msg_id` 0. Die Payloads sind CBOR-Maps.

**Nachricht 1 (App → Box)** — `e, es, s, ss`:

| Feld | Typ | Wann | Bedeutung |
|---|---|---|---|
| `mode` | tstr | immer | `"pair"` oder `"session"` |
| `pid` | uint | `pair` | Kopplungs-ID aus dem Code (§6.1) |
| `dev` | tstr | `pair` | Gerätename für die Geräteliste, höchstens 64 Byte |
| `app` | tstr | immer | App-Version |

**Nachricht 2 (Box → App)** — `e, ee, se` (bei Kopplung zusätzlich `psk`):

| Feld | Typ | Wann | Bedeutung |
|---|---|---|---|
| `v` | uint | immer | Protokollversion der Box |
| `name` | tstr | `pair` | Anzeigename der Box |
| `dev_id` | uint | `session` | Geräte-ID |
| `user` | tstr | `session` | Benutzer, an den das Gerät gebunden ist |
| `role` | tstr | `session` | `"admin"` oder `"user"` |

**Ablehnung:** Kann die Box keine gültige Nachricht 2 bilden, sendet sie
**einen** Handshake-Frame mit einer **unverschlüsselten** CBOR-Map
`{"error": "<code>"}` und trennt die Verbindung. Da diese Antwort nicht
authentisiert ist, DARF die App sie nur als Hinweis anzeigen und KEINE
gespeicherten Daten deswegen löschen.

**Unterscheidung:** Eine gültige Nachricht 2 ist mindestens **48 Byte** lang
(ephemerer Schlüssel 32 + Tag 16). Eine Handshake-Antwort mit weniger als 48
Byte ist eine Ablehnung; die Box MUSS Ablehnungen deshalb kürzer als 48 Byte
halten (alle Codes oben erfüllen das).

| Code | Bedeutung |
|---|---|
| `unknown_device` | statischer Schlüssel der App ist nicht (mehr) gekoppelt |
| `pairing_invalid` | `pid` unbekannt, abgelaufen oder verbraucht |
| `locked` | zu viele Fehlversuche, später erneut versuchen (§8) |
| `version` | Protokollversion nicht unterstützt |
| `busy` | Box nimmt gerade keine Verbindungen an |
| `ble_disabled` | BLE wurde im Web-Portal abgeschaltet |

Ein Fehler bei `read_message` (falscher Schlüssel, manipulierte Daten) führt
ohne Antwort zum Trennen.

**Muster erkennen (Box):** Nachricht 1 sagt nicht im Klartext, ob sie zu `IK`
oder `IKpsk2` gehört — die Protokollnamen fließen in den Hash ein. Die Box
versucht deshalb zuerst `IK` (Sitzung) und, wenn das Entschlüsseln scheitert,
`IKpsk2` (Kopplung). Bei `IKpsk2` hängt Nachricht 1 **nicht** von der psk ab:
Die Box liest sie mit einer beliebigen Platzhalter-psk, entnimmt dem Payload
die `pid` und setzt erst danach die richtige psk für Nachricht 2. Ein falsches
Kopplungsgeheimnis bemerkt deshalb erst die App beim Lesen von Nachricht 2; die
Box erfährt es nur daran, dass die Bestätigung (§6.3) ausbleibt.

### 5.4 Transportphase

Nach Nachricht 2 hat jede Seite zwei CipherStates (Senden/Empfangen). Jede
weitere Nachricht ist genau eine Noise-Transportnachricht: Klartext (CBOR, §7)
plus 16 Byte Tag, Nonce implizit ab 0 je Richtung.

- Entschlüsselungsfehler ⇒ der Empfänger MUSS die Verbindung sofort trennen.
- Antworten und Events der Box teilen sich dieselbe Senderichtung und damit
  dieselbe Nonce-Folge (siehe Regel 3 in §4).
- Ein Rekey ist nicht vorgesehen; jede neue Verbindung führt einen neuen
  Handshake durch.

Testvektoren: `handshake_pair` und `handshake_session` (Schlüssel, Payloads,
Nachrichten, Handshake-Hash, erste Transportnachrichten je Richtung, Frames bei
MTU 23).

## 6. Kopplung

Gekoppelt wird **ausschließlich über das Web-Portal** (Companion-Plan, §4.1). Ein
angemeldeter Benutzer erzeugt unter *Bluetooth → Gerät koppeln* einen
Kopplungscode; das gekoppelte Gerät gehört danach diesem Benutzer.

### 6.1 Kopplungscode

- 100 Zufallsbit: **`pid`** (20 Bit, Kopplungs-ID) ‖ **`secret`** (80 Bit).
- Darstellung: Crockford-Base32 (Alphabet `0123456789ABCDEFGHJKMNPQRSTVWXYZ`),
  big-endian, 20 Zeichen in Gruppen zu fünf: `B8Y11-WF2TF-2BB9M-QH1WP`.
  Eingaben werden normalisiert: Großbuchstaben, `I`/`L` → `1`, `O` → `0`,
  Bindestriche und Leerzeichen entfernen.
- Gültig **5 Minuten**, **einmal** verwendbar, höchstens **3 Handshakes** je
  `pid`; danach ist der Code verbraucht.
- **psk** = HKDF-SHA256(IKM = `secret` (10 Byte), Salt = statischer
  Box-Schlüssel (32 Byte), Info = `OGC-BLE/1 pair`, Länge 32).

Testvektor: `pairing_code`.

### 6.2 Darstellung im Portal

1. **QR-Code** und **Button „In der App öffnen“** enthalten denselben Link:

   ```
   offgridcloud://pair?v=1&c=<Code ohne Bindestriche>&pk=<Box-Schlüssel, base64url>&n=<Boxname>
   ```

   Mit `pk` kennt die App den Box-Schlüssel authentisch und MUSS vor dem
   Handshake prüfen, dass `info.pk` übereinstimmt.
2. **Code zum Abtippen:** nur der Code; die App nimmt `info.pk` der Box, mit der
   sie gerade verbunden ist. Ein Angreifer, der einen falschen `pk` unterschiebt,
   scheitert an der psk (§9).

### 6.3 Ablauf

1. App verbindet sich, liest `info`, prüft Version und ggf. `pk`.
2. Handshake `IKpsk2` mit `mode: "pair"`, `pid`, `dev`.
3. Die Box liest Nachricht 1 (ohne psk möglich, §5.3), sucht den offenen Code
   zu `pid`, leitet die psk ab und antwortet mit Nachricht 2. Die App liest sie
   nur mit der richtigen psk erfolgreich.
4. **Schlüsselbestätigung:** Die erste Transportnachricht der App MUSS
   `POST /ble/pair/confirm` sein (§7.4). Erst wenn die Box diese Nachricht
   entschlüsseln kann, ist die psk auf beiden Seiten bestätigt. Dann trägt die Box
   den statischen App-Schlüssel als Gerät ein (Benutzer = Ersteller des Codes,
   Name = `dev`), markiert den Code als verbraucht, schreibt einen Audit-Eintrag
   und antwortet mit `{dev_id, user, role}`.
5. Kommt die Bestätigung nicht binnen 30 s, verwirft die Box den Vorgang; der
   Code bleibt bis zu seinem Ablauf (und innerhalb der 3 Versuche) gültig.

Danach verbindet sich die App mit `Noise_IK` (`mode: "session"`).

### 6.4 Widerruf

Geräte werden im Web-Portal widerrufen (eigene Geräte durch den Benutzer, alle
durch Admins). Die Box MUSS danach neue Handshakes dieses Schlüssels mit
`unknown_device` ablehnen und **bestehende Verbindungen des Geräts binnen 5 s
trennen**. Wird der Benutzer deaktiviert oder gelöscht, gilt das für alle seine
Geräte.

## 7. RPC

### 7.1 CBOR

Alle Payloads sind CBOR nach RFC 8949: Maps mit Text-Schlüsseln, keine Tags,
keine undefinierten Werte. Sender SOLLEN die kürzeste Kodierung verwenden und
Schlüssel in der Reihenfolge dieser Spezifikation schreiben (damit Testvektoren
byte-gleich sind); Empfänger MÜSSEN jede gültige Kodierung und Reihenfolge
akzeptieren. JSON-Bodies der REST-API werden 1:1 abgebildet (Objekt → Map,
Array → Array, `null` → `null`, Zahl → int/float).

### 7.2 Anfrage (App → Box)

```
{ "id": 1, "method": "GET", "path": "/api/health", "query": {…}, "body": … }
```

| Feld | Typ | Bedeutung |
|---|---|---|
| `id` | uint 1…65535 | frei gewählt, eindeutig unter den offenen Anfragen der Verbindung |
| `method` | tstr | `GET`, `POST`, `PUT`, `PATCH`, `DELETE` |
| `path` | tstr | Pfad ohne Query, beginnt mit `/api/` oder `/ble/` |
| `query` | map tstr → tstr | optional |
| `body` | any | optional, wird als JSON an die REST-API gegeben |

### 7.3 Antwort (Box → App)

```
{ "id": 1, "status": 200, "body": {…} }
{ "id": 3, "status": 0, "body": null, "error": "not_allowed" }
```

| Feld | Typ | Bedeutung |
|---|---|---|
| `id` | uint | `id` der Anfrage |
| `status` | uint | HTTP-Status der REST-API; **0** = Fehler der Brücke |
| `body` | any | JSON-Body der REST-API als CBOR, `null` bei leerem Body |
| `error` | tstr | nur bei `status` 0, siehe Tabelle |

| `error` | Bedeutung |
|---|---|
| `not_allowed` | Methode/Pfad nicht in der Whitelist |
| `bad_request` | Anfrage formal ungültig |
| `too_large` | Body oder Antwort über der Nachrichtengrenze |
| `busy` | zu viele offene Anfragen (§8) |
| `rate_limited` | Anfragelimit des Geräts überschritten (§8) |
| `read_only` | Verbindung hat keine Schreibberechtigung (§7.6) |
| `backend_unavailable` | Backend antwortet nicht |

Die Box beantwortet jede Anfrage genau einmal. Antworten DÜRFEN in anderer
Reihenfolge als die Anfragen kommen.

**Anmeldung:** Die App sendet keine Tokens. Die Brücke holt sich über die
interne Schnittstelle (§10) ein kurzlebiges Access-Token für den Benutzer, an
den das Gerät gebunden ist, und setzt es als `Authorization: Bearer` an die
REST-API. Die Rechteprüfung bleibt vollständig im Backend: ein normaler
Benutzer bekommt dort für Admin-Endpunkte 403, genau wie im Web-UI.

### 7.4 Brücken-Endpunkte (`/ble/…`)

Diese Pfade beantwortet der BLE-Dienst selbst — auch dann, wenn das Backend
nicht antwortet.

| Methode und Pfad | Wer | Wirkung | Antwort |
|---|---|---|---|
| `POST /ble/pair/confirm` | nur in Kopplungssitzung | schließt die Kopplung ab (§6.3) | `{dev_id, user, role}` |
| `GET /ble/health` | alle | Minimal-Status | `{backend_up, version, uptime_s}` |
| `POST /ble/subscribe` | alle | Body `{topics: ["status"]}` | `{topics}` |
| `POST /ble/unsubscribe` | alle | Body `{topics: […]}` | `{topics}` |
| `POST /ble/backend/restart` | Admin | startet den Dienst `offgridcloud` neu | `{ok: true}` |

In einer Kopplungssitzung ist bis zur Bestätigung nur `/ble/pair/confirm`
erlaubt; jede andere Anfrage beantwortet die Box mit `not_allowed`.

### 7.5 Whitelist (Version 1)

Maßgeblich ist die Whitelist im Box-Dienst. Pfadmuster sind Präfixe auf ganze
Segmente (`/api/network` erlaubt `/api/network` und `/api/network/…`).

| Pfad | Methoden | Bemerkung |
|---|---|---|
| `/api/health` | GET | |
| `/api/network` | alle | WLAN-Scan, bekannte Netze, anwenden (Admin) |
| `/api/transfers` | alle | Liste, Retry |
| `/api/bandwidth` | alle | Gate, Limit, Probe, Uplinks (Admin) |
| `/api/vpn` | alle | Status, verbinden/trennen (Admin) |
| `/api/storage` | alle **außer** `POST /api/storage/format` | Formatieren per Funk ist ausgeschlossen |
| `/api/system/power/{action}` | POST | Neustart/Ausschalten (Admin) |
| `/api/updates` | alle | prüfen, einspielen (Admin) |
| `/api/folders` | GET | Ordner- und Medienliste, ohne Downloads (`…/download` gesperrt) |
| `/api/media/search` | GET | |
| `/api/uplink/handover` | GET, POST, DELETE | Upload-Handover (Companion-Plan §3.6) |

Nicht erreichbar sind insbesondere: Datei-Downloads und Thumbnails,
Provider-Zugangsdaten, Benutzer, Gruppen, Passkeys, HTTPS-Einstellungen,
Pool-Verwaltung, `/api/events` (dafür gibt es §7.7). Antworten mit einem anderen
Content-Type als JSON beantwortet die Brücke mit `bad_request`.

### 7.6 Schreibberechtigung

Je Box hat höchstens **eine** Verbindung Schreibrecht (alle Methoden außer
`GET`). Sie geht an die erste Verbindung, die schreibt. Verbindet sich ein
**Admin**, während ein normaler Benutzer das Schreibrecht hält, übernimmt der
Admin; die bisherige Verbindung erhält das Event `session` mit
`{"write": false}` und danach `read_only` auf Schreibanfragen. Das Schreibrecht
wird frei, wenn die haltende Verbindung endet oder 60 s lang nicht schreibt.

### 7.7 Events (Box → App)

Frames mit `EVENT`-Flag tragen `{ "event": <tstr>, "body": <any> }`.

**`status`** (nach `/ble/subscribe` mit `status`): sofort ein vollständiger
Stand, danach nur bei Änderung, höchstens alle **3 s**, und nur solange
abonniert ist. Basis ist `build_snapshot()` aus `routers/events.py`, mit
denselben Rechten (Admin-Felder nur für Admins):

| Feld | Für | Inhalt |
|---|---|---|
| `folders` | alle | Summen über die sichtbaren Ordner: `{queued, uploading, failed, done}` |
| `transfers` | Admin | `{running, queued, failed}` |
| `kbps` | Admin | aktuelle Übertragungsrate gesamt |
| `gated` | Admin | Bandbreiten-Gate hält Uploads zurück |
| `gate_reason` | Admin | Grund oder `null` |
| `notices` | Admin | höchstens 3 jüngste Hinweise (Text gekürzt auf 120 Zeichen) |

**`session`**: Änderungen der Verbindung, z. B. `{"write": false}` (§7.6).

## 8. Grenzen und Schutzmaßnahmen

| Was | Grenze |
|---|---|
| offene Anfragen je Verbindung | 4, darüber `busy` |
| Anfragen je Gerät | 20 je 10 s, darüber `rate_limited` |
| Kopplungsversuche mit unbekannter `pid` | 5 je 60 s je Box, danach 60 s `locked` (wie der Setup-Assistent) |
| Handshakes je `pid` | 3, danach verbraucht |
| Sitzungs-Handshakes mit unbekanntem Schlüssel | 10 je Minute je Box, danach 60 s `locked` |
| Zusammensetzen einer Nachricht | 10 s |
| Leerlauf einer Verbindung | 5 min ohne Anfrage → Box trennt |

Gefährliche Aktionen (Neustart, Ausschalten, Update, Netz anwenden) bestätigt
die App zusätzlich lokal per Dialog und Biometrie; das ist Sache der App und
nicht Teil des Protokolls.

**Audit:** Jede Kopplung, jeder Widerruf und jede schreibende Anfrage über BLE
erscheint in `/api/system/audit` mit Kanal `ble` und Gerätename.

## 9. Sicherheitsbetrachtung

- **Abhören:** Nach dem Handshake ist alles mit ChaCha20-Poly1305 verschlüsselt
  und authentisiert. Lesbar bleiben nur Werbung und `info`.
- **Fremde Geräte:** Ohne gekoppelten Schlüssel scheitert der Sitzungs-Handshake
  (`ss` und `se` setzen den statischen App-Schlüssel voraus). Ohne gültigen Code
  scheitert die Kopplung.
- **Man-in-the-Middle bei der Kopplung:** Mit QR/Link ist der Box-Schlüssel
  authentisch; ein Angreifer kann `es` nicht berechnen. Beim abgetippten Code
  kann ein Angreifer einen eigenen `pk` vorspiegeln, scheitert aber an der psk.
- **Offline-Raten des Codes:** In `IKpsk2` fließt die psk erst in Nachricht 2
  ein. Wer sich als App ausgibt, erhält eine Nachricht 2 und kann damit offline
  raten — gegen 80 Bit Geheimnis ist das aussichtslos. Wer sich als Box
  ausgibt, bekommt von der App nichts, was von der psk abhängt. Deshalb hat der
  Code 80 Bit Geheimnis statt der anfangs geplanten 6 Ziffern.
- **Replay:** Ephemere Schlüssel je Handshake und implizite Nonces verhindern das
  Wiedereinspielen alter Nachrichten.
- **Forward Secrecy:** Nach `ee` hängen Sitzungsschlüssel nicht mehr allein an
  den statischen Schlüsseln.
- **Verfolgbarkeit:** Werbung und `info.pk` identifizieren eine Box dauerhaft.
  Das ist für ein Gerät, das ohnehin seinen Namen ausstrahlt, hinnehmbar.
- **Kein Eigenbau:** Primitive und Muster kommen aus Noise; vor dem ersten
  Store-Release ist eine externe Prüfung vorgesehen (Companion-Plan, Phase B6).

## 10. Interne Schnittstelle (Box)

Der BLE-Dienst ist eine Brücke ohne eigene Rechte. Für Geräte und Codes fragt
er das Backend über localhost-only Endpunkte, abgesichert durch ein Secret in
`/var/lib/offgridcloud/` (wie der Setup-Status). Benötigt werden mindestens:

- offenen Kopplungscode zu `pid` abfragen und als verbraucht markieren,
- Gerät (statischer Schlüssel, Name, Benutzer) anlegen, nachschlagen, widerrufen,
- kurzlebiges Access-Token für den Benutzer eines Geräts ausstellen,
- Audit-Einträge mit Kanal `ble` schreiben.

Die genaue Form dieser Endpunkte gehört zur Implementierung, nicht zum
Funkprotokoll.

## 11. Testvektoren

`docs/ble/testvectors.json` enthält:

| Schlüssel | Inhalt |
|---|---|
| `advertising` | Manufacturer-Data und Name |
| `framing` | Nachrichten und ihre Frames bei MTU 23 und 185, inkl. leerer Nachricht |
| `cbor` | Anfrage, Antwort, Brückenfehler, Event, `info` als Hex |
| `pairing_code` | `pid`, `secret`, Code, Box-Schlüssel, abgeleitete psk |
| `handshake_pair` | vollständiger `IKpsk2`-Handshake mit festen Schlüsseln, Payloads, Nachrichten, Handshake-Hash, erste Transportnachrichten, Frames |
| `handshake_session` | dasselbe für `IK` |

Box-Dienst (pytest) und App (Jest) MÜSSEN alle Vektoren reproduzieren. Die
Datei wird mit `scripts/ble_testvectors.py` erzeugt; `--check` meldet
Abweichungen. Die Schlüssel darin sind öffentlich und nur für Tests.

## 12. Offene Punkte

- Eigene Bluetooth-SIG-Company-ID statt `0xFFFF` vor einem öffentlichen Release.
- Genaue Felder der `status`-Events nach ersten Messungen der Funklast
  nachschärfen (Ziel: ein Event < 200 Byte).
- Form der internen Schnittstelle (§10) und des Modells für gekoppelte Geräte
  (gemeinsam mit `DeviceCredential` für Desktop-Client und HTTPS-Transport der
  App).
