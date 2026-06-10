"""Tests for ai/scene_context.py — SceneContext model and builder."""

from __future__ import annotations

import pytest

from ai.scene_context import SceneContext, build_scene_context
from engine.character import AbilityScores, CharacterClass, Race
from engine.combat import CombatSide, CombatState, Combatant
from engine.inventory import Inventory
from world.location import Location
from world.npc import NPC


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def cathedral() -> Location:
    return Location(
        name="Place de la Cathédrale",
        description="Une vaste place pavée devant la cathédrale de Saint-Éloi.",
        connections=["Intérieur de la cathédrale", "Ruelle nord"],
        npcs_present=["Père Aldric", "Frère Corin"],
        items_available=["Autel de pierre", "Statue de saint"],
    )


@pytest.fixture()
def scores() -> AbilityScores:
    return AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10)


@pytest.fixture()
def aldric(scores: AbilityScores) -> NPC:
    return NPC(
        name="Père Aldric",
        race=Race.HUMAN,
        char_class=CharacterClass.CLERIC,
        ability_scores=scores,
        hp=15,
        max_hp=15,
        ac=12,
        description="Un vieil homme en prière près de l'autel.",
        location_name="Place de la Cathédrale",
    )


@pytest.fixture()
def corin(scores: AbilityScores) -> NPC:
    return NPC(
        name="Frère Corin",
        race=Race.HUMAN,
        char_class=CharacterClass.CLERIC,
        ability_scores=scores,
        hp=10,
        max_hp=10,
        ac=10,
        description="Un jeune novice qui range les cierges.",
        location_name="Place de la Cathédrale",
    )


@pytest.fixture()
def distant_npc(scores: AbilityScores) -> NPC:
    """An NPC in a different location — should be filtered out."""
    return NPC(
        name="Maître forgeron",
        race=Race.DWARF,
        ability_scores=scores,
        hp=20,
        max_hp=20,
        ac=14,
        description="Un nain costaud qui martèle une enclume.",
        location_name="Forge du village",
    )


# ---------------------------------------------------------------------------
# SceneContext model
# ---------------------------------------------------------------------------


class TestSceneContextModel:
    def test_minimal_construction(self) -> None:
        ctx = SceneContext(
            location_name="Test",
            location_description="desc",
        )
        assert ctx.location_name == "Test"
        assert ctx.location_description == "desc"
        assert ctx.visible_npcs == []
        assert ctx.visible_exits == []
        assert ctx.visible_objects == []
        assert ctx.in_combat is False
        assert ctx.combat_summary is None
        assert ctx.enemies_visible == []


# ---------------------------------------------------------------------------
# build_scene_context — exploration mode
# ---------------------------------------------------------------------------


class TestBuildSceneContextExploration:
    def test_with_location_and_npcs(
        self,
        cathedral: Location,
        aldric: NPC,
        corin: NPC,
    ) -> None:
        ctx = build_scene_context(
            location=cathedral,
            npcs={aldric.name: aldric, corin.name: corin},
        )
        assert ctx.location_name == "Place de la Cathédrale"
        assert "vaste place pavée" in ctx.location_description
        assert set(ctx.visible_npcs) == {"Père Aldric", "Frère Corin"}
        assert ctx.visible_exits == ["Intérieur de la cathédrale", "Ruelle nord"]
        assert ctx.visible_objects == ["Autel de pierre", "Statue de saint"]
        assert ctx.in_combat is False

    def test_filters_npcs_by_location(
        self,
        cathedral: Location,
        aldric: NPC,
        distant_npc: NPC,
    ) -> None:
        """NPCs in other locations are not visible in the current scene."""
        ctx = build_scene_context(
            location=cathedral,
            npcs={aldric.name: aldric, distant_npc.name: distant_npc},
        )
        assert ctx.visible_npcs == ["Père Aldric"]
        assert distant_npc.name not in ctx.visible_npcs

    def test_filters_dead_npcs(
        self,
        cathedral: Location,
        aldric: NPC,
        corin: NPC,
    ) -> None:
        """Dead NPCs must not reappear in the scene (audit H15)."""
        corin.kill()
        ctx = build_scene_context(
            location=cathedral,
            npcs={aldric.name: aldric, corin.name: corin},
        )
        assert ctx.visible_npcs == ["Père Aldric"]

    def test_none_location_returns_empty_scene(self) -> None:
        ctx = build_scene_context(location=None, npcs={})
        assert ctx.location_name == ""
        assert ctx.location_description == ""
        assert ctx.visible_npcs == []
        assert ctx.visible_exits == []
        assert ctx.visible_objects == []

    def test_includes_npc_when_location_name_is_none(
        self,
        cathedral: Location,
        scores: AbilityScores,
    ) -> None:
        """NPCs with no location_name are considered ambient / ignored."""
        ambient = NPC(
            name="Ombre",
            race=Race.HUMAN,
            ability_scores=scores,
            hp=1,
            max_hp=1,
            ac=10,
            location_name=None,
        )
        ctx = build_scene_context(
            location=cathedral,
            npcs={ambient.name: ambient},
        )
        assert ctx.visible_npcs == []


# ---------------------------------------------------------------------------
# build_scene_context — combat mode
# ---------------------------------------------------------------------------


class TestBuildSceneContextCombat:
    def test_combat_populates_enemies_and_summary(
        self,
        cathedral: Location,
        scores: AbilityScores,
    ) -> None:
        from engine.character import create_character

        pc = create_character("Arden", Race.HUMAN, CharacterClass.FIGHTER, scores)
        enemy_char = create_character(
            "Goblin", Race.HALFLING, CharacterClass.ROGUE, scores,
        )
        pc_combatant = Combatant(
            name="Arden",
            side=CombatSide.PLAYER,
            character=pc,
            inventory=Inventory(),
        )
        enemy_combatant = Combatant(
            name="Goblin",
            side=CombatSide.ENEMY,
            character=enemy_char,
            inventory=Inventory(),
        )
        combat = CombatState(
            combatants=[pc_combatant, enemy_combatant],
            round_number=2,
            current_turn_index=0,
        )
        ctx = build_scene_context(
            location=cathedral,
            npcs={},
            combat_state=combat,
        )
        assert ctx.in_combat is True
        assert ctx.enemies_visible == ["Goblin"]
        assert ctx.combat_summary is not None
        assert "round 2" in ctx.combat_summary.lower()

    def test_no_combat_state_means_exploration(
        self,
        cathedral: Location,
    ) -> None:
        ctx = build_scene_context(
            location=cathedral,
            npcs={},
            combat_state=None,
        )
        assert ctx.in_combat is False
        assert ctx.enemies_visible == []
        assert ctx.combat_summary is None
