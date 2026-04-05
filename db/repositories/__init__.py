"""Repository classes for CRUD operations."""

from db.repositories.campaign_repo import CampaignRepository
from db.repositories.exchange_repo import ExchangeRepository
from db.repositories.location_repo import LocationRepository
from db.repositories.npc_repo import NPCRepository
from db.repositories.quest_repo import QuestRepository
from db.repositories.summary_repo import SummaryRepository

__all__ = [
    "CampaignRepository",
    "ExchangeRepository",
    "LocationRepository",
    "NPCRepository",
    "QuestRepository",
    "SummaryRepository",
]
