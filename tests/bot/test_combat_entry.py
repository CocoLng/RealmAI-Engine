"""Tests for bot/combat_entry.py — combat trigger detection + party-wide entry."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from ai.models import InterpretedAction
from bot.combat_entry import (
    CombatTrigger,
    CombatTriggerKind,
    InitiativeSide,
    detect_combat_trigger,
    enter_combat,
)
from engine.character import (
    AbilityScores,
    Character,
    CharacterClass,
    Race,
    apply_racial_bonuses,
    create_character,
)
from engine.combat import CombatSide
from engine.inventory import create_inventory
from engine.npc_stat_block import NPCStatBlock, NPCTier
from engine.validators import ActionType
from world.location import Location
from world.npc import NPC, NPCDisposition


# ---------------------------------------------------------------------------
# Lightweight session double
# ---------------------------------------------------------------------------


@dataclass
class _SessionStub:
    """Minimal structural stand-in for GameSession.

    Only the fields touched by detect_combat_trigger/enter_combat are
    included; the real GameSession dataclass carries many more.
    """

    characters: dict[int, Character] = field(default_factory=dict)
    inventories: dict = field(default_factory=dict)
    spellcasters: dict = field(default_factory=dict)
    npcs: dict[str, NPC] = field(default_factory=dict)
    current_location: Location | None = None
    combat_state: object | None = None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_commoner(name: str = "Jeanne") -> NPC:
    return NPC(
        name=name,
        race=Race.HUMAN,
        level=1,
        ability_scores=AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10),
        hp=4,
        max_hp=4,
        ac=10,
        disposition=NPCDisposition.FRIENDLY,
    )


def _make_strong_neutral_npc(name: str = "Mageta") -> NPC:
    return NPC(
        name=name,
        race=Race.HUMAN,
        level=5,
        ability_scores=AbilityScores(STR=14, DEX=12, CON=14, INT=12, WIS=12, CHA=14),
        hp=30,
        max_hp=30,
        ac=14,
        disposition=NPCDisposition.NEUTRAL,
    )


def _make_hostile_npc(name: str = "Bandit") -> NPC:
    return NPC(
        name=name,
        race=Race.HUMAN,
        level=3,
        ability_scores=AbilityScores(STR=14, DEX=12, CON=12, INT=10, WIS=10, CHA=10),
        hp=20,
        max_hp=20,
        ac=13,
        disposition=NPCDisposition.HOSTILE,
    )


def _make_boss_npc(name: str = "Dragon") -> NPC:
    """NPC whose combat-worthiness comes from its stat block rather than HP/AC."""
    return NPC(
        name=name,
        race=Race.HUMAN,
        level=1,
        ability_scores=AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10),
        hp=4,
        max_hp=4,
        ac=10,
        disposition=NPCDisposition.FRIENDLY,
        stat_block=NPCStatBlock(tier=NPCTier.BOSS, archetype="mini-boss"),
    )


def _make_fighter(name: str = "Arden") -> Character:
    scores = AbilityScores(STR=16, DEX=14, CON=14, INT=10, WIS=12, CHA=8)
    scores = apply_racial_bonuses(scores, Race.HUMAN)
    return create_character(name, Race.HUMAN, CharacterClass.FIGHTER, scores)


def _session_with_pc(
    *, pcs: list[Character], npcs: list[NPC] | None = None,
    location: Location | None = None,
) -> _SessionStub:
    session = _SessionStub()
    for i, pc in enumerate(pcs):
        uid = 1000 + i
        session.characters[uid] = pc
        session.inventories[uid] = create_inventory()
        session.spellcasters[uid] = None
    session.npcs = {npc.name: npc for npc in (npcs or [])}
    session.current_location = location
    return session


def _make_action(
    action_type: ActionType,
    actor_name: str = "Arden",
    target_name: str | None = None,
    raw_input: str = "test",
) -> InterpretedAction:
    return InterpretedAction(
        action_type=action_type,
        actor_name=actor_name,
        target_name=target_name,
        raw_input=raw_input,
    )


# ---------------------------------------------------------------------------
# detect_combat_trigger — ATTACK
# ---------------------------------------------------------------------------


def test_detect_attack_hostile_npc_returns_both_ready() -> None:
    """Face-off: hostile target → no surprise on either side."""
    npc = _make_hostile_npc()
    session = _session_with_pc(pcs=[_make_fighter()], npcs=[npc])
    action = _make_action(ActionType.ATTACK, target_name=npc.name)

    trigger = detect_combat_trigger(action, session)  # type: ignore[arg-type]

    assert trigger is not None
    assert trigger.kind == CombatTriggerKind.PLAYER_ATTACK
    assert trigger.surprise_side == InitiativeSide.BOTH_READY
    assert trigger.enemy_names == [npc.name]
    assert trigger.aggressor_name == "Arden"


def test_detect_attack_neutral_npc_returns_player_surprise() -> None:
    """Neutral target caught off guard → PCs win surprise."""
    npc = _make_strong_neutral_npc()
    session = _session_with_pc(pcs=[_make_fighter()], npcs=[npc])
    action = _make_action(ActionType.ATTACK, target_name=npc.name)

    trigger = detect_combat_trigger(action, session)  # type: ignore[arg-type]

    assert trigger is not None
    assert trigger.surprise_side == InitiativeSide.PLAYERS


def test_detect_attack_commoner_returns_none() -> None:
    """Weak friendly commoner → trivial_resolve path, not a full combat."""
    npc = _make_commoner()
    session = _session_with_pc(pcs=[_make_fighter()], npcs=[npc])
    action = _make_action(ActionType.ATTACK, target_name=npc.name)

    assert detect_combat_trigger(action, session) is None  # type: ignore[arg-type]


def test_detect_attack_stat_blocked_commoner_still_triggers() -> None:
    """Low HP/AC but carrying a stat block → still combat-worthy."""
    npc = _make_boss_npc()
    session = _session_with_pc(pcs=[_make_fighter()], npcs=[npc])
    action = _make_action(ActionType.ATTACK, target_name=npc.name)

    trigger = detect_combat_trigger(action, session)  # type: ignore[arg-type]
    assert trigger is not None


def test_detect_attack_unknown_target_returns_none() -> None:
    npc = _make_hostile_npc()
    session = _session_with_pc(pcs=[_make_fighter()], npcs=[npc])
    action = _make_action(ActionType.ATTACK, target_name="Ghost")

    assert detect_combat_trigger(action, session) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# detect_combat_trigger — IMPROVISE lethal intent (task 40 stub)
# ---------------------------------------------------------------------------


def test_detect_improvise_lethal_intent() -> None:
    npc = _make_strong_neutral_npc()
    session = _session_with_pc(pcs=[_make_fighter()], npcs=[npc])
    action = _make_action(
        ActionType.IMPROVISE, target_name=npc.name, raw_input="je dégaine mon épée",
    )
    # Simulate the task-40 flag via attribute injection — InterpretedAction
    # does not carry this field today, but getattr() tolerates it.
    object.__setattr__(action, "is_lethal_intent", True)

    trigger = detect_combat_trigger(action, session)  # type: ignore[arg-type]

    assert trigger is not None
    assert trigger.kind == CombatTriggerKind.LETHAL_INTENT
    assert trigger.surprise_side == InitiativeSide.PLAYERS


def test_detect_improvise_without_lethal_flag_returns_none() -> None:
    npc = _make_strong_neutral_npc()
    session = _session_with_pc(pcs=[_make_fighter()], npcs=[npc])
    action = _make_action(ActionType.IMPROVISE, target_name=npc.name)

    assert detect_combat_trigger(action, session) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# detect_combat_trigger — INTERACT ambush (task 41 stub)
# ---------------------------------------------------------------------------


class _TriggerDef:
    """Stand-in for the task-41 CombatTriggerDef attached to locations."""

    def __init__(self, spawn_npcs: list[str], reveal_narration: str = "") -> None:
        self.spawn_npcs = spawn_npcs
        self.reveal_narration = reveal_narration


def test_detect_interact_on_trap_trigger() -> None:
    npc = _make_hostile_npc("Skeleton")
    location = Location(name="Crypt")
    # ``combat_triggers`` is an optional first-class field on some rows;
    # simulate it here via attribute injection so the detector's hasattr()
    # guard fires.
    object.__setattr__(location, "combat_triggers", {"sarcophagus": _TriggerDef(["Skeleton"], "De la poussière tombe du plafond.")})
    session = _session_with_pc(pcs=[_make_fighter()], npcs=[npc], location=location)
    action = _make_action(ActionType.INTERACT, target_name="sarcophagus")

    trigger = detect_combat_trigger(action, session)  # type: ignore[arg-type]

    assert trigger is not None
    assert trigger.kind == CombatTriggerKind.AMBUSH
    assert trigger.surprise_side == InitiativeSide.NPCS
    assert trigger.enemy_names == ["Skeleton"]
    assert "poussière" in trigger.narrative_hint


def test_detect_interact_without_trigger_returns_none() -> None:
    location = Location(name="Tavern")
    session = _session_with_pc(pcs=[_make_fighter()], location=location)
    action = _make_action(ActionType.INTERACT, target_name="chair")

    assert detect_combat_trigger(action, session) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# detect_combat_trigger — neutral actions
# ---------------------------------------------------------------------------


def test_detect_look_returns_none() -> None:
    session = _session_with_pc(pcs=[_make_fighter()])
    action = _make_action(ActionType.LOOK)
    assert detect_combat_trigger(action, session) is None  # type: ignore[arg-type]


def test_detect_talk_returns_none_for_now() -> None:
    """Provocation path (case 3) is owned by task 81."""
    npc = _make_strong_neutral_npc()
    session = _session_with_pc(pcs=[_make_fighter()], npcs=[npc])
    action = _make_action(ActionType.TALK, target_name=npc.name)
    assert detect_combat_trigger(action, session) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# enter_combat
# ---------------------------------------------------------------------------


def test_enter_combat_builds_party_wide_state() -> None:
    fighter = _make_fighter("Arden")
    fighter2 = _make_fighter("Bren")
    enemy = _make_hostile_npc("Bandit")
    session = _session_with_pc(pcs=[fighter, fighter2], npcs=[enemy])
    trigger = CombatTrigger(
        kind=CombatTriggerKind.PLAYER_ATTACK,
        aggressor_name="Arden",
        enemy_names=["Bandit"],
        surprise_side=InitiativeSide.BOTH_READY,
    )

    state = enter_combat(session, trigger)  # type: ignore[arg-type]

    assert len(state.combatants) == 3
    pc_names = {c.name for c in state.combatants if c.side == CombatSide.PLAYER}
    enemy_names = {c.name for c in state.combatants if c.side == CombatSide.ENEMY}
    assert pc_names == {"Arden", "Bren"}
    assert enemy_names == {"Bandit"}
    assert state.is_active is True
    assert state.round_number == 1


def test_enter_combat_raises_when_no_enemies_found() -> None:
    fighter = _make_fighter()
    session = _session_with_pc(pcs=[fighter])
    trigger = CombatTrigger(
        kind=CombatTriggerKind.PLAYER_ATTACK,
        aggressor_name="Arden",
        enemy_names=["Ghost"],
        surprise_side=InitiativeSide.BOTH_READY,
    )

    with pytest.raises(ValueError):
        enter_combat(session, trigger)  # type: ignore[arg-type]


def test_enter_combat_persists_on_session() -> None:
    fighter = _make_fighter()
    enemy = _make_hostile_npc()
    session = _session_with_pc(pcs=[fighter], npcs=[enemy])
    trigger = CombatTrigger(
        kind=CombatTriggerKind.PLAYER_ATTACK,
        aggressor_name="Arden",
        enemy_names=["Bandit"],
        surprise_side=InitiativeSide.BOTH_READY,
    )

    state = enter_combat(session, trigger)  # type: ignore[arg-type]
    assert session.combat_state is state
