"""Context Assembler — combines all 4 memory layers into a single prompt.

This is the single entry point called before each Narrator LLM call.
Orchestrates Layer 1 (structured state), Layer 2 (sliding window),
Layer 3 (compressed summaries), and Layer 4 (semantic RAG).
"""

import logging

from sqlalchemy.orm import Session

from ai.client import OllamaClient
from engine.character import Character
from engine.combat import CombatState
from engine.inventory import Inventory
from memory.models import ContextBudget, ExchangeRole, NarrativeExchange
from memory.semantic import SemanticMemory
from memory.sliding_window import SlidingWindow
from memory.state import StateBuilder
from memory.summarizer import Summarizer
from memory.token_utils import estimate_tokens, truncate_to_tokens

logger = logging.getLogger(__name__)


class ContextAssembler:
    """Combines all 4 memory layers into a single prompt string."""

    def __init__(
        self,
        session: Session,
        semantic_memory: SemanticMemory,
        ollama_client: OllamaClient,
        budget: ContextBudget | None = None,
    ) -> None:
        self._state_builder = StateBuilder(session)
        self._sliding_window = SlidingWindow(session)
        self._summarizer = Summarizer(session, ollama_client)
        self._semantic = semantic_memory
        self._budget = budget or ContextBudget()

    def assemble(
        self,
        campaign_id: str,
        player_input: str,
        player_characters: list[Character] | None = None,
        combat_state: CombatState | None = None,
        inventories: dict[str, Inventory] | None = None,
    ) -> str:
        """Build the full context prompt for the Narrator LLM."""
        logger.info("CONTEXT campaign=%s input=%r", campaign_id, player_input[:80])

        # 1. Auto-summarization side effect
        if self._summarizer.should_summarize(campaign_id):
            logger.info("SUMMARY triggered campaign=%s", campaign_id)
            self._summarizer.summarize(campaign_id)

        # 2. Build each layer
        state_summary = self._state_builder.build(
            campaign_id, player_characters, combat_state, inventories,
        )
        layer1_text = self._state_builder.render(
            state_summary, self._budget.layer1_max,
        )

        window = self._sliding_window.get_window(campaign_id)
        layer2_text = self._sliding_window.render(
            window, self._budget.layer2_max,
        )

        summaries = self._summarizer.get_recent_summaries(campaign_id)
        layer3_text = self._summarizer.render(
            summaries, self._budget.layer3_max,
        )

        rag_query = self._build_rag_query(player_input, window)
        relevant_docs = self._semantic.query(campaign_id, rag_query)
        layer4_text = self._semantic.render(
            relevant_docs, self._budget.layer4_max,
        )

        logger.info(
            "CONTEXT layers L1=%d L2=%d L3=%d L4=%d total=%d tokens",
            estimate_tokens(layer1_text), estimate_tokens(layer2_text),
            estimate_tokens(layer3_text), estimate_tokens(layer4_text),
            estimate_tokens(layer1_text) + estimate_tokens(layer2_text)
            + estimate_tokens(layer3_text) + estimate_tokens(layer4_text),
        )

        # 3. Assemble with priority-based truncation
        return self._assemble_prompt(
            layer1_text, layer2_text, layer3_text, layer4_text,
        )

    @staticmethod
    def _build_rag_query(
        player_input: str,
        recent_exchanges: list["NarrativeExchange"],
    ) -> str:
        """Combine the current input with the last 3 narrative exchange snippets.

        Truncates each exchange's content to ~120 characters so the query
        remains short and focused on the freshest signal.
        """
        snippets = [ex.content[:120] for ex in recent_exchanges[-3:]]
        return "\n".join(snippets + [player_input])

    def record_exchange(
        self,
        campaign_id: str,
        role: ExchangeRole,
        content: str,
        interaction_number: int,
    ) -> NarrativeExchange:
        """Record a new exchange in the sliding window."""
        return self._sliding_window.add_exchange(
            campaign_id, role, content, interaction_number,
        )

    def _assemble_prompt(
        self, layer1: str, layer2: str, layer3: str, layer4: str,
    ) -> str:
        """Combine layers, respecting total budget.

        Priority for truncation (lowest priority first):
        Layer 4 > Layer 3 > Layer 2 > Layer 1.
        Layer 1 (game state) is never truncated.
        """
        sections = [s for s in [layer1, layer2, layer3, layer4] if s]
        combined = "\n\n".join(sections)

        total = estimate_tokens(combined)
        if total <= self._budget.total_max:
            return combined

        # Truncate from lowest priority up
        layers = [layer1, layer2, layer3, layer4]
        for i in [3, 2, 1]:  # layer4, layer3, layer2
            if not layers[i]:
                continue
            excess = total - self._budget.total_max
            if excess <= 0:
                break
            layer_tokens = estimate_tokens(layers[i])
            new_budget = max(0, layer_tokens - excess)
            if new_budget == 0:
                total -= layer_tokens
                layers[i] = ""
            else:
                layers[i] = truncate_to_tokens(layers[i], new_budget)
                total = sum(estimate_tokens(ly) for ly in layers if ly)

        # Final clamp: ceil() rounding may leave 1-3 tokens over budget
        total = sum(estimate_tokens(ly) for ly in layers if ly)
        if total > self._budget.total_max:
            for i in [3, 2, 1]:
                if layers[i]:
                    overage = total - self._budget.total_max
                    new_budget = max(0, estimate_tokens(layers[i]) - overage)
                    layers[i] = truncate_to_tokens(layers[i], new_budget)
                    total = sum(estimate_tokens(ly) for ly in layers if ly)
                    if total <= self._budget.total_max:
                        break

        sections = [s for s in layers if s]
        return "\n\n".join(sections)
