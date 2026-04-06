"""Dice expression parser and roller.

Parses expressions like "2d6+3" and returns structured DiceResult.
For d20 checks against a DC, use roll_check() to get a D20CheckResult
with a 6-tier RollOutcome (critical_failure → critical_success).

Pure deterministic logic (randomness via random.randint only).
"""

import random
import re
from enum import StrEnum

from pydantic import BaseModel


class DiceResult(BaseModel):
    """Result of rolling a dice expression."""

    expression: str
    rolls: list[int]
    modifier: int = 0
    total: int


# Pattern: count 'd' sides, optional +/- modifier
_DICE_RE = re.compile(r"^(\d+)d(\d+)([+-]\d+)?$")


def roll(expression: str) -> DiceResult:
    """Roll a dice expression like '2d6+3' and return a DiceResult.

    Args:
        expression: Dice notation string (e.g. "1d20", "2d6+3", "1d8-1").

    Returns:
        DiceResult with individual rolls, modifier, and total.

    Raises:
        ValueError: If the expression is not valid dice notation.
    """
    cleaned = expression.replace(" ", "")
    match = _DICE_RE.match(cleaned)
    if not match:
        raise ValueError(f"Invalid dice expression: '{expression}'")

    num_dice = int(match.group(1))
    num_sides = int(match.group(2))

    if num_dice < 1 or num_sides < 1:
        raise ValueError(f"Invalid dice expression: '{expression}'")

    modifier = int(match.group(3)) if match.group(3) else 0

    rolls = [random.randint(1, num_sides) for _ in range(num_dice)]
    total = sum(rolls) + modifier

    return DiceResult(
        expression=cleaned,
        rolls=rolls,
        modifier=modifier,
        total=total,
    )


# ---------------------------------------------------------------------------
# D20 check outcomes (6-tier)
# ---------------------------------------------------------------------------


class RollOutcome(StrEnum):
    """Six-tier outcome for d20 checks against a DC."""

    CRITICAL_FAILURE = "critical_failure"
    FAILURE = "failure"
    NEAR_FAILURE = "near_failure"
    NEAR_SUCCESS = "near_success"
    SUCCESS = "success"
    CRITICAL_SUCCESS = "critical_success"


class D20CheckResult(DiceResult):
    """Result of a d20 roll evaluated against a DC.

    Extends DiceResult with the difficulty class, the margin (total − DC),
    and a 6-tier outcome that the narrator can use for tone guidance.
    """

    dc: int
    outcome: RollOutcome
    margin: int


def _compute_outcome(natural_roll: int, margin: int) -> RollOutcome:
    """Determine the 6-tier outcome from a natural d20 roll and margin.

    Nat 1 / nat 20 always override margin-based tiers.
    """
    if natural_roll == 1:
        return RollOutcome.CRITICAL_FAILURE
    if natural_roll == 20:
        return RollOutcome.CRITICAL_SUCCESS
    if margin <= -5:
        return RollOutcome.FAILURE
    if margin < 0:
        return RollOutcome.NEAR_FAILURE
    if margin < 5:
        return RollOutcome.NEAR_SUCCESS
    return RollOutcome.SUCCESS


def roll_check(expression: str, dc: int) -> D20CheckResult:
    """Roll a d20 expression against a DC and return a D20CheckResult.

    Args:
        expression: Dice notation (typically "1d20" or "1d20+5").
        dc: Difficulty class / armor class to beat.

    Returns:
        D20CheckResult with outcome tier, margin, and all DiceResult fields.
    """
    base = roll(expression)
    natural_roll = base.rolls[0]
    margin = base.total - dc
    outcome = _compute_outcome(natural_roll, margin)

    return D20CheckResult(
        expression=base.expression,
        rolls=base.rolls,
        modifier=base.modifier,
        total=base.total,
        dc=dc,
        outcome=outcome,
        margin=margin,
    )
