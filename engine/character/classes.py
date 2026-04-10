"""Class reference tables for the character system."""

from pydantic import BaseModel

from .enums import Ability, CharacterClass, Skill
from .features import Feature, FeatureSource, MechanicalEffect

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

# ── Class skill choices ──────────────────────────────────────────────────


class ClassSkillConfig(BaseModel):
    """How many skills a class picks and from which options."""

    choose: int
    options: list[Skill]


CLASS_SKILL_CHOICES: dict[CharacterClass, ClassSkillConfig] = {
    CharacterClass.FIGHTER: ClassSkillConfig(
        choose=2,
        options=[
            Skill.ACROBATICS,
            Skill.ANIMAL_HANDLING,
            Skill.ATHLETICS,
            Skill.HISTORY,
            Skill.INSIGHT,
            Skill.INTIMIDATION,
            Skill.PERCEPTION,
            Skill.SURVIVAL,
        ],
    ),
    CharacterClass.WIZARD: ClassSkillConfig(
        choose=2,
        options=[
            Skill.ARCANA,
            Skill.HISTORY,
            Skill.INSIGHT,
            Skill.INVESTIGATION,
            Skill.MEDICINE,
            Skill.RELIGION,
        ],
    ),
    CharacterClass.ROGUE: ClassSkillConfig(
        choose=4,
        options=[
            Skill.ACROBATICS,
            Skill.ATHLETICS,
            Skill.DECEPTION,
            Skill.INSIGHT,
            Skill.INTIMIDATION,
            Skill.INVESTIGATION,
            Skill.PERCEPTION,
            Skill.PERFORMANCE,
            Skill.PERSUASION,
            Skill.SLEIGHT_OF_HAND,
            Skill.STEALTH,
        ],
    ),
    CharacterClass.CLERIC: ClassSkillConfig(
        choose=2,
        options=[
            Skill.HISTORY,
            Skill.INSIGHT,
            Skill.MEDICINE,
            Skill.PERSUASION,
            Skill.RELIGION,
        ],
    ),
    CharacterClass.RANGER: ClassSkillConfig(
        choose=3,
        options=[
            Skill.ANIMAL_HANDLING,
            Skill.ATHLETICS,
            Skill.INSIGHT,
            Skill.INVESTIGATION,
            Skill.NATURE,
            Skill.PERCEPTION,
            Skill.STEALTH,
            Skill.SURVIVAL,
        ],
    ),
    CharacterClass.BARBARIAN: ClassSkillConfig(
        choose=2,
        options=[
            Skill.ANIMAL_HANDLING,
            Skill.ATHLETICS,
            Skill.INTIMIDATION,
            Skill.NATURE,
            Skill.PERCEPTION,
            Skill.SURVIVAL,
        ],
    ),
}

# ── Class features catalog (level 1 only) ────────────────────────────────

_CS = FeatureSource.CLASS  # shorthand

CLASS_FEATURES: dict[CharacterClass, list[Feature]] = {
    CharacterClass.FIGHTER: [
        Feature(
            name="Fighting Style",
            source=_CS,
            source_name="Fighter",
            description="You adopt a particular style of fighting as your specialty.",
            effects=[MechanicalEffect(effect_type="fighting_style", value=1)],
        ),
        Feature(
            name="Second Wind",
            source=_CS,
            source_name="Fighter",
            description="Bonus action to regain 1d10 + fighter level HP, once per short rest.",
            effects=[MechanicalEffect(effect_type="second_wind", value="1d10")],
        ),
    ],
    CharacterClass.WIZARD: [
        Feature(
            name="Arcane Recovery",
            source=_CS,
            source_name="Wizard",
            description="Recover spell slots during a short rest once per day.",
            effects=[MechanicalEffect(effect_type="arcane_recovery", value=1)],
        ),
        Feature(
            name="Spellcasting",
            source=_CS,
            source_name="Wizard",
            description="You can cast wizard spells using INT as your spellcasting ability.",
            effects=[
                MechanicalEffect(effect_type="spellcasting", value="INT")
            ],
        ),
    ],
    CharacterClass.ROGUE: [
        Feature(
            name="Sneak Attack",
            source=_CS,
            source_name="Rogue",
            description="Extra 1d6 damage when you have advantage or an ally is adjacent.",
            effects=[MechanicalEffect(effect_type="sneak_attack", value="1d6")],
        ),
        Feature(
            name="Expertise",
            source=_CS,
            source_name="Rogue",
            description="Double proficiency bonus for two chosen skill proficiencies.",
            effects=[MechanicalEffect(effect_type="expertise", value=2)],
        ),
        Feature(
            name="Thieves' Cant",
            source=_CS,
            source_name="Rogue",
            description="You know thieves' cant, a secret mix of dialect and coded messages.",
            effects=[
                MechanicalEffect(effect_type="language", value="Thieves' Cant")
            ],
        ),
    ],
    CharacterClass.CLERIC: [
        Feature(
            name="Spellcasting",
            source=_CS,
            source_name="Cleric",
            description="You can cast cleric spells using WIS as your spellcasting ability.",
            effects=[
                MechanicalEffect(effect_type="spellcasting", value="WIS")
            ],
        ),
        Feature(
            name="Divine Domain",
            source=_CS,
            source_name="Cleric",
            description="Choose a domain that grants bonus spells and features.",
            effects=[MechanicalEffect(effect_type="divine_domain", value=1)],
        ),
    ],
    CharacterClass.RANGER: [
        Feature(
            name="Favored Enemy",
            source=_CS,
            source_name="Ranger",
            description="Advantage on Survival checks to track and INT checks to recall info about chosen enemy type.",
            effects=[MechanicalEffect(effect_type="favored_enemy", value=1)],
        ),
        Feature(
            name="Natural Explorer",
            source=_CS,
            source_name="Ranger",
            description="You are adept at traveling and surviving in a chosen terrain.",
            effects=[
                MechanicalEffect(effect_type="natural_explorer", value=1)
            ],
        ),
    ],
    CharacterClass.BARBARIAN: [
        Feature(
            name="Rage",
            source=_CS,
            source_name="Barbarian",
            description="Enter a rage for bonus damage and resistance to physical damage.",
            effects=[
                MechanicalEffect(effect_type="rage", value=2),
                MechanicalEffect(
                    effect_type="damage_resistance",
                    value=["bludgeoning", "piercing", "slashing"],
                ),
            ],
        ),
        Feature(
            name="Unarmored Defense",
            source=_CS,
            source_name="Barbarian",
            description="While not wearing armor, AC = 10 + DEX mod + CON mod.",
            effects=[
                MechanicalEffect(effect_type="unarmored_defense", value="CON")
            ],
        ),
    ],
}
