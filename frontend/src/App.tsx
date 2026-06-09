import { useEffect, useState } from "react";

interface Health {
  status: string;
  app: string;
  version: string;
  environment: string;
  rclone: { available: boolean; version: string | null; error: string | null };
}

/** Placeholder dashboard tile. Real folder/transfer tiles arrive in Phase 6. */
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

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/health")
      .then((r) => r.json())
      .then(setHealth)
      .catch((e) => setError(String(e)));
  }, []);

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-900 to-ogc-indigo/40 text-slate-100">
      <header className="mx-auto flex max-w-5xl items-center gap-3 px-6 py-8">
        <img src="/logo-icon.svg" alt="OffgridCloud" className="h-12 w-12" />
        <div>
          <h1 className="text-2xl font-bold">OffgridCloud</h1>
          <p className="text-sm text-slate-400">Upload when the signal is right.</p>
        </div>
      </header>

      <main className="mx-auto max-w-5xl px-6 pb-16">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <Tile
            title="Backend"
            value={health ? health.status.toUpperCase() : error ? "OFFLINE" : "…"}
            hint={health ? `v${health.version} · ${health.environment}` : undefined}
            accent="from-ogc-teal to-ogc-blue"
          />
          <Tile
            title="Transfer-Engine (rclone)"
            value={
              health
                ? health.rclone.available
                  ? "BEREIT"
                  : "FEHLT"
                : "…"
            }
            hint={health?.rclone.version ?? health?.rclone.error ?? undefined}
            accent="from-ogc-blue to-ogc-indigo"
          />
          <Tile
            title="Nächster Schritt"
            value="Phase 1"
            hint="Auth & User-Management"
            accent="from-ogc-indigo to-ogc-teal"
          />
        </div>

        <p className="mt-8 text-center text-xs text-slate-500">
          Grundgerüst (Phase 0). Ordner-, Transfer- und Provider-Kacheln folgen.
        </p>
      </main>
    </div>
  );
}
