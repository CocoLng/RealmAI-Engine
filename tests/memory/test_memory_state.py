"""Tests for memory/state.py — Layer 1 structured state builder."""

from sqlalchemy.orm import Session

from db.repositories.campaign_repo import CampaignRepository
from db.repositories.location_repo import LocationRepository
from db.repositories.npc_repo import NPCRepository
from db.repositories.quest_repo import QuestRepository
from engine.character import Character
from engine.combat import CombatSide, CombatState, Combatant
from engine.inventory import Inventory
from memory.models import GameStateSummary
from memory.state import StateBuilder
from memory.token_utils import estimate_tokens
from world.campaign import Campaign
from world.location import Location
from world.npc import NPC
from world.quest import Quest


class TestStateBuilder:
    def test_build_minimal(
        self, db_session: Session, sample_campaign: Campaign,
    ) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        db_session.commit()
        builder = StateBuilder(db_session)
        summary = builder.build(sample_campaign.id)
        assert isinstance(summary, GameStateSummary)
        assert summary.campaign_name == sample_campaign.name
        assert summary.player_characters == []
        assert summary.combat is None

    def test_build_unknown_campaign(self, db_session: Session) -> None:
        builder = StateBuilder(db_session)
        summary = builder.build("nonexistent-id")
        assert summary.campaign_name == "Unknown"

    def test_build_with_location(
        self,
        db_session: Session,
        sample_campaign: Campaign,
        sample_location: Location,
    ) -> None:
        campaign = sample_campaign.model_copy(
            update={"current_location": "Neverwinter"},
        )
        CampaignRepository(db_session).save(campaign)
        LocationRepository(db_session).save(sample_location, campaign.id)
        db_session.commit()
        builder = StateBuilder(db_session)
        summary = builder.build(campaign.id)
        assert summary.current_location == "Neverwinter"
        assert summary.location_description == "A bustling coastal city"

    def test_build_with_npcs_at_location(
        self,
        db_session: Session,
        sample_campaign: Campaign,
        sample_location: Location,
        sample_npc: NPC,
    ) -> None:
        campaign = sample_campaign.model_copy(
            update={"current_location": "Neverwinter"},
        )
        CampaignRepository(db_session).save(campaign)
        LocationRepository(db_session).save(sample_location, campaign.id)
        NPCRepository(db_session).save(sample_npc, campaign.id)
        db_session.commit()
        builder = StateBuilder(db_session)
        summary = builder.build(campaign.id)
        assert "Gundren Rockseeker (friendly)" in summary.nearby_npcs

    def test_build_with_active_quests(
        self,
        db_session: Session,
        sample_campaign: Campaign,
        sample_quest: Quest,
    ) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        QuestRepository(db_session).save(sample_quest, sample_campaign.id)
        db_session.commit()
        builder = StateBuilder(db_session)
        summary = builder.build(sample_campaign.id)
        assert "Find the Lost Mine (active)" in summary.active_quests

    def test_build_with_characters(
        self,
        db_session: Session,
        sample_campaign: Campaign,
        sample_character: Character,
    ) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        db_session.commit()
        builder = StateBuilder(db_session)
        summary = builder.build(
            sample_campaign.id, player_characters=[sample_character],
        )
        assert len(summary.player_characters) == 1
        assert summary.player_characters[0].name == "Thorin"
        assert summary.player_characters[0].race == "Dwarf"

    def test_build_with_combat_state(
        self,
        db_session: Session,
        sample_campaign: Campaign,
        sample_character: Character,
        sample_inventory: Inventory,
    ) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        db_session.commit()
        combatant = Combatant(
            name="Thorin",
            side=CombatSide.PLAYER,
            character=sample_character,
            inventory=sample_inventory,
            initiative=15,
            is_alive=True,
        )
        combat = CombatState(
            combatants=[combatant],
            round_number=3,
            current_turn_index=0,
            is_active=True,
        )
        builder = StateBuilder(db_session)
        summary = builder.build(
            sample_campaign.id,
            player_characters=[sample_character],
            combat_state=combat,
        )
        assert summary.combat is not None
        assert summary.combat.is_active is True
        assert summary.combat.round_number == 3
        assert summary.combat.current_turn == "Thorin"

    def test_render_basic(
        self, db_session: Session, sample_campaign: Campaign,
    ) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        db_session.commit()
        builder = StateBuilder(db_session)
        summary = builder.build(sample_campaign.id)
        text = builder.render(summary)
        assert "[GAME STATE]" in text
        assert sample_campaign.name in text

    def test_render_within_token_budget(
        self, db_session: Session, sample_campaign: Campaign,
    ) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        db_session.commit()
        builder = StateBuilder(db_session)
        summary = builder.build(sample_campaign.id)
        text = builder.render(summary, max_tokens=450)
        assert estimate_tokens(text) <= 450

    def test_render_truncation_preserves_combat_and_arc(
        self, db_session: Session,
    ) -> None:
        """Under a tight budget, combat and story-arc lines must survive —
        quest/inventory lines are the expendable ones (audit low: truncation
        was cutting the combat/arc tail instead)."""
        from memory.models import CharacterSummary, CombatSummary, GameStateSummary

        summary = GameStateSummary(
            campaign_name="Test",
            current_location="Cragmaw",
            location_description="A dark cave with " + "many details " * 30,
            player_characters=[
                CharacterSummary(
                    name="Thorin", race="Dwarf", char_class="Fighter",
                    level=3, hp=20, max_hp=25, ac=16,
                ),
            ],
            nearby_npcs=[f"Npc{i} (neutral)" for i in range(10)],
            active_quests=[f"Quest {i} (active) with a long title" for i in range(10)],
            combat=CombatSummary(
                is_active=True, round_number=2, current_turn="Thorin",
                combatants=[
                    CharacterSummary(
                        name="Thorin", race="Dwarf", char_class="Fighter",
                        level=3, hp=20, max_hp=25, ac=16,
                    ),
                ],
            ),
            inventory_highlights=[f"Magic item {i}" for i in range(10)],
            current_story_beat="The Goblin Ambush — the trail goes cold",
            upcoming_story_beat="Cragmaw Hideout",
            villain_context="Nezznar — wants the Forge of Spells",
        )
        builder = StateBuilder(db_session)
        full = builder.render(summary, max_tokens=10_000)
        tight_budget = estimate_tokens(full) - 40
        text = builder.render(summary, max_tokens=tight_budget)
        assert estimate_tokens(text) <= tight_budget
        assert "Combat: Round 2" in text
        assert "[STORY ARC]" in text
        assert "Goblin Ambush" in text
