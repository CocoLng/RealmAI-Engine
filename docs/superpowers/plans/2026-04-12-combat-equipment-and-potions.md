# Combat Equipment & Potions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix frozen combat (weapon auto-resolve + NPC stat_block) and add weapon swap + potion use in combat.

**Architecture:** Two bugfixes to unblock combat, then a new EQUIP ActionType (free action, 1x/turn) with validate_equip + _resolve_equip, USE_ITEM for potions with heal_dice on Item model, two new Discord select views following SpellSelectView pattern, and a free-action flow in TurnManager that re-prompts instead of advancing the turn.

**Tech Stack:** Python 3.12, Pydantic v2, discord.py 2.4+, pytest, engine/dice for heal rolls.

**Spec:** `docs/superpowers/specs/2026-04-12-combat-equipment-and-potions-design.md`

---

### Task 1: Bug fix — NPC stat_block passthrough

**Files:**
- Modify: `bot/combat_entry.py:194-199`
- Test: `tests/bot/test_combat_entry.py`

- [ ] **Step 1: Write failing test**

In `tests/bot/test_combat_entry.py`, add at the end of the file:

```python
from bot.combat_entry import build_npc_combatant


def test_build_npc_combatant_transfers_stat_block() -> None:
    """When NPC has a stat_block, the Combatant must carry it."""
    npc = _make_boss_npc("Wyvern")
    combatant = build_npc_combatant(npc)
    assert combatant.stat_block is npc.stat_block
    assert combatant.stat_block is not None


def test_build_npc_combatant_none_stat_block() -> None:
    """When NPC has no stat_block, the Combatant has None."""
    npc = _make_commoner("Villager")
    combatant = build_npc_combatant(npc)
    assert combatant.stat_block is None
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `uv run pytest tests/bot/test_combat_entry.py::test_build_npc_combatant_transfers_stat_block -v`
Expected: FAIL — `assert combatant.stat_block is npc.stat_block` fails because stat_block is None.

- [ ] **Step 3: Fix build_npc_combatant**

In `bot/combat_entry.py`, replace lines 194-199:

```python
    return Combatant(
        name=npc.name,
        side=CombatSide.ENEMY,
        character=char,
        inventory=inv,
    )
```

with:

```python
    return Combatant(
        name=npc.name,
        side=CombatSide.ENEMY,
        character=char,
        inventory=inv,
        stat_block=npc.stat_block,
    )
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `uv run pytest tests/bot/test_combat_entry.py::test_build_npc_combatant_transfers_stat_block tests/bot/test_combat_entry.py::test_build_npc_combatant_none_stat_block -v`
Expected: Both PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/combat_entry.py tests/bot/test_combat_entry.py
git commit -m "fix(combat): pass NPC stat_block to Combatant on combat entry"
```

---

### Task 2: Bug fix — Weapon auto-resolution for ATTACK

**Files:**
- Modify: `bot/action_pipeline.py:354-356`
- Test: `tests/bot/test_action_pipeline.py`

- [ ] **Step 1: Write failing tests**

In `tests/bot/test_action_pipeline.py`, add at the end (or in a new `class TestWeaponAutoResolve`):

```python
from engine.inventory import (
    DamageType,
    EquipmentSlot,
    Inventory,
    Weapon,
    WeaponCategory,
)


def _make_longsword() -> Weapon:
    return Weapon(
        name="Longsword",
        weight=3.0,
        damage_dice="1d8",
        damage_type=DamageType.SLASHING,
        weapon_category=WeaponCategory.MARTIAL_MELEE,
    )


class TestWeaponAutoResolve:
    """weapon_name auto-filled from MAIN_HAND when player omits it."""

    def test_auto_resolves_main_hand_weapon(self) -> None:
        """ATTACK + weapon_name=None + Longsword in MAIN_HAND → weapon_name='Longsword'."""
        from bot.action_pipeline import ActionPipeline

        sword = _make_longsword()
        inv = Inventory(items=[], equipped={EquipmentSlot.MAIN_HAND: sword})
        # We only need to test the auto-resolve logic, not the full pipeline.
        # Instantiate with minimal args; the test checks _continue_from_resolution
        # indirectly by calling _validate after the patch point.
        # Instead, test the auto-resolve snippet directly via a helper.
        assert ActionPipeline._auto_resolve_weapon_name(None, inv) == "Longsword"

    def test_no_override_when_weapon_specified(self) -> None:
        """Player explicitly named 'Dagger' → keeps 'Dagger'."""
        from bot.action_pipeline import ActionPipeline

        sword = _make_longsword()
        inv = Inventory(items=[], equipped={EquipmentSlot.MAIN_HAND: sword})
        assert ActionPipeline._auto_resolve_weapon_name("Dagger", inv) == "Dagger"

    def test_no_resolve_empty_main_hand(self) -> None:
        """No weapon in MAIN_HAND → stays None."""
        from bot.action_pipeline import ActionPipeline

        inv = Inventory(items=[], equipped={})
        assert ActionPipeline._auto_resolve_weapon_name(None, inv) is None

    def test_no_resolve_non_weapon_in_main_hand(self) -> None:
        """Shield in MAIN_HAND → stays None (not a Weapon instance)."""
        from bot.action_pipeline import ActionPipeline
        from engine.inventory import Item, ItemType

        shield = Item(name="Shield", item_type=ItemType.SHIELD, weight=6.0)
        inv = Inventory(items=[], equipped={EquipmentSlot.MAIN_HAND: shield})
        assert ActionPipeline._auto_resolve_weapon_name(None, inv) is None

    def test_no_resolve_no_inventory(self) -> None:
        """inventory is None → stays None, no crash."""
        from bot.action_pipeline import ActionPipeline

        assert ActionPipeline._auto_resolve_weapon_name(None, None) is None
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `uv run pytest tests/bot/test_action_pipeline.py::TestWeaponAutoResolve -v`
Expected: FAIL — `_auto_resolve_weapon_name` does not exist.

- [ ] **Step 3: Add static helper + call it in the pipeline**

In `bot/action_pipeline.py`, add a static method to `ActionPipeline` (inside the class, before `_validate`):

```python
    @staticmethod
    def _auto_resolve_weapon_name(
        weapon_name: str | None,
        inventory: Inventory | None,
    ) -> str | None:
        """Return the MAIN_HAND weapon name when the player omits it."""
        if weapon_name is not None:
            return weapon_name
        if inventory is None:
            return None
        main_hand = inventory.equipped.get(EquipmentSlot.MAIN_HAND)
        if main_hand is not None and isinstance(main_hand, Weapon):
            return main_hand.name
        return None
```

Then in `_continue_from_resolution`, after the entity resolution patching block (line ~354, after the `elif` for `item_name`) and before the `await self._emit(progress_callback, PipelinePhase.VALIDATING)` line, insert:

```python
        # --- Auto-resolve weapon for ATTACK when player omitted weapon name ---
        if interpreted.action_type == ActionType.ATTACK:
            resolved_weapon = self._auto_resolve_weapon_name(
                interpreted.weapon_name, self.inventory,
            )
            if resolved_weapon != interpreted.weapon_name:
                interpreted = interpreted.model_copy(
                    update={"weapon_name": resolved_weapon},
                )
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `uv run pytest tests/bot/test_action_pipeline.py::TestWeaponAutoResolve -v`
Expected: All 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/action_pipeline.py tests/bot/test_action_pipeline.py
git commit -m "fix(combat): auto-resolve MAIN_HAND weapon when player omits weapon_name"
```

---

### Task 3: ActionBudget — add weapon_swapped_this_turn

**Files:**
- Modify: `engine/combat.py:94-119`
- Test: `tests/engine/test_combat.py`

- [ ] **Step 1: Write failing test**

In `tests/engine/test_combat.py`, add:

```python
class TestActionBudgetWeaponSwap:
    """weapon_swapped_this_turn field on ActionBudget."""

    def test_defaults_false(self) -> None:
        budget = ActionBudget()
        assert budget.weapon_swapped_this_turn is False

    def test_reset_clears_flag(self) -> None:
        budget = ActionBudget(weapon_swapped_this_turn=True)
        budget.reset_for_new_turn(30)
        assert budget.weapon_swapped_this_turn is False
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `uv run pytest tests/engine/test_combat.py::TestActionBudgetWeaponSwap -v`
Expected: FAIL — `weapon_swapped_this_turn` is not a valid field.

- [ ] **Step 3: Add the field**

In `engine/combat.py`, in class `ActionBudget`, after line 110 (`disengaged_this_turn: bool = False`), add:

```python
    weapon_swapped_this_turn: bool = False
    """Set by EQUIP free action (once per turn). Prevents a second swap."""
```

In `reset_for_new_turn`, after `self.disengaged_this_turn = False`, add:

```python
        self.weapon_swapped_this_turn = False
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `uv run pytest tests/engine/test_combat.py::TestActionBudgetWeaponSwap -v`
Expected: Both PASS.

- [ ] **Step 5: Commit**

```bash
git add engine/combat.py tests/engine/test_combat.py
git commit -m "feat(combat): add weapon_swapped_this_turn to ActionBudget"
```

---

### Task 4: ActionType.EQUIP + validate_equip

**Files:**
- Modify: `engine/validators.py:25-51` (ActionType), `engine/validators.py:190-198` (dispatcher), new function
- Test: `tests/engine/test_validators.py`

- [ ] **Step 1: Write failing tests**

In `tests/engine/test_validators.py`, add a new test class. First check existing imports at the top — you'll need `Weapon`, `DamageType`, `WeaponCategory`, `EquipmentSlot` from `engine.inventory`. Add them if missing.

```python
from engine.inventory import (
    DamageType,
    EquipmentSlot,
    Weapon,
    WeaponCategory,
)


class TestValidateEquip:
    """Validate EQUIP free-action checks."""

    def test_valid_equip(self, combat_state: CombatState) -> None:
        """Equip an unequipped weapon from inventory."""
        actor = combat_state.combatants[0]  # Arden
        bow = Weapon(
            name="Shortbow",
            weight=2.0,
            damage_dice="1d6",
            damage_type=DamageType.PIERCING,
            weapon_category=WeaponCategory.SIMPLE_RANGED,
        )
        actor.inventory.items.append(bow)
        action = Action(
            actor_name="Arden",
            action_type=ActionType.EQUIP,
            item_name="Shortbow",
        )
        result = validate_action(action, combat_state)
        assert result.is_valid

    def test_equip_no_item_name(self, combat_state: CombatState) -> None:
        action = Action(
            actor_name="Arden",
            action_type=ActionType.EQUIP,
            item_name=None,
        )
        result = validate_action(action, combat_state)
        assert not result.is_valid
        assert "item" in (result.error_message or "").lower()

    def test_equip_item_not_in_inventory(self, combat_state: CombatState) -> None:
        action = Action(
            actor_name="Arden",
            action_type=ActionType.EQUIP,
            item_name="Ghost Sword",
        )
        result = validate_action(action, combat_state)
        assert not result.is_valid

    def test_equip_already_swapped(self, combat_state: CombatState) -> None:
        """Second swap in same turn is rejected."""
        actor = combat_state.combatants[0]
        bow = Weapon(
            name="Shortbow",
            weight=2.0,
            damage_dice="1d6",
            damage_type=DamageType.PIERCING,
            weapon_category=WeaponCategory.SIMPLE_RANGED,
        )
        actor.inventory.items.append(bow)
        actor.action_budget.weapon_swapped_this_turn = True
        action = Action(
            actor_name="Arden",
            action_type=ActionType.EQUIP,
            item_name="Shortbow",
        )
        result = validate_action(action, combat_state)
        assert not result.is_valid
        assert "déjà" in (result.error_message or "").lower()

    def test_equip_non_weapon_rejected(self, combat_state: CombatState) -> None:
        """Cannot equip a non-weapon item (potion, gear)."""
        from engine.inventory import Item, ItemType

        actor = combat_state.combatants[0]
        potion = Item(
            name="Healing Potion",
            item_type=ItemType.POTION,
            weight=0.5,
        )
        actor.inventory.items.append(potion)
        action = Action(
            actor_name="Arden",
            action_type=ActionType.EQUIP,
            item_name="Healing Potion",
        )
        result = validate_action(action, combat_state)
        assert not result.is_valid
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `uv run pytest tests/engine/test_validators.py::TestValidateEquip -v`
Expected: FAIL — `ActionType.EQUIP` does not exist.

- [ ] **Step 3: Add ActionType.EQUIP and validate_equip**

In `engine/validators.py`, add to `ActionType` enum (after `USE_ITEM = "Use Item"`, before the `# Exploration` comment):

```python
    EQUIP = "Equip"
```

Add `validate_equip` function (after `validate_use_item`, before the exploration validators section):

```python
def validate_equip(action: Action, state: CombatState) -> ValidationResult:
    """Validate an equip free-action. Common + weapon in inventory + not already swapped."""
    common = _validate_common(action, state)
    if common is not None:
        return common

    actor = _find_combatant(action.actor_name, state)
    assert actor is not None

    if action.item_name is None:
        return ValidationResult(
            is_valid=False,
            error_message="Equip requires an item name",
        )

    if actor.action_budget.weapon_swapped_this_turn:
        return ValidationResult(
            is_valid=False,
            error_message=f"'{action.actor_name}' a déjà changé d'arme ce tour.",
        )

    # Check item is in unequipped inventory
    matching = [i for i in actor.inventory.items if i.name == action.item_name]
    if not matching:
        return ValidationResult(
            is_valid=False,
            error_message=f"Item '{action.item_name}' not found in inventory",
        )

    item = matching[0]
    if not isinstance(item, Weapon):
        return ValidationResult(
            is_valid=False,
            error_message=f"'{action.item_name}' n'est pas une arme équipable.",
        )

    return ValidationResult(is_valid=True)
```

Register in the dispatcher dict (line ~190-198), add:

```python
        ActionType.EQUIP: validate_equip,
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `uv run pytest tests/engine/test_validators.py::TestValidateEquip -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add engine/validators.py tests/engine/test_validators.py
git commit -m "feat(combat): add ActionType.EQUIP and validate_equip"
```

---

### Task 5: Item.heal_dice field + ITEM_CATALOG update

**Files:**
- Modify: `engine/inventory.py:107-119` (Item class), `engine/inventory.py:613-621` (catalog)
- Test: `tests/engine/test_inventory.py`

- [ ] **Step 1: Write failing test**

In `tests/engine/test_inventory.py`, add:

```python
class TestHealDice:
    """Item.heal_dice structured field for potions."""

    def test_default_none(self) -> None:
        item = Item(name="Rope", item_type=ItemType.ADVENTURING_GEAR, weight=5.0)
        assert item.heal_dice is None

    def test_healing_potion_has_heal_dice(self) -> None:
        potion = ITEM_CATALOG["Healing Potion"]
        assert potion.heal_dice == "2d4+2"

    def test_potion_heal_dice_is_valid_dice_expression(self) -> None:
        from engine.dice import parse_dice

        potion = ITEM_CATALOG["Healing Potion"]
        assert potion.heal_dice is not None
        count, sides, modifier = parse_dice(potion.heal_dice)
        assert count == 2
        assert sides == 4
        assert modifier == 2
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `uv run pytest tests/engine/test_inventory.py::TestHealDice -v`
Expected: FAIL — `heal_dice` is not a valid field on Item.

- [ ] **Step 3: Add the field + update catalog**

In `engine/inventory.py`, in class `Item` (after `quantity: int = Field(default=1, ge=1)` at line 119), add:

```python
    heal_dice: str | None = None
    """Dice expression for healing potions, e.g. '2d4+2'. None for non-healing items."""
```

In `ITEM_CATALOG`, update the Healing Potion entry (line ~613-621) to:

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

- [ ] **Step 4: Run tests — expect PASS**

Run: `uv run pytest tests/engine/test_inventory.py::TestHealDice -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add engine/inventory.py tests/engine/test_inventory.py
git commit -m "feat(inventory): add heal_dice field to Item model"
```

---

### Task 6: Harden validate_use_item — action budget + potion type check

**Files:**
- Modify: `engine/validators.py:525-550`
- Test: `tests/engine/test_validators.py`

- [ ] **Step 1: Write failing tests**

In `tests/engine/test_validators.py`, add to an existing or new class:

```python
class TestValidateUseItemCombat:
    """USE_ITEM validation in combat: action budget + potion type."""

    def test_use_item_rejects_when_action_used(self, combat_state: CombatState) -> None:
        """Cannot use item after already using action this turn."""
        actor = combat_state.combatants[0]
        from engine.inventory import Item, ItemType

        potion = Item(
            name="Healing Potion",
            item_type=ItemType.POTION,
            weight=0.5,
            heal_dice="2d4+2",
        )
        actor.inventory.items.append(potion)
        actor.action_budget.action_used = True
        action = Action(
            actor_name="Arden",
            action_type=ActionType.USE_ITEM,
            item_name="Healing Potion",
        )
        result = validate_action(action, combat_state)
        assert not result.is_valid
        assert "Action" in (result.error_message or "")

    def test_use_item_rejects_non_healing_potion(self, combat_state: CombatState) -> None:
        """V1: only potions with heal_dice are usable in combat."""
        actor = combat_state.combatants[0]
        from engine.inventory import Item, ItemType

        buff_potion = Item(
            name="Potion of Speed",
            item_type=ItemType.POTION,
            weight=0.5,
        )
        actor.inventory.items.append(buff_potion)
        action = Action(
            actor_name="Arden",
            action_type=ActionType.USE_ITEM,
            item_name="Potion of Speed",
        )
        result = validate_action(action, combat_state)
        assert not result.is_valid
        assert "soin" in (result.error_message or "").lower() or "heal" in (result.error_message or "").lower()
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `uv run pytest tests/engine/test_validators.py::TestValidateUseItemCombat -v`
Expected: FAIL — validate_use_item doesn't check action_used or potion type.

- [ ] **Step 3: Update validate_use_item**

In `engine/validators.py`, replace `validate_use_item` (lines 525-550) with:

```python
def validate_use_item(action: Action, state: CombatState) -> ValidationResult:
    """Validate a use item action. Common + action budget + item in inventory + potion check."""
    common = _validate_common(action, state)
    if common is not None:
        return common

    actor = _find_combatant(action.actor_name, state)
    assert actor is not None

    if actor.action_budget.action_used:
        return ValidationResult(
            is_valid=False,
            error_message=f"'{action.actor_name}' a déjà utilisé son Action ce tour.",
        )

    if action.item_name is None:
        return ValidationResult(
            is_valid=False,
            error_message="Use Item requires an item name",
        )

    # Check item is in inventory (carried or equipped)
    all_items = [i for i in actor.inventory.items] + [
        i for i in actor.inventory.equipped.values()
    ]
    matching = [i for i in all_items if i.name == action.item_name]
    if not matching:
        return ValidationResult(
            is_valid=False,
            error_message=f"Item '{action.item_name}' not found in inventory",
        )

    # V1: only healing potions are usable in combat
    item = matching[0]
    if item.item_type == ItemType.POTION and not getattr(item, "heal_dice", None):
        return ValidationResult(
            is_valid=False,
            error_message=f"'{action.item_name}' n'est pas une potion de soin utilisable en combat.",
        )

    return ValidationResult(is_valid=True)
```

Add `from engine.inventory import ItemType` to the imports at the top of `engine/validators.py` if not already present.

- [ ] **Step 4: Run tests — expect PASS**

Run: `uv run pytest tests/engine/test_validators.py::TestValidateUseItemCombat -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add engine/validators.py tests/engine/test_validators.py
git commit -m "feat(combat): harden validate_use_item with action budget and potion type check"
```

---

### Task 7: ActionPipelineResult.is_free_action + _resolve_equip + _resolve_use_item

**Files:**
- Modify: `bot/action_pipeline.py:143-154` (result class), `bot/action_pipeline.py:879-999` (_resolve_mechanics)
- Test: `tests/bot/test_action_pipeline.py`

- [ ] **Step 1: Add is_free_action to ActionPipelineResult**

In `bot/action_pipeline.py`, in `ActionPipelineResult` class (after `is_question: bool = False` at line 154), add:

```python
    is_free_action: bool = False
    """True for EQUIP (free action) — TurnManager re-prompts instead of advancing."""
```

- [ ] **Step 2: Add _resolve_equip and _resolve_use_item methods**

In `bot/action_pipeline.py`, add these methods to `ActionPipeline` (in the `_resolve_mechanics` section, or as separate methods nearby):

```python
    def _resolve_equip(self, action: InterpretedAction) -> MechanicsOutcome:
        """Swap equipped weapon — free action, no turn advance."""
        intent = self._build_player_intent(action)
        if self.combat_state is None or action.item_name is None:
            return MechanicsOutcome(summary="Equip failed.", player_intent=intent)

        actor = next(
            (c for c in self.combat_state.combatants if c.name == action.actor_name),
            None,
        )
        if actor is None:
            return MechanicsOutcome(summary="Equip failed.", player_intent=intent)

        inv = actor.inventory

        # Unequip current MAIN_HAND if occupied
        if EquipmentSlot.MAIN_HAND in inv.equipped:
            unequip_item(inv, EquipmentSlot.MAIN_HAND)

        # Equip the new weapon
        equip_item(inv, action.item_name, EquipmentSlot.MAIN_HAND)
        actor.action_budget.weapon_swapped_this_turn = True

        return MechanicsOutcome(
            summary=f"{action.actor_name} dégaine {action.item_name}.",
            player_intent=intent,
        )

    def _resolve_use_item(self, action: InterpretedAction) -> MechanicsOutcome:
        """Use a healing potion — costs the action."""
        intent = self._build_player_intent(action)
        if self.combat_state is None or action.item_name is None:
            return MechanicsOutcome(summary="Use item failed.", player_intent=intent)

        actor = next(
            (c for c in self.combat_state.combatants if c.name == action.actor_name),
            None,
        )
        if actor is None:
            return MechanicsOutcome(summary="Use item failed.", player_intent=intent)

        # Find the potion
        matching = [i for i in actor.inventory.items if i.name == action.item_name]
        if not matching:
            return MechanicsOutcome(
                summary=f"{action.item_name} not found.", player_intent=intent,
            )

        item = matching[0]
        heal_dice = getattr(item, "heal_dice", None)
        if not heal_dice:
            return MechanicsOutcome(
                summary=f"{action.actor_name} uses {action.item_name}.",
                player_intent=intent,
            )

        # Roll healing dice
        from engine.dice import roll as roll_dice

        dice_result = roll_dice(heal_dice)
        healed = dice_result.total
        old_hp = actor.character.hp
        actor.character.hp = min(old_hp + healed, actor.character.max_hp)
        actual_healed = actor.character.hp - old_hp

        # Remove potion from inventory
        remove_item(actor.inventory, action.item_name)

        # Mark action used
        actor.action_budget.action_used = True

        summary = (
            f"{action.actor_name} boit {action.item_name} "
            f"— récupère {actual_healed} PV ({dice_result.expression}: {dice_result.total})"
        )
        return MechanicsOutcome(
            summary=summary,
            player_intent=intent,
            outcome_facts=summary,
            public_effects=PublicEffects(
                hp_delta={action.actor_name: actual_healed},
            ),
        )
```

Add `from engine.inventory import equip_item, remove_item, unequip_item` to the existing inventory import line (line 70) if `equip_item`, `remove_item`, `unequip_item` are not already imported.

- [ ] **Step 3: Wire into _resolve_mechanics**

In `_resolve_mechanics`, after the `at = action.action_type` line (line 879), add two new dispatch cases before the existing `if at == ActionType.FLEE:` block:

```python
        if at == ActionType.EQUIP:
            return self._resolve_equip(action)

        if at == ActionType.USE_ITEM:
            return self._resolve_use_item(action)
```

- [ ] **Step 4: Set is_free_action for EQUIP in the result builder**

In `_continue_from_resolution`, where `ActionPipelineResult` is constructed (around line 504), add:

```python
        is_free = interpreted.action_type == ActionType.EQUIP
```

And update the `ActionPipelineResult(...)` constructor to include:

```python
            is_free_action=is_free,
```

- [ ] **Step 5: Run existing test suite — no regressions**

Run: `uv run pytest tests/bot/test_action_pipeline.py -v`
Expected: All existing tests PASS.

- [ ] **Step 6: Commit**

```bash
git add bot/action_pipeline.py tests/bot/test_action_pipeline.py
git commit -m "feat(combat): add _resolve_equip, _resolve_use_item, and is_free_action flag"
```

---

### Task 8: PotionSelectView

**Files:**
- Create: `bot/views/potion_select_view.py`
- Test: `tests/bot/views/test_potion_select_view.py`

- [ ] **Step 1: Write the test**

Create `tests/bot/views/test_potion_select_view.py`:

```python
"""Tests for PotionSelectView."""

from __future__ import annotations

import pytest

from bot.views.potion_select_view import PotionSelectView


def test_potion_select_creates_options() -> None:
    """Options are built from potion name list."""
    async def noop(name: str) -> None:
        pass

    view = PotionSelectView(
        potion_names=["Healing Potion", "Greater Healing Potion"],
        user_id=123,
        on_choice=noop,
    )
    options = view.select.options
    assert len(options) == 2
    assert options[0].label == "Healing Potion"
    assert options[1].label == "Greater Healing Potion"


def test_potion_select_empty_shows_placeholder() -> None:
    """Empty list → sentinel '__none__' option."""
    async def noop(name: str) -> None:
        pass

    view = PotionSelectView(potion_names=[], user_id=123, on_choice=noop)
    assert len(view.select.options) == 1
    assert view.select.options[0].value == "__none__"
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `uv run pytest tests/bot/views/test_potion_select_view.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Create PotionSelectView**

Create `bot/views/potion_select_view.py`:

```python
"""Potion select dropdown for combat.

Ephemeral single-option dropdown used by ``CombatActionView`` when the
player clicks **Potion**. Follows the same pattern as
:class:`SpellSelectView`.
"""

from __future__ import annotations

from typing import Awaitable, Callable

import discord
from discord import ui

from bot.views.base import LoggedView

_MAX_OPTIONS = 25
_DEFAULT_TIMEOUT = 60.0


class PotionSelectView(LoggedView):
    """Dropdown of usable potions for the active combatant."""

    def __init__(
        self,
        *,
        potion_names: list[str],
        user_id: int,
        on_choice: Callable[[str], Awaitable[None]],
        descriptions: dict[str, str] | None = None,
    ) -> None:
        super().__init__(timeout=_DEFAULT_TIMEOUT)
        self.user_id = user_id
        self.on_choice = on_choice

        options: list[discord.SelectOption] = []
        for name in potion_names[:_MAX_OPTIONS]:
            desc = (descriptions or {}).get(name)
            options.append(
                discord.SelectOption(
                    label=name[:100],
                    value=name,
                    description=(desc[:100] if desc else None),
                    emoji="🧪",
                ),
            )

        if not options:
            options.append(
                discord.SelectOption(
                    label="Aucune potion disponible", value="__none__", emoji="🚫",
                ),
            )

        self.select: ui.Select["PotionSelectView"] = ui.Select(
            placeholder="Choisis ta potion",
            min_values=1,
            max_values=1,
            options=options,
        )
        self.select.callback = self._on_selected  # type: ignore[method-assign]
        self.add_item(self.select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Ce n'est pas ton tour.", ephemeral=True,
            )
            return False
        return True

    async def _on_selected(self, interaction: discord.Interaction) -> None:
        value = self.select.values[0]
        if value == "__none__":
            await interaction.response.edit_message(
                content="Aucune potion à utiliser.", view=None,
            )
            self.stop()
            return
        await interaction.response.edit_message(
            content=f"✔ Potion : **{value}**", view=None,
        )
        self.stop()
        await self.on_choice(value)
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `uv run pytest tests/bot/views/test_potion_select_view.py -v`
Expected: Both PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/views/potion_select_view.py tests/bot/views/test_potion_select_view.py
git commit -m "feat(combat): add PotionSelectView for potion use in combat"
```

---

### Task 9: EquipSelectView

**Files:**
- Create: `bot/views/equip_select_view.py`
- Test: `tests/bot/views/test_equip_select_view.py`

- [ ] **Step 1: Write the test**

Create `tests/bot/views/test_equip_select_view.py`:

```python
"""Tests for EquipSelectView."""

from __future__ import annotations

from bot.views.equip_select_view import EquipSelectView


def test_equip_select_creates_options() -> None:
    async def noop(name: str) -> None:
        pass

    view = EquipSelectView(
        weapon_names=["Shortbow", "Dagger"],
        user_id=123,
        on_choice=noop,
        descriptions={"Shortbow": "1d6 perçant", "Dagger": "1d4 perçant"},
    )
    options = view.select.options
    assert len(options) == 2
    assert options[0].label == "Shortbow"


def test_equip_select_empty_shows_placeholder() -> None:
    async def noop(name: str) -> None:
        pass

    view = EquipSelectView(weapon_names=[], user_id=123, on_choice=noop)
    assert len(view.select.options) == 1
    assert view.select.options[0].value == "__none__"
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `uv run pytest tests/bot/views/test_equip_select_view.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Create EquipSelectView**

Create `bot/views/equip_select_view.py`:

```python
"""Equip weapon select dropdown for combat.

Ephemeral single-option dropdown used by ``CombatActionView`` when the
player clicks **Équiper**. Follows the same pattern as
:class:`SpellSelectView`.
"""

from __future__ import annotations

from typing import Awaitable, Callable

import discord
from discord import ui

from bot.views.base import LoggedView

_MAX_OPTIONS = 25
_DEFAULT_TIMEOUT = 60.0


class EquipSelectView(LoggedView):
    """Dropdown of equippable weapons for the active combatant."""

    def __init__(
        self,
        *,
        weapon_names: list[str],
        user_id: int,
        on_choice: Callable[[str], Awaitable[None]],
        descriptions: dict[str, str] | None = None,
    ) -> None:
        super().__init__(timeout=_DEFAULT_TIMEOUT)
        self.user_id = user_id
        self.on_choice = on_choice

        options: list[discord.SelectOption] = []
        for name in weapon_names[:_MAX_OPTIONS]:
            desc = (descriptions or {}).get(name)
            options.append(
                discord.SelectOption(
                    label=name[:100],
                    value=name,
                    description=(desc[:100] if desc else None),
                    emoji="🗡️",
                ),
            )

        if not options:
            options.append(
                discord.SelectOption(
                    label="Aucune arme disponible", value="__none__", emoji="🚫",
                ),
            )

        self.select: ui.Select["EquipSelectView"] = ui.Select(
            placeholder="Choisis ton arme",
            min_values=1,
            max_values=1,
            options=options,
        )
        self.select.callback = self._on_selected  # type: ignore[method-assign]
        self.add_item(self.select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Ce n'est pas ton tour.", ephemeral=True,
            )
            return False
        return True

    async def _on_selected(self, interaction: discord.Interaction) -> None:
        value = self.select.values[0]
        if value == "__none__":
            await interaction.response.edit_message(
                content="Aucune arme à équiper.", view=None,
            )
            self.stop()
            return
        await interaction.response.edit_message(
            content=f"✔ Arme : **{value}**", view=None,
        )
        self.stop()
        await self.on_choice(value)
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `uv run pytest tests/bot/views/test_equip_select_view.py -v`
Expected: Both PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/views/equip_select_view.py tests/bot/views/test_equip_select_view.py
git commit -m "feat(combat): add EquipSelectView for weapon swap in combat"
```

---

### Task 10: CombatActionView — add Potion + Équiper buttons

**Files:**
- Modify: `bot/views/combat_action_view.py`
- Test: `tests/bot/views/test_combat_action_view.py` (if exists, else add inline)

- [ ] **Step 1: Update constructor**

In `bot/views/combat_action_view.py`, update the constructor to accept two new parameters. After `dispatch_callback: DispatchCallback,` add:

```python
        potion_names: list[str] | None = None,
        equippable_names: list[str] | None = None,
```

Store them and set disable logic:

```python
        self.potion_names = potion_names or []
        self.equippable_names = equippable_names or []

        # Disable buttons whose pre-conditions are not satisfied.
        self._attack_button.disabled = not target_names
        self._spell_button.disabled = not spell_names
        self._move_button.disabled = not adjacent_zone_names
        self._potion_button.disabled = not self.potion_names
        self._equip_button.disabled = not self.equippable_names
```

Add imports at the top of the file:

```python
from bot.views.potion_select_view import PotionSelectView
from bot.views.equip_select_view import EquipSelectView
```

- [ ] **Step 2: Add Potion button (row 0)**

After the `_spell_button` method, add:

```python
    @ui.button(
        label="Potion",
        style=discord.ButtonStyle.success,
        emoji="🧪",
        row=0,
    )
    async def _potion_button(
        self, interaction: discord.Interaction, button: ui.Button["CombatActionView"],
    ) -> None:
        del button
        await self._open_potion_select(interaction)
```

- [ ] **Step 3: Add Équiper button (row 1)**

After the `_move_button` method, add:

```python
    @ui.button(
        label="Équiper",
        style=discord.ButtonStyle.secondary,
        emoji="🗡️",
        row=1,
    )
    async def _equip_button(
        self, interaction: discord.Interaction, button: ui.Button["CombatActionView"],
    ) -> None:
        del button
        await self._open_equip_select(interaction)
```

- [ ] **Step 4: Add _open_potion_select and _open_equip_select methods**

In the "Secondary view orchestration" section, add:

```python
    async def _open_potion_select(self, interaction: discord.Interaction) -> None:
        """Post an ephemeral potion picker."""

        async def on_potion_chosen(potion_name: str) -> None:
            await self._disable_self_and_edit(interaction)
            await self.dispatch_callback(
                InterpretedAction(
                    action_type=ActionType.USE_ITEM,
                    actor_name=self.actor_name,
                    item_name=potion_name,
                    raw_input=f"(bouton Potion → {potion_name})",
                ),
            )

        view = PotionSelectView(
            potion_names=self.potion_names,
            user_id=self.user_id,
            on_choice=on_potion_chosen,
        )
        await interaction.response.send_message(
            "Choisis ta potion :", view=view, ephemeral=True,
        )

    async def _open_equip_select(self, interaction: discord.Interaction) -> None:
        """Post an ephemeral weapon picker for equip."""

        async def on_weapon_chosen(weapon_name: str) -> None:
            await self._disable_self_and_edit(interaction)
            await self.dispatch_callback(
                InterpretedAction(
                    action_type=ActionType.EQUIP,
                    actor_name=self.actor_name,
                    item_name=weapon_name,
                    raw_input=f"(bouton Équiper → {weapon_name})",
                ),
            )

        view = EquipSelectView(
            weapon_names=self.equippable_names,
            user_id=self.user_id,
            on_choice=on_weapon_chosen,
        )
        await interaction.response.send_message(
            "Choisis ton arme :", view=view, ephemeral=True,
        )
```

- [ ] **Step 5: Run lint + existing tests**

Run: `uv run ruff check bot/views/combat_action_view.py && uv run pytest tests/bot/views/ -v`
Expected: No lint errors, existing tests pass.

- [ ] **Step 6: Commit**

```bash
git add bot/views/combat_action_view.py
git commit -m "feat(combat): add Potion and Équiper buttons to CombatActionView"
```

---

### Task 11: TurnManager — potion/equip data + free action flow

**Files:**
- Modify: `bot/combat_turn_manager.py:217-258` (_prompt_pc_turn), `bot/combat_turn_manager.py:173-199` (dispatch_action)

- [ ] **Step 1: Update _prompt_pc_turn to compute potion/equip lists**

In `bot/combat_turn_manager.py`, in `_prompt_pc_turn`, after the existing data computation lines (after `adjacent_zones = ...`), add:

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

Add the required imports at the top of the file:

```python
from engine.inventory import ItemType, Weapon
```

Update the `CombatActionView(...)` constructor call to include:

```python
            potion_names=potion_names,
            equippable_names=equippable_names,
```

- [ ] **Step 2: Update dispatch_action for free actions**

In `dispatch_action`, replace the tail of the method. After `await self._render_pipeline_result(pipeline, result, action)`, change:

```python
        await self.on_action_resolved()
```

to:

```python
        # Free actions (EQUIP) re-prompt the same combatant instead of advancing.
        if (
            isinstance(result, ActionPipelineResult)
            and result.is_free_action
        ):
            state = self.session.combat_state
            if state is not None:
                current = get_current_combatant(state)
                if current is not None:
                    await self._prompt_turn(current)
            return

        await self.on_action_resolved()
```

Add `from bot.action_pipeline import ActionPipelineResult` to imports if not already present. Also ensure `get_current_combatant` is imported (check existing imports).

- [ ] **Step 3: Run full test suite**

Run: `uv run pytest tests/ -x --timeout=60`
Expected: All PASS, no regressions.

- [ ] **Step 4: Commit**

```bash
git add bot/combat_turn_manager.py
git commit -m "feat(combat): wire potion/equip buttons and free-action re-prompt in TurnManager"
```

---

### Task 12: Interpreter prompt — add EQUIP ActionType

**Files:**
- Modify: `ai/prompts/system_interpreter.txt`

- [ ] **Step 1: Add EQUIP to ActionType list**

In `ai/prompts/system_interpreter.txt`, after the `"Use Item"` entry (line 23) and before `"Pick Up"`, add:

```
- "Equip"       — change equipped weapon or gear during combat. Free action, once per turn. Set `item_name` to the item to equip. Only valid in combat (the scene context will say `In combat: yes`).
```

- [ ] **Step 2: Run interpreter tests**

Run: `uv run pytest tests/ai/test_interpreter.py -v`
Expected: PASS (the interpreter tests use mocked LLM responses, so the prompt change doesn't break them).

- [ ] **Step 3: Commit**

```bash
git add ai/prompts/system_interpreter.txt
git commit -m "feat(interpreter): add EQUIP ActionType to interpreter prompt"
```

---

### Task 13: Final verification

- [ ] **Step 1: Full test suite**

Run: `uv run pytest tests/ -x`
Expected: All PASS.

- [ ] **Step 2: Lint + type check**

Run: `uv run ruff check . && uv run mypy .`
Expected: No errors.

- [ ] **Step 3: Verify EQUIP is in EXPLORATION_ACTION_TYPES exclusion**

EQUIP is a combat-only action. Check that `EXPLORATION_ACTION_TYPES` in `engine/validators.py` does NOT include `ActionType.EQUIP` (it shouldn't, since we only added it to the combat enum area). If it's accidentally included, remove it.

- [ ] **Step 4: Commit if any cleanup needed**

```bash
git add -A && git commit -m "chore: final cleanup for combat equipment and potions"
```
