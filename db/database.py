"""Database setup — SQLAlchemy engine, session factory, initialization."""

import logging
import sqlite3
from pathlib import Path

from sqlalchemy import create_engine, event
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
