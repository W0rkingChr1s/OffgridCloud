import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  ApiError,
  type StorageCache,
  type StorageDisk,
  type StorageImportJob,
  type StorageOverview,
  type StoragePartition,
} from "../api";
import Layout from "../components/Layout";
import MediaBrowser from "../components/MediaBrowser";
import { useToast } from "../toast";
import { formatBytes } from "../upload";

const TRANSPORT_LABEL: Record<string, string> = {
  usb: "USB",
  mmc: "SD-Karte",
  sata: "SATA",
  nvme: "NVMe",
  virtio: "virtuell",
};

const FS_HINT: Record<string, string> = {
  exfat: "exFAT — Windows, macOS, Kameras; große Dateien (empfohlen)",
  vfat: "FAT32 — maximal kompatibel, Dateien max. 4 GB",
  ext4: "ext4 — Linux, robust (z. B. als Puffer-SSD)",
};

const IMPORT_STATUS: Record<string, string> = {
  pending: "wartet",
  running: "läuft",
  done: "fertig",
  failed: "Fehler",
  cancelled: "abgebrochen",
};

function diskTitle(d: StorageDisk): string {
  // Some buses report a raw PCI/USB vendor id ("0x1af4") — not a useful name.
  const vendor = /^0x[0-9a-f]+$/i.test(d.vendor) ? "" : d.vendor;
  const name = [vendor, d.model].filter(Boolean).join(" ").trim();
  return name || d.name;
}

function partTitle(p: StoragePartition): string {
  return p.label || p.name;
}

function Badge({ tone, children }: { tone: "red" | "amber" | "teal" | "slate"; children: React.ReactNode }) {
  const cls = {
    red: "bg-red-500/15 text-red-300 ring-red-500/30",
    amber: "bg-amber-500/15 text-amber-200 ring-amber-500/30",
    teal: "bg-ogc-teal/15 text-ogc-teal ring-ogc-teal/30",
    slate: "bg-white/5 text-slate-300 ring-white/10",
  }[tone];
  return <span className={`rounded-full px-2 py-0.5 text-xs font-medium ring-1 ${cls}`}>{children}</span>;
}

function ProgressBar({ done, total, failed }: { done: number; total: number; failed?: boolean }) {
  const pct = total ? Math.min(100, Math.round((done / total) * 100)) : 0;
  return (
    <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-slate-700">
      <div
        className={`h-full rounded-full transition-[width] ${failed ? "bg-red-500" : "bg-gradient-to-r from-ogc-teal to-ogc-blue"}`}
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

function UsageBar({ used, total }: { used: number; total: number }) {
  const pct = total ? Math.round((used / total) * 100) : 0;
  return (
    <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-slate-700">
      <div
        className={`h-full rounded-full ${pct > 90 ? "bg-red-500" : "bg-gradient-to-r from-ogc-teal to-ogc-blue"}`}
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

const btn =
  "rounded-lg border border-white/10 px-3 py-1.5 text-xs font-medium text-slate-200 hover:bg-white/5 disabled:cursor-not-allowed disabled:opacity-40";
const btnPrimary =
  "rounded-lg bg-gradient-to-r from-ogc-teal to-ogc-blue px-3 py-1.5 text-xs font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40";

export default function Storage() {
  const toast = useToast();
  const [overview, setOverview] = useState<StorageOverview | null>(null);
  const [jobs, setJobs] = useState<StorageImportJob[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [formatTarget, setFormatTarget] = useState<{ device: string; name: string; whole: boolean } | null>(null);
  // What the media browser shows: a partition, optionally rooted at a cache snapshot.
  const [browsing, setBrowsing] = useState<{ device: string; title: string; basePath: string } | null>(null);
  const [caches, setCaches] = useState<StorageCache[]>([]);
  const [backupFrom, setBackupFrom] = useState<StoragePartition | null>(null);
  const browserRef = useRef<HTMLDivElement>(null);

  const report = useCallback((e: unknown) => {
    setError(e instanceof ApiError ? e.message : "Fehler");
  }, []);

  const load = useCallback(() => {
    api<StorageOverview>("/api/storage").then(setOverview).catch(report);
  }, [report]);
  const loadJobs = useCallback(() => {
    api<StorageImportJob[]>("/api/storage/imports").then(setJobs).catch(() => {});
  }, []);
  const loadCaches = useCallback(() => {
    api<StorageCache[]>("/api/storage/caches").then(setCaches).catch(() => {});
  }, []);

  useEffect(() => {
    load();
    loadJobs();
    loadCaches();
    // Poll so plugged-in / removed media show up without a reload.
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [load, loadJobs, loadCaches]);

  const active = jobs.some((j) => j.status === "pending" || j.status === "running");
  useEffect(() => {
    const t = setInterval(loadJobs, active ? 1500 : 6000);
    return () => clearInterval(t);
  }, [active, loadJobs]);
  // Snapshot sizes change while a backup runs and when it ends.
  const wasActive = useRef(false);
  useEffect(() => {
    if (wasActive.current && !active) loadCaches();
    wasActive.current = active;
  }, [active, loadCaches]);

  // Close the browser if its medium vanished or got unmounted.
  useEffect(() => {
    if (!browsing || !overview) return;
    const still = overview.disks.flatMap((d) => d.partitions).find((p) => p.path === browsing.device);
    if (!still || !still.browsable) setBrowsing(null);
  }, [overview, browsing]);

  function open(device: string, title: string, basePath = "") {
    setBrowsing({ device, title, basePath });
    setTimeout(() => browserRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
  }
  const cacheReady = caches.some((c) => c.device && c.mountpoint);

  async function act(path: string, device: string, okMsg: string) {
    setError(null);
    setBusy(device);
    try {
      await api(path, { method: "POST", body: JSON.stringify({ device }) });
      toast.success(okMsg);
    } catch (e) {
      report(e);
    } finally {
      setBusy(null);
      load();
    }
  }

  const manage = overview?.manage_enabled ?? false;
  const buf = overview?.buffer;

  return (
    <Layout>
      <div className="mb-6 flex items-center justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold">Speicher</h2>
          <p className="text-sm text-slate-400">
            Angeschlossene Medien verwalten und Dateien davon für den Cloud-Upload übernehmen.
          </p>
        </div>
        <button onClick={load} className={btn}>
          Aktualisieren
        </button>
      </div>

      {error && (
        <div className="mb-4 flex items-start justify-between gap-3 rounded-lg bg-red-500/15 px-3 py-2 text-sm text-red-300">
          <span>{error}</span>
          <button onClick={() => setError(null)} className="text-red-200" aria-label="Schließen">
            ✕
          </button>
        </div>
      )}
      {overview?.error && (
        <div className="mb-4 rounded-lg bg-red-500/15 px-3 py-2 text-sm text-red-300">{overview.error}</div>
      )}
      {overview && !manage && (
        <div className="mb-4 rounded-2xl border border-amber-500/30 bg-amber-500/10 p-4 text-sm text-amber-200">
          <div className="font-semibold">Verwaltung nicht eingerichtet</div>
          <p className="mt-1 text-amber-200/90">
            Einhängen, Auswerfen und Formatieren brauchen den Root-Helfer. Auf einer nativen Installation
            richtet ihn <code className="text-amber-200">install.sh</code> (bzw. ein Update) automatisch ein.
            Anzeigen, Durchsuchen und Importieren von bereits eingehängten Medien funktioniert trotzdem.
          </p>
        </div>
      )}

      {buf && (
        <div className="mb-6 rounded-2xl bg-slate-800/60 p-5 ring-1 ring-white/10">
          <div className="flex items-baseline justify-between">
            <div className="text-sm font-medium text-slate-400">Puffer (Ziel für Importe)</div>
            <div className="text-sm text-slate-300">
              <span className="font-semibold text-white">{formatBytes(buf.free)}</span> frei von{" "}
              {formatBytes(buf.total)}
            </div>
          </div>
          <UsageBar used={buf.used} total={buf.total} />
        </div>
      )}

      {jobs.length > 0 && (
        <ImportJobs
          jobs={jobs}
          onChange={loadJobs}
          onError={report}
        />
      )}

      {browsing && (
        <div ref={browserRef} className="mb-6 scroll-mt-20">
          <MediaBrowser
            key={`${browsing.device}|${browsing.basePath}`}
            device={browsing.device}
            title={browsing.title}
            basePath={browsing.basePath}
            canCache={cacheReady && !caches.some((c) => c.device === browsing.device)}
            onStarted={() => {
              loadJobs();
              load();
            }}
            onClose={() => setBrowsing(null)}
          />
        </div>
      )}

      {caches.length > 0 && (
        <CachePanel caches={caches} onOpen={open} onChange={() => { loadCaches(); load(); }} onError={report} toast={toast} />
      )}

      {overview && overview.disks.length === 0 && !overview.error && (
        <div className="rounded-2xl bg-slate-800/60 p-8 text-center text-slate-400 ring-1 ring-white/10">
          Keine Laufwerke gefunden. USB-Stick oder SD-Karte anschließen — die Liste aktualisiert sich selbst.
        </div>
      )}

      <div className="grid gap-4">
        {overview?.disks.map((d) => (
          <div key={d.path} className="rounded-2xl bg-slate-800/60 p-5 ring-1 ring-white/10">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="truncate text-lg font-semibold text-white">{diskTitle(d)}</span>
                  {d.system && <Badge tone="red">System</Badge>}
                  {d.buffer && <Badge tone="amber">Puffer</Badge>}
                  {d.removable && !d.protected && <Badge tone="teal">Wechselmedium</Badge>}
                  {d.read_only && <Badge tone="slate">schreibgeschützt</Badge>}
                </div>
                <div className="mt-0.5 text-xs text-slate-500">
                  {d.path} · {formatBytes(d.size)}
                  {d.transport && ` · ${TRANSPORT_LABEL[d.transport] ?? d.transport.toUpperCase()}`}
                </div>
              </div>
              {!d.protected && (
                <div className="flex flex-wrap gap-2">
                  <button
                    className={btn}
                    disabled={!manage || busy !== null || d.read_only || d.partitions.some((p) => p.cache)}
                    title={d.partitions.some((p) => p.cache) ? "Als Cache markiert — erst die Markierung entfernen" : undefined}
                    onClick={() => setFormatTarget({ device: d.path, name: d.name, whole: true })}
                  >
                    Komplett formatieren
                  </button>
                  <button
                    className={btn}
                    disabled={!manage || busy !== null || d.partitions.some((p) => p.busy)}
                    onClick={() => act("/api/storage/eject", d.path, `${diskTitle(d)} kann entfernt werden.`)}
                  >
                    {busy === d.path ? "…" : "Auswerfen"}
                  </button>
                </div>
              )}
            </div>

            {d.protected && (
              <p className="mt-3 text-xs text-slate-500">
                {d.system
                  ? "Enthält das Betriebssystem — hier nicht verwaltbar."
                  : "Enthält den OffgridCloud-Puffer — hier nicht verwaltbar."}
              </p>
            )}

            {d.partitions.length === 0 && !d.protected && (
              <p className="mt-3 text-sm text-slate-400">
                Kein Dateisystem erkannt. Mit „Komplett formatieren“ nutzbar machen.
              </p>
            )}

            <div className="mt-4 grid gap-2">
              {d.partitions.map((p) => (
                <div
                  key={p.path}
                  className={`rounded-xl bg-slate-900/50 px-4 py-3 ring-1 ${
                    browsing?.device === p.path ? "ring-ogc-teal/50" : "ring-white/5"
                  }`}
                >
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-medium text-white">{partTitle(p)}</span>
                        <span className="text-xs text-slate-500">
                          {p.path} · {p.fstype || "kein Dateisystem"} · {formatBytes(p.size)}
                        </span>
                        {p.cache && <Badge tone="amber">Cache</Badge>}
                        {p.busy && <Badge tone="teal">in Arbeit</Badge>}
                      </div>
                      {p.mountpoint ? (
                        <div className="mt-1 text-xs text-slate-400">
                          {p.total > 0 && (
                            <>
                              {formatBytes(p.free)} frei · {formatBytes(p.used)} belegt ·{" "}
                            </>
                          )}
                          <code className="text-slate-500">{p.mountpoint}</code>
                        </div>
                      ) : (
                        <div className="mt-1 text-xs text-slate-500">nicht eingehängt</div>
                      )}
                      {p.mountpoint && p.total > 0 && <UsageBar used={p.used} total={p.total} />}
                    </div>
                    {!p.protected && (
                      <div className="flex flex-wrap gap-2">
                        {p.mountpoint ? (
                          <>
                            <button
                              className={btnPrimary}
                              onClick={() =>
                                browsing?.device === p.path && !browsing.basePath
                                  ? setBrowsing(null)
                                  : open(p.path, `${partTitle(p)} · ${diskTitle(d)}`)
                              }
                            >
                              {browsing?.device === p.path && !browsing.basePath ? "Schließen" : "Fotos & Dateien"}
                            </button>
                            {!p.cache && cacheReady && (
                              <button className={btn} disabled={p.busy} onClick={() => setBackupFrom(p)}>
                                Auf Cache sichern
                              </button>
                            )}
                            {!p.cache && !p.read_only && (
                              <button
                                className={btn}
                                disabled={busy !== null}
                                title="Dieses Laufwerk als Zwischenspeicher für Speicherkarten nutzen"
                                onClick={async () => {
                                  try {
                                    await api("/api/storage/caches", { method: "POST", body: JSON.stringify({ device: p.path }) });
                                    toast.success("Cache eingerichtet", `${partTitle(p)} nimmt jetzt Kartensicherungen auf.`);
                                    loadCaches();
                                    load();
                                  } catch (e) {
                                    report(e);
                                  }
                                }}
                              >
                                Als Cache nutzen
                              </button>
                            )}
                            <button
                              className={btn}
                              disabled={!manage || busy !== null || p.busy}
                              onClick={() => act("/api/storage/unmount", p.path, `${partTitle(p)} ausgehängt.`)}
                            >
                              {busy === p.path ? "…" : "Aushängen"}
                            </button>
                          </>
                        ) : (
                          <button
                            className={btnPrimary}
                            disabled={!manage || busy !== null || !p.fstype}
                            onClick={() => act("/api/storage/mount", p.path, `${partTitle(p)} eingehängt.`)}
                          >
                            {busy === p.path ? "…" : "Einhängen"}
                          </button>
                        )}
                        {p.path !== d.path && !p.cache && (
                          <button
                            className={btn}
                            disabled={!manage || busy !== null || p.busy || p.read_only}
                            onClick={() => setFormatTarget({ device: p.path, name: p.name, whole: false })}
                          >
                            Formatieren
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>

      {backupFrom && (
        <BackupModal
          partition={backupFrom}
          caches={caches.filter((c) => c.device && c.mountpoint)}
          onClose={() => setBackupFrom(null)}
          onDone={(job) => {
            setBackupFrom(null);
            toast.success("Sicherung läuft", `${job.total_files} Datei(en), ${formatBytes(job.total_bytes)} → Cache`);
            loadJobs();
            load();
          }}
        />
      )}

      {formatTarget && overview && (
        <FormatModal
          target={formatTarget}
          filesystems={overview.filesystems}
          labelMax={overview.label_max}
          onClose={() => setFormatTarget(null)}
          onDone={() => {
            setFormatTarget(null);
            toast.success("Formatiert", `${formatTarget.device} ist bereit — jetzt einhängen.`);
            load();
          }}
        />
      )}
    </Layout>
  );
}

// --- Format dialog ---------------------------------------------------------

function FormatModal({
  target,
  filesystems,
  labelMax,
  onClose,
  onDone,
}: {
  target: { device: string; name: string; whole: boolean };
  filesystems: string[];
  labelMax: Record<string, number>;
  onClose: () => void;
  onDone: () => void;
}) {
  const [fstype, setFstype] = useState(filesystems[0] ?? "exfat");
  const [label, setLabel] = useState("OFFGRID");
  const [confirm, setConfirm] = useState("");
  const [running, setRunning] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const max = labelMax[fstype] ?? 11;
  const labelOk = /^[A-Za-z0-9_-]*$/.test(label) && label.length <= max;

  async function submit() {
    setErr(null);
    setRunning(true);
    try {
      await api("/api/storage/format", {
        method: "POST",
        body: JSON.stringify({ device: target.device, fstype, label, confirm }),
      });
      onDone();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Fehler");
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/60 p-4" onClick={running ? undefined : onClose}>
      <div className="w-full max-w-lg rounded-2xl bg-slate-900 p-5 ring-1 ring-white/10" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-lg font-semibold text-white">
          {target.whole ? "Laufwerk komplett formatieren" : "Partition formatieren"}
        </h3>
        <div className="mt-2 rounded-lg bg-red-500/15 px-3 py-2 text-sm text-red-200">
          <strong>Alle Daten auf {target.device} werden gelöscht.</strong>
          {target.whole && " Die Partitionstabelle wird neu geschrieben (eine Partition über das ganze Laufwerk)."}
        </div>

        <label className="mt-4 block text-sm text-slate-300">Dateisystem</label>
        <div className="mt-1 grid gap-2">
          {filesystems.map((fs) => (
            <label key={fs} className="flex items-start gap-2 text-sm">
              <input type="radio" checked={fstype === fs} onChange={() => setFstype(fs)} className="mt-1" />
              <span className="text-slate-200">{FS_HINT[fs] ?? fs}</span>
            </label>
          ))}
        </div>

        <label className="mt-4 block text-sm text-slate-300">
          Bezeichnung <span className="text-slate-500">(A–Z, 0–9, _ und -, max. {max})</span>
        </label>
        <input
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          className="mt-1 w-full rounded-lg bg-slate-800 px-3 py-2 text-sm text-white ring-1 ring-white/10"
        />
        {!labelOk && <div className="mt-1 text-xs text-red-300">Ungültige Bezeichnung.</div>}

        <label className="mt-4 block text-sm text-slate-300">
          Zur Bestätigung <code className="text-white">{target.name}</code> eintippen
        </label>
        <input
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          autoComplete="off"
          className="mt-1 w-full rounded-lg bg-slate-800 px-3 py-2 text-sm text-white ring-1 ring-white/10"
        />

        {err && <div className="mt-3 rounded-lg bg-red-500/15 px-3 py-2 text-sm text-red-300">{err}</div>}

        <div className="mt-5 flex justify-end gap-2">
          <button className={btn} onClick={onClose} disabled={running}>
            Abbrechen
          </button>
          <button
            className="rounded-lg bg-red-600 px-3 py-1.5 text-xs font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40"
            disabled={running || !labelOk || confirm.trim() !== target.name}
            onClick={submit}
          >
            {running ? "Formatiere …" : "Formatieren"}
          </button>
        </div>
      </div>
    </div>
  );
}

// --- Import jobs ---------------------------------------------------------------

function ImportJobs({
  jobs,
  onChange,
  onError,
}: {
  jobs: StorageImportJob[];
  onChange: () => void;
  onError: (e: unknown) => void;
}) {
  async function cancel(id: string) {
    try {
      await api(`/api/storage/imports/${id}/cancel`, { method: "POST" });
    } catch (e) {
      onError(e);
    } finally {
      onChange();
    }
  }
  async function clear() {
    try {
      await api("/api/storage/imports", { method: "DELETE" });
    } catch (e) {
      onError(e);
    } finally {
      onChange();
    }
  }
  const anyFinished = jobs.some((j) => j.status !== "pending" && j.status !== "running");

  return (
    <div className="mb-6 rounded-2xl bg-slate-800/60 p-5 ring-1 ring-white/10">
      <div className="mb-3 flex items-center justify-between">
        <div className="text-sm font-medium text-slate-400">Vorgänge</div>
        {anyFinished && (
          <button className={btn} onClick={clear}>
            Erledigte ausblenden
          </button>
        )}
      </div>
      <div className="grid gap-3">
        {jobs.map((j) => {
          const pct = j.total_bytes ? Math.round((j.done_bytes / j.total_bytes) * 100) : j.status === "done" ? 100 : 0;
          const running = j.status === "pending" || j.status === "running";
          return (
            <div key={j.id} className="rounded-xl bg-slate-900/50 px-4 py-3 ring-1 ring-white/5">
              <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
                <div>
                  <span className="mr-2 text-xs uppercase tracking-wide text-slate-500">
                    {j.kind === "cache" ? "Cache-Sicherung" : "Upload"}
                  </span>
                  <span className="font-medium text-white">{j.device}</span>
                  <span className="text-slate-400">
                    {" "}→ {j.kind === "cache" ? j.target.split("::")[1] : `„${j.folder_name}“`}
                    {j.kind === "cache" && j.eject_after && " · danach auswerfen"}
                  </span>
                </div>
                <div className="flex items-center gap-3">
                  <Badge tone={j.status === "failed" ? "red" : j.status === "done" ? "teal" : "slate"}>
                    {IMPORT_STATUS[j.status] ?? j.status}
                  </Badge>
                  {running && (
                    <button className={btn} onClick={() => cancel(j.id)}>
                      Abbrechen
                    </button>
                  )}
                </div>
              </div>
              <ProgressBar done={j.status === "done" ? 1 : j.done_bytes} total={j.status === "done" ? 1 : j.total_bytes} failed={j.status === "failed"} />
              <div className="mt-1.5 text-xs text-slate-400">
                {j.done_files}/{j.total_files} Dateien · {formatBytes(j.done_bytes)} / {formatBytes(j.total_bytes)} ({pct}%)
                {" · "}
                {j.imported} {j.kind === "cache" ? "kopiert" : "übernommen"}
                {j.skipped > 0 && `, ${j.skipped} übersprungen`}
                {j.failed > 0 && <span className="text-red-300">, {j.failed} fehlgeschlagen</span>}
              </div>
              {running && j.current && <div className="mt-1 truncate text-xs text-slate-500">{j.current}</div>}
              {j.error && <div className="mt-1 text-xs text-red-300">{j.error}</div>}
              {j.errors.length > 0 && (
                <details className="mt-1 text-xs text-red-300/90">
                  <summary className="cursor-pointer">Fehlerdetails</summary>
                  <ul className="mt-1 list-disc pl-5">
                    {j.errors.map((e, i) => (
                      <li key={i}>{e}</li>
                    ))}
                  </ul>
                </details>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// --- Cache -----------------------------------------------------------------------

const snapFmt = new Intl.DateTimeFormat("de-DE", { dateStyle: "medium", timeStyle: "short" });

function CachePanel({
  caches,
  onOpen,
  onChange,
  onError,
  toast,
}: {
  caches: StorageCache[];
  onOpen: (device: string, title: string, basePath: string) => void;
  onChange: () => void;
  onError: (e: unknown) => void;
  toast: ReturnType<typeof useToast>;
}) {
  async function removeSnapshot(c: StorageCache, name: string) {
    if (!confirm(`Sicherung „${name}“ vom Cache löschen? Bereits hochgeladene Dateien bleiben in der Cloud.`)) return;
    try {
      await api(`/api/storage/caches/${encodeURIComponent(c.uuid)}/snapshots/${encodeURIComponent(name)}`, { method: "DELETE" });
      toast.success("Sicherung gelöscht", name);
    } catch (e) {
      onError(e);
    } finally {
      onChange();
    }
  }
  async function unmark(c: StorageCache) {
    if (!confirm(`„${c.label}“ nicht mehr als Cache nutzen? Die Sicherungen darauf bleiben erhalten.`)) return;
    try {
      await api(`/api/storage/caches/${encodeURIComponent(c.uuid)}`, { method: "DELETE" });
    } catch (e) {
      onError(e);
    } finally {
      onChange();
    }
  }

  return (
    <div className="mb-6 rounded-2xl bg-slate-800/60 p-5 ring-1 ring-amber-500/20">
      <div className="mb-1 text-sm font-medium text-slate-400">Cache — Kartensicherungen</div>
      <p className="mb-4 text-xs text-slate-500">
        Karte auf den Cache sichern, auswerfen, weiterfotografieren. Ausgewählt und hochgeladen wird später aus der Sicherung.
      </p>
      <div className="grid gap-4">
        {caches.map((c) => (
          <div key={c.uuid}>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <span className="font-semibold text-white">{c.label}</span>
                {c.device ? (
                  <span className="text-xs text-slate-500">
                    {c.device}
                    {c.mountpoint ? ` · ${formatBytes(c.free)} frei von ${formatBytes(c.total)}` : ""}
                  </span>
                ) : null}
                {c.error && <Badge tone="amber">{c.error}</Badge>}
              </div>
              <button className={btn} onClick={() => unmark(c)}>
                Nicht mehr als Cache nutzen
              </button>
            </div>
            {c.mountpoint && c.total > 0 && <UsageBar used={c.total - c.free} total={c.total} />}
            {c.snapshots.length === 0 && c.mountpoint && (
              <p className="mt-3 text-sm text-slate-500">Noch keine Sicherungen. Bei einer Karte auf „Auf Cache sichern“ tippen.</p>
            )}
            <div className="mt-3 grid gap-2">
              {c.snapshots.map((s) => (
                <div key={s.name} className="flex flex-wrap items-center gap-3 rounded-xl bg-slate-900/50 px-4 py-2.5 ring-1 ring-white/5">
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-medium text-white">{s.name}</div>
                    <div className="text-xs text-slate-400">
                      {s.files} Dateien · {formatBytes(s.size)}
                      {s.modified && ` · zuletzt ${snapFmt.format(new Date(s.modified))}`}
                    </div>
                  </div>
                  <button className={btnPrimary} disabled={!c.device} onClick={() => c.device && onOpen(c.device, `Cache · ${s.name}`, s.path)}>
                    Öffnen
                  </button>
                  <button className={btn} onClick={() => removeSnapshot(c, s.name)}>
                    Löschen
                  </button>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function BackupModal({
  partition,
  caches,
  onClose,
  onDone,
}: {
  partition: StoragePartition;
  caches: StorageCache[];
  onClose: () => void;
  onDone: (job: StorageImportJob) => void;
}) {
  const [cacheUuid, setCacheUuid] = useState(caches[0]?.uuid ?? "");
  const [onlyNew, setOnlyNew] = useState(true);
  const [mediaOnly, setMediaOnly] = useState(true);
  const [eject, setEject] = useState(true);
  const [running, setRunning] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit() {
    setRunning(true);
    setErr(null);
    try {
      const job = await api<StorageImportJob>("/api/storage/caches/backup", {
        method: "POST",
        body: JSON.stringify({
          device: partition.path,
          cache_uuid: cacheUuid || null,
          only_new: onlyNew,
          media_only: mediaOnly,
          eject_after: eject,
        }),
      });
      onDone(job);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Fehler");
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/60 p-4" onClick={running ? undefined : onClose}>
      <div className="w-full max-w-lg rounded-2xl bg-slate-900 p-5 ring-1 ring-white/10" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-lg font-semibold text-white">„{partTitle(partition)}“ auf Cache sichern</h3>
        <p className="mt-1 text-sm text-slate-400">
          Kopiert die Karte samt Ordnerstruktur und Aufnahmezeit auf den Cache. Danach kann sie sofort wieder in die Kamera.
        </p>
        {caches.length > 1 && (
          <label className="mt-4 block text-sm text-slate-300">
            Ziel
            <select
              value={cacheUuid}
              onChange={(e) => setCacheUuid(e.target.value)}
              className="mt-1 w-full rounded-lg bg-slate-800 px-3 py-2 text-sm text-white ring-1 ring-white/10"
            >
              {caches.map((c) => (
                <option key={c.uuid} value={c.uuid}>
                  {c.label} ({formatBytes(c.free)} frei)
                </option>
              ))}
            </select>
          </label>
        )}
        <div className="mt-4 grid gap-2 text-sm text-slate-200">
          <label className="flex items-start gap-2">
            <input type="checkbox" className="mt-1" checked={mediaOnly} onChange={(e) => setMediaOnly(e.target.checked)} />
            Nur Fotos, Videos und Audio
          </label>
          <label className="flex items-start gap-2">
            <input type="checkbox" className="mt-1" checked={onlyNew} onChange={(e) => setOnlyNew(e.target.checked)} />
            Nur Neues — schon hochgeladene Dateien nicht noch einmal sichern
          </label>
          <label className="flex items-start gap-2">
            <input type="checkbox" className="mt-1" checked={eject} onChange={(e) => setEject(e.target.checked)} />
            Danach auswerfen (nur wenn alles fehlerfrei kopiert wurde)
          </label>
        </div>
        {err && <div className="mt-3 rounded-lg bg-red-500/15 px-3 py-2 text-sm text-red-300">{err}</div>}
        <div className="mt-5 flex justify-end gap-2">
          <button className={btn} onClick={onClose} disabled={running}>
            Abbrechen
          </button>
          <button className={btnPrimary} onClick={submit} disabled={running}>
            {running ? "Starte …" : "Sichern"}
          </button>
        </div>
      </div>
    </div>
  );
}
