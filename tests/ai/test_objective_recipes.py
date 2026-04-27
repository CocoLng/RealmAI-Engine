"""Tests for ai.objective_recipes — per-beat-type calibration table."""

from __future__ import annotations

import pytest

from ai.objective_recipes import (
    _slugify,
    all_recipes,
    get_recipe,
    scaffold_objectives,
)
from world.story_arc import (
    AdvanceRule,
    GateKind,
    ObjectiveKind,
)


# ---------------------------------------------------------------------------
# Recipe table coverage
# ---------------------------------------------------------------------------


def test_every_known_subtype_has_a_recipe() -> None:
    """The recipe table covers every (encounter_type, subtype) combo emitted
    by the arc recipes engine."""
    from engine.arc_recipes import BEAT_SUBTYPES

    recipes = all_recipes()
    for beat_type, subtypes in BEAT_SUBTYPES.items():
        # The wildcard (type, None) must always exist as a fallback.
        assert (beat_type.value, None) in recipes, (
            f"Missing wildcard recipe for ({beat_type.value}, None)"
        )
        for subtype in subtypes:
            # Either an exact subtype recipe exists, or the wildcard absorbs it.
            key_exact = (beat_type.value, subtype)
            assert key_exact in recipes or (beat_type.value, None) in recipes, (
                f"No recipe for {beat_type.value}/{subtype}"
            )


def test_get_recipe_falls_back_to_wildcard_on_unknown_subtype() -> None:
    """Unknown subtype routes to the (type, None) wildcard."""
    recipe = get_recipe("social", "totally_invented_subtype")
    assert recipe.encounter_type == "social"
    assert recipe.encounter_subtype is None


def test_get_recipe_unknown_type_falls_back_to_social_wildcard() -> None:
    """Unknown encounter type degrades to the social wildcard (safe default)."""
    recipe = get_recipe("interpretive_dance", None)
    assert recipe.encounter_type == "social"


# ---------------------------------------------------------------------------
# Per-recipe shape sanity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "subtype,expected_gate_kind,expected_value",
    [
        ("negotiation", GateKind.MIN_REVEALS, 2),
        ("interrogation", GateKind.MIN_REVEALS, 3),
        ("seduction", GateKind.MIN_DISPOSITION, 2),
        ("deception", GateKind.MIN_REVEALS, 2),
    ],
)
def test_social_subtypes_have_calibrated_gates(
    subtype: str, expected_gate_kind: GateKind, expected_value: int,
) -> None:
    recipe = get_recipe("social", subtype)
    primary = recipe.objectives[0]
    assert primary.kind == ObjectiveKind.TALK
    assert primary.gate_kind == expected_gate_kind
    assert primary.gate_value == expected_value


def test_social_negotiation_has_secondary_disposition_objective() -> None:
    """Negotiation should have BOTH MIN_REVEALS (primary) and MIN_DISPOSITION (secondary)."""
    recipe = get_recipe("social", "negotiation")
    assert len(recipe.objectives) >= 2
    gate_kinds = {o.gate_kind for o in recipe.objectives}
    assert GateKind.MIN_REVEALS in gate_kinds
    assert GateKind.MIN_DISPOSITION in gate_kinds


def test_social_ceremony_has_explicit_flag_objective() -> None:
    """Ceremony beats demand an explicit player commit via FLAG kind."""
    recipe = get_recipe("social", "ceremony")
    flag_objs = [o for o in recipe.objectives if o.kind == ObjectiveKind.FLAG]
    assert flag_objs, "ceremony should require an explicit FLAG commit"


def test_combat_recipe_uses_defeat_with_no_gate() -> None:
    recipe = get_recipe("combat", None)
    assert len(recipe.objectives) == 1
    assert recipe.objectives[0].kind == ObjectiveKind.DEFEAT
    assert recipe.objectives[0].gate_kind is None


def test_boss_recipe_targets_villain() -> None:
    recipe = get_recipe("boss", None)
    assert recipe.objectives[0].kind == ObjectiveKind.DEFEAT
    assert recipe.objectives[0].target_source == "villain"


def test_exploration_discovery_uses_m_of_n() -> None:
    recipe = get_recipe("exploration", "discovery")
    assert recipe.advance_rule == AdvanceRule.M_OF_N
    assert recipe.advance_threshold == 2


def test_puzzle_riddle_has_flag_for_explicit_commit() -> None:
    """Riddles must require the player to explicitly state their solution."""
    recipe = get_recipe("puzzle", "riddle")
    flag_objs = [o for o in recipe.objectives if o.kind == ObjectiveKind.FLAG]
    assert flag_objs, "riddle should have FLAG kind for explicit answer"


def test_puzzle_ritual_uses_has_item_gate() -> None:
    recipe = get_recipe("puzzle", "ritual")
    primary = recipe.objectives[0]
    assert primary.gate_kind == GateKind.HAS_ITEM


def test_puzzle_investigation_uses_m_of_n() -> None:
    recipe = get_recipe("puzzle", "investigation")
    assert recipe.advance_rule == AdvanceRule.M_OF_N
    assert recipe.advance_threshold == 2


# ---------------------------------------------------------------------------
# scaffold_objectives — runtime resolution
# ---------------------------------------------------------------------------


def test_scaffold_negotiation_uses_first_npc_as_target() -> None:
    objectives, rule, threshold, rubric, hint = scaffold_objectives(
        beat_number=1,
        encounter_type="social",
        encounter_subtype="negotiation",
        npc_names=["Lady Veyra", "le héraut"],
        villain_name="Vellus",
        location_hint="la salle du Trône",
        beat_title="L'Audience",
    )
    primary = objectives[0]
    assert primary.kind == ObjectiveKind.TALK
    assert primary.target == "Lady Veyra"
    assert primary.gate is not None
    assert primary.gate.kind == GateKind.MIN_REVEALS
    assert primary.gate.value == 2
    assert rule == AdvanceRule.ALL_REQUIRED
    assert threshold is None
    assert "négociation" in rubric.lower()
    assert hint  # non-empty


def test_scaffold_combat_uses_first_npc_as_target() -> None:
    objectives, _rule, _threshold, _rubric, _hint = scaffold_objectives(
        beat_number=2,
        encounter_type="combat",
        encounter_subtype="ambush",
        npc_names=["Brigand maître"],
        villain_name="Vellus",
        location_hint="route forestière",
        beat_title="Embuscade",
    )
    assert objectives[0].kind == ObjectiveKind.DEFEAT
    assert objectives[0].target == "Brigand maître"


def test_scaffold_boss_uses_villain_as_target() -> None:
    objectives, _rule, _threshold, rubric, _hint = scaffold_objectives(
        beat_number=10,
        encounter_type="boss",
        encounter_subtype="boss",
        npc_names=[],
        villain_name="Vellus l'Ombre",
        location_hint="le sanctum",
        beat_title="Confrontation",
    )
    assert objectives[0].kind == ObjectiveKind.DEFEAT
    assert objectives[0].target == "Vellus l'Ombre"
    assert "villain" in rubric.lower() or "antagoniste" in rubric.lower()


def test_scaffold_exploration_uses_location_as_target() -> None:
    objectives, _rule, _threshold, _rubric, _hint = scaffold_objectives(
        beat_number=3,
        encounter_type="exploration",
        encounter_subtype="navigation",
        npc_names=[],
        villain_name="X",
        location_hint="Pic des Cendres",
        beat_title="Ascension",
    )
    assert objectives[0].kind == ObjectiveKind.ARRIVE
    assert objectives[0].target == "Pic des Cendres"


def test_scaffold_puzzle_riddle_two_objectives_all_required() -> None:
    objectives, rule, _threshold, rubric, _hint = scaffold_objectives(
        beat_number=4,
        encounter_type="puzzle",
        encounter_subtype="riddle",
        npc_names=[],
        villain_name="X",
        location_hint="cour intérieure",
        beat_title="Le Sphinx silencieux",
    )
    assert len(objectives) == 2
    assert rule == AdvanceRule.ALL_REQUIRED
    kinds = {o.kind for o in objectives}
    assert ObjectiveKind.INTERACT in kinds
    assert ObjectiveKind.FLAG in kinds
    flag_obj = next(o for o in objectives if o.kind == ObjectiveKind.FLAG)
    assert flag_obj.target == "riddle_solved"


def test_scaffold_objective_ids_are_unique_per_beat() -> None:
    objectives, *_ = scaffold_objectives(
        beat_number=5,
        encounter_type="social",
        encounter_subtype="negotiation",
        npc_names=["Solène"],
        villain_name="X",
        location_hint="cour",
        beat_title="Audience",
    )
    ids = [o.id for o in objectives]
    assert len(ids) == len(set(ids)), "objective ids must be unique within a beat"


def test_scaffold_objective_ids_include_beat_number() -> None:
    objectives, *_ = scaffold_objectives(
        beat_number=7,
        encounter_type="social",
        encounter_subtype="negotiation",
        npc_names=["X"],
        villain_name="V",
        location_hint="L",
        beat_title="T",
    )
    for obj in objectives:
        assert obj.id.startswith("b7_"), f"id {obj.id} should start with b7_"


def test_scaffold_handles_empty_npc_names_for_social() -> None:
    """Social beat without npc_names — fall back to a generic placeholder."""
    objectives, *_ = scaffold_objectives(
        beat_number=1,
        encounter_type="social",
        encounter_subtype="negotiation",
        npc_names=[],
        villain_name="X",
        location_hint="L",
        beat_title="T",
    )
    assert objectives[0].target  # non-empty placeholder


def test_scaffold_judge_rubric_and_hint_are_non_empty() -> None:
    """All recipes provide rubric + hint so /hint and BeatJudge get context."""
    for (etype, subtype), recipe in all_recipes().items():
        assert recipe.judge_rubric, f"empty rubric for {etype}/{subtype}"
        assert recipe.player_visible_hint, f"empty hint for {etype}/{subtype}"


def test_scaffold_descriptions_are_substituted() -> None:
    """Description templates {target} placeholders are filled in."""
    objectives, *_ = scaffold_objectives(
        beat_number=1,
        encounter_type="social",
        encounter_subtype="negotiation",
        npc_names=["Lady Veyra"],
        villain_name="X",
        location_hint="L",
        beat_title="T",
    )
    primary = objectives[0]
    assert "Lady Veyra" in primary.description
    assert "{target}" not in primary.description


# ---------------------------------------------------------------------------
# _slugify utility
# ---------------------------------------------------------------------------


def test_slugify_strips_accents_and_punctuation() -> None:
    assert _slugify("Élise d'Aubépine") == "elise_d_aubepine"


def test_slugify_returns_safe_default_for_empty() -> None:
    assert _slugify("") == "x"
    assert _slugify("???") == "x"
