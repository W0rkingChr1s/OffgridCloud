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

export interface Folder {
  id: number;
  name: string;
  description: string;
  created_at: string;
  user_ids: number[];
  group_ids: number[];
  media_count: number;
}

export interface Group {
  id: number;
  name: string;
  description: string;
  created_at: string;
  member_ids: number[];
}

export type MediaStatus =
  | "received"
  | "queued"
  | "uploading"
  | "verified"
  | "done"
  | "failed";

export interface MediaItem {
  id: number;
  folder_id: number;
  filename: string;
  size: number;
  sha256: string;
  status: MediaStatus;
  local_deleted: boolean;
  uploaded_by: number | null;
  created_at: string;
}

export interface FolderProviderLink {
  id: number;
  folder_id: number;
  provider_id: number;
  provider_name: string;
  dest_path: string;
  priority: number;
  enabled: boolean;
}

export interface BandwidthWindow {
  start: string;
  end: string;
  kbps: number;
}

export interface BandwidthStatus {
  enabled: boolean;
  min_bandwidth_kbps: number;
  bwlimit_kbps: number;
  schedule: BandwidthWindow[];
  last_kbps: number;
  last_measured_at: string | null;
  effective_bwlimit_kbps: number;
  gated: boolean;
  gate_reason: string;
}

export type TransferStatus = "queued" | "running" | "done" | "failed";

export interface TransferJob {
  id: number;
  media_id: number;
  provider_id: number;
  status: TransferStatus;
  progress: number;
  bytes_transferred: number;
  attempts: number;
  last_error: string;
  created_at: string;
  updated_at: string;
  media_filename: string;
  provider_name: string;
  folder_id: number | null;
}

export type ProviderStatus = "unknown" | "ok" | "error";

export interface Provider {
  id: number;
  name: string;
  type: string;
  status: ProviderStatus;
  last_error: string;
  last_tested_at: string | null;
  created_at: string;
  config: Record<string, string>;
}

export interface ProviderField {
  key: string;
  label: string;
  type: string;
  required: boolean;
  secret: boolean;
  help: string;
  default: string;
  options: string[];
}

export interface ProviderTypeDef {
  key: string;
  label: string;
  help: string;
  category: string;
  description: string;
  popular: boolean;
  fields: ProviderField[];
}

export interface ProviderCategory {
  key: string;
  label: string;
  description: string;
  icon: string;
}

export type VpnType = "wireguard" | "openvpn";

export interface VpnTunnel {
  id: number;
  name: string;
  type: VpnType;
  autostart: boolean;
  last_error: string;
  created_at: string;
  has_username: boolean;
  active: boolean;
}

export interface VpnStatus {
  active_id: number | null;
  state: "down" | "up" | "error";
  detail: string;
  endpoint: string;
  last_handshake: string;
}

export interface VpnCapabilities {
  net_admin: boolean;
  tun_device: boolean;
  wireguard: boolean;
  openvpn: boolean;
  ready: boolean;
  message: string;
}

export interface DiskUsage {
  total: number;
  used: number;
  free: number;
  percent_used: number;
  low_space: boolean;
}

export interface SystemStatus {
  delete_local_after_upload: boolean;
  delete_remote_on_local_delete: boolean;
  auto_resync: boolean;
  reconcile_interval: number;
  probe_url: string;
  webhook_url: string;
  disk: DiskUsage;
  rclone_available: boolean;
}

export interface MediaDeleteResult {
  deleted: boolean;
  remote_attempted: number;
  remote_deleted: number;
  remote_errors: string[];
}

export interface UpdateInfo {
  current: string;
  latest: string | null;
  update_available: boolean;
  release_url: string;
  release_name: string;
  published_at: string;
  notes: string;
  error: string;
  self_update_enabled: boolean;
}

export interface AuditEvent {
  id: number;
  created_at: string;
  user_email: string;
  action: string;
  detail: string;
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
