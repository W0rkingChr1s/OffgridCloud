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
sudo ./deploy/install.sh --start --with-ap-fallback
```

`--with-ap-fallback` installiert NetworkManager (+ `dnsmasq-base` für die
AP-DHCP, `iw` für die Funkregulierung), richtet den Watchdog-Dienst ein, legt
eine eng gefasste sudoers-Regel für **nur** `apply.sh` an und verdrahtet
`OGC_NET_APPLY_COMMAND` in der `.env`.

### Nachträglich

```bash
sudo /opt/offgridcloud/deploy/netfallback/install.sh
# (im rohen Checkout: sudo ./deploy/netfallback/install.sh)
```

### Im Web-UI

Unter **Netzwerk** (nur Admin):

1. **Rückfall-WLAN aktivieren** und AP-Name/-Passwort setzen (WPA2, 8–63
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
- **Tuning:** Prüf-Intervall und die Zahl der Fehlversuche bis zum Umschalten
  sind im UI einstellbar. Ein „Anwenden“ startet den Watchdog neu, damit ein
  geändertes Intervall sofort greift.

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
- **Kein NetworkManager?** Auf Systemen mit `dhcpcd`/`systemd-networkd` steuert
  die App nichts aktiv. Die Konfiguration wird trotzdem exportiert; die
  Umsetzung müsste dann ein eigenes Setup (hostapd/dnsmasq) übernehmen.
