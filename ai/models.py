"""Pydantic v2 models for the AI layer inputs and outputs."""

from typing import Literal

from pydantic import BaseModel, Field

from engine.validators import ActionType


class InterpretedAction(BaseModel):
    """Output of the Interpreter: structured player action parsed from free text."""

    action_type: ActionType
    actor_name: str
    target_name: str | None = None
    weapon_name: str | None = None
    spell_name: str | None = None
    item_name: str | None = None
    raw_input: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    # Exploration extras
    talk_topic: str | None = None
    search_detail: str | None = None
    improvise_description: str | None = None


class NarrativeResult(BaseModel):
    """Output of the Narrator: immersive narrative description of resolved action."""

    narrative: str
    tone: Literal["dramatic", "tense", "humorous", "somber"]


class DirectorNote(BaseModel):
    """Output of the Story Director: coherence analysis and narrative hooks."""

    coherence_issues: list[str]
    suggested_hooks: list[str]
    priority: Literal["low", "medium", "high"]


class NPCResponse(BaseModel):
    """Output of the NPC Agent: dialogue and disposition signal."""

    dialogue: str
    disposition_change: int = Field(default=0, ge=-2, le=2)
    revealed_info: list[str] = Field(default_factory=list)


class MechanicsOutcome(BaseModel):
    """Structured output of `_resolve_mechanics`.

    Carries three layers separately so the narrator can both honor the
    player's intent and stay faithful to canon facts:

    - ``summary``: short mechanical phrase, used for the Discord stats embed
      and for backward-compatible ``ActionPipelineResult.mechanics_text``.
    - ``player_intent``: how the player framed the action (raw_input plus
      any interpreter-extracted detail like ``search_detail`` or
      ``talk_topic``). May be empty for system-driven actions.
    - ``outcome_facts``: what mechanically changed in engine state
      (item moved, location changed, NPC killed). May be empty when no
      state mutation occurred (e.g. LOOK).
    """

    summary: str
    player_intent: str = ""
    outcome_facts: str = ""


class NPCSheet(BaseModel):
    """Canon backstory generated for an NPC by the NPCGenerator.

    Persisted onto the NPC entity once generated. The agent reads it
    when producing dialogue. ``secrets`` are things the NPC knows but
    won't volunteer easily; ``knowledge`` are things the NPC will share
    when asked appropriately.
    """

    personality: str
    description: str
    secrets: list[str] = Field(default_factory=list)
    knowledge: list[str] = Field(default_factory=list)
