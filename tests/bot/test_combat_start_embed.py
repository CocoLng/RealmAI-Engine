"""Tests for bot/embeds/combat_start_embed.py (task 61)."""

from __future__ import annotations

import discord

from bot.embeds.combat_start_embed import build_combat_start_embed
from engine.character import (
    AbilityScores,
    CharacterClass,
    Race,
    create_character,
)
from engine.combat import CombatSide, CombatState, Combatant
from engine.combat_trigger import (
    CombatTrigger,
    CombatTriggerKind,
    InitiativeSide,
)
from engine.conditions import ActiveCondition, ConditionType
from engine.inventory import create_inventory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pc(name: str = "Aragorn", initiative: int = 18) -> Combatant:
    char = create_character(
        name=name,
        race=Race.HUMAN,
        char_class=CharacterClass.FIGHTER,
        ability_scores=AbilityScores(
            STR=16, DEX=14, CON=14, INT=10, WIS=12, CHA=10,
        ),
    )
    return Combatant(
        name=name,
        side=CombatSide.PLAYER,
        character=char,
        inventory=create_inventory(),
        initiative=initiative,
    )


def _npc(name: str = "Gobelin", initiative: int = 10, *, surprised: bool = False) -> Combatant:
    char = create_character(
        name=name,
        race=Race.HUMAN,
        char_class=CharacterClass.FIGHTER,
        ability_scores=AbilityScores(
            STR=10, DEX=14, CON=10, INT=8, WIS=8, CHA=8,
        ),
    )
    c = Combatant(
        name=name,
        side=CombatSide.ENEMY,
        character=char,
        inventory=create_inventory(),
        initiative=initiative,
    )
    if surprised:
        c.conditions.append(
            ActiveCondition(
                condition_type=ConditionType.SURPRISED, source="combat_entry",
            ),
        )
    return c


def _state(combatants: list[Combatant], *, idx: int = 0) -> CombatState:
    return CombatState(
        combatants=combatants, round_number=1, current_turn_index=idx,
    )


def _trigger(
    *,
    kind: CombatTriggerKind = CombatTriggerKind.PLAYER_ATTACK,
    surprise: InitiativeSide = InitiativeSide.BOTH_READY,
    aggressor: str = "Aragorn",
    enemies: list[str] | None = None,
    narrative_hint: str = "",
) -> CombatTrigger:
    return CombatTrigger(
        kind=kind,
        aggressor_name=aggressor,
        enemy_names=enemies or ["Gobelin"],
        surprise_side=surprise,
        narrative_hint=narrative_hint,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCombatStartEmbed:
    def test_title_and_color(self) -> None:
        embed = build_combat_start_embed(
            _state([_pc(), _npc()]), _trigger(),
        )
        assert embed.title == "⚔️ Combat commence"
        assert embed.color == discord.Color(0xCC0000)

    def test_description_uses_narrative_hint(self) -> None:
        embed = build_combat_start_embed(
            _state([_pc(), _npc()]),
            _trigger(narrative_hint="Les ruines crépitent d'embuscade."),
        )
        assert embed.description is not None
        assert "ruines crépitent" in embed.description

    def test_fallback_description_when_no_hint(self) -> None:
        embed = build_combat_start_embed(
            _state([_pc(), _npc()]), _trigger(),
        )
        assert embed.description is not None
        assert "armes" in embed.description.lower()

    def test_initiative_order_lists_combatants(self) -> None:
        embed = build_combat_start_embed(
            _state([_pc("Aragorn", 18), _npc("Gobelin", 10)]),
            _trigger(),
        )
        init_field = next(f for f in embed.fields if "initiative" in f.name.lower())
        assert "Aragorn" in init_field.value
        assert "Gobelin" in init_field.value
        assert "18" in init_field.value
        assert "10" in init_field.value

    def test_active_combatant_has_arrow_marker(self) -> None:
        embed = build_combat_start_embed(
            _state([_pc("Aragorn"), _npc("Gobelin")], idx=0),
            _trigger(),
        )
        init_field = next(f for f in embed.fields if "initiative" in f.name.lower())
        aragorn_line = next(
            line for line in init_field.value.split("\n") if "Aragorn" in line
        )
        assert aragorn_line.startswith("➡️")

    def test_player_surprise_case_produces_field(self) -> None:
        embed = build_combat_start_embed(
            _state([_pc(), _npc("Gobelin", surprised=True)]),
            _trigger(
                surprise=InitiativeSide.PLAYERS, enemies=["Gobelin"],
            ),
        )
        surprise_field = next((f for f in embed.fields if f.name == "Surprise"), None)
        assert surprise_field is not None
        assert "surpris" in surprise_field.value.lower()
        assert "Gobelin" in surprise_field.value

    def test_npc_surprise_case_produces_field(self) -> None:
        pc = _pc()
        pc.conditions.append(
            ActiveCondition(
                condition_type=ConditionType.SURPRISED, source="combat_entry",
            ),
        )
        embed = build_combat_start_embed(
            _state([pc, _npc("Gobelin")]),
            _trigger(
                surprise=InitiativeSide.NPCS, enemies=["Gobelin"],
            ),
        )
        surprise_field = next((f for f in embed.fields if f.name == "Surprise"), None)
        assert surprise_field is not None
        assert "surpris" in surprise_field.value.lower()
        assert "Gobelin" in surprise_field.value

    def test_both_ready_case_has_no_surprise_field(self) -> None:
        embed = build_combat_start_embed(
            _state([_pc(), _npc()]),
            _trigger(surprise=InitiativeSide.BOTH_READY),
        )
        assert not any(f.name == "Surprise" for f in embed.fields)

    def test_surprised_combatant_has_surprised_suffix_in_initiative(self) -> None:
        embed = build_combat_start_embed(
            _state([_pc(), _npc("Gobelin", surprised=True)]),
            _trigger(
                surprise=InitiativeSide.PLAYERS, enemies=["Gobelin"],
            ),
        )
        init_field = next(f for f in embed.fields if "initiative" in f.name.lower())
        gobelin_line = next(
            line for line in init_field.value.split("\n") if "Gobelin" in line
        )
        assert "surpris" in gobelin_line.lower()

    def test_commands_examples_field_present(self) -> None:
        embed = build_combat_start_embed(_state([_pc(), _npc()]), _trigger())
        assert any(f.name == "À votre tour" for f in embed.fields)
