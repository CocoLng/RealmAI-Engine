"""Dedicated narrator path for boss phase transitions (task 71).

When :func:`engine.combat.check_phase_transition` fires, an unconsumed
:class:`engine.combat.PhaseTransitionEvent` is appended to
``CombatState.pending_phase_narrations``. The action pipeline calls
:func:`narrate_phase_transition` on each unconsumed event after the main
action narration has been produced, and wraps the resulting prose in a
distinct gold embed so the phase shift reads as a cinematic beat rather
than a line buried inside the combat log.

The engine never touches this module. The narrator never touches dice.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ai.client import OllamaClient
from ai.language import language_instruction
from engine.combat import Combatant, CombatState, PhaseTransitionEvent

logger = logging.getLogger(__name__)

MODEL = "qwen3.5:9b"

_PHASE_SYSTEM_PROMPT = (
    Path(__file__).parent / "prompts" / "system_narrator_phase.txt"
).read_text()


def narrate_phase_transition(
    client: OllamaClient,
    event: PhaseTransitionEvent,
    boss: Combatant,
    state: CombatState,
    language: str = "fr",
) -> str:
    """Generate a short cinematic narration for a boss phase transition.

    Args:
        client: Shared Ollama client instance.
        event: The unconsumed phase event produced by ``apply_damage``.
        boss: The combatant whose HP threshold was crossed.
        state: The current combat state (used for round context only).
        language: ISO 639-1 language code for the output narration.

    Returns:
        A 3 to 5 sentence prose paragraph. The caller is responsible for
        setting ``event.consumed = True`` so the event is not narrated
        twice on action retries.
    """
    system = language_instruction(language) + _PHASE_SYSTEM_PROMPT
    user = _build_phase_context(event, boss, state)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    logger.info(
        "PHASE_NARRATE boss=%s phase=%d round=%d",
        boss.name, event.phase_index, state.round_number,
    )
    data = client.chat_json(MODEL, messages, temperature=0.85)
    narration = str(data.get("narration", "")).strip()
    logger.info("PHASE_NARRATE output=%r", narration[:200])
    return narration


def _build_phase_context(
    event: PhaseTransitionEvent,
    boss: Combatant,
    state: CombatState,
) -> str:
    """Assemble the user-facing context for a phase-transition narration."""
    cue = event.narrative_cue.strip() or "(pas de cue fourni)"
    return "\n".join(
        [
            "# Phase transition event",
            f"Boss : {boss.name}",
            f"HP actuels : {boss.character.hp}/{boss.character.max_hp}",
            f"Round : {state.round_number}",
            f"Phase franchie : index {event.phase_index}",
            "",
            "## Narrative cue (base à amplifier)",
            cue,
            "",
            "## Ta tâche",
            (
                "Narre ce basculement en 3 à 5 phrases. Ton sombre, phrases "
                "courtes, aucun chiffre mécanique. Termine sur une menace "
                "implicite — le combat continue DIFFÉREMMENT maintenant."
            ),
        ]
    )
