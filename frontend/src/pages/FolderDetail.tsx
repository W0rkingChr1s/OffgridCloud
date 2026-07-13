import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  api,
  ApiError,
  type Folder,
  getToken,
  type MediaDeleteResult,
  type MediaItem,
} from "../api";
import Layout from "../components/Layout";
import { useToast } from "../toast";
import { formatBytes, uploadFile } from "../upload";

function downloadUrl(id: number): string {
  return `/api/media/${id}/download?token=${encodeURIComponent(getToken() ?? "")}`;
}

function Thumb({ id }: { id: number }) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return (
      <div className="flex h-10 w-14 items-center justify-center rounded bg-slate-700/60 text-slate-400">
        ▦
      </div>
    );
  }
  return (
    <img
      src={`/api/media/${id}/thumbnail?token=${encodeURIComponent(getToken() ?? "")}`}
      alt=""
      loading="lazy"
      className="h-10 w-14 rounded object-cover"
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
  const [uploads, setUploads] = useState<UploadRow[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const toast = useToast();

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
          toast.success("Upload fertig", `„${file.name}" ist da – Cloud-Transfer startet.`);
        } catch (e) {
          const msg = e instanceof ApiError ? e.message : "Upload fehlgeschlagen";
          update({ error: msg });
          toast.error("Upload fehlgeschlagen", `„${file.name}": ${msg}`);
        }
      }
      loadMedia();
    },
    [folderId, loadMedia, toast],
  );

  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragOver(false);
    if (e.dataTransfer.files.length) handleFiles(e.dataTransfer.files);
  }

  const [busyId, setBusyId] = useState<number | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

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

  return (
    <Layout>
      <div className="mb-6">
        <Link to="/" className="text-sm text-slate-400 hover:text-white">
          ← Ordner
        </Link>
        <h2 className="mt-1 text-2xl font-bold">{folder?.name ?? "Ordner"}</h2>
        {folder?.description && <p className="text-sm text-slate-400">{folder.description}</p>}
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

      <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-400">
        Medien ({media.length})
      </h3>
      {media.length === 0 ? (
        <p className="text-sm text-slate-500">Noch keine Dateien in diesem Ordner.</p>
      ) : (
        <>
          {/* Mobile: card list */}
          <div className="space-y-2 md:hidden">
            {media.map((m) => (
              <div key={m.id} className="flex items-center gap-3 rounded-xl bg-slate-800/60 p-3 ring-1 ring-white/5">
                <div className="shrink-0">
                  {m.local_deleted ? (
                    <div className="flex h-10 w-14 items-center justify-center rounded bg-slate-700/60 text-xs text-slate-500">gel.</div>
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
                    <span className="text-ogc-teal">{m.status}</span>
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
            ))}
          </div>

          {/* Desktop: table */}
          <div className="hidden overflow-hidden rounded-2xl ring-1 ring-white/10 md:block">
            <table className="w-full text-left text-sm">
              <thead className="bg-slate-800/80 text-slate-400">
                <tr>
                  <th className="px-4 py-3">Vorschau</th>
                  <th className="px-4 py-3">Datei</th>
                  <th className="px-4 py-3">Größe</th>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3">Hochgeladen</th>
                  <th className="px-4 py-3 text-right">Aktion</th>
                </tr>
              </thead>
              <tbody>
                {media.map((m) => (
                  <tr key={m.id} className="border-t border-white/5 bg-slate-900/40">
                    <td className="px-4 py-3">{m.local_deleted ? <span className="text-xs text-slate-500">gelöscht</span> : <Thumb id={m.id} />}</td>
                    <td className="px-4 py-3 font-medium text-white">{m.filename}</td>
                    <td className="px-4 py-3 text-slate-300">{formatBytes(m.size)}</td>
                    <td className="px-4 py-3">
                      <span className="rounded bg-ogc-teal/15 px-2 py-0.5 text-xs text-ogc-teal">
                        {m.status}
                      </span>
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
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Layout>
  );
}
