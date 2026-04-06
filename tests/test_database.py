"""Tests for database initialization and schema migrations."""

from sqlalchemy import create_engine, text

from db.database import Base, _migrate_schema
from db.models import CampaignRow  # noqa: F401


class TestMigrateSchema:
    """Tests for _migrate_schema incremental column additions."""

    def test_adds_missing_column(self):
        """If combat_state_json is missing, migration adds it."""
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        _migrate_schema(engine)

        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA table_info(campaigns)"))
            columns = {row[1] for row in result}
        assert "combat_state_json" in columns

    def test_migration_is_idempotent(self):
        """Running migration twice does not error."""
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        _migrate_schema(engine)
        _migrate_schema(engine)  # second run should be a no-op

        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA table_info(campaigns)"))
            columns = {row[1] for row in result}
        assert "combat_state_json" in columns
