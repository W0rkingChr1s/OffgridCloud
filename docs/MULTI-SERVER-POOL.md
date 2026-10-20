# Multi-Server-Pool — mehrere Boxen als Flotte

Sind mehrere OffgridCloud-Boxen im Einsatz — etwa eine pro Fahrzeug bei einem
großen Einsatz —, will man den Überblick nicht pro Box einzeln zusammensuchen.
Der **Pool** macht eine Box zum **Hub**: Sie fragt die hinterlegten Peer-Boxen
periodisch ab und zeigt eine **gemeinsame Flottenübersicht**.

## Prinzip

Pooling ist im Kern **read-only-Aggregation** — kein verteiltes Dateisystem,
keine Konsens-Protokolle. Einzige Ausnahme ist die **Fernsteuerung von
Transfers**, die jede Box pro Hub ausdrücklich erlauben muss (siehe unten). Das hält es
sicher und sparsam genug für den Raspberry Pi:

- Jede Box liefert unter `GET /api/pool/status` eine **kompakte Statuszusammen­-
  fassung** (Version, Medien nach Status, aktive Transfers, Durchsatz, Speicher).
- Dieser Endpunkt ist geschützt: Er akzeptiert entweder einen Admin-Login **oder**
  ein **Token, das die Box genau diesem Hub ausgestellt hat** (Header
  `X-Pool-Token`). Jeder Hub hat sein eigenes.
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

### Der schnelle Weg: finden und per Code koppeln

Boxen im selben Netz **finden sich automatisch** (mDNS, Dienst
`_offgridcloud._tcp`). So koppelt man eine Box an den Hub:

1. **An der Peer-Box:** Web-UI → **Pool** → **„Kopplungscode erzeugen“** —
   oder an der Konsole der Box *Pool → Kopplungscode für einen Hub anzeigen*.
   Es erscheint ein **6-stelliger Code**, 10 Minuten gültig, einmal verwendbar.
2. **Am Hub:** Web-UI → **Pool** → *In der Nähe gefunden* → bei der Box
   **„Koppeln …“**, Code eingeben.

Fertig: Die Box ist Peer des Hubs, mit einem eigenen Token mit Recht
**nur lesen**. *Steuern* vergibt man danach bewusst an der Peer-Box (siehe
unten). Steht eine Box nicht in der Liste (anderes Subnetz, Docker ohne
Host-Netz), geht dasselbe per *„Box nicht in der Liste? Per Adresse + Code
koppeln“* mit ihrer URL.

**Voraussetzungen:** Hub und Peer im selben Netz (Multicast wird von Routern
nicht zwischen Subnetzen weitergereicht), UDP-Port 5353 offen. Im
**Docker-Container** nur mit `network_mode: host` (siehe `docker-compose.yml`) —
im Standard-Bridge-Netz bleibt die Liste leer, die Seite sagt das dazu. Wer eine
Box nicht im Netz zeigen will, schaltet *„Diese Box im Netz anzeigen (mDNS)“* aus
oder setzt `OGC_MDNS_ENABLED=false` (dann sucht sie auch selbst nicht).

### Von Hand: Token erzeugen und eintragen

#### 1. Auf jeder Peer-Box: Token für den Hub erzeugen

Web-UI → **Pool** → *Zugriff für Hubs auf diese Box*: Namen des Hubs eintragen
(z. B. `ELW`), Recht wählen, **„Token erzeugen“**. Das Token wird **einmalig**
angezeigt — kopieren. Ohne Token ist die Box nicht abfragbar (Standard).

| Recht | Darf |
|-------|------|
| **Nur lesen** (Standard) | Status abfragen, Job-Liste mit Dateinamen, Ziel und Fehlertext ansehen |
| **Lesen + Transfers steuern** | zusätzlich Uploads pausieren/fortsetzen, Fehlgeschlagene neu anstoßen — alle auf einmal oder einzeln |

Auch *nur lesen* zeigt damit **Dateinamen** der Box. Wer einem Hub nicht einmal
die zeigen will, gibt ihm kein Token.

Fragen mehrere Hubs dieselbe Box ab, bekommt **jeder sein eigenes Token**. Die
Liste zeigt pro Token, wann und von welchem Hub (Hostname) es zuletzt benutzt
wurde. Recht ändern oder Zugriff entziehen wirkt sofort und nur für diesen Hub.

Die URL der Box ist die, unter der der Hub sie erreicht, z. B.
`https://box2.local:8000` oder `http://10.0.0.12:8000`.

#### 2. Auf der Hub-Box: Peers eintragen

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

Vom Hub aus lassen sich auf jeder Box, deren Token für diesen Hub das Recht
*steuern* hat:

- **Uploads pausieren / fortsetzen** — pausiert starten keine neuen Uploads;
  laufende werden noch fertig. Typisch: Eine Box soll die gemeinsame Leitung
  kurz freigeben, weil eine andere Wichtigeres zu senden hat.
- **Fehlgeschlagene Transfers erneut anstoßen** — alle auf einmal, Zähler und
  Fehlertext werden zurückgesetzt.
- **Einzelne Jobs erneut anstoßen** — über die Job-Liste (siehe unten).

Die Buttons stehen direkt auf der Kachel des Knotens. Die eigene Box lässt sich
immer steuern; dieselben Aktionen gibt es lokal auf der Seite **Transfers**.

### Job-Liste eines Peers

**„Jobs ansehen“** auf einer Kachel klappt darunter die Transfers dieser Box auf:
Datei, Ziel, Versuche, Zeitpunkt und bei Fehlschlägen der **Fehlertext** — also
*warum* eine Box nicht hochlädt (Zugangsdaten falsch, Ziel voll, Netz weg …).
Filter: Fehlgeschlagen (Standard), Wartend, Läuft, Alle. Pro Peer werden die 100
zuletzt geänderten Jobs gezeigt, live vom Peer geholt.

Mit Recht *steuern* hat jeder fehlgeschlagene oder wartende Job einen Button
**„Erneut“**. Das ist der richtige Weg, wenn nur ein Job hängt und man nicht
alle anderen Fehlschläge gleich mit neu anstoßen will.

### Erlauben (auf der Peer-Box)

Web-UI → **Pool** → *Zugriff für Hubs auf diese Box* → beim Token des Hubs das
Recht auf **„Lesen + steuern“** stellen (oder das Token gleich so erzeugen).
**Standard: nur lesen.** Bei einem Lese-Token zeigt der Hub auf der Kachel
„Nur Lesezugriff“ und keine Buttons.

### Was bewusst nicht geht

Der Hub kann **nichts löschen, keine Einstellungen ändern und keine Daten
abrufen**. Die drei Aktionen sind fest verdrahtet (`pause`, `resume`,
`retry_failed`), alles andere lehnt die Box ab. Schlimmstenfalls hält ein
Missbrauch des Tokens Uploads an — das sieht das Team vor Ort sofort (gelber
Hinweis auf **Transfers**, Status unter **Bandbreite**) und hebt es mit einem
Klick wieder auf.

Jede Aktion steht im **Audit-Log** beider Boxen: auf dem Hub mit dem
ausführenden Admin, auf dem Peer als `pool.remote_…` mit dem Token-Namen und dem
Hostnamen des Hubs.

## Gemeinsame Leitung: Vorrang und faires Teilen

### Vorrang

Teilen sich mehrere Boxen **eine** Leitung — etwa ein Starlink am Einsatzort —,
konkurrieren ihre Uploads um dieselbe Bandbreite. Mit **Vorrang** legt man am
Hub eine Reihenfolge fest: Web-UI → **Pool** → *Gemeinsame Leitung*, pro Box
**1 – hoch**, **2 – mittel**, **3 – niedrig** oder **nicht verwaltet**, dann
„Vorrang“ anhaken und **Speichern**.

Der Hub prüft jede Minute: **Hat eine Box mit höherem Rang etwas zu senden,
pausiert er die Boxen mit niedrigerem Rang, die ebenfalls senden wollen** —
und setzt sie fort, sobald die Leitung wieder frei ist. Gleicher Rang teilt
sich die Leitung. Beispiel: ELW-Lagebilder (Rang 1) vor Drohnen-Rohmaterial
(Rang 3).

**Wichtig:** Nur Boxen eintragen, die sich wirklich eine Leitung teilen. Eine
Box mit eigenem Uplink zu pausieren, verschenkt nur Bandbreite — die bleibt auf
„nicht verwaltet“.

Regeln, damit Hub und Team nicht gegeneinander arbeiten:

- Der Hub setzt **nur fort, was er selbst pausiert hat** (das merkt er sich
  auch über einen Neustart hinweg). Eine von Hand pausierte Box bleibt
  pausiert und gilt nicht als „sendend“.
- Setzt jemand an einer Box eine Vorrang-Pause **von Hand fort**, lässt der Hub
  diese Box in Ruhe, bis die höherrangige Box fertig ist („✋ … Vorrang ruht für
  diese Box“).
- Ausschalten oder eine Box aus der Reihenfolge nehmen gibt pausierte Boxen
  sofort wieder frei.
- Pausieren braucht das Recht **steuern** im Token, das der Peer diesem Hub
  ausgestellt hat. Fehlt es, sagt die Pool-Seite das, und die Meldungen nennen
  es einmal („⚠️ … Vorrang nicht anwendbar“). Für die Box mit dem höchsten Rang
  ist es nicht nötig — sie wird nie pausiert.
- Laufende Uploads werden nicht abgebrochen; die niedrigeren Boxen starten nur
  keine neuen. Reaktionszeit: bis zu einer Minute.
- Was der Vorrang tut, steht in den gebündelten Meldungen
  („⏸ Drohne: Uploads pausiert – Vorrang für ELW“) und auf den Kacheln
  („Rang 3“, „pausiert (Vorrang)“).

### Bandbreite fair teilen

Im selben Abschnitt (**Pool → Gemeinsame Leitung**) lässt sich die
**Kapazität der Leitung** eintragen (KB/s, am besten der gemessene Upload, mit
etwas Luft nach unten) und **„Bandbreite fair teilen“** einschalten. Dann
teilen sich alle Boxen mit Rang die Leitung:

- Jede Minute bekommt jede **sendende** Box *Kapazität ÷ Anzahl Sendender*.
  Gerade nicht sendende (oder per Vorrang pausierte) Boxen bekommen schon den
  Anteil, den sie beim Einstieg hätten, damit ein Neuling nicht kurz die ganze
  Leitung belegt. Beispiel 1200 KB/s: zwei senden → je 600; eine pausiert → die
  andere 1200, die pausierte vorsorglich 600.
- Der Anteil wirkt **zusätzlich** zum eigenen Limit der Box — das kleinere gilt.
  Die Box zeigt ihn unter **Bandbreite → Aktuelles Limit**, der Hub auf der
  Kachel („≤ 600 KB/s“).
- **Wirkt ab der nächsten Datei:** rclone bekommt das Limit beim Start eines
  Uploads. Ein schon laufender großer Upload behält sein bisheriges Tempo, bis
  er fertig ist.
- **Läuft ab:** Erneuert der Hub den Anteil nicht (ausgeschaltet, Hub weg,
  Netz weg), fällt er nach 3 Minuten von selbst weg — keine Box bleibt
  dauerhaft gedrosselt. Ausschalten hebt die Anteile sofort auf.
- Braucht bei **jeder** Box mit Rang das Recht *steuern* (auch beim höchsten
  Rang, anders als beim Vorrang). Fehlt es, warnt die Pool-Seite, und die
  Meldungen nennen es einmal.
- Vorrang und faires Teilen lassen sich kombinieren: Erst pausiert der Vorrang
  niedrigere Ränge, dann wird die Leitung unter den übrigen Sendern geteilt.

## Meldungen vom Hub

Der Hub prüft seine aktiven Peers **jede Minute** und schickt bei Änderungen
**eine gebündelte Nachricht** über seine Kanäle (Telegram, E-Mail, Webhook;
eingerichtet unter **System → Benachrichtigungen**), z. B.:

```
🛰️ Pool: 2 Änderungen
🔴 Box B – Boot: nicht erreichbar (keine Antwort (Zeitüberschreitung))
💾 Drohne: Speicher knapp – noch 1.2 GB frei (98 % belegt)
```

| Meldung | Wann |
|---------|------|
| 🔴 nicht erreichbar | nach **2 Fehlversuchen in Folge** (ein einzelner Aussetzer meldet nichts) |
| 🟢 wieder erreichbar | sobald die Box wieder antwortet |
| 💾 Speicher knapp | unter 10 % oder 500 MB frei — einmal, erneut erst nach Erholung |
| ⏸ / ▶️ Uploads pausiert / fortgesetzt | bei jedem Wechsel (von Hand, am Hub oder an der Box) |
| ❌ neue fehlgeschlagene Transfers | wenn die Zahl einen neuen Höchststand erreicht |

Gemeldet werden **nur Wechsel**: Eine Box, die offline bleibt, erscheint einmal,
nicht jede Minute. Fehlschläge: Die Box reiht Fehlgeschlagene alle 15 Minuten
automatisch neu ein, die Zahl fällt dabei kurz auf 0 — deshalb meldet der Hub
erst wieder, wenn eine Stunde lang nichts mehr fehlgeschlagen ist oder es mehr
werden als zuvor. Ein dauerhaft kaputtes Ziel nervt also nicht alle 15 Minuten.
Nach einem Neustart merkt sich der Hub nur den Ist-Zustand; was dann schon
offline ist, steht in der Start-Zusammenfassung („Pool: 1/2 Geräte verbunden“).

**Empfehlung:** Benachrichtigungskanäle nur am **Hub** einrichten. Dann kommt
alles Flottenweite gebündelt von dort, statt dass jede Box einzeln funkt. Wer
an einer Peer-Box zusätzlich eigene Kanäle hat, bekommt deren Meldungen
weiterhin auch. Abschalten am Hub: **System → Benachrichtigungen → „Pool-
Änderungen (nur am Hub)“**.

## Sicherheit

- **Auf der Peer-Box nur als Hash.** Ausgestellte Tokens speichert die Box nur
  als SHA-256; der Klartext erscheint einmal beim Erzeugen. Wer die Datenbank
  liest, kann sich damit nicht als Hub ausgeben.
- **Auf dem Hub verschlüsselt.** Peer-Tokens werden auf dem Hub mit demselben
  Schlüssel wie die Provider-Credentials verschlüsselt (`OGC_SECRET_KEY`) und nie
  an den Browser zurückgegeben.
- **TLS empfohlen.** Da das Token im Header über die Leitung geht, sollten die
  Boxen hinter einem Reverse-Proxy mit TLS stehen (siehe
  [docs/BETRIEB.md](BETRIEB.md), Abschnitt 3) — im Offline-Feld genügt ein
  self-signed Zertifikat.
- **Gefunden heißt nicht vertraut.** mDNS ist nicht authentifiziert — jeder im
  LAN kann sich als OffgridCloud-Box ausgeben. Die Liste macht deshalb nur
  *Vorschläge*: Zugriff gibt es erst über den Kopplungscode, den man an der
  echten Box abliest. Der Hub schickt nie ein gespeichertes Token an eine
  Adresse, die er nur per mDNS kennt; ändert sich die IP eines Peers, koppelt
  man neu (der Peer wird dabei aktualisiert, nicht verdoppelt).
- **Kopplungscode.** 6 Ziffern, 10 Minuten gültig, nach 5 Fehlversuchen
  verbrannt — Raten hat damit eine Chance von 5 zu einer Million, und für jeden
  neuen Code muss jemand an der Box sein. Kopplung gibt nur Leserecht.
- **Ein Token pro Hub, Rechte pro Token.** Ein verlorener oder fremder Hub wird
  gesperrt, indem man sein Token löscht — die anderen Hubs merken nichts davon.
  Tokens starten mit *nur lesen*; *steuern* muss man bewusst vergeben.
- **Umstieg von älteren Versionen.** Das frühere gemeinsame Token wird beim
  Start automatisch als „Bisheriges Token“ (*nur lesen*) übernommen; Hubs, die
  es benutzen, laufen weiter. Empfehlung: jedem Hub ein eigenes Token ausstellen,
  beim Hub eintragen und das bisherige danach löschen.

## Grenzen

- Kein zentrales Verschieben/Umsortieren von Medien zwischen Boxen — jede Box
  verwaltet ihre Uploads selbst; der Hub kann sie nur anhalten, fortsetzen und
  Jobs neu anstoßen, nicht löschen oder umpriorisieren.
- Pausieren bricht laufende Uploads nicht ab.
- Der Hub aggregiert nur **aktive** Peers; ein nicht erreichbarer Peer erscheint
  rot mit Fehlergrund (Timeout, `Pool-Token ungültig`, …).

## API (Kurzreferenz)

| Methode & Pfad | Zweck |
|----------------|-------|
| `GET /api/pool/status` | Kompakter Status dieser Box (Admin **oder** `X-Pool-Token`). |
| `POST /api/pool/control` | Transfer-Aktion auf dieser Box (Admin **oder** `X-Pool-Token` mit Recht *steuern*). Body: `{"action": "pause" \| "resume" \| "retry_failed"}`. |
| `GET /api/pool/jobs?status=…` | Transfer-Jobs dieser Box, max. 100 (Admin **oder** `X-Pool-Token`). |
| `POST /api/pool/jobs/{id}/retry` | Einen Job neu einreihen (Admin **oder** `X-Pool-Token` mit Recht *steuern*). |
| `POST /api/pool/peers/{id}/control` | Hub leitet eine Aktion an einen Peer weiter (Admin). |
| `GET /api/pool/peers/{id}/jobs?status=…` · `POST /api/pool/peers/{id}/jobs/{job}/retry` | Hub holt die Job-Liste eines Peers bzw. stößt dort einen Job neu an (Admin). |
| `GET/POST /api/transfers/control` | Dieselben Aktionen lokal (Admin). |
| `GET /api/pool/overview` | Hub-Sicht: diese Box + alle Peers + Summen (Admin). |
| `GET/POST/PATCH/DELETE /api/pool/peers` | Peers verwalten (Admin). |
| `GET /api/pool/discovery` · `PUT /api/pool/discovery` | Per mDNS gefundene Boxen (mit Hinweis, ob schon Peer); eigene Ankündigung an/aus (Admin). |
| `GET/PUT /api/pool/priority` | Gemeinsame Leitung: Vorrang an/aus, Rang pro Box (`peer_id` null = diese Box; 0 = nicht verwaltet, 1–3), `share_enabled` + `share_kbps` für faires Teilen (Admin). |
| `POST /api/pool/limit` | Hub setzt den Leitungsanteil dieser Box `{kbps}` (0 hebt auf; läuft nach 3 min ohne Erneuerung ab) (Admin **oder** `X-Pool-Token` mit Recht *steuern*). |
| `GET/POST/DELETE /api/pool/pairing` | Kopplungscode dieser Box anzeigen/erzeugen/verwerfen (Admin). |
| `POST /api/pool/pair` | Hub tauscht Code gegen Lese-Token (ohne Login; der Code ist der Nachweis). |
| `POST /api/pool/peers/pair` | Hub koppelt sich mit `{base_url, code}` und legt den Peer an (Admin). |
| `GET/POST /api/pool/tokens` | Tokens für Hubs auflisten/erzeugen; Body `{"name", "scope": "read" \| "control"}`. Klartext nur in der POST-Antwort (Admin). |
| `PATCH/DELETE /api/pool/tokens/{id}` | Recht/Name ändern, Zugriff entziehen (Admin). |

## Ausbauplan

Wohin sich der Pool sinnvoll entwickeln kann — sortiert nach Nutzen pro Aufwand.
Umgesetzt ist bisher nur, was mit ☑ markiert ist.

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
| ☑ **Job-Liste des Peers im Hub** | Fehlgeschlagene Jobs eines Peers mit Fehlertext ansehen und einzeln neu anstoßen, statt nur „alle“. Baut direkt auf der Fernsteuerung auf. | klein |
| ☑ **Peer-Tokens pro Hub** | Statt eines geteilten Tokens bekommt jeder Hub ein eigenes, mit Bereich *lesen* oder *steuern*. Einen Hub sperren, ohne alle anderen neu einzurichten. **Voraussetzung für Stufe 3**, da Relay deutlich mehr Rechte braucht. | klein–mittel |
| ☑ **Peers automatisch finden** (mDNS, `_offgridcloud._tcp`) | Boxen im selben Netz tauchen als Vorschlag auf; Kopplung per 6-stelligem Code statt Token-Copy-Paste. Spart im Einsatz Minuten und Tippfehler. | mittel |
| ☑ **Pool-Meldungen gebündelt** | Der Hub meldet Flotten-Ereignisse (Box offline, Box voll, Box pausiert) einmal per Telegram/E-Mail, statt dass jede Box einzeln funkt. Passt zum Telegram-Problem mehrerer Boxen aus 0.7.1. | mittel |

### Stufe 2 — Koordination der Leitung

| Baustein | Nutzen | Aufwand |
|---|---|---|
| ☑ **Prioritäten pro Box** | Der Hub legt eine Reihenfolge fest (z. B. „ELW-Lagebilder vor Drohnen-Rohmaterial“). Boxen mit niedriger Priorität pausieren automatisch, solange eine höhere etwas sendet. Technisch: der Hub nutzt die vorhandene Fernsteuerung im Takt der Abfrage. | mittel |
| ☑ **Bandbreite fair teilen** | Teilen sich Boxen einen Uplink, verteilt der Hub das KB/s-Limit (`bwlimit`) auf die Boxen mit wartenden Jobs. Braucht eine zusätzliche Steuer-Aktion „Limit setzen“ und eine Angabe pro Peer „hängt am selben Uplink wie …“. | mittel |
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
