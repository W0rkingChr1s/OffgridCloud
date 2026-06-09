import { useEffect, useMemo, useState } from "react";
import {
  api,
  ApiError,
  type Provider,
  type ProviderField,
  type ProviderTypeDef,
} from "../api";
import Layout from "../components/Layout";

const STATUS: Record<string, { label: string; cls: string }> = {
  ok: { label: "verbunden", cls: "bg-emerald-500/20 text-emerald-300" },
  error: { label: "Fehler", cls: "bg-red-500/20 text-red-300" },
  unknown: { label: "ungetestet", cls: "bg-slate-500/20 text-slate-400" },
};

export default function Providers() {
  const [types, setTypes] = useState<ProviderTypeDef[]>([]);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState<Provider | null>(null);

  function load() {
    api<Provider[]>("/api/providers").then(setProviders).catch(report);
  }
  function report(e: unknown) {
    setError(e instanceof ApiError ? e.message : "Fehler");
  }
  useEffect(() => {
    api<ProviderTypeDef[]>("/api/providers/types").then(setTypes).catch(report);
    load();
  }, []);

  async function testSaved(p: Provider) {
    setError(null);
    try {
      await api<Provider>(`/api/providers/${p.id}/test`, { method: "POST" });
      load();
    } catch (e) {
      report(e);
    }
  }

  async function remove(p: Provider) {
    if (!confirm(`Provider „${p.name}“ löschen?`)) return;
    try {
      await api(`/api/providers/${p.id}`, { method: "DELETE" });
      load();
    } catch (e) {
      report(e);
    }
  }

  function startCreate() {
    setEditing(null);
    setShowForm(true);
  }
  function startEdit(p: Provider) {
    setEditing(p);
    setShowForm(true);
  }
  function close() {
    setShowForm(false);
    setEditing(null);
    load();
  }

  return (
    <Layout>
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold">Cloud-Provider</h2>
          <p className="text-sm text-slate-400">
            Ziel-Speicher verknüpfen und Verbindung testen.
          </p>
        </div>
        <button
          onClick={startCreate}
          className="rounded-lg bg-gradient-to-r from-ogc-teal to-ogc-blue px-4 py-2 text-sm font-semibold text-white"
        >
          + Provider
        </button>
      </div>

      {error && (
        <div className="mb-4 rounded-lg bg-red-500/15 px-3 py-2 text-sm text-red-300">{error}</div>
      )}

      {showForm && (
        <ProviderForm types={types} editing={editing} onDone={close} onCancel={() => setShowForm(false)} />
      )}

      {providers.length === 0 ? (
        <p className="text-sm text-slate-500">Noch keine Provider verknüpft.</p>
      ) : (
        <div className="space-y-3">
          {providers.map((p) => {
            const s = STATUS[p.status] ?? STATUS.unknown;
            const typeLabel = types.find((t) => t.key === p.type)?.label ?? p.type;
            return (
              <div key={p.id} className="rounded-2xl bg-slate-800/60 p-5 ring-1 ring-white/10">
                <div className="flex items-center justify-between">
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="text-lg font-semibold text-white">{p.name}</span>
                      <span className={`rounded px-2 py-0.5 text-xs ${s.cls}`}>{s.label}</span>
                    </div>
                    <div className="text-sm text-slate-400">{typeLabel}</div>
                    {p.status === "error" && p.last_error && (
                      <div className="mt-1 text-xs text-red-300">{p.last_error}</div>
                    )}
                  </div>
                  <div className="flex gap-2">
                    <button onClick={() => testSaved(p)} className="rounded border border-white/10 px-3 py-1.5 text-sm hover:bg-white/5">
                      Testen
                    </button>
                    <button onClick={() => startEdit(p)} className="rounded border border-white/10 px-3 py-1.5 text-sm hover:bg-white/5">
                      Bearbeiten
                    </button>
                    <button onClick={() => remove(p)} className="rounded border border-red-500/30 px-3 py-1.5 text-sm text-red-300 hover:bg-red-500/10">
                      Löschen
                    </button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </Layout>
  );
}

function ProviderForm({
  types,
  editing,
  onDone,
  onCancel,
}: {
  types: ProviderTypeDef[];
  editing: Provider | null;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [typeKey, setTypeKey] = useState(editing?.type ?? types[0]?.key ?? "");
  const [name, setName] = useState(editing?.name ?? "");
  const [config, setConfig] = useState<Record<string, string>>(editing?.config ?? {});
  const [testMsg, setTestMsg] = useState<{ ok: boolean; message: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const def = useMemo(() => types.find((t) => t.key === typeKey), [types, typeKey]);

  function changeType(key: string) {
    setTypeKey(key);
    setConfig({});
    setTestMsg(null);
  }
  function setField(key: string, value: string) {
    setConfig((c) => ({ ...c, [key]: value }));
  }

  async function runTest() {
    setBusy(true);
    setTestMsg(null);
    try {
      const res = await api<{ ok: boolean; message: string }>("/api/providers/test", {
        method: "POST",
        body: JSON.stringify({ type: typeKey, config }),
      });
      setTestMsg(res);
    } catch (e) {
      setTestMsg({ ok: false, message: e instanceof ApiError ? e.message : "Test fehlgeschlagen" });
    } finally {
      setBusy(false);
    }
  }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (editing) {
        await api(`/api/providers/${editing.id}`, {
          method: "PATCH",
          body: JSON.stringify({ name, config }),
        });
      } else {
        await api("/api/providers", {
          method: "POST",
          body: JSON.stringify({ name, type: typeKey, config }),
        });
      }
      onDone();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Speichern fehlgeschlagen");
    } finally {
      setBusy(false);
    }
  }

  const input = "w-full rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 outline-none focus:border-ogc-teal";

  return (
    <form onSubmit={save} className="mb-8 rounded-2xl bg-slate-800/60 p-5 ring-1 ring-white/10">
      <div className="mb-4 grid gap-3 sm:grid-cols-2">
        <label className="text-sm">
          <span className="mb-1 block text-slate-400">Name</span>
          <input className={input} value={name} onChange={(e) => setName(e.target.value)} required />
        </label>
        <label className="text-sm">
          <span className="mb-1 block text-slate-400">Typ</span>
          <select className={input} value={typeKey} disabled={!!editing} onChange={(e) => changeType(e.target.value)}>
            {types.map((t) => (
              <option key={t.key} value={t.key}>
                {t.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {def?.help && <p className="mb-3 text-xs text-slate-500">{def.help}</p>}

      <div className="grid gap-3 sm:grid-cols-2">
        {def?.fields.map((f) => (
          <FieldInput key={f.key} field={f} value={config[f.key] ?? f.default} onChange={(v) => setField(f.key, v)} />
        ))}
      </div>

      {testMsg && (
        <div className={`mt-4 rounded-lg px-3 py-2 text-sm ${testMsg.ok ? "bg-emerald-500/15 text-emerald-300" : "bg-red-500/15 text-red-300"}`}>
          {testMsg.message}
        </div>
      )}
      {error && <div className="mt-4 rounded-lg bg-red-500/15 px-3 py-2 text-sm text-red-300">{error}</div>}

      <div className="mt-5 flex gap-2">
        <button type="button" onClick={runTest} disabled={busy} className="rounded-lg border border-white/10 px-4 py-2 text-sm hover:bg-white/5 disabled:opacity-50">
          Verbindung testen
        </button>
        <button type="submit" disabled={busy} className="rounded-lg bg-gradient-to-r from-ogc-teal to-ogc-blue px-4 py-2 text-sm font-semibold text-white disabled:opacity-50">
          {editing ? "Speichern" : "Anlegen"}
        </button>
        <button type="button" onClick={onCancel} className="rounded-lg px-4 py-2 text-sm text-slate-400 hover:text-white">
          Abbrechen
        </button>
      </div>
    </form>
  );
}

function FieldInput({
  field,
  value,
  onChange,
}: {
  field: ProviderField;
  value: string;
  onChange: (value: string) => void;
}) {
  const input = "w-full rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 outline-none focus:border-ogc-teal";
  const label = (
    <span className="mb-1 block text-slate-400">
      {field.label}
      {field.required && <span className="text-red-400"> *</span>}
    </span>
  );

  if (field.type === "bool") {
    return (
      <label className="flex items-center gap-2 text-sm sm:col-span-2">
        <input type="checkbox" checked={value === "true"} onChange={(e) => onChange(e.target.checked ? "true" : "false")} />
        <span className="text-slate-300">{field.label}</span>
      </label>
    );
  }
  if (field.type === "select") {
    return (
      <label className="text-sm">
        {label}
        <select className={input} value={value} onChange={(e) => onChange(e.target.value)}>
          {field.options.map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
        </select>
      </label>
    );
  }
  if (field.type === "textarea") {
    return (
      <label className="text-sm sm:col-span-2">
        {label}
        <textarea className={`${input} h-24 font-mono text-xs`} value={value} onChange={(e) => onChange(e.target.value)} />
        {field.help && <span className="mt-1 block text-xs text-slate-500">{field.help}</span>}
      </label>
    );
  }
  return (
    <label className="text-sm">
      {label}
      <input
        className={input}
        type={field.type === "password" ? "password" : field.type === "number" ? "number" : "text"}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={field.secret ? "••••••" : ""}
      />
      {field.help && <span className="mt-1 block text-xs text-slate-500">{field.help}</span>}
    </label>
  );
}
