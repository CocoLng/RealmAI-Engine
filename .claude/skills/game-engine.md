---
name: game-engine
description: >
  Reference for building the RPG game engine (engine/ directory). Use this skill whenever working on
  dice rolling, character creation, combat mechanics, inventory management, spells, conditions,
  validators, or any engine/ module. Covers Pydantic v2 model patterns, simplified SRD 5e rules,
  ActionValidator legality checks, ActionResult output format, Enum definitions, and pytest
  conventions. Trigger on: engine/, dice, character, combat, inventory, spells, conditions,
  validators, HP, AC, damage, ability scores, saving throws, Pydantic game models, ActionResult,
  ActionValidator, DamageType, or any mechanical game logic.
---

# Game Engine Skill

## Core Principle

**The LLM narrates. The code arbitrates. No exceptions.**

Everything in `engine/` is pure deterministic Python. No LLM calls, no randomness beyond
`random.randint` (seeded for tests), no network I/O. If it rolls dice, deals damage, validates
an action, or updates game state — it lives here and it's tested.

## Module Build Order

Build in this sequence — each module depends only on prior ones:

1. `dice.py` — Dice expressions (`"2d6+3"`) → DiceResult
2. `character.py` — Classes, races, ability scores, levels, HP
3. `inventory.py` — Items, equipment, weight, attunement
4. `spells.py` — Spell definitions, slots, casting, effects
5. `conditions.py` — Status conditions and their mechanical effects
6. `combat.py` — Initiative, attacks, damage, turns, death saves
7. `validators.py` — Action legality checks (the ActionValidator)

---

## Pydantic v2 Patterns

Every data model uses Pydantic v2 BaseModel. No raw dicts. No dataclasses for validated data.

### BaseModel with Config

```python
from pydantic import BaseModel, Field, field_validator, model_validator

class Character(BaseModel):
    """A player or NPC character."""

    model_config = {"frozen": False, "str_strip_whitespace": True}

    name: str = Field(min_length=1, max_length=64)
    level: int = Field(default=1, ge=1, le=20)
    hit_points: int = Field(ge=0)
    max_hit_points: int = Field(gt=0)
    armor_class: int = Field(ge=0, le=30)
    ability_scores: dict[Ability, int]  # Always use Enum keys
    conditions: list[Condition] = Field(default_factory=list)
```

### Field Patterns

```python
# Constrained numeric
damage: int = Field(ge=0, description="Damage dealt")

# Default factory for mutable defaults
inventory: list[Item] = Field(default_factory=list)

# Discriminated unions for polymorphic actions
action: AttackAction | CastSpellAction | MoveAction = Field(discriminator="action_type")
```

### Validators

```python
class Character(BaseModel):
    # IMPORTANT: max_hit_points BEFORE hit_points — Pydantic v2 validates in declaration order
    max_hit_points: int = Field(gt=0)
    hit_points: int = Field(ge=0)

    @field_validator("hit_points")
    @classmethod
    def hp_cannot_exceed_max(cls, v: int, info) -> int:
        if "max_hit_points" in info.data and v > info.data["max_hit_points"]:
            return info.data["max_hit_points"]
        return v

    @model_validator(mode="after")
    def validate_ability_scores(self) -> "Character":
        for ability in Ability:
            if ability not in self.ability_scores:
                raise ValueError(f"Missing ability score: {ability}")
        return self
```

### Serialization

```python
# To dict (for DB storage)
character.model_dump()

# To JSON string (for LLM context)
character.model_dump_json()

# From dict
Character.model_validate(data)
```

---

## Enum Definitions

Use `StrEnum` for values that appear in serialized output. Use `IntEnum` sparingly.

### Ability (the 6 core stats)

```python
from enum import StrEnum

class Ability(StrEnum):
    STRENGTH = "strength"
    DEXTERITY = "dexterity"
    CONSTITUTION = "constitution"
    INTELLIGENCE = "intelligence"
    WISDOM = "wisdom"
    CHARISMA = "charisma"
```

### SkillType (tied to abilities)

```python
class SkillType(StrEnum):
    # Strength
    ATHLETICS = "athletics"
    # Dexterity
    ACROBATICS = "acrobatics"
    SLEIGHT_OF_HAND = "sleight_of_hand"
    STEALTH = "stealth"
    # Intelligence
    ARCANA = "arcana"
    HISTORY = "history"
    INVESTIGATION = "investigation"
    NATURE = "nature"
    RELIGION = "religion"
    # Wisdom
    ANIMAL_HANDLING = "animal_handling"
    INSIGHT = "insight"
    MEDICINE = "medicine"
    PERCEPTION = "perception"
    SURVIVAL = "survival"
    # Charisma
    DECEPTION = "deception"
    INTIMIDATION = "intimidation"
    PERFORMANCE = "performance"
    PERSUASION = "persuasion"
```

### DamageType

```python
class DamageType(StrEnum):
    BLUDGEONING = "bludgeoning"
    PIERCING = "piercing"
    SLASHING = "slashing"
    FIRE = "fire"
    COLD = "cold"
    LIGHTNING = "lightning"
    THUNDER = "thunder"
    ACID = "acid"
    POISON = "poison"
    NECROTIC = "necrotic"
    RADIANT = "radiant"
    FORCE = "force"
    PSYCHIC = "psychic"
```

### Condition

```python
class Condition(StrEnum):
    BLINDED = "blinded"
    CHARMED = "charmed"
    DEAFENED = "deafened"
    FRIGHTENED = "frightened"
    GRAPPLED = "grappled"
    INCAPACITATED = "incapacitated"
    INVISIBLE = "invisible"
    PARALYZED = "paralyzed"
    PETRIFIED = "petrified"
    POISONED = "poisoned"
    PRONE = "prone"
    RESTRAINED = "restrained"
    STUNNED = "stunned"
    UNCONSCIOUS = "unconscious"
    EXHAUSTION = "exhaustion"  # Levels 1-6, tracked separately
```

### Other Enums

```python
class CharacterClass(StrEnum):
    BARBARIAN = "barbarian"
    BARD = "bard"
    CLERIC = "cleric"
    DRUID = "druid"
    FIGHTER = "fighter"
    MONK = "monk"
    PALADIN = "paladin"
    RANGER = "ranger"
    ROGUE = "rogue"
    SORCERER = "sorcerer"
    WARLOCK = "warlock"
    WIZARD = "wizard"

class Race(StrEnum):
    HUMAN = "human"
    ELF = "elf"
    DWARF = "dwarf"
    HALFLING = "halfling"
    GNOME = "gnome"
    HALF_ORC = "half_orc"
    HALF_ELF = "half_elf"
    TIEFLING = "tiefling"
    DRAGONBORN = "dragonborn"

# Also define: SpellSchool (8 schools), WeaponProperty (finesse, heavy, light, etc.)
```

---

## Simplified SRD 5e Reference

### Ability Modifier Formula

```python
def ability_modifier(score: int) -> int:
    return (score - 10) // 2
```

| Score | Modifier | Score | Modifier |
|-------|----------|-------|----------|
| 1     | -5       | 12-13 | +1       |
| 2-3   | -4       | 14-15 | +2       |
| 4-5   | -3       | 16-17 | +3       |
| 6-7   | -2       | 18-19 | +4       |
| 8-9   | -1       | 20    | +5       |
| 10-11 | +0       |       |          |

### Proficiency Bonus

| Level | Bonus | Level  | Bonus |
|-------|-------|--------|-------|
| 1-4   | +2    | 9-12   | +4    |
| 5-8   | +3    | 13-16  | +5    |
|       |       | 17-20  | +6    |

### Armor Class

- **Unarmored:** 10 + DEX mod
- **Light armor:** armor base + DEX mod
- **Medium armor:** armor base + DEX mod (max +2)
- **Heavy armor:** armor base (no DEX)
- **Shield:** +2 to AC

### Hit Points Calculation

- **Level 1:** Hit die maximum + CON modifier (e.g. Fighter: 10 + CON mod)
- **Level 2+:** Average roll rounded up + CON modifier per level (d10→6, d8→5, d6→4, d12→7)
- **Formula:** `HP = hit_die_max + (level - 1) * (avg_roll + CON_mod) + CON_mod`

### Classes (SRD 5e subset)

| Class     | Hit Die | Primary Ability | Saving Throws   |
|-----------|---------|-----------------|------------------|
| Barbarian | d12     | STR             | STR, CON         |
| Bard      | d8      | CHA             | DEX, CHA         |
| Cleric    | d8      | WIS             | WIS, CHA         |
| Druid     | d8      | WIS             | INT, WIS         |
| Fighter   | d10     | STR or DEX      | STR, CON         |
| Monk      | d8      | DEX & WIS       | STR, DEX         |
| Paladin   | d10     | STR & CHA       | WIS, CHA         |
| Ranger    | d10     | DEX & WIS       | STR, DEX         |
| Rogue     | d8      | DEX             | DEX, INT         |
| Sorcerer  | d6      | CHA             | CON, CHA         |
| Warlock   | d8      | CHA             | WIS, CHA         |
| Wizard    | d6      | INT             | INT, WIS         |

### Races (SRD 5e subset)

| Race        | ASI              | Speed | Notable Trait          |
|-------------|------------------|-------|------------------------|
| Human       | All +1           | 30ft  | Extra language          |
| Elf         | DEX +2           | 30ft  | Darkvision, trance      |
| Dwarf       | CON +2           | 25ft  | Darkvision, resilience  |
| Halfling    | DEX +2           | 25ft  | Lucky (reroll 1s)       |
| Gnome       | INT +2           | 25ft  | Darkvision, cunning     |
| Half-Orc    | STR +2, CON +1   | 30ft  | Relentless endurance    |
| Half-Elf    | CHA +2, two +1   | 30ft  | Darkvision, versatile   |
| Tiefling    | CHA +2, INT +1   | 30ft  | Darkvision, fire resist |
| Dragonborn  | STR +2, CHA +1   | 30ft  | Breath weapon           |

### Basic Spells Reference

| Spell          | Lvl | School      | Effect                          |
|----------------|-----|-------------|---------------------------------|
| Fire Bolt      | 0   | Evocation   | 1d10 fire (ranged attack)       |
| Sacred Flame   | 0   | Evocation   | 1d8 radiant (DEX save)          |
| Eldritch Blast | 0   | Evocation   | 1d10 force (ranged attack)      |
| Cure Wounds    | 1   | Evocation   | 1d8+mod healing (touch)         |
| Magic Missile  | 1   | Evocation   | 3×1d4+1 force (auto-hit)        |
| Shield         | 1   | Abjuration  | +5 AC until next turn           |
| Thunderwave    | 1   | Evocation   | 2d8 thunder (CON save, push)    |
| Hold Person    | 2   | Enchantment | Paralyzed (WIS save, conc.)     |
| Fireball       | 3   | Evocation   | 8d6 fire (DEX save, 20ft)       |
| Counterspell   | 3   | Abjuration  | Negate spell ≤3 (check higher)  |
| Healing Word   | 1   | Evocation   | 1d4+mod healing (bonus, 60ft)   |
| Guiding Bolt   | 1   | Evocation   | 4d6 radiant (next attack advtg) |
| Misty Step     | 2   | Conjuration | Teleport 30ft (bonus action)    |
| Revivify       | 3   | Necromancy  | Raise dead <1min, 1HP, 300gp    |
| Bless          | 1   | Enchantment | 1d4 to attacks/saves (conc.)    |

### Skill-to-Ability Mapping

STR: Athletics | DEX: Acrobatics, Sleight of Hand, Stealth | INT: Arcana, History, Investigation, Nature, Religion | WIS: Animal Handling, Insight, Medicine, Perception, Survival | CHA: Deception, Intimidation, Performance, Persuasion

---

## Action Models

Actions parsed by the Interpreter. Use a `Literal` discriminator for union dispatch.

```python
from typing import Literal

class AttackAction(BaseModel):
    action_type: Literal["attack"] = "attack"
    actor_id: str
    target_id: str
    weapon_id: str  # Must be in actor's inventory

class CastSpellAction(BaseModel):
    action_type: Literal["cast_spell"] = "cast_spell"
    actor_id: str
    target_id: str | None = None
    spell_name: str
    spell_level: int = Field(ge=0)  # 0 = cantrip

class MoveAction(BaseModel):
    action_type: Literal["move"] = "move"
    actor_id: str
    destination: str  # Location ID

Action = AttackAction | CastSpellAction | MoveAction
```

## GameState Shape (minimal for validator)

```python
class CharacterState(BaseModel):
    id: str
    hit_points: int
    conditions: list[Condition] = Field(default_factory=list)
    inventory: list[str] = Field(default_factory=list)  # Item IDs
    spell_slots: dict[int, int] = Field(default_factory=dict)  # {level: remaining}

class CombatState(BaseModel):
    is_active: bool = False
    initiative_order: list[str] = Field(default_factory=list)  # Character IDs
    current_turn_id: str | None = None

class GameState(BaseModel):
    characters: dict[str, CharacterState]
    combat: CombatState = Field(default_factory=CombatState)
```

---

## ActionValidator Pattern

The validator sits between the Interpreter (LLM) and the Engine. It checks legality using
only game state — no LLM involvement.

```python
class ValidationError(BaseModel):
    """Why an action was rejected."""
    code: str  # e.g. "NOT_YOUR_TURN", "TARGET_OUT_OF_RANGE"
    message: str

class ValidationResult(BaseModel):
    """Result of validating a player action."""
    is_valid: bool
    errors: list[ValidationError] = Field(default_factory=list)

class ActionValidator:
    """Checks action legality against game state. Pure Python, no LLM."""

    def validate(self, action: Action, game_state: GameState) -> ValidationResult:
        errors: list[ValidationError] = []
        # Run all checks — collect all errors, don't short-circuit
        self._check_actor_alive(action, game_state, errors)
        self._check_turn_order(action, game_state, errors)
        self._check_action_economy(action, game_state, errors)
        self._check_target_valid(action, game_state, errors)
        self._check_resource_available(action, game_state, errors)
        self._check_equipment_owned(action, game_state, errors)
        self._check_conditions_allow(action, game_state, errors)
        return ValidationResult(is_valid=len(errors) == 0, errors=errors)
```

### What Each Check Does

| Check                    | Rejects when...                                    |
|--------------------------|-----------------------------------------------------|
| `_check_actor_alive`     | Character is dead or unconscious                    |
| `_check_turn_order`      | Not this character's turn in combat                 |
| `_check_action_economy`  | Action/bonus/reaction already spent this turn        |
| `_check_target_valid`    | Target doesn't exist, is dead, or out of range      |
| `_check_resource_available` | No spell slots, no ammo, no item charges          |
| `_check_equipment_owned` | Weapon/item not in inventory or not equipped         |
| `_check_conditions_allow`| Paralyzed trying to attack, silenced trying to cast |

---

## ActionResult Format

The engine produces an ActionResult after resolving mechanics. The Narrator receives this
to describe what happened — it decides nothing mechanically.

```python
class DiceResult(BaseModel):
    """Result of a dice roll."""
    expression: str  # "2d6+3"
    rolls: list[int]  # [4, 2]
    modifier: int  # 3
    total: int  # 9

class StateChange(BaseModel):
    """A single change to game state."""
    entity_id: str
    field: str  # "hit_points", "conditions", "position"
    old_value: Any
    new_value: Any

class ActionResult(BaseModel):
    """Structured output from the engine. Sent to Narrator for description."""

    success: bool
    action_type: str  # "attack", "cast_spell", "move", "use_item"
    actor_id: str
    target_id: str | None = None

    # Dice
    dice_results: list[DiceResult] = Field(default_factory=list)

    # Outcomes
    damage_dealt: int = 0
    damage_type: DamageType | None = None
    healing_done: int = 0
    conditions_applied: list[Condition] = Field(default_factory=list)
    conditions_removed: list[Condition] = Field(default_factory=list)

    # State changes (for DB update)
    state_changes: list[StateChange] = Field(default_factory=list)

    # Items
    items_gained: list[str] = Field(default_factory=list)
    items_lost: list[str] = Field(default_factory=list)

    # Human-readable summary for Discord raw stats display
    summary: str
```

---

## pytest Conventions

### File Structure

```
tests/
├── conftest.py          # Shared fixtures
├── test_dice.py         # Tests for engine/dice.py
├── test_character.py    # Tests for engine/character.py
├── test_inventory.py    # Tests for engine/inventory.py
├── test_spells.py       # Tests for engine/spells.py
├── test_conditions.py   # Tests for engine/conditions.py
├── test_combat.py       # Tests for engine/combat.py
└── test_validators.py   # Tests for engine/validators.py
```

One test file per engine module. Name mirrors the module: `engine/dice.py` → `tests/test_dice.py`.

### Running Tests

```bash
uv run pytest                      # All tests
uv run pytest tests/test_dice.py   # Single module
uv run pytest -x                   # Stop on first failure
uv run pytest --cov=engine         # Coverage report
uv run ruff check .                # Lint
uv run mypy .                      # Type check
```

### Fixture Patterns

```python
# conftest.py — shared fixtures for reusable test characters
@pytest.fixture
def fighter() -> Character:
    """A level 5 fighter with standard stats."""
    return Character(
        name="Thorn", level=5, character_class=CharacterClass.FIGHTER,
        race=Race.HUMAN, hit_points=44, max_hit_points=44, armor_class=18,
        ability_scores={
            Ability.STRENGTH: 16, Ability.DEXTERITY: 14, Ability.CONSTITUTION: 14,
            Ability.INTELLIGENCE: 10, Ability.WISDOM: 12, Ability.CHARISMA: 8,
        },
    )
```

### Key Patterns

```python
# Parametrize for dice/damage ranges
@pytest.mark.parametrize("expression,min_val,max_val", [
    ("1d6", 1, 6), ("2d6", 2, 12), ("1d20+5", 6, 25),
])
def test_dice_roll_range(expression: str, min_val: int, max_val: int) -> None:
    for _ in range(100):
        result = roll(expression)
        assert min_val <= result.total <= max_val

# Arrange / Act / Assert structure
def test_attack_hits_when_roll_meets_ac(fighter: Character) -> None:
    target = Character(name="Goblin", armor_class=13, ...)  # Arrange
    result = resolve_attack(AttackAction(...), roll_override=13)  # Act
    assert result.success is True  # Assert
    assert result.damage_dealt > 0
```

### Coverage Target

Every engine module needs >80% line coverage. Check with:

```bash
uv run pytest --cov=engine --cov-report=term-missing
```

Focus test effort on branching logic: critical hits, death saves, condition interactions,
edge cases (0 HP, max level, empty inventory).
