import { useState } from "react";
import { getThemePref, lightSupported, setThemePref, type ThemePref } from "../theme";

const OPTIONS: { value: ThemePref; label: string }[] = [
  { value: "dark", label: "Dunkel" },
  { value: "light", label: "Hell" },
  { value: "system", label: "System" },
];

/** Dark / light / follow-the-OS switch for the menu drawer. */
export default function ThemeToggle() {
  const [pref, setPref] = useState<ThemePref>(getThemePref());

  if (!lightSupported()) return null;

  function choose(value: ThemePref) {
    setThemePref(value);
    setPref(value);
  }

  return (
    <div className="rounded-xl border border-white/10 px-4 py-3">
      <div className="mb-2 flex items-center gap-2 text-sm font-medium text-slate-200">
        <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 3a9 9 0 1 0 9 9 7 7 0 0 1-9-9Z" />
        </svg>
        Darstellung
      </div>
      <div role="radiogroup" aria-label="Darstellung" className="grid grid-cols-3 gap-1 rounded-lg bg-white/5 p-1">
        {OPTIONS.map((o) => (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={pref === o.value}
            onClick={() => choose(o.value)}
            className={`rounded-md px-2 py-1.5 text-xs font-medium transition ${
              pref === o.value ? "bg-ogc-teal/20 text-white ring-1 ring-ogc-teal/40" : "text-slate-400 active:bg-white/5"
            }`}
          >
            {o.label}
          </button>
        ))}
      </div>
    </div>
  );
}
