import { useEffect, useState } from "react";
import {
  ApiError,
  deletePasskey,
  listPasskeys,
  passkeysSupported,
  renamePasskey,
  type Passkey,
} from "../api";
import Layout from "../components/Layout";
import { useToast } from "../toast";
import { registerPasskey } from "../webauthn";

export default function Passkeys() {
  const toast = useToast();
  const [items, setItems] = useState<Passkey[]>([]);
  const [busy, setBusy] = useState(false);

  function load() {
    listPasskeys().then(setItems).catch(() => setItems([]));
  }
  useEffect(load, []);

  async function add() {
    const name = window.prompt("Name für diesen Passkey (z. B. iPhone)", "");
    if (name === null) return;
    setBusy(true);
    try {
      await registerPasskey(name || "Passkey");
      toast.info("Passkey", "Passkey hinzugefügt.");
      load();
    } catch (err) {
      if (err instanceof DOMException && err.name === "NotAllowedError") {
        toast.error("Passkey", "Registrierung abgebrochen.");
      } else {
        toast.error("Passkey", err instanceof ApiError ? err.message : "Fehlgeschlagen");
      }
    } finally {
      setBusy(false);
    }
  }

  async function rename(p: Passkey) {
    const name = window.prompt("Neuer Name", p.name);
    if (!name) return;
    await renamePasskey(p.id, name).then(load).catch(() => toast.error("Passkey", "Fehlgeschlagen"));
  }

  async function remove(p: Passkey) {
    if (!window.confirm(`Passkey „${p.name}" löschen?`)) return;
    await deletePasskey(p.id).then(load).catch(() => toast.error("Passkey", "Fehlgeschlagen"));
  }

  return (
    <Layout>
      <h2 className="mb-4 text-lg font-semibold">Passkeys</h2>
      {!passkeysSupported() && (
        <div className="mb-4 rounded-lg bg-slate-900/60 px-3 py-2 text-xs text-slate-400">
          Passkeys brauchen HTTPS (z. B. https://offgridcloud.local) oder localhost. Über eine
          nackte IP ohne HTTPS lassen sie sich nicht einrichten.
        </div>
      )}
      <button
        type="button"
        onClick={add}
        disabled={busy || !passkeysSupported()}
        className="mb-6 rounded-lg bg-gradient-to-r from-ogc-teal to-ogc-blue px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
      >
        {busy ? "…" : "Passkey hinzufügen"}
      </button>

      {items.length === 0 ? (
        <p className="text-sm text-slate-500">Noch keine Passkeys registriert.</p>
      ) : (
        <ul className="space-y-2">
          {items.map((p) => (
            <li
              key={p.id}
              className="flex items-center justify-between rounded-xl bg-slate-800/60 px-4 py-3 ring-1 ring-white/10"
            >
              <div>
                <div className="text-sm font-medium text-slate-200">{p.name}</div>
                <div className="text-xs text-slate-500">
                  {p.rp_id} · erstellt {new Date(p.created_at + "Z").toLocaleDateString()}
                  {p.last_used_at && ` · zuletzt ${new Date(p.last_used_at + "Z").toLocaleDateString()}`}
                </div>
              </div>
              <div className="flex gap-2">
                <button type="button" onClick={() => rename(p)} className="text-xs text-slate-400 hover:text-slate-200">
                  Umbenennen
                </button>
                <button type="button" onClick={() => remove(p)} className="text-xs text-red-400 hover:text-red-300">
                  Löschen
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Layout>
  );
}
