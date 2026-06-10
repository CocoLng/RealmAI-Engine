"""Sanity checks on the narrator prompt files.

These tests exist to catch accidental removal of the combat awareness
block from ``system_narrator.txt``. They are deliberately shallow —
substring checks — because the prompt wording is not a stable API and
should not be over-specified.
"""

from __future__ import annotations

from pathlib import Path

_NARRATOR_PROMPT = (
    Path(__file__).resolve().parents[2] / "ai" / "prompts" / "system_narrator.txt"
)
_PHASE_PROMPT = (
    Path(__file__).resolve().parents[2]
    / "ai"
    / "prompts"
    / "system_narrator_phase.txt"
)


def test_narrator_prompt_contains_combat_active_section() -> None:
    text = _NARRATOR_PROMPT.read_text()
    assert "COMBAT ACTIVE" in text
    # Key behavioral anchors — if any of these regress, the narrator will
    # start hallucinating outcomes again.
    assert "miss" in text.lower()
    assert "invitation" in text.lower() or "next turn" in text.lower()
    assert "vague" in text.lower() or "indemne" in text.lower()


def test_narrator_prompt_contains_acting_character_awareness() -> None:
    text = _NARRATOR_PROMPT.read_text()
    assert "Acting character" in text
    # The block must tell the narrator to use class / race / weapon.
    assert "race" in text.lower() and "class" in text.lower()
    assert "weapon" in text.lower()


def test_phase_prompt_exists_and_enforces_length() -> None:
    text = _PHASE_PROMPT.read_text()
    assert "3 to 5" in text or "3 à 5" in text or "3-5" in text
    assert "narration" in text.lower()
    # Output schema must be declared so the model returns JSON, not prose.
    assert "{" in text and "narration" in text


def test_narrator_prompt_has_no_bracketed_placeholder_examples() -> None:
    """H13 — small models parrot bracketed examples verbatim; the prompt
    must demonstrate the next-turn invitation with a concrete name."""
    text = _NARRATOR_PROMPT.read_text()
    assert "[nom]" not in text
    assert "[name]" not in text


def test_narrator_prompt_forbids_placeholders_explicitly() -> None:
    text = _NARRATOR_PROMPT.read_text()
    assert "placeholder" in text.lower()


def test_narrator_prompt_forbids_invented_enemy_damage() -> None:
    """H12 — the combat block must forbid narrating enemy ripostes/damage
    absent from the ActionResult."""
    text = _NARRATOR_PROMPT.read_text()
    assert "riposte" in text.lower() or "counter-attack" in text.lower()
    assert "NEVER invent an enemy attack" in text
