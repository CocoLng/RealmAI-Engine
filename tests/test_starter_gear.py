"""Tests for starter gear engine module."""

import pytest

from engine.character import CharacterClass
from engine.inventory import (
    ITEM_CATALOG,
    Armor,
    EquipmentSlot,
    Inventory,
    Weapon,
)
from engine.starter_gear import (
    apply_starter_kit,
    get_starter_kits,
)


# ---------------------------------------------------------------------------
# get_starter_kits tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("char_class", list(CharacterClass))
def test_every_class_has_two_or_three_kits(char_class: CharacterClass) -> None:
    """Every class returns 2-3 kits from get_starter_kits()."""
    kits = get_starter_kits(char_class)
    assert 2 <= len(kits) <= 3, (
        f"{char_class} has {len(kits)} kits, expected 2-3"
    )


@pytest.mark.parametrize("char_class", list(CharacterClass))
def test_all_kit_items_exist_in_catalog(char_class: CharacterClass) -> None:
    """All item names in all kits exist in ITEM_CATALOG."""
    for kit in get_starter_kits(char_class):
        for item_name in kit.items:
            assert item_name in ITEM_CATALOG, (
                f"Item '{item_name}' in kit '{kit.name}' for {char_class} "
                f"not found in ITEM_CATALOG"
            )


# ---------------------------------------------------------------------------
# apply_starter_kit tests
# ---------------------------------------------------------------------------


def test_apply_starter_kit_populates_correct_item_count() -> None:
    """apply_starter_kit() adds the correct number of items (items + equipped)."""
    kit = get_starter_kits(CharacterClass.ROGUE)[0]  # Shadow Blade
    inv = apply_starter_kit(kit, Inventory())

    total_items = len(inv.items) + len(inv.equipped)
    assert total_items == len(kit.items)


def test_apply_starter_kit_sets_gold() -> None:
    """apply_starter_kit() sets gold correctly."""
    kit = get_starter_kits(CharacterClass.WIZARD)[1]  # War Scholar, gold=20
    inv = apply_starter_kit(kit, Inventory())

    assert inv.gold == 20


def test_apply_starter_kit_equips_weapon_in_main_hand() -> None:
    """apply_starter_kit() auto-equips a weapon in MAIN_HAND."""
    kit = get_starter_kits(CharacterClass.FIGHTER)[0]  # Sword & Shield
    inv = apply_starter_kit(kit, Inventory())

    assert EquipmentSlot.MAIN_HAND in inv.equipped
    assert isinstance(inv.equipped[EquipmentSlot.MAIN_HAND], Weapon)


def test_apply_starter_kit_equips_armor_in_armor_slot() -> None:
    """apply_starter_kit() auto-equips armor in ARMOR slot."""
    kit = get_starter_kits(CharacterClass.FIGHTER)[0]  # Sword & Shield
    inv = apply_starter_kit(kit, Inventory())

    assert EquipmentSlot.ARMOR in inv.equipped
    assert isinstance(inv.equipped[EquipmentSlot.ARMOR], Armor)


def test_sword_and_shield_equips_shield_in_off_hand() -> None:
    """Fighter 'Sword & Shield' kit equips Shield in OFF_HAND."""
    kit = get_starter_kits(CharacterClass.FIGHTER)[0]
    assert kit.name == "Sword & Shield"

    inv = apply_starter_kit(kit, Inventory())

    assert EquipmentSlot.OFF_HAND in inv.equipped
    assert inv.equipped[EquipmentSlot.OFF_HAND].name == "Shield"


def test_savage_fighter_adds_two_handaxes() -> None:
    """Barbarian 'Savage Fighter' kit adds 2 Handaxes."""
    kits = get_starter_kits(CharacterClass.BARBARIAN)
    kit = next(k for k in kits if k.name == "Savage Fighter")

    inv = apply_starter_kit(kit, Inventory())

    # One Handaxe is equipped in MAIN_HAND, the other stays in items
    handaxes_in_items = [i for i in inv.items if i.name == "Handaxe"]
    handaxe_equipped = (
        inv.equipped.get(EquipmentSlot.MAIN_HAND) is not None
        and inv.equipped[EquipmentSlot.MAIN_HAND].name == "Handaxe"
    )

    total_handaxes = len(handaxes_in_items) + (1 if handaxe_equipped else 0)
    assert total_handaxes == 2


def test_apply_starter_kit_mutates_in_place() -> None:
    """apply_starter_kit() mutates the inventory in-place and returns it."""
    kit = get_starter_kits(CharacterClass.CLERIC)[0]
    original = Inventory()
    result = apply_starter_kit(kit, original)

    assert result is original
    assert len(result.items) > 0 or len(result.equipped) > 0
    assert result.gold == kit.gold


@pytest.mark.parametrize("char_class", list(CharacterClass))
def test_all_kits_apply_without_error(char_class: CharacterClass) -> None:
    """Every starter kit for every class applies without raising."""
    for kit in get_starter_kits(char_class):
        inv = apply_starter_kit(kit, Inventory())
        # Sanity check: at least one item equipped
        assert len(inv.equipped) >= 1, (
            f"Kit '{kit.name}' for {char_class} equipped nothing"
        )
