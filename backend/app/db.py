"""Database setup (SQLite via SQLAlchemy).

SQLite keeps the appliance dependency-free — important for a 1 GB Raspberry Pi.
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    """Base class for all ORM models."""


_settings = get_settings()
_settings.ensure_dirs()

engine = create_engine(
    _settings.database_url,
    # check_same_thread=False is required for SQLite + FastAPI's threadpool.
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    """Create tables. Replace with Alembic migrations as the schema grows."""
    # Import models so they register with the metadata before create_all.
    from . import models  # noqa: F401

    Base.metadata.create_all(bind=engine)


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a scoped session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
