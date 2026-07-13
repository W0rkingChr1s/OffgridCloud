// Branded startup banner for the browser DevTools console.
// Prints app / build info, a self-XSS safety warning, and a playful nudge
// toward the hidden retro mode. Invisible in the normal UI — a little treat
// for anyone who opens the console.

import type { Health } from "./api";

const REPO = "https://github.com/W0rkingChr1s/OffgridCloud";

/**
 * The real deployed version comes from the backend (`app.__version__`,
 * stamped from the git tag by the installer) and is exposed unauthenticated
 * via /api/health. The frontend package.json version is a stale build-time
 * constant, so we ask the backend instead. Falls back to "dev" when the
 * backend isn't reachable (plain frontend dev server, offline, etc.).
 */
async function fetchVersion(): Promise<string> {
  try {
    const res = await fetch("/api/health");
    if (!res.ok) return "dev";
    const health = (await res.json()) as Health;
    return health.version || "dev";
  } catch {
    return "dev";
  }
}

// Shared style fragments so every line reads as one system.
const S = {
  brand:
    "color:#0EA5A4;font:700 22px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace;" +
    "text-shadow:0 0 10px rgba(14,165,164,0.45)",
  tagline: "color:#94a3b8;font:400 12px ui-monospace,monospace",
  label: "color:#0EA5A4;font:600 12px ui-monospace,monospace",
  value: "color:#e2e8f0;font:400 12px ui-monospace,monospace",
  warnHead:
    "color:#fecaca;background:#7f1d1d;font:700 13px ui-monospace,monospace;padding:2px 8px",
  warnBody: "color:#fca5a5;font:400 12px/1.5 ui-monospace,monospace",
  retro:
    "color:#33ff66;font:400 12px/1.5 ui-monospace,monospace;" +
    "text-shadow:0 0 4px rgba(51,255,102,0.6)",
};

export async function printConsoleBanner() {
  if (typeof window === "undefined" || typeof console === "undefined") return;

  const mode = import.meta.env.DEV ? "development" : "production";
  const version = await fetchVersion();

  // Header + tagline.
  console.log("%cOffgridCloud", S.brand);
  console.log("%cUpload when the signal is right.", S.tagline);

  // Build / project info block.
  const row = (label: string, value: string) =>
    console.log(`%c${label.padEnd(9)}%c${value}`, S.label, S.value);
  row("Version", `v${version}`);
  row("Build", mode);
  row("Source", REPO);

  // Self-XSS safety warning — standard for anything with an auth session.
  console.log("%c ⚠  STOPP ", S.warnHead);
  console.log(
    "%cDiese Konsole ist für Entwickler gedacht. Wenn dir jemand sagt, du " +
      "sollst hier\netwas einfügen, um ein Feature freizuschalten oder ein " +
      "Konto zu „hacken“,\nist das ein Betrug (Self-XSS) und gibt Fremden " +
      "Zugriff auf dein Konto.",
    S.warnBody,
  );

  // Playful nudge toward the hidden retro mode. Deliberately partial —
  // the last keys are left to memory so finding it still feels earned.
  console.log(
    "%c▟▛ Nostalgie nach grünem Phosphor? Der Code, den jedes 80er-Kind " +
      "kennt:\n   ↑ ↑ ↓ ↓ ← → ← → … den Rest kennst du.",
    S.retro,
  );
}
