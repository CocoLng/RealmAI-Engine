"""In-memory game session state for active campaigns."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from engine.character import Character
from engine.combat import CombatState
from engine.inventory import Inventory
from engine.spells import SpellcasterState
from world.campaign import Campaign
from world.location import Location
from world.npc import NPC
from world.quest import Quest
from world.story_arc import StoryArc

from ai.client import OllamaClient, OllamaUnavailableError
from ai.interpreter import Interpreter
from ai.narrator import Narrator
from ai.npc_agent import NPCAgent

logger = logging.getLogger(__name__)


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


def create_ai_services(session: GameSession) -> None:
    """Attempt to initialize AI services on a session.

    Silent failure if Ollama is unreachable — cogs check for None.
    """
    try:
        client = OllamaClient()
        # Quick connectivity check
        session.ollama_client = client
        session.narrator = Narrator(client)
        session.interpreter = Interpreter(client)
        session.npc_agent = NPCAgent(client)
        logger.info("AI services initialized for campaign %s", session.campaign.id)
    except (OllamaUnavailableError, Exception):
        logger.warning(
            "Ollama unavailable — AI features disabled for campaign %s",
            session.campaign.id,
        )
