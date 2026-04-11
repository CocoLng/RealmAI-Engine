"""Tests for bot/embeds/combat_embed.py (task 62 refactor).

Covers flat layout, zone grouping, condition rendering, boss legendary
points field, skipping of dead and fled combatants, and active-turn
marker. The builder is backward-compatible with the flat layout used
when no :class:`~world.location.Location` is supplied.
"""

from __future__ import annotations

import discord

from bot.embeds.combat_embed import build_combat_embed
from engine.character import (
    AbilityScores,
    CharacterClass,
    Race,
    create_character,
)
from engine.combat import CombatSide, CombatState, Combatant
from engine.conditions import ActiveCondition, ConditionType
from engine.inventory import DamageType, create_inventory
from engine.npc_stat_block import (
    NPCAttack,
    NPCStatBlock,
    NPCTier,
)
from world.combat_zone import Zone
from world.location import Location


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _pc(name: str = "Aragorn", *, hp: int = 60, max_hp: int = 80) -> Combatant:
    char = create_character(
        name=name,
        race=Race.HUMAN,
        char_class=CharacterClass.FIGHTER,
        ability_scores=AbilityScores(
            STR=16, DEX=14, CON=14, INT=10, WIS=12, CHA=10,
        ),
    )
    char.hp = hp
    char.max_hp = max_hp
    return Combatant(
        name=name,
        side=CombatSide.PLAYER,
        character=char,
        inventory=create_inventory(),
        initiative=15,
    )


def _make_attack(name: str = "Griffe") -> NPCAttack:
    return NPCAttack(
        name=name,
        to_hit_bonus=4,
        damage_dice="1d6+2",
        damage_type=DamageType.SLASHING,
        range_type="melee",
    )


def _minion_statblock() -> NPCStatBlock:
    return NPCStatBlock(
        tier=NPCTier.MINION,
        archetype="goblin",
        attacks=[_make_attack()],
    )


def _boss_statblock() -> NPCStatBlock:
    return NPCStatBlock(
        tier=NPCTier.BOSS,
        archetype="dragon",
        attacks=[_make_attack(name="Morsure")],
        legendary_points_per_round=3,
    )


def _enemy(
    name: str,
    *,
    tier: NPCTier = NPCTier.MINION,
    hp: int = 12,
    max_hp: int = 12,
    legendary: int = 0,
) -> Combatant:
    char = create_character(
        name=name,
        race=Race.HUMAN,
        char_class=CharacterClass.FIGHTER,
        ability_scores=AbilityScores(
            STR=10, DEX=14, CON=10, INT=10, WIS=10, CHA=10,
        ),
    )
    char.hp = hp
    char.max_hp = max_hp
    stat_block = _boss_statblock() if tier == NPCTier.BOSS else _minion_statblock()
    return Combatant(
        name=name,
        side=CombatSide.ENEMY,
        character=char,
        inventory=create_inventory(),
        initiative=10,
        stat_block=stat_block,
        legendary_points_remaining=legendary,
    )


def _state(combatants: list[Combatant], *, round_number: int = 2, idx: int = 0) -> CombatState:
    return CombatState(
        combatants=combatants, round_number=round_number, current_turn_index=idx,
    )


def _location_with_zones() -> Location:
    zones = [
        Zone(name="Parvis", adjacent_zone_names=["Nef"]),
        Zone(name="Nef", adjacent_zone_names=["Parvis"]),
    ]
    return Location(name="Cathédrale", combat_zones=zones)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFlatLayout:
    def test_title_contains_round(self) -> None:
        embed = build_combat_embed(_state([_pc(), _enemy("Gobelin")]))
        assert embed.title == "Combat — Round 2"

    def test_color_is_red(self) -> None:
        embed = build_combat_embed(_state([_pc()]))
        assert embed.color == discord.Color(0xCC0000)

    def test_flat_field_lists_all_visible_combatants(self) -> None:
        embed = build_combat_embed(_state([_pc("Aragorn"), _enemy("Gobelin")]))
        field = next(f for f in embed.fields if f.name == "Combattants")
        assert "Aragorn" in field.value
        assert "Gobelin" in field.value

    def test_active_combatant_marker(self) -> None:
        embed = build_combat_embed(
            _state([_pc("Aragorn"), _enemy("Gobelin")], idx=0),
        )
        field = next(f for f in embed.fields if f.name == "Combattants")
        aragorn_line = next(
            line for line in field.value.split("\n") if "Aragorn" in line
        )
        assert aragorn_line.startswith("➡️")

    def test_footer_includes_active_name(self) -> None:
        embed = build_combat_embed(_state([_pc("Aragorn")]))
        assert embed.footer.text == "Tour de : Aragorn"


class TestConditionsDisplay:
    def test_condition_rendered_in_french_with_duration(self) -> None:
        enemy = _enemy("Gobelin")
        enemy.conditions.append(
            ActiveCondition(
                condition_type=ConditionType.POISONED,
                source="poison arrow",
                duration_rounds=3,
            ),
        )
        embed = build_combat_embed(_state([_pc("Aragorn"), enemy]))
        field = next(f for f in embed.fields if f.name == "Combattants")
        assert "Empoisonné" in field.value
        assert "(3r)" in field.value


class TestDeadAndFled:
    def test_dead_combatant_is_hidden(self) -> None:
        pc = _pc("Aragorn")
        dead = _enemy("Gobelin")
        dead.is_alive = False
        embed = build_combat_embed(_state([pc, dead]))
        field = next(f for f in embed.fields if f.name == "Combattants")
        assert "Gobelin" not in field.value
        assert "Aragorn" in field.value

    def test_fled_combatant_is_hidden(self) -> None:
        pc = _pc("Aragorn")
        runner = _enemy("Gobelin")
        runner.fled = True
        embed = build_combat_embed(_state([pc, runner]))
        field = next(f for f in embed.fields if f.name == "Combattants")
        assert "Gobelin" not in field.value


class TestZoneLayout:
    def test_zones_produce_one_field_each(self) -> None:
        pc = _pc("Aragorn")
        pc.current_zone = "Parvis"
        gob = _enemy("Gobelin")
        gob.current_zone = "Nef"
        location = _location_with_zones()
        embed = build_combat_embed(_state([pc, gob]), location=location)
        field_names = [f.name for f in embed.fields]
        assert any("Parvis" in n for n in field_names)
        assert any("Nef" in n for n in field_names)

    def test_combatants_placed_in_their_zone(self) -> None:
        pc = _pc("Aragorn")
        pc.current_zone = "Parvis"
        gob = _enemy("Gobelin")
        gob.current_zone = "Nef"
        location = _location_with_zones()
        embed = build_combat_embed(_state([pc, gob]), location=location)
        parvis = next(f for f in embed.fields if "Parvis" in f.name)
        nef = next(f for f in embed.fields if "Nef" in f.name)
        assert "Aragorn" in parvis.value
        assert "Gobelin" not in parvis.value
        assert "Gobelin" in nef.value

    def test_empty_zone_shows_placeholder(self) -> None:
        pc = _pc("Aragorn")
        pc.current_zone = "Parvis"
        location = _location_with_zones()
        embed = build_combat_embed(_state([pc]), location=location)
        nef = next(f for f in embed.fields if "Nef" in f.name)
        assert "vide" in nef.value.lower()

    def test_unzoned_combatants_get_fallback_field(self) -> None:
        pc = _pc("Aragorn")
        pc.current_zone = None  # Defensive — shouldn't normally happen
        location = _location_with_zones()
        embed = build_combat_embed(_state([pc]), location=location)
        assert any(f.name == "Hors zone" for f in embed.fields)


class TestBossField:
    def test_boss_field_shows_legendary_points(self) -> None:
        pc = _pc("Aragorn")
        dragon = _enemy("Dragon", tier=NPCTier.BOSS, hp=200, max_hp=200, legendary=3)
        embed = build_combat_embed(_state([pc, dragon]))
        boss_fields = [f for f in embed.fields if "Dragon" in f.name]
        assert len(boss_fields) == 1
        assert "3" in boss_fields[0].value
        assert "légendaire" in boss_fields[0].value.lower()

    def test_no_boss_field_when_no_boss_present(self) -> None:
        embed = build_combat_embed(_state([_pc("Aragorn"), _enemy("Gobelin")]))
        # No field name should match the boss pattern.
        for f in embed.fields:
            assert "Dragon" not in f.name
            assert "Boss" not in f.name
