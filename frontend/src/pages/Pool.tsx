import { useCallback, useEffect, useState } from "react";
import {
  api,
  ApiError,
  type PoolNodeStatus,
  type PoolOverview,
  type PoolPeer,
  type PoolSelf,
} from "../api";
import Layout from "../components/Layout";
import { formatBytes } from "../upload";

function NodeCard({ node, isSelf }: { node: PoolNodeStatus; isSelf?: boolean }) {
  const diskPct = node.disk_total ? (1 - node.disk_free / node.disk_total) * 100 : 0;
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
              <span className="rounded bg-ogc-teal/20 px-1.5 py-0.5 text-[10px] text-ogc-teal">
                diese Box
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
    "rounded-lg border border-white/10 bg-slate-800/60 px-3 py-2 text-sm text-white outline-none focus:border-ogc-teal/50";
  const totals = overview?.totals;

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

      <div className="mb-8 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {overview && <NodeCard node={overview.self} isSelf />}
        {overview?.peers.map((p) => (
          <NodeCard key={p.peer_id ?? p.name} node={p} />
        ))}
      </div>

      <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-400">
        Peers verwalten
      </h3>
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
          placeholder="https://box2.local:8000"
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
          className="rounded-lg bg-gradient-to-r from-ogc-teal to-ogc-blue px-4 py-2 text-sm font-medium text-white"
        >
          Peer hinzufügen
        </button>
      </form>

      {peers.length > 0 && (
        <div className="mb-8 space-y-2">
          {peers.map((p) => (
            <div
              key={p.id}
              className="flex items-center justify-between rounded-xl bg-slate-800/60 p-3 text-sm ring-1 ring-white/5"
            >
              <div className="min-w-0">
                <div className="truncate font-medium text-white">{p.name}</div>
                <div className="truncate text-xs text-slate-500">{p.base_url}</div>
              </div>
              <div className="flex items-center gap-2">
                {!p.has_token && (
                  <span className="rounded bg-amber-500/15 px-2 py-0.5 text-xs text-amber-300">
                    kein Token
                  </span>
                )}
                <button
                  onClick={() => togglePeer(p)}
                  className="rounded border border-white/10 px-3 py-1 text-xs hover:bg-white/5"
                >
                  {p.enabled ? "Aktiv" : "Pausiert"}
                </button>
                <button
                  onClick={() => removePeer(p)}
                  className="rounded border border-red-500/30 px-3 py-1 text-xs text-red-300 hover:bg-red-500/10"
                >
                  Entfernen
                </button>
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
    </Layout>
  );
}
