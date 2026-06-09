"""Regression tests for combat turn-handling concurrency bugs.

Bug 1 — same-task deadlock on free-text actions during active combat:
``ActionHandlerCog.on_message`` held ``session.action_lock`` around the whole
``_run_pipeline`` call, while ``TurnManager.on_action_resolved`` (invoked at
the end of ``_run_pipeline``) re-acquires the same non-reentrant
``asyncio.Lock`` from the same task. Every @bot free-text action during
combat therefore hung forever. ``bot/cogs/test_bridge.py`` hit the same
deadlock through its direct ``_run_pipeline`` call.

Bug 2 — auto-dodge timeout watcher cancels itself:
``TurnManager._timeout_watcher`` calls ``dispatch_action``, whose first
statement ``_cancel_timeout()`` cancelled ``pending_timeout`` — which at that
moment *is* the running watcher task. The pending ``CancelledError`` then
fired at the first real suspension inside the pipeline, so the bot announced
"Défense automatique" but the DEFEND never resolved and the turn never
advanced. Synchronous mocks hide this — the fake pipeline below suspends for
real.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from ai.models import InterpretedAction
from bot.action_pipeline import ActionPipelineResult
from bot.cogs.action_handler import ActionHandlerCog
from bot.combat_turn_manager import TurnManager
from engine.character import (
    AbilityScores,
    CharacterClass,
    Race,
    create_character,
)
from engine.combat import CombatSide, CombatState, Combatant
from engine.inventory import create_inventory
from engine.validators import ActionType

_PLAYER_ID = 42
_CHANNEL_ID = 123


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _pc(name: str = "Aldric", hp: int = 40) -> Combatant:
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


def _enemy(name: str = "Gobelin", hp: int = 12) -> Combatant:
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
    return Combatant(
        name=name,
        side=CombatSide.ENEMY,
        character=char,
        inventory=create_inventory(),
        initiative=10,
    )


def _fake_channel() -> MagicMock:
    channel = MagicMock()
    channel.id = _CHANNEL_ID
    channel.send = AsyncMock(
        return_value=MagicMock(edit=AsyncMock(), delete=AsyncMock()),
    )
    return channel


def _fake_session(combatants: list[Combatant]) -> MagicMock:
    session = MagicMock()
    session.campaign = MagicMock()
    session.campaign.id = "camp-concurrency"
    session.combat_state = CombatState(
        combatants=combatants, round_number=1, current_turn_index=0,
    )
    session.combat_turn_manager = None
    session.current_location = None
    session.characters = {_PLAYER_ID: combatants[0].character}
    session.inventories = {_PLAYER_ID: create_inventory()}
    session.spellcasters = {_PLAYER_ID: None}
    session.language = "fr"
    session.story_bible = None  # record_turn_and_maybe_check tolerates None
    session.story_arc = None
    session.semantic_indexer = None
    session.force_next_director_run = False
    session.action_lock = asyncio.Lock()
    return session


def _make_bot(session: Any) -> MagicMock:
    bot = MagicMock()
    bot.user = MagicMock()
    bot.user.id = 9999
    bot.sessions = {_CHANNEL_ID: session}
    bot.db_factory = None
    return bot


def _make_message(channel: Any, bot: Any) -> MagicMock:
    message = MagicMock()
    message.author = MagicMock()
    message.author.id = _PLAYER_ID
    message.author.bot = False
    message.channel = channel
    message.content = "<@9999> j'attaque le gobelin"
    message.mentions = [bot.user]
    message.reply = AsyncMock()
    return message


def _attack_result(actor_name: str) -> ActionPipelineResult:
    return ActionPipelineResult(
        narrative="Le coup porte.",
        tone="tense",
        mechanics_text="ATTACK: touché, 5 dégâts",
        interpreted_action=InterpretedAction(
            action_type=ActionType.ATTACK,
            actor_name=actor_name,
            target_name="Gobelin",
            raw_input="j'attaque le gobelin",
        ),
    )


class _FakePipeline:
    """Stands in for ActionPipeline in the free-text cog path.

    Records whether ``session.action_lock`` was held while the pipeline body
    ran (the serialization invariant the lock exists for) and suspends for
    real so task-level cancellation/deadlock behaviour is not masked.
    """

    def __init__(self, result: ActionPipelineResult, session: Any) -> None:
        self._result = result
        self._session = session
        self._pending_dice_embeds: list[Any] = []
        self._pending_combat_start_embed = None
        self.lock_held_during_process: bool | None = None

    async def process(
        self, *, player_text: str, progress_callback: Any,
    ) -> ActionPipelineResult:
        del player_text, progress_callback
        self.lock_held_during_process = self._session.action_lock.locked()
        await asyncio.sleep(0)  # real suspension, like the actual pipeline
        return self._result


def _combat_setup() -> tuple[MagicMock, MagicMock, TurnManager, Combatant]:
    """Session with active combat (PC turn), TurnManager wired in."""
    pc = _pc()
    goblin = _enemy()
    session = _fake_session([pc, goblin])
    channel = _fake_channel()
    tm = TurnManager(
        channel=channel, session=session, pipeline_factory=MagicMock(),
    )
    session.combat_turn_manager = tm
    return session, channel, tm, pc


# ---------------------------------------------------------------------------
# Bug 1 — free-text action during combat must not deadlock on action_lock
# ---------------------------------------------------------------------------


class TestFreeTextDuringCombat:
    @pytest.mark.asyncio
    async def test_on_message_during_combat_advances_turn_without_deadlock(
        self,
    ) -> None:
        """@bot free-text during combat completes and advances the turn.

        Regression: on_message held action_lock across _run_pipeline while
        on_action_resolved re-acquired it from the same task → permanent
        hang. wait_for turns the hang into a test failure.
        """
        session, channel, tm, pc = _combat_setup()
        # Stop the lifecycle after the turn advance — NPC turns and PC
        # re-prompts are out of scope here.
        tm._prompt_turn = AsyncMock()  # type: ignore[method-assign]

        bot = _make_bot(session)
        cog = ActionHandlerCog(bot)
        pipeline = _FakePipeline(_attack_result(pc.name), session)
        cog._pipeline_factory = MagicMock(return_value=pipeline)

        message = _make_message(channel, bot)

        await asyncio.wait_for(cog.on_message(message), timeout=5.0)

        # The pipeline body must still run under the per-session lock.
        assert pipeline.lock_held_during_process is True
        # on_action_resolved ran: the turn advanced to the goblin.
        assert session.combat_state.current_turn_index == 1
        tm._prompt_turn.assert_awaited_once()
        # The lock is free again for the next action.
        assert not session.action_lock.locked()

    @pytest.mark.asyncio
    async def test_run_pipeline_direct_call_serializes_and_advances_turn(
        self,
    ) -> None:
        """_run_pipeline owns the action_lock itself.

        bot/cogs/test_bridge.py invokes ``cog._run_pipeline`` directly for
        its narrate command — the helper must acquire the lock around the
        pipeline body and release it before the TurnManager handoff, so no
        caller can deadlock it by pre-holding the lock.
        """
        session, channel, tm, pc = _combat_setup()
        tm._prompt_turn = AsyncMock()  # type: ignore[method-assign]

        bot = _make_bot(session)
        cog = ActionHandlerCog(bot)
        pipeline = _FakePipeline(_attack_result(pc.name), session)
        cog._pipeline_factory = MagicMock(return_value=pipeline)

        message = _make_message(channel, bot)

        await asyncio.wait_for(
            cog._run_pipeline(message, session, "j'attaque le gobelin"),
            timeout=5.0,
        )

        assert pipeline.lock_held_during_process is True
        assert session.combat_state.current_turn_index == 1
        assert not session.action_lock.locked()


# ---------------------------------------------------------------------------
# Bug 2 — timeout watcher must survive its own dispatch_action call
# ---------------------------------------------------------------------------


class TestTimeoutWatcherAutoDodge:
    @pytest.mark.asyncio
    async def test_watcher_fires_and_resolves_auto_defend(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The armed watcher expires, dispatches DEFEND, and advances the turn.

        Regression: dispatch_action's _cancel_timeout() cancelled the
        running watcher task itself; the CancelledError fired at the first
        real await inside the pipeline and silently killed the auto-dodge.
        """
        monkeypatch.setattr("bot.combat_turn_manager._TIMEOUT_SECONDS", 0.05)

        session, channel, _, pc = _combat_setup()

        fake_pipeline = MagicMock()
        fake_pipeline._pending_dice_embeds = []

        async def _process(action: InterpretedAction) -> ActionPipelineResult:
            # Real suspension point (the actual pipeline hits asyncio.to_thread
            # and LLM retries here) — where the self-cancel used to detonate.
            await asyncio.sleep(0)
            return ActionPipelineResult(
                narrative="Esquive.",
                tone="tense",
                mechanics_text="DEFEND",
                interpreted_action=action,
            )

        fake_pipeline.process_interpreted_action = AsyncMock(side_effect=_process)

        tm = TurnManager(
            channel=channel,
            session=session,
            pipeline_factory=MagicMock(return_value=fake_pipeline),
        )
        session.combat_turn_manager = tm
        # Stop the lifecycle after the dispatch — asserting the handoff is
        # enough; the advance/re-prompt chain is covered elsewhere.
        tm.on_action_resolved = AsyncMock()  # type: ignore[method-assign]

        await tm._prompt_pc_turn(pc, session.combat_state)
        watcher = tm.pending_timeout
        assert watcher is not None, "PC prompt did not arm the timeout watcher"

        done, _ = await asyncio.wait({watcher}, timeout=2.0)
        assert watcher in done, "timeout watcher never finished"
        assert not watcher.cancelled(), (
            "watcher cancelled itself — dispatch_action._cancel_timeout "
            "hit the running watcher task"
        )

        # The auto-DEFEND went through the pipeline…
        fake_pipeline.process_interpreted_action.assert_awaited_once()
        (auto_action,) = fake_pipeline.process_interpreted_action.await_args.args
        assert auto_action.action_type == ActionType.DEFEND
        assert auto_action.actor_name == pc.name
        # …and the turn-advance handoff happened.
        tm.on_action_resolved.assert_awaited_once()

        # The player-facing announcement was posted.
        contents = [
            call.kwargs.get("content") or ""
            for call in channel.send.await_args_list
        ]
        assert any("Défense automatique" in c for c in contents)
