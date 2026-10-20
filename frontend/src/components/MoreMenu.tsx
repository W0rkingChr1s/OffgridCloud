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
 * The dropdown is positioned against the viewport, so a clipping parent (a
 * rounded table, a scroll box) can't cut it off; near the bottom it opens upward.
 */
export default function MoreMenu({ items, label = "Weitere Aktionen" }: { items: MoreMenuItem[]; label?: string }) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ right: number; top?: number; bottom?: number }>({ right: 0 });
  const ref = useRef<HTMLDivElement>(null);
  const btnRef = useRef<HTMLButtonElement>(null);

  function toggle() {
    const r = btnRef.current?.getBoundingClientRect();
    if (r && !open) {
      const right = window.innerWidth - r.right;
      // Roughly the height of a few items; flip up when it wouldn't fit below.
      const flip = window.innerHeight - r.bottom < 40 * items.length + 16;
      setPos(flip ? { right, bottom: window.innerHeight - r.top + 4 } : { right, top: r.bottom + 4 });
    }
    setOpen((o) => !o);
  }

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    // A fixed dropdown would drift away from its button, so just close it.
    const close = () => setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
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
        ref={btnRef}
        onClick={toggle}
        className="rounded-lg border border-white/10 px-2.5 py-1.5 text-xs font-medium leading-none text-slate-300 hover:bg-white/5"
      >
        ⋯
      </button>
      {open && (
        <div
          role="menu"
          style={pos}
          className="fixed z-40 min-w-48 overflow-hidden rounded-xl bg-slate-900 py-1 shadow-xl ring-1 ring-white/10"
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
