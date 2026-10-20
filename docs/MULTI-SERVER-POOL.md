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

