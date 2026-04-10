"""Character progression functions for the character system."""

from .abilities import compute_modifier
from .classes import _HIT_DIE_MAX
from .enums import Ability, CharacterClass
from .models import Character

# Index 0 unused; index N = XP needed to reach level N.
XP_THRESHOLDS: list[int] = [
    0,       # placeholder (no level 0)
    0,       # level 1
    300,     # level 2
    900,     # level 3
    2_700,   # level 4
    6_500,   # level 5
    14_000,  # level 6
    23_000,  # level 7
    34_000,  # level 8
    48_000,  # level 9
    64_000,  # level 10
    85_000,  # level 11
    100_000, # level 12
    120_000, # level 13
    140_000, # level 14
    165_000, # level 15
    195_000, # level 16
    225_000, # level 17
    265_000, # level 18
    305_000, # level 19
    355_000, # level 20
]

PROFICIENCY_BONUS_BY_LEVEL: list[int] = [
    0,  # placeholder
    2, 2, 2, 2,       # levels 1-4
    3, 3, 3, 3,       # levels 5-8
    4, 4, 4, 4,       # levels 9-12
    5, 5, 5, 5,       # levels 13-16
    6, 6, 6, 6,       # levels 17-20
]


def compute_proficiency_bonus(level: int) -> int:
    """Proficiency bonus for a given character level (1-20)."""
    if not 1 <= level <= 20:
        raise ValueError(f"Level must be 1-20, got {level}")
    return PROFICIENCY_BONUS_BY_LEVEL[level]


def compute_max_hp(
    char_class: CharacterClass, level: int, con_modifier: int
) -> int:
    """Compute max HP for a class, level, and CON modifier.

    Level 1: max hit die + CON mod (minimum 1).
    Levels 2+: average hit die (ceil) + CON mod per level (minimum 1 each).
    """
    hit_die_max = _HIT_DIE_MAX[char_class]
    hit_die_avg = hit_die_max // 2 + 1  # e.g. d10 → 6, d8 → 5, d6 → 4

    # Level 1: max roll + CON mod
    total = max(1, hit_die_max + con_modifier)

    # Levels 2+: average + CON mod per level
    for _ in range(level - 1):
        total += max(1, hit_die_avg + con_modifier)

    return total


def check_level_up(character: Character) -> bool:
    """Check if a character has enough XP to level up."""
    if character.level >= 20:
        return False
    return character.xp >= XP_THRESHOLDS[character.level + 1]


def add_xp(character: Character, amount: int) -> Character:
    """Add XP to a character. Does NOT auto-level.

    Mutates in place and returns the character.
    """
    if amount < 0:
        raise ValueError("XP amount must be positive")
    character.xp += amount
    return character


def level_up(character: Character) -> Character:
    """Level up a character by one level. Mutates in place and returns it.

    Raises ValueError if already level 20 or not enough XP.
    Updates: level, proficiency_bonus, max_hp, hp.
    """
    if character.level >= 20:
        raise ValueError("Character is already level 20")
    if character.xp < XP_THRESHOLDS[character.level + 1]:
        raise ValueError(
            f"Not enough XP: has {character.xp}, "
            f"needs {XP_THRESHOLDS[character.level + 1]} for level {character.level + 1}"
        )

    character.level += 1
    character.proficiency_bonus = compute_proficiency_bonus(character.level)

    # HP gain for the new level
    con_mod = compute_modifier(character.ability_scores.get(Ability.CON))
    hit_die_avg = _HIT_DIE_MAX[character.char_class] // 2 + 1
    hp_gain = max(1, hit_die_avg + con_mod)
    character.max_hp += hp_gain
    character.hp += hp_gain

    return character
