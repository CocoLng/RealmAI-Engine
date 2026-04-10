# Character System Refactor — Design Spec

**Date**: 2026-04-10
**Status**: Draft
**Phase**: 1 (Engine) + 3 (Discord Bot)

## Context

The current `engine/character.py` is a 369-line monolith handling races, classes, stats, creation, and progression. All reference data (racial bonuses, hit dice, XP tables) is hardcoded as module-level dicts alongside the logic. The model lacks support for racial traits, class features, skill proficiencies, and has no extensibility path for backgrounds or feats.

This refactor splits the character system into focused modules, introduces a generic Feature system for traits/abilities, adds Standard Array stat assignment, and skill proficiencies. The goal: a character sheet rich enough for immersive play, with architecture that scales to backgrounds and feats later.

## Scope

**In scope:**
- Decompose `character.py` into a `character/` package
- Generic Feature system (racial traits, class features)
- Standard Array stat assignment (15, 14, 13, 12, 10, 8)
- Skill proficiencies (18 D&D 5e skills)
- Discord wizard updates (stat assignment step, skill selection step)
- DB migration for existing characters
- Test migration (update imports, add feature/skill tests)

**Out of scope (noted in TODO for later):**
- Backgrounds (Acolyte, Criminal, Noble, etc.)
- Feats
- Multiclassing
- Shop/buy-sell system
- Extended spell catalog

---

## 1. Package Structure

Replace `engine/character.py` with `engine/character/` package:

```
engine/character/
├── __init__.py         # Public API re-exports
├── models.py           # Character, AbilityScores Pydantic models
├── enums.py            # Ability, Alignment, Size, Skill enums
├── races.py            # Race enum + racial data (bonuses, speed, size, traits)
├── classes.py          # CharacterClass enum + class data (hit die, saves, skill choices, features)
├── abilities.py        # compute_modifier(), apply_racial_bonuses(), STANDARD_ARRAY
├── progression.py      # XP_THRESHOLDS, PROFICIENCY_BONUS, level_up(), add_xp(), check_level_up()
├── creation.py         # create_character() orchestrator
└── features.py         # Feature model + racial/class feature catalogs
```

### Migration strategy

- `engine/character/__init__.py` re-exports all public names that `character.py` currently exposes
- All existing imports (`from engine.character import Character, Race, ...`) continue to work
- Old `character.py` is deleted after the package is in place
- A single `ruff check` + `mypy` + `pytest` pass confirms zero breakage

### What goes where

| Current location | New location |
|---|---|
| `Ability`, `Alignment`, `Size` enums | `enums.py` |
| `Race` enum + `RACIAL_*` tables | `races.py` |
| `CharacterClass` enum + `CLASS_*` tables | `classes.py` |
| `AbilityScores`, `Character` models | `models.py` |
| `compute_modifier`, `apply_racial_bonuses`, `roll_ability_scores` | `abilities.py` |
| `XP_THRESHOLDS`, `PROFICIENCY_BONUS_BY_LEVEL`, `level_up`, `add_xp`, `check_level_up` | `progression.py` |
| `create_character` | `creation.py` |
| (new) Feature system | `features.py` |
| (new) `Skill` enum + class skill lists | `enums.py` (Skill) + `classes.py` (skill lists) |

---

## 2. Feature System

### Model

```python
class FeatureSource(StrEnum):
    RACE = "race"
    CLASS = "class"
    BACKGROUND = "background"   # future
    FEAT = "feat"               # future

class MechanicalEffect(BaseModel):
    """A single mechanical effect a feature grants."""
    effect_type: str            # "darkvision", "damage_resistance", "skill_proficiency", etc.
    value: int | str | list[str]  # 60, "fire", ["perception", "stealth"]

class Feature(BaseModel):
    name: str                           # "Darkvision", "Rage", "Sneak Attack"
    source: FeatureSource
    source_name: str                    # "Elf", "Barbarian" — which race/class granted it
    description: str                    # Narrative text for character sheet
    effects: list[MechanicalEffect]     # Parsable by engine
    level_requirement: int = 1          # Level at which feature is gained
```

### Supported effect types (v1)

| effect_type | value type | Example | Used by |
|---|---|---|---|
| `darkvision` | `int` (feet) | `60` | Elf, Dwarf, Half-Orc, Gnome, Tiefling |
| `damage_resistance` | `list[str]` | `["poison"]` | Dwarf, Tiefling ("fire") |
| `save_advantage` | `str` | `"poison"` | Dwarf, Halfling ("frightened") |
| `skill_proficiency` | `list[str]` | `["perception"]` | Elf ("Keen Senses") |
| `speed_bonus` | `int` | `-5`, `+10` | — (future use) |
| `extra_language` | `list[str]` | `["elvish"]` | — (flavor, no mechanic yet) |
| `lucky_reroll` | `bool` | `true` | Halfling ("Lucky") |
| `relentless_endurance` | `bool` | `true` | Half-Orc |
| `savage_attacks` | `bool` | `true` | Half-Orc |
| `cantrip` | `str` | `"thaumaturgy"` | Tiefling, Gnome |
| `extra_hp_per_level` | `int` | `1` | — (Tough feat, future) |

### Racial feature catalogs

Defined in `races.py` as `RACIAL_FEATURES: dict[Race, list[Feature]]`. Example:

```python
RACIAL_FEATURES = {
    Race.ELF: [
        Feature(name="Darkvision", source=FeatureSource.RACE, source_name="Elf",
                description="You can see in dim light within 60 feet as if bright light.",
                effects=[MechanicalEffect(effect_type="darkvision", value=60)]),
        Feature(name="Keen Senses", source=FeatureSource.RACE, source_name="Elf",
                description="You have proficiency in the Perception skill.",
                effects=[MechanicalEffect(effect_type="skill_proficiency", value=["perception"])]),
        Feature(name="Fey Ancestry", source=FeatureSource.RACE, source_name="Elf",
                description="You have advantage on saving throws against being charmed.",
                effects=[MechanicalEffect(effect_type="save_advantage", value="charmed")]),
    ],
    # ... other races
}
```

### Class feature catalogs (level 1 only for v1)

Defined in `classes.py` as `CLASS_FEATURES: dict[CharacterClass, list[Feature]]`. Only level-1 features for now. Higher-level features added as progression is expanded.

### Integration with Character model

```python
class Character(BaseModel):
    # ... existing fields ...
    features: list[Feature] = Field(default_factory=list)
    skill_proficiencies: list[Skill] = Field(default_factory=list)
```

The `features` list is populated at creation and updated on level-up. Combat, validators, and other engine modules can query features via helper functions:

```python
def has_feature(character: Character, name: str) -> bool: ...
def get_feature_effects(character: Character, effect_type: str) -> list[MechanicalEffect]: ...
def has_darkvision(character: Character) -> int | None: ...  # returns range or None
def get_damage_resistances(character: Character) -> list[str]: ...
```

---

## 3. Standard Array Stat Assignment

### Constants

```python
STANDARD_ARRAY: tuple[int, ...] = (15, 14, 13, 12, 10, 8)
```

### Engine function

```python
def assign_standard_array(
    assignments: dict[Ability, int],
    race: Race,
) -> AbilityScores:
    """Assign Standard Array values to abilities, then apply racial bonuses.

    Validates that exactly the 6 Standard Array values are used, each exactly once.
    Returns AbilityScores with racial bonuses applied.
    """
```

### Discord wizard step

A new view `StatAssignmentView` in `bot/views/`:
- Displays 6 select menus (one per ability: STR, DEX, CON, INT, WIS, CHA)
- Each menu shows the remaining unassigned values from `[15, 14, 13, 12, 10, 8]`
- Selecting a value for one stat removes it from the other menus (dynamic update)
- Shows a "Recommended for [class]" hint based on primary abilities
- Confirm button to proceed

### Removal of 4d6-drop-lowest

`roll_ability_scores()` is kept as a utility but no longer used in the default creation flow. Standard Array is the sole method for now.

---

## 4. Skill Proficiencies

### Skill enum (18 skills)

```python
class Skill(StrEnum):
    # STR
    ATHLETICS = "Athletics"
    # DEX
    ACROBATICS = "Acrobatics"
    SLEIGHT_OF_HAND = "Sleight of Hand"
    STEALTH = "Stealth"
    # INT
    ARCANA = "Arcana"
    HISTORY = "History"
    INVESTIGATION = "Investigation"
    NATURE = "Nature"
    RELIGION = "Religion"
    # WIS
    ANIMAL_HANDLING = "Animal Handling"
    INSIGHT = "Insight"
    MEDICINE = "Medicine"
    PERCEPTION = "Perception"
    SURVIVAL = "Survival"
    # CHA
    DECEPTION = "Deception"
    INTIMIDATION = "Intimidation"
    PERFORMANCE = "Performance"
    PERSUASION = "Persuasion"
```

### Skill-to-ability mapping

```python
SKILL_ABILITY: dict[Skill, Ability] = {
    Skill.ATHLETICS: Ability.STR,
    Skill.ACROBATICS: Ability.DEX,
    # ...
}
```

### Class skill choices

```python
CLASS_SKILL_CHOICES: dict[CharacterClass, ClassSkillConfig] = {
    CharacterClass.FIGHTER: ClassSkillConfig(
        choose=2,
        options=[Skill.ACROBATICS, Skill.ANIMAL_HANDLING, Skill.ATHLETICS,
                 Skill.HISTORY, Skill.INSIGHT, Skill.INTIMIDATION,
                 Skill.PERCEPTION, Skill.SURVIVAL],
    ),
    # ... other classes
}
```

### Skill check function

```python
def compute_skill_modifier(character: Character, skill: Skill) -> int:
    """Compute skill check modifier: ability_mod + proficiency_bonus if proficient."""
    ability = SKILL_ABILITY[skill]
    mod = compute_modifier(character.ability_scores.get(ability))
    if skill in character.skill_proficiencies:
        mod += character.proficiency_bonus
    return mod
```

### Discord wizard step

A `SkillSelectionView` in `bot/views/`:
- Shows the available skills for the chosen class
- Player selects N skills (number depends on class, typically 2-4)
- Multi-select menu with skill descriptions

---

## 5. Updated Character Creation Flow (Discord Wizard)

### Full wizard sequence

```
[Start Onboarding]
  → Step 1: Race select (existing CharacterCreateView)
  → Step 2: Class select (existing, unlocked after race)
  → Step 3: Alignment select (existing)
  → Step 4: Stat assignment (NEW — StatAssignmentView)
  → Step 5: Skill selection (NEW — SkillSelectionView)
  → Step 6: Name modal (existing CharacterNameModal)
  → Step 7: Starter gear (existing StarterGearView)
  → Step 8: Character sheet recap (embed with all stats + features + skills)
```

Steps 4-5 are new. Steps 1-3, 6-7 exist and need minor updates to pass the new data through the flow.

---

## 6. DB Migration

### Current schema

`PlayerCharacterRow.character_json` stores a JSON blob of the `Character` Pydantic model. The refactored `Character` model adds:
- `features: list[Feature]` (default: `[]`)
- `skill_proficiencies: list[Skill]` (default: `[]`)

### Migration approach

Since both new fields have default values (`[]`), existing JSON blobs deserialize correctly — Pydantic fills in defaults for missing keys. No schema migration needed for the `character_json` column itself.

However, existing characters won't have features or skills. A one-time backfill function:

```python
def backfill_character_features(character: Character) -> Character:
    """Add racial and class features to a character that was created before the feature system."""
    if not character.features:
        character.features = get_racial_features(character.race) + get_class_features(character.char_class, character.level)
    return character
```

This runs transparently on load (in `mappers.py`) so old saves "just work."

### Safety: character isolation

The existing PK is `(discord_user_id, campaign_id)`. This already prevents writing to another player's character. The repository methods should enforce this — verify that all `update`/`save` operations check both keys.

---

## 7. Test Strategy

### Approach

- Migrate existing `tests/test_character.py` imports to the new package structure
- All existing tests must pass without logic changes (just import paths)
- New test files:

| File | Covers |
|---|---|
| `tests/test_features.py` | Feature model, catalogs, has_feature(), get_damage_resistances() |
| `tests/test_skills.py` | Skill enum, compute_skill_modifier(), class skill configs |
| `tests/test_standard_array.py` | assign_standard_array() validation, racial bonus application |
| `tests/test_creation_flow.py` | End-to-end: create_character() with features + skills |

### Quality gates

- `pytest` — all green
- `ruff check .` — no lint errors
- `mypy .` — no type errors
- Coverage >80% on new modules

---

## 8. TODO items for later phases

These are explicitly deferred and should be noted in `tasks/todo.md`:

- [ ] Backgrounds (Acolyte, Criminal, Noble, etc.) — adds 2 skill proficiencies + equipment + RP trait
- [ ] Feats (level 4/8/12/16/19 ASI-or-feat choice)
- [ ] Multiclassing
- [ ] Languages system
- [ ] Tool proficiencies
- [ ] Higher-level class features (level 2+ progression)
- [ ] Point Buy and 4d6-drop-lowest as alternative stat methods
- [ ] Shop/buy-sell system for items
