import { useEffect, useState } from "react";
import {
  api,
  ApiError,
  type BandwidthStatus,
  type BandwidthWindow,
  type MeteredMode,
  type NetworkStatus,
} from "../api";
import Layout from "../components/Layout";
import UplinkDiagram from "../components/UplinkDiagram";

export default function Bandwidth() {
  const [status, setStatus] = useState<BandwidthStatus | null>(null);
  // Which links exist (cable, Wi-Fi, phone) — for the bundling diagram.
  const [net, setNet] = useState<NetworkStatus | null>(null);
  const [enabled, setEnabled] = useState(false);
  const [minKbps, setMinKbps] = useState(0);
  const [bwlimit, setBwlimit] = useState(0);
  const [schedule, setSchedule] = useState<BandwidthWindow[]>([]);
  const [meteredMode, setMeteredMode] = useState<MeteredMode>("allow");
  const [budgetMb, setBudgetMb] = useState(500);
  const [bonding, setBonding] = useState(false);
  const [parallel, setParallel] = useState(1);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  function apply(s: BandwidthStatus) {
    setStatus(s);
    setEnabled(s.enabled);
    setMinKbps(s.min_bandwidth_kbps);
    setBwlimit(s.bwlimit_kbps);
    setSchedule(s.schedule);
    setMeteredMode(s.metered_mode);
    setBudgetMb(s.metered_budget_mb);
    setBonding(s.bonding_enabled);
    setParallel(s.parallel_uploads);
  }

  function load() {
    api<BandwidthStatus>("/api/bandwidth")
      .then(apply)
      .catch((e) => setError(e instanceof ApiError ? e.message : "Fehler"));
    api<NetworkStatus>("/api/network/status").then(setNet).catch(() => setNet(null));
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
          metered_mode: meteredMode,
          metered_budget_mb: budgetMb,
          parallel_uploads: parallel,
          // Only sent once net-merge is installed — the API refuses it otherwise.
          bonding_enabled: status?.bonding_available ? bonding : undefined,
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

  const uplinks = net?.links ?? [];
  // Interfaces net-merge reports that aren't uplinks in the diagram (wg0, docker0 …).
  const otherBondingLinks = (status?.bonding_links ?? []).filter(
    (l) => !uplinks.some((u) => u.device === l.name),
  );

  const field = "rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 outline-hidden focus:border-ogc-teal";

  return (
    <Layout>
      <h2 className="mb-1 text-2xl font-bold">Bandbreiten-Steuerung</h2>
      <p className="mb-6 text-sm text-slate-400">
        Drosselung, Zeitfenster und Mindest-Bandbreite (Werte in KB/s).
      </p>

      {error && <div className="mb-4 rounded-lg bg-red-500/15 px-3 py-2 text-sm text-red-300">{error}</div>}

      {status && (
        <div className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-3">
          <Tile
            title="Aktuelles Limit"
            value={(() => {
              // Own limit and a pool share both apply; the lower one wins.
              const limits = [status.effective_bwlimit_kbps, status.pool_limit_kbps].filter((k) => k > 0);
              return limits.length ? `${Math.min(...limits)} KB/s` : "unbegrenzt";
            })()}
            hint={
              status.pool_limit_kbps > 0
                ? `Anteil an gemeinsamer Leitung: ${status.pool_limit_kbps} KB/s (${status.pool_limit_by})`
                : undefined
            }
          />
          <div className="rounded-2xl bg-slate-800/60 p-5 shadow-lg ring-1 ring-white/5">
            <div className="mb-3 h-1.5 w-12 rounded-full bg-linear-to-r from-ogc-blue to-ogc-indigo" />
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

        <label className="mt-4 block text-sm">
          <span className="mb-1 block text-slate-400">Parallele Uploads</span>
          <select className={field} value={parallel} onChange={(e) => setParallel(Number(e.target.value))}>
            {[1, 2, 3, 4].map((n) => (
              <option key={n} value={n}>
                {n === 1 ? "1 (nacheinander)" : `${n} gleichzeitig`}
              </option>
            ))}
          </select>
          <span className="mt-1 block text-xs text-slate-500">
            Mehrere Dateien gleichzeitig hochladen. Lohnt sich vor allem mit Leitungsbündelung (jeder Upload kann
            eine andere Leitung nehmen) und bei vielen kleinen Dateien. Das Limit oben gilt für alle zusammen.
            Auf einem Pi 3 höchstens 2.
            {status && status.running_uploads > 0 && ` Gerade aktiv: ${status.running_uploads}.`}
          </span>
        </label>

        <div className="mt-6">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-sm font-semibold text-slate-300">Zeitfenster</span>
            <button type="button" onClick={addWindow} className="rounded-sm border border-white/10 px-2 py-1 text-xs hover:bg-white/5">
              + Fenster
            </button>
          </div>
          <p className="mb-2 text-xs text-slate-500">
            Innerhalb eines Fensters gilt dessen Limit (0 = volle Last, z. B. nachts). Fenster dürfen über Mitternacht gehen.
          </p>
          {schedule.length === 0 && <div className="text-sm text-slate-500">Keine Zeitfenster — es gilt immer das Standard-Limit.</div>}
          <div className="space-y-2">
            {schedule.map((w, i) => (
              <div key={i} className="flex flex-wrap items-center gap-2 text-sm">
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

        <div className="mt-6 border-t border-white/10 pt-5">
          <div className="mb-1 flex flex-wrap items-center gap-2">
            <span className="text-sm font-semibold text-slate-300">
              Getaktete Verbindung (USB-Tethering)
            </span>
            {status && (
              <span
                className={`rounded-full px-2 py-0.5 text-xs font-semibold ${
                  status.metered || net?.metered_present
                    ? "bg-amber-500/20 text-amber-200"
                    : "bg-white/5 text-slate-400"
                }`}
              >
                {status.metered
                  ? `aktiv: ${status.uplink}`
                  : net?.metered_present
                    ? "angeschlossen, nicht die Hauptleitung"
                    : "gerade nicht getaktet"}
              </span>
            )}
          </div>
          <p className="mb-3 text-xs text-slate-500">
            Hängt ein iPhone oder Android-Handy per USB als Uplink an der Box, laufen Uploads über
            dessen Datenvolumen. Gilt unabhängig vom Schalter oben.
          </p>
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="text-sm">
              <span className="mb-1 block text-slate-400">Uploads über getaktete Verbindung</span>
              <select
                className={`${field} w-full`}
                value={meteredMode}
                onChange={(e) => setMeteredMode(e.target.value as MeteredMode)}
              >
                <option value="allow">normal senden</option>
                <option value="limit">bis zum Tagesvolumen senden</option>
                <option value="pause">pausieren, bis Kabel oder WLAN da ist</option>
              </select>
            </label>
            {meteredMode === "limit" && (
              <label className="text-sm">
                <span className="mb-1 block text-slate-400">Tagesvolumen (MB)</span>
                <input
                  type="number"
                  min={1}
                  className={field}
                  value={budgetMb}
                  onChange={(e) => setBudgetMb(Number(e.target.value))}
                />
                <span className="mt-1 block text-xs text-slate-500">
                  Heute verbraucht: {status ? formatMb(status.metered_used_bytes) : "—"}. Dateien,
                  die nicht mehr ins Restvolumen passen, warten auf Kabel oder WLAN.
                </span>
              </label>
            )}
          </div>
        </div>

        <div className="mt-6 border-t border-white/10 pt-5">
          <div className="mb-1 flex flex-wrap items-center gap-2">
            <span className="text-sm font-semibold text-slate-300">Leitungsbündelung (net-merge)</span>
            {status && (
              <span
                className={`rounded-full px-2 py-0.5 text-xs font-semibold ${
                  !status.bonding_available
                    ? "bg-white/5 text-slate-400"
                    : status.bonding_enabled && status.bonding_running
                      ? "bg-emerald-500/20 text-emerald-200"
                      : status.bonding_enabled
                        ? "bg-red-500/20 text-red-200"
                        : "bg-white/5 text-slate-400"
                }`}
              >
                {!status.bonding_available
                  ? "nicht installiert"
                  : !status.bonding_enabled
                    ? "aus"
                    : status.bonding_running
                      ? "aktiv"
                      : "net-merge läuft nicht – Uploads gehen direkt"}
              </span>
            )}
          </div>
          <p className="mb-3 text-xs text-slate-500">
            Verteilt Uploads auf alle Leitungen gleichzeitig – Kabel, WLAN und Handy. Bringt nur etwas,
            wenn die Leitungen unabhängig ins Internet gehen (z. B. WLAN + LTE-Handy), und vor allem bei
            großen Dateien zu S3, Azure oder B2, die in mehreren Teilen parallel hochgeladen werden. Ziele
            im LAN oder hinter dem VPN gehen weiter direkt. Ein angestecktes Handy wird nur mitgenutzt,
            wenn getaktete Verbindungen auf „normal senden“ stehen.
          </p>
          {status && !status.bonding_available ? (
            <p className="text-xs text-slate-400">
              Einrichten an der Box mit{" "}
              <code className="rounded-sm bg-slate-900/60 px-1">sudo /opt/offgridcloud/deploy/netmerge/install.sh</code>
            </p>
          ) : (
            <label className="flex items-center gap-3">
              <input type="checkbox" checked={bonding} onChange={(e) => setBonding(e.target.checked)} />
              <span className="text-sm text-slate-200">Uploads über alle Leitungen bündeln</span>
            </label>
          )}
          {status && uplinks.length > 0 && (
            <div className="mt-4">
              <UplinkDiagram
                links={uplinks}
                bonding={
                  status.bonding_available
                    ? {
                        enabled: status.bonding_enabled,
                        links: status.bonding_links,
                        bypass: status.bonding_bypass ?? "",
                      }
                    : undefined
                }
              />
            </div>
          )}
          {otherBondingLinks.length > 0 && (
            <ul className="mt-3 space-y-1 text-xs">
              {otherBondingLinks.map((l) => (
                <li key={l.name} className="flex flex-wrap gap-2">
                  <span className={l.used ? "font-semibold text-emerald-300" : "text-slate-500"}>
                    {l.used ? "✓" : "–"} {l.name}
                  </span>
                  <span className="text-slate-500">{l.detail}</span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="mt-6 flex items-center gap-3">
          <button type="submit" className="rounded-lg bg-linear-to-r from-ogc-teal to-ogc-blue px-4 py-2 font-semibold text-white">
            Speichern
          </button>
          {saved && <span className="text-sm text-emerald-300">Gespeichert.</span>}
        </div>
      </form>
    </Layout>
  );
}

function formatMb(bytes: number): string {
  return `${(bytes / (1024 * 1024)).toFixed(bytes < 10 * 1024 * 1024 ? 1 : 0)} MB`;
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
      <div className={`mb-3 h-1.5 w-12 rounded-full bg-linear-to-r ${accent}`} />
      <div className="text-sm font-medium text-slate-400">{title}</div>
      <div className="mt-1 text-2xl font-bold text-white">{value}</div>
      {hint && <div className="mt-1 text-xs text-slate-500">{hint}</div>}
    </div>
  );
}
