import { useEffect, useState } from "react";
import { getToken } from "./api";

export interface FolderSnapshot {
  id: number;
  name: string;
  total: number;
  done: number;
  uploading: number;
  queued: number;
  failed: number;
}

export interface ActiveTransfer {
  id: number;
  filename: string;
  provider: string;
  bytes: number;
  total: number;
  progress: number;
  kbps: number;
}

export interface Snapshot {
  folders: FolderSnapshot[];
  transfers?: { counts: Record<string, number>; active: ActiveTransfer[] };
  bandwidth?: {
    enabled: boolean;
    effective_bwlimit_kbps: number;
    last_kbps: number;
    gated: boolean;
    gate_reason: string;
  };
}

/** Subscribe to the server's live state via SSE. Returns the latest snapshot. */
export function useEvents(): Snapshot | null {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);

  useEffect(() => {
    const token = getToken();
    if (!token) return;
    const es = new EventSource(`/api/events?token=${encodeURIComponent(token)}`);
    es.onmessage = (e) => {
      try {
        setSnapshot(JSON.parse(e.data));
      } catch {
        /* ignore malformed frame */
      }
    };
    // EventSource reconnects automatically on error; nothing to do here.
    return () => es.close();
  }, []);

  return snapshot;
}
