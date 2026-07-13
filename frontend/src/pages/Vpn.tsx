import { useEffect, useState } from "react";
import {
  api,
  ApiError,
  type VpnCapabilities,
  type VpnStatus,
  type VpnTunnel,
  type VpnType,
} from "../api";
import Layout from "../components/Layout";

const TYPE_LABEL: Record<VpnType, string> = {
  wireguard: "WireGuard",
  openvpn: "OpenVPN",
};

export default function Vpn() {
  const [caps, setCaps] = useState<VpnCapabilities | null>(null);
  const [tunnels, setTunnels] = useState<VpnTunnel[]>([]);
  const [statusMap, setStatusMap] = useState<VpnStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState<VpnTunnel | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  function report(e: unknown) {
    setError(e instanceof ApiError ? e.message : "Fehler");
  }
  function load() {
    api<VpnTunnel[]>("/api/vpn").then(setTunnels).catch(report);
    api<VpnStatus>("/api/vpn/status").then(setStatusMap).catch(() => {});
  }
  useEffect(() => {
    api<VpnCapabilities>("/api/vpn/capabilities").then(setCaps).catch(report);
    load();
  }, []);

  async function connect(t: VpnTunnel) {
    setError(null);
    setBusyId(t.id);
    try {
      await api(`/api/vpn/${t.id}/connect`, { method: "POST" });
      load();
    } catch (e) {
      report(e);
      load();
    } finally {
      setBusyId(null);
    }
  }
  async function disconnect(t: VpnTunnel) {
    setBusyId(t.id);
    try {
      await api(`/api/vpn/${t.id}/disconnect`, { method: "POST" });
      load();
    } catch (e) {
      report(e);
    } finally {
      setBusyId(null);
    }
  }
  async function remove(t: VpnTunnel) {
    if (!confirm(`VPN „${t.name}“ löschen?`)) return;
    try {
      await api(`/api/vpn/${t.id}`, { method: "DELETE" });
      load();
    } catch (e) {
      report(e);
    }
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
          <h2 className="text-2xl font-bold">VPN</h2>
          <p className="text-sm text-slate-400">
            In ein Heimnetz einwählen, um interne Ziele (z. B. NAS per SMB) zu erreichen.
          </p>
        </div>
        <button
          onClick={() => {
            setEditing(null);
            setShowForm(true);
          }}
          className="rounded-lg bg-gradient-to-r from-ogc-teal to-ogc-blue px-4 py-2 text-sm font-semibold text-white"
        >
          + VPN
        </button>
      </div>

      {caps && !caps.ready && (
        <div className="mb-4 rounded-2xl border border-amber-500/30 bg-amber-500/10 p-4 text-sm text-amber-200">
          <div className="font-semibold">VPN benötigt erhöhte Container-Rechte</div>
          <p className="mt-1 text-amber-200/90">{caps.message}</p>
          <pre className="mt-3 overflow-x-auto rounded-lg bg-slate-900/70 px-3 py-2 text-xs text-slate-300">
docker run --cap-add=NET_ADMIN --device=/dev/net/tun … offgridcloud</pre>
          <p className="mt-2 text-xs text-amber-200/70">
            Bei docker-compose:{" "}
            <code className="text-amber-200">cap_add: [NET_ADMIN]</code> und{" "}
            <code className="text-amber-200">devices: ["/dev/net/tun"]</code>.
          </p>
        </div>
      )}

      {caps && caps.ready && (
        <div className="mb-4 flex flex-wrap gap-2 text-xs">
          <CapBadge ok={caps.wireguard} label="WireGuard" />
          <CapBadge ok={caps.openvpn} label="OpenVPN" />
        </div>
      )}

      {error && (
        <div className="mb-4 rounded-lg bg-red-500/15 px-3 py-2 text-sm text-red-300">{error}</div>
      )}

      {showForm && <VpnForm caps={caps} editing={editing} onDone={close} onCancel={() => setShowForm(false)} />}

      {tunnels.length === 0 ? (
        <p className="text-sm text-slate-500">Noch kein VPN eingerichtet.</p>
      ) : (
        <div className="space-y-3">
          {tunnels.map((t) => {
            const active = t.active;
            const st = active ? statusMap : null;
            return (
              <div key={t.id} className="rounded-2xl bg-slate-800/60 p-5 ring-1 ring-white/10">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="text-lg font-semibold text-white">{t.name}</span>
                      <span
                        className={`rounded px-2 py-0.5 text-xs ${
                          active
                            ? "bg-emerald-500/20 text-emerald-300"
                            : "bg-slate-500/20 text-slate-400"
                        }`}
                      >
                        {active ? "verbunden" : "getrennt"}
                      </span>
                      <span className="rounded bg-white/5 px-2 py-0.5 text-xs text-slate-400">
                        {TYPE_LABEL[t.type]}
                      </span>
                    </div>
                    {active && st && (
                      <div className="mt-1 text-xs text-slate-400">
                        {st.endpoint && <>Endpoint: {st.endpoint} · </>}
                        {st.last_handshake && <>Handshake: {st.last_handshake}</>}
                        {st.detail && <>{st.detail}</>}
                      </div>
                    )}
                    {!active && t.last_error && (
                      <div className="mt-1 text-xs text-red-300">{t.last_error}</div>
                    )}
                    {t.autostart && (
                      <div className="mt-1 text-xs text-slate-500">Autostart aktiv</div>
                    )}
                  </div>
                  <div className="flex gap-2">
                    {active ? (
                      <button
                        onClick={() => disconnect(t)}
                        disabled={busyId === t.id}
                        className="rounded border border-white/10 px-3 py-1.5 text-sm hover:bg-white/5 disabled:opacity-50"
                      >
                        Trennen
                      </button>
                    ) : (
                      <button
                        onClick={() => connect(t)}
                        disabled={busyId === t.id || (caps ? !caps.ready : false)}
                        className="rounded bg-gradient-to-r from-ogc-teal to-ogc-blue px-3 py-1.5 text-sm font-semibold text-white disabled:opacity-50"
                      >
                        {busyId === t.id ? "…" : "Verbinden"}
                      </button>
                    )}
                    <button
                      onClick={() => {
                        setEditing(t);
                        setShowForm(true);
                      }}
                      className="rounded border border-white/10 px-3 py-1.5 text-sm hover:bg-white/5"
                    >
                      Bearbeiten
                    </button>
                    <button
                      onClick={() => remove(t)}
                      className="rounded border border-red-500/30 px-3 py-1.5 text-sm text-red-300 hover:bg-red-500/10"
                    >
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

function CapBadge({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span
      className={`rounded px-2 py-0.5 ${
        ok ? "bg-emerald-500/15 text-emerald-300" : "bg-slate-600/20 text-slate-500"
      }`}
    >
      {ok ? "✓" : "✕"} {label}
    </span>
  );
}

function VpnForm({
  caps,
  editing,
  onDone,
  onCancel,
}: {
  caps: VpnCapabilities | null;
  editing: VpnTunnel | null;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState(editing?.name ?? "");
  const [type, setType] = useState<VpnType>(editing?.type ?? "wireguard");
  const [config, setConfig] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [autostart, setAutostart] = useState(editing?.autostart ?? false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (editing) {
        const body: Record<string, unknown> = { name, autostart };
        if (config.trim()) body.config = config;
        if (username) body.username = username;
        if (password) body.password = password;
        await api(`/api/vpn/${editing.id}`, { method: "PATCH", body: JSON.stringify(body) });
      } else {
        await api("/api/vpn", {
          method: "POST",
          body: JSON.stringify({ name, type, config, username, password, autostart }),
        });
      }
      onDone();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Speichern fehlgeschlagen");
    } finally {
      setBusy(false);
    }
  }

  const input =
    "w-full rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 outline-none focus:border-ogc-teal";
  const placeholder =
    type === "wireguard"
      ? "[Interface]\nPrivateKey = …\nAddress = 10.0.0.2/32\n\n[Peer]\nPublicKey = …\nEndpoint = deinanschluss.myfritz.net:51820\nAllowedIPs = 192.168.178.0/24"
      : "client\ndev tun\nproto udp\nremote deinanschluss.myfritz.net 1194\n…";

  return (
    <form onSubmit={save} className="mb-8 rounded-2xl bg-slate-800/60 p-5 ring-1 ring-white/10">
      <div className="mb-4 grid gap-3 sm:grid-cols-2">
        <label className="text-sm">
          <span className="mb-1 block text-slate-400">Name</span>
          <input className={input} value={name} onChange={(e) => setName(e.target.value)} required />
        </label>
        <label className="text-sm">
          <span className="mb-1 block text-slate-400">Typ</span>
          <select
            className={input}
            value={type}
            disabled={!!editing}
            onChange={(e) => setType(e.target.value as VpnType)}
          >
            <option value="wireguard" disabled={caps ? !caps.wireguard : false}>
              WireGuard{caps && !caps.wireguard ? " (nicht verfügbar)" : ""}
            </option>
            <option value="openvpn" disabled={caps ? !caps.openvpn : false}>
              OpenVPN{caps && !caps.openvpn ? " (nicht verfügbar)" : ""}
            </option>
          </select>
        </label>
      </div>

      <label className="mb-3 block text-sm">
        <span className="mb-1 block text-slate-400">
          Konfiguration{!editing && <span className="text-red-400"> *</span>}
          {editing && <span className="text-slate-500"> (leer lassen, um zu behalten)</span>}
        </span>
        <textarea
          className={`${input} h-40 font-mono text-xs`}
          value={config}
          onChange={(e) => setConfig(e.target.value)}
          placeholder={placeholder}
          required={!editing}
        />
        <span className="mt-1 block text-xs text-slate-500">
          {type === "wireguard"
            ? "Die WireGuard-Config aus der FRITZ!Box (WireGuard-Verbindung → Konfiguration anzeigen) hier einfügen."
            : "Die vollständige .ovpn-Datei hier einfügen."}
        </span>
      </label>

      {type === "openvpn" && (
        <div className="mb-3 grid gap-3 sm:grid-cols-2">
          <label className="text-sm">
            <span className="mb-1 block text-slate-400">Benutzer (optional)</span>
            <input className={input} value={username} onChange={(e) => setUsername(e.target.value)} />
          </label>
          <label className="text-sm">
            <span className="mb-1 block text-slate-400">Passwort (optional)</span>
            <input
              className={input}
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={editing?.has_username ? "••••••" : ""}
            />
          </label>
        </div>
      )}

      <label className="mb-1 flex items-center gap-2 text-sm">
        <input type="checkbox" checked={autostart} onChange={(e) => setAutostart(e.target.checked)} />
        <span className="text-slate-300">Beim Start automatisch verbinden</span>
      </label>

      {error && <div className="mt-4 rounded-lg bg-red-500/15 px-3 py-2 text-sm text-red-300">{error}</div>}

      <div className="mt-5 flex gap-2">
        <button
          type="submit"
          disabled={busy}
          className="rounded-lg bg-gradient-to-r from-ogc-teal to-ogc-blue px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
        >
          {editing ? "Speichern" : "Anlegen"}
        </button>
        <button type="button" onClick={onCancel} className="rounded-lg px-4 py-2 text-sm text-slate-400 hover:text-white">
          Abbrechen
        </button>
      </div>
    </form>
  );
}
