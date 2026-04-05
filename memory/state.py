"""Layer 1 — Structured state builder.

Reads from existing SQLite repositories (Campaign, NPC, Location, Quest)
and accepts in-memory objects (Character, CombatState, Inventory) to build
a compact GameStateSummary for prompt injection.
"""

from sqlalchemy.orm import Session

from db.repositories.campaign_repo import CampaignRepository
from db.repositories.location_repo import LocationRepository
from db.repositories.npc_repo import NPCRepository
from db.repositories.quest_repo import QuestRepository
from engine.character import Character
from engine.combat import CombatState
from engine.inventory import Inventory
from memory.models import (
    CharacterSummary,
    CombatSummary,
    GameStateSummary,
)
from memory.token_utils import truncate_to_tokens


class StateBuilder:
    """Builds a structured state summary from DB and in-memory state."""

    def __init__(self, session: Session) -> None:
        self._campaign_repo = CampaignRepository(session)
        self._npc_repo = NPCRepository(session)
        self._location_repo = LocationRepository(session)
        self._quest_repo = QuestRepository(session)

    def build(
        self,
        campaign_id: str,
        player_characters: list[Character] | None = None,
        combat_state: CombatState | None = None,
        inventories: dict[str, Inventory] | None = None,
    ) -> GameStateSummary:
        """Build a GameStateSummary from all structured data sources."""
        campaign = self._campaign_repo.get_by_id(campaign_id)
        if campaign is None:
            return GameStateSummary(campaign_name="Unknown")

        # Location
        current_location = campaign.current_location
        location_description = ""
        if current_location:
            loc = self._location_repo.get_by_name(current_location, campaign_id)
            if loc:
                location_description = loc.description

        # NPCs at current location
        nearby_npcs: list[str] = []
        if current_location:
            npcs = self._npc_repo.list_by_location(current_location, campaign_id)
            nearby_npcs = [
                f"{npc.name} ({npc.disposition.value})"
                for npc in npcs
                if npc.is_alive
            ]

        # Active quests
        quests = self._quest_repo.list_by_campaign(campaign_id)
        active_quests = [
            f"{q.title} ({q.status.value})"
            for q in quests
            if q.status.value in ("active", "available")
        ]

        # Player characters
        char_summaries: list[CharacterSummary] = []
        if player_characters:
            for char in player_characters:
                char_summaries.append(
                    CharacterSummary(
                        name=char.name,
                        race=char.race.value,
                        char_class=char.char_class.value,
                        level=char.level,
                        hp=char.hp,
                        max_hp=char.max_hp,
                        ac=char.ac,
                    )
                )

        # Combat state
        combat_summary: CombatSummary | None = None
        if combat_state and combat_state.is_active and combat_state.combatants:
            idx = combat_state.current_turn_index % len(combat_state.combatants)
            current_combatant = combat_state.combatants[idx]
            combat_chars = [
                CharacterSummary(
                    name=c.name,
                    race=c.character.race.value,
                    char_class=c.character.char_class.value,
                    level=c.character.level,
                    hp=c.character.hp,
                    max_hp=c.character.max_hp,
                    ac=c.character.ac,
                    conditions=[
                        cond.condition_type.value for cond in c.conditions
                    ],
                )
                for c in combat_state.combatants
                if c.is_alive
            ]
            combat_summary = CombatSummary(
                is_active=True,
                round_number=combat_state.round_number,
                current_turn=current_combatant.name,
                combatants=combat_chars,
            )

        # Inventory highlights
        inventory_highlights: list[str] = []
        if inventories:
            for name, inv in inventories.items():
                notable = [
                    f"{item.name} x{item.quantity}"
                    if item.quantity > 1
                    else item.name
                    for item in inv.items
                    if item.magical or item.rarity.value != "Common"
                ]
                inventory_highlights.extend(notable)

        return GameStateSummary(
            campaign_name=campaign.name,
            current_location=current_location,
            location_description=location_description,
            player_characters=char_summaries,
            nearby_npcs=nearby_npcs,
            active_quests=active_quests,
            combat=combat_summary,
            inventory_highlights=inventory_highlights,
        )

    def render(self, summary: GameStateSummary, max_tokens: int = 450) -> str:
        """Render the GameStateSummary into a text block for the prompt."""
        lines: list[str] = ["[GAME STATE]"]
        lines.append(f"Campaign: {summary.campaign_name}")

        if summary.current_location:
            loc_line = f"Location: {summary.current_location}"
            if summary.location_description:
                loc_line += f" — {summary.location_description}"
            lines.append(loc_line)

        if summary.player_characters:
            chars = ", ".join(
                f"{c.name} ({c.race} {c.char_class} L{c.level}, "
                f"HP {c.hp}/{c.max_hp}, AC {c.ac})"
                for c in summary.player_characters
            )
            lines.append(f"Players: {chars}")

        if summary.nearby_npcs:
            lines.append(f"Nearby NPCs: {', '.join(summary.nearby_npcs)}")

        if summary.active_quests:
            lines.append(f"Active Quests: {', '.join(summary.active_quests)}")

        if summary.combat and summary.combat.is_active:
            c = summary.combat
            combatant_strs = ", ".join(
                f"{ch.name} (HP {ch.hp}/{ch.max_hp})"
                for ch in c.combatants
            )
            lines.append(
                f"Combat: Round {c.round_number}, {c.current_turn}'s turn. "
                f"Combatants: {combatant_strs}"
            )

        if summary.inventory_highlights:
            lines.append(
                f"Notable Items: {', '.join(summary.inventory_highlights)}"
            )

        text = "\n".join(lines)
        return truncate_to_tokens(text, max_tokens)
