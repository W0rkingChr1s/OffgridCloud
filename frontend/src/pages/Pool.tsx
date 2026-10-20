import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  ApiError,
  type DiscoveredBox,
  type PairingCode,
  type PoolDiscovery,
  type PoolPriority,
  type PoolNodeStatus,
  type PoolOverview,
  type PoolAccessToken,
  type PoolAccessTokenCreated,
  type PoolPeer,
  type PoolTokenScope,
  type TransferAction,
  type TransferControl,
  type TransferJob,
  type TransferStatus,
} from "../api";
import Layout from "../components/Layout";
import MoreMenu from "../components/MoreMenu";
import { SortMenu, type SortOption, useSort } from "../components/Sort";
import { useToast } from "../toast";
import { formatBytes } from "../upload";

const NODE_SORT: SortOption<PoolNodeStatus>[] = [
  { key: "name", label: "Name", get: (n) => n.name },
  { key: "media", label: "Medien", get: (n) => n.media_total },
  { key: "transfers", label: "Aktive Transfers", get: (n) => n.active_transfers },
  { key: "throughput", label: "Durchsatz", get: (n) => n.throughput_kbps },
  { key: "disk", label: "Speicher frei", get: (n) => n.disk_free },
];

const PEER_SORT: SortOption<PoolPeer>[] = [
  { key: "name", label: "Name", get: (p) => p.name },
  { key: "status", label: "Status", get: (p) => (p.enabled ? 0 : 1) },
  { key: "created", label: "Hinzugefügt", get: (p) => p.created_at },
];

function NodeCard({
  node,
  isSelf,
  busy,
  jobsOpen,
  onAction,
  onToggleJobs,
}: {
  node: PoolNodeStatus;
  isSelf?: boolean;
  busy?: boolean;
  jobsOpen?: boolean;
  onAction: (node: PoolNodeStatus, action: TransferAction) => void;
  onToggleJobs: (node: PoolNodeStatus) => void;
}) {
  const diskPct = node.disk_total ? (1 - node.disk_free / node.disk_total) * 100 : 0;
  // Our own box is always controllable; a peer only if its admin allowed it.
  const canControl = node.reachable && (isSelf || node.control_enabled);
  const btn =
    "rounded-lg border border-white/10 px-3 py-1 text-xs hover:bg-white/5 disabled:opacity-50";
  return (
    <div
      className={`rounded-2xl p-4 ring-1 ${
        node.reachable ? "bg-slate-800/60 ring-white/10" : "bg-red-500/5 ring-red-500/20"
      }`}
    >
      <div className="flex items-center justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span
              className={`h-2.5 w-2.5 shrink-0 rounded-full ${
                node.reachable ? "bg-emerald-400" : "bg-red-400"
              }`}
            />
            <span className="truncate font-semibold text-white">{node.name}</span>
            {isSelf && (
              <span className="rounded-sm bg-ogc-teal/20 px-1.5 py-0.5 text-[10px] text-ogc-teal">
                diese Box
              </span>
            )}
            {node.priority > 0 && (
              <span
                title="Rang bei gemeinsamer Leitung"
                className="rounded-sm bg-white/10 px-1.5 py-0.5 text-[10px] text-slate-300"
              >
                Rang {node.priority}
              </span>
            )}
            {node.reachable && node.uploads_paused && (
              <span className="rounded-sm bg-amber-500/15 px-1.5 py-0.5 text-[10px] text-amber-300">
                {node.priority_paused ? "pausiert (Vorrang)" : "Uploads pausiert"}
              </span>
            )}
          </div>
          {node.base_url && (
            <div className="mt-0.5 truncate text-xs text-slate-500">{node.base_url}</div>
          )}
        </div>
        {node.version && <span className="text-xs text-slate-500">v{node.version}</span>}
      </div>

      {node.reachable ? (
        <dl className="mt-3 grid grid-cols-2 gap-2 text-sm">
          <div>
            <dt className="text-xs text-slate-500">Medien</dt>
            <dd className="text-white">{node.media_total}</dd>
          </div>
          <div>
            <dt className="text-xs text-slate-500">Aktive Transfers</dt>
            <dd className="text-white">{node.active_transfers}</dd>
          </div>
          <div>
            <dt className="text-xs text-slate-500">Durchsatz</dt>
            <dd className="text-white">{node.throughput_kbps.toFixed(0)} KB/s</dd>
          </div>
          <div>
            <dt className="text-xs text-slate-500">Speicher frei</dt>
            <dd className="text-white">
              {node.disk_total ? `${formatBytes(node.disk_free)} (${diskPct.toFixed(0)}%)` : "—"}
            </dd>
          </div>
        </dl>
      ) : (
        <div className="mt-3 text-sm text-red-300">{node.error || "nicht erreichbar"}</div>
      )}

      {node.reachable && (
        <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-white/5 pt-3">
          <button
            onClick={() => onToggleJobs(node)}
            aria-expanded={jobsOpen}
            className={`${btn} ${jobsOpen ? "bg-white/10" : ""}`}
          >
            {jobsOpen ? "Jobs ausblenden" : "Jobs ansehen"}
          </button>
          {canControl && (
            <>
              <button
                disabled={busy}
                onClick={() => onAction(node, node.uploads_paused ? "resume" : "pause")}
                className={btn}
              >
                {node.uploads_paused ? "Uploads fortsetzen" : "Uploads pausieren"}
              </button>
              {node.failed_transfers > 0 && (
                <button
                  disabled={busy}
                  onClick={() => onAction(node, "retry_failed")}
                  className={btn}
                >
                  Fehlgeschlagene erneut ({node.failed_transfers})
                </button>
              )}
            </>
          )}
          {!canControl && (
            <span className="text-xs text-slate-500">
              Nur Lesezugriff — das Token dieses Hubs darf hier nicht steuern
            </span>
          )}
        </div>
      )}
    </div>
  );
}

const JOB_FILTERS: { key: TransferStatus | ""; label: string }[] = [
  { key: "failed", label: "Fehlgeschlagen" },
  { key: "queued", label: "Wartend" },
  { key: "running", label: "Läuft" },
  { key: "", label: "Alle" },
];

const JOB_STATUS: Record<TransferStatus, { label: string; cls: string }> = {
  queued: { label: "wartet", cls: "bg-slate-500/20 text-slate-300" },
  running: { label: "läuft", cls: "bg-ogc-teal/20 text-ogc-teal" },
  done: { label: "fertig", cls: "bg-emerald-500/20 text-emerald-300" },
  failed: { label: "fehlgeschlagen", cls: "bg-red-500/20 text-red-300" },
};

/** Transfer jobs of one node — this box via the local API, a peer via the hub. */
function NodeJobs({
  node,
  onClose,
  onChanged,
}: {
  node: PoolNodeStatus;
  onClose: () => void;
  onChanged: () => void;
}) {
  const isSelf = node.peer_id === null;
  const canControl = isSelf || node.control_enabled;
  const [filter, setFilter] = useState<TransferStatus | "">("failed");
  const [jobs, setJobs] = useState<TransferJob[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyJob, setBusyJob] = useState<number | null>(null);
  const toast = useToast();
  const panel = useRef<HTMLDivElement>(null);

  // On phones the panel sits below every card — bring it into view on open.
  useEffect(() => {
    panel.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, []);

  const load = useCallback(() => {
    const url = isSelf
      ? `/api/transfers${filter ? `?status_filter=${filter}` : ""}`
      : `/api/pool/peers/${node.peer_id}/jobs${filter ? `?status=${filter}` : ""}`;
    setError(null);
    api<TransferJob[]>(url)
      .then(setJobs)
      .catch((e) => {
        setJobs([]);
        setError(e instanceof ApiError ? e.message : "Fehler");
      });
  }, [isSelf, node.peer_id, filter]);

  useEffect(() => {
    setJobs(null);
    load();
  }, [load]);

  async function retry(job: TransferJob) {
    setBusyJob(job.id);
    try {
      await api(
        isSelf
          ? `/api/transfers/${job.id}/retry`
          : `/api/pool/peers/${node.peer_id}/jobs/${job.id}/retry`,
        { method: "POST" },
      );
      toast.success(node.name, `„${job.media_filename}" neu eingereiht`);
      load();
      onChanged();
    } catch (e) {
      toast.error(node.name, e instanceof ApiError ? e.message : "Fehlgeschlagen");
    } finally {
      setBusyJob(null);
    }
  }

  return (
    <div ref={panel} className="mb-8 scroll-mt-4 rounded-2xl bg-slate-800/40 p-4 ring-1 ring-white/10">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-semibold text-white">Jobs auf {node.name}</h3>
        <div className="flex flex-wrap items-center gap-1">
          {JOB_FILTERS.map((f) => (
            <button
              key={f.key || "all"}
              onClick={() => setFilter(f.key)}
              className={`rounded-lg px-2.5 py-1 text-xs ${
                filter === f.key ? "bg-white/10 text-white" : "text-slate-400 hover:bg-white/5"
              }`}
            >
              {f.label}
            </button>
          ))}
          <button
            onClick={load}
            className="ml-1 rounded-lg border border-white/10 px-2.5 py-1 text-xs hover:bg-white/5"
          >
            Aktualisieren
          </button>
          <button
            onClick={onClose}
            aria-label="Schließen"
            className="rounded-lg px-2 py-1 text-xs text-slate-400 hover:bg-white/5"
          >
            ✕
          </button>
        </div>
      </div>

      {error && <div className="mb-2 text-sm text-red-300">{error}</div>}
      {jobs === null ? (
        <p className="text-sm text-slate-500">Lädt …</p>
      ) : jobs.length === 0 ? (
        !error && <p className="text-sm text-slate-500">Keine Jobs in dieser Ansicht.</p>
      ) : (
        <ul className="divide-y divide-white/5">
          {jobs.map((j) => {
            const st = JOB_STATUS[j.status] ?? JOB_STATUS.queued;
            return (
              <li key={j.id} className="flex flex-wrap items-start justify-between gap-2 py-2 text-sm">
                <div className="min-w-0 flex-1">
                  <div className="truncate font-medium text-white">{j.media_filename}</div>
                  <div className="text-xs text-slate-500">
                    → {j.provider_name}
                    {j.attempts > 0 && ` · ${j.attempts} Versuche`}
                    {` · ${new Date(j.updated_at + "Z").toLocaleString()}`}
                  </div>
                  {j.status === "failed" && j.last_error && (
                    <div className="mt-0.5 wrap-break-word text-xs text-red-300">{j.last_error}</div>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  <span className={`whitespace-nowrap rounded-sm px-2 py-0.5 text-xs ${st.cls}`}>
                    {st.label}
                  </span>
                  {canControl && (j.status === "failed" || j.status === "queued") && (
                    <button
                      disabled={busyJob === j.id}
                      onClick={() => retry(j)}
                      className="whitespace-nowrap rounded-lg border border-white/10 px-3 py-1 text-xs hover:bg-white/5 disabled:opacity-50"
                    >
                      Erneut
                    </button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
      {jobs !== null && jobs.length >= 100 && (
        <p className="mt-2 text-xs text-slate-500">Es werden nur die neuesten Jobs angezeigt.</p>
      )}
    </div>
  );
}

const LEVELS = [
  { value: 0, label: "nicht verwaltet" },
  { value: 1, label: "1 – hoch" },
  { value: 2, label: "2 – mittel" },
  { value: 3, label: "3 – niedrig" },
];

/** Ranks for boxes on ONE shared uplink; the hub pauses lower ranks while higher ones send. */
function PriorityPanel({ overview, onSaved }: { overview: PoolOverview | null; onSaved: () => void }) {
  const [prio, setPrio] = useState<PoolPriority | null>(null);
  const [dirty, setDirty] = useState(false);
  const toast = useToast();

  const load = useCallback(() => {
    api<PoolPriority>("/api/pool/priority").then((p) => {
      setPrio(p);
      setDirty(false);
    });
  }, []);
  useEffect(load, [load]);

  if (!prio || prio.nodes.length < 2) return null; // needs at least the hub + one peer

  function update(patch: Partial<PoolPriority>) {
    setPrio((p) => (p ? { ...p, ...patch } : p));
    setDirty(true);
  }

  async function save() {
    try {
      setPrio(await api<PoolPriority>("/api/pool/priority", { method: "PUT", body: JSON.stringify(prio) }));
      setDirty(false);
      toast.success("Vorrang", "Gespeichert – wirkt ab der nächsten Minute.");
      onSaved();
    } catch (e) {
      toast.error("Vorrang", e instanceof ApiError ? e.message : "Fehler");
    }
  }

  const ranked = prio.nodes.filter((n) => n.level > 0);
  // Only boxes below the top rank ever get paused — they need the "control" right.
  const top = Math.min(...ranked.map((n) => n.level));
  const readOnly = ranked.filter(
    (n) =>
      n.level > top &&
      n.peer_id !== null &&
      overview?.peers.find((p) => p.peer_id === n.peer_id && p.reachable && !p.control_enabled),
  );

  return (
    <div className="mb-8 rounded-2xl bg-slate-800/40 p-4 ring-1 ring-white/10">
      <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-400">
          Vorrang bei gemeinsamer Leitung
        </h3>
        <label className="flex cursor-pointer items-center gap-2 text-sm text-slate-300">
          <input
            type="checkbox"
            checked={prio.enabled}
            onChange={(e) => update({ enabled: e.target.checked })}
          />
          aktiv
        </label>
      </div>
      <p className="mb-3 text-sm text-slate-400">
        Nur für Boxen, die sich <strong>eine</strong> Leitung teilen (z. B. ein Starlink am
        Einsatzort). Solange eine Box mit höherem Rang etwas sendet, pausiert der Hub Boxen mit
        niedrigerem Rang und setzt sie danach fort. Boxen mit eigenem Uplink auf „nicht verwaltet“
        lassen — sonst verschenken sie Bandbreite.
      </p>
      <div className="mb-3 grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {prio.nodes.map((n, i) => (
          <label
            key={n.peer_id ?? "self"}
            className="flex items-center justify-between gap-2 rounded-xl bg-slate-800/60 px-3 py-2 text-sm ring-1 ring-white/5"
          >
            <span className="truncate text-white">
              {n.name}
              {n.peer_id === null && <span className="ml-1 text-xs text-slate-500">(diese Box)</span>}
            </span>
            <select
              className="rounded-lg border border-white/10 bg-slate-800/60 px-2 py-1 text-xs text-white"
              value={n.level}
              onChange={(e) => {
                const nodes = prio.nodes.slice();
                nodes[i] = { ...n, level: Number(e.target.value) };
                update({ nodes });
              }}
            >
              {LEVELS.map((l) => (
                <option key={l.value} value={l.value}>
                  {l.label}
                </option>
              ))}
            </select>
          </label>
        ))}
      </div>
      {prio.enabled && ranked.length === 1 && (
        <p className="mb-2 text-xs text-amber-300">Mindestens zwei Boxen brauchen einen Rang.</p>
      )}
      {prio.enabled && readOnly.length > 0 && (
        <p className="mb-2 text-xs text-amber-300">
          Wirkt nicht bei {readOnly.map((n) => n.name).join(", ")}: deren Token für diesen Hub darf
          nur lesen. Dort unter „Zugriff für Hubs“ auf „Lesen + steuern“ stellen.
        </p>
      )}
      <button
        onClick={save}
        disabled={!dirty}
        className="rounded-lg border border-white/10 px-4 py-1.5 text-sm hover:bg-white/5 disabled:opacity-40"
      >
        Speichern
      </button>
    </div>
  );
}

/** Boxes announcing themselves in the LAN; pair one by the code it shows. */
function NearbyBoxes({ onPaired }: { onPaired: () => void }) {
  const [disc, setDisc] = useState<PoolDiscovery | null>(null);
  const [pairing, setPairing] = useState<string | null>(null); // node_id or "manual"
  const [code, setCode] = useState("");
  const [manualUrl, setManualUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  const load = useCallback(() => {
    api<PoolDiscovery>("/api/pool/discovery").then(setDisc).catch(() => setDisc(null));
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, [load]);

  function startPairing(key: string) {
    setPairing(key);
    setCode("");
  }

  async function pair(e: React.FormEvent, baseUrl: string, name?: string) {
    e.preventDefault();
    setBusy(true);
    try {
      const peer = await api<PoolPeer>("/api/pool/peers/pair", {
        method: "POST",
        body: JSON.stringify({ base_url: baseUrl, code, name }),
      });
      toast.success("Gekoppelt", `„${peer.name}" ist jetzt Peer (nur lesen).`);
      setPairing(null);
      setManualUrl("");
      load();
      onPaired();
    } catch (err) {
      toast.error("Kopplung fehlgeschlagen", err instanceof ApiError ? err.message : "Fehler");
    } finally {
      setBusy(false);
    }
  }

  const input =
    "w-28 rounded-lg border border-white/10 bg-slate-800/60 px-2 py-1 text-center font-mono text-sm tracking-widest text-white outline-hidden focus:border-ogc-teal/50";
  const btn = "rounded-lg border border-white/10 px-3 py-1 text-xs hover:bg-white/5 disabled:opacity-50";

  function codeForm(onSubmit: (e: React.FormEvent) => void) {
    return (
      <form onSubmit={onSubmit} className="flex items-center gap-2">
        <input
          className={input}
          inputMode="numeric"
          autoFocus
          placeholder="Code"
          maxLength={6}
          value={code}
          onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
          aria-label="Kopplungscode"
        />
        <button type="submit" disabled={busy || code.length !== 6} className={btn}>
          Koppeln
        </button>
        <button type="button" onClick={() => setPairing(null)} className="text-xs text-slate-400">
          Abbrechen
        </button>
      </form>
    );
  }

  if (!disc) return null;
  const open = disc.boxes.filter((b: DiscoveredBox) => b.peer_id === null);
  return (
    <div className="mb-8">
      <h3 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-400">
        In der Nähe gefunden
      </h3>
      {!disc.running ? (
        <p className="mb-2 text-sm text-slate-500">
          Automatische Suche nicht verfügbar: {disc.error}. In Docker funktioniert sie nur mit{" "}
          <code>network_mode: host</code>.
        </p>
      ) : disc.boxes.length === 0 ? (
        <p className="mb-2 text-sm text-slate-500">
          Keine andere Box im Netz gefunden. Boxen müssen im selben Netz (Subnetz) sein und sich
          anzeigen lassen.
        </p>
      ) : (
        <div className="mb-2 space-y-2">
          {disc.boxes.map((b) => (
            <div
              key={b.node_id}
              className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-slate-800/60 p-3 text-sm ring-1 ring-white/5"
            >
              <div className="min-w-0">
                <div className="truncate font-medium text-white">
                  {b.name}
                  {b.version && <span className="ml-2 text-xs text-slate-500">v{b.version}</span>}
                </div>
                <div className="truncate text-xs text-slate-500">{b.url}</div>
              </div>
              {b.peer_id !== null ? (
                <span className="rounded-sm bg-emerald-500/15 px-2 py-0.5 text-xs text-emerald-300">
                  gekoppelt
                </span>
              ) : pairing === b.node_id ? (
                codeForm((e) => pair(e, b.url, b.name))
              ) : (
                <button onClick={() => startPairing(b.node_id)} className={btn}>
                  Koppeln …
                </button>
              )}
            </div>
          ))}
          {open.length > 0 && (
            <p className="text-xs text-slate-500">
              „Koppeln“: Auf der anderen Box unter Pool einen Kopplungscode erzeugen und hier
              eingeben.
            </p>
          )}
        </div>
      )}
      {pairing === "manual" ? (
        <div className="flex flex-wrap items-center gap-2">
          <input
            className="rounded-lg border border-white/10 bg-slate-800/60 px-3 py-1 text-sm text-white outline-hidden focus:border-ogc-teal/50"
            placeholder="http://192.168.1.20:8000"
            value={manualUrl}
            onChange={(e) => setManualUrl(e.target.value)}
            aria-label="Adresse der Box"
          />
          {codeForm((e) => pair(e, manualUrl))}
        </div>
      ) : (
        <button onClick={() => startPairing("manual")} className="text-xs text-slate-400 underline">
          Box nicht in der Liste? Per Adresse + Code koppeln
        </button>
      )}
    </div>
  );
}

/** This box's side: show a pairing code, and whether to announce itself. */
function PairingPanel({ onChanged }: { onChanged: () => void }) {
  const [pairing, setPairing] = useState<PairingCode | null>(null);
  const [left, setLeft] = useState(0);
  const [announce, setAnnounce] = useState<boolean | null>(null);

  useEffect(() => {
    api<PairingCode>("/api/pool/pairing").then((p) => {
      if (p.code) {
        setPairing(p);
        setLeft(p.expires_in);
      }
    });
    api<PoolDiscovery>("/api/pool/discovery")
      .then((d) => setAnnounce(d.announce))
      .catch(() => setAnnounce(null));
  }, []);

  // Count down locally; when the code expires (or got used), drop it.
  useEffect(() => {
    if (!pairing) return;
    const t = setInterval(() => {
      setLeft((s) => {
        if (s <= 1) {
          setPairing(null);
          onChanged();
          return 0;
        }
        return s - 1;
      });
    }, 1000);
    return () => clearInterval(t);
  }, [pairing, onChanged]);

  // A hub that redeemed the code shows up as a new token — notice it.
  useEffect(() => {
    if (!pairing) return;
    const t = setInterval(() => {
      api<PairingCode>("/api/pool/pairing").then((p) => {
        if (!p.code) {
          setPairing(null);
          onChanged();
        }
      });
    }, 3000);
    return () => clearInterval(t);
  }, [pairing, onChanged]);

  async function create() {
    const p = await api<PairingCode>("/api/pool/pairing", { method: "POST" });
    setPairing(p);
    setLeft(p.expires_in);
  }

  async function cancel() {
    await api("/api/pool/pairing", { method: "DELETE" });
    setPairing(null);
  }

  async function toggleAnnounce() {
    if (announce === null) return;
    const d = await api<PoolDiscovery>("/api/pool/discovery", {
      method: "PUT",
      body: JSON.stringify({ announce: !announce }),
    });
    setAnnounce(d.announce);
  }

  const btn = "rounded-lg border border-white/10 px-4 py-2 text-sm hover:bg-white/5";
  return (
    <div className="mb-4 space-y-3">
      {pairing ? (
        <div className="rounded-xl bg-slate-800/60 p-4 ring-1 ring-ogc-teal/30">
          <div className="text-xs text-slate-400">
            Kopplungscode — am Hub unter „In der Nähe gefunden“ eingeben:
          </div>
          <div className="my-1 font-mono text-4xl font-bold tracking-[0.3em] text-ogc-teal">
            {pairing.code}
          </div>
          <div className="flex items-center gap-3 text-xs text-slate-400">
            noch {Math.floor(left / 60)}:{String(left % 60).padStart(2, "0")} min gültig · einmal
            verwendbar · der Hub bekommt nur Leserecht
            <button onClick={cancel} className="text-slate-300 underline">
              Abbrechen
            </button>
          </div>
        </div>
      ) : (
        <button onClick={create} className={btn}>
          Kopplungscode erzeugen
        </button>
      )}
      {announce !== null && (
        <label className="flex cursor-pointer items-center gap-2 text-sm text-slate-300">
          <input type="checkbox" checked={announce} onChange={toggleAnnounce} />
          Diese Box im Netz anzeigen (mDNS), damit Hubs sie finden
        </label>
      )}
    </div>
  );
}

export default function Pool() {
  const [overview, setOverview] = useState<PoolOverview | null>(null);
  const [peers, setPeers] = useState<PoolPeer[]>([]);
  const [tokens, setTokens] = useState<PoolAccessToken[]>([]);
  const [error, setError] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [token, setToken] = useState("");
  const [revealedToken, setRevealedToken] = useState<PoolAccessTokenCreated | null>(null);
  const [tokenName, setTokenName] = useState("");
  const [tokenScope, setTokenScope] = useState<PoolTokenScope>("read");
  const [addingPeer, setAddingPeer] = useState(false);
  const [busyNode, setBusyNode] = useState<string | null>(null);
  const [jobsFor, setJobsFor] = useState<string | null>(null);
  const toast = useToast();

  const loadPeers = useCallback(() => {
    api<PoolPeer[]>("/api/pool/peers").then(setPeers).catch(() => setPeers([]));
    api<PoolAccessToken[]>("/api/pool/tokens").then(setTokens).catch(() => setTokens([]));
  }, []);

  const loadOverview = useCallback(() => {
    api<PoolOverview>("/api/pool/overview")
      .then((o) => {
        setOverview(o);
        setError(null);
      })
      .catch((e) => setError(e instanceof ApiError ? e.message : "Fehler"));
  }, []);

  useEffect(() => {
    loadPeers();
    loadOverview();
    const t = setInterval(loadOverview, 15000);
    return () => clearInterval(t);
  }, [loadPeers, loadOverview]);

  async function addPeer(e: React.FormEvent) {
    e.preventDefault();
    try {
      await api("/api/pool/peers", {
        method: "POST",
        body: JSON.stringify({ name, base_url: baseUrl, token }),
      });
      setName("");
      setBaseUrl("");
      setToken("");
      setAddingPeer(false);
      loadPeers();
      loadOverview();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Fehler");
    }
  }

  async function togglePeer(peer: PoolPeer) {
    await api(`/api/pool/peers/${peer.id}`, {
      method: "PATCH",
      body: JSON.stringify({ enabled: !peer.enabled }),
    });
    loadPeers();
    loadOverview();
  }

  async function removePeer(peer: PoolPeer) {
    if (!window.confirm(`Peer „${peer.name}" entfernen?`)) return;
    await api(`/api/pool/peers/${peer.id}`, { method: "DELETE" });
    loadPeers();
    loadOverview();
  }

  async function controlNode(node: PoolNodeStatus, action: TransferAction) {
    const isPeer = node.peer_id !== null;
    if (
      isPeer &&
      action === "pause" &&
      !window.confirm(`Uploads auf „${node.name}" pausieren? Laufende Uploads laufen noch zu Ende.`)
    )
      return;
    const key = nodeKey(node);
    setBusyNode(key);
    try {
      const res = await api<TransferControl>(
        isPeer ? `/api/pool/peers/${node.peer_id}/control` : "/api/transfers/control",
        { method: "POST", body: JSON.stringify({ action }) },
      );
      toast.success(node.name, res.message);
      loadOverview();
    } catch (err) {
      toast.error(node.name, err instanceof ApiError ? err.message : "Fehlgeschlagen");
    } finally {
      setBusyNode(null);
    }
  }

  async function createToken(e: React.FormEvent) {
    e.preventDefault();
    try {
      const res = await api<PoolAccessTokenCreated>("/api/pool/tokens", {
        method: "POST",
        body: JSON.stringify({ name: tokenName, scope: tokenScope }),
      });
      setRevealedToken(res);
      setTokenName("");
      setTokenScope("read");
      loadPeers();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Fehler");
    }
  }

  async function setScope(t: PoolAccessToken, scope: PoolTokenScope) {
    if (
      scope === "control" &&
      !window.confirm(
        `Hub „${t.name}" darf dann hier Uploads pausieren/fortsetzen und fehlgeschlagene ` +
          "Transfers neu anstoßen. Erlauben?",
      )
    )
      return;
    try {
      await api(`/api/pool/tokens/${t.id}`, { method: "PATCH", body: JSON.stringify({ scope }) });
      loadPeers();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Fehler");
    }
  }

  async function revokeToken(t: PoolAccessToken) {
    if (!window.confirm(`Zugriff für „${t.name}" sofort entziehen? Andere Hubs bleiben verbunden.`))
      return;
    await api(`/api/pool/tokens/${t.id}`, { method: "DELETE" });
    if (revealedToken?.id === t.id) setRevealedToken(null);
    loadPeers();
  }

  function nodeKey(node: PoolNodeStatus) {
    return node.peer_id === null ? "self" : String(node.peer_id);
  }

  function toggleJobs(node: PoolNodeStatus) {
    const key = nodeKey(node);
    setJobsFor((cur) => (cur === key ? null : key));
  }

  // Resolved from the latest overview, so scope changes show up on refresh.
  const jobsNode = !overview
    ? null
    : jobsFor === "self"
      ? overview.self
      : (overview.peers.find((p) => String(p.peer_id) === jobsFor && p.reachable) ?? null);

  const field =
    "rounded-lg border border-white/10 bg-slate-800/60 px-3 py-2 text-sm text-white outline-hidden focus:border-ogc-teal/50";
  const totals = overview?.totals;

  const nodeSort = useSort(overview?.peers ?? [], NODE_SORT, { key: "name" });
  const peerSort = useSort(peers, PEER_SORT, { key: "name" });

  return (
    <Layout>
      <div className="mb-6">
        <h2 className="text-2xl font-bold">Pool</h2>
        <p className="text-sm text-slate-400">
          Mehrere OffgridCloud-Boxen im Blick — diese Box fragt die hinterlegten Peers ab und
          zeigt eine gemeinsame Flottenübersicht.
        </p>
      </div>

      {error && (
        <div className="mb-4 rounded-lg bg-red-500/15 px-3 py-2 text-sm text-red-300">{error}</div>
      )}

      {totals && (
        <div className="mb-6 flex flex-wrap gap-4 rounded-2xl bg-slate-800/40 px-4 py-3 text-sm ring-1 ring-white/5">
          <span>
            <span className="text-slate-500">Knoten online: </span>
            <span className="font-semibold text-white">
              {totals.nodes_online}/{totals.nodes}
            </span>
          </span>
          <span>
            <span className="text-slate-500">Medien gesamt: </span>
            <span className="font-semibold text-white">{totals.media_total}</span>
          </span>
          <span>
            <span className="text-slate-500">Aktive Transfers: </span>
            <span className="font-semibold text-white">{totals.active_transfers}</span>
          </span>
          <span>
            <span className="text-slate-500">Durchsatz: </span>
            <span className="font-semibold text-white">
              {totals.throughput_kbps.toFixed(0)} KB/s
            </span>
          </span>
        </div>
      )}

      {/* Sorting only pays off once the list no longer fits at a glance. */}
      {overview && overview.peers.length > 4 && (
        <div className="mb-3 flex justify-end">
          <SortMenu sort={nodeSort} />
        </div>
      )}
      <div className="mb-8 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {overview && (
          <NodeCard
            node={overview.self}
            isSelf
            busy={busyNode === "self"}
            jobsOpen={jobsFor === "self"}
            onAction={controlNode}
            onToggleJobs={toggleJobs}
          />
        )}
        {nodeSort.sorted.map((p) => (
          <NodeCard
            key={p.peer_id ?? p.name}
            node={p}
            busy={busyNode === String(p.peer_id)}
            jobsOpen={jobsFor === String(p.peer_id)}
            onAction={controlNode}
            onToggleJobs={toggleJobs}
          />
        ))}
      </div>

      {jobsNode && (
        <NodeJobs
          key={jobsFor}
          node={jobsNode}
          onClose={() => setJobsFor(null)}
          onChanged={loadOverview}
        />
      )}

      <PriorityPanel key={peers.length} overview={overview} onSaved={loadOverview} />

      <NearbyBoxes
        onPaired={() => {
          loadPeers();
          loadOverview();
        }}
      />

      <div className="mb-3 flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-400">
          Peers verwalten
        </h3>
        <div className="flex items-center gap-2">
          {peers.length > 4 && <SortMenu sort={peerSort} />}
          {!addingPeer && (
            <button
              type="button"
              onClick={() => setAddingPeer(true)}
              className="whitespace-nowrap rounded-lg border border-white/10 px-3 py-1.5 text-xs font-medium text-slate-200 hover:bg-white/5"
            >
              + Peer hinzufügen
            </button>
          )}
        </div>
      </div>
      {(addingPeer || peers.length === 0) && (
        <form onSubmit={addPeer} className="mb-4 grid grid-cols-1 gap-2 sm:grid-cols-4">
          <input
            className={field}
            placeholder="Name (z. B. Box 2)"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
          <input
            className={field}
            placeholder="http://192.168.1.20:8000"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            required
          />
          <input
            className={field}
            placeholder="Pool-Token des Peers"
            value={token}
            onChange={(e) => setToken(e.target.value)}
          />
          <button
            type="submit"
            className="rounded-lg bg-linear-to-r from-ogc-teal to-ogc-blue px-4 py-2 text-sm font-medium text-white"
          >
            Peer hinzufügen
          </button>
        </form>
      )}

      {peers.length > 0 && (
        <div className="mb-8 space-y-2">
          {peerSort.sorted.map((p) => (
            <div
              key={p.id}
              className="flex items-center justify-between gap-3 rounded-xl bg-slate-800/60 p-3 text-sm ring-1 ring-white/5"
            >
              <div className="min-w-0">
                <div className="truncate font-medium text-white">{p.name}</div>
                <div className="truncate text-xs text-slate-500">{p.base_url}</div>
              </div>
              <div className="flex items-center gap-2">
                {!p.has_token && (
                  <span className="rounded-sm bg-amber-500/15 px-2 py-0.5 text-xs text-amber-300">
                    kein Token
                  </span>
                )}
                <button
                  onClick={() => togglePeer(p)}
                  title={p.enabled ? "Abfrage pausieren" : "Abfrage wieder aufnehmen"}
                  className="rounded-lg border border-white/10 px-3 py-1 text-xs hover:bg-white/5"
                >
                  {p.enabled ? "Aktiv" : "Pausiert"}
                </button>
                <MoreMenu items={[{ label: "Peer entfernen …", danger: true, onClick: () => removePeer(p) }]} />
              </div>
            </div>
          ))}
        </div>
      )}

      <h3 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-400">
        Zugriff für Hubs auf diese Box
      </h3>
      <p className="mb-3 text-sm text-slate-400">
        Jede Box, die <em>diese</em> hier als Peer abfragen soll, bekommt ein eigenes Token. Am
        einfachsten per Kopplungscode; alternativ hier ein Token erzeugen und dort mit dieser URL
        eintragen. Einzelne Hubs lassen sich jederzeit sperren, ohne die anderen neu einzurichten.
      </p>

      <PairingPanel onChanged={loadPeers} />

      <form onSubmit={createToken} className="mb-3 grid grid-cols-1 gap-2 sm:grid-cols-4">
        <input
          className={`${field} sm:col-span-2`}
          placeholder="Für welchen Hub? (z. B. ELW)"
          value={tokenName}
          onChange={(e) => setTokenName(e.target.value)}
          required
        />
        <select
          className={field}
          value={tokenScope}
          onChange={(e) => setTokenScope(e.target.value as PoolTokenScope)}
        >
          <option value="read">Nur lesen</option>
          <option value="control">Lesen + Transfers steuern</option>
        </select>
        <button
          type="submit"
          className="rounded-lg border border-white/10 px-4 py-2 text-sm hover:bg-white/5"
        >
          Token erzeugen
        </button>
      </form>

      {revealedToken && (
        <div className="mb-3 rounded-lg bg-slate-800/60 p-3 ring-1 ring-white/5">
          <div className="text-xs text-slate-400">
            Token für „{revealedToken.name}" jetzt kopieren — er wird nur einmal angezeigt:
          </div>
          <code className="mt-1 block break-all text-sm text-ogc-teal">{revealedToken.token}</code>
        </div>
      )}

      {tokens.length === 0 ? (
        <p className="text-sm text-slate-500">Noch kein Token — diese Box ist nicht abfragbar.</p>
      ) : (
        <div className="space-y-2">
          {tokens.map((t) => (
            <div
              key={t.id}
              className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-slate-800/60 p-3 text-sm ring-1 ring-white/5"
            >
              <div className="min-w-0">
                <div className="truncate font-medium text-white">
                  {t.name} <span className="font-normal text-slate-500">…{t.hint}</span>
                </div>
                <div className="truncate text-xs text-slate-500">
                  {t.last_used_at
                    ? `zuletzt ${new Date(t.last_used_at + "Z").toLocaleString()}` +
                      (t.last_used_hub ? ` von ${t.last_used_hub}` : "")
                    : "noch nie benutzt"}
                </div>
              </div>
              <div className="flex items-center gap-2">
                <select
                  aria-label={`Recht für ${t.name}`}
                  className="rounded-lg border border-white/10 bg-slate-800/60 px-2 py-1 text-xs text-white"
                  value={t.scope}
                  onChange={(e) => setScope(t, e.target.value as PoolTokenScope)}
                >
                  <option value="read">Nur lesen</option>
                  <option value="control">Lesen + steuern</option>
                </select>
                <MoreMenu
                  items={[{ label: "Zugriff entziehen …", danger: true, onClick: () => revokeToken(t) }]}
                />
              </div>
            </div>
          ))}
        </div>
      )}
    </Layout>
  );
}
