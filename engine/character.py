"""Character system — classes, races, ability scores, levels, HP, AC.

Simplified SRD 5e rules. Pure deterministic Python (no LLM).
"""

from enum import StrEnum

from pydantic import BaseModel, Field

from engine.dice import roll


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Ability(StrEnum):
    """The six core ability scores."""

    STR = "STR"
    DEX = "DEX"
    CON = "CON"
    INT = "INT"
    WIS = "WIS"
    CHA = "CHA"


class Race(StrEnum):
    """Playable races (SRD 5e subset)."""

    HUMAN = "Human"
    ELF = "Elf"
    DWARF = "Dwarf"
    HALFLING = "Halfling"
    HALF_ORC = "Half-Orc"
    GNOME = "Gnome"
    TIEFLING = "Tiefling"


class CharacterClass(StrEnum):
    """Playable classes (SRD 5e subset)."""

    FIGHTER = "Fighter"
    WIZARD = "Wizard"
    ROGUE = "Rogue"
    CLERIC = "Cleric"
    RANGER = "Ranger"
    BARBARIAN = "Barbarian"


class Size(StrEnum):
    """Creature size categories."""

    SMALL = "Small"
    MEDIUM = "Medium"


class Alignment(StrEnum):
    """The nine alignments."""

    LAWFUL_GOOD = "Lawful Good"
    NEUTRAL_GOOD = "Neutral Good"
    CHAOTIC_GOOD = "Chaotic Good"
    LAWFUL_NEUTRAL = "Lawful Neutral"
    TRUE_NEUTRAL = "True Neutral"
    CHAOTIC_NEUTRAL = "Chaotic Neutral"
    LAWFUL_EVIL = "Lawful Evil"
    NEUTRAL_EVIL = "Neutral Evil"
    CHAOTIC_EVIL = "Chaotic Evil"


# ---------------------------------------------------------------------------
# Reference tables (SRD 5e simplified)
# ---------------------------------------------------------------------------


RACIAL_ABILITY_BONUSES: dict[Race, dict[Ability, int]] = {
    Race.HUMAN: {a: 1 for a in Ability},
    Race.ELF: {Ability.DEX: 2},
    Race.DWARF: {Ability.CON: 2},
    Race.HALFLING: {Ability.DEX: 2},
    Race.HALF_ORC: {Ability.STR: 2, Ability.CON: 1},
    Race.GNOME: {Ability.INT: 2},
    Race.TIEFLING: {Ability.CHA: 2, Ability.INT: 1},
}

RACIAL_SIZE: dict[Race, Size] = {
    Race.HUMAN: Size.MEDIUM,
    Race.ELF: Size.MEDIUM,
    Race.DWARF: Size.MEDIUM,
    Race.HALFLING: Size.SMALL,
    Race.HALF_ORC: Size.MEDIUM,
    Race.GNOME: Size.SMALL,
    Race.TIEFLING: Size.MEDIUM,
}

RACIAL_SPEED: dict[Race, int] = {
    Race.HUMAN: 30,
    Race.ELF: 30,
    Race.DWARF: 25,
    Race.HALFLING: 25,
    Race.HALF_ORC: 30,
    Race.GNOME: 25,
    Race.TIEFLING: 30,
}

CLASS_HIT_DIE: dict[CharacterClass, str] = {
    CharacterClass.BARBARIAN: "1d12",
    CharacterClass.FIGHTER: "1d10",
    CharacterClass.RANGER: "1d10",
    CharacterClass.CLERIC: "1d8",
    CharacterClass.ROGUE: "1d8",
    CharacterClass.WIZARD: "1d6",
}

CLASS_SAVING_THROWS: dict[CharacterClass, tuple[Ability, Ability]] = {
    CharacterClass.FIGHTER: (Ability.STR, Ability.CON),
    CharacterClass.WIZARD: (Ability.INT, Ability.WIS),
    CharacterClass.ROGUE: (Ability.DEX, Ability.INT),
    CharacterClass.CLERIC: (Ability.WIS, Ability.CHA),
    CharacterClass.RANGER: (Ability.STR, Ability.DEX),
    CharacterClass.BARBARIAN: (Ability.STR, Ability.CON),
}

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

# Max face value of each class hit die (parsed from CLASS_HIT_DIE).
_HIT_DIE_MAX: dict[CharacterClass, int] = {
    cls: int(die.split("d")[1]) for cls, die in CLASS_HIT_DIE.items()
}


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


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
    race: Race
    char_class: CharacterClass
    level: int = Field(default=1, ge=1, le=20)
    xp: int = Field(default=0, ge=0)
    alignment: Alignment = Alignment.TRUE_NEUTRAL

    ability_scores: AbilityScores
    hp: int = Field(ge=0)
    max_hp: int = Field(ge=1)
    ac: int = Field(ge=0)
    speed: int = Field(ge=0)
    proficiency_bonus: int = Field(ge=2)
    saving_throw_proficiencies: tuple[Ability, Ability]
    hit_die: str
    size: Size


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------


def compute_modifier(score: int) -> int:
    """Compute ability modifier from a score. SRD formula: (score - 10) // 2."""
    return (score - 10) // 2


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


# ---------------------------------------------------------------------------
# Racial bonuses
# ---------------------------------------------------------------------------


def apply_racial_bonuses(scores: AbilityScores, race: Race) -> AbilityScores:
    """Apply racial ability score bonuses. Returns a new AbilityScores."""
    bonuses = RACIAL_ABILITY_BONUSES[race]
    data = scores.model_dump()
    for ability, bonus in bonuses.items():
        data[ability.name] += bonus
    return AbilityScores(**data)


# ---------------------------------------------------------------------------
# Ability score generation
# ---------------------------------------------------------------------------


def roll_ability_scores() -> AbilityScores:
    """Roll ability scores using the 4d6-drop-lowest method.

    Rolls 4d6 six times, drops the lowest die each time, sums the top 3.
    Assigns scores to STR, DEX, CON, INT, WIS, CHA in order.
    """
    values: list[int] = []
    for _ in range(6):
        result = roll("4d6")
        sorted_rolls = sorted(result.rolls)
        # Drop lowest, sum top 3
        values.append(sum(sorted_rolls[1:]))

    return AbilityScores(
        STR=values[0],
        DEX=values[1],
        CON=values[2],
        INT=values[3],
        WIS=values[4],
        CHA=values[5],
    )


# ---------------------------------------------------------------------------
# Character creation and progression
# ---------------------------------------------------------------------------


def create_character(
    name: str,
    race: Race,
    char_class: CharacterClass,
    ability_scores: AbilityScores,
    alignment: Alignment = Alignment.TRUE_NEUTRAL,
) -> Character:
    """Create a new level-1 character with computed derived stats.

    The provided ability_scores should already include racial bonuses.
    """
    con_mod = compute_modifier(ability_scores.get(Ability.CON))
    dex_mod = compute_modifier(ability_scores.get(Ability.DEX))
    max_hp = compute_max_hp(char_class, 1, con_mod)

    return Character(
        name=name,
        race=race,
        char_class=char_class,
        level=1,
        xp=0,
        alignment=alignment,
        ability_scores=ability_scores,
        hp=max_hp,
        max_hp=max_hp,
        ac=10 + dex_mod,
        speed=RACIAL_SPEED[race],
        proficiency_bonus=compute_proficiency_bonus(1),
        saving_throw_proficiencies=CLASS_SAVING_THROWS[char_class],
        hit_die=CLASS_HIT_DIE[char_class],
        size=RACIAL_SIZE[race],
    )


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
