"""Forward schema reconciliation for the SQLite database.

``Base.metadata.create_all()`` creates *missing tables* but never alters an
existing one, so a DB created before a model gained a column silently lacks
that column. This module closes that gap: :func:`ensure_schema` creates all
tables, then adds any model-defined column an existing table is missing
(``ALTER TABLE ... ADD COLUMN``), and stamps a ``schema_version`` row.

Intentionally lightweight — no Alembic. It handles the common forward-only
case (a new column) automatically and safely. Structural changes (renames,
type changes, data backfills) still warrant an explicit, reviewed migration;
``SCHEMA_VERSION`` is the hook for sequencing those when they arrive.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Column, inspect
from sqlalchemy.engine import Engine

from db import models as _models  # noqa: F401  (registers tables on Base.metadata)
from db.database import Base

logger = logging.getLogger(__name__)

# Bump only when a change needs an explicit migration the auto column-add below
# cannot derive (rename, type change, backfill).
SCHEMA_VERSION = 1

_VERSION_TABLE = "schema_version"


@dataclass
class SchemaReport:
    """Outcome of an :func:`ensure_schema` run."""

    version: int
    columns_added: list[str] = field(default_factory=list)


def ensure_schema(engine: Engine) -> SchemaReport:
    """Create all tables, add any missing columns, and stamp the schema version."""
    Base.metadata.create_all(engine)
    added = _add_missing_columns(engine)
    _stamp_version(engine, SCHEMA_VERSION)
    if added:
        logger.info(
            "Schema reconciliation added %d column(s): %s", len(added), ", ".join(added)
        )
    return SchemaReport(version=SCHEMA_VERSION, columns_added=added)


def get_schema_version(engine: Engine) -> int:
    """Return the recorded schema version, or 0 if never stamped."""
    if _VERSION_TABLE not in inspect(engine).get_table_names():
        return 0
    with engine.begin() as conn:
        row = conn.exec_driver_sql(f"SELECT version FROM {_VERSION_TABLE} LIMIT 1").fetchone()
    return int(row[0]) if row else 0


def _add_missing_columns(engine: Engine) -> list[str]:
    """Add every model column absent from its (already-existing) table."""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    plan: list[tuple[str, Column[Any]]] = []
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue  # create_all just built it complete
        present = {col["name"] for col in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name not in present:
                plan.append((table.name, column))

    if not plan:
        return []

    added: list[str] = []
    with engine.begin() as conn:
        for table_name, column in plan:
            clause = _add_column_clause(column, engine)
            if clause is None:
                logger.warning(
                    "Cannot safely add column %s.%s (NOT NULL, no derivable "
                    "default); skipping — add an explicit migration.",
                    table_name,
                    column.name,
                )
                continue
            conn.exec_driver_sql(f'ALTER TABLE "{table_name}" ADD COLUMN {clause}')
            added.append(f"{table_name}.{column.name}")
    return added


def _add_column_clause(column: Column[Any], engine: Engine) -> str | None:
    """Render the ``ADD COLUMN`` body, or None if it cannot be added safely."""
    type_sql = column.type.compile(dialect=engine.dialect)
    if column.nullable:
        return f'"{column.name}" {type_sql}'
    default = _default_literal(column)
    if default is None:
        return None  # NOT NULL with no usable default — unsafe on a populated table
    return f'"{column.name}" {type_sql} NOT NULL DEFAULT {default}'


def _default_literal(column: Column[Any]) -> str | None:
    """A SQL literal for the column's Python default, or None if not derivable."""
    default = column.default
    if default is None:
        return None
    if getattr(default, "is_scalar", False):
        return _scalar_to_sql(getattr(default, "arg", None))
    if getattr(default, "is_callable", False):
        sample = _invoke_callable_default(getattr(default, "arg", None))
        return _scalar_to_sql(sample)
    return None


def _invoke_callable_default(arg: Any) -> Any:
    """Call a default factory (e.g. ``list``/``dict``), tolerating SQLAlchemy's
    context-callable wrapping."""
    if not callable(arg):
        return None
    for call in (lambda: arg(), lambda: arg({}), lambda: arg(None)):
        try:
            return call()
        except Exception:  # noqa: BLE001 — probing call signatures
            continue
    return None


def _scalar_to_sql(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return _sql_quote(value)
    if isinstance(value, (list, dict)):
        return _sql_quote(json.dumps(value))
    return None


def _sql_quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _stamp_version(engine: Engine, version: int) -> None:
    with engine.begin() as conn:
        conn.exec_driver_sql(
            f"CREATE TABLE IF NOT EXISTS {_VERSION_TABLE} (version INTEGER NOT NULL)"
        )
        existing = conn.exec_driver_sql(
            f"SELECT version FROM {_VERSION_TABLE} LIMIT 1"
        ).fetchone()
        if existing is None:
            conn.exec_driver_sql(f"INSERT INTO {_VERSION_TABLE} (version) VALUES ({version})")
        else:
            conn.exec_driver_sql(f"UPDATE {_VERSION_TABLE} SET version = {version}")
