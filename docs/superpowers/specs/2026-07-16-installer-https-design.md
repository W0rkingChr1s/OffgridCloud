# Design: HTTPS-Einrichtung im Installer + konfigurierbarer mDNS-Hostname

**Datum:** 2026-07-16
**Status:** Genehmigt (Design), bereit für Implementierungsplan

## Ziel

OffgridCloud lässt sich heute nur über plain HTTP erreichen; TLS ist ein
manueller, separater Schritt (`deploy/Caddyfile` / `deploy/nginx.conf.example`
von Hand editieren, siehe `docs/BETRIEB.md` §3). Das soll in den interaktiven
Installer wandern, damit eine frische Installation ohne Zusatzwissen per HTTPS
erreichbar ist — sowohl im **Offline-/Feldeinsatz** (nur LAN, selbstsigniertes
Zertifikat) als auch **zuhause mit echter Domain** (automatisches
Let's-Encrypt-Zertifikat). Zusätzlich wird der mDNS-Hostname beim Install
konfigurierbar (Vorgabe `offgridcloud.local`).

Motivation: Diese Einrichtung ist auch die Voraussetzung dafür, später
WebAuthn/Passkeys anzubieten (die brauchen einen stabilen HTTPS-Origin).

## Kernentscheidungen (aus dem Brainstorming)

1. **Caddy statt nginx.** Caddy bringt Auto-TLS (Let's Encrypt) und
   `tls internal` (selbstsigniert für offline) out of the box. nginx bleibt als
   manuell dokumentierte Alternative bestehen, wird aber nicht automatisiert.
2. **Beide Modi immer gleichzeitig aktiv (Ansatz B).** Es gibt kein
   exklusives „Modus-Umschalten". Der LAN-Block (`<hostname>.local`,
   `tls internal`) ist **immer** aktiv. Ein Domain-Block mit echtem Zertifikat
   wird **additiv** angehängt, sobald eine Domain hinterlegt ist. LAN-Zugriff
   geht dadurch nie verloren. „Umschalten" = Domain setzen bzw. entfernen.
3. **Standard-Hostname `offgridcloud.local`.** Der Installer setzt den echten
   System-Hostnamen (`hostnamectl set-hostname`), Avahi meldet dann automatisch
   `<hostname>.local`. Die Admin-Login-Mail-Domain (`admin@offgrid.local`)
   bleibt unangetastet — das ist nur ein Login-Bezeichner, kein Netzwerkname.
4. **HTTPS standardmäßig aktiv (Installer-Frage mit Vorgabe „Ja").** Passt zu
   „so einfach wie möglich absichern". Feld-Modus (selbstsigniert) funktioniert
   ohne weitere Angaben; Domain ist optional.
5. **Moduswechsel über die Settings-UI**, umgesetzt durch ein schlankes eigenes
   Skript (`apply.sh`) — kein npm/apt-Lauf, dauert Sekunden. Derselbe Weg wie
   Update-Button und Power-Control: die Backend-API ruft ein privilegiertes
   Skript über eine `NOPASSWD`-sudoers-Regel auf.

## Architektur

### Neues Deploy-Modul `deploy/https/`

Folgt dem bestehenden Muster von `deploy/vpn`, `deploy/netfallback`,
`deploy/kiosk` (jeweils ein `install.sh --prefix ...`, vom Haupt-Installer
optional aufgerufen).

**`deploy/https/install.sh --prefix DIR --hostname NAME --domain DOMAIN`**
(einmalige Einrichtung, root-only):
1. Installiert Caddy (offizielles Caddy-apt-Repo) und `avahi-daemon` /
   `avahi-utils`. Best-effort mit klarem Hinweis bei Fehlschlag (Muster wie der
   rclone-Installer im Haupt-`install.sh`), nicht fatal für den Rest.
2. Ruft `apply.sh --hostname NAME [--domain DOMAIN]` für die Erstkonfiguration.
3. Richtet die `NOPASSWD`-sudoers-Regel für `apply.sh` ein (via `visudo -cf`
   validiert, wie die bestehenden Update-/Power-Regeln).
4. Schreibt `OGC_HTTPS_APPLY_COMMAND=sudo <prefix>/deploy/https/apply.sh` in die
   `.env` (nur anhängen, wenn noch nicht vorhanden — wie beim Update-Command).

**`deploy/https/apply.sh --hostname NAME [--domain DOMAIN]`** (das Arbeitspferd,
aufgerufen vom Installer UND später von der Backend-API):
1. Normalisiert den Hostnamen (strippt ein evtl. `.local`-Suffix; Avahi hängt
   `.local` automatisch an den Kurznamen an).
2. Rendert die Caddyfile neu in eine temporäre Datei:
   - **Immer**: LAN-Block `<hostname>.local { tls internal; reverse_proxy
     localhost:8000 }` inkl. der SSE-freundlichen `/api/events`-Behandlung.
   - **Nur wenn `--domain` gesetzt**: zusätzlicher Block `<domain> {
     reverse_proxy localhost:8000 }` (Caddy holt/erneuert das echte Zertifikat
     automatisch).
3. `caddy validate` gegen die temporäre Datei. Bei Fehler: **Abbruch, aktive
   Config bleibt unangetastet**, `stderr` als Fehlermeldung.
4. Atomarer `mv` der temporären Datei nach `/etc/caddy/Caddyfile`, dann
   `caddy reload` (bzw. `systemctl reload caddy`).
5. Hostname setzen — **nur wenn abweichend** vom aktuellen (vermeidet unnötige
   Avahi-Neustarts): `hostnamectl set-hostname <name>`, `/etc/hosts`-Zeile
   `127.0.1.1 <name>` mitpflegen, `avahi-daemon` neustarten.
6. Schreibt den neuen Zustand nach `<prefix>/data/https_state.json`
   (`{hostname, domain}`) — die einzige Quelle, aus der das Backend liest.
7. Idempotent: gleicher Aufruf zweimal ändert nichts.

Kein separates `switch.sh`: Domain setzen/entfernen ist ein `apply.sh`-Aufruf
mit bzw. ohne `--domain`.

### Installer-Integration (`deploy/install.sh`)

Neuer Frageblock in gleicher Optik wie die bestehenden `ask`/`ask_yn`:

```
HTTPS aktivieren (empfohlen)? [J/n]              # WITH_HTTPS, Vorgabe 1
  mDNS-Hostname (erreichbar als <name>.local) [offgridcloud.local]:
  Öffentliche Domain (leer lassen, falls keine vorhanden) []:
```

- Re-Run-Erkennung wie bei Kiosk/VPN/AP-Fallback: existiert
  `/etc/caddy/Caddyfile` bzw. `data/https_state.json`, werden Hostname/Domain
  aus dem gespeicherten Zustand vorbefüllt statt zurückgesetzt.
- Env-Overrides für headless: `OGC_WITH_HTTPS`, `OGC_HTTPS_HOSTNAME`,
  `OGC_HTTPS_DOMAIN` (dokumentiert im Kopf-Kommentar des Installers).
- Aufruf am Ende (nach der Service-Installation, damit Caddy auf einen laufenden
  Dienst zeigt), nicht-fatal via `|| echo ...` wie die anderen Module.

### Backend

- **`config.py`**: neues Feld `https_apply_command: str = ""` (leer = Feature
  nicht eingerichtet → in der UI deaktiviert, wie `restart_service_command`).
- **Zustand**: nicht in der DB dupliziert. `apply.sh` schreibt
  `data/https_state.json`; das Backend liest diese Datei nur.
- **Neuer Router `backend/app/routers/https.py`** (admin-only, eigene Datei wie
  `updates.py`, damit `system.py` nicht weiter wächst):
  - `GET /api/system/https` → `{enabled, hostname, domain, lan_url, public_url}`
  - `PUT /api/system/https` mit `{hostname?, domain?}` (Domain `""` = entfernen).
    Validiert Hostname-/Domain-Format serverseitig, ruft `apply.sh` **synchron**
    über `subprocess.run(..., timeout=30, capture_output=True)` auf (kein
    Job-Runner nötig — Sekunden, nicht Minuten), schreibt ins Audit-Log, gibt
    bei Fehler den `stderr`-Tail als 409/500 an die UI zurück.
- **`schemas.py`**: `HttpsStatusOut`, `HttpsConfigUpdate`.
- Die Run-Funktion wird injizierbar gehalten (wie `popen` in `power.py`), damit
  Tests sie ohne echten Subprozess prüfen können.

### Frontend

- **`System.tsx`**: neue `HttpsCard`-Komponente (Geschwister von `PowerCard`).
  Zeigt die LAN-URL (`https://<hostname>.local`), ein Eingabefeld für die Domain
  mit „Speichern"-Button, und einen Hinweis, wenn `enabled=false`
  („HTTPS ist nicht eingerichtet — Installer erneut ausführen").
- **`api.ts`**: typisierte `getHttpsStatus()` / `updateHttps()`.
- **UI-Copy** macht explizit klar: das Setzen einer Domain konfiguriert nur die
  Caddy-Seite. **DNS-A-Record beim Registrar und Portweiterleitung 80/443**
  muss der Nutzer selbst einrichten — das kann die Box nicht automatisieren.
  Zertifikatsausstellung kann bis zu ~1 Minute dauern und schlägt sonst still
  im Caddy-Log fehl.

## Fehlerbehandlung

- `caddy validate` vor jedem Reload; Schreiben in temporäre Datei + atomarer
  `mv` erst nach erfolgreicher Validierung (Prinzip analog zu `visudo -cf` im
  Haupt-Installer). Bestehende Config bleibt bei Fehlern intakt.
- Backend reicht `stderr`-Tail von `apply.sh` an die UI durch (z. B. bei
  ungültigem Domain-Format).
- HTTPS-Modul-Fehler beim Install sind nicht fatal für den Rest der
  Installation (`|| echo ...`-Pattern).
- Nicht prüfbar durch die Box (nur als UI-Hinweis): dass die Domain per DNS
  tatsächlich auf die Box zeigt und 80/443 von außen erreichbar sind.

## Tests

- **Backend `test_https.py`** nach Vorbild `test_power.py`: injizierbare
  Run-Funktion (kein echter Subprozess), Endpoint-Tests mit
  `client`/`admin_auth`-Fixtures, Default-off-Check (leerer
  `https_apply_command` → `enabled=false`, `PUT` liefert 409), Format-Validierung
  für Hostname/Domain.
- **Frontend**: kein Testframework im Repo → Verifikation über `npm run dev`.
- **Bash-Skripte**: keine bestehende Test-Infra für Deploy-Skripte →
  manuelle/dokumentierte Verifikation (lokale VM oder Pi):
  1. Erstinstall mit HTTPS (Feld-Modus, selbstsigniert) → `https://offgridcloud.local` erreichbar.
  2. Domain nachträglich über Settings setzen → echtes Zertifikat, LAN-URL bleibt erreichbar.
  3. Domain wieder entfernen → nur noch LAN-Block.
  4. Installer-Re-Run ändert bestehenden Hostnamen nicht ungefragt (Vorbefüllung greift).

## Doku-Updates

- `docs/BETRIEB.md` §3 umschreiben: kein manuelles Caddyfile-Editieren mehr —
  Installer-Frage + Settings-UI beschreiben.
- `deploy/Caddyfile` und `deploy/nginx.conf.example`: Platzhalter `offgrid.local`
  → `offgridcloud.local`. nginx bleibt als manuelle Alternative dokumentiert.

## Bewusst ausgeklammert (YAGNI)

- WebAuthn/Passkeys selbst (separates Vorhaben; dieses Design schafft nur die
  HTTPS-Voraussetzung).
- Automatisierung von DNS/Portweiterleitung (kann die Box nicht leisten).
- DynDNS-Integration (nur als Hinweis in der Doku, nicht implementiert).
- Exklusiver Modus-Schalter (durch Ansatz B überflüssig).
