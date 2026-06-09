"""Pydantic request/response schemas.

Note: ``email`` is the login identifier. For a self-hosted, often-offline
appliance it must accept addresses like ``admin@offgrid.local`` — so we use a
light format check, not strict deliverability validation.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import MediaStatus, ProviderStatus, Role, TransferStatus


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
    media_count: int = 0


class FolderAccessUpdate(BaseModel):
    """Replace the full set of users with upload access to a folder."""

    user_ids: list[int]


# --- Media & uploads ------------------------------------------------------


class MediaItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    folder_id: int
    filename: str
    size: int
    sha256: str
    status: MediaStatus
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


# --- Folder <-> Provider links & transfers --------------------------------


class FolderProviderLinkCreate(BaseModel):
    provider_id: int
    dest_path: str = ""


class FolderProviderLinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    folder_id: int
    provider_id: int
    provider_name: str = ""
    dest_path: str
    enabled: bool


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
