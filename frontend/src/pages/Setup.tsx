import { useEffect, useState } from "react";
import { api, ApiError, type SetupResult, type SetupStatus } from "../api";

// First-boot wizard of the prebuilt image (see backend/app/setup_wizard.py).
// Shown instead of the whole app while the box has no account yet.

const input =
  "w-full rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 outline-hidden focus:border-ogc-teal";
const MANUAL = "__manual__";

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="mb-4 block text-sm">
      <span className="mb-1 block text-slate-400">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-slate-500">{hint}</span>}
    </label>
  );
}

export default function Setup({ status }: { status: SetupStatus }) {
  const [code, setCode] = useState("");
  const [email, setEmail] = useState("admin@offgrid.local");
  const [password, setPassword] = useState("");
  const [password2, setPassword2] = useState("");
  const [hostname, setHostname] = useState(status.suggested_hostname);
  const [useWifi, setUseWifi] = useState(true);
  const [ssids, setSsids] = useState<string[]>([]);
  const [ssidChoice, setSsidChoice] = useState("");
  const [ssidManual, setSsidManual] = useState("");
  const [wifiPassword, setWifiPassword] = useState("");
  const [fallbackPassword, setFallbackPassword] = useState("");
  const [country, setCountry] = useState(status.country || "DE");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [done, setDone] = useState<SetupResult | null>(null);

  function scan() {
    setScanning(true);
    api<string[]>("/api/setup/wifi")
      .then((list) => {
        setSsids(list);
        setSsidChoice((cur) => cur || list[0] || MANUAL);
      })
      .catch(() => setSsidChoice((cur) => cur || MANUAL))
      .finally(() => setScanning(false));
  }
  useEffect(scan, []);

  const ssid = ssidChoice === MANUAL ? ssidManual.trim() : ssidChoice;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (password !== password2) return setError("Die Admin-Passwörter stimmen nicht überein.");
    if (password.length < 8) return setError("Das Admin-Passwort braucht mindestens 8 Zeichen.");
    if (fallbackPassword.length < 8 || fallbackPassword.length > 63)
      return setError("Das Rückfall-WLAN-Passwort muss 8–63 Zeichen lang sein.");
    if (useWifi && !ssid) return setError("Bitte ein WLAN wählen oder eingeben.");
    setBusy(true);
    try {
      const result = await api<SetupResult>("/api/setup/complete", {
        method: "POST",
        body: JSON.stringify({
          code,
          admin_email: email,
          admin_password: password,
          hostname,
          wifi_ssid: useWifi ? ssid : "",
          wifi_password: useWifi ? wifiPassword : "",
          fallback_password: fallbackPassword,
          country,
        }),
      });
      setDone(result);
    } catch (err) {
      setError(
        err instanceof ApiError && err.status !== 422
          ? err.message
          : "Eingaben ungültig — bitte Hostname, Ländercode und Passwörter prüfen.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-linear-to-br from-slate-900 via-slate-900 to-ogc-indigo/40 px-4 py-10 text-slate-100">
      <div className="w-full max-w-md rounded-2xl bg-slate-800/60 p-8 shadow-xl ring-1 ring-white/10">
        <div className="mb-6 flex flex-col items-center gap-2 text-center">
          <img src="/logo-icon.svg" alt="OffgridCloud" className="h-14 w-14" />
          <h1 className="text-xl font-bold">OffgridCloud einrichten</h1>
          <p className="text-xs text-slate-400">
            Einmalig: Admin-Zugang, Name der Box und Netzwerk festlegen.
          </p>
        </div>

        {done ? (
          <Done result={done} />
        ) : (
          <form onSubmit={submit}>
            {error && (
              <div className="mb-4 rounded-lg bg-red-500/15 px-3 py-2 text-sm text-red-300">{error}</div>
            )}

            {status.code_required && (
              <Field label="Setup-Code" hint="Steht in der Datei offgridcloud-setup.txt, die du auf die SD-Karte gelegt hast.">
                <input className={input} value={code} onChange={(e) => setCode(e.target.value)} required autoFocus />
              </Field>
            )}

            <h2 className="mb-3 mt-2 text-sm font-semibold uppercase tracking-wide text-ogc-teal">Admin</h2>
            <Field label="E-Mail (Login)">
              <input className={input} value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" required />
            </Field>
            <Field label="Passwort" hint="Mindestens 8 Zeichen.">
              <input type="password" className={input} value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" required />
            </Field>
            <Field label="Passwort wiederholen">
              <input type="password" className={input} value={password2} onChange={(e) => setPassword2(e.target.value)} autoComplete="new-password" required />
            </Field>

            <h2 className="mb-3 mt-6 text-sm font-semibold uppercase tracking-wide text-ogc-teal">Box</h2>
            <Field label="Name" hint={`Erreichbar danach unter https://${hostname || "…"}.local`}>
              <input className={input} value={hostname} onChange={(e) => setHostname(e.target.value.toLowerCase())} required />
            </Field>
            <Field label="Land (WLAN-Funkregeln)" hint="ISO-Code, z. B. DE, AT, CH.">
              <input className={input} value={country} maxLength={2} onChange={(e) => setCountry(e.target.value.toUpperCase())} required />
            </Field>

            <h2 className="mb-3 mt-6 text-sm font-semibold uppercase tracking-wide text-ogc-teal">Netzwerk</h2>
            <div className="mb-4 flex gap-2 text-sm">
              <button type="button" onClick={() => setUseWifi(true)} className={`flex-1 rounded-lg border px-3 py-2 ${useWifi ? "border-ogc-teal bg-ogc-teal/10" : "border-white/10"}`}>
                WLAN verbinden
              </button>
              <button type="button" onClick={() => setUseWifi(false)} className={`flex-1 rounded-lg border px-3 py-2 ${!useWifi ? "border-ogc-teal bg-ogc-teal/10" : "border-white/10"}`}>
                Kabel / so lassen
              </button>
            </div>
            {useWifi ? (
              <>
                <Field label="WLAN">
                  <div className="flex gap-2">
                    <select className={input} value={ssidChoice} onChange={(e) => setSsidChoice(e.target.value)}>
                      {ssids.map((s) => (
                        <option key={s} value={s}>{s}</option>
                      ))}
                      <option value={MANUAL}>Anderes WLAN eingeben…</option>
                    </select>
                    <button type="button" onClick={scan} disabled={scanning} className="rounded-lg border border-white/15 px-3 text-xs hover:bg-white/5 disabled:opacity-50">
                      {scanning ? "…" : "Suchen"}
                    </button>
                  </div>
                </Field>
                {ssidChoice === MANUAL && (
                  <Field label="WLAN-Name (SSID)">
                    <input className={input} value={ssidManual} onChange={(e) => setSsidManual(e.target.value)} required />
                  </Field>
                )}
                <Field label="WLAN-Passwort" hint="Leer lassen bei offenem WLAN.">
                  <input type="password" className={input} value={wifiPassword} onChange={(e) => setWifiPassword(e.target.value)} />
                </Field>
              </>
            ) : (
              <p className="mb-4 text-xs text-slate-400">
                Die Box behält ihre jetzige Verbindung (Netzwerkkabel oder ein im Raspberry Pi Imager hinterlegtes WLAN).
              </p>
            )}
            <Field
              label="Passwort fürs Rückfall-WLAN"
              hint={`Findet die Box kein bekanntes Netz, öffnet sie ihr eigenes WLAN „${hostname || "…"}“ mit diesem Passwort. 8–63 Zeichen.`}
            >
              <input type="password" className={input} value={fallbackPassword} onChange={(e) => setFallbackPassword(e.target.value)} autoComplete="new-password" required />
            </Field>

            <button
              type="submit"
              disabled={busy}
              className="mt-2 w-full rounded-lg bg-linear-to-r from-ogc-teal to-ogc-blue py-2.5 font-semibold text-white transition hover:opacity-90 disabled:opacity-50"
            >
              {busy ? "Wird eingerichtet…" : "Einrichtung abschließen"}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}

function Done({ result }: { result: SetupResult }) {
  return (
    <div className="space-y-4 text-sm">
      <div className="rounded-lg bg-emerald-500/15 px-3 py-2 text-emerald-300">Fertig — die Box übernimmt jetzt die Einstellungen.</div>
      <ol className="list-decimal space-y-2 pl-5 text-slate-300">
        <li>Das Setup-WLAN schaltet sich in wenigen Sekunden ab.</li>
        {result.wifi_ssid ? (
          <li>
            Verbinde dein Gerät mit dem WLAN <b>{result.wifi_ssid}</b> — die Box wählt sich dort ebenfalls ein.
          </li>
        ) : (
          <li>Bleib im selben Netz wie die Box (Kabel / bestehendes WLAN).</li>
        )}
        <li>
          Öffne{" "}
          <a href={result.url} className="font-semibold text-ogc-teal underline">
            {result.url}
          </a>{" "}
          und melde dich mit deinem neuen Admin-Zugang an. Das Zertifikat ist selbstsigniert — die Warnung des Browsers einmal bestätigen.
        </li>
      </ol>
      <p className="text-xs text-slate-500">
        Klappt das WLAN nicht (z. B. falsches Passwort), öffnet die Box nach etwa einer Minute ihr Rückfall-WLAN <b>{result.hostname}</b>.
        Darüber ist sie unter http://10.42.0.1:8000 erreichbar und du kannst das WLAN unter Admin → Netzwerk korrigieren.
      </p>
      {!result.finish_started && (
        <p className="rounded-lg bg-amber-500/15 px-3 py-2 text-xs text-amber-300">
          Hinweis: Der Abschluss-Helfer ist auf dieser Installation nicht eingerichtet — Netzwerk und Hostname wurden nur gespeichert.
        </p>
      )}
    </div>
  );
}
