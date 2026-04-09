"""Spell system — schools, slots, casting, cantrip scaling, spell catalog.

Pure deterministic Python (no LLM).
"""

import logging
from enum import StrEnum

from pydantic import BaseModel, Field

from engine.character import Ability, CharacterClass, compute_modifier
from engine.dice import parse_dice
from engine.inventory import DamageType

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class SpellSchool(StrEnum):
    """The eight schools of magic."""

    ABJURATION = "Abjuration"
    CONJURATION = "Conjuration"
    DIVINATION = "Divination"
    ENCHANTMENT = "Enchantment"
    EVOCATION = "Evocation"
    ILLUSION = "Illusion"
    NECROMANCY = "Necromancy"
    TRANSMUTATION = "Transmutation"


class CastingTime(StrEnum):
    """How long it takes to cast a spell."""

    ACTION = "Action"
    BONUS_ACTION = "Bonus Action"
    REACTION = "Reaction"
    MINUTE_1 = "1 Minute"
    MINUTE_10 = "10 Minutes"


class SpellRange(StrEnum):
    """How far a spell can reach."""

    SELF = "Self"
    TOUCH = "Touch"
    FEET_30 = "30 feet"
    FEET_60 = "60 feet"
    FEET_90 = "90 feet"
    FEET_120 = "120 feet"
    FEET_150 = "150 feet"


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class Spell(BaseModel):
    """A spell definition with mechanical effects."""

    name: str = Field(min_length=1)
    level: int = Field(ge=0, le=9)  # 0 = cantrip
    school: SpellSchool
    casting_time: CastingTime
    spell_range: SpellRange  # 'range' is a Python builtin
    components: list[str] = Field(default_factory=list)  # ["V", "S", "M"]
    duration_rounds: int | None = None  # None = instantaneous
    concentration: bool = False
    description: str = ""
    # Effect fields (optional)
    damage_dice: str | None = None
    damage_type: DamageType | None = None
    healing_dice: str | None = None
    saving_throw: Ability | None = None
    condition_applied: str | None = None  # ConditionType name as string
    higher_level_dice: str | None = None  # extra dice per slot above base level


class SpellcasterState(BaseModel):
    """Tracks a character's spellcasting resources."""

    spellcasting_ability: Ability
    spells_known: list[str] = Field(default_factory=list)  # spell names
    spell_slots_max: dict[int, int] = Field(default_factory=dict)  # {1: 4, 2: 3}
    spell_slots_remaining: dict[int, int] = Field(default_factory=dict)
    concentration_spell: str | None = None


# ---------------------------------------------------------------------------
# Lookup tables (SRD 5e)
# ---------------------------------------------------------------------------


CLASS_SPELLCASTING_ABILITY: dict[CharacterClass, Ability | None] = {
    CharacterClass.WIZARD: Ability.INT,
    CharacterClass.CLERIC: Ability.WIS,
    CharacterClass.RANGER: Ability.WIS,
    CharacterClass.FIGHTER: None,
    CharacterClass.ROGUE: None,
    CharacterClass.BARBARIAN: None,
}

# Full caster spell slots (Wizard, Cleric) — SRD 5e table
FULL_CASTER_SLOTS: dict[int, dict[int, int]] = {
    1:  {1: 2},
    2:  {1: 3},
    3:  {1: 4, 2: 2},
    4:  {1: 4, 2: 3},
    5:  {1: 4, 2: 3, 3: 2},
    6:  {1: 4, 2: 3, 3: 3},
    7:  {1: 4, 2: 3, 3: 3, 4: 1},
    8:  {1: 4, 2: 3, 3: 3, 4: 2},
    9:  {1: 4, 2: 3, 3: 3, 4: 3, 5: 1},
    10: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2},
    11: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2, 6: 1},
    12: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2, 6: 1},
    13: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2, 6: 1, 7: 1},
    14: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2, 6: 1, 7: 1},
    15: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2, 6: 1, 7: 1, 8: 1},
    16: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2, 6: 1, 7: 1, 8: 1},
    17: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2, 6: 1, 7: 1, 8: 1, 9: 1},
    18: {1: 4, 2: 3, 3: 3, 4: 3, 5: 3, 6: 1, 7: 1, 8: 1, 9: 1},
    19: {1: 4, 2: 3, 3: 3, 4: 3, 5: 3, 6: 2, 7: 1, 8: 1, 9: 1},
    20: {1: 4, 2: 3, 3: 3, 4: 3, 5: 3, 6: 2, 7: 2, 8: 1, 9: 1},
}

# Half caster spell slots (Ranger) — SRD 5e table, no slots at level 1
HALF_CASTER_SLOTS: dict[int, dict[int, int]] = {
    1:  {},
    2:  {1: 2},
    3:  {1: 3},
    4:  {1: 3},
    5:  {1: 4, 2: 2},
    6:  {1: 4, 2: 2},
    7:  {1: 4, 2: 3},
    8:  {1: 4, 2: 3},
    9:  {1: 4, 2: 3, 3: 2},
    10: {1: 4, 2: 3, 3: 2},
    11: {1: 4, 2: 3, 3: 3},
    12: {1: 4, 2: 3, 3: 3},
    13: {1: 4, 2: 3, 3: 3, 4: 1},
    14: {1: 4, 2: 3, 3: 3, 4: 1},
    15: {1: 4, 2: 3, 3: 3, 4: 2},
    16: {1: 4, 2: 3, 3: 3, 4: 2},
    17: {1: 4, 2: 3, 3: 3, 4: 3, 5: 1},
    18: {1: 4, 2: 3, 3: 3, 4: 3, 5: 1},
    19: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2},
    20: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2},
}

# Cantrip damage scaling: character level thresholds
_CANTRIP_SCALE: list[tuple[int, int]] = [
    (17, 4),
    (11, 3),
    (5, 2),
    (1, 1),
]


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------


def get_spell_slots(char_class: CharacterClass, level: int) -> dict[int, int]:
    """Return spell slot counts by spell level for a class at a given character level.

    Non-casters return an empty dict.

    Raises:
        ValueError: If level is out of range.
    """
    if not 1 <= level <= 20:
        raise ValueError(f"Level must be 1-20, got {level}")

    ability = CLASS_SPELLCASTING_ABILITY.get(char_class)
    if ability is None:
        return {}

    if char_class == CharacterClass.RANGER:
        return dict(HALF_CASTER_SLOTS[level])

    # Full casters: Wizard, Cleric
    return dict(FULL_CASTER_SLOTS[level])


def compute_spell_dc(ability_score: int, proficiency_bonus: int) -> int:
    """Compute spell save DC: 8 + ability modifier + proficiency bonus."""
    return 8 + compute_modifier(ability_score) + proficiency_bonus


def compute_spell_attack_bonus(ability_score: int, proficiency_bonus: int) -> int:
    """Compute spell attack modifier: ability modifier + proficiency bonus."""
    return compute_modifier(ability_score) + proficiency_bonus


def get_cantrip_damage_dice(spell: Spell, caster_level: int) -> str:
    """Scale cantrip damage dice by caster level. Returns a new dice expression string (no mutation).

    Raises:
        ValueError: If spell is not a cantrip or has no damage_dice.
    """
    if spell.level != 0:
        raise ValueError(f"'{spell.name}' is not a cantrip (level {spell.level})")
    if spell.damage_dice is None:
        raise ValueError(f"'{spell.name}' has no damage dice")

    # Determine scale factor from caster level
    num_dice = 1
    for threshold, dice_count in _CANTRIP_SCALE:
        if caster_level >= threshold:
            num_dice = dice_count
            break

    # Parse the base die using canonical regex parser
    _, sides, _ = parse_dice(spell.damage_dice)
    return f"{num_dice}d{sides}"


def can_cast_spell(state: SpellcasterState, spell: Spell) -> bool:
    """Check if caster knows the spell and has an available slot.

    Cantrips always return True if known. Leveled spells require an
    available slot of the spell's level or higher.
    """
    if spell.name not in state.spells_known:
        return False

    # Cantrips don't consume slots
    if spell.level == 0:
        return True

    # Check for any slot at spell level or higher
    for slot_level, remaining in state.spell_slots_remaining.items():
        if slot_level >= spell.level and remaining > 0:
            return True
    return False


def cast_spell(
    state: SpellcasterState,
    spell: Spell,
    slot_level: int | None = None,
) -> SpellcasterState:
    """Consume a spell slot and manage concentration. Mutates state in place.

    - Cantrips: no slot consumed.
    - slot_level defaults to spell.level if not provided.
    - If spell requires concentration and caster is already concentrating,
      the old concentration ends.
    - Raises ValueError if can't cast.
    """
    if spell.name not in state.spells_known:
        raise ValueError(f"Spell '{spell.name}' is not known")

    # Cantrips: no slot needed
    if spell.level == 0:
        if spell.concentration:
            state.concentration_spell = spell.name
        return state

    # Determine slot level
    if slot_level is None:
        slot_level = spell.level

    if slot_level < spell.level:
        raise ValueError(
            f"Slot level {slot_level} is below spell level {spell.level}"
        )

    remaining = state.spell_slots_remaining.get(slot_level, 0)
    if remaining <= 0:
        raise ValueError(f"No spell slots remaining at level {slot_level}")

    state.spell_slots_remaining[slot_level] = remaining - 1

    # Concentration management: casting a new concentration spell ends the old one (SRD 5e)
    if spell.concentration:
        if state.concentration_spell is not None:
            logger.info(
                "Concentration on '%s' broken by casting '%s'",
                state.concentration_spell,
                spell.name,
            )
        state.concentration_spell = spell.name

    return state


def create_spellcaster_state(
    char_class: CharacterClass, level: int,
) -> SpellcasterState | None:
    """Create a SpellcasterState for a class at a given level.

    Returns None for non-casters.
    """
    ability = CLASS_SPELLCASTING_ABILITY.get(char_class)
    if ability is None:
        return None

    slots = get_spell_slots(char_class, level)
    return SpellcasterState(
        spellcasting_ability=ability,
        spell_slots_max=dict(slots),
        spell_slots_remaining=dict(slots),
    )


def restore_spell_slots(state: SpellcasterState) -> SpellcasterState:
    """Long rest: reset all spell slots to max. Mutates in place."""
    state.spell_slots_remaining = dict(state.spell_slots_max)
    state.concentration_spell = None
    return state


# ---------------------------------------------------------------------------
# Spell catalog (~20 SRD 5e spells)
# ---------------------------------------------------------------------------


SPELL_CATALOG: dict[str, Spell] = {
    # --- Cantrips (level 0) ---
    "Fire Bolt": Spell(
        name="Fire Bolt",
        level=0,
        school=SpellSchool.EVOCATION,
        casting_time=CastingTime.ACTION,
        spell_range=SpellRange.FEET_120,
        components=["V", "S"],
        damage_dice="1d10",
        damage_type=DamageType.FIRE,
        description="A mote of fire streaks toward a creature within range.",
    ),
    "Sacred Flame": Spell(
        name="Sacred Flame",
        level=0,
        school=SpellSchool.EVOCATION,
        casting_time=CastingTime.ACTION,
        spell_range=SpellRange.FEET_60,
        components=["V", "S"],
        damage_dice="1d8",
        damage_type=DamageType.RADIANT,
        saving_throw=Ability.DEX,
        description="Flame-like radiance descends on a creature you can see.",
    ),
    "Ray of Frost": Spell(
        name="Ray of Frost",
        level=0,
        school=SpellSchool.EVOCATION,
        casting_time=CastingTime.ACTION,
        spell_range=SpellRange.FEET_60,
        components=["V", "S"],
        damage_dice="1d8",
        damage_type=DamageType.COLD,
        description="A frigid beam of blue-white light streaks toward a creature.",
    ),
    "Light": Spell(
        name="Light",
        level=0,
        school=SpellSchool.EVOCATION,
        casting_time=CastingTime.ACTION,
        spell_range=SpellRange.TOUCH,
        components=["V"],
        duration_rounds=60,
        description="An object you touch sheds bright light in a 20-foot radius.",
    ),
    "Guidance": Spell(
        name="Guidance",
        level=0,
        school=SpellSchool.DIVINATION,
        casting_time=CastingTime.ACTION,
        spell_range=SpellRange.TOUCH,
        components=["V", "S"],
        concentration=True,
        duration_rounds=10,
        description="You touch one willing creature and grant a d4 bonus to one ability check.",
    ),
    # --- Level 1 ---
    "Magic Missile": Spell(
        name="Magic Missile",
        level=1,
        school=SpellSchool.EVOCATION,
        casting_time=CastingTime.ACTION,
        spell_range=SpellRange.FEET_120,
        components=["V", "S"],
        damage_dice="3d4+3",
        damage_type=DamageType.FORCE,
        description="Three glowing darts of magical force strike unerringly.",
        higher_level_dice="1d4+1",
    ),
    "Shield": Spell(
        name="Shield",
        level=1,
        school=SpellSchool.ABJURATION,
        casting_time=CastingTime.REACTION,
        spell_range=SpellRange.SELF,
        components=["V", "S"],
        duration_rounds=1,
        description="+5 AC until the start of your next turn.",
    ),
    "Cure Wounds": Spell(
        name="Cure Wounds",
        level=1,
        school=SpellSchool.EVOCATION,
        casting_time=CastingTime.ACTION,
        spell_range=SpellRange.TOUCH,
        components=["V", "S"],
        healing_dice="1d8",
        description="A creature you touch regains hit points.",
        higher_level_dice="1d8",
    ),
    "Burning Hands": Spell(
        name="Burning Hands",
        level=1,
        school=SpellSchool.EVOCATION,
        casting_time=CastingTime.ACTION,
        spell_range=SpellRange.SELF,
        components=["V", "S"],
        damage_dice="3d6",
        damage_type=DamageType.FIRE,
        saving_throw=Ability.DEX,
        description="A thin sheet of flames shoots forth from your outstretched fingertips.",
    ),
    "Thunderwave": Spell(
        name="Thunderwave",
        level=1,
        school=SpellSchool.EVOCATION,
        casting_time=CastingTime.ACTION,
        spell_range=SpellRange.SELF,
        components=["V", "S"],
        damage_dice="2d8",
        damage_type=DamageType.THUNDER,
        saving_throw=Ability.CON,
        description="A wave of thunderous force sweeps out from you.",
    ),
    "Healing Word": Spell(
        name="Healing Word",
        level=1,
        school=SpellSchool.EVOCATION,
        casting_time=CastingTime.BONUS_ACTION,
        spell_range=SpellRange.FEET_60,
        components=["V"],
        healing_dice="1d4",
        description="A creature of your choice that you can see regains hit points.",
        higher_level_dice="1d4",
    ),
    "Hunter's Mark": Spell(
        name="Hunter's Mark",
        level=1,
        school=SpellSchool.DIVINATION,
        casting_time=CastingTime.BONUS_ACTION,
        spell_range=SpellRange.FEET_90,
        components=["V"],
        concentration=True,
        duration_rounds=10,
        description="You choose a creature you can see and mark it as your quarry.",
    ),
    "Bless": Spell(
        name="Bless",
        level=1,
        school=SpellSchool.ENCHANTMENT,
        casting_time=CastingTime.ACTION,
        spell_range=SpellRange.FEET_30,
        components=["V", "S", "M"],
        concentration=True,
        duration_rounds=10,
        description="Up to three creatures add a d4 to attack rolls and saving throws.",
    ),
    # --- Level 2 ---
    "Scorching Ray": Spell(
        name="Scorching Ray",
        level=2,
        school=SpellSchool.EVOCATION,
        casting_time=CastingTime.ACTION,
        spell_range=SpellRange.FEET_120,
        components=["V", "S"],
        damage_dice="2d6",
        damage_type=DamageType.FIRE,
        description="You create three rays of fire and hurl them at targets within range.",
    ),
    "Hold Person": Spell(
        name="Hold Person",
        level=2,
        school=SpellSchool.ENCHANTMENT,
        casting_time=CastingTime.ACTION,
        spell_range=SpellRange.FEET_60,
        components=["V", "S", "M"],
        concentration=True,
        duration_rounds=10,
        saving_throw=Ability.WIS,
        condition_applied="Paralyzed",
        description="A humanoid you can see must succeed on a WIS save or be paralyzed.",
    ),
    "Misty Step": Spell(
        name="Misty Step",
        level=2,
        school=SpellSchool.CONJURATION,
        casting_time=CastingTime.BONUS_ACTION,
        spell_range=SpellRange.SELF,
        components=["V"],
        description="You teleport up to 30 feet to an unoccupied space you can see.",
    ),
    "Spiritual Weapon": Spell(
        name="Spiritual Weapon",
        level=2,
        school=SpellSchool.EVOCATION,
        casting_time=CastingTime.BONUS_ACTION,
        spell_range=SpellRange.FEET_60,
        components=["V", "S"],
        damage_dice="1d8",
        damage_type=DamageType.RADIANT,
        duration_rounds=10,
        description="You create a floating, spectral weapon that attacks on your behalf.",
    ),
    # --- Level 3 ---
    "Fireball": Spell(
        name="Fireball",
        level=3,
        school=SpellSchool.EVOCATION,
        casting_time=CastingTime.ACTION,
        spell_range=SpellRange.FEET_150,
        components=["V", "S", "M"],
        damage_dice="8d6",
        damage_type=DamageType.FIRE,
        saving_throw=Ability.DEX,
        description="A bright streak of fire explodes into flame at a point you choose.",
        higher_level_dice="1d6",
    ),
    "Lightning Bolt": Spell(
        name="Lightning Bolt",
        level=3,
        school=SpellSchool.EVOCATION,
        casting_time=CastingTime.ACTION,
        spell_range=SpellRange.SELF,
        components=["V", "S", "M"],
        damage_dice="8d6",
        damage_type=DamageType.LIGHTNING,
        saving_throw=Ability.DEX,
        description="A stroke of lightning blasts out from you in a 100-foot line.",
        higher_level_dice="1d6",
    ),
    "Counterspell": Spell(
        name="Counterspell",
        level=3,
        school=SpellSchool.ABJURATION,
        casting_time=CastingTime.REACTION,
        spell_range=SpellRange.FEET_60,
        components=["S"],
        description="You attempt to interrupt a creature in the process of casting a spell.",
    ),
    "Revivify": Spell(
        name="Revivify",
        level=3,
        school=SpellSchool.NECROMANCY,
        casting_time=CastingTime.ACTION,
        spell_range=SpellRange.TOUCH,
        components=["V", "S", "M"],
        description="You touch a creature that has died within the last minute and restore it to 1 HP.",
    ),
}
