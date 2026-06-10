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

from ai.models import InterpretedAction
from bot.action_pipeline import ActionPipelineResult, UnknownEntityResult
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
    channel.send = AsyncMock(
        return_value=MagicMock(edit=AsyncMock(), delete=AsyncMock()),
    )
    return channel


def _turn_manager(
    session: MagicMock,
    channel: MagicMock,
    pipeline_factory: object | None = None,
    db_factory: object | None = None,
) -> TurnManager:
    return TurnManager(
        channel=channel,
        session=session,
        pipeline_factory=pipeline_factory or MagicMock(),
        db_factory=db_factory,
    )


def _pipeline_result(
    actor_name: str,
    action_type: ActionType = ActionType.ATTACK,
    *,
    is_question: bool = False,
    is_free_action: bool = False,
) -> ActionPipelineResult:
    return ActionPipelineResult(
        narrative="Quelque chose se passe.",
        tone="tense",
        mechanics_text="MECANIQUE",
        interpreted_action=InterpretedAction(
            action_type=action_type,
            actor_name=actor_name,
            raw_input="action de test",
        ),
        is_question=is_question,
        is_free_action=is_free_action,
    )


def _unknown_result(actor_name: str) -> UnknownEntityResult:
    return UnknownEntityResult(
        field_name="target_name",
        raw_value="le dragon invisible",
        partial_action=InterpretedAction(
            action_type=ActionType.ATTACK,
            actor_name=actor_name,
            raw_input="j'attaque le dragon invisible",
        ),
        refusal_narrative="Tu ne vois rien de tel ici.",
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
    async def test_finalize_clears_turn_manager_and_preserves_state(
        self,
    ) -> None:
        """``_finalize`` delegates to :func:`finalize_combat`, clears the
        turn manager from the session, but keeps ``session.combat_state``
        in place for history / inspection.
        """
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
        # combat_state is preserved after finalize, the next combat_entry
        # call resets it.
        assert session.combat_state is not None
        assert session.combat_state.is_active is False
        assert session.combat_state.end_reason == CombatEndReason.VICTORY
        channel.send.assert_awaited()  # posted the end embed

    @pytest.mark.asyncio
    async def test_finalize_applies_xp_via_combat_end(self) -> None:
        """XP is now applied by :func:`finalize_combat`, not by a local
        ``_apply_xp_stub``."""
        pc = _pc()
        goblin = _enemy(name="Gobelin")
        session = _fake_session([pc, goblin])
        session.combat_state.end_reason = CombatEndReason.VICTORY
        tm = _turn_manager(session, _fake_channel())
        session.combat_turn_manager = tm

        goblin.is_alive = False
        pc_xp_before = pc.character.xp
        await tm._finalize()

        # Minion tier = 50 XP / 1 survivor = 50.
        assert pc.character.xp == pc_xp_before + 50

    @pytest.mark.asyncio
    async def test_finalize_skips_embed_without_end_reason(self) -> None:
        """Degenerate case: _finalize called but end_reason is None —
        still clears the turn manager, no crash.
        """
        pc = _pc()
        session = _fake_session([pc])
        session.combat_state.end_reason = None
        tm = _turn_manager(session, _fake_channel())
        session.combat_turn_manager = tm

        await tm._finalize()

        assert session.combat_turn_manager is None


# ---------------------------------------------------------------------------
# Post-turn auto-checkpoint
# ---------------------------------------------------------------------------


class TestPersistState:
    @pytest.mark.asyncio
    async def test_persist_state_is_noop_without_db_factory(self) -> None:
        session = _fake_session([_pc()])
        tm = _turn_manager(session, _fake_channel(), db_factory=None)

        # Should not raise, nothing to assert — just confirming the guard.
        await tm._persist_state()

    @pytest.mark.asyncio
    async def test_persist_state_calls_persist_session_via_thread(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        session = _fake_session([_pc()])
        db_factory = MagicMock()
        call_count = {"n": 0}

        def fake_persist(factory, sess):
            call_count["n"] += 1
            assert factory is db_factory
            assert sess is session

        monkeypatch.setattr(
            "bot.combat_turn_manager.persist_session", fake_persist,
        )

        tm = _turn_manager(session, _fake_channel(), db_factory=db_factory)
        await tm._persist_state()

        assert call_count["n"] == 1

    @pytest.mark.asyncio
    async def test_persist_state_swallows_exceptions(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        session = _fake_session([_pc()])
        db_factory = MagicMock()

        def boom(factory, sess):
            raise RuntimeError("db unavailable")

        monkeypatch.setattr(
            "bot.combat_turn_manager.persist_session", boom,
        )

        tm = _turn_manager(session, _fake_channel(), db_factory=db_factory)
        # Must not raise — auto-checkpoint failure is logged and swallowed.
        await tm._persist_state()

    @pytest.mark.asyncio
    async def test_checkpoint_failure_warns_in_channel_once(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """M5 — players must learn their progress is not being saved, but
        only once per failure streak (no warning spam every turn)."""
        session = _fake_session([_pc()])
        channel = _fake_channel()

        monkeypatch.setattr(
            "bot.combat_turn_manager.persist_session",
            MagicMock(side_effect=RuntimeError("db unavailable")),
        )

        tm = _turn_manager(session, channel, db_factory=MagicMock())
        await tm._persist_state()
        await tm._persist_state()  # still failing — no second warning

        warnings = [
            call.kwargs.get("content", "") or ""
            for call in channel.send.await_args_list
            if "sauvegarde" in (call.kwargs.get("content", "") or "").lower()
        ]
        assert len(warnings) == 1, (
            f"expected exactly one checkpoint warning, got {warnings!r}"
        )

    @pytest.mark.asyncio
    async def test_checkpoint_dirty_flag_set_on_failure_cleared_on_success(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """M5 — a failed checkpoint flags the state dirty; the next
        successful checkpoint (which persists the full session anyway)
        clears it."""
        session = _fake_session([_pc()])
        calls = {"fail": True}

        def persist(factory, sess):
            if calls["fail"]:
                raise RuntimeError("db unavailable")

        monkeypatch.setattr(
            "bot.combat_turn_manager.persist_session", persist,
        )

        tm = _turn_manager(session, _fake_channel(), db_factory=MagicMock())
        assert tm.checkpoint_dirty is False

        await tm._persist_state()
        assert tm.checkpoint_dirty is True

        calls["fail"] = False
        await tm._persist_state()
        assert tm.checkpoint_dirty is False

    @pytest.mark.asyncio
    async def test_checkpoint_warns_again_after_recovery(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """M5 — a NEW failure streak after a successful checkpoint warns
        again (the once-only guard is per streak, not per combat)."""
        session = _fake_session([_pc()])
        channel = _fake_channel()
        calls = {"fail": True}

        def persist(factory, sess):
            if calls["fail"]:
                raise RuntimeError("db unavailable")

        monkeypatch.setattr(
            "bot.combat_turn_manager.persist_session", persist,
        )

        tm = _turn_manager(session, channel, db_factory=MagicMock())
        await tm._persist_state()   # fail → warn #1
        calls["fail"] = False
        await tm._persist_state()   # recovery
        calls["fail"] = True
        await tm._persist_state()   # new streak → warn #2

        warnings = [
            call.kwargs.get("content", "") or ""
            for call in channel.send.await_args_list
            if "sauvegarde" in (call.kwargs.get("content", "") or "").lower()
        ]
        assert len(warnings) == 2, (
            f"expected two checkpoint warnings, got {warnings!r}"
        )

    @pytest.mark.asyncio
    async def test_finalize_persists_final_state(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """_finalize must persist after finalize_combat so a reload
        doesn't resurrect a stale is_active=True state.
        """
        pc = _pc()
        goblin = _enemy()
        session = _fake_session([pc, goblin])
        session.combat_state.end_reason = CombatEndReason.VICTORY
        goblin.is_alive = False

        db_factory = MagicMock()
        persisted_states: list[bool] = []

        def capture(factory, sess):
            # Snapshot is_active at persist time.
            persisted_states.append(sess.combat_state.is_active)

        monkeypatch.setattr(
            "bot.combat_turn_manager.persist_session", capture,
        )

        tm = _turn_manager(session, _fake_channel(), db_factory=db_factory)
        session.combat_turn_manager = tm

        await tm._finalize()

        assert persisted_states == [False]


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


# ---------------------------------------------------------------------------
# _flush_pending_cues narrates phase transitions
# ---------------------------------------------------------------------------


class TestFlushPendingPhaseTransitions:
    @pytest.mark.asyncio
    async def test_phase_event_consumed_and_gold_embed_posted(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from engine.combat import PhaseTransitionEvent

        pc = _pc()
        boss = _enemy("Vellus", hp=20, tier=NPCTier.BOSS)
        session = _fake_session([pc, boss])
        # Feed a fake ollama client so the narrator path runs.
        session.ollama_client = MagicMock()

        # Patch the narrator to avoid hitting the LLM.
        monkeypatch.setattr(
            "bot.combat_turn_manager.narrate_phase_transition",
            MagicMock(return_value="L'air devient noir."),
        )

        session.combat_state.pending_phase_narrations.append(
            PhaseTransitionEvent(
                combatant_name="Vellus",
                phase_index=0,
                narrative_cue="Ses yeux virent au blanc.",
            )
        )

        channel = _fake_channel()
        tm = _turn_manager(session, channel)

        await tm._flush_pending_cues(session.combat_state)

        # The event must be consumed so subsequent flushes skip it.
        event = session.combat_state.pending_phase_narrations[0]
        assert event.consumed is True

        # A phase transition embed must have been posted (gold color, dedicated title).
        sends_with_embed = [
            call
            for call in channel.send.await_args_list
            if call.kwargs.get("embed") is not None
        ]
        phase_embeds = [
            c.kwargs["embed"]
            for c in sends_with_embed
            if c.kwargs["embed"].title and "Phase transition" in c.kwargs["embed"].title
        ]
        assert phase_embeds, "no phase transition embed posted"
        embed = phase_embeds[0]
        # Gold color 0xF1C40F — discord wraps this in a Colour object.
        assert embed.color is not None
        color_value = (
            embed.color.value if hasattr(embed.color, "value") else int(embed.color)
        )
        assert color_value == 0xF1C40F
        # The narrator output must be the embed body.
        assert "L'air devient noir." in (embed.description or "")

    @pytest.mark.asyncio
    async def test_phase_event_already_consumed_is_skipped(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from engine.combat import PhaseTransitionEvent

        pc = _pc()
        boss = _enemy("Vellus", hp=20, tier=NPCTier.BOSS)
        session = _fake_session([pc, boss])
        session.ollama_client = MagicMock()

        narrator_mock = MagicMock(return_value="…")
        monkeypatch.setattr(
            "bot.combat_turn_manager.narrate_phase_transition", narrator_mock,
        )

        session.combat_state.pending_phase_narrations.append(
            PhaseTransitionEvent(
                combatant_name="Vellus",
                phase_index=0,
                narrative_cue="Old cue.",
                consumed=True,
            )
        )

        channel = _fake_channel()
        tm = _turn_manager(session, channel)
        await tm._flush_pending_cues(session.combat_state)

        narrator_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_phase_event_marks_consumed_before_llm_call(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If the narrator raises, the event must still be flagged consumed
        so that subsequent flushes don't retry forever.
        """
        from engine.combat import PhaseTransitionEvent

        pc = _pc()
        boss = _enemy("Vellus", hp=20, tier=NPCTier.BOSS)
        session = _fake_session([pc, boss])
        session.ollama_client = MagicMock()

        def _raise(*args: object, **kwargs: object) -> str:
            raise RuntimeError("LLM down")

        monkeypatch.setattr(
            "bot.combat_turn_manager.narrate_phase_transition", _raise,
        )

        event = PhaseTransitionEvent(
            combatant_name="Vellus",
            phase_index=0,
            narrative_cue="A cue.",
        )
        session.combat_state.pending_phase_narrations.append(event)

        channel = _fake_channel()
        tm = _turn_manager(session, channel)

        # Must not propagate the RuntimeError.
        await tm._flush_pending_cues(session.combat_state)

        assert event.consumed is True
        # The raw cue must still have been surfaced as the embed body.
        sends_with_embed = [
            call
            for call in channel.send.await_args_list
            if call.kwargs.get("embed") is not None
        ]
        assert any(
            "A cue." in (c.kwargs["embed"].description or "")
            for c in sends_with_embed
        )

    @pytest.mark.asyncio
    async def test_phase_event_without_ollama_client_uses_raw_cue(self) -> None:
        from engine.combat import PhaseTransitionEvent

        pc = _pc()
        boss = _enemy("Vellus", hp=20, tier=NPCTier.BOSS)
        session = _fake_session([pc, boss])
        session.ollama_client = None  # no client → fallback to raw cue

        event = PhaseTransitionEvent(
            combatant_name="Vellus",
            phase_index=0,
            narrative_cue="Ses yeux virent au blanc.",
        )
        session.combat_state.pending_phase_narrations.append(event)

        channel = _fake_channel()
        tm = _turn_manager(session, channel)
        await tm._flush_pending_cues(session.combat_state)

        assert event.consumed is True
        sends_with_embed = [
            call
            for call in channel.send.await_args_list
            if call.kwargs.get("embed") is not None
        ]
        assert any(
            "Ses yeux virent au blanc." in (c.kwargs["embed"].description or "")
            for c in sends_with_embed
        )

    @pytest.mark.asyncio
    async def test_missing_boss_in_state_skips_without_crash(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from engine.combat import PhaseTransitionEvent

        pc = _pc()
        session = _fake_session([pc])
        session.ollama_client = MagicMock()

        narrator_mock = MagicMock(return_value="X")
        monkeypatch.setattr(
            "bot.combat_turn_manager.narrate_phase_transition", narrator_mock,
        )

        # Event points at a combatant that was never in the state.
        event = PhaseTransitionEvent(
            combatant_name="Nobody",
            narrative_cue="…",
        )
        session.combat_state.pending_phase_narrations.append(event)

        channel = _fake_channel()
        tm = _turn_manager(session, channel)
        await tm._flush_pending_cues(session.combat_state)

        assert event.consumed is True
        narrator_mock.assert_not_called()


# ---------------------------------------------------------------------------
# _flush_dice_embeds — attack_roll embed
# ---------------------------------------------------------------------------


class TestFlushDiceEmbedsAttackRoll:
    @pytest.mark.asyncio
    async def test_attack_roll_kind_calls_build_attack_roll_embed(self) -> None:
        """An 'attack_roll' entry in _pending_dice_embeds posts an attack embed."""
        from unittest.mock import MagicMock, patch

        from engine.combat import AttackResult
        from engine.dice import RollOutcome
        from engine.inventory import DamageType

        pc = _pc()
        enemy = _enemy()
        session = _fake_session([pc, enemy])
        channel = _fake_channel()
        tm = _turn_manager(session, channel)

        fake_result = AttackResult(
            attacker="Aragorn",
            defender="Gobelin",
            weapon_name="Longsword",
            attack_roll=15,
            attack_total=18,
            ac=12,
            hit=True,
            critical=False,
            outcome=RollOutcome.SUCCESS,
            damage=7,
            damage_type=DamageType.SLASHING,
            defender_hp_remaining=5,
        )

        # Build a fake pipeline stub with the entry queued.
        fake_pipeline = MagicMock()
        fake_pipeline._pending_dice_embeds = [("attack_roll", fake_result, "Aragorn")]

        # The flush goes through bot.embeds.dice_embed.embed_for_dice_entry,
        # which dispatches to build_attack_roll_embed for the "attack_roll"
        # tag.
        with patch(
            "bot.embeds.dice_embed.build_attack_roll_embed",
        ) as mock_builder:
            mock_builder.return_value = MagicMock()
            await tm._flush_dice_embeds(fake_pipeline, "Aragorn")

        mock_builder.assert_called_once_with(fake_result, "Aragorn")
        channel.send.assert_awaited_once()


# ---------------------------------------------------------------------------
# _upsert_hub — delete-and-repost instead of edit-in-place
# ---------------------------------------------------------------------------


class TestUpsertHub:
    @pytest.mark.asyncio
    async def test_first_upsert_sends_new_message(self) -> None:
        """When hub_message is None, _upsert_hub sends a new message."""
        import discord

        session = _fake_session([_pc(), _enemy()])
        channel = _fake_channel()
        tm = _turn_manager(session, channel)

        embed = discord.Embed(title="Combat")
        await tm._upsert_hub(content="test", embed=embed, view=None)

        channel.send.assert_awaited_once()
        assert tm.hub_message is not None

    @pytest.mark.asyncio
    async def test_second_upsert_deletes_old_and_sends_new(self) -> None:
        """When hub_message already exists, _upsert_hub deletes it then posts fresh."""
        import discord

        session = _fake_session([_pc(), _enemy()])
        channel = _fake_channel()
        tm = _turn_manager(session, channel)

        embed = discord.Embed(title="Combat")

        # First upsert sets hub_message
        await tm._upsert_hub(content="turn 1", embed=embed, view=None)
        first_message = tm.hub_message
        assert first_message is not None

        # Second upsert should delete the old message and send a new one
        await tm._upsert_hub(content="turn 2", embed=embed, view=None)

        first_message.delete.assert_awaited_once()
        assert channel.send.await_count == 2
        # hub_message is the result of the second send
        assert tm.hub_message is not None

    @pytest.mark.asyncio
    async def test_upsert_tolerates_discord_not_found_on_delete(self) -> None:
        """If the old hub message is already gone, delete failure is swallowed."""
        import discord

        session = _fake_session([_pc(), _enemy()])
        channel = _fake_channel()
        tm = _turn_manager(session, channel)

        embed = discord.Embed(title="Combat")
        await tm._upsert_hub(content="turn 1", embed=embed, view=None)

        # Simulate the old message having been deleted already
        old_message = tm.hub_message
        assert old_message is not None
        old_message.delete = AsyncMock(
            side_effect=discord.HTTPException(MagicMock(), "Unknown Message"),
        )

        # Should not raise — just swallow and repost
        await tm._upsert_hub(content="turn 2", embed=embed, view=None)

        old_message.delete.assert_awaited_once()
        # A new hub was still sent
        assert channel.send.await_count == 2


# ---------------------------------------------------------------------------
# Surprise skip (fix 3b)
# ---------------------------------------------------------------------------


class TestSurpriseSkip:
    """A surprised NPC's turn must be skipped without dispatching the brain."""

    def _apply_surprise(self, combatant: Combatant) -> None:
        from engine.conditions import ActiveCondition, ConditionType, apply_condition

        apply_condition(
            combatant.conditions,
            ActiveCondition(condition_type=ConditionType.SURPRISED),
        )

    @pytest.mark.asyncio
    async def test_surprised_npc_turn_skipped_cleanly(self) -> None:
        """NPC brain is not called; a 'surpris' message is posted; turn advances."""
        pc = _pc()
        goblin = _enemy(tier=NPCTier.MINION)
        self._apply_surprise(goblin)

        session = _fake_session([pc, goblin])
        channel = _fake_channel()
        tm = _turn_manager(session, channel)

        tm._dispatch_npc_brain = MagicMock()  # type: ignore[method-assign]
        tm.on_action_resolved = AsyncMock()  # type: ignore[method-assign]

        await tm._resolve_npc_turn(goblin)

        tm._dispatch_npc_brain.assert_not_called()
        tm.on_action_resolved.assert_awaited_once()

        posted = [call.kwargs.get("content", "") for call in channel.send.await_args_list]
        assert any("surpris" in (c or "").lower() for c in posted)

    @pytest.mark.asyncio
    async def test_surprised_npc_records_combat_event(self) -> None:
        """The surprise skip is exposed to the narrator via recent_events."""
        pc = _pc()
        goblin = _enemy(tier=NPCTier.MINION)
        self._apply_surprise(goblin)

        session = _fake_session([pc, goblin])
        channel = _fake_channel()
        tm = _turn_manager(session, channel)
        tm.on_action_resolved = AsyncMock()  # type: ignore[method-assign]

        await tm._resolve_npc_turn(goblin)

        state = session.combat_state
        assert state is not None
        assert any("surpris" in ev.lower() for ev in state.recent_events)


# ---------------------------------------------------------------------------
# NPC brain must not block the event loop (boss tier = synchronous LLM call)
# ---------------------------------------------------------------------------


class TestNPCBrainOffLoop:
    @pytest.mark.asyncio
    async def test_npc_brain_runs_off_the_event_loop(self) -> None:
        """Boss-tier decisions go through a synchronous httpx LLM call —
        run in-loop it freezes the whole bot (gateway heartbeat included)
        for the entire generation (audit H2, boss branch).

        The fake brain below blocks until a loop-side task manages to
        run: a synchronous in-loop call starves that task and times out;
        via ``asyncio.to_thread`` the loop stays free and sets the event.
        """
        import threading

        from engine.npc_ai.scripted import NPCActionPlan

        pc = _pc()
        goblin = _enemy(tier=NPCTier.MINION)
        session = _fake_session([pc, goblin])
        channel = _fake_channel()
        tm = _turn_manager(session, channel)
        tm.on_action_resolved = AsyncMock()  # type: ignore[method-assign]

        loop_alive = threading.Event()
        loop_ran_during_brain = {"value": False}

        def blocking_brain(combatant, state):  # type: ignore[no-untyped-def]
            loop_ran_during_brain["value"] = loop_alive.wait(timeout=1.0)
            return NPCActionPlan(action_type=ActionType.DEFEND, rationale="t")

        tm._dispatch_npc_brain = blocking_brain  # type: ignore[method-assign]

        async def _heartbeat() -> None:
            await asyncio.sleep(0.05)
            loop_alive.set()

        hb = asyncio.create_task(_heartbeat())
        await tm._resolve_npc_turn(goblin)
        await hb

        assert loop_ran_during_brain["value"], (
            "the NPC brain blocked the event loop — boss LLM calls must "
            "run via asyncio.to_thread"
        )


# ---------------------------------------------------------------------------
# H14 — NPC turn summaries must reach players clean (no internal tags)
# ---------------------------------------------------------------------------


class TestNPCSummarySanitization:
    @pytest.mark.asyncio
    async def test_internal_bracket_tags_stripped_before_send(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Defense in depth: even if an executor leaks an internal
        '[Tag] …' diagnostic into the summary, the TurnManager must strip
        it before posting / recording (audit H14)."""
        from engine.npc_ai.scripted import NPCActionPlan

        pc = _pc()
        goblin = _enemy(tier=NPCTier.MINION)
        session = _fake_session([pc, goblin])
        channel = _fake_channel()
        tm = _turn_manager(session, channel)
        tm.on_action_resolved = AsyncMock()  # type: ignore[method-assign]
        tm._dispatch_npc_brain = MagicMock(  # type: ignore[method-assign]
            return_value=NPCActionPlan(
                action_type=ActionType.DEFEND,
                rationale="test",
            ),
        )

        tainted = (
            "Gobelin utilise Nova : [Nova] aoe_damage not implemented — "
            "fallback to standard attack; Aragorn subit 5 dégâts"
        )
        monkeypatch.setattr(
            "bot.combat_turn_manager.execute_action_plan",
            MagicMock(return_value=tainted),
        )

        await tm._resolve_npc_turn(goblin)

        posted = [
            call.kwargs.get("content", "") or ""
            for call in channel.send.await_args_list
        ]
        summary_posts = [c for c in posted if c.startswith("📜")]
        assert summary_posts, "no NPC summary was posted"
        for content in summary_posts:
            assert "[" not in content, content
            assert "not implemented" not in content, content
            assert "fallback" not in content, content
        # The clean part of the summary survived.
        assert any("Aragorn subit 5 dégâts" in c for c in summary_posts)
        # The recorded combat event is the same sanitized text.
        assert session.combat_state is not None
        assert all(
            "[" not in ev for ev in session.combat_state.recent_events
        )


# ---------------------------------------------------------------------------
# C3 — the turn must only advance when the current combatant actually
# consumed their action
# ---------------------------------------------------------------------------


class TestTurnAdvanceGuard:
    """``on_action_resolved`` advanced the turn on EVERY pipeline output:
    refused actions (UnknownEntityResult), off-turn-legal QUESTION/LOOK,
    and messages from players whose turn it is not. All of those must
    leave ``current_turn_index`` untouched.
    """

    def _setup(self) -> tuple[MagicMock, MagicMock, TurnManager, Combatant]:
        pc = _pc()
        goblin = _enemy()
        session = _fake_session([pc, goblin])
        channel = _fake_channel()
        tm = _turn_manager(session, channel)
        session.combat_turn_manager = tm
        tm._prompt_turn = AsyncMock()  # type: ignore[method-assign]
        return session, channel, tm, pc

    @pytest.mark.asyncio
    async def test_refused_action_does_not_advance_turn(self) -> None:
        """An UnknownEntityResult must not burn the current combatant's turn."""
        session, _, tm, pc = self._setup()

        await tm.on_action_resolved(_unknown_result(pc.name))

        assert session.combat_state.current_turn_index == 0
        tm._prompt_turn.assert_not_awaited()
        tm._cancel_timeout()

    @pytest.mark.asyncio
    async def test_question_does_not_advance_turn_and_rearms_watcher(
        self,
    ) -> None:
        """QUESTION is off-turn-legal and informational — no turn consumed.

        The current PC's auto-dodge watcher was paused on the way into the
        pipeline; the no-advance path must put the safety net back.
        """
        session, _, tm, pc = self._setup()

        await tm.on_action_resolved(
            _pipeline_result(pc.name, ActionType.QUESTION, is_question=True),
        )

        assert session.combat_state.current_turn_index == 0
        tm._prompt_turn.assert_not_awaited()
        watcher = tm.pending_timeout
        assert watcher is not None and not watcher.done(), (
            "watcher not re-armed after a non-consuming action — combat "
            "would soft-stall with no auto-dodge"
        )
        tm._cancel_timeout()

    @pytest.mark.asyncio
    async def test_look_does_not_advance_turn(self) -> None:
        """LOOK is the other off-turn-legal informational action."""
        session, _, tm, pc = self._setup()

        await tm.on_action_resolved(_pipeline_result(pc.name, ActionType.LOOK))

        assert session.combat_state.current_turn_index == 0
        tm._prompt_turn.assert_not_awaited()
        tm._cancel_timeout()

    @pytest.mark.asyncio
    async def test_off_turn_actor_does_not_advance_turn(self) -> None:
        """A teammate's pipeline output must not consume the current turn —
        and must not disarm the current PC's auto-dodge watcher either.
        """
        session, _, tm, _ = self._setup()

        async def _long_sleep() -> None:
            await asyncio.sleep(60)

        watcher = asyncio.create_task(_long_sleep())
        tm.pending_timeout = watcher

        await tm.on_action_resolved(
            _pipeline_result("Bryn", ActionType.ATTACK),
        )

        assert session.combat_state.current_turn_index == 0
        tm._prompt_turn.assert_not_awaited()
        assert tm.pending_timeout is watcher and not watcher.cancelled(), (
            "off-turn output disarmed the current PC's watcher"
        )
        tm._cancel_timeout()

    @pytest.mark.asyncio
    async def test_free_action_result_does_not_advance_turn(self) -> None:
        """EQUIP (free action) re-prompts instead of advancing — the guard
        must treat it as non-consuming on the free-text path too."""
        session, _, tm, pc = self._setup()

        await tm.on_action_resolved(
            _pipeline_result(pc.name, ActionType.EQUIP, is_free_action=True),
        )

        assert session.combat_state.current_turn_index == 0
        tm._cancel_timeout()

    @pytest.mark.asyncio
    async def test_consuming_attack_advances_turn(self) -> None:
        """Regression guard: a real on-turn ATTACK still advances."""
        session, _, tm, pc = self._setup()

        await tm.on_action_resolved(_pipeline_result(pc.name, ActionType.ATTACK))

        assert session.combat_state.current_turn_index == 1
        tm._prompt_turn.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_internal_none_result_advances_turn(self) -> None:
        """NPC turns and the surprise skip pass ``None`` — trusted callers
        whose action economy was already enforced engine-side."""
        session, _, tm, _ = self._setup()

        await tm.on_action_resolved(None)

        assert session.combat_state.current_turn_index == 1
        tm._prompt_turn.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dispatch_refused_action_reprompts_same_combatant(
        self,
    ) -> None:
        """Button path: a refused action must re-prompt the same combatant
        (the clicked view disabled itself) instead of advancing the turn."""
        pc = _pc()
        goblin = _enemy()
        session = _fake_session([pc, goblin])
        channel = _fake_channel()

        fake_pipeline = MagicMock()
        fake_pipeline._pending_dice_embeds = []
        fake_pipeline.process_interpreted_action = AsyncMock(
            return_value=_unknown_result(pc.name),
        )
        tm = TurnManager(
            channel=channel,
            session=session,
            pipeline_factory=MagicMock(return_value=fake_pipeline),
        )
        session.combat_turn_manager = tm
        tm._prompt_turn = AsyncMock()  # type: ignore[method-assign]

        action = InterpretedAction(
            action_type=ActionType.ATTACK,
            actor_name=pc.name,
            target_name="le dragon invisible",
            raw_input="bouton attaque",
        )
        await asyncio.wait_for(tm.dispatch_action(action), timeout=5.0)

        assert session.combat_state.current_turn_index == 0
        tm._prompt_turn.assert_awaited_once()
        (prompted,) = tm._prompt_turn.await_args.args
        assert prompted.name == pc.name
        tm._cancel_timeout()
