import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Folder } from "../api";
import { useAuth } from "../auth";
import Layout from "../components/Layout";
import { useEvents, type FolderSnapshot } from "../events";

const ACCENTS = [
  "from-ogc-teal to-ogc-blue",
  "from-ogc-blue to-ogc-indigo",
  "from-ogc-indigo to-ogc-teal",
];

export default function Dashboard() {
  const { user } = useAuth();
  const snapshot = useEvents();
  const [meta, setMeta] = useState<Folder[]>([]);

  useEffect(() => {
    api<Folder[]>("/api/folders").then(setMeta).catch(() => setMeta([]));
  }, []);

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

  return (
    <Layout>
      <div className="mb-6 flex items-end justify-between">
        <div>
          <h2 className="text-2xl font-bold">Ordner</h2>
          <p className="text-sm text-slate-400">Wähle einen Ordner, um Medien hochzuladen.</p>
        </div>
        {user?.role === "admin" && (
          <Link to="/admin/folders" className="rounded-lg border border-white/10 px-3 py-1.5 text-sm text-slate-300 hover:bg-white/5">
            Ordner verwalten
          </Link>
        )}
      </div>

      {user?.role === "admin" && snapshot?.bandwidth && (
        <div className="mb-6 flex flex-wrap items-center gap-3 rounded-2xl bg-slate-800/40 px-5 py-3 text-sm ring-1 ring-white/5">
          <span className={`h-2 w-2 rounded-full ${snapshot.bandwidth.gated ? "bg-red-400" : "bg-emerald-400"}`} />
          <span className="text-slate-300">
            {snapshot.bandwidth.gated ? "Uploads pausiert" : "Uploads bereit"}
          </span>
          <span className="text-slate-500">
            Limit:{" "}
            {snapshot.bandwidth.effective_bwlimit_kbps === 0
              ? "unbegrenzt"
              : `${snapshot.bandwidth.effective_bwlimit_kbps} KB/s`}
          </span>
          {snapshot.bandwidth.last_kbps > 0 && (
            <span className="text-slate-500">Gemessen: {snapshot.bandwidth.last_kbps.toFixed(0)} KB/s</span>
          )}
          {snapshot.transfers && (
            <span className="ml-auto text-slate-500">
              {snapshot.transfers.counts.running ?? 0} aktiv · {snapshot.transfers.counts.queued ?? 0} wartend
            </span>
          )}
        </div>
      )}

      {folders.length === 0 ? (
        <div className="rounded-2xl bg-slate-800/40 p-10 text-center text-slate-400 ring-1 ring-white/5">
          {user?.role === "admin"
            ? "Noch keine Ordner. Lege unter „Ordner verwalten“ den ersten an."
            : "Dir wurden noch keine Ordner freigegeben. Wende dich an einen Admin."}
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {folders.map((f, i) => {
            const pct = f.total > 0 ? Math.round((f.done / f.total) * 100) : 0;
            return (
              <Link
                key={f.id}
                to={`/folders/${f.id}`}
                className="group rounded-2xl bg-slate-800/60 p-5 shadow-lg ring-1 ring-white/5 transition hover:ring-ogc-teal/40"
              >
                <div className={`mb-3 h-1.5 w-12 rounded-full bg-gradient-to-r ${ACCENTS[i % 3]}`} />
                <div className="text-lg font-semibold text-white group-hover:text-ogc-teal">{f.name}</div>
                {descById[f.id] && <div className="mt-1 line-clamp-2 text-sm text-slate-400">{descById[f.id]}</div>}

                {f.total > 0 && (
                  <>
                    <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-slate-700">
                      <div className="h-full rounded-full bg-gradient-to-r from-ogc-teal to-ogc-blue transition-all" style={{ width: `${pct}%` }} />
                    </div>
                    <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-slate-500">
                      <span>{f.total} Dateien</span>
                      <span className="text-emerald-400">{f.done} fertig</span>
                      {f.uploading > 0 && <span className="text-ogc-teal">{f.uploading} lädt</span>}
                      {f.queued > 0 && <span>{f.queued} wartend</span>}
                      {f.failed > 0 && <span className="text-red-400">{f.failed} Fehler</span>}
                    </div>
                  </>
                )}
                {f.total === 0 && <div className="mt-3 text-xs text-slate-500">noch keine Dateien</div>}
              </Link>
            );
          })}
        </div>
      )}
    </Layout>
  );
}
