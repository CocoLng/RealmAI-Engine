"""Combat trigger domain model.

A ``CombatTrigger`` describes **why** a combat encounter is starting — who
the aggressor is, which NPCs should join as enemies, and which side (if any)
enjoys surprise on the first round. It is produced by the combat entry
detector in :mod:`bot.combat_entry` and consumed by :func:`engine.combat.start_combat`
to drive the 5e SRD surprise rules.

This module lives in ``engine/`` (not ``bot/``) because the engine's
``start_combat`` needs to import it; engine code may never import ``bot``
or ``ai`` (LLM-free rule). ``bot/combat_entry.py`` re-exports these symbols
for cog-side readability.

Pure deterministic Python — no LLM calls.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class CombatTriggerKind(StrEnum):
    """The reason a combat encounter begins.

    - ``PLAYER_ATTACK``: the player explicitly declared an Attack action.
    - ``LETHAL_INTENT``: the interpreter flagged an Improvise action as a
      lethal attempt (e.g. "je sors mon épée contre le marchand").
    - ``AMBUSH``: the player interacted with a scripted trap / trigger.
    - ``PROVOCATION``: a Talk action pushed an NPC past its aggression
      threshold (task 81).
    - ``SCRIPTED_BEAT``: a combat beat fired automatically from the story
      progression (campaign launch or beat advance).
    """

    PLAYER_ATTACK = "player_attack"
    LETHAL_INTENT = "lethal_intent"
    AMBUSH = "ambush"
    PROVOCATION = "provocation"
    SCRIPTED_BEAT = "scripted_beat"


class InitiativeSide(StrEnum):
    """Which side, if any, has initiative-by-surprise on the first round.

    - ``PLAYERS``: the player side catches the NPCs off-guard. PCs act
      first; each named enemy starts with the SURPRISED condition.
    - ``NPCS``: the NPCs ambush the party. Each PC starts SURPRISED; the
      ambusher NPCs act first in initiative order.
    - ``BOTH_READY``: a recognised face-off — no surprise, roll standard
      initiative for everyone.
    """

    PLAYERS = "players"
    NPCS = "npcs"
    BOTH_READY = "both_ready"


class CombatTrigger(BaseModel):
    """Declarative payload describing a combat entry event.

    Instances are built by :func:`bot.combat_entry.detect_combat_trigger`
    (or by scripted-beat entry points) and passed to
    :func:`engine.combat.start_combat` which handles the initiative roll,
    ordering, and SURPRISED condition application.
    """

    kind: CombatTriggerKind
    aggressor_name: str = Field(min_length=1)
    """Name of the PC or NPC whose action triggered the combat."""

    enemy_names: list[str] = Field(min_length=1)
    """NPC names that should be added as enemies when the combat state is built."""

    surprise_side: InitiativeSide
    """Which side enjoys surprise — see :class:`InitiativeSide` for the three cases."""

    narrative_hint: str = ""
    """Short free-form text the narrator can use to open the combat description.

    Optional — may be empty when the trigger carries no narrative context.
    """


__all__ = [
    "CombatTrigger",
    "CombatTriggerKind",
    "InitiativeSide",
]
