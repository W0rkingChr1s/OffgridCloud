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


class MediaItem(Base):
    __tablename__ = "media_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    folder_id: Mapped[int] = mapped_column(ForeignKey("folders.id", ondelete="CASCADE"))
    filename: Mapped[str] = mapped_column(String(500))
    stored_path: Mapped[str] = mapped_column(Text)
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[MediaStatus] = mapped_column(Enum(MediaStatus), default=MediaStatus.RECEIVED)
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    folder: Mapped[UploadFolder] = relationship(back_populates="media")


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
    progress: Mapped[float] = mapped_column(default=0.0)
    bytes_transferred: Mapped[int] = mapped_column(BigInteger, default=0)
    attempts: Mapped[int] = mapped_column(default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


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
