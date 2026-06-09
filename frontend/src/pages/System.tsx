import { useEffect, useState } from "react";
import { api, ApiError, type AuditEvent, type SystemStatus } from "../api";
import Layout from "../components/Layout";
import { formatBytes } from "../upload";

export default function System() {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [audit, setAudit] = useState<AuditEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [probeUrl, setProbeUrl] = useState("");
  const [webhookUrl, setWebhookUrl] = useState("");

  function load() {
    api<SystemStatus>("/api/system")
      .then((s) => {
        setStatus(s);
        setProbeUrl(s.probe_url);
        setWebhookUrl(s.webhook_url);
      })
      .catch(report);
    api<AuditEvent[]>("/api/system/audit").then(setAudit).catch(report);
  }
  function report(e: unknown) {
    setError(e instanceof ApiError ? e.message : "Fehler");
  }
  useEffect(load, []);

  async function save(patch: Record<string, unknown>) {
    try {
      const s = await api<SystemStatus>("/api/system", {
        method: "PUT",
        body: JSON.stringify(patch),
      });
      setStatus(s);
      load();
    } catch (e) {
      report(e);
    }
  }
  const toggleDelete = (value: boolean) => save({ delete_local_after_upload: value });

  const disk = status?.disk;
  const pct = disk ? Math.round(disk.percent_used) : 0;

  return (
    <Layout>
      <h2 className="mb-1 text-2xl font-bold">System</h2>
      <p className="mb-6 text-sm text-slate-400">Speicher, Einstellungen und Aktivitätsprotokoll.</p>

      {error && <div className="mb-4 rounded-lg bg-red-500/15 px-3 py-2 text-sm text-red-300">{error}</div>}

      <div className="mb-6 grid gap-4 sm:grid-cols-2">
        <div className="rounded-2xl bg-slate-800/60 p-5 ring-1 ring-white/10">
          <div className="mb-2 text-sm font-medium text-slate-400">Puffer-Speicher</div>
          {disk ? (
            <>
              <div className="text-2xl font-bold text-white">
                {formatBytes(disk.free)} <span className="text-sm font-normal text-slate-400">frei</span>
              </div>
              <div className="mt-3 h-2 overflow-hidden rounded-full bg-slate-700">
                <div
                  className={`h-full rounded-full ${disk.low_space ? "bg-red-500" : "bg-gradient-to-r from-ogc-teal to-ogc-blue"}`}
                  style={{ width: `${pct}%` }}
                />
              </div>
              <div className="mt-2 text-xs text-slate-500">
                {formatBytes(disk.used)} / {formatBytes(disk.total)} belegt ({pct}%)
              </div>
              {disk.low_space && <div className="mt-2 text-xs text-red-300">⚠ Wenig freier Speicher</div>}
            </>
          ) : (
            <div className="text-slate-500">…</div>
          )}
        </div>

        <div className="rounded-2xl bg-slate-800/60 p-5 ring-1 ring-white/10">
          <div className="mb-2 text-sm font-medium text-slate-400">Transfer-Engine</div>
          <div className="text-2xl font-bold text-white">
            {status ? (status.rclone_available ? "rclone bereit" : "rclone fehlt") : "…"}
          </div>
          <label className="mt-4 flex items-start gap-3 text-sm">
            <input
              type="checkbox"
              className="mt-1"
              checked={status?.delete_local_after_upload ?? false}
              onChange={(e) => toggleDelete(e.target.checked)}
            />
            <span className="text-slate-300">
              Lokale Kopie nach erfolgreichem Upload löschen
              <span className="mt-0.5 block text-xs text-slate-500">
                Entfernt die Pufferdatei, sobald alle Zielprovider bestätigt sind.
              </span>
            </span>
          </label>
        </div>
      </div>

      <div className="mb-6 rounded-2xl bg-slate-800/60 p-5 ring-1 ring-white/10">
        <div className="mb-3 text-sm font-medium text-slate-400">Integrationen</div>
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="text-sm">
            <span className="mb-1 block text-slate-400">Bandbreiten-Probe-URL</span>
            <input
              className="w-full rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 outline-none focus:border-ogc-teal"
              placeholder="https://… (Datei für aktive Messung)"
              value={probeUrl}
              onChange={(e) => setProbeUrl(e.target.value)}
              onBlur={() => probeUrl !== status?.probe_url && save({ probe_url: probeUrl })}
            />
            <span className="mt-1 block text-xs text-slate-500">Download misst den Durchsatz (Knopf „Jetzt messen“ unter Bandbreite).</span>
          </label>
          <label className="text-sm">
            <span className="mb-1 block text-slate-400">Webhook-URL (bei „fertig“)</span>
            <input
              className="w-full rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 outline-none focus:border-ogc-teal"
              placeholder="https://… (POST bei fertigem Upload)"
              value={webhookUrl}
              onChange={(e) => setWebhookUrl(e.target.value)}
              onBlur={() => webhookUrl !== status?.webhook_url && save({ webhook_url: webhookUrl })}
            />
            <span className="mt-1 block text-xs text-slate-500">Erhält ein JSON, sobald ein Medium überallhin hochgeladen wurde.</span>
          </label>
        </div>
      </div>

      <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-400">Aktivität</h3>
      {audit.length === 0 ? (
        <p className="text-sm text-slate-500">Noch keine Einträge.</p>
      ) : (
        <div className="overflow-hidden rounded-2xl ring-1 ring-white/10">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-800/80 text-slate-400">
              <tr>
                <th className="px-4 py-3">Zeit</th>
                <th className="px-4 py-3">Benutzer</th>
                <th className="px-4 py-3">Aktion</th>
                <th className="px-4 py-3">Detail</th>
              </tr>
            </thead>
            <tbody>
              {audit.map((e) => (
                <tr key={e.id} className="border-t border-white/5 bg-slate-900/40">
                  <td className="px-4 py-3 text-slate-400">{new Date(e.created_at + "Z").toLocaleString()}</td>
                  <td className="px-4 py-3 text-slate-300">{e.user_email || "—"}</td>
                  <td className="px-4 py-3">
                    <span className="rounded bg-slate-700/60 px-2 py-0.5 text-xs text-slate-200">{e.action}</span>
                  </td>
                  <td className="px-4 py-3 text-slate-400">{e.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Layout>
  );
}
