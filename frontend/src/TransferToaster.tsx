import { useEffect, useRef } from "react";
import { getToken, type ServerNotice } from "./api";
import { useAuth } from "./auth";
import { useToast, type ToastVariant } from "./toast";

/**
 * App-wide toaster that watches the live SSE snapshot and raises a toast when
 * transfers cross into "done" or "failed". The transfer overview is admin-only
 * in the snapshot, so this is effectively a no-op for regular users (who still
 * get their own per-file upload toasts in the folder view).
 *
 * It also surfaces server-side status notices (startup summary, reconnect ping,
 * bandwidth pause/resume — see app/notices.py) that ride along in the snapshot.
 */
export default function TransferToaster() {
  const { user } = useAuth();
  const toast = useToast();
  const prev = useRef<{ done: number; failed: number } | null>(null);
  // Highest notice id already toasted; -1 until the first frame seeds a baseline
  // so we never replay old notices as a burst on (re)connect.
  const lastNotice = useRef<number>(-1);

  useEffect(() => {
    const token = getToken();
    if (!token || !user) return;
    prev.current = null; // fresh baseline per session — never toast the first frame
    lastNotice.current = -1;
    const es = new EventSource(`/api/events?token=${encodeURIComponent(token)}`);
    es.onmessage = (e) => {
      let snap: {
        transfers?: { counts?: Record<string, number> };
        notices?: ServerNotice[];
      };
      try {
        snap = JSON.parse(e.data);
      } catch {
        return;
      }
      raiseNotices(snap?.notices, lastNotice, toast);
      const counts = snap?.transfers?.counts;
      if (!counts) return;
      const done = counts.done ?? 0;
      const failed = counts.failed ?? 0;
      if (prev.current) {
        const newlyDone = done - prev.current.done;
        const newlyFailed = failed - prev.current.failed;
        if (newlyDone > 0) {
          toast.push({
            variant: "success",
            title: "Transfer abgeschlossen",
            message: `${newlyDone} Transfer${newlyDone > 1 ? "s" : ""} in die Cloud fertig.`,
            os: true,
          });
        }
        if (newlyFailed > 0) {
          toast.push({
            variant: "error",
            title: "Transfer fehlgeschlagen",
            message: `${newlyFailed} Transfer${newlyFailed > 1 ? "s" : ""} endgültig gescheitert.`,
            os: true,
          });
        }
      }
      prev.current = { done, failed };
    };
    return () => es.close();
  }, [user, toast]);

  return null;
}

type ToastPush = ReturnType<typeof useToast>["push"];

/** Toast any notices newer than the last one seen. The first frame only seeds
 * the baseline (no toast) so a page load never replays historical notices. */
function raiseNotices(
  notices: ServerNotice[] | undefined,
  lastSeen: React.MutableRefObject<number>,
  toast: { push: ToastPush },
): void {
  if (!notices?.length) return;
  const maxId = notices.reduce((m, n) => Math.max(m, n.id), lastSeen.current);
  if (lastSeen.current < 0) {
    lastSeen.current = maxId; // first frame: baseline only
    return;
  }
  for (const n of notices) {
    if (n.id <= lastSeen.current) continue;
    toast.push({
      variant: n.level as ToastVariant,
      title: n.title,
      message: n.message || undefined,
      os: true,
    });
  }
  lastSeen.current = maxId;
}
