"""Character system — classes, races, ability scores, levels, HP, AC.

Simplified SRD 5e rules. Pure deterministic Python (no LLM).

This package re-exports all public names from the monolith for backward compatibility.
"""

from .abilities import (
    STANDARD_ARRAY,
    apply_racial_bonuses,
    assign_standard_array,
    compute_modifier,
    compute_skill_modifier,
    roll_ability_scores,
)
from .classes import (
    CLASS_FEATURES,
    CLASS_HIT_DIE,
    CLASS_SAVING_THROWS,
    CLASS_SKILL_CHOICES,
    ClassSkillConfig,
)
from .creation import create_character
from .enums import Ability, CharacterClass, Race, Size, Skill, SKILL_ABILITY
from .features import (
    Feature,
    FeatureSource,
    MechanicalEffect,
    get_damage_resistances,
    get_feature_effects,
    has_darkvision,
    has_feature,
)
from .models import AbilityScores, Character
from .presets import CLASS_STAT_PRESETS, get_class_preset
from .progression import (
    PROFICIENCY_BONUS_BY_LEVEL,
    XP_THRESHOLDS,
    add_xp,
    check_level_up,
    compute_max_hp,
    compute_proficiency_bonus,
    level_up,
)
from .races import RACIAL_ABILITY_BONUSES, RACIAL_FEATURES, RACIAL_SIZE, RACIAL_SPEED
from .random_stats import (
    CLASS_STAT_PRIORITY,
    auto_assign_random,
    roll_4d6_drop_lowest,
)

__all__ = [
    # Enums
    "Ability",
    "CharacterClass",
    "Race",
    "Size",
    "Skill",
    # Enum tables
    "SKILL_ABILITY",
    # Models
    "AbilityScores",
    "Character",
    "Feature",
    "FeatureSource",
    "MechanicalEffect",
    "ClassSkillConfig",
    # Race tables
    "RACIAL_ABILITY_BONUSES",
    "RACIAL_FEATURES",
    "RACIAL_SIZE",
    "RACIAL_SPEED",
    # Class tables
    "CLASS_HIT_DIE",
    "CLASS_SAVING_THROWS",
    "CLASS_FEATURES",
    "CLASS_SKILL_CHOICES",
    # Progression tables
    "XP_THRESHOLDS",
    "PROFICIENCY_BONUS_BY_LEVEL",
    # Standard Array
    "STANDARD_ARRAY",
    "assign_standard_array",
    # Functions
    "compute_modifier",
    "apply_racial_bonuses",
    "roll_ability_scores",
    "compute_skill_modifier",
    "compute_proficiency_bonus",
    "compute_max_hp",
    "check_level_up",
    "add_xp",
    "level_up",
    "create_character",
    # Feature helpers
    "has_feature",
    "get_feature_effects",
    "has_darkvision",
    "get_damage_resistances",
    # Stat presets and random gen
    "CLASS_STAT_PRESETS",
    "get_class_preset",
    "CLASS_STAT_PRIORITY",
    "auto_assign_random",
    "roll_4d6_drop_lowest",
]
