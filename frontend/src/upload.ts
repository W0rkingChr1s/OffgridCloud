import { ApiError, getToken, type MediaItem } from "./api";

const CHUNK_SIZE = 4 * 1024 * 1024; // 4 MiB

// A request that moves no bytes (and gets no answer) for this long is treated
// as dead and retried. Measured from the last upload progress, so a slow but
// moving link is never cut off — only a stalled one.
const IDLE_TIMEOUT_MS = 45_000;
// Finalizing hashes the whole file on the box; on a Pi with a USB buffer that
// can take a while for large videos, so give it far more room.
const COMPLETE_TIMEOUT_MS = 10 * 60_000;
// Consecutive failures without progress before giving up. Any chunk that gets
// through resets the count, so a long upload survives many short dropouts.
const MAX_ATTEMPTS = 6;
const BACKOFF_MS = [1_000, 2_000, 4_000, 8_000, 15_000];

interface Session {
  id: string;
  received: number;
}

export interface UploadCallbacks {
  /** Fraction in [0, 1]. */
  onProgress: (fraction: number) => void;
  /** Called before a retry (1-based attempt) and with `null` once it recovered. */
  onRetry?: (info: { attempt: number; max: number; reason: string } | null) => void;
}

/**
 * Upload a single file to a folder using the resumable chunk protocol.
 *
 * Every request has an idle timeout. A stalled or failed chunk is retried with
 * backoff after asking the box for its committed offset, so the upload resumes
 * exactly where the box stands instead of hanging or starting over.
 */
export async function uploadFile(
  folderId: number,
  file: File,
  callbacks: UploadCallbacks | ((fraction: number) => void),
  signal?: AbortSignal,
): Promise<MediaItem> {
  const { onProgress, onRetry } =
    typeof callbacks === "function" ? { onProgress: callbacks, onRetry: undefined } : callbacks;
  const token = getToken();
  const authHeaders: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {};

  let failures = 0;
  let retrying = false;
  // Run `fn`, retrying transient failures with backoff. `resync` runs before
  // each retry (e.g. to fetch the box's committed offset).
  async function withRetry<T>(fn: () => Promise<T>, resync?: () => Promise<void>): Promise<T> {
    for (;;) {
      try {
        const result = await fn();
        failures = 0;
        if (retrying) {
          retrying = false;
          onRetry?.(null);
        }
        return result;
      } catch (err) {
        if (signal?.aborted || !isTransient(err)) throw err;
        failures++;
        if (failures >= MAX_ATTEMPTS) throw finalError(err);
        retrying = true;
        onRetry?.({ attempt: failures, max: MAX_ATTEMPTS - 1, reason: describe(err) });
        await sleep(BACKOFF_MS[Math.min(failures - 1, BACKOFF_MS.length - 1)], signal);
        if (resync) {
          try {
            await resync();
          } catch (e) {
            if (signal?.aborted || !isTransient(e)) throw e;
            // Box still unreachable — the next attempt counts as another failure.
          }
        }
      }
    }
  }

  // 1. Open the session.
  const session = await withRetry(async () => {
    const res = await send("POST", `/api/folders/${folderId}/uploads`, {
      headers: { ...authHeaders, "Content-Type": "application/json" },
      body: JSON.stringify({ filename: file.name, size: file.size }),
      signal,
    });
    return res.json<Session>();
  });

  // 2. Send chunks from the server's current offset (resume-friendly).
  let offset = session.received;
  const resyncOffset = async () => {
    const res = await send("GET", `/api/uploads/${session.id}`, { headers: authHeaders, signal });
    offset = res.json<Session>().received;
    onProgress(file.size === 0 ? 1 : offset / file.size);
  };
  while (offset < file.size) {
    await withRetry(async () => {
      const start = offset;
      const slice = file.slice(start, start + CHUNK_SIZE);
      const res = await send("PUT", `/api/uploads/${session.id}`, {
        headers: {
          ...authHeaders,
          "Content-Type": "application/octet-stream",
          "X-Offset": String(start),
        },
        body: slice,
        signal,
        onUploadProgress: (loaded) => onProgress(Math.min(start + loaded, file.size) / file.size),
      });
      offset = res.json<Session>().received;
      onProgress(file.size === 0 ? 1 : offset / file.size);
    }, resyncOffset);
  }

  // 3. Finalize. Not idempotent: if an attempt reached the box but its answer
  // got lost, the session is gone (404) — then look the new item up instead.
  let attempted = false;
  const media = await withRetry(async () => {
    try {
      const res = await send("POST", `/api/uploads/${session.id}/complete`, {
        headers: authHeaders,
        signal,
        timeoutMs: COMPLETE_TIMEOUT_MS,
      });
      return res.json<MediaItem>();
    } catch (err) {
      if (attempted && err instanceof ApiError && err.status === 404) {
        const found = await findCompleted(folderId, file.size, authHeaders, signal);
        if (found) return found;
      }
      attempted = true;
      throw err;
    }
  });
  onProgress(1);
  return media;
}

/** The newest item in the folder with this size (a finalize whose answer was lost). */
async function findCompleted(
  folderId: number,
  size: number,
  headers: Record<string, string>,
  signal?: AbortSignal,
): Promise<MediaItem | null> {
  const res = await send("GET", `/api/folders/${folderId}/media`, { headers, signal });
  const items = res.json<MediaItem[]>().filter((m) => m.size === size);
  return items.reduce<MediaItem | null>((best, m) => (best && best.id > m.id ? best : m), null);
}

// --- transport ---------------------------------------------------------------

/** Network error, stall or no answer — the request may or may not have arrived. */
class NetworkError extends Error {}

interface SendOptions {
  headers?: Record<string, string>;
  body?: XMLHttpRequestBodyInit | null;
  signal?: AbortSignal;
  /** Abort after this long without upload progress or a response. */
  timeoutMs?: number;
  onUploadProgress?: (loadedBytes: number) => void;
}

interface SendResult {
  json<T>(): T;
}

// XMLHttpRequest instead of fetch: it reports upload progress, which lets the
// idle timeout tell a stalled link from a slow one (and gives a smooth bar).
function send(method: string, url: string, opts: SendOptions = {}): Promise<SendResult> {
  const timeoutMs = opts.timeoutMs ?? IDLE_TIMEOUT_MS;
  return new Promise((resolve, reject) => {
    if (opts.signal?.aborted) {
      reject(abortError());
      return;
    }
    const xhr = new XMLHttpRequest();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let timedOut = false;
    const arm = () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        timedOut = true;
        xhr.abort();
      }, timeoutMs);
    };
    const onAbort = () => xhr.abort();
    const cleanup = () => {
      clearTimeout(timer);
      opts.signal?.removeEventListener("abort", onAbort);
    };

    xhr.open(method, url);
    for (const [k, v] of Object.entries(opts.headers ?? {})) xhr.setRequestHeader(k, v);
    xhr.upload.onprogress = (e) => {
      arm();
      opts.onUploadProgress?.(e.loaded);
    };
    xhr.onprogress = arm;
    xhr.onload = () => {
      cleanup();
      if (xhr.status >= 200 && xhr.status < 300) {
        const text = xhr.responseText;
        resolve({ json: <T>() => JSON.parse(text) as T });
      } else {
        reject(toError(xhr));
      }
    };
    xhr.onerror = () => {
      cleanup();
      reject(new NetworkError("Keine Verbindung zur Box"));
    };
    xhr.onabort = () => {
      cleanup();
      if (timedOut) reject(new NetworkError("Zeitüberschreitung – keine Antwort von der Box"));
      else reject(abortError());
    };
    opts.signal?.addEventListener("abort", onAbort);
    arm();
    xhr.send(opts.body ?? null);
  });
}

function toError(xhr: XMLHttpRequest): ApiError {
  let detail = xhr.statusText;
  try {
    const body = JSON.parse(xhr.responseText);
    if (typeof body?.detail === "string") detail = body.detail;
  } catch {
    /* ignore */
  }
  return new ApiError(xhr.status, detail);
}

function isTransient(err: unknown): boolean {
  if (err instanceof NetworkError) return true;
  if (err instanceof ApiError) {
    // 409: offset mismatch or buffer drive briefly gone — resync and retry.
    return err.status === 408 || err.status === 409 || err.status === 429 || err.status >= 500;
  }
  return false;
}

function describe(err: unknown): string {
  return err instanceof Error ? err.message : "Unbekannter Fehler";
}

function finalError(err: unknown): Error {
  if (err instanceof ApiError) return err;
  return new ApiError(0, `${describe(err)} – Upload nach mehreren Versuchen abgebrochen`);
}

function abortError(): Error {
  return new DOMException("Upload abgebrochen", "AbortError");
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(abortError());
      return;
    }
    const onAbort = () => {
      clearTimeout(t);
      reject(abortError());
    };
    const t = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i++;
  }
  return `${value.toFixed(1)} ${units[i]}`;
}
