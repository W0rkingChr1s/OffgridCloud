import { useEffect, useState } from "react";
import { api, type Health } from "../api";
import { useAuth } from "../auth";
import Layout from "../components/Layout";

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

export default function Dashboard() {
  const { user } = useAuth();
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    api<Health>("/api/health").then(setHealth).catch(() => setError(true));
  }, []);

  return (
    <Layout>
      <div className="mb-6">
        <h2 className="text-2xl font-bold">Willkommen, {user?.name || user?.email}</h2>
        <p className="text-sm text-slate-400">
          Übersicht des OffgridCloud-Servers. Ordner- und Transfer-Kacheln folgen in den
          nächsten Phasen.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <Tile
          title="Backend"
          value={health ? health.status.toUpperCase() : error ? "OFFLINE" : "…"}
          hint={health ? `v${health.version} · ${health.environment}` : undefined}
          accent="from-ogc-teal to-ogc-blue"
        />
        <Tile
          title="Transfer-Engine (rclone)"
          value={health ? (health.rclone.available ? "BEREIT" : "FEHLT") : "…"}
          hint={health?.rclone.version ?? health?.rclone.error ?? undefined}
          accent="from-ogc-blue to-ogc-indigo"
        />
        <Tile
          title="Nächster Schritt"
          value="Phase 2"
          hint="Ordner & lokale Datei-Annahme"
          accent="from-ogc-indigo to-ogc-teal"
        />
      </div>
    </Layout>
  );
}
