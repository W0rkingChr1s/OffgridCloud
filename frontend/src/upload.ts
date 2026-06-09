import { ApiError, getToken, type MediaItem } from "./api";

const CHUNK_SIZE = 4 * 1024 * 1024; // 4 MiB

interface Session {
  id: string;
  received: number;
}

/**
 * Upload a single file to a folder using the resumable chunk protocol.
 * `onProgress` is called with a fraction in [0, 1].
 */
export async function uploadFile(
  folderId: number,
  file: File,
  onProgress: (fraction: number) => void,
  signal?: AbortSignal,
): Promise<MediaItem> {
  const token = getToken();
  const authHeaders: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {};

  // 1. Open the session.
  const createRes = await fetch(`/api/folders/${folderId}/uploads`, {
    method: "POST",
    headers: { ...authHeaders, "Content-Type": "application/json" },
    body: JSON.stringify({ filename: file.name, size: file.size }),
    signal,
  });
  if (!createRes.ok) throw await toError(createRes);
  const session: Session = await createRes.json();

  // 2. Send chunks from the server's current offset (resume-friendly).
  let offset = session.received;
  while (offset < file.size) {
    const slice = file.slice(offset, offset + CHUNK_SIZE);
    const res = await fetch(`/api/uploads/${session.id}`, {
      method: "PUT",
      headers: { ...authHeaders, "Content-Type": "application/octet-stream", "X-Offset": String(offset) },
      body: slice,
      signal,
    });
    if (!res.ok) throw await toError(res);
    offset = (await res.json()).received;
    onProgress(file.size === 0 ? 1 : offset / file.size);
  }

  // 3. Finalize.
  const done = await fetch(`/api/uploads/${session.id}/complete`, {
    method: "POST",
    headers: authHeaders,
    signal,
  });
  if (!done.ok) throw await toError(done);
  onProgress(1);
  return done.json();
}

async function toError(res: Response): Promise<ApiError> {
  let detail = res.statusText;
  try {
    const body = await res.json();
    if (typeof body?.detail === "string") detail = body.detail;
  } catch {
    /* ignore */
  }
  return new ApiError(res.status, detail);
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
