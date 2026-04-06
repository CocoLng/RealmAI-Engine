"""Domain ↔ DB conversion functions.

Each entity has a to_db() and from_db() mapper. JSON fields use
Pydantic's model_dump/model_validate for serialization.
"""

from datetime import datetime

from engine.character import AbilityScores, Character, CharacterClass, Race
from engine.inventory import Inventory
from engine.spells import SpellcasterState
from world.campaign import Campaign
from world.location import Location
from world.npc import NPC, NPCDisposition
from world.quest import Quest, QuestObjective, QuestStatus

from memory.models import CompressedSummary, ExchangeRole, NarrativeExchange

from bot.config import GuildConfig
from db.models import (
    CampaignChannelRow,
    CampaignRow,
    ExchangeRow,
    GuildConfigRow,
    LocationRow,
    NPCRow,
    PlayerCharacterRow,
    QuestRow,
    SummaryRow,
)


# ---------------------------------------------------------------------------
# Campaign
# ---------------------------------------------------------------------------


def campaign_to_db(campaign: Campaign) -> CampaignRow:
    """Convert a Campaign domain model to a DB row."""
    return CampaignRow(
        id=campaign.id,
        name=campaign.name,
        created_at=campaign.created_at,
        player_names=campaign.player_names,
        current_location=campaign.current_location,
        interaction_count=campaign.interaction_count,
        combat_state_json=campaign.combat_state_json,
    )


def campaign_from_db(row: CampaignRow) -> Campaign:
    """Convert a CampaignRow to a Campaign domain model."""
    return Campaign(
        id=row.id,
        name=row.name,
        created_at=row.created_at if isinstance(row.created_at, datetime) else datetime.fromisoformat(row.created_at),  # type: ignore[arg-type]
        player_names=list(row.player_names) if row.player_names else [],
        current_location=row.current_location,
        interaction_count=row.interaction_count,
        combat_state_json=row.combat_state_json,
    )


# ---------------------------------------------------------------------------
# NPC
# ---------------------------------------------------------------------------


def npc_to_db(npc: NPC, campaign_id: str) -> NPCRow:
    """Convert an NPC domain model to a DB row."""
    return NPCRow(
        campaign_id=campaign_id,
        name=npc.name,
        race=npc.race.value,
        char_class=npc.char_class.value if npc.char_class else None,
        level=npc.level,
        ability_scores=npc.ability_scores.model_dump(),
        hp=npc.hp,
        max_hp=npc.max_hp,
        ac=npc.ac,
        disposition=npc.disposition.value,
        is_alive=npc.is_alive,
        description=npc.description,
        personality=npc.personality,
        location_name=npc.location_name,
    )


def npc_from_db(row: NPCRow) -> NPC:
    """Convert an NPCRow to an NPC domain model."""
    return NPC(
        name=row.name,
        race=Race(row.race),
        char_class=CharacterClass(row.char_class) if row.char_class else None,
        level=row.level,
        ability_scores=AbilityScores.model_validate(row.ability_scores),
        hp=row.hp,
        max_hp=row.max_hp,
        ac=row.ac,
        disposition=NPCDisposition(row.disposition),
        is_alive=row.is_alive,
        description=row.description,
        personality=row.personality,
        location_name=row.location_name,
    )


# ---------------------------------------------------------------------------
# Location
# ---------------------------------------------------------------------------


def location_to_db(location: Location, campaign_id: str) -> LocationRow:
    """Convert a Location domain model to a DB row."""
    return LocationRow(
        campaign_id=campaign_id,
        name=location.name,
        description=location.description,
        connections=location.connections,
        npcs_present=location.npcs_present,
        items_available=location.items_available,
    )


def location_from_db(row: LocationRow) -> Location:
    """Convert a LocationRow to a Location domain model."""
    return Location(
        name=row.name,
        description=row.description,
        connections=list(row.connections) if row.connections else [],
        npcs_present=list(row.npcs_present) if row.npcs_present else [],
        items_available=list(row.items_available) if row.items_available else [],
    )


# ---------------------------------------------------------------------------
# Quest
# ---------------------------------------------------------------------------


def quest_to_db(quest: Quest, campaign_id: str) -> QuestRow:
    """Convert a Quest domain model to a DB row."""
    return QuestRow(
        campaign_id=campaign_id,
        title=quest.title,
        description=quest.description,
        status=quest.status.value,
        objectives=[obj.model_dump() for obj in quest.objectives],
        reward_xp=quest.reward_xp,
        reward_gold=quest.reward_gold,
        giver_npc=quest.giver_npc,
    )


def quest_from_db(row: QuestRow) -> Quest:
    """Convert a QuestRow to a Quest domain model."""
    return Quest(
        title=row.title,
        description=row.description,
        status=QuestStatus(row.status),
        objectives=[QuestObjective.model_validate(o) for o in row.objectives] if row.objectives else [],
        reward_xp=row.reward_xp,
        reward_gold=row.reward_gold,
        giver_npc=row.giver_npc,
    )


# ---------------------------------------------------------------------------
# NarrativeExchange
# ---------------------------------------------------------------------------


def exchange_to_db(exchange: NarrativeExchange) -> ExchangeRow:
    """Convert a NarrativeExchange domain model to a DB row."""
    return ExchangeRow(
        id=exchange.id,
        campaign_id=exchange.campaign_id,
        role=exchange.role.value,
        content=exchange.content,
        interaction_number=exchange.interaction_number,
        created_at=exchange.created_at,
    )


def exchange_from_db(row: ExchangeRow) -> NarrativeExchange:
    """Convert an ExchangeRow to a NarrativeExchange domain model."""
    return NarrativeExchange(
        id=row.id,
        campaign_id=row.campaign_id,
        role=ExchangeRole(row.role),
        content=row.content,
        interaction_number=row.interaction_number,
        created_at=(
            row.created_at
            if isinstance(row.created_at, datetime)
            else datetime.fromisoformat(row.created_at)
        ),
    )


# ---------------------------------------------------------------------------
# CompressedSummary
# ---------------------------------------------------------------------------


def summary_to_db(summary: CompressedSummary) -> SummaryRow:
    """Convert a CompressedSummary domain model to a DB row."""
    return SummaryRow(
        id=summary.id,
        campaign_id=summary.campaign_id,
        summary_text=summary.summary_text,
        start_interaction=summary.start_interaction,
        end_interaction=summary.end_interaction,
        created_at=summary.created_at,
    )


def summary_from_db(row: SummaryRow) -> CompressedSummary:
    """Convert a SummaryRow to a CompressedSummary domain model."""
    return CompressedSummary(
        id=row.id,
        campaign_id=row.campaign_id,
        summary_text=row.summary_text,
        start_interaction=row.start_interaction,
        end_interaction=row.end_interaction,
        created_at=(
            row.created_at
            if isinstance(row.created_at, datetime)
            else datetime.fromisoformat(row.created_at)
        ),
    )


# ---------------------------------------------------------------------------
# GuildConfig
# ---------------------------------------------------------------------------


def guild_config_to_db(config: GuildConfig) -> GuildConfigRow:
    """Convert a GuildConfig domain model to a DB row."""
    return GuildConfigRow(
        guild_id=config.guild_id,
        category_name=config.category_name,
    )


def guild_config_from_db(row: GuildConfigRow) -> GuildConfig:
    """Convert a GuildConfigRow to a GuildConfig domain model."""
    return GuildConfig(
        guild_id=row.guild_id,
        category_name=row.category_name,
    )


# ---------------------------------------------------------------------------
# PlayerCharacter
# ---------------------------------------------------------------------------


def player_character_to_db(
    user_id: int,
    campaign_id: str,
    character: Character,
    inventory: Inventory,
    spellcaster: SpellcasterState | None,
) -> PlayerCharacterRow:
    """Convert player character domain models to a DB row."""
    return PlayerCharacterRow(
        discord_user_id=user_id,
        campaign_id=campaign_id,
        character_json=character.model_dump_json(),
        inventory_json=inventory.model_dump_json(),
        spellcaster_json=spellcaster.model_dump_json() if spellcaster else None,
    )


def player_character_from_db(
    row: PlayerCharacterRow,
) -> tuple[int, Character, Inventory, SpellcasterState | None]:
    """Convert a PlayerCharacterRow to domain models.

    Returns:
        Tuple of (discord_user_id, Character, Inventory, SpellcasterState | None).
    """
    character = Character.model_validate_json(row.character_json)
    inventory = Inventory.model_validate_json(row.inventory_json)
    spellcaster = (
        SpellcasterState.model_validate_json(row.spellcaster_json)
        if row.spellcaster_json
        else None
    )
    return row.discord_user_id, character, inventory, spellcaster


# ---------------------------------------------------------------------------
# CampaignChannel
# ---------------------------------------------------------------------------


def campaign_channel_to_db(
    channel_id: int, campaign_id: str, guild_id: int,
) -> CampaignChannelRow:
    """Convert campaign-channel mapping to a DB row."""
    return CampaignChannelRow(
        channel_id=channel_id,
        campaign_id=campaign_id,
        guild_id=guild_id,
    )


def campaign_channel_from_db(row: CampaignChannelRow) -> tuple[int, str, int]:
    """Convert a CampaignChannelRow to a tuple.

    Returns:
        Tuple of (channel_id, campaign_id, guild_id).
    """
    return row.channel_id, row.campaign_id, row.guild_id
