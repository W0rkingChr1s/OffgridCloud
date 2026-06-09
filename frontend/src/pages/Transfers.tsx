import { useEffect, useState } from "react";
import { api, ApiError, type TransferJob } from "../api";
import Layout from "../components/Layout";
import { formatBytes } from "../upload";

const STATUS: Record<string, { label: string; cls: string }> = {
  queued: { label: "wartet", cls: "bg-slate-500/20 text-slate-300" },
  running: { label: "läuft", cls: "bg-ogc-teal/20 text-ogc-teal" },
  done: { label: "fertig", cls: "bg-emerald-500/20 text-emerald-300" },
  failed: { label: "fehlgeschlagen", cls: "bg-red-500/20 text-red-300" },
};

export default function Transfers() {
  const [jobs, setJobs] = useState<TransferJob[]>([]);
  const [error, setError] = useState<string | null>(null);

  function load() {
    api<TransferJob[]>("/api/transfers")
      .then(setJobs)
      .catch((e) => setError(e instanceof ApiError ? e.message : "Fehler"));
  }
  useEffect(() => {
    load();
    const t = setInterval(load, 4000); // light polling until realtime (Phase 6)
    return () => clearInterval(t);
  }, []);

  async function retry(id: number) {
    try {
      await api(`/api/transfers/${id}/retry`, { method: "POST" });
      load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Fehler");
    }
  }

  const counts = jobs.reduce<Record<string, number>>((acc, j) => {
    acc[j.status] = (acc[j.status] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <Layout>
      <div className="mb-6">
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

      {error && <div className="mb-4 rounded-lg bg-red-500/15 px-3 py-2 text-sm text-red-300">{error}</div>}

      {jobs.length === 0 ? (
        <p className="text-sm text-slate-500">Noch keine Transfers. Lade Medien in einen verknüpften Ordner.</p>
      ) : (
        <div className="overflow-hidden rounded-2xl ring-1 ring-white/10">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-800/80 text-slate-400">
              <tr>
                <th className="px-4 py-3">Datei</th>
                <th className="px-4 py-3">Ziel</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3">Versuche</th>
                <th className="px-4 py-3 text-right">Aktion</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((j) => {
                const s = STATUS[j.status] ?? STATUS.queued;
                return (
                  <tr key={j.id} className="border-t border-white/5 bg-slate-900/40">
                    <td className="px-4 py-3 font-medium text-white">{j.media_filename}</td>
                    <td className="px-4 py-3 text-slate-300">{j.provider_name}</td>
                    <td className="px-4 py-3">
                      <span className={`rounded px-2 py-0.5 text-xs ${s.cls}`}>{s.label}</span>
                      {j.status === "done" && j.bytes_transferred > 0 && (
                        <span className="ml-2 text-xs text-slate-500">{formatBytes(j.bytes_transferred)}</span>
                      )}
                      {j.status === "failed" && j.last_error && (
                        <div className="mt-1 text-xs text-red-300">{j.last_error}</div>
                      )}
                    </td>
                    <td className="px-4 py-3 text-slate-400">{j.attempts}</td>
                    <td className="px-4 py-3 text-right">
                      {(j.status === "failed" || j.status === "queued") && (
                        <button onClick={() => retry(j.id)} className="rounded border border-white/10 px-3 py-1 text-xs hover:bg-white/5">
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
    </Layout>
  );
}
