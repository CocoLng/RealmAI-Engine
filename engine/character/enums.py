"""Enums for the character system."""

from enum import StrEnum


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
