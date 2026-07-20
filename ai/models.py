"""Pydantic v2 models for the AI layer inputs and outputs.

The cross-layer I/O contracts (:class:`InterpretedAction`,
:class:`MechanicsOutcome`, :class:`PublicEffects`, :class:`TacticalDecision`)
live in :mod:`engine.contracts` — the engine must never import from ``ai/``.
They are re-exported here so existing ``from ai.models import ...`` call sites
in ``ai/`` and ``bot/`` keep working unchanged.
"""

from typing import Literal

from pydantic import BaseModel, Field

from engine.contracts import (
    InterpretedAction,
    MechanicsOutcome,
    PublicEffects,
    TacticalDecision,
)

__all__ = [
    "InterpretedAction",
    "MechanicsOutcome",
    "PublicEffects",
    "TacticalDecision",
    "NarrativeResult",
    "DirectorNote",
    "NPCResponse",
    "NPCSheet",
    "Tone",
]

Tone = Literal["dramatic", "tense", "humorous", "somber"]
"""Canonical narrative tones — drive the Discord embed color."""


class NarrativeResult(BaseModel):
    """Output of the Narrator: immersive narrative description of resolved action.

    The ``narrative`` and ``tone`` fields drive the Discord embed shown to the
    player. The remaining fields are *meta-telemetry* for the Story Director's
    drift detector — they are NEVER displayed to the player.
    """

    narrative: str
    tone: Tone
    scene_goal_touched: bool = False
    beat_advanced: bool = False
    npcs_mentioned: list[str] = Field(default_factory=list)
    locked_facts_used: list[str] = Field(default_factory=list)


class DirectorNote(BaseModel):
    """Output of the Story Director: coherence analysis and explicit narrative direction.

    The legacy fields (``coherence_issues``, ``suggested_hooks``, ``priority``) feed
    the semantic memory layer. The newer "direction" fields feed the Narrator's
    prompt as an explicit ``[STORY DIRECTION]`` block on the next turn — they
    tell the narrator what mood to evoke, what to avoid re-revealing, and which
    NPCs to weave back in.

    Note: ``current_beat_atmosphere`` is descriptive (mood/tone), not prescriptive
    (plot moves). The Beat Progression Engine decides when to advance beats.
    """

    coherence_issues: list[str]
    suggested_hooks: list[str]
    priority: Literal["low", "medium", "high"]
    current_objective: str = ""
    current_beat_atmosphere: str = ""
    forbidden_topics: list[str] = Field(default_factory=list)
    required_mentions: list[str] = Field(default_factory=list)


class NPCResponse(BaseModel):
    """Output of the NPC Agent: dialogue and disposition signal."""

    dialogue: str
    disposition_change: int = Field(default=0, ge=-2, le=2)
    revealed_info: list[str] = Field(default_factory=list)


class NPCSheet(BaseModel):
    """Canon backstory generated for an NPC by the NPCGenerator.

    Persisted onto the NPC entity once generated. The agent reads it
    when producing dialogue. ``secrets`` are things the NPC knows but
    won't volunteer easily; ``knowledge`` are things the NPC will share
    when asked appropriately.
    """

    personality: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    secrets: list[str] = Field(default_factory=list, min_length=1)
    knowledge: list[str] = Field(default_factory=list, min_length=1)
