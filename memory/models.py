"""Pydantic v2 domain models for the 4-layer memory system."""

from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Layer 1 — Structured state snapshot
# ---------------------------------------------------------------------------


class CharacterSummary(BaseModel):
    """Compact character representation for context injection."""

    name: str
    race: str
    char_class: str
    level: int
    hp: int
    max_hp: int
    ac: int
    conditions: list[str] = Field(default_factory=list)


class CombatSummary(BaseModel):
    """Compact combat state for context injection."""

    is_active: bool = False
    round_number: int = 0
    current_turn: str | None = None
    combatants: list[CharacterSummary] = Field(default_factory=list)


class GameStateSummary(BaseModel):
    """Layer 1 output: structured snapshot of current game state."""

    campaign_name: str
    current_location: str | None = None
    location_description: str = ""
    player_characters: list[CharacterSummary] = Field(default_factory=list)
    nearby_npcs: list[str] = Field(default_factory=list)
    active_quests: list[str] = Field(default_factory=list)
    combat: CombatSummary | None = None
    inventory_highlights: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Layer 2 — Narrative exchanges
# ---------------------------------------------------------------------------


class ExchangeRole(StrEnum):
    """Who produced this exchange."""

    PLAYER = "player"
    NARRATOR = "narrator"
    SYSTEM = "system"


class NarrativeExchange(BaseModel):
    """A single narrative exchange (player input or narrator output)."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    campaign_id: str
    role: ExchangeRole
    content: str
    interaction_number: int
    created_at: datetime = Field(default_factory=datetime.now)


# ---------------------------------------------------------------------------
# Layer 3 — Compressed summaries
# ---------------------------------------------------------------------------


class CompressedSummary(BaseModel):
    """An auto-generated summary covering a range of interactions."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    campaign_id: str
    summary_text: str
    start_interaction: int
    end_interaction: int
    created_at: datetime = Field(default_factory=datetime.now)


# ---------------------------------------------------------------------------
# Layer 4 — Semantic memory documents
# ---------------------------------------------------------------------------


class SemanticDocumentType(StrEnum):
    """Categories of documents stored in semantic memory."""

    WORLD_LORE = "world_lore"
    NPC_SHEET = "npc_sheet"
    PAST_EVENT = "past_event"
    LOCATION_DETAIL = "location_detail"
    QUEST_DETAIL = "quest_detail"


class SemanticDocument(BaseModel):
    """A document to store in ChromaDB for semantic retrieval."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    campaign_id: str
    doc_type: SemanticDocumentType
    content: str
    metadata: dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Context assembler config
# ---------------------------------------------------------------------------


class ContextBudget(BaseModel):
    """Token budget per layer. Total should be ~1500-2500."""

    layer1_max: int = 450
    layer2_max: int = 700
    layer3_max: int = 400
    layer4_max: int = 350
    total_max: int = 2500
