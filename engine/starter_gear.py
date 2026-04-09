"""Starter equipment kits for new characters.

Each class gets 2-3 pre-built kits. Items must exist in ITEM_CATALOG.
"""

from pydantic import BaseModel, Field

from engine.character import CharacterClass
from engine.inventory import (
    ITEM_CATALOG,
    Armor,
    EquipmentSlot,
    Inventory,
    ItemType,
    Weapon,
    add_item,
    equip_item,
)


class StarterKit(BaseModel):
    """A pre-built equipment set for a character class."""

    name: str
    description: str
    items: list[str]  # names matching keys in ITEM_CATALOG
    gold: int = Field(default=10, ge=0)


# ---------------------------------------------------------------------------
# Starter kits by class
# ---------------------------------------------------------------------------

STARTER_KITS: dict[CharacterClass, list[StarterKit]] = {
    CharacterClass.FIGHTER: [
        StarterKit(
            name="Sword & Shield",
            description="A balanced fighter with strong defense.",
            items=["Longsword", "Shield", "Chain Mail"],
            gold=10,
        ),
        StarterKit(
            name="Two-Handed Warrior",
            description="A heavy-hitting fighter wielding a massive axe.",
            items=["Greataxe", "Chain Mail"],
            gold=10,
        ),
        StarterKit(
            name="Archer",
            description="A ranged fighter with a backup blade.",
            items=["Longbow", "Leather", "Shortsword"],
            gold=15,
        ),
    ],
    CharacterClass.WIZARD: [
        StarterKit(
            name="Classic Arcanist",
            description="A traditional wizard with staff and light armor.",
            items=["Quarterstaff", "Padded"],
            gold=15,
        ),
        StarterKit(
            name="War Scholar",
            description="A combat-ready wizard favoring agility.",
            items=["Dagger", "Leather"],
            gold=20,
        ),
    ],
    CharacterClass.ROGUE: [
        StarterKit(
            name="Shadow Blade",
            description="A dual-wielding rogue built for close combat.",
            items=["Shortsword", "Dagger", "Leather"],
            gold=15,
        ),
        StarterKit(
            name="Scout",
            description="A ranged rogue with a backup dagger.",
            items=["Shortbow", "Dagger", "Leather"],
            gold=10,
        ),
    ],
    CharacterClass.CLERIC: [
        StarterKit(
            name="Battle Priest",
            description="A heavily armored front-line cleric.",
            items=["Longsword", "Chain Mail", "Shield"],
            gold=5,
        ),
        StarterKit(
            name="Healer",
            description="A lightly armored cleric focused on support.",
            items=["Quarterstaff", "Leather"],
            gold=15,
        ),
    ],
    CharacterClass.RANGER: [
        StarterKit(
            name="Woodland Archer",
            description="A ranged ranger with a sidearm.",
            items=["Longbow", "Shortsword", "Leather"],
            gold=10,
        ),
        StarterKit(
            name="Dual Wielder",
            description="A melee ranger wielding blade and dagger.",
            items=["Shortsword", "Dagger", "Leather"],
            gold=15,
        ),
    ],
    CharacterClass.BARBARIAN: [
        StarterKit(
            name="Berserker",
            description="A raging barbarian with a mighty greataxe.",
            items=["Greataxe", "Leather"],
            gold=10,
        ),
        StarterKit(
            name="Savage Fighter",
            description="A barbarian wielding two handaxes.",
            items=["Handaxe", "Handaxe", "Leather"],
            gold=15,
        ),
    ],
}


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def get_starter_kits(char_class: CharacterClass) -> list[StarterKit]:
    """Return available starter kits for a character class."""
    return STARTER_KITS[char_class]


def apply_starter_kit(kit: StarterKit, inventory: Inventory) -> Inventory:
    """Populate inventory with kit items and auto-equip weapon + armor.

    Returns a new Inventory with:
    - All items from the kit added
    - Gold set to the kit amount
    - First weapon auto-equipped to MAIN_HAND
    - First armor auto-equipped to ARMOR slot
    - Shield auto-equipped to OFF_HAND if present

    Args:
        kit: The starter kit to apply.
        inventory: The inventory to populate.

    Returns:
        A new Inventory with kit items added and equipped.
    """
    inv = inventory.model_copy(update={"gold": kit.gold})

    # Add all items from the kit
    for item_name in kit.items:
        catalog_item = ITEM_CATALOG[item_name]
        inv = add_item(inv, catalog_item.model_copy())

    # Auto-equip the first weapon found
    for item in inv.items:
        if isinstance(item, Weapon):
            inv = equip_item(inv, item.name, EquipmentSlot.MAIN_HAND)
            break

    # Auto-equip the first armor found
    for item in inv.items:
        if isinstance(item, Armor):
            inv = equip_item(inv, item.name, EquipmentSlot.ARMOR)
            break

    # Auto-equip shield to off-hand if present (type-based detection)
    for item in inv.items:
        if item.item_type == ItemType.SHIELD:
            inv = equip_item(inv, item.name, EquipmentSlot.OFF_HAND)
            break

    return inv
