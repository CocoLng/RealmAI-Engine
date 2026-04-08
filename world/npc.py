"""NPC domain model.

Represents non-player characters in the game world.
"""

from enum import StrEnum

from pydantic import BaseModel, Field

from engine.character import AbilityScores, CharacterClass, Race


class NPCDisposition(StrEnum):
    """How an NPC feels toward the player(s)."""

    HOSTILE = "hostile"
    UNFRIENDLY = "unfriendly"
    NEUTRAL = "neutral"
    FRIENDLY = "friendly"
    ALLIED = "allied"


class DialogueExchange(BaseModel):
    """One round of dialogue between a player and an NPC.

    Stored on ``NPC.dialogue_history`` so subsequent conversations can
    avoid repeating reveals and build narrative continuity.
    """

    player_said: str
    npc_said: str
    revealed: list[str] = Field(default_factory=list)


class NPC(BaseModel):
    """A non-player character in the game world."""

    name: str
    race: Race
    char_class: CharacterClass | None = None
    level: int = Field(default=1, ge=1, le=20)
    ability_scores: AbilityScores
    hp: int = Field(ge=0)
    max_hp: int = Field(ge=1)
    ac: int = Field(ge=0)
    disposition: NPCDisposition = NPCDisposition.NEUTRAL
    is_alive: bool = True
    description: str = ""
    personality: str = ""
    location_name: str | None = None
    aliases: list[str] = Field(default_factory=list)
    secrets: list[str] = Field(default_factory=list)
    knowledge: list[str] = Field(default_factory=list)
    dialogue_history: list[DialogueExchange] = Field(default_factory=list)

    def kill(self) -> None:
        """Mark this NPC as dead. Idempotent."""
        self.hp = 0
        self.is_alive = False
