# Phase 3 — Validation & Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implémenter les Task 30 (validators stricts D&D 5e), Task 31 (ActionPipeline dispatch combat-aware + MOVE→FLEE), et Task 32 (résolution de Flee avec check DEX).

**Architecture:**
- Task 30 enrichit `engine/validators.py` (pur Python, aucun LLM) avec les checks action budget, friendly fire, range zone-aware, et SURPRISED safety net.
- Task 31 refond `ActionPipeline._validate` pour utiliser `detect_combat_trigger` + `enter_combat` + `start_combat` au lieu de `_bootstrap_combat_against`, et auto-convertit MOVE→FLEE en combat.
- Task 32 ajoute `_resolve_flee` dans `ActionPipeline` : roll DEX DC 12, marque `fled=True` sur succès, termine le combat quand tous les PCs ont fui.

**Tech Stack:** Python 3.12, Pydantic v2, pytest, ruff, mypy — `uv run` pour tout.

---

## Pré-lecture obligatoire

Avant de démarrer : lire [tasks/combat/PRE_IMPLEMENTATION_FIXES.md](../../tasks/combat/PRE_IMPLEMENTATION_FIXES.md) en entier.  
Bonne nouvelle : les fixes #3, #4, #5 (champs `Combatant`/`CombatState`) sont **déjà appliqués** dans le code actuel. Les fixes #1 (`CombatTrigger` dans `engine/`) est aussi déjà fait. Aucun pre-fix à appliquer pour Phase 3.

## État du code avant Phase 3

- `engine/combat.py` : `Combatant.fled`, `advance_turn` (skip fled), `check_combat_end` (FLED) — **tous déjà implémentés** (Phase 2 ✅).
- `bot/combat_entry.py` : `detect_combat_trigger`, `enter_combat` — **déjà implémentés** (Phase 2 ✅).
- `engine/validators.py` : `validate_action` dispatcher existe mais manque SURPRISED, action budget, range. `validate_attack` manque friendly fire, budget, range. `validate_cast_spell` manque budget casting_time. `validate_move_in_combat` n'existe pas encore.
- `bot/action_pipeline.py` : `_validate` utilise encore `_bootstrap_combat_against` (sans initiative roll), pas de MOVE→FLEE, pas de `_pending_flee_destination`, pas de `_resolve_flee`.

## Mapping fichiers → tâches

| Fichier | Tâche | Nature |
|---------|-------|--------|
| `engine/validators.py` | Task 30 | Modify |
| `tests/test_validators.py` | Task 30 | Modify (add tests) |
| `bot/action_pipeline.py` | Tasks 31 & 32 | Modify |
| `tests/bot/test_action_pipeline.py` | Tasks 31 & 32 | Modify (add tests) |
| `docs/internal/ACTION_PIPELINE.md` | Tasks 30–32 | Modify (doc) |
| `docs/internal/STATE.md` | Tasks 30–32 | Modify (avancement) |

---

## Task 1 — Validators stricts (spec Task 30)

**Files:**
- Modify: `engine/validators.py`
- Test: `tests/test_validators.py`

### Changements à `engine/validators.py`

Les modifications sont indépendantes et peuvent se faire dans l'ordre indiqué.

- [ ] **Step 1.1 — Ajouter imports manquants**

Ouvrir `engine/validators.py`. Ligne 12, la section imports. Ajouter les imports suivants juste après `from engine.conditions import cannot_move, is_incapacitated` :

```python
from engine.conditions import cannot_move, is_incapacitated, is_surprised
from engine.inventory import EquipmentSlot, Weapon, WeaponCategory, WeaponProperty
from engine.spells import SPELL_CATALOG, CastingTime, can_cast_spell
```

Remplacer les lignes d'import existantes de `engine.conditions`, `engine.inventory` et `engine.spells` par ce bloc unique. Vérification : `uv run ruff check engine/validators.py` — 0 erreur.

- [ ] **Step 1.2 — Ajouter `_check_range` helper**

Dans `engine/validators.py`, dans la section "Private helpers" (après `_validate_common`), ajouter :

```python
def _check_range(
    attacker: Combatant,
    target: Combatant,
    weapon: Weapon | None,
) -> bool:
    """Zone-aware range check. Melee = same zone only; ranged = any zone.

    Returns True for zoneless combats (current_zone is None on either side)
    since all combatants are considered adjacent.
    """
    if attacker.current_zone is None or target.current_zone is None:
        return True  # zoneless combat — everyone in range
    if attacker.current_zone == target.current_zone:
        return True  # point-blank, any weapon type

    if weapon is None:
        return False  # unarmed = melee only

    is_ranged = weapon.weapon_category in (
        WeaponCategory.SIMPLE_RANGED,
        WeaponCategory.MARTIAL_RANGED,
    )
    is_thrown = WeaponProperty.THROWN in weapon.properties
    return is_ranged or is_thrown
```

- [ ] **Step 1.3 — Écrire les tests qui vont échouer (action budget, friendly fire, range)**

Dans `tests/test_validators.py`, ajouter à la fin du fichier (après les fixtures existantes, utiliser les fixtures existantes `fighter_combatant`, etc.) :

```python
# ---------------------------------------------------------------------------
# Task 30 — Strict validators
# ---------------------------------------------------------------------------


def _make_state(actor: Combatant, target: Combatant) -> CombatState:
    """Helper: build a two-combatant CombatState with actor at index 0."""
    return CombatState(combatants=[actor, target], current_turn_index=0)


@pytest.fixture()
def enemy_combatant(fighter_combatant: Combatant) -> Combatant:
    """Same stats as fighter but on the ENEMY side, unarmed."""
    from engine.combat import CombatSide
    from engine.inventory import Inventory
    c = fighter_combatant.model_copy(deep=True)
    c.name = "Goblin"
    c.side = CombatSide.ENEMY
    c.inventory = Inventory()
    return c


@pytest.fixture()
def ally_combatant(fighter_combatant: Combatant) -> Combatant:
    """Same stats, same PLAYER side — for friendly fire test."""
    c = fighter_combatant.model_copy(deep=True)
    c.name = "Ally Fighter"
    return c


def test_validate_attack_rejects_if_action_already_used(
    fighter_combatant: Combatant,
    enemy_combatant: Combatant,
) -> None:
    fighter_combatant.action_budget.action_used = True
    state = _make_state(fighter_combatant, enemy_combatant)
    action = Action(
        actor_name=fighter_combatant.name,
        action_type=ActionType.ATTACK,
        target_name=enemy_combatant.name,
        weapon_name="Longsword",
    )
    result = validate_attack(action, state)
    assert not result.is_valid
    assert "Action" in (result.error_message or "")


def test_validate_attack_rejects_friendly_fire(
    fighter_combatant: Combatant,
    ally_combatant: Combatant,
) -> None:
    from engine.inventory import EquipmentSlot
    # Equip sword on ally so weapon check passes first
    ally_combatant.inventory.equipped[EquipmentSlot.MAIN_HAND] = (
        fighter_combatant.inventory.equipped[EquipmentSlot.MAIN_HAND]
    )
    state = _make_state(fighter_combatant, ally_combatant)
    action = Action(
        actor_name=fighter_combatant.name,
        action_type=ActionType.ATTACK,
        target_name=ally_combatant.name,
        weapon_name="Longsword",
    )
    result = validate_attack(action, state)
    assert not result.is_valid
    assert "ally" in (result.error_message or "").lower()


def test_validate_attack_rejects_out_of_range_melee_cross_zone(
    fighter_combatant: Combatant,
    enemy_combatant: Combatant,
) -> None:
    fighter_combatant.current_zone = "zone_a"
    enemy_combatant.current_zone = "zone_b"
    state = _make_state(fighter_combatant, enemy_combatant)
    action = Action(
        actor_name=fighter_combatant.name,
        action_type=ActionType.ATTACK,
        target_name=enemy_combatant.name,
        weapon_name="Longsword",  # melee weapon
    )
    result = validate_attack(action, state)
    assert not result.is_valid
    assert "range" in (result.error_message or "").lower()


def test_validate_attack_allows_ranged_cross_zone(
    fighter_combatant: Combatant,
    enemy_combatant: Combatant,
) -> None:
    from engine.inventory import DamageType, Item, ItemType, Weapon, WeaponCategory
    bow = Weapon(
        name="Shortbow",
        item_type=ItemType.WEAPON,
        damage_dice="1d6",
        damage_type=DamageType.PIERCING,
        weapon_category=WeaponCategory.SIMPLE_RANGED,
        properties=[WeaponProperty.AMMUNITION],
    )
    fighter_combatant.inventory.equipped[EquipmentSlot.MAIN_HAND] = bow
    fighter_combatant.current_zone = "zone_a"
    enemy_combatant.current_zone = "zone_b"
    state = _make_state(fighter_combatant, enemy_combatant)
    action = Action(
        actor_name=fighter_combatant.name,
        action_type=ActionType.ATTACK,
        target_name=enemy_combatant.name,
        weapon_name="Shortbow",
    )
    result = validate_attack(action, state)
    assert result.is_valid


def test_validate_move_in_combat_rejects_without_movement(
    fighter_combatant: Combatant,
    enemy_combatant: Combatant,
) -> None:
    from engine.validators import validate_move_in_combat
    fighter_combatant.action_budget.movement_remaining_feet = 0
    state = _make_state(fighter_combatant, enemy_combatant)
    action = Action(
        actor_name=fighter_combatant.name,
        action_type=ActionType.MOVE,
        target_name="zone_b",
    )
    result = validate_move_in_combat(action, state)
    assert not result.is_valid
    assert "movement" in (result.error_message or "").lower()


def test_validate_move_in_combat_rejects_while_restrained(
    fighter_combatant: Combatant,
    enemy_combatant: Combatant,
) -> None:
    from engine.validators import validate_move_in_combat
    fighter_combatant.conditions.append(
        ActiveCondition(condition_type=ConditionType.RESTRAINED, source="test")
    )
    state = _make_state(fighter_combatant, enemy_combatant)
    action = Action(
        actor_name=fighter_combatant.name,
        action_type=ActionType.MOVE,
        target_name="zone_b",
    )
    result = validate_move_in_combat(action, state)
    assert not result.is_valid


def test_validate_cast_spell_rejects_if_action_budget_used(
    fighter_combatant: Combatant,
    enemy_combatant: Combatant,
) -> None:
    from engine.spells import SpellcasterState, SPELL_CATALOG
    fighter_combatant.spellcaster = SpellcasterState(
        known_spells=["Magic Missile"],
        spell_slots={1: 2},
    )
    fighter_combatant.action_budget.action_used = True
    state = _make_state(fighter_combatant, enemy_combatant)
    action = Action(
        actor_name=fighter_combatant.name,
        action_type=ActionType.CAST_SPELL,
        spell_name="Magic Missile",
        target_name=enemy_combatant.name,
    )
    result = validate_cast_spell(action, state)
    assert not result.is_valid
    assert "Action" in (result.error_message or "")


def test_validate_cast_spell_rejects_if_bonus_action_budget_used(
    fighter_combatant: Combatant,
    enemy_combatant: Combatant,
) -> None:
    from engine.spells import SpellcasterState
    fighter_combatant.spellcaster = SpellcasterState(
        known_spells=["Healing Word"],
        spell_slots={1: 2},
    )
    fighter_combatant.action_budget.bonus_action_used = True
    state = _make_state(fighter_combatant, enemy_combatant)
    action = Action(
        actor_name=fighter_combatant.name,
        action_type=ActionType.CAST_SPELL,
        spell_name="Healing Word",
        target_name=fighter_combatant.name,
    )
    result = validate_cast_spell(action, state)
    assert not result.is_valid
    assert "Bonus" in (result.error_message or "")


def test_validate_action_rejects_surprised_combatant(
    fighter_combatant: Combatant,
    enemy_combatant: Combatant,
) -> None:
    fighter_combatant.conditions.append(
        ActiveCondition(condition_type=ConditionType.SURPRISED, source="ambush")
    )
    state = _make_state(fighter_combatant, enemy_combatant)
    action = Action(
        actor_name=fighter_combatant.name,
        action_type=ActionType.ATTACK,
        target_name=enemy_combatant.name,
        weapon_name="Longsword",
    )
    result = validate_action(action, state)
    assert not result.is_valid
    assert "surpris" in (result.error_message or "").lower()


def test_validate_exploration_rejects_move_in_combat(
    fighter_combatant: Combatant,
    enemy_combatant: Combatant,
) -> None:
    state = _make_state(fighter_combatant, enemy_combatant)
    action = Action(
        actor_name=fighter_combatant.name,
        action_type=ActionType.MOVE,
        target_name="taverne",
    )
    result = validate_exploration_action(action, combat_state=state)
    assert not result.is_valid


def test_validate_exploration_allows_look_in_combat(
    fighter_combatant: Combatant,
    enemy_combatant: Combatant,
) -> None:
    state = _make_state(fighter_combatant, enemy_combatant)
    action = Action(
        actor_name=fighter_combatant.name,
        action_type=ActionType.LOOK,
    )
    result = validate_exploration_action(action, combat_state=state)
    assert result.is_valid
```

- [ ] **Step 1.4 — Vérifier que les tests échouent**

```bash
uv run pytest tests/test_validators.py -k "task_30 or rejects_if_action or friendly_fire or cross_zone or ranged_cross or move_in_combat or cast_spell_rejects or surprised_combatant or exploration_rejects or exploration_allows" -v 2>&1 | tail -20
```

Attendu : `FAILED` sur les tests qui testent des features manquantes (`action_budget`, `friendly_fire`, `_check_range`, `validate_move_in_combat`, `is_surprised`). Les tests `validate_exploration_*` passeront probablement déjà (implémentés en Task 01).

- [ ] **Step 1.5 — Implémenter `validate_action` (SURPRISED + MOVE dans dispatch)**

Dans `engine/validators.py`, remplacer la fonction `validate_action` entière :

```python
def validate_action(action: Action, combat_state: CombatState) -> ValidationResult:
    """Validate a combat action. Common checks + surprised guard + type dispatch.

    Adds a SURPRISED safety net before dispatching: a surprised combatant
    cannot act (the turn manager should already skip them, but the validator
    enforces it as a belt-and-suspenders check). Unknown action types are
    rejected with a clear message rather than raising KeyError.
    """
    actor = _find_combatant(action.actor_name, combat_state)
    if actor is not None and is_surprised(actor.conditions):
        return ValidationResult(
            is_valid=False,
            error_message=f"'{action.actor_name}' est surpris et ne peut rien faire ce tour.",
        )

    validators: dict[ActionType, Any] = {
        ActionType.ATTACK: validate_attack,
        ActionType.CAST_SPELL: validate_cast_spell,
        ActionType.DEFEND: validate_defend,
        ActionType.DISENGAGE: validate_disengage,
        ActionType.FLEE: validate_flee,
        ActionType.USE_ITEM: validate_use_item,
        ActionType.MOVE: validate_move_in_combat,
    }
    validator = validators.get(action.action_type)
    if validator is None:
        return ValidationResult(
            is_valid=False,
            error_message=f"'{action.action_type.value}' n'est pas une action de combat valide.",
        )
    return validator(action, combat_state)
```

Ajouter `from typing import Any` en tête de fichier si absent.

- [ ] **Step 1.6 — Implémenter `validate_move_in_combat`**

Dans `engine/validators.py`, juste avant `validate_use_item`, ajouter la nouvelle fonction :

```python
def validate_move_in_combat(action: Action, state: CombatState) -> ValidationResult:
    """Validate a Move action in combat: movement budget + cannot_move conditions.

    Adjacency check (whether the target zone is actually adjacent) is deferred
    to resolution — the validator only verifies that the combatant *can* move
    and has movement left this turn. A target zone name is required.
    """
    common = _validate_common(action, state)
    if common is not None:
        return common

    actor = _find_combatant(action.actor_name, state)
    assert actor is not None  # checked in _validate_common

    if cannot_move(actor.conditions):
        return ValidationResult(
            is_valid=False,
            error_message=f"'{action.actor_name}' ne peut pas se déplacer (entravé/agrippé/etc.).",
        )
    if actor.action_budget.movement_remaining_feet <= 0:
        return ValidationResult(
            is_valid=False,
            error_message=f"'{action.actor_name}' n'a plus de mouvement ce tour.",
        )
    if action.target_name is None:
        return ValidationResult(
            is_valid=False,
            error_message="Move nécessite un nom de zone cible.",
        )
    return ValidationResult(is_valid=True)
```

- [ ] **Step 1.7 — Enrichir `validate_attack` (action budget + friendly fire + range)**

Remplacer la fonction `validate_attack` entière par :

```python
def validate_attack(action: Action, state: CombatState) -> ValidationResult:
    """Validate an attack action.

    Checks (in order): common checks, action budget, target exists and alive,
    no friendly fire, weapon equipped, zone-based range.
    """
    common = _validate_common(action, state)
    if common is not None:
        return common

    actor = _find_combatant(action.actor_name, state)
    assert actor is not None  # checked in _validate_common

    # Action economy
    if actor.action_budget.action_used:
        return ValidationResult(
            is_valid=False,
            error_message=f"'{action.actor_name}' a déjà utilisé son Action ce tour.",
        )

    # Target required
    if action.target_name is None:
        return ValidationResult(
            is_valid=False, error_message="Attack requires a target"
        )

    target = _find_combatant(action.target_name, state)
    if target is None:
        return ValidationResult(
            is_valid=False,
            error_message=f"Target '{action.target_name}' is not in combat",
        )
    if not target.is_alive:
        return ValidationResult(
            is_valid=False,
            error_message=f"Target '{action.target_name}' is already dead",
        )

    # No friendly fire
    if target.side == actor.side:
        return ValidationResult(
            is_valid=False,
            error_message=f"Impossible d'attaquer l'allié '{action.target_name}'.",
        )

    # Weapon check: need weapon_name and it must be equipped
    if action.weapon_name is None:
        return ValidationResult(
            is_valid=False, error_message="Attack requires a weapon"
        )

    weapon_obj: Weapon | None = None
    for slot in (EquipmentSlot.MAIN_HAND, EquipmentSlot.OFF_HAND):
        item = actor.inventory.equipped.get(slot)
        if item is not None and isinstance(item, Weapon) and item.name == action.weapon_name:
            weapon_obj = item
            break

    if weapon_obj is None:
        return ValidationResult(
            is_valid=False,
            error_message=f"Weapon '{action.weapon_name}' is not equipped",
        )

    # Zone-based range check
    if not _check_range(actor, target, weapon_obj):
        return ValidationResult(
            is_valid=False,
            error_message=(
                f"'{action.target_name}' est hors de portée de "
                f"'{action.weapon_name}' (zones différentes, arme de mêlée)."
            ),
        )

    return ValidationResult(is_valid=True)
```

- [ ] **Step 1.8 — Enrichir `validate_cast_spell` (action/bonus budget)**

Dans `validate_cast_spell`, après la vérification du `spellcaster` (ligne `if actor.spellcaster is None`) et après avoir obtenu le `spell`, ajouter le bloc budget AVANT le check `can_cast_spell` :

```python
    # Action economy based on casting time
    if spell.casting_time == CastingTime.ACTION and actor.action_budget.action_used:
        return ValidationResult(
            is_valid=False,
            error_message=f"'{action.actor_name}' a déjà utilisé son Action ce tour.",
        )
    if spell.casting_time == CastingTime.BONUS_ACTION and actor.action_budget.bonus_action_used:
        return ValidationResult(
            is_valid=False,
            error_message=f"'{action.actor_name}' a déjà utilisé sa Bonus Action ce tour.",
        )
    if spell.casting_time == CastingTime.REACTION and actor.action_budget.reaction_used_this_round:
        return ValidationResult(
            is_valid=False,
            error_message=f"'{action.actor_name}' a déjà utilisé sa Réaction ce round.",
        )
```

Insérer ces lignes juste avant `if not can_cast_spell(actor.spellcaster, spell):`.

- [ ] **Step 1.9 — Vérifier que les tests passent**

```bash
uv run pytest tests/test_validators.py -v 2>&1 | tail -30
```

Attendu : tous les tests passent. S'il y a des `NameError` sur `Any`, ajouter `from typing import Any` aux imports de `validators.py`.

- [ ] **Step 1.10 — Linter + type check**

```bash
uv run ruff check engine/validators.py
uv run mypy engine/validators.py
```

Attendu : 0 erreur. Corriger si nécessaire.

- [ ] **Step 1.11 — Régression complète**

```bash
uv run pytest tests/ -x -q 2>&1 | tail -20
```

Attendu : tous les tests passent.

- [ ] **Step 1.12 — Mettre à jour les docs internes**

Dans `docs/internal/ACTION_PIPELINE.md`, section "Validation", ajouter ou remplacer (si elle existe) la description des validators :

```markdown
### Validators stricts (Task 30)

`engine/validators.py::validate_action` est le dispatcher combat. Il applique dans l'ordre :
1. **SURPRISED safety net** : un combattant surpris ne peut rien faire ce tour.
2. **Dispatch par type** : ATTACK, CAST_SPELL, DEFEND, DISENGAGE, FLEE, USE_ITEM, MOVE → `validate_move_in_combat`.
3. Types inconnus → rejet explicite (pas de KeyError).

Règles par type :
- **ATTACK** : budget Action, cible vivante, pas de friendly fire, arme équipée, range zone-aware (`_check_range` : mêlée = même zone, ranged/thrown = toute zone).
- **CAST_SPELL** : budget Action/Bonus/Réaction selon `spell.casting_time`, slot dispo.
- **MOVE** (`validate_move_in_combat`) : budget mouvement, `cannot_move`, zone cible requise.
- **EXPLORE** en combat : seuls LOOK/QUESTION/IMPROVISE passent.
```

Dans `docs/internal/STATE.md`, passer les lignes Task 30 de ❌ à ✅.

- [ ] **Step 1.13 — Commit**

```bash
git add engine/validators.py tests/test_validators.py docs/internal/
git commit -m "feat(combat): Task 30 — strict D&D 5e combat validators"
```

---

## Task 2 — ActionPipeline dispatch combat-aware (spec Task 31)

**Files:**
- Modify: `bot/action_pipeline.py` (méthode `_validate`, nouveaux champs dataclass, imports)
- Test: `tests/bot/test_action_pipeline.py`

### Contexte

La méthode `_validate` actuelle (lignes ~524–570 dans `bot/action_pipeline.py`) utilise `_bootstrap_combat_against` qui :
- Ne roule pas l'initiative
- Met toujours l'attaquant en premier
- N'utilise pas `detect_combat_trigger` / `enter_combat` / `start_combat`

Task 31 remplace cette logique par le pipeline propre du combat entry.

**Point d'attention — comportement BOTH_READY** : Quand un joueur attaque un NPC hostile (BOTH_READY), `start_combat` roule l'initiative. Si le NPC gagne, il n'est pas au `current_turn_index=0` → `validate_attack` échouera (`_is_actors_turn` false). C'est un comportement correct D&D 5e (le NPC réagit en premier). Pour les NPCs neutres/amicaux, `PLAYERS` surprise s'applique et l'agresseur est toujours placé en premier.

- [ ] **Step 2.1 — Écrire les tests qui vont échouer**

Dans `tests/bot/test_action_pipeline.py`, ajouter à la fin du fichier (lire d'abord le fichier existant pour comprendre les fixtures en place) :

```python
# ---------------------------------------------------------------------------
# Task 31 — Combat dispatch
# ---------------------------------------------------------------------------


def _make_pipeline_with_combat(combat_state: "CombatState") -> ActionPipeline:
    """Create a minimal ActionPipeline with a pre-existing CombatState."""
    from unittest.mock import MagicMock
    pipeline = ActionPipeline(
        interpreter=MagicMock(),
        narrator=MagicMock(),
        location=None,
        npcs={},
        actor_name="Héros",
        combat_state=combat_state,
    )
    return pipeline


def test_pipeline_autoconverts_move_to_flee_in_active_combat() -> None:
    """MOVE in active combat → action_type becomes FLEE."""
    from engine.combat import CombatSide, CombatState, Combatant, ActionBudget
    from engine.character import Character, CharacterClass, Race, Size, AbilityScores
    from engine.inventory import Inventory
    from ai.models import InterpretedAction
    from engine.validators import ActionType

    char = Character(
        name="Héros", character_class=CharacterClass.FIGHTER, race=Race.HUMAN,
        level=1, size=Size.MEDIUM,
        ability_scores=AbilityScores(STR=16, DEX=14, CON=14, INT=10, WIS=12, CHA=8),
    )
    combatant = Combatant(
        name="Héros", side=CombatSide.PLAYER, character=char, inventory=Inventory(),
    )
    enemy_char = char.model_copy(update={"name": "Goblin"})
    enemy = Combatant(
        name="Goblin", side=CombatSide.ENEMY, character=enemy_char, inventory=Inventory(),
    )
    state = CombatState(combatants=[combatant, enemy], current_turn_index=0)

    pipeline = _make_pipeline_with_combat(state)
    pipeline.actor_name = "Héros"

    action = InterpretedAction(
        action_type=ActionType.MOVE,
        actor_name="Héros",
        target_name="forêt",
        raw_input="je fuis vers la forêt",
    )
    result = pipeline._validate(action)
    # MOVE→FLEE: the flee validation runs; cannot_move check passes → valid
    # (or fails for another reason, but NOT because MOVE is blocked in combat)
    assert pipeline._pending_flee_destination == "forêt"  # type: ignore[attr-defined]


def test_pipeline_stores_flee_destination() -> None:
    """_pending_flee_destination is set to the original MOVE target_name."""
    from engine.combat import CombatSide, CombatState, Combatant
    from engine.character import Character, CharacterClass, Race, Size, AbilityScores
    from engine.inventory import Inventory
    from ai.models import InterpretedAction
    from engine.validators import ActionType
    from unittest.mock import MagicMock

    char = Character(
        name="Héros", character_class=CharacterClass.FIGHTER, race=Race.HUMAN,
        level=1, size=Size.MEDIUM,
        ability_scores=AbilityScores(STR=16, DEX=14, CON=14, INT=10, WIS=12, CHA=8),
    )
    hero = Combatant(name="Héros", side=CombatSide.PLAYER, character=char, inventory=Inventory())
    goblin_char = char.model_copy(update={"name": "Goblin"})
    goblin = Combatant(name="Goblin", side=CombatSide.ENEMY, character=goblin_char, inventory=Inventory())
    state = CombatState(combatants=[hero, goblin], current_turn_index=0)

    pipeline = ActionPipeline(
        interpreter=MagicMock(), narrator=MagicMock(),
        location=None, npcs={}, actor_name="Héros", combat_state=state,
    )
    action = InterpretedAction(
        action_type=ActionType.MOVE, actor_name="Héros",
        target_name="village", raw_input="vers le village",
    )
    pipeline._validate(action)
    assert pipeline._pending_flee_destination == "village"  # type: ignore[attr-defined]


def test_pipeline_dispatches_to_combat_validator_when_active() -> None:
    """When combat is active, a ATTACK action goes through validate_action (combat path)."""
    from engine.combat import CombatSide, CombatState, Combatant
    from engine.character import Character, CharacterClass, Race, Size, AbilityScores
    from engine.inventory import Inventory, EquipmentSlot, Weapon, WeaponCategory, DamageType, ItemType
    from ai.models import InterpretedAction
    from engine.validators import ActionType
    from unittest.mock import MagicMock

    char = Character(
        name="Héros", character_class=CharacterClass.FIGHTER, race=Race.HUMAN,
        level=1, size=Size.MEDIUM,
        ability_scores=AbilityScores(STR=16, DEX=14, CON=14, INT=10, WIS=12, CHA=8),
    )
    sword = Weapon(
        name="Longsword", item_type=ItemType.WEAPON,
        damage_dice="1d8", damage_type=DamageType.SLASHING,
        weapon_category=WeaponCategory.MARTIAL_MELEE,
    )
    inv = Inventory()
    inv.equipped[EquipmentSlot.MAIN_HAND] = sword

    hero = Combatant(name="Héros", side=CombatSide.PLAYER, character=char, inventory=inv)
    goblin_char = char.model_copy(update={"name": "Goblin"})
    goblin = Combatant(name="Goblin", side=CombatSide.ENEMY, character=goblin_char, inventory=Inventory())
    state = CombatState(combatants=[hero, goblin], current_turn_index=0)

    pipeline = ActionPipeline(
        interpreter=MagicMock(), narrator=MagicMock(),
        location=None, npcs={}, actor_name="Héros", combat_state=state,
    )
    action = InterpretedAction(
        action_type=ActionType.ATTACK, actor_name="Héros",
        target_name="Goblin", weapon_name="Longsword", raw_input="j'attaque",
    )
    result = pipeline._validate(action)
    assert result.is_valid


def test_pipeline_dispatches_to_exploration_validator_when_inactive() -> None:
    """When no combat, a LOOK action goes through validate_exploration_action."""
    from ai.models import InterpretedAction
    from engine.validators import ActionType
    from unittest.mock import MagicMock

    pipeline = ActionPipeline(
        interpreter=MagicMock(), narrator=MagicMock(),
        location=None, npcs={}, actor_name="Héros", combat_state=None,
    )
    action = InterpretedAction(
        action_type=ActionType.LOOK, actor_name="Héros", raw_input="je regarde",
    )
    result = pipeline._validate(action)
    assert result.is_valid


def test_pipeline_exploration_rejected_in_combat_except_info_actions() -> None:
    """TALK is rejected in active combat; LOOK is allowed."""
    from engine.combat import CombatSide, CombatState, Combatant
    from engine.character import Character, CharacterClass, Race, Size, AbilityScores
    from engine.inventory import Inventory
    from ai.models import InterpretedAction
    from engine.validators import ActionType
    from unittest.mock import MagicMock

    char = Character(
        name="Héros", character_class=CharacterClass.FIGHTER, race=Race.HUMAN,
        level=1, size=Size.MEDIUM,
        ability_scores=AbilityScores(STR=16, DEX=14, CON=14, INT=10, WIS=12, CHA=8),
    )
    hero = Combatant(name="Héros", side=CombatSide.PLAYER, character=char, inventory=Inventory())
    goblin_char = char.model_copy(update={"name": "Goblin"})
    goblin = Combatant(name="Goblin", side=CombatSide.ENEMY, character=goblin_char, inventory=Inventory())
    state = CombatState(combatants=[hero, goblin], current_turn_index=0)

    pipeline = ActionPipeline(
        interpreter=MagicMock(), narrator=MagicMock(),
        location=None, npcs={}, actor_name="Héros", combat_state=state,
    )

    talk = InterpretedAction(
        action_type=ActionType.TALK, actor_name="Héros",
        target_name="Goblin", raw_input="je parle",
    )
    look = InterpretedAction(
        action_type=ActionType.LOOK, actor_name="Héros", raw_input="je regarde",
    )
    assert not pipeline._validate(talk).is_valid
    assert pipeline._validate(look).is_valid
```

- [ ] **Step 2.2 — Vérifier que les nouveaux tests échouent**

```bash
uv run pytest tests/bot/test_action_pipeline.py -k "autoconverts or flee_destination or dispatches or exploration_rejected" -v 2>&1 | tail -20
```

Attendu : `AttributeError: 'ActionPipeline' object has no attribute '_pending_flee_destination'` ou similaire.

- [ ] **Step 2.3 — Ajouter les nouveaux champs au dataclass `ActionPipeline`**

Dans `bot/action_pipeline.py`, dans la définition du `@dataclass ActionPipeline` (section champs, après `_trivial_kill_mechanics`), ajouter :

```python
    _pending_flee_destination: str | None = field(default=None, init=False)
    """Destination zone stored when MOVE is auto-converted to FLEE in combat.
    Consumed by _resolve_flee after a successful full-party escape."""

    _pending_combat_start_embed: tuple["CombatState", "CombatTrigger"] | None = field(
        default=None, init=False,
    )
    """Stored by _validate when a new combat is bootstrapped. The caller
    (ActionHandlerCog) reads this after _validate returns and posts the
    combat-start embed before narration."""

    _pending_dice_embeds: list[Any] = field(default_factory=list, init=False)
    """Dice roll results to display as embeds (task 60). Populated by
    _resolve_flee (and future combat resolvers). Consumed by the caller."""
```

Ajouter `from typing import Any` si absent dans les imports (il est probablement déjà là via `TYPE_CHECKING`). Vérifier : `from typing import TYPE_CHECKING, Any`.

- [ ] **Step 2.4 — Ajouter les imports manquants**

Dans `bot/action_pipeline.py`, dans la section imports, ajouter :

```python
from bot.combat_entry import CombatTrigger, detect_combat_trigger, enter_combat
from engine.combat import CombatState, TrivialResolveResult, start_combat, trivial_resolve
```

(Vérifier que `start_combat` et `CombatTrigger` ne sont pas déjà importés — `CombatState` l'est probablement déjà.)

- [ ] **Step 2.5 — Refondre `_validate`**

Remplacer la méthode `_validate` entière (de `def _validate` jusqu'à la dernière ligne `return validate_action(eng_action, self.combat_state)`) par :

```python
    def _validate(self, action: InterpretedAction) -> ValidationResult:
        """Convert InterpretedAction → Action and dispatch to the right validator.

        Dispatch logic (in order):
        1. If combat active AND action is MOVE → auto-convert to FLEE, store destination.
        2. If no combat → try detect_combat_trigger; bootstrap if trigger found.
        3. If combat active → combat validators (validate_action or validate_exploration_action).
        4. If no combat → exploration validators, or trivial-kill check, or error.
        """
        eng_action = Action(
            actor_name=action.actor_name,
            action_type=action.action_type,
            target_name=action.target_name,
            weapon_name=action.weapon_name,
            spell_name=action.spell_name,
            item_name=action.item_name,
        )

        # --- 1. Auto-convert MOVE → FLEE in active combat ---
        if (
            eng_action.action_type == ActionType.MOVE
            and self.combat_state is not None
            and self.combat_state.is_active
        ):
            logger.info(
                "MOVE auto-converted to FLEE campaign=%s actor=%s destination=%s",
                self.campaign_id, action.actor_name, eng_action.target_name,
            )
            self._pending_flee_destination = eng_action.target_name
            eng_action = eng_action.model_copy(
                update={"action_type": ActionType.FLEE, "target_name": None},
            )
            # Fall through to combat dispatch below

        # --- 2. If no combat, try to detect a trigger and bootstrap ---
        if self.combat_state is None or not self.combat_state.is_active:
            trigger: CombatTrigger | None = None
            if self.session is not None:
                trigger = detect_combat_trigger(action, self.session)

            if trigger is not None:
                logger.info(
                    "COMBAT bootstrapped kind=%s campaign=%s aggressor=%s enemies=%s",
                    trigger.kind, self.campaign_id,
                    trigger.aggressor_name, trigger.enemy_names,
                )
                # Build party-wide CombatState, roll initiative, apply surprise
                pre_state = enter_combat(self.session, trigger)  # type: ignore[arg-type]
                self.combat_state = start_combat(pre_state.combatants, trigger=trigger)
                self.session.combat_state = self.combat_state  # type: ignore[union-attr]
                self._pending_combat_start_embed = (self.combat_state, trigger)
                # Fall through to combat dispatch below

        # --- 3. Dispatch to the right validator ---
        if self.combat_state is not None and self.combat_state.is_active:
            if eng_action.action_type in EXPLORATION_ACTION_TYPES:
                return validate_exploration_action(
                    eng_action, combat_state=self.combat_state,
                )
            return validate_action(eng_action, self.combat_state)

        # --- 4. No combat --- exploration path or trivial kill ---
        if eng_action.action_type in EXPLORATION_ACTION_TYPES:
            return validate_exploration_action(eng_action, combat_state=None)

        # Combat action requested with no active combat → check trivial kill
        if (
            eng_action.action_type == ActionType.ATTACK
            and eng_action.target_name is not None
            and self.npcs.get(eng_action.target_name) is not None
        ):
            target_npc = self.npcs[eng_action.target_name]
            if self._should_trivial_resolve(target_npc):
                self._trivial_kill(target_npc)
                return ValidationResult(is_valid=True)

        return ValidationResult(
            is_valid=False,
            error_message=(
                f"'{eng_action.action_type.value}' nécessite un combat actif."
            ),
        )
```

- [ ] **Step 2.6 — Supprimer `_bootstrap_combat_against`**

La méthode `_bootstrap_combat_against` (lignes ~773–794) est maintenant obsolète. La supprimer entièrement. Si des tests l'utilisent directement, ils échoueront à l'étape suivante et devront être mis à jour.

- [ ] **Step 2.7 — Vérifier que les nouveaux tests passent**

```bash
uv run pytest tests/bot/test_action_pipeline.py -k "autoconverts or flee_destination or dispatches or exploration_rejected" -v 2>&1 | tail -20
```

Attendu : tous verts.

- [ ] **Step 2.8 — Régression complète**

```bash
uv run pytest tests/ -x -q 2>&1 | tail -30
```

Si des tests utilisent `_bootstrap_combat_against` directement, les corriger pour utiliser `enter_combat` + `start_combat`. Si des scenarios tests échouent parce que le bootstrap initialise maintenant l'initiative (au lieu d'un ordre fixe), mettre à jour les fixtures pour utiliser `CombatState` avec `current_turn_index` pointant sur le bon combattant.

- [ ] **Step 2.9 — Linter + type check**

```bash
uv run ruff check bot/action_pipeline.py
uv run mypy bot/action_pipeline.py
```

Corriger les erreurs mypy sur les `type: ignore[arg-type]` si le type de `session` est Optional et non-Optional dans la signature d'`enter_combat`.

- [ ] **Step 2.10 — Mettre à jour docs internes**

Dans `docs/internal/ACTION_PIPELINE.md`, section "Validation" ou "Dispatch", ajouter :

```markdown
### Dispatch pipeline combat-aware (Task 31)

`ActionPipeline._validate` suit l'organigramme suivant :

1. **MOVE en combat actif** → converti en FLEE (destination stockée dans `_pending_flee_destination`).
2. **Pas de combat** → `detect_combat_trigger(action, session)`. Si trigger : `enter_combat` + `start_combat` (initiative + surprise). `_pending_combat_start_embed` est peuplé pour que le cog le poste.
3. **Combat actif** → `validate_exploration_action` (LOOK/QUESTION/IMPROVISE) ou `validate_action` (combat).
4. **Pas de combat, action de combat** → trivial kill possible pour les commoners ; sinon erreur.
```

- [ ] **Step 2.11 — Commit**

```bash
git add bot/action_pipeline.py tests/bot/test_action_pipeline.py docs/internal/
git commit -m "feat(combat): Task 31 — pipeline combat-aware + MOVE→FLEE auto-convert"
```

---

## Task 3 — Résolution de Flee (spec Task 32)

**Files:**
- Modify: `bot/action_pipeline.py` (nouveau `_resolve_flee`, FLEE dans `_resolve_mechanics`)
- Test: `tests/bot/test_action_pipeline.py` + `tests/test_combat.py`

**Note sur l'implémentation vs la spec** : La spec Task 32 propose `_resolve_flee` comme méthode sync via `asyncio.to_thread`. Mais `change_location` dans ce codebase est `async` (voir `_resolve_mechanics` ligne MOVE qui l'`await`). `_resolve_flee` est donc défini comme `async` et appelé directement avec `await` dans `_resolve_mechanics`, sans `to_thread`. Les dice rolls (sync) fonctionnent normalement dans une coroutine.

- [ ] **Step 3.1 — Écrire les tests `_resolve_flee`**

Dans `tests/bot/test_action_pipeline.py`, ajouter :

```python
# ---------------------------------------------------------------------------
# Task 32 — Flee resolution
# ---------------------------------------------------------------------------


def _make_flee_pipeline(
    dex_score: int = 14,
    movement_feet: int = 30,
) -> tuple["ActionPipeline", "CombatState"]:
    """Build a minimal pipeline in active combat ready to test flee."""
    from engine.combat import CombatSide, CombatState, Combatant
    from engine.character import Character, CharacterClass, Race, Size, AbilityScores
    from engine.inventory import Inventory
    from unittest.mock import MagicMock

    scores = AbilityScores(STR=10, DEX=dex_score, CON=10, INT=10, WIS=10, CHA=10)
    char = Character(
        name="Héros", character_class=CharacterClass.FIGHTER, race=Race.HUMAN,
        level=1, size=Size.MEDIUM, ability_scores=scores,
    )
    hero = Combatant(name="Héros", side=CombatSide.PLAYER, character=char, inventory=Inventory())
    hero.action_budget.movement_remaining_feet = movement_feet

    goblin_char = char.model_copy(update={"name": "Goblin"})
    goblin = Combatant(name="Goblin", side=CombatSide.ENEMY, character=goblin_char, inventory=Inventory())

    state = CombatState(combatants=[hero, goblin], current_turn_index=0)

    pipeline = ActionPipeline(
        interpreter=MagicMock(), narrator=MagicMock(),
        location=None, npcs={}, actor_name="Héros", combat_state=state,
    )
    return pipeline, state


@pytest.mark.asyncio
async def test_flee_success_marks_combatant_fled() -> None:
    """On successful DEX check, combatant.fled is True."""
    from ai.models import InterpretedAction
    from engine.validators import ActionType
    from engine.dice import RollOutcome
    from unittest.mock import MagicMock, patch

    pipeline, state = _make_flee_pipeline(dex_score=20)

    mock_result = MagicMock()
    mock_result.total = 23
    mock_result.outcome = RollOutcome.SUCCESS

    with patch("bot.action_pipeline.roll_check", return_value=mock_result):
        action = InterpretedAction(
            action_type=ActionType.FLEE, actor_name="Héros", raw_input="je fuis",
        )
        outcome = await pipeline._resolve_flee(action)

    hero = state.combatants[0]
    assert hero.fled is True
    assert "réussit" in outcome.summary.lower()


@pytest.mark.asyncio
async def test_flee_failure_consumes_action_stays_in_combat() -> None:
    """On failed DEX check, action_used=True, combatant.fled=False."""
    from ai.models import InterpretedAction
    from engine.validators import ActionType
    from engine.dice import RollOutcome
    from unittest.mock import MagicMock, patch

    pipeline, state = _make_flee_pipeline(dex_score=8)

    mock_result = MagicMock()
    mock_result.total = 2
    mock_result.outcome = RollOutcome.FAILURE

    with patch("bot.action_pipeline.roll_check", return_value=mock_result):
        action = InterpretedAction(
            action_type=ActionType.FLEE, actor_name="Héros", raw_input="je fuis",
        )
        outcome = await pipeline._resolve_flee(action)

    hero = state.combatants[0]
    assert hero.fled is False
    assert hero.action_budget.action_used is True
    assert "échoue" in outcome.summary.lower()


@pytest.mark.asyncio
async def test_flee_with_all_pcs_fled_ends_combat() -> None:
    """When all PCs have fled, combat state becomes inactive with FLED reason."""
    from ai.models import InterpretedAction
    from engine.validators import ActionType
    from engine.combat import CombatEndReason
    from engine.dice import RollOutcome
    from unittest.mock import MagicMock, patch

    pipeline, state = _make_flee_pipeline(dex_score=20)

    mock_result = MagicMock()
    mock_result.total = 20
    mock_result.outcome = RollOutcome.SUCCESS

    with patch("bot.action_pipeline.roll_check", return_value=mock_result):
        action = InterpretedAction(
            action_type=ActionType.FLEE, actor_name="Héros", raw_input="je fuis",
        )
        await pipeline._resolve_flee(action)

    assert state.is_active is False
    assert state.end_reason == CombatEndReason.FLED


@pytest.mark.asyncio
async def test_flee_dice_embed_added_to_pending() -> None:
    """After flee attempt, _pending_dice_embeds has one entry."""
    from ai.models import InterpretedAction
    from engine.validators import ActionType
    from engine.dice import RollOutcome
    from unittest.mock import MagicMock, patch

    pipeline, state = _make_flee_pipeline()

    mock_result = MagicMock()
    mock_result.total = 12
    mock_result.outcome = RollOutcome.NEAR_SUCCESS

    with patch("bot.action_pipeline.roll_check", return_value=mock_result):
        action = InterpretedAction(
            action_type=ActionType.FLEE, actor_name="Héros", raw_input="je fuis",
        )
        await pipeline._resolve_flee(action)

    assert len(pipeline._pending_dice_embeds) == 1  # type: ignore[attr-defined]
```

Pour `tests/test_combat.py`, ajouter dans le fichier :

```python
# Task 32 — advance_turn skips fled combatants + check_combat_end FLED

def test_advance_turn_skips_fled_combatant() -> None:
    from engine.combat import advance_turn, CombatSide, CombatState, Combatant
    from engine.character import Character, CharacterClass, Race, Size, AbilityScores
    from engine.inventory import Inventory

    scores = AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10)
    def _char(name: str) -> Character:
        return Character(
            name=name, character_class=CharacterClass.FIGHTER, race=Race.HUMAN,
            level=1, size=Size.MEDIUM, ability_scores=scores,
        )

    hero = Combatant(name="Héros", side=CombatSide.PLAYER, character=_char("Héros"), inventory=Inventory())
    fled_pc = Combatant(name="Fuyeur", side=CombatSide.PLAYER, character=_char("Fuyeur"), inventory=Inventory(), fled=True)
    goblin = Combatant(name="Goblin", side=CombatSide.ENEMY, character=_char("Goblin"), inventory=Inventory())

    state = CombatState(combatants=[hero, fled_pc, goblin], current_turn_index=0)
    state = advance_turn(state)
    # Should skip fled_pc (index 1) and land on goblin (index 2)
    assert state.combatants[state.current_turn_index].name == "Goblin"


def test_check_combat_end_returns_fled_when_all_pcs_gone_but_some_fled() -> None:
    from engine.combat import check_combat_end, CombatEndReason, CombatSide, CombatState, Combatant
    from engine.character import Character, CharacterClass, Race, Size, AbilityScores
    from engine.inventory import Inventory

    scores = AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10)
    def _char(name: str) -> Character:
        return Character(
            name=name, character_class=CharacterClass.FIGHTER, race=Race.HUMAN,
            level=1, size=Size.MEDIUM, ability_scores=scores,
        )

    hero = Combatant(name="Héros", side=CombatSide.PLAYER, character=_char("Héros"), inventory=Inventory(), fled=True)
    goblin = Combatant(name="Goblin", side=CombatSide.ENEMY, character=_char("Goblin"), inventory=Inventory())
    state = CombatState(combatants=[hero, goblin])
    assert check_combat_end(state) == CombatEndReason.FLED


def test_check_combat_end_returns_defeat_when_all_pcs_dead_none_fled() -> None:
    from engine.combat import check_combat_end, CombatEndReason, CombatSide, CombatState, Combatant
    from engine.character import Character, CharacterClass, Race, Size, AbilityScores
    from engine.inventory import Inventory

    scores = AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10)
    def _char(name: str) -> Character:
        return Character(
            name=name, character_class=CharacterClass.FIGHTER, race=Race.HUMAN,
            level=1, size=Size.MEDIUM, ability_scores=scores,
        )

    hero = Combatant(name="Héros", side=CombatSide.PLAYER, character=_char("Héros"), inventory=Inventory(), is_alive=False)
    goblin = Combatant(name="Goblin", side=CombatSide.ENEMY, character=_char("Goblin"), inventory=Inventory())
    state = CombatState(combatants=[hero, goblin])
    assert check_combat_end(state) == CombatEndReason.DEFEAT
```

- [ ] **Step 3.2 — Vérifier que les tests échouent**

```bash
uv run pytest tests/bot/test_action_pipeline.py -k "flee_success or flee_failure or flee_with_all or flee_dice" -v 2>&1 | tail -20
uv run pytest tests/test_combat.py -k "skips_fled or fled_when_all or defeat_when_all_pcs" -v 2>&1 | tail -20
```

Attendu : les tests `test_combat` passent peut-être déjà (`advance_turn` et `check_combat_end` sont déjà implémentés). Les tests `_resolve_flee` échouent (`AttributeError: _resolve_flee`).

- [ ] **Step 3.3 — Mettre à jour les imports dans `action_pipeline.py`**

Dans `bot/action_pipeline.py`, modifier les lignes d'import existantes :

```python
# Remplacer la ligne existante :
#   from engine.character import Character
# par :
from engine.character import Ability, Character, compute_modifier

# Remplacer la ligne existante :
#   from engine.combat import CombatState, TrivialResolveResult, trivial_resolve
# par :
from engine.combat import (
    CombatEndReason,
    CombatState,
    TrivialResolveResult,
    check_combat_end,
    trivial_resolve,
)

# Ajouter une nouvelle ligne d'import (engine.dice n'est pas encore importé) :
from engine.dice import D20CheckResult, RollOutcome, roll_check
```

- [ ] **Step 3.4 — Implémenter `_resolve_flee`**

Dans `bot/action_pipeline.py`, dans la section "Phase helpers" après `_validate`, ajouter :

```python
    async def _resolve_flee(self, action: InterpretedAction) -> MechanicsOutcome:
        """Roll a DEX check (DC 12) to escape combat.

        On success: combatant.fled = True, removed from turn rotation.
        On failure: action_budget.action_used = True, combatant stays.
        If all alive PCs have fled → combat ends (FLED), pending flee destination
        triggers a location change if set.

        Always pushes a dice-embed tuple to _pending_dice_embeds for the
        caller (task 60) to display as an embed.

        Defined as async because it may call change_location (async).
        """
        assert self.combat_state is not None

        combatant = next(
            (c for c in self.combat_state.combatants if c.name == action.actor_name),
            None,
        )
        if combatant is None:
            return MechanicsOutcome(
                summary=f"{action.actor_name} n'est pas en combat.",
                player_intent=self._build_player_intent(action),
            )

        dex_mod = compute_modifier(
            combatant.character.ability_scores.get(Ability.DEX)
        )
        check: D20CheckResult = roll_check(f"1d20+{dex_mod}", dc=12)

        # Store for task-60 dice embed
        self._pending_dice_embeds.append(("flee_check", check, action.actor_name))

        intent = self._build_player_intent(action)

        if check.outcome in (
            RollOutcome.NEAR_SUCCESS,
            RollOutcome.SUCCESS,
            RollOutcome.CRITICAL_SUCCESS,
        ):
            combatant.fled = True
            outcome_desc = (
                f"{action.actor_name} réussit à fuir "
                f"(DEX {check.total} vs DC 12) et s'échappe du combat."
            )
        else:
            combatant.action_budget.action_used = True
            return MechanicsOutcome(
                summary=(
                    f"{action.actor_name} échoue à fuir "
                    f"(DEX {check.total} vs DC 12) et reste bloqué en combat."
                ),
                player_intent=intent,
                outcome_facts=f"Flee failed: {action.actor_name} stays.",
            )

        # Check if combat should end (all PCs fled or dead)
        end = check_combat_end(self.combat_state)
        if end == CombatEndReason.FLED:
            self.combat_state.is_active = False
            self.combat_state.end_reason = CombatEndReason.FLED

            dest_name: str | None = None
            if self._pending_flee_destination and self.session is not None and self.db_factory is not None:
                from bot.world_navigation import LocationChangeError, change_location
                try:
                    dest = await change_location(
                        self.session,
                        self._pending_flee_destination,
                        db_factory=self.db_factory,
                    )
                    dest_name = dest.name
                    self.location = dest
                    outcome_desc += f" Le groupe s'échappe vers {dest_name}."
                except LocationChangeError as exc:
                    logger.warning(
                        "FLEE change_location failed campaign=%s target=%r reason=%s",
                        self.campaign_id, self._pending_flee_destination, exc.reason,
                    )

            return MechanicsOutcome(
                summary=outcome_desc,
                player_intent=intent,
                outcome_facts=outcome_desc,
                public_effects=PublicEffects(
                    location_change=dest_name,
                ) if dest_name else PublicEffects(),
            )

        # Partial flee (only some PCs have fled; combat continues)
        return MechanicsOutcome(
            summary=outcome_desc,
            player_intent=intent,
            outcome_facts=outcome_desc,
        )
```

Ajouter `from engine.dice import RollOutcome` si non encore importé (vérifier la ligne existante `from engine.dice import ...`).

- [ ] **Step 3.5 — Ajouter le FLEE branch dans `_resolve_mechanics`**

Dans `_resolve_mechanics`, AVANT le bloc `if at == ActionType.LOOK:` (le tout premier bloc), insérer :

```python
        if at == ActionType.FLEE:
            return await self._resolve_flee(action)
```

- [ ] **Step 3.6 — Vérifier que les tests passent**

```bash
uv run pytest tests/bot/test_action_pipeline.py -k "flee" -v 2>&1 | tail -30
uv run pytest tests/test_combat.py -k "fled" -v 2>&1 | tail -20
```

Attendu : tous verts. Si `pytest.mark.asyncio` manque, s'assurer que `pytest-asyncio` est installé (`uv add --dev pytest-asyncio`) et que `asyncio_mode = "auto"` est dans `pyproject.toml`.

- [ ] **Step 3.7 — Régression complète**

```bash
uv run pytest tests/ -x -q 2>&1 | tail -30
```

Attendu : tous les tests passent.

- [ ] **Step 3.8 — Linter + type check**

```bash
uv run ruff check bot/action_pipeline.py
uv run mypy bot/action_pipeline.py
```

Corriger les erreurs. Si `check_combat_end` importé deux fois, consolider.

- [ ] **Step 3.9 — Mettre à jour docs internes**

Dans `docs/internal/ACTION_PIPELINE.md`, ajouter section :

```markdown
### Résolution de Flee (Task 32)

`ActionPipeline._resolve_flee` (async) gère la fuite :
- Roll `1d20 + DEX_mod` vs DC 12.
- **Succès** (near_success, success, critical_success) : `combatant.fled = True`, retiré de la rotation de tours.
- **Échec** : `action_budget.action_used = True`, le combattant reste en combat.
- Quand **tous les PCs** ont fui ou sont morts avec au moins un fui : `CombatState.end_reason = FLED`, `is_active = False`. Si `_pending_flee_destination` est défini, `change_location` est appelé.
- `_pending_dice_embeds` reçoit un tuple `("flee_check", D20CheckResult, actor_name)` pour l'affichage (task 60).
```

Dans `docs/internal/STATE.md`, marquer Tasks 30, 31, 32 ✅.

- [ ] **Step 3.10 — Commit**

```bash
git add bot/action_pipeline.py tests/bot/test_action_pipeline.py tests/test_combat.py docs/internal/
git commit -m "feat(combat): Task 32 — flee resolution, DEX check DC 12, FLED end condition"
```

---

## Validation finale Phase 3

- [ ] **Suite complète**

```bash
uv run pytest tests/ -q 2>&1 | tail -10
```

Attendu : 0 failures, 0 errors.

- [ ] **Linters**

```bash
uv run ruff check .
uv run mypy engine/validators.py bot/action_pipeline.py
```

- [ ] **Mettre à jour tasks/todo.md**

Cocher Tasks 30, 31, 32 dans `tasks/todo.md`.

---

## Hors scope Phase 3

- Group DEX check multi-PC (Task 32 spec : solo pour le MVP).
- OOA déclenchées sur flee (non implémenté — Flee = Disengage implicite pour le MVP).
- Embed de combat end (Task 80).
- `_pending_combat_start_embed` consommé par le cog (Task 61/63 — l'embed est stocké mais pas encore posté).
- Réactions automatiques (Shield, Counterspell) — futures tâches.
- Dash/Disengage/Dodge comme ActionType distincts — sous Defend ou future tâche.
