"""Database setup — SQLAlchemy engine, session factory, initialization."""

import logging
import sqlite3
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Anchored to the project root (db/ is one level below it) — a relative
# path silently created a fresh empty database when the bot was launched
# from any other working directory.
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "realmai.db"

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy table models."""


def _configure_sqlite(dbapi_conn: sqlite3.Connection, connection_record: object) -> None:
    """Per-connection SQLite hardening (M5b).

    - foreign_keys: enforce FK constraints (off by default in SQLite).
    - journal_mode=WAL: readers don't block the writer — persistence runs
      in worker threads (asyncio.to_thread) while the bot keeps reading.
      No-op on in-memory databases.
    - busy_timeout: wait for a locked database instead of failing fast
      with 'database is locked'.
    """
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def get_engine(db_url: str | None = None) -> Engine:
    """Create a SQLAlchemy engine.

    In-memory databases (tests/dev) get a StaticPool with the thread check
    disabled: every thread must see the SAME database, otherwise work
    off-loaded via ``asyncio.to_thread`` would silently write to a fresh
    empty copy.

    Args:
        db_url: Database URL. Defaults to SQLite at data/realmai.db.
    """
    url = db_url or f"sqlite:///{DB_PATH}"
    kwargs: dict = {}
    if url.startswith("sqlite") and ":memory:" in url:
        kwargs = {
            "connect_args": {"check_same_thread": False},
            "poolclass": StaticPool,
        }
    engine = create_engine(url, **kwargs)
    event.listen(engine, "connect", _configure_sqlite)
    return engine


def get_session_factory(engine: Engine | None = None) -> sessionmaker[Session]:
    """Create a session factory bound to an engine."""
    if engine is None:
        engine = get_engine()
    return sessionmaker(bind=engine)


def init_db(engine: Engine | None = None) -> None:
    """Create all tables and reconcile any missing columns. Creates data/ if needed.

    Delegates to :func:`db.migrations.ensure_schema`, which runs
    ``create_all()`` and then adds any model column an existing table is missing
    (``create_all`` alone never alters an existing table). Imported locally to
    avoid an import cycle (``db.migrations`` imports ``Base`` from this module).
    """
    if engine is None:
        engine = get_engine()
    url_str = str(engine.url)
    if ":memory:" not in url_str:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    from db.migrations import ensure_schema

    ensure_schema(engine)
