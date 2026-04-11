"""Tests for ai/narrator_phase.py — boss phase-transition narrator path (task 71)."""

from __future__ import annotations


from pytest_httpx import HTTPXMock

from ai.client import OllamaClient
from ai.narrator_phase import _build_phase_context, narrate_phase_transition
from engine.character import (
    AbilityScores,
    CharacterClass,
    Race,
    create_character,
)
from engine.combat import (
    Combatant,
    CombatSide,
    CombatState,
    PhaseTransitionEvent,
)
from engine.inventory import Inventory
from engine.npc_stat_block import NPCStatBlock, NPCTier
from tests.ai.conftest import CHAT_URL, make_ollama_response


def _build_boss_combatant() -> Combatant:
    char = create_character(
        name="Vellus",
        race=Race.HUMAN,
        char_class=CharacterClass.FIGHTER,
        ability_scores=AbilityScores(
            STR=16, DEX=14, CON=14, INT=12, WIS=14, CHA=16,
        ),
    )
    char.hp = 25
    char.max_hp = 55
    stat_block = NPCStatBlock(
        archetype="shadow_tyrant",
        tier=NPCTier.BOSS,
    )
    return Combatant(
        name="Vellus",
        side=CombatSide.ENEMY,
        character=char,
        inventory=Inventory(),
        stat_block=stat_block,
    )


def _build_state_with_boss(boss: Combatant, round_number: int = 4) -> CombatState:
    return CombatState(
        combatants=[boss],
        round_number=round_number,
        current_turn_index=0,
        is_active=True,
    )


def test_build_phase_context_embeds_cue_and_hp() -> None:
    boss = _build_boss_combatant()
    state = _build_state_with_boss(boss)
    event = PhaseTransitionEvent(
        combatant_name="Vellus",
        phase_index=1,
        narrative_cue="Ses yeux virent au blanc.",
    )
    ctx = _build_phase_context(event, boss, state)
    assert "Vellus" in ctx
    assert "25/55" in ctx
    assert "Round : 4" in ctx
    assert "Ses yeux virent au blanc." in ctx
    assert "3 à 5 phrases" in ctx


def test_build_phase_context_handles_empty_cue() -> None:
    boss = _build_boss_combatant()
    state = _build_state_with_boss(boss)
    event = PhaseTransitionEvent(combatant_name="Vellus", narrative_cue="")
    ctx = _build_phase_context(event, boss, state)
    assert "(pas de cue fourni)" in ctx


def test_narrate_phase_transition_returns_stripped_string(
    httpx_mock: HTTPXMock, ollama_client: OllamaClient,
) -> None:
    httpx_mock.add_response(
        url=CHAT_URL,
        json=make_ollama_response(
            {"narration": "  L'air devient noir.  Le silence tombe.  "},
        ),
    )
    boss = _build_boss_combatant()
    state = _build_state_with_boss(boss)
    event = PhaseTransitionEvent(
        combatant_name="Vellus",
        phase_index=1,
        narrative_cue="Ses yeux virent au blanc.",
    )
    out = narrate_phase_transition(ollama_client, event, boss, state)
    assert out == "L'air devient noir.  Le silence tombe."


def test_narrate_phase_transition_sends_dedicated_system_prompt(
    httpx_mock: HTTPXMock, ollama_client: OllamaClient,
) -> None:
    httpx_mock.add_response(
        url=CHAT_URL,
        json=make_ollama_response({"narration": "X"}),
    )
    boss = _build_boss_combatant()
    state = _build_state_with_boss(boss)
    event = PhaseTransitionEvent(
        combatant_name="Vellus", narrative_cue="A cue.",
    )
    narrate_phase_transition(ollama_client, event, boss, state)

    requests = httpx_mock.get_requests()
    # First request is /api/tags (health check), second is /api/chat.
    chat_request = next(r for r in requests if "api/chat" in str(r.url))
    body = chat_request.read().decode("utf-8")
    # System prompt must come from system_narrator_phase.txt, not the main one.
    assert "PHASE TRANSITION" in body or "phase transition" in body.lower()
    # The output schema must be wrapped as {"narration": "..."} — so the
    # prompt text must reference it.
    assert "narration" in body


def test_narrate_phase_transition_builds_language_prefix(
    httpx_mock: HTTPXMock, ollama_client: OllamaClient,
) -> None:
    httpx_mock.add_response(
        url=CHAT_URL,
        json=make_ollama_response({"narration": "Y"}),
    )
    boss = _build_boss_combatant()
    state = _build_state_with_boss(boss)
    event = PhaseTransitionEvent(combatant_name="Vellus", narrative_cue="A cue.")
    narrate_phase_transition(
        ollama_client, event, boss, state, language="en",
    )
    requests = httpx_mock.get_requests()
    chat_request = next(r for r in requests if "api/chat" in str(r.url))
    body = chat_request.read().decode("utf-8")
    # English prefix from language_instruction should appear.
    assert "English" in body
