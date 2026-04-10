"""Racial reference tables for the character system."""

from .enums import Ability, Race, Size
from .features import Feature, FeatureSource, MechanicalEffect

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

# ── Racial features catalog ──────────────────────────────────────────────

_RS = FeatureSource.RACE  # shorthand

RACIAL_FEATURES: dict[Race, list[Feature]] = {
    Race.HUMAN: [],  # +1 to all handled by ability bonuses
    Race.ELF: [
        Feature(
            name="Darkvision",
            source=_RS,
            source_name="Elf",
            description="You can see in dim light within 60 feet as if bright light.",
            effects=[MechanicalEffect(effect_type="darkvision", value=60)],
        ),
        Feature(
            name="Keen Senses",
            source=_RS,
            source_name="Elf",
            description="You have proficiency in the Perception skill.",
            effects=[
                MechanicalEffect(effect_type="skill_proficiency", value="Perception")
            ],
        ),
        Feature(
            name="Fey Ancestry",
            source=_RS,
            source_name="Elf",
            description="You have advantage on saving throws against being charmed.",
            effects=[
                MechanicalEffect(effect_type="save_advantage", value="charmed")
            ],
        ),
    ],
    Race.DWARF: [
        Feature(
            name="Darkvision",
            source=_RS,
            source_name="Dwarf",
            description="You can see in dim light within 60 feet as if bright light.",
            effects=[MechanicalEffect(effect_type="darkvision", value=60)],
        ),
        Feature(
            name="Dwarven Resilience",
            source=_RS,
            source_name="Dwarf",
            description="Advantage on saves vs poison; resistance to poison damage.",
            effects=[
                MechanicalEffect(effect_type="save_advantage", value="poison"),
                MechanicalEffect(effect_type="damage_resistance", value="poison"),
            ],
        ),
        Feature(
            name="Stonecunning",
            source=_RS,
            source_name="Dwarf",
            description="Double proficiency bonus on History checks related to stonework.",
            effects=[
                MechanicalEffect(effect_type="expertise_conditional", value="stonework")
            ],
        ),
    ],
    Race.HALFLING: [
        Feature(
            name="Lucky",
            source=_RS,
            source_name="Halfling",
            description="Reroll natural 1 on attack, ability check, or saving throw.",
            effects=[MechanicalEffect(effect_type="reroll_nat1", value=1)],
        ),
        Feature(
            name="Brave",
            source=_RS,
            source_name="Halfling",
            description="Advantage on saving throws against being frightened.",
            effects=[
                MechanicalEffect(effect_type="save_advantage", value="frightened")
            ],
        ),
        Feature(
            name="Halfling Nimbleness",
            source=_RS,
            source_name="Halfling",
            description="You can move through the space of any creature one size larger.",
            effects=[
                MechanicalEffect(
                    effect_type="movement_through_larger", value=1
                )
            ],
        ),
    ],
    Race.HALF_ORC: [
        Feature(
            name="Darkvision",
            source=_RS,
            source_name="Half-Orc",
            description="You can see in dim light within 60 feet as if bright light.",
            effects=[MechanicalEffect(effect_type="darkvision", value=60)],
        ),
        Feature(
            name="Relentless Endurance",
            source=_RS,
            source_name="Half-Orc",
            description="Drop to 1 HP instead of 0 once per long rest.",
            effects=[
                MechanicalEffect(effect_type="relentless_endurance", value=1)
            ],
        ),
        Feature(
            name="Savage Attacks",
            source=_RS,
            source_name="Half-Orc",
            description="Roll one extra damage die on melee critical hits.",
            effects=[
                MechanicalEffect(effect_type="savage_attacks", value=1)
            ],
        ),
    ],
    Race.GNOME: [
        Feature(
            name="Darkvision",
            source=_RS,
            source_name="Gnome",
            description="You can see in dim light within 60 feet as if bright light.",
            effects=[MechanicalEffect(effect_type="darkvision", value=60)],
        ),
        Feature(
            name="Gnome Cunning",
            source=_RS,
            source_name="Gnome",
            description="Advantage on INT, WIS, CHA saves against magic.",
            effects=[
                MechanicalEffect(
                    effect_type="save_advantage_magic",
                    value=["INT", "WIS", "CHA"],
                )
            ],
        ),
    ],
    Race.TIEFLING: [
        Feature(
            name="Darkvision",
            source=_RS,
            source_name="Tiefling",
            description="You can see in dim light within 60 feet as if bright light.",
            effects=[MechanicalEffect(effect_type="darkvision", value=60)],
        ),
        Feature(
            name="Hellish Resistance",
            source=_RS,
            source_name="Tiefling",
            description="You have resistance to fire damage.",
            effects=[MechanicalEffect(effect_type="damage_resistance", value="fire")],
        ),
        Feature(
            name="Infernal Legacy",
            source=_RS,
            source_name="Tiefling",
            description="You know the thaumaturgy cantrip.",
            effects=[
                MechanicalEffect(effect_type="cantrip", value="thaumaturgy")
            ],
        ),
    ],
}
