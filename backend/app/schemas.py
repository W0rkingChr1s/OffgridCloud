"""Pydantic request/response schemas.

Note: ``email`` is the login identifier. For a self-hosted, often-offline
appliance it must accept addresses like ``admin@offgrid.local`` — so we use a
light format check, not strict deliverability validation.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import Role


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
