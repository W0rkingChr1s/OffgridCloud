import { useEffect, useState } from "react";
import { api, ApiError, type BandwidthStatus, type BandwidthWindow } from "../api";
import Layout from "../components/Layout";

export default function Bandwidth() {
  const [status, setStatus] = useState<BandwidthStatus | null>(null);
  const [enabled, setEnabled] = useState(false);
  const [minKbps, setMinKbps] = useState(0);
  const [bwlimit, setBwlimit] = useState(0);
  const [schedule, setSchedule] = useState<BandwidthWindow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  function apply(s: BandwidthStatus) {
    setStatus(s);
    setEnabled(s.enabled);
    setMinKbps(s.min_bandwidth_kbps);
    setBwlimit(s.bwlimit_kbps);
    setSchedule(s.schedule);
  }

  function load() {
    api<BandwidthStatus>("/api/bandwidth")
      .then(apply)
      .catch((e) => setError(e instanceof ApiError ? e.message : "Fehler"));
  }
  useEffect(load, []);

  async function probe() {
    setError(null);
    try {
      apply(await api<BandwidthStatus>("/api/bandwidth/probe", { method: "POST" }));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Messung fehlgeschlagen");
    }
  }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSaved(false);
    try {
      const s = await api<BandwidthStatus>("/api/bandwidth", {
        method: "PUT",
        body: JSON.stringify({
          enabled,
          min_bandwidth_kbps: minKbps,
          bwlimit_kbps: bwlimit,
          schedule,
        }),
      });
      apply(s);
      setSaved(true);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Speichern fehlgeschlagen");
    }
  }

  function addWindow() {
    setSchedule((s) => [...s, { start: "22:00", end: "06:00", kbps: 0 }]);
  }
  function updateWindow(i: number, patch: Partial<BandwidthWindow>) {
    setSchedule((s) => s.map((w, idx) => (idx === i ? { ...w, ...patch } : w)));
  }
  function removeWindow(i: number) {
    setSchedule((s) => s.filter((_, idx) => idx !== i));
  }

  const field = "rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 outline-none focus:border-ogc-teal";

  return (
    <Layout>
      <h2 className="mb-1 text-2xl font-bold">Bandbreiten-Steuerung</h2>
      <p className="mb-6 text-sm text-slate-400">
        Drosselung, Zeitfenster und Mindest-Bandbreite (Werte in KB/s).
      </p>

      {error && <div className="mb-4 rounded-lg bg-red-500/15 px-3 py-2 text-sm text-red-300">{error}</div>}

      {status && (
        <div className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-3">
          <Tile title="Aktuelles Limit" value={status.effective_bwlimit_kbps === 0 ? "unbegrenzt" : `${status.effective_bwlimit_kbps} KB/s`} />
          <div className="rounded-2xl bg-slate-800/60 p-5 shadow-lg ring-1 ring-white/5">
            <div className="mb-3 h-1.5 w-12 rounded-full bg-gradient-to-r from-ogc-blue to-ogc-indigo" />
            <div className="text-sm font-medium text-slate-400">Zuletzt gemessen</div>
            <div className="mt-1 text-2xl font-bold text-white">
              {status.last_kbps > 0 ? `${status.last_kbps.toFixed(0)} KB/s` : "—"}
            </div>
            <div className="mt-1 text-xs text-slate-500">
              {status.last_measured_at ? new Date(status.last_measured_at + "Z").toLocaleString() : "noch keine Messung"}
            </div>
            <button
              type="button"
              onClick={probe}
              className="mt-3 rounded-lg border border-white/10 px-3 py-1.5 text-xs hover:bg-white/5"
            >
              Jetzt messen
            </button>
          </div>
          <Tile
            title="Upload-Status"
            value={status.gated ? "pausiert" : "bereit"}
            hint={status.gate_reason || undefined}
            accent={status.gated ? "from-red-500 to-orange-500" : "from-ogc-teal to-ogc-blue"}
          />
        </div>
      )}

      <form onSubmit={save} className="rounded-2xl bg-slate-800/60 p-5 ring-1 ring-white/10">
        <label className="mb-4 flex items-center gap-3">
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
          <span className="text-sm text-slate-200">Bandbreiten-bewusstes Scheduling aktivieren</span>
        </label>

        <div className="grid gap-4 sm:grid-cols-2">
          <label className="text-sm">
            <span className="mb-1 block text-slate-400">Standard-Limit (KB/s, 0 = unbegrenzt)</span>
            <input type="number" min={0} className={field} value={bwlimit} onChange={(e) => setBwlimit(Number(e.target.value))} />
          </label>
          <label className="text-sm">
            <span className="mb-1 block text-slate-400">Mindest-Bandbreite (KB/s, 0 = aus)</span>
            <input type="number" min={0} className={field} value={minKbps} onChange={(e) => setMinKbps(Number(e.target.value))} />
            <span className="mt-1 block text-xs text-slate-500">
              Unterhalb dieses Werts pausieren neue Uploads (best-effort, aus Transfer-Messung).
            </span>
          </label>
        </div>

        <div className="mt-6">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-sm font-semibold text-slate-300">Zeitfenster</span>
            <button type="button" onClick={addWindow} className="rounded border border-white/10 px-2 py-1 text-xs hover:bg-white/5">
              + Fenster
            </button>
          </div>
          <p className="mb-2 text-xs text-slate-500">
            Innerhalb eines Fensters gilt dessen Limit (0 = volle Last, z. B. nachts). Fenster dürfen über Mitternacht gehen.
          </p>
          {schedule.length === 0 && <div className="text-sm text-slate-500">Keine Zeitfenster — es gilt immer das Standard-Limit.</div>}
          <div className="space-y-2">
            {schedule.map((w, i) => (
              <div key={i} className="flex items-center gap-2 text-sm">
                <input type="time" className={field} value={w.start} onChange={(e) => updateWindow(i, { start: e.target.value })} />
                <span className="text-slate-500">bis</span>
                <input type="time" className={field} value={w.end} onChange={(e) => updateWindow(i, { end: e.target.value })} />
                <input
                  type="number"
                  min={0}
                  className={`${field} w-28`}
                  value={w.kbps}
                  onChange={(e) => updateWindow(i, { kbps: Number(e.target.value) })}
                  placeholder="KB/s"
                />
                <span className="text-xs text-slate-500">KB/s</span>
                <button type="button" onClick={() => removeWindow(i)} className="ml-auto text-xs text-red-300 hover:underline">
                  entfernen
                </button>
              </div>
            ))}
          </div>
        </div>

        <div className="mt-6 flex items-center gap-3">
          <button type="submit" className="rounded-lg bg-gradient-to-r from-ogc-teal to-ogc-blue px-4 py-2 font-semibold text-white">
            Speichern
          </button>
          {saved && <span className="text-sm text-emerald-300">Gespeichert.</span>}
        </div>
      </form>
    </Layout>
  );
}

function Tile({
  title,
  value,
  hint,
  accent = "from-ogc-teal to-ogc-blue",
}: {
  title: string;
  value: string;
  hint?: string;
  accent?: string;
}) {
  return (
    <div className="rounded-2xl bg-slate-800/60 p-5 shadow-lg ring-1 ring-white/5">
      <div className={`mb-3 h-1.5 w-12 rounded-full bg-gradient-to-r ${accent}`} />
      <div className="text-sm font-medium text-slate-400">{title}</div>
      <div className="mt-1 text-2xl font-bold text-white">{value}</div>
      {hint && <div className="mt-1 text-xs text-slate-500">{hint}</div>}
    </div>
  );
}
