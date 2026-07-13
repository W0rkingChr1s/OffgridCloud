/**
 * Foreground OS push notifications via the Web Notifications API.
 *
 * Scope: works while the site is open on the device (incl. backgrounded as an
 * installed PWA) — no server-side Web Push / VAPID needed. When the page is
 * visible the in-app toast is enough, so a system notification is only raised
 * while the tab/PWA is hidden. Opt-in and per-device (browser permission +
 * a local preference).
 */

const PREF_KEY = "ogc_notify";

export function notificationsSupported(): boolean {
  return typeof window !== "undefined" && "Notification" in window;
}

export function permission(): NotificationPermission {
  return notificationsSupported() ? Notification.permission : "denied";
}

function prefOn(): boolean {
  return localStorage.getItem(PREF_KEY) === "1";
}

export function isEnabled(): boolean {
  return notificationsSupported() && permission() === "granted" && prefOn();
}

/** Ask for permission (must run from a user gesture) and, if granted, turn the
 * preference on. Returns the resulting enabled state. */
export async function requestAndEnable(): Promise<boolean> {
  if (!notificationsSupported()) return false;
  let perm = permission();
  if (perm === "default") perm = await Notification.requestPermission();
  const enabled = perm === "granted";
  localStorage.setItem(PREF_KEY, enabled ? "1" : "0");
  return enabled;
}

export function disable(): void {
  localStorage.setItem(PREF_KEY, "0");
}

/** Raise a system notification for an important event, if enabled and the page
 * is currently hidden. Best-effort — never throws. */
export function osNotify(title: string, body?: string): void {
  if (!isEnabled()) return;
  if (document.visibilityState !== "hidden") return; // visible: the toast suffices
  const opts: NotificationOptions = {
    body,
    icon: "/icon-192.png",
    badge: "/icon-192.png",
    tag: "offgridcloud", // collapse rapid bursts into one
  };
  // Prefer the service worker: `new Notification()` is unreliable on mobile.
  if (navigator.serviceWorker?.controller) {
    navigator.serviceWorker.ready
      .then((reg) => reg.showNotification(title, opts))
      .catch(() => fallback(title, opts));
  } else {
    fallback(title, opts);
  }
}

function fallback(title: string, opts: NotificationOptions): void {
  try {
    new Notification(title, opts);
  } catch {
    /* some browsers only allow SW notifications; ignore */
  }
}
