"""Tests for bot/embeds/dice_embed.py (task 60)."""

from __future__ import annotations

import pytest

from bot.embeds.dice_embed import (
    build_attack_roll_embed,
    build_damage_roll_embed,
    build_generic_check_embed,
    build_save_check_embed,
)
from engine.combat import AttackResult
from engine.dice import D20CheckResult, DiceResult, RollOutcome
from engine.inventory import DamageType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_attack(
    *,
    hit: bool = True,
    critical: bool = False,
    outcome: RollOutcome = RollOutcome.SUCCESS,
    attack_roll: int = 15,
    attack_total: int = 19,
    ac: int = 14,
    damage: int = 8,
    damage_type: DamageType = DamageType.SLASHING,
) -> AttackResult:
    return AttackResult(
        attacker="Aragorn",
        defender="Gobelin",
        weapon_name="Épée longue",
        attack_roll=attack_roll,
        attack_total=attack_total,
        ac=ac,
        hit=hit,
        critical=critical,
        outcome=outcome,
        damage=damage if hit else 0,
        damage_type=damage_type,
        defender_hp_remaining=4 if hit else 12,
    )


def _make_check(
    *,
    total: int = 15,
    dc: int = 12,
    outcome: RollOutcome = RollOutcome.SUCCESS,
    natural: int = 11,
    modifier: int = 4,
) -> D20CheckResult:
    return D20CheckResult(
        expression=f"1d20+{modifier}",
        rolls=[natural],
        modifier=modifier,
        total=total,
        dc=dc,
        outcome=outcome,
        margin=total - dc,
    )


# ---------------------------------------------------------------------------
# Attack roll embed
# ---------------------------------------------------------------------------


class TestAttackRollEmbed:
    def test_hit_is_green(self) -> None:
        embed = build_attack_roll_embed(_make_attack(hit=True), "Aragorn")
        assert embed.color is not None
        assert embed.color.value == 0x2ECC71
        assert embed.description is not None
        assert "Touché" in embed.description

    def test_miss_is_red(self) -> None:
        attack = _make_attack(
            hit=False,
            outcome=RollOutcome.FAILURE,
            attack_roll=5,
            attack_total=9,
            damage=0,
        )
        embed = build_attack_roll_embed(attack, "Aragorn")
        assert embed.color is not None
        assert embed.color.value == 0xE74C3C
        assert embed.description is not None
        assert "Raté" in embed.description

    def test_critical_hit_is_gold(self) -> None:
        attack = _make_attack(
            hit=True,
            critical=True,
            outcome=RollOutcome.CRITICAL_SUCCESS,
            attack_roll=20,
            attack_total=24,
            damage=16,
        )
        embed = build_attack_roll_embed(attack, "Aragorn")
        assert embed.color is not None
        assert embed.color.value == 0xF1C40F
        assert embed.description is not None
        assert "critique" in embed.description.lower()

    def test_hit_includes_damage_in_french(self) -> None:
        attack = _make_attack(damage=11, damage_type=DamageType.FIRE)
        embed = build_attack_roll_embed(attack, "Aragorn")
        assert embed.description is not None
        assert "11" in embed.description
        assert "feu" in embed.description

    def test_footer_includes_nat_roll(self) -> None:
        attack = _make_attack(attack_roll=17, attack_total=21, ac=14)
        embed = build_attack_roll_embed(attack, "Aragorn")
        assert embed.footer is not None
        assert "Nat 17" in (embed.footer.text or "")
        assert "+7" in (embed.footer.text or "")


# ---------------------------------------------------------------------------
# Save / check embeds
# ---------------------------------------------------------------------------


class TestSaveCheckEmbed:
    def test_success_is_green(self) -> None:
        check = _make_check(total=15, dc=12, outcome=RollOutcome.SUCCESS)
        embed = build_save_check_embed(
            check, "Jet de sauvegarde", "Aragorn", ability="DEX",
        )
        assert embed.color is not None
        assert embed.color.value == 0x2ECC71
        assert embed.title is not None
        assert "DEX" in embed.title

    def test_failure_is_red(self) -> None:
        check = _make_check(
            total=7, dc=14, outcome=RollOutcome.FAILURE, natural=5,
        )
        embed = build_save_check_embed(
            check, "Tentative de fuite", "Aragorn", ability="DEX",
        )
        assert embed.color is not None
        assert embed.color.value == 0xE74C3C

    def test_generic_check_embed_defaults_dash_ability(self) -> None:
        check = _make_check()
        embed = build_generic_check_embed(check, "Jet de perception", "Aragorn")
        assert embed.title is not None
        assert "DEX" not in embed.title
        # Ability "-" is hidden from the title; only the label remains.
        assert "perception" in embed.title.lower()

    def test_footer_includes_nat_roll_and_margin(self) -> None:
        check = _make_check(total=18, dc=14, natural=14)
        embed = build_save_check_embed(
            check, "Jet d'initiative", "Aragorn", ability="DEX",
        )
        assert embed.footer is not None
        footer = embed.footer.text or ""
        assert "Nat 14" in footer
        assert "+4" in footer


# ---------------------------------------------------------------------------
# Damage roll embed
# ---------------------------------------------------------------------------


class TestDamageRollEmbed:
    def test_shows_individual_rolls_in_description(self) -> None:
        result = DiceResult(
            expression="3d8", rolls=[3, 4, 6], modifier=0, total=13,
        )
        embed = build_damage_roll_embed(
            "3d8", result, DamageType.SLASHING, source_name="Frappe tonnerre",
        )
        assert embed.description is not None
        assert "(3, 4, 6)" in embed.description
        assert embed.title is not None
        assert "13" in embed.title
        assert "tranchant" in embed.title

    def test_includes_source_name_when_provided(self) -> None:
        result = DiceResult(expression="2d6+2", rolls=[5, 3], modifier=2, total=10)
        embed = build_damage_roll_embed(
            "2d6+2", result, DamageType.FIRE, source_name="Boule de feu",
        )
        assert embed.title is not None
        assert "Boule de feu" in embed.title
        assert "feu" in embed.title


# ---------------------------------------------------------------------------
# Color parametrisation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "outcome, expected_color",
    [
        (RollOutcome.CRITICAL_SUCCESS, 0xF1C40F),
        (RollOutcome.SUCCESS, 0x2ECC71),
        (RollOutcome.NEAR_SUCCESS, 0x2ECC71),
        (RollOutcome.NEAR_FAILURE, 0xE74C3C),
        (RollOutcome.FAILURE, 0xE74C3C),
        (RollOutcome.CRITICAL_FAILURE, 0xE74C3C),
    ],
)
def test_save_embed_color_matches_outcome(
    outcome: RollOutcome, expected_color: int,
) -> None:
    check = _make_check(outcome=outcome)
    embed = build_save_check_embed(
        check, "Jet", "Aragorn", ability="DEX",
    )
    assert embed.color is not None
    assert embed.color.value == expected_color
