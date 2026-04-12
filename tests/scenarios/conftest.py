"""Fixtures for scenario integration tests."""

from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from ai.client import OllamaClient
from db.database import Base
from engine.character import (
    Ability,
    AbilityScores,
    Character,
    CharacterClass,
    Race,
    Size,
)
from engine.combat import CombatSide, Combatant
from engine.inventory import (
    DamageType,
    EquipmentSlot,
    ItemType,
    Rarity,
    Weapon,
    WeaponCategory,
    add_item,
    create_inventory,
    equip_item,
)
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

from tests.scenarios.scenario_runner import ScenarioRunner

# ---------------------------------------------------------------------------
# Ollama mock fixture (re-exported here so tests/scenarios/* can use it
# without importing from tests/ai/conftest.py).
# ---------------------------------------------------------------------------

OLLAMA_BASE = "http://localhost:11434"
TAGS_URL = f"{OLLAMA_BASE}/api/tags"


@pytest.fixture()
def ollama_client(httpx_mock: HTTPXMock) -> OllamaClient:
    """Create an OllamaClient with the /api/tags health check mocked."""
    httpx_mock.add_response(url=TAGS_URL, json={"models": []})
    return OllamaClient()


@pytest.fixture()
def scenario_db_engine():
    """In-memory SQLite engine shared across all connections.

    Uses StaticPool so every connection sees the same in-memory DB.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Enable foreign keys for SQLite
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _set_fk(dbapi_conn, _):  # type: ignore[no-untyped-def]
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def scenario_db_factory(scenario_db_engine):
    """Session factory for scenario tests.

    Returns a factory that creates real DB sessions on the shared engine.
    """
    return sessionmaker(bind=scenario_db_engine)


@pytest.fixture()
def scenario(scenario_db_factory) -> ScenarioRunner:
    """A ready-to-use ScenarioRunner."""
    return ScenarioRunner(scenario_db_factory)


# ---------------------------------------------------------------------------
# Enemy helpers
# ---------------------------------------------------------------------------


def make_enemy(
    name: str = "Gobelin",
    hp: int = 10,
    ac: int = 12,
    weapon_damage: str = "1d6",
    weapon_name: str = "Cimeterre",
) -> Combatant:
    """Create a simple enemy combatant for scenario tests."""
    char = Character(
        name=name,
        race=Race.HUMAN,
        char_class=CharacterClass.FIGHTER,
        level=1,
        ability_scores=AbilityScores(STR=12, DEX=12, CON=10, INT=8, WIS=8, CHA=8),
        hp=hp,
        max_hp=hp,
        ac=ac,
        speed=30,
        proficiency_bonus=2,
        saving_throw_proficiencies=(Ability.STR, Ability.CON),
        hit_die="1d10",
        size=Size.MEDIUM,
    )
    inv = create_inventory()
    weapon = Weapon(
        name=weapon_name,
        item_type=ItemType.WEAPON,
        weight=3.0,
        rarity=Rarity.COMMON,
        value_gp=10,
        damage_dice=weapon_damage,
        damage_type=DamageType.SLASHING,
        weapon_category=WeaponCategory.MARTIAL_MELEE,
    )
    inv = add_item(inv, weapon)
    inv = equip_item(inv, weapon_name, EquipmentSlot.MAIN_HAND)
    return Combatant(
        name=name,
        side=CombatSide.ENEMY,
        character=char,
        inventory=inv,
    )


def make_weak_enemy(name: str = "Gobelin faible") -> Combatant:
    """Create a very weak enemy (1 HP) for quick combat tests."""
    return make_enemy(name=name, hp=1, ac=5, weapon_damage="1d4")


def make_strong_enemy(name: str = "Ogre") -> Combatant:
    """Create a tough enemy for longer combat tests."""
    return make_enemy(name=name, hp=50, ac=16, weapon_damage="2d8", weapon_name="Massue")


def make_boss_enemy(
    name: str,
    stat_block: NPCStatBlock,
    *,
    hp: int = 80,
    ac: int = 16,
) -> Combatant:
    """Build a boss-tier enemy with an explicit ``NPCStatBlock``.

    Used by the end-to-end scenarios (Mageta vs Vellus) to exercise
    the boss AI path (phase transitions, legendary actions, TRUCE
    validation, etc.). The returned combatant is wired the same way
    ``make_enemy`` wires minions, plus the ``stat_block`` attribute
    the boss brain dispatches on.
    """
    char = Character(
        name=name,
        race=Race.HUMAN,
        char_class=CharacterClass.FIGHTER,
        level=10,
        ability_scores=AbilityScores(STR=16, DEX=14, CON=16, INT=14, WIS=14, CHA=18),
        hp=hp,
        max_hp=hp,
        ac=ac,
        speed=30,
        proficiency_bonus=4,
        saving_throw_proficiencies=(Ability.STR, Ability.CON),
        hit_die="1d10",
        size=Size.MEDIUM,
    )
    inv = create_inventory()
    primary = Weapon(
        name=stat_block.attacks[0].name if stat_block.attacks else "Griffe",
        item_type=ItemType.WEAPON,
        weight=3.0,
        rarity=Rarity.RARE,
        value_gp=100,
        damage_dice=(
            stat_block.attacks[0].damage_dice if stat_block.attacks else "1d8"
        ),
        damage_type=(
            stat_block.attacks[0].damage_type if stat_block.attacks else DamageType.SLASHING
        ),
        weapon_category=WeaponCategory.MARTIAL_MELEE,
    )
    inv = add_item(inv, primary)
    inv = equip_item(inv, primary.name, EquipmentSlot.MAIN_HAND)
    return Combatant(
        name=name,
        side=CombatSide.ENEMY,
        character=char,
        inventory=inv,
        stat_block=stat_block,
        legendary_points_remaining=stat_block.legendary_points_per_round,
    )


@pytest.fixture()
def vellus_stat_block() -> NPCStatBlock:
    """Realistic boss stat block for Vellus le Mentisseur.

    Three-attack multiattack, one signature ability, three legendary
    actions, a 50% HP phase transition, an aggression_threshold around
    25 (so a CHA 18 PC with proficiency +2 and a d20 roll near 20 can
    barely succeed a TRUCE attempt on the first try), and non-mindless
    so TRUCE is open in phase 1.
    """
    return NPCStatBlock(
        tier=NPCTier.BOSS,
        archetype="desert_sorcerer",
        multiattack_count=3,
        attacks=[
            NPCAttack(
                name="Lame de sable",
                damage_dice="1d8+3",
                damage_type=DamageType.SLASHING,
                to_hit_bonus=6,
                range_type="melee",
            ),
        ],
        signature_abilities=[
            SignatureAbility(
                name="Chant du Silence Éternel",
                description="Un murmure qui dérègle la concentration.",
                usage="per_combat",
                uses_remaining=1,
                effects=[
                    SignatureAbilityEffect(
                        kind="debuff",
                        condition_name="CONCENTRATING",
                        target_scope="single",
                    ),
                ],
            ),
        ],
        legendary_actions=[
            LegendaryAction(
                name="Coup rapide",
                cost=1,
                description="Une lame de sable off-turn.",
                effects=[
                    SignatureAbilityEffect(
                        kind="damage",
                        dice="1d8+3",
                        damage_type=DamageType.SLASHING,
                        target_scope="single",
                    ),
                ],
            ),
            LegendaryAction(
                name="Glissement ombreux",
                cost=2,
                description="Se téléporte vers une zone adjacente.",
                effects=[
                    SignatureAbilityEffect(
                        kind="move",
                        target_scope="self",
                    ),
                ],
            ),
            LegendaryAction(
                name="Fracas éternel",
                cost=3,
                description="Une onde de sable qui secoue la zone.",
                effects=[
                    SignatureAbilityEffect(
                        kind="aoe_damage",
                        dice="2d6",
                        damage_type=DamageType.BLUDGEONING,
                        target_scope="zone",
                    ),
                ],
            ),
        ],
        legendary_points_per_round=3,
        phases=[
            PhaseTransition(
                trigger_hp_percent=50,
                narrative_cue=(
                    "Vellus s'effondre... puis se relève, les yeux blancs."
                ),
                unlock_signatures=["Rage du Désert"],
                attack_bonus=2,
                save_bonus=2,
            ),
        ],
        behavior_profile=BehaviorProfile.TACTICAL,
        aggression_threshold=25,
        mindless=False,
    )


def give_starter_weapon(runner: ScenarioRunner, player_idx: int = 0) -> None:
    """Give a player a basic sword and equip it."""
    session = runner.session
    if session is None:
        msg = "No active session"
        raise RuntimeError(msg)
    player = runner._make_player(player_idx)
    inv = session.inventories[player.id]
    sword = Weapon(
        name="Epee longue",
        item_type=ItemType.WEAPON,
        weight=3.0,
        rarity=Rarity.COMMON,
        value_gp=15,
        damage_dice="1d8",
        damage_type=DamageType.SLASHING,
        weapon_category=WeaponCategory.MARTIAL_MELEE,
    )
    inv = add_item(inv, sword)
    inv = equip_item(inv, "Epee longue", EquipmentSlot.MAIN_HAND)
    session.inventories[player.id] = inv
