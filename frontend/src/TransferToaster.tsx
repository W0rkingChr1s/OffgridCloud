import { useEffect, useRef } from "react";
import { getToken } from "./api";
import { useAuth } from "./auth";
import { useToast } from "./toast";

/**
 * App-wide toaster that watches the live SSE snapshot and raises a toast when
 * transfers cross into "done" or "failed". The transfer overview is admin-only
 * in the snapshot, so this is effectively a no-op for regular users (who still
 * get their own per-file upload toasts in the folder view).
 */
export default function TransferToaster() {
  const { user } = useAuth();
  const toast = useToast();
  const prev = useRef<{ done: number; failed: number } | null>(null);

  useEffect(() => {
    const token = getToken();
    if (!token || !user) return;
    prev.current = null; // fresh baseline per session — never toast the first frame
    const es = new EventSource(`/api/events?token=${encodeURIComponent(token)}`);
    es.onmessage = (e) => {
      let snap: { transfers?: { counts?: Record<string, number> } };
      try {
        snap = JSON.parse(e.data);
      } catch {
        return;
      }
      const counts = snap?.transfers?.counts;
      if (!counts) return;
      const done = counts.done ?? 0;
      const failed = counts.failed ?? 0;
      if (prev.current) {
        const newlyDone = done - prev.current.done;
        const newlyFailed = failed - prev.current.failed;
        if (newlyDone > 0) {
          toast.success(
            "Transfer abgeschlossen",
            `${newlyDone} Transfer${newlyDone > 1 ? "s" : ""} in die Cloud fertig.`,
          );
        }
        if (newlyFailed > 0) {
          toast.error(
            "Transfer fehlgeschlagen",
            `${newlyFailed} Transfer${newlyFailed > 1 ? "s" : ""} endgültig gescheitert.`,
          );
        }
      }
      prev.current = { done, failed };
    };
    return () => es.close();
  }, [user, toast]);

  return null;
}
