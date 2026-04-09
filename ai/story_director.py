"""Story Director — periodic narrative coherence checker."""

import logging
from pathlib import Path

from ai.client import OllamaClient
from ai.models import DirectorNote
from memory.models import SemanticDocument, SemanticDocumentType
from memory.semantic import SemanticMemory

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "system_story_director.txt").read_text()


class StoryDirector:
    """Checks narrative coherence and stores hooks in semantic memory.

    Should be triggered when campaign.interaction_count % 20 == 0.
    The caller is responsible for checking the trigger condition.
    """

    MODEL = "qwen3.5:9b"

    def __init__(self, client: OllamaClient, semantic_memory: SemanticMemory) -> None:
        self._client = client
        self._semantic = semantic_memory

    def check_coherence(
        self,
        campaign_id: str,
        context_prompt: str,
    ) -> DirectorNote:
        """Analyze campaign context for coherence issues and story hooks.

        Side effect: stores the DirectorNote as a SemanticDocument in memory
        so future context assembly benefits from it.

        Args:
            campaign_id: The campaign to analyze.
            context_prompt: Assembled context from ContextAssembler.

        Returns:
            DirectorNote with coherence issues, hooks, and priority.
        """
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": context_prompt},
        ]

        data = self._client.chat_json(self.MODEL, messages, temperature=0.7, think=True)
        raw_hooks: list[str] = data.get("suggested_hooks", [])
        deduped_hooks = list(dict.fromkeys(
            h.strip().lower() for h in raw_hooks if h.strip()
        ))
        # Restore original casing: pick the first raw hook matching each key.
        hooks_by_key = {h.strip().lower(): h.strip() for h in reversed(raw_hooks) if h.strip()}
        unique_hooks = [hooks_by_key[k] for k in deduped_hooks]

        note = DirectorNote(
            coherence_issues=data.get("coherence_issues", []),
            suggested_hooks=unique_hooks,
            priority=data.get("priority", "low"),
        )

        logger.info(
            "DIRECTOR campaign=%s issues=%d hooks=%d priority=%s",
            campaign_id, len(note.coherence_issues),
            len(note.suggested_hooks), note.priority,
        )
        self._store_in_memory(campaign_id, note)
        return note

    def _store_in_memory(self, campaign_id: str, note: DirectorNote) -> None:
        """Persist the DirectorNote as a SemanticDocument for future retrieval."""
        issues_text = (
            f"Issues: {'; '.join(note.coherence_issues)}" if note.coherence_issues else "No issues."
        )
        hooks_text = f"Hooks: {'; '.join(note.suggested_hooks)}"
        content = f"[Story Director Note — Priority: {note.priority}]\n{issues_text}\n{hooks_text}"

        doc = SemanticDocument(
            campaign_id=campaign_id,
            doc_type=SemanticDocumentType.PAST_EVENT,
            content=content,
            metadata={"source": "story_director", "priority": note.priority},
        )
        self._semantic.add_documents([doc])
