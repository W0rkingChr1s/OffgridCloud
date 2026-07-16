"""ORM models.

Phase 0: User. Phase 1: roles/auth. Phase 2: folders, per-user access, media
items and resumable upload sessions. Providers and transfer jobs follow later
(see docs/ENTWICKLUNGSPLAN.md).
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class Role(str, enum.Enum):
    ADMIN = "admin"
    USER = "user"


class ProviderStatus(str, enum.Enum):
    UNKNOWN = "unknown"
    OK = "ok"
    ERROR = "error"


class TransferStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class MediaStatus(str, enum.Enum):
    RECEIVED = "received"
    QUEUED = "queued"
    UPLOADING = "uploading"
    VERIFIED = "verified"
    DONE = "done"
    FAILED = "failed"


class VpnType(str, enum.Enum):
    WIREGUARD = "wireguard"
    OPENVPN = "openvpn"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(Enum(Role), default=Role.USER)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    # WebAuthn: stable non-PII user handle for discoverable credentials.
    # Nullable + filled lazily on first passkey registration (existing users
    # created before this feature won't have one yet).
    webauthn_user_handle: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)


class WebAuthnCredential(Base):
    """A registered passkey. Bound to one RP-ID (origin) — a user may hold one
    credential per origin (e.g. offgridcloud.local and a public domain)."""

    __tablename__ = "webauthn_credentials"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    credential_id: Mapped[bytes] = mapped_column(LargeBinary, unique=True, index=True)
    public_key: Mapped[bytes] = mapped_column(LargeBinary)
    sign_count: Mapped[int] = mapped_column(default=0)
    rp_id: Mapped[str] = mapped_column(String(255))
    transports: Mapped[str] = mapped_column(String(255), default="")  # JSON list
    name: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class UploadFolder(Base):
    __tablename__ = "folders"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    access: Mapped[list[FolderAccess]] = relationship(
        back_populates="folder", cascade="all, delete-orphan"
    )
    media: Mapped[list[MediaItem]] = relationship(
        back_populates="folder", cascade="all, delete-orphan"
    )


class FolderAccess(Base):
    """Which user may upload into which folder (m:n)."""

    __tablename__ = "folder_access"
    __table_args__ = (UniqueConstraint("folder_id", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    folder_id: Mapped[int] = mapped_column(ForeignKey("folders.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    folder: Mapped[UploadFolder] = relationship(back_populates="access")


class Group(Base):
    """A team. Users in a group inherit access to folders shared with it."""

    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class GroupMembership(Base):
    __tablename__ = "group_memberships"
    __table_args__ = (UniqueConstraint("group_id", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))


class FolderGroupAccess(Base):
    """Grants a whole group upload access to a folder (m:n)."""

    __tablename__ = "folder_group_access"
    __table_args__ = (UniqueConstraint("folder_id", "group_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    folder_id: Mapped[int] = mapped_column(ForeignKey("folders.id", ondelete="CASCADE"))
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"))


class MediaItem(Base):
    __tablename__ = "media_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    folder_id: Mapped[int] = mapped_column(ForeignKey("folders.id", ondelete="CASCADE"))
    filename: Mapped[str] = mapped_column(String(500))
    stored_path: Mapped[str] = mapped_column(Text)
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[MediaStatus] = mapped_column(Enum(MediaStatus), default=MediaStatus.RECEIVED)
    local_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    # Dedup flags so a given notification is sent at most once per episode.
    notified: Mapped[bool] = mapped_column(Boolean, default=False)  # "done" sent
    notified_failed: Mapped[bool] = mapped_column(Boolean, default=False)  # "failed" sent
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    folder: Mapped[UploadFolder] = relationship(back_populates="media")


class MediaTag(Base):
    """A free-form label on a media item, for search & filtering.

    Tags are lower-cased and de-duplicated per item. The FK cascade (enforced on
    SQLite via the ``PRAGMA foreign_keys`` hook in ``db.py``) drops tags with
    their media item, so no ORM relationship is needed here.
    """

    __tablename__ = "media_tags"
    __table_args__ = (UniqueConstraint("media_id", "tag"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    media_id: Mapped[int] = mapped_column(
        ForeignKey("media_items.id", ondelete="CASCADE"), index=True
    )
    tag: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class MediaDescription(Base):
    """A thematic note that summarises and describes a group of media items.

    Field teams often upload a batch of photos/videos that belong together and
    want to explain *what is shown* in one place. A description groups those
    items and carries a free-text explanation. Crucially, the note is also
    materialised as a plain-text sidecar — its own :class:`MediaItem` — so the
    explanation travels to every linked cloud target alongside the media (the
    sidecar flows through the normal transfer pipeline). ``txt_media_id`` points
    at that generated file; it goes ``NULL`` if the sidecar is deleted directly.
    """

    __tablename__ = "media_descriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    folder_id: Mapped[int] = mapped_column(ForeignKey("folders.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # The generated .txt sidecar (a MediaItem). Kept in sync on create/update and
    # removed with the description on delete.
    txt_media_id: Mapped[int | None] = mapped_column(
        ForeignKey("media_items.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class MediaDescriptionItem(Base):
    """Links a description to each media item it covers (m:n).

    The FK cascades (enforced on SQLite via the ``PRAGMA foreign_keys`` hook in
    ``db.py``) drop the link rows with either side, so a covered photo that is
    deleted simply falls out of the group without orphaning anything.
    """

    __tablename__ = "media_description_items"
    __table_args__ = (UniqueConstraint("description_id", "media_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    description_id: Mapped[int] = mapped_column(
        ForeignKey("media_descriptions.id", ondelete="CASCADE"), index=True
    )
    media_id: Mapped[int] = mapped_column(
        ForeignKey("media_items.id", ondelete="CASCADE"), index=True
    )


class PoolPeer(Base):
    """Another OffgridCloud node in the fleet, polled for an aggregated view.

    Multi-server pooling stays deliberately simple and safe for the Pi target:
    one node acts as a hub that periodically reads each peer's compact
    ``/api/pool/status`` (authenticated by the peer's shared pool token, stored
    here encrypted at rest). It is read-only aggregation — no distributed
    coordination, no writes across nodes.
    """

    __tablename__ = "pool_peers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    base_url: Mapped[str] = mapped_column(String(500))  # e.g. https://box2.local:8000
    token_encrypted: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class CloudProvider(Base):
    """A configured upload target. Credentials are stored encrypted as JSON."""

    __tablename__ = "cloud_providers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    type: Mapped[str] = mapped_column(String(50))  # registry key, e.g. "s3"
    config_encrypted: Mapped[str] = mapped_column(Text)  # encrypted JSON blob
    status: Mapped[ProviderStatus] = mapped_column(
        Enum(ProviderStatus), default=ProviderStatus.UNKNOWN
    )
    last_error: Mapped[str] = mapped_column(Text, default="")
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class FolderProviderLink(Base):
    """Maps a folder to a target provider (+ destination path/bucket prefix)."""

    __tablename__ = "folder_provider_links"
    __table_args__ = (UniqueConstraint("folder_id", "provider_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    folder_id: Mapped[int] = mapped_column(ForeignKey("folders.id", ondelete="CASCADE"))
    provider_id: Mapped[int] = mapped_column(
        ForeignKey("cloud_providers.id", ondelete="CASCADE")
    )
    dest_path: Mapped[str] = mapped_column(String(500), default="")
    priority: Mapped[int] = mapped_column(default=0)  # higher = uploaded sooner
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class TransferJob(Base):
    """One upload of a media item to one provider."""

    __tablename__ = "transfer_jobs"
    __table_args__ = (UniqueConstraint("media_id", "provider_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    media_id: Mapped[int] = mapped_column(ForeignKey("media_items.id", ondelete="CASCADE"))
    provider_id: Mapped[int] = mapped_column(
        ForeignKey("cloud_providers.id", ondelete="CASCADE")
    )
    status: Mapped[TransferStatus] = mapped_column(
        Enum(TransferStatus), default=TransferStatus.QUEUED, index=True
    )
    priority: Mapped[int] = mapped_column(default=0, index=True)
    progress: Mapped[float] = mapped_column(default=0.0)
    bytes_transferred: Mapped[int] = mapped_column(BigInteger, default=0)
    attempts: Mapped[int] = mapped_column(default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class BandwidthPolicy(Base):
    """Singleton (id=1) controlling bandwidth-aware scheduling.

    Throughput values are in KiB/s (matching rclone's --bwlimit units).
    """

    __tablename__ = "bandwidth_policy"

    id: Mapped[int] = mapped_column(primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # Minimum measured throughput required to start uploads (0 = no gate).
    min_bandwidth_kbps: Mapped[int] = mapped_column(default=0)
    # Base throttle passed to rclone --bwlimit (0 = unlimited).
    bwlimit_kbps: Mapped[int] = mapped_column(default=0)
    # JSON list of windows: [{"start":"HH:MM","end":"HH:MM","kbps":int}].
    schedule_json: Mapped[str] = mapped_column(Text, default="[]")
    # Last observed throughput (from real transfers) for the min-bandwidth gate.
    last_kbps: Mapped[float] = mapped_column(default=0.0)
    last_measured_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class SystemSettings(Base):
    """Singleton (id=1) for operational toggles."""

    __tablename__ = "system_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Delete the local buffer copy once all transfers for a media item succeed.
    delete_local_after_upload: Mapped[bool] = mapped_column(Boolean, default=False)
    # When a media item is deleted locally, also remove the copies already
    # uploaded to every linked provider (via rclone). Off by default — deleting
    # remote data is destructive and should be opted into deliberately.
    delete_remote_on_local_delete: Mapped[bool] = mapped_column(Boolean, default=False)
    # Periodically re-queue failed/stuck transfers and backfill any missing jobs
    # so a temporary outage (offline, provider down) self-heals once connectivity
    # returns — no manual "retry" needed.
    auto_resync: Mapped[bool] = mapped_column(Boolean, default=True)
    # Optional URL whose download is used to actively measure bandwidth.
    probe_url: Mapped[str] = mapped_column(String(1000), default="")
    # Optional webhook called when a media item finishes uploading everywhere.
    webhook_url: Mapped[str] = mapped_column(String(1000), default="")
    # Shared token a pool hub must present (header ``X-Pool-Token``) to read this
    # node's ``/api/pool/status``. Empty = this node does not expose itself as a
    # poolable peer. Rotate/clear from the Pool admin page.
    pool_token: Mapped[str] = mapped_column(String(128), default="")

    # --- Notifications ("Info-Service") ----------------------------------
    # Which events emit a notification on every configured channel (webhook,
    # Telegram, e-mail). "done" defaults on to preserve the original webhook
    # behaviour; the noisier "received" defaults off.
    notify_on_received: Mapped[bool] = mapped_column(Boolean, default=False)
    notify_on_done: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_on_failed: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_on_low_space: Mapped[bool] = mapped_column(Boolean, default=True)
    # Operational status announcements (see app.announce): a comprehensive
    # summary on startup, a short ping when the uplink recovers, and a message
    # when the minimum-bandwidth gate pauses/resumes sending. All default on.
    notify_on_startup: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_on_reconnect: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_on_bandwidth: Mapped[bool] = mapped_column(Boolean, default=True)
    # Telegram bot channel. Token encrypted at rest with the same key as
    # provider credentials; empty token = channel disabled.
    telegram_bot_token_encrypted: Mapped[str] = mapped_column(Text, default="")
    telegram_chat_id: Mapped[str] = mapped_column(String(64), default="")
    # SMTP e-mail channel. Password encrypted at rest; empty host = disabled.
    smtp_host: Mapped[str] = mapped_column(String(255), default="")
    smtp_port: Mapped[int] = mapped_column(default=587)
    smtp_username: Mapped[str] = mapped_column(String(255), default="")
    smtp_password_encrypted: Mapped[str] = mapped_column(Text, default="")
    smtp_from: Mapped[str] = mapped_column(String(255), default="")
    smtp_to: Mapped[str] = mapped_column(String(255), default="")
    smtp_tls: Mapped[bool] = mapped_column(Boolean, default=True)  # STARTTLS
    # Transient dedup: a low-space alert is sent once per episode and re-armed
    # when free space recovers above the threshold.
    low_space_notified: Mapped[bool] = mapped_column(Boolean, default=False)


class NetworkSettings(Base):
    """Singleton (id=1) for the access-point fallback ("Rückfallebene").

    When the box loses its upstream network it can host its own Wi-Fi AP so the
    field team keeps a way to upload. Applying this needs root, so the app only
    stores the desired config here and exports it for the privileged helper +
    watchdog (see ``network.py`` and ``deploy/netfallback/``).
    """

    __tablename__ = "network_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Master switch for hosting the fallback AP when no known network is reachable.
    fallback_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # AP the box hosts as its fallback.
    ap_ssid: Mapped[str] = mapped_column(String(32), default="OffgridCloud")
    ap_psk_encrypted: Mapped[str] = mapped_column(Text, default="")  # empty = open
    ap_hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    ap_address: Mapped[str] = mapped_column(String(32), default="10.42.0.1/24")
    country_code: Mapped[str] = mapped_column(String(2), default="")  # regulatory domain
    # Watchdog tuning: seconds between connectivity checks and how many
    # consecutive failures flip the box into AP mode.
    check_interval: Mapped[int] = mapped_column(default=20)
    fail_threshold: Mapped[int] = mapped_column(default=3)


class KnownNetwork(Base):
    """A Wi-Fi network the box should join automatically when in range.

    These are the "hinterlegten" networks: adding one and applying it tells the
    box to prefer this uplink over hosting its own AP. Passphrases are encrypted
    at rest with the same key as provider credentials.
    """

    __tablename__ = "known_networks"

    id: Mapped[int] = mapped_column(primary_key=True)
    ssid: Mapped[str] = mapped_column(String(32))
    psk_encrypted: Mapped[str] = mapped_column(Text, default="")  # empty = open
    priority: Mapped[int] = mapped_column(default=0)  # higher = preferred uplink
    autoconnect: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class VpnTunnel(Base):
    """A saved VPN client profile (WireGuard/OpenVPN), config encrypted at rest.

    Lets an off-site OffgridCloud dial into a home LAN so internal targets
    (e.g. a NAS reachable only via SMB on a private IP) become usable. Only one
    tunnel is active at a time; ``active_since`` marks the currently-connected
    profile (best-effort, reconciled against the live interface).
    """

    __tablename__ = "vpn_tunnels"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    type: Mapped[VpnType] = mapped_column(Enum(VpnType), default=VpnType.WIREGUARD)
    # Encrypted JSON: {"config": "<raw .conf/.ovpn>", "username": "", "password": ""}
    config_encrypted: Mapped[str] = mapped_column(Text)
    autostart: Mapped[bool] = mapped_column(Boolean, default=False)
    last_error: Mapped[str] = mapped_column(Text, default="")
    active_since: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class AuditEvent(Base):
    """Append-only record of notable admin actions."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    user_email: Mapped[str] = mapped_column(String(255), default="")
    action: Mapped[str] = mapped_column(String(100))
    detail: Mapped[str] = mapped_column(Text, default="")


class UploadSession(Base):
    """A resumable upload in progress. Persisted so it survives restarts."""

    __tablename__ = "upload_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # uuid4
    folder_id: Mapped[int] = mapped_column(ForeignKey("folders.id", ondelete="CASCADE"))
    filename: Mapped[str] = mapped_column(String(500))
    temp_path: Mapped[str] = mapped_column(Text)
    size: Mapped[int] = mapped_column(BigInteger, default=0)  # expected total, 0 = unknown
    received: Mapped[int] = mapped_column(BigInteger, default=0)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
