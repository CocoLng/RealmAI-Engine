"""Feature system for racial traits and class features."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from .models import Character


class FeatureSource(StrEnum):
    """Where a feature originates."""

    RACE = "race"
    CLASS = "class"
    BACKGROUND = "background"  # future
    FEAT = "feat"  # future


class MechanicalEffect(BaseModel):
    """A single mechanical effect granted by a feature."""

    effect_type: str  # "darkvision", "damage_resistance", etc.
    value: int | str | list[str]


class Feature(BaseModel):
    """A racial trait, class feature, or feat with mechanical effects."""

    name: str
    source: FeatureSource
    source_name: str  # "Elf", "Barbarian"
    description: str
    effects: list[MechanicalEffect]
    level_requirement: int = 1


def has_feature(character: Character, name: str) -> bool:
    """Check whether a character has a feature by name."""
    return any(f.name == name for f in character.features)


def get_feature_effects(
    character: Character, effect_type: str
) -> list[MechanicalEffect]:
    """Return all MechanicalEffects of a given type from the character's features."""
    return [
        eff
        for feat in character.features
        for eff in feat.effects
        if eff.effect_type == effect_type
    ]


def has_darkvision(character: Character) -> int | None:
    """Return darkvision range in feet, or None if no darkvision."""
    effects = get_feature_effects(character, "darkvision")
    if not effects:
        return None
    # Return the maximum range if multiple sources
    ranges: list[int] = []
    for eff in effects:
        v = eff.value
        if isinstance(v, int):
            ranges.append(v)
        elif isinstance(v, str):
            ranges.append(int(v))
    return max(ranges) if ranges else None


def get_damage_resistances(character: Character) -> list[str]:
    """Return a list of damage types the character has resistance to."""
    effects = get_feature_effects(character, "damage_resistance")
    resistances: list[str] = []
    for eff in effects:
        if isinstance(eff.value, list):
            resistances.extend(eff.value)
        else:
            resistances.append(str(eff.value))
    return resistances
