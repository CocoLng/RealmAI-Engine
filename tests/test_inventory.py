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
    add_item,
    attune_item,
    compute_ac_from_equipment,
    compute_carrying_capacity,
    compute_total_weight,
    create_inventory,
    equip_item,
    is_encumbered,
    remove_item,
    unattune_item,
    unequip_item,
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


class TestAddItem:
    """Adding items to inventory."""

    def test_add_single_item(self) -> None:
        inv = create_inventory()
        torch = Item(name="Torch", item_type=ItemType.ADVENTURING_GEAR, weight=1.0)
        result = add_item(inv, torch)
        assert len(result.items) == 1
        assert result.items[0].name == "Torch"

    def test_returns_new_inventory(self) -> None:
        inv = create_inventory()
        torch = Item(name="Torch", item_type=ItemType.ADVENTURING_GEAR, weight=1.0)
        result = add_item(inv, torch)
        assert result is not inv

    def test_add_stackable_increments_quantity(self) -> None:
        arrows = Item(
            name="Arrows",
            item_type=ItemType.AMMUNITION,
            weight=0.05,
            stackable=True,
            quantity=20,
        )
        inv = Inventory(items=[arrows])
        more_arrows = Item(
            name="Arrows",
            item_type=ItemType.AMMUNITION,
            weight=0.05,
            stackable=True,
            quantity=10,
        )
        result = add_item(inv, more_arrows)
        assert len(result.items) == 1
        assert result.items[0].quantity == 30

    def test_add_non_stackable_duplicates(self) -> None:
        torch1 = Item(name="Torch", item_type=ItemType.ADVENTURING_GEAR, weight=1.0)
        torch2 = Item(name="Torch", item_type=ItemType.ADVENTURING_GEAR, weight=1.0)
        inv = Inventory(items=[torch1])
        result = add_item(inv, torch2)
        assert len(result.items) == 2


class TestRemoveItem:
    """Removing items from inventory."""

    def test_remove_item(self) -> None:
        torch = Item(name="Torch", item_type=ItemType.ADVENTURING_GEAR, weight=1.0)
        inv = Inventory(items=[torch])
        result, removed = remove_item(inv, "Torch")
        assert len(result.items) == 0
        assert removed.name == "Torch"

    def test_returns_new_inventory(self) -> None:
        torch = Item(name="Torch", item_type=ItemType.ADVENTURING_GEAR, weight=1.0)
        inv = Inventory(items=[torch])
        result, _ = remove_item(inv, "Torch")
        assert result is not inv

    def test_remove_from_stack(self) -> None:
        arrows = Item(
            name="Arrows",
            item_type=ItemType.AMMUNITION,
            weight=0.05,
            stackable=True,
            quantity=20,
        )
        inv = Inventory(items=[arrows])
        result, removed = remove_item(inv, "Arrows", quantity=5)
        assert result.items[0].quantity == 15
        assert removed.quantity == 5

    def test_remove_entire_stack(self) -> None:
        arrows = Item(
            name="Arrows",
            item_type=ItemType.AMMUNITION,
            weight=0.05,
            stackable=True,
            quantity=20,
        )
        inv = Inventory(items=[arrows])
        result, removed = remove_item(inv, "Arrows", quantity=20)
        assert len(result.items) == 0
        assert removed.quantity == 20

    def test_not_found_raises(self) -> None:
        inv = create_inventory()
        with pytest.raises(ValueError, match="not found"):
            remove_item(inv, "Ghost Item")

    def test_insufficient_quantity_raises(self) -> None:
        arrows = Item(
            name="Arrows",
            item_type=ItemType.AMMUNITION,
            weight=0.05,
            stackable=True,
            quantity=5,
        )
        inv = Inventory(items=[arrows])
        with pytest.raises(ValueError, match="Insufficient quantity"):
            remove_item(inv, "Arrows", quantity=10)


class TestEquipItem:
    """Equipping items to slots."""

    def test_equip_weapon_to_main_hand(self) -> None:
        sword = Weapon(
            name="Longsword",
            weight=3.0,
            damage_dice="1d8",
            damage_type=DamageType.SLASHING,
            weapon_category=WeaponCategory.MARTIAL_MELEE,
        )
        inv = Inventory(items=[sword])
        result = equip_item(inv, "Longsword", EquipmentSlot.MAIN_HAND)
        assert EquipmentSlot.MAIN_HAND in result.equipped
        assert result.equipped[EquipmentSlot.MAIN_HAND].name == "Longsword"
        assert len(result.items) == 0

    def test_returns_new_inventory(self) -> None:
        sword = Weapon(
            name="Longsword",
            weight=3.0,
            damage_dice="1d8",
            damage_type=DamageType.SLASHING,
            weapon_category=WeaponCategory.MARTIAL_MELEE,
        )
        inv = Inventory(items=[sword])
        result = equip_item(inv, "Longsword", EquipmentSlot.MAIN_HAND)
        assert result is not inv

    def test_equip_armor_to_armor_slot(self) -> None:
        leather = Armor(
            name="Leather",
            weight=10.0,
            armor_category=ArmorCategory.LIGHT,
            base_ac=11,
        )
        inv = Inventory(items=[leather])
        result = equip_item(inv, "Leather", EquipmentSlot.ARMOR)
        assert result.equipped[EquipmentSlot.ARMOR].name == "Leather"

    def test_equip_shield_to_off_hand(self) -> None:
        shield = Item(name="Shield", item_type=ItemType.SHIELD, weight=6.0)
        inv = Inventory(items=[shield])
        result = equip_item(inv, "Shield", EquipmentSlot.OFF_HAND)
        assert result.equipped[EquipmentSlot.OFF_HAND].name == "Shield"

    def test_swap_equipped_item(self) -> None:
        sword1 = Weapon(
            name="Longsword",
            weight=3.0,
            damage_dice="1d8",
            damage_type=DamageType.SLASHING,
            weapon_category=WeaponCategory.MARTIAL_MELEE,
        )
        sword2 = Weapon(
            name="Shortsword",
            weight=2.0,
            damage_dice="1d6",
            damage_type=DamageType.PIERCING,
            weapon_category=WeaponCategory.MARTIAL_MELEE,
            properties=[WeaponProperty.FINESSE, WeaponProperty.LIGHT],
        )
        inv = Inventory(
            items=[sword2],
            equipped={EquipmentSlot.MAIN_HAND: sword1},
        )
        result = equip_item(inv, "Shortsword", EquipmentSlot.MAIN_HAND)
        assert result.equipped[EquipmentSlot.MAIN_HAND].name == "Shortsword"
        assert any(i.name == "Longsword" for i in result.items)

    def test_two_handed_clears_off_hand(self) -> None:
        greataxe = Weapon(
            name="Greataxe",
            weight=7.0,
            damage_dice="1d12",
            damage_type=DamageType.SLASHING,
            weapon_category=WeaponCategory.MARTIAL_MELEE,
            properties=[WeaponProperty.HEAVY, WeaponProperty.TWO_HANDED],
        )
        shield = Item(name="Shield", item_type=ItemType.SHIELD, weight=6.0)
        inv = Inventory(
            items=[greataxe],
            equipped={EquipmentSlot.OFF_HAND: shield},
        )
        result = equip_item(inv, "Greataxe", EquipmentSlot.MAIN_HAND)
        assert EquipmentSlot.MAIN_HAND in result.equipped
        assert EquipmentSlot.OFF_HAND not in result.equipped
        assert any(i.name == "Shield" for i in result.items)

    def test_item_not_found_raises(self) -> None:
        inv = create_inventory()
        with pytest.raises(ValueError, match="not found"):
            equip_item(inv, "Ghost", EquipmentSlot.MAIN_HAND)

    def test_weapon_in_armor_slot_raises(self) -> None:
        sword = Weapon(
            name="Longsword",
            weight=3.0,
            damage_dice="1d8",
            damage_type=DamageType.SLASHING,
            weapon_category=WeaponCategory.MARTIAL_MELEE,
        )
        inv = Inventory(items=[sword])
        with pytest.raises(ValueError, match="Cannot equip"):
            equip_item(inv, "Longsword", EquipmentSlot.ARMOR)

    def test_armor_in_main_hand_raises(self) -> None:
        leather = Armor(
            name="Leather",
            weight=10.0,
            armor_category=ArmorCategory.LIGHT,
            base_ac=11,
        )
        inv = Inventory(items=[leather])
        with pytest.raises(ValueError, match="Cannot equip"):
            equip_item(inv, "Leather", EquipmentSlot.MAIN_HAND)


class TestUnequipItem:
    """Unequipping items from slots."""

    def test_unequip_returns_to_items(self) -> None:
        sword = Weapon(
            name="Longsword",
            weight=3.0,
            damage_dice="1d8",
            damage_type=DamageType.SLASHING,
            weapon_category=WeaponCategory.MARTIAL_MELEE,
        )
        inv = Inventory(equipped={EquipmentSlot.MAIN_HAND: sword})
        result = unequip_item(inv, EquipmentSlot.MAIN_HAND)
        assert EquipmentSlot.MAIN_HAND not in result.equipped
        assert any(i.name == "Longsword" for i in result.items)

    def test_returns_new_inventory(self) -> None:
        sword = Weapon(
            name="Longsword",
            weight=3.0,
            damage_dice="1d8",
            damage_type=DamageType.SLASHING,
            weapon_category=WeaponCategory.MARTIAL_MELEE,
        )
        inv = Inventory(equipped={EquipmentSlot.MAIN_HAND: sword})
        result = unequip_item(inv, EquipmentSlot.MAIN_HAND)
        assert result is not inv

    def test_empty_slot_raises(self) -> None:
        inv = create_inventory()
        with pytest.raises(ValueError, match="Nothing equipped"):
            unequip_item(inv, EquipmentSlot.MAIN_HAND)


MAX_ATTUNEMENT = 3


class TestAttuneItem:
    """Attunement management (max 3)."""

    def test_attune_item(self) -> None:
        ring = Item(
            name="Ring of Protection",
            item_type=ItemType.ADVENTURING_GEAR,
            weight=0.0,
            magical=True,
            requires_attunement=True,
        )
        inv = Inventory(items=[ring])
        result = attune_item(inv, "Ring of Protection")
        assert len(result.attuned) == 1
        assert result.attuned[0].name == "Ring of Protection"

    def test_returns_new_inventory(self) -> None:
        ring = Item(
            name="Ring of Protection",
            item_type=ItemType.ADVENTURING_GEAR,
            weight=0.0,
            magical=True,
            requires_attunement=True,
        )
        inv = Inventory(items=[ring])
        result = attune_item(inv, "Ring of Protection")
        assert result is not inv

    def test_max_attunement_raises(self) -> None:
        items = [
            Item(
                name=f"Ring {i}",
                item_type=ItemType.ADVENTURING_GEAR,
                weight=0.0,
                magical=True,
                requires_attunement=True,
            )
            for i in range(4)
        ]
        inv = Inventory(
            items=[items[3]],
            attuned=items[:3],
        )
        with pytest.raises(ValueError, match="Maximum attunement"):
            attune_item(inv, "Ring 3")

    def test_not_attuneable_raises(self) -> None:
        torch = Item(name="Torch", item_type=ItemType.ADVENTURING_GEAR, weight=1.0)
        inv = Inventory(items=[torch])
        with pytest.raises(ValueError, match="does not require attunement"):
            attune_item(inv, "Torch")

    def test_item_not_found_raises(self) -> None:
        inv = create_inventory()
        with pytest.raises(ValueError, match="not found"):
            attune_item(inv, "Ghost")

    def test_already_attuned_raises(self) -> None:
        ring = Item(
            name="Ring of Protection",
            item_type=ItemType.ADVENTURING_GEAR,
            weight=0.0,
            magical=True,
            requires_attunement=True,
        )
        inv = Inventory(items=[ring], attuned=[ring])
        with pytest.raises(ValueError, match="already attuned"):
            attune_item(inv, "Ring of Protection")


class TestUnattuneItem:
    """Removing attunement."""

    def test_unattune_item(self) -> None:
        ring = Item(
            name="Ring of Protection",
            item_type=ItemType.ADVENTURING_GEAR,
            weight=0.0,
            magical=True,
            requires_attunement=True,
        )
        inv = Inventory(attuned=[ring])
        result = unattune_item(inv, "Ring of Protection")
        assert len(result.attuned) == 0

    def test_returns_new_inventory(self) -> None:
        ring = Item(
            name="Ring of Protection",
            item_type=ItemType.ADVENTURING_GEAR,
            weight=0.0,
            magical=True,
            requires_attunement=True,
        )
        inv = Inventory(attuned=[ring])
        result = unattune_item(inv, "Ring of Protection")
        assert result is not inv

    def test_not_attuned_raises(self) -> None:
        inv = create_inventory()
        with pytest.raises(ValueError, match="not attuned"):
            unattune_item(inv, "Ghost")


class TestComputeACFromEquipment:
    """AC computation from armor and shield."""

    def test_no_armor(self) -> None:
        assert compute_ac_from_equipment({}, dex_modifier=2) == 12  # 10 + 2

    def test_light_armor_full_dex(self) -> None:
        leather = Armor(
            name="Leather",
            weight=10.0,
            armor_category=ArmorCategory.LIGHT,
            base_ac=11,
        )
        equipped = {EquipmentSlot.ARMOR: leather}
        assert compute_ac_from_equipment(equipped, dex_modifier=3) == 14  # 11 + 3

    def test_medium_armor_dex_capped_at_2(self) -> None:
        half_plate = Armor(
            name="Half Plate",
            weight=40.0,
            armor_category=ArmorCategory.MEDIUM,
            base_ac=15,
            dex_cap=2,
            stealth_disadvantage=True,
        )
        equipped = {EquipmentSlot.ARMOR: half_plate}
        assert compute_ac_from_equipment(equipped, dex_modifier=4) == 17  # 15 + 2

    def test_medium_armor_low_dex(self) -> None:
        half_plate = Armor(
            name="Half Plate",
            weight=40.0,
            armor_category=ArmorCategory.MEDIUM,
            base_ac=15,
            dex_cap=2,
            stealth_disadvantage=True,
        )
        equipped = {EquipmentSlot.ARMOR: half_plate}
        assert compute_ac_from_equipment(equipped, dex_modifier=1) == 16  # 15 + 1

    def test_heavy_armor_no_dex(self) -> None:
        chain_mail = Armor(
            name="Chain Mail",
            weight=55.0,
            armor_category=ArmorCategory.HEAVY,
            base_ac=16,
            dex_cap=0,
            strength_required=13,
            stealth_disadvantage=True,
        )
        equipped = {EquipmentSlot.ARMOR: chain_mail}
        assert compute_ac_from_equipment(equipped, dex_modifier=5) == 16  # flat 16

    def test_shield_adds_2(self) -> None:
        shield = Item(name="Shield", item_type=ItemType.SHIELD, weight=6.0, value_gp=10)
        equipped = {EquipmentSlot.OFF_HAND: shield}
        assert compute_ac_from_equipment(equipped, dex_modifier=2) == 14  # 10 + 2 + 2

    def test_armor_plus_shield(self) -> None:
        chain_mail = Armor(
            name="Chain Mail",
            weight=55.0,
            armor_category=ArmorCategory.HEAVY,
            base_ac=16,
            dex_cap=0,
            strength_required=13,
            stealth_disadvantage=True,
        )
        shield = Item(name="Shield", item_type=ItemType.SHIELD, weight=6.0, value_gp=10)
        equipped = {
            EquipmentSlot.ARMOR: chain_mail,
            EquipmentSlot.OFF_HAND: shield,
        }
        assert compute_ac_from_equipment(equipped, dex_modifier=2) == 18  # 16 + 2

    def test_negative_dex_lowers_ac(self) -> None:
        assert compute_ac_from_equipment({}, dex_modifier=-1) == 9  # 10 + (-1)
