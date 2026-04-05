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


class Weapon(Item):
    """A weapon with damage and properties."""

    item_type: ItemType = ItemType.WEAPON
    damage_dice: str = Field(min_length=1)
    damage_type: DamageType
    weapon_category: WeaponCategory
    properties: list[WeaponProperty] = Field(default_factory=list)
    range_ft: int | None = Field(default=None, gt=0)


class Armor(Item):
    """Armor with AC and category."""

    item_type: ItemType = ItemType.ARMOR
    armor_category: ArmorCategory
    base_ac: int = Field(ge=10)
    dex_cap: int | None = Field(default=None, ge=0)
    strength_required: int = Field(default=0, ge=0)
    stealth_disadvantage: bool = False


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


def compute_carrying_capacity(strength: int, size: Size) -> float:
    """Compute carrying capacity in pounds. STR x 15, halved for Small.

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
