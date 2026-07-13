import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  api,
  ApiError,
  type Folder,
  getToken,
  type MediaBulkDeleteResult,
  type MediaDeleteResult,
  type MediaItem,
} from "../api";
import Layout from "../components/Layout";
import { TagEditor } from "../components/Tags";
import { formatBytes, uploadFile } from "../upload";

function downloadUrl(id: number): string {
  return `/api/media/${id}/download?token=${encodeURIComponent(getToken() ?? "")}`;
}

function bulkDownloadUrl(folderId: number, ids?: number[]): string {
  const token = encodeURIComponent(getToken() ?? "");
  const idParam = ids && ids.length ? `ids=${ids.join(",")}&` : "";
  return `/api/folders/${folderId}/download?${idParam}token=${token}`;
}

/** Trigger a browser download without navigating away from the SPA. */
function triggerDownload(url: string) {
  const a = document.createElement("a");
  a.href = url;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

const STATUS_LABELS: Record<string, string> = {
  received: "empfangen",
  queued: "in Warteschlange",
  uploading: "wird übertragen",
  verified: "geprüft",
  done: "gesichert",
  failed: "Fehler",
};

function StatusPill({ status }: { status: string }) {
  const tone =
    status === "done" || status === "verified"
      ? "bg-emerald-500/15 text-emerald-300"
      : status === "failed"
        ? "bg-red-500/15 text-red-300"
        : status === "uploading"
          ? "bg-ogc-blue/20 text-sky-300"
          : "bg-ogc-teal/15 text-ogc-teal";
  return (
    <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${tone}`}>
      {STATUS_LABELS[status] ?? status}
    </span>
  );
}

function Thumb({ id }: { id: number }) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return (
      <div className="flex h-11 w-16 items-center justify-center rounded-lg bg-slate-700/60 text-slate-400">
        ▦
      </div>
    );
  }
  return (
    <img
      src={`/api/media/${id}/thumbnail?token=${encodeURIComponent(getToken() ?? "")}`}
      alt=""
      loading="lazy"
      className="h-11 w-16 rounded-lg object-cover ring-1 ring-white/10"
      onError={() => setFailed(true)}
    />
  );
}

interface UploadRow {
  name: string;
  size: number;
  progress: number;
  error?: string;
  done?: boolean;
}

export default function FolderDetail() {
  const { id } = useParams();
  const folderId = Number(id);
  const [folder, setFolder] = useState<Folder | null>(null);
  const [media, setMedia] = useState<MediaItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [uploads, setUploads] = useState<UploadRow[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [busyId, setBusyId] = useState<number | null>(null);
  const [bulkBusy, setBulkBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const loadMedia = useCallback(() => {
    api<MediaItem[]>(`/api/folders/${folderId}/media`)
      .then(setMedia)
      .catch((e) => setError(e instanceof ApiError ? e.message : "Fehler"));
  }, [folderId]);

  useEffect(() => {
    api<Folder[]>("/api/folders")
      .then((fs) => setFolder(fs.find((f) => f.id === folderId) ?? null))
      .catch(() => setFolder(null));
    loadMedia();
  }, [folderId, loadMedia]);

  const patchTags = useCallback((mediaId: number, tags: string[]) => {
    setMedia((prev) => prev.map((m) => (m.id === mediaId ? { ...m, tags } : m)));
  }, []);

  // Files whose local copy is still present (downloadable / selectable).
  const available = useMemo(() => media.filter((m) => !m.local_deleted), [media]);
  const availableIds = useMemo(() => available.map((m) => m.id), [available]);
  const allSelected = availableIds.length > 0 && availableIds.every((id) => selected.has(id));
  const selectedIds = useMemo(
    () => availableIds.filter((id) => selected.has(id)),
    [availableIds, selected],
  );

  // Drop ids that vanished (e.g. after a delete) from the selection.
  useEffect(() => {
    setSelected((prev) => {
      const next = new Set([...prev].filter((id) => availableIds.includes(id)));
      return next.size === prev.size ? prev : next;
    });
  }, [availableIds]);

  const toggle = useCallback((mid: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(mid)) next.delete(mid);
      else next.add(mid);
      return next;
    });
  }, []);

  const toggleAll = useCallback(() => {
    setSelected((prev) => (prev.size >= availableIds.length ? new Set() : new Set(availableIds)));
  }, [availableIds]);

  const handleFiles = useCallback(
    async (files: FileList | File[]) => {
      const list = Array.from(files);
      for (const file of list) {
        const row: UploadRow = { name: file.name, size: file.size, progress: 0 };
        setUploads((prev) => [row, ...prev]);
        const update = (patch: Partial<UploadRow>) =>
          setUploads((prev) => prev.map((r) => (r === row ? Object.assign(row, patch) : r)));
        try {
          await uploadFile(folderId, file, (frac) => update({ progress: frac }));
          update({ progress: 1, done: true });
        } catch (e) {
          update({ error: e instanceof ApiError ? e.message : "Upload fehlgeschlagen" });
        }
      }
      loadMedia();
    },
    [folderId, loadMedia],
  );

  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragOver(false);
    if (e.dataTransfer.files.length) handleFiles(e.dataTransfer.files);
  }

  const remove = useCallback(
    async (m: MediaItem) => {
      if (!window.confirm(`„${m.filename}" wirklich löschen?`)) return;
      setBusyId(m.id);
      setError(null);
      setNotice(null);
      try {
        const res = await api<MediaDeleteResult>(
          `/api/folders/${folderId}/media/${m.id}`,
          { method: "DELETE" },
        );
        if (res.remote_errors.length) {
          setError(`Remote nicht überall gelöscht: ${res.remote_errors.join("; ")}`);
        } else if (res.remote_deleted > 0) {
          setNotice(`Gelöscht – auch bei ${res.remote_deleted} Ziel(en) entfernt.`);
        }
        loadMedia();
      } catch (e) {
        setError(e instanceof ApiError ? e.message : "Löschen fehlgeschlagen");
      } finally {
        setBusyId(null);
      }
    },
    [folderId, loadMedia],
  );

  const removeSelected = useCallback(async () => {
    if (!selectedIds.length) return;
    if (!window.confirm(`${selectedIds.length} Datei(en) wirklich löschen?`)) return;
    setBulkBusy(true);
    setError(null);
    setNotice(null);
    try {
      const res = await api<MediaBulkDeleteResult>(
        `/api/folders/${folderId}/media/bulk-delete`,
        { method: "POST", body: JSON.stringify({ media_ids: selectedIds }) },
      );
      const parts = [`${res.deleted} Datei(en) gelöscht`];
      if (res.remote_deleted > 0) parts.push(`bei ${res.remote_deleted} Ziel(en) remote entfernt`);
      if (res.remote_errors.length) {
        setError(`Remote nicht überall gelöscht: ${res.remote_errors.join("; ")}`);
      } else {
        setNotice(`${parts.join(" – ")}.`);
      }
      setSelected(new Set());
      loadMedia();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Löschen fehlgeschlagen");
    } finally {
      setBulkBusy(false);
    }
  }, [folderId, selectedIds, loadMedia]);

  const downloadSelected = useCallback(() => {
    if (!selectedIds.length) return;
    triggerDownload(bulkDownloadUrl(folderId, selectedIds));
  }, [folderId, selectedIds]);

  const downloadAll = useCallback(() => {
    if (!availableIds.length) return;
    triggerDownload(bulkDownloadUrl(folderId));
  }, [folderId, availableIds]);

  return (
    <Layout>
      <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
        <div>
          <Link to="/" className="text-sm text-slate-400 hover:text-white">
            ← Ordner
          </Link>
          <h2 className="mt-1 text-2xl font-bold">{folder?.name ?? "Ordner"}</h2>
          {folder?.description && <p className="text-sm text-slate-400">{folder.description}</p>}
        </div>
        {available.length > 0 && (
          <button
            onClick={downloadAll}
            className="rounded-lg border border-white/10 px-3 py-1.5 text-sm text-slate-200 hover:bg-white/5"
          >
            Alle herunterladen (.zip)
          </button>
        )}
      </div>

      {error && (
        <div className="mb-4 rounded-lg bg-red-500/15 px-3 py-2 text-sm text-red-300">{error}</div>
      )}
      {notice && (
        <div className="mb-4 rounded-lg bg-emerald-500/15 px-3 py-2 text-sm text-emerald-300">{notice}</div>
      )}

      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
        onClick={() => inputRef.current?.click()}
        className={`mb-6 cursor-pointer rounded-2xl border-2 border-dashed p-10 text-center transition ${
          dragOver ? "border-ogc-teal bg-ogc-teal/5" : "border-white/15 hover:border-white/30"
        }`}
      >
        <input
          ref={inputRef}
          type="file"
          multiple
          className="hidden"
          onChange={(e) => e.target.files && handleFiles(e.target.files)}
        />
        <div className="text-slate-300">Dateien hierher ziehen oder klicken zum Auswählen</div>
        <div className="mt-1 text-xs text-slate-500">
          Große Videos werden in Teilen hochgeladen und können fortgesetzt werden.
        </div>
      </div>

      {uploads.length > 0 && (
        <div className="mb-6 space-y-2">
          {uploads.map((u, i) => (
            <div key={i} className="rounded-lg bg-slate-800/60 p-3 ring-1 ring-white/5">
              <div className="flex justify-between text-sm">
                <span className="truncate text-slate-200">{u.name}</span>
                <span className="text-slate-400">
                  {u.error ? "Fehler" : u.done ? "fertig" : `${Math.round(u.progress * 100)}%`}
                </span>
              </div>
              <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-700">
                <div
                  className={`h-full rounded-full ${u.error ? "bg-red-500" : "bg-gradient-to-r from-ogc-teal to-ogc-blue"}`}
                  style={{ width: `${Math.round(u.progress * 100)}%` }}
                />
              </div>
              {u.error && <div className="mt-1 text-xs text-red-300">{u.error}</div>}
            </div>
          ))}
        </div>
      )}

      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-400">
          Medien ({media.length})
        </h3>
        {available.length > 0 && (
          <button
            onClick={toggleAll}
            className="text-xs text-slate-400 hover:text-white"
          >
            {allSelected ? "Auswahl aufheben" : "Alle auswählen"}
          </button>
        )}
      </div>

      {media.length === 0 ? (
        <p className="rounded-xl border border-dashed border-white/10 p-8 text-center text-sm text-slate-500">
          Noch keine Dateien in diesem Ordner.
        </p>
      ) : (
        <>
          {/* Mobile: card list */}
          <div className="space-y-2 md:hidden">
            {media.map((m) => {
              const isSel = selected.has(m.id);
              return (
                <div
                  key={m.id}
                  className={`flex items-start gap-3 rounded-xl p-3 ring-1 transition ${
                    isSel ? "bg-ogc-teal/10 ring-ogc-teal/40" : "bg-slate-800/60 ring-white/5"
                  }`}
                >
                  {!m.local_deleted && (
                    <input
                      type="checkbox"
                      checked={isSel}
                      onChange={() => toggle(m.id)}
                      aria-label={`${m.filename} auswählen`}
                      className="mt-1 h-4 w-4 shrink-0 accent-ogc-teal"
                    />
                  )}
                  <div className="shrink-0">
                    {m.local_deleted ? (
                      <div className="flex h-11 w-16 items-center justify-center rounded-lg bg-slate-700/60 text-xs text-slate-500">
                        entf.
                      </div>
                    ) : (
                      <Thumb id={m.id} />
                    )}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium text-white">{m.filename}</div>
                    <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-slate-400">
                      <span>{formatBytes(m.size)}</span>
                      <span aria-hidden>·</span>
                      <span>{new Date(m.created_at).toLocaleDateString()}</span>
                      <span aria-hidden>·</span>
                      <StatusPill status={m.status} />
                    </div>
                    <div className="mt-1.5">
                      <TagEditor mediaId={m.id} tags={m.tags} onChange={(t) => patchTags(m.id, t)} />
                    </div>
                    <div className="mt-1.5 flex items-center gap-3 text-xs">
                      {m.local_deleted ? (
                        <span className="text-slate-500">lokal entfernt</span>
                      ) : (
                        <a href={downloadUrl(m.id)} download className="text-ogc-teal hover:underline">
                          Herunterladen
                        </a>
                      )}
                      <button
                        onClick={() => remove(m)}
                        disabled={busyId === m.id}
                        className="text-red-300 hover:underline disabled:opacity-50"
                      >
                        {busyId === m.id ? "…" : "Löschen"}
                      </button>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          {/* Desktop: table */}
          <div className="hidden overflow-hidden rounded-2xl ring-1 ring-white/10 md:block">
            <table className="w-full text-left text-sm">
              <thead className="bg-slate-800/80 text-slate-400">
                <tr>
                  <th className="w-10 px-4 py-3">
                    <input
                      type="checkbox"
                      checked={allSelected}
                      onChange={toggleAll}
                      aria-label="Alle auswählen"
                      disabled={available.length === 0}
                      className="h-4 w-4 accent-ogc-teal"
                    />
                  </th>
                  <th className="px-4 py-3">Vorschau</th>
                  <th className="px-4 py-3">Datei</th>
                  <th className="px-4 py-3">Größe</th>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3">Tags</th>
                  <th className="px-4 py-3">Hochgeladen</th>
                  <th className="px-4 py-3 text-right">Aktion</th>
                </tr>
              </thead>
              <tbody>
                {media.map((m) => {
                  const isSel = selected.has(m.id);
                  return (
                    <tr
                      key={m.id}
                      className={`border-t border-white/5 transition ${
                        isSel ? "bg-ogc-teal/10" : "bg-slate-900/40 hover:bg-slate-800/40"
                      }`}
                    >
                      <td className="px-4 py-3">
                        {!m.local_deleted && (
                          <input
                            type="checkbox"
                            checked={isSel}
                            onChange={() => toggle(m.id)}
                            aria-label={`${m.filename} auswählen`}
                            className="h-4 w-4 accent-ogc-teal"
                          />
                        )}
                      </td>
                      <td className="px-4 py-3">
                        {m.local_deleted ? (
                          <span className="text-xs text-slate-500">entfernt</span>
                        ) : (
                          <Thumb id={m.id} />
                        )}
                      </td>
                      <td className="px-4 py-3 font-medium text-white">{m.filename}</td>
                      <td className="px-4 py-3 text-slate-300">{formatBytes(m.size)}</td>
                      <td className="px-4 py-3">
                        <StatusPill status={m.status} />
                      </td>
                      <td className="px-4 py-3">
                        <TagEditor mediaId={m.id} tags={m.tags} onChange={(t) => patchTags(m.id, t)} />
                      </td>
                      <td className="px-4 py-3 text-slate-400">
                        {new Date(m.created_at).toLocaleString()}
                      </td>
                      <td className="px-4 py-3 text-right">
                        <div className="flex items-center justify-end gap-2">
                          {!m.local_deleted && (
                            <a
                              href={downloadUrl(m.id)}
                              download
                              className="rounded border border-white/10 px-3 py-1 text-xs hover:bg-white/5"
                            >
                              Download
                            </a>
                          )}
                          <button
                            onClick={() => remove(m)}
                            disabled={busyId === m.id}
                            className="rounded border border-red-500/30 px-3 py-1 text-xs text-red-300 hover:bg-red-500/10 disabled:opacity-50"
                          >
                            {busyId === m.id ? "…" : "Löschen"}
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}

      {/* Sticky bulk-action bar — appears while items are selected. */}
      {selectedIds.length > 0 && (
        <div className="pointer-events-none fixed inset-x-0 bottom-4 z-20 flex justify-center px-4">
          <div className="pointer-events-auto flex flex-wrap items-center gap-3 rounded-2xl bg-slate-900/95 px-4 py-3 shadow-xl ring-1 ring-white/15 backdrop-blur">
            <span className="text-sm text-slate-200">
              {selectedIds.length} ausgewählt
            </span>
            <button
              onClick={downloadSelected}
              disabled={bulkBusy}
              className="rounded-lg bg-gradient-to-r from-ogc-teal to-ogc-blue px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
            >
              Herunterladen (.zip)
            </button>
            <button
              onClick={removeSelected}
              disabled={bulkBusy}
              className="rounded-lg border border-red-500/40 px-3 py-1.5 text-sm text-red-300 hover:bg-red-500/10 disabled:opacity-50"
            >
              {bulkBusy ? "Lösche…" : "Löschen"}
            </button>
            <button
              onClick={() => setSelected(new Set())}
              disabled={bulkBusy}
              className="text-sm text-slate-400 hover:text-white disabled:opacity-50"
            >
              Abbrechen
            </button>
          </div>
        </div>
      )}
    </Layout>
  );
}
