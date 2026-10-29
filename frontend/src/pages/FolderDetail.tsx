import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  api,
  ApiError,
  type Folder,
  getToken,
  type MediaBulkDeleteResult,
  type MediaDeleteResult,
  type MediaDescription,
  type MediaItem,
} from "../api";
import Layout from "../components/Layout";
import MoreMenu from "../components/MoreMenu";
import FileBrowser from "../components/FileBrowser";
import { type SortOption } from "../components/Sort";
import { useToast } from "../toast";
import { TagEditor } from "../components/Tags";
import { DescriptionModal, DescriptionsList } from "../components/Descriptions";
import { formatBytes, uploadFile } from "../upload";

type DescModalState =
  | { mode: "create"; mediaIds: number[] }
  | { mode: "edit"; existing: MediaDescription };

const MEDIA_SORT: SortOption<MediaItem>[] = [
  { key: "name", label: "Datei", get: (m) => m.filename },
  { key: "size", label: "Größe", get: (m) => m.size },
  { key: "status", label: "Status", get: (m) => m.status },
  { key: "created", label: "Hochgeladen", get: (m) => m.created_at },
];

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

const dateFmt = new Intl.DateTimeFormat("de-DE", { dateStyle: "short" });
const dateTimeFmt = new Intl.DateTimeFormat("de-DE", { dateStyle: "short", timeStyle: "short" });

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
    <span className={`inline-block whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ${tone}`}>
      {STATUS_LABELS[status] ?? status}
    </span>
  );
}

function Thumb({ id, big = false }: { id: number; big?: boolean }) {
  const [failed, setFailed] = useState(false);
  const box = big ? "h-full w-full" : "h-11 w-16 rounded-lg";
  if (failed) {
    return <div className={`flex items-center justify-center bg-slate-700/60 text-slate-400 ${box}`}>▦</div>;
  }
  return (
    <img
      src={`/api/media/${id}/thumbnail?token=${encodeURIComponent(getToken() ?? "")}`}
      alt=""
      loading="lazy"
      decoding="async"
      className={`object-cover ${box} ${big ? "" : "ring-1 ring-white/10"}`}
      onError={() => setFailed(true)}
    />
  );
}

/** Placeholder for a file whose local copy is gone (only in the cloud now). */
function Gone({ big = false }: { big?: boolean }) {
  return (
    <div
      className={`flex items-center justify-center bg-slate-700/60 text-xs text-slate-500 ${big ? "h-full w-full" : "h-11 w-16 rounded-lg"}`}
      title="Lokale Kopie entfernt"
    >
      entf.
    </div>
  );
}

const mediaKey = (m: MediaItem) => String(m.id);
const mediaName = (m: MediaItem) => m.filename;
const isAvailable = (m: MediaItem) => !m.local_deleted;

interface UploadRow {
  name: string;
  size: number;
  progress: number;
  error?: string;
  done?: boolean;
  /** Set while a stalled/failed chunk is being retried. */
  retry?: string;
}

export default function FolderDetail() {
  const { id } = useParams();
  const folderId = Number(id);
  const [folder, setFolder] = useState<Folder | null>(null);
  const [media, setMedia] = useState<MediaItem[]>([]);
  const [descriptions, setDescriptions] = useState<MediaDescription[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [uploads, setUploads] = useState<UploadRow[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [busyId, setBusyId] = useState<number | null>(null);
  const [bulkBusy, setBulkBusy] = useState(false);
  const [descModal, setDescModal] = useState<DescModalState | null>(null);
  // Optional description typed before an upload — applied to that whole batch.
  const [descTitle, setDescTitle] = useState("");
  const [descBody, setDescBody] = useState("");
  // The composer stays folded until asked for (or while it still holds text).
  const [descOpen, setDescOpen] = useState(false);
  // Read the latest composer text from inside the async upload loop.
  const descTitleRef = useRef("");
  const descBodyRef = useRef("");
  descTitleRef.current = descTitle;
  descBodyRef.current = descBody;
  const inputRef = useRef<HTMLInputElement>(null);
  const toast = useToast();

  const loadMedia = useCallback(() => {
    api<MediaItem[]>(`/api/folders/${folderId}/media`)
      .then(setMedia)
      .catch((e) => setError(e instanceof ApiError ? e.message : "Fehler"));
  }, [folderId]);

  const loadDescriptions = useCallback(() => {
    api<MediaDescription[]>(`/api/folders/${folderId}/descriptions`)
      .then(setDescriptions)
      .catch(() => setDescriptions([]));
  }, [folderId]);

  useEffect(() => {
    api<Folder[]>("/api/folders")
      .then((fs) => setFolder(fs.find((f) => f.id === folderId) ?? null))
      .catch(() => setFolder(null));
    loadMedia();
    loadDescriptions();
  }, [folderId, loadMedia, loadDescriptions]);

  // filename lookup for description cards / modals.
  const filenames = useMemo(() => {
    const map: Record<number, string> = {};
    for (const m of media) map[m.id] = m.filename;
    return map;
  }, [media]);

  const patchTags = useCallback((mediaId: number, tags: string[]) => {
    setMedia((prev) => prev.map((m) => (m.id === mediaId ? { ...m, tags } : m)));
  }, []);

  // Files whose local copy is still present (downloadable / selectable).
  const available = useMemo(() => media.filter((m) => !m.local_deleted), [media]);
  const availableIds = useMemo(() => available.map((m) => m.id), [available]);
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

  const selectedKeys = useMemo(() => new Set([...selected].map(String)), [selected]);

  const handleFiles = useCallback(
    async (files: FileList | File[]) => {
      const list = Array.from(files);
      const uploadedIds: number[] = [];
      for (const file of list) {
        const row: UploadRow = { name: file.name, size: file.size, progress: 0 };
        setUploads((prev) => [row, ...prev]);
        const update = (patch: Partial<UploadRow>) =>
          setUploads((prev) => prev.map((r) => (r === row ? Object.assign(row, patch) : r)));
        try {
          const item = await uploadFile(folderId, file, {
            onProgress: (frac) => update({ progress: frac }),
            onRetry: (info) =>
              update({
                retry: info
                  ? `${info.reason} – neuer Versuch ${info.attempt}/${info.max} …`
                  : undefined,
              }),
          });
          uploadedIds.push(item.id);
          update({ progress: 1, done: true, retry: undefined });
          toast.push({
            variant: "success",
            title: "Upload fertig",
            message: `„${file.name}" ist da – Cloud-Transfer startet.`,
            os: true,
          });
        } catch (e) {
          const msg = e instanceof ApiError ? e.message : "Upload fehlgeschlagen";
          update({ error: msg, retry: undefined });
          toast.push({
            variant: "error",
            title: "Upload fehlgeschlagen",
            message: `„${file.name}": ${msg}`,
            os: true,
          });
        }
      }

      // If a description was entered for this batch, generate its cloud sidecar.
      const body = descBodyRef.current.trim();
      if (body && uploadedIds.length) {
        try {
          await api<MediaDescription>(`/api/folders/${folderId}/descriptions`, {
            method: "POST",
            body: JSON.stringify({
              title: descTitleRef.current.trim(),
              body,
              media_ids: uploadedIds,
            }),
          });
          setDescTitle("");
          setDescBody("");
          toast.push({
            variant: "success",
            title: "Beschreibung gespeichert",
            message: "Eine Textdatei wird mit in die Cloud geladen.",
          });
          loadDescriptions();
        } catch (e) {
          toast.push({
            variant: "error",
            title: "Beschreibung fehlgeschlagen",
            message: e instanceof ApiError ? e.message : "Konnte nicht gespeichert werden.",
          });
        }
      }
      loadMedia();
    },
    [folderId, loadMedia, loadDescriptions, toast],
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

  const deleteItem = (m: MediaItem) => ({
    label: busyId === m.id ? "Lösche …" : "Löschen …",
    danger: true,
    disabled: busyId !== null,
    onClick: () => remove(m),
  });

  return (
    <Layout>
      <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
        <div>
          <Link to="/folders" className="text-sm text-slate-400 hover:text-white">
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

      {/* Optional description composer — applies to the next uploaded batch. */}
      {descOpen || descTitle || descBody ? (
        <div className="mb-3 rounded-2xl bg-slate-800/40 p-3 ring-1 ring-white/5">
          <div className="mb-2 flex items-center justify-between gap-3 text-xs text-slate-400">
            <span>Beschreibung für die nächsten Uploads – wird als Textdatei mit in die Cloud geladen.</span>
            <button
              type="button"
              onClick={() => {
                setDescTitle("");
                setDescBody("");
                setDescOpen(false);
              }}
              className="shrink-0 hover:text-white"
            >
              Entfernen
            </button>
          </div>
          <input
            value={descTitle}
            onChange={(e) => setDescTitle(e.target.value)}
            maxLength={200}
            placeholder="Thema (optional) – z. B. Bootsfahrt am Morgen"
            className="w-full rounded-lg bg-slate-800 px-3 py-2 text-sm text-white outline-hidden ring-1 ring-white/10 focus:ring-ogc-teal/50"
          />
          <textarea
            value={descBody}
            onChange={(e) => setDescBody(e.target.value)}
            rows={2}
            maxLength={20000}
            placeholder="Was ist auf den Fotos/Videos zu sehen?"
            className="mt-2 w-full resize-y rounded-lg bg-slate-800 px-3 py-2 text-sm text-white outline-hidden ring-1 ring-white/10 focus:ring-ogc-teal/50"
          />
        </div>
      ) : null}

      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
        onClick={() => inputRef.current?.click()}
        className={`mb-6 cursor-pointer rounded-2xl border-2 border-dashed px-6 py-6 text-center transition ${
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
        {!(descOpen || descTitle || descBody) && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setDescOpen(true);
            }}
            className="mt-1 text-xs text-slate-500 hover:text-ogc-teal"
          >
            + Beschreibung für diesen Upload
          </button>
        )}
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
                  className={`h-full rounded-full ${u.error ? "bg-red-500" : "bg-linear-to-r from-ogc-teal to-ogc-blue"}`}
                  style={{ width: `${Math.round(u.progress * 100)}%` }}
                />
              </div>
              {u.retry && !u.error && <div className="mt-1 text-xs text-amber-300">{u.retry}</div>}
              {u.error && <div className="mt-1 text-xs text-red-300">{u.error}</div>}
            </div>
          ))}
        </div>
      )}

      <DescriptionsList
        descriptions={descriptions}
        filenames={filenames}
        onEdit={(d) => setDescModal({ mode: "edit", existing: d })}
        onDeleted={() => loadDescriptions()}
      />

      <FileBrowser
        items={media}
        getKey={mediaKey}
        getName={mediaName}
        isSelectable={isAvailable}
        selected={selectedKeys}
        onSelectedChange={(next) => setSelected(new Set([...next].map(Number)))}
        onOpen={(m) => !m.local_deleted && triggerDownload(downloadUrl(m.id))}
        sortOptions={MEDIA_SORT}
        initialSort={{ key: "created", dir: "desc" }}
        storageKey="folder-media"
        resetKey={folderId}
        header={
          <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-400">Medien ({media.length})</h3>
        }
        emptyText="Noch keine Dateien in diesem Ordner."
        renderIcon={(m) => (m.local_deleted ? <Gone /> : <Thumb id={m.id} />)}
        renderTile={(m) => (m.local_deleted ? <Gone big /> : <Thumb id={m.id} big />)}
        renderTileBadge={(m) => <StatusPill status={m.status} />}
        renderSubtitle={(m) => (
          <>
            <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-slate-400 lg:hidden">
              <span>{formatBytes(m.size)}</span>
              <span aria-hidden>·</span>
              <span>{dateFmt.format(new Date(m.created_at))}</span>
              <span className="md:hidden">
                <StatusPill status={m.status} />
              </span>
            </div>
            <div className="mt-1" data-no-row-click>
              <TagEditor mediaId={m.id} tags={m.tags} quietAdd onChange={(t) => patchTags(m.id, t)} />
            </div>
          </>
        )}
        columns={[
          {
            key: "size",
            label: "Größe",
            sortKey: "size",
            align: "right",
            className: "hidden w-20 text-slate-300 lg:block",
            render: (m) => formatBytes(m.size),
          },
          {
            key: "status",
            label: "Status",
            sortKey: "status",
            className: "hidden w-32 md:block",
            render: (m) => <StatusPill status={m.status} />,
          },
          {
            key: "created",
            label: "Hochgeladen",
            sortKey: "created",
            align: "right",
            className: "hidden w-32 text-slate-400 lg:block",
            render: (m) => dateTimeFmt.format(new Date(m.created_at)),
          },
        ]}
        renderActions={(m) => (
          <div className="flex items-center gap-2">
            {!m.local_deleted && (
              <a
                href={downloadUrl(m.id)}
                download
                aria-label={`${m.filename} herunterladen`}
                className="hidden rounded-lg border border-white/10 px-2.5 py-1.5 text-xs leading-none text-slate-300 hover:bg-white/5 sm:inline-block"
              >
                ↓
              </a>
            )}
            <MoreMenu
              items={[
                ...(m.local_deleted
                  ? []
                  : [{ label: "Herunterladen", onClick: () => triggerDownload(downloadUrl(m.id)) }]),
                deleteItem(m),
              ]}
            />
          </div>
        )}
      />

      {/* Sticky bulk-action bar — appears while items are selected. */}
      {selectedIds.length > 0 && (
        <div className="pointer-events-none fixed inset-x-0 bottom-4 z-20 flex justify-center px-4">
          <div className="pointer-events-auto flex flex-wrap items-center gap-3 rounded-2xl bg-slate-900/95 px-4 py-3 shadow-xl ring-1 ring-white/15 backdrop-blur-sm">
            <span className="text-sm text-slate-200">
              {selectedIds.length} ausgewählt
            </span>
            <button
              onClick={() => setDescModal({ mode: "create", mediaIds: selectedIds })}
              disabled={bulkBusy}
              className="rounded-lg border border-white/15 px-3 py-1.5 text-sm text-slate-200 hover:bg-white/5 disabled:opacity-50"
            >
              Beschreiben
            </button>
            <button
              onClick={downloadSelected}
              disabled={bulkBusy}
              className="rounded-lg bg-linear-to-r from-ogc-teal to-ogc-blue px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
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

      {descModal && (
        <DescriptionModal
          folderId={folderId}
          existing={descModal.mode === "edit" ? descModal.existing : null}
          mediaIds={descModal.mode === "create" ? descModal.mediaIds : []}
          filenames={filenames}
          onClose={() => setDescModal(null)}
          onSaved={() => {
            setDescModal(null);
            if (descModal.mode === "create") setSelected(new Set());
            loadDescriptions();
            loadMedia();
          }}
        />
      )}
    </Layout>
  );
}
