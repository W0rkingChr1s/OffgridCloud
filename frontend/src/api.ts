const TOKEN_KEY = "ogc_token";

export type Role = "admin" | "user";

export interface User {
  id: number;
  email: string;
  name: string;
  role: Role;
  active: boolean;
  created_at: string;
}

export interface Health {
  status: string;
  app: string;
  version: string;
  environment: string;
  rclone: { available: boolean; version: string | null; error: string | null };
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null): void {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

export async function api<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const headers = new Headers(opts.headers);
  if (opts.body) headers.set("Content-Type", "application/json");
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const res = await fetch(path, { ...opts, headers });

  if (!res.ok) {
    if (res.status === 401) setToken(null);
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (body?.detail) detail = typeof body.detail === "string" ? body.detail : detail;
    } catch {
      /* no JSON body */
    }
    throw new ApiError(res.status, detail);
  }

  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}
