"""Per-ObjectiveKind matcher functions.

Each matcher returns a float in [0.0, 1.0] indicating how well the player
action matches the objective. Threshold check happens in the engine, not here.

Pure functions. No I/O. No mutable state. Safe to call N times per turn.

Implementation note — ActionType deviation:
    The spec references ``ActionType.EXAMINE``, but this project's ActionType
    enum does not include EXAMINE. The EXAMINE objective kind is therefore
    matched against ``ActionType.LOOK``, which is the closest examination-type
    action available in the enum.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from typing import Any

from ai.models import InterpretedAction, MechanicsOutcome
from engine.validators import ActionType
from world.location import Location
from world.story_arc import BeatObjective, GateKind, ObjectiveGate, ObjectiveKind


_ARTICLES = frozenset({
    "the", "a", "an",
    "le", "la", "les", "l", "un", "une", "des", "du", "de",
})


def normalize(text: str) -> str:
    """Lowercase, strip accents, remove leading articles and punctuation.

    Examples::

        normalize("Le Marché aux Poissons") == "marche aux poissons"
        normalize("L'Auberge") == "auberge"
    """
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_lower = nfkd.encode("ascii", "ignore").decode("ascii").lower()
    cleaned = re.sub(r"[^\w\s]", " ", ascii_lower)
    words = [w for w in cleaned.split() if w not in _ARTICLES]
    return " ".join(words)


def _fuzzy(a: str, b: str) -> float:
    """SequenceMatcher ratio after normalization."""
    return difflib.SequenceMatcher(None, normalize(a), normalize(b)).ratio()


def compute_match_score(
    obj: BeatObjective,
    interpreted: InterpretedAction,
    outcome: MechanicsOutcome,
    location: Location | None,
    world_flags: dict[str, Any],
    inventory: set[str],
) -> float:
    """Compute how well this action matches this objective.

    Returns 0.0 for definite no-match (wrong action_type, no location for
    ARRIVE, etc.). Returns up to 1.0 for a perfect match. The threshold
    comparison (score >= obj.fuzzy_threshold) is the caller's responsibility.

    Args:
        obj:         The beat objective to evaluate against.
        interpreted: The player's parsed action for this turn.
        outcome:     The mechanical result produced by the engine.
        location:    The player's current location after the action, or None.
        world_flags: Mutable world-state flags (flag_name → truthy value).
        inventory:   The player's current inventory as a set of item names.

    Returns:
        float in [0.0, 1.0].
    """
    if obj.kind == ObjectiveKind.TALK:
        if interpreted.action_type != ActionType.TALK:
            return 0.0
        if not interpreted.target_name:
            return 0.0
        return _fuzzy(interpreted.target_name, obj.target)

    if obj.kind == ObjectiveKind.DEFEAT:
        # Defeat is a structured signal — combat code paths set
        # outcome.target_defeated when an action kills a creature.
        # We do a fuzzy match (not exact) so casing/articles/typos
        # in the beat objective's target don't cause false negatives.
        if not outcome.target_defeated:
            return 0.0
        return _fuzzy(outcome.target_defeated, obj.target)

    if obj.kind == ObjectiveKind.ARRIVE:
        if location is None:
            return 0.0
        return _fuzzy(location.name, obj.target)

    if obj.kind == ObjectiveKind.EXAMINE:
        # ActionType.EXAMINE does not exist in this project; LOOK is the
        # examination-type action (see module docstring).
        if interpreted.action_type != ActionType.LOOK:
            return 0.0
        if not interpreted.target_name:
            return 0.0
        return _fuzzy(interpreted.target_name, obj.target)

    if obj.kind == ObjectiveKind.POSSESS:
        # POSSESS is binary: the item is in inventory or it isn't.
        normalized_target = normalize(obj.target)
        for item in inventory:
            if normalize(item) == normalized_target:
                return 1.0
        return 0.0

    if obj.kind == ObjectiveKind.FLAG:
        # FLAG is binary: world_flags[target] is truthy.
        return 1.0 if world_flags.get(obj.target) else 0.0

    return 0.0


def evaluate_gate(
    gate: ObjectiveGate,
    outcome: MechanicsOutcome,
    world_flags: dict[str, Any],
    inventory: set[str],
) -> bool:
    """Evaluate a gate constraint. Returns True if the gate is satisfied."""
    if gate.kind == GateKind.MIN_REVEALS:
        threshold = int(gate.value)
        return outcome.talk_reveals_count >= threshold

    if gate.kind == GateKind.MIN_DISPOSITION:
        threshold = int(gate.value)
        return outcome.talk_disposition_change >= threshold

    if gate.kind == GateKind.HAS_ITEM:
        target = str(gate.value)
        normalized_target = normalize(target)
        return any(normalize(item) == normalized_target for item in inventory)

    if gate.kind == GateKind.FLAG_SET:
        return bool(world_flags.get(str(gate.value)))

    return False
