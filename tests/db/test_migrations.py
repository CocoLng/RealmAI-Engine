"""Tests for db.migrations — schema creation + forward column reconciliation.

The headline behaviour: ``create_all()`` never adds a column to a table that
already exists, so a DB created before a model gained a field silently lacks
that column. ``ensure_schema`` closes that gap by adding missing columns on
startup, and records a ``schema_version`` for observability.
"""

from pathlib import Path

from sqlalchemy import inspect

from db.database import get_engine, init_db
from db.migrations import SCHEMA_VERSION, ensure_schema, get_schema_version

DOMAIN_TABLES = {
    "campaigns",
    "npcs",
    "locations",
    "exchanges",
    "summaries",
    "story_arcs",
    "player_characters",
    "campaign_channels",
    "guild_configs",
    "hint_usage",
}


def _engine(tmp_path: Path):
    return get_engine(f"sqlite:///{tmp_path / 'test.db'}")


def test_ensure_schema_creates_all_domain_tables(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    ensure_schema(engine)
    tables = set(inspect(engine).get_table_names())
    assert DOMAIN_TABLES <= tables
    assert "schema_version" in tables


def test_ensure_schema_stamps_version(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    assert get_schema_version(engine) == 0  # nothing recorded yet
    ensure_schema(engine)
    assert get_schema_version(engine) == SCHEMA_VERSION


def test_ensure_schema_refuses_downgrade(tmp_path: Path) -> None:
    """A DB stamped by NEWER code must not be touched by older code.

    The auto column-add only goes forward; running an old binary against
    a newer schema risks silent corruption. The stamp is now actually
    read, and a higher version aborts before any DDL.
    """
    import pytest

    engine = _engine(tmp_path)
    ensure_schema(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("UPDATE schema_version SET version = 999")

    with pytest.raises(RuntimeError, match="999"):
        ensure_schema(engine)

    # The newer stamp must remain untouched.
    assert get_schema_version(engine) == 999


def test_ensure_schema_adds_missing_nullable_column(tmp_path: Path) -> None:
    """The real bug: create_all() never adds a column to an existing table."""
    engine = _engine(tmp_path)
    # Simulate a pre-existing DB whose campaigns table predates combat_state_json.
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE campaigns ("
            " id VARCHAR PRIMARY KEY, name VARCHAR NOT NULL,"
            " created_at DATETIME NOT NULL, player_names JSON,"
            " current_location VARCHAR, interaction_count INTEGER)"
        )
        conn.exec_driver_sql(
            "INSERT INTO campaigns (id, name, created_at, interaction_count)"
            " VALUES ('c1', 'Old Camp', '2026-01-01 00:00:00', 5)"
        )

    report = ensure_schema(engine)

    cols = {c["name"] for c in inspect(engine).get_columns("campaigns")}
    assert "combat_state_json" in cols
    assert "campaigns.combat_state_json" in report.columns_added
    # The existing row survives; the new nullable column is NULL for it.
    with engine.begin() as conn:
        row = conn.exec_driver_sql(
            "SELECT id, name, interaction_count, combat_state_json FROM campaigns"
        ).one()
    assert row == ("c1", "Old Camp", 5, None)


def test_ensure_schema_adds_not_null_json_column_with_default(tmp_path: Path) -> None:
    """A NOT NULL JSON column must be addable to a populated table (needs DEFAULT)."""
    engine = _engine(tmp_path)
    # locations predates npc_roles (NOT NULL JSON dict in the model).
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE locations ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT, campaign_id VARCHAR NOT NULL,"
            " name VARCHAR NOT NULL, description VARCHAR, arrival_hook VARCHAR,"
            " connections JSON, exit_aliases JSON, npcs_present JSON,"
            " items_available JSON, item_descriptions JSON, state_flags JSON,"
            " unlocked_exits JSON, generated BOOLEAN, combat_zones JSON,"
            " combat_triggers JSON)"
        )
        conn.exec_driver_sql(
            "INSERT INTO locations (campaign_id, name) VALUES ('c1', 'Crypt')"
        )

    report = ensure_schema(engine)

    cols = {c["name"] for c in inspect(engine).get_columns("locations")}
    assert "npc_roles" in cols
    assert "locations.npc_roles" in report.columns_added
    # Existing row preserved with a usable (non-NULL) default.
    with engine.begin() as conn:
        value = conn.exec_driver_sql(
            "SELECT npc_roles FROM locations WHERE name='Crypt'"
        ).scalar_one()
    assert value == "{}"


def test_ensure_schema_is_idempotent(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    ensure_schema(engine)
    report = ensure_schema(engine)
    assert report.columns_added == []
    assert get_schema_version(engine) == SCHEMA_VERSION


def test_init_db_reconciles_existing_db(tmp_path: Path) -> None:
    """init_db is the public entry point — it must run the reconciliation too."""
    engine = _engine(tmp_path)
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE campaigns ("
            " id VARCHAR PRIMARY KEY, name VARCHAR NOT NULL,"
            " created_at DATETIME NOT NULL, player_names JSON,"
            " current_location VARCHAR, interaction_count INTEGER)"
        )
    init_db(engine)
    cols = {c["name"] for c in inspect(engine).get_columns("campaigns")}
    assert "combat_state_json" in cols


def test_ensure_schema_adds_story_arc_archetype_column(tmp_path: Path) -> None:
    """A pre-existing story_arcs table gains the ``archetype`` column.

    Guards the cross-campaign anti-repetition wiring: without the column,
    ``get_latest_archetype_for_guild`` would fail on a legacy database.
    """
    engine = _engine(tmp_path)
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE story_arcs ("
            "  campaign_id VARCHAR(36) PRIMARY KEY,"
            "  arc_json TEXT NOT NULL,"
            "  current_beat_index INTEGER"
            ")",
        )

    report = ensure_schema(engine)

    columns = {c["name"] for c in inspect(engine).get_columns("story_arcs")}
    assert "archetype" in columns
    assert "story_arcs.archetype" in report.columns_added
