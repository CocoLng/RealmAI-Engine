"""Quest domain model.

Represents quests and their objectives.
"""

from enum import StrEnum

from pydantic import BaseModel, Field


class QuestStatus(StrEnum):
    """Lifecycle state of a quest."""

    AVAILABLE = "available"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"


class QuestObjective(BaseModel):
    """A single objective within a quest."""

    description: str
    is_complete: bool = False


class Quest(BaseModel):
    """A quest in the game world."""

    title: str
    description: str = ""
    status: QuestStatus = QuestStatus.AVAILABLE
    objectives: list[QuestObjective] = []
    reward_xp: int = Field(default=0, ge=0)
    reward_gold: int = Field(default=0, ge=0)
    giver_npc: str | None = None
