"""Tests for bot/combat_turn_manager.py (task 64).

Covers the turn lifecycle primitives in isolation: hub upsert, NPC brain
dispatch, timeout cancellation, finalize XP stub, and the auto-Dodge
timeout path. Discord interactions are mocked — the TurnManager's job is
orchestration, not Discord protocol details.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.combat_turn_manager import TurnManager
from engine.character import (
    AbilityScores,
    CharacterClass,
    Race,
    create_character,
)
from engine.combat import (
    CombatEndReason,
    CombatSide,
    CombatState,
    Combatant,
)
from engine.combat_trigger import (
    CombatTrigger,
    CombatTriggerKind,
    InitiativeSide,
)
from engine.inventory import DamageType, create_inventory
from engine.npc_stat_block import NPCAttack, NPCStatBlock, NPCTier
from engine.validators import ActionType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _pc(name: str = "Aragorn", hp: int = 40) -> Combatant:
    char = create_character(
        name=name,
        race=Race.HUMAN,
        char_class=CharacterClass.FIGHTER,
        ability_scores=AbilityScores(
            STR=16, DEX=14, CON=14, INT=10, WIS=12, CHA=10,
        ),
    )
    char.hp = hp
    char.max_hp = hp
    return Combatant(
        name=name,
        side=CombatSide.PLAYER,
        character=char,
        inventory=create_inventory(),
        initiative=18,
    )


def _minion_statblock() -> NPCStatBlock:
    return NPCStatBlock(
        tier=NPCTier.MINION,
        archetype="goblin",
        attacks=[
            NPCAttack(
                name="Griffe",
                to_hit_bonus=3,
                damage_dice="1d6",
                damage_type=DamageType.SLASHING,
                range_type="melee",
            ),
        ],
    )


def _boss_statblock() -> NPCStatBlock:
    return NPCStatBlock(
        tier=NPCTier.BOSS,
        archetype="dragon",
        attacks=[
            NPCAttack(
                name="Morsure",
                to_hit_bonus=8,
                damage_dice="2d10+5",
                damage_type=DamageType.PIERCING,
                range_type="melee",
            ),
        ],
    )


def _enemy(
    name: str = "Gobelin",
    hp: int = 12,
    *,
    tier: NPCTier = NPCTier.MINION,
) -> Combatant:
    char = create_character(
        name=name,
        race=Race.HUMAN,
        char_class=CharacterClass.FIGHTER,
        ability_scores=AbilityScores(
            STR=10, DEX=14, CON=10, INT=8, WIS=8, CHA=8,
        ),
    )
    char.hp = hp
    char.max_hp = hp
    stat_block = _boss_statblock() if tier == NPCTier.BOSS else _minion_statblock()
    return Combatant(
        name=name,
        side=CombatSide.ENEMY,
        character=char,
        inventory=create_inventory(),
        initiative=10,
        stat_block=stat_block,
    )


def _state(combatants: list[Combatant], *, idx: int = 0) -> CombatState:
    return CombatState(
        combatants=combatants, round_number=1, current_turn_index=idx,
    )


def _fake_session(combatants: list[Combatant]) -> MagicMock:
    session = MagicMock()
    session.campaign = MagicMock()
    session.campaign.id = "test-campaign"
    session.combat_state = _state(combatants)
    session.combat_turn_manager = None
    session.current_location = None
    session.characters = {}
    for c in combatants:
        if c.side == CombatSide.PLAYER:
            session.characters[42] = c.character
    session.inventories = {42: create_inventory()}
    session.spellcasters = {42: None}
    session.language = "fr"
    session.narrator = None
    session.interpreter = None
    session.ollama_client = None
    session.story_bible = None  # record_turn_and_maybe_check tolerates None
    session.action_lock = asyncio.Lock()
    return session


def _fake_channel() -> MagicMock:
    channel = MagicMock()
    channel.send = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
    return channel


def _turn_manager(
    session: MagicMock,
    channel: MagicMock,
    pipeline_factory: object | None = None,
) -> TurnManager:
    return TurnManager(
        channel=channel,
        session=session,
        pipeline_factory=pipeline_factory or MagicMock(),
    )


# ---------------------------------------------------------------------------
# start() — posts the banner
# ---------------------------------------------------------------------------


class TestStart:
    @pytest.mark.asyncio
    async def test_start_posts_combat_start_banner(self) -> None:
        pc = _pc()
        enemy = _enemy()
        session = _fake_session([pc, enemy])
        channel = _fake_channel()
        tm = _turn_manager(session, channel)

        trigger = CombatTrigger(
            kind=CombatTriggerKind.PLAYER_ATTACK,
            aggressor_name="Aragorn",
            enemy_names=["Gobelin"],
            surprise_side=InitiativeSide.BOTH_READY,
        )
        await tm.start(trigger)

        channel.send.assert_awaited_once()
        args, kwargs = channel.send.await_args
        assert "embed" in kwargs
        assert kwargs["embed"].title == "⚔️ Combat commence"


# ---------------------------------------------------------------------------
# NPC brain dispatch
# ---------------------------------------------------------------------------


class TestNPCBrainDispatch:
    def test_minion_uses_scripted_brain(self) -> None:
        pc = _pc()
        goblin = _enemy(tier=NPCTier.MINION)
        session = _fake_session([pc, goblin])
        tm = _turn_manager(session, _fake_channel())

        plan = tm._dispatch_npc_brain(goblin, session.combat_state)
        assert plan.action_type in (ActionType.ATTACK, ActionType.DEFEND, ActionType.MOVE)

    def test_boss_falls_back_to_elite_without_ollama(self) -> None:
        pc = _pc()
        dragon = _enemy(name="Dragon", hp=80, tier=NPCTier.BOSS)
        session = _fake_session([pc, dragon])
        session.ollama_client = None
        tm = _turn_manager(session, _fake_channel())

        plan = tm._dispatch_npc_brain(dragon, session.combat_state)
        assert plan.action_type in (ActionType.ATTACK, ActionType.DEFEND, ActionType.MOVE)


# ---------------------------------------------------------------------------
# Timeout lifecycle
# ---------------------------------------------------------------------------


class TestTimeout:
    def test_cancel_timeout_is_idempotent(self) -> None:
        session = _fake_session([_pc()])
        tm = _turn_manager(session, _fake_channel())
        tm._cancel_timeout()  # no pending task
        assert tm.pending_timeout is None

    @pytest.mark.asyncio
    async def test_cancel_timeout_cancels_active_task(self) -> None:
        session = _fake_session([_pc()])
        tm = _turn_manager(session, _fake_channel())

        async def _long_sleep() -> None:
            await asyncio.sleep(60)

        tm.pending_timeout = asyncio.create_task(_long_sleep())
        tm._cancel_timeout()
        # Yield so the cancellation propagates.
        await asyncio.sleep(0)
        assert tm.pending_timeout is None


# ---------------------------------------------------------------------------
# Finalize — XP stub + session cleanup
# ---------------------------------------------------------------------------


class TestFinalize:
    @pytest.mark.asyncio
    async def test_finalize_clears_turn_manager_on_session(self) -> None:
        pc = _pc()
        goblin = _enemy()
        session = _fake_session([pc, goblin])
        session.combat_state.end_reason = CombatEndReason.VICTORY
        channel = _fake_channel()
        tm = _turn_manager(session, channel)
        session.combat_turn_manager = tm

        goblin.is_alive = False
        await tm._finalize()

        assert session.combat_turn_manager is None
        assert session.combat_state is None
        channel.send.assert_awaited()  # posted the closing XP line

    def test_xp_stub_awards_100_per_dead_enemy(self) -> None:
        pc = _pc()
        goblin1 = _enemy(name="Gobelin 1")
        goblin2 = _enemy(name="Gobelin 2")
        goblin1.is_alive = False
        goblin2.is_alive = False
        state = _state([pc, goblin1, goblin2])
        session = _fake_session([pc, goblin1, goblin2])
        session.combat_state = state
        tm = _turn_manager(session, _fake_channel())

        xp_each, level_ups = tm._apply_xp_stub(state)
        # 2 dead enemies × 100 / 1 survivor
        assert xp_each == 200
        assert isinstance(level_ups, list)

    def test_xp_stub_returns_zero_without_state(self) -> None:
        session = _fake_session([_pc()])
        tm = _turn_manager(session, _fake_channel())
        xp_each, level_ups = tm._apply_xp_stub(None)
        assert xp_each == 0
        assert level_ups == []


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------


class TestSessionHelpers:
    def test_find_user_id_matches_character_name(self) -> None:
        pc = _pc("Aragorn")
        session = _fake_session([pc])
        tm = _turn_manager(session, _fake_channel())
        assert tm._find_user_id("Aragorn") == 42
        assert tm._find_user_id("Legolas") is None

    def test_adjacent_zones_empty_without_location(self) -> None:
        pc = _pc()
        session = _fake_session([pc])
        session.current_location = None
        tm = _turn_manager(session, _fake_channel())
        assert tm._get_adjacent_zones(pc) == []
