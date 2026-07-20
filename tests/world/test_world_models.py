"""Tests for world/ domain models — pure Pydantic, no DB."""

import pytest
from pydantic import ValidationError

from engine.character import AbilityScores, CharacterClass, Race
from world.campaign import Campaign
from world.location import Location
from world.npc import NPC, NPCDisposition
from world.story_arc import StoryArc, StoryBeat


# ---------------------------------------------------------------------------
# NPC
# ---------------------------------------------------------------------------


class TestNPCDisposition:
    """NPCDisposition enum tests."""

    def test_all_values_exist(self) -> None:
        assert len(NPCDisposition) == 5

    def test_string_values(self) -> None:
        assert NPCDisposition.HOSTILE == "hostile"
        assert NPCDisposition.ALLIED == "allied"


class TestNPC:
    """NPC model tests."""

    def test_valid_construction(self, sample_npc: NPC) -> None:
        assert sample_npc.name == "Gundren Rockseeker"
        assert sample_npc.race == Race.DWARF
        assert sample_npc.char_class == CharacterClass.FIGHTER
        assert sample_npc.level == 5
        assert sample_npc.hp == 35
        assert sample_npc.max_hp == 40
        assert sample_npc.ac == 16

    def test_defaults(self, sample_ability_scores: AbilityScores) -> None:
        npc = NPC(
            name="Commoner",
            race=Race.HUMAN,
            ability_scores=sample_ability_scores,
            hp=4,
            max_hp=4,
            ac=10,
        )
        assert npc.char_class is None
        assert npc.level == 1
        assert npc.disposition == NPCDisposition.NEUTRAL
        assert npc.is_alive is True
        assert npc.description == ""
        assert npc.personality == ""
        assert npc.location_name is None

    def test_level_min_constraint(self, sample_ability_scores: AbilityScores) -> None:
        with pytest.raises(ValidationError):
            NPC(
                name="Bad",
                race=Race.HUMAN,
                ability_scores=sample_ability_scores,
                hp=1,
                max_hp=1,
                ac=10,
                level=0,
            )

    def test_level_max_constraint(self, sample_ability_scores: AbilityScores) -> None:
        with pytest.raises(ValidationError):
            NPC(
                name="Bad",
                race=Race.HUMAN,
                ability_scores=sample_ability_scores,
                hp=1,
                max_hp=1,
                ac=10,
                level=21,
            )

    def test_hp_min_constraint(self, sample_ability_scores: AbilityScores) -> None:
        with pytest.raises(ValidationError):
            NPC(
                name="Bad",
                race=Race.HUMAN,
                ability_scores=sample_ability_scores,
                hp=-1,
                max_hp=1,
                ac=10,
            )

    def test_max_hp_min_constraint(self, sample_ability_scores: AbilityScores) -> None:
        with pytest.raises(ValidationError):
            NPC(
                name="Bad",
                race=Race.HUMAN,
                ability_scores=sample_ability_scores,
                hp=0,
                max_hp=0,
                ac=10,
            )

    def test_ac_min_constraint(self, sample_ability_scores: AbilityScores) -> None:
        with pytest.raises(ValidationError):
            NPC(
                name="Bad",
                race=Race.HUMAN,
                ability_scores=sample_ability_scores,
                hp=1,
                max_hp=1,
                ac=-1,
            )

    def test_model_roundtrip(self, sample_npc: NPC) -> None:
        data = sample_npc.model_dump()
        restored = NPC.model_validate(data)
        assert restored == sample_npc

    def test_hp_zero_is_valid(self, sample_ability_scores: AbilityScores) -> None:
        """NPC can have 0 HP (unconscious/dead)."""
        npc = NPC(
            name="Fallen",
            race=Race.HUMAN,
            ability_scores=sample_ability_scores,
            hp=0,
            max_hp=10,
            ac=10,
        )
        assert npc.hp == 0


# ---------------------------------------------------------------------------
# Location
# ---------------------------------------------------------------------------


class TestLocation:
    """Location model tests."""

    def test_valid_construction(self, sample_location: Location) -> None:
        assert sample_location.name == "Neverwinter"
        assert len(sample_location.connections) == 2
        assert "Phandalin" in sample_location.connections

    def test_defaults(self) -> None:
        loc = Location(name="Empty Cave")
        assert loc.description == ""
        assert loc.connections == []
        assert loc.npcs_present == []
        assert loc.items_available == []

    def test_model_roundtrip(self, sample_location: Location) -> None:
        data = sample_location.model_dump()
        restored = Location.model_validate(data)
        assert restored == sample_location


# ---------------------------------------------------------------------------
# Campaign
# ---------------------------------------------------------------------------


class TestCampaign:
    """Campaign model tests."""

    def test_valid_construction(self, sample_campaign: Campaign) -> None:
        assert sample_campaign.name == "Lost Mines of Phandelver"
        assert sample_campaign.id == "test-campaign-001"

    def test_auto_generated_id(self) -> None:
        c1 = Campaign(name="A")
        c2 = Campaign(name="B")
        assert c1.id != c2.id
        assert len(c1.id) == 36  # UUID format

    def test_auto_generated_created_at(self) -> None:
        campaign = Campaign(name="Test")
        assert campaign.created_at is not None

    def test_defaults(self) -> None:
        campaign = Campaign(name="Test")
        assert campaign.player_names == []
        assert campaign.current_location is None
        assert campaign.interaction_count == 0

    def test_model_roundtrip(self, sample_campaign: Campaign) -> None:
        data = sample_campaign.model_dump()
        restored = Campaign.model_validate(data)
        assert restored.id == sample_campaign.id
        assert restored.name == sample_campaign.name


# ---------------------------------------------------------------------------
# StoryArc — append_beat_locked_facts (coherence gate, task 6)
# ---------------------------------------------------------------------------


def _make_beat(n: int) -> StoryBeat:
    return StoryBeat(
        beat_number=n, title="B", description="d", location_hint="l",
        encounter_type="social",
    )


class TestAppendBeatLockedFacts:
    def _arc(self) -> StoryArc:
        return StoryArc(
            campaign_id="c1", theme="t", premise="Une longue prémisse valide.",
            beats=[_make_beat(n) for n in range(1, 9)],
            villain_name="V", villain_motivation="m",
        )

    def test_explicit_facts_and_hint_are_locked(self) -> None:
        from world.story_arc import BeatEffects, append_beat_locked_facts
        arc = self._arc()
        effects = BeatEffects(
            locked_facts=["Le pont de pierre est effondré."],
            narrative_hint="La herse de la crypte est levée.",
        )
        append_beat_locked_facts(arc, effects, beat_number=3)
        ids = [f.id for f in arc.locked_facts]
        assert ids == ["beat:3:0", "beat:3:hint"]
        assert arc.locked_facts[1].text == "La herse de la crypte est levée."

    def test_append_is_idempotent(self) -> None:
        from world.story_arc import BeatEffects, append_beat_locked_facts
        arc = self._arc()
        effects = BeatEffects(narrative_hint="La herse est levée.")
        append_beat_locked_facts(arc, effects, beat_number=3)
        append_beat_locked_facts(arc, effects, beat_number=3)
        assert len(arc.locked_facts) == 1

    def test_empty_effects_add_nothing(self) -> None:
        from world.story_arc import BeatEffects, append_beat_locked_facts
        arc = self._arc()
        append_beat_locked_facts(arc, BeatEffects(), beat_number=3)
        assert arc.locked_facts == []
