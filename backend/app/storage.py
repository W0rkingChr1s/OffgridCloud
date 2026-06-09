"""Storage paths, filename safety and folder-access checks."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import FolderAccess, FolderGroupAccess, GroupMembership, Role, User


def safe_filename(name: str) -> str:
    """Strip any path components and control characters from a client filename."""
    name = name.replace("\\", "/").split("/")[-1]
    name = "".join(ch for ch in name if ch.isprintable()).strip()
    # Avoid hidden/relative names.
    if not name or name in {".", ".."}:
        return "unnamed"
    return name[:500]


def folder_dir(folder_id: int) -> Path:
    path = get_settings().buffer_dir / str(folder_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def uploads_tmp_dir() -> Path:
    path = get_settings().buffer_dir / ".uploads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def user_can_access_folder(db: Session, user: User, folder_id: int) -> bool:
    if user.role == Role.ADMIN:
        return True
    direct = db.scalar(
        select(FolderAccess).where(
            FolderAccess.folder_id == folder_id, FolderAccess.user_id == user.id
        )
    )
    if direct is not None:
        return True
    via_group = db.scalar(
        select(FolderGroupAccess)
        .join(GroupMembership, GroupMembership.group_id == FolderGroupAccess.group_id)
        .where(
            FolderGroupAccess.folder_id == folder_id,
            GroupMembership.user_id == user.id,
        )
    )
    return via_group is not None


def accessible_folder_ids(db: Session, user: User) -> set[int]:
    """Folder ids a non-admin user can access (direct + via group membership)."""
    direct = set(
        db.scalars(select(FolderAccess.folder_id).where(FolderAccess.user_id == user.id))
    )
    via_group = set(
        db.scalars(
            select(FolderGroupAccess.folder_id)
            .join(GroupMembership, GroupMembership.group_id == FolderGroupAccess.group_id)
            .where(GroupMembership.user_id == user.id)
        )
    )
    return direct | via_group
