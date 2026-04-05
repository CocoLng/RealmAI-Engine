"""NPC Agent — generates in-character dialogue and disposition signals."""

import logging
from pathlib import Path

from ai.client import OllamaClient
from ai.models import NPCResponse
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
    ) -> NPCResponse:
        """Generate an in-character response from an NPC.

        Args:
            npc: The NPC to speak as (read-only — never mutated).
            player_input: What the player said to this NPC.
            context_prompt: Assembled context from ContextAssembler.

        Returns:
            NPCResponse with dialogue, disposition_change signal, and revealed info.
            The caller must apply disposition_change to the NPC object.
        """
        user_content = self._build_user_message(npc, player_input, context_prompt)
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        data = self._client.chat_json(self.MODEL, messages, temperature=0.7)
        return NPCResponse(
            dialogue=str(data.get("dialogue", "")),
            disposition_change=int(data.get("disposition_change", 0)),
            revealed_info=list(data.get("revealed_info", [])),
        )

    def _build_user_message(self, npc: NPC, player_input: str, context_prompt: str) -> str:
        """Build the user message with NPC sheet and player input."""
        npc_sheet = (
            f"Character: {npc.name}\n"
            f"Race: {npc.race.value}\n"
            f"Disposition: {npc.disposition.value}\n"
            f"Personality: {npc.personality}\n"
            f"Description: {npc.description}\n"
            f"HP: {npc.hp}/{npc.max_hp}"
        )
        return f"{context_prompt}\n\n## Your Character\n{npc_sheet}\n\n## Player says\n{player_input}"
