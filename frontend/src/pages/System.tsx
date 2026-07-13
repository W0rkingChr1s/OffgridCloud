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

      {status && <NotificationsCard status={status} save={save} />}

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

const EVENT_TOGGLES: { key: keyof SystemStatus; label: string; help: string }[] = [
  {
    key: "notify_on_received",
    label: "Upload angenommen",
    help: "Meldet, sobald das Feld-Team eine Datei lokal fertig hochgeladen hat und der Cloud-Transfer eingereiht wird. Kann bei vielen Dateien laut werden – Standard: aus.",
  },
  {
    key: "notify_on_done",
    label: "Transfer fertig",
    help: "Meldet, sobald ein Medium erfolgreich in ALLE verknüpften Cloud-Ziele übertragen wurde. Steuert auch den bestehenden Webhook.",
  },
  {
    key: "notify_on_failed",
    label: "Transfer fehlgeschlagen",
    help: "Meldet, wenn ein Transfer nach allen Wiederholungen endgültig scheitert – wichtig fürs Feld, damit ein Problem nicht unbemerkt bleibt.",
  },
  {
    key: "notify_on_low_space",
    label: "Speicher wird knapp",
    help: "Meldet einmalig, wenn der Puffer-Speicher zur Neige geht (weitere Uploads würden sonst stoppen). Wird erneut scharfgeschaltet, sobald wieder Platz frei ist.",
  },
];

function NotificationsCard({
  status,
  save,
}: {
  status: SystemStatus;
  save: (patch: Record<string, unknown>) => Promise<void>;
}) {
  const [chatId, setChatId] = useState(status.telegram_chat_id);
  const [token, setToken] = useState("");
  const [smtp, setSmtp] = useState({
    smtp_host: status.smtp_host,
    smtp_port: status.smtp_port,
    smtp_username: status.smtp_username,
    smtp_from: status.smtp_from,
    smtp_to: status.smtp_to,
    smtp_tls: status.smtp_tls,
  });
  const [smtpPassword, setSmtpPassword] = useState("");

  useEffect(() => {
    setChatId(status.telegram_chat_id);
    setSmtp({
      smtp_host: status.smtp_host,
      smtp_port: status.smtp_port,
      smtp_username: status.smtp_username,
      smtp_from: status.smtp_from,
      smtp_to: status.smtp_to,
      smtp_tls: status.smtp_tls,
    });
  }, [status]);

  async function saveTelegram() {
    const patch: Record<string, unknown> = { telegram_chat_id: chatId };
    if (token) patch.telegram_bot_token = token; // only overwrite when newly entered
    await save(patch);
    setToken("");
  }
  async function saveEmail() {
    const patch: Record<string, unknown> = { ...smtp };
    if (smtpPassword) patch.smtp_password = smtpPassword;
    await save(patch);
    setSmtpPassword("");
  }

  const inputCls =
    "w-full rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 outline-none focus:border-ogc-teal";
  const btnCls =
    "rounded-lg bg-gradient-to-r from-ogc-teal to-ogc-blue px-4 py-2 text-sm font-semibold text-white disabled:opacity-50";

  return (
    <div className="mb-6 rounded-2xl bg-slate-800/60 p-5 ring-1 ring-white/10">
      <div className="mb-1 flex items-center gap-1.5 text-sm font-medium text-slate-400">
        Benachrichtigungen (Info-Service)
        <InfoTip text="Statusmeldungen an zusätzliche Kanäle: Telegram und E-Mail (der Webhook oben läuft parallel weiter). Wähle unten, welche Ereignisse eine Nachricht auslösen. Zugangsdaten werden verschlüsselt gespeichert und nie wieder angezeigt." />
      </div>
      <p className="mb-4 text-xs text-slate-500">
        Sendet Statusmeldungen an Telegram und/oder E-Mail. Ohne konfigurierten Kanal passiert nichts.
      </p>

      {/* Which events notify */}
      <div className="mb-5 grid gap-2 sm:grid-cols-2">
        {EVENT_TOGGLES.map((t) => (
          <label key={t.key} className="flex items-start gap-3 text-sm">
            <input
              type="checkbox"
              className="mt-1"
              checked={Boolean(status[t.key])}
              onChange={(e) => save({ [t.key]: e.target.checked })}
            />
            <span className="text-slate-300">
              <span className="inline-flex items-center gap-1.5">
                {t.label}
                <InfoTip text={t.help} />
              </span>
            </span>
          </label>
        ))}
      </div>

      <div className="grid gap-6 sm:grid-cols-2">
        {/* Telegram */}
        <div>
          <div className="mb-2 flex items-center gap-1.5 text-sm font-medium text-slate-300">
            Telegram
            {status.telegram_configured && (
              <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-xs text-emerald-300">
                konfiguriert
              </span>
            )}
            <InfoTip text="Erstelle bei @BotFather einen Bot, trage hier den Bot-Token ein und die Chat-ID des Empfängers (z. B. via @userinfobot). Die Box sendet Statusmeldungen dann per Bot-API – kein eigener Server nötig." />
          </div>
          <label className="mb-2 block text-sm">
            <span className="mb-1 block text-slate-400">Bot-Token</span>
            <input
              className={inputCls}
              type="password"
              placeholder={status.telegram_configured ? "•••••• (gespeichert – zum Ändern neu eingeben)" : "123456:ABC-DEF…"}
              value={token}
              onChange={(e) => setToken(e.target.value)}
            />
          </label>
          <label className="mb-3 block text-sm">
            <span className="mb-1 block text-slate-400">Chat-ID</span>
            <input
              className={inputCls}
              placeholder="z. B. 123456789 oder -100…"
              value={chatId}
              onChange={(e) => setChatId(e.target.value)}
            />
          </label>
          <button type="button" onClick={saveTelegram} className={btnCls}>
            Telegram speichern
          </button>
        </div>

        {/* E-Mail (SMTP) */}
        <div>
          <div className="mb-2 flex items-center gap-1.5 text-sm font-medium text-slate-300">
            E-Mail (SMTP)
            {status.smtp_configured && (
              <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-xs text-emerald-300">
                Passwort gespeichert
              </span>
            )}
            <InfoTip text="Trage die Zugangsdaten eines erreichbaren SMTP-Servers ein. STARTTLS empfohlen (Port 587). Die Box verschickt Statusmeldungen dann als E-Mail an den Empfänger." />
          </div>
          <div className="grid grid-cols-3 gap-2">
            <label className="col-span-2 block text-sm">
              <span className="mb-1 block text-slate-400">Server</span>
              <input
                className={inputCls}
                placeholder="smtp.example.com"
                value={smtp.smtp_host}
                onChange={(e) => setSmtp((s) => ({ ...s, smtp_host: e.target.value }))}
              />
            </label>
            <label className="block text-sm">
              <span className="mb-1 block text-slate-400">Port</span>
              <input
                className={inputCls}
                type="number"
                value={smtp.smtp_port}
                onChange={(e) => setSmtp((s) => ({ ...s, smtp_port: Number(e.target.value) }))}
              />
            </label>
          </div>
          <div className="mt-2 grid grid-cols-2 gap-2">
            <label className="block text-sm">
              <span className="mb-1 block text-slate-400">Benutzer</span>
              <input
                className={inputCls}
                placeholder="login"
                value={smtp.smtp_username}
                onChange={(e) => setSmtp((s) => ({ ...s, smtp_username: e.target.value }))}
              />
            </label>
            <label className="block text-sm">
              <span className="mb-1 block text-slate-400">Passwort</span>
              <input
                className={inputCls}
                type="password"
                placeholder={status.smtp_configured ? "•••••• (gespeichert)" : ""}
                value={smtpPassword}
                onChange={(e) => setSmtpPassword(e.target.value)}
              />
            </label>
          </div>
          <div className="mt-2 grid grid-cols-2 gap-2">
            <label className="block text-sm">
              <span className="mb-1 block text-slate-400">Absender</span>
              <input
                className={inputCls}
                placeholder="box@field.local"
                value={smtp.smtp_from}
                onChange={(e) => setSmtp((s) => ({ ...s, smtp_from: e.target.value }))}
              />
            </label>
            <label className="block text-sm">
              <span className="mb-1 block text-slate-400">Empfänger</span>
              <input
                className={inputCls}
                placeholder="ops@office.local"
                value={smtp.smtp_to}
                onChange={(e) => setSmtp((s) => ({ ...s, smtp_to: e.target.value }))}
              />
            </label>
          </div>
          <label className="mt-3 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={smtp.smtp_tls}
              onChange={(e) => setSmtp((s) => ({ ...s, smtp_tls: e.target.checked }))}
            />
            STARTTLS verwenden (empfohlen)
          </label>
          <div className="mt-3">
            <button type="button" onClick={saveEmail} className={btnCls}>
              E-Mail speichern
            </button>
          </div>
        </div>
      </div>
    </div>
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
