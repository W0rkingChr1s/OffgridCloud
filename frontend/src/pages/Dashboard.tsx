import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Folder, type Health } from "../api";
import { useAuth } from "../auth";
import Layout from "../components/Layout";

export default function Dashboard() {
  const { user } = useAuth();
  const [folders, setFolders] = useState<Folder[]>([]);
  const [health, setHealth] = useState<Health | null>(null);

  useEffect(() => {
    api<Folder[]>("/api/folders").then(setFolders).catch(() => setFolders([]));
    api<Health>("/api/health").then(setHealth).catch(() => setHealth(null));
  }, []);

  return (
    <Layout>
      <div className="mb-6 flex items-end justify-between">
        <div>
          <h2 className="text-2xl font-bold">Ordner</h2>
          <p className="text-sm text-slate-400">
            Wähle einen Ordner, um Medien hochzuladen.
          </p>
        </div>
        {user?.role === "admin" && (
          <Link
            to="/admin/folders"
            className="rounded-lg border border-white/10 px-3 py-1.5 text-sm text-slate-300 hover:bg-white/5"
          >
            Ordner verwalten
          </Link>
        )}
      </div>

      {folders.length === 0 ? (
        <div className="rounded-2xl bg-slate-800/40 p-10 text-center text-slate-400 ring-1 ring-white/5">
          {user?.role === "admin"
            ? "Noch keine Ordner. Lege unter „Ordner verwalten“ den ersten an."
            : "Dir wurden noch keine Ordner freigegeben. Wende dich an einen Admin."}
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {folders.map((f, i) => {
            const accents = [
              "from-ogc-teal to-ogc-blue",
              "from-ogc-blue to-ogc-indigo",
              "from-ogc-indigo to-ogc-teal",
            ];
            return (
              <Link
                key={f.id}
                to={`/folders/${f.id}`}
                className="group rounded-2xl bg-slate-800/60 p-5 shadow-lg ring-1 ring-white/5 transition hover:ring-ogc-teal/40"
              >
                <div className={`mb-3 h-1.5 w-12 rounded-full bg-gradient-to-r ${accents[i % 3]}`} />
                <div className="text-lg font-semibold text-white group-hover:text-ogc-teal">
                  {f.name}
                </div>
                {f.description && (
                  <div className="mt-1 line-clamp-2 text-sm text-slate-400">{f.description}</div>
                )}
                <div className="mt-3 text-xs text-slate-500">
                  {f.media_count} {f.media_count === 1 ? "Datei" : "Dateien"}
                </div>
              </Link>
            );
          })}
        </div>
      )}

      {health && (
        <div className="mt-8 text-center text-xs text-slate-500">
          Backend {health.status} · v{health.version} · rclone{" "}
          {health.rclone.available ? "bereit" : "fehlt"}
        </div>
      )}
    </Layout>
  );
}
