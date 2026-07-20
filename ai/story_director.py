"""Story Director — periodic narrative coherence checker."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ai.client import LLMParseError, OllamaClient
from ai.models import DirectorNote
from memory.models import SemanticDocument, SemanticDocumentType
from memory.semantic import SemanticMemory

if TYPE_CHECKING:
    from engine.beat_progression import BeatProgress

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "system_story_director.txt").read_text()
_BRAINSTORM_PROMPT = (Path(__file__).parent / "prompts" / "brainstorm_story_check.txt").read_text()

# ---------------------------------------------------------------------------
# Module-level note cache — populated after every check_coherence() call
# ---------------------------------------------------------------------------

_LATEST_NOTES: dict[str, DirectorNote] = {}


def cached_note_for(campaign_id: str) -> DirectorNote | None:
    """Most recent DirectorNote for ``campaign_id``, if any."""
    return _LATEST_NOTES.get(campaign_id)


def _store_latest_note(campaign_id: str, note: DirectorNote) -> None:
    """Store the latest DirectorNote for ``campaign_id``."""
    _LATEST_NOTES[campaign_id] = note


def invalidate_note(campaign_id: str) -> None:
    """Drop the cached DirectorNote for ``campaign_id``.

    Called by the orchestrator when engine state moves past what the note
    describes (beat advance, location change) — a stale note would keep
    steering the narrator toward the previous beat/scene.
    """
    _LATEST_NOTES.pop(campaign_id, None)


def reset_latest_notes() -> None:
    """Test helper — clear the cache."""
    _LATEST_NOTES.clear()


class StoryDirector:
    """Checks narrative coherence and stores hooks in semantic memory.

    Should be triggered when campaign.interaction_count % 20 == 0.
    The caller is responsible for checking the trigger condition.

    Uses a 2-call chain:
      1. Brainstorm coherence analysis angles (think=False, dedicated cap)
      2. Generate structured coherence report JSON (think=False)
    """

    MODEL = "qwen3.5:9b"

    BRAINSTORM_NUM_PREDICT = 1024
    """Output-token cap for the brainstorm call (3 short options ≈ 300-500
    tokens). The call used to run think=True with a 2048 num_predict — but
    num_predict caps thinking + content COMBINED, and the 9b's reasoning
    trace saturated it on every live cadence (15/15 on 2026-07-19): ~112 s
    of GPU per run for an empty content and a fallback. think=False with a
    dedicated cap keeps the brainstorm fast and always parseable."""

    def __init__(self, client: OllamaClient, semantic_memory: SemanticMemory) -> None:
        self._client = client
        self._semantic = semantic_memory

    def check_coherence(
        self,
        campaign_id: str,
        context_prompt: str,
        beat_progress: "BeatProgress | None" = None,
    ) -> DirectorNote:
        """Analyze campaign context for coherence issues and story hooks.

        Side effect: stores the DirectorNote as a SemanticDocument in memory
        so future context assembly benefits from it.

        Args:
            campaign_id: The campaign to analyze.
            context_prompt: Assembled context from ContextAssembler.
            beat_progress: Optional engine snapshot of current beat progression.
                When provided, appended to context_prompt as authoritative truth.

        Returns:
            DirectorNote with coherence issues, hooks, and priority.
        """
        if beat_progress is not None:
            progress_block = self._format_beat_progress(beat_progress)
            context_prompt = f"{context_prompt}\n\n{progress_block}"

        # --- Call 1: Brainstorm (think=False, dedicated cap) ---
        brainstorm_context = self._brainstorm(context_prompt)

        # --- Call 2: Generate (think=False) ---
        if brainstorm_context:
            generate_user = (
                f"{context_prompt}\n\n"
                f"## Brainstorm analysis\n{brainstorm_context}\n\n"
                f"Using the analysis above, generate the coherence report."
            )
        else:
            generate_user = context_prompt

        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": generate_user},
        ]

        data = self._client.chat_json(self.MODEL, messages, temperature=0.7, think=False)
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
            current_objective=str(data.get("current_objective", "")),
            current_beat_atmosphere=str(data.get("current_beat_atmosphere", "")),
            forbidden_topics=list(data.get("forbidden_topics") or []),
            required_mentions=list(data.get("required_mentions") or []),
        )

        logger.info(
            "DIRECTOR campaign=%s issues=%d hooks=%d priority=%s",
            campaign_id, len(note.coherence_issues),
            len(note.suggested_hooks), note.priority,
        )
        self._store_in_memory(campaign_id, note)
        _store_latest_note(campaign_id, note)
        return note

    def _brainstorm(self, context_prompt: str) -> str | None:
        """Run the brainstorm call (Call 1) and return analysis context.

        Returns None if the brainstorm call fails, allowing graceful fallback.
        """
        messages = [
            {"role": "system", "content": _BRAINSTORM_PROMPT},
            {"role": "user", "content": context_prompt},
        ]
        try:
            data: dict[str, Any] = self._client.chat_json(
                self.MODEL, messages, temperature=0.7, think=False,
                num_predict=self.BRAINSTORM_NUM_PREDICT,
            )
            logger.info("DIRECTOR brainstorm returned %d options", len(data.get("options", [])))
            return self._format_brainstorm(data)
        except (LLMParseError, KeyError, ValueError) as exc:
            logger.warning("DIRECTOR brainstorm failed, falling back to single-call: %s", exc)
            return None

    @staticmethod
    def _format_brainstorm(data: dict[str, Any]) -> str:
        """Format brainstorm output into a concise context string."""
        options = data.get("options", [])
        parts: list[str] = []
        for opt in options:
            marker = "[SELECTED] " if opt.get("selected") else ""
            concept = opt.get("concept", "")
            elements = opt.get("key_elements", [])
            elements_str = "; ".join(str(e) for e in elements)
            parts.append(f"{marker}{concept} — {elements_str}")
        return "\n".join(parts)

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

    @staticmethod
    def _format_beat_progress(progress: "BeatProgress") -> str:
        """Format a BeatProgress snapshot for the director's context prompt."""
        lines = [
            "## Current beat progress (engine truth)",
            f"- Beat: {progress.beat.title}",
            f"- Progress score: {progress.progress_score}/100",
            f"- Last action advanced: {progress.last_action_advanced}",
            "- Objective states:",
        ]
        for obj_id, state in progress.objective_states.items():
            lines.append(f"  * {obj_id}: {state.status}")
        return "\n".join(lines)
