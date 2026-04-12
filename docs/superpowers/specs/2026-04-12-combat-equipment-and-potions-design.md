# Combat Equipment & Potions Design

**Date:** 2026-04-12
**Scope:** Bugfixes (combat gelé) + changement d'arme en combat + utilisation de potions

---

## Problem

During a live campaign (2026-04-12), combat is completely stuck:

1. **Player weapon not detected:** The player has a Longsword equipped but the system rejects attacks with "Attack requires a weapon" because the Interpreter returns `weapon_name=null` when the player doesn't explicitly name a weapon.
2. **NPC cannot attack:** The NPC's `stat_block` is not transferred from the `NPC` model to the `Combatant`, so the NPC brain finds no attacks and errors with "has no stat-block attack named None".
3. **No weapon swap in combat:** Players cannot change equipment mid-combat (e.g., switch from sword to bow).
4. **No potion use in combat:** Players cannot drink potions during their turn.

---

## Design

### Part A: Bugfixes

#### Bug 1 — Weapon auto-resolution

**File:** `bot/action_pipeline.py`, method `_continue_from_resolution()`

After entity resolution (line ~354) and before validation (line ~356), insert:

```python
if (
    interpreted.action_type == ActionType.ATTACK
    and interpreted.weapon_name is None
    and self.inventory is not None
):
    main_hand = self.inventory.equipped.get(EquipmentSlot.MAIN_HAND)
    if main_hand is not None and isinstance(main_hand, Weapon):
        interpreted = interpreted.model_copy(
            update={"weapon_name": main_hand.name},
        )
```

- Only resolves from MAIN_HAND (5e: main hand is default attack weapon)
- Does NOT override if the player explicitly named a weapon
- No new imports needed (`EquipmentSlot`, `Weapon` already imported at line 70)

#### Bug 2 — NPC stat_block passthrough

**File:** `bot/combat_entry.py`, function `build_npc_combatant()`

Add `stat_block=npc.stat_block` to the `Combatant(...)` constructor:

```python
return Combatant(
    name=npc.name,
    side=CombatSide.ENEMY,
    character=char,
    inventory=inv,
    stat_block=npc.stat_block,
)
```

When `npc.stat_block is None`, the field stays `None` — the legacy fallback (character + inventory) still works.

---

### Part B: New ActionType — EQUIP

#### Rules

- **Free action**, once per turn (does not consume the standard action)
- New flag `weapon_swapped_this_turn: bool = False` on `ActionBudget`
- Reset to `False` in `ActionBudget.reset_for_new_turn()`
- The turn does NOT advance after EQUIP — the player can still attack, cast, etc.

#### ActionType addition

**File:** `engine/validators.py`

```python
class ActionType(StrEnum):
    # ... existing ...
    EQUIP = "Equip"
```

#### Validator — `validate_equip()`

**File:** `engine/validators.py`

Checks:
1. Common checks (actor exists, alive, it's their turn, not incapacitated)
2. `action.item_name` is not None
3. Item exists in `actor.inventory.items` (unequipped items)
4. Item is a `Weapon` (or `Shield` for OFF_HAND, but V1 = MAIN_HAND only)
5. `actor.action_budget.weapon_swapped_this_turn` is `False`

Register in the `validators` dispatch dict inside `validate_action()`.

#### Mechanics resolution — `_resolve_equip()`

**File:** `bot/action_pipeline.py`

1. If MAIN_HAND is occupied: `unequip_item(inv, EquipmentSlot.MAIN_HAND)` — old weapon returns to items
2. `equip_item(inv, new_weapon_name, EquipmentSlot.MAIN_HAND)` — new weapon in hand
3. Set `combatant.action_budget.weapon_swapped_this_turn = True`
4. Return `MechanicsOutcome(summary="Fighty dégaine Shortbow")`

Note: `is_free_action` lives on `ActionPipelineResult`, not `MechanicsOutcome`. The pipeline sets it based on `action_type == ActionType.EQUIP`.

#### Pipeline result flag

**File:** `bot/action_pipeline.py`

Add `is_free_action: bool = False` to `ActionPipelineResult`. Set to `True` for EQUIP actions.

---

### Part C: USE_ITEM — Potions in combat

#### Potion model enhancement

**File:** `engine/inventory.py`

Add a structured field on `Item`:

```python
class Item(BaseModel):
    # ... existing fields ...
    heal_dice: str | None = None  # e.g., "2d4+2" for healing potions
```

Update `ITEM_CATALOG` entries for potions:

```python
"Healing Potion": Item(
    name="Healing Potion",
    item_type=ItemType.POTION,
    weight=0.5,
    value_gp=50,
    description="Heals 2d4+2 hit points.",
    stackable=True,
    quantity=1,
    heal_dice="2d4+2",
),
```

#### Mechanics resolution — `_resolve_use_item()`

**File:** `bot/action_pipeline.py`

1. Find the potion in `combatant.inventory.items`
2. Verify `item.item_type == ItemType.POTION`
3. If `item.heal_dice` is set: roll via `engine.dice`, apply healing `min(hp + heal, max_hp)`
4. Remove potion from inventory: `remove_item(inv, potion_name)` (handles stackable quantity)
5. Set `combatant.action_budget.action_used = True`
6. Return `MechanicsOutcome(summary="Fighty boit Healing Potion — récupère 8 PV")`

The existing `validate_use_item()` already validates item presence. Add a check that `action_budget.action_used` is `False` (prevents using a potion after attacking in the same turn).

**V1 scope:** Only healing potions are supported mechanically (`heal_dice` field). Non-healing potions (buffs, etc.) are out of scope — the validator should reject them with a clear message until a future iteration adds support.

---

### Part D: Discord UI

#### CombatActionView — new buttons

**File:** `bot/views/combat_action_view.py`

| Row | Buttons |
|-----|---------|
| 0 | ⚔️ Attaquer, ✨ Sort, **🧪 Potion** |
| 1 | 🛡️ Défendre, 🏃 Fuir, 🧭 Déplacer, **🗡️ Équiper** |

Constructor gains two new parameters:
- `potion_names: list[str]` — potions in inventory
- `equippable_names: list[str]` — unequipped weapons in inventory

Disable logic:
- 🧪 Potion: `disabled = not potion_names`
- 🗡️ Équiper: `disabled = not equippable_names`

Button callbacks:
- 🧪 Potion → open `PotionSelectView` (ephemeral)
- 🗡️ Équiper → open `EquipSelectView` (ephemeral)

#### PotionSelectView

**New file:** `bot/views/potion_select_view.py`

Follows `SpellSelectView` pattern exactly:
- `LoggedView` subclass, `timeout=60.0`
- Constructor: `option_names: list[str]`, `user_id: int`, `on_choice: Callable[[str], Awaitable[None]]`
- Single `discord.ui.Select` with potion names
- Options show: name + description + "(x3)" quantity suffix if > 1
- `on_choice(potion_name)` dispatches `InterpretedAction(action_type=USE_ITEM, item_name=potion_name)`

#### EquipSelectView

**New file:** `bot/views/equip_select_view.py`

Same pattern:
- Lists unequipped weapons from inventory
- Options show: name + damage dice + damage type (e.g., "Shortbow — 1d6 perçant")
- `on_choice(weapon_name)` dispatches `InterpretedAction(action_type=EQUIP, item_name=weapon_name)`

---

### Part E: TurnManager — free action handling

**File:** `bot/combat_turn_manager.py`

#### Computing button data

In `_prompt_pc_turn()`, add:

```python
potion_names = [
    i.name for i in combatant.inventory.items
    if i.item_type == ItemType.POTION
]
equippable_names = [
    i.name for i in combatant.inventory.items
    if isinstance(i, Weapon)
]
```

Pass these to `CombatActionView(...)`.

#### Free action flow

In `dispatch_action()` (or `_render_pipeline_result()`):

```
if result.is_free_action:
    post mechanics summary + short narration
    re-build CombatActionView with UPDATED inventory state:
      - weapon_swapped_this_turn=True → Équiper button disabled
      - potion_names/equippable_names recomputed from current inventory
    re-prompt the same combatant with new view
    return  # do NOT call on_action_resolved() / advance_turn()
else:
    existing flow (post result, advance turn)
```

---

### Part F: Interpreter update

**File:** `ai/prompts/system_interpreter.txt`

Add `EQUIP` to the ActionType list:

```
- "Equip" — change equipped weapon or gear during combat. 
  Free action, once per turn. Set `item_name` to the item to equip.
```

This allows free-text equip commands via `@bot` in addition to the button.

---

## Files summary

| File | Change |
|------|--------|
| `engine/validators.py` | Add `ActionType.EQUIP`, `validate_equip()` |
| `engine/combat.py` | Add `weapon_swapped_this_turn` to `ActionBudget` |
| `engine/inventory.py` | Add `heal_dice` field on `Item`, update catalog |
| `bot/action_pipeline.py` | Weapon auto-resolve, `_resolve_equip()`, `_resolve_use_item()`, `is_free_action` flag |
| `bot/combat_entry.py` | Pass `stat_block=npc.stat_block` |
| `bot/combat_turn_manager.py` | Compute potion/equip lists, free action flow |
| `bot/views/combat_action_view.py` | Add 🧪 Potion + 🗡️ Équiper buttons |
| `bot/views/potion_select_view.py` | **NEW** — potion selection dropdown |
| `bot/views/equip_select_view.py` | **NEW** — weapon selection dropdown |
| `ai/prompts/system_interpreter.txt` | Add EQUIP ActionType |

## Verification

```bash
# Unit tests
uv run pytest tests/bot/test_combat_entry.py -v           # stat_block passthrough
uv run pytest tests/bot/test_action_pipeline.py -v         # weapon auto-resolve + resolve_equip + resolve_use_item
uv run pytest tests/engine/test_validators.py -v           # validate_equip
uv run pytest tests/engine/test_inventory.py -v            # heal_dice field

# Full suite
uv run pytest tests/ -x

# Lint + types
uv run ruff check .
uv run mypy .

# Live test on Discord
# 1. Start campaign, enter combat
# 2. Attack without naming weapon → should work (auto-resolve)
# 3. Click 🗡️ Équiper → select bow → attack with bow
# 4. Click 🧪 Potion → select healing potion → verify HP restored
# 5. Verify NPC can attack (stat_block fix)
```
