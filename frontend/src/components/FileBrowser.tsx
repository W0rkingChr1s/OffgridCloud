import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useWindowVirtualizer } from "@tanstack/react-virtual";
import { SortMenu, type SortDir, type SortOption, useSort } from "./Sort";

/**
 * Shared file browser: list and tile view over any kind of file-ish item.
 *
 * Built for long listings on slow phones: rows and tile rows are virtualised
 * against the page scroll, so a camera card with thousands of files only ever
 * renders what's on screen. Selection is controlled by the caller (keys as
 * strings); the browser adds Shift-click ranges, select-all over the filtered
 * view and keyboard navigation (arrows, Space, Enter, Ctrl/Cmd+A, Esc).
 */

export type FileView = "list" | "grid";

export interface FileColumn<T> {
  key: string;
  label: string;
  /** Width / visibility classes, applied to header and cells alike (e.g. "w-24 hidden md:block"). */
  className: string;
  render: (item: T) => ReactNode;
  /** Sort option key the header toggles. */
  sortKey?: string;
  align?: "left" | "right";
}

export interface FileBrowserProps<T> {
  items: T[];
  getKey: (item: T) => string;
  getName: (item: T) => string;
  /** Directories sort first and open on click instead of toggling the selection. */
  isDir?: (item: T) => boolean;
  /** Items that can't be selected get no checkbox (e.g. already removed locally). */
  isSelectable?: (item: T) => boolean;
  /** Omit to show the browser without any selection. */
  selected?: Set<string>;
  onSelectedChange?: (next: Set<string>) => void;
  /** Enter / double click; single click for directories. */
  onOpen?: (item: T) => void;
  sortOptions: SortOption<T>[];
  initialSort?: { key?: string; dir?: SortDir };
  /** Small icon or thumbnail in front of the name (list view). */
  renderIcon: (item: T) => ReactNode;
  /** Square preview filling a tile (grid view). */
  renderTile: (item: T) => ReactNode;
  /** Extra line under the name in list view (meta on small screens, tags …). */
  renderSubtitle?: (item: T) => ReactNode;
  /** Overlay in the lower-left of a tile (status badge). */
  renderTileBadge?: (item: T) => ReactNode;
  /** Right-click (or menu key on the focused entry) on a row or tile. */
  onContextMenu?: (item: T, e: React.MouseEvent) => void;
  /** Trailing per-row actions (download, menu). */
  renderActions?: (item: T) => ReactNode;
  columns?: FileColumn<T>[];
  /** Left side of the toolbar (breadcrumbs, title). */
  header?: ReactNode;
  /** Extra controls in the toolbar (filters). */
  toolbar?: ReactNode;
  /** Client-side name filter. Off where the server already searches. */
  filterable?: boolean;
  emptyText?: ReactNode;
  loading?: boolean;
  /** localStorage key for the remembered view mode. */
  storageKey: string;
  defaultView?: FileView;
  /** Changes when the listing is a different place (directory path), resetting cursor and range anchor. */
  resetKey?: string | number;
}

const TILE_MIN = 140; // px, minimum tile width before another column fits
const TILE_GAP = 6;
const LIST_ROW = 56;

const INTERACTIVE = "a,button,input,select,textarea,label,[data-no-row-click]";

function loadView(key: string, fallback: FileView): FileView {
  try {
    const v = localStorage.getItem(`ogc.fileview.${key}`);
    return v === "list" || v === "grid" ? v : fallback;
  } catch {
    return fallback;
  }
}

function saveView(key: string, v: FileView) {
  try {
    localStorage.setItem(`ogc.fileview.${key}`, v);
  } catch {
    // private mode / blocked storage: the choice just isn't remembered
  }
}

export default function FileBrowser<T>({
  items,
  getKey,
  getName,
  isDir,
  isSelectable,
  selected,
  onSelectedChange,
  onOpen,
  sortOptions,
  initialSort,
  renderIcon,
  renderTile,
  renderSubtitle,
  renderTileBadge,
  renderActions,
  onContextMenu,
  columns = [],
  header,
  toolbar,
  filterable = true,
  emptyText = "Keine Dateien.",
  loading = false,
  storageKey,
  defaultView = "list",
  resetKey,
}: FileBrowserProps<T>) {
  const [view, setViewState] = useState<FileView>(() => loadView(storageKey, defaultView));
  const [filter, setFilter] = useState("");
  const [active, setActive] = useState(-1);
  const anchor = useRef<number | null>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const selectable = selected !== undefined && onSelectedChange !== undefined;
  const canSelect = useCallback((item: T) => (isSelectable ? isSelectable(item) : true), [isSelectable]);

  const setView = (v: FileView) => {
    setViewState(v);
    saveView(storageKey, v);
  };

  const filtered = useMemo(() => {
    const q = filter.trim().toLocaleLowerCase("de");
    return q ? items.filter((i) => getName(i).toLocaleLowerCase("de").includes(q)) : items;
  }, [items, filter, getName]);

  const sort = useSort(filtered, sortOptions, initialSort);
  // Directories first, keeping the chosen order within each group.
  const visible = useMemo(
    () => (isDir ? [...sort.sorted.filter(isDir), ...sort.sorted.filter((i) => !isDir(i))] : sort.sorted),
    [sort.sorted, isDir],
  );

  // Another directory or filter invalidates the cursor and the Shift-click anchor.
  useEffect(() => {
    setActive(-1);
    anchor.current = null;
  }, [resetKey, filter]);
  useEffect(() => {
    setActive((a) => Math.min(a, visible.length - 1));
  }, [visible.length]);

  // --- layout -----------------------------------------------------------------------

  const [width, setWidth] = useState(0);
  const [offset, setOffset] = useState(0);
  useLayoutEffect(() => {
    const el = bodyRef.current;
    if (!el) return;
    const measure = () => {
      setWidth(el.clientWidth);
      setOffset(el.getBoundingClientRect().top + window.scrollY);
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    // Content above the list (upload progress, notices) moves it without resizing it.
    ro.observe(document.body);
    return () => ro.disconnect();
  }, []);

  const cols = view === "grid" ? Math.max(2, Math.floor((width + TILE_GAP) / (TILE_MIN + TILE_GAP))) : 1;
  const tileSize = view === "grid" && width ? (width - TILE_GAP * (cols - 1)) / cols : TILE_MIN;
  const rowCount = Math.ceil(visible.length / cols);

  const virtualizer = useWindowVirtualizer({
    count: rowCount,
    estimateSize: () => (view === "grid" ? tileSize + 28 + TILE_GAP : LIST_ROW),
    overscan: view === "grid" ? 3 : 8,
    scrollMargin: offset,
    useFlushSync: false,
  });

  useEffect(() => {
    virtualizer.measure();
  }, [view, cols, virtualizer]);

  // --- selection ------------------------------------------------------------------

  const setKeys = useCallback(
    (keys: string[], on: boolean) => {
      if (!selectable) return;
      const next = new Set(selected);
      for (const k of keys) {
        if (on) next.add(k);
        else next.delete(k);
      }
      onSelectedChange(next);
    },
    [selectable, selected, onSelectedChange],
  );

  const selectableVisible = useMemo(() => visible.filter(canSelect), [visible, canSelect]);
  const nSelectedHere = selectable ? selectableVisible.filter((i) => selected.has(getKey(i))).length : 0;
  const allHere = selectableVisible.length > 0 && nSelectedHere === selectableVisible.length;

  const toggleAll = () => setKeys(selectableVisible.map(getKey), !allHere);

  const pick = (index: number, shift: boolean) => {
    const item = visible[index];
    if (!item || !canSelect(item)) return;
    if (shift && anchor.current !== null) {
      const [a, b] = [anchor.current, index].sort((x, y) => x - y);
      setKeys(visible.slice(a, b + 1).filter(canSelect).map(getKey), true);
    } else {
      setKeys([getKey(item)], !selected?.has(getKey(item)));
      anchor.current = index;
    }
  };

  const activate = (index: number, e: { shiftKey: boolean; target: EventTarget }) => {
    if ((e.target as HTMLElement).closest(INTERACTIVE)) return;
    setActive(index);
    const item = visible[index];
    if (isDir?.(item) && onOpen) onOpen(item);
    else if (selectable) pick(index, e.shiftKey);
    else onOpen?.(item);
  };

  // --- keyboard -------------------------------------------------------------------

  const moveTo = (index: number) => {
    const i = Math.max(0, Math.min(visible.length - 1, index));
    setActive(i);
    virtualizer.scrollToIndex(Math.floor(i / cols), { align: "auto" });
    return i;
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.target !== e.currentTarget || visible.length === 0) return;
    const cur = active < 0 ? -1 : active;
    const step = (d: number) => {
      e.preventDefault();
      const i = moveTo(cur < 0 ? 0 : cur + d);
      if (e.shiftKey && selectable) {
        if (anchor.current === null) anchor.current = cur < 0 ? i : cur;
        pick(i, true);
      }
    };
    switch (e.key) {
      case "ArrowDown":
        return step(cols);
      case "ArrowUp":
        return step(-cols);
      case "ArrowRight":
        return view === "grid" ? step(1) : undefined;
      case "ArrowLeft":
        return view === "grid" ? step(-1) : undefined;
      case "Home":
        e.preventDefault();
        moveTo(0);
        return;
      case "End":
        e.preventDefault();
        moveTo(visible.length - 1);
        return;
      case " ":
        if (cur >= 0 && selectable) {
          e.preventDefault();
          pick(cur, false);
        }
        return;
      case "Enter":
        if (cur >= 0 && onOpen) {
          e.preventDefault();
          onOpen(visible[cur]);
        }
        return;
      case "Escape":
        if (selectable && selected.size) {
          e.preventDefault();
          onSelectedChange(new Set());
        }
        return;
      case "a":
      case "A":
        if ((e.ctrlKey || e.metaKey) && selectable) {
          e.preventDefault();
          setKeys(selectableVisible.map(getKey), true);
        }
        return;
    }
  };

  // --- render ---------------------------------------------------------------------

  const optionId = (i: number) => `${storageKey}-opt-${i}`;
  const headerCell = (c: FileColumn<T>) => {
    const isSorted = c.sortKey && sort.key === c.sortKey;
    return (
      <div key={c.key} className={`${c.className} shrink-0 ${c.align === "right" ? "text-right" : ""}`}>
        {c.sortKey ? (
          <button className="inline-flex items-center gap-1 hover:text-slate-200" onClick={() => sort.sortBy(c.sortKey!)}>
            {c.label}
            <span className={`text-[10px] ${isSorted ? "text-ogc-teal" : "text-slate-600"}`} aria-hidden>
              {isSorted ? (sort.dir === "asc" ? "▲" : "▼") : "↕"}
            </span>
          </button>
        ) : (
          c.label
        )}
      </div>
    );
  };
  const nameSorted = sortOptions.some((o) => o.key === "name");
  // Lives inside the name cell so it never pushes the column titles out of line.
  const counter = (filter || nSelectedHere > 0) && (
    <span className="truncate text-slate-500">
      {filter && `${visible.length}/${items.length}`}
      {filter && nSelectedHere > 0 && " · "}
      {nSelectedHere > 0 && <span className="text-ogc-teal">{nSelectedHere} ausgewählt</span>}
    </span>
  );

  return (
    <div>
      {/* Toolbar */}
      <div className="mb-3 flex flex-wrap items-center gap-2">
        {header && <div className="mr-auto min-w-0">{header}</div>}
        {toolbar}
        {filterable && (
          <input
            type="search"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filtern …"
            aria-label="Nach Namen filtern"
            className="w-32 rounded-lg border border-white/10 bg-slate-800/60 px-2.5 py-1 text-sm text-white outline-hidden focus:border-ogc-teal/50 sm:w-44"
          />
        )}
        {items.length > 1 && <SortMenu sort={sort} />}
        <div className="flex rounded-lg bg-slate-900/70 p-0.5 ring-1 ring-white/10" role="group" aria-label="Ansicht">
          {(["list", "grid"] as FileView[]).map((v) => (
            <button
              key={v}
              onClick={() => setView(v)}
              aria-pressed={view === v}
              title={v === "list" ? "Liste" : "Kacheln"}
              className={`rounded-md px-2 py-1 ${view === v ? "bg-white/10 text-white" : "text-slate-400 hover:text-white"}`}
            >
              <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                {v === "list" ? (
                  <path d="M8 6h13M8 12h13M8 18h13M3.5 6h.01M3.5 12h.01M3.5 18h.01" />
                ) : (
                  <path d="M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z" />
                )}
              </svg>
            </button>
          ))}
        </div>
      </div>

      {/* Header line: select-all, counts, list column titles */}
      <div className="flex items-center gap-3 rounded-t-xl bg-slate-800/80 px-3 py-2 text-xs text-slate-400 ring-1 ring-white/10">
        {selectable && (
          <input
            type="checkbox"
            checked={allHere}
            ref={(el) => {
              if (el) el.indeterminate = nSelectedHere > 0 && !allHere;
            }}
            onChange={toggleAll}
            disabled={selectableVisible.length === 0}
            aria-label="Alle sichtbaren auswählen"
            className="h-4 w-4 shrink-0 accent-ogc-teal"
          />
        )}
        {view === "list" ? (
          <>
            <div className="flex min-w-0 flex-1 items-center gap-2">
              {nameSorted ? headerCell({ key: "name", label: "Name", className: "", sortKey: "name", render: () => null }) : "Name"}
              {counter}
            </div>
            {columns.map(headerCell)}
            {renderActions && <div className="w-8 shrink-0 sm:w-24" aria-hidden />}
          </>
        ) : (
          <div className="flex flex-1 items-center gap-2">
            {visible.length} {visible.length === 1 ? "Eintrag" : "Einträge"}
            {counter}
          </div>
        )}
      </div>

      {/* Body */}
      <div
        ref={bodyRef}
        tabIndex={0}
        role="listbox"
        aria-multiselectable={selectable || undefined}
        aria-activedescendant={active >= 0 ? optionId(active) : undefined}
        aria-label="Dateien"
        onKeyDown={onKeyDown}
        onContextMenu={(e) => {
          // Menu key / Shift+F10 on the list itself: act on the entry under the cursor.
          if (e.target === e.currentTarget && active >= 0 && visible[active]) onContextMenu?.(visible[active], e);
        }}
        onFocus={(e) => {
          if (e.target === e.currentTarget && active < 0 && visible.length) setActive(0);
        }}
        className={`rounded-b-xl bg-slate-900/40 outline-hidden ring-1 ring-white/10 focus-visible:ring-ogc-teal/60 ${
          view === "grid" ? "p-1.5" : ""
        }`}
      >
        {visible.length === 0 ? (
          <div className="px-3 py-10 text-center text-sm text-slate-500">
            {loading ? "Wird geladen …" : filter ? "Nichts passt zum Filter." : emptyText}
          </div>
        ) : (
          <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
            {virtualizer.getVirtualItems().map((row) => {
              const top = row.start - virtualizer.options.scrollMargin;
              if (view === "list") {
                const item = visible[row.index];
                return (
                  <ListRow
                    key={getKey(item)}
                    index={row.index}
                    id={optionId(row.index)}
                    top={top}
                    measure={virtualizer.measureElement}
                    item={item}
                    dir={isDir?.(item) ?? false}
                    active={active === row.index}
                    checked={selectable ? selected.has(getKey(item)) : undefined}
                    canSelect={canSelect(item)}
                    name={getName(item)}
                    columns={columns}
                    renderIcon={renderIcon}
                    renderSubtitle={renderSubtitle}
                    renderActions={renderActions}
                    onActivate={activate}
                    onPick={pick}
                    onOpen={onOpen}
                    onContextMenu={onContextMenu}
                  />
                );
              }
              const start = row.index * cols;
              return (
                <div
                  key={row.key}
                  data-index={row.index}
                  ref={virtualizer.measureElement}
                  className="absolute inset-x-0 grid"
                  style={{ top, gap: TILE_GAP, paddingBottom: TILE_GAP, gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}
                >
                  {visible.slice(start, start + cols).map((item, j) => {
                    const index = start + j;
                    return (
                      <Tile
                        key={getKey(item)}
                        index={index}
                        id={optionId(index)}
                        item={item}
                        dir={isDir?.(item) ?? false}
                        active={active === index}
                        checked={selectable ? selected.has(getKey(item)) : undefined}
                        canSelect={canSelect(item)}
                        name={getName(item)}
                        renderTile={renderTile}
                        renderTileBadge={renderTileBadge}
                        onActivate={activate}
                        onPick={pick}
                        onOpen={onOpen}
                        onContextMenu={onContextMenu}
                      />
                    );
                  })}
                </div>
              );
            })}
          </div>
        )}
      </div>
      {selectable && visible.length > 0 && (
        <div className="mt-1.5 hidden text-[11px] text-slate-500 sm:block">
          Shift-Klick wählt einen Bereich · Strg/⌘+A alle · Pfeiltasten + Leertaste · Esc hebt die Auswahl auf
        </div>
      )}
    </div>
  );
}

// --- Row / tile ----------------------------------------------------------------------

interface ItemProps<T> {
  index: number;
  id: string;
  item: T;
  dir: boolean;
  active: boolean;
  /** undefined: the browser has no selection at all. */
  checked: boolean | undefined;
  canSelect: boolean;
  name: string;
  onActivate: (index: number, e: { shiftKey: boolean; target: EventTarget }) => void;
  onPick: (index: number, shift: boolean) => void;
  onOpen?: (item: T) => void;
  onContextMenu?: (item: T, e: React.MouseEvent) => void;
}

function Check({ index, checked, canSelect, name, onPick }: Pick<ItemProps<unknown>, "index" | "checked" | "canSelect" | "name" | "onPick">) {
  if (checked === undefined) return null;
  if (!canSelect) return <span className="h-4 w-4 shrink-0" />;
  return (
    <input
      type="checkbox"
      checked={checked}
      onChange={() => {}}
      onClick={(e) => onPick(index, e.shiftKey)}
      aria-label={`${name} auswählen`}
      tabIndex={-1}
      className="h-4 w-4 shrink-0 accent-ogc-teal"
    />
  );
}

function ListRow<T>({
  top,
  measure,
  columns,
  renderIcon,
  renderSubtitle,
  renderActions,
  ...p
}: ItemProps<T> & {
  top: number;
  measure: (el: Element | null) => void;
  columns: FileColumn<T>[];
  renderIcon: (item: T) => ReactNode;
  renderSubtitle?: (item: T) => ReactNode;
  renderActions?: (item: T) => ReactNode;
}) {
  return (
    <div
      id={p.id}
      role="option"
      aria-selected={p.checked ?? false}
      data-index={p.index}
      ref={measure}
      onClick={(e) => p.onActivate(p.index, e)}
      onDoubleClick={(e) => {
        if (!p.dir && p.onOpen && !(e.target as HTMLElement).closest(INTERACTIVE)) p.onOpen(p.item);
      }}
      onContextMenu={p.onContextMenu && ((e) => p.onContextMenu!(p.item, e))}
      className={`absolute inset-x-0 flex cursor-pointer select-none items-center gap-3 border-t border-white/5 px-3 py-1.5 text-sm first:border-t-0 ${
        p.checked ? "bg-ogc-teal/10" : "hover:bg-slate-800/40"
      } ${p.active ? "outline-1 -outline-offset-1 outline-ogc-teal/60" : ""}`}
      style={{ top }}
    >
      <Check {...p} />
      <div className="shrink-0">{renderIcon(p.item)}</div>
      <div className="min-w-0 flex-1">
        <div className={`truncate ${p.dir ? "font-medium text-white" : "text-slate-200"}`} title={p.name}>
          {p.name}
        </div>
        {renderSubtitle && <div className="mt-0.5 min-w-0">{renderSubtitle(p.item)}</div>}
      </div>
      {columns.map((c) => (
        <div key={c.key} className={`${c.className} shrink-0 truncate ${c.align === "right" ? "text-right" : ""}`}>
          {c.render(p.item)}
        </div>
      ))}
      {renderActions && <div className="flex w-8 shrink-0 justify-end sm:w-24">{renderActions(p.item)}</div>}
    </div>
  );
}

function Tile<T>({
  renderTile,
  renderTileBadge,
  ...p
}: ItemProps<T> & { renderTile: (item: T) => ReactNode; renderTileBadge?: (item: T) => ReactNode }) {
  return (
    <div
      id={p.id}
      role="option"
      aria-selected={p.checked ?? false}
      onClick={(e) => p.onActivate(p.index, e)}
      onDoubleClick={(e) => {
        if (!p.dir && p.onOpen && !(e.target as HTMLElement).closest(INTERACTIVE)) p.onOpen(p.item);
      }}
      onContextMenu={p.onContextMenu && ((e) => p.onContextMenu!(p.item, e))}
      className="group min-w-0 cursor-pointer select-none"
      title={p.name}
    >
      <div
        className={`relative aspect-square overflow-hidden rounded-lg bg-slate-900 ${
          p.checked ? "ring-2 ring-ogc-teal" : p.active ? "ring-2 ring-ogc-teal/50" : "ring-1 ring-white/5"
        }`}
      >
        <div className={`h-full w-full transition ${p.checked ? "scale-[0.92]" : ""}`}>{renderTile(p.item)}</div>
        {p.checked !== undefined && p.canSelect && (
          <button
            tabIndex={-1}
            aria-label={`${p.name} auswählen`}
            onClick={(e) => {
              e.stopPropagation();
              p.onPick(p.index, e.shiftKey);
            }}
            className={`absolute left-1.5 top-1.5 flex h-5 w-5 items-center justify-center rounded-full border text-[11px] font-bold ${
              p.checked
                ? "border-ogc-teal bg-ogc-teal text-white"
                : "border-white/70 bg-black/30 text-transparent opacity-0 group-hover:opacity-100 [@media(hover:none)]:opacity-100"
            }`}
          >
            ✓
          </button>
        )}
        {renderTileBadge && (
          // Dark backing so tinted badges stay legible on bright photos.
          <div className="absolute bottom-1.5 left-1.5 right-1.5 flex [&>*]:max-w-full [&>*]:bg-slate-950/85">
            {renderTileBadge(p.item)}
          </div>
        )}
      </div>
      <div className={`truncate px-0.5 pt-1 text-xs ${p.dir ? "font-medium text-white" : "text-slate-300"}`}>{p.name}</div>
    </div>
  );
}

/** Generic file-type glyph for items without a thumbnail. */
export function FileGlyph({ dir = false, className = "h-5 w-5" }: { dir?: boolean; className?: string }) {
  const common = { fill: "none", stroke: "currentColor", strokeWidth: 1.6, strokeLinecap: "round", strokeLinejoin: "round" } as const;
  return dir ? (
    <svg viewBox="0 0 24 24" className={`${className} text-ogc-teal`} {...common}>
      <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
    </svg>
  ) : (
    <svg viewBox="0 0 24 24" className={`${className} text-slate-500`} {...common}>
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
      <path d="M14 3v5h5" />
    </svg>
  );
}
