"""Combat trigger definition — mechanism/item that bootstraps an ambush.

A ``CombatTriggerDef`` is attached to a :class:`Location` via
``Location.combat_triggers`` and keyed by the item or mechanism name the
interpreter resolves for an ``INTERACT`` action. When a player interacts
with the keyed entity, the combat entry module (task 20) consumes the
trigger, spawns the NPCs listed in ``spawn_npcs``, and bootstraps an
ambush combat with surprise applied to the party.

Pure data — no runtime logic lives here.
"""

from pydantic import BaseModel, Field


class CombatTriggerDef(BaseModel):
    """Mechanism/item in a location that triggers combat when interacted with.

    Stored on :attr:`Location.combat_triggers` keyed by the item or
    mechanism name the interpreter resolves for an ``INTERACT`` action.
    When a player interacts with the keyed entity, :mod:`bot.combat_entry`
    spawns ``spawn_npcs`` and bootstraps an ambush combat.
    """

    item_name: str = Field(min_length=1)
    """Canonical item/mechanism name matching a ``Location.items_available``
    entry or a mechanism mentioned in the location description."""

    spawn_npcs: list[str] = Field(default_factory=list)
    """NPC names that appear in the scene when the trigger fires. The
    runtime will hydrate them on the fly via the scene hydration layer."""

    reveal_narration: str = ""
    """One-sentence cue describing the moment the ambush is revealed."""

    consumed: bool = False
    """Idempotent flag flipped to ``True`` once the trigger has fired.
    Prevents the same mechanism from spawning the ambush twice."""
