"""Tests for db/repositories/ — CRUD operations with in-memory SQLite."""

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db.repositories.campaign_repo import CampaignRepository
from db.repositories.location_repo import LocationRepository
from db.repositories.npc_repo import NPCRepository
from db.repositories.quest_repo import QuestRepository
from world.campaign import Campaign
from world.location import Location
from world.npc import NPC, DialogueExchange, NPCDisposition
from world.quest import Quest, QuestStatus


# ---------------------------------------------------------------------------
# CampaignRepository
# ---------------------------------------------------------------------------


class TestCampaignRepository:
    """Campaign CRUD tests."""

    def test_save_and_get(self, db_session: Session, sample_campaign: Campaign) -> None:
        repo = CampaignRepository(db_session)
        repo.save(sample_campaign)
        db_session.commit()

        result = repo.get_by_id(sample_campaign.id)
        assert result is not None
        assert result.name == sample_campaign.name

    def test_get_missing_returns_none(self, db_session: Session) -> None:
        repo = CampaignRepository(db_session)
        assert repo.get_by_id("nonexistent") is None

    def test_list_all(self, db_session: Session) -> None:
        repo = CampaignRepository(db_session)
        repo.save(Campaign(id="c1", name="First"))
        repo.save(Campaign(id="c2", name="Second"))
        db_session.commit()

        results = repo.list_all()
        assert len(results) == 2
        names = {c.name for c in results}
        assert names == {"First", "Second"}

    def test_update(self, db_session: Session, sample_campaign: Campaign) -> None:
        repo = CampaignRepository(db_session)
        repo.save(sample_campaign)
        db_session.commit()

        updated = sample_campaign.model_copy(
            update={"name": "Updated Name", "interaction_count": 10}
        )
        repo.update(updated)
        db_session.commit()

        result = repo.get_by_id(sample_campaign.id)
        assert result is not None
        assert result.name == "Updated Name"
        assert result.interaction_count == 10

    def test_update_missing_raises(self, db_session: Session) -> None:
        repo = CampaignRepository(db_session)
        with pytest.raises(ValueError, match="not found"):
            repo.update(Campaign(id="nonexistent", name="X"))

    def test_delete(self, db_session: Session, sample_campaign: Campaign) -> None:
        repo = CampaignRepository(db_session)
        repo.save(sample_campaign)
        db_session.commit()

        repo.delete(sample_campaign.id)
        db_session.commit()

        assert repo.get_by_id(sample_campaign.id) is None

    def test_delete_missing_is_noop(self, db_session: Session) -> None:
        repo = CampaignRepository(db_session)
        repo.delete("nonexistent")  # should not raise


# ---------------------------------------------------------------------------
# NPCRepository
# ---------------------------------------------------------------------------


class TestNPCRepository:
    """NPC CRUD tests."""

    def test_save_and_get(self, db_session: Session, sample_campaign: Campaign, sample_npc: NPC) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = NPCRepository(db_session)
        repo.save(sample_npc, sample_campaign.id)
        db_session.commit()

        result = repo.get_by_name("Gundren Rockseeker", sample_campaign.id)
        assert result is not None
        assert result.name == "Gundren Rockseeker"
        assert result.race == sample_npc.race
        assert result.ability_scores == sample_npc.ability_scores

    def test_get_missing_returns_none(self, db_session: Session) -> None:
        repo = NPCRepository(db_session)
        assert repo.get_by_name("Nobody", "no-campaign") is None

    def test_list_by_campaign(self, db_session: Session, sample_campaign: Campaign, sample_npc: NPC) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = NPCRepository(db_session)
        repo.save(sample_npc, sample_campaign.id)
        npc2 = sample_npc.model_copy(update={"name": "Sildar Hallwinter", "location_name": "Phandalin"})
        repo.save(npc2, sample_campaign.id)
        db_session.commit()

        results = repo.list_by_campaign(sample_campaign.id)
        assert len(results) == 2

    def test_list_by_location(self, db_session: Session, sample_campaign: Campaign, sample_npc: NPC) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = NPCRepository(db_session)
        repo.save(sample_npc, sample_campaign.id)  # location: Neverwinter
        npc2 = sample_npc.model_copy(update={"name": "Sildar", "location_name": "Phandalin"})
        repo.save(npc2, sample_campaign.id)
        db_session.commit()

        results = repo.list_by_location("Neverwinter", sample_campaign.id)
        assert len(results) == 1
        assert results[0].name == "Gundren Rockseeker"

    def test_list_by_location_excludes_dead_npcs(
        self, db_session: Session, sample_campaign: Campaign, sample_npc: NPC,
    ) -> None:
        """Dead NPCs must not be rehydrated into the scene (audit H15)."""
        CampaignRepository(db_session).save(sample_campaign)
        repo = NPCRepository(db_session)
        repo.save(sample_npc, sample_campaign.id)  # location: Neverwinter
        corpse = sample_npc.model_copy(
            update={"name": "Bandit mort", "is_alive": False, "hp": 0},
        )
        repo.save(corpse, sample_campaign.id)
        db_session.commit()

        results = repo.list_by_location("Neverwinter", sample_campaign.id)
        assert [n.name for n in results] == ["Gundren Rockseeker"]

    def test_list_by_location_alive_only_false_includes_dead(
        self, db_session: Session, sample_campaign: Campaign, sample_npc: NPC,
    ) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = NPCRepository(db_session)
        corpse = sample_npc.model_copy(
            update={"name": "Bandit mort", "is_alive": False, "hp": 0},
        )
        repo.save(corpse, sample_campaign.id)
        db_session.commit()

        results = repo.list_by_location(
            "Neverwinter", sample_campaign.id, alive_only=False,
        )
        assert [n.name for n in results] == ["Bandit mort"]

    def test_update(self, db_session: Session, sample_campaign: Campaign, sample_npc: NPC) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = NPCRepository(db_session)
        repo.save(sample_npc, sample_campaign.id)
        db_session.commit()

        updated = sample_npc.model_copy(update={"hp": 10, "disposition": NPCDisposition.HOSTILE})
        repo.update(updated, sample_campaign.id)
        db_session.commit()

        result = repo.get_by_name(sample_npc.name, sample_campaign.id)
        assert result is not None
        assert result.hp == 10
        assert result.disposition == NPCDisposition.HOSTILE

    def test_update_preserves_all_fields(
        self, db_session: Session, sample_campaign: Campaign, sample_npc: NPC,
    ) -> None:
        """Regression: update() must persist aliases, secrets, knowledge, dialogue_history."""
        CampaignRepository(db_session).save(sample_campaign)
        repo = NPCRepository(db_session)

        # Save NPC with rich data in the four previously-lost fields
        npc_with_data = sample_npc.model_copy(update={
            "aliases": ["Gundren", "The Rockseeker"],
            "secrets": ["Knows location of Wave Echo Cave"],
            "knowledge": ["Phandalin history", "Mining lore"],
            "dialogue_history": [
                DialogueExchange(
                    player_said="Where is the cave?",
                    npc_said="I cannot tell you yet.",
                    revealed=["cave_exists"],
                ),
            ],
        })
        repo.save(npc_with_data, sample_campaign.id)
        db_session.commit()

        # Update: change disposition AND add more dialogue
        updated = npc_with_data.model_copy(update={
            "disposition": NPCDisposition.HOSTILE,
            "dialogue_history": npc_with_data.dialogue_history + [
                DialogueExchange(
                    player_said="Tell me now!",
                    npc_said="Fine, it is to the east.",
                    revealed=["cave_location"],
                ),
            ],
            "secrets": ["Knows location of Wave Echo Cave", "Has a map"],
        })
        repo.update(updated, sample_campaign.id)
        db_session.commit()

        result = repo.get_by_name(sample_npc.name, sample_campaign.id)
        assert result is not None
        assert result.disposition == NPCDisposition.HOSTILE
        assert result.aliases == ["Gundren", "The Rockseeker"]
        assert result.secrets == ["Knows location of Wave Echo Cave", "Has a map"]
        assert result.knowledge == ["Phandalin history", "Mining lore"]
        assert len(result.dialogue_history) == 2
        assert result.dialogue_history[0].player_said == "Where is the cave?"
        assert result.dialogue_history[1].npc_said == "Fine, it is to the east."
        assert result.dialogue_history[1].revealed == ["cave_location"]

    def test_update_missing_raises(self, db_session: Session, sample_npc: NPC) -> None:
        repo = NPCRepository(db_session)
        with pytest.raises(ValueError, match="not found"):
            repo.update(sample_npc, "no-campaign")

    def test_delete(self, db_session: Session, sample_campaign: Campaign, sample_npc: NPC) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = NPCRepository(db_session)
        repo.save(sample_npc, sample_campaign.id)
        db_session.commit()

        repo.delete(sample_npc.name, sample_campaign.id)
        db_session.commit()

        assert repo.get_by_name(sample_npc.name, sample_campaign.id) is None

    def test_unique_constraint(self, db_session: Session, sample_campaign: Campaign, sample_npc: NPC) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = NPCRepository(db_session)
        repo.save(sample_npc, sample_campaign.id)
        db_session.commit()

        repo.save(sample_npc, sample_campaign.id)
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_roundtrips_without_stat_block(
        self,
        db_session: Session,
        sample_campaign: Campaign,
        sample_npc: NPC,
    ) -> None:
        """Regression: commoner NPC without stat_block still roundtrips cleanly."""
        CampaignRepository(db_session).save(sample_campaign)
        repo = NPCRepository(db_session)
        repo.save(sample_npc, sample_campaign.id)
        db_session.commit()

        result = repo.get_by_name(sample_npc.name, sample_campaign.id)
        assert result is not None
        assert result.stat_block is None

    def test_roundtrips_stat_block(
        self,
        db_session: Session,
        sample_campaign: Campaign,
        sample_npc: NPC,
    ) -> None:
        """Save then load an NPC that carries a combat stat block."""
        from engine.inventory import DamageType
        from engine.npc_stat_block import (
            BehaviorProfile,
            LegendaryAction,
            NPCAttack,
            NPCStatBlock,
            NPCTier,
            PhaseTransition,
            SignatureAbility,
            SignatureAbilityEffect,
        )

        block = NPCStatBlock(
            tier=NPCTier.BOSS,
            archetype="villain",
            multiattack_count=3,
            attacks=[
                NPCAttack(
                    name="Dread Blade",
                    damage_dice="2d6+4",
                    damage_type=DamageType.SLASHING,
                    to_hit_bonus=7,
                ),
            ],
            signature_abilities=[
                SignatureAbility(
                    name="Terrifying Shout",
                    description="Enemies must save or be frightened.",
                    usage="per_combat",
                    uses_remaining=1,
                    effects=[
                        SignatureAbilityEffect(
                            kind="condition",
                            condition_name="Frightened",
                            condition_duration_rounds=2,
                            save_ability="WIS",
                            save_dc=15,
                            target_scope="all_enemies",
                        ),
                    ],
                ),
            ],
            legendary_actions=[
                LegendaryAction(
                    name="Quick Strike",
                    cost=1,
                    description="An off-turn melee attack.",
                ),
            ],
            legendary_points_per_round=3,
            phases=[
                PhaseTransition(
                    trigger_hp_percent=50,
                    narrative_cue="The villain grows enraged.",
                    attack_bonus=2,
                ),
            ],
            behavior_profile=BehaviorProfile.TACTICAL,
            aggression_threshold=10,
        )
        boss = sample_npc.model_copy(update={"name": "Vellus the Cruel", "stat_block": block})

        CampaignRepository(db_session).save(sample_campaign)
        repo = NPCRepository(db_session)
        repo.save(boss, sample_campaign.id)
        db_session.commit()

        result = repo.get_by_name("Vellus the Cruel", sample_campaign.id)
        assert result is not None
        assert result.stat_block is not None
        assert result.stat_block.tier == NPCTier.BOSS
        assert result.stat_block.multiattack_count == 3
        assert result.stat_block.legendary_points_per_round == 3
        assert len(result.stat_block.phases) == 1
        assert result.stat_block.phases[0].trigger_hp_percent == 50
        assert result.stat_block == block

    def test_update_preserves_stat_block(
        self,
        db_session: Session,
        sample_campaign: Campaign,
        sample_npc: NPC,
    ) -> None:
        """update() path must persist stat_block changes, not drop them."""
        from engine.inventory import DamageType
        from engine.npc_stat_block import NPCAttack, NPCStatBlock, NPCTier

        block = NPCStatBlock(
            tier=NPCTier.MINION,
            archetype="bandit",
            attacks=[
                NPCAttack(
                    name="Scimitar",
                    damage_dice="1d6+1",
                    damage_type=DamageType.SLASHING,
                    to_hit_bonus=3,
                ),
            ],
        )
        npc_with_block = sample_npc.model_copy(update={"stat_block": block})

        CampaignRepository(db_session).save(sample_campaign)
        repo = NPCRepository(db_session)
        repo.save(npc_with_block, sample_campaign.id)
        db_session.commit()

        # Mutate and update
        new_block = block.model_copy(update={"aggression_threshold": 5})
        updated = npc_with_block.model_copy(update={"stat_block": new_block})
        repo.update(updated, sample_campaign.id)
        db_session.commit()

        result = repo.get_by_name(sample_npc.name, sample_campaign.id)
        assert result is not None
        assert result.stat_block is not None
        assert result.stat_block.aggression_threshold == 5


# ---------------------------------------------------------------------------
# LocationRepository
# ---------------------------------------------------------------------------


class TestLocationRepository:
    """Location CRUD tests."""

    def test_save_and_get(self, db_session: Session, sample_campaign: Campaign, sample_location: Location) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = LocationRepository(db_session)
        repo.save(sample_location, sample_campaign.id)
        db_session.commit()

        result = repo.get_by_name("Neverwinter", sample_campaign.id)
        assert result is not None
        assert result.description == sample_location.description
        assert result.connections == ["Phandalin", "Triboar"]

    def test_get_missing_returns_none(self, db_session: Session) -> None:
        repo = LocationRepository(db_session)
        assert repo.get_by_name("Nowhere", "no-campaign") is None

    def test_list_by_campaign(self, db_session: Session, sample_campaign: Campaign, sample_location: Location) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = LocationRepository(db_session)
        repo.save(sample_location, sample_campaign.id)
        repo.save(Location(name="Phandalin"), sample_campaign.id)
        db_session.commit()

        results = repo.list_by_campaign(sample_campaign.id)
        assert len(results) == 2

    def test_update(self, db_session: Session, sample_campaign: Campaign, sample_location: Location) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = LocationRepository(db_session)
        repo.save(sample_location, sample_campaign.id)
        db_session.commit()

        updated = sample_location.model_copy(update={"description": "A ruined city"})
        repo.update(updated, sample_campaign.id)
        db_session.commit()

        result = repo.get_by_name(sample_location.name, sample_campaign.id)
        assert result is not None
        assert result.description == "A ruined city"

    def test_update_persists_item_descriptions(
        self, db_session: Session, sample_campaign: Campaign, sample_location: Location,
    ) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = LocationRepository(db_session)
        repo.save(sample_location, sample_campaign.id)
        db_session.commit()

        updated = sample_location.model_copy(
            update={"item_descriptions": {"Healing Potion": "A red vial."}},
        )
        repo.update(updated, sample_campaign.id)
        db_session.commit()

        result = repo.get_by_name(sample_location.name, sample_campaign.id)
        assert result is not None
        assert result.item_descriptions == {"Healing Potion": "A red vial."}

    def test_update_persists_combat_triggers_and_npc_roles(
        self, db_session: Session, sample_campaign: Campaign, sample_location: Location,
    ) -> None:
        """H6 — ambush triggers and NPC roles must survive update().

        Losing them on the first save/reload erased generated ambushes,
        re-spawned enemies as 8-HP commoners, and made consumed triggers
        re-farmable.
        """
        from world.combat_trigger_def import CombatTriggerDef

        CampaignRepository(db_session).save(sample_campaign)
        repo = LocationRepository(db_session)
        repo.save(sample_location, sample_campaign.id)
        db_session.commit()

        updated = sample_location.model_copy(update={
            "combat_triggers": {
                "levier": CombatTriggerDef(
                    item_name="levier",
                    spawn_npcs=["Squelette"],
                    consumed=True,
                ),
            },
            "npc_roles": {"Garde": "soldier"},
        })
        repo.update(updated, sample_campaign.id)
        db_session.commit()

        result = repo.get_by_name(sample_location.name, sample_campaign.id)
        assert result is not None
        assert "levier" in result.combat_triggers
        assert result.combat_triggers["levier"].spawn_npcs == ["Squelette"]
        assert result.combat_triggers["levier"].consumed is True
        assert result.npc_roles == {"Garde": "soldier"}

    def test_upsert_persists_combat_triggers_and_npc_roles(
        self, db_session: Session, sample_campaign: Campaign, sample_location: Location,
    ) -> None:
        """H6 — same guarantee on the upsert (existing-row) path."""
        from world.combat_trigger_def import CombatTriggerDef

        CampaignRepository(db_session).save(sample_campaign)
        repo = LocationRepository(db_session)
        repo.save(sample_location, sample_campaign.id)
        db_session.commit()

        updated = sample_location.model_copy(update={
            "combat_triggers": {
                "coffre": CombatTriggerDef(item_name="coffre", spawn_npcs=["Mimic"]),
            },
            "npc_roles": {"Aubergiste": "commoner"},
        })
        repo.upsert(updated, sample_campaign.id)
        db_session.commit()

        result = repo.get_by_name(sample_location.name, sample_campaign.id)
        assert result is not None
        assert result.combat_triggers["coffre"].spawn_npcs == ["Mimic"]
        assert result.npc_roles == {"Aubergiste": "commoner"}

    def test_update_missing_raises(self, db_session: Session, sample_location: Location) -> None:
        repo = LocationRepository(db_session)
        with pytest.raises(ValueError, match="not found"):
            repo.update(sample_location, "no-campaign")

    def test_delete(self, db_session: Session, sample_campaign: Campaign, sample_location: Location) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = LocationRepository(db_session)
        repo.save(sample_location, sample_campaign.id)
        db_session.commit()

        repo.delete(sample_location.name, sample_campaign.id)
        db_session.commit()

        assert repo.get_by_name(sample_location.name, sample_campaign.id) is None

    def test_unique_constraint(self, db_session: Session, sample_campaign: Campaign, sample_location: Location) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = LocationRepository(db_session)
        repo.save(sample_location, sample_campaign.id)
        db_session.commit()

        repo.save(sample_location, sample_campaign.id)
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_roundtrips_combat_zones(
        self,
        db_session: Session,
        sample_campaign: Campaign,
    ) -> None:
        """Locations with combat zones persist and reload cleanly."""
        from world.combat_zone import Zone, ZoneTag

        location = Location(
            name="Burning Barn",
            description="A barn set ablaze in the middle of the night.",
            combat_zones=[
                Zone(
                    name="Entrance",
                    description="The scorched barn doors.",
                    adjacent_zone_names=["Hayloft", "Central"],
                ),
                Zone(
                    name="Central",
                    description="The main open area, engulfed in smoke.",
                    adjacent_zone_names=["Entrance", "Hayloft"],
                    tags=[ZoneTag.HAZARD, ZoneTag.OBSCURED],
                ),
                Zone(
                    name="Hayloft",
                    description="A raised loft above the central floor.",
                    adjacent_zone_names=["Entrance", "Central"],
                    tags=[ZoneTag.ELEVATED, ZoneTag.COVER],
                ),
            ],
        )

        CampaignRepository(db_session).save(sample_campaign)
        repo = LocationRepository(db_session)
        repo.save(location, sample_campaign.id)
        db_session.commit()

        result = repo.get_by_name("Burning Barn", sample_campaign.id)
        assert result is not None
        assert result.has_combat_zones() is True
        assert len(result.combat_zones) == 3
        assert result.are_adjacent("Entrance", "Central") is True
        hayloft = result.get_zone("Hayloft")
        assert hayloft is not None
        assert ZoneTag.ELEVATED in hayloft.tags
        assert ZoneTag.COVER in hayloft.tags

    def test_roundtrips_without_combat_zones(
        self,
        db_session: Session,
        sample_campaign: Campaign,
        sample_location: Location,
    ) -> None:
        """Regression: legacy locations without zones still roundtrip."""
        CampaignRepository(db_session).save(sample_campaign)
        repo = LocationRepository(db_session)
        repo.save(sample_location, sample_campaign.id)
        db_session.commit()

        result = repo.get_by_name(sample_location.name, sample_campaign.id)
        assert result is not None
        assert result.combat_zones == []
        assert result.has_combat_zones() is False

    def test_update_persists_combat_zones(
        self,
        db_session: Session,
        sample_campaign: Campaign,
        sample_location: Location,
    ) -> None:
        """The update() path must propagate zone changes to the row."""
        from world.combat_zone import Zone

        CampaignRepository(db_session).save(sample_campaign)
        repo = LocationRepository(db_session)
        repo.save(sample_location, sample_campaign.id)
        db_session.commit()

        updated = sample_location.model_copy(
            update={
                "combat_zones": [
                    Zone(name="North", adjacent_zone_names=["South"]),
                    Zone(name="South", adjacent_zone_names=["North"]),
                ],
            }
        )
        repo.update(updated, sample_campaign.id)
        db_session.commit()

        result = repo.get_by_name(sample_location.name, sample_campaign.id)
        assert result is not None
        assert len(result.combat_zones) == 2
        assert result.are_adjacent("North", "South") is True

    def test_location_state_flags_persist(self, db_session: Session, sample_campaign: Campaign) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = LocationRepository(db_session)
        loc = Location(
            name="Bone Barrier",
            description="A wall of bones.",
            connections=["Village"],
            state_flags={"lever_activated": True, "breach_open": True},
            unlocked_exits=["Inner Court"],
        )
        repo.save(loc, sample_campaign.id)
        db_session.commit()

        loaded = repo.get_by_name("Bone Barrier", sample_campaign.id)
        assert loaded is not None
        assert loaded.state_flags == {"lever_activated": True, "breach_open": True}
        assert loaded.unlocked_exits == ["Inner Court"]

    def test_location_update_persists_state_flags(self, db_session: Session, sample_campaign: Campaign) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = LocationRepository(db_session)
        loc = Location(name="Barrier", description="Desc", connections=["A"])
        repo.save(loc, sample_campaign.id)
        db_session.commit()

        loc.state_flags["puzzle_solved"] = True
        loc.unlocked_exits.append("Secret Exit")
        repo.update(loc, sample_campaign.id)
        db_session.commit()

        loaded = repo.get_by_name("Barrier", sample_campaign.id)
        assert loaded is not None
        assert loaded.state_flags == {"puzzle_solved": True}
        assert loaded.unlocked_exits == ["Secret Exit"]

    def test_exit_aliases_and_generated_flag_persist(
        self, db_session: Session, sample_campaign: Campaign,
    ) -> None:
        """exit_aliases and the generated flag round-trip through the DB."""
        CampaignRepository(db_session).save(sample_campaign)
        repo = LocationRepository(db_session)
        loc = Location(
            name="Carrefour",
            description="Un carrefour brumeux.",
            connections=["Sentier nord", "Route sud"],
            exit_aliases={
                "Sentier nord": ["nord", "sentier"],
                "Route sud": ["sud", "route"],
            },
            generated=True,
        )
        repo.save(loc, sample_campaign.id)
        db_session.commit()

        loaded = repo.get_by_name("Carrefour", sample_campaign.id)
        assert loaded is not None
        assert loaded.exit_aliases == {
            "Sentier nord": ["nord", "sentier"],
            "Route sud": ["sud", "route"],
        }
        assert loaded.generated is True

    def test_upsert_inserts_when_missing(
        self, db_session: Session, sample_campaign: Campaign,
    ) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = LocationRepository(db_session)
        loc = Location(name="Stub", description="", generated=False)
        repo.upsert(loc, sample_campaign.id)
        db_session.commit()

        loaded = repo.get_by_name("Stub", sample_campaign.id)
        assert loaded is not None
        assert loaded.generated is False

    def test_upsert_is_idempotent(
        self, db_session: Session, sample_campaign: Campaign,
    ) -> None:
        """Calling upsert twice with the same name updates the row in place
        instead of raising on the (campaign_id, name) unique constraint."""
        CampaignRepository(db_session).save(sample_campaign)
        repo = LocationRepository(db_session)
        loc = Location(name="Place", description="First", generated=False)
        repo.upsert(loc, sample_campaign.id)
        db_session.commit()

        # Second call: same name, different content + marking as generated.
        updated = Location(
            name="Place",
            description="Fully hydrated",
            connections=["Ailleurs"],
            exit_aliases={"Ailleurs": ["autre", "loin"]},
            generated=True,
        )
        repo.upsert(updated, sample_campaign.id)
        db_session.commit()

        loaded = repo.get_by_name("Place", sample_campaign.id)
        assert loaded is not None
        assert loaded.description == "Fully hydrated"
        assert loaded.generated is True
        assert loaded.connections == ["Ailleurs"]
        assert loaded.exit_aliases == {"Ailleurs": ["autre", "loin"]}
        # Only one row should exist.
        assert len(repo.list_by_campaign(sample_campaign.id)) == 1


# ---------------------------------------------------------------------------
# QuestRepository
# ---------------------------------------------------------------------------


class TestQuestRepository:
    """Quest CRUD tests."""

    def test_save_and_get(self, db_session: Session, sample_campaign: Campaign, sample_quest: Quest) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = QuestRepository(db_session)
        repo.save(sample_quest, sample_campaign.id)
        db_session.commit()

        result = repo.get_by_title("Find the Lost Mine", sample_campaign.id)
        assert result is not None
        assert result.status == QuestStatus.ACTIVE
        assert len(result.objectives) == 2
        assert result.objectives[1].is_complete is True

    def test_get_missing_returns_none(self, db_session: Session) -> None:
        repo = QuestRepository(db_session)
        assert repo.get_by_title("Nothing", "no-campaign") is None

    def test_list_by_campaign(self, db_session: Session, sample_campaign: Campaign, sample_quest: Quest) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = QuestRepository(db_session)
        repo.save(sample_quest, sample_campaign.id)
        repo.save(Quest(title="Side Quest"), sample_campaign.id)
        db_session.commit()

        results = repo.list_by_campaign(sample_campaign.id)
        assert len(results) == 2

    def test_update_status(self, db_session: Session, sample_campaign: Campaign, sample_quest: Quest) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = QuestRepository(db_session)
        repo.save(sample_quest, sample_campaign.id)
        db_session.commit()

        updated = sample_quest.model_copy(update={"status": QuestStatus.COMPLETED})
        repo.update(updated, sample_campaign.id)
        db_session.commit()

        result = repo.get_by_title(sample_quest.title, sample_campaign.id)
        assert result is not None
        assert result.status == QuestStatus.COMPLETED

    def test_update_missing_raises(self, db_session: Session, sample_quest: Quest) -> None:
        repo = QuestRepository(db_session)
        with pytest.raises(ValueError, match="not found"):
            repo.update(sample_quest, "no-campaign")

    def test_delete(self, db_session: Session, sample_campaign: Campaign, sample_quest: Quest) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = QuestRepository(db_session)
        repo.save(sample_quest, sample_campaign.id)
        db_session.commit()

        repo.delete(sample_quest.title, sample_campaign.id)
        db_session.commit()

        assert repo.get_by_title(sample_quest.title, sample_campaign.id) is None

    def test_unique_constraint(self, db_session: Session, sample_campaign: Campaign, sample_quest: Quest) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = QuestRepository(db_session)
        repo.save(sample_quest, sample_campaign.id)
        db_session.commit()

        repo.save(sample_quest, sample_campaign.id)
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()


# ---------------------------------------------------------------------------
# Cascade deletes
# ---------------------------------------------------------------------------


class TestCascadeDeletes:
    """Verify that deleting a campaign cascades to all children."""

    def test_delete_campaign_cascades(
        self,
        db_session: Session,
        sample_campaign: Campaign,
        sample_npc: NPC,
        sample_location: Location,
        sample_quest: Quest,
    ) -> None:
        camp_repo = CampaignRepository(db_session)
        camp_repo.save(sample_campaign)

        NPCRepository(db_session).save(sample_npc, sample_campaign.id)
        LocationRepository(db_session).save(sample_location, sample_campaign.id)
        QuestRepository(db_session).save(sample_quest, sample_campaign.id)
        db_session.commit()

        camp_repo.delete(sample_campaign.id)
        db_session.commit()

        assert NPCRepository(db_session).list_by_campaign(sample_campaign.id) == []
        assert LocationRepository(db_session).list_by_campaign(sample_campaign.id) == []
        assert QuestRepository(db_session).list_by_campaign(sample_campaign.id) == []


# ---------------------------------------------------------------------------
# C5 — Idempotent upsert on PC/NPC/Quest/StoryArc
# ---------------------------------------------------------------------------


class TestUpsertIdempotent:
    """Each new upsert() method must insert when missing and update when present,
    without the exception-driven control flow that bot/persistence.py used to rely on.
    """

    def test_npc_upsert_inserts_then_updates(
        self,
        db_session: Session,
        sample_campaign: Campaign,
        sample_npc: NPC,
    ) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = NPCRepository(db_session)

        repo.upsert(sample_npc, sample_campaign.id)
        db_session.commit()

        # Mutate and upsert again — same name, should update in place.
        sample_npc.hp = 1
        repo.upsert(sample_npc, sample_campaign.id)
        db_session.commit()

        loaded = repo.get_by_name(sample_npc.name, sample_campaign.id)
        assert loaded is not None
        assert loaded.hp == 1
        assert len(repo.list_by_campaign(sample_campaign.id)) == 1

    def test_quest_upsert_inserts_then_updates(
        self,
        db_session: Session,
        sample_campaign: Campaign,
        sample_quest: Quest,
    ) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = QuestRepository(db_session)

        repo.upsert(sample_quest, sample_campaign.id)
        db_session.commit()

        sample_quest.status = QuestStatus.COMPLETED
        repo.upsert(sample_quest, sample_campaign.id)
        db_session.commit()

        loaded = repo.get_by_title(sample_quest.title, sample_campaign.id)
        assert loaded is not None
        assert loaded.status == QuestStatus.COMPLETED
        assert len(repo.list_by_campaign(sample_campaign.id)) == 1

    def test_player_character_upsert_inserts_then_updates(
        self,
        db_session: Session,
        sample_campaign: Campaign,
    ) -> None:
        from engine.character import (
            AbilityScores,
            CharacterClass,
            Race,
            create_character,
        )
        from engine.inventory import create_inventory

        from db.repositories.player_character_repo import (
            PlayerCharacterRepository,
        )

        CampaignRepository(db_session).save(sample_campaign)
        scores = AbilityScores(STR=16, DEX=14, CON=13, INT=10, WIS=12, CHA=8)
        char = create_character("Thorin", Race.DWARF, CharacterClass.FIGHTER, scores)
        inv = create_inventory()
        repo = PlayerCharacterRepository(db_session)

        repo.upsert(123, sample_campaign.id, char, inv, None)
        db_session.commit()

        # Same user_id + campaign_id → update in place (no IntegrityError)
        char.hp = 1
        repo.upsert(123, sample_campaign.id, char, inv, None)
        db_session.commit()

        loaded = repo.get(123, sample_campaign.id)
        assert loaded is not None
        loaded_char, _, _ = loaded
        assert loaded_char.hp == 1
        assert len(repo.get_all_for_campaign(sample_campaign.id)) == 1

    def test_story_arc_upsert_inserts_then_updates(
        self,
        db_session: Session,
        sample_campaign: Campaign,
    ) -> None:
        from world.story_arc import StoryArc, StoryBeat

        from db.repositories.story_arc_repo import StoryArcRepository

        CampaignRepository(db_session).save(sample_campaign)
        types = ["social", "combat", "exploration", "puzzle", "boss"]
        beats = [
            StoryBeat(
                beat_number=i + 1,
                title=f"Beat {i + 1}",
                description="A meaningful beat description.",
                location_hint=f"Place {i + 1}",
                encounter_type=types[i % len(types)],
            )
            for i in range(8)
        ]
        arc = StoryArc(
            campaign_id=sample_campaign.id,
            theme="mystery",
            premise="A long-buried evil has stirred and the heroes must investigate.",
            beats=beats,
            villain_name="The Hollow King",
            villain_motivation="Restore his sundered realm",
            current_beat_index=0,
        )
        repo = StoryArcRepository(db_session)

        # Insert path
        repo.upsert(arc)
        db_session.commit()

        # Update path: change theme + advance beat
        arc2 = arc.model_copy(update={"current_beat_index": 1, "theme": "horror"})
        repo.upsert(arc2)
        db_session.commit()

        loaded = repo.get_by_campaign(sample_campaign.id)
        assert loaded is not None
        assert loaded.theme == "horror"
        assert loaded.current_beat_index == 1
