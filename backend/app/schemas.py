"""Pydantic request/response schemas.

Note: ``email`` is the login identifier. For a self-hosted, often-offline
appliance it must accept addresses like ``admin@offgrid.local`` — so we use a
light format check, not strict deliverability validation.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import MediaStatus, ProviderStatus, Role, TransferStatus, VpnType


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


class BandwidthStatusOut(BaseModel):
    enabled: bool
    min_bandwidth_kbps: int
    bwlimit_kbps: int
    schedule: list[BandwidthWindow]
    last_kbps: float
    last_measured_at: datetime | None
    effective_bwlimit_kbps: int  # what applies right now
    gated: bool  # uploads currently paused by the min-bandwidth gate
    gate_reason: str


class DiskUsageOut(BaseModel):
    total: int
    used: int
    free: int
    percent_used: float
    low_space: bool


class SystemStatusOut(BaseModel):
    delete_local_after_upload: bool
    delete_remote_on_local_delete: bool
    auto_resync: bool
    reconcile_interval: float
    probe_url: str
    webhook_url: str
    disk: DiskUsageOut
    rclone_available: bool


class SystemSettingsUpdate(BaseModel):
    delete_local_after_upload: bool | None = None
    delete_remote_on_local_delete: bool | None = None
    auto_resync: bool | None = None
    probe_url: str | None = None
    webhook_url: str | None = None


class MediaDeleteResult(BaseModel):
    deleted: bool
    remote_attempted: int = 0
    remote_deleted: int = 0
    remote_errors: list[str] = []
# --- Network redundancy / AP fallback -------------------------------------


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


class NetworkSettingsOut(BaseModel):
    fallback_enabled: bool
    ap_ssid: str
    ap_hidden: bool
    ap_address: str
    country_code: str
    check_interval: int
    fail_threshold: int
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


class NetworkOverviewOut(BaseModel):
    """Everything the Network admin page needs in one call."""

    status: NetworkStatusOut
    settings: NetworkSettingsOut
    known_networks: list[KnownNetworkOut]


class NetworkApplyResult(BaseModel):
    ok: bool
    message: str
    output: str = ""


class WifiScanOut(BaseModel):
    ssids: list[str]


class UpdateInfoOut(BaseModel):
    current: str
    latest: str | None = None
    update_available: bool = False
    release_url: str = ""
    release_name: str = ""
    published_at: str = ""
    notes: str = ""
    error: str = ""
    # Whether one-click apply is wired up on this instance.
    self_update_enabled: bool = False


class UpdateApplyResult(BaseModel):
    started: bool
    message: str


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
