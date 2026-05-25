"""Tests for tests/simulation/rules/soft.py — R2.* heuristics."""

from __future__ import annotations

from dataclasses import dataclass, field

from tests.simulation.rules.soft import (
    check_npc_name_drift,
    check_repetition,
    check_tense_drift,
    check_unknown_proper_noun,
)


@dataclass
class FakeNPC:
    name: str


@dataclass
class FakeState:
    npcs: dict[str, FakeNPC] = field(default_factory=dict)
    player_names: list[str] = field(default_factory=list)
    locations_known: list[str] = field(default_factory=list)
    factions_known: list[str] = field(default_factory=list)
    current_turn: int = 0


class TestR2Repetition:
    def test_identical_phrase_in_window_triggers(self) -> None:
        history = [
            {"narration": "L'air est lourd de menaces dans cette pièce sombre"},
            {"narration": "Le héros entre."},
            {"narration": "L'air est lourd de menaces dans cette pièce sombre"},
        ]
        narration = "L'air est lourd de menaces dans cette pièce sombre"
        alerts = check_repetition(narration, FakeState(), diff={}, history=history)
        assert len(alerts) == 1
        assert alerts[0].rule == "R2.repetition"

    def test_distinct_narration_no_trigger(self) -> None:
        history = [{"narration": "Le héros entre."}, {"narration": "Il regarde autour."}]
        narration = "Une chouette ulule dans la nuit."
        alerts = check_repetition(narration, FakeState(), diff={}, history=history)
        assert alerts == []


class TestR2NpcNameDrift:
    def test_levenshtein_close_match_triggers(self) -> None:
        state = FakeState(npcs={"Garm": FakeNPC("Garm")})
        narration = "Gorm hoche la tête."
        alerts = check_npc_name_drift(narration, state, diff={}, history=[])
        assert len(alerts) == 1
        assert alerts[0].rule == "R2.npc_name_drift"

    def test_exact_match_no_trigger(self) -> None:
        state = FakeState(npcs={"Garm": FakeNPC("Garm")})
        narration = "Garm hoche la tête."
        alerts = check_npc_name_drift(narration, state, diff={}, history=[])
        assert alerts == []

    def test_far_match_no_trigger(self) -> None:
        state = FakeState(npcs={"Garm": FakeNPC("Garm")})
        narration = "Khaalim hoche la tête."
        alerts = check_npc_name_drift(narration, state, diff={}, history=[])
        assert alerts == []

    def test_first_word_of_multi_word_name_no_drift(self) -> None:
        # Registry holds "Elara, la Gardienne des Marbres" — narration uses the
        # short form "Elara". Must be treated as canonical, not drift.
        state = FakeState(npcs={"Elara, la Gardienne des Marbres": FakeNPC(
            "Elara, la Gardienne des Marbres",
        )})
        narration = "Elara hoche la tête."
        alerts = check_npc_name_drift(narration, state, diff={}, history=[])
        assert alerts == []


class TestR2TenseDrift:
    def test_mixed_tense_in_one_sentence_triggers(self) -> None:
        narration = "Le héros a marché vers la grotte et regarde l'entrée."
        # "a marché" = passé composé, "regarde" = présent
        alerts = check_tense_drift(narration, FakeState(), diff={}, history=[])
        assert len(alerts) == 1

    def test_consistent_tense_no_trigger(self) -> None:
        narration = "Le héros marche vers la grotte et regarde l'entrée."
        alerts = check_tense_drift(narration, FakeState(), diff={}, history=[])
        assert alerts == []


class TestR2UnknownProperNoun:
    def test_unknown_capitalized_word_triggers(self) -> None:
        state = FakeState(
            npcs={"Garm": FakeNPC("Garm")},
            locations_known=["Cave entrance"],
            factions_known=["Order of the Phoenix"],
        )
        narration = "Le héros aperçoit le Volcanus au loin."
        alerts = check_unknown_proper_noun(narration, state, diff={}, history=[])
        assert any("Volcanus" in a.expected for a in alerts)

    def test_known_words_no_trigger(self) -> None:
        state = FakeState(
            npcs={"Garm": FakeNPC("Garm")},
            locations_known=["Cave entrance"],
            factions_known=["Order"],
        )
        narration = "Garm parle d'Order et de Cave entrance."
        alerts = check_unknown_proper_noun(narration, state, diff={}, history=[])
        assert alerts == []
