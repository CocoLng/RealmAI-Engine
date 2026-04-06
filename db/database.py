"""Database setup — SQLAlchemy engine, session factory, initialization."""

from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DB_PATH = Path("data/realmai.db")


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy table models."""


def _enable_sqlite_fk(dbapi_conn, connection_record):  # type: ignore[no-untyped-def]  # noqa: ANN001
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


def _migrate_schema(engine: Engine) -> None:
    """Add columns introduced after initial schema creation.

    SQLAlchemy's create_all() only creates missing tables, not missing columns.
    This handles incremental column additions for existing databases.
    """
    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA table_info(campaigns)"))
        columns = {row[1] for row in result}
        if not columns:
            # Table doesn't exist yet (no models imported) — skip migration
            return
        if "combat_state_json" not in columns:
            conn.execute(
                text("ALTER TABLE campaigns ADD COLUMN combat_state_json TEXT")
            )
            conn.commit()

        # Add language column to guild_configs if missing
        result2 = conn.execute(text("PRAGMA table_info(guild_configs)"))
        gc_columns = {row[1] for row in result2}
        if gc_columns and "language" not in gc_columns:
            conn.execute(
                text("ALTER TABLE guild_configs ADD COLUMN language TEXT DEFAULT 'fr'")
            )
            conn.commit()


def init_db(engine: Engine | None = None) -> None:
    """Create all tables. Creates data/ directory if needed."""
    if engine is None:
        engine = get_engine()
    url_str = str(engine.url)
    if ":memory:" not in url_str:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)
    _migrate_schema(engine)
