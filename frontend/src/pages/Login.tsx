import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, passkeysSupported } from "../api";
import { useAuth } from "../auth";

export default function Login() {
  const { login, loginPasskey } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await login(email, password);
      navigate("/");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Anmeldung fehlgeschlagen");
    } finally {
      setBusy(false);
    }
  }

  async function passkey() {
    setError(null);
    setBusy(true);
    try {
      await loginPasskey(email || undefined);
      navigate("/");
    } catch (err) {
      if (err instanceof DOMException && err.name === "NotAllowedError") {
        setError("Passkey-Anmeldung abgebrochen.");
      } else {
        setError(err instanceof ApiError ? err.message : "Passkey-Anmeldung fehlgeschlagen");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-slate-900 via-slate-900 to-ogc-indigo/40 px-4 text-slate-100">
      <form
        onSubmit={submit}
        className="w-full max-w-sm rounded-2xl bg-slate-800/60 p-8 shadow-xl ring-1 ring-white/10"
      >
        <div className="mb-6 flex flex-col items-center gap-2">
          <img src="/logo-icon.svg" alt="OffgridCloud" className="h-14 w-14" />
          <h1 className="text-xl font-bold">OffgridCloud</h1>
          <p className="text-xs text-slate-400">Upload when the signal is right.</p>
        </div>

        {error && (
          <div className="mb-4 rounded-lg bg-red-500/15 px-3 py-2 text-sm text-red-300">
            {error}
          </div>
        )}

        <label className="mb-3 block text-sm">
          <span className="mb-1 block text-slate-400">E-Mail</span>
          <input
            type="text"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="username"
            required
            className="w-full rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 outline-none focus:border-ogc-teal"
          />
        </label>
        <label className="mb-6 block text-sm">
          <span className="mb-1 block text-slate-400">Passwort</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
            className="w-full rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 outline-none focus:border-ogc-teal"
          />
        </label>

        <button
          type="submit"
          disabled={busy}
          className="w-full rounded-lg bg-gradient-to-r from-ogc-teal to-ogc-blue py-2.5 font-semibold text-white transition hover:opacity-90 disabled:opacity-50"
        >
          {busy ? "Anmelden…" : "Anmelden"}
        </button>

        {passkeysSupported() && (
          <button
            type="button"
            onClick={passkey}
            disabled={busy}
            className="mt-3 w-full rounded-lg border border-white/15 py-2.5 font-semibold text-slate-200 transition hover:bg-white/5 disabled:opacity-50"
          >
            Mit Passkey anmelden
          </button>
        )}
      </form>
    </div>
  );
}
