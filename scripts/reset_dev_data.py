"""Reset development data — wipes all campaigns, characters, and game instances
while preserving guild configuration (language, category names).

Usage:
    uv run python scripts/reset_dev_data.py
"""

import sys
from pathlib import Path
from typing import Any, cast

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from sqlalchemy.engine import CursorResult

from db.database import get_engine, get_session_factory


def reset_dev_data() -> None:
    """Delete all game data while preserving guild_configs."""
    engine = get_engine()
    SessionFactory = get_session_factory(engine)

    with SessionFactory() as session:
        # Deleting campaigns cascades to all child tables:
        # npcs, locations, exchanges, summaries,
        # player_characters, campaign_channels, story_arcs
        # Session.execute is typed Result[Any], but DML statements always
        # return a CursorResult (the only Result carrying rowcount).
        result = cast(CursorResult[Any], session.execute(text("DELETE FROM campaigns")))
        deleted = result.rowcount
        session.commit()

    print(f"✓ Supprimé {deleted} campagne(s) et toutes les données associées.")
    print("✓ Configurations des serveurs (guild_configs) conservées.")


if __name__ == "__main__":
    reset_dev_data()
