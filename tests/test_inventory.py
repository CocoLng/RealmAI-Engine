"""Tests for engine/inventory.py — items, equipment, weight, attunement."""

import pytest

from engine.character import Size
from engine.inventory import (
    Armor,
    ArmorCategory,
    DamageType,
    EquipmentSlot,
    Inventory,
    Item,
    ItemType,
    Rarity,
    Weapon,
    WeaponCategory,
    WeaponProperty,
    compute_carrying_capacity,
    compute_total_weight,
    create_inventory,
    is_encumbered,
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


class TestInventory:
    """Inventory container model."""

    def test_create_empty_inventory(self) -> None:
        inv = create_inventory()
        assert inv.items == []
        assert inv.equipped == {}
        assert inv.attuned == []
        assert inv.gold == 0

    def test_negative_gold_raises(self) -> None:
        with pytest.raises(ValueError):
            Inventory(gold=-1)

    def test_model_dump_roundtrip(self) -> None:
        inv = create_inventory()
        data = inv.model_dump()
        restored = Inventory(**data)
        assert restored == inv


class TestComputeCarryingCapacity:
    """Carrying capacity = STR x 15, halved for Small."""

    def test_strength_10_medium(self) -> None:
        assert compute_carrying_capacity(10, Size.MEDIUM) == 150.0

    def test_strength_20_medium(self) -> None:
        assert compute_carrying_capacity(20, Size.MEDIUM) == 300.0

    def test_strength_10_small(self) -> None:
        assert compute_carrying_capacity(10, Size.SMALL) == 75.0

    def test_strength_1_minimum(self) -> None:
        assert compute_carrying_capacity(1, Size.MEDIUM) == 15.0

    def test_strength_0_raises(self) -> None:
        with pytest.raises(ValueError, match="Strength must be 1-30"):
            compute_carrying_capacity(0, Size.MEDIUM)

    def test_strength_31_raises(self) -> None:
        with pytest.raises(ValueError, match="Strength must be 1-30"):
            compute_carrying_capacity(31, Size.MEDIUM)


class TestComputeTotalWeight:
    """Total weight includes all items (inventory + equipped), respecting quantity."""

    def test_empty_inventory(self) -> None:
        inv = create_inventory()
        assert compute_total_weight(inv) == 0.0

    def test_items_only(self) -> None:
        inv = Inventory(
            items=[
                Item(name="Torch", item_type=ItemType.ADVENTURING_GEAR, weight=1.0),
                Item(name="Rope", item_type=ItemType.ADVENTURING_GEAR, weight=10.0),
            ],
        )
        assert compute_total_weight(inv) == 11.0

    def test_stackable_quantity(self) -> None:
        inv = Inventory(
            items=[
                Item(
                    name="Arrows",
                    item_type=ItemType.AMMUNITION,
                    weight=0.05,
                    stackable=True,
                    quantity=20,
                ),
            ],
        )
        assert compute_total_weight(inv) == pytest.approx(1.0)

    def test_includes_equipped_items(self) -> None:
        sword = Item(name="Sword", item_type=ItemType.WEAPON, weight=3.0)
        inv = Inventory(
            equipped={EquipmentSlot.MAIN_HAND: sword},
        )
        assert compute_total_weight(inv) == 3.0

    def test_items_plus_equipped(self) -> None:
        torch = Item(name="Torch", item_type=ItemType.ADVENTURING_GEAR, weight=1.0)
        sword = Item(name="Sword", item_type=ItemType.WEAPON, weight=3.0)
        inv = Inventory(
            items=[torch],
            equipped={EquipmentSlot.MAIN_HAND: sword},
        )
        assert compute_total_weight(inv) == 4.0


class TestIsEncumbered:
    """Encumbered when total weight > carrying capacity."""

    def test_not_encumbered(self) -> None:
        inv = create_inventory()
        assert is_encumbered(inv, 10, Size.MEDIUM) is False

    def test_encumbered(self) -> None:
        heavy = Item(name="Anvil", item_type=ItemType.ADVENTURING_GEAR, weight=200.0)
        inv = Inventory(items=[heavy])
        assert is_encumbered(inv, 10, Size.MEDIUM) is True

    def test_exactly_at_capacity(self) -> None:
        exact = Item(name="Load", item_type=ItemType.ADVENTURING_GEAR, weight=150.0)
        inv = Inventory(items=[exact])
        assert is_encumbered(inv, 10, Size.MEDIUM) is False
