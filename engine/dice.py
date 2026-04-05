"""Dice expression parser and roller.

Parses expressions like "2d6+3" and returns structured DiceResult.
Pure deterministic logic (randomness via random.randint only).
"""

import random
import re

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
