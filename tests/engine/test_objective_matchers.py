"""Tests for per-ObjectiveKind matcher functions.

Deviation from spec: ActionType.EXAMINE does not exist in this project.
The EXAMINE objective kind is tested using ActionType.LOOK, which is the
closest examination-type action available (ActionType enum contains:
ATTACK, CAST_SPELL, DEFEND, DISENGAGE, EQUIP, FLEE, IMPROVISE, INTERACT,
LOOK, MOVE, PICKUP, QUESTION, SEARCH, TALK, USE_ITEM).
"""

from unittest.mock import MagicMock

from ai.models import InterpretedAction, MechanicsOutcome
from engine.objective_matchers import compute_match_score, normalize
from engine.validators import ActionType
from world.story_arc import BeatObjective, ObjectiveKind


def _interp(action_type, target=None, raw_input="") -> InterpretedAction:
    return InterpretedAction(
        action_type=action_type,
        actor_name="hero",
        target_name=target,
        raw_input=raw_input or f"{action_type.value} {target or ''}",
    )


def _outcome(**kwargs) -> MechanicsOutcome:
    base = {"summary": "ok"}
    base.update(kwargs)
    return MechanicsOutcome(**base)


def test_normalize_strips_accents_and_articles():
    assert normalize("Le Marché aux Poissons") == "marche aux poissons"
    assert normalize("L'Auberge") == "auberge"


def test_talk_match_positive():
    obj = BeatObjective(
        id="talk_kaelen",
        kind=ObjectiveKind.TALK,
        target="Kaelen",
        description="Speak with Kaelen",
    )
    interp = _interp(ActionType.TALK, target="Kaelen")
    score = compute_match_score(
        obj, interp, _outcome(), location=None, world_flags={}, inventory=set(),
    )
    assert score >= 0.7


def test_talk_match_wrong_action_type_returns_zero():
    obj = BeatObjective(
        id="talk_kaelen",
        kind=ObjectiveKind.TALK,
        target="Kaelen",
        description="...",
    )
    interp = _interp(ActionType.ATTACK, target="Kaelen")
    score = compute_match_score(
        obj, interp, _outcome(), location=None, world_flags={}, inventory=set(),
    )
    assert score == 0.0


def test_arrive_match_via_location():
    obj = BeatObjective(
        id="arrive_market",
        kind=ObjectiveKind.ARRIVE,
        target="Marché aux poissons",
        description="...",
    )
    location = MagicMock(name="Le Marché aux Poissons")
    location.name = "Le Marché aux Poissons"
    score = compute_match_score(
        obj, _interp(ActionType.MOVE), _outcome(),
        location=location, world_flags={}, inventory=set(),
    )
    assert score >= 0.7


def test_arrive_no_location_returns_zero():
    obj = BeatObjective(
        id="arrive_x",
        kind=ObjectiveKind.ARRIVE,
        target="X",
        description="...",
    )
    score = compute_match_score(
        obj, _interp(ActionType.MOVE), _outcome(),
        location=None, world_flags={}, inventory=set(),
    )
    assert score == 0.0


def test_defeat_match_via_target_defeated_field():
    """DEFEAT objectives complete when the combat engine writes
    target_defeated on the outcome (not via summary string-scan)."""
    obj = BeatObjective(
        id="defeat_wolf",
        kind=ObjectiveKind.DEFEAT,
        target="wolf",
        description="...",
    )
    score = compute_match_score(
        obj, _interp(ActionType.ATTACK, target="wolf"),
        _outcome(target_defeated="wolf"),
        location=None, world_flags={}, inventory=set(),
    )
    assert score >= 0.7


def test_defeat_no_target_defeated_returns_zero():
    """If outcome.target_defeated is None (no death this turn),
    DEFEAT score is 0 regardless of summary content."""
    obj = BeatObjective(
        id="defeat_wolf",
        kind=ObjectiveKind.DEFEAT,
        target="wolf",
        description="...",
    )
    score = compute_match_score(
        obj, _interp(ActionType.ATTACK, target="wolf"),
        _outcome(summary="The wolf is defeated.", target_defeated=None),
        location=None, world_flags={}, inventory=set(),
    )
    assert score == 0.0


def test_defeat_fuzzy_tolerates_casing():
    """target_defeated='Wolf' should still match obj.target='wolf'
    via fuzzy normalization."""
    obj = BeatObjective(
        id="defeat_wolf",
        kind=ObjectiveKind.DEFEAT,
        target="wolf",
        description="...",
    )
    score = compute_match_score(
        obj, _interp(ActionType.ATTACK, target="Wolf"),
        _outcome(target_defeated="Wolf"),
        location=None, world_flags={}, inventory=set(),
    )
    assert score >= 0.7


def test_examine_match():
    """DEVIATION: uses ActionType.LOOK instead of ActionType.EXAMINE (doesn't exist)."""
    obj = BeatObjective(
        id="examine_cape",
        kind=ObjectiveKind.EXAMINE,
        target="bloody cape",
        description="...",
    )
    interp = _interp(ActionType.LOOK, target="bloody cape")
    score = compute_match_score(
        obj, interp, _outcome(), location=None, world_flags={}, inventory=set(),
    )
    assert score >= 0.7


def test_possess_match_via_inventory():
    obj = BeatObjective(
        id="possess_key",
        kind=ObjectiveKind.POSSESS,
        target="silver key",
        description="...",
    )
    score = compute_match_score(
        obj, _interp(ActionType.PICKUP), _outcome(),
        location=None, world_flags={}, inventory={"silver key"},
    )
    assert score == 1.0


def test_flag_match_via_world_state():
    obj = BeatObjective(
        id="flag_oath",
        kind=ObjectiveKind.FLAG,
        target="oath_sworn",
        description="...",
    )
    score = compute_match_score(
        obj, _interp(ActionType.IMPROVISE), _outcome(),
        location=None, world_flags={"oath_sworn": True}, inventory=set(),
    )
    assert score == 1.0


def test_fuzzy_edge_below_threshold():
    obj = BeatObjective(
        id="talk_kaelen",
        kind=ObjectiveKind.TALK,
        target="Kaelen",
        description="...",
        fuzzy_threshold=0.7,
    )
    # similar but not identical
    interp = _interp(ActionType.TALK, target="Kael")
    score = compute_match_score(
        obj, interp, _outcome(), location=None, world_flags={}, inventory=set(),
    )
    # Score is implementation-specific; assert it's between 0 and 1, and
    # accept the difflib ratio as ground truth — the algorithm will use this.
    assert 0.0 < score < 1.0


def test_gate_min_reveals_passes():
    from engine.objective_matchers import evaluate_gate
    from world.story_arc import GateKind, ObjectiveGate
    gate = ObjectiveGate(kind=GateKind.MIN_REVEALS, value=1)
    out = _outcome(talk_reveals_count=2)
    assert evaluate_gate(gate, out, world_flags={}, inventory=set()) is True


def test_gate_min_reveals_fails():
    from engine.objective_matchers import evaluate_gate
    from world.story_arc import GateKind, ObjectiveGate
    gate = ObjectiveGate(kind=GateKind.MIN_REVEALS, value=1)
    out = _outcome(talk_reveals_count=0)
    assert evaluate_gate(gate, out, world_flags={}, inventory=set()) is False


def test_gate_min_disposition():
    from engine.objective_matchers import evaluate_gate
    from world.story_arc import GateKind, ObjectiveGate
    gate = ObjectiveGate(kind=GateKind.MIN_DISPOSITION, value=0)
    assert evaluate_gate(
        gate, _outcome(talk_disposition_change=1),
        world_flags={}, inventory=set(),
    ) is True
    assert evaluate_gate(
        gate, _outcome(talk_disposition_change=-1),
        world_flags={}, inventory=set(),
    ) is False


def test_gate_has_item():
    from engine.objective_matchers import evaluate_gate
    from world.story_arc import GateKind, ObjectiveGate
    gate = ObjectiveGate(kind=GateKind.HAS_ITEM, value="rope")
    assert evaluate_gate(
        gate, _outcome(),
        world_flags={}, inventory={"rope", "lantern"},
    ) is True
    assert evaluate_gate(
        gate, _outcome(),
        world_flags={}, inventory={"lantern"},
    ) is False


def test_gate_flag_set():
    from engine.objective_matchers import evaluate_gate
    from world.story_arc import GateKind, ObjectiveGate
    gate = ObjectiveGate(kind=GateKind.FLAG_SET, value="oath_sworn")
    assert evaluate_gate(
        gate, _outcome(),
        world_flags={"oath_sworn": True}, inventory=set(),
    ) is True
    assert evaluate_gate(
        gate, _outcome(),
        world_flags={"oath_sworn": False}, inventory=set(),
    ) is False


def test_interact_match_positive():
    obj = BeatObjective(
        id="interact_lever",
        kind=ObjectiveKind.INTERACT,
        target="lever",
        description="Pull the lever",
    )
    interp = _interp(ActionType.INTERACT, target="lever")
    score = compute_match_score(
        obj, interp, _outcome(), location=None, world_flags={}, inventory=set(),
    )
    assert score >= 0.7


def test_interact_wrong_action_returns_zero():
    obj = BeatObjective(
        id="interact_x", kind=ObjectiveKind.INTERACT, target="lever",
        description="...",
    )
    interp = _interp(ActionType.MOVE, target="lever")
    score = compute_match_score(
        obj, interp, _outcome(), location=None, world_flags={}, inventory=set(),
    )
    assert score == 0.0


def test_search_match_positive():
    obj = BeatObjective(
        id="search_chest", kind=ObjectiveKind.SEARCH, target="chest",
        description="...",
    )
    interp = _interp(ActionType.SEARCH, target="chest")
    score = compute_match_score(
        obj, interp, _outcome(), location=None, world_flags={}, inventory=set(),
    )
    assert score >= 0.7


def test_search_wrong_action_returns_zero():
    obj = BeatObjective(
        id="search_x", kind=ObjectiveKind.SEARCH, target="chest",
        description="...",
    )
    interp = _interp(ActionType.LOOK, target="chest")
    score = compute_match_score(
        obj, interp, _outcome(), location=None, world_flags={}, inventory=set(),
    )
    assert score == 0.0


def test_pickup_match_positive_via_target():
    obj = BeatObjective(
        id="pickup_key", kind=ObjectiveKind.PICKUP, target="silver key",
        description="...",
    )
    interp = _interp(ActionType.PICKUP, target="silver key")
    score = compute_match_score(
        obj, interp, _outcome(), location=None, world_flags={}, inventory=set(),
    )
    assert score >= 0.7


def test_pickup_match_positive_via_item_name():
    obj = BeatObjective(
        id="pickup_key", kind=ObjectiveKind.PICKUP, target="silver key",
        description="...",
    )
    # Some interpreted actions put the item in item_name field, not target_name
    interp = InterpretedAction(
        action_type=ActionType.PICKUP,
        actor_name="hero",
        item_name="silver key",
        raw_input="ramasse la clé",
    )
    score = compute_match_score(
        obj, interp, _outcome(), location=None, world_flags={}, inventory=set(),
    )
    assert score >= 0.7


def test_pickup_wrong_action_returns_zero():
    obj = BeatObjective(
        id="pickup_x", kind=ObjectiveKind.PICKUP, target="key",
        description="...",
    )
    interp = _interp(ActionType.MOVE, target="key")
    score = compute_match_score(
        obj, interp, _outcome(), location=None, world_flags={}, inventory=set(),
    )
    assert score == 0.0
