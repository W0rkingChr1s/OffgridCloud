"""Pydantic request/response schemas.

Note: ``email`` is the login identifier. For a self-hosted, often-offline
appliance it must accept addresses like ``admin@offgrid.local`` — so we use a
light format check, not strict deliverability validation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import (
    MediaStatus,
    PoolTokenScope,
    ProviderStatus,
    Role,
    TransferStatus,
    VpnType,
)


def _normalise_email(value: str) -> str:
    value = value.strip().lower()
    if "@" not in value or value.startswith("@") or value.endswith("@"):
        raise ValueError("must be a valid login email, e.g. user@offgrid.local")
    return value


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    name: str
    role: Role
    active: bool
    created_at: datetime


class UserCreate(BaseModel):
    email: str
    password: str = Field(min_length=8)
    name: str = ""
    role: Role = Role.USER

    _norm_email = field_validator("email")(_normalise_email)


class UserUpdate(BaseModel):
    """All fields optional — patch semantics."""

    name: str | None = None
    role: Role | None = None
    active: bool | None = None
    password: str | None = Field(default=None, min_length=8)


# --- Folders --------------------------------------------------------------


class FolderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""


class FolderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None


class FolderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str
    created_at: datetime
    user_ids: list[int] = []
    group_ids: list[int] = []
    media_count: int = 0


class FolderAccessUpdate(BaseModel):
    """Replace the full set of users with upload access to a folder."""

    user_ids: list[int]


class FolderGroupsUpdate(BaseModel):
    """Replace the full set of groups with upload access to a folder."""

    group_ids: list[int]


class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""


class GroupUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None


class GroupMembersUpdate(BaseModel):
    user_ids: list[int]


class GroupOut(BaseModel):
    id: int
    name: str
    description: str
    created_at: datetime
    member_ids: list[int] = []


# --- Media & uploads ------------------------------------------------------


class MediaItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    folder_id: int
    filename: str
    size: int
    sha256: str
    status: MediaStatus
    local_deleted: bool = False
    uploaded_by: int | None
    created_at: datetime
    tags: list[str] = []


class MediaSearchOut(MediaItemOut):
    """A search hit, enriched with the folder name for the global search view."""

    folder_name: str = ""


class TagsUpdate(BaseModel):
    """Replace the full tag set of a media item."""

    tags: list[str] = []


# --- Thematic descriptions ------------------------------------------------


def _require_nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    return value


class DescriptionCreate(BaseModel):
    """Describe a group of media items; generates a ``.txt`` cloud sidecar."""

    title: str = Field(default="", max_length=200)
    body: str = Field(min_length=1, max_length=20_000)
    media_ids: list[int] = []

    _body_nonblank = field_validator("body")(_require_nonblank)


class DescriptionUpdate(BaseModel):
    """Patch a description — all fields optional."""

    title: str | None = Field(default=None, max_length=200)
    body: str | None = Field(default=None, min_length=1, max_length=20_000)
    media_ids: list[int] | None = None

    @field_validator("body")
    @classmethod
    def _body_nonblank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("must not be blank")
        return value


class DescriptionOut(BaseModel):
    id: int
    folder_id: int
    title: str
    body: str
    created_by: int | None = None
    created_at: datetime
    updated_at: datetime
    media_ids: list[int] = []
    # The generated .txt sidecar (a MediaItem) uploaded alongside the media.
    txt_media_id: int | None = None
    txt_filename: str = ""
    txt_status: MediaStatus | None = None


class DescriptionDeleteResult(BaseModel):
    deleted: bool
    remote_attempted: int = 0
    remote_deleted: int = 0
    remote_errors: list[str] = []


class UploadCreate(BaseModel):
    filename: str = Field(min_length=1, max_length=500)
    size: int = Field(default=0, ge=0)


class UploadSessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    folder_id: int
    filename: str
    size: int
    received: int


# --- Cloud providers ------------------------------------------------------


class ProviderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    type: str
    config: dict[str, str] = {}


class ProviderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    config: dict[str, str] | None = None


class ProviderTestRequest(BaseModel):
    """Test an unsaved draft, or a saved provider with config overrides."""

    type: str
    config: dict[str, str] = {}
    subpath: str = ""


class ProviderTestResult(BaseModel):
    ok: bool
    message: str


class ProviderOut(BaseModel):
    id: int
    name: str
    type: str
    status: ProviderStatus
    last_error: str
    last_tested_at: datetime | None
    created_at: datetime
    config: dict[str, str]  # secrets masked


# --- VPN client -----------------------------------------------------------


class VpnCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    type: VpnType = VpnType.WIREGUARD
    config: str = Field(min_length=1)  # raw .conf / .ovpn text
    username: str = ""  # OpenVPN auth-user-pass (optional)
    password: str = ""
    autostart: bool = False


class VpnUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    config: str | None = None
    username: str | None = None
    password: str | None = None
    autostart: bool | None = None


class VpnStatusOut(BaseModel):
    active_id: int | None = None
    state: str = "down"  # down | up | error
    detail: str = ""
    endpoint: str = ""
    last_handshake: str = ""


class VpnTunnelOut(BaseModel):
    id: int
    name: str
    type: VpnType
    autostart: bool
    last_error: str
    created_at: datetime
    has_username: bool
    active: bool


class VpnCapabilitiesOut(BaseModel):
    net_admin: bool
    tun_device: bool
    wireguard: bool
    openvpn: bool
    ready: bool  # net_admin and tun_device (base requirements met)
    message: str = ""  # explanation when not ready
    docker: bool = False  # running in a container → Docker-flag remediation
    enable_command: str = ""  # native one-liner to grant the capability (if not docker)


# --- Folder <-> Provider links & transfers --------------------------------


class FolderProviderLinkCreate(BaseModel):
    provider_id: int
    dest_path: str = ""
    priority: int = 0


class FolderProviderLinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    folder_id: int
    provider_id: int
    provider_name: str = ""
    dest_path: str
    priority: int = 0
    enabled: bool


class FolderProviderLinkUpdate(BaseModel):
    dest_path: str | None = None
    priority: int | None = None
    enabled: bool | None = None


class BandwidthWindow(BaseModel):
    start: str  # "HH:MM"
    end: str  # "HH:MM"
    kbps: int = 0  # KiB/s, 0 = unlimited in this window


class BandwidthPolicyUpdate(BaseModel):
    enabled: bool | None = None
    min_bandwidth_kbps: int | None = Field(default=None, ge=0)
    bwlimit_kbps: int | None = Field(default=None, ge=0)
    schedule: list[BandwidthWindow] | None = None
    metered_mode: Literal["allow", "pause", "limit"] | None = None
    metered_budget_mb: int | None = Field(default=None, ge=1, le=10_000_000)
    bonding_enabled: bool | None = None
    parallel_uploads: int | None = Field(default=None, ge=1, le=4)


class BondingLinkOut(BaseModel):
    name: str  # interface, e.g. "wlan0"
    used: bool  # net-merge spreads uploads over it
    detail: str = ""  # its address, or why it is skipped


class BandwidthStatusOut(BaseModel):
    enabled: bool
    min_bandwidth_kbps: int
    bwlimit_kbps: int
    schedule: list[BandwidthWindow]
    last_kbps: float
    last_measured_at: datetime | None
    effective_bwlimit_kbps: int  # what applies right now
    gated: bool  # uploads currently paused (min-bandwidth gate or metered rule)
    gate_reason: str
    metered_mode: str = "allow"  # allow | pause | limit
    metered_budget_mb: int = 500
    metered_used_bytes: int = 0  # sent over a metered link today
    metered: bool = False  # the uplink in use right now is metered
    uplink: str = ""  # label of the uplink in use, e.g. "USB-Tethering (iPhone)"
    bonding_enabled: bool = False  # admin switched link bundling on
    bonding_available: bool = False  # net-merge installed (deploy/netmerge/install.sh)
    bonding_running: bool = False  # its SOCKS5 proxy answers
    bonding_links: list[BondingLinkOut] = []  # what net-merge sees
    # Why uploads go direct right now although bundling is on ("" = they don't),
    # e.g. a phone is plugged in while metered links are paused.
    bonding_bypass: str = ""
    parallel_uploads: int = 1  # uploads running at the same time (1-4)
    running_uploads: int = 0  # how many run right now
    uploads_paused: bool = False  # manually stopped (Transfers page or pool hub)
    pool_limit_kbps: int = 0  # uplink share assigned by a pool hub (KiB/s, 0 = none)
    pool_limit_by: str = ""  # which hub


class DiskUsageOut(BaseModel):
    total: int
    used: int
    free: int
    percent_used: float
    low_space: bool


class SystemStatusOut(BaseModel):
    # Notifications waiting for an unreachable channel, per channel.
    notify_queued: dict[str, int] = {}
    delete_local_after_upload: bool
    delete_remote_on_local_delete: bool
    auto_resync: bool
    reconcile_interval: float
    probe_url: str
    webhook_url: str
    disk: DiskUsageOut
    rclone_available: bool
    # Notifications ("Info-Service"). Secrets are never returned — only whether
    # each channel is configured.
    notify_on_received: bool
    notify_on_done: bool
    notify_on_failed: bool
    notify_on_low_space: bool
    notify_on_startup: bool
    notify_on_reconnect: bool
    notify_on_bandwidth: bool
    notify_on_pool: bool
    notify_on_auto_import: bool
    telegram_chat_id: str
    telegram_configured: bool
    # Another box polls with the same bot token (Telegram allows only one).
    telegram_conflict: bool = False
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_from: str
    smtp_to: str
    smtp_tls: bool
    smtp_configured: bool
    # System power control ("System steuern"). Each flag says whether the
    # corresponding privileged command is wired up on this instance.
    power_restart_service_enabled: bool = False
    power_reboot_enabled: bool = False
    power_shutdown_enabled: bool = False


class PowerActionResult(BaseModel):
    started: bool
    message: str


class TelegramTestResult(BaseModel):
    sent: bool
    message: str


class TelegramNameSuggestions(BaseModel):
    box_name: str
    label: str
    display_names: list[str]
    usernames: list[str]


class TelegramTokenCheckIn(BaseModel):
    # Omitted/empty: check the token already stored on the box.
    token: str = ""


class TelegramTokenCheck(BaseModel):
    ok: bool
    message: str
    username: str = ""
    name: str = ""


class TelegramChatOut(BaseModel):
    id: str
    type: str
    title: str
    seen_at: float


class TelegramChatsOut(BaseModel):
    configured: bool
    conflict: bool
    chats: list[TelegramChatOut]


class SystemSettingsUpdate(BaseModel):
    delete_local_after_upload: bool | None = None
    delete_remote_on_local_delete: bool | None = None
    auto_resync: bool | None = None
    probe_url: str | None = None
    webhook_url: str | None = None
    # Notification event toggles.
    notify_on_received: bool | None = None
    notify_on_done: bool | None = None
    notify_on_failed: bool | None = None
    notify_on_low_space: bool | None = None
    notify_on_startup: bool | None = None
    notify_on_reconnect: bool | None = None
    notify_on_bandwidth: bool | None = None
    notify_on_pool: bool | None = None
    notify_on_auto_import: bool | None = None
    # Telegram channel (token is write-only; "" clears it).
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    # SMTP channel (password is write-only; "" clears it).
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_to: str | None = None
    smtp_tls: bool | None = None


class MediaDeleteResult(BaseModel):
    deleted: bool
    remote_attempted: int = 0
    remote_deleted: int = 0
    remote_errors: list[str] = []


class MediaBulkDelete(BaseModel):
    """Delete several media items from a folder in one request."""

    media_ids: list[int] = Field(min_length=1)


class MediaBulkDeleteResult(BaseModel):
    requested: int
    deleted: int
    not_found: list[int] = []
    remote_attempted: int = 0
    remote_deleted: int = 0
    remote_errors: list[str] = []
# --- Network redundancy / AP fallback -------------------------------------


class UplinkOut(BaseModel):
    device: str  # e.g. "wlan0" — matches BondingLinkOut.name
    kind: str  # ethernet | client | tether
    label: str
    ip: str | None = None
    metered: bool = False
    primary: bool = False  # carries the default route


class NetworkStatusOut(BaseModel):
    supported: bool
    apply_wired: bool
    mode: str
    online: bool
    connectivity: str
    ethernet: bool
    wifi_ssid: str | None = None
    wifi_ip: str | None = None
    ap_active: bool
    ap_ssid: str | None = None
    tether: str | None = None  # "iphone" | "android" while a phone tethers via USB
    tether_ip: str | None = None
    metered: bool = False
    metered_present: bool = False
    uplink_ip: str | None = None
    links: list[UplinkOut] = []  # every uplink, primary first
    detail: str = ""


class KnownNetworkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ssid: str
    priority: int
    autoconnect: bool
    has_password: bool = False
    created_at: datetime


class KnownNetworkCreate(BaseModel):
    ssid: str = Field(min_length=1, max_length=32)
    password: str = ""
    priority: int = Field(default=0, ge=0)
    autoconnect: bool = True


class KnownNetworkUpdate(BaseModel):
    password: str | None = None
    priority: int | None = Field(default=None, ge=0)
    autoconnect: bool | None = None


class SystemNetworkOut(BaseModel):
    """Wi-Fi profile configured outside OffgridCloud (e.g. Raspberry Pi Imager)."""

    name: str
    ssid: str
    priority: int
    autoconnect: bool
    has_password: bool = False
    active: bool = False


class NetworkSettingsOut(BaseModel):
    fallback_enabled: bool
    ap_ssid: str
    ap_hidden: bool
    ap_address: str
    country_code: str
    check_interval: int
    fail_threshold: int
    ap_probe_interval: int = 120
    ap_blind_probe_interval: int = 600
    ap_has_password: bool = False


class NetworkSettingsUpdate(BaseModel):
    fallback_enabled: bool | None = None
    ap_ssid: str | None = Field(default=None, min_length=1, max_length=32)
    ap_password: str | None = None
    ap_hidden: bool | None = None
    ap_address: str | None = None
    country_code: str | None = None
    check_interval: int | None = Field(default=None, ge=5, le=3600)
    fail_threshold: int | None = Field(default=None, ge=1, le=20)
    ap_probe_interval: int | None = Field(default=None, ge=30, le=3600)
    ap_blind_probe_interval: int | None = Field(default=None, ge=60, le=86400)


class NetworkOverviewOut(BaseModel):
    """Everything the Network admin page needs in one call."""

    status: NetworkStatusOut
    settings: NetworkSettingsOut
    known_networks: list[KnownNetworkOut]
    system_networks: list[SystemNetworkOut] = []


class NetworkApplyResult(BaseModel):
    ok: bool
    message: str
    output: str = ""


class WifiScanOut(BaseModel):
    ssids: list[str]


class UpdateInfoOut(BaseModel):
    current: str
    channel: str = "release"  # release | main
    commit: str = ""
    latest: str | None = None
    behind_by: int = 0  # main channel: commits on main not yet installed
    update_available: bool = False
    release_url: str = ""
    release_name: str = ""
    published_at: str = ""
    notes: str = ""
    error: str = ""
    # Whether one-click apply is wired up on this instance.
    self_update_enabled: bool = False
    # Running in a container: updates mean pulling a new image, not update.sh.
    in_docker: bool = False


class AutoUpdateOut(BaseModel):
    """Unattended-update settings plus what the scheduler is waiting for."""

    enabled: bool
    start_hour: int
    end_hour: int
    allow_metered: bool
    # False on Docker or with one-click update switched off: nothing to run.
    available: bool
    status: str = ""
    last_day: str = ""
    failed_target: str = ""


class AutoUpdateIn(BaseModel):
    enabled: bool | None = None
    start_hour: int | None = Field(default=None, ge=0, le=23)
    end_hour: int | None = Field(default=None, ge=0, le=23)
    allow_metered: bool | None = None


class UpdateApplyResult(BaseModel):
    started: bool
    message: str


class UpdateProgressOut(BaseModel):
    """Live state of a one-click self-update, streamed to the portal."""

    phase: str  # idle | running | success | failed | unknown
    running: bool
    from_version: str = ""
    to_version: str = ""
    message: str = ""
    returncode: int | None = None
    started_at: float = 0.0
    finished_at: float = 0.0
    log: str = ""


class HttpsStatusOut(BaseModel):
    """Current reverse-proxy state, read from data/https_state.json."""

    enabled: bool  # True when HTTPS is actually serving (apply.sh ran / Caddy up)
    manageable: bool  # True when the UI can re-apply config (apply_command wired)
    hostname: str  # mDNS short name, reachable as <hostname>.local
    domain: str  # public domain, or "" if none
    lan_url: str  # e.g. https://offgridcloud.local
    public_url: str  # e.g. https://cloud.example.com, or "" if no domain
    ca_available: bool = False  # root CA exported by apply.sh → downloadable
    ca_fingerprint: str = ""  # SHA-256 of the root CA, to compare on the device


class HttpsConfigUpdate(BaseModel):
    """Patch semantics: only provided fields change. domain="" removes it."""

    hostname: str | None = None
    domain: str | None = None


class AuditEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    user_email: str
    action: str
    detail: str


class TransferJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    media_id: int
    provider_id: int
    status: TransferStatus
    progress: float
    bytes_transferred: int
    attempts: int
    last_error: str
    created_at: datetime
    updated_at: datetime
    # Enriched for the admin view:
    media_filename: str = ""
    provider_name: str = ""
    folder_id: int | None = None


# --- Multi-server pooling -------------------------------------------------


class PoolNodeStatus(BaseModel):
    """Compact status of one node (this box or a polled peer)."""

    name: str
    version: str = ""
    reachable: bool = True
    error: str = ""
    base_url: str = ""  # empty for this node
    peer_id: int | None = None  # set for peers
    media: dict[str, int] = {}  # media counts keyed by status
    media_total: int = 0
    active_transfers: int = 0
    throughput_kbps: float = 0.0
    disk_free: int = 0
    disk_total: int = 0
    # Remote control. Peers on older versions omit these, so the defaults keep
    # the hub from offering actions such a peer cannot perform.
    uploads_paused: bool = False
    failed_transfers: int = 0
    control_enabled: bool = False  # the caller's token may control transfers here
    pending_transfers: int | None = None  # waiting + running; None from older peers
    pool_limit_kbps: int = 0  # uplink share a hub assigned (KiB/s, 0 = none)
    # Hub view only: rank on a shared uplink and whether that rule paused it.
    priority: int = 0
    priority_paused: bool = False


class PoolTotals(BaseModel):
    nodes: int = 0
    nodes_online: int = 0
    media_total: int = 0
    active_transfers: int = 0
    throughput_kbps: float = 0.0
    disk_free: int = 0
    disk_total: int = 0


class PoolOverviewOut(BaseModel):
    """Everything the Pool admin page renders in one call."""

    self_node: PoolNodeStatus = Field(alias="self")
    peers: list[PoolNodeStatus] = []
    totals: PoolTotals = PoolTotals()

    model_config = ConfigDict(populate_by_name=True)


class PoolPeerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    base_url: str
    enabled: bool
    has_token: bool = False
    created_at: datetime


class PoolPeerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    base_url: str = Field(min_length=1, max_length=500)
    token: str = ""


class PoolPeerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    base_url: str | None = Field(default=None, min_length=1, max_length=500)
    token: str | None = None
    enabled: bool | None = None


class PoolAccessTokenOut(BaseModel):
    """A token this node issued to one hub. The plaintext is never returned."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    hint: str  # last characters, to match it with what the hub has stored
    scope: PoolTokenScope
    created_at: datetime
    last_used_at: datetime | None = None
    last_used_hub: str = ""


class PoolAccessTokenCreated(PoolAccessTokenOut):
    token: str  # shown exactly once


class PoolAccessTokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    scope: PoolTokenScope = PoolTokenScope.READ


class PoolAccessTokenUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    scope: PoolTokenScope | None = None


class DiscoveredBoxOut(BaseModel):
    """Another box announcing itself via mDNS — a suggestion, not a trusted peer."""

    node_id: str
    name: str
    version: str = ""
    url: str  # best guess for reaching it, e.g. http://192.168.1.20:8000
    addresses: list[str] = []
    peer_id: int | None = None  # already configured as a peer here


class DiscoveryOut(BaseModel):
    running: bool  # mDNS works on this box
    error: str = ""  # why not, if it doesn't
    announce: bool  # this box announces itself
    boxes: list[DiscoveredBoxOut] = []


class DiscoveryUpdate(BaseModel):
    announce: bool


class PairingCodeOut(BaseModel):
    code: str = ""  # empty = no pairing open
    expires_in: int = 0  # seconds


class PairIn(BaseModel):
    """A hub asks this box for a token, proving presence with the shown code."""

    code: str = Field(min_length=1, max_length=12)
    hub_name: str = Field(min_length=1, max_length=100)
    hub_node_id: str = Field(default="", max_length=32)


class PairOut(BaseModel):
    token: str
    node_id: str
    name: str
    version: str = ""


class PeerPairIn(BaseModel):
    """Hub side: pair with the box at ``base_url`` using the code it shows."""

    base_url: str = Field(min_length=1, max_length=500)
    code: str = Field(min_length=1, max_length=12)
    name: str | None = Field(default=None, max_length=200)


class PoolPriorityNode(BaseModel):
    peer_id: int | None = None  # None = this box (the hub)
    name: str = ""
    level: int = Field(default=0, ge=0, le=3)  # 0 = not managed, 1 = high … 3 = low


class PoolPriorityOut(BaseModel):
    enabled: bool
    nodes: list[PoolPriorityNode]
    share_enabled: bool = False  # split share_kbps fairly among ranked boxes
    share_kbps: int = 0  # capacity of the shared line, KiB/s


class PoolPriorityIn(BaseModel):
    enabled: bool
    nodes: list[PoolPriorityNode] = []
    share_enabled: bool | None = None
    share_kbps: int | None = Field(default=None, ge=0, le=10_000_000)


class TransferControlIn(BaseModel):
    """One manual transfer action, locally or sent by a pool hub."""

    action: Literal["pause", "resume", "retry_failed"]


class PoolLimitIn(BaseModel):
    """A hub assigns this box its share of a shared uplink (0 clears it)."""

    kbps: int = Field(ge=0, le=10_000_000)


class PoolLimitOut(BaseModel):
    pool_limit_kbps: int
    changed: bool


class TransferControlOut(BaseModel):
    uploads_paused: bool
    failed_transfers: int
    message: str = ""  # what the last action did, e.g. "3 … neu eingereiht"


# --- WebAuthn / passkeys ----------------------------------------------------
class WebAuthnRegisterVerify(BaseModel):
    """Attestation returned by navigator.credentials.create(), plus our nonce."""

    nonce: str
    credential: dict  # raw PublicKeyCredential JSON from the browser
    name: str = ""


class WebAuthnLoginOptionsRequest(BaseModel):
    email: str | None = None


class WebAuthnLoginVerify(BaseModel):
    nonce: str
    credential: dict


class PasskeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    rp_id: str
    created_at: datetime
    last_used_at: datetime | None


class PasskeyRename(BaseModel):
    name: str


# --- Attached storage media ------------------------------------------------


class StoragePartitionOut(BaseModel):
    name: str
    path: str
    size: int
    fstype: str
    label: str
    uuid: str
    mountpoint: str
    read_only: bool
    total: int
    used: int
    free: int
    system: bool
    buffer: bool
    protected: bool
    browsable: bool
    busy: bool
    cache: bool = False
    buffer_drive: bool = False  # marked as additional media buffer (by UUID)


class StorageDiskOut(BaseModel):
    name: str
    path: str
    size: int
    model: str
    vendor: str
    transport: str
    removable: bool
    read_only: bool
    system: bool
    buffer: bool
    protected: bool
    partitions: list[StoragePartitionOut]


class StorageBufferOut(BaseModel):
    """A removable drive marked as additional media buffer."""

    uuid: str
    label: str
    device: str | None  # None while the drive is unplugged
    mountpoint: str
    online: bool  # mounted and writable — new files may land on it
    total: int
    free: int
    used: int
    pending: int  # files on it not yet at every provider
    error: str = ""


class StorageOverviewOut(BaseModel):
    manage_enabled: bool
    filesystems: list[str]
    label_max: dict[str, int]
    disks: list[StorageDiskOut]
    buffer: DiskUsageOut
    buffers: list[StorageBufferOut] = Field(default_factory=list)
    error: str = ""


class StorageDeviceRequest(BaseModel):
    device: str


class StorageFormatRequest(BaseModel):
    device: str
    fstype: str = "exfat"
    label: str = ""
    # Must repeat the device name (e.g. "sda") — guards against a mis-click.
    confirm: str


class StorageKnownOut(BaseModel):
    """Where an identical file already lives on the box."""

    match: str  # exact | likely
    media_id: int
    status: str
    folder_id: int
    folder_name: str


class StorageEntryOut(BaseModel):
    name: str
    path: str
    is_dir: bool
    size: int
    modified: datetime | None
    is_media: bool
    known: StorageKnownOut | None = None


class StorageBrowseOut(BaseModel):
    device: str
    mountpoint: str
    path: str
    parent: str | None
    entries: list[StorageEntryOut]
    truncated: bool = False


class StorageImportRequest(BaseModel):
    device: str
    paths: list[str] = Field(min_length=1, max_length=5000)
    folder_id: int
    media_only: bool = False
    skip_duplicates: bool = True
    tags: list[str] = Field(default_factory=list, max_length=20)


class StorageImportJobOut(BaseModel):
    id: str
    kind: str
    device: str
    folder_id: int | None
    folder_name: str
    target: str
    eject_after: bool
    auto: bool = False
    user_id: int | None
    status: str
    total_files: int
    total_bytes: int
    done_files: int
    done_bytes: int
    imported: int
    skipped: int
    failed: int
    current: str
    error: str
    errors: list[str]
    created_at: str
    finished_at: str | None



class AutoImportMediumOut(BaseModel):
    device: str
    label: str
    state: str  # busy | done | error | ignored
    message: str
    job_id: str
    ejected: bool


class AutoImportOut(BaseModel):
    enabled: bool
    mode: str  # import | cache
    folder_id: int | None
    cache_uuid: str
    media_only: bool
    skip_duplicates: bool
    eject_after: bool
    led_enabled: bool
    led_name: str
    # Read-only context for the page.
    manage_enabled: bool
    leds: list[str]
    led_active: str  # the LED actually used ("" = none found)
    led_state: str  # idle | busy | done | error
    media: list[AutoImportMediumOut]


class AutoImportUpdate(BaseModel):
    enabled: bool | None = None
    mode: Literal["import", "cache"] | None = None
    folder_id: int | None = None
    cache_uuid: str | None = Field(default=None, max_length=64)
    media_only: bool | None = None
    skip_duplicates: bool | None = None
    eject_after: bool | None = None
    led_enabled: bool | None = None
    led_name: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.:-]{0,64}$")


class LedTestRequest(BaseModel):
    state: Literal["busy", "done", "error"]


class StorageGalleryItem(BaseModel):
    path: str
    name: str
    dir: str
    size: int
    mtime: float
    kind: str  # photo | raw | video | audio
    known: StorageKnownOut | None = None


class StorageGalleryOut(BaseModel):
    device: str
    mountpoint: str
    path: str
    items: list[StorageGalleryItem]


class StorageCacheSnapshotOut(BaseModel):
    name: str
    path: str  # relative to the cache partition's mountpoint
    files: int
    size: int
    modified: datetime | None


class StorageCacheOut(BaseModel):
    uuid: str
    label: str
    device: str | None  # None while the drive is unplugged
    mountpoint: str
    total: int
    free: int
    snapshots: list[StorageCacheSnapshotOut]
    error: str = ""


class StorageCacheBackupRequest(BaseModel):
    device: str
    # Empty = the whole medium.
    paths: list[str] = Field(default_factory=list, max_length=5000)
    cache_uuid: str | None = None
    media_only: bool = True
    only_new: bool = True
    eject_after: bool = False


# --- First-boot setup wizard (prebuilt image) ------------------------------


class SetupStatusOut(BaseModel):
    required: bool
    code_required: bool = False
    suggested_hostname: str = ""
    country: str = ""


class SetupCompleteRequest(BaseModel):
    code: str = ""
    admin_email: str
    admin_password: str = Field(min_length=8)
    admin_name: str = Field(default="", max_length=200)
    hostname: str
    # Empty = keep the current uplink (cable / Wi-Fi set up in Raspberry Pi Imager).
    wifi_ssid: str = ""
    wifi_password: str = ""
    fallback_password: str
    country: str = "DE"

    _norm_email = field_validator("admin_email")(_normalise_email)


class SetupCompleteOut(BaseModel):
    hostname: str
    url: str
    wifi_ssid: str
    finish_started: bool


# --- Bluetooth (companion app, docs/BLE-PROTOKOLL.md) --------------------------


class BleStatusOut(BaseModel):
    enabled: bool
    # offgridcloud-ble reported itself recently (installed and running).
    service_running: bool
    box_name: str
    # Static X25519 key of the box, base64url without padding (§3.2).
    box_public_key: str


class BleSettingsUpdate(BaseModel):
    enabled: bool


class BlePairingOut(BaseModel):
    code: str
    link: str
    expires_at: datetime
    # Seconds the code stays valid. The portal counts down from this rather
    # than from expires_at: a Pi without RTC may disagree with the browser clock.
    expires_in: int


class BleDeviceOut(BaseModel):
    id: int
    name: str
    user_id: int
    user_email: str
    created_at: datetime
    last_seen_at: datetime | None


class BleDeviceUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
