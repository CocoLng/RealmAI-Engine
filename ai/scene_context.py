"""SceneContext — what the acting character currently perceives.

Passed to the Interpreter so it can resolve references like 'the priest' or
'that door' using only in-scene information, and to validators/resolvers to
decide what actions are available.

This module intentionally takes primitive arguments instead of a GameSession
to avoid a circular dependency between ai/ and bot/.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from engine.combat import CombatSide, CombatState
from world.location import Location
from world.npc import NPC


class SceneContext(BaseModel):
    """Snapshot of everything the acting character can perceive right now."""

    location_name: str
    location_description: str
    visible_npcs: list[str] = Field(default_factory=list)
    visible_exits: list[str] = Field(default_factory=list)
    visible_objects: list[str] = Field(default_factory=list)
    in_combat: bool = False
    combat_summary: str | None = None
    enemies_visible: list[str] = Field(default_factory=list)


def build_scene_context(
    location: Location | None,
    npcs: dict[str, NPC],
    combat_state: CombatState | None = None,
) -> SceneContext:
    """Assemble a SceneContext from primitive world objects.

    Args:
        location: The current location, or None if no location is set.
        npcs: The full NPC registry. Will be filtered to only those whose
            ``location_name`` matches the current location.
        combat_state: Active combat, if any.

    Returns:
        A SceneContext ready to feed the Interpreter and EntityResolver.
    """
    if location is None:
        return SceneContext(location_name="", location_description="")

    visible_npcs = [
        npc.name
        for npc in npcs.values()
        if npc.is_alive
        and npc.location_name is not None
        and npc.location_name == location.name
    ]

    ctx = SceneContext(
        location_name=location.name,
        location_description=location.description,
        visible_npcs=visible_npcs,
        visible_exits=list(location.connections),
        visible_objects=list(location.items_available),
    )

    if combat_state is not None:
        ctx.in_combat = True
        ctx.enemies_visible = [
            c.name
            for c in combat_state.combatants
            if c.side == CombatSide.ENEMY and c.is_alive
        ]
        ctx.combat_summary = (
            f"Round {combat_state.round_number}, "
            f"current turn: {combat_state.combatants[combat_state.current_turn_index].name}"
        )

    return ctx
