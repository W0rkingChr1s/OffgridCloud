# Design: WebAuthn/Passkey-Login (Ergänzung zum Passwort-Login)

**Datum:** 2026-07-16
**Status:** Genehmigt (Design), bereit für Implementierungsplan

## Ziel

Nutzer sollen sich zusätzlich zum bestehenden E-Mail/Passwort-Login per
**Passkey (WebAuthn)** anmelden können. Das Passwort bleibt die Basis und der
Fallback; Passkeys sind eine bequemere, phishing-resistente Ergänzung, die jeder
Nutzer selbst für seine Geräte einrichtet.

## Abhängigkeit / Branch-Basis

Passkeys brauchen einen **stabilen HTTPS-Origin** (RP-ID). Diese Grundlage
(Caddy-Reverse-Proxy, `offgridcloud.local`, `data/https_state.json` mit
`hostname`+`domain`) wird von der HTTPS-Arbeit in **PR #57** geliefert. Die
Passkey-**Implementierung** baut auf `main` auf, **nachdem #57 gemerged** ist
(so entschieden im Brainstorming). Diese Spec und der Implementierungsplan
werden vorab geschrieben; die Code-Umsetzung startet nach dem Merge.

Konkrete Wiederverwendung aus #57: `app/https_config.read_state(data_dir)` liest
`{hostname, domain}` — daraus baut die Passkey-Logik die Origin-Allowlist.

## Kernentscheidungen (aus dem Brainstorming)

1. **Ergänzung, Selbstregistrierung.** Passwort bleibt Basis-Login und Fallback.
   Ein eingeloggter Nutzer registriert selbst einen oder mehrere Passkeys und
   loggt danach wahlweise per Passkey ODER Passwort ein. Keine Admin-Freigabe
   nötig.
2. **Beide Login-Flows.** Login-Seite bietet einen Ein-Klick-Passkey-Button
   (discoverable credentials, ohne E-Mail) UND — wenn das E-Mail-Feld gefüllt ist
   — den E-Mail-first-Flow (gefilterte `allowCredentials`).
3. **Volle Origin-Abdeckung (2 Passkeys pro Nutzer möglich).** Ein Passkey ist an
   genau eine RP-ID gebunden. Die RP-ID wird pro Request aus der aktuellen Origin
   abgeleitet und gegen eine Allowlist geprüft. Ein Nutzer kann je einen Passkey
   für `offgridcloud.local` und für die öffentliche Domain registrieren; der
   Login bietet nur die zur aktuellen Origin passenden Credentials an.
4. **Challenge-Store: In-Memory mit TTL.** uvicorn läuft als Einzelprozess
   (systemd-Unit ohne `--workers`), daher genügt ein Prozess-lokales Dict mit
   kurzer TTL — kein Redis/DB. Challenge-Verlust bei Neustart ist unkritisch
   (Lebensdauer Sekunden).
5. **Library `py_webauthn`** (Backend) + native `navigator.credentials`-API
   (Frontend, keine JS-Lib). **Attestation `none`** — für eine selbstgehostete
   Appliance muss die Authenticator-Herkunft nicht geprüft werden.

## Datenmodell

**Neue Tabelle `webauthn_credentials`** (wird automatisch via `create_all`
angelegt — eine ganz neue Tabelle braucht keine `_ADDED_COLUMNS`-Migration):

```
WebAuthnCredential:
  id             PK (int)
  user_id        FK → users.id, ON DELETE CASCADE, indexed
  credential_id  LargeBinary, unique, indexed   (roher Credential-Identifier)
  public_key     LargeBinary                    (COSE-kodierter Public Key)
  sign_count     int, default 0                 (Klon-Erkennung)
  rp_id          str(255)                       ("offgridcloud.local" | "cloud.example.com" | "localhost")
  transports     str(255), default ""           (JSON-Liste: usb/internal/hybrid…)
  name           str(120), default ""           (nutzergegebener Gerätename)
  created_at     datetime
  last_used_at   datetime | null
```

**Neue Spalte an `users`**: `webauthn_user_handle` (LargeBinary, 32 zufällige
Bytes, einmalig pro Nutzer). Stabiler, nicht-PII User-Handle für discoverable
credentials. Wird als `_ADDED_COLUMNS`-Eintrag in `db.py` ergänzt; bestehende
Nutzer bekommen ihn **lazy beim ersten Passkey-Registrieren** (nicht als NOT
NULL DEFAULT, sondern nullable + on-demand befüllt).

## Architektur

Alles hängt am bestehenden JWT-Flow. Nach erfolgreicher WebAuthn-Assertion ruft
der Server denselben `security.create_access_token(user_id=…, role=…)` auf wie
der Passwort-Login (`routers/auth.py`). Das Frontend erhält exakt dasselbe
`TokenResponse` und speichert es wie bisher in `localStorage`. Kein Umbau am
Token-/Session-Mechanismus, kein CORS, keine Server-Sessions.

**Neue Module (isoliert, testbar):**
- `backend/app/webauthn_config.py` — reine Helfer: Origin/RP-ID aus Request
  ableiten + gegen Allowlist prüfen; Challenge-Store (In-Memory, TTL).
- `backend/app/routers/webauthn.py` — die Ceremony- + Verwaltungs-Endpoints.
- `WebAuthnCredential`-Modell in `models.py`; Schemas in `schemas.py`.
- `frontend/src/webauthn.ts` — Browser-API-Kapselung (base64url, register/login).

## Endpoints & Ceremonies

Alle unter dem bestehenden Auth-Prefix. Registrierung + Verwaltung erfordern ein
Token (`Depends(get_current_user)`); Login ist öffentlich.

**Registrierung (eingeloggt):**
- `POST /api/auth/webauthn/register/options` → `PublicKeyCredentialCreationOptions`
  via py_webauthn. `residentKey: "preferred"`, `userVerification: "preferred"`,
  Attestation `none`. Challenge in den TTL-Store (Key = Zufallsnonce, im Response
  mitgegeben). RP-ID = aktuelle (validierte) Origin. `excludeCredentials` =
  vorhandene Credentials des Nutzers für diese RP-ID (verhindert Doppel-Reg).
- `POST /api/auth/webauthn/register/verify` mit `{nonce, credential, name?}` →
  `verify_registration_response` gegen die gespeicherte Challenge; legt eine
  `webauthn_credentials`-Zeile an (`rp_id` = aktuelle Origin). 409 bei bereits
  existierender `credential_id`. Löscht die Challenge (Einmalnutzung).

**Login (öffentlich):**
- `POST /api/auth/webauthn/login/options` mit `{email?}` →
  - *E-Mail gesetzt:* `allowCredentials` = Credentials dieses Nutzers **für die
    aktuelle RP-ID**.
  - *Keine E-Mail:* `allowCredentials` leer → Browser bietet discoverable
    credentials an.
  - Challenge in den TTL-Store (Key = Zufallsnonce, im Response mitgegeben).
  - **Enumerierungs-sicher:** unbekannte E-Mail → trotzdem plausible Options mit
    leerer `allowCredentials`, kein „Nutzer existiert nicht".
- `POST /api/auth/webauthn/login/verify` mit `{nonce, credential}` →
  `verify_authentication_response`; Nutzer über `credential_id` (bzw.
  `user_handle` beim Ein-Klick-Flow) auflösen; prüft, dass die Assertion-RP-ID
  zur aktuellen Origin passt; prüft `sign_count` (Rückschritt → Ablehnung +
  Audit); aktualisiert `sign_count` + `last_used_at`; prüft `user.active`. Erfolg
  → dasselbe `TokenResponse` wie `/api/auth/login`. Löscht die Challenge.

**Verwaltung (eingeloggt, eigene Credentials):**
- `GET /api/auth/webauthn/credentials` → eigene Passkeys (id, name, rp_id,
  created_at, last_used_at; keine Schlüssel-Bytes).
- `PATCH /api/auth/webauthn/credentials/{id}` → `{name}` umbenennen.
- `DELETE /api/auth/webauthn/credentials/{id}` → entfernen (nur eigene).

## RP-ID/Origin-Ableitung (sicherheitskritisch)

WebAuthn verlangt drei zusammenpassende Werte: RP-ID (Hostname), expected origin
(`https://<origin>`) und was der Browser sendet. In `webauthn_config.py`:

1. Origin aus dem Request lesen: primär `Origin`-Header (bei den fetch-Calls
   vorhanden), Host als Fallback.
2. RP-ID (Hostname ohne Schema/Port) + expected origin extrahieren.
3. **Gegen Allowlist prüfen** — nicht gelistete Origin → 400. Verhindert
   RP-ID-Spoofing über gefälschten Host-Header.

**Allowlist** (dynamisch gebaut):
- `<hostname>.local` und die Domain aus `data/https_state.json`
  (`https_config.read_state`).
- `localhost` (lokale Entwicklung — WebAuthn erlaubt `http://localhost` ohne TLS).
- Optionaler `.env`-Override `OGC_WEBAUTHN_EXTRA_ORIGINS` (Komma-Liste).

**Fallback ohne HTTPS:** kein `https_state.json` → Allowlist nur `localhost`.
Über nackte LAN-IP/HTTP kann der Browser ohnehin keine Passkeys nutzen; das
Frontend blendet die Passkey-Optionen dann aus (siehe Frontend) statt einen
Browser-Fehler zu provozieren.

## Frontend

**Login-Seite (`Login.tsx`):**
- E-Mail + Passwort + „Anmelden" bleiben unverändert.
- Zusätzlich Button **„Mit Passkey anmelden"**: ohne E-Mail → Ein-Klick
  (discoverable); mit ausgefülltem E-Mail-Feld → E-Mail-first. Ein Button, beide
  Fälle.
- Passkey-Optionen nur sichtbar, wenn `window.PublicKeyCredential` existiert
  **und** die Origin passkey-fähig ist (HTTPS oder localhost); sonst dezenter
  Hinweis („HTTPS/mDNS-Name nötig").

**Einstellungen — Sektion „Passkeys"** (eingeloggter Nutzer):
- Liste eigener Passkeys (Name, erstellt, zuletzt genutzt, rp_id/Domain).
- „Passkey hinzufügen" → Registrierungs-Ceremony, danach Gerätename abfragen.
- Umbenennen + Löschen je Eintrag.

**`frontend/src/webauthn.ts`:**
- base64url ↔ ArrayBuffer (WebAuthn tauscht Buffer, JSON braucht Strings).
- `registerPasskey(name)`: Options → `navigator.credentials.create()` → verify.
- `loginWithPasskey(email?)`: Options → `navigator.credentials.get()` → verify →
  Token setzen wie beim Passwort-Login.
- Fehler-Mapping: Nutzer-Abbruch (`NotAllowedError`) → ruhige Meldung.

**`auth.tsx`:** neue `loginWithPasskey`-Funktion analog zu `login`, damit
Token-/User-Rehydrierung identisch läuft.

## Fehlerbehandlung

- Abgelaufene/benutzte Challenge → 400 („Anmeldung abgelaufen, bitte erneut").
- Origin nicht in Allowlist → 400.
- Doppeltes Credential beim Registrieren → 409.
- `sign_count`-Rückschritt (möglicher Klon) → Login-Ablehnung + Audit-Eintrag.
- `NotAllowedError` (Nutzer-Abbruch) → ruhige UI-Meldung, kein roter Fehler.
- **Aussperr-Schutz:** Passwort bleibt immer Fallback; Admins können Passwörter
  zurücksetzen (bestehende Funktion). Deshalb **keine** Admin-seitige
  Passkey-Verwaltung fremder Nutzer im MVP (YAGNI).

## Tests

- **Reine Helfer (`webauthn_config.py`)** — voll TDD: Origin/RP-ID-Ableitung,
  Allowlist-Prüfung inkl. Spoofing-Abwehr, Challenge-Store (Ablauf +
  Einmalnutzung).
- **Endpoint-Tests (`test_webauthn.py`)** — die `py_webauthn`-Verify-Funktionen
  werden injiziert/gemockt (kein echter Authenticator im Test), sodass die
  **Server-Logik** geprüft wird: Credential angelegt, `sign_count`/`last_used_at`
  aktualisiert, Token ausgestellt, `allowCredentials` nach rp_id gefiltert,
  Enumerierungs-Sicherheit bei unbekannter E-Mail, Ablehnung inaktiver Nutzer,
  Challenge-Einmalnutzung, 409 bei Doppel-Credential. Injizierbare
  Verify-Funktion nach dem `run=`/`popen=`-Muster (Power/HTTPS).
- **Frontend** — kein Testframework: `npm run lint` (Typecheck) + Dev-Server-Smoke.
- **On-Device-E2E** (dokumentiert): echter Authenticator oder Chromes virtueller
  Authenticator (DevTools) gegen `https://offgridcloud.local` — registrieren,
  ausloggen, per Passkey einloggen (Ein-Klick + E-Mail-first), zweiten Passkey
  über die Domain registrieren, Passkey löschen.

## Bewusst ausgeklammert (YAGNI)

- Passwort-Login abschalten / Passkey erzwingen (Aussperr-Risiko im Feld).
- Admin-seitige Verwaltung fremder Passkeys (Passwort-Reset genügt).
- Attestation-Prüfung / Authenticator-Allowlists.
- Persistenter/mehrprozess-fähiger Challenge-Store (Einzelprozess genügt).
- WebAuthn als zweiter Faktor zusätzlich zum Passwort (hier: alternativer
  Erst-Faktor, nicht 2FA).
```
