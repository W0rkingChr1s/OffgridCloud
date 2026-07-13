import { useState } from "react";
import {
  disable,
  isEnabled,
  notificationsSupported,
  permission,
  requestAndEnable,
} from "../notifications";

/**
 * Per-device opt-in for foreground OS push notifications. Requesting the
 * browser permission must happen from a user gesture, so it lives behind this
 * button in the menu drawer.
 */
export default function NotifyToggle() {
  const supported = notificationsSupported();
  const [enabled, setEnabled] = useState(isEnabled());
  const [perm, setPerm] = useState(permission());
  const [busy, setBusy] = useState(false);

  if (!supported) {
    return (
      <p className="px-4 py-2 text-xs text-slate-500">
        Push-Benachrichtigungen werden von diesem Browser nicht unterstützt (auf dem iPhone erst
        nach „Zum Home-Bildschirm").
      </p>
    );
  }

  async function toggle() {
    setBusy(true);
    try {
      if (enabled) {
        disable();
        setEnabled(false);
      } else {
        const ok = await requestAndEnable();
        setEnabled(ok);
        setPerm(permission());
      }
    } finally {
      setBusy(false);
    }
  }

  const blocked = perm === "denied";

  return (
    <div>
      <button
        onClick={toggle}
        disabled={busy || blocked}
        aria-pressed={enabled}
        className="flex w-full items-center justify-between gap-2 rounded-xl border border-white/10 px-4 py-3 text-sm font-medium text-slate-200 active:bg-white/5 disabled:opacity-50"
      >
        <span className="flex items-center gap-2">
          <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9M13.7 21a2 2 0 0 1-3.4 0" />
          </svg>
          Push aufs Gerät
        </span>
        <span
          className={`relative h-5 w-9 shrink-0 rounded-full transition ${
            enabled ? "bg-ogc-teal" : "bg-slate-600"
          }`}
        >
          <span
            className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition-all ${
              enabled ? "left-[1.125rem]" : "left-0.5"
            }`}
          />
        </span>
      </button>
      {blocked && (
        <p className="mt-1 px-1 text-xs text-slate-500">Im Browser blockiert — in den Website-Einstellungen erlauben.</p>
      )}
      {!blocked && (
        <p className="mt-1 px-1 text-xs text-slate-500">
          Meldet Upload-/Transfer-Status, während die App im Hintergrund läuft.
        </p>
      )}
    </div>
  );
}
