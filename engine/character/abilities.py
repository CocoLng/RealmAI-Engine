"""Ability score functions for the character system."""

from engine.dice import roll

from .enums import Race
from .models import AbilityScores
from .races import RACIAL_ABILITY_BONUSES


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
