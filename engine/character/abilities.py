"""Ability score functions for the character system."""

from __future__ import annotations

from typing import TYPE_CHECKING

from engine.dice import roll

from .enums import Ability, Race, Skill, SKILL_ABILITY
from .models import AbilityScores
from .races import RACIAL_ABILITY_BONUSES

if TYPE_CHECKING:
    from .models import Character


STANDARD_ARRAY: tuple[int, ...] = (15, 14, 13, 12, 10, 8)


def compute_modifier(score: int) -> int:
    """Compute ability modifier from a score. SRD formula: (score - 10) // 2."""
    return (score - 10) // 2


def apply_racial_bonuses(scores: AbilityScores, race: Race) -> AbilityScores:
    """Apply racial ability score bonuses. Returns a new AbilityScores."""
    bonuses = RACIAL_ABILITY_BONUSES[race]
    data = scores.model_dump()
    for ability, bonus in bonuses.items():
        data[ability.name] += bonus
    return AbilityScores(**data)


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


def assign_standard_array(
    assignments: dict[Ability, int],
    race: Race,
) -> AbilityScores:
    """Assign Standard Array values to abilities, then apply racial bonuses.

    Validations:
    - Exactly 6 assignments (one per Ability)
    - Each Standard Array value used exactly once
    - All 6 Abilities covered

    Returns AbilityScores with racial bonuses applied.
    Raises ValueError if assignments are invalid.
    """
    all_abilities = list(Ability)

    if len(assignments) != len(all_abilities):
        raise ValueError(
            f"Expected exactly {len(all_abilities)} assignments, got {len(assignments)}"
        )

    missing = [a for a in all_abilities if a not in assignments]
    if missing:
        raise ValueError(f"Missing ability assignments: {[a.value for a in missing]}")

    values_used = sorted(assignments.values())
    expected = sorted(STANDARD_ARRAY)
    if values_used != expected:
        raise ValueError(
            f"Values {values_used} do not match Standard Array {expected}. "
            "Each Standard Array value must be used exactly once."
        )

    scores = AbilityScores(
        STR=assignments[Ability.STR],
        DEX=assignments[Ability.DEX],
        CON=assignments[Ability.CON],
        INT=assignments[Ability.INT],
        WIS=assignments[Ability.WIS],
        CHA=assignments[Ability.CHA],
    )
    return apply_racial_bonuses(scores, race)


def compute_skill_modifier(character: Character, skill: Skill) -> int:
    """Compute the skill check modifier for ``skill``.

    SRD 5e formula:
        ability_mod + (2 × proficiency_bonus  if Expertise applies)
                    + (proficiency_bonus       if simply proficient)

    Expertise is granted by the Rogue (level 1) and Bard (level 3) class
    features and is tracked on :attr:`Character.expertise_skills`. A skill
    listed in ``expertise_skills`` always uses the doubled bonus, even if
    the same skill is missing from ``skill_proficiencies`` — the only way
    to legitimately acquire Expertise implies proficiency, so we collapse
    the two into one branch.
    """
    ability = SKILL_ABILITY[skill]
    mod = compute_modifier(character.ability_scores.get(ability))
    if skill in character.expertise_skills:
        mod += 2 * character.proficiency_bonus
    elif skill in character.skill_proficiencies:
        mod += character.proficiency_bonus
    return mod
