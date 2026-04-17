# Combat UI & Weapon Resolution Fixes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 4 independent combat bugs surfaced during the 2026-04-17 session: weapon name alias resolution, missing player d20 attack embed, combat hub stuck at top of channel, and all combatants shown as "Hors zone."

**Architecture:** All fixes are surgical — no new abstractions, no refactors beyond the touched functions. Each bug has a clear root cause traced to a single code location. Two fixes live in `bot/action_pipeline.py`, two in `bot/combat_turn_manager.py`.

**Tech Stack:** Python 3.12, Pydantic v2, discord.py 2.4, pytest/AsyncMock, `uv run pytest`

---

## Bug Map (Root Causes)

| Bug | File | Lines | Root Cause |
|-----|------|-------|-----------|
| "épée" not found | `bot/action_pipeline.py` | 587–600 | `_auto_resolve_weapon_name` returns player text verbatim when non-None; validator does `item.name == weapon_name` exact match |
| No player d20 embed | `bot/action_pipeline.py` | 992–1122 | `_resolve_mechanics` has no `ActionType.ATTACK` branch; falls to generic no-op at line 1119 without calling `resolve_attack()` |
| Hub stuck at top | `bot/combat_turn_manager.py` | 521–552 | `_upsert_hub` calls `hub_message.edit()` in-place; new messages pile below; hub never reaches channel bottom |
| All "Hors zone" | `bot/action_pipeline.py` | 650–654 | No code sets `Combatant.current_zone` after `enter_combat()` + `start_combat()`; embed matches `c.current_zone == zone.name` which is always None |

---

## File Structure

**Modified files only (no new files):**

- `bot/action_pipeline.py` — Tasks 1, 2, 4
- `bot/combat_turn_manager.py` — Tasks 2 (embed flush), 3
- `tests/bot/test_action_pipeline.py` — Tests for Tasks 1, 2, 4
- `tests/bot/test_turn_manager.py` — Tests for Task 3

---

## Task 1 — Weapon Fuzzy Resolution

Fix `_auto_resolve_weapon_name` so that when the player mentions "épée", "sword", or any alias that doesn't exactly match an equipped item, it falls back to the equipped weapon rather than returning the raw player text.

**Files:**
- Modify: `bot/action_pipeline.py:587–600`
- Test: `tests/bot/test_action_pipeline.py` (add to existing `TestAutoResolveWeaponName` class, or create it)

### Current code (lines 587–600):
```python
@staticmethod
def _auto_resolve_weapon_name(
    weapon_name: str | None,
    inventory: Inventory | None,
) -> str | None:
    """Return the MAIN_HAND weapon name when the player omits it."""
    if weapon_name is not None:
        return weapon_name   # ← BUG: returns alias verbatim, validator then fails
    if inventory is None:
        return None
    main_hand = inventory.equipped.get(EquipmentSlot.MAIN_HAND)
    if main_hand is not None and isinstance(main_hand, Weapon):
        return main_hand.name
    return None
```

- [ ] **Step 1.1 — Write failing tests**

Add the following tests (after the existing `_auto_resolve_weapon_name` tests, or in a new class):

```python
# tests/bot/test_action_pipeline.py

from engine.inventory import (
    ITEM_CATALOG,
    EquipmentSlot,
    Weapon,
    add_item,
    create_inventory,
    equip_item,
)


def _inv_with_longsword() -> Inventory:
    """Return an inventory with a Longsword equipped in MAIN_HAND."""
    inv = create_inventory()
    sword = ITEM_CATALOG["Longsword"]
    inv = add_item(inv, sword)
    return equip_item(inv, sword.name, EquipmentSlot.MAIN_HAND)


class TestAutoResolveWeaponNameFuzzy:
    def test_alias_falls_back_to_only_equipped_weapon(self) -> None:
        """Player says 'épée'; only Longsword equipped → resolve to 'Longsword'."""
        inv = _inv_with_longsword()
        result = ActionPipeline._auto_resolve_weapon_name("épée", inv)
        assert result == "Longsword"

    def test_case_insensitive_match_returns_canonical(self) -> None:
        """'longsword' (lowercase) → canonical 'Longsword'."""
        inv = _inv_with_longsword()
        result = ActionPipeline._auto_resolve_weapon_name("longsword", inv)
        assert result == "Longsword"

    def test_exact_match_still_works(self) -> None:
        """'Longsword' exact → 'Longsword' (unchanged)."""
        inv = _inv_with_longsword()
        result = ActionPipeline._auto_resolve_weapon_name("Longsword", inv)
        assert result == "Longsword"

    def test_none_resolves_to_main_hand(self) -> None:
        """weapon_name=None still resolves to main-hand as before."""
        inv = _inv_with_longsword()
        result = ActionPipeline._auto_resolve_weapon_name(None, inv)
        assert result == "Longsword"

    def test_none_with_empty_inventory_returns_none(self) -> None:
        result = ActionPipeline._auto_resolve_weapon_name(None, create_inventory())
        assert result is None

    def test_alias_with_none_inventory_returns_none(self) -> None:
        result = ActionPipeline._auto_resolve_weapon_name("sword", None)
        assert result is None
```

- [ ] **Step 1.2 — Run tests, confirm they fail**

```bash
uv run pytest tests/bot/test_action_pipeline.py::TestAutoResolveWeaponNameFuzzy -v
```

Expected: 5 failures (test_exact_match and test_none tests may pass already).

- [ ] **Step 1.3 — Replace `_auto_resolve_weapon_name` in `bot/action_pipeline.py`**

Replace lines 587–600 with:

```python
@staticmethod
def _auto_resolve_weapon_name(
    weapon_name: str | None,
    inventory: Inventory | None,
) -> str | None:
    """Return the canonical equipped weapon name, resolving player aliases.

    Strategy:
    1. Collect all equipped weapons (MAIN_HAND, OFF_HAND).
    2. If weapon_name is None → return MAIN_HAND if equipped, else first found.
    3. Try case-insensitive exact match against equipped names.
    4. If no match AND only one weapon is equipped → assume the player
       meant that weapon (handles "épée", "sword", "mon arme", etc.).
    5. If multiple weapons and no match → fall back to MAIN_HAND.
    """
    if inventory is None:
        return None

    equipped_weapons: list[Weapon] = [
        item
        for slot in (EquipmentSlot.MAIN_HAND, EquipmentSlot.OFF_HAND)
        if (item := inventory.equipped.get(slot)) is not None
        and isinstance(item, Weapon)
    ]

    if weapon_name is None:
        # Original auto-resolve path: prefer MAIN_HAND.
        main = inventory.equipped.get(EquipmentSlot.MAIN_HAND)
        if main is not None and isinstance(main, Weapon):
            return main.name
        return equipped_weapons[0].name if equipped_weapons else None

    # Case-insensitive exact match.
    for w in equipped_weapons:
        if w.name.lower() == weapon_name.lower():
            return w.name

    # No match — if unambiguous (single weapon), assume the player meant it.
    if len(equipped_weapons) == 1:
        return equipped_weapons[0].name

    # Ambiguous or no weapon equipped — fall back to MAIN_HAND.
    main = inventory.equipped.get(EquipmentSlot.MAIN_HAND)
    return main.name if main is not None and isinstance(main, Weapon) else None
```

- [ ] **Step 1.4 — Run tests, confirm they pass**

```bash
uv run pytest tests/bot/test_action_pipeline.py::TestAutoResolveWeaponNameFuzzy -v
```

Expected: 6/6 PASS.

- [ ] **Step 1.5 — Run full test suite to catch regressions**

```bash
uv run pytest tests/bot/test_action_pipeline.py -v
```

Expected: all green.

- [ ] **Step 1.6 — Commit**

```bash
git add bot/action_pipeline.py tests/bot/test_action_pipeline.py
git commit -m "fix(combat): fuzzy weapon name resolution — alias 'épée' resolves to equipped Longsword"
```

---

## Task 2 — Player Attack d20 Embed

`ActionType.ATTACK` falls through `_resolve_mechanics` to a generic no-op. Add a `_resolve_pc_attack` method that calls `engine.combat.resolve_attack()` (which mutates defender HP in-place), queues the `AttackResult` on `_pending_dice_embeds`, and returns a proper `MechanicsOutcome`. Wire the `"attack_roll"` embed kind in `_flush_dice_embeds`.

**Key types to know:**
- `engine.combat.resolve_attack(attacker, defender, weapon)` → `AttackResult`, mutates defender HP
- `engine.combat.consume_action(combatant)` → `None`, marks action slot used (raises if already used)
- `AttackResult` fields: `attacker`, `defender`, `weapon_name`, `attack_roll`, `attack_total`, `ac`, `hit`, `critical`, `outcome`, `damage`, `damage_type`, `defender_hp_remaining`
- `PublicEffects.hp_delta: dict[str, int]` — maps name → delta (negative = damage)
- `_pending_dice_embeds`: `list[tuple[str, Any, str]]` — `(kind, result_obj, actor_name)`

**Files:**
- Modify: `bot/action_pipeline.py` (add branch + `_resolve_pc_attack`)
- Modify: `bot/combat_turn_manager.py:495–515` (`_flush_dice_embeds`)
- Test: `tests/bot/test_action_pipeline.py`

### Sub-task 2A — `_resolve_pc_attack` in `bot/action_pipeline.py`

- [ ] **Step 2.1 — Write failing tests**

Add the following test class in `tests/bot/test_action_pipeline.py`:

```python
# tests/bot/test_action_pipeline.py — add imports at top if missing:
# from unittest.mock import patch
# from engine.combat import (
#     CombatSide, CombatState, Combatant, AttackResult,
#     start_combat,
# )
# from engine.combat_trigger import CombatTrigger, CombatTriggerKind, InitiativeSide
# from engine.character import AbilityScores, CharacterClass, Race, create_character

from engine.combat import CombatSide, CombatState, Combatant


def _make_pc_combatant(name: str = "JeanTest", hp: int = 20) -> Combatant:
    """PC combatant with a Longsword equipped, hp set."""
    from engine.character import AbilityScores, CharacterClass, Race, create_character

    char = create_character(
        name=name,
        race=Race.HUMAN,
        char_class=CharacterClass.FIGHTER,
        ability_scores=AbilityScores(STR=16, DEX=10, CON=14, INT=10, WIS=10, CHA=10),
    )
    char.hp = hp
    char.max_hp = hp
    inv = _inv_with_longsword()  # reuse helper from Task 1 tests
    return Combatant(
        name=name,
        side=CombatSide.PLAYER,
        character=char,
        inventory=inv,
        initiative=18,
    )


def _make_enemy_combatant(name: str = "Gobelin", hp: int = 15, ac: int = 10) -> Combatant:
    from engine.character import AbilityScores, CharacterClass, Race, create_character

    char = create_character(
        name=name,
        race=Race.HUMAN,
        char_class=CharacterClass.FIGHTER,
        ability_scores=AbilityScores(STR=10, DEX=10, CON=10, INT=8, WIS=8, CHA=8),
    )
    char.hp = hp
    char.max_hp = hp
    char.ac = ac
    return Combatant(
        name=name,
        side=CombatSide.ENEMY,
        character=char,
        inventory=create_inventory(),
        initiative=5,
    )


def _active_combat_state(pc: Combatant, enemy: Combatant) -> CombatState:
    return CombatState(combatants=[pc, enemy], round_number=1, current_turn_index=0)


class TestResolvePcAttack:
    @pytest.mark.asyncio
    async def test_hit_updates_defender_hp(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A hit reduces defender HP in-place and reflects in public_effects."""
        pc = _make_pc_combatant(hp=20)
        enemy = _make_enemy_combatant(hp=15, ac=1)  # AC 1 ensures hit
        state = _active_combat_state(pc, enemy)

        narrator = FakeNarrator(responses=[NarrativeResult(narrative="Touché!", tone="tense")])
        pipeline = _make_pipeline(FakeInterpreter(response=InterpretedAction(
            action_type=ActionType.ATTACK, actor_name="JeanTest", raw_input="",
        )), narrator, None, {})
        pipeline.combat_state = state
        pipeline.inventory = pc.inventory

        action = InterpretedAction(
            action_type=ActionType.ATTACK,
            actor_name="JeanTest",
            target_name="Gobelin",
            weapon_name="Longsword",
            raw_input="(bouton Attaquer → Gobelin)",
        )
        outcome = await pipeline._resolve_mechanics(action)

        # Defender HP must have decreased
        assert enemy.character.hp < 15
        # hp_delta must be negative
        assert outcome.public_effects.hp_delta.get("Gobelin", 0) < 0
        # Dice embed was queued
        assert len(pipeline._pending_dice_embeds) == 1
        kind, result_obj, actor = pipeline._pending_dice_embeds[0]
        assert kind == "attack_roll"
        assert actor == "JeanTest"

    @pytest.mark.asyncio
    async def test_miss_does_not_change_defender_hp(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A miss leaves defender HP unchanged and hp_delta empty."""
        from engine.dice import RollOutcome
        from engine.combat import AttackResult, DamageType as EngDamageType

        pc = _make_pc_combatant(hp=20)
        enemy = _make_enemy_combatant(hp=15, ac=30)  # AC 30 guarantees miss

        state = _active_combat_state(pc, enemy)

        narrator = FakeNarrator(responses=[NarrativeResult(narrative="Raté.", tone="tense")])
        pipeline = _make_pipeline(FakeInterpreter(response=InterpretedAction(
            action_type=ActionType.ATTACK, actor_name="JeanTest", raw_input="",
        )), narrator, None, {})
        pipeline.combat_state = state
        pipeline.inventory = pc.inventory

        action = InterpretedAction(
            action_type=ActionType.ATTACK,
            actor_name="JeanTest",
            target_name="Gobelin",
            weapon_name="Longsword",
            raw_input="(bouton Attaquer → Gobelin)",
        )
        outcome = await pipeline._resolve_mechanics(action)

        assert enemy.character.hp == 15  # unchanged
        assert outcome.public_effects.hp_delta == {}
        assert len(pipeline._pending_dice_embeds) == 1
        kind, result_obj, _ = pipeline._pending_dice_embeds[0]
        assert kind == "attack_roll"

    @pytest.mark.asyncio
    async def test_action_budget_consumed_after_attack(self) -> None:
        """consume_action() is called — action_budget.action_used becomes True."""
        pc = _make_pc_combatant(hp=20)
        enemy = _make_enemy_combatant(hp=15, ac=1)
        state = _active_combat_state(pc, enemy)

        narrator = FakeNarrator(responses=[NarrativeResult(narrative=".", tone="tense")])
        pipeline = _make_pipeline(FakeInterpreter(response=InterpretedAction(
            action_type=ActionType.ATTACK, actor_name="JeanTest", raw_input="",
        )), narrator, None, {})
        pipeline.combat_state = state
        pipeline.inventory = pc.inventory

        assert not pc.action_budget.action_used

        action = InterpretedAction(
            action_type=ActionType.ATTACK,
            actor_name="JeanTest",
            target_name="Gobelin",
            weapon_name="Longsword",
            raw_input="(bouton Attaquer → Gobelin)",
        )
        await pipeline._resolve_mechanics(action)

        assert pc.action_budget.action_used

    @pytest.mark.asyncio
    async def test_missing_combatant_returns_generic_outcome(self) -> None:
        """If actor not in combat state, returns a non-crashing fallback."""
        pc = _make_pc_combatant(hp=20)
        state = _active_combat_state(pc, _make_enemy_combatant())

        narrator = FakeNarrator(responses=[NarrativeResult(narrative=".", tone="tense")])
        pipeline = _make_pipeline(FakeInterpreter(response=InterpretedAction(
            action_type=ActionType.ATTACK, actor_name="JeanTest", raw_input="",
        )), narrator, None, {})
        pipeline.combat_state = state
        pipeline.inventory = pc.inventory

        action = InterpretedAction(
            action_type=ActionType.ATTACK,
            actor_name="JeanTest",
            target_name="NonExistentEnemy",  # not in state
            weapon_name="Longsword",
            raw_input="",
        )
        outcome = await pipeline._resolve_mechanics(action)

        # Should not crash, should return a fallback MechanicsOutcome
        from ai.models import MechanicsOutcome
        assert isinstance(outcome, MechanicsOutcome)
        assert len(pipeline._pending_dice_embeds) == 0
```

- [ ] **Step 2.2 — Run tests to confirm they fail**

```bash
uv run pytest tests/bot/test_action_pipeline.py::TestResolvePcAttack -v
```

Expected: 4 failures — `_resolve_pc_attack` doesn't exist yet.

- [ ] **Step 2.3 — Add the `_resolve_pc_attack` method in `bot/action_pipeline.py`**

**Part A:** Add `ATTACK` branch in `_resolve_mechanics` (before the final fallback at line 1119):

In the `_resolve_mechanics` method, insert before the final `return MechanicsOutcome(...)` line at 1119:

```python
        if at == ActionType.ATTACK:
            return self._resolve_pc_attack(action)
```

**Part B:** Add the `_resolve_pc_attack` method. Place it after `_resolve_talk_in_combat` (search for that method name to find the right location). Add:

```python
    def _resolve_pc_attack(self, action: InterpretedAction) -> MechanicsOutcome:
        """Resolve a player weapon attack in combat.

        Calls engine.combat.resolve_attack() which mutates defender HP in-place.
        Queues an AttackResult on _pending_dice_embeds for the turn manager to
        render as a dice embed. Returns a MechanicsOutcome with hp_delta populated.
        """
        from engine.combat import AttackResult, consume_action, resolve_attack
        from engine.inventory import EquipmentSlot, Weapon

        intent = self._build_player_intent(action)
        state = self.combat_state

        if state is None:
            return MechanicsOutcome(
                summary=f"{action.actor_name} performs Attack.",
                player_intent=intent,
            )

        attacker = next(
            (c for c in state.combatants if c.name == action.actor_name and c.is_alive),
            None,
        )
        target = next(
            (c for c in state.combatants if c.name == action.target_name and c.is_alive),
            None,
        )
        if attacker is None or target is None:
            return MechanicsOutcome(
                summary=f"{action.actor_name} performs Attack.",
                player_intent=intent,
            )

        weapon: Weapon | None = None
        for slot in (EquipmentSlot.MAIN_HAND, EquipmentSlot.OFF_HAND):
            item = attacker.inventory.equipped.get(slot)
            if item is not None and isinstance(item, Weapon) and item.name == action.weapon_name:
                weapon = item
                break

        if weapon is None:
            return MechanicsOutcome(
                summary=f"{action.actor_name} performs Attack.",
                player_intent=intent,
            )

        consume_action(attacker)
        result = resolve_attack(attacker, target, weapon)  # mutates target HP in-place

        self._pending_dice_embeds.append(("attack_roll", result, action.actor_name))

        if result.hit:
            summary = (
                f"{action.actor_name} touche {target.name} avec {weapon.name}"
                f" — {result.damage} dégâts"
            )
            facts = (
                f"{target.name} subit {result.damage} dégâts ({result.damage_type.value})."
                + (f" {target.name} est vaincu." if not target.is_alive else "")
            )
            public = PublicEffects(hp_delta={target.name: -result.damage})
        else:
            summary = f"{action.actor_name} rate {target.name} avec {weapon.name}"
            facts = ""
            public = PublicEffects()

        return MechanicsOutcome(
            summary=summary,
            player_intent=intent,
            outcome_facts=facts,
            public_effects=public,
        )
```

**Required imports:** Verify these are already at the top of `bot/action_pipeline.py`. Add if missing:
- `from ai.models import PublicEffects` (likely already present via `MechanicsOutcome`)
- `from engine.combat import ...` — the local imports inside the method avoid circular imports.

- [ ] **Step 2.4 — Run tests to confirm Task 2A passes**

```bash
uv run pytest tests/bot/test_action_pipeline.py::TestResolvePcAttack -v
```

Expected: 4/4 PASS.

### Sub-task 2B — Wire `"attack_roll"` embed in `_flush_dice_embeds`

- [ ] **Step 2.5 — Write failing test for flush**

In `tests/bot/test_turn_manager.py`, add:

```python
class TestFlushDiceEmbeds:
    @pytest.mark.asyncio
    async def test_attack_roll_kind_calls_build_attack_roll_embed(self) -> None:
        """An 'attack_roll' entry in _pending_dice_embeds posts an attack embed."""
        from unittest.mock import AsyncMock, MagicMock, patch
        from engine.combat import AttackResult
        from engine.dice import RollOutcome
        from engine.inventory import DamageType

        pc = _pc()
        enemy = _enemy()
        session = _fake_session([pc, enemy])
        channel = _fake_channel()
        tm = _turn_manager(session, channel)

        fake_result = AttackResult(
            attacker="Aragorn",
            defender="Gobelin",
            weapon_name="Longsword",
            attack_roll=15,
            attack_total=18,
            ac=12,
            hit=True,
            critical=False,
            outcome=RollOutcome.SUCCESS,
            damage=7,
            damage_type=DamageType.SLASHING,
            defender_hp_remaining=5,
        )

        # Build a fake pipeline with the attack_roll embed queued
        from bot.action_pipeline import ActionPipeline
        from unittest.mock import MagicMock
        fake_pipeline = MagicMock(spec=ActionPipeline)
        fake_pipeline._pending_dice_embeds = [("attack_roll", fake_result, "Aragorn")]

        with patch("bot.combat_turn_manager.build_attack_roll_embed") as mock_builder:
            mock_builder.return_value = MagicMock()  # fake embed
            await tm._flush_dice_embeds(fake_pipeline, "Aragorn")

        mock_builder.assert_called_once_with(fake_result, "Aragorn")
        channel.send.assert_awaited_once()
```

- [ ] **Step 2.6 — Run test, confirm it fails**

```bash
uv run pytest tests/bot/test_turn_manager.py::TestFlushDiceEmbeds -v
```

Expected: FAIL — `"attack_roll"` falls to `build_generic_check_embed` (or raises due to type mismatch).

- [ ] **Step 2.7 — Add `"attack_roll"` case in `_flush_dice_embeds` (`bot/combat_turn_manager.py:495–515`)**

Replace the body of `_flush_dice_embeds` with:

```python
    async def _flush_dice_embeds(
        self, pipeline: ActionPipeline, actor_name: str,
    ) -> None:
        """Surface any dice result stashed on the pipeline as an embed."""
        from engine.combat import AttackResult

        dice_embeds = getattr(pipeline, "_pending_dice_embeds", None) or []
        for entry in dice_embeds:
            if not isinstance(entry, tuple) or len(entry) < 2:
                continue
            kind = entry[0]
            result = entry[1]
            name = entry[2] if len(entry) >= 3 else actor_name

            if kind == "attack_roll" and isinstance(result, AttackResult):
                embed = build_attack_roll_embed(result, name)
            elif kind == "flee_check":
                embed = build_save_check_embed(
                    result, label="Tentative de fuite", actor_name=name, ability="DEX",
                )
            else:
                embed = build_generic_check_embed(
                    result, label=str(kind).replace("_", " ").title(), actor_name=name,
                )
            await self._safe_send(embed=embed)
        pipeline._pending_dice_embeds.clear()
```

- [ ] **Step 2.8 — Run tests, confirm Task 2B passes**

```bash
uv run pytest tests/bot/test_turn_manager.py::TestFlushDiceEmbeds -v
```

Expected: PASS.

- [ ] **Step 2.9 — Run full test suite**

```bash
uv run pytest tests/bot/ -v
```

Expected: all green.

- [ ] **Step 2.10 — Commit**

```bash
git add bot/action_pipeline.py bot/combat_turn_manager.py tests/bot/test_action_pipeline.py tests/bot/test_turn_manager.py
git commit -m "feat(combat): resolve player weapon attacks with d20 embed — hp_delta, action budget, dice embed"
```

---

## Task 3 — Combat Hub Delete + Repost

Currently `_upsert_hub` calls `hub_message.edit()`, keeping the hub stuck wherever it was first posted. Every subsequent narrative, NPC attack embed, or dice result appears below it. The fix: always delete the old hub and post a fresh one at the current channel bottom.

**Safety note on `_finalize`:** The `_finalize` method (lines 697–708) calls `self.hub_message.edit()` *directly* — not via `_upsert_hub`. It intentionally keeps the frozen "combat terminé" embed in place. This is unaffected by our change.

**Safety note on button interactions:** `_disable_self_and_edit(interaction)` in `CombatActionView` edits `interaction.message` to grey buttons. This completes *before* `dispatch_callback()` runs, which is before `on_action_resolved()` triggers `_upsert_hub`. The sequence is: grey-out hub → pipeline → narrative posted → `_upsert_hub` deletes greyed hub → fresh hub at bottom. No race condition.

**Files:**
- Modify: `bot/combat_turn_manager.py:521–552`
- Test: `tests/bot/test_turn_manager.py`

- [ ] **Step 3.1 — Update `_fake_channel` to include `delete` on the returned message mock**

In `tests/bot/test_turn_manager.py`, update `_fake_channel`:

```python
def _fake_channel() -> MagicMock:
    channel = MagicMock()
    channel.send = AsyncMock(
        return_value=MagicMock(edit=AsyncMock(), delete=AsyncMock()),
    )
    return channel
```

- [ ] **Step 3.2 — Write failing tests**

Add to `tests/bot/test_turn_manager.py`:

```python
class TestUpsertHub:
    @pytest.mark.asyncio
    async def test_first_upsert_sends_new_message(self) -> None:
        """When hub_message is None, a new message is sent."""
        import discord
        pc = _pc()
        enemy = _enemy()
        session = _fake_session([pc, enemy])
        channel = _fake_channel()
        tm = _turn_manager(session, channel)

        embed = discord.Embed(title="Test")
        await tm._upsert_hub(content="Turn 1", embed=embed, view=None)

        channel.send.assert_awaited_once()
        assert tm.hub_message is not None

    @pytest.mark.asyncio
    async def test_second_upsert_deletes_old_and_sends_new(self) -> None:
        """On second call, old hub is deleted and a fresh message is posted."""
        import discord
        pc = _pc()
        enemy = _enemy()
        session = _fake_session([pc, enemy])
        channel = _fake_channel()
        tm = _turn_manager(session, channel)

        embed = discord.Embed(title="Test")
        await tm._upsert_hub(content="Turn 1", embed=embed, view=None)
        old_hub = tm.hub_message

        embed2 = discord.Embed(title="Test 2")
        await tm._upsert_hub(content="Turn 2", embed=embed2, view=None)

        # Old message was deleted
        old_hub.delete.assert_awaited_once()
        # New message was sent (send called twice total)
        assert channel.send.await_count == 2
        # hub_message reference updated to the new message
        assert tm.hub_message is not old_hub

    @pytest.mark.asyncio
    async def test_upsert_tolerates_discord_not_found_on_delete(self) -> None:
        """If old hub is already gone (NotFound), delete error is swallowed."""
        import discord
        pc = _pc()
        enemy = _enemy()
        session = _fake_session([pc, enemy])
        channel = _fake_channel()
        tm = _turn_manager(session, channel)

        embed = discord.Embed(title="Test")
        await tm._upsert_hub(content="Turn 1", embed=embed, view=None)
        tm.hub_message.delete = AsyncMock(
            side_effect=discord.HTTPException(MagicMock(), "Not found"),
        )

        embed2 = discord.Embed(title="Turn 2")
        # Must not raise
        await tm._upsert_hub(content="Turn 2", embed=embed2, view=None)

        assert channel.send.await_count == 2
```

- [ ] **Step 3.3 — Run tests, confirm they fail**

```bash
uv run pytest tests/bot/test_turn_manager.py::TestUpsertHub -v
```

Expected: `test_second_upsert_deletes_old_and_sends_new` fails — old hub is edited, not deleted.

- [ ] **Step 3.4 — Replace `_upsert_hub` in `bot/combat_turn_manager.py`**

Replace lines 521–552 with:

```python
    async def _upsert_hub(
        self,
        *,
        content: str,
        embed: discord.Embed,
        view: discord.ui.View | None,
    ) -> None:
        """Delete the old hub and post a fresh one at the channel bottom.

        Re-posting on every turn start keeps the combat panel visible at the
        most recent position in the channel as narrative messages accumulate.
        _finalize() edits hub_message directly and is not affected.
        """
        send_view: Any = view if view is not None else discord.utils.MISSING

        # Capture and clear the reference before awaiting to avoid a
        # stale reference if an exception interrupts the delete.
        old = self.hub_message
        self.hub_message = None

        if old is not None:
            try:
                await old.delete()
            except discord.HTTPException:
                pass  # Already gone — safe to continue.

        try:
            self.hub_message = await self.channel.send(
                content=content, embed=embed, view=send_view,
            )
        except discord.HTTPException as exc:
            logger.warning("TurnManager hub send failed: %s", exc)
```

- [ ] **Step 3.5 — Run Task 3 tests**

```bash
uv run pytest tests/bot/test_turn_manager.py::TestUpsertHub -v
```

Expected: 3/3 PASS.

- [ ] **Step 3.6 — Run full test suite**

```bash
uv run pytest tests/bot/ -v
```

Expected: all green.

- [ ] **Step 3.7 — Commit**

```bash
git add bot/combat_turn_manager.py tests/bot/test_turn_manager.py
git commit -m "fix(combat): re-post combat hub at channel bottom on each turn — hub no longer sticks at top"
```

---

## Task 4 — Zone Initialization on Combat Start

After `start_combat()` in `bot/action_pipeline.py:654`, all `Combatant.current_zone` fields are `None`. The combat embed calls `c.current_zone == zone.name` and falls all combatants into the "Hors zone" bucket. Fix: after bootstrapping, if the location has `combat_zones`, assign each combatant to a zone — PCs to the first zone, enemies to the last zone (same as first when there is only one).

**Important:** This is a direct assignment, not a call to `move_combatant_to_zone()`. The movement function enforces adjacency and action budget — both irrelevant at combat start.

**Files:**
- Modify: `bot/action_pipeline.py` (add helper function + call after `start_combat()`)
- Test: `tests/bot/test_action_pipeline.py`

- [ ] **Step 4.1 — Write failing tests**

Add to `tests/bot/test_action_pipeline.py`:

```python
from world.location import Location
from world.combat_zone import Zone
from engine.combat import CombatSide, CombatState, Combatant


def _location_two_zones() -> Location:
    return Location(
        name="Allée",
        combat_zones=[
            Zone(name="Entrée", adjacent_zone_names=["Sortie"]),
            Zone(name="Sortie", adjacent_zone_names=["Entrée"]),
        ],
    )


def _location_one_zone() -> Location:
    return Location(
        name="Salle",
        combat_zones=[Zone(name="Centre", adjacent_zone_names=[])],
    )


def _location_no_zones() -> Location:
    return Location(name="Champ")


class TestAssignInitialZones:
    def test_pcs_get_first_zone_npcs_get_last_zone(self) -> None:
        """With 2 zones: PCs → zones[0], NPCs → zones[1]."""
        from bot.action_pipeline import _assign_initial_zones

        pc = _make_pc_combatant("Hero")
        npc = _make_enemy_combatant("Gobelin")
        state = _active_combat_state(pc, npc)
        location = _location_two_zones()

        _assign_initial_zones(state, location)

        assert pc.current_zone == "Entrée"
        assert npc.current_zone == "Sortie"

    def test_single_zone_all_combatants_placed(self) -> None:
        """With 1 zone: everyone → zones[0]."""
        from bot.action_pipeline import _assign_initial_zones

        pc = _make_pc_combatant("Hero")
        npc = _make_enemy_combatant("Gobelin")
        state = _active_combat_state(pc, npc)
        location = _location_one_zone()

        _assign_initial_zones(state, location)

        assert pc.current_zone == "Centre"
        assert npc.current_zone == "Centre"

    def test_no_zones_leaves_current_zone_none(self) -> None:
        """Location with no combat_zones → combatants stay zone-less."""
        from bot.action_pipeline import _assign_initial_zones

        pc = _make_pc_combatant("Hero")
        npc = _make_enemy_combatant("Gobelin")
        state = _active_combat_state(pc, npc)
        location = _location_no_zones()

        _assign_initial_zones(state, location)

        assert pc.current_zone is None
        assert npc.current_zone is None

    def test_pre_assigned_zone_not_overwritten(self) -> None:
        """A combatant already holding a zone is left alone."""
        from bot.action_pipeline import _assign_initial_zones

        pc = _make_pc_combatant("Hero")
        pc.current_zone = "Sortie"  # pre-assigned
        npc = _make_enemy_combatant("Gobelin")
        state = _active_combat_state(pc, npc)
        location = _location_two_zones()

        _assign_initial_zones(state, location)

        assert pc.current_zone == "Sortie"  # unchanged
        assert npc.current_zone == "Sortie"
```

- [ ] **Step 4.2 — Run tests, confirm they fail**

```bash
uv run pytest tests/bot/test_action_pipeline.py::TestAssignInitialZones -v
```

Expected: 4 failures — `_assign_initial_zones` not yet defined.

- [ ] **Step 4.3 — Add `_assign_initial_zones` to `bot/action_pipeline.py`**

Add as a module-level function (not a method), near the top of the file after the imports block. Place it before the `ActionPipeline` class definition:

```python
def _assign_initial_zones(state: "CombatState", location: "Location") -> None:
    """Place combatants into combat zones at the start of an encounter.

    PCs are assigned to zones[0]; enemies to zones[-1] (same as zones[0]
    when there is only one zone). Combatants that already have a zone
    (current_zone is not None) are left unchanged.

    This is a direct field assignment — not move_combatant_to_zone(), which
    enforces adjacency and action budget constraints irrelevant at combat start.
    """
    zones = location.combat_zones
    if not zones:
        return
    pc_zone = zones[0].name
    npc_zone = zones[-1].name  # same as pc_zone when len(zones) == 1
    for combatant in state.combatants:
        if combatant.current_zone is None:
            combatant.current_zone = (
                pc_zone if combatant.side == CombatSide.PLAYER else npc_zone
            )
```

**Required imports at module level** (add if missing):
- `from engine.combat import CombatSide` — already imported in the file.
- The type hints `"CombatState"` and `"Location"` use forward references (strings) so no circular import.

- [ ] **Step 4.4 — Hook `_assign_initial_zones` after `start_combat()` in `_validate`**

In `bot/action_pipeline.py`, find the bootstrap block (around line 650–657):

```python
                pre_state = enter_combat(self.session, trigger)
                # ...
                self.combat_state = start_combat(pre_state.combatants, trigger=trigger)
                self.session.combat_state = self.combat_state
                self._pending_combat_start_embed = (self.combat_state, trigger)
```

After `self._pending_combat_start_embed = ...`, add:

```python
                if self.location is not None and self.location.has_combat_zones():
                    _assign_initial_zones(self.combat_state, self.location)
```

- [ ] **Step 4.5 — Run Task 4 tests**

```bash
uv run pytest tests/bot/test_action_pipeline.py::TestAssignInitialZones -v
```

Expected: 4/4 PASS.

- [ ] **Step 4.6 — Run full test suite**

```bash
uv run pytest -x
```

Expected: all green.

- [ ] **Step 4.7 — Run linting**

```bash
uv run ruff check . && uv run mypy .
```

Expected: no new errors.

- [ ] **Step 4.8 — Commit**

```bash
git add bot/action_pipeline.py tests/bot/test_action_pipeline.py
git commit -m "fix(combat): assign initial combat zones on bootstrap — combatants no longer stuck as Hors zone"
```

---

## Verification

End-to-end test after all tasks complete:

1. **All tests green:**
   ```bash
   uv run pytest -x --tb=short
   ```

2. **No linting errors:**
   ```bash
   uv run ruff check . && uv run mypy .
   ```

3. **Discord live test** (via `discord-live-testing` skill):
   - Start a campaign, create Fighter with Longsword
   - Type "j'attaque avec mon épée" → attack resolves, d20 embed appears below narrative
   - Click "Attaquer" button, pick target → d20 embed appears, combat hub re-posts at bottom
   - Verify zones show "Entrée" / "Sortie" (or whichever zones exist), not "Hors zone"
   - Run 3 turns and confirm hub is always the last message in the channel

---

## Self-Review

**Spec coverage:**
- ✅ Weapon alias resolution (épée → Longsword): Task 1
- ✅ Player d20 roll embed visible: Task 2
- ✅ Hub re-posted at bottom after each turn: Task 3
- ✅ Combatants placed in zones on combat start: Task 4
- ✅ `consume_action` called before attack (action budget): Task 2, Step 2.3
- ✅ `hp_delta` populated in `PublicEffects`: Task 2, tests assert this

**No placeholders:** All test code and implementation code is complete.

**Type consistency:**
- `_assign_initial_zones(state: "CombatState", location: "Location")` — used in both Task 4.3 (definition) and Task 4.4 (call): consistent.
- `_pending_dice_embeds.append(("attack_roll", result, action.actor_name))` in Task 2.3, consumed via `kind == "attack_roll"` check in Task 2.7: consistent.
- `AttackResult` fields used in tests (`attack_roll`, `attack_total`, `ac`, `hit`, `critical`, `outcome`, `damage`, `damage_type`, `defender_hp_remaining`) match the actual model at `engine/combat.py:208–222`.
