"""Smoke tests for the action progress embed builder."""

from __future__ import annotations

from ai.entity_resolver import EntityCandidate
from ai.models import InterpretedAction
from bot.action_pipeline import AmbiguityResult, PipelinePhase
from bot.embeds.action_progress_embed import build_action_progress_embed
from bot.views.clarification_view import (
    ClarificationView,
    build_clarification_embed,
)
from engine.validators import ActionType


# ---------------------------------------------------------------------------
# build_action_progress_embed
# ---------------------------------------------------------------------------


class TestActionProgressEmbed:
    def test_first_phase_has_one_in_progress_marker(self) -> None:
        embed = build_action_progress_embed(
            actor_name="Aldric",
            raw_text="je regarde",
            current_phase=PipelinePhase.INTERPRETING,
            elapsed_seconds=0.4,
        )
        assert embed.title is not None
        assert "Aldric" in embed.title
        assert embed.description is not None
        assert "je regarde" in embed.description
        in_progress = [
            f for f in embed.fields if f.name and f.name.startswith("🔄")
        ]
        assert len(in_progress) == 1
        assert "Interprétation" in in_progress[0].name

    def test_done_phase_marks_all_phases_completed(self) -> None:
        embed = build_action_progress_embed(
            actor_name="Aldric",
            raw_text="je regarde",
            current_phase=PipelinePhase.DONE,
            elapsed_seconds=22.7,
        )
        completed = [
            f for f in embed.fields if f.name and f.name.startswith("✅")
        ]
        # All 6 phases should be marked done
        assert len(completed) == 6
        assert embed.footer is not None and "22.7s" in (embed.footer.text or "")

    def test_failed_phase_renders_failure_indicator(self) -> None:
        embed = build_action_progress_embed(
            actor_name="Aldric",
            raw_text="je regarde",
            current_phase=PipelinePhase.FAILED,
            elapsed_seconds=10.0,
        )
        failed = [
            f for f in embed.fields if f.name and f.name.startswith("❌")
        ]
        assert len(failed) == 6

    def test_long_text_is_truncated_in_description(self) -> None:
        long_text = "x" * 500
        embed = build_action_progress_embed(
            actor_name="Aldric",
            raw_text=long_text,
            current_phase=PipelinePhase.INTERPRETING,
            elapsed_seconds=0.0,
        )
        assert embed.description is not None
        assert len(embed.description) <= 250  # truncated + "..."


# ---------------------------------------------------------------------------
# ClarificationView + clarification embed
# ---------------------------------------------------------------------------


def _make_ambiguity() -> AmbiguityResult:
    partial = InterpretedAction(
        action_type=ActionType.TALK,
        actor_name="Aldric",
        target_name="Marc",
        raw_input="je parle à Marc",
        confidence=0.4,
    )
    return AmbiguityResult(
        field_name="target_name",
        raw_value="Marc",
        candidates=[
            EntityCandidate(id="Frère Marc", label="Frère Marc", description="Vieux moine"),
            EntityCandidate(id="Frère Marc le Sage", label="Frère Marc le Sage", description="Jeune novice"),
        ],
        partial_action=partial,
    )


class TestClarificationView:
    def test_renders_one_button_per_candidate_plus_cancel(self) -> None:
        view = ClarificationView(_make_ambiguity(), author_id=42)
        # 2 candidates + 1 cancel
        assert len(view.children) == 3

    def test_caps_candidates_at_four(self) -> None:
        partial = InterpretedAction(
            action_type=ActionType.TALK,
            actor_name="Aldric",
            target_name="Marc",
            raw_input="je parle",
        )
        many = AmbiguityResult(
            field_name="target_name",
            raw_value="Marc",
            candidates=[
                EntityCandidate(id=f"id{i}", label=f"Marc {i}")
                for i in range(6)
            ],
            partial_action=partial,
        )
        view = ClarificationView(many, author_id=42)
        # 4 candidates + 1 cancel
        assert len(view.children) == 5

    def test_chosen_entity_id_starts_none(self) -> None:
        view = ClarificationView(_make_ambiguity(), author_id=42)
        assert view.chosen_entity_id is None
        assert view.cancelled is False


class TestClarificationEmbed:
    def test_includes_raw_value_and_candidate_descriptions(self) -> None:
        embed = build_clarification_embed(_make_ambiguity())
        assert embed.description is not None
        assert "Marc" in embed.description
        names = {f.name for f in embed.fields}
        assert "Frère Marc" in names
        assert "Frère Marc le Sage" in names
