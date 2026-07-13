import { useEffect, useState } from "react";
import {
  api,
  ApiError,
  type KnownNetwork,
  type NetworkApplyResult,
  type NetworkOverview,
  type NetworkSettings,
  type NetworkStatus,
} from "../api";
import Layout from "../components/Layout";

const MODE_LABELS: Record<NetworkStatus["mode"], string> = {
  ethernet: "Kabel (Ethernet)",
  client: "WLAN-Client",
  ap: "Eigener Access Point",
  offline: "Offline",
  unknown: "Unbekannt",
};

export default function Network() {
  const [data, setData] = useState<NetworkOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [applyMsg, setApplyMsg] = useState<NetworkApplyResult | null>(null);
  const [busy, setBusy] = useState(false);

  function report(e: unknown) {
    setError(e instanceof ApiError ? e.message : "Fehler");
  }
  function load() {
    api<NetworkOverview>("/api/network").then(setData).catch(report);
  }
  useEffect(load, []);

  async function apply() {
    setBusy(true);
    setApplyMsg(null);
    setError(null);
    try {
      const r = await api<NetworkApplyResult>("/api/network/apply", { method: "POST" });
      setApplyMsg(r);
      load();
    } catch (e) {
      report(e);
    } finally {
      setBusy(false);
    }
  }

  const status = data?.status;

  return (
    <Layout>
      <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="mb-1 text-2xl font-bold">Netzwerk-Redundanz</h2>
          <p className="text-sm text-slate-400">
            Rückfallebene: Fällt der Router aus, hostet die Box ihr eigenes WLAN — bis ein
            hinterlegtes Netzwerk wieder erreichbar ist.
          </p>
        </div>
        <button
          type="button"
          onClick={apply}
          disabled={busy}
          className="rounded-lg bg-gradient-to-r from-ogc-teal to-ogc-blue px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
        >
          {busy ? "…" : "Anwenden"}
        </button>
      </div>

      {error && (
        <div className="mb-4 rounded-lg bg-red-500/15 px-3 py-2 text-sm text-red-300">{error}</div>
      )}
      {applyMsg && (
        <div
          className={`mb-4 rounded-lg px-3 py-2 text-sm ${
            applyMsg.ok ? "bg-emerald-500/15 text-emerald-300" : "bg-amber-500/15 text-amber-200"
          }`}
        >
          {applyMsg.message}
          {applyMsg.output && (
            <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-black/30 p-2 text-xs text-slate-300">
              {applyMsg.output}
            </pre>
          )}
        </div>
      )}

      {status && <StatusCard status={status} />}

      {status && !status.supported && (
        <div className="mb-6 rounded-2xl bg-slate-800/60 p-5 text-sm text-slate-300 ring-1 ring-amber-400/20">
          <div className="mb-1 font-medium text-amber-200">Live-Steuerung nicht verfügbar</div>
          {status.detail || "NetworkManager (nmcli) wurde auf diesem Host nicht gefunden."}
          <div className="mt-2 text-xs text-slate-500">
            Die Einstellungen unten werden trotzdem gespeichert und beim nächsten „Anwenden“ auf
            einem Gerät mit NetworkManager (z. B. Raspberry Pi OS) übernommen. Details:
            docs/NETZWERK-REDUNDANZ.md.
          </div>
        </div>
      )}

      {data && (
        <>
          <FallbackSettings settings={data.settings} onSaved={load} onError={report} />
          <KnownNetworks networks={data.known_networks} onChanged={load} onError={report} />
        </>
      )}
    </Layout>
  );
}

function StatusCard({ status }: { status: NetworkStatus }) {
  const online = status.online;
  const badge = status.ap_active
    ? { text: "AP aktiv", cls: "bg-amber-500/20 text-amber-200" }
    : online
      ? { text: "Online", cls: "bg-emerald-500/20 text-emerald-300" }
      : { text: "Offline", cls: "bg-red-500/20 text-red-300" };
  return (
    <div className="mb-6 rounded-2xl bg-slate-800/60 p-5 ring-1 ring-white/10">
      <div className="mb-3 flex items-center gap-3">
        <span className="text-sm font-medium text-slate-400">Aktueller Status</span>
        <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${badge.cls}`}>
          {badge.text}
        </span>
      </div>
      <div className="grid gap-4 sm:grid-cols-3">
        <Field label="Modus" value={MODE_LABELS[status.mode]} />
        <Field
          label="Verbunden mit"
          value={status.wifi_ssid || (status.ethernet ? "Kabel" : status.ap_ssid || "—")}
        />
        <Field label="IP-Adresse" value={status.wifi_ip || "—"} />
      </div>
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs text-slate-500">{label}</div>
      <div className="truncate text-lg font-semibold text-white">{value}</div>
    </div>
  );
}

function FallbackSettings({
  settings,
  onSaved,
  onError,
}: {
  settings: NetworkSettings;
  onSaved: () => void;
  onError: (e: unknown) => void;
}) {
  const [form, setForm] = useState(settings);
  const [password, setPassword] = useState("");
  const [saving, setSaving] = useState(false);
  useEffect(() => setForm(settings), [settings]);

  async function save(patch: Record<string, unknown>) {
    setSaving(true);
    try {
      await api<NetworkSettings>("/api/network/settings", {
        method: "PUT",
        body: JSON.stringify(patch),
      });
      onSaved();
    } catch (e) {
      onError(e);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="mb-6 rounded-2xl bg-slate-800/60 p-5 ring-1 ring-white/10">
      <label className="mb-4 flex items-start gap-3">
        <input
          type="checkbox"
          className="mt-1"
          checked={form.fallback_enabled}
          onChange={(e) => save({ fallback_enabled: e.target.checked })}
        />
        <span className="text-sm">
          <span className="font-medium text-white">Rückfall-WLAN aktivieren</span>
          <span className="mt-0.5 block text-xs text-slate-500">
            Ist kein hinterlegtes Netzwerk erreichbar, öffnet die Box automatisch ihren eigenen
            Access Point, damit das Feld-Team weiter hochladen kann.
          </span>
        </span>
      </label>

      <div className="grid gap-4 sm:grid-cols-2">
        <label className="text-sm">
          <span className="mb-1 block text-slate-400">AP-Name (SSID)</span>
          <input
            className="w-full rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 outline-none focus:border-ogc-teal"
            value={form.ap_ssid}
            onChange={(e) => setForm({ ...form, ap_ssid: e.target.value })}
            onBlur={() => form.ap_ssid !== settings.ap_ssid && save({ ap_ssid: form.ap_ssid })}
          />
        </label>
        <label className="text-sm">
          <span className="mb-1 block text-slate-400">AP-Passwort</span>
          <input
            type="password"
            className="w-full rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 outline-none focus:border-ogc-teal"
            placeholder={settings.ap_has_password ? "•••••••• (gesetzt)" : "8–63 Zeichen (leer = offen)"}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onBlur={() => {
              if (password) {
                save({ ap_password: password });
                setPassword("");
              }
            }}
          />
        </label>
        <label className="text-sm">
          <span className="mb-1 block text-slate-400">AP-Adresse</span>
          <input
            className="w-full rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 outline-none focus:border-ogc-teal"
            value={form.ap_address}
            onChange={(e) => setForm({ ...form, ap_address: e.target.value })}
            onBlur={() =>
              form.ap_address !== settings.ap_address && save({ ap_address: form.ap_address })
            }
          />
        </label>
        <label className="text-sm">
          <span className="mb-1 block text-slate-400">Ländercode (WLAN-Regulierung)</span>
          <input
            className="w-full rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 outline-none focus:border-ogc-teal"
            placeholder="z. B. DE"
            maxLength={2}
            value={form.country_code}
            onChange={(e) => setForm({ ...form, country_code: e.target.value.toUpperCase() })}
            onBlur={() =>
              form.country_code !== settings.country_code &&
              save({ country_code: form.country_code })
            }
          />
        </label>
        <label className="text-sm">
          <span className="mb-1 block text-slate-400">Prüf-Intervall (Sekunden)</span>
          <input
            type="number"
            min={5}
            max={3600}
            className="w-full rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 outline-none focus:border-ogc-teal"
            value={form.check_interval}
            onChange={(e) => setForm({ ...form, check_interval: Number(e.target.value) })}
            onBlur={() =>
              form.check_interval !== settings.check_interval &&
              save({ check_interval: form.check_interval })
            }
          />
        </label>
        <label className="text-sm">
          <span className="mb-1 block text-slate-400">Fehlversuche bis Umschalten</span>
          <input
            type="number"
            min={1}
            max={20}
            className="w-full rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 outline-none focus:border-ogc-teal"
            value={form.fail_threshold}
            onChange={(e) => setForm({ ...form, fail_threshold: Number(e.target.value) })}
            onBlur={() =>
              form.fail_threshold !== settings.fail_threshold &&
              save({ fail_threshold: form.fail_threshold })
            }
          />
        </label>
      </div>

      <label className="mt-4 flex items-center gap-2 text-sm text-slate-300">
        <input
          type="checkbox"
          checked={form.ap_hidden}
          onChange={(e) => save({ ap_hidden: e.target.checked })}
        />
        AP-Namen verbergen (SSID nicht senden)
      </label>
      {saving && <div className="mt-2 text-xs text-slate-500">Speichern…</div>}
    </div>
  );
}

function KnownNetworks({
  networks,
  onChanged,
  onError,
}: {
  networks: KnownNetwork[];
  onChanged: () => void;
  onError: (e: unknown) => void;
}) {
  const [ssid, setSsid] = useState("");
  const [password, setPassword] = useState("");
  const [priority, setPriority] = useState(0);

  async function add() {
    if (!ssid.trim()) return;
    try {
      await api<KnownNetwork>("/api/network/known", {
        method: "POST",
        body: JSON.stringify({ ssid: ssid.trim(), password, priority }),
      });
      setSsid("");
      setPassword("");
      setPriority(0);
      onChanged();
    } catch (e) {
      onError(e);
    }
  }

  async function remove(id: number) {
    try {
      await api(`/api/network/known/${id}`, { method: "DELETE" });
      onChanged();
    } catch (e) {
      onError(e);
    }
  }

  return (
    <div className="mb-6 rounded-2xl bg-slate-800/60 p-5 ring-1 ring-white/10">
      <div className="mb-1 text-sm font-medium text-white">Hinterlegte Netzwerke</div>
      <p className="mb-4 text-xs text-slate-500">
        WLANs, mit denen sich die Box automatisch verbindet. Höhere Priorität wird bevorzugt. Ist
        eines erreichbar, verlässt die Box den Rückfall-AP.
      </p>

      <div className="mb-4 grid gap-2 sm:grid-cols-[1fr,1fr,auto,auto]">
        <input
          className="rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 text-sm outline-none focus:border-ogc-teal"
          placeholder="SSID"
          value={ssid}
          onChange={(e) => setSsid(e.target.value)}
        />
        <input
          type="password"
          className="rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 text-sm outline-none focus:border-ogc-teal"
          placeholder="Passwort (leer = offen)"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <input
          type="number"
          min={0}
          className="w-24 rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 text-sm outline-none focus:border-ogc-teal"
          placeholder="Prio"
          value={priority}
          onChange={(e) => setPriority(Number(e.target.value))}
        />
        <button
          type="button"
          onClick={add}
          className="rounded-lg border border-white/10 px-4 py-2 text-sm font-medium text-slate-200 hover:bg-white/5"
        >
          Hinzufügen
        </button>
      </div>

      {networks.length === 0 ? (
        <p className="text-sm text-slate-500">Noch keine Netzwerke hinterlegt.</p>
      ) : (
        <div className="overflow-hidden rounded-xl ring-1 ring-white/10">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-800/80 text-slate-400">
              <tr>
                <th className="px-4 py-2">SSID</th>
                <th className="px-4 py-2">Priorität</th>
                <th className="px-4 py-2">Passwort</th>
                <th className="px-4 py-2">Auto</th>
                <th className="px-4 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {networks.map((n) => (
                <tr key={n.id} className="border-t border-white/5 bg-slate-900/40">
                  <td className="px-4 py-2 font-medium text-white">{n.ssid}</td>
                  <td className="px-4 py-2 text-slate-400">{n.priority}</td>
                  <td className="px-4 py-2 text-slate-400">{n.has_password ? "🔒" : "offen"}</td>
                  <td className="px-4 py-2 text-slate-400">{n.autoconnect ? "ja" : "nein"}</td>
                  <td className="px-4 py-2 text-right">
                    <button
                      type="button"
                      onClick={() => remove(n.id)}
                      className="rounded-lg px-2 py-1 text-xs text-red-300 hover:bg-red-500/10"
                    >
                      Entfernen
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
