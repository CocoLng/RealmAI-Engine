"""Scene hydration — turn `Location.npcs_present` strings into real NPC rows.

The world generator emits `Location.npcs_present` as a list of plain strings
(``["La Marchande de Brumes", "Le Gardien du Moulin"]``). Without this helper
those names never become persisted ``NPC`` entities, so the entity resolver
sees an empty ``session.npcs`` and refuses every TALK action.

This module is the bridge: at campaign launch and after every MOVE, we walk
``location.npcs_present`` and ensure each name corresponds to a row in the
``npcs`` table for the campaign. Already-persisted NPCs are left untouched
(idempotent).

**Tier dispatch** — Hydrated NPCs are no longer always commoners:
  1. Villain (``arc.villain_name == name``) → custom ``arc.villain_stat_block``
     (or ``generic_boss`` fallback if the arc did not produce one).
  2. Explicit ``role`` hint on ``Location.npc_roles`` matching an archetype
     in :mod:`engine.npc_library` → ``get_archetype(role)``.
  3. NPC appears in a combat/boss beat → ``get_archetype("guard")``.
  4. Everyone else → ``get_archetype("commoner")``.
HP/AC/ability scores are derived from the stat block's tier; narrative fields
(``description``, ``personality``, ``secrets``, ``dialogue_history``) are
preserved across idempotent upgrades.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from bot.npc_prefetch import schedule_npc_prefetch
from db.repositories.location_repo import LocationRepository
from db.repositories.npc_repo import NPCRepository
from engine.character import AbilityScores, Character, Race
from engine.combat import CombatSide, Combatant, CombatState
from engine.inventory import EquipmentSlot, Inventory, Item, ItemType
from engine.npc_library import ARCHETYPE_BUILDERS, get_archetype
from engine.npc_stat_block import NPCStatBlock, NPCTier
from world.location import Location
from world.npc import NPC, NPCDisposition

if TYPE_CHECKING:
    from bot.game_session import GameSession
    from world.story_arc import StoryArc

logger = logging.getLogger(__name__)


def _stats_from_stat_block(
    sb: NPCStatBlock,
) -> tuple[int, int, int, AbilityScores]:
    """Derive ``(hp, max_hp, ac, ability_scores)`` from a tier.

    The legacy ``NPC`` model carries per-tier fields the engine uses for
    trivial resolve, health bars, and damage calc. We snapshot them from a
    fixed per-tier table so combat-capable NPCs are no longer one-shot.
    """
    if sb.tier == NPCTier.MINION:
        return (
            8,
            8,
            12,
            AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10),
        )
    if sb.tier == NPCTier.ELITE:
        return (
            25,
            25,
            14,
            AbilityScores(STR=14, DEX=12, CON=13, INT=10, WIS=12, CHA=12),
        )
    # BOSS
    return (
        55,
        55,
        16,
        AbilityScores(STR=16, DEX=14, CON=14, INT=12, WIS=14, CHA=14),
    )


def _level_from_tier(tier: NPCTier) -> int:
    return {NPCTier.MINION: 1, NPCTier.ELITE: 3, NPCTier.BOSS: 6}[tier]


def _get_world_role_hint(
    location: Location, npc_name: str,
) -> str | None:
    """Return the world generator's ``role`` hint for ``npc_name``, if any.

    World generator emits a ``role`` key inside each ``npc_details`` entry.
    The parser persists those roles on :attr:`Location.npc_roles`. This
    helper looks them up without crashing when the field is absent or
    empty (back-compat with legacy locations).
    """
    roles = getattr(location, "npc_roles", None) or {}
    if not isinstance(roles, dict):
        return None
    value = roles.get(npc_name)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _resolve_archetype(
    name: str,
    arc: "StoryArc | None",
    world_role_hint: str | None,
) -> tuple[str, NPCStatBlock | None]:
    """Pick the right archetype for an NPC and optionally a custom stat block.

    Priority:
      1. Villain by name → the custom ``arc.villain_stat_block``. Falls
         back to ``generic_boss`` (without a custom stat block) when the
         arc has no villain stat block attached.
      2. ``world_role_hint`` matches a library archetype → use it.
      3. Name appears in a beat with ``encounter_type in {'combat','boss'}``
         → fall back to ``"guard"``.
      4. Default → ``"commoner"``.

    Returns ``(archetype_name, custom_stat_block_or_None)``.
    """
    if arc is not None and name == arc.villain_name:
        if arc.villain_stat_block is not None:
            return (arc.villain_stat_block.archetype, arc.villain_stat_block)
        return ("generic_boss", None)

    if world_role_hint is not None and world_role_hint in ARCHETYPE_BUILDERS:
        return (world_role_hint, None)

    if arc is not None:
        for beat in arc.beats:
            if beat.encounter_type in ("combat", "boss") and name in beat.npc_names:
                return ("guard", None)

    return ("commoner", None)


def _build_npc_by_context(
    name: str,
    location_name: str,
    arc: "StoryArc | None",
    world_role_hint: str | None = None,
) -> NPC:
    """Create an NPC with the right stat block for its narrative role."""
    archetype_name, custom_stat_block = _resolve_archetype(
        name, arc, world_role_hint,
    )
    stat_block = (
        custom_stat_block
        if custom_stat_block is not None
        else get_archetype(archetype_name)
    )
    hp, max_hp, ac, ability_scores = _stats_from_stat_block(stat_block)
    disposition = (
        NPCDisposition.HOSTILE
        if stat_block.tier != NPCTier.MINION
        else NPCDisposition.NEUTRAL
    )
    return NPC(
        name=name,
        race=Race.HUMAN,
        char_class=None,
        level=_level_from_tier(stat_block.tier),
        ability_scores=ability_scores,
        hp=hp,
        max_hp=max_hp,
        ac=ac,
        disposition=disposition,
        is_alive=True,
        description="",
        personality="",
        location_name=location_name,
        aliases=[],
        stat_block=stat_block,
    )


# Kept under the old name for backward compatibility with tests that only
# need a minimal commoner NPC without arc/role dispatch. New callers must
# prefer :func:`_build_npc_by_context`.
def _build_default_npc(name: str, location_name: str) -> NPC:
    """Create a minimal commoner NPC for a name with no prior backstory."""
    return _build_npc_by_context(
        name=name,
        location_name=location_name,
        arc=None,
        world_role_hint=None,
    )


def _should_upgrade_npc(
    existing: NPC,
    *,
    arc: "StoryArc | None",
) -> bool:
    """Return ``True`` when an existing NPC row is under-statted and needs
    to be upgraded to a real stat block.

    Upgrade triggers when the existing NPC has no stat block AND either
    matches the villain name or appears in a combat/boss beat. This keeps
    hydration idempotent: re-running it on an NPC that was created by the
    legacy code path (pre-task 43) will correct the stats without
    destroying the conversation history.
    """
    if existing.stat_block is not None:
        return False
    if arc is None:
        return False
    if existing.name == arc.villain_name:
        return True
    return any(
        beat.encounter_type in ("combat", "boss")
        and existing.name in beat.npc_names
        for beat in arc.beats
    )


def hydrate_scene(
    session: "GameSession",
    *,
    db_factory: Callable[[], Any],
) -> None:
    """Ensure every name in ``location.npcs_present`` and ``items_available``
    is reachable by the entity resolver.

    - NPCs: missing names are inserted as commoner ``NPC`` rows tied to the
      current location, then ``session.npcs`` is reloaded.
    - Items: ``Location.items_available`` is the source of truth — no DB
      mutation is required, but we make sure the location row reflects the
      in-memory list (in case the launcher mutated it post-save).

    Idempotent and best-effort: a single failure logs a warning and returns
    without aborting the launch.
    """
    location = session.current_location
    if location is None:
        return
    campaign_id = session.campaign.id
    arc = getattr(session, "story_arc", None)

    db_session = db_factory()
    try:
        npc_repo = NPCRepository(db_session)
        loc_repo = LocationRepository(db_session)

        # 1. Hydrate any missing NPC.
        created = 0
        upgraded = 0
        for name in location.npcs_present:
            if not name or not name.strip():
                continue
            existing = npc_repo.get_by_name(name, campaign_id)
            world_role = _get_world_role_hint(location, name)
            if existing is None:
                npc_repo.save(
                    _build_npc_by_context(name, location.name, arc, world_role),
                    campaign_id,
                )
                created += 1
            elif not existing.is_alive:
                # Dead NPC lingering in a stale npcs_present list — never
                # rebind, upgrade, or recreate it (audit H15).
                continue
            elif _should_upgrade_npc(existing, arc=arc):
                # Idempotent upgrade: an NPC created by the legacy code path
                # matches the villain name or a combat beat but has no stat
                # block. Rebuild with the right archetype/stat_block and
                # preserve narrative state.
                replacement = _build_npc_by_context(
                    name, location.name, arc, world_role,
                )
                replacement.description = existing.description
                replacement.personality = existing.personality
                replacement.secrets = list(existing.secrets)
                replacement.knowledge = list(existing.knowledge)
                replacement.dialogue_history = list(existing.dialogue_history)
                replacement.aliases = list(existing.aliases)
                npc_repo.update(replacement, campaign_id)
                upgraded += 1
            elif existing.location_name != location.name:
                # NPC exists in DB but isn't tagged to this location yet —
                # rebind so the resolver picks them up.
                existing.location_name = location.name
                npc_repo.update(existing, campaign_id)

        # 2. Persist the items_available list (in case it was mutated in
        #    memory by a prior PICKUP and not yet flushed). The location row
        #    must exist; if it doesn't, swallow gracefully.
        try:
            loc_repo.update(location, campaign_id)
        except ValueError:
            pass  # Location not yet persisted — caller will save it.

        db_session.commit()

        # 3. Reload session.npcs from the canonical DB list.
        npcs = npc_repo.list_by_location(location.name, campaign_id)
        session.npcs = {n.name: n for n in npcs}

        if created or upgraded:
            logger.info(
                "SCENE hydrated campaign=%s location=%s created=%d upgraded=%d total=%d",
                campaign_id, location.name, created, upgraded, len(session.npcs),
            )
    except Exception:
        logger.error(
            "SCENE hydration failed campaign=%s location=%s",
            campaign_id, location.name, exc_info=True,
        )
        # Fallback: populate session.npcs from location names so the
        # entity resolver can still match targets even without DB rows.
        if not session.npcs:
            for name in location.npcs_present:
                if name and name.strip() and name not in session.npcs:
                    session.npcs[name] = _build_default_npc(
                        name, location.name,
                    )
            logger.info(
                "SCENE fallback npcs campaign=%s count=%d",
                campaign_id, len(session.npcs),
            )
    finally:
        db_session.close()

    # Chantier I (H8): pre-generate missing NPC sheets in the background so
    # the first TALK doesn't pay the 18-27 s lazy generation mid-action.
    # No-op without a running loop, an npc_generator, or empty-sheet NPCs.
    schedule_npc_prefetch(session, db_factory=db_factory)


def take_scene_item(
    session: "GameSession",
    *,
    item_name: str,
    user_id: int,
    db_factory: Callable[[], Any],
) -> Item | None:
    """Move an item from the current location into the player's inventory.

    Returns the created :class:`engine.inventory.Item` on success, ``None``
    if no matching scene item exists. The location row and the player
    inventory row are persisted in a single transaction. If that persist
    fails, the in-memory mutation is reverted and ``None`` is returned —
    reporting success on a failed write would leave a phantom item that
    duplicates at the next reload (audit low).
    """
    location = session.current_location
    if location is None:
        return None
    if item_name not in location.items_available:
        return None

    item = Item(
        name=item_name,
        item_type=ItemType.ADVENTURING_GEAR,
        weight=0.5,
        value_gp=0,
        description=f"Ramassé dans {location.name}.",
    )

    # Mutate in-memory state first.
    location.items_available = [
        n for n in location.items_available if n != item_name
    ]
    inventory = session.inventories.get(user_id)
    if inventory is not None:
        inventory.items.append(item)

    # Persist.
    db_session = db_factory()
    try:
        from db.mappers import player_character_to_db
        from db.models import PlayerCharacterRow
        from sqlalchemy import select

        loc_repo = LocationRepository(db_session)
        try:
            loc_repo.update(location, session.campaign.id)
        except ValueError:
            loc_repo.save(location, session.campaign.id)

        if inventory is not None and user_id in session.characters:
            stmt = select(PlayerCharacterRow).where(
                PlayerCharacterRow.discord_user_id == user_id,
                PlayerCharacterRow.campaign_id == session.campaign.id,
            )
            row = db_session.execute(stmt).scalar_one_or_none()
            if row is not None:
                row.inventory_json = inventory.model_dump_json()
            else:
                spell = session.spellcasters.get(user_id)
                db_session.add(
                    player_character_to_db(
                        user_id,
                        session.campaign.id,
                        session.characters[user_id],
                        inventory,
                        spell,
                    ),
                )

        db_session.commit()
    except Exception:
        logger.warning(
            "PICKUP persist failed campaign=%s item=%r user=%s",
            session.campaign.id, item_name, user_id, exc_info=True,
        )
        db_session.rollback()
        # Revert the in-memory mutation — memory and DB must agree.
        location.items_available.append(item_name)
        if inventory is not None and item in inventory.items:
            inventory.items.remove(item)
        return None
    finally:
        db_session.close()

    logger.info(
        "PICKUP campaign=%s user=%s item=%r location=%s",
        session.campaign.id, user_id, item_name, location.name,
    )
    return item


def describe_scene_for_narrator(
    session: "GameSession",
    *,
    actor_name: str,
    current_outcome_summary: str | None = None,
    ongoing_dialogue_with: str | None = None,
) -> str:
    """Build a rich, narrator-facing description of the current scene.

    Includes location name + description, exits, items (with canon
    descriptions when available), and present NPCs (with disposition,
    description, and personality). Falls back gracefully when fields are
    empty so it works on freshly hydrated commoner NPCs.

    Args:
        session: The live game session.
        actor_name: Name of the combatant acting this turn (for the Acting
            character section).
        current_outcome_summary: If set, an event whose text matches this
            summary is excluded from the "Derniers événements mécaniques"
            block. Prevents the narrator from seeing the current turn's
            outcome twice (once as the action to narrate, once as past
            history) and hallucinating a doubled narration.
        ongoing_dialogue_with: NPC name when narrating a Talk action that
            is a continuation of an existing dialogue. When the NPC has
            at least one exchange prior to the current turn, this triggers
            two behaviors: (1) suppress the verbose ``## NPCs present``
            block so the narrator stops re-describing the NPC's appearance
            every turn; (2) emit a compact ``## Dialogue in progress`` block
            with up to 2 prior exchanges so the narrator can anchor on
            continuity rather than scenery.
    """
    lines: list[str] = []
    location = session.current_location

    # Determine if this call is a continuation of a multi-turn dialogue.
    # A talk action is "ongoing" once there are at least 2 entries in the
    # NPC's ``dialogue_history`` (one already past + the one just appended
    # for the current turn).
    ongoing_npc = None
    if ongoing_dialogue_with and session.npcs:
        candidate = session.npcs.get(ongoing_dialogue_with)
        if candidate is not None and len(candidate.dialogue_history) >= 2:
            ongoing_npc = candidate

    if location is not None:
        lines.append(f"## Location\n{location.name}\n{location.description}")

        all_exits = location.connections + location.unlocked_exits
        if all_exits:
            lines.append("## Exits\n" + ", ".join(all_exits))

        if location.items_available:
            item_lines = []
            descriptions = getattr(location, "item_descriptions", {}) or {}
            for name in location.items_available:
                desc = descriptions.get(name, "").strip()
                if desc:
                    item_lines.append(f"- {name} — {desc}")
                else:
                    item_lines.append(f"- {name}")
            lines.append("## Visible items\n" + "\n".join(item_lines))

        # Skip the ## NPCs present block when narrating an ongoing dialogue:
        # keeping it causes the narrator to re-describe the NPC's appearance
        # at every exchange. The dialogue-in-progress block (below) provides
        # sufficient continuity context.
        if ongoing_npc is None:
            present = [
                npc for npc in (session.npcs or {}).values()
                if npc.is_alive and npc.location_name == location.name
            ]
            if present:
                npc_lines = []
                for npc in present:
                    bits = [npc.name]
                    if npc.race is not None:
                        bits.append(f"({npc.race.value})")
                    bits.append(f"— disposition: {npc.disposition.value}")
                    if npc.description:
                        bits.append(f"— {npc.description}")
                    if npc.personality:
                        bits.append(f"— personality: {npc.personality}")
                    npc_lines.append(" ".join(bits))
                lines.append("## NPCs present\n" + "\n".join(npc_lines))

        if location.state_flags:
            active = [k.replace("_", " ") for k, v in location.state_flags.items() if v]
            if active:
                lines.append("## Environment state\n" + ", ".join(active))

    arc = getattr(session, "story_arc", None)
    if arc is not None:
        beat = arc.beats[arc.current_beat_index]
        beat_lines = [
            "## Current story beat",
            f"{beat.title} — {beat.description}",
            f"Type: {beat.encounter_type}",
        ]
        if beat.is_twist:
            beat_lines.append("(Ce beat est un TWIST — reveal narratif attendu.)")
        lines.append("\n".join(beat_lines))

    combat_state = getattr(session, "combat_state", None)
    if isinstance(combat_state, CombatState) and combat_state.is_active:
        lines.append(
            _describe_combat_for_narrator(
                combat_state,
                current_outcome_summary=current_outcome_summary,
            )
        )

    if ongoing_npc is not None:
        # Skip the current turn's just-appended exchange; show the last 2
        # prior exchanges so the narrator anchors on continuity.
        prior = ongoing_npc.dialogue_history[:-1][-2:]
        exchange_lines = [f"## Dialogue in progress with {ongoing_npc.name}"]
        for ex in prior:
            exchange_lines.append(f"Player: {ex.player_said}")
            exchange_lines.append(f"{ongoing_npc.name}: {ex.npc_said}")
        lines.append("\n".join(exchange_lines))

    lines.append(_describe_actor(session, actor_name))
    return "\n\n".join(lines)


def _describe_actor(session: "GameSession", actor_name: str) -> str:
    """Build the ``## Acting character`` section with race/class/level/weapon.

    Resolves the acting entity in two passes: first against the combat
    roster (so NPC monsters on their own turn are enriched too), then
    against ``session.characters`` as a fallback for out-of-combat PC
    actions. If nothing matches, falls back to just the name — the
    narrator still works, it just gets less texture.

    Also surfaces the player's ``kit`` and ``motivation`` (captured at
    onboarding) when the actor maps to a PC — these anchor the narrator's
    framing turn after turn and keep a Shadow Blade mercenary reading as
    a sellsword, not a destined hero.
    """
    character: Character | None = None
    inventory: Inventory | None = None
    pc_user_id: int | None = None

    combat_state = getattr(session, "combat_state", None)
    if isinstance(combat_state, CombatState):
        for combatant in combat_state.combatants:
            if combatant.name == actor_name:
                character = combatant.character
                inventory = combatant.inventory
                break

    characters = getattr(session, "characters", None)
    if isinstance(characters, dict):
        inventories = getattr(session, "inventories", None)
        for uid, pc in characters.items():
            if isinstance(pc, Character) and pc.name == actor_name:
                pc_user_id = uid
                if character is None:
                    character = pc
                    if isinstance(inventories, dict):
                        inventory = inventories.get(uid)
                break

    if character is None:
        return f"## Acting character\n{actor_name}"

    lines = [
        "## Acting character",
        character.name,
        (
            f"Race {character.race.value}, classe {character.char_class.value}, "
            f"niveau {character.level}."
        ),
    ]

    if pc_user_id is not None:
        kits = getattr(session, "character_kits", None)
        if isinstance(kits, dict):
            kit = kits.get(pc_user_id)
            if kit:
                lines.append(f"Kit : {kit}.")
        motivations = getattr(session, "character_motivations", None)
        if isinstance(motivations, dict):
            motivation = motivations.get(pc_user_id)
            if motivation:
                lines.append(f"Motivation : {motivation}.")

    weapon_name = _main_weapon_name(inventory)
    if weapon_name:
        lines.append(f"Arme équipée : {weapon_name}.")
    return "\n".join(lines)


def _main_weapon_name(inventory: Inventory | None) -> str | None:
    """Return the display name of the main-hand equipped weapon, if any."""
    if inventory is None:
        return None
    item = inventory.equipped.get(EquipmentSlot.MAIN_HAND)
    if item is None:
        return None
    return item.name


def _describe_combat_for_narrator(
    state: CombatState,
    *,
    current_outcome_summary: str | None = None,
) -> str:
    """Build the ``## COMBAT ACTIVE`` section for an active combat.

    Lists round number, active combatant, each participant with
    filtered HP (exact for PCs, vague tier for NPCs), zone, conditions,
    and a short flavor line from the stat block when present. Appends
    the last three ``state.recent_events`` as mechanical grounding and
    closes on an explicit rule reminder for the narrator.

    ``current_outcome_summary`` is optionally used to dedup: the event
    matching the current turn's outcome is filtered out of the past-events
    list so the narrator doesn't see the current action twice.
    """
    lines: list[str] = ["## COMBAT ACTIVE"]
    lines.append(f"Round {state.round_number}")
    if 0 <= state.current_turn_index < len(state.combatants):
        current = state.combatants[state.current_turn_index]
        lines.append(f"Tour en cours : {current.name}")

    lines.append("")
    lines.append("### Combattants")
    for combatant in state.combatants:
        lines.append(_format_combatant_line(combatant))

    if state.recent_events:
        past_events = [
            ev for ev in state.recent_events[-3:]
            if current_outcome_summary is None or ev != current_outcome_summary
        ]
        if past_events:
            lines.append("")
            lines.append("### Derniers événements mécaniques")
            for event_text in past_events:
                lines.append(f"- {event_text}")

    lines.append("")
    lines.append(
        "**Règle** : tu DOIS respecter l'état mécanique. Un miss est un miss, "
        "les dégâts chiffrés sont canon, personne n'ignore le combat."
    )
    return "\n".join(lines)


def _format_combatant_line(combatant: Combatant) -> str:
    """Format one bullet line for the COMBAT ACTIVE combatants list."""
    if not combatant.is_alive and not combatant.fled:
        return f"- {combatant.name} : MORT"
    if combatant.fled:
        return f"- {combatant.name} : a fui"

    if combatant.side == CombatSide.PLAYER:
        hp_str = f"{combatant.character.hp}/{combatant.character.max_hp} HP"
    else:
        hp_str = _describe_npc_hp_vague(
            combatant.character.hp, combatant.character.max_hp,
        )

    zone_str = (
        f" (zone : {combatant.current_zone})" if combatant.current_zone else ""
    )

    conditions = [ac.condition_type.value for ac in combatant.conditions]
    cond_str = f" [{', '.join(conditions)}]" if conditions else ""

    flavor_str = ""
    if (
        combatant.side == CombatSide.ENEMY
        and combatant.stat_block is not None
    ):
        archetype = combatant.stat_block.archetype.strip()
        tier = combatant.stat_block.tier.value
        if archetype:
            flavor_str = f" — archétype {archetype} ({tier})"

    return f"- {combatant.name} : {hp_str}{zone_str}{cond_str}{flavor_str}"


def _describe_npc_hp_vague(hp: int, max_hp: int) -> str:
    """Return a coarse, spoiler-safe HP tier label for an NPC combatant."""
    ratio = hp / max(1, max_hp)
    if ratio > 0.8:
        return "indemne"
    if ratio > 0.5:
        return "légèrement blessé"
    if ratio > 0.2:
        return "gravement blessé"
    return "à l'article de la mort"
