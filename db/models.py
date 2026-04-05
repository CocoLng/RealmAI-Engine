"""SQLAlchemy table models — mirrors world/ domain models."""

from datetime import datetime

from sqlalchemy import ForeignKey, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from db.database import Base


class CampaignRow(Base):
    """Campaigns table."""

    __tablename__ = "campaigns"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    player_names: Mapped[list] = mapped_column(JSON, default=list)  # type: ignore[type-arg]
    current_location: Mapped[str | None] = mapped_column(String, nullable=True)
    interaction_count: Mapped[int] = mapped_column(default=0)


class NPCRow(Base):
    """NPCs table."""

    __tablename__ = "npcs"
    __table_args__ = (UniqueConstraint("campaign_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    race: Mapped[str] = mapped_column(String, nullable=False)
    char_class: Mapped[str | None] = mapped_column(String, nullable=True)
    level: Mapped[int] = mapped_column(nullable=False)
    ability_scores: Mapped[dict] = mapped_column(JSON, nullable=False)  # type: ignore[type-arg]
    hp: Mapped[int] = mapped_column(nullable=False)
    max_hp: Mapped[int] = mapped_column(nullable=False)
    ac: Mapped[int] = mapped_column(nullable=False)
    disposition: Mapped[str] = mapped_column(String, nullable=False)
    is_alive: Mapped[bool] = mapped_column(nullable=False, default=True)
    description: Mapped[str] = mapped_column(String, default="")
    personality: Mapped[str] = mapped_column(String, default="")
    location_name: Mapped[str | None] = mapped_column(String, nullable=True)


class LocationRow(Base):
    """Locations table."""

    __tablename__ = "locations"
    __table_args__ = (UniqueConstraint("campaign_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(String, default="")
    connections: Mapped[list] = mapped_column(JSON, default=list)  # type: ignore[type-arg]
    npcs_present: Mapped[list] = mapped_column(JSON, default=list)  # type: ignore[type-arg]
    items_available: Mapped[list] = mapped_column(JSON, default=list)  # type: ignore[type-arg]


class QuestRow(Base):
    """Quests table."""

    __tablename__ = "quests"
    __table_args__ = (UniqueConstraint("campaign_id", "title"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String, nullable=False)
    objectives: Mapped[list] = mapped_column(JSON, default=list)  # type: ignore[type-arg]
    reward_xp: Mapped[int] = mapped_column(default=0)
    reward_gold: Mapped[int] = mapped_column(default=0)
    giver_npc: Mapped[str | None] = mapped_column(String, nullable=True)


class ExchangeRow(Base):
    """Narrative exchanges table (Layer 2 memory)."""

    __tablename__ = "exchanges"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(String, nullable=False)
    interaction_number: Mapped[int] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False)


class SummaryRow(Base):
    """Compressed summaries table (Layer 3 memory)."""

    __tablename__ = "summaries"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False
    )
    summary_text: Mapped[str] = mapped_column(String, nullable=False)
    start_interaction: Mapped[int] = mapped_column(nullable=False)
    end_interaction: Mapped[int] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
