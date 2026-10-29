import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError, type TransferAction, type TransferControl, type TransferJob } from "../api";
import { copyText } from "../clipboard";
import { type ContextMenuEntry, useContextMenu } from "../components/ContextMenu";
import Layout from "../components/Layout";
import { SortTh, type SortOption, useSort } from "../components/Sort";
import { useEvents } from "../events";
import { useToast } from "../toast";
import { formatBytes } from "../upload";

const TRANSFER_SORT: SortOption<TransferJob>[] = [
  { key: "file", label: "Datei", get: (j) => j.media_filename },
  { key: "provider", label: "Ziel", get: (j) => j.provider_name },
  { key: "status", label: "Status", get: (j) => j.status },
  { key: "attempts", label: "Versuche", get: (j) => j.attempts },
  { key: "created", label: "Erstellt", get: (j) => j.created_at },
];

const STATUS: Record<string, { label: string; cls: string }> = {
  queued: { label: "wartet", cls: "bg-slate-500/20 text-slate-300" },
  running: { label: "läuft", cls: "bg-ogc-teal/20 text-ogc-teal" },
  done: { label: "fertig", cls: "bg-emerald-500/20 text-emerald-300" },
  failed: { label: "fehlgeschlagen", cls: "bg-red-500/20 text-red-300" },
};

export default function Transfers() {
  const snapshot = useEvents();
  const [jobs, setJobs] = useState<TransferJob[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [control, setControl] = useState<TransferControl | null>(null);

  function load() {
    api<TransferJob[]>("/api/transfers")
      .then(setJobs)
      .catch((e) => setError(e instanceof ApiError ? e.message : "Fehler"));
    // Also catches a pause set remotely by a pool hub.
    api<TransferControl>("/api/transfers/control").then(setControl).catch(() => setControl(null));
  }

  async function runControl(action: TransferAction) {
    try {
      setControl(
        await api<TransferControl>("/api/transfers/control", {
          method: "POST",
          body: JSON.stringify({ action }),
        }),
      );
      load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Fehler");
    }
  }
  useEffect(() => {
    load();
  }, []);

  // Re-fetch the list whenever the live status counts change.
  const countsKey = JSON.stringify(snapshot?.transfers?.counts ?? {});
  useEffect(() => {
    load();
  }, [countsKey]);

  async function retry(id: number) {
    try {
      await api(`/api/transfers/${id}/retry`, { method: "POST" });
      load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Fehler");
    }
  }

  const sort = useSort(jobs, TRANSFER_SORT, { key: "created", dir: "desc" });

  const navigate = useNavigate();
  const toast = useToast();
  const ctx = useContextMenu();
  const copy = async (text: string, what: string) => {
    if (await copyText(text)) toast.success("Kopiert", `${what} liegt in der Zwischenablage.`);
    else toast.error("Kopieren fehlgeschlagen", "Der Browser hat den Zugriff verweigert.");
  };
  const rowMenu = (j: TransferJob): ContextMenuEntry[] => [
    (j.status === "failed" || j.status === "queued") && { label: "Erneut versuchen", onClick: () => retry(j.id) },
    j.folder_id !== null && { label: "Zum Ordner", onClick: () => navigate(`/folders/${j.folder_id}`) },
    "separator",
    { label: "Dateinamen kopieren", onClick: () => copy(j.media_filename, "Der Dateiname") },
    j.status === "failed" && !!j.last_error && {
      label: "Fehlermeldung kopieren",
      onClick: () => copy(`${j.media_filename} → ${j.provider_name}: ${j.last_error}`, "Die Fehlermeldung"),
    },
  ];

  const active = Object.fromEntries((snapshot?.transfers?.active ?? []).map((a) => [a.id, a]));
  const counts = snapshot?.transfers?.counts ??
    jobs.reduce<Record<string, number>>((acc, j) => {
      acc[j.status] = (acc[j.status] ?? 0) + 1;
      return acc;
    }, {});

  return (
    <Layout>
      <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-2xl font-bold">Transfers</h2>
          <p className="text-sm text-slate-400">
            Uploads in die Cloud · {jobs.length} Jobs
            {Object.entries(counts).map(([s, n]) => (
              <span key={s} className="ml-2">
                {STATUS[s]?.label ?? s}: {n}
              </span>
            ))}
          </p>
        </div>
        {control && (
          <div className="flex flex-wrap gap-2">
            {control.failed_transfers > 0 && (
              <button
                onClick={() => runControl("retry_failed")}
                className="whitespace-nowrap rounded-lg border border-white/10 px-3 py-1.5 text-xs hover:bg-white/5"
              >
                Alle fehlgeschlagenen erneut ({control.failed_transfers})
              </button>
            )}
            <button
              onClick={() => runControl(control.uploads_paused ? "resume" : "pause")}
              className="whitespace-nowrap rounded-lg border border-white/10 px-3 py-1.5 text-xs hover:bg-white/5"
            >
              {control.uploads_paused ? "Uploads fortsetzen" : "Uploads pausieren"}
            </button>
          </div>
        )}
      </div>

      {control?.uploads_paused && (
        <div className="mb-4 rounded-lg bg-amber-500/15 px-3 py-2 text-sm text-amber-200">
          Uploads sind pausiert — es starten keine neuen, laufende werden noch fertig. Pausiert
          von hier oder von einem Pool-Hub (siehe Audit-Log).
        </div>
      )}

      {error && <div className="mb-4 rounded-lg bg-red-500/15 px-3 py-2 text-sm text-red-300">{error}</div>}

      {jobs.length === 0 ? (
        <p className="text-sm text-slate-500">Noch keine Transfers. Lade Medien in einen verknüpften Ordner.</p>
      ) : (
        <div className="overflow-x-auto rounded-2xl ring-1 ring-white/10">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-800/80 text-slate-400">
              <tr>
                <SortTh sort={sort} field="file">Datei</SortTh>
                <SortTh sort={sort} field="provider" className="hidden sm:table-cell">Ziel</SortTh>
                <SortTh sort={sort} field="status">Status</SortTh>
                <SortTh sort={sort} field="attempts" className="hidden sm:table-cell">Versuche</SortTh>
                <th className="px-4 py-3" aria-label="Aktion" />
              </tr>
            </thead>
            <tbody>
              {sort.sorted.map((j) => {
                const s = STATUS[j.status] ?? STATUS.queued;
                return (
                  <tr key={j.id} onContextMenu={ctx.bind(() => rowMenu(j))} className="border-t border-white/5 bg-slate-900/40">
                    <td className="px-4 py-3">
                      <div className="font-medium text-white">{j.media_filename}</div>
                      {/* Target and attempts move under the file name on phones. */}
                      <div className="text-xs text-slate-400 sm:hidden">
                        → {j.provider_name}
                        {j.attempts > 1 && ` · ${j.attempts} Versuche`}
                      </div>
                    </td>
                    <td className="hidden px-4 py-3 text-slate-300 sm:table-cell">{j.provider_name}</td>
                    <td className="px-4 py-3">
                      <span className={`whitespace-nowrap rounded-sm px-2 py-0.5 text-xs ${s.cls}`}>{s.label}</span>
                      {j.status === "done" && j.bytes_transferred > 0 && (
                        <span className="ml-2 text-xs text-slate-500">{formatBytes(j.bytes_transferred)}</span>
                      )}
                      {j.status === "running" && active[j.id] && (
                        <div className="mt-1.5 w-full max-w-48">
                          <div className="h-1.5 overflow-hidden rounded-full bg-slate-700">
                            <div
                              className="h-full rounded-full bg-linear-to-r from-ogc-teal to-ogc-blue transition-all"
                              style={{ width: `${Math.round((active[j.id].progress || 0) * 100)}%` }}
                            />
                          </div>
                          <div className="mt-0.5 text-xs text-slate-500">
                            {active[j.id].total > 0 && `${formatBytes(active[j.id].bytes)} / ${formatBytes(active[j.id].total)}`}
                            {active[j.id].kbps > 0 && ` · ${active[j.id].kbps.toFixed(0)} KB/s`}
                          </div>
                        </div>
                      )}
                      {j.status === "failed" && j.last_error && (
                        <div className="mt-1 wrap-break-word text-xs text-red-300">{j.last_error}</div>
                      )}
                    </td>
                    <td className="hidden px-4 py-3 text-slate-400 sm:table-cell">{j.attempts}</td>
                    <td className="px-4 py-3 text-right">
                      {(j.status === "failed" || j.status === "queued") && (
                        <button onClick={() => retry(j.id)} className="whitespace-nowrap rounded-lg border border-white/10 px-3 py-1 text-xs hover:bg-white/5">
                          Erneut
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {ctx.menu}
    </Layout>
  );
}
