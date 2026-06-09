"""Storage paths, filename safety and folder-access checks."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import FolderAccess, Role, User


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
    stmt = select(FolderAccess).where(
        FolderAccess.folder_id == folder_id, FolderAccess.user_id == user.id
    )
    return db.scalar(stmt) is not None
