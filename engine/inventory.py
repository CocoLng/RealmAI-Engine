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
