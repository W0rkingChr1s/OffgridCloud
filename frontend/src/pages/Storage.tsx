import { useCallback, useEffect, useMemo, useState } from "react";
import {
  api,
  ApiError,
  type Folder,
  type StorageBrowse,
  type StorageDisk,
  type StorageImportJob,
  type StorageOverview,
  type StoragePartition,
} from "../api";
import Layout from "../components/Layout";
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
  const [browsing, setBrowsing] = useState<StoragePartition | null>(null);

  const report = useCallback((e: unknown) => {
    setError(e instanceof ApiError ? e.message : "Fehler");
  }, []);

  const load = useCallback(() => {
    api<StorageOverview>("/api/storage").then(setOverview).catch(report);
  }, [report]);
  const loadJobs = useCallback(() => {
    api<StorageImportJob[]>("/api/storage/imports").then(setJobs).catch(() => {});
  }, []);

  useEffect(() => {
    load();
    loadJobs();
    // Poll so plugged-in / removed media show up without a reload.
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [load, loadJobs]);

  const active = jobs.some((j) => j.status === "pending" || j.status === "running");
  useEffect(() => {
    const t = setInterval(loadJobs, active ? 1500 : 6000);
    return () => clearInterval(t);
  }, [active, loadJobs]);

  // Close the browser if its medium vanished or got unmounted.
  useEffect(() => {
    if (!browsing || !overview) return;
    const still = overview.disks.flatMap((d) => d.partitions).find((p) => p.path === browsing.path);
    if (!still || !still.browsable) setBrowsing(null);
  }, [overview, browsing]);

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
                    disabled={!manage || busy !== null || d.read_only}
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
                    browsing?.path === p.path ? "ring-ogc-teal/50" : "ring-white/5"
                  }`}
                >
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-medium text-white">{partTitle(p)}</span>
                        <span className="text-xs text-slate-500">
                          {p.path} · {p.fstype || "kein Dateisystem"} · {formatBytes(p.size)}
                        </span>
                        {p.busy && <Badge tone="teal">Import läuft</Badge>}
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
                              onClick={() => setBrowsing(browsing?.path === p.path ? null : p)}
                            >
                              {browsing?.path === p.path ? "Schließen" : "Durchsuchen"}
                            </button>
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
                        {p.path !== d.path && (
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
                  {browsing?.path === p.path && (
                    <Browser
                      partition={p}
                      onStarted={() => {
                        loadJobs();
                        load();
                      }}
                    />
                  )}
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>

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

// --- File browser + import ---------------------------------------------------

function Browser({ partition, onStarted }: { partition: StoragePartition; onStarted: () => void }) {
  const toast = useToast();
  const [path, setPath] = useState("");
  const [listing, setListing] = useState<StorageBrowse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  // Marked paths -> whether each is a directory (imported recursively).
  const [selected, setSelected] = useState<Map<string, boolean>>(new Map());
  const [folders, setFolders] = useState<Folder[]>([]);
  const [folderId, setFolderId] = useState<number | "">("");
  const [mediaOnly, setMediaOnly] = useState(true);
  const [skipDupes, setSkipDupes] = useState(true);
  const [tags, setTags] = useState("");
  const [starting, setStarting] = useState(false);

  useEffect(() => {
    api<Folder[]>("/api/folders")
      .then((f) => {
        setFolders(f);
        if (f.length === 1) setFolderId(f[0].id);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    setErr(null);
    api<StorageBrowse>(
      `/api/storage/browse?device=${encodeURIComponent(partition.path)}&path=${encodeURIComponent(path)}`,
    )
      .then(setListing)
      .catch((e) => setErr(e instanceof ApiError ? e.message : "Fehler"));
  }, [partition.path, path]);

  const entries = useMemo(
    () => (listing?.entries ?? []).filter((e) => e.is_dir || !mediaOnly || e.is_media),
    [listing, mediaOnly],
  );
  const allHere = entries.length > 0 && entries.every((e) => selected.has(e.path));

  function toggle(p: string, isDir: boolean) {
    setSelected((prev) => {
      const next = new Map(prev);
      if (next.has(p)) next.delete(p);
      else next.set(p, isDir);
      return next;
    });
  }
  function toggleAll() {
    setSelected((prev) => {
      const next = new Map(prev);
      for (const e of entries) {
        if (allHere) next.delete(e.path);
        else next.set(e.path, e.is_dir);
      }
      return next;
    });
  }

  async function startImport() {
    if (folderId === "") return;
    setStarting(true);
    setErr(null);
    try {
      const job = await api<StorageImportJob>("/api/storage/imports", {
        method: "POST",
        body: JSON.stringify({
          device: partition.path,
          paths: [...selected.keys()],
          folder_id: folderId,
          media_only: mediaOnly,
          skip_duplicates: skipDupes,
          tags: tags
            .split(",")
            .map((t) => t.trim())
            .filter(Boolean),
        }),
      });
      toast.success("Import gestartet", `${job.total_files} Datei(en), ${formatBytes(job.total_bytes)} → „${job.folder_name}“`);
      setSelected(new Map());
      onStarted();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Fehler");
    } finally {
      setStarting(false);
    }
  }

  const crumbs = path ? path.split("/") : [];
  const selectedDirs = [...selected.values()].filter(Boolean).length;

  return (
    <div className="mt-4 border-t border-white/5 pt-4">
      <div className="mb-3 flex flex-wrap items-center gap-1 text-sm">
        <button className="text-ogc-teal hover:underline" onClick={() => setPath("")}>
          {partTitle(partition)}
        </button>
        {crumbs.map((c, i) => (
          <span key={i} className="flex items-center gap-1">
            <span className="text-slate-600">/</span>
            <button
              className="text-ogc-teal hover:underline"
              onClick={() => setPath(crumbs.slice(0, i + 1).join("/"))}
            >
              {c}
            </button>
          </span>
        ))}
        <label className="ml-auto flex items-center gap-2 text-xs text-slate-300">
          <input type="checkbox" checked={mediaOnly} onChange={(e) => setMediaOnly(e.target.checked)} />
          Nur Fotos/Videos/Audio
        </label>
      </div>

      {err && <div className="mb-3 rounded-lg bg-red-500/15 px-3 py-2 text-sm text-red-300">{err}</div>}

      <div className="max-h-96 overflow-y-auto rounded-lg ring-1 ring-white/5">
        <table className="w-full text-sm">
          <thead className="sticky top-0 bg-slate-900 text-left text-xs text-slate-400">
            <tr>
              <th className="w-8 px-3 py-2">
                <input type="checkbox" checked={allHere} onChange={toggleAll} aria-label="Alle markieren" />
              </th>
              <th className="px-2 py-2">Name</th>
              <th className="hidden px-2 py-2 text-right sm:table-cell">Größe</th>
              <th className="hidden px-3 py-2 text-right sm:table-cell">Geändert</th>
            </tr>
          </thead>
          <tbody>
            {listing?.parent !== null && listing?.parent !== undefined && (
              <tr className="border-t border-white/5">
                <td />
                <td colSpan={3} className="px-2 py-1.5">
                  <button className="text-slate-300 hover:text-white" onClick={() => setPath(listing.parent ?? "")}>
                    ↑ ..
                  </button>
                </td>
              </tr>
            )}
            {entries.map((e) => (
              <tr key={e.path} className={`border-t border-white/5 ${selected.has(e.path) ? "bg-ogc-teal/10" : ""}`}>
                <td className="px-3 py-1.5">
                  <input type="checkbox" checked={selected.has(e.path)} onChange={() => toggle(e.path, e.is_dir)} />
                </td>
                <td className="max-w-0 truncate px-2 py-1.5">
                  {e.is_dir ? (
                    <button className="text-white hover:text-ogc-teal" onClick={() => setPath(e.path)}>
                      📁 {e.name}
                    </button>
                  ) : (
                    <span className={e.is_media ? "text-slate-200" : "text-slate-400"}>{e.name}</span>
                  )}
                </td>
                <td className="hidden whitespace-nowrap px-2 py-1.5 text-right text-slate-400 sm:table-cell">
                  {e.is_dir ? "" : formatBytes(e.size)}
                </td>
                <td className="hidden whitespace-nowrap px-3 py-1.5 text-right text-slate-500 sm:table-cell">
                  {e.modified ? new Date(e.modified).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" }) : ""}
                </td>
              </tr>
            ))}
            {listing && entries.length === 0 && (
              <tr>
                <td colSpan={4} className="px-3 py-6 text-center text-slate-500">
                  {mediaOnly ? "Keine Mediendateien in diesem Ordner." : "Ordner ist leer."}
                </td>
              </tr>
            )}
          </tbody>
        </table>
        {listing?.truncated && (
          <div className="px-3 py-2 text-xs text-amber-200">Nur die ersten 2000 Einträge angezeigt.</div>
        )}
      </div>

      <div className="mt-4 grid gap-3 rounded-xl bg-slate-800/50 p-4 sm:grid-cols-2">
        <div className="sm:col-span-2 text-sm text-slate-300">
          {selected.size === 0 ? (
            "Dateien oder Ordner markieren, um sie für den Upload zu übernehmen. Ordner werden komplett (inkl. Unterordner) übernommen."
          ) : (
            <>
              <span className="font-semibold text-white">{selected.size}</span> markiert
              {selectedDirs > 0 && ` (davon ${selectedDirs} Ordner)`}
              <button className="ml-3 text-xs text-slate-400 hover:text-white" onClick={() => setSelected(new Map())}>
                Auswahl aufheben
              </button>
            </>
          )}
        </div>
        <label className="text-xs text-slate-400">
          Ziel-Ordner
          <select
            value={folderId}
            onChange={(e) => setFolderId(e.target.value ? Number(e.target.value) : "")}
            className="mt-1 w-full rounded-lg bg-slate-900 px-3 py-2 text-sm text-white ring-1 ring-white/10"
          >
            <option value="">— wählen —</option>
            {folders.map((f) => (
              <option key={f.id} value={f.id}>
                {f.name}
              </option>
            ))}
          </select>
        </label>
        <label className="text-xs text-slate-400">
          Tags (optional, kommagetrennt)
          <input
            value={tags}
            onChange={(e) => setTags(e.target.value)}
            placeholder="z. B. drohne, tag-2"
            className="mt-1 w-full rounded-lg bg-slate-900 px-3 py-2 text-sm text-white ring-1 ring-white/10"
          />
        </label>
        <label className="flex items-center gap-2 text-xs text-slate-300">
          <input type="checkbox" checked={skipDupes} onChange={(e) => setSkipDupes(e.target.checked)} />
          Bereits übernommene Dateien überspringen
        </label>
        <div className="flex justify-end">
          <button
            className={btnPrimary + " px-4 py-2 text-sm"}
            disabled={selected.size === 0 || folderId === "" || starting}
            onClick={startImport}
          >
            {starting ? "Starte …" : "Für Upload übernehmen"}
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
        <div className="text-sm font-medium text-slate-400">Importe</div>
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
                  <span className="font-medium text-white">{j.device}</span>
                  <span className="text-slate-400"> → „{j.folder_name}“</span>
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
              <UsageBar used={j.done_bytes} total={j.total_bytes || 1} />
              <div className="mt-1.5 text-xs text-slate-400">
                {j.done_files}/{j.total_files} Dateien · {formatBytes(j.done_bytes)} / {formatBytes(j.total_bytes)} ({pct}%)
                {" · "}
                {j.imported} übernommen
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
