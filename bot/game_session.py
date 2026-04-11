"""In-memory game session state for active campaigns."""

from __future__ import annotations

import asyncio
import difflib
import logging
import unicodedata
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from engine.character import Character
from engine.combat import CombatState
from engine.inventory import Inventory
from engine.spells import SpellcasterState
from world.campaign import Campaign
from world.location import Location
from world.npc import NPC
from world.quest import Quest
from world.story_arc import StoryArc, StoryBeat, advance_beat

from ai.client import OllamaClient, OllamaUnavailableError
from ai.interpreter import Interpreter
from ai.narrator import Narrator
from ai.npc_agent import NPCAgent
from ai.npc_generator import NPCGenerator
from ai.story_director import StoryDirector
from bot.story_bible_logger import StoryBibleLogger
from memory.semantic import SemanticMemory

if TYPE_CHECKING:
    from bot.combat_turn_manager import TurnManager

logger = logging.getLogger(__name__)

_BEAT_MATCH_THRESHOLD = 0.7
"""Minimum difflib ratio to consider a location name matching a beat hint."""


@dataclass
class GameSession:
    """Live state for one campaign channel.

    Loaded from DB on /start_campaign or /resume.
    Persisted to DB on /save or /end_campaign.
    """

    campaign: Campaign
    characters: dict[int, Character] = field(default_factory=dict)
    inventories: dict[int, Inventory] = field(default_factory=dict)
    spellcasters: dict[int, SpellcasterState | None] = field(default_factory=dict)
    combat_state: CombatState | None = None
    combat_turn_manager: "TurnManager | None" = None
    """Live Discord UI orchestrator for the active combat encounter (task 64).

    Set by :class:`bot.cogs.action_handler.ActionHandlerCog` right after
    the pipeline bootstraps a fresh ``combat_state``. Cleared by
    :meth:`bot.combat_turn_manager.TurnManager._finalize` when the
    encounter ends. ``None`` outside of combat.
    """
    current_location: Location | None = None
    npcs: dict[str, NPC] = field(default_factory=dict)
    quests: list[Quest] = field(default_factory=list)
    story_arc: StoryArc | None = None
    language: str = "fr"

    # AI services — None if Ollama is unavailable
    ollama_client: OllamaClient | None = None
    narrator: Narrator | None = None
    interpreter: Interpreter | None = None
    npc_agent: NPCAgent | None = None
    npc_generator: NPCGenerator | None = None
    story_director: StoryDirector | None = None
    semantic_memory: SemanticMemory | None = None

    # Audit log — always created, independent of Ollama availability
    story_bible: StoryBibleLogger | None = None

    # Warnings collected during AI service initialization (e.g. ChromaDB
    # unavailable). Cogs should check this list after session creation and
    # post them to the campaign channel so users know about degraded features.
    ai_warnings: list[str] = field(default_factory=list)

    # Serializes player actions per session: only one pipeline runs at a time.
    action_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    # ------------------------------------------------------------------
    # Lot D — story beat progression
    # ------------------------------------------------------------------

    def advance_beat_if_ready(self) -> StoryBeat | None:
        """Advance ``story_arc`` if the current location matches the next beat.

        Uses fuzzy matching (lowercase + accent-strip + difflib ratio) on
        ``next_beat.location_hint`` vs ``current_location.name``. Threshold
        is ``_BEAT_MATCH_THRESHOLD`` (location_hint is freeform LLM output,
        looser than the entity-resolver's 0.75).

        Returns the new :class:`StoryBeat` if advanced, ``None`` otherwise.
        """
        if self.story_arc is None or self.current_location is None:
            return None
        arc = self.story_arc
        next_idx = arc.current_beat_index + 1
        if next_idx >= len(arc.beats):
            return None
        next_beat = arc.beats[next_idx]
        ratio = difflib.SequenceMatcher(
            None,
            _normalize_location(next_beat.location_hint),
            _normalize_location(self.current_location.name),
        ).ratio()
        if ratio < _BEAT_MATCH_THRESHOLD:
            return None
        self.story_arc = advance_beat(arc)
        new_beat = self.story_arc.beats[self.story_arc.current_beat_index]
        logger.info(
            "BEAT advance ratio=%.2f hint=%r location=%r → beat %d/%d %r",
            ratio,
            next_beat.location_hint,
            self.current_location.name,
            self.story_arc.current_beat_index + 1,
            len(self.story_arc.beats),
            new_beat.title,
        )
        return new_beat


_ARTICLES = frozenset({
    "the", "a", "an",
    "le", "la", "les", "l", "un", "une", "des", "du", "de",
})


def _normalize_location(text: str) -> str:
    """Lowercase + strip accents + remove articles/punctuation for fuzzy comparison."""
    import re
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_lower = nfkd.encode("ascii", "ignore").decode("ascii").lower()
    # Remove punctuation (hyphens, apostrophes, etc.)
    cleaned = re.sub(r"[^\w\s]", " ", ascii_lower)
    # Remove common articles
    words = [w for w in cleaned.split() if w not in _ARTICLES]
    # Collapse whitespace
    return " ".join(words)


def create_ai_services(session: GameSession) -> None:
    """Attempt to initialize AI services on a session.

    Silent failure if Ollama is unreachable — cogs check for None.
    The story bible logger is always created (it does not depend on Ollama).
    """
    # Audit logger is always available, even without an LLM backend.
    session.story_bible = StoryBibleLogger(session.campaign.id)

    try:
        client = OllamaClient()
        # Quick connectivity check
        session.ollama_client = client
        session.narrator = Narrator(client)
        session.interpreter = Interpreter(client)
        session.npc_agent = NPCAgent(client)
        session.npc_generator = NPCGenerator(client)
        try:
            session.semantic_memory = SemanticMemory()
            session.story_director = StoryDirector(client, session.semantic_memory)
        except Exception:
            logger.warning(
                "Story Director init failed for campaign %s", session.campaign.id,
                exc_info=True,
            )
            session.semantic_memory = None
            session.story_director = None
            session.ai_warnings.append(
                "\u26a0\ufe0f M\u00e9moire s\u00e9mantique indisponible "
                "\u2014 la coh\u00e9rence narrative long-terme est d\u00e9sactiv\u00e9e."
            )
        logger.info("AI services initialized for campaign %s", session.campaign.id)
    except (OllamaUnavailableError, Exception):
        logger.warning(
            "Ollama unavailable — AI features disabled for campaign %s",
            session.campaign.id,
        )
