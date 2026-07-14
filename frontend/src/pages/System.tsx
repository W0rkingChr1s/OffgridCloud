import { useEffect, useRef, useState, type RefObject } from "react";
import {
  api,
  ApiError,
  type AuditEvent,
  type SystemStatus,
  type UpdateInfo,
  type UpdateProgress,
} from "../api";
import InfoTip from "../components/InfoTip";
import Layout from "../components/Layout";
import { SortTh, type SortOption, useSort } from "../components/Sort";
import { useToast } from "../toast";
import { formatBytes } from "../upload";

const AUDIT_SORT: SortOption<AuditEvent>[] = [
  { key: "time", label: "Zeit", get: (e) => e.created_at },
  { key: "user", label: "Benutzer", get: (e) => e.user_email },
  { key: "action", label: "Aktion", get: (e) => e.action },
  { key: "detail", label: "Detail", get: (e) => e.detail },
];

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
  const auditSort = useSort(audit, AUDIT_SORT, { key: "time", dir: "desc" });

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

      {status && <PowerCard status={status} />}

      <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-400">Aktivität</h3>
      {audit.length === 0 ? (
        <p className="text-sm text-slate-500">Noch keine Einträge.</p>
      ) : (
        <div className="overflow-hidden rounded-2xl ring-1 ring-white/10">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-800/80 text-slate-400">
              <tr>
                <SortTh sort={auditSort} field="time">Zeit</SortTh>
                <SortTh sort={auditSort} field="user">Benutzer</SortTh>
                <SortTh sort={auditSort} field="action">Aktion</SortTh>
                <SortTh sort={auditSort} field="detail">Detail</SortTh>
              </tr>
            </thead>
            <tbody>
              {auditSort.sorted.map((e) => (
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

// The three system-control actions. `action` is the POST /api/system/power slug;
// `enabled` picks the flag that says whether the command is wired up on the box.
const POWER_ACTIONS: {
  action: string;
  label: string;
  confirm: string;
  help: string;
  danger: boolean;
  enabled: keyof SystemStatus;
}[] = [
  {
    action: "restart-service",
    label: "OffgridCloud neustarten",
    confirm: "OffgridCloud-Dienst wirklich neu starten? Das Portal ist kurz nicht erreichbar.",
    help: "Startet nur den OffgridCloud-Dienst neu (systemctl restart) – das Betriebssystem läuft weiter. Laufende Uploads werden nach dem Neustart automatisch wieder aufgenommen. Das Portal ist dabei einige Sekunden nicht erreichbar.",
    danger: false,
    enabled: "power_restart_service_enabled",
  },
  {
    action: "reboot",
    label: "System neustarten",
    confirm: "Das ganze System wirklich neu starten? Die Box ist einige Zeit offline.",
    help: "Startet die gesamte Box neu (reboot). Sinnvoll nach Systemaktualisierungen oder bei hängender Hardware. Die Box ist bis zum Hochfahren komplett offline.",
    danger: true,
    enabled: "power_reboot_enabled",
  },
  {
    action: "shutdown",
    label: "System herunterfahren",
    confirm: "Das ganze System wirklich herunterfahren? Es muss danach von Hand wieder eingeschaltet werden.",
    help: "Fährt die Box komplett herunter (poweroff). Danach ist sie aus und muss vor Ort wieder eingeschaltet werden – aus der Ferne lässt sie sich nicht mehr starten.",
    danger: true,
    enabled: "power_shutdown_enabled",
  },
];

function PowerCard({ status }: { status: SystemStatus }) {
  const toast = useToast();
  const [busy, setBusy] = useState<string | null>(null);
  const anyEnabled = POWER_ACTIONS.some((a) => status[a.enabled]);

  async function run(a: (typeof POWER_ACTIONS)[number]) {
    if (!window.confirm(a.confirm)) return;
    setBusy(a.action);
    try {
      const res = await api<{ started: boolean; message: string }>(
        `/api/system/power/${a.action}`,
        { method: "POST" },
      );
      toast.info(a.label, res.message);
    } catch (e) {
      toast.error(a.label, e instanceof ApiError ? e.message : "Aktion fehlgeschlagen");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="mb-6 rounded-2xl bg-slate-800/60 p-5 ring-1 ring-white/10">
      <div className="mb-1 flex items-center gap-1.5 text-sm font-medium text-slate-400">
        System steuern
        <InfoTip text="Steuert Dienst und Box direkt aus dem Portal: den OffgridCloud-Dienst neu starten, die ganze Box neu starten oder herunterfahren. Erfordert erhöhte Rechte und muss am Server bewusst freigeschaltet werden (Installer mit --power-control)." />
      </div>
      <p className="mb-4 text-xs text-slate-500">
        Neustart und Herunterfahren wirken sofort – laufende Übertragungen werden unterbrochen und
        nach einem Neustart automatisch fortgesetzt.
      </p>

      {!anyEnabled && (
        <div className="mb-4 rounded-lg bg-slate-900/60 px-3 py-2 text-xs text-slate-400">
          Nicht aktiviert. Am Server freischalten:
          <code className="ml-1 rounded bg-black/30 px-1.5 py-0.5 text-slate-200">
            sudo /opt/offgridcloud/src/deploy/install.sh --power-control
          </code>
        </div>
      )}

      <div className="flex flex-wrap gap-3">
        {POWER_ACTIONS.map((a) => {
          const enabled = Boolean(status[a.enabled]);
          const cls = a.danger
            ? "border border-red-500/40 text-red-300 hover:bg-red-500/10"
            : "border border-white/10 text-slate-200 hover:bg-white/5";
          return (
            <span key={a.action} className="inline-flex items-center gap-1.5">
              <button
                type="button"
                onClick={() => run(a)}
                disabled={!enabled || busy !== null}
                className={`rounded-lg px-4 py-2 text-sm font-semibold disabled:cursor-not-allowed disabled:opacity-40 ${cls}`}
              >
                {busy === a.action ? "…" : a.label}
              </button>
              <InfoTip text={a.help} />
            </span>
          );
        })}
      </div>
    </div>
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
  {
    key: "notify_on_startup",
    label: "Start des Servers",
    help: "Sendet beim Hochfahren eine ausführliche Zusammenfassung: Startzeit, belegter/freier Speicher, verbundene Cloud-Ziele, VPN-Status, externe und interne IP, wartende Übertragungen, gemessene Bandbreite und verbundene Pool-Geräte.",
  },
  {
    key: "notify_on_reconnect",
    label: "Wieder online",
    help: "Meldet sich kurz, sobald die Internetverbindung nach einem Ausfall wiederhergestellt ist – mit aktueller Bandbreite sowie neuer externer und interner IP.",
  },
  {
    key: "notify_on_bandwidth",
    label: "Senden pausiert/fortgesetzt",
    help: "Meldet, wenn das Senden wegen zu geringer Bandbreite gestoppt und später wieder aufgenommen wird.",
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

const TERMINAL_PHASES = ["success", "failed", "unknown"];

function UpdateCard() {
  const [info, setInfo] = useState<UpdateInfo | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [progress, setProgress] = useState<UpdateProgress | null>(null);
  const [applying, setApplying] = useState(false);
  const pollTimer = useRef<number | null>(null);
  const logRef = useRef<HTMLPreElement>(null);

  function check(force = false) {
    setBusy(true);
    setMsg(null);
    api<UpdateInfo>(`/api/updates${force ? "?force=true" : ""}`)
      .then(setInfo)
      .catch((e) => setMsg(e instanceof ApiError ? e.message : "Fehler"))
      .finally(() => setBusy(false));
  }

  // Poll live update progress. Keeps polling through the service restart the
  // update triggers — requests fail while the box reboots, which we treat as
  // "restarting" rather than an error, until a terminal phase settles it.
  function poll() {
    api<UpdateProgress>("/api/updates/progress")
      .then((p) => {
        setProgress(p);
        if (p.phase === "running") return schedule();
        // Terminal: stop polling. On success, refresh the version badge.
        setApplying(false);
        if (p.phase === "success") check(true);
      })
      .catch(() => schedule()); // box likely restarting — keep trying
  }
  function schedule() {
    if (pollTimer.current) window.clearTimeout(pollTimer.current);
    pollTimer.current = window.setTimeout(poll, 2000);
  }

  useEffect(() => {
    check();
    // Pick up an update already in flight (e.g. after a page reload mid-update).
    api<UpdateProgress>("/api/updates/progress")
      .then((p) => {
        setProgress(p);
        if (p.phase === "running") {
          setApplying(true);
          schedule();
        }
      })
      .catch(() => {});
    return () => {
      if (pollTimer.current) window.clearTimeout(pollTimer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Keep the streamed log scrolled to the newest line.
  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [progress?.log]);

  async function apply() {
    setBusy(true);
    setMsg(null);
    try {
      await api<{ started: boolean; message: string }>("/api/updates/apply", {
        method: "POST",
      });
      setApplying(true);
      setProgress({
        phase: "running",
        running: true,
        from_version: info?.current ?? "",
        to_version: "",
        message: "Update läuft …",
        returncode: null,
        started_at: 0,
        finished_at: 0,
        log: "",
      });
      schedule();
    } catch (e) {
      setMsg(e instanceof ApiError ? e.message : "Update fehlgeschlagen");
    } finally {
      setBusy(false);
    }
  }

  const available = info?.update_available;
  const phase = progress?.phase;
  const running = applying || phase === "running";
  const busyOrRunning = busy || running;
  return (
    <div
      className={`mb-6 rounded-2xl p-5 ring-1 ${
        available && !running ? "bg-ogc-teal/10 ring-ogc-teal/30" : "bg-slate-800/60 ring-white/10"
      }`}
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="text-sm font-medium text-slate-400">Version</span>
        <span className="text-lg font-bold text-white">{info?.current ?? "…"}</span>
        {available && !running && info?.latest && (
          <span className="rounded-full bg-ogc-teal/20 px-2 py-0.5 text-xs font-semibold text-ogc-teal">
            Update verfügbar: {info.latest}
          </span>
        )}
        {info && !available && !info.error && !running && (
          <span className="text-xs text-emerald-300">aktuell</span>
        )}
        <button
          type="button"
          onClick={() => check(true)}
          disabled={busyOrRunning}
          className="ml-auto rounded-lg border border-white/10 px-3 py-1.5 text-xs hover:bg-white/5 disabled:opacity-50"
        >
          {busy ? "…" : "Nach Updates suchen"}
        </button>
      </div>

      {info?.error && !running && <div className="mt-2 text-xs text-slate-500">{info.error}</div>}

      {available && !running && !TERMINAL_PHASES.includes(phase ?? "") && (
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
                disabled={busyOrRunning}
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

      {progress && (running || TERMINAL_PHASES.includes(progress.phase)) && (
        <UpdateProgressView progress={progress} logRef={logRef} onReload={() => window.location.reload()} />
      )}

      {msg && <div className="mt-3 text-sm text-slate-300">{msg}</div>}
    </div>
  );
}

function UpdateProgressView({
  progress,
  logRef,
  onReload,
}: {
  progress: UpdateProgress;
  logRef: RefObject<HTMLPreElement>;
  onReload: () => void;
}) {
  const { phase, message, log, to_version } = progress;
  const tone =
    phase === "success"
      ? { ring: "ring-emerald-500/30", text: "text-emerald-300", icon: "✓" }
      : phase === "failed"
        ? { ring: "ring-red-500/30", text: "text-red-300", icon: "✗" }
        : phase === "unknown"
          ? { ring: "ring-amber-500/30", text: "text-amber-300", icon: "?" }
          : { ring: "ring-ogc-teal/30", text: "text-ogc-teal", icon: "" };

  return (
    <div className={`mt-3 rounded-xl bg-slate-900/60 p-4 ring-1 ${tone.ring}`}>
      <div className={`flex items-center gap-2 text-sm font-medium ${tone.text}`}>
        {phase === "running" ? (
          <span className="inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" />
        ) : (
          <span aria-hidden>{tone.icon}</span>
        )}
        <span>
          {phase === "running"
            ? message || "Update läuft …"
            : phase === "success"
              ? message || "Update abgeschlossen."
              : phase === "failed"
                ? message || "Update fehlgeschlagen."
                : message || "Ergebnis unklar."}
        </span>
        {to_version && phase === "success" && (
          <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-xs">{to_version}</span>
        )}
      </div>

      {phase === "running" && (
        <p className="mt-1 text-xs text-slate-500">
          Der Dienst baut neu und startet dann neu — die Seite bleibt kurz nicht erreichbar. Bitte nicht schließen.
        </p>
      )}

      {log && (
        <pre
          ref={logRef}
          className="mt-3 max-h-56 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-black/40 p-3 text-xs leading-relaxed text-slate-300"
        >
          {log}
        </pre>
      )}

      {phase === "success" && (
        <button
          type="button"
          onClick={onReload}
          className="mt-3 rounded-lg bg-gradient-to-r from-ogc-teal to-ogc-blue px-4 py-2 text-sm font-semibold text-white"
        >
          Portal neu laden
        </button>
      )}
    </div>
  );
}
