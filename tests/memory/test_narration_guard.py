"""Tests for memory/narration_guard.py — deterministic post-narration checks.

The guard holds per-campaign state (dead NPCs, recent narrations) in a
module-level registry — same pattern as the drift tracker — because the
narration call site only knows the campaign_id.
"""

import pytest

from memory import narration_guard


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
            narrative="GRIM se redresse lentement.",
            npcs_mentioned=[],
        )
        assert violations == ["Grim"]

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
