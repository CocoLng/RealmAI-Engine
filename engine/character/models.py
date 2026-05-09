"""Pydantic models for the character system."""

from pydantic import BaseModel, Field

from .enums import Ability, CharacterClass, Race, Size, Skill
from .features import Feature


class AbilityScores(BaseModel):
    """The six core ability scores for a character."""

    STR: int = Field(ge=1, le=30)
    DEX: int = Field(ge=1, le=30)
    CON: int = Field(ge=1, le=30)
    INT: int = Field(ge=1, le=30)
    WIS: int = Field(ge=1, le=30)
    CHA: int = Field(ge=1, le=30)

    def get(self, ability: Ability) -> int:
        """Get a score by Ability enum."""
        return getattr(self, ability.name)


class Character(BaseModel):
    """A player or NPC character with SRD 5e stats."""

    name: str = Field(min_length=1, max_length=64)
    concept: str = Field(default="", max_length=200)
    race: Race
    char_class: CharacterClass
    level: int = Field(default=1, ge=1, le=20)
    xp: int = Field(default=0, ge=0)

    ability_scores: AbilityScores
    hp: int = Field(ge=0)
    max_hp: int = Field(ge=1)
    ac: int = Field(ge=0)
    speed: int = Field(ge=0)
    proficiency_bonus: int = Field(ge=2)
    saving_throw_proficiencies: tuple[Ability, Ability]
    hit_die: str
    size: Size
    features: list[Feature] = Field(default_factory=list)
    skill_proficiencies: list[Skill] = Field(default_factory=list)
    expertise_skills: list[Skill] = Field(default_factory=list)
    """Skills with the Expertise feature applied (double proficiency bonus).

    Populated at character creation when the player is a Rogue (level 1)
    or Bard (level 3) and picks two skills. Empty for everyone else.
    """
