import { useEffect, useRef, useState } from "react";

/**
 * A small "ⓘ" affordance that reveals an explanatory bubble on hover, keyboard
 * focus or tap. Meant to sit next to a setting label / checkbox so the "why"
 * and "what happens" are one click away without cluttering the form.
 */
export default function InfoTip({ text, label = "Mehr Informationen" }: { text: string; label?: string }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);

  // Dismiss on outside click / Escape (matters for the tap-to-open path).
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

  return (
    <span
      ref={ref}
      className="relative inline-flex align-middle"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        aria-label={label}
        aria-expanded={open}
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        className="flex h-4 w-4 items-center justify-center rounded-full border border-slate-500/60 text-[10px] font-bold leading-none text-slate-400 transition hover:border-ogc-teal hover:text-ogc-teal focus:outline-none focus-visible:ring-2 focus-visible:ring-ogc-teal"
      >
        i
      </button>
      {open && (
        <span
          role="tooltip"
          className="absolute bottom-full left-1/2 z-40 mb-2 w-60 -translate-x-1/2 rounded-lg border border-white/10 bg-slate-800 px-3 py-2 text-xs font-normal leading-relaxed text-slate-200 shadow-xl"
        >
          {text}
          <span className="absolute left-1/2 top-full h-2 w-2 -translate-x-1/2 -translate-y-1/2 rotate-45 border-b border-r border-white/10 bg-slate-800" />
        </span>
      )}
    </span>
  );
}
