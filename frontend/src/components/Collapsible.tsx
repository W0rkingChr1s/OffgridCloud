import type { ReactNode } from "react";

/**
 * A card whose body folds away behind its header. For settings that are set
 * once and then left alone, so they don't push the everyday things off-screen.
 * `summary` is shown next to the title while folded (e.g. "eingerichtet").
 */
export default function Collapsible({
  title,
  summary,
  defaultOpen = false,
  nested = false,
  children,
}: {
  title: ReactNode;
  summary?: ReactNode;
  defaultOpen?: boolean;
  /** Render as a row inside another card instead of a card of its own. */
  nested?: boolean;
  children: ReactNode;
}) {
  return (
    <details
      open={defaultOpen}
      className={`group ${
        nested ? "rounded-xl bg-slate-900/50 ring-1 ring-white/5" : "mb-6 rounded-2xl bg-slate-800/60 ring-1 ring-white/10"
      }`}
    >
      <summary
        className={`flex cursor-pointer select-none list-none items-center gap-3 [&::-webkit-details-marker]:hidden ${
          nested ? "px-4 py-3" : "p-5"
        }`}
      >
        <span className={`whitespace-nowrap text-sm font-medium ${nested ? "text-slate-200" : "text-slate-400"}`}>
          {title}
        </span>
        {summary && <span className="min-w-0 truncate text-xs text-slate-500">{summary}</span>}
        <span aria-hidden className="ml-auto text-slate-500 transition-transform group-open:rotate-180">
          ▾
        </span>
      </summary>
      <div className={nested ? "px-4 pb-4" : "px-5 pb-5"}>{children}</div>
    </details>
  );
}
