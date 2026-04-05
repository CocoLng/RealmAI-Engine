"""Tests for engine/inventory.py — items, equipment, weight, attunement."""

import pytest

from engine.inventory import (
    ArmorCategory,
    DamageType,
    EquipmentSlot,
    ItemType,
    Rarity,
    WeaponCategory,
    WeaponProperty,
)


class TestItemType:
    def test_all_members_exist(self) -> None:
        expected = {
            "WEAPON", "ARMOR", "SHIELD", "POTION", "SCROLL",
            "ADVENTURING_GEAR", "TOOL", "AMMUNITION",
        }
        assert set(ItemType.__members__) == expected


class TestRarity:
    def test_all_members_exist(self) -> None:
        expected = {"COMMON", "UNCOMMON", "RARE", "VERY_RARE", "LEGENDARY"}
        assert set(Rarity.__members__) == expected


class TestWeaponCategory:
    def test_all_members_exist(self) -> None:
        expected = {"SIMPLE_MELEE", "SIMPLE_RANGED", "MARTIAL_MELEE", "MARTIAL_RANGED"}
        assert set(WeaponCategory.__members__) == expected


class TestArmorCategory:
    def test_all_members_exist(self) -> None:
        expected = {"LIGHT", "MEDIUM", "HEAVY"}
        assert set(ArmorCategory.__members__) == expected


class TestDamageType:
    def test_all_members_exist(self) -> None:
        expected = {
            "SLASHING", "PIERCING", "BLUDGEONING", "FIRE", "COLD",
            "LIGHTNING", "POISON", "RADIANT", "NECROTIC",
        }
        assert set(DamageType.__members__) == expected


class TestWeaponProperty:
    def test_all_members_exist(self) -> None:
        expected = {
            "FINESSE", "VERSATILE", "THROWN", "TWO_HANDED", "LIGHT",
            "HEAVY", "REACH", "LOADING", "AMMUNITION",
        }
        assert set(WeaponProperty.__members__) == expected


class TestEquipmentSlot:
    def test_all_members_exist(self) -> None:
        expected = {
            "MAIN_HAND", "OFF_HAND", "ARMOR", "HEAD",
            "HANDS", "FEET", "NECK", "RING_1", "RING_2",
        }
        assert set(EquipmentSlot.__members__) == expected
