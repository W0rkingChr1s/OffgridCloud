# Multi-Server-Pool — mehrere Boxen als Flotte

Sind mehrere OffgridCloud-Boxen im Einsatz — etwa eine pro Fahrzeug bei einem
großen Einsatz —, will man den Überblick nicht pro Box einzeln zusammensuchen.
Der **Pool** macht eine Box zum **Hub**: Sie fragt die hinterlegten Peer-Boxen
periodisch ab und zeigt eine **gemeinsame Flottenübersicht**.

## Prinzip

Pooling ist im Kern **read-only-Aggregation** — kein verteiltes Dateisystem,
keine Konsens-Protokolle. Einzige Ausnahme ist die **Fernsteuerung von
Transfers**, die jede Box ausdrücklich freigeben muss (siehe unten). Das hält es
sicher und sparsam genug für den Raspberry Pi:

- Jede Box liefert unter `GET /api/pool/status` eine **kompakte Statuszusammen­-
  fassung** (Version, Medien nach Status, aktive Transfers, Durchsatz, Speicher).
- Dieser Endpunkt ist geschützt: Er akzeptiert entweder einen Admin-Login **oder**
  ein **Shared-Token** der Box (Header `X-Pool-Token`).
- Der Hub fragt alle aktiven Peers **nebenläufig** mit kurzem Timeout ab und
  summiert die Werte. Nicht erreichbare Peers werden markiert, blockieren aber
  nichts.

```
        ┌───────────┐   X-Pool-Token   ┌───────────┐
        │  Hub-Box  │ ───────────────▶ │  Box 2    │  /api/pool/status
        │ (Übersicht)│ ───────────────▶ │  Box 3    │  (kompakter Status)
        └───────────┘                  └───────────┘
```

## Einrichtung

### 1. Auf jeder Peer-Box: Token erzeugen

Web-UI → **Pool** → **„Token erzeugen“**. Das Token wird **einmalig** angezeigt
— kopieren. Ohne Token ist die Box nicht abfragbar (Standard).

Die URL der Box ist die, unter der der Hub sie erreicht, z. B.
`https://box2.local:8000` oder `http://10.0.0.12:8000`.

### 2. Auf der Hub-Box: Peers eintragen

Web-UI → **Pool** → Formular *Peers verwalten*:

| Feld  | Beispiel |
|-------|----------|
| Name  | `Box 2 – Boot` |
| URL   | `https://box2.local:8000` |
| Token | *(das in Schritt 1 erzeugte Token)* |

Danach erscheinen alle Knoten als Kacheln mit Live-Werten; oben stehen die
Flotten-Summen. Peers lassen sich **pausieren** (bleiben gespeichert, werden
aber nicht abgefragt) oder **entfernen**.

## Fernsteuerung von Transfers

Vom Hub aus lassen sich auf jeder Box, die es erlaubt:

- **Uploads pausieren / fortsetzen** — pausiert starten keine neuen Uploads;
  laufende werden noch fertig. Typisch: Eine Box soll die gemeinsame Leitung
  kurz freigeben, weil eine andere Wichtigeres zu senden hat.
- **Fehlgeschlagene Transfers erneut anstoßen** — alle auf einmal, Zähler und
  Fehlertext werden zurückgesetzt.

Die Buttons stehen direkt auf der Kachel des Knotens. Die eigene Box lässt sich
immer steuern; dieselben Aktionen gibt es lokal auf der Seite **Transfers**.

### Freigeben (auf der Peer-Box)

Web-UI → **Pool** → *Diese Box als Peer freigeben* → **„Fernsteuerung durch Hub
erlauben“**. Der Schalter erscheint, sobald ein Pool-Token existiert.
**Standard: aus** — das Token allein erlaubt nur Lesen. Ohne Freigabe zeigt der
Hub auf der Kachel „Fernsteuerung auf dieser Box nicht freigegeben“.

### Was bewusst nicht geht

Der Hub kann **nichts löschen, keine Einstellungen ändern und keine Daten
abrufen**. Die drei Aktionen sind fest verdrahtet (`pause`, `resume`,
`retry_failed`), alles andere lehnt die Box ab. Schlimmstenfalls hält ein
Missbrauch des Tokens Uploads an — das sieht das Team vor Ort sofort (gelber
Hinweis auf **Transfers**, Status unter **Bandbreite**) und hebt es mit einem
Klick wieder auf.

Jede Aktion steht im **Audit-Log** beider Boxen: auf dem Hub mit dem
ausführenden Admin, auf dem Peer als `pool.remote_…` mit dem Hostnamen des Hubs.

## Sicherheit

- **Token verschlüsselt at rest.** Peer-Tokens werden auf dem Hub mit demselben
  Schlüssel wie die Provider-Credentials verschlüsselt (`OGC_SECRET_KEY`) und nie
  an den Browser zurückgegeben.
- **Konstant­zeit-Vergleich.** Die Token-Prüfung nutzt `secrets.compare_digest`.
- **TLS empfohlen.** Da das Token im Header über die Leitung geht, sollten die
  Boxen hinter einem Reverse-Proxy mit TLS stehen (siehe
  [docs/BETRIEB.md](BETRIEB.md), Abschnitt 3) — im Offline-Feld genügt ein
  self-signed Zertifikat.
- **Lesen per Token, Steuern nur nach Freigabe.** Ohne den Schalter auf der
  Peer-Box kann der Hub ausschließlich abfragen. Mit Freigabe kommen genau die
  drei Transfer-Aktionen hinzu.
- **Ein Token pro Box, nicht pro Hub.** Alle Hubs, die eine Box abfragen, teilen
  sich ihr Token. Wer einem Hub den Zugriff entziehen will, muss das Token neu
  erzeugen und bei den übrigen Hubs neu eintragen.

## Grenzen

- Kein zentrales Verschieben/Umsortieren von Medien zwischen Boxen und keine
  Steuerung einzelner Jobs — jede Box verwaltet ihre Uploads selbst; der Hub kann
  sie nur anhalten, fortsetzen und Fehlgeschlagene neu anstoßen.
- Pausieren bricht laufende Uploads nicht ab.
- Der Hub aggregiert nur **aktive** Peers; ein nicht erreichbarer Peer erscheint
  rot mit Fehlergrund (Timeout, `Pool-Token ungültig`, …).

## API (Kurzreferenz)

| Methode & Pfad | Zweck |
|----------------|-------|
| `GET /api/pool/status` | Kompakter Status dieser Box (Admin **oder** `X-Pool-Token`). |
| `POST /api/pool/control` | Transfer-Aktion auf dieser Box (Admin **oder** `X-Pool-Token` + Freigabe). Body: `{"action": "pause" \| "resume" \| "retry_failed"}`. |
| `POST /api/pool/peers/{id}/control` | Hub leitet eine Aktion an einen Peer weiter (Admin). |
| `GET/PUT /api/pool/self` | Eigenes Token anzeigen, Fernsteuerung freigeben (`control_enabled`) (Admin). |
| `GET/POST /api/transfers/control` | Dieselben Aktionen lokal (Admin). |
| `GET /api/pool/overview` | Hub-Sicht: diese Box + alle Peers + Summen (Admin). |
| `GET/POST/PATCH/DELETE /api/pool/peers` | Peers verwalten (Admin). |
| `POST /api/pool/token` · `DELETE /api/pool/token` | Eigenes Pool-Token erzeugen/löschen (Admin). |

## Ausbauplan

Wohin sich der Pool sinnvoll entwickeln kann — sortiert nach Nutzen pro Aufwand.
Noch nichts davon ist umgesetzt.

### Vorweg: Wann bringt Lastverteilung überhaupt etwas?

„Lastverteilung“ heißt hier: Eine Box mit schlechter Leitung gibt Dateien an eine
Box mit besserer Leitung ab, die sie dann hochlädt. Das lohnt sich **nur, wenn
zwei Bedingungen gleichzeitig gelten**:

1. Die Boxen haben **unterschiedliche Uplinks** (z. B. ELW mit Starlink, Boot nur
   mit LTE).
2. Zwischen ihnen gibt es eine **schnelle lokale Verbindung** (gemeinsames WLAN,
   Kabel, Mesh), die deutlich schneller ist als der schwächere Uplink.

Hängen alle Boxen am selben Router oder Starlink, bringt das Verschieben von
Dateien **nichts** — der Engpass bleibt dieselbe Leitung. Dann hilft nur,
die Leitung **fair oder nach Priorität aufzuteilen** (Stufe 2). Deshalb kommt die
Koordination vor dem Relay.

### Stufe 1 — Grundlagen (klein, sofort nützlich)

| Baustein | Nutzen | Aufwand |
|---|---|---|
| **Job-Liste des Peers im Hub** | Fehlgeschlagene Jobs eines Peers mit Fehlertext ansehen und einzeln neu anstoßen, statt nur „alle“. Baut direkt auf der Fernsteuerung auf. | klein |
| **Peer-Tokens pro Hub** | Statt eines geteilten Tokens bekommt jeder Hub ein eigenes, mit Bereich *lesen* oder *steuern*. Einen Hub sperren, ohne alle anderen neu einzurichten. **Voraussetzung für Stufe 3**, da Relay deutlich mehr Rechte braucht. | klein–mittel |
| **Peers automatisch finden** (mDNS, `_offgridcloud._tcp`) | Boxen im selben Netz tauchen als Vorschlag auf; Kopplung per 6-stelligem Code statt Token-Copy-Paste. Spart im Einsatz Minuten und Tippfehler. | mittel |
| **Pool-Meldungen gebündelt** | Der Hub meldet Flotten-Ereignisse (Box offline, Box voll, Box pausiert) einmal per Telegram/E-Mail, statt dass jede Box einzeln funkt. Passt zum Telegram-Problem mehrerer Boxen aus 0.7.1. | mittel |

### Stufe 2 — Koordination der Leitung

| Baustein | Nutzen | Aufwand |
|---|---|---|
| **Prioritäten pro Box** | Der Hub legt eine Reihenfolge fest (z. B. „ELW-Lagebilder vor Drohnen-Rohmaterial“). Boxen mit niedriger Priorität pausieren automatisch, solange eine höhere etwas sendet. Technisch: der Hub nutzt die vorhandene Fernsteuerung im Takt der Abfrage. | mittel |
| **Bandbreite fair teilen** | Teilen sich Boxen einen Uplink, verteilt der Hub das KB/s-Limit (`bwlimit`) auf die Boxen mit wartenden Jobs. Braucht eine zusätzliche Steuer-Aktion „Limit setzen“ und eine Angabe pro Peer „hängt am selben Uplink wie …“. | mittel |
| **Fernsteuerung im Companion** | Pool-Übersicht und die drei Aktionen in der App (steht im Companion-Plan schon als „Pool-Übersicht“). | mittel |

### Stufe 3 — Echte Lastverteilung (Relay)

**Ablauf:** Box A erkennt, dass ihre Warteschlange bei der aktuellen Leitung
viel länger braucht als bei Box B. Sie schickt Dateien über das LAN an B, B lädt
sie ins Ziel hoch und meldet „fertig“ zurück. A behält ihre lokale Kopie, bis B
das bestätigt.

Kernfragen, die vorher geklärt sein müssen:

- **Zugangsdaten:** B braucht Schreibzugriff auf das Ziel von A. Sauberste
  Lösung: **dasselbe Ziel ist auf beiden Boxen eingerichtet** (Zuordnung über
  einen Ziel-Schlüssel), A überträgt *keine* Zugangsdaten. Ohne passendes Ziel
  auf B findet kein Relay statt.
- **Entscheidungsregel:** Abgeben nur, wenn `geschätzte Dauer auf A >
  LAN-Übertragung + Warteschlange von B + Upload auf B` mit deutlichem Abstand
  (z. B. Faktor 2), damit Dateien nicht hin und her pendeln. Nie an eine Box,
  deren getaktetes Tagesvolumen knapp ist oder deren Speicher fast voll ist.
- **Doppelte Uploads vermeiden:** Prüfung über `sha256` (liegt pro Medium schon
  vor). Bricht B ab, holt A den Job zurück.
- **Nachvollziehbarkeit:** Im Medium steht, welche Box es tatsächlich
  hochgeladen hat.

Aufwand: **groß**. Erst angehen, wenn sich im Einsatz zeigt, dass Boxen
tatsächlich unterschiedlich gut angebunden nebeneinander stehen.

### Stufe 4 — Ausfallsicherheit

| Baustein | Nutzen | Aufwand |
|---|---|---|
| **Spiegelung vor dem Upload** | Für ausgewählte Ordner legt die Box sofort nach Empfang eine Kopie auf einer zweiten Box ab. Geht eine Box verloren (Diebstahl, Wasser, Brand), bevor alles hochgeladen ist, sind die Daten nicht weg. Nutzt dieselbe LAN-Übertragung wie das Relay. | mittel (nach Stufe 3) |
| **Speicher-Ausgleich** | Läuft eine Box voll, lagert sie wartende Dateien auf eine Box mit Platz aus, statt neue Uploads abzulehnen. | mittel (nach Stufe 3) |

### Bewusst nicht geplant

- **Konfiguration zentral verteilen** (Ziele, Ordner, Zugangsdaten vom Hub auf
  alle Boxen schieben): Ein kompromittierter Hub hätte dann die Zugangsdaten der
  ganzen Flotte. Besser: Export/Import einer Vorlage, die ein Admin auf jeder Box
  bewusst einspielt.
- **Updates fernauslösen:** Ein misslungenes Update auf allen Boxen gleichzeitig
  mitten im Einsatz ist das schlimmste mögliche Szenario. Der Hub zeigt die
  Versionen an; aktualisiert wird vor Ort, Box für Box.
- **Verteiltes Dateisystem / Konsens:** Zu schwer für den Pi und für die
  Netzqualität im Feld. Jede Box bleibt allein lauffähig — das ist der
  wichtigste Grundsatz und gilt auch für alle Stufen oben.
