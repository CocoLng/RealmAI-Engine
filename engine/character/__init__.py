"""Character system — classes, races, ability scores, levels, HP, AC.

Simplified SRD 5e rules. Pure deterministic Python (no LLM).

This package re-exports all public names from the monolith for backward compatibility.
"""

from .abilities import apply_racial_bonuses, compute_modifier, roll_ability_scores
from .classes import CLASS_HIT_DIE, CLASS_SAVING_THROWS
from .creation import create_character
from .enums import Ability, Alignment, CharacterClass, Race, Size
from .models import AbilityScores, Character
from .progression import (
    PROFICIENCY_BONUS_BY_LEVEL,
    XP_THRESHOLDS,
    add_xp,
    check_level_up,
    compute_max_hp,
    compute_proficiency_bonus,
    level_up,
)
from .races import RACIAL_ABILITY_BONUSES, RACIAL_SIZE, RACIAL_SPEED

__all__ = [
    # Enums
    "Ability",
    "Alignment",
    "CharacterClass",
    "Race",
    "Size",
    # Models
    "AbilityScores",
    "Character",
    # Race tables
    "RACIAL_ABILITY_BONUSES",
    "RACIAL_SIZE",
    "RACIAL_SPEED",
    # Class tables
    "CLASS_HIT_DIE",
    "CLASS_SAVING_THROWS",
    # Progression tables
    "XP_THRESHOLDS",
    "PROFICIENCY_BONUS_BY_LEVEL",
    # Functions
    "compute_modifier",
    "apply_racial_bonuses",
    "roll_ability_scores",
    "compute_proficiency_bonus",
    "compute_max_hp",
    "check_level_up",
    "add_xp",
    "level_up",
    "create_character",
]
