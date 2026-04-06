"""Repository classes for CRUD operations."""

from db.repositories.campaign_channel_repo import CampaignChannelRepository
from db.repositories.campaign_repo import CampaignRepository
from db.repositories.exchange_repo import ExchangeRepository
from db.repositories.guild_config_repo import GuildConfigRepository
from db.repositories.location_repo import LocationRepository
from db.repositories.npc_repo import NPCRepository
from db.repositories.player_character_repo import PlayerCharacterRepository
from db.repositories.quest_repo import QuestRepository
from db.repositories.summary_repo import SummaryRepository

__all__ = [
    "CampaignChannelRepository",
    "CampaignRepository",
    "ExchangeRepository",
    "GuildConfigRepository",
    "LocationRepository",
    "NPCRepository",
    "PlayerCharacterRepository",
    "QuestRepository",
    "SummaryRepository",
]
