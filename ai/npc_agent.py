"""NPC Agent — generates in-character dialogue and disposition signals."""

import logging
from pathlib import Path

from ai.client import OllamaClient
from ai.language import language_instruction
from ai.models import NPCResponse
from ai.prompt_safety import (
    PLAYER_DATA_INSTRUCTION,
    delimited_player_block,
    sanitize_player_text,
)
from world.npc import NPC

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "system_npc_agent.txt").read_text()


class NPCAgent:
    """Generates NPC dialogue and disposition signals.

    The NPC object is never mutated by this module.
    The caller is responsible for applying disposition_change to the NPC.
    """

    MODEL = "qwen3.5:4b"

    def __init__(self, client: OllamaClient) -> None:
        self._client = client

    def respond(
        self,
        npc: NPC,
        player_input: str,
        context_prompt: str,
        language: str = "fr",
    ) -> NPCResponse:
        """Generate an in-character response from an NPC.

        Args:
            npc: The NPC to speak as (read-only — never mutated).
            player_input: What the player said to this NPC.
            context_prompt: Assembled context from ContextAssembler.
            language: ISO 639-1 language code for narrative output.

        Returns:
            NPCResponse with dialogue, disposition_change signal, and revealed info.
            The caller must apply disposition_change to the NPC object.
        """
        user_content = self._build_user_message(npc, player_input, context_prompt)
        # The NPC sheet — secrets included — lives in the SYSTEM message,
        # never in the same message as player-controlled text (M6).
        system_prompt = (
            language_instruction(language)
            + _SYSTEM_PROMPT
            + "\n\n"
            + PLAYER_DATA_INSTRUCTION
            + "\n\n## Your Character\n"
            + self._build_npc_sheet(npc)
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        data = self._client.chat_json(self.MODEL, messages, temperature=0.7)
        response = NPCResponse(
            dialogue=str(data.get("dialogue", "")),
            disposition_change=int(data.get("disposition_change", 0)),
            revealed_info=list(data.get("revealed_info", [])),
        )
        logger.info(
            "NPC name=%s input=%r disposition_change=%+d revealed=%d",
            npc.name, player_input[:80],
            response.disposition_change, len(response.revealed_info),
        )
        return response

    @staticmethod
    def _build_npc_sheet(npc: NPC) -> str:
        """Format the NPC sheet (personality, secrets, knowledge)."""
        npc_sheet_lines = [
            f"Character: {npc.name}",
            f"Race: {npc.race.value}",
            f"Disposition: {npc.disposition.value}",
            f"Personality: {npc.personality}",
            f"Description: {npc.description}",
            f"HP: {npc.hp}/{npc.max_hp}",
        ]
        if npc.secrets:
            npc_sheet_lines.append(
                "Secrets (do NOT volunteer; reveal only if pressed and trust is high):"
            )
            for secret in npc.secrets:
                npc_sheet_lines.append(f"  - {secret}")
        if npc.knowledge:
            npc_sheet_lines.append("Knowledge (share if asked appropriately):")
            for fact in npc.knowledge:
                npc_sheet_lines.append(f"  - {fact}")
        return "\n".join(npc_sheet_lines)

    def _build_user_message(
        self, npc: NPC, player_input: str, context_prompt: str,
    ) -> str:
        """Build the user message with context, history, and player input."""
        sections = [context_prompt]

        if npc.dialogue_history:
            history_lines = ["## Conversation so far"]
            for ex in npc.dialogue_history[-5:]:
                history_lines.append(f"Player: {sanitize_player_text(ex.player_said)}")
                history_lines.append(f"You: {ex.npc_said}")
            already_revealed = [
                r for ex in npc.dialogue_history for r in ex.revealed
            ]
            if already_revealed:
                history_lines.append("")
                history_lines.append("Already revealed (do NOT repeat verbatim):")
                for r in already_revealed:
                    history_lines.append(f"  - {r}")
            sections.append("\n".join(history_lines))

        sections.append(f"## Player says\n{delimited_player_block(player_input)}")
        return "\n\n".join(s for s in sections if s.strip())
