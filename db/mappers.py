"""Domain ↔ DB conversion functions.

Each entity has a to_db() and from_db() mapper. JSON fields use
Pydantic's model_dump/model_validate for serialization.
"""

import logging
from datetime import datetime
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from engine.character import AbilityScores, Character, CharacterClass, Race
from engine.inventory import Inventory
from engine.npc_stat_block import NPCStatBlock
from engine.spells import SpellcasterState
from world.campaign import Campaign
from world.combat_trigger_def import CombatTriggerDef
from world.combat_zone import Zone
from world.location import Location
from world.npc import NPC, DialogueExchange, NPCDisposition
from world.quest import Quest, QuestObjective, QuestStatus

from memory.models import CompressedSummary, ExchangeRole, NarrativeExchange

from world.story_arc import StoryArc

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
    StoryArcRow,
    SummaryRow,
)

logger = logging.getLogger(__name__)

_T = TypeVar("_T", bound=BaseModel)


class CorruptSaveError(Exception):
    """A persisted JSON blob no longer validates against its domain model.

    Raised on load paths where silently dropping the data would lose a
    player character or the campaign's story arc. Carries the entity name
    and the first faulty field so callers can tell the player exactly what
    broke instead of surfacing a raw ValidationError traceback.
    """

    def __init__(self, entity: str, context: str, exc: ValidationError) -> None:
        self.entity = entity
        self.context = context
        errors = exc.errors()
        if errors:
            first = errors[0]
            self.field = ".".join(str(part) for part in first.get("loc", ())) or "?"
            self.detail = str(first.get("msg", exc))
        else:
            self.field = "?"
            self.detail = str(exc)
        super().__init__(
            f"{entity} ({context}): champ '{self.field}' — {self.detail}",
        )


def _validate_json_or_corrupt(
    model_cls: type[_T],
    raw_json: str,
    *,
    entity: str,
    context: str,
) -> _T:
    """Validate a JSON string, raising :class:`CorruptSaveError` on failure."""
    try:
        return model_cls.model_validate_json(raw_json)
    except ValidationError as exc:
        raise CorruptSaveError(entity, context, exc) from exc


def _validate_list(
    model_cls: type[_T],
    raw_items: list | None,
    *,
    context: str,
) -> list[_T]:
    """Validate a list of dicts into Pydantic models, skipping bad entries.

    Logs each failed item with ``context`` (e.g. ``"NPC dialogue_history npc=Goblin"``)
    so a single corrupted entry never crashes campaign load. Returns an empty
    list if ``raw_items`` is falsy.
    """
    if not raw_items:
        return []
    validated: list[_T] = []
    for index, item in enumerate(raw_items):
        try:
            validated.append(model_cls.model_validate(item))
        except ValidationError as exc:
            logger.warning(
                "Skipping invalid %s[%d] in %s: %s",
                model_cls.__name__, index, context, exc,
            )
    return validated


def _validate_dict(
    model_cls: type[_T],
    raw_items: dict | None,
    *,
    context: str,
) -> dict[str, _T]:
    """Validate a dict-of-dicts into Pydantic models, skipping bad entries."""
    if not raw_items:
        return {}
    validated: dict[str, _T] = {}
    for key, value in raw_items.items():
        try:
            validated[str(key)] = model_cls.model_validate(value)
        except ValidationError as exc:
            logger.warning(
                "Skipping invalid %s[%r] in %s: %s",
                model_cls.__name__, key, context, exc,
            )
    return validated


def _safe_validate_json(
    model_cls: type[_T],
    raw_json: str | None,
    *,
    context: str,
) -> _T | None:
    """Validate a JSON string into a Pydantic model, returning None on failure."""
    if not raw_json:
        return None
    try:
        return model_cls.model_validate_json(raw_json)
    except ValidationError as exc:
        logger.warning(
            "Invalid %s JSON in %s, treating as missing: %s",
            model_cls.__name__, context, exc,
        )
        return None


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
        created_at=row.created_at if isinstance(row.created_at, datetime) else datetime.fromisoformat(row.created_at),
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
        aliases=list(npc.aliases),
        secrets=list(npc.secrets),
        knowledge=list(npc.knowledge),
        dialogue_history=[exch.model_dump() for exch in npc.dialogue_history],
        stat_block_json=npc.stat_block.model_dump_json() if npc.stat_block else None,
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
        aliases=list(row.aliases) if row.aliases else [],
        secrets=list(row.secrets) if row.secrets else [],
        knowledge=list(row.knowledge) if row.knowledge else [],
        dialogue_history=_validate_list(
            DialogueExchange,
            row.dialogue_history,
            context=f"NPC dialogue_history npc={row.name!r}",
        ),
        stat_block=_safe_validate_json(
            NPCStatBlock,
            row.stat_block_json,
            context=f"NPC stat_block npc={row.name!r}",
        ),
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
        arrival_hook=location.arrival_hook,
        connections=location.connections,
        exit_aliases=location.exit_aliases,
        npcs_present=location.npcs_present,
        items_available=location.items_available,
        item_descriptions=location.item_descriptions,
        state_flags=location.state_flags,
        unlocked_exits=location.unlocked_exits,
        generated=location.generated,
        combat_zones=[z.model_dump() for z in location.combat_zones],
        combat_triggers={
            key: trigger.model_dump()
            for key, trigger in location.combat_triggers.items()
        },
        npc_roles=dict(location.npc_roles),
    )


def location_from_db(row: LocationRow) -> Location:
    """Convert a LocationRow to a Location domain model.

    Combat zones are all-or-nothing (H4): dropping a single invalid zone
    would leave orphan adjacency references behind, and the Location graph
    validator would then make the whole row unloadable — crashing /resume
    and MOVE. If any zone entry is invalid, or the zone graph itself no
    longer validates, ALL zones are disabled and the location loads
    zone-less (combat falls back to the legacy spatial-free flow).
    """
    zones: list[Zone] = []
    if row.combat_zones:
        try:
            zones = [Zone.model_validate(z) for z in row.combat_zones]
        except ValidationError as exc:
            logger.warning(
                "Combat zones disabled for location %r: invalid zone entry: %s",
                row.name, exc,
            )
            zones = []

    fields: dict = dict(
        name=row.name,
        description=row.description,
        arrival_hook=row.arrival_hook or "",
        connections=list(row.connections) if row.connections else [],
        exit_aliases={
            str(k): [str(a) for a in v]
            for k, v in (row.exit_aliases or {}).items()
        },
        npcs_present=list(row.npcs_present) if row.npcs_present else [],
        items_available=list(row.items_available) if row.items_available else [],
        item_descriptions=dict(row.item_descriptions) if row.item_descriptions else {},
        state_flags=dict(row.state_flags) if row.state_flags else {},
        unlocked_exits=list(row.unlocked_exits) if row.unlocked_exits else [],
        generated=bool(row.generated),
        combat_triggers=_validate_dict(
            CombatTriggerDef,
            row.combat_triggers,
            context=f"Location combat_triggers name={row.name!r}",
        ),
        npc_roles={
            str(key): str(value)
            for key, value in (row.npc_roles or {}).items()
        },
    )
    try:
        return Location(**fields, combat_zones=zones)
    except ValidationError as exc:
        if not zones:
            raise  # not a zone problem — genuine corruption, surface it
        logger.warning(
            "Combat zones disabled for location %r: inconsistent zone graph: %s",
            row.name, exc,
        )
        return Location(**fields, combat_zones=[])


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
        objectives=_validate_list(
            QuestObjective,
            row.objectives,
            context=f"Quest objectives title={row.title!r}",
        ),
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
        language=config.language,
    )


def guild_config_from_db(row: GuildConfigRow) -> GuildConfig:
    """Convert a GuildConfigRow to a GuildConfig domain model.

    Rows written before /settings validated its input can hold poisoned
    values (e.g. ``language="French"``). Each field falls back to its
    default individually — a bad language must not kill /start_campaign,
    /resume and /settings for the whole guild (H7).
    """
    try:
        return GuildConfig(
            guild_id=row.guild_id,
            category_name=row.category_name,
            language=row.language,
        )
    except ValidationError as exc:
        logger.warning(
            "Poisoned guild_config row for guild %s — resetting invalid "
            "fields to defaults: %s",
            row.guild_id, exc,
        )
        valid: dict = {"guild_id": row.guild_id}
        for field_name, value in (
            ("category_name", row.category_name),
            ("language", row.language),
        ):
            try:
                GuildConfig.model_validate({"guild_id": row.guild_id, field_name: value})
            except ValidationError:
                continue
            valid[field_name] = value
        return GuildConfig.model_validate(valid)


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


def backfill_character_features(character: Character) -> Character:
    """Add racial and class features if absent (for pre-refactor characters).

    Characters saved before the feature system was introduced have an empty
    ``features`` list.  This function fills it in from the canonical race and
    class tables so the rest of the engine can rely on features being present.
    """
    if not character.features:
        from engine.character.races import RACIAL_FEATURES
        from engine.character.classes import CLASS_FEATURES

        racial = RACIAL_FEATURES.get(character.race, [])
        class_feats = [
            f
            for f in CLASS_FEATURES.get(character.char_class, [])
            if f.level_requirement <= character.level
        ]
        character.features = list(racial) + list(class_feats)
    return character


def player_character_from_db(
    row: PlayerCharacterRow,
) -> tuple[int, Character, Inventory, SpellcasterState | None]:
    """Convert a PlayerCharacterRow to domain models.

    Returns:
        Tuple of (discord_user_id, Character, Inventory, SpellcasterState | None).
    """
    context = f"user_id={row.discord_user_id} campaign={row.campaign_id!r}"
    character = _validate_json_or_corrupt(
        Character, row.character_json, entity="Character", context=context,
    )
    character = backfill_character_features(character)
    inventory = _validate_json_or_corrupt(
        Inventory, row.inventory_json, entity="Inventory", context=context,
    )
    # Spellcaster state is optional — a drifted blob degrades to None
    # (spell slots reset) instead of blocking the whole character load.
    spellcaster = _safe_validate_json(
        SpellcasterState,
        row.spellcaster_json,
        context=f"PlayerCharacter spellcaster {context}",
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


# ---------------------------------------------------------------------------
# StoryArc
# ---------------------------------------------------------------------------


def story_arc_to_db(arc: StoryArc) -> StoryArcRow:
    """Convert a StoryArc domain model to a DB row."""
    return StoryArcRow(
        campaign_id=arc.campaign_id,
        arc_json=arc.model_dump_json(),
        current_beat_index=arc.current_beat_index,
        archetype=arc.archetype,
    )


def story_arc_from_db(row: StoryArcRow) -> StoryArc:
    """Convert a StoryArcRow to a StoryArc domain model.

    The dedicated ``current_beat_index`` column is authoritative;
    the value inside ``arc_json`` is ignored for this field.
    """
    arc = _validate_json_or_corrupt(
        StoryArc,
        row.arc_json,
        entity="StoryArc",
        context=f"campaign={row.campaign_id!r}",
    )
    if arc.current_beat_index != row.current_beat_index:
        arc = arc.model_copy(update={"current_beat_index": row.current_beat_index})
    return arc
