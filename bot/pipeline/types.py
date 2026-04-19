"""Shared pipeline data types.

``PipelineContext`` carries per-action data through the stages.
``PipelineDeps`` carries long-lived service references (LLM clients, etc.).

Stages follow this signature:

    async def run(ctx: PipelineContext, *, deps: PipelineDeps) -> PipelineContext

Each stage builds on the previous one's output by calling
``ctx.model_copy(update={...})`` — never mutates fields set earlier.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from ai.interpreter import Interpreter
    from ai.narrator import Narrator


class PipelineContext(BaseModel):
    """Carried through every stage. Stages add fields, never mutate earlier ones.

    The "pending_*" / "trivial_kill_mechanics" fields exist to preserve the
    legacy side-channel state previously held on ``ActionPipeline`` instance
    attributes. They are read by the Facade adapter and by some downstream
    callers (e.g. ``ActionHandlerCog`` reads ``pending_combat_start_embed``).
    """

    model_config = {"arbitrary_types_allowed": True}

    # --- Per-action input ---
    campaign_id: str
    player_message_id: int
    player_input: str
    actor_name: str
    language: str = "fr"

    # --- Set by interpret stage ---
    interpreted: Any = None  # InterpretedAction | None
    validation_error: str | None = None

    # --- Set by resolve stage ---
    mechanics_outcome: Any = None  # MechanicsOutcome | None
    beat_advanced: bool = False
    new_beat: Any = None  # StoryBeat | None — typed as Any to avoid heavy import
    combat_state_after: Any = None  # CombatState | None

    # --- Set by narrate stage ---
    assembled_context: str | None = None
    narrative_result: Any = None  # NarrativeResult | None

    # --- Side-channel state (legacy compatibility) ---
    pending_flee_destination: str | None = None
    pending_combat_start_embed: Any = None  # tuple[CombatState, CombatTrigger] | None
    pending_dice_embeds: list[Any] = Field(default_factory=list)
    trivial_kill_mechanics: str | None = None


@dataclass(frozen=True)
class PipelineDeps:
    """Long-lived services injected into stage functions.

    Frozen so that stages cannot mutate the dependency graph mid-pipeline.
    """

    interpreter: "Interpreter"
    narrator: "Narrator"
