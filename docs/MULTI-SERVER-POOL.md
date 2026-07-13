# Multi-Server-Pool — mehrere Boxen als Flotte

Sind mehrere OffgridCloud-Boxen im Einsatz — etwa eine pro Fahrzeug bei einem
großen Einsatz —, will man den Überblick nicht pro Box einzeln zusammensuchen.
Der **Pool** macht eine Box zum **Hub**: Sie fragt die hinterlegten Peer-Boxen
periodisch ab und zeigt eine **gemeinsame Flottenübersicht**.

## Prinzip

Pooling ist bewusst **read-only-Aggregation** — kein verteiltes Dateisystem,
keine Konsens-Protokolle, keine Schreibvorgänge über Boxen hinweg. Das hält es
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

## Sicherheit

- **Token verschlüsselt at rest.** Peer-Tokens werden auf dem Hub mit demselben
  Schlüssel wie die Provider-Credentials verschlüsselt (`OGC_SECRET_KEY`) und nie
  an den Browser zurückgegeben.
- **Konstant­zeit-Vergleich.** Die Token-Prüfung nutzt `secrets.compare_digest`.
- **TLS empfohlen.** Da das Token im Header über die Leitung geht, sollten die
  Boxen hinter einem Reverse-Proxy mit TLS stehen (siehe
  [docs/BETRIEB.md](BETRIEB.md), Abschnitt 3) — im Offline-Feld genügt ein
  self-signed Zertifikat.
- **Nur lesend.** Der Hub kann Peers ausschließlich abfragen, nichts verändern.

## Grenzen

- Kein zentrales Verschieben/Umsortieren von Medien zwischen Boxen — die
  Übersicht ist informativ, jede Box verwaltet ihre Uploads selbst.
- Der Hub aggregiert nur **aktive** Peers; ein nicht erreichbarer Peer erscheint
  rot mit Fehlergrund (Timeout, `Pool-Token ungültig`, …).

## API (Kurzreferenz)

| Methode & Pfad | Zweck |
|----------------|-------|
| `GET /api/pool/status` | Kompakter Status dieser Box (Admin **oder** `X-Pool-Token`). |
| `GET /api/pool/overview` | Hub-Sicht: diese Box + alle Peers + Summen (Admin). |
| `GET/POST/PATCH/DELETE /api/pool/peers` | Peers verwalten (Admin). |
| `POST /api/pool/token` · `DELETE /api/pool/token` | Eigenes Pool-Token erzeugen/löschen (Admin). |
