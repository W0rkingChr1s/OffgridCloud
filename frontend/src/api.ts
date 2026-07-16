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
  tags: string[];
}

export interface MediaSearchResult extends MediaItem {
  folder_name: string;
}

export interface MediaDescription {
  id: number;
  folder_id: number;
  title: string;
  body: string;
  created_by: number | null;
  created_at: string;
  updated_at: string;
  media_ids: number[];
  txt_media_id: number | null;
  txt_filename: string;
  txt_status: MediaStatus | null;
}

export interface DescriptionDeleteResult {
  deleted: boolean;
  remote_attempted: number;
  remote_deleted: number;
  remote_errors: string[];
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
  docker: boolean;
  enable_command: string;
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
  // Notifications ("Info-Service"). Secrets are never returned — only whether
  // each channel is configured.
  notify_on_received: boolean;
  notify_on_done: boolean;
  notify_on_failed: boolean;
  notify_on_low_space: boolean;
  notify_on_startup: boolean;
  notify_on_reconnect: boolean;
  notify_on_bandwidth: boolean;
  telegram_chat_id: string;
  telegram_configured: boolean;
  smtp_host: string;
  smtp_port: number;
  smtp_username: string;
  smtp_from: string;
  smtp_to: string;
  smtp_tls: boolean;
  smtp_configured: boolean;
  // System power control ("System steuern"). Each flag says whether the
  // corresponding privileged command is wired up on this instance.
  power_restart_service_enabled: boolean;
  power_reboot_enabled: boolean;
  power_shutdown_enabled: boolean;
}

export interface HttpsStatus {
  enabled: boolean; // HTTPS is actually serving (Caddy up / config applied)
  manageable: boolean; // the UI can change the domain (apply command wired up)
  hostname: string;
  domain: string;
  lan_url: string;
  public_url: string;
}

export async function getHttpsStatus(): Promise<HttpsStatus> {
  return api<HttpsStatus>("/api/system/https");
}

export async function updateHttps(patch: {
  hostname?: string;
  domain?: string;
}): Promise<HttpsStatus> {
  return api<HttpsStatus>("/api/system/https", {
    method: "PUT",
    body: JSON.stringify(patch),
  });
}

export interface Passkey {
  id: number;
  name: string;
  rp_id: string;
  created_at: string;
  last_used_at: string | null;
}

export async function listPasskeys(): Promise<Passkey[]> {
  return api<Passkey[]>("/api/auth/webauthn/credentials");
}

export async function renamePasskey(id: number, name: string): Promise<Passkey> {
  return api<Passkey>(`/api/auth/webauthn/credentials/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ name }),
  });
}

export async function deletePasskey(id: number): Promise<void> {
  await api<void>(`/api/auth/webauthn/credentials/${id}`, { method: "DELETE" });
}

/** True when this browser + origin can use passkeys (WebAuthn needs a secure
 * context: https, or http://localhost for dev). */
export function passkeysSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    "PublicKeyCredential" in window &&
    (window.isSecureContext || window.location.hostname === "localhost")
  );
}

/** A server-side status blip carried in the live SSE snapshot (see
 * app/notices.py): startup summary, reconnect ping, bandwidth pause/resume. */
export interface ServerNotice {
  id: number;
  level: "success" | "error" | "info" | "warning";
  title: string;
  message: string;
}

export interface MediaDeleteResult {
  deleted: boolean;
  remote_attempted: number;
  remote_deleted: number;
  remote_errors: string[];
}

export interface MediaBulkDeleteResult {
  requested: number;
  deleted: number;
  not_found: number[];
  remote_attempted: number;
  remote_deleted: number;
  remote_errors: string[];
}

export interface NetworkStatus {
  supported: boolean;
  apply_wired: boolean;
  mode: "ethernet" | "client" | "ap" | "offline" | "unknown";
  online: boolean;
  connectivity: string;
  ethernet: boolean;
  wifi_ssid: string | null;
  wifi_ip: string | null;
  ap_active: boolean;
  ap_ssid: string | null;
  detail: string;
}

export interface KnownNetwork {
  id: number;
  ssid: string;
  priority: number;
  autoconnect: boolean;
  has_password: boolean;
  created_at: string;
}

export interface NetworkSettings {
  fallback_enabled: boolean;
  ap_ssid: string;
  ap_hidden: boolean;
  ap_address: string;
  country_code: string;
  check_interval: number;
  fail_threshold: number;
  ap_has_password: boolean;
}

export interface NetworkOverview {
  status: NetworkStatus;
  settings: NetworkSettings;
  known_networks: KnownNetwork[];
}

export interface NetworkApplyResult {
  ok: boolean;
  message: string;
  output: string;
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

export interface UpdateProgress {
  phase: "idle" | "running" | "success" | "failed" | "unknown";
  running: boolean;
  from_version: string;
  to_version: string;
  message: string;
  returncode: number | null;
  started_at: number;
  finished_at: number;
  log: string;
}

export interface AuditEvent {
  id: number;
  created_at: string;
  user_email: string;
  action: string;
  detail: string;
}

// --- Multi-server pooling ---------------------------------------------------

export interface PoolNodeStatus {
  name: string;
  version: string;
  reachable: boolean;
  error: string;
  base_url: string;
  peer_id: number | null;
  media: Record<string, number>;
  media_total: number;
  active_transfers: number;
  throughput_kbps: number;
  disk_free: number;
  disk_total: number;
}

export interface PoolTotals {
  nodes: number;
  nodes_online: number;
  media_total: number;
  active_transfers: number;
  throughput_kbps: number;
  disk_free: number;
  disk_total: number;
}

export interface PoolOverview {
  self: PoolNodeStatus;
  peers: PoolNodeStatus[];
  totals: PoolTotals;
}

export interface PoolPeer {
  id: number;
  name: string;
  base_url: string;
  enabled: boolean;
  has_token: boolean;
  created_at: string;
}

export interface PoolSelf {
  pool_token: string;
  token_set: boolean;
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
