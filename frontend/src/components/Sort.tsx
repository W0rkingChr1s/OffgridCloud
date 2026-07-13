import { useMemo, useState } from "react";
import type { ReactNode } from "react";

export type SortDir = "asc" | "desc";

/** A single sort criterion: a stable key, a human label and a value getter. */
export interface SortOption<T> {
  key: string;
  label: string;
  get: (item: T) => string | number | boolean | null | undefined;
}

export interface SortState<T> {
  options: SortOption<T>[];
  sorted: T[];
  key: string;
  dir: SortDir;
  /** Set the active field, keeping the current direction. */
  setKey: (key: string) => void;
  /** Flip ascending ⇄ descending. */
  toggleDir: () => void;
  /** Table-header behaviour: same field toggles direction, a new one resets to asc. */
  sortBy: (key: string) => void;
}

/**
 * Sorts `items` by the currently selected option. Empty values (null / undefined /
 * "") always sink to the bottom, regardless of direction. Strings compare with a
 * locale-aware, numeric, case-insensitive collator so "Box 2" precedes "Box 10".
 */
export function useSort<T>(
  items: T[],
  options: SortOption<T>[],
  initial?: { key?: string; dir?: SortDir },
): SortState<T> {
  const [key, setKey] = useState(initial?.key ?? options[0]?.key ?? "");
  const [dir, setDir] = useState<SortDir>(initial?.dir ?? "asc");

  const sorted = useMemo(() => {
    const opt = options.find((o) => o.key === key) ?? options[0];
    if (!opt) return items;
    return [...items].sort((a, b) => {
      const av = opt.get(a);
      const bv = opt.get(b);
      const aEmpty = av === null || av === undefined || av === "";
      const bEmpty = bv === null || bv === undefined || bv === "";
      if (aEmpty || bEmpty) {
        if (aEmpty && bEmpty) return 0;
        return aEmpty ? 1 : -1; // empties always last
      }
      let r: number;
      if (typeof av === "number" && typeof bv === "number") r = av - bv;
      else if (typeof av === "boolean" && typeof bv === "boolean") r = Number(av) - Number(bv);
      else r = String(av).localeCompare(String(bv), "de", { numeric: true, sensitivity: "base" });
      return dir === "asc" ? r : -r;
    });
  }, [items, options, key, dir]);

  return {
    options,
    sorted,
    key,
    dir,
    setKey,
    toggleDir: () => setDir((d) => (d === "asc" ? "desc" : "asc")),
    sortBy: (k: string) => {
      if (k === key) {
        setDir((d) => (d === "asc" ? "desc" : "asc"));
      } else {
        setKey(k);
        setDir("asc");
      }
    },
  };
}

/**
 * Compact "Sortieren: [field ▾] [↑]" dropdown for card / non-table lists.
 */
export function SortMenu<T>({
  sort,
  className = "",
}: {
  sort: SortState<T>;
  className?: string;
}) {
  return (
    <div className={`flex items-center gap-1.5 text-sm ${className}`}>
      <span className="text-xs text-slate-500">Sortieren:</span>
      <select
        value={sort.key}
        onChange={(e) => sort.setKey(e.target.value)}
        aria-label="Sortierfeld"
        className="rounded-lg border border-white/10 bg-slate-800/60 px-2 py-1 text-sm text-white outline-none focus:border-ogc-teal/50"
      >
        {sort.options.map((o) => (
          <option key={o.key} value={o.key}>
            {o.label}
          </option>
        ))}
      </select>
      <button
        type="button"
        onClick={sort.toggleDir}
        title={sort.dir === "asc" ? "Aufsteigend" : "Absteigend"}
        aria-label={sort.dir === "asc" ? "Aufsteigend" : "Absteigend"}
        className="rounded-lg border border-white/10 px-2 py-1 leading-none text-slate-300 hover:bg-white/5"
      >
        {sort.dir === "asc" ? "↑" : "↓"}
      </button>
    </div>
  );
}

/**
 * Clickable table header cell. Click toggles direction on the active column or
 * switches to this column (ascending). Renders a direction indicator.
 */
export function SortTh<T>({
  sort,
  field,
  children,
  className = "",
}: {
  sort: SortState<T>;
  field: string;
  children: ReactNode;
  className?: string;
}) {
  const active = sort.key === field;
  return (
    <th
      scope="col"
      onClick={() => sort.sortBy(field)}
      aria-sort={active ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}
      className={`cursor-pointer select-none px-4 py-3 hover:text-slate-200 ${className}`}
    >
      <span className="inline-flex items-center gap-1">
        {children}
        <span className={`text-xs ${active ? "text-ogc-teal" : "text-slate-600"}`} aria-hidden>
          {active ? (sort.dir === "asc" ? "▲" : "▼") : "↕"}
        </span>
      </span>
    </th>
  );
}
