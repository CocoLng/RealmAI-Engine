"""4d6-drop-lowest stat generation with class-priority auto-assignment."""

import random
from .enums import Ability, CharacterClass

# Priority order per class (highest first)
CLASS_STAT_PRIORITY: dict[CharacterClass, list[Ability]] = {
    CharacterClass.FIGHTER:   [Ability.STR, Ability.CON, Ability.DEX, Ability.WIS, Ability.INT, Ability.CHA],
    CharacterClass.BARBARIAN: [Ability.STR, Ability.CON, Ability.DEX, Ability.WIS, Ability.CHA, Ability.INT],
    CharacterClass.WIZARD:    [Ability.INT, Ability.DEX, Ability.CON, Ability.WIS, Ability.CHA, Ability.STR],
    CharacterClass.CLERIC:    [Ability.WIS, Ability.CON, Ability.STR, Ability.DEX, Ability.CHA, Ability.INT],
    CharacterClass.ROGUE:     [Ability.DEX, Ability.CON, Ability.INT, Ability.CHA, Ability.WIS, Ability.STR],
    CharacterClass.RANGER:    [Ability.DEX, Ability.WIS, Ability.CON, Ability.STR, Ability.INT, Ability.CHA],
}

def roll_4d6_drop_lowest() -> list[int]:
    """Roll 4d6 and drop the lowest die, six times. Returns sorted descending."""
    rolls = []
    for _ in range(6):
        dice = sorted(random.randint(1, 6) for _ in range(4))
        rolls.append(sum(dice[1:]))  # drop lowest
    return sorted(rolls, reverse=True)

def auto_assign_random(char_class: CharacterClass, rolls: list[int]) -> dict[Ability, int]:
    """Assign 6 sorted-desc rolls to abilities by class priority."""
    priority = CLASS_STAT_PRIORITY[char_class]
    return dict(zip(priority, rolls, strict=True))
