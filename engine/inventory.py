"""Inventory system — items, equipment, weight, attunement.

Simplified SRD 5e rules. Pure deterministic Python (no LLM).
"""

from enum import StrEnum

from pydantic import BaseModel, Field

from engine.character import CharacterClass, Size


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
    THUNDER = "Thunder"
    POISON = "Poison"
    RADIANT = "Radiant"
    NECROTIC = "Necrotic"
    FORCE = "Force"


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
    """Add an item to the inventory. Returns a new Inventory (immutable pattern).

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
    """Equip an item from the items list into a slot. Returns a new Inventory (immutable pattern).

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
        if armor.armor_category == ArmorCategory.LIGHT:
            ac = armor.base_ac + dex_modifier
        elif armor.armor_category == ArmorCategory.MEDIUM:
            ac = armor.base_ac + min(dex_modifier, armor.dex_cap or 2)
        elif armor.armor_category == ArmorCategory.HEAVY:
            ac = armor.base_ac

    # Shield bonus
    off_hand = equipped.get(EquipmentSlot.OFF_HAND)
    if off_hand is not None and off_hand.item_type == ItemType.SHIELD:
        ac += 2

    return ac


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
    "Mace": Weapon(
        name="Mace",
        weight=4.0,
        value_gp=5,
        damage_dice="1d6",
        damage_type=DamageType.BLUDGEONING,
        weapon_category=WeaponCategory.SIMPLE_MELEE,
    ),
    "Rapier": Weapon(
        name="Rapier",
        weight=2.0,
        value_gp=25,
        damage_dice="1d8",
        damage_type=DamageType.PIERCING,
        weapon_category=WeaponCategory.MARTIAL_MELEE,
        properties=[WeaponProperty.FINESSE],
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


# ---------------------------------------------------------------------------
# Default weapons by class (for NPC bootstrap)
# ---------------------------------------------------------------------------

_DEFAULT_WEAPON_BY_CLASS: dict[CharacterClass, str] = {
    CharacterClass.FIGHTER: "Longsword",
    CharacterClass.RANGER: "Longsword",
    CharacterClass.ROGUE: "Shortsword",
    CharacterClass.BARBARIAN: "Greataxe",
    CharacterClass.WIZARD: "Quarterstaff",
    CharacterClass.CLERIC: "Mace",
}


def default_weapon_for_class(char_class: CharacterClass) -> Weapon:
    """Return a sensible default weapon for a character class.

    Used when bootstrapping NPC combatants that have no explicit weapon.
    Falls back to Shortsword if the class has no specific mapping.

    Args:
        char_class: The character's class.

    Returns:
        A copy of the appropriate Weapon from the ITEM_CATALOG.
    """
    weapon_name = _DEFAULT_WEAPON_BY_CLASS.get(char_class, "Shortsword")
    item = ITEM_CATALOG[weapon_name]
    assert isinstance(item, Weapon)
    return item.model_copy()
