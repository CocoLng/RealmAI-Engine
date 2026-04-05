# Inventory System Design — `engine/inventory.py`

## Context

Phase 1 of RealmAI-Engine requires a deterministic inventory system (items, equipment, weight, attunement) as the third engine module after dice and character. This module must be pure Python with no LLM calls, follow simplified SRD 5e rules, and integrate loosely with the character system.

## Enums (all `StrEnum`)

| Enum | Values | Purpose |
|------|--------|---------|
| `ItemType` | WEAPON, ARMOR, SHIELD, POTION, SCROLL, ADVENTURING_GEAR, TOOL, AMMUNITION | Categorize items |
| `Rarity` | COMMON, UNCOMMON, RARE, VERY_RARE, LEGENDARY | Magic item tiers |
| `WeaponCategory` | SIMPLE_MELEE, SIMPLE_RANGED, MARTIAL_MELEE, MARTIAL_RANGED | Weapon proficiency groups |
| `ArmorCategory` | LIGHT, MEDIUM, HEAVY | Armor class groups |
| `DamageType` | SLASHING, PIERCING, BLUDGEONING, FIRE, COLD, LIGHTNING, POISON, RADIANT, NECROTIC | Damage classification |
| `WeaponProperty` | FINESSE, VERSATILE, THROWN, TWO_HANDED, LIGHT, HEAVY, REACH, LOADING, AMMUNITION | Weapon traits |
| `EquipmentSlot` | MAIN_HAND, OFF_HAND, ARMOR, HEAD, HANDS, FEET, NECK, RING_1, RING_2 | Body slots for equipped items |

## Pydantic Models

### `Item(BaseModel)`

Base model for all items.

| Field | Type | Constraints | Default |
|-------|------|-------------|---------|
| `name` | `str` | `min_length=1` | required |
| `item_type` | `ItemType` | — | required |
| `weight` | `float` | `>= 0.0` | required |
| `value_gp` | `int` | `>= 0` | `0` |
| `rarity` | `Rarity` | — | `COMMON` |
| `description` | `str` | — | `""` |
| `requires_attunement` | `bool` | — | `False` |
| `magical` | `bool` | — | `False` |
| `stackable` | `bool` | — | `False` |
| `quantity` | `int` | `>= 1` | `1` |

### `Weapon(Item)`

Inherits Item, `item_type` forced to `WEAPON`.

| Field | Type | Constraints | Default |
|-------|------|-------------|---------|
| `damage_dice` | `str` | valid dice expr | required |
| `damage_type` | `DamageType` | — | required |
| `weapon_category` | `WeaponCategory` | — | required |
| `properties` | `list[WeaponProperty]` | — | `[]` |
| `range_ft` | `int \| None` | `> 0` if set | `None` |

### `Armor(Item)`

Inherits Item, `item_type` forced to `ARMOR`.

| Field | Type | Constraints | Default |
|-------|------|-------------|---------|
| `armor_category` | `ArmorCategory` | — | required |
| `base_ac` | `int` | `>= 10` | required |
| `dex_cap` | `int \| None` | `None`=unlimited, `0`=no DEX, `2`=medium cap | `None` |
| `strength_required` | `int` | `>= 0` | `0` |
| `stealth_disadvantage` | `bool` | — | `False` |

### `Inventory(BaseModel)`

Container for a character's possessions.

| Field | Type | Default |
|-------|------|---------|
| `items` | `list[Item]` | `[]` |
| `equipped` | `dict[EquipmentSlot, Item]` | `{}` |
| `attuned` | `list[Item]` | `[]` |
| `gold` | `int` (>= 0) | `0` |

## Pure Functions

### Weight & Encumbrance

- **`compute_carrying_capacity(strength: int, size: Size) -> float`**
  - Formula: STR × 15. Halved for SMALL size.
  - Validates strength 1-30.

- **`compute_total_weight(inventory: Inventory) -> float`**
  - Sum of `item.weight * item.quantity` for all items (including equipped).

- **`is_encumbered(inventory: Inventory, strength: int, size: Size) -> bool`**
  - True if total weight > carrying capacity.

### Item Management (all return new Inventory)

- **`create_inventory() -> Inventory`** — Factory for empty inventory.

- **`add_item(inventory: Inventory, item: Item) -> Inventory`**
  - Adds item to items list. If stackable and same name exists, increments quantity.

- **`remove_item(inventory: Inventory, item_name: str, quantity: int = 1) -> tuple[Inventory, Item]`**
  - Removes item by name. Decrements quantity for stackable items.
  - Raises `ValueError` if item not found or insufficient quantity.
  - Returns (new_inventory, removed_item).

### Equipment (all return new Inventory)

- **`equip_item(inventory: Inventory, item_name: str, slot: EquipmentSlot) -> Inventory`**
  - Validates: item exists in items, slot is compatible with item type.
  - Slot compatibility: WEAPON → MAIN_HAND/OFF_HAND, ARMOR → ARMOR, SHIELD → OFF_HAND.
  - If slot occupied, previous item returns to items list.
  - TWO_HANDED weapons occupy MAIN_HAND and clear OFF_HAND.

- **`unequip_item(inventory: Inventory, slot: EquipmentSlot) -> Inventory`**
  - Moves equipped item back to items list.
  - Raises `ValueError` if slot empty.

### Attunement

- **`attune_item(inventory: Inventory, item_name: str) -> Inventory`**
  - Max 3 attuned items (SRD rule).
  - Item must have `requires_attunement=True`.
  - Raises `ValueError` if at cap, item not found, or doesn't require attunement.

- **`unattune_item(inventory: Inventory, item_name: str) -> Inventory`**
  - Removes from attuned list.
  - Raises `ValueError` if not attuned.

### AC Computation

- **`compute_ac_from_equipment(equipped: dict[EquipmentSlot, Item], dex_modifier: int) -> int`**
  - No armor: 10 + DEX mod.
  - Light armor: base_ac + DEX mod.
  - Medium armor: base_ac + min(DEX mod, 2).
  - Heavy armor: base_ac (no DEX).
  - Shield in OFF_HAND: +2.
  - Takes `dex_modifier: int` (not Character) for loose coupling.

## Starter Item Catalog

Module-level `ITEM_CATALOG: dict[str, Item]` with ~25 items:

### Weapons (~8)
| Name | Category | Damage | Properties | Weight | GP |
|------|----------|--------|------------|--------|----|
| Dagger | Simple Melee | 1d4 piercing | finesse, light, thrown | 1.0 | 2 |
| Handaxe | Simple Melee | 1d6 slashing | light, thrown | 2.0 | 5 |
| Quarterstaff | Simple Melee | 1d6 bludgeoning | versatile | 4.0 | 2 |
| Shortsword | Martial Melee | 1d6 piercing | finesse, light | 2.0 | 10 |
| Longsword | Martial Melee | 1d8 slashing | versatile | 3.0 | 15 |
| Greataxe | Martial Melee | 1d12 slashing | heavy, two-handed | 7.0 | 30 |
| Shortbow | Simple Ranged | 1d6 piercing | ammunition, two-handed | 2.0 | 25 |
| Longbow | Martial Ranged | 1d8 piercing | ammunition, heavy, two-handed | 2.0 | 50 |

### Armor (~7)
| Name | Category | Base AC | DEX Cap | STR Req | Stealth Disadv | Weight | GP |
|------|----------|---------|---------|---------|----------------|--------|----|
| Padded | Light | 11 | None | 0 | Yes | 8.0 | 5 |
| Leather | Light | 11 | None | 0 | No | 10.0 | 10 |
| Studded Leather | Light | 12 | None | 0 | No | 13.0 | 45 |
| Chain Mail | Heavy | 16 | 0 | 13 | Yes | 55.0 | 75 |
| Half Plate | Medium | 15 | 2 | 0 | Yes | 40.0 | 750 |
| Plate | Heavy | 18 | 0 | 15 | Yes | 65.0 | 1500 |
| Shield | — | +2 | — | — | No | 6.0 | 10 |

### Adventuring Gear & Ammo (~8)
Backpack, Rope (50ft), Torch, Rations (1 day), Healing Potion, Bedroll, Arrows (20), Bolts (20).
All with SRD-accurate weight and GP values. Ammo and rations are `stackable=True`.

## Integration Points

- **`engine/character.py`**: `compute_ac()` can later delegate to `compute_ac_from_equipment()`. Character model will eventually gain an `inventory: Inventory` field (not in this PR).
- **`engine/combat.py`** (future): Will read equipped weapons for attack/damage rolls.
- **`engine/spells.py`** (future): Spell scrolls and components stored as inventory items.

## Verification Plan

1. `uv run pytest tests/test_inventory.py -v` — all tests pass
2. `uv run pytest --cov=engine/inventory --cov-report=term-missing` — >80% coverage
3. `uv run ruff check engine/inventory.py tests/test_inventory.py` — clean
4. `uv run mypy engine/inventory.py tests/test_inventory.py` — clean
