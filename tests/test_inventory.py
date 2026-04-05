"""Tests for engine/inventory.py — items, equipment, weight, attunement."""

import pytest

from engine.inventory import (
    Armor,
    ArmorCategory,
    DamageType,
    EquipmentSlot,
    Item,
    ItemType,
    Rarity,
    Weapon,
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


class TestItem:
    """Item base model validation."""

    def test_create_simple_item(self) -> None:
        item = Item(
            name="Torch",
            item_type=ItemType.ADVENTURING_GEAR,
            weight=1.0,
        )
        assert item.name == "Torch"
        assert item.item_type == ItemType.ADVENTURING_GEAR
        assert item.weight == 1.0
        assert item.value_gp == 0
        assert item.rarity == Rarity.COMMON
        assert item.description == ""
        assert item.requires_attunement is False
        assert item.magical is False
        assert item.stackable is False
        assert item.quantity == 1

    def test_empty_name_raises(self) -> None:
        with pytest.raises(ValueError):
            Item(name="", item_type=ItemType.POTION, weight=0.0)

    def test_negative_weight_raises(self) -> None:
        with pytest.raises(ValueError):
            Item(name="Bad", item_type=ItemType.POTION, weight=-1.0)

    def test_negative_value_raises(self) -> None:
        with pytest.raises(ValueError):
            Item(name="Bad", item_type=ItemType.POTION, weight=0.0, value_gp=-1)

    def test_quantity_below_1_raises(self) -> None:
        with pytest.raises(ValueError):
            Item(name="Bad", item_type=ItemType.POTION, weight=0.0, quantity=0)

    def test_model_dump_roundtrip(self) -> None:
        item = Item(
            name="Healing Potion",
            item_type=ItemType.POTION,
            weight=0.5,
            value_gp=50,
            magical=True,
            stackable=True,
            quantity=3,
        )
        data = item.model_dump()
        restored = Item(**data)
        assert restored == item


class TestWeapon:
    """Weapon model validation."""

    def test_create_longsword(self) -> None:
        sword = Weapon(
            name="Longsword",
            weight=3.0,
            value_gp=15,
            damage_dice="1d8",
            damage_type=DamageType.SLASHING,
            weapon_category=WeaponCategory.MARTIAL_MELEE,
            properties=[WeaponProperty.VERSATILE],
        )
        assert sword.item_type == ItemType.WEAPON
        assert sword.damage_dice == "1d8"
        assert sword.damage_type == DamageType.SLASHING
        assert sword.weapon_category == WeaponCategory.MARTIAL_MELEE
        assert sword.properties == [WeaponProperty.VERSATILE]
        assert sword.range_ft is None

    def test_create_shortbow(self) -> None:
        bow = Weapon(
            name="Shortbow",
            weight=2.0,
            value_gp=25,
            damage_dice="1d6",
            damage_type=DamageType.PIERCING,
            weapon_category=WeaponCategory.SIMPLE_RANGED,
            properties=[WeaponProperty.AMMUNITION, WeaponProperty.TWO_HANDED],
            range_ft=80,
        )
        assert bow.range_ft == 80
        assert bow.item_type == ItemType.WEAPON

    def test_item_type_forced_to_weapon(self) -> None:
        sword = Weapon(
            name="Test",
            weight=1.0,
            damage_dice="1d6",
            damage_type=DamageType.SLASHING,
            weapon_category=WeaponCategory.SIMPLE_MELEE,
        )
        assert sword.item_type == ItemType.WEAPON

    def test_model_dump_roundtrip(self) -> None:
        sword = Weapon(
            name="Dagger",
            weight=1.0,
            value_gp=2,
            damage_dice="1d4",
            damage_type=DamageType.PIERCING,
            weapon_category=WeaponCategory.SIMPLE_MELEE,
            properties=[WeaponProperty.FINESSE, WeaponProperty.LIGHT, WeaponProperty.THROWN],
            range_ft=20,
        )
        data = sword.model_dump()
        restored = Weapon(**data)
        assert restored == sword


class TestArmor:
    """Armor model validation."""

    def test_create_chain_mail(self) -> None:
        armor = Armor(
            name="Chain Mail",
            weight=55.0,
            value_gp=75,
            armor_category=ArmorCategory.HEAVY,
            base_ac=16,
            dex_cap=0,
            strength_required=13,
            stealth_disadvantage=True,
        )
        assert armor.item_type == ItemType.ARMOR
        assert armor.armor_category == ArmorCategory.HEAVY
        assert armor.base_ac == 16
        assert armor.dex_cap == 0
        assert armor.strength_required == 13
        assert armor.stealth_disadvantage is True

    def test_create_leather_armor(self) -> None:
        armor = Armor(
            name="Leather",
            weight=10.0,
            value_gp=10,
            armor_category=ArmorCategory.LIGHT,
            base_ac=11,
        )
        assert armor.dex_cap is None  # unlimited DEX for light
        assert armor.strength_required == 0
        assert armor.stealth_disadvantage is False

    def test_item_type_forced_to_armor(self) -> None:
        armor = Armor(
            name="Test",
            weight=1.0,
            armor_category=ArmorCategory.LIGHT,
            base_ac=11,
        )
        assert armor.item_type == ItemType.ARMOR

    def test_base_ac_below_10_raises(self) -> None:
        with pytest.raises(ValueError):
            Armor(
                name="Bad",
                weight=1.0,
                armor_category=ArmorCategory.LIGHT,
                base_ac=9,
            )

    def test_model_dump_roundtrip(self) -> None:
        armor = Armor(
            name="Half Plate",
            weight=40.0,
            value_gp=750,
            armor_category=ArmorCategory.MEDIUM,
            base_ac=15,
            dex_cap=2,
            stealth_disadvantage=True,
        )
        data = armor.model_dump()
        restored = Armor(**data)
        assert restored == armor
