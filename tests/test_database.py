"""Tests for database initialization and schema migrations."""

import pytest
from sqlalchemy import create_engine, text

from db.database import Base, _migrate_schema
from db.models import CampaignRow  # noqa: F401


class TestMigrateSchema:
    """Tests for _migrate_schema versioned migration system."""

    def test_adds_missing_column(self) -> None:
        """If combat_state_json is missing, migration adds it."""
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        _migrate_schema(engine)

        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA table_info(campaigns)"))
            columns = {row[1] for row in result}
        assert "combat_state_json" in columns

    def test_migration_is_idempotent(self) -> None:
        """Running migration twice does not error."""
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        _migrate_schema(engine)
        _migrate_schema(engine)  # second run should be a no-op

        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA table_info(campaigns)"))
            columns = {row[1] for row in result}
        assert "combat_state_json" in columns

    def test_user_version_set_after_all_migrations(self) -> None:
        """PRAGMA user_version reflects the latest migration version."""
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        _migrate_schema(engine)

        with engine.connect() as conn:
            raw = conn.connection.dbapi_connection
            version = raw.execute("PRAGMA user_version").fetchone()[0]  # type: ignore[union-attr]
        assert version == 2  # v0→v1 + v1→v2

    def test_v2_adds_current_beat_index_column(self) -> None:
        """V1→V2 migration adds current_beat_index to story_arcs."""
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        _migrate_schema(engine)

        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA table_info(story_arcs)"))
            columns = {row[1] for row in result}
        assert "current_beat_index" in columns

    def test_partial_migration_rolls_back(self) -> None:
        """If a migration function raises, user_version stays at previous level."""
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)

        def _boom(raw: object) -> None:
            raise RuntimeError("boom")

        import db.database as db_mod
        original = db_mod._MIGRATIONS[:]
        try:
            db_mod._MIGRATIONS[1] = _boom  # replace v1→v2 with failure
            with pytest.raises(RuntimeError, match="boom"):
                _migrate_schema(engine)
        finally:
            db_mod._MIGRATIONS[:] = original

        # user_version should be 1 (v0→v1 succeeded, v1→v2 rolled back)
        with engine.connect() as conn:
            raw = conn.connection.dbapi_connection
            version = raw.execute("PRAGMA user_version").fetchone()[0]  # type: ignore[union-attr]
        assert version == 1

    def test_skips_already_applied_migrations(self) -> None:
        """Migrations already applied (by user_version) are skipped."""
        from unittest.mock import MagicMock

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        _migrate_schema(engine)  # applies v1 + v2

        import db.database as db_mod
        mock_v1 = MagicMock()
        mock_v2 = MagicMock()
        original = db_mod._MIGRATIONS[:]
        try:
            db_mod._MIGRATIONS[:] = [mock_v1, mock_v2]
            _migrate_schema(engine)  # should skip both
            mock_v1.assert_not_called()
            mock_v2.assert_not_called()
        finally:
            db_mod._MIGRATIONS[:] = original
