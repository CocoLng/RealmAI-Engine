"""The player must see their death saving throws as they happen."""

from __future__ import annotations

from bot.embeds.dice_embed import build_death_save_embed
from engine.combat import DeathSaveResult
from engine.dice import RollOutcome


def _result(
    *,
    roll: int = 15,
    success: bool = True,
    outcome: RollOutcome = RollOutcome.SUCCESS,
    successes: int = 1,
    failures: int = 0,
    stabilized: bool = False,
    died: bool = False,
    revived: bool = False,
) -> DeathSaveResult:
    return DeathSaveResult(
        character_name="Thorin",
        roll=roll,
        success=success,
        outcome=outcome,
        total_successes=successes,
        total_failures=failures,
        stabilized=stabilized,
        died=died,
        revived=revived,
    )


class TestBuildDeathSaveEmbed:
    def test_names_the_character_and_the_roll(self) -> None:
        embed = build_death_save_embed(_result(roll=15))
        assert "Thorin" in (embed.title or "")
        assert "15" in (embed.description or "")

    def test_shows_the_running_tally(self) -> None:
        """The tension is the tally — 2 failures must read as 2 failures."""
        embed = build_death_save_embed(_result(successes=1, failures=2))
        body = embed.description or ""
        assert "1" in body and "2" in body

    def test_death_is_unmistakable(self) -> None:
        embed = build_death_save_embed(
            _result(
                roll=3, success=False, outcome=RollOutcome.FAILURE,
                successes=0, failures=3, died=True,
            ),
        )
        assert "mort" in (embed.description or "").lower()

    def test_stabilized_is_announced(self) -> None:
        embed = build_death_save_embed(
            _result(successes=3, failures=0, stabilized=True),
        )
        assert "stabilis" in (embed.description or "").lower()

    def test_nat_20_revival_is_announced(self) -> None:
        embed = build_death_save_embed(
            _result(
                roll=20, outcome=RollOutcome.CRITICAL_SUCCESS,
                successes=0, failures=0, revived=True,
            ),
        )
        body = (embed.description or "").lower()
        assert "1 pv" in body or "debout" in body

    def test_a_plain_failure_reads_as_a_failure(self) -> None:
        embed = build_death_save_embed(
            _result(roll=4, success=False, outcome=RollOutcome.FAILURE, failures=1),
        )
        assert embed.color is not None
