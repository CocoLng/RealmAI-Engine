"""Database setup — SQLAlchemy engine, session factory, initialization."""

import logging
import sqlite3
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DB_PATH = Path("data/realmai.db")

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy table models."""


def _enable_sqlite_fk(dbapi_conn: sqlite3.Connection, connection_record: object) -> None:
    """Enable foreign key enforcement for SQLite connections."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def get_engine(db_url: str | None = None) -> Engine:
    """Create a SQLAlchemy engine.

    Args:
        db_url: Database URL. Defaults to SQLite at data/realmai.db.
    """
    url = db_url or f"sqlite:///{DB_PATH}"
    engine = create_engine(url)
    event.listen(engine, "connect", _enable_sqlite_fk)
    return engine


def get_session_factory(engine: Engine | None = None) -> sessionmaker[Session]:
    """Create a session factory bound to an engine."""
    if engine is None:
        engine = get_engine()
    return sessionmaker(bind=engine)


# ---------------------------------------------------------------------------
# Schema migrations — versioned via PRAGMA user_version
# ---------------------------------------------------------------------------


def _get_table_columns(raw: sqlite3.Connection, table: str) -> set[str]:
    """Return column names for a table, or empty set if table doesn't exist."""
    result = raw.execute(f"PRAGMA table_info({table})")  # noqa: S608
    return {row[1] for row in result}


def _add_column_if_missing(
    raw: sqlite3.Connection, table: str, column: str, col_type: str,
) -> None:
    """Add a column to a table if it doesn't already exist (safety guard)."""
    columns = _get_table_columns(raw, table)
    if columns and column not in columns:
        raw.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")  # noqa: S608


def _migrate_v0_to_v1(raw: sqlite3.Connection) -> None:
    """V0 → V1: all columns added before versioned migrations.

    Column-existence guards kept for safety with pre-existing databases
    that may already have some of these columns.
    """
    # campaigns
    _add_column_if_missing(raw, "campaigns", "combat_state_json", "TEXT")

    # guild_configs
    _add_column_if_missing(raw, "guild_configs", "language", "TEXT DEFAULT 'fr'")

    # npcs
    _add_column_if_missing(raw, "npcs", "aliases", "JSON DEFAULT '[]'")
    _add_column_if_missing(raw, "npcs", "secrets", "JSON DEFAULT '[]'")
    _add_column_if_missing(raw, "npcs", "knowledge", "JSON DEFAULT '[]'")
    _add_column_if_missing(raw, "npcs", "dialogue_history", "JSON DEFAULT '[]'")

    # locations
    _add_column_if_missing(raw, "locations", "item_descriptions", "JSON DEFAULT '{}'")


def _migrate_v1_to_v2(raw: sqlite3.Connection) -> None:
    """V1 → V2: extract current_beat_index from story_arcs JSON blob."""
    _add_column_if_missing(
        raw, "story_arcs", "current_beat_index", "INTEGER DEFAULT 0",
    )


def _migrate_v2_to_v3(raw: sqlite3.Connection) -> None:
    """V2 → V3: add state_flags and unlocked_exits to locations."""
    _add_column_if_missing(raw, "locations", "state_flags", "JSON DEFAULT '{}'")
    _add_column_if_missing(raw, "locations", "unlocked_exits", "JSON DEFAULT '[]'")


# Ordered list of migration functions. Index 0 = v0→v1, index 1 = v1→v2, etc.
_MIGRATIONS = [_migrate_v0_to_v1, _migrate_v1_to_v2, _migrate_v2_to_v3]


def _migrate_schema(engine: Engine) -> None:
    """Run pending schema migrations using PRAGMA user_version for tracking.

    Each migration step runs inside a transaction. On failure the
    transaction is rolled back and the exception re-raised so that
    the database is never left in a half-migrated state.
    """
    with engine.connect() as conn:
        # Check that at least the campaigns table exists (models imported)
        result = conn.execute(text("PRAGMA table_info(campaigns)"))
        if not {row[1] for row in result}:
            return

        raw: sqlite3.Connection = conn.connection.dbapi_connection  # type: ignore[assignment]

        current_version: int = raw.execute("PRAGMA user_version").fetchone()[0]

        for version, migrate_fn in enumerate(_MIGRATIONS, start=1):
            if current_version < version:
                try:
                    migrate_fn(raw)
                    raw.execute(f"PRAGMA user_version = {version}")
                    raw.commit()
                    logger.info("Schema migrated to version %d", version)
                except Exception:
                    raw.rollback()
                    logger.exception(
                        "Schema migration to version %d failed — rolled back",
                        version,
                    )
                    raise
                current_version = version


def init_db(engine: Engine | None = None) -> None:
    """Create all tables. Creates data/ directory if needed."""
    if engine is None:
        engine = get_engine()
    url_str = str(engine.url)
    if ":memory:" not in url_str:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)
    _migrate_schema(engine)
