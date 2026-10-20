import { useCallback, useEffect, useState } from "react";
import {
  api,
  ApiError,
  type PoolNodeStatus,
  type PoolOverview,
  type PoolPeer,
  type PoolSelf,
  type TransferAction,
  type TransferControl,
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
  onAction,
}: {
  node: PoolNodeStatus;
  isSelf?: boolean;
  busy?: boolean;
  onAction: (node: PoolNodeStatus, action: TransferAction) => void;
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
            {node.reachable && node.uploads_paused && (
              <span className="rounded-sm bg-amber-500/15 px-1.5 py-0.5 text-[10px] text-amber-300">
                Uploads pausiert
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

      {canControl ? (
        <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-white/5 pt-3">
          <button
            disabled={busy}
            onClick={() => onAction(node, node.uploads_paused ? "resume" : "pause")}
            className={btn}
          >
            {node.uploads_paused ? "Uploads fortsetzen" : "Uploads pausieren"}
          </button>
          {node.failed_transfers > 0 && (
            <button disabled={busy} onClick={() => onAction(node, "retry_failed")} className={btn}>
              Fehlgeschlagene erneut ({node.failed_transfers})
            </button>
          )}
        </div>
      ) : (
        node.reachable &&
        !isSelf && (
          <div className="mt-3 text-xs text-slate-500">Fernsteuerung auf dieser Box nicht freigegeben</div>
        )
      )}
    </div>
  );
}

export default function Pool() {
  const [overview, setOverview] = useState<PoolOverview | null>(null);
  const [peers, setPeers] = useState<PoolPeer[]>([]);
  const [self, setSelf] = useState<PoolSelf | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [token, setToken] = useState("");
  const [revealedToken, setRevealedToken] = useState<string | null>(null);
  const [addingPeer, setAddingPeer] = useState(false);
  const [busyNode, setBusyNode] = useState<string | null>(null);
  const toast = useToast();

  const loadPeers = useCallback(() => {
    api<PoolPeer[]>("/api/pool/peers").then(setPeers).catch(() => setPeers([]));
    api<PoolSelf>("/api/pool/self").then(setSelf).catch(() => setSelf(null));
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
    const key = node.peer_id === null ? "self" : String(node.peer_id);
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

  async function toggleControl() {
    if (!self) return;
    if (
      !self.control_enabled &&
      !window.confirm(
        "Jeder Hub mit dem Pool-Token dieser Box darf dann Uploads pausieren/fortsetzen und " +
          "fehlgeschlagene Transfers neu anstoßen. Freigeben?",
      )
    )
      return;
    try {
      const res = await api<PoolSelf>("/api/pool/self", {
        method: "PUT",
        body: JSON.stringify({ control_enabled: !self.control_enabled }),
      });
      setSelf(res);
      loadOverview();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Fehler");
    }
  }

  async function rotateToken() {
    const res = await api<{ pool_token: string }>("/api/pool/token", { method: "POST" });
    setRevealedToken(res.pool_token);
    loadPeers();
  }

  async function clearToken() {
    await api("/api/pool/token", { method: "DELETE" });
    setRevealedToken(null);
    loadPeers();
  }

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
            onAction={controlNode}
          />
        )}
        {nodeSort.sorted.map((p) => (
          <NodeCard
            key={p.peer_id ?? p.name}
            node={p}
            busy={busyNode === String(p.peer_id)}
            onAction={controlNode}
          />
        ))}
      </div>

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
        Diese Box als Peer freigeben
      </h3>
      <p className="mb-3 text-sm text-slate-400">
        Damit eine andere Box <em>diese</em> hier abfragen darf, hier ein Pool-Token erzeugen und
        dort als Peer mit dieser URL + Token eintragen.
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <button
          onClick={rotateToken}
          className="rounded-lg border border-white/10 px-4 py-2 text-sm hover:bg-white/5"
        >
          {self?.token_set ? "Token neu erzeugen" : "Token erzeugen"}
        </button>
        {self?.token_set && (
          <button
            onClick={clearToken}
            className="rounded-lg border border-red-500/30 px-4 py-2 text-sm text-red-300 hover:bg-red-500/10"
          >
            Token löschen
          </button>
        )}
        {self && !self.token_set && !revealedToken && (
          <span className="text-sm text-slate-500">Noch kein Token — diese Box ist nicht abfragbar.</span>
        )}
      </div>
      {revealedToken && (
        <div className="mt-3 rounded-lg bg-slate-800/60 p-3 ring-1 ring-white/5">
          <div className="text-xs text-slate-400">
            Token jetzt kopieren — er wird nur einmal angezeigt:
          </div>
          <code className="mt-1 block break-all text-sm text-ogc-teal">{revealedToken}</code>
        </div>
      )}
      {self?.token_set && (
        <label className="mt-4 flex cursor-pointer items-start gap-3 rounded-lg bg-slate-800/40 p-3 text-sm ring-1 ring-white/5">
          <input
            type="checkbox"
            className="mt-0.5"
            checked={self.control_enabled}
            onChange={toggleControl}
          />
          <span>
            <span className="font-medium text-white">Fernsteuerung durch Hub erlauben</span>
            <span className="block text-xs text-slate-400">
              Ein Hub mit diesem Token darf hier Uploads pausieren/fortsetzen und fehlgeschlagene
              Transfers neu anstoßen. Löschen oder Einstellungen ändern kann er nicht. Jede Aktion
              landet im Audit-Log.
            </span>
          </span>
        </label>
      )}
    </Layout>
  );
}
