import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Folder, type Provider, type SystemStatus } from "../api";
import { useAuth } from "../auth";
import Layout from "../components/Layout";
import { useEvents, type FolderSnapshot } from "../events";
import { formatBytes } from "../upload";

const ACCENTS = [
  "from-ogc-teal to-ogc-blue",
  "from-ogc-blue to-ogc-indigo",
  "from-ogc-indigo to-ogc-teal",
];

/** KPI tile shown in the stat grid at the top of the overview. */
function Stat({
  label,
  value,
  hint,
  tone = "default",
  to,
}: {
  label: string;
  value: string | number;
  hint?: string;
  tone?: "default" | "good" | "active" | "warn" | "bad";
  to?: string;
}) {
  const toneCls = {
    default: "text-white",
    good: "text-emerald-400",
    active: "text-ogc-teal",
    warn: "text-amber-400",
    bad: "text-red-400",
  }[tone];

  const inner = (
    <>
      <div className="text-sm text-slate-400">{label}</div>
      <div className={`mt-1 text-3xl font-bold tabular-nums ${toneCls}`}>{value}</div>
      {hint && <div className="mt-1 text-xs text-slate-500">{hint}</div>}
    </>
  );

  const base =
    "rounded-2xl bg-slate-800/60 p-5 shadow-lg ring-1 ring-white/5 transition";
  return to ? (
    <Link to={to} className={`${base} block hover:ring-ogc-teal/40`}>
      {inner}
    </Link>
  ) : (
    <div className={base}>{inner}</div>
  );
}

export default function Overview() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const snapshot = useEvents();
  const [meta, setMeta] = useState<Folder[]>([]);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [system, setSystem] = useState<SystemStatus | null>(null);

  useEffect(() => {
    api<Folder[]>("/api/folders").then(setMeta).catch(() => setMeta([]));
  }, []);

  // Admin-only extras: provider health and disk usage.
  useEffect(() => {
    if (!isAdmin) return;
    api<Provider[]>("/api/providers").then(setProviders).catch(() => setProviders([]));
    api<SystemStatus>("/api/system").then(setSystem).catch(() => setSystem(null));
  }, [isAdmin]);

  // Prefer live SSE folder counts; fall back to the folder metadata.
  const descById = Object.fromEntries(meta.map((m) => [m.id, m.description]));
  const folders: FolderSnapshot[] =
    snapshot?.folders ??
    meta.map((m) => ({
      id: m.id,
      name: m.name,
      total: m.media_count,
      done: 0,
      uploading: 0,
      queued: 0,
      failed: 0,
    }));

  const agg = useMemo(
    () =>
      folders.reduce(
        (a, f) => ({
          total: a.total + f.total,
          done: a.done + f.done,
          uploading: a.uploading + f.uploading,
          queued: a.queued + f.queued,
          failed: a.failed + f.failed,
        }),
        { total: 0, done: 0, uploading: 0, queued: 0, failed: 0 },
      ),
    [folders],
  );

  const pct = agg.total > 0 ? Math.round((agg.done / agg.total) * 100) : 0;
  const bw = snapshot?.bandwidth;
  const active = snapshot?.transfers?.active ?? [];
  const running = snapshot?.transfers?.counts?.running ?? agg.uploading;
  const queuedJobs = snapshot?.transfers?.counts?.queued ?? agg.queued;

  const providersOk = providers.filter((p) => p.status === "ok").length;
  const providersErr = providers.filter((p) => p.status === "error").length;

  // Top folders by outstanding work (uploading + queued), then by size.
  const topFolders = [...folders]
    .sort((a, b) => b.uploading + b.queued - (a.uploading + a.queued) || b.total - a.total)
    .slice(0, 5);

  return (
    <Layout>
      <div className="mb-6">
        <h2 className="text-2xl font-bold sm:text-3xl">Übersicht</h2>
        <p className="mt-0.5 text-sm text-slate-400">
          {user?.name ? `Willkommen zurück, ${user.name}. ` : ""}
          Live-Status deiner Box auf einen Blick.
        </p>
      </div>

      {/* Status hero: bandwidth gate + measured line (admin) */}
      {isAdmin && bw && (
        <div className="mb-6 overflow-hidden rounded-2xl bg-gradient-to-br from-slate-800/70 to-slate-800/30 p-5 shadow-lg ring-1 ring-white/5">
          <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
            <span className="flex items-center gap-2.5">
              <span
                className={`relative flex h-3 w-3 ${bw.gated ? "" : "animate-pulse"}`}
                aria-hidden
              >
                <span
                  className={`inline-flex h-3 w-3 rounded-full ${bw.gated ? "bg-red-400" : "bg-emerald-400"}`}
                />
              </span>
              <span className="text-base font-semibold text-white">
                {bw.gated ? "Uploads pausiert" : "Uploads bereit"}
              </span>
            </span>

            {/* Signal bars */}
            <span className="flex items-end gap-1" aria-hidden>
              <span className="h-2.5 w-1.5 rounded-sm bg-ogc-teal" />
              <span className="h-3.5 w-1.5 rounded-sm bg-ogc-teal" />
              <span className={`h-5 w-1.5 rounded-sm ${bw.gated ? "bg-slate-600" : "bg-ogc-teal"}`} />
            </span>

            <span className="text-sm text-slate-400">
              Limit:{" "}
              <span className="font-medium text-slate-200">
                {bw.effective_bwlimit_kbps === 0 ? "unbegrenzt" : `${bw.effective_bwlimit_kbps} KB/s`}
              </span>
            </span>
            {bw.last_kbps > 0 && (
              <span className="text-sm text-slate-400">
                Gemessen: <span className="font-medium text-slate-200">{bw.last_kbps.toFixed(0)} KB/s</span>
              </span>
            )}
            {bw.gated && bw.gate_reason && (
              <span className="text-sm text-amber-400/90">{bw.gate_reason}</span>
            )}
            <Link
              to="/admin/bandwidth"
              className="ml-auto rounded-lg border border-white/10 px-3 py-1.5 text-sm text-slate-300 transition hover:bg-white/5"
            >
              Bandbreite
            </Link>
          </div>
        </div>
      )}

      {/* KPI stat grid */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="Dateien gesamt" value={agg.total} hint={`${folders.length} Ordner`} to="/folders" />
        <Stat
          label="Fertig"
          value={agg.done}
          hint={agg.total > 0 ? `${pct}% übertragen` : "—"}
          tone="good"
        />
        <Stat label="In Übertragung" value={running} hint="läuft gerade" tone="active" />
        <Stat
          label={agg.failed > 0 ? "Fehler" : "Warteschlange"}
          value={agg.failed > 0 ? agg.failed : queuedJobs}
          hint={agg.failed > 0 ? "brauchen Aufmerksamkeit" : "wartet auf Upload"}
          tone={agg.failed > 0 ? "bad" : "default"}
          to={isAdmin ? "/admin/transfers" : undefined}
        />
      </div>

      {/* Overall progress */}
      {agg.total > 0 && (
        <div className="mt-5 rounded-2xl bg-slate-800/40 p-5 ring-1 ring-white/5">
          <div className="mb-2 flex items-baseline justify-between">
            <span className="text-sm font-medium text-slate-300">Gesamt-Fortschritt in die Cloud</span>
            <span className="text-sm tabular-nums text-slate-400">
              {agg.done} / {agg.total} · {pct}%
            </span>
          </div>
          <div className="h-2.5 overflow-hidden rounded-full bg-slate-700">
            <div
              className="h-full rounded-full bg-gradient-to-r from-ogc-teal to-ogc-blue transition-all"
              style={{ width: `${pct}%` }}
            />
          </div>
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-500">
            <span className="text-emerald-400">{agg.done} fertig</span>
            {agg.uploading > 0 && <span className="text-ogc-teal">{agg.uploading} lädt</span>}
            {agg.queued > 0 && <span>{agg.queued} wartend</span>}
            {agg.failed > 0 && <span className="text-red-400">{agg.failed} Fehler</span>}
          </div>
        </div>
      )}

      {/* Two-column: active transfers + folders */}
      <div className="mt-5 grid grid-cols-1 gap-5 lg:grid-cols-2">
        {/* Active transfers */}
        <section className="rounded-2xl bg-slate-800/40 p-5 ring-1 ring-white/5">
          <div className="mb-4 flex items-center justify-between">
            <h3 className="text-lg font-semibold text-white">Aktive Übertragungen</h3>
            {isAdmin && (
              <Link to="/admin/transfers" className="text-sm text-ogc-teal hover:underline">
                Alle Transfers
              </Link>
            )}
          </div>
          {active.length === 0 ? (
            <div className="rounded-xl bg-slate-900/40 px-4 py-8 text-center text-sm text-slate-500">
              Gerade laufen keine Uploads.
              {queuedJobs > 0 && ` ${queuedJobs} in der Warteschlange.`}
            </div>
          ) : (
            <ul className="space-y-3">
              {active.slice(0, 5).map((t) => {
                const p = Math.round((t.progress || 0) * 100);
                return (
                  <li key={t.id}>
                    <div className="flex items-baseline justify-between gap-3">
                      <span className="truncate text-sm font-medium text-white">{t.filename}</span>
                      <span className="shrink-0 text-xs text-slate-400">{t.provider}</span>
                    </div>
                    <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-slate-700">
                      <div
                        className="h-full rounded-full bg-gradient-to-r from-ogc-teal to-ogc-blue transition-all"
                        style={{ width: `${p}%` }}
                      />
                    </div>
                    <div className="mt-1 flex justify-between text-xs text-slate-500">
                      <span>
                        {t.total > 0 && `${formatBytes(t.bytes)} / ${formatBytes(t.total)}`}
                      </span>
                      <span className="tabular-nums">
                        {t.kbps > 0 && `${t.kbps.toFixed(0)} KB/s`} · {p}%
                      </span>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        {/* Folders quick list */}
        <section className="rounded-2xl bg-slate-800/40 p-5 ring-1 ring-white/5">
          <div className="mb-4 flex items-center justify-between">
            <h3 className="text-lg font-semibold text-white">Ordner</h3>
            <Link to="/folders" className="text-sm text-ogc-teal hover:underline">
              Alle Ordner
            </Link>
          </div>
          {folders.length === 0 ? (
            <div className="rounded-xl bg-slate-900/40 px-4 py-8 text-center text-sm text-slate-500">
              {isAdmin
                ? "Noch keine Ordner. Lege unter „Verwalten“ den ersten an."
                : "Dir wurden noch keine Ordner freigegeben."}
            </div>
          ) : (
            <ul className="space-y-2">
              {topFolders.map((f, i) => {
                const fp = f.total > 0 ? Math.round((f.done / f.total) * 100) : 0;
                return (
                  <li key={f.id}>
                    <Link
                      to={`/folders/${f.id}`}
                      className="group flex items-center gap-3 rounded-xl px-3 py-2.5 transition hover:bg-white/5"
                    >
                      <span className={`h-8 w-1.5 shrink-0 rounded-full bg-gradient-to-b ${ACCENTS[i % 3]}`} />
                      <span className="min-w-0 flex-1">
                        <span className="flex items-baseline justify-between gap-2">
                          <span className="truncate text-sm font-medium text-white group-hover:text-ogc-teal">
                            {f.name}
                          </span>
                          <span className="shrink-0 text-xs tabular-nums text-slate-500">
                            {f.total > 0 ? `${fp}%` : "leer"}
                          </span>
                        </span>
                        <span className="mt-1 flex h-1 overflow-hidden rounded-full bg-slate-700">
                          <span
                            className="h-full rounded-full bg-gradient-to-r from-ogc-teal to-ogc-blue"
                            style={{ width: `${fp}%` }}
                          />
                        </span>
                        {descById[f.id] && (
                          <span className="mt-1 block truncate text-xs text-slate-500">{descById[f.id]}</span>
                        )}
                      </span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      </div>

      {/* Admin: providers + storage */}
      {isAdmin && (
        <div className="mt-5 grid grid-cols-1 gap-5 sm:grid-cols-2">
          <Link
            to="/admin/providers"
            className="flex items-center justify-between rounded-2xl bg-slate-800/40 p-5 ring-1 ring-white/5 transition hover:ring-ogc-teal/40"
          >
            <div>
              <div className="text-sm text-slate-400">Cloud-Ziele</div>
              <div className="mt-1 text-2xl font-bold text-white tabular-nums">{providers.length}</div>
              <div className="mt-1 text-xs text-slate-500">
                {providersOk > 0 && <span className="text-emerald-400">{providersOk} ok</span>}
                {providersOk > 0 && providersErr > 0 && " · "}
                {providersErr > 0 && <span className="text-red-400">{providersErr} Fehler</span>}
                {providers.length === 0 && "noch keine konfiguriert"}
              </div>
            </div>
            <svg viewBox="0 0 24 24" className="h-9 w-9 text-slate-600" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
              <path d="M7 18a4 4 0 0 1-.5-7.97A5.5 5.5 0 0 1 17.5 9.5 3.75 3.75 0 0 1 17 18H7Z" />
            </svg>
          </Link>

          <Link
            to="/admin/system"
            className="rounded-2xl bg-slate-800/40 p-5 ring-1 ring-white/5 transition hover:ring-ogc-teal/40"
          >
            <div className="flex items-baseline justify-between">
              <span className="text-sm text-slate-400">Lokaler Puffer</span>
              {system?.disk && (
                <span className={`text-xs tabular-nums ${system.disk.low_space ? "text-amber-400" : "text-slate-500"}`}>
                  {system.disk.percent_used.toFixed(0)}% belegt
                </span>
              )}
            </div>
            {system?.disk ? (
              <>
                <div className="mt-1 text-2xl font-bold text-white">
                  {formatBytes(system.disk.free)} <span className="text-sm font-normal text-slate-500">frei</span>
                </div>
                <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-700">
                  <div
                    className={`h-full rounded-full ${system.disk.low_space ? "bg-amber-500" : "bg-gradient-to-r from-ogc-teal to-ogc-blue"}`}
                    style={{ width: `${Math.min(100, system.disk.percent_used)}%` }}
                  />
                </div>
                <div className="mt-1 text-xs text-slate-500">
                  von {formatBytes(system.disk.total)}
                  {system.disk.low_space && <span className="ml-2 text-amber-400">Speicher knapp</span>}
                </div>
              </>
            ) : (
              <div className="mt-1 text-sm text-slate-500">Speicherdaten werden geladen …</div>
            )}
          </Link>
        </div>
      )}
    </Layout>
  );
}
