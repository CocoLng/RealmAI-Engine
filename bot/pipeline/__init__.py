"""Action pipeline package — splits the legacy ActionPipeline into stages.

Stages:
- ``interpret`` — text → InterpretedAction + entity resolution + validation
- ``resolve``   — mechanics dispatch + combat helpers + beat completion check
- ``narrate``   — context assembly + Narrator call + refusal narrators

The orchestrator wires them together and the Facade in
``bot.action_pipeline.ActionPipeline`` preserves the legacy public API.
"""

from bot.pipeline import interpret, narrate, resolve
from bot.pipeline.types import PipelineContext, PipelineDeps

__all__ = ["PipelineContext", "PipelineDeps", "interpret", "narrate", "resolve"]
