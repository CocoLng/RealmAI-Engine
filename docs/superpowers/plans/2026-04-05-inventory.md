# Inventory System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `engine/inventory.py` with items, equipment, weight, attunement, and a starter item catalog following simplified SRD 5e rules.

**Architecture:** Pure deterministic Python module with Pydantic v2 models for Item/Weapon/Armor/Inventory, StrEnum for classification, pure functions for all operations (add/remove/equip/attune), and a module-level item catalog. Loosely coupled — does not import Character.

**Tech Stack:** Python 3.12+, Pydantic v2, pytest, ruff, mypy

---

### Task 1: Enums

**Files:**
- Create: `engine/inventory.py`
- Test: `tests/test_inventory.py`

- [ ] **Step 1: Write failing tests for all enums**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_inventory.py -v`
Expected: ImportError — `engine.inventory` does not exist yet.

- [ ] **Step 3: Implement all enums**

```python
"""Inventory system — items, equipment, weight, attunement.

Simplified SRD 5e rules. Pure deterministic Python (no LLM).
"""

from enum import StrEnum

from pydantic import BaseModel, Field

from engine.character import Size


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ItemType(StrEnum):
    """Item classification."""

    WEAPON = "Weapon"
    ARMOR = "Armor"
    SHIELD = "Shield"
    POTION = "Potion"
    SCROLL = "Scroll"
    ADVENTURING_GEAR = "Adventuring Gear"
    TOOL = "Tool"
    AMMUNITION = "Ammunition"


class Rarity(StrEnum):
    """Magic item rarity tiers."""

    COMMON = "Common"
    UNCOMMON = "Uncommon"
    RARE = "Rare"
    VERY_RARE = "Very Rare"
    LEGENDARY = "Legendary"


class WeaponCategory(StrEnum):
    """Weapon proficiency groups."""

    SIMPLE_MELEE = "Simple Melee"
    SIMPLE_RANGED = "Simple Ranged"
    MARTIAL_MELEE = "Martial Melee"
    MARTIAL_RANGED = "Martial Ranged"


class ArmorCategory(StrEnum):
    """Armor weight classes."""

    LIGHT = "Light"
    MEDIUM = "Medium"
    HEAVY = "Heavy"


class DamageType(StrEnum):
    """Damage classification."""

    SLASHING = "Slashing"
    PIERCING = "Piercing"
    BLUDGEONING = "Bludgeoning"
    FIRE = "Fire"
    COLD = "Cold"
    LIGHTNING = "Lightning"
    POISON = "Poison"
    RADIANT = "Radiant"
    NECROTIC = "Necrotic"


class WeaponProperty(StrEnum):
    """Weapon traits (SRD 5e)."""

    FINESSE = "Finesse"
    VERSATILE = "Versatile"
    THROWN = "Thrown"
    TWO_HANDED = "Two-Handed"
    LIGHT = "Light"
    HEAVY = "Heavy"
    REACH = "Reach"
    LOADING = "Loading"
    AMMUNITION = "Ammunition"


class EquipmentSlot(StrEnum):
    """Body slots for equipped items."""

    MAIN_HAND = "Main Hand"
    OFF_HAND = "Off Hand"
    ARMOR = "Armor"
    HEAD = "Head"
    HANDS = "Hands"
    FEET = "Feet"
    NECK = "Neck"
    RING_1 = "Ring 1"
    RING_2 = "Ring 2"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_inventory.py -v`
Expected: All 7 enum tests PASS.

- [ ] **Step 5: Commit**

```bash
git add engine/inventory.py tests/test_inventory.py
git commit -m "test: add failing tests for inventory enums

feat: implement inventory enums (ItemType, Rarity, DamageType, etc.)"
```

---

### Task 2: Item, Weapon, Armor models

**Files:**
- Modify: `engine/inventory.py`
- Modify: `tests/test_inventory.py`

- [ ] **Step 1: Write failing tests for Item model**

Append to `tests/test_inventory.py`:

```python
from engine.inventory import (
    ArmorCategory,
    DamageType,
    EquipmentSlot,
    Item,
    ItemType,
    Rarity,
    WeaponCategory,
    WeaponProperty,
)


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_inventory.py::TestItem -v`
Expected: ImportError for `Item`.

- [ ] **Step 3: Implement Item model**

Add after the enums section in `engine/inventory.py`:

```python
# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class Item(BaseModel):
    """Base model for all items."""

    name: str = Field(min_length=1)
    item_type: ItemType
    weight: float = Field(ge=0.0)
    value_gp: int = Field(default=0, ge=0)
    rarity: Rarity = Rarity.COMMON
    description: str = ""
    requires_attunement: bool = False
    magical: bool = False
    stackable: bool = False
    quantity: int = Field(default=1, ge=1)
```

- [ ] **Step 4: Run tests to verify Item tests pass**

Run: `uv run pytest tests/test_inventory.py::TestItem -v`
Expected: All 6 Item tests PASS.

- [ ] **Step 5: Write failing tests for Weapon model**

Append to `tests/test_inventory.py`:

```python
from engine.inventory import Weapon


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
```

- [ ] **Step 6: Run tests to verify Weapon tests fail**

Run: `uv run pytest tests/test_inventory.py::TestWeapon -v`
Expected: ImportError for `Weapon`.

- [ ] **Step 7: Implement Weapon model**

Add after `Item` in `engine/inventory.py`:

```python
class Weapon(Item):
    """A weapon with damage and properties."""

    item_type: ItemType = ItemType.WEAPON
    damage_dice: str = Field(min_length=1)
    damage_type: DamageType
    weapon_category: WeaponCategory
    properties: list[WeaponProperty] = Field(default_factory=list)
    range_ft: int | None = Field(default=None, gt=0)
```

- [ ] **Step 8: Run tests to verify Weapon tests pass**

Run: `uv run pytest tests/test_inventory.py::TestWeapon -v`
Expected: All 4 Weapon tests PASS.

- [ ] **Step 9: Write failing tests for Armor model**

Append to `tests/test_inventory.py`:

```python
from engine.inventory import Armor


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
```

- [ ] **Step 10: Run tests to verify Armor tests fail**

Run: `uv run pytest tests/test_inventory.py::TestArmor -v`
Expected: ImportError for `Armor`.

- [ ] **Step 11: Implement Armor model**

Add after `Weapon` in `engine/inventory.py`:

```python
class Armor(Item):
    """Armor with AC and category."""

    item_type: ItemType = ItemType.ARMOR
    armor_category: ArmorCategory
    base_ac: int = Field(ge=10)
    dex_cap: int | None = Field(default=None, ge=0)
    strength_required: int = Field(default=0, ge=0)
    stealth_disadvantage: bool = False
```

- [ ] **Step 12: Run tests to verify Armor tests pass**

Run: `uv run pytest tests/test_inventory.py::TestArmor -v`
Expected: All 5 Armor tests PASS.

- [ ] **Step 13: Commit**

```bash
git add engine/inventory.py tests/test_inventory.py
git commit -m "feat: add Item, Weapon, Armor pydantic models"
```

---

### Task 3: Inventory model and create_inventory

**Files:**
- Modify: `engine/inventory.py`
- Modify: `tests/test_inventory.py`

- [ ] **Step 1: Write failing tests for Inventory model**

Append to `tests/test_inventory.py`:

```python
from engine.inventory import Inventory, create_inventory


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_inventory.py::TestInventory -v`
Expected: ImportError for `Inventory`.

- [ ] **Step 3: Implement Inventory model and create_inventory**

Add after `Armor` in `engine/inventory.py`:

```python
class Inventory(BaseModel):
    """A character's item container."""

    items: list[Item] = Field(default_factory=list)
    equipped: dict[EquipmentSlot, Item] = Field(default_factory=dict)
    attuned: list[Item] = Field(default_factory=list)
    gold: int = Field(default=0, ge=0)


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------


def create_inventory() -> Inventory:
    """Create an empty inventory."""
    return Inventory()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_inventory.py::TestInventory -v`
Expected: All 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add engine/inventory.py tests/test_inventory.py
git commit -m "feat: add Inventory model and create_inventory factory"
```

---

### Task 4: Weight and encumbrance functions

**Files:**
- Modify: `engine/inventory.py`
- Modify: `tests/test_inventory.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_inventory.py`:

```python
from engine.character import Size
from engine.inventory import (
    compute_carrying_capacity,
    compute_total_weight,
    is_encumbered,
)


class TestComputeCarryingCapacity:
    """Carrying capacity = STR × 15, halved for Small."""

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_inventory.py::TestComputeCarryingCapacity tests/test_inventory.py::TestComputeTotalWeight tests/test_inventory.py::TestIsEncumbered -v`
Expected: ImportError for `compute_carrying_capacity`.

- [ ] **Step 3: Implement weight functions**

Add after `create_inventory` in `engine/inventory.py`:

```python
def compute_carrying_capacity(strength: int, size: Size) -> float:
    """Compute carrying capacity in pounds. STR × 15, halved for Small.

    Args:
        strength: Strength score (1-30).
        size: Creature size.

    Returns:
        Maximum weight in pounds.

    Raises:
        ValueError: If strength is out of range.
    """
    if not 1 <= strength <= 30:
        raise ValueError(f"Strength must be 1-30, got {strength}")
    capacity = strength * 15.0
    if size == Size.SMALL:
        capacity /= 2
    return capacity


def compute_total_weight(inventory: Inventory) -> float:
    """Compute total weight of all items (carried + equipped).

    Stackable items multiply weight by quantity.
    """
    total = 0.0
    for item in inventory.items:
        total += item.weight * item.quantity
    for item in inventory.equipped.values():
        total += item.weight * item.quantity
    return total


def is_encumbered(inventory: Inventory, strength: int, size: Size) -> bool:
    """Check if inventory exceeds carrying capacity."""
    return compute_total_weight(inventory) > compute_carrying_capacity(strength, size)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_inventory.py::TestComputeCarryingCapacity tests/test_inventory.py::TestComputeTotalWeight tests/test_inventory.py::TestIsEncumbered -v`
Expected: All 14 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add engine/inventory.py tests/test_inventory.py
git commit -m "feat: add weight and encumbrance functions"
```

---

### Task 5: add_item and remove_item

**Files:**
- Modify: `engine/inventory.py`
- Modify: `tests/test_inventory.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_inventory.py`:

```python
from engine.inventory import add_item, remove_item


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_inventory.py::TestAddItem tests/test_inventory.py::TestRemoveItem -v`
Expected: ImportError for `add_item`.

- [ ] **Step 3: Implement add_item and remove_item**

Add after weight functions in `engine/inventory.py`:

```python
def add_item(inventory: Inventory, item: Item) -> Inventory:
    """Add an item to the inventory. Returns a new Inventory.

    Stackable items with the same name merge quantities.
    """
    new_items = list(inventory.items)
    if item.stackable:
        for i, existing in enumerate(new_items):
            if existing.name == item.name and existing.stackable:
                merged = existing.model_copy(
                    update={"quantity": existing.quantity + item.quantity},
                )
                new_items[i] = merged
                return inventory.model_copy(update={"items": new_items})
    new_items.append(item)
    return inventory.model_copy(update={"items": new_items})


def remove_item(
    inventory: Inventory, item_name: str, quantity: int = 1,
) -> tuple[Inventory, Item]:
    """Remove an item by name. Returns (new_inventory, removed_item).

    For stackable items, decrements quantity. Removes entirely if quantity reaches 0.

    Raises:
        ValueError: If item not found or insufficient quantity.
    """
    new_items = list(inventory.items)
    for i, existing in enumerate(new_items):
        if existing.name == item_name:
            if existing.stackable and existing.quantity > quantity:
                updated = existing.model_copy(
                    update={"quantity": existing.quantity - quantity},
                )
                removed = existing.model_copy(update={"quantity": quantity})
                new_items[i] = updated
                return inventory.model_copy(update={"items": new_items}), removed
            if existing.stackable and existing.quantity < quantity:
                raise ValueError(
                    f"Insufficient quantity of '{item_name}': "
                    f"has {existing.quantity}, need {quantity}"
                )
            # Non-stackable or exact quantity match
            removed = new_items.pop(i)
            if existing.stackable:
                removed = removed.model_copy(update={"quantity": quantity})
            return inventory.model_copy(update={"items": new_items}), removed
    raise ValueError(f"Item '{item_name}' not found in inventory")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_inventory.py::TestAddItem tests/test_inventory.py::TestRemoveItem -v`
Expected: All 10 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add engine/inventory.py tests/test_inventory.py
git commit -m "feat: add add_item and remove_item functions"
```

---

### Task 6: equip_item and unequip_item

**Files:**
- Modify: `engine/inventory.py`
- Modify: `tests/test_inventory.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_inventory.py`:

```python
from engine.inventory import equip_item, unequip_item


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_inventory.py::TestEquipItem tests/test_inventory.py::TestUnequipItem -v`
Expected: ImportError for `equip_item`.

- [ ] **Step 3: Implement equip_item and unequip_item**

Add after `remove_item` in `engine/inventory.py`:

```python
# Slot compatibility: which item types can go in which slots.
_SLOT_COMPATIBILITY: dict[EquipmentSlot, set[ItemType]] = {
    EquipmentSlot.MAIN_HAND: {ItemType.WEAPON},
    EquipmentSlot.OFF_HAND: {ItemType.WEAPON, ItemType.SHIELD},
    EquipmentSlot.ARMOR: {ItemType.ARMOR},
    EquipmentSlot.HEAD: {ItemType.ADVENTURING_GEAR},
    EquipmentSlot.HANDS: {ItemType.ADVENTURING_GEAR},
    EquipmentSlot.FEET: {ItemType.ADVENTURING_GEAR},
    EquipmentSlot.NECK: {ItemType.ADVENTURING_GEAR},
    EquipmentSlot.RING_1: {ItemType.ADVENTURING_GEAR},
    EquipmentSlot.RING_2: {ItemType.ADVENTURING_GEAR},
}


def equip_item(
    inventory: Inventory, item_name: str, slot: EquipmentSlot,
) -> Inventory:
    """Equip an item from the items list into a slot. Returns a new Inventory.

    If the slot is occupied, the previous item goes back to items.
    Two-handed weapons clear the off-hand slot.

    Raises:
        ValueError: If item not found or slot incompatible.
    """
    # Find the item
    item_index: int | None = None
    for i, item in enumerate(inventory.items):
        if item.name == item_name:
            item_index = i
            break
    if item_index is None:
        raise ValueError(f"Item '{item_name}' not found in inventory")

    item = inventory.items[item_index]

    # Validate slot compatibility
    allowed = _SLOT_COMPATIBILITY.get(slot, set())
    if item.item_type not in allowed:
        raise ValueError(
            f"Cannot equip {item.item_type} in {slot} slot"
        )

    new_items = list(inventory.items)
    new_items.pop(item_index)
    new_equipped = dict(inventory.equipped)

    # Return previously equipped item to items
    if slot in new_equipped:
        new_items.append(new_equipped[slot])

    # Two-handed weapons clear off-hand
    if (
        isinstance(item, Weapon)
        and WeaponProperty.TWO_HANDED in item.properties
        and slot == EquipmentSlot.MAIN_HAND
    ):
        off_hand = new_equipped.pop(EquipmentSlot.OFF_HAND, None)
        if off_hand is not None:
            new_items.append(off_hand)

    new_equipped[slot] = item
    return inventory.model_copy(update={"items": new_items, "equipped": new_equipped})


def unequip_item(inventory: Inventory, slot: EquipmentSlot) -> Inventory:
    """Unequip an item from a slot back to items. Returns a new Inventory.

    Raises:
        ValueError: If slot is empty.
    """
    if slot not in inventory.equipped:
        raise ValueError(f"Nothing equipped in {slot} slot")

    new_items = list(inventory.items)
    new_equipped = dict(inventory.equipped)
    item = new_equipped.pop(slot)
    new_items.append(item)
    return inventory.model_copy(update={"items": new_items, "equipped": new_equipped})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_inventory.py::TestEquipItem tests/test_inventory.py::TestUnequipItem -v`
Expected: All 12 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add engine/inventory.py tests/test_inventory.py
git commit -m "feat: add equip_item and unequip_item functions"
```

---

### Task 7: Attunement functions

**Files:**
- Modify: `engine/inventory.py`
- Modify: `tests/test_inventory.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_inventory.py`:

```python
from engine.inventory import attune_item, unattune_item

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_inventory.py::TestAttuneItem tests/test_inventory.py::TestUnattuneItem -v`
Expected: ImportError for `attune_item`.

- [ ] **Step 3: Implement attune_item and unattune_item**

Add after `unequip_item` in `engine/inventory.py`:

```python
MAX_ATTUNEMENT = 3


def attune_item(inventory: Inventory, item_name: str) -> Inventory:
    """Attune to an item. Returns a new Inventory.

    The item must be in the items list or equipped and require attunement.
    Maximum 3 attuned items (SRD rule).

    Raises:
        ValueError: If at cap, item not found, already attuned, or doesn't need attunement.
    """
    if len(inventory.attuned) >= MAX_ATTUNEMENT:
        raise ValueError(
            f"Maximum attunement reached ({MAX_ATTUNEMENT} items)"
        )

    # Check if already attuned
    for attuned in inventory.attuned:
        if attuned.name == item_name:
            raise ValueError(f"'{item_name}' is already attuned")

    # Find the item in items or equipped
    item: Item | None = None
    for i in inventory.items:
        if i.name == item_name:
            item = i
            break
    if item is None:
        for i in inventory.equipped.values():
            if i.name == item_name:
                item = i
                break
    if item is None:
        raise ValueError(f"Item '{item_name}' not found in inventory")

    if not item.requires_attunement:
        raise ValueError(f"'{item_name}' does not require attunement")

    new_attuned = list(inventory.attuned)
    new_attuned.append(item)
    return inventory.model_copy(update={"attuned": new_attuned})


def unattune_item(inventory: Inventory, item_name: str) -> Inventory:
    """Remove attunement from an item. Returns a new Inventory.

    Raises:
        ValueError: If item is not attuned.
    """
    new_attuned = list(inventory.attuned)
    for i, item in enumerate(new_attuned):
        if item.name == item_name:
            new_attuned.pop(i)
            return inventory.model_copy(update={"attuned": new_attuned})
    raise ValueError(f"'{item_name}' is not attuned")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_inventory.py::TestAttuneItem tests/test_inventory.py::TestUnattuneItem -v`
Expected: All 9 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add engine/inventory.py tests/test_inventory.py
git commit -m "feat: add attunement functions (attune_item, unattune_item)"
```

---

### Task 8: AC computation from equipment

**Files:**
- Modify: `engine/inventory.py`
- Modify: `tests/test_inventory.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_inventory.py`:

```python
from engine.inventory import compute_ac_from_equipment


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_inventory.py::TestComputeACFromEquipment -v`
Expected: ImportError for `compute_ac_from_equipment`.

- [ ] **Step 3: Implement compute_ac_from_equipment**

Add after `unattune_item` in `engine/inventory.py`:

```python
def compute_ac_from_equipment(
    equipped: dict[EquipmentSlot, Item], dex_modifier: int,
) -> int:
    """Compute AC from equipped armor and shield.

    - No armor: 10 + DEX mod.
    - Light armor: base_ac + DEX mod.
    - Medium armor: base_ac + min(DEX mod, dex_cap).
    - Heavy armor: base_ac (no DEX).
    - Shield: +2.

    Args:
        equipped: Currently equipped items by slot.
        dex_modifier: Character's DEX ability modifier.

    Returns:
        Computed armor class.
    """
    ac = 10 + dex_modifier

    armor = equipped.get(EquipmentSlot.ARMOR)
    if armor is not None and isinstance(armor, Armor):
        if armor.dex_cap is None:
            # Light armor: full DEX
            ac = armor.base_ac + dex_modifier
        elif armor.dex_cap == 0:
            # Heavy armor: no DEX
            ac = armor.base_ac
        else:
            # Medium armor: capped DEX
            ac = armor.base_ac + min(dex_modifier, armor.dex_cap)

    # Shield bonus
    off_hand = equipped.get(EquipmentSlot.OFF_HAND)
    if off_hand is not None and off_hand.item_type == ItemType.SHIELD:
        ac += 2

    return ac
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_inventory.py::TestComputeACFromEquipment -v`
Expected: All 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add engine/inventory.py tests/test_inventory.py
git commit -m "feat: add compute_ac_from_equipment function"
```

---

### Task 9: Starter item catalog

**Files:**
- Modify: `engine/inventory.py`
- Modify: `tests/test_inventory.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_inventory.py`:

```python
from engine.inventory import ITEM_CATALOG


class TestItemCatalog:
    """Starter item catalog with SRD-accurate entries."""

    def test_catalog_not_empty(self) -> None:
        assert len(ITEM_CATALOG) >= 20

    def test_longsword_stats(self) -> None:
        sword = ITEM_CATALOG["Longsword"]
        assert isinstance(sword, Weapon)
        assert sword.damage_dice == "1d8"
        assert sword.damage_type == DamageType.SLASHING
        assert sword.weapon_category == WeaponCategory.MARTIAL_MELEE
        assert WeaponProperty.VERSATILE in sword.properties
        assert sword.weight == 3.0
        assert sword.value_gp == 15

    def test_chain_mail_stats(self) -> None:
        armor = ITEM_CATALOG["Chain Mail"]
        assert isinstance(armor, Armor)
        assert armor.armor_category == ArmorCategory.HEAVY
        assert armor.base_ac == 16
        assert armor.dex_cap == 0
        assert armor.strength_required == 13
        assert armor.stealth_disadvantage is True

    def test_shield_stats(self) -> None:
        shield = ITEM_CATALOG["Shield"]
        assert shield.item_type == ItemType.SHIELD
        assert shield.weight == 6.0
        assert shield.value_gp == 10

    def test_healing_potion_stats(self) -> None:
        potion = ITEM_CATALOG["Healing Potion"]
        assert potion.item_type == ItemType.POTION
        assert potion.value_gp == 50

    def test_arrows_stackable(self) -> None:
        arrows = ITEM_CATALOG["Arrows"]
        assert arrows.stackable is True
        assert arrows.quantity == 20
        assert arrows.item_type == ItemType.AMMUNITION

    def test_all_weapons_are_weapon_type(self) -> None:
        for name, item in ITEM_CATALOG.items():
            if isinstance(item, Weapon):
                assert item.item_type == ItemType.WEAPON, f"{name} has wrong type"

    def test_all_armors_are_armor_type(self) -> None:
        for name, item in ITEM_CATALOG.items():
            if isinstance(item, Armor):
                assert item.item_type == ItemType.ARMOR, f"{name} has wrong type"

    def test_dagger_has_finesse_light_thrown(self) -> None:
        dagger = ITEM_CATALOG["Dagger"]
        assert isinstance(dagger, Weapon)
        assert set(dagger.properties) == {
            WeaponProperty.FINESSE,
            WeaponProperty.LIGHT,
            WeaponProperty.THROWN,
        }
        assert dagger.range_ft == 20

    def test_plate_armor(self) -> None:
        plate = ITEM_CATALOG["Plate"]
        assert isinstance(plate, Armor)
        assert plate.base_ac == 18
        assert plate.dex_cap == 0
        assert plate.strength_required == 15
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_inventory.py::TestItemCatalog -v`
Expected: ImportError for `ITEM_CATALOG`.

- [ ] **Step 3: Implement the catalog**

Add at the bottom of `engine/inventory.py`:

```python
# ---------------------------------------------------------------------------
# Starter item catalog (SRD 5e simplified)
# ---------------------------------------------------------------------------

ITEM_CATALOG: dict[str, Item] = {
    # --- Weapons ---
    "Dagger": Weapon(
        name="Dagger",
        weight=1.0,
        value_gp=2,
        damage_dice="1d4",
        damage_type=DamageType.PIERCING,
        weapon_category=WeaponCategory.SIMPLE_MELEE,
        properties=[WeaponProperty.FINESSE, WeaponProperty.LIGHT, WeaponProperty.THROWN],
        range_ft=20,
    ),
    "Handaxe": Weapon(
        name="Handaxe",
        weight=2.0,
        value_gp=5,
        damage_dice="1d6",
        damage_type=DamageType.SLASHING,
        weapon_category=WeaponCategory.SIMPLE_MELEE,
        properties=[WeaponProperty.LIGHT, WeaponProperty.THROWN],
        range_ft=20,
    ),
    "Quarterstaff": Weapon(
        name="Quarterstaff",
        weight=4.0,
        value_gp=2,
        damage_dice="1d6",
        damage_type=DamageType.BLUDGEONING,
        weapon_category=WeaponCategory.SIMPLE_MELEE,
        properties=[WeaponProperty.VERSATILE],
    ),
    "Shortsword": Weapon(
        name="Shortsword",
        weight=2.0,
        value_gp=10,
        damage_dice="1d6",
        damage_type=DamageType.PIERCING,
        weapon_category=WeaponCategory.MARTIAL_MELEE,
        properties=[WeaponProperty.FINESSE, WeaponProperty.LIGHT],
    ),
    "Longsword": Weapon(
        name="Longsword",
        weight=3.0,
        value_gp=15,
        damage_dice="1d8",
        damage_type=DamageType.SLASHING,
        weapon_category=WeaponCategory.MARTIAL_MELEE,
        properties=[WeaponProperty.VERSATILE],
    ),
    "Greataxe": Weapon(
        name="Greataxe",
        weight=7.0,
        value_gp=30,
        damage_dice="1d12",
        damage_type=DamageType.SLASHING,
        weapon_category=WeaponCategory.MARTIAL_MELEE,
        properties=[WeaponProperty.HEAVY, WeaponProperty.TWO_HANDED],
    ),
    "Shortbow": Weapon(
        name="Shortbow",
        weight=2.0,
        value_gp=25,
        damage_dice="1d6",
        damage_type=DamageType.PIERCING,
        weapon_category=WeaponCategory.SIMPLE_RANGED,
        properties=[WeaponProperty.AMMUNITION, WeaponProperty.TWO_HANDED],
        range_ft=80,
    ),
    "Longbow": Weapon(
        name="Longbow",
        weight=2.0,
        value_gp=50,
        damage_dice="1d8",
        damage_type=DamageType.PIERCING,
        weapon_category=WeaponCategory.MARTIAL_RANGED,
        properties=[WeaponProperty.AMMUNITION, WeaponProperty.HEAVY, WeaponProperty.TWO_HANDED],
        range_ft=150,
    ),
    # --- Armor ---
    "Padded": Armor(
        name="Padded",
        weight=8.0,
        value_gp=5,
        armor_category=ArmorCategory.LIGHT,
        base_ac=11,
        stealth_disadvantage=True,
    ),
    "Leather": Armor(
        name="Leather",
        weight=10.0,
        value_gp=10,
        armor_category=ArmorCategory.LIGHT,
        base_ac=11,
    ),
    "Studded Leather": Armor(
        name="Studded Leather",
        weight=13.0,
        value_gp=45,
        armor_category=ArmorCategory.LIGHT,
        base_ac=12,
    ),
    "Half Plate": Armor(
        name="Half Plate",
        weight=40.0,
        value_gp=750,
        armor_category=ArmorCategory.MEDIUM,
        base_ac=15,
        dex_cap=2,
        stealth_disadvantage=True,
    ),
    "Chain Mail": Armor(
        name="Chain Mail",
        weight=55.0,
        value_gp=75,
        armor_category=ArmorCategory.HEAVY,
        base_ac=16,
        dex_cap=0,
        strength_required=13,
        stealth_disadvantage=True,
    ),
    "Plate": Armor(
        name="Plate",
        weight=65.0,
        value_gp=1500,
        armor_category=ArmorCategory.HEAVY,
        base_ac=18,
        dex_cap=0,
        strength_required=15,
        stealth_disadvantage=True,
    ),
    # --- Shield ---
    "Shield": Item(
        name="Shield",
        item_type=ItemType.SHIELD,
        weight=6.0,
        value_gp=10,
    ),
    # --- Adventuring Gear ---
    "Backpack": Item(
        name="Backpack",
        item_type=ItemType.ADVENTURING_GEAR,
        weight=5.0,
        value_gp=2,
    ),
    "Rope (50ft)": Item(
        name="Rope (50ft)",
        item_type=ItemType.ADVENTURING_GEAR,
        weight=10.0,
        value_gp=1,
    ),
    "Torch": Item(
        name="Torch",
        item_type=ItemType.ADVENTURING_GEAR,
        weight=1.0,
        value_gp=0,
        stackable=True,
        quantity=1,
    ),
    "Rations (1 day)": Item(
        name="Rations (1 day)",
        item_type=ItemType.ADVENTURING_GEAR,
        weight=2.0,
        value_gp=0,
        stackable=True,
        quantity=1,
    ),
    "Healing Potion": Item(
        name="Healing Potion",
        item_type=ItemType.POTION,
        weight=0.5,
        value_gp=50,
        description="Heals 2d4+2 hit points.",
        stackable=True,
        quantity=1,
    ),
    "Bedroll": Item(
        name="Bedroll",
        item_type=ItemType.ADVENTURING_GEAR,
        weight=7.0,
        value_gp=1,
    ),
    # --- Ammunition ---
    "Arrows": Item(
        name="Arrows",
        item_type=ItemType.AMMUNITION,
        weight=0.05,
        value_gp=1,
        stackable=True,
        quantity=20,
    ),
    "Bolts": Item(
        name="Bolts",
        item_type=ItemType.AMMUNITION,
        weight=0.075,
        value_gp=1,
        stackable=True,
        quantity=20,
    ),
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_inventory.py::TestItemCatalog -v`
Expected: All 10 catalog tests PASS.

- [ ] **Step 5: Commit**

```bash
git add engine/inventory.py tests/test_inventory.py
git commit -m "feat: add starter item catalog (~23 SRD items)"
```

---

### Task 10: Quality gates and final verification

**Files:**
- All: `engine/inventory.py`, `tests/test_inventory.py`

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest tests/test_inventory.py -v`
Expected: All tests PASS (should be ~73 tests).

- [ ] **Step 2: Run full project tests (no regressions)**

Run: `uv run pytest -v`
Expected: All tests across all modules PASS.

- [ ] **Step 3: Check coverage**

Run: `uv run pytest --cov=engine/inventory --cov-report=term-missing tests/test_inventory.py`
Expected: >80% coverage.

- [ ] **Step 4: Run ruff**

Run: `uv run ruff check engine/inventory.py tests/test_inventory.py`
Expected: Clean (no errors).

- [ ] **Step 5: Run mypy**

Run: `uv run mypy engine/inventory.py`
Expected: Clean (no errors).

- [ ] **Step 6: Fix any issues found, re-run checks**

If any check fails, fix the issue and re-run all checks.

- [ ] **Step 7: Final commit (if fixes needed)**

```bash
git add engine/inventory.py tests/test_inventory.py
git commit -m "fix: address lint/type/coverage issues in inventory module"
```
