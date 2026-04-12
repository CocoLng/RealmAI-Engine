"""Tests for engine/npc_library.py."""

import pytest

from engine.npc_library import ARCHETYPE_BUILDERS, get_archetype, list_archetypes
from engine.npc_stat_block import NPCStatBlock, NPCTier


# Expected tier per archetype — acts as a consistency check against any
# accidental tier drift in a future edit.
EXPECTED_TIERS: dict[str, NPCTier] = {
    "commoner": NPCTier.MINION,
    "guard": NPCTier.MINION,
    "bandit": NPCTier.MINION,
    "cultist": NPCTier.MINION,
    "soldier": NPCTier.ELITE,
    "captain": NPCTier.ELITE,
    "brute": NPCTier.ELITE,
    "mage": NPCTier.ELITE,
    "assassin": NPCTier.ELITE,
    "shaman": NPCTier.ELITE,
    "generic_boss": NPCTier.BOSS,
}


class TestArchetypeBuilders:
    def test_all_archetypes_build_successfully(self) -> None:
        for name, builder in ARCHETYPE_BUILDERS.items():
            block = builder()
            assert isinstance(block, NPCStatBlock)
            assert block.archetype == name

    def test_eleven_archetypes_registered(self) -> None:
        assert len(ARCHETYPE_BUILDERS) == 11

    def test_archetype_tier_consistency(self) -> None:
        for name, expected_tier in EXPECTED_TIERS.items():
            assert ARCHETYPE_BUILDERS[name]().tier == expected_tier

    def test_every_archetype_has_at_least_one_attack(self) -> None:
        for name, builder in ARCHETYPE_BUILDERS.items():
            block = builder()
            assert len(block.attacks) >= 1, f"{name} has no attacks"


class TestTierConventions:
    def test_minions_have_no_signatures(self) -> None:
        for name in ("commoner", "guard", "bandit", "cultist"):
            block = get_archetype(name)
            assert block.signature_abilities == [], (
                f"minion {name} should have no signatures"
            )
            assert block.legendary_actions == []
            assert block.legendary_points_per_round == 0

    def test_minions_have_multiattack_one(self) -> None:
        for name in ("commoner", "guard", "bandit", "cultist"):
            assert get_archetype(name).multiattack_count == 1

    def test_elites_have_at_least_one_signature(self) -> None:
        for name in ("soldier", "captain", "brute", "mage", "assassin", "shaman"):
            block = get_archetype(name)
            assert len(block.signature_abilities) >= 1, (
                f"elite {name} should have at least one signature"
            )
            assert block.legendary_points_per_round == 0

    def test_elites_have_multiattack_two(self) -> None:
        for name in ("soldier", "captain", "brute", "mage", "assassin", "shaman"):
            assert get_archetype(name).multiattack_count == 2

    def test_boss_has_multiattack_three(self) -> None:
        assert get_archetype("generic_boss").multiattack_count == 3

    def test_boss_has_legendary_actions_and_phases(self) -> None:
        block = get_archetype("generic_boss")
        assert len(block.legendary_actions) == 3
        assert block.legendary_points_per_round == 3
        assert len(block.phases) == 2


class TestFreshInstancePerCall:
    def test_returns_fresh_instance(self) -> None:
        first = get_archetype("captain")
        second = get_archetype("captain")
        assert first is not second
        assert first.signature_abilities[0] is not second.signature_abilities[0]

    def test_mutation_isolated_between_calls(self) -> None:
        first = get_archetype("captain")
        first.signature_abilities[0].uses_remaining = 0
        second = get_archetype("captain")
        assert second.signature_abilities[0].uses_remaining == 1


class TestPublicHelpers:
    def test_list_archetypes_sorted(self) -> None:
        names = list_archetypes()
        assert names == sorted(names)
        assert set(names) == set(ARCHETYPE_BUILDERS.keys())

    def test_get_unknown_archetype_raises(self) -> None:
        with pytest.raises(KeyError):
            get_archetype("nonexistent_archetype")
