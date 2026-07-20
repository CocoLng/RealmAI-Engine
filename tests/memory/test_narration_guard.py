"""Tests for memory/narration_guard.py — deterministic post-narration checks.

The guard holds per-campaign state (dead NPCs, recent narrations) in a
module-level registry — same pattern as the drift tracker — because the
narration call site only knows the campaign_id.
"""

import pytest

from memory import narration_guard
from memory.coherence_rules import CoherenceSnapshot
from memory.narration_guard import GuardVerdict, check_narration


@pytest.fixture(autouse=True)
def _reset_guard():
    narration_guard.reset("camp-guard")
    yield
    narration_guard.reset("camp-guard")


class TestDeadNpcViolations:
    def test_dead_npc_named_in_narrative(self) -> None:
        narration_guard.set_dead_npcs("camp-guard", ["Grim"])
        violations = narration_guard.find_dead_npc_violations(
            "camp-guard",
            narrative="Grim vous tend une chope de bière fraîche.",
            npcs_mentioned=[],
        )
        assert violations == ["Grim"]

    def test_dead_npc_in_npcs_mentioned(self) -> None:
        narration_guard.set_dead_npcs("camp-guard", ["Grim"])
        violations = narration_guard.find_dead_npc_violations(
            "camp-guard",
            narrative="Le tavernier vous salue.",
            npcs_mentioned=["Grim"],
        )
        assert violations == ["Grim"]

    def test_multiword_name_matches_first_word(self) -> None:
        """The narrator often uses the short form of a multi-word name."""
        narration_guard.set_dead_npcs("camp-guard", ["Père Aldric"])
        violations = narration_guard.find_dead_npc_violations(
            "camp-guard",
            narrative="Aldric murmure une prière en vous voyant.",
            npcs_mentioned=[],
        )
        assert violations == ["Père Aldric"]

    def test_case_insensitive(self) -> None:
        narration_guard.set_dead_npcs("camp-guard", ["Grim"])
        violations = narration_guard.find_dead_npc_violations(
            "camp-guard",
            narrative="GRIM ATTAQUE sans prévenir.",
            npcs_mentioned=[],
        )
        assert violations == ["Grim"]

    def test_mention_without_active_verb_does_not_flag(self) -> None:
        """New contract (spec §1.2): mentioning the corpse is legitimate —
        only an active verb in the same sentence, or a self-reported
        mention, counts as a violation."""
        narration_guard.set_dead_npcs("camp-guard", ["Aldric"])
        violations = narration_guard.find_dead_npc_violations(
            "camp-guard",
            narrative="Le cadavre d'Aldric gît là.",
            npcs_mentioned=[],
        )
        assert violations == []

    def test_active_verb_flags_the_dead_npc(self) -> None:
        narration_guard.set_dead_npcs("camp-guard", ["Aldric"])
        violations = narration_guard.find_dead_npc_violations(
            "camp-guard",
            narrative="Aldric sourit.",
            npcs_mentioned=[],
        )
        assert violations == ["Aldric"]

    def test_no_partial_word_match(self) -> None:
        """'Grim' must not match inside 'Grimoire'."""
        narration_guard.set_dead_npcs("camp-guard", ["Grim"])
        violations = narration_guard.find_dead_npc_violations(
            "camp-guard",
            narrative="Vous ouvrez le Grimoire poussiéreux.",
            npcs_mentioned=[],
        )
        assert violations == []

    def test_alive_npcs_do_not_trigger(self) -> None:
        narration_guard.set_dead_npcs("camp-guard", [])
        violations = narration_guard.find_dead_npc_violations(
            "camp-guard",
            narrative="Grim vous salue.",
            npcs_mentioned=["Grim"],
        )
        assert violations == []

    def test_unknown_campaign_is_silent(self) -> None:
        violations = narration_guard.find_dead_npc_violations(
            "camp-inconnu",
            narrative="Grim vous salue.",
            npcs_mentioned=[],
        )
        assert violations == []


_LONG_SENTENCE = (
    "Les ombres dansent sur les murs de pierre tandis que la torche "
    "crachote dans l'air humide de la crypte abandonnée."
)


class TestRepetitionGuard:
    """Anti-monotony: a narration repeating ≥8 contiguous words from one
    of the last 2 narrations is flagged (mirrors the simulator's
    R2.repetition rule)."""

    def test_detects_verbatim_repetition(self) -> None:
        narration_guard.record_narration("camp-guard", _LONG_SENTENCE)
        snippet = narration_guard.find_repetition(
            "camp-guard",
            "Au détour du couloir, " + _LONG_SENTENCE,
        )
        assert snippet is not None
        assert "ombres dansent" in snippet

    def test_short_overlap_is_fine(self) -> None:
        narration_guard.record_narration("camp-guard", _LONG_SENTENCE)
        snippet = narration_guard.find_repetition(
            "camp-guard",
            "Les ombres dansent sur le sol, mais tout le reste est différent ici.",
        )
        assert snippet is None

    def test_only_last_two_narrations_kept(self) -> None:
        narration_guard.record_narration("camp-guard", _LONG_SENTENCE)
        narration_guard.record_narration("camp-guard", "Deuxième narration sans rapport aucun.")
        narration_guard.record_narration("camp-guard", "Troisième narration tout aussi originale.")
        # find_repetition's legacy BLOCKING check only compares against the
        # last 2 — the first narration has rolled out of that comparison
        # window (it is still held by the deque itself, now sized 5 for
        # the core's R2.repetition/OBSERVE via check_narration).
        assert narration_guard.find_repetition("camp-guard", _LONG_SENTENCE) is None

    def test_unknown_campaign_is_silent(self) -> None:
        assert narration_guard.find_repetition("camp-inconnu", _LONG_SENTENCE) is None

    def test_empty_narration_not_recorded(self) -> None:
        narration_guard.record_narration("camp-guard", "   ")
        assert narration_guard.find_repetition("camp-guard", _LONG_SENTENCE) is None


class TestCheckNarration:
    def test_blocking_and_observed_are_split_by_mode(self) -> None:
        narration_guard.reset("c1")
        narration_guard.set_dead_npcs("c1", ["Aldric"])
        snap = CoherenceSnapshot(known_npc_names=["Elara"])
        verdict = check_narration(
            "c1",
            narrative="Aldric sourit tandis que Baldur observe.",
            snapshot=snap,
            npcs_mentioned=[],
        )
        assert isinstance(verdict, GuardVerdict)
        assert [v.rule for v in verdict.blocking] == ["R1.npc_status"]
        assert "R1.phantom_npc" in {v.rule for v in verdict.observed}

    def test_guard_state_merges_into_snapshot(self) -> None:
        # dead_npcs du registre + recent_narrations de la deque sont fusionnés
        # même quand le snapshot fourni est vide.
        narration_guard.reset("c2")
        narration_guard.set_dead_npcs("c2", ["Mira"])
        verdict = check_narration(
            "c2", narrative="Mira attaque sans hésiter.",
            snapshot=None, npcs_mentioned=[],
        )
        assert [v.rule for v in verdict.blocking] == ["R1.npc_status"]

    def test_clean_narration_yields_empty_verdict(self) -> None:
        narration_guard.reset("c3")
        verdict = check_narration(
            "c3", narrative="Le vent souffle.", snapshot=None, npcs_mentioned=[],
        )
        assert verdict.blocking == [] and verdict.observed == []


class TestRecentNarrationsWindow:
    def test_deque_keeps_five_but_find_repetition_checks_last_two(self) -> None:
        narration_guard.reset("c4")
        eight = "un deux trois quatre cinq six sept huit"
        narration_guard.record_narration("c4", eight)          # n-3
        narration_guard.record_narration("c4", "toto")          # n-2
        narration_guard.record_narration("c4", "titi")          # n-1
        # La répétition vs n-3 n'est PLUS bloquante (fenêtre legacy = 2)…
        assert narration_guard.find_repetition("c4", eight) is None
        # …mais reste visible du noyau via check_narration (R2 en OBSERVE).
        verdict = check_narration(
            "c4", narrative=eight, snapshot=None, npcs_mentioned=[],
        )
        assert "R2.repetition" in {v.rule for v in verdict.observed}
