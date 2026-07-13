"""Database setup (SQLite via SQLAlchemy).

SQLite keeps the appliance dependency-free — important for a 1 GB Raspberry Pi.
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
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


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    """Enforce ``ON DELETE CASCADE`` on SQLite.

    SQLite ships with foreign-key enforcement *off* by default, so the
    ``ForeignKey(..., ondelete="CASCADE")`` declarations across the models are
    silently ignored — deleting a media item or folder would leave orphaned
    ``transfer_jobs`` behind. Turning the pragma on per-connection makes the
    declared cascades actually fire. Guarded so it's a no-op for non-SQLite DBs.
    """
    if "sqlite" not in engine.url.drivername:
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


# Lightweight additive migrations for SQLite: ``create_all`` never alters an
# existing table, so columns added to a model after a DB already exists would be
# missing. Until Alembic is introduced, we add any missing columns by hand. Each
# entry is (table, column, SQL column definition incl. a DEFAULT).
_ADDED_COLUMNS: list[tuple[str, str, str]] = [
    ("system_settings", "delete_remote_on_local_delete", "BOOLEAN NOT NULL DEFAULT 0"),
    ("system_settings", "auto_resync", "BOOLEAN NOT NULL DEFAULT 1"),
    # Notification "Info-Service" — added after the original webhook column.
    ("media_items", "notified_failed", "BOOLEAN NOT NULL DEFAULT 0"),
    ("system_settings", "notify_on_received", "BOOLEAN NOT NULL DEFAULT 0"),
    ("system_settings", "notify_on_done", "BOOLEAN NOT NULL DEFAULT 1"),
    ("system_settings", "notify_on_failed", "BOOLEAN NOT NULL DEFAULT 1"),
    ("system_settings", "notify_on_low_space", "BOOLEAN NOT NULL DEFAULT 1"),
    ("system_settings", "telegram_bot_token_encrypted", "TEXT NOT NULL DEFAULT ''"),
    ("system_settings", "telegram_chat_id", "VARCHAR(64) NOT NULL DEFAULT ''"),
    ("system_settings", "smtp_host", "VARCHAR(255) NOT NULL DEFAULT ''"),
    ("system_settings", "smtp_port", "INTEGER NOT NULL DEFAULT 587"),
    ("system_settings", "smtp_username", "VARCHAR(255) NOT NULL DEFAULT ''"),
    ("system_settings", "smtp_password_encrypted", "TEXT NOT NULL DEFAULT ''"),
    ("system_settings", "smtp_from", "VARCHAR(255) NOT NULL DEFAULT ''"),
    ("system_settings", "smtp_to", "VARCHAR(255) NOT NULL DEFAULT ''"),
    ("system_settings", "smtp_tls", "BOOLEAN NOT NULL DEFAULT 1"),
    ("system_settings", "low_space_notified", "BOOLEAN NOT NULL DEFAULT 0"),
    ("system_settings", "pool_token", "VARCHAR(128) NOT NULL DEFAULT ''"),
]


def _apply_additive_migrations() -> None:
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, column, ddl in _ADDED_COLUMNS:
            if table not in existing_tables:
                continue  # create_all just made it with the column present
            cols = {c["name"] for c in inspector.get_columns(table)}
            if column not in cols:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


def init_db() -> None:
    """Create tables. Replace with Alembic migrations as the schema grows."""
    # Import models so they register with the metadata before create_all.
    from . import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _apply_additive_migrations()


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a scoped session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
