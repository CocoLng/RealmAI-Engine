"""Class reference tables for the character system."""

from .enums import Ability, CharacterClass

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

# Max face value of each class hit die (parsed from CLASS_HIT_DIE).
_HIT_DIE_MAX: dict[CharacterClass, int] = {
    cls: int(die.split("d")[1]) for cls, die in CLASS_HIT_DIE.items()
}
