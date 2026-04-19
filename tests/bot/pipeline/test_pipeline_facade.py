"""Facade contract tests — pin the public surface of bot.action_pipeline."""

import inspect

from bot.action_pipeline import (
    ActionPipeline,
    ActionPipelineResult,
    AmbiguityResult,
    PipelineOutput,
    PipelinePhase,
    UnknownEntityResult,
    is_trivially_defeatable,
)


def test_facade_exports_action_pipeline_class() -> None:
    assert inspect.isclass(ActionPipeline)


def test_facade_exports_result_types() -> None:
    assert inspect.isclass(ActionPipelineResult)
    assert inspect.isclass(AmbiguityResult)
    assert inspect.isclass(UnknownEntityResult)


def test_facade_pipeline_output_alias_is_union() -> None:
    members = getattr(PipelineOutput, "__args__", ())
    assert ActionPipelineResult in members
    assert AmbiguityResult in members
    assert UnknownEntityResult in members


def test_facade_phase_enum_has_expected_phases() -> None:
    expected = {"PENDING", "INTERPRETING", "RESOLVING_ENTITIES", "VALIDATING",
                "RESOLVING_ACTION", "ASSEMBLING_CONTEXT", "NARRATING", "DONE", "FAILED"}
    assert {p.name for p in PipelinePhase} >= expected


def test_facade_action_pipeline_has_three_public_methods() -> None:
    method_names = {m for m in dir(ActionPipeline) if not m.startswith("_")}
    assert {"process", "resume_with_resolution", "process_interpreted_action"} <= method_names


def test_facade_is_trivially_defeatable_callable() -> None:
    assert callable(is_trivially_defeatable)


def test_facade_action_pipeline_constructor_accepts_legacy_kwargs() -> None:
    """The Facade must accept the same kwargs the dataclass version did."""
    sig = inspect.signature(ActionPipeline)
    params = sig.parameters
    expected = {"interpreter", "narrator", "location", "npcs", "actor_name",
                "language", "campaign_id", "combat_state", "inventory",
                "session", "db_factory"}
    assert expected <= set(params)
