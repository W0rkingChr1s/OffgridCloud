import { useEffect, useState } from "react";
import { getToken, type ServerNotice } from "./api";

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
    /** The uplink in use is metered (e.g. a USB-tethered phone). */
    metered?: boolean;
    uplink?: string;
    effective_bwlimit_kbps: number;
    last_kbps: number;
    gated: boolean;
    gate_reason: string;
  };
}

type Listener = (snapshot: Snapshot & { notices?: ServerNotice[] }) => void;

// One EventSource per tab, shared by every subscriber (dashboard pages and the
// app-wide TransferToaster). It is closed while the tab is hidden — a sleeping
// iPad otherwise keeps a stream open that the box keeps feeding for nobody —
// and reopened when the tab comes back.
const listeners = new Set<Listener>();
let source: EventSource | null = null;
let sourceToken: string | null = null;

function sync(): void {
  const token = getToken();
  const want = listeners.size > 0 && !!token && document.visibilityState !== "hidden";
  if (source && (!want || sourceToken !== token)) {
    source.close();
    source = null;
  }
  if (!want || source) return;
  sourceToken = token;
  source = new EventSource(`/api/events?token=${encodeURIComponent(token!)}`);
  source.onmessage = (e) => {
    let snap: Parameters<Listener>[0];
    try {
      snap = JSON.parse(e.data);
    } catch {
      return; // ignore malformed frame
    }
    for (const l of listeners) l(snap);
  };
  // EventSource reconnects automatically on error; nothing to do here.
}

if (typeof document !== "undefined") {
  document.addEventListener("visibilitychange", sync);
}

/** Receive every live snapshot until the returned function is called. */
export function subscribeEvents(listener: Listener): () => void {
  listeners.add(listener);
  sync();
  return () => {
    listeners.delete(listener);
    sync();
  };
}

/** Subscribe to the server's live state via SSE. Returns the latest snapshot. */
export function useEvents(): Snapshot | null {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  useEffect(() => subscribeEvents(setSnapshot), []);
  return snapshot;
}
