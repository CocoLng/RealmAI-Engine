"""Tests for off-turn legendary action resolution.

Covers:
- ``engine.npc_ai.legendary.maybe_spend_legendary_action`` gating +
  picker heuristic (cost-1 eager, cost-2 when possible, cost-3 only
  when HP < 30%).
- ``engine.combat.advance_turn`` hooks: reset legendary points at the
  start of the boss's turn, fire off-turn legendary after every PC
  turn, append summaries to ``state.pending_legendary_summaries``.
"""

from __future__ import annotations

import pytest

from engine.character import (
    AbilityScores,
    CharacterClass,
    Race,
    apply_racial_bonuses,
    create_character,
)
from engine.combat import (
    CombatSide,
    CombatState,
    Combatant,
    advance_turn,
)
from engine.dice import DiceResult
from engine.inventory import (
    DamageType,
    EquipmentSlot,
    ITEM_CATALOG,
    add_item,
    create_inventory,
    equip_item,
)
from engine.npc_ai.legendary import (
    _pick_legendary,
    maybe_spend_legendary_action,
)
from engine.npc_stat_block import (
    BehaviorProfile,
    LegendaryAction,
    NPCAttack,
    NPCStatBlock,
    NPCTier,
    SignatureAbilityEffect,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _cost_1() -> LegendaryAction:
    return LegendaryAction(
        name="Quick Strike",
        cost=1,
        description="Quick off-turn attack.",
        effects=[
            SignatureAbilityEffect(
                kind="damage",
                dice="1d6+2",
                damage_type=DamageType.SLASHING,
                target_scope="single",
            ),
        ],
    )


def _cost_2() -> LegendaryAction:
    return LegendaryAction(
        name="Shadow Step",
        cost=2,
        description="Reposition and strike.",
        effects=[
            SignatureAbilityEffect(
                kind="damage",
                dice="2d6+2",
                damage_type=DamageType.SLASHING,
                target_scope="single",
            ),
        ],
    )


def _cost_3() -> LegendaryAction:
    return LegendaryAction(
        name="Dark Surge",
        cost=3,
        description="Devastating blast.",
        effects=[
            SignatureAbilityEffect(
                kind="damage",
                dice="4d8",
                damage_type=DamageType.NECROTIC,
                target_scope="single",
            ),
        ],
    )


def _make_boss(
    name: str = "Dread",
    hp: int = 80,
    max_hp: int = 80,
    legendary_points: int = 3,
    actions: list[LegendaryAction] | None = None,
    tier: NPCTier = NPCTier.BOSS,
) -> Combatant:
    scores = AbilityScores(STR=16, DEX=14, CON=16, INT=14, WIS=14, CHA=14)
    char = create_character(name, Race.HUMAN, CharacterClass.FIGHTER, scores)
    char.hp = hp
    char.max_hp = max_hp
    char.ac = 18
    inv = create_inventory()
    stat_block = NPCStatBlock(
        tier=tier,
        archetype="test_boss",
        multiattack_count=3,
        attacks=[
            NPCAttack(
                name="Greataxe",
                damage_dice="1d12+4",
                damage_type=DamageType.SLASHING,
                to_hit_bonus=7,
            ),
        ],
        legendary_actions=actions or [],
        legendary_points_per_round=legendary_points,
        behavior_profile=BehaviorProfile.AGGRESSIVE,
    )
    combatant = Combatant(
        name=name,
        side=CombatSide.ENEMY,
        character=char,
        inventory=inv,
        stat_block=stat_block,
    )
    combatant.legendary_points_remaining = legendary_points
    return combatant


def _make_pc(name: str, hp: int = 20) -> Combatant:
    scores = AbilityScores(STR=14, DEX=12, CON=14, INT=10, WIS=10, CHA=10)
    scores = apply_racial_bonuses(scores, Race.HUMAN)
    char = create_character(name, Race.HUMAN, CharacterClass.FIGHTER, scores)
    char.hp = hp
    char.max_hp = max(hp, char.max_hp)
    char.ac = 15
    inv = create_inventory()
    inv = add_item(inv, ITEM_CATALOG["Longsword"])
    inv = equip_item(inv, "Longsword", EquipmentSlot.MAIN_HAND)
    return Combatant(
        name=name,
        side=CombatSide.PLAYER,
        character=char,
        inventory=inv,
    )


def _state(combatants: list[Combatant]) -> CombatState:
    return CombatState(combatants=combatants, round_number=1, current_turn_index=0)


@pytest.fixture(autouse=True)
def _deterministic_roll(monkeypatch: pytest.MonkeyPatch) -> None:
    """Legendary effects go through the signature executor which rolls dice.
    Pin the roll to a deterministic value for every test."""
    monkeypatch.setattr(
        "engine.npc_ai.elite.roll",
        lambda expr: DiceResult(expression=expr, rolls=[5], total=5),
    )


# ---------------------------------------------------------------------------
# Gating
# ---------------------------------------------------------------------------


class TestMaybeSpendGating:
    def test_returns_none_when_no_points(self) -> None:
        boss = _make_boss(legendary_points=3, actions=[_cost_1()])
        boss.legendary_points_remaining = 0
        pc = _make_pc("Thorin")
        state = _state([boss, pc])

        result = maybe_spend_legendary_action(state, boss, pc)

        assert result is None

    def test_returns_none_for_non_boss(self) -> None:
        elite = _make_boss(
            tier=NPCTier.ELITE, actions=[_cost_1()],
        )
        pc = _make_pc("Thorin")
        state = _state([elite, pc])

        result = maybe_spend_legendary_action(state, elite, pc)

        assert result is None

    def test_returns_none_when_no_affordable_action(self) -> None:
        boss = _make_boss(actions=[_cost_3()])
        boss.legendary_points_remaining = 1  # can't afford cost-3
        boss.character.hp = 80  # high HP so cost-3 wouldn't be picked anyway
        pc = _make_pc("Thorin")
        state = _state([boss, pc])

        result = maybe_spend_legendary_action(state, boss, pc)

        assert result is None

    def test_returns_none_when_boss_dead(self) -> None:
        boss = _make_boss(actions=[_cost_1()])
        boss.is_alive = False
        pc = _make_pc("Thorin")
        state = _state([boss, pc])

        result = maybe_spend_legendary_action(state, boss, pc)

        assert result is None


# ---------------------------------------------------------------------------
# Picker heuristic
# ---------------------------------------------------------------------------


class TestPickLegendary:
    def test_cost_1_picked_eagerly(self) -> None:
        boss = _make_boss(hp=80, max_hp=80)
        chosen = _pick_legendary([_cost_1()], boss)
        assert chosen is not None
        assert chosen.cost == 1

    def test_cost_3_only_when_hp_critical(self) -> None:
        boss = _make_boss(hp=10, max_hp=100)  # 10% HP
        chosen = _pick_legendary([_cost_1(), _cost_2(), _cost_3()], boss)
        assert chosen is not None
        assert chosen.cost == 3

    def test_cost_2_preferred_over_cost_1_when_healthy(self) -> None:
        boss = _make_boss(hp=80, max_hp=80)
        chosen = _pick_legendary([_cost_1(), _cost_2()], boss)
        assert chosen is not None
        assert chosen.cost == 2

    def test_cost_3_not_picked_when_healthy(self) -> None:
        boss = _make_boss(hp=80, max_hp=80)
        chosen = _pick_legendary([_cost_1(), _cost_2(), _cost_3()], boss)
        assert chosen is not None
        # Healthy boss prefers cost-2 over cost-3
        assert chosen.cost == 2


# ---------------------------------------------------------------------------
# Spend + decrement + effects
# ---------------------------------------------------------------------------


class TestMaybeSpendEffect:
    def test_decrements_points_and_applies_effect(self) -> None:
        boss = _make_boss(legendary_points=3, actions=[_cost_1()])
        pc = _make_pc("Thorin", hp=20)
        state = _state([boss, pc])

        summary = maybe_spend_legendary_action(state, boss, pc)

        assert summary is not None
        assert "Quick Strike" in summary
        assert boss.legendary_points_remaining == 2
        assert pc.character.hp < 20  # damage applied

    def test_applies_effect_via_signature_pipeline(self) -> None:
        boss = _make_boss(legendary_points=3, actions=[_cost_1()])
        pc = _make_pc("Thorin", hp=20)
        state = _state([boss, pc])

        maybe_spend_legendary_action(state, boss, pc)

        # 5 damage rolled (see autouse mock), so 20 - 5 = 15 HP remaining.
        assert pc.character.hp == 15


# ---------------------------------------------------------------------------
# Player-facing summary (audit H14 — French, no internal tags)
# ---------------------------------------------------------------------------


class TestSummaryPlayerFacing:
    """Summaries are posted verbatim in the combat channel by the
    TurnManager cue flush — they must be clean French with no internal
    markers like ``[LEGENDARY]``."""

    def test_summary_is_french_without_internal_tag(self) -> None:
        boss = _make_boss(legendary_points=3, actions=[_cost_1()])
        pc = _make_pc("Thorin", hp=20)
        state = _state([boss, pc])

        summary = maybe_spend_legendary_action(state, boss, pc)

        assert summary == "Dread utilise Quick Strike : Thorin subit 5 dégâts"

    def test_summary_without_effects_falls_back_to_french(self) -> None:
        no_effect = LegendaryAction(name="Menace", cost=1, description="Rugit.")
        boss = _make_boss(legendary_points=3, actions=[no_effect])
        pc = _make_pc("Thorin", hp=20)
        state = _state([boss, pc])

        summary = maybe_spend_legendary_action(state, boss, pc)

        assert summary == "Dread utilise Menace : aucun effet"


# ---------------------------------------------------------------------------
# advance_turn integration
# ---------------------------------------------------------------------------


class TestAdvanceTurnLegendaryHooks:
    def test_legendary_points_reset_at_boss_turn_start(self) -> None:
        """When the round wraps back to the boss, its points reset."""
        boss = _make_boss(legendary_points=3)
        boss.legendary_points_remaining = 0  # spent everything
        pc = _make_pc("Thorin")
        state = _state([pc, boss])  # PC first
        state.current_turn_index = 0  # PC's turn

        advance_turn(state)  # advances to boss's turn

        assert state.current_turn_index == 1
        assert boss.legendary_points_remaining == 3

    def test_legendary_fires_after_pc_turn(self) -> None:
        """After a PC's turn, a boss on the other side spends a legendary action.

        Note: the boss's own turn starts immediately after (we advance to
        index 1), which refills its legendary_points_remaining per 5e RAW.
        The observable evidence that a legendary fired is the pending
        summary AND the PC having taken off-turn damage.
        """
        boss = _make_boss(legendary_points=3, actions=[_cost_1()])
        pc = _make_pc("Thorin", hp=20)
        state = _state([pc, boss])
        state.current_turn_index = 0  # PC's turn

        advance_turn(state)

        assert len(state.pending_legendary_summaries) == 1
        assert "Quick Strike" in state.pending_legendary_summaries[0]
        # PC took damage from the off-turn legendary action (5 damage mocked)
        assert pc.character.hp == 15

    def test_legendary_does_not_fire_after_npc_turn(self) -> None:
        """Bosses only react off-turn to PC turns, not to other NPC turns."""
        boss = _make_boss(legendary_points=3, actions=[_cost_1()])
        minion = _make_boss(  # another enemy NPC
            name="Minion",
            tier=NPCTier.MINION,
            legendary_points=0,
            actions=[],
        )
        pc = _make_pc("Thorin", hp=20)
        state = _state([minion, pc, boss])
        state.current_turn_index = 0  # minion's turn

        advance_turn(state)

        # No legendary fired because current was NPC, not PC
        assert state.pending_legendary_summaries == []

    def test_legendary_fires_only_on_opposing_boss(self) -> None:
        """A boss on the PC's side (ally) should not fire on its own team."""
        ally_boss = _make_boss(
            name="Friendly Boss",
            legendary_points=3,
            actions=[_cost_1()],
        )
        ally_boss.side = CombatSide.PLAYER  # ally of Thorin
        pc = _make_pc("Thorin", hp=20)
        state = _state([pc, ally_boss])
        state.current_turn_index = 0

        advance_turn(state)

        assert state.pending_legendary_summaries == []
