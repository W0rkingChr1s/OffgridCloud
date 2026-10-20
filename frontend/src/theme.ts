// Colour theme: dark (default), light, or follow the OS.
//
// The light theme doesn't touch the markup: it redefines Tailwind's colour
// variables under html.light (see index.css). That only works where the
// browser evaluates color-mix() — older engines get Tailwind's static
// fallbacks for every "/opacity" class and would end up half dark — so on
// those the light theme is simply not offered.
//
// index.html applies the stored choice before first paint (keep KEY and the
// resolve logic there in sync).

export type ThemePref = "dark" | "light" | "system";
type Theme = "dark" | "light";

const KEY = "ogc_theme";
const LIGHT_QUERY = "(prefers-color-scheme: light)";

export function lightSupported(): boolean {
  return typeof CSS !== "undefined" && !!CSS.supports?.("color", "color-mix(in oklab, red, blue)");
}

export function getThemePref(): ThemePref {
  try {
    const v = localStorage.getItem(KEY);
    if (v === "light" || v === "system") return v;
  } catch {
    // storage blocked (private mode) → default
  }
  return "dark";
}

function resolve(pref: ThemePref): Theme {
  if (!lightSupported()) return "dark";
  if (pref === "system") return window.matchMedia(LIGHT_QUERY).matches ? "light" : "dark";
  return pref;
}

export function applyTheme(pref: ThemePref = getThemePref()): void {
  const theme = resolve(pref);
  const root = document.documentElement;
  root.classList.toggle("light", theme === "light");
  root.classList.toggle("dark", theme === "dark");
}

export function setThemePref(pref: ThemePref): void {
  try {
    localStorage.setItem(KEY, pref);
  } catch {
    // not persisted — still applied for this page view
  }
  applyTheme(pref);
}

/** Re-apply when the OS switches between light and dark (pref "system"). */
export function watchSystemTheme(): () => void {
  const mq = window.matchMedia(LIGHT_QUERY);
  const onChange = () => {
    if (getThemePref() === "system") applyTheme("system");
  };
  mq.addEventListener("change", onChange);
  return () => mq.removeEventListener("change", onChange);
}
