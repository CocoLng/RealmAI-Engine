"""Tests for bot/cogs/action_handler.py — message filter + dispatch."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from ai.models import InterpretedAction
from bot.action_pipeline import (
    ActionPipelineResult,
    AmbiguityResult,
    LowConfidenceResult,
    PipelinePhase,
    UnknownEntityResult,
)
from bot.cogs.action_handler import (
    ActionHandlerCog,
    looks_like_action,
)
from engine.character import (
    AbilityScores,
    CharacterClass,
    Race,
    create_character,
)
from engine.validators import ActionType
from world.campaign import Campaign
from world.location import Location


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class FakeMessage:
    """Minimal Discord-message-like object for cog tests."""

    content: str
    author: Any
    channel: Any
    mentions: list[Any] = field(default_factory=list)
    reply: AsyncMock = field(default_factory=AsyncMock)


@dataclass
class FakeAuthor:
    id: int
    bot: bool = False
    display_name: str = "Player"


@dataclass
class FakeChannel:
    id: int
    send: AsyncMock = field(default_factory=AsyncMock)


_SENTINEL = object()


def _make_session(
    *,
    interpreter: Any = _SENTINEL,
    narrator: Any = _SENTINEL,
    location: Location | None = None,
    player_id: int = 1,
) -> Any:
    """Build a minimal GameSession-shaped fake.

    Pass ``interpreter=None`` (or ``narrator=None``) explicitly to simulate an
    Ollama-unavailable session.
    """
    scores = AbilityScores(STR=12, DEX=10, CON=10, INT=10, WIS=10, CHA=10)
    char = create_character("Aldric", Race.HUMAN, CharacterClass.FIGHTER, scores)
    session = MagicMock()
    session.campaign = Campaign(id="camp-1", name="Test", player_names=[str(player_id)])
    session.characters = {player_id: char}
    session.npcs = {}
    session.current_location = location
    session.combat_state = None
    session.inventories = {}
    session.language = "fr"
    session.interpreter = MagicMock() if interpreter is _SENTINEL else interpreter
    session.narrator = MagicMock() if narrator is _SENTINEL else narrator
    session.action_lock = asyncio.Lock()
    return session


def _make_bot(
    *,
    bot_user_id: int = 9999,
    sessions: dict[int, Any] | None = None,
) -> Any:
    bot = MagicMock()
    bot.user = MagicMock()
    bot.user.id = bot_user_id
    bot.sessions = sessions or {}
    return bot


def _make_cog(bot: Any) -> ActionHandlerCog:
    return ActionHandlerCog(bot)


# ---------------------------------------------------------------------------
# Pipeline factory injection
# ---------------------------------------------------------------------------


class FakePipelineFactory:
    """Replaces ActionPipeline construction for tests.

    Stores the last constructed pipeline so tests can inspect it.
    """

    def __init__(self, output: Any) -> None:
        self.output = output
        self.constructed: list[Any] = []
        self.process_calls: list[str] = []
        self.resume_calls: list[tuple[Any, str]] = []
        self.process_interpreted_calls: list[Any] = []
        self.resume_output: Any = None

    def __call__(self, **kwargs: Any) -> Any:
        pipeline = MagicMock()
        pipeline.kwargs = kwargs

        async def process(player_text: str, progress_callback: Any = None) -> Any:
            self.process_calls.append(player_text)
            if progress_callback is not None:
                # Drive a couple of phases to exercise the callback
                await progress_callback(PipelinePhase.INTERPRETING)
                await progress_callback(PipelinePhase.DONE)
            return self.output

        async def resume(
            ambiguity: Any, chosen_entity_id: str,
            progress_callback: Any = None,
        ) -> Any:
            self.resume_calls.append((ambiguity, chosen_entity_id))
            return self.output

        async def process_interpreted(
            action: Any, progress_callback: Any = None,
        ) -> Any:
            self.process_interpreted_calls.append(action)
            return self.resume_output if self.resume_output is not None else self.output

        pipeline.process = process
        pipeline.resume_with_resolution = resume
        pipeline.process_interpreted_action = process_interpreted
        self.constructed.append(pipeline)
        return pipeline


# ---------------------------------------------------------------------------
# looks_like_action heuristic
# ---------------------------------------------------------------------------


class TestLooksLikeAction:
    @pytest.mark.parametrize(
        "text",
        [
            "je fouille l'autel",
            "j'attaque le gobelin",
            "I look around the room",
            "explore the crypt and find the secret door",
        ],
    )
    def test_recognizes_actions(self, text: str) -> None:
        assert looks_like_action(text) is True

    @pytest.mark.parametrize(
        "text",
        ["", "  ", "ok", "merci", "GG", "lol"],
    )
    def test_filters_ooc_noise(self, text: str) -> None:
        assert looks_like_action(text) is False


# ---------------------------------------------------------------------------
# on_message — filter chain
# ---------------------------------------------------------------------------


class TestOnMessageFilters:
    @pytest.mark.asyncio
    async def test_ignores_bot_own_message(self) -> None:
        bot = _make_bot()
        cog = _make_cog(bot)
        msg = FakeMessage(
            content="<@9999> hello",
            author=FakeAuthor(id=9999, bot=True),
            channel=FakeChannel(id=1),
        )
        await cog.on_message(msg)  # type: ignore[arg-type]
        msg.reply.assert_not_called()

    @pytest.mark.asyncio
    async def test_ignores_message_outside_campaign_channel(self) -> None:
        bot = _make_bot(sessions={})
        cog = _make_cog(bot)
        msg = FakeMessage(
            content="<@9999> je regarde",
            author=FakeAuthor(id=42),
            channel=FakeChannel(id=999),
            mentions=[bot.user],
        )
        await cog.on_message(msg)  # type: ignore[arg-type]
        msg.reply.assert_not_called()

    @pytest.mark.asyncio
    async def test_ignores_message_without_bot_mention(self) -> None:
        session = _make_session()
        bot = _make_bot(sessions={1: session})
        cog = _make_cog(bot)
        msg = FakeMessage(
            content="je regarde",
            author=FakeAuthor(id=1),
            channel=FakeChannel(id=1),
            mentions=[],
        )
        await cog.on_message(msg)  # type: ignore[arg-type]
        msg.reply.assert_not_called()

    @pytest.mark.asyncio
    async def test_silently_ignores_non_player_user(self) -> None:
        """Viewers (added via /add_member post-launch) ping the bot freely
        but the handler must NOT reply — they're spectators by design."""
        session = _make_session(player_id=1)
        bot = _make_bot(sessions={1: session})
        cog = _make_cog(bot)
        msg = FakeMessage(
            content="<@9999> je regarde",
            author=FakeAuthor(id=42),  # not in session.characters
            channel=FakeChannel(id=1),
            mentions=[bot.user],
        )
        await cog.on_message(msg)  # type: ignore[arg-type]
        msg.reply.assert_not_called()

    @pytest.mark.asyncio
    async def test_replies_when_interpreter_unavailable(self) -> None:
        session = _make_session(interpreter=None, player_id=1)
        bot = _make_bot(sessions={1: session})
        cog = _make_cog(bot)
        msg = FakeMessage(
            content="<@9999> je regarde",
            author=FakeAuthor(id=1),
            channel=FakeChannel(id=1),
            mentions=[bot.user],
        )
        await cog.on_message(msg)  # type: ignore[arg-type]
        msg.reply.assert_called_once()
        text = msg.reply.call_args.args[0]
        assert "Game Master" in text or "Ollama" in text

    @pytest.mark.asyncio
    async def test_filters_ooc_noise_without_pipeline(self) -> None:
        session = _make_session(player_id=1)
        bot = _make_bot(sessions={1: session})
        cog = _make_cog(bot)
        factory = FakePipelineFactory(
            ActionPipelineResult(
                narrative="x",
                tone="dramatic",
                mechanics_text="x",
                interpreted_action=InterpretedAction(
                    action_type=ActionType.LOOK,
                    actor_name="Aldric",
                    raw_input="x",
                ),
            ),
        )
        cog._pipeline_factory = factory  # type: ignore[assignment]
        msg = FakeMessage(
            content="<@9999> merci",
            author=FakeAuthor(id=1),
            channel=FakeChannel(id=1),
            mentions=[bot.user],
        )
        await cog.on_message(msg)  # type: ignore[arg-type]
        # Pipeline must NOT have been built for OOC
        assert factory.process_calls == []
        msg.reply.assert_called_once()


# ---------------------------------------------------------------------------
# on_message — full dispatch
# ---------------------------------------------------------------------------


class TestOnMessageDispatch:
    def _setup(self, output: Any):
        location = Location(
            name="Place",
            description="d",
            connections=[],
            npcs_present=[],
            items_available=[],
        )
        session = _make_session(location=location, player_id=1)
        bot = _make_bot(sessions={1: session})
        cog = _make_cog(bot)
        factory = FakePipelineFactory(output)
        cog._pipeline_factory = factory  # type: ignore[assignment]
        return cog, bot, session, factory

    @pytest.mark.asyncio
    async def test_success_posts_narrative_embed(self) -> None:
        result = ActionPipelineResult(
            narrative="Tu observes la place.",
            tone="dramatic",
            mechanics_text="Aldric observes Place.",
            interpreted_action=InterpretedAction(
                action_type=ActionType.LOOK,
                actor_name="Aldric",
                raw_input="je regarde",
            ),
        )
        cog, bot, _session, factory = self._setup(result)
        channel = FakeChannel(id=1)
        msg = FakeMessage(
            content="<@9999> je regarde la place",
            author=FakeAuthor(id=1),
            channel=channel,
            mentions=[bot.user],
        )

        await cog.on_message(msg)  # type: ignore[arg-type]

        # Pipeline was built with the right kwargs
        assert factory.process_calls == ["je regarde la place"]
        last = factory.constructed[-1]
        assert last.kwargs["actor_name"] == "Aldric"
        assert last.kwargs["language"] == "fr"

        # An initial progress message was sent and then edited
        assert channel.send.called
        sent_message = channel.send.return_value
        assert sent_message.edit.called

    @pytest.mark.asyncio
    async def test_ambiguity_attaches_clarification_view(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        ambig = AmbiguityResult(
            field_name="target_name",
            raw_value="Marc",
            candidates=[],
            partial_action=InterpretedAction(
                action_type=ActionType.TALK,
                actor_name="Aldric",
                target_name="Marc",
                raw_input="je parle à Marc",
            ),
        )
        cog, bot, _session, factory = self._setup(ambig)

        # Stub ClarificationView.wait so the test does not hang on a real
        # 2-minute timeout. The view records the cancellation flag for us.
        async def _instant_wait(self: Any) -> None:
            self.cancelled = True

        from bot.views import clarification_view as cv_module

        monkeypatch.setattr(cv_module.ClarificationView, "wait", _instant_wait)

        channel = FakeChannel(id=1)
        msg = FakeMessage(
            content="<@9999> je parle à Marc",
            author=FakeAuthor(id=1),
            channel=channel,
            mentions=[bot.user],
        )

        await cog.on_message(msg)  # type: ignore[arg-type]

        sent = channel.send.return_value
        # The cog must have edited the progress message to attach the
        # clarification view at least once.
        edit_calls_with_view = [
            call for call in sent.edit.call_args_list
            if call.kwargs.get("view") is not None
        ]
        assert edit_calls_with_view, "Expected an edit call attaching the view"

    @pytest.mark.asyncio
    async def test_unknown_entity_posts_refusal_embed(self) -> None:
        unknown = UnknownEntityResult(
            field_name="target_name",
            raw_value="Dragon",
            partial_action=InterpretedAction(
                action_type=ActionType.TALK,
                actor_name="Aldric",
                target_name="Dragon",
                raw_input="je parle au dragon",
            ),
            refusal_narrative="Tu ne vois pas de dragon.",
            tone="somber",
        )
        cog, bot, _session, factory = self._setup(unknown)
        channel = FakeChannel(id=1)
        msg = FakeMessage(
            content="<@9999> je parle au dragon",
            author=FakeAuthor(id=1),
            channel=channel,
            mentions=[bot.user],
        )

        await cog.on_message(msg)  # type: ignore[arg-type]

        assert factory.process_calls == ["je parle au dragon"]
        sent = channel.send.return_value
        assert sent.edit.called

    # ----------------------------------------------------------------------
    # Lot A — scene embed posted after a successful MOVE
    # ----------------------------------------------------------------------

    def _setup_with_location(self, output: Any, location: Location):
        session = _make_session(location=location, player_id=1)
        bot = _make_bot(sessions={1: session})
        cog = _make_cog(bot)
        factory = FakePipelineFactory(output)
        cog._pipeline_factory = factory  # type: ignore[assignment]
        return cog, bot, session, factory

    @pytest.mark.asyncio
    async def test_post_move_scene_embed_is_sent(self) -> None:
        """A successful MOVE triggers an additional scene embed in the channel."""
        location = Location(
            name="Le Village",
            description="Un hameau brumeux.",
            connections=["forêt"],
            npcs_present=["Jeanne"],
        )
        result = ActionPipelineResult(
            narrative="Aldric s'enfonce dans la forêt.",
            tone="dramatic",
            mechanics_text="Aldric moves toward forêt.",
            interpreted_action=InterpretedAction(
                action_type=ActionType.MOVE,
                actor_name="Aldric",
                target_name="forêt",
                raw_input="j'entre dans la forêt",
            ),
        )
        cog, bot, _session, _factory = self._setup_with_location(result, location)
        channel = FakeChannel(id=1)
        msg = FakeMessage(
            content="<@9999> j'entre dans la forêt",
            author=FakeAuthor(id=1),
            channel=channel,
            mentions=[bot.user],
        )

        await cog.on_message(msg)  # type: ignore[arg-type]

        embeds_sent = [
            call.kwargs.get("embed")
            for call in channel.send.call_args_list
            if call.kwargs.get("embed") is not None
        ]
        scene_embeds = [
            e for e in embeds_sent if e.title and "Le Village" in (e.title or "")
        ]
        assert scene_embeds, "expected a scene embed posted after MOVE"

    @pytest.mark.asyncio
    async def test_concurrent_action_is_refused_with_wait_message(self) -> None:
        """While one action is being processed, a second @bot mention in the
        same channel must be refused with a wait message — not dispatched."""
        session = _make_session(player_id=1)
        # Add a second registered player so the second message passes filters.
        scores = AbilityScores(
            STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10,
        )
        session.characters[2] = create_character(
            "Bera", Race.HUMAN, CharacterClass.FIGHTER, scores,
        )
        bot = _make_bot(sessions={1: session})
        cog = _make_cog(bot)

        gate = asyncio.Event()
        process_calls: list[str] = []

        def factory(**kwargs: Any) -> Any:
            pipeline = MagicMock()

            async def process(player_text: str, progress_callback: Any = None) -> Any:
                process_calls.append(player_text)
                await gate.wait()
                return ActionPipelineResult(
                    narrative="ok",
                    tone="dramatic",
                    mechanics_text="ok",
                    interpreted_action=InterpretedAction(
                        action_type=ActionType.LOOK,
                        actor_name="Aldric",
                        raw_input=player_text,
                    ),
                )

            pipeline.process = process
            return pipeline

        cog._pipeline_factory = factory  # type: ignore[assignment]

        msg1 = FakeMessage(
            content="<@9999> je fouille l'autel",
            author=FakeAuthor(id=1),
            channel=FakeChannel(id=1),
            mentions=[bot.user],
        )
        msg2 = FakeMessage(
            content="<@9999> j'attaque le gobelin",
            author=FakeAuthor(id=2),
            channel=FakeChannel(id=1),
            mentions=[bot.user],
        )

        # Start the first action; it will block on the gate.
        task1 = asyncio.create_task(cog.on_message(msg1))  # type: ignore[arg-type]
        # Yield until the first pipeline is actually running.
        for _ in range(20):
            if process_calls:
                break
            await asyncio.sleep(0)

        # Now fire the second action — it must be refused immediately.
        await cog.on_message(msg2)  # type: ignore[arg-type]

        msg2.reply.assert_called_once()
        wait_text = msg2.reply.call_args.args[0]
        assert "cours" in wait_text.lower() or "attends" in wait_text.lower()
        assert process_calls == ["je fouille l'autel"]

        # Release the first action and let it finish cleanly.
        gate.set()
        await task1

    @pytest.mark.asyncio
    async def test_post_non_move_does_not_post_scene(self) -> None:
        """A non-MOVE action does not trigger a scene embed."""
        location = Location(
            name="Le Village",
            description="x",
            connections=[],
            npcs_present=[],
            items_available=[],
        )
        result = ActionPipelineResult(
            narrative="Aldric regarde autour de lui.",
            tone="dramatic",
            mechanics_text="Aldric observes Le Village.",
            interpreted_action=InterpretedAction(
                action_type=ActionType.LOOK,
                actor_name="Aldric",
                raw_input="je regarde",
            ),
        )
        cog, bot, _session, _factory = self._setup_with_location(result, location)
        channel = FakeChannel(id=1)
        msg = FakeMessage(
            content="<@9999> je regarde autour de moi",
            author=FakeAuthor(id=1),
            channel=channel,
            mentions=[bot.user],
        )

        await cog.on_message(msg)  # type: ignore[arg-type]

        embeds_sent = [
            call.kwargs.get("embed")
            for call in channel.send.call_args_list
            if call.kwargs.get("embed") is not None
        ]
        scene_embeds = [
            e for e in embeds_sent if e.title and "Le Village" in (e.title or "")
        ]
        assert not scene_embeds, "non-MOVE actions must not post a scene embed"


# ---------------------------------------------------------------------------
# Confiance basse — confirmation Oui/Reformuler
# ---------------------------------------------------------------------------


def _low_confidence_output() -> LowConfidenceResult:
    return LowConfidenceResult(
        interpreted_action=InterpretedAction(
            action_type=ActionType.IMPROVISE,
            actor_name="Aldric",
            improvise_description="je danse",
            raw_input="je danse",
            confidence=0.3,
        ),
    )


def _success_output() -> ActionPipelineResult:
    return ActionPipelineResult(
        narrative="Tu danses.",
        tone="humorous",
        mechanics_text="",
        interpreted_action=InterpretedAction(
            action_type=ActionType.IMPROVISE,
            actor_name="Aldric",
            raw_input="je danse",
        ),
    )


def _campaign_message(bot: Any) -> FakeMessage:
    return FakeMessage(
        content="<@9999> je danse",
        author=FakeAuthor(id=1),
        channel=FakeChannel(id=1),
        mentions=[bot.user],
    )


class TestLowConfidenceFlow:
    @pytest.mark.asyncio
    async def test_confirm_resumes_via_process_interpreted_action(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        session = _make_session(player_id=1)
        bot = _make_bot(sessions={1: session})
        cog = _make_cog(bot)
        factory = FakePipelineFactory(_low_confidence_output())
        factory.resume_output = _success_output()
        cog._pipeline_factory = factory

        from bot.views import confirm_action_view as cav_module

        async def _instant_confirm(self: Any) -> None:
            self.confirmed = True

        monkeypatch.setattr(
            cav_module.ConfirmActionView, "wait", _instant_confirm,
        )

        await cog.on_message(_campaign_message(bot))  # type: ignore[arg-type]

        assert len(factory.process_interpreted_calls) == 1
        resumed = factory.process_interpreted_calls[0]
        assert resumed.action_type is ActionType.IMPROVISE
        assert resumed.confidence == 0.3

    @pytest.mark.asyncio
    async def test_reformulate_drops_action_without_resume(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        session = _make_session(player_id=1)
        bot = _make_bot(sessions={1: session})
        cog = _make_cog(bot)
        factory = FakePipelineFactory(_low_confidence_output())
        cog._pipeline_factory = factory

        from bot.views import confirm_action_view as cav_module

        async def _instant_timeout(self: Any) -> None:
            self.confirmed = False  # Reformuler et timeout : même chemin

        monkeypatch.setattr(
            cav_module.ConfirmActionView, "wait", _instant_timeout,
        )

        await cog.on_message(_campaign_message(bot))  # type: ignore[arg-type]

        assert factory.process_interpreted_calls == []
