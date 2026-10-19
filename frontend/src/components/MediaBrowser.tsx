import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  ApiError,
  getToken,
  type Folder,
  type StorageBrowse,
  type StorageGallery,
  type StorageGalleryItem,
  type StorageImportJob,
  type StorageKnown,
  type StorageMediaKind,
} from "../api";
import { useToast } from "../toast";
import { formatBytes } from "../upload";

/**
 * Picks files on an attached medium for upload.
 *
 * Built for the camera-card case: the "Galerie" view flattens DCIM/100XXX…
 * folders into one photo grid grouped by capture day, with thumbnails and a
 * duplicate check per file. "Ordner" is the plain file tree for everything else.
 */

type Kind = StorageMediaKind;
type View = "gallery" | "folders";
type KindFilter = "all" | Kind;

const KIND_LABEL: Record<Kind, string> = { photo: "Fotos", raw: "RAW", video: "Videos", audio: "Audio" };

const btn =
  "rounded-lg border border-white/10 px-3 py-1.5 text-xs font-medium text-slate-200 hover:bg-white/5 disabled:cursor-not-allowed disabled:opacity-40";
const btnPrimary =
  "rounded-lg bg-gradient-to-r from-ogc-teal to-ogc-blue px-3 py-1.5 text-xs font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40";

const dayFmt = new Intl.DateTimeFormat("de-DE", { weekday: "short", day: "numeric", month: "long", year: "numeric" });
const timeFmt = new Intl.DateTimeFormat("de-DE", { hour: "2-digit", minute: "2-digit" });
const fullFmt = new Intl.DateTimeFormat("de-DE", { dateStyle: "medium", timeStyle: "short" });

function thumbUrl(device: string, path: string, size = 320): string {
  const q = new URLSearchParams({ device, path, size: String(size), token: getToken() ?? "" });
  return `/api/storage/thumb?${q}`;
}

function dayKey(mtime: number): string {
  const d = new Date(mtime * 1000);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** Badge text for a file that's already on the box. */
function knownLabel(k: StorageKnown): { text: string; icon: string; tone: string; title: string } {
  if (k.match === "likely")
    return {
      icon: "≈",
      text: "≈ evtl. vorhanden",
      tone: "bg-amber-500/90 text-slate-900",
      title: `Gleicher Name und Größe wie eine hochgeladene Datei in „${k.folder_name}“`,
    };
  if (k.status === "done")
    return { icon: "☁", text: "☁ gesichert", tone: "bg-emerald-500/90 text-white", title: `In der Cloud — Ordner „${k.folder_name}“` };
  return { icon: "✓", text: "✓ übernommen", tone: "bg-sky-500/90 text-white", title: `Schon auf der Box — Ordner „${k.folder_name}“` };
}

function KindIcon({ kind, className = "h-8 w-8" }: { kind: Kind | "other"; className?: string }) {
  const common = { fill: "none", stroke: "currentColor", strokeWidth: 1.6, strokeLinecap: "round", strokeLinejoin: "round" } as const;
  if (kind === "video")
    return (
      <svg viewBox="0 0 24 24" className={className} {...common}>
        <rect x="3" y="6" width="13" height="12" rx="2" />
        <path d="M16 10l5-3v10l-5-3" />
      </svg>
    );
  if (kind === "audio")
    return (
      <svg viewBox="0 0 24 24" className={className} {...common}>
        <path d="M9 18V6l10-2v12" />
        <circle cx="7" cy="18" r="2" />
        <circle cx="17" cy="16" r="2" />
      </svg>
    );
  return (
    <svg viewBox="0 0 24 24" className={className} {...common}>
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <circle cx="9" cy="10" r="1.6" />
      <path d="M21 16l-5-5-9 8" />
    </svg>
  );
}

export interface MediaBrowserProps {
  device: string;
  title: string;
  /** Path on the medium the view is rooted at (e.g. a cache snapshot). */
  basePath?: string;
  /** Whether a cache drive is available to back the selection up to. */
  canCache?: boolean;
  onStarted: () => void;
  onClose: () => void;
}

export default function MediaBrowser({ device, title, basePath = "", canCache = false, onStarted, onClose }: MediaBrowserProps) {
  const toast = useToast();
  const [view, setView] = useState<View>("gallery");
  const [gallery, setGallery] = useState<StorageGallery | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  // Marked paths -> whether each is a directory (folder view; imported recursively).
  const [selected, setSelected] = useState<Map<string, boolean>>(new Map());
  const [kindFilter, setKindFilter] = useState<KindFilter>("all");
  const [hideKnown, setHideKnown] = useState(false);
  const [preview, setPreview] = useState<number | null>(null);
  const lastClicked = useRef<number | null>(null);

  const loadGallery = useCallback(() => {
    setLoading(true);
    api<StorageGallery>(`/api/storage/gallery?device=${encodeURIComponent(device)}&path=${encodeURIComponent(basePath)}`)
      .then((g) => {
        setGallery(g);
        // Nothing camera-like here (a document stick): start in the folder view.
        if (g.items.length === 0) setView("folders");
      })
      .catch((e) => setErr(e instanceof ApiError ? e.message : "Fehler"))
      .finally(() => setLoading(false));
  }, [device, basePath]);

  useEffect(() => {
    setSelected(new Map());
    loadGallery();
  }, [loadGallery]);

  const items = gallery?.items ?? [];
  const visible = useMemo(
    () => items.filter((i) => (kindFilter === "all" || i.kind === kindFilter) && !(hideKnown && i.known)),
    [items, kindFilter, hideKnown],
  );
  const groups = useMemo(() => {
    const out: { key: string; label: string; items: { item: StorageGalleryItem; index: number }[] }[] = [];
    visible.forEach((item, index) => {
      const key = dayKey(item.mtime);
      let g = out[out.length - 1];
      if (!g || g.key !== key) {
        g = { key, label: dayFmt.format(new Date(item.mtime * 1000)), items: [] };
        out.push(g);
      }
      g.items.push({ item, index });
    });
    return out;
  }, [visible]);

  const counts = useMemo(() => {
    const c: Record<Kind, number> = { photo: 0, raw: 0, video: 0, audio: 0 };
    let known = 0;
    for (const i of items) {
      c[i.kind] += 1;
      if (i.known) known += 1;
    }
    return { ...c, known, fresh: items.length - known };
  }, [items]);

  const sizeOf = useMemo(() => new Map(items.map((i) => [i.path, i.size])), [items]);
  const selectedBytes = [...selected.keys()].reduce((n, p) => n + (sizeOf.get(p) ?? 0), 0);
  const selectedDirs = [...selected.values()].filter(Boolean).length;

  function setMany(paths: string[], on: boolean) {
    setSelected((prev) => {
      const next = new Map(prev);
      for (const p of paths) {
        if (on) next.set(p, false);
        else next.delete(p);
      }
      return next;
    });
  }

  function clickTile(index: number, shift: boolean) {
    const item = visible[index];
    if (shift && lastClicked.current !== null) {
      // Range picks skip files already on the box, like "Tag auswählen".
      const [a, b] = [lastClicked.current, index].sort((x, y) => x - y);
      setMany(visible.slice(a, b + 1).filter((i) => !i.known || i === item).map((i) => i.path), true);
    } else {
      setMany([item.path], !selected.has(item.path));
    }
    lastClicked.current = index;
  }

  function toggleGroup(paths: string[]) {
    const fresh = paths.filter((p) => !items.find((i) => i.path === p)?.known);
    const target = fresh.length ? fresh : paths;
    const allOn = target.every((p) => selected.has(p));
    setMany(target, !allOn);
  }

  return (
    <div className="rounded-2xl bg-slate-800/60 ring-1 ring-ogc-teal/30">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/5 px-5 py-4">
        <div className="min-w-0">
          <div className="truncate text-lg font-semibold text-white">{title}</div>
          <div className="text-xs text-slate-400">
            {loading
              ? "Medium wird gelesen …"
              : gallery &&
                `${items.length} Mediendateien · ${counts.fresh} neu${counts.known ? ` · ${counts.known} schon auf der Box` : ""}`}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex rounded-lg bg-slate-900/70 p-0.5 ring-1 ring-white/10">
            {(["gallery", "folders"] as View[]).map((v) => (
              <button
                key={v}
                onClick={() => {
                  setView(v);
                  setSelected(new Map());
                }}
                className={`rounded-md px-3 py-1 text-xs font-medium ${view === v ? "bg-white/10 text-white" : "text-slate-400 hover:text-white"}`}
              >
                {v === "gallery" ? "Galerie" : "Ordner"}
              </button>
            ))}
          </div>
          <button className={btn} onClick={loadGallery} title="Neu einlesen">
            ↻
          </button>
          <button className={btn} onClick={onClose}>
            Schließen
          </button>
        </div>
      </div>

      {err && <div className="mx-5 mt-4 rounded-lg bg-red-500/15 px-3 py-2 text-sm text-red-300">{err}</div>}

      {view === "gallery" ? (
        <div className="px-5 pb-2 pt-4">
          {/* Filters */}
          <div className="mb-4 flex flex-wrap items-center gap-2">
            {(["all", "photo", "raw", "video", "audio"] as KindFilter[])
              .filter((k) => k === "all" || counts[k] > 0)
              .map((k) => (
                <button
                  key={k}
                  onClick={() => setKindFilter(k)}
                  className={`rounded-full px-3 py-1 text-xs font-medium ring-1 ${
                    kindFilter === k ? "bg-ogc-teal/20 text-white ring-ogc-teal/50" : "text-slate-300 ring-white/10 hover:bg-white/5"
                  }`}
                >
                  {k === "all" ? `Alle ${items.length}` : `${KIND_LABEL[k]} ${counts[k]}`}
                </button>
              ))}
            <label className="ml-1 flex items-center gap-2 text-xs text-slate-300">
              <input type="checkbox" checked={hideKnown} onChange={(e) => setHideKnown(e.target.checked)} />
              Schon übernommene ausblenden
            </label>
            <div className="ml-auto flex gap-2">
              <button
                className={btn}
                disabled={counts.fresh === 0}
                onClick={() => setMany(visible.filter((i) => !i.known).map((i) => i.path), true)}
              >
                Alle neuen auswählen
              </button>
              <button className={btn} disabled={selected.size === 0} onClick={() => setSelected(new Map())}>
                Auswahl aufheben
              </button>
            </div>
          </div>

          {!loading && items.length === 0 && (
            <div className="py-10 text-center text-sm text-slate-400">
              Keine Fotos, Videos oder Audiodateien gefunden — die Ordner-Ansicht zeigt alle Dateien.
            </div>
          )}

          {groups.map((g) => {
            const paths = g.items.map(({ item }) => item.path);
            const nSel = paths.filter((p) => selected.has(p)).length;
            const bytes = g.items.reduce((n, { item }) => n + item.size, 0);
            return (
              <section key={g.key} className="mb-6" style={{ contentVisibility: "auto", containIntrinsicSize: "auto 600px" }}>
                <div className="sticky top-[60px] z-10 -mx-2 mb-2 flex flex-wrap items-baseline gap-x-3 gap-y-0.5 rounded-lg bg-slate-800/95 px-2 py-1.5 backdrop-blur sm:top-[68px]">
                  <span className="text-sm font-semibold text-white sm:text-base">{g.label}</span>
                  <span className="order-3 w-full text-xs text-slate-400 sm:order-none sm:w-auto">
                    {g.items.length} Dateien · {formatBytes(bytes)}
                    {nSel > 0 && <span className="text-ogc-teal"> · {nSel} ausgewählt</span>}
                  </span>
                  <button className="ml-auto text-xs font-medium text-ogc-teal hover:underline" onClick={() => toggleGroup(paths)}>
                    {nSel > 0 && nSel === paths.length ? "Tag abwählen" : "Tag auswählen"}
                  </button>
                </div>
                <div className="grid grid-cols-3 gap-1.5 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-8">
                  {g.items.map(({ item, index }) => (
                    <Tile
                      key={item.path}
                      device={device}
                      item={item}
                      selected={selected.has(item.path)}
                      onClick={(shift) => clickTile(index, shift)}
                      onPreview={() => setPreview(index)}
                    />
                  ))}
                </div>
              </section>
            );
          })}
        </div>
      ) : (
        <FolderView device={device} basePath={basePath} selected={selected} setSelected={setSelected} />
      )}

      <ActionBar
        device={device}
        selected={selected}
        selectedBytes={selectedBytes}
        selectedDirs={selectedDirs}
        canCache={canCache}
        onDone={() => {
          setSelected(new Map());
          onStarted();
          // Refresh the duplicate badges once the job has had a moment.
          setTimeout(loadGallery, 2500);
        }}
        toast={toast}
      />

      {preview !== null && visible[preview] && (
        <Lightbox
          device={device}
          items={visible}
          index={preview}
          selected={selected}
          onIndex={setPreview}
          onToggle={(p) => setMany([p], !selected.has(p))}
          onClose={() => setPreview(null)}
        />
      )}
    </div>
  );
}

// --- Gallery tile -----------------------------------------------------------------

function Tile({
  device,
  item,
  selected,
  onClick,
  onPreview,
}: {
  device: string;
  item: StorageGalleryItem;
  selected: boolean;
  onClick: (shift: boolean) => void;
  onPreview: () => void;
}) {
  const [failed, setFailed] = useState(false);
  const badge = item.known ? knownLabel(item.known) : null;
  return (
    <div
      role="checkbox"
      aria-checked={selected}
      tabIndex={0}
      onClick={(e) => onClick(e.shiftKey)}
      onKeyDown={(e) => {
        if (e.key === " " || e.key === "Enter") {
          e.preventDefault();
          onClick(e.shiftKey);
        }
      }}
      className={`group relative aspect-square cursor-pointer select-none overflow-hidden rounded-lg bg-slate-900 outline-none ring-offset-2 ring-offset-slate-800 focus-visible:ring-2 focus-visible:ring-ogc-teal ${
        selected ? "ring-2 ring-ogc-teal" : ""
      }`}
      title={`${item.name} · ${formatBytes(item.size)} · ${timeFmt.format(new Date(item.mtime * 1000))}`}
    >
      {!failed && item.kind !== "audio" ? (
        <img
          src={thumbUrl(device, item.path)}
          alt={item.name}
          loading="lazy"
          decoding="async"
          onError={() => setFailed(true)}
          className={`h-full w-full object-cover transition ${selected ? "scale-[0.92] rounded-md" : "group-hover:scale-[1.03]"} ${
            item.known && !selected ? "opacity-50" : ""
          }`}
        />
      ) : (
        <div className={`flex h-full w-full flex-col items-center justify-center gap-1 text-slate-500 ${item.known ? "opacity-50" : ""}`}>
          <KindIcon kind={item.kind} />
          <span className="max-w-full truncate px-2 text-[10px]">{item.name}</span>
        </div>
      )}

      {/* selection mark */}
      <span
        className={`absolute left-1.5 top-1.5 flex h-5 w-5 items-center justify-center rounded-full border text-[11px] font-bold ${
          selected
            ? "border-ogc-teal bg-ogc-teal text-white"
            : "border-white/70 bg-black/30 text-transparent opacity-0 group-hover:opacity-100"
        }`}
      >
        ✓
      </span>

      {/* preview */}
      {item.kind !== "audio" && (
        <button
          onClick={(e) => {
            e.stopPropagation();
            onPreview();
          }}
          aria-label="Vorschau"
          className="absolute right-1.5 top-1.5 flex h-7 w-7 items-center justify-center rounded-full bg-black/50 text-white opacity-0 transition group-hover:opacity-100 focus:opacity-100 [@media(hover:none)]:opacity-100"
        >
          <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <circle cx="11" cy="11" r="6" />
            <path d="M20 20l-4.5-4.5M11 8v6M8 11h6" />
          </svg>
        </button>
      )}

      {(item.kind === "raw" || item.kind === "video") && (
        <span className="absolute bottom-1.5 right-1.5 rounded bg-black/60 px-1 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-white">
          {item.kind === "raw" ? item.name.split(".").pop() : "▶ Video"}
        </span>
      )}
      {badge && (
        <span title={badge.title} className={`absolute bottom-1.5 left-1.5 rounded px-1 py-0.5 text-[9px] font-semibold ${badge.tone}`}>
          <span className="sm:hidden">{badge.icon}</span>
          <span className="hidden sm:inline">{badge.text}</span>
        </span>
      )}
    </div>
  );
}

// --- Lightbox ----------------------------------------------------------------------

function Lightbox({
  device,
  items,
  index,
  selected,
  onIndex,
  onToggle,
  onClose,
}: {
  device: string;
  items: StorageGalleryItem[];
  index: number;
  selected: Map<string, boolean>;
  onIndex: (i: number) => void;
  onToggle: (path: string) => void;
  onClose: () => void;
}) {
  const item = items[index];
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      else if (e.key === "ArrowRight" && index < items.length - 1) onIndex(index + 1);
      else if (e.key === "ArrowLeft" && index > 0) onIndex(index - 1);
      else if (e.key === " ") {
        e.preventDefault();
        onToggle(item.path);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [index, items.length, item.path, onClose, onIndex, onToggle]);

  const isSel = selected.has(item.path);
  const badge = item.known ? knownLabel(item.known) : null;
  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-slate-950/[.97] backdrop-blur-sm" onClick={onClose}>
      <div className="flex items-center gap-3 px-4 py-3 text-sm text-slate-200" onClick={(e) => e.stopPropagation()}>
        <div className="min-w-0 flex-1">
          <div className="truncate font-medium text-white">{item.name}</div>
          <div className="text-xs text-slate-400">
            {fullFmt.format(new Date(item.mtime * 1000))} · {formatBytes(item.size)} · {item.dir || "/"} · {index + 1}/{items.length}
          </div>
        </div>
        {badge && <span className={`rounded px-2 py-0.5 text-xs font-semibold ${badge.tone}`}>{badge.text} · {item.known!.folder_name}</span>}
        <button className={isSel ? btnPrimary : btn} onClick={() => onToggle(item.path)}>
          {isSel ? "✓ Ausgewählt" : "Auswählen"}
        </button>
        <button className={btn} onClick={onClose} aria-label="Schließen">
          ✕
        </button>
      </div>
      <div className="relative flex min-h-0 flex-1 items-center justify-center px-12 pb-6" onClick={(e) => e.stopPropagation()}>
        <img
          key={item.path}
          src={thumbUrl(device, item.path, 1600)}
          alt={item.name}
          className="max-h-full max-w-full rounded object-contain"
        />
        {item.kind === "video" && (
          <span className="absolute bottom-10 rounded bg-black/70 px-2 py-1 text-xs text-slate-200">Video — Standbild</span>
        )}
        {index > 0 && (
          <button
            onClick={() => onIndex(index - 1)}
            className="absolute left-2 top-1/2 -translate-y-1/2 rounded-full bg-white/10 px-3 py-2 text-2xl text-white hover:bg-white/20"
            aria-label="Vorheriges"
          >
            ‹
          </button>
        )}
        {index < items.length - 1 && (
          <button
            onClick={() => onIndex(index + 1)}
            className="absolute right-2 top-1/2 -translate-y-1/2 rounded-full bg-white/10 px-3 py-2 text-2xl text-white hover:bg-white/20"
            aria-label="Nächstes"
          >
            ›
          </button>
        )}
      </div>
      <div className="pb-3 text-center text-[11px] text-slate-500">← → blättern · Leertaste auswählen · Esc schließen</div>
    </div>
  );
}

// --- Folder view -------------------------------------------------------------------

function FolderView({
  device,
  basePath,
  selected,
  setSelected,
}: {
  device: string;
  basePath: string;
  selected: Map<string, boolean>;
  setSelected: React.Dispatch<React.SetStateAction<Map<string, boolean>>>;
}) {
  const [path, setPath] = useState(basePath);
  const [listing, setListing] = useState<StorageBrowse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [mediaOnly, setMediaOnly] = useState(false);

  useEffect(() => {
    setErr(null);
    api<StorageBrowse>(`/api/storage/browse?device=${encodeURIComponent(device)}&path=${encodeURIComponent(path)}`)
      .then(setListing)
      .catch((e) => setErr(e instanceof ApiError ? e.message : "Fehler"));
  }, [device, path]);

  const entries = (listing?.entries ?? []).filter((e) => e.is_dir || !mediaOnly || e.is_media);
  const allHere = entries.length > 0 && entries.every((e) => selected.has(e.path));
  const rel = basePath ? path.slice(basePath.length).replace(/^\//, "") : path;
  const crumbs = rel ? rel.split("/") : [];
  const join = (n: number) => [basePath, ...crumbs.slice(0, n)].filter(Boolean).join("/");

  function toggle(p: string, isDir: boolean) {
    setSelected((prev) => {
      const next = new Map(prev);
      if (next.has(p)) next.delete(p);
      else next.set(p, isDir);
      return next;
    });
  }

  return (
    <div className="px-5 pb-2 pt-4">
      <div className="mb-3 flex flex-wrap items-center gap-1 text-sm">
        <button className="text-ogc-teal hover:underline" onClick={() => setPath(basePath)}>
          Start
        </button>
        {crumbs.map((c, i) => (
          <span key={i} className="flex items-center gap-1">
            <span className="text-slate-600">/</span>
            <button className="text-ogc-teal hover:underline" onClick={() => setPath(join(i + 1))}>
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
      <div className="max-h-[28rem] overflow-y-auto rounded-lg ring-1 ring-white/5">
        <table className="w-full text-sm">
          <thead className="sticky top-0 bg-slate-900 text-left text-xs text-slate-400">
            <tr>
              <th className="w-8 px-3 py-2">
                <input
                  type="checkbox"
                  checked={allHere}
                  aria-label="Alle markieren"
                  onChange={() =>
                    setSelected((prev) => {
                      const next = new Map(prev);
                      for (const e of entries) {
                        if (allHere) next.delete(e.path);
                        else next.set(e.path, e.is_dir);
                      }
                      return next;
                    })
                  }
                />
              </th>
              <th className="px-2 py-2">Name</th>
              <th className="hidden px-2 py-2 sm:table-cell">Status</th>
              <th className="hidden px-2 py-2 text-right sm:table-cell">Größe</th>
              <th className="hidden px-3 py-2 text-right md:table-cell">Geändert</th>
            </tr>
          </thead>
          <tbody>
            {path !== basePath && listing && (
              <tr className="border-t border-white/5">
                <td />
                <td colSpan={4} className="px-2 py-1.5">
                  <button className="text-slate-300 hover:text-white" onClick={() => setPath(listing.parent ?? basePath)}>
                    ↑ ..
                  </button>
                </td>
              </tr>
            )}
            {entries.map((e) => {
              const badge = e.known ? knownLabel(e.known) : null;
              return (
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
                  <td className="hidden px-2 py-1.5 sm:table-cell">
                    {badge && (
                      <span title={badge.title} className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${badge.tone}`}>
                        {badge.text}
                      </span>
                    )}
                  </td>
                  <td className="hidden whitespace-nowrap px-2 py-1.5 text-right text-slate-400 sm:table-cell">
                    {e.is_dir ? "" : formatBytes(e.size)}
                  </td>
                  <td className="hidden whitespace-nowrap px-3 py-1.5 text-right text-slate-500 md:table-cell">
                    {e.modified ? fullFmt.format(new Date(e.modified)) : ""}
                  </td>
                </tr>
              );
            })}
            {listing && entries.length === 0 && (
              <tr>
                <td colSpan={5} className="px-3 py-6 text-center text-slate-500">
                  Ordner ist leer.
                </td>
              </tr>
            )}
          </tbody>
        </table>
        {listing?.truncated && <div className="px-3 py-2 text-xs text-amber-200">Nur die ersten 2000 Einträge angezeigt.</div>}
      </div>
    </div>
  );
}

// --- Sticky action bar ---------------------------------------------------------------

function ActionBar({
  device,
  selected,
  selectedBytes,
  selectedDirs,
  canCache,
  onDone,
  toast,
}: {
  device: string;
  selected: Map<string, boolean>;
  selectedBytes: number;
  selectedDirs: number;
  canCache: boolean;
  onDone: () => void;
  toast: ReturnType<typeof useToast>;
}) {
  const [folders, setFolders] = useState<Folder[]>([]);
  const [folderId, setFolderId] = useState<number | "">("");
  const [tags, setTags] = useState("");
  const [skipDupes, setSkipDupes] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [showOpts, setShowOpts] = useState(false);

  useEffect(() => {
    api<Folder[]>("/api/folders")
      .then((f) => {
        setFolders(f);
        if (f.length === 1) setFolderId(f[0].id);
      })
      .catch(() => {});
  }, []);

  async function run(kind: "import" | "cache") {
    setBusy(true);
    setErr(null);
    try {
      const paths = [...selected.keys()];
      const job =
        kind === "import"
          ? await api<StorageImportJob>("/api/storage/imports", {
              method: "POST",
              body: JSON.stringify({
                device,
                paths,
                folder_id: folderId,
                media_only: false,
                skip_duplicates: skipDupes,
                tags: tags.split(",").map((t) => t.trim()).filter(Boolean),
              }),
            })
          : await api<StorageImportJob>("/api/storage/caches/backup", {
              method: "POST",
              body: JSON.stringify({ device, paths, media_only: false, only_new: false }),
            });
      toast.success(
        kind === "import" ? "Upload gestartet" : "Sicherung auf Cache gestartet",
        `${job.total_files} Datei(en), ${formatBytes(job.total_bytes)}${kind === "import" ? ` → „${job.folder_name}“` : ""}`,
      );
      onDone();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Fehler");
    } finally {
      setBusy(false);
    }
  }

  const n = selected.size;
  return (
    <div className="sticky bottom-0 z-20 rounded-b-2xl border-t border-white/10 bg-slate-900/95 px-5 py-3 backdrop-blur">
      {err && <div className="mb-2 rounded-lg bg-red-500/15 px-3 py-1.5 text-xs text-red-300">{err}</div>}
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex w-full min-w-[9rem] items-center text-sm text-slate-300 sm:w-auto">
          <div className="flex-1">
          {n === 0 ? (
            <span className="text-slate-500">
                Bilder antippen zum Auswählen<span className="hidden sm:inline"> · Shift für Bereich</span>
              </span>
          ) : (
            <>
              <span className="font-semibold text-white">{n}</span> ausgewählt
              {selectedBytes > 0 && <span className="text-slate-400"> · {formatBytes(selectedBytes)}</span>}
              {selectedDirs > 0 && <span className="text-slate-400"> · {selectedDirs} Ordner</span>}
            </>
          )}
          </div>
          <button className="text-xs text-slate-400 underline sm:hidden" onClick={() => setShowOpts((v) => !v)}>
            {showOpts ? "weniger" : "Optionen"}
          </button>
        </div>
        <select
          value={folderId}
          onChange={(e) => setFolderId(e.target.value ? Number(e.target.value) : "")}
          className="min-w-0 flex-1 rounded-lg bg-slate-800 px-3 py-1.5 text-sm text-white ring-1 ring-white/10 sm:flex-none"
          aria-label="Ziel-Ordner"
        >
          <option value="">Ziel-Ordner wählen …</option>
          {folders.map((f) => (
            <option key={f.id} value={f.id}>
              {f.name}
            </option>
          ))}
        </select>
        <input
          value={tags}
          onChange={(e) => setTags(e.target.value)}
          placeholder="Tags (optional)"
          className={`${showOpts ? "block" : "hidden"} w-full rounded-lg bg-slate-800 px-3 py-1.5 text-sm text-white ring-1 ring-white/10 sm:block sm:w-40`}
        />
        <label className={`${showOpts ? "flex" : "hidden"} items-center gap-1.5 text-xs text-slate-300 sm:flex`} title="Dateien, die schon irgendwo auf der Box liegen, nicht noch einmal laden">
          <input type="checkbox" checked={skipDupes} onChange={(e) => setSkipDupes(e.target.checked)} />
          Duplikate überspringen
        </label>
        <div className="flex gap-2 sm:ml-auto">
          {canCache && (
            <button className={btn} disabled={n === 0 || busy} onClick={() => run("cache")}>
              Auf Cache sichern
            </button>
          )}
          <button className={`${btnPrimary} px-4 py-2 text-sm`} disabled={n === 0 || folderId === "" || busy} onClick={() => run("import")}>
            {busy ? "Starte …" : n ? `${n} hochladen` : "Hochladen"}
          </button>
        </div>
      </div>
    </div>
  );
}
