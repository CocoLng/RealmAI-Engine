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
    confidence: float = 1.0


class NarrativeResult(BaseModel):
    """Output of the Narrator: immersive narrative description of resolved action."""

    narrative: str
    tone: str


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
