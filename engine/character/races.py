"""Racial reference tables for the character system."""

from .enums import Ability, Race, Size

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
