"""Opening Reframer --- rewrites the campaign opening around the real party.

The arc generator runs in parallel with onboarding, so its ``premise`` /
``situation`` / ``call_to_action`` are written *before* the players have
picked their classes, kits, and motivations. The Reframer closes that gap:
right before launch, it takes the original opening plus the final party
composition, and rewrites the four opening surfaces (premise, situation,
call_to_action, arrival_hook) so the party's chosen roles and motivations
are honored — no more Rogue Shadow Blades cast as "the last guardians."

It also returns a one-sentence ``party_premise`` — a frozen fact persisted
on the :class:`world.story_arc.StoryArc` and surfaced to downstream
narrators / the story director so framing stays consistent turn after turn.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from ai.client import OllamaClient
from ai.language import language_instruction

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    Path(__file__).parent / "prompts" / "system_opening_reframer.txt"
).read_text()


class PartyMember(BaseModel):
    """One player character's opening-narrative-relevant facts."""

    name: str
    race: str
    char_class: str
    kit: str
    """Canonical English kit name (e.g. ``"Shadow Blade"``). The prompt
    describes what common kits imply so the LLM can translate them into
    narrative roles without the launcher pre-translating."""
    motivation: str
    """Canonical English motivation key — one of ``"Contract"``, ``"Personal"``,
    ``"Curiosity"``, ``"Conviction"``. The prompt maps each key to a framing."""


class ReframedOpening(BaseModel):
    """The four opening surfaces rewritten around the party + a frozen premise."""

    premise: str = Field(min_length=10)
    situation: str = Field(min_length=10)
    call_to_action: str = Field(min_length=10)
    arrival_hook: str = Field(min_length=10)
    party_premise: str = Field(min_length=5)


class OpeningReframer:
    """Post-onboarding re-anchoring pass: reshape the arc's opening around
    the real party composition.

    One LLM call (~20-30s) that runs after the story arc + location +
    onboarding are all ready, and before the opening scene is posted.
    Failures are non-fatal at the call site — callers should fall back
    to the original arc text rather than block the launch.
    """

    MODEL = "qwen3.5:9b"

    def __init__(self, client: OllamaClient) -> None:
        self._client = client

    def reframe(
        self,
        *,
        original_premise: str,
        original_situation: str,
        original_call_to_action: str,
        original_arrival_hook: str,
        location_name: str,
        villain_name: str,
        first_beat_description: str,
        party: list[PartyMember],
        language: str = "fr",
    ) -> ReframedOpening:
        """Return a :class:`ReframedOpening` honoring the party's roles.

        Raises:
            pydantic.ValidationError: If the LLM response cannot be coerced
                into the :class:`ReframedOpening` schema. Callers are expected
                to swallow this and fall back to the original arc text — the
                reframer is a polish step, not a gate.
        """
        if not party:
            raise ValueError("Reframer requires at least one party member")

        lang_prefix = language_instruction(language)
        system_prompt = lang_prefix + _SYSTEM_PROMPT
        user_content = self._build_user_message(
            original_premise=original_premise,
            original_situation=original_situation,
            original_call_to_action=original_call_to_action,
            original_arrival_hook=original_arrival_hook,
            location_name=location_name,
            villain_name=villain_name,
            first_beat_description=first_beat_description,
            party=party,
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        data = self._client.chat_json(
            self.MODEL, messages, temperature=0.7, think=False,
        )

        try:
            reframed = ReframedOpening.model_validate(data)
        except ValidationError:
            logger.warning(
                "REFRAME validation failed — raw keys=%s",
                sorted(data.keys()) if isinstance(data, dict) else type(data).__name__,
            )
            raise

        logger.info(
            "REFRAME ok party_premise=%r call_to_action_head=%r",
            reframed.party_premise[:80],
            reframed.call_to_action[:80],
        )
        return reframed

    @staticmethod
    def _build_user_message(
        *,
        original_premise: str,
        original_situation: str,
        original_call_to_action: str,
        original_arrival_hook: str,
        location_name: str,
        villain_name: str,
        first_beat_description: str,
        party: list[PartyMember],
    ) -> str:
        """Render the party composition and originals into a prompt block."""
        lines = [
            "## Original opening (generated without knowing the players)",
            f"premise: {original_premise}",
            f"situation: {original_situation}",
            f"call_to_action: {original_call_to_action}",
            f"arrival_hook: {original_arrival_hook}",
            "",
            "## Campaign anchors (MUST be preserved)",
            f"Starting location: {location_name}",
            f"Villain (never spoil the name): {villain_name}",
            f"First beat objective: {first_beat_description}",
            "",
            "## Party composition",
        ]
        for member in party:
            lines.append(
                f"- {member.name} ({member.race} {member.char_class}) — "
                f"kit: {member.kit} — motivation: {member.motivation}",
            )
        lines.extend([
            "",
            "Rewrite the four opening surfaces so they honor the party's kits "
            "and motivations. Return only the JSON object described in the "
            "system prompt.",
        ])
        return "\n".join(lines)
