"""Tests for Task 52 — NPCTactician (boss LLM brain).

Covers ``ai.npc_tactician.NPCTactician``: prompt assembly, JSON parsing,
post-validation of references (target, signature, weapon), and error
handling on malformed output. Uses ``pytest-httpx`` to mock Ollama.
"""

from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from ai.client import OllamaClient
from ai.models import TacticalDecision
from ai.npc_tactician import NPCTactician
from engine.character import (
    AbilityScores,
    CharacterClass,
    Race,
    apply_racial_bonuses,
    create_character,
)
from engine.combat import CombatSide, CombatState, Combatant
from engine.inventory import (
    DamageType,
    EquipmentSlot,
    ITEM_CATALOG,
    add_item,
    create_inventory,
    equip_item,
)
from engine.npc_stat_block import (
    BehaviorProfile,
    LegendaryAction,
    NPCAttack,
    NPCStatBlock,
    NPCTier,
    PhaseTransition,
    SignatureAbility,
    SignatureAbilityEffect,
)
from tests.ai.conftest import CHAT_URL, make_ollama_response


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_boss(name: str = "Vellus") -> Combatant:
    scores = AbilityScores(STR=14, DEX=14, CON=16, INT=16, WIS=14, CHA=16)
    char = create_character(name, Race.HUMAN, CharacterClass.WIZARD, scores)
    char.hp = 80
    char.max_hp = 80
    char.ac = 17
    inv = create_inventory()
    stat_block = NPCStatBlock(
        tier=NPCTier.BOSS,
        archetype="desert_sorcerer",
        multiattack_count=3,
        attacks=[
            NPCAttack(
                name="Sand Blade",
                damage_dice="1d8+3",
                damage_type=DamageType.SLASHING,
                to_hit_bonus=6,
            ),
        ],
        signature_abilities=[
            SignatureAbility(
                name="Silence Song",
                description="Muffle a zone for 2 rounds.",
                usage="per_combat",
                uses_remaining=1,
                effects=[
                    SignatureAbilityEffect(
                        kind="condition",
                        condition_name="Silenced",
                        target_scope="zone",
                    ),
                ],
            ),
            SignatureAbility(
                name="Dark Surge",
                description="Necrotic blast on a single target.",
                usage="per_combat",
                uses_remaining=2,
                effects=[
                    SignatureAbilityEffect(
                        kind="damage",
                        dice="4d8",
                        damage_type=DamageType.NECROTIC,
                        target_scope="single",
                    ),
                ],
            ),
        ],
        legendary_actions=[
            LegendaryAction(
                name="Shadow Strike",
                cost=1,
                description="Quick off-turn melee attack.",
                effects=[
                    SignatureAbilityEffect(
                        kind="damage",
                        dice="1d8+3",
                        damage_type=DamageType.SLASHING,
                        target_scope="single",
                    ),
                ],
            ),
        ],
        legendary_points_per_round=3,
        phases=[
            PhaseTransition(
                trigger_hp_percent=50,
                narrative_cue="The boss's eyes flare.",
                attack_bonus=2,
            ),
        ],
        behavior_profile=BehaviorProfile.TACTICAL,
    )
    return Combatant(
        name=name,
        side=CombatSide.ENEMY,
        character=char,
        inventory=inv,
        stat_block=stat_block,
        current_zone="Central",
    )


def _make_pc(name: str, hp: int = 25) -> Combatant:
    scores = AbilityScores(STR=14, DEX=12, CON=14, INT=10, WIS=10, CHA=10)
    scores = apply_racial_bonuses(scores, Race.HUMAN)
    char = create_character(name, Race.HUMAN, CharacterClass.FIGHTER, scores)
    char.hp = hp
    char.max_hp = max(hp, char.max_hp)
    char.ac = 15
    inv = create_inventory()
    inv = add_item(inv, ITEM_CATALOG["Longsword"])
    inv = equip_item(inv, "Longsword", EquipmentSlot.MAIN_HAND)
    return Combatant(
        name=name,
        side=CombatSide.PLAYER,
        character=char,
        inventory=inv,
        current_zone="Central",
    )


def _make_state(combatants: list[Combatant]) -> CombatState:
    return CombatState(combatants=combatants, round_number=2, current_turn_index=0)


@pytest.fixture
def tactician(ollama_client: OllamaClient) -> NPCTactician:
    return NPCTactician(ollama_client)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_tactician_parses_valid_json_decision(
    httpx_mock: HTTPXMock, tactician: NPCTactician
) -> None:
    boss = _make_boss()
    pc = _make_pc("Thorin")
    state = _make_state([boss, pc])

    response_data = {
        "action_type": "attack",
        "target_name": "Thorin",
        "weapon_name": "Sand Blade",
        "signature_name": None,
        "move_to_zone": None,
        "reasoning": "Thorin est la plus grande menace immédiate.",
        "legendary_action_name": None,
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    decision = tactician.decide(
        boss, state, party_context="Désert en plein jour.", recent_events=[],
    )

    assert isinstance(decision, TacticalDecision)
    assert decision.action_type == "attack"
    assert decision.target_name == "Thorin"
    assert decision.weapon_name == "Sand Blade"


def test_tactician_rejects_unknown_target(
    httpx_mock: HTTPXMock, tactician: NPCTactician
) -> None:
    boss = _make_boss()
    pc = _make_pc("Thorin")
    state = _make_state([boss, pc])

    response_data = {
        "action_type": "attack",
        "target_name": "Nobody",  # doesn't exist
        "weapon_name": "Sand Blade",
        "signature_name": None,
        "move_to_zone": None,
        "reasoning": "Invalid target reference.",
        "legendary_action_name": None,
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    with pytest.raises(ValueError, match="unknown combatant"):
        tactician.decide(boss, state, party_context="", recent_events=[])


def test_tactician_rejects_unknown_signature(
    httpx_mock: HTTPXMock, tactician: NPCTactician
) -> None:
    boss = _make_boss()
    pc = _make_pc("Thorin")
    state = _make_state([boss, pc])

    response_data = {
        "action_type": "signature",
        "target_name": "Thorin",
        "weapon_name": None,
        "signature_name": "Fireball",  # doesn't exist on stat block
        "move_to_zone": None,
        "reasoning": "Mock signature that isn't on the stat block.",
        "legendary_action_name": None,
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    with pytest.raises(ValueError, match="unknown signature"):
        tactician.decide(boss, state, party_context="", recent_events=[])


def test_tactician_rejects_unknown_weapon(
    httpx_mock: HTTPXMock, tactician: NPCTactician
) -> None:
    boss = _make_boss()
    pc = _make_pc("Thorin")
    state = _make_state([boss, pc])

    response_data = {
        "action_type": "attack",
        "target_name": "Thorin",
        "weapon_name": "Shortbow",  # not on stat block
        "signature_name": None,
        "move_to_zone": None,
        "reasoning": "Invalid weapon.",
        "legendary_action_name": None,
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    with pytest.raises(ValueError, match="unknown attack"):
        tactician.decide(boss, state, party_context="", recent_events=[])


def test_tactician_rejects_malformed_json(
    httpx_mock: HTTPXMock, tactician: NPCTactician
) -> None:
    boss = _make_boss()
    pc = _make_pc("Thorin")
    state = _make_state([boss, pc])

    # Missing required fields (reasoning min_length=5)
    response_data = {
        "action_type": "attack",
        "target_name": "Thorin",
        "reasoning": "x",  # too short
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    with pytest.raises(ValueError, match="schema validation"):
        tactician.decide(boss, state, party_context="", recent_events=[])


def test_tactician_builds_context_with_stat_block(
    tactician: NPCTactician,
) -> None:
    """Sanity check the prompt context includes the key stat-block fields."""
    boss = _make_boss()
    pc = _make_pc("Thorin")
    state = _make_state([boss, pc])

    ctx = tactician._build_context(
        boss, state, party_context="test party", recent_events=["event1", "event2"],
    )

    assert "Vellus" in ctx
    assert "Sand Blade" in ctx  # attack
    assert "Silence Song" in ctx  # signature
    assert "Dark Surge" in ctx  # signature
    assert "Thorin" in ctx  # enemy
    assert "test party" in ctx  # party context
    assert "event1" in ctx  # recent event
