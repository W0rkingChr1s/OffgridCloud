import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  api,
  ApiError,
  type Folder,
  getToken,
  type MediaSearchResult,
  type MediaStatus,
} from "../api";
import Layout from "../components/Layout";
import { SortMenu, type SortOption, useSort } from "../components/Sort";
import { TagEditor } from "../components/Tags";
import { formatBytes } from "../upload";

const RESULT_SORT: SortOption<MediaSearchResult>[] = [
  { key: "name", label: "Name", get: (m) => m.filename },
  { key: "folder", label: "Ordner", get: (m) => m.folder_name },
  { key: "size", label: "Größe", get: (m) => m.size },
  { key: "status", label: "Status", get: (m) => m.status },
  { key: "created", label: "Hochgeladen", get: (m) => m.created_at },
];

const STATUSES: MediaStatus[] = [
  "received",
  "queued",
  "uploading",
  "verified",
  "done",
  "failed",
];

function downloadUrl(id: number): string {
  return `/api/media/${id}/download?token=${encodeURIComponent(getToken() ?? "")}`;
}

export default function Search() {
  const [q, setQ] = useState("");
  const [tag, setTag] = useState("");
  const [status, setStatus] = useState("");
  const [folderId, setFolderId] = useState("");
  const [folders, setFolders] = useState<Folder[]>([]);
  const [tagOptions, setTagOptions] = useState<string[]>([]);
  const [results, setResults] = useState<MediaSearchResult[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    api<Folder[]>("/api/folders").then(setFolders).catch(() => setFolders([]));
    api<string[]>("/api/media/tags").then(setTagOptions).catch(() => setTagOptions([]));
  }, []);

  const query = useMemo(() => {
    const p = new URLSearchParams();
    if (q.trim()) p.set("q", q.trim());
    if (tag) p.set("tag", tag);
    if (status) p.set("status", status);
    if (folderId) p.set("folder_id", folderId);
    return p.toString();
  }, [q, tag, status, folderId]);

  // Debounced auto-search whenever a filter changes.
  useEffect(() => {
    const handle = setTimeout(() => {
      setLoading(true);
      api<MediaSearchResult[]>(`/api/media/search?${query}`)
        .then((r) => {
          setResults(r);
          setError(null);
        })
        .catch((e) => setError(e instanceof ApiError ? e.message : "Fehler"))
        .finally(() => setLoading(false));
    }, 250);
    return () => clearTimeout(handle);
  }, [query]);

  const field =
    "rounded-lg border border-white/10 bg-slate-800/60 px-3 py-2 text-sm text-white outline-none focus:border-ogc-teal/50";

  const sort = useSort(results, RESULT_SORT, { key: "name" });

  return (
    <Layout>
      <div className="mb-6">
        <h2 className="text-2xl font-bold">Suche</h2>
        <p className="text-sm text-slate-400">
          Medien über alle Ordner finden — nach Name, Tag, Status.
        </p>
      </div>

      <div className="mb-6 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <input
          className={field}
          placeholder="Dateiname…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <select className={field} value={tag} onChange={(e) => setTag(e.target.value)}>
          <option value="">Alle Tags</option>
          {tagOptions.map((t) => (
            <option key={t} value={t}>
              #{t}
            </option>
          ))}
        </select>
        <select className={field} value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">Alle Status</option>
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <select className={field} value={folderId} onChange={(e) => setFolderId(e.target.value)}>
          <option value="">Alle Ordner</option>
          {folders.map((f) => (
            <option key={f.id} value={f.id}>
              {f.name}
            </option>
          ))}
        </select>
      </div>

      {error && (
        <div className="mb-4 rounded-lg bg-red-500/15 px-3 py-2 text-sm text-red-300">{error}</div>
      )}

      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="text-sm text-slate-400">
          {loading ? "Suche…" : `${results.length} Treffer`}
        </div>
        {results.length > 0 && <SortMenu sort={sort} />}
      </div>

      {results.length === 0 && !loading ? (
        <p className="text-sm text-slate-500">Keine Medien passen zu den Filtern.</p>
      ) : (
        <div className="space-y-2">
          {sort.sorted.map((m) => (
            <div
              key={m.id}
              className="flex flex-col gap-2 rounded-xl bg-slate-800/60 p-3 ring-1 ring-white/5 sm:flex-row sm:items-center sm:justify-between"
            >
              <div className="min-w-0">
                <div className="truncate text-sm font-medium text-white">{m.filename}</div>
                <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-slate-400">
                  <Link to={`/folders/${m.folder_id}`} className="text-ogc-teal hover:underline">
                    {m.folder_name}
                  </Link>
                  <span aria-hidden>·</span>
                  <span>{formatBytes(m.size)}</span>
                  <span aria-hidden>·</span>
                  <span>{m.status}</span>
                </div>
                <div className="mt-1.5">
                  <TagEditor mediaId={m.id} tags={m.tags} />
                </div>
              </div>
              {!m.local_deleted && (
                <a
                  href={downloadUrl(m.id)}
                  download
                  className="shrink-0 self-start rounded border border-white/10 px-3 py-1 text-xs hover:bg-white/5 sm:self-center"
                >
                  Download
                </a>
              )}
            </div>
          ))}
        </div>
      )}
    </Layout>
  );
}
