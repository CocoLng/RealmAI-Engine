"""Action pipeline package — splits the legacy ActionPipeline into stages."""

from bot.pipeline import interpret, narrate, orchestrator, resolve
from bot.pipeline.orchestrator import (
    ActionPipelineResult,
    AmbiguityResult,
    PipelineOutput,
    PipelinePhase,
    PipelineRunner,
    ProgressCallback,
    UnknownEntityResult,
)

__all__ = [
    "ActionPipelineResult",
    "AmbiguityResult",
    "PipelineOutput",
    "PipelinePhase",
    "PipelineRunner",
    "ProgressCallback",
    "UnknownEntityResult",
    "interpret",
    "narrate",
    "orchestrator",
    "resolve",
]
