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
    # Pool discovery: stable box identity + whether to announce via mDNS.
    ("system_settings", "node_id", "VARCHAR(32) NOT NULL DEFAULT ''"),
    ("system_settings", "pool_announce", "BOOLEAN NOT NULL DEFAULT 1"),
    ("pool_peers", "node_id", "VARCHAR(32) NOT NULL DEFAULT ''"),
    # Pool priorities between boxes on a shared uplink.
    ("system_settings", "pool_priority_enabled", "BOOLEAN NOT NULL DEFAULT 0"),
    ("system_settings", "pool_priority_self", "INTEGER NOT NULL DEFAULT 0"),
    ("system_settings", "pool_priority_paused", "BOOLEAN NOT NULL DEFAULT 0"),
    ("system_settings", "pool_share_enabled", "BOOLEAN NOT NULL DEFAULT 0"),
    ("system_settings", "pool_share_kbps", "INTEGER NOT NULL DEFAULT 0"),
    ("pool_peers", "priority", "INTEGER NOT NULL DEFAULT 0"),
    ("pool_peers", "priority_paused", "BOOLEAN NOT NULL DEFAULT 0"),
    # Operational status announcements (startup / reconnect / bandwidth gate).
    ("system_settings", "notify_on_startup", "BOOLEAN NOT NULL DEFAULT 1"),
    ("system_settings", "notify_on_reconnect", "BOOLEAN NOT NULL DEFAULT 1"),
    ("system_settings", "notify_on_bandwidth", "BOOLEAN NOT NULL DEFAULT 1"),
    ("system_settings", "notify_on_pool", "BOOLEAN NOT NULL DEFAULT 1"),
    ("system_settings", "notify_on_auto_import", "BOOLEAN NOT NULL DEFAULT 1"),
    # WebAuthn user handle — added to an existing users table. Nullable (no
    # DEFAULT): filled on first passkey registration.
    ("users", "webauthn_user_handle", "BLOB"),
    # Storage import: source file mtime for duplicate detection. Nullable.
    ("media_items", "source_mtime", "FLOAT"),
    # Watchdog: probing for a known network while hosting the fallback AP.
    ("network_settings", "ap_probe_interval", "INTEGER NOT NULL DEFAULT 120"),
    ("network_settings", "ap_blind_probe_interval", "INTEGER NOT NULL DEFAULT 600"),
    # Uploads over a metered uplink (USB tethering): pause or daily budget.
    ("bandwidth_policy", "metered_mode", "VARCHAR(8) NOT NULL DEFAULT 'allow'"),
    ("bandwidth_policy", "metered_budget_mb", "INTEGER NOT NULL DEFAULT 500"),
    ("bandwidth_policy", "metered_used_bytes", "BIGINT NOT NULL DEFAULT 0"),
    ("bandwidth_policy", "metered_day", "VARCHAR(10) NOT NULL DEFAULT ''"),
    # Link bundling via net-merge (all uplinks at once).
    ("bandwidth_policy", "bonding_enabled", "BOOLEAN NOT NULL DEFAULT 0"),
    ("bandwidth_policy", "parallel_uploads", "INTEGER NOT NULL DEFAULT 1"),
    # Manual upload stop (locally or by a pool hub with control rights).
    ("bandwidth_policy", "uploads_paused", "BOOLEAN NOT NULL DEFAULT 0"),
    # Uplink share assigned by a pool hub (expires unless renewed).
    ("bandwidth_policy", "pool_limit_kbps", "INTEGER NOT NULL DEFAULT 0"),
    ("bandwidth_policy", "pool_limit_until", "DATETIME"),
    ("bandwidth_policy", "pool_limit_by", "VARCHAR(100) NOT NULL DEFAULT ''"),
    # Bluetooth management for the companion app.
    ("system_settings", "ble_enabled", "BOOLEAN NOT NULL DEFAULT 1"),
    # Unattended updates (app.autoupdate).
    ("system_settings", "auto_update_enabled", "BOOLEAN NOT NULL DEFAULT 0"),
    ("system_settings", "auto_update_start_hour", "INTEGER NOT NULL DEFAULT 3"),
    ("system_settings", "auto_update_end_hour", "INTEGER NOT NULL DEFAULT 5"),
    ("system_settings", "auto_update_metered", "BOOLEAN NOT NULL DEFAULT 0"),
    ("system_settings", "auto_update_last_day", "VARCHAR(10) NOT NULL DEFAULT ''"),
    ("system_settings", "auto_update_failed_target", "VARCHAR(100) NOT NULL DEFAULT ''"),
    # Upload handover over BLE (app.handover).
    ("system_settings", "handover_users_may_pause", "BOOLEAN NOT NULL DEFAULT 0"),
    ("ble_devices", "wifi_psk_encrypted", "TEXT NOT NULL DEFAULT ''"),
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
