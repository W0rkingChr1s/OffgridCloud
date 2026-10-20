import { useEffect, useRef, useState } from "react";

export type MoreMenuItem = {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  danger?: boolean;
  title?: string;
};

/**
 * A "⋯" button that tucks rarely used or destructive actions into a small
 * dropdown, so a card only shows the one or two things people actually do.
 */
export default function MoreMenu({ items, label = "Weitere Aktionen" }: { items: MoreMenuItem[]; label?: string }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (items.length === 0) return null;

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="rounded-lg border border-white/10 px-2.5 py-1.5 text-xs font-medium leading-none text-slate-300 hover:bg-white/5"
      >
        ⋯
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 z-30 mt-1 min-w-48 overflow-hidden rounded-xl bg-slate-900 py-1 shadow-xl ring-1 ring-white/10"
        >
          {items.map((it) => (
            <button
              key={it.label}
              type="button"
              role="menuitem"
              disabled={it.disabled}
              title={it.title}
              onClick={() => {
                setOpen(false);
                it.onClick();
              }}
              className={`block w-full px-3 py-2 text-left text-sm hover:bg-white/5 disabled:cursor-not-allowed disabled:opacity-40 ${
                it.danger ? "text-red-300" : "text-slate-200"
              }`}
            >
              {it.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
