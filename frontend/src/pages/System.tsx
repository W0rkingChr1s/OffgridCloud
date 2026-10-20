import { useEffect, useRef, useState, type RefObject } from "react";
import {
  api,
  ApiError,
  getHttpsStatus,
  rootCaUrls,
  updateHttps,
  type AuditEvent,
  type HttpsStatus,
  type SystemStatus,
  type UpdateInfo,
  type UpdateProgress,
} from "../api";
import Collapsible from "../components/Collapsible";
import InfoTip from "../components/InfoTip";
import Layout from "../components/Layout";
import TelegramWizard from "../components/TelegramWizard";
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
  const [auditShown, setAuditShown] = useState(10);

  return (
    <Layout>
      <h2 className="mb-1 text-2xl font-bold">System</h2>
      <p className="mb-6 text-sm text-slate-400">Updates, Upload-Verhalten, Benachrichtigungen und Wartung.</p>

      {error && <div className="mb-4 rounded-lg bg-red-500/15 px-3 py-2 text-sm text-red-300">{error}</div>}

      <UpdateCard />

      <div className="mb-6 rounded-2xl bg-slate-800/60 p-5 ring-1 ring-white/10">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <div className="text-sm font-medium text-slate-400">Uploads</div>
          {disk && (
            <div className="text-sm text-slate-300">
              Puffer: <span className="font-semibold text-white">{formatBytes(disk.free)}</span> frei von{" "}
              {formatBytes(disk.total)}
            </div>
          )}
        </div>
        {disk && (
          <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-slate-700">
            <div
              className={`h-full rounded-full ${disk.low_space ? "bg-red-500" : "bg-linear-to-r from-ogc-teal to-ogc-blue"}`}
              style={{ width: `${pct}%` }}
            />
          </div>
        )}
        {disk?.low_space && <div className="mt-2 text-xs text-red-300">⚠ Wenig freier Speicher</div>}
        {status && !status.rclone_available && (
          <div className="mt-3 rounded-lg bg-red-500/15 px-3 py-2 text-sm text-red-300">
            rclone fehlt — Übertragungen in die Cloud laufen nicht. Installer bzw. Update erneut ausführen.
          </div>
        )}

        <div className="mt-4 grid gap-3">
          <Toggle
            checked={status?.delete_local_after_upload ?? false}
            onChange={toggleDelete}
            label="Lokale Kopie nach erfolgreichem Upload löschen"
            help="Sobald ein Medium an ALLE verknüpften Ziele bestätigt hochgeladen wurde, wird die lokale Pufferdatei entfernt, um Speicher (z. B. auf der SD-Karte) zu sparen. Erst nach erfolgreicher Prüfung – nie vorher. Danach ist kein lokaler Download mehr möglich."
          />
          <Toggle
            checked={status?.delete_remote_on_local_delete ?? false}
            onChange={toggleRemoteDelete}
            label="Beim lokalen Löschen auch in der Cloud löschen"
            help="Wenn du eine Datei aus einem Ordner löschst, wird sie zusätzlich bei allen Zielen entfernt, zu denen sie bereits hochgeladen wurde (per rclone). Ohne diesen Haken bleibt die Cloud-Kopie als Backup erhalten."
            warn="Unwiderruflich — die Cloud-Kopie ist danach weg."
          />
          <Toggle
            checked={status?.auto_resync ?? false}
            onChange={toggleAutoResync}
            label="Hängengebliebene Uploads automatisch wiederholen"
            help={`Prüft im Hintergrund regelmäßig${resyncMinutes ? ` (alle ~${resyncMinutes} Min.)` : ""} alle Ziele: fehlgeschlagene Transfers werden erneut eingereiht und fehlende Jobs nachgezogen. So heilt eine kurze Störung (offline, Ziel nicht erreichbar) von selbst, sobald die Verbindung zurück ist – ganz ohne manuelles „Erneut".`}
          />
        </div>
      </div>

      {status && <NotificationsCard status={status} save={save} onStatus={setStatus} />}

      {status && <PowerCard status={status} />}

      <Collapsible title="Erweitert" summary="Webhook, Bandbreiten-Messziel, HTTPS-Domain">
        <div className="grid gap-5">
          <label className="text-sm">
            <span className="mb-1 flex items-center gap-1.5 text-slate-300">
              Webhook-URL
              <InfoTip text="Sobald ein Medium an alle Ziele hochgeladen ist, wird an diese URL einmalig ein JSON-POST gesendet (Dateiname, Größe, SHA-256, Ordner). Ideal, um andere Systeme zu benachrichtigen oder Automatisierungen anzustoßen. Leer lassen = deaktiviert." />
            </span>
            <input
              className={inputCls}
              placeholder="https://… (POST, sobald ein Medium überall angekommen ist)"
              value={webhookUrl}
              onChange={(e) => setWebhookUrl(e.target.value)}
              onBlur={() => webhookUrl !== status?.webhook_url && save({ webhook_url: webhookUrl })}
            />
          </label>
          <label className="text-sm">
            <span className="mb-1 flex items-center gap-1.5 text-slate-300">
              Bandbreiten-Messziel
              <InfoTip text="Ziel-Datei, die für die aktive Durchsatzmessung heruntergeladen wird. Leer lassen nutzt eine öffentliche Cloudflare-Testdatei – für „Jetzt messen“ musst du hier nichts eintragen. Nur setzen, wenn du bewusst ein eigenes Testziel (z. B. im lokalen Netz) verwenden willst." />
            </span>
            <input
              className={inputCls}
              placeholder="Leer = öffentliche Test-Datei (Cloudflare)"
              value={probeUrl}
              onChange={(e) => setProbeUrl(e.target.value)}
              onBlur={() => probeUrl !== status?.probe_url && save({ probe_url: probeUrl })}
            />
          </label>
          <HttpsSettings />
        </div>
      </Collapsible>

      <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-400">Aktivität</h3>
      {audit.length === 0 ? (
        <p className="text-sm text-slate-500">Noch keine Einträge.</p>
      ) : (
        <>
          <div className="overflow-x-auto rounded-2xl ring-1 ring-white/10">
            <table className="w-full text-left text-sm">
              <thead className="bg-slate-800/80 text-slate-400">
                <tr>
                  <SortTh sort={auditSort} field="time">Zeit</SortTh>
                  <SortTh sort={auditSort} field="user" className="hidden sm:table-cell">Benutzer</SortTh>
                  <SortTh sort={auditSort} field="action">Aktion</SortTh>
                  <SortTh sort={auditSort} field="detail" className="hidden sm:table-cell">Detail</SortTh>
                </tr>
              </thead>
              <tbody>
                {auditSort.sorted.slice(0, auditShown).map((e) => (
                  <tr key={e.id} className="border-t border-white/5 bg-slate-900/40">
                    <td className="whitespace-nowrap px-4 py-2.5 align-top text-slate-400">{auditFmt.format(new Date(e.created_at + "Z"))}</td>
                    <td className="hidden px-4 py-2.5 text-slate-300 sm:table-cell">{e.user_email || "—"}</td>
                    <td className="px-4 py-2.5">
                      <span className="rounded-sm bg-slate-700/60 px-2 py-0.5 text-xs text-slate-200">{e.action}</span>
                      {e.detail && <div className="mt-1 text-xs text-slate-400 sm:hidden">{e.detail}</div>}
                    </td>
                    <td className="hidden px-4 py-2.5 text-slate-400 sm:table-cell">{e.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {audit.length > auditShown && (
            <button
              type="button"
              onClick={() => setAuditShown((n) => n + 25)}
              className="mt-3 rounded-lg border border-white/10 px-3 py-1.5 text-xs font-medium text-slate-300 hover:bg-white/5"
            >
              Ältere anzeigen ({audit.length - auditShown} weitere)
            </button>
          )}
        </>
      )}
    </Layout>
  );
}

const inputCls =
  "w-full rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 outline-hidden focus:border-ogc-teal";

const auditFmt = new Intl.DateTimeFormat("de-DE", { dateStyle: "short", timeStyle: "short" });

/** Checkbox with label, an ⓘ for the details and an optional always-visible warning. */
function Toggle({
  checked,
  onChange,
  label,
  help,
  warn,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  label: string;
  help: string;
  warn?: string;
}) {
  return (
    <label className="flex items-start gap-3 text-sm">
      <input type="checkbox" className="mt-1" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span className="text-slate-300">
        <span className="inline-flex items-center gap-1.5">
          {label}
          <InfoTip text={help} />
        </span>
        {warn && <span className="mt-0.5 block text-xs text-amber-300/80">{warn}</span>}
      </span>
    </label>
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

function HttpsSettings() {
  const toast = useToast();
  const [https, setHttps] = useState<HttpsStatus | null>(null);
  const [domain, setDomain] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getHttpsStatus()
      .then((s) => {
        setHttps(s);
        setDomain(s.domain);
      })
      .catch(() => setHttps(null));
  }, []);

  async function saveDomain() {
    setBusy(true);
    try {
      const s = await updateHttps({ domain: domain.trim() });
      setHttps(s);
      setDomain(s.domain);
      toast.info("HTTPS", s.domain ? `Domain gesetzt: ${s.domain}` : "Domain entfernt.");
    } catch (e) {
      toast.error("HTTPS", e instanceof ApiError ? e.message : "Speichern fehlgeschlagen");
    } finally {
      setBusy(false);
    }
  }

  if (!https) return null;

  const urls = https.lan_url && (
    <span className="mt-1 block text-xs text-slate-500">
      Erreichbar unter <code className="text-slate-300">{https.lan_url}</code>
      {https.public_url && (
        <>
          {" "}und <code className="text-slate-300">{https.public_url}</code>
        </>
      )}
    </span>
  );

  return (
    <div className="text-sm">
      <div className="mb-1 flex items-center gap-1.5 text-slate-300">
        Öffentliche HTTPS-Domain
        <InfoTip text="Die Box ist im lokalen Netz per https://<name>.local mit einem selbstsignierten Zertifikat erreichbar. Trägst du eine öffentliche Domain ein, holt Caddy zusätzlich automatisch ein echtes Zertifikat (Let's Encrypt) — der lokale Zugriff bleibt bestehen. Voraussetzung: Die Domain zeigt per DNS auf diese Box und die Ports 80/443 sind von außen erreichbar (Portweiterleitung am Router). Das Zertifikat zu holen kann bis zu einer Minute dauern." />
      </div>
      {!https.enabled ? (
        <span className="block text-xs text-slate-500">
          HTTPS ist auf dieser Box nicht eingerichtet — dazu den Installer erneut ausführen und HTTPS aktivieren.
        </span>
      ) : !https.manageable ? (
        <>
          <span className="block text-xs text-slate-500">
            Ändern geht nur über den Installer (erneut ausführen und HTTPS aktivieren).
          </span>
          {urls}
        </>
      ) : (
        <>
          <div className="flex gap-2">
            <input
              className={inputCls}
              placeholder="z. B. cloud.example.com (optional)"
              value={domain}
              onChange={(e) => setDomain(e.target.value)}
            />
            <button
              type="button"
              onClick={saveDomain}
              disabled={busy || domain.trim() === https.domain}
              className="rounded-lg border border-white/10 px-4 py-2 text-sm font-semibold text-slate-200 hover:bg-white/5 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {busy ? "…" : "Speichern"}
            </button>
          </div>
          {urls}
        </>
      )}
      {https.enabled && <RootCaTrust https={https} />}
    </div>
  );
}

// tls internal: the leaf certificate rotates every ~12 h, so "accept the
// warning once" keeps breaking (Safari then fails requests silently). Trusting
// the box's root CA once per device fixes it for good.
function RootCaTrust({ https }: { https: HttpsStatus }) {
  const ca = rootCaUrls();
  return (
    <div className="mt-4">
      <div className="mb-1 flex items-center gap-1.5 text-slate-300">
        Zertifikat auf Geräten vertrauen
        <InfoTip text="Das Zertifikat für https://<name>.local stellt die Box selbst aus und erneuert es etwa alle 12 Stunden. Eine einmal bestätigte Browser-Warnung gilt nur für das alte Zertifikat – danach schlagen Anmeldung und Daten still fehl. Wer das Root-Zertifikat der Box einmal installiert, hat Ruhe (es ist 10 Jahre gültig). Auch Passkeys laufen damit zuverlässig." />
      </div>
      {!https.ca_available ? (
        <span className="block text-xs text-slate-500">
          Root-Zertifikat noch nicht exportiert – nach dem nächsten Update (oder erneutem
          Ausführen von deploy/https/apply.sh) steht es hier zum Download bereit.
        </span>
      ) : (
        <>
          <div className="flex flex-wrap gap-2">
            <a
              href={ca.profile}
              className="rounded-lg border border-white/10 px-4 py-2 text-sm font-semibold text-slate-200 hover:bg-white/5"
            >
              Profil für iPhone/iPad/Mac
            </a>
            <a
              href={ca.crt}
              className="rounded-lg border border-white/10 px-4 py-2 text-sm font-semibold text-slate-200 hover:bg-white/5"
            >
              Zertifikat (.crt)
            </a>
          </div>
          <ul className="mt-2 list-disc space-y-0.5 pl-5 text-xs text-slate-500">
            <li>
              iOS: Profil laden → Einstellungen → „Profil geladen“ → Installieren. Danach
              Einstellungen → Allgemein → Info → Zertifikatsvertrauenseinstellungen →
              „Volles Vertrauen“ einschalten.
            </li>
            <li>Android/Windows/Linux: .crt als vertrauenswürdige Stammzertifizierungsstelle importieren.</li>
            <li>
              Vor dem Vertrauen den Fingerabdruck (SHA-256) vergleichen:{" "}
              <code className="break-all text-slate-300">{https.ca_fingerprint}</code>
            </li>
          </ul>
        </>
      )}
    </div>
  );
}

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
      <div className="mb-3 flex items-center gap-1.5 text-sm font-medium text-slate-400">
        Wartung
        <InfoTip
          text={`${POWER_ACTIONS.map((a) => `${a.label}: ${a.help}`).join(" ")} Laufende Übertragungen werden unterbrochen und nach einem Neustart automatisch fortgesetzt.`}
        />
      </div>

      {!anyEnabled ? (
        <div className="rounded-lg bg-slate-900/60 px-3 py-2 text-xs text-slate-400">
          Alle Aktionen sind deaktiviert (Befehle leer). In der{" "}
          <code className="rounded-sm bg-black/30 px-1.5 py-0.5 text-slate-200">.env</code> die
          <code className="ml-1 rounded-sm bg-black/30 px-1.5 py-0.5 text-slate-200">OGC_*_COMMAND</code>
          -Werte setzen oder den Installer erneut ausführen.
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          {POWER_ACTIONS.map((a) => {
            const enabled = Boolean(status[a.enabled]);
            const cls = a.danger
              ? "border border-red-500/40 text-red-300 hover:bg-red-500/10"
              : "border border-white/10 text-slate-200 hover:bg-white/5";
            return (
              <button
                key={a.action}
                type="button"
                title={a.help}
                onClick={() => run(a)}
                disabled={!enabled || busy !== null}
                className={`rounded-lg px-3 py-1.5 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-40 ${cls}`}
              >
                {busy === a.action ? "…" : a.label}
              </button>
            );
          })}
        </div>
      )}
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
  {
    key: "notify_on_pool",
    label: "Pool-Änderungen (nur am Hub)",
    help: "Ist diese Box Hub eines Pools, fasst sie Änderungen an den Peers zu einer Nachricht pro Minute zusammen: Box nicht erreichbar oder wieder da, Speicher knapp, Uploads pausiert/fortgesetzt, neue fehlgeschlagene Transfers. Gemeldet werden nur Wechsel, nicht der Dauerzustand.",
  },
];

function NotificationsCard({
  status,
  save,
  onStatus,
}: {
  status: SystemStatus;
  save: (patch: Record<string, unknown>) => Promise<void>;
  onStatus: (s: SystemStatus) => void;
}) {
  const toast = useToast();
  const [wizard, setWizard] = useState(false);
  const [chatId, setChatId] = useState(status.telegram_chat_id);
  const [token, setToken] = useState("");
  const [testing, setTesting] = useState(false);
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
  async function testTelegram() {
    setTesting(true);
    try {
      const res = await api<{ sent: boolean; message: string }>("/api/system/telegram/test", {
        method: "POST",
      });
      if (res.sent) toast.success("Telegram", res.message);
      else toast.error("Telegram", res.message);
    } catch (e) {
      toast.error("Telegram", e instanceof ApiError ? e.message : "Test fehlgeschlagen");
    } finally {
      setTesting(false);
    }
  }
  async function saveEmail() {
    const patch: Record<string, unknown> = { ...smtp };
    if (smtpPassword) patch.smtp_password = smtpPassword;
    await save(patch);
    setSmtpPassword("");
  }

  const btnCls =
    "rounded-lg bg-linear-to-r from-ogc-teal to-ogc-blue px-4 py-2 text-sm font-semibold text-white disabled:opacity-50";
  const configured = (on: boolean, text = "eingerichtet") =>
    on ? <span className="text-emerald-300">{text}</span> : "nicht eingerichtet";

  return (
    <div className="mb-6 rounded-2xl bg-slate-800/60 p-5 ring-1 ring-white/10">
      <div className="mb-3 flex items-center gap-1.5 text-sm font-medium text-slate-400">
        Benachrichtigungen
        <InfoTip text="Statusmeldungen per Telegram und/oder E-Mail (der Webhook unter „Erweitert“ läuft parallel weiter). Ohne eingerichteten Kanal passiert nichts. Zugangsdaten werden verschlüsselt gespeichert und nie wieder angezeigt." />
      </div>

      {/* Which events notify */}
      <div className="mb-4 grid gap-2 sm:grid-cols-2">
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

      <div className="grid gap-2">
        <Collapsible nested title="Telegram" summary={configured(status.telegram_configured && !!status.telegram_chat_id)}>
          <div className="mb-3 flex flex-wrap items-center gap-3 rounded-lg bg-ogc-teal/5 px-3 py-2 ring-1 ring-ogc-teal/20">
            <span className="min-w-0 flex-1 text-xs text-slate-400">
              {status.telegram_configured && status.telegram_chat_id
                ? "Anderen Bot oder Chat? Der Assistent führt in einer Minute durch."
                : "Neu hier? Der Assistent schlägt Bot-Namen vor, prüft den Token und findet den Chat von selbst."}
            </span>
            <button
              type="button"
              onClick={() => setWizard(true)}
              className={status.telegram_configured && status.telegram_chat_id ? "rounded-lg px-3 py-2 text-sm text-slate-300 ring-1 ring-white/10 hover:bg-white/5" : btnCls}
            >
              🧭 Einrichtungsassistent
            </button>
          </div>
          {wizard && <TelegramWizard status={status} onClose={() => setWizard(false)} onSaved={onStatus} />}
          <p className="mb-3 flex items-center gap-1.5 text-xs text-slate-500">
            Manuell: Bot bei @BotFather anlegen, Token eintragen, Chat-ID per /id beim Bot erfragen.
            <InfoTip text="Nach dem Speichern meldet sich der Bot mit „OffgridCloud verbunden“ im Chat und richtet sein Befehlsmenü ein. Ist der Chat eine Gruppe oder ein Kanal und der Bot dort Admin (Infos ändern, Anheften), benennt er sie in „OffgridCloud · <Box>“ um, setzt Beschreibung und Logo und pinnt die Begrüßung. Befehle: /status, /queue, /speicher, /fehler, /retry, /netz, /speedtest, /ping, /restart, /reboot, /shutdown und /update (jeweils mit Bestätigung per Button), /hilfe – kein eigener Server und kein offener Port nötig. Jede Box braucht ihren eigenen Bot: Telegram lässt pro Bot-Token nur eine Box Befehle abholen." />
          </p>
          {status.telegram_conflict && (
            <p className="mb-3 rounded-lg bg-amber-500/10 px-3 py-2 text-xs text-amber-300 ring-1 ring-amber-500/30">
              Eine andere Box nutzt denselben Bot-Token. Telegram erlaubt pro Bot nur einen
              Empfänger, deshalb kommen Befehle und Buttons hier nicht zuverlässig an. Lege bei
              @BotFather für jede Box einen eigenen Bot an. Meldungen werden weiter gesendet.
            </p>
          )}
          <div className="grid gap-2 sm:grid-cols-2">
            <label className="block text-sm">
              <span className="mb-1 block text-slate-400">Bot-Token</span>
              <input
                className={inputCls}
                type="password"
                placeholder={status.telegram_configured ? "•••••• (gespeichert)" : "123456:ABC-DEF…"}
                value={token}
                onChange={(e) => setToken(e.target.value)}
              />
            </label>
            <label className="block text-sm">
              <span className="mb-1 block text-slate-400">Chat-ID</span>
              <input
                className={inputCls}
                placeholder="z. B. 123456789 oder -100…"
                value={chatId}
                onChange={(e) => setChatId(e.target.value)}
              />
            </label>
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            <button type="button" onClick={saveTelegram} className={btnCls}>
              Speichern
            </button>
            <button
              type="button"
              onClick={testTelegram}
              disabled={!status.telegram_configured || !status.telegram_chat_id || testing}
              className="rounded-lg px-3 py-2 text-sm text-slate-300 ring-1 ring-white/10 hover:bg-white/5 disabled:opacity-40"
            >
              {testing ? "Sende …" : "Testnachricht"}
            </button>
          </div>
        </Collapsible>

        <Collapsible
          nested
          title="E-Mail"
          summary={
            status.smtp_host && status.smtp_to
              ? configured(true, `an ${status.smtp_to}`)
              : configured(false)
          }
        >
          <div className="grid grid-cols-3 gap-2">
            <label className="col-span-2 block text-sm">
              <span className="mb-1 block text-slate-400">SMTP-Server</span>
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
          <div className="mt-3 flex flex-wrap items-center gap-4">
            <button type="button" onClick={saveEmail} className={btnCls}>
              Speichern
            </button>
            <label className="flex items-center gap-2 text-sm text-slate-300">
              <input
                type="checkbox"
                checked={smtp.smtp_tls}
                onChange={(e) => setSmtp((s) => ({ ...s, smtp_tls: e.target.checked }))}
              />
              STARTTLS (empfohlen, Port 587)
            </label>
          </div>
        </Collapsible>
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

  function beginPolling() {
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
  }

  async function apply() {
    setBusy(true);
    setMsg(null);
    try {
      await api<{ started: boolean; message: string }>("/api/updates/apply", {
        method: "POST",
      });
      beginPolling();
    } catch (e) {
      // A real ApiError means the server responded and rejected us (not enabled,
      // already running) — show it. A network failure means no response came
      // back: the box very likely restarted mid-request after a fast update, so
      // don't call it a failure — switch to polling, which bridges the downtime
      // and reports the true outcome from /progress once the box is back.
      if (e instanceof ApiError) {
        setMsg(e.message);
      } else {
        beginPolling();
      }
    } finally {
      setBusy(false);
    }
  }

  async function dismiss() {
    // Clear a finished result (incl. a stale one left by a pre-fix update) so
    // the card goes away — no terminal needed. Optimistically hide it first.
    setProgress(null);
    setApplying(false);
    try {
      await api("/api/updates/dismiss", { method: "POST" });
    } catch {
      /* best-effort — the card is already hidden locally */
    }
  }

  const available = info?.update_available;
  const onMain = info?.channel === "main";
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
        {onMain && (
          <span
            className="rounded-full bg-white/5 px-2 py-0.5 font-mono text-xs text-slate-300 ring-1 ring-white/10"
            title="Folgt dem main-Branch (Commit für Commit) statt Releases"
          >
            main{info.commit ? ` @ ${info.commit.slice(0, 7)}` : ""}
          </span>
        )}
        {available && !running && info?.latest && (
          <span className="rounded-full bg-ogc-teal/20 px-2 py-0.5 text-xs font-semibold text-ogc-teal">
            Update verfügbar:{" "}
            {onMain
              ? `${info.behind_by} ${info.behind_by === 1 ? "neuer Commit" : "neue Commits"} auf main`
              : info.latest}
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

      {available && !running && (
        <div className="mt-3 space-y-3">
          {info?.release_url && (
            <a
              href={info.release_url}
              target="_blank"
              rel="noreferrer"
              className="inline-block text-sm text-ogc-teal hover:underline"
            >
              {onMain ? "Änderungen ansehen ↗" : "Release-Notes ansehen ↗"}
            </a>
          )}
          {info?.self_update_enabled ? (
            <div>
              <button
                type="button"
                onClick={apply}
                disabled={busyOrRunning}
                className="rounded-lg bg-linear-to-r from-ogc-teal to-ogc-blue px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
              >
                Jetzt aktualisieren
              </button>
            </div>
          ) : (
            <div className="rounded-lg bg-slate-900/60 px-3 py-2 text-xs text-slate-400">
              Update auf dem Server ausführen:
              <code className="ml-1 rounded-sm bg-black/30 px-1.5 py-0.5 text-slate-200">
                {info?.in_docker
                  ? "docker compose pull && docker compose up -d"
                  : "sudo /opt/offgridcloud/src/deploy/update.sh"}
              </code>
            </div>
          )}
        </div>
      )}

      {progress && (running || TERMINAL_PHASES.includes(progress.phase)) && (
        <UpdateProgressView
          progress={progress}
          logRef={logRef}
          onReload={() => window.location.reload()}
          onDismiss={dismiss}
        />
      )}

      {msg && <div className="mt-3 text-sm text-slate-300">{msg}</div>}
    </div>
  );
}

function UpdateProgressView({
  progress,
  logRef,
  onReload,
  onDismiss,
}: {
  progress: UpdateProgress;
  logRef: RefObject<HTMLPreElement | null>;
  onReload: () => void;
  onDismiss: () => void;
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
          className="mt-3 max-h-56 overflow-auto whitespace-pre-wrap wrap-break-word rounded-lg bg-black/40 p-3 text-xs leading-relaxed text-slate-300"
        >
          {log}
        </pre>
      )}

      {phase !== "running" && (
        <div className="mt-3 flex gap-2">
          {phase === "success" && (
            <button
              type="button"
              onClick={onReload}
              className="rounded-lg bg-linear-to-r from-ogc-teal to-ogc-blue px-4 py-2 text-sm font-semibold text-white"
            >
              Portal neu laden
            </button>
          )}
          <button
            type="button"
            onClick={onDismiss}
            className="rounded-lg border border-white/10 px-4 py-2 text-sm font-medium text-slate-300 hover:bg-white/5"
          >
            Verstanden
          </button>
        </div>
      )}
    </div>
  );
}
