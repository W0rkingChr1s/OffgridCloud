import { useEffect, useState } from "react";
import { api, ApiError, type AuditEvent, type SystemStatus, type UpdateInfo } from "../api";
import InfoTip from "../components/InfoTip";
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
  const toggleRemoteDelete = (value: boolean) => save({ delete_remote_on_local_delete: value });
  const toggleAutoResync = (value: boolean) => save({ auto_resync: value });

  const disk = status?.disk;
  const pct = disk ? Math.round(disk.percent_used) : 0;
  const resyncMinutes = status ? Math.round(status.reconcile_interval / 60) : 0;

  return (
    <Layout>
      <h2 className="mb-1 text-2xl font-bold">System</h2>
      <p className="mb-6 text-sm text-slate-400">Speicher, Einstellungen und Aktivitätsprotokoll.</p>

      {error && <div className="mb-4 rounded-lg bg-red-500/15 px-3 py-2 text-sm text-red-300">{error}</div>}

      <UpdateCard />

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
              <span className="inline-flex items-center gap-1.5">
                Lokale Kopie nach erfolgreichem Upload löschen
                <InfoTip text="Sobald ein Medium an ALLE verknüpften Ziele bestätigt hochgeladen wurde, wird die lokale Pufferdatei entfernt, um Speicher (z. B. auf der SD-Karte) zu sparen. Erst nach erfolgreicher Prüfung – nie vorher. Danach ist kein lokaler Download mehr möglich." />
              </span>
              <span className="mt-0.5 block text-xs text-slate-500">
                Entfernt die Pufferdatei, sobald alle Zielprovider bestätigt sind.
              </span>
            </span>
          </label>

          <label className="mt-4 flex items-start gap-3 text-sm">
            <input
              type="checkbox"
              className="mt-1"
              checked={status?.delete_remote_on_local_delete ?? false}
              onChange={(e) => toggleRemoteDelete(e.target.checked)}
            />
            <span className="text-slate-300">
              <span className="inline-flex items-center gap-1.5">
                Beim lokalen Löschen auch remote löschen
                <InfoTip text="Wenn du eine Datei aus einem Ordner löschst, wird sie zusätzlich bei allen Zielen entfernt, zu denen sie bereits hochgeladen wurde (per rclone). Achtung: unwiderruflich – die Cloud-Kopie ist danach weg. Ohne diesen Haken bleibt die Remote-Kopie als Backup erhalten." />
              </span>
              <span className="mt-0.5 block text-xs text-slate-500">
                Standard: aus. Löscht die Cloud-Kopien mit – unwiderruflich.
              </span>
            </span>
          </label>

          <label className="mt-4 flex items-start gap-3 text-sm">
            <input
              type="checkbox"
              className="mt-1"
              checked={status?.auto_resync ?? false}
              onChange={(e) => toggleAutoResync(e.target.checked)}
            />
            <span className="text-slate-300">
              <span className="inline-flex items-center gap-1.5">
                Automatischer Re-Sync
                <InfoTip text={`Prüft im Hintergrund regelmäßig${resyncMinutes ? ` (alle ~${resyncMinutes} Min.)` : ""} alle Ziele: fehlgeschlagene Transfers werden erneut eingereiht und fehlende Jobs nachgezogen. So heilt eine kurze Störung (offline, Ziel nicht erreichbar) von selbst, sobald die Verbindung zurück ist – ganz ohne manuelles „Erneut".`} />
              </span>
              <span className="mt-0.5 block text-xs text-slate-500">
                Wiederholt hängengebliebene Uploads automatisch, sobald wieder Verbindung besteht.
              </span>
            </span>
          </label>
        </div>
      </div>

      <div className="mb-6 rounded-2xl bg-slate-800/60 p-5 ring-1 ring-white/10">
        <div className="mb-3 text-sm font-medium text-slate-400">Integrationen</div>
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="text-sm">
            <span className="mb-1 flex items-center gap-1.5 text-slate-400">
              Bandbreiten-Probe-URL (optional)
              <InfoTip text="Ziel-Datei, die für die aktive Durchsatzmessung heruntergeladen wird. Leer lassen nutzt eine öffentliche Cloudflare-Testdatei – für „Jetzt messen“ musst du hier nichts eintragen. Nur setzen, wenn du bewusst ein eigenes Testziel (z. B. im lokalen Netz) verwenden willst." />
            </span>
            <input
              className="w-full rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 outline-none focus:border-ogc-teal"
              placeholder="Standard: öffentliche Test-Datei (Cloudflare)"
              value={probeUrl}
              onChange={(e) => setProbeUrl(e.target.value)}
              onBlur={() => probeUrl !== status?.probe_url && save({ probe_url: probeUrl })}
            />
            <span className="mt-1 block text-xs text-slate-500">
              Leer lassen — „Jetzt messen“ (unter Bandbreite) funktioniert ohne Eingabe. Nur setzen, um ein eigenes Testziel zu nutzen.
            </span>
          </label>
          <label className="text-sm">
            <span className="mb-1 flex items-center gap-1.5 text-slate-400">
              Webhook-URL (bei „fertig“)
              <InfoTip text="Sobald ein Medium an alle Ziele hochgeladen ist, wird an diese URL einmalig ein JSON-POST gesendet (Dateiname, Größe, SHA-256, Ordner). Ideal, um andere Systeme zu benachrichtigen oder Automatisierungen anzustoßen. Leer lassen = deaktiviert." />
            </span>
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

function UpdateCard() {
  const [info, setInfo] = useState<UpdateInfo | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  function check(force = false) {
    setBusy(true);
    setMsg(null);
    api<UpdateInfo>(`/api/updates${force ? "?force=true" : ""}`)
      .then(setInfo)
      .catch((e) => setMsg(e instanceof ApiError ? e.message : "Fehler"))
      .finally(() => setBusy(false));
  }
  useEffect(() => check(), []);

  async function apply() {
    setBusy(true);
    setMsg(null);
    try {
      const r = await api<{ started: boolean; message: string }>("/api/updates/apply", {
        method: "POST",
      });
      setMsg(r.message);
    } catch (e) {
      setMsg(e instanceof ApiError ? e.message : "Update fehlgeschlagen");
    } finally {
      setBusy(false);
    }
  }

  const available = info?.update_available;
  return (
    <div
      className={`mb-6 rounded-2xl p-5 ring-1 ${
        available ? "bg-ogc-teal/10 ring-ogc-teal/30" : "bg-slate-800/60 ring-white/10"
      }`}
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="text-sm font-medium text-slate-400">Version</span>
        <span className="text-lg font-bold text-white">{info?.current ?? "…"}</span>
        {available && info?.latest && (
          <span className="rounded-full bg-ogc-teal/20 px-2 py-0.5 text-xs font-semibold text-ogc-teal">
            Update verfügbar: {info.latest}
          </span>
        )}
        {info && !available && !info.error && (
          <span className="text-xs text-emerald-300">aktuell</span>
        )}
        <button
          type="button"
          onClick={() => check(true)}
          disabled={busy}
          className="ml-auto rounded-lg border border-white/10 px-3 py-1.5 text-xs hover:bg-white/5 disabled:opacity-50"
        >
          {busy ? "…" : "Nach Updates suchen"}
        </button>
      </div>

      {info?.error && <div className="mt-2 text-xs text-slate-500">{info.error}</div>}

      {available && (
        <div className="mt-3 space-y-3">
          {info?.release_url && (
            <a
              href={info.release_url}
              target="_blank"
              rel="noreferrer"
              className="text-sm text-ogc-teal hover:underline"
            >
              Release-Notes ansehen ↗
            </a>
          )}
          {info?.self_update_enabled ? (
            <div>
              <button
                type="button"
                onClick={apply}
                disabled={busy}
                className="rounded-lg bg-gradient-to-r from-ogc-teal to-ogc-blue px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
              >
                Jetzt aktualisieren
              </button>
            </div>
          ) : (
            <div className="rounded-lg bg-slate-900/60 px-3 py-2 text-xs text-slate-400">
              Update auf dem Server ausführen:
              <code className="ml-1 rounded bg-black/30 px-1.5 py-0.5 text-slate-200">
                sudo /opt/offgridcloud/src/deploy/update.sh
              </code>
            </div>
          )}
        </div>
      )}

      {msg && <div className="mt-3 text-sm text-slate-300">{msg}</div>}
    </div>
  );
}
