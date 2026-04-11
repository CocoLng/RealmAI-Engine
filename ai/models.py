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
    public_effects: "PublicEffects" = Field(default_factory=lambda: PublicEffects())
    npc_name: str | None = None
    npc_dialogue: str | None = None


class PublicEffects(BaseModel):
    """Player-safe effects extracted from mechanics resolution.

    Only contains information a player may legitimately see (HP they took,
    items they picked up, where they moved). Hidden stats — NPC disposition,
    secrets, rolls, DCs — NEVER live here and NEVER reach the Discord embed.
    """

    hp_delta: dict[str, int] = Field(default_factory=dict)
    items_gained: list[str] = Field(default_factory=list)
    items_lost: list[str] = Field(default_factory=list)
    gold_delta: int = 0
    location_change: str | None = None
    xp_gained: int = 0
    level_up: bool = False

    def is_empty(self) -> bool:
        return (
            not self.hp_delta
            and not self.items_gained
            and not self.items_lost
            and self.gold_delta == 0
            and self.location_change is None
            and self.xp_gained == 0
            and not self.level_up
        )

    def to_footer_text(self) -> str | None:
        """Render a compact one-line footer, or None if nothing to show."""
        if self.is_empty():
            return None
        parts: list[str] = []
        for name, delta in self.hp_delta.items():
            sign = "+" if delta >= 0 else ""
            parts.append(f"\u2764 {name} {sign}{delta}")
        for item in self.items_gained:
            parts.append(f"+ {item}")
        for item in self.items_lost:
            parts.append(f"- {item}")
        if self.gold_delta:
            sign = "+" if self.gold_delta >= 0 else ""
            parts.append(f"{sign}{self.gold_delta} po")
        if self.location_change:
            parts.append(f"\u2192 {self.location_change}")
        if self.xp_gained:
            parts.append(f"+{self.xp_gained} XP")
        if self.level_up:
            parts.append("\u2b06 LEVEL UP")
        return "  \u2022  ".join(parts)


# Rebuild forward ref for MechanicsOutcome.public_effects
MechanicsOutcome.model_rebuild()


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
