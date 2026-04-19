"""Unit tests for PipelineContext and PipelineDeps."""

from unittest.mock import MagicMock

import pytest

from ai.interpreter import Interpreter
from ai.models import InterpretedAction
from ai.narrator import Narrator
from bot.pipeline.types import PipelineContext, PipelineDeps
from engine.validators import ActionType


class TestPipelineContext:
    def test_minimal_construction(self) -> None:
        ctx = PipelineContext(
            campaign_id="cmp_test",
            player_message_id=42,
            player_input="Je fouille la pièce.",
            actor_name="Thorin",
        )
        assert ctx.campaign_id == "cmp_test"
        assert ctx.language == "fr"
        assert ctx.interpreted is None
        assert ctx.mechanics_outcome is None
        assert ctx.beat_advanced is False

    def test_optional_stage_fields_default_none(self) -> None:
        ctx = PipelineContext(
            campaign_id="cmp", player_message_id=1, player_input="x", actor_name="X",
        )
        assert ctx.validation_error is None
        assert ctx.combat_state_after is None
        assert ctx.assembled_context is None
        assert ctx.narrative_result is None

    def test_can_attach_interpreted_action(self) -> None:
        ctx = PipelineContext(
            campaign_id="cmp", player_message_id=1, player_input="x", actor_name="X",
        )
        action = InterpretedAction(
            action_type=ActionType.SEARCH,
            actor_name="Thorin",
            raw_input="Je fouille.",
        )
        ctx2 = ctx.model_copy(update={"interpreted": action})
        assert ctx2.interpreted is action

    def test_pending_side_channels_default_empty(self) -> None:
        ctx = PipelineContext(
            campaign_id="cmp", player_message_id=1, player_input="x", actor_name="X",
        )
        assert ctx.pending_flee_destination is None
        assert ctx.pending_combat_start_embed is None
        assert ctx.pending_dice_embeds == []
        assert ctx.trivial_kill_mechanics is None


class TestPipelineDeps:
    def test_construction(self) -> None:
        interpreter = MagicMock(spec=Interpreter)
        narrator = MagicMock(spec=Narrator)
        deps = PipelineDeps(interpreter=interpreter, narrator=narrator)
        assert deps.interpreter is interpreter
        assert deps.narrator is narrator

    def test_deps_is_frozen(self) -> None:
        interpreter = MagicMock(spec=Interpreter)
        narrator = MagicMock(spec=Narrator)
        deps = PipelineDeps(interpreter=interpreter, narrator=narrator)
        with pytest.raises((AttributeError, TypeError)):
            deps.interpreter = MagicMock(spec=Interpreter)  # type: ignore[misc]
