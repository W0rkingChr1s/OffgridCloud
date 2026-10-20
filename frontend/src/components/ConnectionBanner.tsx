import { useEffect, useState } from "react";
import { rootCaUrls, UNREACHABLE_EVENT } from "../api";

// The service worker serves the cached app shell when the box doesn't answer,
// so the page looks fine while every API call fails. Typical cause on
// https://<name>.local: Caddy's tls-internal certificate rotated (every ~12 h)
// and the browser's one-off exception no longer covers it — Safari then fails
// requests silently instead of warning again. This banner makes that visible.

async function probe(): Promise<boolean> {
  try {
    await fetch("/api/health", { cache: "no-store" });
    return true;
  } catch {
    return false;
  }
}

/** Drop the service worker + its cache and reload: the next navigation then
 *  really hits the network, so the browser shows its certificate warning. */
async function reconnect() {
  try {
    const regs = (await navigator.serviceWorker?.getRegistrations()) ?? [];
    await Promise.all(regs.map((r) => r.unregister()));
    const keys = (await window.caches?.keys()) ?? [];
    await Promise.all(keys.map((k) => caches.delete(k)));
  } finally {
    window.location.reload();
  }
}

const isApple = /iPad|iPhone|iPod|Macintosh/.test(navigator.userAgent);

export default function ConnectionBanner() {
  const [down, setDown] = useState(false);

  useEffect(() => {
    let alive = true;
    const check = () => void probe().then((ok) => alive && setDown(!ok));
    check();
    window.addEventListener(UNREACHABLE_EVENT, check);
    window.addEventListener("online", check);
    return () => {
      alive = false;
      window.removeEventListener(UNREACHABLE_EVENT, check);
      window.removeEventListener("online", check);
    };
  }, []);

  if (!down) return null;

  const certLikely =
    window.location.protocol === "https:" && window.location.hostname.endsWith(".local");
  const ca = rootCaUrls();
  const link = "font-semibold text-amber-100 underline underline-offset-2";

  return (
    <div
      role="alert"
      className="sticky top-0 z-50 border-b border-amber-400/30 bg-amber-950/95 px-4 py-3 text-sm text-amber-100 shadow-lg backdrop-blur"
    >
      <div className="mx-auto flex max-w-3xl flex-col gap-2">
        <p>
          <strong>Box nicht erreichbar.</strong>{" "}
          {certLikely
            ? "Wahrscheinlich vertraut dein Gerät dem Zertifikat nicht mehr – die Box erneuert es regelmäßig. Angezeigt wird nur eine zwischengespeicherte Seite."
            : "Angezeigt wird nur eine zwischengespeicherte Seite. Netzwerk und Box prüfen."}
        </p>
        {certLikely && (
          <p className="text-amber-200/90">
            Dauerhaft beheben: einmal das Root-Zertifikat der Box installieren{" "}
            {isApple ? (
              <>
                (<a className={link} href={ca.profile}>Profil für iPhone/iPad/Mac</a>, danach unter
                Einstellungen → Allgemein → Info → Zertifikatsvertrauenseinstellungen
                „Volles Vertrauen“ einschalten)
              </>
            ) : (
              <>
                (<a className={link} href={ca.crt}>Zertifikat herunterladen</a> und als
                vertrauenswürdige Stammzertifizierungsstelle importieren)
              </>
            )}
            .
          </p>
        )}
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => void reconnect()}
            className="rounded-lg bg-amber-400/20 px-3 py-1.5 font-semibold hover:bg-amber-400/30"
          >
            {certLikely ? "Neu verbinden (Zertifikat erneut bestätigen)" : "Neu verbinden"}
          </button>
          <button
            type="button"
            onClick={() => void probe().then((ok) => setDown(!ok))}
            className="rounded-lg border border-amber-400/30 px-3 py-1.5 hover:bg-amber-400/10"
          >
            Erneut prüfen
          </button>
        </div>
      </div>
    </div>
  );
}
