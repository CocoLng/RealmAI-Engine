"""Tests for db/database.py — engine configuration (M5b) and DB path."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from db.database import DB_PATH, Base, get_engine


class TestDBPath:
    """Low — the default DB path must not depend on the CWD.

    Launching the bot from another directory used to silently create a
    fresh empty database wherever the shell happened to be.
    """

    def test_db_path_is_absolute(self) -> None:
        assert DB_PATH.is_absolute()

    def test_db_path_anchored_to_project_root(self) -> None:
        import db

        project_root = Path(db.__file__).resolve().parent.parent
        assert DB_PATH == project_root / "data" / "realmai.db"


class TestSQLitePragmas:
    """M5b — WAL journal + busy timeout on every connection."""

    def test_file_db_uses_wal_and_busy_timeout(self, tmp_path) -> None:
        engine = get_engine(f"sqlite:///{tmp_path}/realmai.db")
        try:
            with engine.connect() as conn:
                journal = conn.exec_driver_sql("PRAGMA journal_mode").scalar()
                busy = conn.exec_driver_sql("PRAGMA busy_timeout").scalar()
                fks = conn.exec_driver_sql("PRAGMA foreign_keys").scalar()
            assert journal == "wal"
            assert busy == 5000
            assert fks == 1
        finally:
            engine.dispose()

    def test_memory_db_keeps_foreign_keys(self) -> None:
        engine = get_engine("sqlite:///:memory:")
        try:
            with engine.connect() as conn:
                fks = conn.exec_driver_sql("PRAGMA foreign_keys").scalar()
            assert fks == 1
        finally:
            engine.dispose()


class TestMemoryEngineThreading:
    """In-memory engines must expose ONE shared database across threads.

    persist_session runs via asyncio.to_thread; with the default
    SingletonThreadPool each worker thread got a fresh empty :memory: DB
    and writes silently landed nowhere.
    """

    def test_worker_thread_sees_same_database(self) -> None:
        engine = get_engine("sqlite:///:memory:")
        try:
            Base.metadata.create_all(engine)

            def list_tables() -> list[str]:
                with engine.connect() as conn:
                    rows = conn.exec_driver_sql(
                        "SELECT name FROM sqlite_master WHERE type='table'",
                    ).fetchall()
                return [r[0] for r in rows]

            with ThreadPoolExecutor(max_workers=1) as pool:
                tables_in_worker = pool.submit(list_tables).result()

            assert "campaigns" in tables_in_worker
        finally:
            engine.dispose()
