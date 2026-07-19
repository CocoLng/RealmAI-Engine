"""Regression tests for combat turn-handling concurrency bugs.

Bug 1 — same-task deadlock on free-text actions during active combat:
``ActionHandlerCog.on_message`` held ``session.action_lock`` around the whole
``_run_pipeline`` call, while ``TurnManager.on_action_resolved`` (invoked at
the end of ``_run_pipeline``) re-acquires the same non-reentrant
``asyncio.Lock`` from the same task. Every @bot free-text action during
combat therefore hung forever. ``bot/cogs/test_bridge.py`` hit the same
deadlock through its direct ``_run_pipeline`` call.

Bug 2 (historical) — the old auto-dodge watcher cancelled itself via its
own ``dispatch_action``. The auto-dodge is GONE (design decision
2026-07-19: the game never acts in the player's place — a turn may stay
open for hours). The watcher now posts one reminder and stops; the test
pins that contract: reminder posted, nothing dispatched, turn untouched.

Bug 3 — the watcher fires mid-pipeline on slow free-text actions:
a free-text action for the CURRENT combatant whose LLM pipeline runs longer
than ``_REMINDER_SECONDS`` did not pause the watcher, which posted a
spurious reminder while the player was in fact acting. Fix:
``_run_pipeline`` pauses the watcher via ``TurnManager.pause_timeout_for``
before entering the pipeline and re-arms it via
``TurnManager.rearm_timeout`` on every path where the action does not
resolve the turn (pipeline failure, dropped progress message) — the
reminder net stays armed for genuinely idle turns.

Bug 4 — stale dispatch resolves a turn that already advanced: a button
click can outlive its turn when the old hub's delete failed.
``dispatch_action`` re-checks under the lock that combat is still active
and that the action's actor is still the current combatant, and drops the
action without advancing the turn otherwise. The guard runs BEFORE
``_cancel_timeout`` so a stale dispatch cannot disarm the watcher
protecting the new current turn.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from ai.entity_resolver import EntityCandidate
from ai.models import InterpretedAction
from bot.action_pipeline import (
    ActionPipelineResult,
    AmbiguityResult,
    UnknownEntityResult,
)
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

    def __init__(
        self,
        result: ActionPipelineResult,
        session: Any,
        delay: float = 0.0,
    ) -> None:
        self._result = result
        self._session = session
        self._delay = delay
        self._pending_dice_embeds: list[Any] = []
        self._pending_combat_start_embed = None
        self.lock_held_during_process: bool | None = None

    async def process(
        self, *, player_text: str, progress_callback: Any,
    ) -> ActionPipelineResult:
        del player_text, progress_callback
        self.lock_held_during_process = self._session.action_lock.locked()
        # Real suspension, like the actual pipeline. ``delay`` simulates a
        # slow LLM run (Bug 3 needs the pipeline to outlive the watcher).
        await asyncio.sleep(self._delay)
        return self._result


def _defend_pipeline() -> MagicMock:
    """TurnManager-side fake pipeline resolving any action, with a real
    suspension point like the actual pipeline."""
    fake = MagicMock()
    fake._pending_dice_embeds = []

    async def _process(action: InterpretedAction) -> ActionPipelineResult:
        await asyncio.sleep(0)
        return ActionPipelineResult(
            narrative="Esquive.",
            tone="tense",
            mechanics_text="DEFEND",
            interpreted_action=action,
        )

    fake.process_interpreted_action = AsyncMock(side_effect=_process)
    return fake


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


class TestTimeoutWatcherReminder:
    @pytest.mark.asyncio
    async def test_watcher_fires_posts_reminder_and_waits(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The armed watcher expires, posts ONE reminder, and acts on nothing.

        Design decision 2026-07-19: no auto-action on timeout, ever. The
        turn stays with the same combatant indefinitely; any human action
        resumes the normal flow.
        """
        monkeypatch.setattr("bot.combat_turn_manager._REMINDER_SECONDS", 0.05)

        session, channel, _, pc = _combat_setup()

        fake_pipeline = MagicMock()
        fake_pipeline._pending_dice_embeds = []
        fake_pipeline.process_interpreted_action = AsyncMock()

        tm = TurnManager(
            channel=channel,
            session=session,
            pipeline_factory=MagicMock(return_value=fake_pipeline),
        )
        session.combat_turn_manager = tm
        tm.on_action_resolved = AsyncMock()  # type: ignore[method-assign]

        await tm._prompt_pc_turn(pc, session.combat_state)
        watcher = tm.pending_timeout
        assert watcher is not None, "PC prompt did not arm the timeout watcher"

        done, _ = await asyncio.wait({watcher}, timeout=2.0)
        assert watcher in done, "timeout watcher never finished"
        assert not watcher.cancelled()

        # Nothing was played in the player's place, the turn did not move…
        fake_pipeline.process_interpreted_action.assert_not_awaited()
        tm.on_action_resolved.assert_not_awaited()
        assert session.combat_state.current_turn_index == 0
        # …no re-arm (one reminder, then silence)…
        assert tm.pending_timeout is None
        # …and the reminder was posted.
        contents = [
            call.kwargs.get("content") or ""
            for call in channel.send.await_args_list
        ]
        assert any("toujours ton tour" in c for c in contents)


# ---------------------------------------------------------------------------
# Bug 3 — slow free-text pipeline must pause the reminder watcher
# ---------------------------------------------------------------------------


def _sent_contents(channel: MagicMock) -> list[str]:
    return [
        call.kwargs.get("content") or ""
        for call in channel.send.await_args_list
    ]


class TestFreeTextPausesReminderWatcher:
    @pytest.mark.asyncio
    async def test_slow_pipeline_does_not_trigger_spurious_reminder(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A free-text action slower than the delay must not draw a reminder.

        Regression: the watcher armed by _prompt_pc_turn kept ticking while
        the current combatant's free-text action ran through the pipeline
        under action_lock — past the delay it nagged a player who was in
        fact acting (and, in the auto-dodge era, double-resolved the turn).
        """
        monkeypatch.setattr("bot.combat_turn_manager._REMINDER_SECONDS", 0.05)

        session, channel, tm, pc = _combat_setup()
        tm_pipeline = _defend_pipeline()
        tm.pipeline_factory = MagicMock(return_value=tm_pipeline)
        # Stop the lifecycle after the turn advance — NPC turns are out of
        # scope here.
        tm._prompt_turn = AsyncMock()  # type: ignore[method-assign]

        bot = _make_bot(session)
        cog = ActionHandlerCog(bot)
        # Pipeline outlives the watcher: 0.2s run vs 0.05s timeout.
        pipeline = _FakePipeline(_attack_result(pc.name), session, delay=0.2)
        cog._pipeline_factory = MagicMock(return_value=pipeline)

        message = _make_message(channel, bot)

        await tm._prompt_pc_turn(pc, session.combat_state)
        assert tm.pending_timeout is not None, "PC prompt did not arm watcher"

        await asyncio.wait_for(cog.on_message(message), timeout=5.0)
        # Let any queued spurious reminder (the bug) surface before asserting.
        await asyncio.sleep(0.1)

        assert not any(
            "toujours ton tour" in c for c in _sent_contents(channel)
        ), "watcher fired mid-pipeline despite the in-flight action"
        # No second resolution went through the TurnManager pipeline…
        tm_pipeline.process_interpreted_action.assert_not_awaited()
        # …and the turn advanced exactly once (PC → goblin).
        assert session.combat_state.current_turn_index == 1
        assert not session.action_lock.locked()

    @pytest.mark.asyncio
    async def test_pipeline_failure_rearms_reminder_watcher(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A paused watcher must be re-armed when the action never resolves.

        The pause must not remove the safety net: if the pipeline raises,
        the turn did not advance and nobody re-prompts — without a re-arm,
        combat soft-stalls forever on an AFK player.
        """
        monkeypatch.setattr("bot.combat_turn_manager._REMINDER_SECONDS", 0.05)

        session, channel, tm, pc = _combat_setup()
        tm_pipeline = _defend_pipeline()
        tm.pipeline_factory = MagicMock(return_value=tm_pipeline)
        # Stop the lifecycle after the dispatch — asserting the handoff is
        # enough; advance/re-prompt is covered elsewhere.
        tm.on_action_resolved = AsyncMock()  # type: ignore[method-assign]

        bot = _make_bot(session)
        cog = ActionHandlerCog(bot)
        failing = MagicMock()
        failing._pending_dice_embeds = []
        failing._pending_combat_start_embed = None
        failing.process = AsyncMock(side_effect=RuntimeError("interpreter down"))
        cog._pipeline_factory = MagicMock(return_value=failing)

        message = _make_message(channel, bot)

        await tm._prompt_pc_turn(pc, session.combat_state)

        await asyncio.wait_for(
            cog._run_pipeline(message, session, "j'attaque le gobelin"),
            timeout=5.0,
        )

        # The action dropped — the safety net must be armed again.
        watcher = tm.pending_timeout
        assert watcher is not None and not watcher.done(), (
            "watcher not re-armed after pipeline failure — combat would "
            "soft-stall with no reminder net"
        )

        # And it is a working net: it fires, posts the reminder, and plays
        # NOTHING in the player's place — the turn stays open.
        done, _ = await asyncio.wait({watcher}, timeout=2.0)
        assert watcher in done, "re-armed watcher never fired"
        tm_pipeline.process_interpreted_action.assert_not_awaited()
        assert session.combat_state.current_turn_index == 0
        assert any(
            "toujours ton tour" in c for c in _sent_contents(channel)
        )

    @pytest.mark.asyncio
    async def test_off_turn_free_text_keeps_watcher_armed(self) -> None:
        """Another player's action must not disarm the current PC's watcher.

        Only the CURRENT combatant's own free-text action pauses the
        timeout — chatter from an off-turn party member leaves the AFK
        protection in place.
        """
        current_pc = _pc("Bryn")
        author_pc = _pc("Aldric")
        goblin = _enemy()
        session = _fake_session([current_pc, author_pc, goblin])
        session.characters = {
            _PLAYER_ID: author_pc.character,
            777: current_pc.character,
        }
        channel = _fake_channel()
        tm = TurnManager(
            channel=channel, session=session, pipeline_factory=MagicMock(),
        )
        session.combat_turn_manager = tm
        # The real handoff starts with _cancel_timeout + advance — mock it
        # to isolate the pause decision made on the way INTO the pipeline.
        tm.on_action_resolved = AsyncMock()  # type: ignore[method-assign]

        bot = _make_bot(session)
        cog = ActionHandlerCog(bot)
        pipeline = _FakePipeline(_attack_result(author_pc.name), session)
        cog._pipeline_factory = MagicMock(return_value=pipeline)

        message = _make_message(channel, bot)

        await tm._prompt_pc_turn(current_pc, session.combat_state)
        watcher = tm.pending_timeout
        assert watcher is not None

        await asyncio.wait_for(
            cog._run_pipeline(message, session, "j'attaque le gobelin"),
            timeout=5.0,
        )

        assert tm.pending_timeout is watcher
        assert not watcher.done(), "off-turn action disarmed the watcher"

        tm._cancel_timeout()  # cleanup the still-armed 300s task


# ---------------------------------------------------------------------------
# Bug 4 — stale dispatch must not resolve a turn that already advanced
# ---------------------------------------------------------------------------


class TestStaleDispatchGuard:
    @pytest.mark.asyncio
    async def test_stale_button_click_after_turn_advance_is_dropped(
        self,
    ) -> None:
        """A button click from the previous turn's PC must be ignored.

        Same guard, button path: the old hub's view can survive a failed
        delete, so a click for an actor whose turn already advanced reaches
        ``dispatch_action`` late. It must neither run the pipeline nor
        advance the turn — and it must NOT disarm the watcher protecting
        the new current PC's turn.
        """
        aldric = _pc("Aldric")
        bryn = _pc("Bryn")
        goblin = _enemy()
        session = _fake_session([aldric, bryn, goblin])
        session.combat_state.current_turn_index = 1  # Bryn's turn now
        session.characters = {
            _PLAYER_ID: aldric.character,
            777: bryn.character,
        }
        channel = _fake_channel()
        tm_pipeline = _defend_pipeline()
        tm = TurnManager(
            channel=channel,
            session=session,
            pipeline_factory=MagicMock(return_value=tm_pipeline),
        )
        session.combat_turn_manager = tm
        tm.on_action_resolved = AsyncMock()  # type: ignore[method-assign]

        await tm._prompt_pc_turn(bryn, session.combat_state)
        watcher = tm.pending_timeout
        assert watcher is not None, "PC prompt did not arm the timeout watcher"

        stale_action = InterpretedAction(
            action_type=ActionType.ATTACK,
            actor_name=aldric.name,
            target_name=goblin.name,
            raw_input="(stale button click)",
        )
        await asyncio.wait_for(tm.dispatch_action(stale_action), timeout=5.0)

        tm_pipeline.process_interpreted_action.assert_not_awaited()
        tm.on_action_resolved.assert_not_awaited()
        assert session.combat_state.current_turn_index == 1
        assert tm.pending_timeout is watcher and not watcher.done(), (
            "stale dispatch disarmed the current turn's auto-dodge watcher"
        )
        assert not session.action_lock.locked()

        tm._cancel_timeout()  # cleanup the still-armed 300s task

    @pytest.mark.asyncio
    async def test_dispatch_after_combat_end_is_dropped(self) -> None:
        """An action landing after combat ended must not run the pipeline."""
        session, channel, tm, pc = _combat_setup()
        tm_pipeline = _defend_pipeline()
        tm.pipeline_factory = MagicMock(return_value=tm_pipeline)
        tm.on_action_resolved = AsyncMock()  # type: ignore[method-assign]

        session.combat_state.is_active = False

        late_action = InterpretedAction(
            action_type=ActionType.DEFEND,
            actor_name=pc.name,
            raw_input="(late auto-dodge)",
        )
        await asyncio.wait_for(tm.dispatch_action(late_action), timeout=5.0)

        tm_pipeline.process_interpreted_action.assert_not_awaited()
        tm.on_action_resolved.assert_not_awaited()


# ---------------------------------------------------------------------------
# C3 — pipeline outputs that did not consume the turn must not advance it
# ---------------------------------------------------------------------------


def _question_result(actor_name: str) -> ActionPipelineResult:
    return ActionPipelineResult(
        narrative="Le Gardien explique la situation.",
        tone="somber",
        mechanics_text="QUESTION",
        interpreted_action=InterpretedAction(
            action_type=ActionType.QUESTION,
            actor_name=actor_name,
            raw_input="que vois-je autour de moi ?",
        ),
        is_question=True,
    )


def _unknown_entity_result(actor_name: str) -> UnknownEntityResult:
    return UnknownEntityResult(
        field_name="target_name",
        raw_value="le spectre",
        partial_action=InterpretedAction(
            action_type=ActionType.ATTACK,
            actor_name=actor_name,
            raw_input="j'attaque le spectre",
        ),
        refusal_narrative="Aucun spectre en vue.",
    )


class TestNonConsumingFreeText:
    @pytest.mark.asyncio
    async def test_question_during_combat_does_not_advance_turn(self) -> None:
        """A QUESTION from the current combatant must not burn their turn.

        Regression (C3): on_action_resolved advanced the turn on every
        pipeline output — a player asking a question in combat consumed
        the active combatant's turn.
        """
        session, channel, tm, pc = _combat_setup()
        tm._prompt_turn = AsyncMock()  # type: ignore[method-assign]

        bot = _make_bot(session)
        cog = ActionHandlerCog(bot)
        pipeline = _FakePipeline(_question_result(pc.name), session)
        cog._pipeline_factory = MagicMock(return_value=pipeline)

        message = _make_message(channel, bot)

        await asyncio.wait_for(cog.on_message(message), timeout=5.0)

        assert session.combat_state.current_turn_index == 0, (
            "a QUESTION consumed the current combatant's turn"
        )
        tm._prompt_turn.assert_not_awaited()
        # The no-advance path must leave the AFK safety net armed.
        watcher = tm.pending_timeout
        assert watcher is not None and not watcher.done()
        assert not session.action_lock.locked()
        tm._cancel_timeout()

    @pytest.mark.asyncio
    async def test_refused_action_during_combat_does_not_advance_turn(
        self,
    ) -> None:
        """An UnknownEntityResult (refused action) must not advance the turn."""
        session, channel, tm, pc = _combat_setup()
        tm._prompt_turn = AsyncMock()  # type: ignore[method-assign]

        bot = _make_bot(session)
        cog = ActionHandlerCog(bot)
        pipeline = _FakePipeline(_unknown_entity_result(pc.name), session)
        cog._pipeline_factory = MagicMock(return_value=pipeline)

        message = _make_message(channel, bot)

        await asyncio.wait_for(cog.on_message(message), timeout=5.0)

        assert session.combat_state.current_turn_index == 0, (
            "a refused action consumed the current combatant's turn"
        )
        tm._prompt_turn.assert_not_awaited()
        assert not session.action_lock.locked()
        tm._cancel_timeout()

    @pytest.mark.asyncio
    async def test_off_turn_player_question_does_not_advance_turn(self) -> None:
        """A teammate's off-turn QUESTION must not consume the current turn."""
        current_pc = _pc("Bryn")
        author_pc = _pc("Aldric")
        goblin = _enemy()
        session = _fake_session([current_pc, author_pc, goblin])
        session.characters = {
            _PLAYER_ID: author_pc.character,
            777: current_pc.character,
        }
        channel = _fake_channel()
        tm = TurnManager(
            channel=channel, session=session, pipeline_factory=MagicMock(),
        )
        session.combat_turn_manager = tm
        tm._prompt_turn = AsyncMock()  # type: ignore[method-assign]

        bot = _make_bot(session)
        cog = ActionHandlerCog(bot)
        pipeline = _FakePipeline(_question_result(author_pc.name), session)
        cog._pipeline_factory = MagicMock(return_value=pipeline)

        message = _make_message(channel, bot)

        await asyncio.wait_for(cog.on_message(message), timeout=5.0)

        assert session.combat_state.current_turn_index == 0, (
            "an off-turn player's question consumed the current turn"
        )
        tm._prompt_turn.assert_not_awaited()
        tm._cancel_timeout()


class TestAmbiguityDuringCombat:
    def _ambiguity(self, actor_name: str) -> AmbiguityResult:
        return AmbiguityResult(
            field_name="target_name",
            raw_value="gobelin",
            candidates=[
                EntityCandidate(id="npc:gobelin-1", label="Gobelin balafré"),
                EntityCandidate(id="npc:gobelin-2", label="Gobelin borgne"),
            ],
            partial_action=InterpretedAction(
                action_type=ActionType.ATTACK,
                actor_name=actor_name,
                target_name="gobelin",
                raw_input="j'attaque le gobelin",
            ),
        )

    def _stub_view(
        self, *, cancelled: bool, chosen_entity_id: str | None,
    ) -> MagicMock:
        view = MagicMock()
        view.wait = AsyncMock()
        view.cancelled = cancelled
        view.chosen_entity_id = chosen_entity_id
        return view

    @pytest.mark.asyncio
    async def test_cancelled_clarification_does_not_advance_turn(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Cancelling the disambiguation aborts the action — no mechanics
        ran, so the turn must not advance (C3)."""
        session, channel, tm, pc = _combat_setup()
        tm._prompt_turn = AsyncMock()  # type: ignore[method-assign]

        bot = _make_bot(session)
        cog = ActionHandlerCog(bot)
        pipeline = _FakePipeline(self._ambiguity(pc.name), session)  # type: ignore[arg-type]
        cog._pipeline_factory = MagicMock(return_value=pipeline)

        monkeypatch.setattr(
            "bot.cogs.action_handler.ClarificationView",
            MagicMock(
                return_value=self._stub_view(
                    cancelled=True, chosen_entity_id=None,
                ),
            ),
        )

        message = _make_message(channel, bot)
        await asyncio.wait_for(cog.on_message(message), timeout=5.0)

        assert session.combat_state.current_turn_index == 0, (
            "a cancelled clarification consumed the current combatant's turn"
        )
        tm._prompt_turn.assert_not_awaited()
        assert not session.action_lock.locked()
        tm._cancel_timeout()

    @pytest.mark.asyncio
    async def test_resolved_clarification_advances_turn(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Picking a candidate resumes the pipeline; the final (consuming)
        result must reach the TurnManager handoff so the turn advances."""
        session, channel, tm, pc = _combat_setup()
        tm._prompt_turn = AsyncMock()  # type: ignore[method-assign]

        bot = _make_bot(session)
        cog = ActionHandlerCog(bot)
        pipeline = _FakePipeline(self._ambiguity(pc.name), session)  # type: ignore[arg-type]
        pipeline.resume_with_resolution = AsyncMock(  # type: ignore[attr-defined]
            return_value=_attack_result(pc.name),
        )
        cog._pipeline_factory = MagicMock(return_value=pipeline)

        monkeypatch.setattr(
            "bot.cogs.action_handler.ClarificationView",
            MagicMock(
                return_value=self._stub_view(
                    cancelled=False, chosen_entity_id="npc:gobelin-1",
                ),
            ),
        )

        message = _make_message(channel, bot)
        await asyncio.wait_for(cog.on_message(message), timeout=5.0)

        assert session.combat_state.current_turn_index == 1, (
            "the resolved clarification's attack never advanced the turn"
        )
        tm._prompt_turn.assert_awaited_once()
        assert not session.action_lock.locked()
