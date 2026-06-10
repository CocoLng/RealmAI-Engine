"""Tests for the combat-bootstrap path of ``interpret.validate`` (audit H18).

Before this chantier, combat was bootstrapped DURING validation:
``enter_combat`` + ``start_combat`` + surprise + zones ran before the
action was validated, and the combat persisted even when the triggering
attack was refused. Worse, ``_assign_initial_zones`` put PCs in zones[0]
and enemies in zones[-1], so any melee attack in a multi-zone location
was guaranteed an "hors de portée" refusal — banner shown, surprise
round burned, unwanted combat stuck on the session.

Pinned behaviour:

1. The triggering melee attacker is charged into the target's zone when
   their weapon cannot reach (the attack IS the closing of distance).
2. A structurally refused trigger action (e.g. weapon not equipped)
   rolls the bootstrap back — no combat committed, no start embed.
3. Ranged attackers stay in their starting zone (no teleport-charge).
4. When the enemy legitimately wins initiative (BOTH_READY face-off),
   combat still starts — the refusal is only "not your turn".
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ai.models import InterpretedAction
from bot.pipeline.interpret import InterpretSideChannel, validate
from engine.character import (
    AbilityScores,
    CharacterClass,
    Race,
    create_character,
)
from engine.dice import DiceResult
from engine.inventory import EquipmentSlot, ITEM_CATALOG, add_item, create_inventory, equip_item
from engine.npc_library import get_archetype
from engine.validators import ActionType
from world.campaign import Campaign
from world.combat_zone import Zone
from world.location import Location
from world.npc import NPC, NPCDisposition


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _pc_with_weapon(weapon_name: str = "Longsword"):
    char = create_character(
        "Arden",
        Race.HUMAN,
        CharacterClass.FIGHTER,
        AbilityScores(STR=16, DEX=14, CON=14, INT=10, WIS=12, CHA=8),
    )
    inv = create_inventory()
    inv = add_item(inv, ITEM_CATALOG[weapon_name])
    inv = equip_item(inv, weapon_name, EquipmentSlot.MAIN_HAND)
    return char, inv


def _guard_npc(disposition: NPCDisposition = NPCDisposition.NEUTRAL) -> NPC:
    return NPC(
        name="Garde",
        race=Race.HUMAN,
        ability_scores=AbilityScores(
            STR=12, DEX=10, CON=12, INT=10, WIS=10, CHA=10,
        ),
        hp=25,
        max_hp=25,
        ac=14,
        disposition=disposition,
        location_name="Pont",
        stat_block=get_archetype("guard"),
    )


def _two_zone_location() -> Location:
    return Location(
        name="Pont",
        description="Un vieux pont de pierre.",
        npcs_present=["Garde"],
        combat_zones=[
            Zone(name="Rive", adjacent_zone_names=["Tablier"]),
            Zone(name="Tablier", adjacent_zone_names=["Rive"]),
        ],
    )


def _session(npc: NPC, location: Location, char, inv) -> SimpleNamespace:
    return SimpleNamespace(
        campaign=Campaign(name="Test H18"),
        characters={1: char},
        inventories={1: inv},
        spellcasters={},
        npcs={npc.name: npc},
        current_location=location,
        combat_state=None,
        story_arc=None,
    )


def _attack_action(weapon_name: str = "Longsword") -> InterpretedAction:
    return InterpretedAction(
        action_type=ActionType.ATTACK,
        actor_name="Arden",
        target_name="Garde",
        weapon_name=weapon_name,
        raw_input="j'attaque le garde",
    )


def _validate(session: SimpleNamespace, action: InterpretedAction):
    side = InterpretSideChannel()
    result = validate(
        action=action,
        actor_name="Arden",
        location=session.current_location,
        npcs=session.npcs,
        combat_state=session.combat_state,
        inventory=session.inventories.get(1),
        session=session,  # type: ignore[arg-type]
        campaign_id="test-h18",
        db_factory=None,
        side=side,
    )
    return result, side


def _combatant(state, name: str):
    return next(c for c in state.combatants if c.name == name)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMeleeChargeOnBootstrap:
    def test_melee_attack_in_multizone_location_is_valid_via_charge(self) -> None:
        char, inv = _pc_with_weapon("Longsword")
        npc = _guard_npc()
        session = _session(npc, _two_zone_location(), char, inv)

        result, side = _validate(session, _attack_action())

        assert result.is_valid, result.error_message
        state = session.combat_state
        assert state is not None and state.is_active
        attacker = _combatant(state, "Arden")
        target = _combatant(state, "Garde")
        assert attacker.current_zone == target.current_zone
        assert side.pending_combat_start_embed is not None

    def test_ranged_attacker_stays_in_starting_zone(self) -> None:
        char, inv = _pc_with_weapon("Shortbow")
        npc = _guard_npc()
        session = _session(npc, _two_zone_location(), char, inv)

        result, _side = _validate(session, _attack_action("Shortbow"))

        assert result.is_valid, result.error_message
        state = session.combat_state
        attacker = _combatant(state, "Arden")
        target = _combatant(state, "Garde")
        assert attacker.current_zone == "Rive"
        assert target.current_zone == "Tablier"
        assert attacker.current_zone != target.current_zone


class TestRefusedBootstrapRollback:
    def test_refused_attack_does_not_commit_combat(self) -> None:
        # Weapon referenced by the action is not equipped → validate_attack
        # refuses → the bootstrapped combat must be rolled back entirely.
        char, inv = _pc_with_weapon("Longsword")
        npc = _guard_npc()
        session = _session(npc, _two_zone_location(), char, inv)

        result, side = _validate(session, _attack_action("Épée fantôme"))

        assert not result.is_valid
        assert session.combat_state is None
        assert side.pending_combat_start_embed is None

    def test_surprise_not_burned_on_refused_bootstrap(self) -> None:
        # After a rollback, retrying with the right weapon must still get
        # the surprise round (nothing was consumed by the failed attempt).
        char, inv = _pc_with_weapon("Longsword")
        npc = _guard_npc()
        session = _session(npc, _two_zone_location(), char, inv)

        _validate(session, _attack_action("Épée fantôme"))
        result, _side = _validate(session, _attack_action("Longsword"))

        assert result.is_valid, result.error_message
        state = session.combat_state
        assert state is not None and state.is_active
        # PLAYERS-surprise trigger → aggressor acts first.
        assert state.combatants[state.current_turn_index].name == "Arden"


class TestAmbushTriggerConsumption:
    """Bonus: a fired combat trigger is marked consumed at commit time."""

    def _ambush_setup(self):
        from world.combat_trigger_def import CombatTriggerDef

        char, inv = _pc_with_weapon("Longsword")
        npc = NPC(
            name="Squelette",
            race=Race.HUMAN,
            ability_scores=AbilityScores(
                STR=10, DEX=14, CON=15, INT=6, WIS=8, CHA=5,
            ),
            hp=13,
            max_hp=13,
            ac=13,
            disposition=NPCDisposition.HOSTILE,
            location_name="Crypte",
            stat_block=get_archetype("guard"),
        )
        location = Location(
            name="Crypte",
            npcs_present=["Squelette"],
            combat_triggers={
                "sarcophage": CombatTriggerDef(
                    item_name="sarcophage",
                    spawn_npcs=["Squelette"],
                    reveal_narration="Le couvercle glisse…",
                ),
            },
        )
        session = _session(npc, location, char, inv)
        action = InterpretedAction(
            action_type=ActionType.INTERACT,
            actor_name="Arden",
            target_name="sarcophage",
            raw_input="j'ouvre le sarcophage",
        )
        return session, location, action

    def test_ambush_commit_marks_trigger_consumed(self) -> None:
        session, location, action = self._ambush_setup()

        _result, side = _validate(session, action)

        assert session.combat_state is not None
        assert session.combat_state.is_active
        assert side.pending_combat_start_embed is not None
        assert location.combat_triggers["sarcophage"].consumed is True

    def test_second_interact_does_not_retrigger(self) -> None:
        session, location, action = self._ambush_setup()
        _validate(session, action)
        # The first ambush eventually ends…
        session.combat_state.is_active = False

        result, side = _validate(session, action)

        assert result.is_valid  # plain exploration INTERACT now
        assert side.pending_combat_start_embed is None
        assert session.combat_state.is_active is False  # no new combat


class TestEnemyWinsInitiative:
    def test_face_off_commits_combat_even_when_not_actors_turn(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # HOSTILE target → BOTH_READY → straight initiative. Force the
        # enemy to win: combat must START (the face-off is real), the
        # attack is merely deferred to the PC's turn.
        rolls = iter([1, 20])

        def _initiative_roll(_expr: str) -> DiceResult:
            total = next(rolls)
            return DiceResult(expression="1d20", rolls=[total], total=total)

        monkeypatch.setattr("engine.combat.roll", _initiative_roll)

        char, inv = _pc_with_weapon("Longsword")
        npc = _guard_npc(NPCDisposition.HOSTILE)
        session = _session(npc, _two_zone_location(), char, inv)

        result, side = _validate(session, _attack_action())

        assert not result.is_valid  # "pas ton tour" — l'ennemi a gagné l'initiative
        state = session.combat_state
        assert state is not None and state.is_active
        assert state.combatants[state.current_turn_index].name == "Garde"
        assert side.pending_combat_start_embed is not None
