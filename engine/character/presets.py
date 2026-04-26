"""Class-optimized stat presets using the Standard Array (15/14/13/12/10/8).

Each preset reorders the array based on the class's primary, secondary,
and tertiary stat priorities. Used by the 'Optimisé pour [Classe]'
button in the character setup flow.
"""

from .enums import Ability, CharacterClass

CLASS_STAT_PRESETS: dict[CharacterClass, dict[Ability, int]] = {
    CharacterClass.FIGHTER:   {Ability.STR: 15, Ability.CON: 14, Ability.DEX: 13, Ability.WIS: 12, Ability.INT: 10, Ability.CHA: 8},
    CharacterClass.BARBARIAN: {Ability.STR: 15, Ability.CON: 14, Ability.DEX: 13, Ability.WIS: 12, Ability.CHA: 10, Ability.INT: 8},
    CharacterClass.WIZARD:    {Ability.INT: 15, Ability.DEX: 14, Ability.CON: 13, Ability.WIS: 12, Ability.CHA: 10, Ability.STR: 8},
    CharacterClass.CLERIC:    {Ability.WIS: 15, Ability.CON: 14, Ability.STR: 13, Ability.DEX: 12, Ability.CHA: 10, Ability.INT: 8},
    CharacterClass.ROGUE:     {Ability.DEX: 15, Ability.CON: 14, Ability.INT: 13, Ability.CHA: 12, Ability.WIS: 10, Ability.STR: 8},
    CharacterClass.RANGER:    {Ability.DEX: 15, Ability.WIS: 14, Ability.CON: 13, Ability.STR: 12, Ability.INT: 10, Ability.CHA: 8},
}
# Vérifier exhaustivité : 6 classes (FIGHTER, BARBARIAN, WIZARD, CLERIC, ROGUE, RANGER) — match engine/character/enums.py:29

def get_class_preset(char_class: CharacterClass) -> dict[Ability, int]:
    """Return the optimized Standard Array assignment for a given class."""
    return dict(CLASS_STAT_PRESETS[char_class])
