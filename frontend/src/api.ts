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
  metered_mode: MeteredMode;
  metered_budget_mb: number;
  metered_used_bytes: number;
  metered: boolean;
  uplink: string;
  bonding_enabled: boolean;
  bonding_available: boolean;
  bonding_running: boolean;
  bonding_links: BondingLink[];
  /** Why uploads skip net-merge right now although bundling is on ("" = they don't). */
  bonding_bypass: string;
  parallel_uploads: number;
  running_uploads: number;
  uploads_paused: boolean;
  pool_limit_kbps: number; // share assigned by a pool hub (0 = none)
  pool_limit_by: string;
}

/** A network link as net-merge sees it (link bundling). */
export interface BondingLink {
  name: string;
  used: boolean;
  detail: string;
}

/** What uploads do while the uplink is metered (USB-tethered phone). */
export type MeteredMode = "allow" | "pause" | "limit";

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
  notify_queued: Record<string, number>;
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
  notify_on_pool: boolean;
  notify_on_auto_import: boolean;
  telegram_chat_id: string;
  telegram_configured: boolean;
  telegram_conflict: boolean;
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

export interface StoragePartition {
  name: string;
  path: string;
  size: number;
  fstype: string;
  label: string;
  uuid: string;
  mountpoint: string;
  read_only: boolean;
  total: number;
  used: number;
  free: number;
  system: boolean; // holds the OS (or is mounted outside /media, /mnt)
  buffer: boolean; // holds the OffgridCloud media buffer / data dir
  protected: boolean;
  browsable: boolean;
  busy: boolean; // an import from it is running
  cache: boolean; // marked as cache for offloading cards
  buffer_drive: boolean; // marked as additional media buffer
}

export interface StorageDisk {
  name: string;
  path: string;
  size: number;
  model: string;
  vendor: string;
  transport: string;
  removable: boolean;
  read_only: boolean;
  system: boolean;
  buffer: boolean;
  protected: boolean;
  partitions: StoragePartition[];
}

export interface StorageOverview {
  manage_enabled: boolean;
  filesystems: string[];
  label_max: Record<string, number>;
  disks: StorageDisk[];
  buffer: DiskUsage; // combined: the box's own buffer + attached buffer drives
  buffers: StorageBuffer[];
  error: string;
}

/** A removable drive marked as additional media buffer. */
export interface StorageBuffer {
  uuid: string;
  label: string;
  device: string | null; // null while unplugged
  mountpoint: string;
  online: boolean; // new files may land on it
  total: number;
  free: number;
  used: number;
  pending: number; // files on it not yet uploaded everywhere
  error: string;
}

/** Where an identical file already lives on the box (duplicate check). */
export interface StorageKnown {
  match: "exact" | "likely";
  media_id: number;
  status: MediaStatus;
  folder_id: number;
  folder_name: string;
}

export interface StorageEntry {
  name: string;
  path: string;
  is_dir: boolean;
  size: number;
  modified: string | null;
  is_media: boolean;
  known: StorageKnown | null;
}

export type StorageMediaKind = "photo" | "raw" | "video" | "audio";

export interface StorageGalleryItem {
  path: string;
  name: string;
  dir: string;
  size: number;
  mtime: number; // epoch seconds
  kind: StorageMediaKind;
  known: StorageKnown | null;
}

export interface StorageGallery {
  device: string;
  mountpoint: string;
  path: string;
  items: StorageGalleryItem[];
}

export interface StorageCacheSnapshot {
  name: string;
  path: string;
  files: number;
  size: number;
  modified: string | null;
}

export interface StorageCache {
  uuid: string;
  label: string;
  device: string | null;
  mountpoint: string;
  total: number;
  free: number;
  snapshots: StorageCacheSnapshot[];
  error: string;
}

export interface AutoImportMedium {
  device: string;
  label: string;
  state: "busy" | "done" | "error";
  message: string;
  job_id: string;
  ejected: boolean;
}

export interface AutoImport {
  enabled: boolean;
  mode: "import" | "cache";
  folder_id: number | null;
  cache_uuid: string;
  media_only: boolean;
  skip_duplicates: boolean;
  eject_after: boolean;
  led_enabled: boolean;
  led_name: string;
  manage_enabled: boolean;
  leds: string[];
  led_active: string;
  led_state: "idle" | "busy" | "done" | "error";
  media: AutoImportMedium[];
}

export interface StorageBrowse {
  device: string;
  mountpoint: string;
  path: string;
  parent: string | null;
  entries: StorageEntry[];
  truncated: boolean;
}

export type StorageImportStatus = "pending" | "running" | "done" | "failed" | "cancelled";

export interface StorageImportJob {
  id: string;
  kind: "import" | "cache";
  device: string;
  folder_id: number | null;
  folder_name: string;
  target: string; // cache: "<device>::<snapshot>"
  eject_after: boolean;
  user_id: number;
  status: StorageImportStatus;
  total_files: number;
  total_bytes: number;
  done_files: number;
  done_bytes: number;
  imported: number;
  skipped: number;
  failed: number;
  current: string;
  error: string;
  errors: string[];
  created_at: string;
  finished_at: string | null;
}

export interface HttpsStatus {
  enabled: boolean; // HTTPS is actually serving (Caddy up / config applied)
  manageable: boolean; // the UI can change the domain (apply command wired up)
  hostname: string;
  domain: string;
  lan_url: string;
  public_url: string;
  ca_available: boolean; // root CA exported → downloadable for devices
  ca_fingerprint: string; // SHA-256, to compare in the device's profile view
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

/** Bluetooth for the companion app (docs/BLE-PROTOKOLL.md §6). */
export interface BleStatus {
  enabled: boolean;
  service_running: boolean;
  box_name: string;
  box_public_key: string;
}

export interface BlePairing {
  code: string;
  link: string;
  expires_at: string;
  expires_in: number;
}

export interface BleDevice {
  id: number;
  name: string;
  user_id: number;
  user_email: string;
  created_at: string;
  last_seen_at: string | null;
}

export async function getBleStatus(): Promise<BleStatus> {
  return api<BleStatus>("/api/ble/status");
}

export async function setBleEnabled(enabled: boolean): Promise<BleStatus> {
  return api<BleStatus>("/api/ble/settings", {
    method: "PUT",
    body: JSON.stringify({ enabled }),
  });
}

export async function createBlePairing(): Promise<BlePairing> {
  return api<BlePairing>("/api/ble/pairings", { method: "POST" });
}

export async function listBleDevices(): Promise<BleDevice[]> {
  return api<BleDevice[]>("/api/ble/devices");
}

export async function renameBleDevice(id: number, name: string): Promise<BleDevice> {
  return api<BleDevice>(`/api/ble/devices/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ name }),
  });
}

export async function revokeBleDevice(id: number): Promise<void> {
  await api<void>(`/api/ble/devices/${id}`, { method: "DELETE" });
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
  mode: "ethernet" | "client" | "tether" | "ap" | "offline" | "unknown";
  online: boolean;
  connectivity: string;
  ethernet: boolean;
  wifi_ssid: string | null;
  wifi_ip: string | null;
  ap_active: boolean;
  ap_ssid: string | null;
  tether: "iphone" | "android" | null;
  tether_ip: string | null;
  metered: boolean;
  metered_present: boolean;
  uplink_ip: string | null;
  links: Uplink[];
  detail: string;
}

/** One connected link to the internet (cable, Wi-Fi, USB-tethered phone). */
export interface Uplink {
  /** Kernel name, e.g. "wlan0" — matches BondingLink.name. */
  device: string;
  kind: "ethernet" | "client" | "tether";
  label: string;
  ip: string | null;
  metered: boolean;
  /** Carries the default route. */
  primary: boolean;
}

export interface KnownNetwork {
  id: number;
  ssid: string;
  priority: number;
  autoconnect: boolean;
  has_password: boolean;
  created_at: string;
}

export interface SystemNetwork {
  name: string;
  ssid: string;
  priority: number;
  autoconnect: boolean;
  has_password: boolean;
  active: boolean;
}

export interface NetworkSettings {
  fallback_enabled: boolean;
  ap_ssid: string;
  ap_hidden: boolean;
  ap_address: string;
  country_code: string;
  check_interval: number;
  fail_threshold: number;
  ap_probe_interval: number;
  ap_blind_probe_interval: number;
  ap_has_password: boolean;
}

export interface NetworkOverview {
  status: NetworkStatus;
  settings: NetworkSettings;
  known_networks: KnownNetwork[];
  system_networks: SystemNetwork[];
}

export interface NetworkApplyResult {
  ok: boolean;
  message: string;
  output: string;
}

export interface UpdateInfo {
  current: string;
  channel: "release" | "main";
  commit: string;
  latest: string | null;
  behind_by: number;
  update_available: boolean;
  release_url: string;
  release_name: string;
  published_at: string;
  notes: string;
  error: string;
  self_update_enabled: boolean;
  in_docker: boolean;
}

export interface AutoUpdate {
  enabled: boolean;
  start_hour: number;
  end_hour: number;
  allow_metered: boolean;
  available: boolean;
  status: string;
  last_day: string;
  failed_target: string;
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
  uploads_paused: boolean;
  failed_transfers: number;
  control_enabled: boolean;
  pending_transfers: number | null;
  priority: number; // 0 = not managed, 1 = high … 3 = low (hub view)
  priority_paused: boolean;
  pool_limit_kbps: number; // share of a shared line assigned by a hub (0 = none)
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

export type PoolTokenScope = "read" | "control";

/** A token this box issued to one hub (plaintext only in PoolAccessTokenCreated). */
export interface PoolAccessToken {
  id: number;
  name: string;
  hint: string;
  scope: PoolTokenScope;
  created_at: string;
  last_used_at: string | null;
  last_used_hub: string;
}

export interface PoolAccessTokenCreated extends PoolAccessToken {
  token: string;
}

/** Another box announcing itself via mDNS — a suggestion, not a trusted peer. */
export interface DiscoveredBox {
  node_id: string;
  name: string;
  version: string;
  url: string;
  addresses: string[];
  peer_id: number | null;
}

export interface PoolDiscovery {
  running: boolean;
  error: string;
  announce: boolean;
  boxes: DiscoveredBox[];
}

export interface PoolPriorityNode {
  peer_id: number | null; // null = this box
  name: string;
  level: number;
}

export interface PoolPriority {
  enabled: boolean;
  nodes: PoolPriorityNode[];
  share_enabled: boolean;
  share_kbps: number; // capacity of the shared line
}

export interface PairingCode {
  code: string;
  expires_in: number;
}

export type TransferAction = "pause" | "resume" | "retry_failed";

export interface TransferControl {
  uploads_paused: boolean;
  failed_transfers: number;
  message: string;
}

export interface Health {
  status: string;
  app: string;
  version: string;
  environment: string;
  rclone: { available: boolean; version: string | null; error: string | null };
}

export interface SetupStatus {
  required: boolean;
  code_required: boolean;
  suggested_hostname: string;
  country: string;
}

export interface SetupResult {
  hostname: string;
  url: string;
  wifi_ssid: string;
  finish_started: boolean;
}

export interface TelegramNames {
  box_name: string;
  label: string;
  display_names: string[];
  usernames: string[];
}

export interface TelegramTokenCheck {
  ok: boolean;
  message: string;
  username: string;
  name: string;
}

export interface TelegramChat {
  id: string;
  type: "private" | "group" | "supergroup" | "channel" | string;
  title: string;
  seen_at: number;
}

export interface TelegramChats {
  configured: boolean;
  conflict: boolean;
  chats: TelegramChat[];
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

/** fetch() itself failed: the box never answered (offline, wrong network, or —
 *  over https://<name>.local — a certificate the device no longer trusts). */
export class NetworkError extends Error {
  constructor() {
    super("Box nicht erreichbar");
  }
}

/** Fired on window whenever an API call fails to reach the box at all. */
export const UNREACHABLE_EVENT = "ogc:unreachable";

/** Root-CA downloads. From https:// they point at the plain-HTTP twin of the
 *  same name — the one way in while the device rejects the certificate. */
export function rootCaUrls(): { profile: string; crt: string } {
  const base =
    window.location.protocol === "https:" ? `http://${window.location.hostname}` : "";
  return {
    profile: `${base}/api/system/https/ca.mobileconfig`,
    crt: `${base}/api/system/https/ca.crt`,
  };
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

  let res: Response;
  try {
    res = await fetch(path, { ...opts, headers });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    window.dispatchEvent(new Event(UNREACHABLE_EVENT));
    throw new NetworkError();
  }

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
  // Some endpoints answer 201 without a body — res.json() would throw on that.
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}
