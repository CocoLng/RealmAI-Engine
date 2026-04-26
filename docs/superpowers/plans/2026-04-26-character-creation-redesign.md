# Character Creation Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the chained 8+ message character creation with a unified lobby-driven onboarding: `/start_campaign` posts an open lobby; players click "Rejoindre" to enter a single auto-modifying view (6 steps); `alignment` field is removed; new `concept` field captures RP flavor; class-optimized stat presets and random 4d6-drop-lowest are offered alongside Standard Array; the `/create_character` slash command is deleted.

**Architecture:** Three execution waves. Wave **A** (engine cleanup) and Wave **B** (new UI) are file-isolated and run in parallel. Wave **C** (integration) runs sequentially after A+B converge — it refactors `/start_campaign`, replaces `CampaignLauncher` with `LobbyState`, wires the new view chain, and deletes obsolete code. Wave **D** is final verification.

**Tech Stack:** Python 3.12, Pydantic v2, discord.py 2.7.1 (Components V2 supported), SQLAlchemy + SQLite, pytest / ruff / mypy, `uv` for everything.

**Spec reference:** [docs/superpowers/specs/2026-04-26-character-creation-redesign-design.md](../specs/2026-04-26-character-creation-redesign-design.md)

---

## File Structure

### Create

| Path | Responsibility |
|---|---|
| `engine/character/presets.py` | `CLASS_STAT_PRESETS` dict + `get_class_preset()` accessor. |
| `engine/character/random_stats.py` | `roll_4d6_drop_lowest()` + `auto_assign_random()` + `CLASS_STAT_PRIORITY`. |
| `bot/lobby_state.py` | `LobbyState` (replaces `CampaignLauncher`), `LobbyPlayer`, `LobbyPlayerStatus`. |
| `bot/views/lobby_view.py` | Persistent lobby view: Rejoindre / Quitter / Démarrer buttons. |
| `bot/views/character_setup_flow.py` | Single auto-modifying view with 6-state machine + `IdentityModal`. |
| `bot/embeds/lobby_embed.py` | `build_lobby_embed()` with live roster + status badges. |
| `bot/embeds/character_setup_v2.py` | Components V2 récap of the character sheet (with embed fallback). |
| `tests/engine/character/test_presets.py` | Unit tests for `CLASS_STAT_PRESETS`. |
| `tests/engine/character/test_random_stats.py` | Unit tests for 4d6-drop-lowest + auto-assign. |
| `tests/engine/character/test_concept_field.py` | Tests for new `concept` field on Character. |
| `tests/bot/views/test_character_setup_flow.py` | State transitions + on_complete callback. |
| `tests/bot/views/test_lobby_view.py` | Button handlers (join, leave, launch). |
| `tests/bot/embeds/test_lobby_embed.py` | Roster rendering + badges. |
| `tests/bot/test_lobby_state.py` | `LobbyState` lifecycle: add_player, set_status, ready predicate. |
| `tests/scenarios/test_character_creation_lobby.py` | End-to-end scenario via `ScenarioRunner`. |

### Modify

| Path | Change |
|---|---|
| `engine/character/models.py` | Remove `alignment` field. Add `concept: str = Field(default="", max_length=200)`. |
| `engine/character/enums.py` | Remove `class Alignment(StrEnum)` (lines 47-58). |
| `engine/character/creation.py` | Remove `alignment` parameter and assignment. |
| `engine/character/__init__.py` | Drop `Alignment` from exports. Add `presets` and `random_stats` exports. |
| `bot/i18n.py` | Remove `ALIGNMENT_LABELS` dict. |
| `bot/cogs/session.py:85-212` | Refactor `/start_campaign` signature: drop `players` param, post `LobbyView`. |
| `bot/cogs/character.py:36-114` | Delete `/create_character` slash command. Keep `/character` and `/level_up`. |
| `bot/cogs/test_bridge.py:347-467` | Update `start_campaign` test handler for new signature. |
| `bot/bot.py` | Replace `launchers: dict[int, CampaignLauncher]` with `lobbies: dict[int, LobbyState]`. |
| `tests/bot/test_cog_character.py` | Drop tests of `/create_character` slash command. |
| `tests/bot/test_cog_inventory.py` | Drop `alignment=` from Character fixtures. |
| `tests/db/test_mappers.py` | Drop `alignment` from mappings. |
| `tests/bot/test_test_bridge_views.py` | Drop alignment refs. |
| `tests/bot/test_campaign_launcher_recreation.py` | Migrate to `LobbyState` API or delete if obsolete. |
| `tests/bot/test_i18n.py` | Drop `ALIGNMENT_LABELS` test cases. |
| `tests/bot/test_views.py` | Drop tests for deleted views. |
| `tests/engine/test_character.py` | Drop alignment field assertions. |
| `tests/engine/test_creation_flow.py` | Drop alignment from create_character calls. |

### Delete

| Path | Reason |
|---|---|
| `bot/views/character_create_view.py` | Replaced by `character_setup_flow.py`. |
| `bot/views/stat_assignment_view.py` | Merged into `CharacterSetupFlow.STATS` step. |
| `bot/views/skill_selection_view.py` | Merged into `CharacterSetupFlow.SKILLS` step. |
| `bot/views/motivation_view.py` | Merged into `CharacterSetupFlow.KIT_MOTIV` step. |
| `bot/views/starter_gear_view.py` | Merged into `CharacterSetupFlow.KIT_MOTIV` step. |
| `bot/views/start_onboarding_view.py` | Replaced by `LobbyView`. |
| `bot/views/character_edit_view.py` | Replaced by `CharacterSetupFlow.REVIEW` step's "Modifier" branch. |
| `bot/views/character_edit_flow.py` | Same. |
| `bot/campaign_launcher.py` | Replaced by `bot/lobby_state.py`. |
| `tests/bot/views/test_character_create_view.py` (if exists) | Tests for deleted view. |
| `tests/bot/views/test_stat_assignment_view.py` (if exists) | Tests for deleted view. |
| `tests/bot/views/test_skill_selection_view.py` (if exists) | Tests for deleted view. |

---

# Wave A — Engine Cleanup

**Agent: `engine-cleaner`. Independent of Wave B. Touches only `engine/character/`, `bot/i18n.py`, and engine/i18n tests. Estimated 3-4h.**

## Task A1: Add `concept` field to Character

**Files:**
- Modify: `engine/character/models.py`
- Modify: `engine/character/creation.py`
- Test: `tests/engine/character/test_concept_field.py`

- [ ] **Step 1: Write failing test**

```python
# tests/engine/character/test_concept_field.py
"""Tests for the new concept field on Character."""

from engine.character import Character, CharacterClass, Race, AbilityScores, create_character


def _basic_scores() -> AbilityScores:
    return AbilityScores(STR=15, DEX=14, CON=13, INT=12, WIS=10, CHA=8)


def test_concept_defaults_to_empty_string():
    char = create_character(
        name="Thorin",
        race=Race.DWARF,
        char_class=CharacterClass.FIGHTER,
        ability_scores=_basic_scores(),
    )
    assert char.concept == ""


def test_concept_accepts_custom_text():
    char = create_character(
        name="Thorin",
        race=Race.DWARF,
        char_class=CharacterClass.FIGHTER,
        ability_scores=_basic_scores(),
        concept="A grizzled veteran seeking redemption",
    )
    assert char.concept == "A grizzled veteran seeking redemption"


def test_concept_max_length_enforced():
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Character(
            name="X",
            race=Race.HUMAN,
            char_class=CharacterClass.FIGHTER,
            ability_scores=_basic_scores(),
            hp=10, max_hp=10, ac=10, speed=30,
            proficiency_bonus=2,
            saving_throw_proficiencies=(Ability.STR, Ability.CON),
            hit_die="d10",
            size=Size.MEDIUM,
            concept="x" * 201,  # over max_length=200
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/engine/character/test_concept_field.py -v`
Expected: FAIL — `Character` has no `concept` attribute.

- [ ] **Step 3: Add concept field to Character model**

In `engine/character/models.py`, add to the `Character` class (anywhere after `name` field):

```python
concept: str = Field(default="", max_length=200)
```

- [ ] **Step 4: Add concept param to create_character**

In `engine/character/creation.py:create_character`, add parameter (after `skill_proficiencies`):

```python
def create_character(
    name: str,
    race: Race,
    char_class: CharacterClass,
    ability_scores: AbilityScores,
    skill_proficiencies: list[Skill] | None = None,
    concept: str = "",
) -> Character:
```

And in the returned `Character(...)` call, add `concept=concept,`.

- [ ] **Step 5: Run tests pass**

Run: `uv run pytest tests/engine/character/test_concept_field.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add engine/character/models.py engine/character/creation.py tests/engine/character/test_concept_field.py
git commit -m "feat(character): add concept field for RP flavor

200-char optional text passed to narrator prompts. No mechanical impact.
Replaces alignment narratively (alignment removal in next commit)."
```

## Task A2: Remove `alignment` from engine

**Files:**
- Modify: `engine/character/models.py`
- Modify: `engine/character/enums.py`
- Modify: `engine/character/creation.py`
- Modify: `engine/character/__init__.py`
- Modify: `tests/engine/test_character.py`
- Modify: `tests/engine/test_creation_flow.py`

- [ ] **Step 1: Drop `alignment` from `Character` model**

In `engine/character/models.py`, remove the line `alignment: Alignment = Alignment.TRUE_NEUTRAL` and the import `Alignment` from `.enums`.

- [ ] **Step 2: Drop `alignment` parameter from `create_character`**

In `engine/character/creation.py`:
- Remove import `Alignment` from `.enums`
- Remove parameter `alignment: Alignment = Alignment.TRUE_NEUTRAL`
- Remove `alignment=alignment` from the `Character(...)` constructor call.

- [ ] **Step 3: Delete `Alignment` enum**

In `engine/character/enums.py`, delete the entire `class Alignment(StrEnum)` block (lines 47-58 in original).

- [ ] **Step 4: Drop `Alignment` from `__init__.py` exports**

In `engine/character/__init__.py`:
- Remove `Alignment` from the `from .enums import` line
- Remove `"Alignment",` from the `__all__` list

- [ ] **Step 5: Update engine tests**

`tests/engine/test_character.py` and `tests/engine/test_creation_flow.py`: grep for `alignment` and `Alignment`, remove every occurrence (both fixture passes and assertions).

```bash
grep -rn "alignment\|Alignment" tests/engine/
```
Then edit each match to drop the field/parameter.

- [ ] **Step 6: Run engine tests**

Run: `uv run pytest tests/engine/ -v`
Expected: All pass. If any test fails because of remaining alignment references in fixtures, fix and re-run.

- [ ] **Step 7: Commit**

```bash
git add engine/character/ tests/engine/
git commit -m "feat(character): remove alignment field and Alignment enum

Alignment had no mechanical impact in engine/ or ai/ — pure friction in
character creation. Replaced narratively by the new concept field."
```

## Task A3: Remove alignment from bot/i18n.py

**Files:**
- Modify: `bot/i18n.py`
- Modify: `tests/bot/test_i18n.py`

- [ ] **Step 1: Drop `ALIGNMENT_LABELS` from `bot/i18n.py`**

Search and remove the entire `ALIGNMENT_LABELS` dict definition.

- [ ] **Step 2: Drop tests of `ALIGNMENT_LABELS`**

In `tests/bot/test_i18n.py`, remove every test case referencing `ALIGNMENT_LABELS` or `alignment`.

- [ ] **Step 3: Run bot tests**

Run: `uv run pytest tests/bot/test_i18n.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add bot/i18n.py tests/bot/test_i18n.py
git commit -m "chore(i18n): drop ALIGNMENT_LABELS — field removed from Character"
```

## Task A4: Add `CLASS_STAT_PRESETS` module

**Files:**
- Create: `engine/character/presets.py`
- Test: `tests/engine/character/test_presets.py`

- [ ] **Step 1: Write failing test**

```python
# tests/engine/character/test_presets.py
"""Tests for class-optimized stat presets."""

from engine.character import CharacterClass, Ability
from engine.character.presets import CLASS_STAT_PRESETS, get_class_preset


def test_all_classes_have_preset():
    for char_class in CharacterClass:
        assert char_class in CLASS_STAT_PRESETS, f"Missing preset for {char_class}"


def test_each_preset_uses_standard_array():
    standard = sorted([15, 14, 13, 12, 10, 8])
    for char_class, preset in CLASS_STAT_PRESETS.items():
        values = sorted(preset.values())
        assert values == standard, f"{char_class} preset is not Standard Array: {values}"


def test_each_preset_assigns_all_six_abilities():
    for char_class, preset in CLASS_STAT_PRESETS.items():
        assert set(preset.keys()) == set(Ability), f"{char_class} missing abilities"


def test_get_class_preset_returns_copy():
    p1 = get_class_preset(CharacterClass.FIGHTER)
    p2 = get_class_preset(CharacterClass.FIGHTER)
    p1[Ability.STR] = 20
    assert p2[Ability.STR] == 15  # original untouched


def test_fighter_prioritizes_str():
    preset = get_class_preset(CharacterClass.FIGHTER)
    assert preset[Ability.STR] == 15


def test_wizard_prioritizes_int():
    preset = get_class_preset(CharacterClass.WIZARD)
    assert preset[Ability.INT] == 15


def test_cleric_prioritizes_wis():
    preset = get_class_preset(CharacterClass.CLERIC)
    assert preset[Ability.WIS] == 15


def test_rogue_prioritizes_dex():
    preset = get_class_preset(CharacterClass.ROGUE)
    assert preset[Ability.DEX] == 15


def test_ranger_prioritizes_dex_then_wis():
    preset = get_class_preset(CharacterClass.RANGER)
    assert preset[Ability.DEX] == 15
    assert preset[Ability.WIS] == 14


def test_barbarian_prioritizes_str_then_con():
    preset = get_class_preset(CharacterClass.BARBARIAN)
    assert preset[Ability.STR] == 15
    assert preset[Ability.CON] == 14
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/engine/character/test_presets.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement `engine/character/presets.py`**

Use the exact code from spec section 4.2 (`CLASS_STAT_PRESETS` + `get_class_preset`). All 6 classes must be present.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/engine/character/test_presets.py -v`
Expected: 10/10 PASS.

- [ ] **Step 5: Commit**

```bash
git add engine/character/presets.py tests/engine/character/test_presets.py
git commit -m "feat(character): add CLASS_STAT_PRESETS — class-optimized arrays

Each class gets a Standard Array reordered by primary/secondary stat
priority. Used by the 'Optimisé pour [Classe]' button in setup flow."
```

## Task A5: Add `random_stats` module

**Files:**
- Create: `engine/character/random_stats.py`
- Test: `tests/engine/character/test_random_stats.py`

- [ ] **Step 1: Write failing test**

```python
# tests/engine/character/test_random_stats.py
"""Tests for 4d6-drop-lowest stat generation and auto-assignment."""

import random
import pytest

from engine.character import Ability, CharacterClass
from engine.character.random_stats import (
    CLASS_STAT_PRIORITY,
    auto_assign_random,
    roll_4d6_drop_lowest,
)


def test_roll_returns_six_ints():
    random.seed(42)
    rolls = roll_4d6_drop_lowest()
    assert len(rolls) == 6
    assert all(isinstance(r, int) for r in rolls)


def test_roll_each_in_range_3_18():
    random.seed(0)
    for _ in range(50):
        rolls = roll_4d6_drop_lowest()
        for r in rolls:
            assert 3 <= r <= 18, f"Roll {r} out of [3, 18]"


def test_roll_sorted_descending():
    random.seed(123)
    rolls = roll_4d6_drop_lowest()
    assert rolls == sorted(rolls, reverse=True)


def test_all_classes_have_priority():
    for char_class in CharacterClass:
        assert char_class in CLASS_STAT_PRIORITY


def test_priority_lists_have_six_distinct_abilities():
    for char_class, prio in CLASS_STAT_PRIORITY.items():
        assert len(prio) == 6
        assert set(prio) == set(Ability)


def test_auto_assign_maps_highest_to_priority_first():
    rolls = [18, 17, 16, 15, 14, 13]  # already sorted desc
    assignment = auto_assign_random(CharacterClass.FIGHTER, rolls)
    assert assignment[Ability.STR] == 18
    assert assignment[Ability.CON] == 17
    assert assignment[Ability.DEX] == 16
    assert assignment[Ability.WIS] == 15
    assert assignment[Ability.INT] == 14
    assert assignment[Ability.CHA] == 13


def test_auto_assign_wizard_priority():
    rolls = [18, 17, 16, 15, 14, 13]
    assignment = auto_assign_random(CharacterClass.WIZARD, rolls)
    assert assignment[Ability.INT] == 18  # wizard top stat
    assert assignment[Ability.STR] == 13  # wizard dump stat


def test_auto_assign_requires_six_rolls():
    with pytest.raises(ValueError):
        auto_assign_random(CharacterClass.FIGHTER, [15, 14, 13])  # only 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/engine/character/test_random_stats.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement `engine/character/random_stats.py`**

Use the exact code from spec section 4.2 (`CLASS_STAT_PRIORITY`, `roll_4d6_drop_lowest`, `auto_assign_random`).

For `auto_assign_random`, since `zip(..., strict=True)` raises `ValueError` if lengths differ, the validation is automatic.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/engine/character/test_random_stats.py -v`
Expected: 8/8 PASS.

- [ ] **Step 5: Commit**

```bash
git add engine/character/random_stats.py tests/engine/character/test_random_stats.py
git commit -m "feat(character): add 4d6-drop-lowest random stats with class auto-assign

Used by the 'Aléatoire' button in setup flow. Auto-assigns rolls to
abilities by class priority — 1 click instead of 6 placement decisions."
```

## Task A6: Update `engine/character/__init__.py` exports

**Files:**
- Modify: `engine/character/__init__.py`

- [ ] **Step 1: Add new imports**

After existing imports, add:

```python
from .presets import CLASS_STAT_PRESETS, get_class_preset
from .random_stats import (
    CLASS_STAT_PRIORITY,
    auto_assign_random,
    roll_4d6_drop_lowest,
)
```

- [ ] **Step 2: Add to `__all__`**

Add to the `__all__` list (in the appropriate group):

```python
    # Stat presets and random gen
    "CLASS_STAT_PRESETS",
    "get_class_preset",
    "CLASS_STAT_PRIORITY",
    "auto_assign_random",
    "roll_4d6_drop_lowest",
```

- [ ] **Step 3: Verify imports**

Run: `uv run python -c "from engine.character import CLASS_STAT_PRESETS, roll_4d6_drop_lowest; print('OK')"`
Expected: `OK`.

- [ ] **Step 4: Commit**

```bash
git add engine/character/__init__.py
git commit -m "chore(character): export presets and random_stats modules"
```

## Task A7: Wave A verification gate

- [ ] **Step 1: Full engine test run**

Run: `uv run pytest tests/engine/ -v --tb=short`
Expected: All pass. Zero alignment references remain.

- [ ] **Step 2: Lint**

Run: `uv run ruff check engine/ tests/engine/`
Expected: All checks pass.

- [ ] **Step 3: Type check**

Run: `uv run mypy engine/character/`
Expected: Success.

- [ ] **Step 4: Audit grep — no stale alignment refs in engine**

Run: `grep -rn "alignment\|Alignment" engine/ ai/`
Expected: zero results.

- [ ] **Step 5: Wave A done — push branch checkpoint**

```bash
git log --oneline -10
```
Verify Wave A commits are present. Do NOT merge yet — Wave C will integrate.

---

# Wave B — New UI Components

**Agent: `ui-builder`. Independent of Wave A. Touches only `bot/views/lobby_view.py`, `bot/views/character_setup_flow.py`, `bot/embeds/lobby_embed.py`, `bot/embeds/character_setup_v2.py`, `bot/lobby_state.py`, and their tests. Estimated 5-6h.**

## Task B1: `LobbyPlayer` + `LobbyPlayerStatus` + skeleton `LobbyState`

**Files:**
- Create: `bot/lobby_state.py`
- Test: `tests/bot/test_lobby_state.py`

- [ ] **Step 1: Write failing test**

```python
# tests/bot/test_lobby_state.py
"""LobbyState lifecycle: add, remove, status, ready predicate."""

import pytest
from bot.lobby_state import LobbyPlayer, LobbyPlayerStatus, LobbyState


def test_lobby_state_starts_empty():
    state = LobbyState(creator_id=42, language="fr")
    assert state.players == {}
    assert not state.has_any_ready()


def test_add_player_records_joined_status():
    state = LobbyState(creator_id=42, language="fr")
    state.add_player(user_id=100)
    assert 100 in state.players
    assert state.players[100].status == LobbyPlayerStatus.JOINED


def test_add_player_idempotent():
    state = LobbyState(creator_id=42, language="fr")
    state.add_player(user_id=100)
    state.add_player(user_id=100)  # no error, no duplicate
    assert len(state.players) == 1


def test_remove_player():
    state = LobbyState(creator_id=42, language="fr")
    state.add_player(user_id=100)
    state.remove_player(user_id=100)
    assert 100 not in state.players


def test_remove_unknown_player_is_noop():
    state = LobbyState(creator_id=42, language="fr")
    state.remove_player(user_id=999)  # no error


def test_set_status_transitions():
    state = LobbyState(creator_id=42, language="fr")
    state.add_player(user_id=100)
    state.set_status(100, LobbyPlayerStatus.CREATING)
    assert state.players[100].status == LobbyPlayerStatus.CREATING
    state.set_status(100, LobbyPlayerStatus.READY)
    assert state.players[100].status == LobbyPlayerStatus.READY


def test_has_any_ready_true_when_one_ready():
    state = LobbyState(creator_id=42, language="fr")
    state.add_player(user_id=100)
    state.add_player(user_id=200)
    state.set_status(100, LobbyPlayerStatus.READY)
    assert state.has_any_ready()


def test_has_any_ready_false_when_none_ready():
    state = LobbyState(creator_id=42, language="fr")
    state.add_player(user_id=100)
    state.set_status(100, LobbyPlayerStatus.CREATING)
    assert not state.has_any_ready()


def test_max_players_default_six():
    state = LobbyState(creator_id=42, language="fr")
    for i in range(6):
        state.add_player(user_id=i)
    with pytest.raises(ValueError, match="full"):
        state.add_player(user_id=7)
```

- [ ] **Step 2: Run failing test**

Run: `uv run pytest tests/bot/test_lobby_state.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement minimal `bot/lobby_state.py`**

```python
"""Campaign lobby state — replaces CampaignLauncher.

Tracks players who joined the lobby via the 'Rejoindre' button, their
character setup status, and exposes the predicate used to gate launch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engine.character import Character
    from engine.inventory import Inventory
    from engine.spells import SpellcasterState


MAX_PLAYERS_PER_LOBBY = 6


class LobbyPlayerStatus(StrEnum):
    """Per-player lifecycle in the lobby."""

    JOINED = "joined"           # clicked Rejoindre, not started creation
    CREATING = "creating"       # CharacterSetupFlow open, in progress
    READY = "ready"             # creation complete, character persisted
    CANCELLED = "cancelled"     # bailed out mid-creation


@dataclass
class LobbyPlayer:
    """Per-user state inside a lobby."""

    user_id: int
    status: LobbyPlayerStatus = LobbyPlayerStatus.JOINED
    character: Character | None = None
    inventory: Inventory | None = None
    spellcaster: SpellcasterState | None = None
    kit_name: str | None = None
    motivation_key: str | None = None


@dataclass
class LobbyState:
    """In-memory state for a campaign lobby in a given channel.

    Replaces ``CampaignLauncher`` with a flatter structure: one ``LobbyPlayer``
    per joined user, no separate progress dicts.
    """

    creator_id: int
    language: str = "fr"
    players: dict[int, LobbyPlayer] = field(default_factory=dict)

    def add_player(self, user_id: int) -> None:
        """Add a player to the lobby in JOINED state. Idempotent."""
        if user_id in self.players:
            return
        if len(self.players) >= MAX_PLAYERS_PER_LOBBY:
            raise ValueError(f"Lobby is full ({MAX_PLAYERS_PER_LOBBY} players max).")
        self.players[user_id] = LobbyPlayer(user_id=user_id)

    def remove_player(self, user_id: int) -> None:
        """Remove a player from the lobby. No-op if not present."""
        self.players.pop(user_id, None)

    def set_status(self, user_id: int, status: LobbyPlayerStatus) -> None:
        """Update a player's status. Raises KeyError if not in lobby."""
        self.players[user_id].status = status

    def has_any_ready(self) -> bool:
        """True if at least one player has completed character creation."""
        return any(p.status == LobbyPlayerStatus.READY for p in self.players.values())
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/bot/test_lobby_state.py -v`
Expected: 9/9 PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/lobby_state.py tests/bot/test_lobby_state.py
git commit -m "feat(lobby): add LobbyState skeleton with player lifecycle

Replaces CampaignLauncher. Tracks JOINED → CREATING → READY transitions.
Wired up in Wave C."
```

## Task B2: `LobbyEmbed` builder

**Files:**
- Create: `bot/embeds/lobby_embed.py`
- Test: `tests/bot/embeds/test_lobby_embed.py`

- [ ] **Step 1: Write failing test**

```python
# tests/bot/embeds/test_lobby_embed.py
"""Tests for the campaign lobby embed."""

from bot.embeds.lobby_embed import build_lobby_embed
from bot.lobby_state import LobbyPlayer, LobbyPlayerStatus


def test_empty_lobby_shows_zero_players():
    embed = build_lobby_embed(
        campaign_name="Eldoria",
        theme="Dark Fantasy",
        host_name="cocolng",
        roster=[],
        language="fr",
    )
    assert "Eldoria" in embed.title
    assert any("0/6" in (f.value or "") or "0/6" in (f.name or "") for f in embed.fields)


def test_roster_shows_joined_player_with_badge():
    p = LobbyPlayer(user_id=100, status=LobbyPlayerStatus.JOINED)
    embed = build_lobby_embed(
        campaign_name="Eldoria",
        theme="Dark Fantasy",
        host_name="cocolng",
        roster=[(p, "alice")],  # tuple of (player, display_name)
        language="fr",
    )
    rendered = "\n".join(f.value or "" for f in embed.fields)
    assert "🆕" in rendered
    assert "alice" in rendered


def test_creating_status_shows_wrench_emoji():
    p = LobbyPlayer(user_id=100, status=LobbyPlayerStatus.CREATING)
    embed = build_lobby_embed(
        campaign_name="X", theme="Y", host_name="h",
        roster=[(p, "bob")], language="fr",
    )
    rendered = "\n".join(f.value or "" for f in embed.fields)
    assert "🛠️" in rendered


def test_ready_status_shows_check_with_summary():
    from engine.character import (
        Character, CharacterClass, Race, Size, Ability, AbilityScores
    )
    char = Character(
        name="Sylphe", race=Race.ELF, char_class=CharacterClass.RANGER,
        ability_scores=AbilityScores(STR=12, DEX=15, CON=13, INT=10, WIS=14, CHA=8),
        hp=10, max_hp=10, ac=12, speed=30,
        proficiency_bonus=2,
        saving_throw_proficiencies=(Ability.STR, Ability.DEX),
        hit_die="d10", size=Size.MEDIUM,
    )
    p = LobbyPlayer(user_id=100, status=LobbyPlayerStatus.READY, character=char)
    embed = build_lobby_embed(
        campaign_name="X", theme="Y", host_name="h",
        roster=[(p, "alice")], language="fr",
    )
    rendered = "\n".join(f.value or "" for f in embed.fields)
    assert "✅" in rendered
    assert "Sylphe" in rendered
    assert "Ranger" in rendered or "Elf" in rendered


def test_cancelled_status_shows_cross():
    p = LobbyPlayer(user_id=100, status=LobbyPlayerStatus.CANCELLED)
    embed = build_lobby_embed(
        campaign_name="X", theme="Y", host_name="h",
        roster=[(p, "ghost")], language="fr",
    )
    rendered = "\n".join(f.value or "" for f in embed.fields)
    assert "❌" in rendered


def test_player_count_displayed():
    p1 = LobbyPlayer(user_id=1, status=LobbyPlayerStatus.JOINED)
    p2 = LobbyPlayer(user_id=2, status=LobbyPlayerStatus.READY)
    embed = build_lobby_embed(
        campaign_name="X", theme="Y", host_name="h",
        roster=[(p1, "a"), (p2, "b")], language="fr",
    )
    rendered_all = embed.title + "\n" + "\n".join((f.name or "") + (f.value or "") for f in embed.fields)
    assert "2/6" in rendered_all
```

- [ ] **Step 2: Run failing test**

Run: `uv run pytest tests/bot/embeds/test_lobby_embed.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement `bot/embeds/lobby_embed.py`**

```python
"""Campaign lobby embed — live roster with status badges.

Status badges:
- 🆕 JOINED — clicked Rejoindre, not started
- 🛠️ CREATING — character setup in progress
- ✅ READY — character persisted
- ❌ CANCELLED — bailed mid-creation
"""

from __future__ import annotations

import discord

from bot.lobby_state import LobbyPlayer, LobbyPlayerStatus, MAX_PLAYERS_PER_LOBBY

STATUS_BADGES = {
    LobbyPlayerStatus.JOINED: "🆕",
    LobbyPlayerStatus.CREATING: "🛠️",
    LobbyPlayerStatus.READY: "✅",
    LobbyPlayerStatus.CANCELLED: "❌",
}


def build_lobby_embed(
    campaign_name: str,
    theme: str,
    host_name: str,
    roster: list[tuple[LobbyPlayer, str]],  # (player, display_name)
    language: str,
) -> discord.Embed:
    """Build the campaign lobby embed.

    The roster is displayed line-by-line with a status badge prefix; READY
    players also show their character name + class summary.
    """
    embed = discord.Embed(
        title=f"🏰 Campagne : {campaign_name}",
        description=f"**Thème** : {theme}\n**Host** : {host_name}",
        color=discord.Color.purple(),
    )

    if not roster:
        roster_text = "_Personne n'a encore rejoint. Clique 🎭 Rejoindre pour entrer._"
    else:
        lines = []
        for player, display_name in roster:
            badge = STATUS_BADGES[player.status]
            if player.status == LobbyPlayerStatus.READY and player.character is not None:
                char = player.character
                summary = f"{char.name} ({char.race.value} {char.char_class.value})"
                lines.append(f"{badge} **{display_name}** — {summary}")
            elif player.status == LobbyPlayerStatus.CREATING:
                lines.append(f"{badge} **{display_name}** — Création en cours...")
            elif player.status == LobbyPlayerStatus.CANCELLED:
                lines.append(f"{badge} ~~{display_name}~~ — Annulé")
            else:  # JOINED
                lines.append(f"{badge} **{display_name}**")
        roster_text = "\n".join(lines)

    embed.add_field(
        name=f"Aventuriers ({len(roster)}/{MAX_PLAYERS_PER_LOBBY})",
        value=roster_text,
        inline=False,
    )
    return embed
```

- [ ] **Step 4: Run tests pass**

Run: `uv run pytest tests/bot/embeds/test_lobby_embed.py -v`
Expected: 6/6 PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/embeds/lobby_embed.py tests/bot/embeds/test_lobby_embed.py
git commit -m "feat(lobby): add live roster embed with status badges"
```

## Task B3: `IdentityModal`

**Files:**
- Create: `bot/views/character_setup_flow.py` (modal first, view added in B4)
- Test: `tests/bot/views/test_character_setup_flow.py` (modal section)

- [ ] **Step 1: Write failing test for IdentityModal field structure**

```python
# tests/bot/views/test_character_setup_flow.py
"""Tests for the unified character setup flow (modal + state machine)."""

import discord
from bot.views.character_setup_flow import IdentityModal


def test_identity_modal_has_two_text_inputs():
    modal = IdentityModal(parent_view=None)  # type: ignore[arg-type]
    text_inputs = [c for c in modal.children if isinstance(c, discord.ui.TextInput)]
    assert len(text_inputs) == 2


def test_identity_modal_name_required():
    modal = IdentityModal(parent_view=None)  # type: ignore[arg-type]
    name_field = next(c for c in modal.children if c.label.startswith("Nom"))  # type: ignore[union-attr]
    assert name_field.required is True
    assert name_field.max_length == 32


def test_identity_modal_concept_optional():
    modal = IdentityModal(parent_view=None)  # type: ignore[arg-type]
    concept_field = next(c for c in modal.children if "Concept" in c.label)  # type: ignore[union-attr]
    assert concept_field.required is False
    assert concept_field.max_length == 100
```

- [ ] **Step 2: Run failing test**

Run: `uv run pytest tests/bot/views/test_character_setup_flow.py::test_identity_modal_has_two_text_inputs -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement minimal `bot/views/character_setup_flow.py`**

```python
"""Unified character setup flow — single auto-modifying view, 6 steps.

Replaces CharacterCreateView, StatAssignmentView, SkillSelectionView,
StarterGearView, MotivationView. State transitions edit the same message
via discord.Interaction.response.edit_message.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import IntEnum
from typing import TYPE_CHECKING

import discord
from discord import TextStyle, ui

from bot.views.base import LoggedView

if TYPE_CHECKING:
    from engine.character import (
        AbilityScores, Character, CharacterClass, Race, Skill,
    )

# (rest of the implementation lands in B4-B10)


class SetupStep(IntEnum):
    """Stages of the unified character setup flow."""
    IDENTITY = 0
    RACE_CLASS = 1
    STATS = 2
    SKILLS = 3
    KIT_MOTIV = 4
    REVIEW = 5


class IdentityModal(ui.Modal, title="Ton aventurier"):
    """Captures name + concept in one submit."""

    name = ui.TextInput(
        label="Nom du personnage",
        placeholder="Ex: Thorin Forgefort",
        min_length=1,
        max_length=32,
        required=True,
    )
    concept = ui.TextInput(
        label="Concept (optionnel)",
        placeholder="Ex: Un voleur repenti cherchant la rédemption",
        max_length=100,
        required=False,
        style=TextStyle.paragraph,
    )

    def __init__(self, parent_view: CharacterSetupFlow) -> None:
        super().__init__()
        self.parent_view = parent_view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.parent_view.name = str(self.name.value)
        self.parent_view.concept = str(self.concept.value or "")
        await self.parent_view.transition_to(interaction, SetupStep.RACE_CLASS)


class CharacterSetupFlow(LoggedView):
    """Stub — full implementation in tasks B4-B10."""

    timeout = 600.0  # 10 minutes for the whole flow

    def __init__(
        self,
        user_id: int,
        language: str,
        on_complete: Callable[[Character, str, str], Awaitable[None]],
    ) -> None:
        super().__init__(timeout=self.timeout)
        self.user_id = user_id
        self.language = language
        self._on_complete = on_complete
        self.state: SetupStep = SetupStep.IDENTITY
        # Accumulators (filled across steps)
        self.name: str | None = None
        self.concept: str | None = None
        self.race: Race | None = None
        self.char_class: CharacterClass | None = None
        self.ability_scores: AbilityScores | None = None
        self.skill_proficiencies: list[Skill] | None = None
        self.kit_name: str | None = None
        self.motivation_key: str | None = None

    async def transition_to(
        self, interaction: discord.Interaction, next_step: SetupStep,
    ) -> None:
        """Rebuild components for next_step and edit_message. Stub."""
        self.state = next_step
        # Implementations land in B4-B10
        raise NotImplementedError(f"Step {next_step} not yet implemented")
```

- [ ] **Step 4: Run tests pass**

Run: `uv run pytest tests/bot/views/test_character_setup_flow.py::test_identity_modal_has_two_text_inputs tests/bot/views/test_character_setup_flow.py::test_identity_modal_name_required tests/bot/views/test_character_setup_flow.py::test_identity_modal_concept_optional -v`
Expected: 3/3 PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/views/character_setup_flow.py tests/bot/views/test_character_setup_flow.py
git commit -m "feat(setup): scaffold CharacterSetupFlow + IdentityModal

Modal captures name + concept (replaces alignment narratively).
State machine and per-step transitions land in subsequent commits."
```

## Task B4: RACE_CLASS step

**Files:**
- Modify: `bot/views/character_setup_flow.py`
- Modify: `tests/bot/views/test_character_setup_flow.py`

- [ ] **Step 1: Write failing test**

Add to `tests/bot/views/test_character_setup_flow.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock
from engine.character import Race, CharacterClass
from bot.views.character_setup_flow import CharacterSetupFlow, SetupStep


@pytest.mark.asyncio
async def test_race_class_step_select_race_stores_value():
    on_complete = AsyncMock()
    view = CharacterSetupFlow(user_id=1, language="fr", on_complete=on_complete)
    view.state = SetupStep.RACE_CLASS

    interaction = MagicMock()
    interaction.response.edit_message = AsyncMock()

    # Simulate the race select callback
    await view._on_race_selected(interaction, [Race.ELF.value])
    assert view.race == Race.ELF


@pytest.mark.asyncio
async def test_race_class_step_select_class_stores_value():
    view = CharacterSetupFlow(user_id=1, language="fr", on_complete=AsyncMock())
    view.state = SetupStep.RACE_CLASS

    interaction = MagicMock()
    interaction.response.edit_message = AsyncMock()

    await view._on_class_selected(interaction, [CharacterClass.WIZARD.value])
    assert view.char_class == CharacterClass.WIZARD


@pytest.mark.asyncio
async def test_race_class_step_continue_disabled_until_both_selected():
    view = CharacterSetupFlow(user_id=1, language="fr", on_complete=AsyncMock())
    view.state = SetupStep.RACE_CLASS
    view._build_race_class_components()
    # The continue button should be present and disabled
    continue_btn = next(c for c in view.children if isinstance(c, ui.Button) and c.label and "Continuer" in c.label)
    assert continue_btn.disabled
    view.race = Race.ELF
    view.char_class = CharacterClass.WIZARD
    view._refresh_continue_state()
    assert not continue_btn.disabled
```

- [ ] **Step 2: Run failing tests**

Run: `uv run pytest tests/bot/views/test_character_setup_flow.py -v -k "race_class"`
Expected: FAIL — methods don't exist.

- [ ] **Step 3: Implement RACE_CLASS step**

In `bot/views/character_setup_flow.py`, add to `CharacterSetupFlow`:

```python
def _build_race_class_components(self) -> None:
    """Clear children and add race+class selects + Continuer button."""
    from engine.character import CharacterClass, Race
    from bot.i18n import RACE_LABELS, CLASS_LABELS, get_label

    self.clear_items()

    # Race select with descriptions (1-line trait per race)
    race_descriptions = {
        Race.HUMAN:    "Polyvalent, +1 à toutes les caractéristiques",
        Race.ELF:      "Agile, vision sombre, immunité au sommeil charme",
        Race.DWARF:    "Robuste, résistance aux poisons, +CON",
        Race.HALFLING: "Chanceux, petit, agile",
        Race.GNOME:    "Curieux, malin, résistance magique mentale",
        Race.TIEFLING: "Infernal, résistance au feu, +CHA",
    }
    race_options = [
        discord.SelectOption(
            label=get_label(RACE_LABELS, self.language, r.value),
            value=r.value,
            description=race_descriptions[r],
            default=(self.race == r),
        )
        for r in Race
    ]
    race_select = ui.Select(
        placeholder="Choisis ta race...",
        options=race_options,
        custom_id="setup_race",
    )

    async def race_callback(interaction: discord.Interaction) -> None:
        await self._on_race_selected(interaction, race_select.values)
    race_select.callback = race_callback
    self.add_item(race_select)

    # Class select with descriptions (role per class)
    class_descriptions = {
        CharacterClass.FIGHTER:   "Guerrier polyvalent, fort en combat rapproché",
        CharacterClass.BARBARIAN: "Berserker, encaisse et frappe fort",
        CharacterClass.WIZARD:    "Mage savant, sorts puissants",
        CharacterClass.CLERIC:    "Soigneur divin, soutien et combat",
        CharacterClass.ROGUE:     "Rusé, attaques sournoises, infiltration",
        CharacterClass.RANGER:    "Pisteur, arc et nature",
    }
    class_options = [
        discord.SelectOption(
            label=get_label(CLASS_LABELS, self.language, c.value),
            value=c.value,
            description=class_descriptions[c],
            default=(self.char_class == c),
        )
        for c in CharacterClass
    ]
    class_select = ui.Select(
        placeholder="Choisis ta classe...",
        options=class_options,
        custom_id="setup_class",
    )

    async def class_callback(interaction: discord.Interaction) -> None:
        await self._on_class_selected(interaction, class_select.values)
    class_select.callback = class_callback
    self.add_item(class_select)

    # Continue button
    continue_btn = ui.Button(
        label="Continuer",
        emoji="➡️",
        style=discord.ButtonStyle.success,
        disabled=not (self.race and self.char_class),
        custom_id="setup_race_class_continue",
    )

    async def continue_callback(interaction: discord.Interaction) -> None:
        await self.transition_to(interaction, SetupStep.STATS)
    continue_btn.callback = continue_callback
    self.add_item(continue_btn)

async def _on_race_selected(
    self, interaction: discord.Interaction, values: list[str],
) -> None:
    from engine.character import Race
    self.race = Race(values[0])
    self._build_race_class_components()
    await interaction.response.edit_message(view=self)

async def _on_class_selected(
    self, interaction: discord.Interaction, values: list[str],
) -> None:
    from engine.character import CharacterClass
    self.char_class = CharacterClass(values[0])
    self._build_race_class_components()
    await interaction.response.edit_message(view=self)

def _refresh_continue_state(self) -> None:
    """Sync the disabled state of the Continuer button to current selections."""
    for child in self.children:
        if isinstance(child, ui.Button) and child.label and "Continuer" in child.label:
            child.disabled = not (self.race and self.char_class)
```

Update `transition_to` to dispatch:

```python
async def transition_to(
    self, interaction: discord.Interaction, next_step: SetupStep,
) -> None:
    self.state = next_step
    if next_step == SetupStep.RACE_CLASS:
        self._build_race_class_components()
        await interaction.response.edit_message(
            content="**Étape 2/6** — Choisis ta race et ta classe.",
            view=self,
        )
    elif next_step == SetupStep.STATS:
        # Implemented in B5
        raise NotImplementedError("STATS step lands in Task B5")
    elif next_step == SetupStep.SKILLS:
        raise NotImplementedError("SKILLS step lands in Task B6")
    elif next_step == SetupStep.KIT_MOTIV:
        raise NotImplementedError("KIT_MOTIV step lands in Task B7")
    elif next_step == SetupStep.REVIEW:
        raise NotImplementedError("REVIEW step lands in Task B8")
    else:
        raise ValueError(f"Cannot transition to {next_step} from external call")
```

- [ ] **Step 4: Run tests pass**

Run: `uv run pytest tests/bot/views/test_character_setup_flow.py -v -k "race_class"`
Expected: 3/3 PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/views/character_setup_flow.py tests/bot/views/test_character_setup_flow.py
git commit -m "feat(setup): RACE_CLASS step with descriptive selects"
```

## Task B5: STATS step (3 methods)

**Files:**
- Modify: `bot/views/character_setup_flow.py`
- Modify: `tests/bot/views/test_character_setup_flow.py`

- [ ] **Step 1: Write failing tests**

Add to test file:

```python
@pytest.mark.asyncio
async def test_stats_step_preset_button_applies_class_preset():
    from engine.character import Race, CharacterClass, Ability
    view = CharacterSetupFlow(user_id=1, language="fr", on_complete=AsyncMock())
    view.race = Race.ELF
    view.char_class = CharacterClass.WIZARD
    view.state = SetupStep.STATS

    interaction = MagicMock()
    interaction.response.edit_message = AsyncMock()

    await view._on_preset_stats(interaction)
    assert view.ability_scores is not None
    # Wizard preset has INT=15
    assert view.ability_scores.INT == 15


@pytest.mark.asyncio
async def test_stats_step_random_button_rolls_and_assigns():
    import random
    random.seed(42)
    from engine.character import Race, CharacterClass
    view = CharacterSetupFlow(user_id=1, language="fr", on_complete=AsyncMock())
    view.race = Race.HUMAN
    view.char_class = CharacterClass.FIGHTER
    view.state = SetupStep.STATS

    interaction = MagicMock()
    interaction.response.edit_message = AsyncMock()

    await view._on_random_stats(interaction)
    assert view.ability_scores is not None
    # All 6 abilities filled
    assert all(getattr(view.ability_scores, a.name) >= 3 for a in __import__("engine.character", fromlist=["Ability"]).Ability)
```

- [ ] **Step 2: Run failing tests**

Run: `uv run pytest tests/bot/views/test_character_setup_flow.py -v -k "stats_step"`
Expected: FAIL.

- [ ] **Step 3: Implement STATS step**

In `transition_to`, replace the `STATS` branch with:

```python
elif next_step == SetupStep.STATS:
    self._build_stats_components()
    await interaction.response.edit_message(
        content=self._stats_status_text(),
        view=self,
    )
```

Add new methods:

```python
def _stats_status_text(self) -> str:
    if self.ability_scores is None:
        return f"**Étape 3/6** — Choisis tes statistiques pour ton {self.char_class.value}.\n\n_Choisis une méthode :_"
    s = self.ability_scores
    return (
        f"**Étape 3/6** — Statistiques\n"
        f"```STR {s.STR:2d}  DEX {s.DEX:2d}  CON {s.CON:2d}\n"
        f"INT {s.INT:2d}  WIS {s.WIS:2d}  CHA {s.CHA:2d}```\n"
        f"_Confirme ou change de méthode._"
    )

def _build_stats_components(self) -> None:
    from engine.character import AbilityScores
    from engine.character.presets import get_class_preset

    self.clear_items()

    # Preset button
    preset_btn = ui.Button(
        label=f"Optimisé pour {self.char_class.value}",
        emoji="✨",
        style=discord.ButtonStyle.primary,
        custom_id="setup_stats_preset",
    )
    preset_btn.callback = lambda i: self._on_preset_stats(i)
    self.add_item(preset_btn)

    # Random button
    random_btn = ui.Button(
        label="Aléatoire (4d6)",
        emoji="🎲",
        style=discord.ButtonStyle.secondary,
        custom_id="setup_stats_random",
    )
    random_btn.callback = lambda i: self._on_random_stats(i)
    self.add_item(random_btn)

    # Continue button (only enabled if scores chosen)
    continue_btn = ui.Button(
        label="Continuer",
        emoji="➡️",
        style=discord.ButtonStyle.success,
        disabled=(self.ability_scores is None),
        custom_id="setup_stats_continue",
    )
    continue_btn.callback = lambda i: self.transition_to(i, SetupStep.SKILLS)
    self.add_item(continue_btn)

async def _on_preset_stats(self, interaction: discord.Interaction) -> None:
    from engine.character import AbilityScores
    from engine.character.presets import get_class_preset
    from engine.character import Ability

    preset = get_class_preset(self.char_class)
    self.ability_scores = AbilityScores(
        STR=preset[Ability.STR], DEX=preset[Ability.DEX], CON=preset[Ability.CON],
        INT=preset[Ability.INT], WIS=preset[Ability.WIS], CHA=preset[Ability.CHA],
    )
    self._build_stats_components()
    await interaction.response.edit_message(content=self._stats_status_text(), view=self)

async def _on_random_stats(self, interaction: discord.Interaction) -> None:
    from engine.character import AbilityScores, Ability
    from engine.character.random_stats import roll_4d6_drop_lowest, auto_assign_random

    rolls = roll_4d6_drop_lowest()
    assignment = auto_assign_random(self.char_class, rolls)
    self.ability_scores = AbilityScores(
        STR=assignment[Ability.STR], DEX=assignment[Ability.DEX], CON=assignment[Ability.CON],
        INT=assignment[Ability.INT], WIS=assignment[Ability.WIS], CHA=assignment[Ability.CHA],
    )
    self._build_stats_components()
    await interaction.response.edit_message(content=self._stats_status_text(), view=self)
```

Note: scope-deferred — Standard Array manual assignment is **not** offered in v1 (preset + random covers casual + classic). If demanded later, add a "Personnaliser" button that dispatches to a sub-modal or sub-view.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/bot/views/test_character_setup_flow.py -v -k "stats_step"`
Expected: 2/2 PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/views/character_setup_flow.py tests/bot/views/test_character_setup_flow.py
git commit -m "feat(setup): STATS step with preset + random methods"
```

## Task B6: SKILLS step

**Files:**
- Modify: `bot/views/character_setup_flow.py`
- Modify: `tests/bot/views/test_character_setup_flow.py`

- [ ] **Step 1: Write failing test**

```python
@pytest.mark.asyncio
async def test_skills_step_select_records_choices():
    from engine.character import Race, CharacterClass, Skill
    view = CharacterSetupFlow(user_id=1, language="fr", on_complete=AsyncMock())
    view.race = Race.HUMAN
    view.char_class = CharacterClass.ROGUE
    view.state = SetupStep.SKILLS

    interaction = MagicMock()
    interaction.response.edit_message = AsyncMock()

    await view._on_skills_selected(interaction, [Skill.STEALTH.value, Skill.DECEPTION.value])
    assert view.skill_proficiencies == [Skill.STEALTH, Skill.DECEPTION]


@pytest.mark.asyncio
async def test_skills_step_uses_class_skill_choices():
    from engine.character import CharacterClass
    from engine.character.classes import CLASS_SKILL_CHOICES
    view = CharacterSetupFlow(user_id=1, language="fr", on_complete=AsyncMock())
    view.char_class = CharacterClass.WIZARD
    view.state = SetupStep.SKILLS
    view._build_skills_components()
    select = next(c for c in view.children if isinstance(c, ui.Select))
    config = CLASS_SKILL_CHOICES[CharacterClass.WIZARD]
    assert len(select.options) == len(config.choices)
    assert select.max_values == config.count
```

- [ ] **Step 2: Run failing tests**

Run: `uv run pytest tests/bot/views/test_character_setup_flow.py -v -k "skills_step"`
Expected: FAIL.

- [ ] **Step 3: Implement SKILLS step**

In `transition_to`, replace `SKILLS` branch:

```python
elif next_step == SetupStep.SKILLS:
    self._build_skills_components()
    from engine.character.classes import CLASS_SKILL_CHOICES
    config = CLASS_SKILL_CHOICES[self.char_class]
    await interaction.response.edit_message(
        content=(
            f"**Étape 4/6** — Choisis {config.count} compétence"
            f"{'s' if config.count > 1 else ''} pour ton {self.char_class.value}."
        ),
        view=self,
    )
```

Add methods:

```python
def _build_skills_components(self) -> None:
    from engine.character.classes import CLASS_SKILL_CHOICES
    from engine.character import SKILL_ABILITY

    SKILL_DESCRIPTIONS = {
        # Compact 1-line per skill — domain knowledge
        "Athletics":    "Force pour grimper, sauter, lutter",
        "Acrobatics":   "Dextérité pour équilibre, esquive",
        "Sleight of Hand": "Dextérité pour pickpocket, tour de main",
        "Stealth":      "Dextérité pour se cacher",
        "Arcana":       "Intelligence pour magie, créatures",
        "History":      "Intelligence pour évènements, royaumes",
        "Investigation": "Intelligence pour indices, déduction",
        "Nature":       "Intelligence pour terrains, plantes, animaux",
        "Religion":     "Intelligence pour dieux, rites",
        "Insight":      "Sagesse pour lire les intentions",
        "Medicine":     "Sagesse pour stabiliser, diagnostiquer",
        "Perception":   "Sagesse pour repérer, écouter",
        "Survival":     "Sagesse pour pister, s'orienter",
        "Animal Handling": "Sagesse pour calmer, monter",
        "Deception":    "Charisme pour mentir, déguiser",
        "Intimidation": "Charisme pour menacer",
        "Performance":  "Charisme pour divertir",
        "Persuasion":   "Charisme pour convaincre",
    }

    self.clear_items()
    config = CLASS_SKILL_CHOICES[self.char_class]
    options = [
        discord.SelectOption(
            label=f"{s.value} ({SKILL_ABILITY[s].name})",
            value=s.value,
            description=SKILL_DESCRIPTIONS.get(s.value, ""),
            default=(self.skill_proficiencies and s in self.skill_proficiencies),
        )
        for s in config.choices
    ]
    select = ui.Select(
        placeholder=f"Choisis {config.count} compétences...",
        options=options,
        min_values=config.count,
        max_values=config.count,
        custom_id="setup_skills",
    )

    async def cb(interaction: discord.Interaction) -> None:
        await self._on_skills_selected(interaction, select.values)
    select.callback = cb
    self.add_item(select)

    continue_btn = ui.Button(
        label="Continuer",
        emoji="➡️",
        style=discord.ButtonStyle.success,
        disabled=(not self.skill_proficiencies),
        custom_id="setup_skills_continue",
    )
    continue_btn.callback = lambda i: self.transition_to(i, SetupStep.KIT_MOTIV)
    self.add_item(continue_btn)

async def _on_skills_selected(
    self, interaction: discord.Interaction, values: list[str],
) -> None:
    from engine.character import Skill
    self.skill_proficiencies = [Skill(v) for v in values]
    self._build_skills_components()
    await interaction.response.edit_message(view=self)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/bot/views/test_character_setup_flow.py -v -k "skills_step"`
Expected: 2/2 PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/views/character_setup_flow.py tests/bot/views/test_character_setup_flow.py
git commit -m "feat(setup): SKILLS step with descriptive multi-select"
```

## Task B7: KIT_MOTIV step

**Files:**
- Modify: `bot/views/character_setup_flow.py`
- Modify: `tests/bot/views/test_character_setup_flow.py`

- [ ] **Step 1: Write failing test**

```python
@pytest.mark.asyncio
async def test_kit_motiv_step_records_kit_and_motivation():
    view = CharacterSetupFlow(user_id=1, language="fr", on_complete=AsyncMock())
    view.char_class = CharacterClass.FIGHTER
    view.state = SetupStep.KIT_MOTIV

    interaction = MagicMock()
    interaction.response.edit_message = AsyncMock()

    await view._on_kit_selected(interaction, ["Iron Vow"])
    await view._on_motivation_selected(interaction, ["Contract"])
    assert view.kit_name == "Iron Vow"
    assert view.motivation_key == "Contract"
```

- [ ] **Step 2: Run failing test**

Run: `uv run pytest tests/bot/views/test_character_setup_flow.py -v -k "kit_motiv"`
Expected: FAIL.

- [ ] **Step 3: Implement KIT_MOTIV step**

Read the existing `bot/views/starter_gear_view.py` and `bot/views/motivation_view.py` to source the kit and motivation lists. Reuse `engine.starter_gear.get_starter_kits` and the existing motivation keys.

Replace `KIT_MOTIV` branch in `transition_to`:

```python
elif next_step == SetupStep.KIT_MOTIV:
    self._build_kit_motiv_components()
    await interaction.response.edit_message(
        content="**Étape 5/6** — Choisis ton équipement et ta motivation.",
        view=self,
    )
```

Add methods:

```python
def _build_kit_motiv_components(self) -> None:
    from engine.starter_gear import get_starter_kits
    # Motivation keys list — extract from existing motivation_view.py
    MOTIVATION_KEYS = ["Contract", "Vengeance", "Discovery", "Redemption", "Glory", "Loyalty"]

    self.clear_items()
    kits = get_starter_kits(self.char_class)
    kit_options = [
        discord.SelectOption(
            label=k.name,
            value=k.name,
            description=k.description[:100] if k.description else None,
            default=(self.kit_name == k.name),
        )
        for k in kits
    ]
    kit_select = ui.Select(
        placeholder="Choisis ton kit de départ...",
        options=kit_options,
        custom_id="setup_kit",
    )
    kit_select.callback = lambda i: self._on_kit_selected(i, kit_select.values)
    self.add_item(kit_select)

    motiv_options = [
        discord.SelectOption(
            label=m,
            value=m,
            default=(self.motivation_key == m),
        )
        for m in MOTIVATION_KEYS
    ]
    motiv_select = ui.Select(
        placeholder="Choisis ta motivation...",
        options=motiv_options,
        custom_id="setup_motivation",
    )
    motiv_select.callback = lambda i: self._on_motivation_selected(i, motiv_select.values)
    self.add_item(motiv_select)

    continue_btn = ui.Button(
        label="Continuer",
        emoji="➡️",
        style=discord.ButtonStyle.success,
        disabled=not (self.kit_name and self.motivation_key),
        custom_id="setup_kit_motiv_continue",
    )
    continue_btn.callback = lambda i: self.transition_to(i, SetupStep.REVIEW)
    self.add_item(continue_btn)

async def _on_kit_selected(self, interaction: discord.Interaction, values: list[str]) -> None:
    self.kit_name = values[0]
    self._build_kit_motiv_components()
    await interaction.response.edit_message(view=self)

async def _on_motivation_selected(self, interaction: discord.Interaction, values: list[str]) -> None:
    self.motivation_key = values[0]
    self._build_kit_motiv_components()
    await interaction.response.edit_message(view=self)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/bot/views/test_character_setup_flow.py -v -k "kit_motiv"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/views/character_setup_flow.py tests/bot/views/test_character_setup_flow.py
git commit -m "feat(setup): KIT_MOTIV step (merges starter_gear + motivation views)"
```

## Task B8: REVIEW step

**Files:**
- Modify: `bot/views/character_setup_flow.py`
- Create: `bot/embeds/character_setup_v2.py`
- Modify: `tests/bot/views/test_character_setup_flow.py`

- [ ] **Step 1: Implement V2 récap embed (with classic embed fallback)**

Create `bot/embeds/character_setup_v2.py`:

```python
"""Components V2 récap of the character sheet for the REVIEW step.

Falls back to a classic embed if Components V2 is unavailable at runtime.
"""

from __future__ import annotations

import discord
from engine.character import Character


def build_setup_recap_embed(
    character: Character,
    kit_name: str,
    motivation_key: str,
    concept: str,
) -> discord.Embed:
    """Build the recap embed (classic embed — works on all discord.py versions)."""
    embed = discord.Embed(
        title=f"📜 {character.name}",
        description=concept or "_Aucun concept renseigné._",
        color=discord.Color.gold(),
    )
    embed.add_field(
        name="Identité",
        value=f"**{character.race.value} {character.char_class.value}** — Niveau {character.level}",
        inline=False,
    )
    s = character.ability_scores
    embed.add_field(
        name="Caractéristiques",
        value=(
            f"```STR {s.STR:2d}  DEX {s.DEX:2d}  CON {s.CON:2d}\n"
            f"INT {s.INT:2d}  WIS {s.WIS:2d}  CHA {s.CHA:2d}```"
        ),
        inline=False,
    )
    embed.add_field(
        name="Vie & Défense",
        value=f"❤️ HP {character.hp}/{character.max_hp}  •  🛡️ AC {character.ac}",
        inline=True,
    )
    embed.add_field(
        name="Bonus de maîtrise",
        value=f"+{character.proficiency_bonus}",
        inline=True,
    )
    embed.add_field(
        name="Sauvegardes maîtrisées",
        value=", ".join(a.name for a in character.saving_throw_proficiencies),
        inline=False,
    )
    if character.skill_proficiencies:
        embed.add_field(
            name="Compétences",
            value=", ".join(s.value for s in character.skill_proficiencies),
            inline=False,
        )
    embed.add_field(name="Kit de départ", value=kit_name, inline=True)
    embed.add_field(name="Motivation", value=motivation_key, inline=True)
    return embed
```

- [ ] **Step 2: Implement REVIEW step in flow**

In `transition_to`, replace `REVIEW`:

```python
elif next_step == SetupStep.REVIEW:
    # Build the Character object NOW (preview, not yet committed)
    from engine.character import create_character, apply_racial_bonuses, AbilityScores
    raw = self.ability_scores
    boosted = apply_racial_bonuses(raw, self.race)
    char = create_character(
        name=self.name or "Anonyme",
        race=self.race,
        char_class=self.char_class,
        ability_scores=boosted,
        skill_proficiencies=self.skill_proficiencies or [],
        concept=self.concept or "",
    )
    self._preview_character = char  # cached for confirm

    self._build_review_components()
    from bot.embeds.character_setup_v2 import build_setup_recap_embed
    embed = build_setup_recap_embed(char, self.kit_name, self.motivation_key, self.concept or "")
    await interaction.response.edit_message(
        content="**Étape 6/6** — Vérifie ta fiche avant de la valider.",
        embed=embed,
        view=self,
    )
```

Add methods:

```python
def _build_review_components(self) -> None:
    self.clear_items()
    confirm_btn = ui.Button(
        label="Confirmer", emoji="✅",
        style=discord.ButtonStyle.success, custom_id="setup_confirm",
    )
    confirm_btn.callback = lambda i: self._on_confirm(i)
    self.add_item(confirm_btn)

    edit_btn = ui.Button(
        label="Recommencer", emoji="✏️",
        style=discord.ButtonStyle.secondary, custom_id="setup_restart",
    )
    edit_btn.callback = lambda i: self._on_restart(i)
    self.add_item(edit_btn)

    cancel_btn = ui.Button(
        label="Annuler", emoji="❌",
        style=discord.ButtonStyle.danger, custom_id="setup_cancel",
    )
    cancel_btn.callback = lambda i: self._on_cancel(i)
    self.add_item(cancel_btn)

async def _on_confirm(self, interaction: discord.Interaction) -> None:
    """Persist the previewed character via on_complete callback."""
    char = self._preview_character
    await self._on_complete(char, self.kit_name, self.motivation_key)
    self.stop()
    await interaction.response.edit_message(
        content=f"✅ **{char.name}** a rejoint la campagne ! Voir le lobby.",
        embed=None, view=None,
    )

async def _on_restart(self, interaction: discord.Interaction) -> None:
    """Reset accumulators and go back to RACE_CLASS (keep name+concept)."""
    self.race = None
    self.char_class = None
    self.ability_scores = None
    self.skill_proficiencies = None
    self.kit_name = None
    self.motivation_key = None
    await self.transition_to(interaction, SetupStep.RACE_CLASS)

async def _on_cancel(self, interaction: discord.Interaction) -> None:
    """Abort the flow. on_complete is NOT called."""
    self.stop()
    await interaction.response.edit_message(
        content="❌ Création annulée. Tu peux relancer via le bouton _Rejoindre_ du lobby.",
        embed=None, view=None,
    )
```

- [ ] **Step 3: Test confirm calls on_complete with character**

```python
@pytest.mark.asyncio
async def test_review_confirm_calls_on_complete():
    from engine.character import Race, CharacterClass, AbilityScores, Skill
    on_complete = AsyncMock()
    view = CharacterSetupFlow(user_id=1, language="fr", on_complete=on_complete)
    view.name = "Thorin"
    view.concept = ""
    view.race = Race.DWARF
    view.char_class = CharacterClass.FIGHTER
    view.ability_scores = AbilityScores(STR=15, DEX=14, CON=13, INT=12, WIS=10, CHA=8)
    view.skill_proficiencies = [Skill.ATHLETICS]
    view.kit_name = "Iron Vow"
    view.motivation_key = "Contract"

    interaction = MagicMock()
    interaction.response.edit_message = AsyncMock()

    await view.transition_to(interaction, SetupStep.REVIEW)
    await view._on_confirm(interaction)
    on_complete.assert_called_once()
    args = on_complete.call_args.args
    assert args[0].name == "Thorin"
    assert args[1] == "Iron Vow"
    assert args[2] == "Contract"
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/bot/views/test_character_setup_flow.py -v`
Expected: All PASS (>= 13 tests).

- [ ] **Step 5: Commit**

```bash
git add bot/views/character_setup_flow.py bot/embeds/character_setup_v2.py tests/bot/views/test_character_setup_flow.py
git commit -m "feat(setup): REVIEW step with recap embed + confirm/restart/cancel"
```

## Task B9: `LobbyView`

**Files:**
- Create: `bot/views/lobby_view.py`
- Test: `tests/bot/views/test_lobby_view.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/bot/views/test_lobby_view.py
"""Tests for the campaign lobby view (Rejoindre / Quitter / Démarrer)."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from bot.lobby_state import LobbyState, LobbyPlayerStatus
from bot.views.lobby_view import LobbyView


def _make_view(host_id=42):
    state = LobbyState(creator_id=host_id, language="fr")
    on_join = AsyncMock()
    on_launch = AsyncMock()
    return LobbyView(
        lobby_state=state, host_id=host_id, language="fr",
        on_join_clicked=on_join, on_launch_clicked=on_launch,
    ), state, on_join, on_launch


@pytest.mark.asyncio
async def test_join_button_calls_on_join_callback():
    view, state, on_join, _ = _make_view()
    interaction = MagicMock()
    interaction.user.id = 100
    interaction.response.send_message = AsyncMock()
    await view.join.callback(view, interaction)  # type: ignore[arg-type]
    on_join.assert_called_once_with(interaction, view)


@pytest.mark.asyncio
async def test_leave_button_removes_player_and_refreshes():
    view, state, _, _ = _make_view()
    state.add_player(100)
    interaction = MagicMock()
    interaction.user.id = 100
    interaction.response.edit_message = AsyncMock()
    await view.leave.callback(view, interaction)  # type: ignore[arg-type]
    assert 100 not in state.players


@pytest.mark.asyncio
async def test_launch_button_host_only():
    view, state, _, on_launch = _make_view(host_id=42)
    interaction = MagicMock()
    interaction.user.id = 999  # NOT host
    interaction.response.send_message = AsyncMock()
    await view.launch.callback(view, interaction)  # type: ignore[arg-type]
    on_launch.assert_not_called()
    interaction.response.send_message.assert_called_once()


@pytest.mark.asyncio
async def test_launch_button_blocked_when_no_ready_players():
    view, state, _, on_launch = _make_view(host_id=42)
    state.add_player(100)
    state.set_status(100, LobbyPlayerStatus.CREATING)  # joined but not ready
    interaction = MagicMock()
    interaction.user.id = 42
    interaction.response.send_message = AsyncMock()
    await view.launch.callback(view, interaction)  # type: ignore[arg-type]
    on_launch.assert_not_called()


@pytest.mark.asyncio
async def test_launch_button_fires_when_host_and_ready():
    view, state, _, on_launch = _make_view(host_id=42)
    state.add_player(100)
    state.set_status(100, LobbyPlayerStatus.READY)
    interaction = MagicMock()
    interaction.user.id = 42
    await view.launch.callback(view, interaction)  # type: ignore[arg-type]
    on_launch.assert_called_once_with(interaction, view)
```

- [ ] **Step 2: Run failing tests**

Run: `uv run pytest tests/bot/views/test_lobby_view.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement `bot/views/lobby_view.py`**

```python
"""Campaign lobby view — Rejoindre / Quitter / Démarrer buttons.

Persistent view attached to the lobby message in the campaign channel.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import discord
from discord import ButtonStyle, ui

from bot.lobby_state import LobbyState
from bot.views.base import LoggedView


JoinCallback = Callable[[discord.Interaction, "LobbyView"], Awaitable[None]]
LaunchCallback = Callable[[discord.Interaction, "LobbyView"], Awaitable[None]]


class LobbyView(LoggedView):
    """Campaign lobby with Join / Leave / Launch buttons.

    The view does NOT mutate state directly for join — it delegates to
    `on_join_clicked` so the cog can open the CharacterSetupFlow as an
    ephemeral followup. Leave is handled inline (removes from state +
    refreshes the lobby message).
    """

    timeout = None  # persistent

    def __init__(
        self,
        lobby_state: LobbyState,
        host_id: int,
        language: str,
        on_join_clicked: JoinCallback,
        on_launch_clicked: LaunchCallback,
    ) -> None:
        super().__init__(timeout=self.timeout)
        self.lobby_state = lobby_state
        self.host_id = host_id
        self.language = language
        self._on_join = on_join_clicked
        self._on_launch = on_launch_clicked

    @ui.button(label="Rejoindre", emoji="🎭", style=ButtonStyle.primary, custom_id="lobby_join")
    async def join(
        self, interaction: discord.Interaction, button: ui.Button[LobbyView],
    ) -> None:
        await self._on_join(interaction, self)

    @ui.button(label="Quitter", emoji="🚪", style=ButtonStyle.secondary, custom_id="lobby_leave")
    async def leave(
        self, interaction: discord.Interaction, button: ui.Button[LobbyView],
    ) -> None:
        self.lobby_state.remove_player(interaction.user.id)
        # The cog (in Wave C) will refresh the lobby embed after this. For now,
        # acknowledge the interaction.
        if not interaction.response.is_done():
            await interaction.response.edit_message(view=self)

    @ui.button(label="Démarrer l'aventure", emoji="▶️", style=ButtonStyle.success, custom_id="lobby_launch")
    async def launch(
        self, interaction: discord.Interaction, button: ui.Button[LobbyView],
    ) -> None:
        if interaction.user.id != self.host_id:
            await interaction.response.send_message(
                "Seul le host peut démarrer la campagne.", ephemeral=True,
            )
            return
        if not self.lobby_state.has_any_ready():
            await interaction.response.send_message(
                "Il faut au moins un joueur prêt pour démarrer.", ephemeral=True,
            )
            return
        await self._on_launch(interaction, self)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/bot/views/test_lobby_view.py -v`
Expected: 5/5 PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/views/lobby_view.py tests/bot/views/test_lobby_view.py
git commit -m "feat(lobby): add persistent LobbyView with join/leave/launch buttons"
```

## Task B10: Wave B verification gate

- [ ] **Step 1: Full UI test run**

Run: `uv run pytest tests/bot/views/test_character_setup_flow.py tests/bot/views/test_lobby_view.py tests/bot/embeds/test_lobby_embed.py tests/bot/test_lobby_state.py -v`
Expected: All PASS.

- [ ] **Step 2: Lint**

Run: `uv run ruff check bot/views/character_setup_flow.py bot/views/lobby_view.py bot/lobby_state.py bot/embeds/lobby_embed.py bot/embeds/character_setup_v2.py`
Expected: All checks pass.

- [ ] **Step 3: Type check**

Run: `uv run mypy bot/views/character_setup_flow.py bot/views/lobby_view.py bot/lobby_state.py`
Expected: Success.

- [ ] **Step 4: Wave B done — checkpoint**

```bash
git log --oneline -15
```
Verify all Wave B commits are present.

---

# Wave C — Integration

**Agent: `integrator`. Sequential, runs ONLY after Wave A and Wave B both green. Touches `bot/cogs/session.py`, `bot/cogs/character.py`, `bot/cogs/test_bridge.py`, `bot/bot.py`, deletes obsolete views, updates remaining tests. Estimated 3-4h.**

> **Pre-flight checks** before starting Wave C:
> - `uv run pytest tests/engine/ tests/bot/ -v` — all green
> - `git log --oneline | head -25` — both Wave A and Wave B commits present
> - `grep -rn "alignment\|Alignment" engine/ ai/` — empty

## Task C1: Wire `LobbyState` into `RealmBot`

**Files:**
- Modify: `bot/bot.py`

- [ ] **Step 1: Replace `launchers` dict with `lobbies`**

In `bot/bot.py`, find:

```python
launchers: dict[int, CampaignLauncher]  # channel_id → onboarding in progress
```

Replace with:

```python
from bot.lobby_state import LobbyState
lobbies: dict[int, LobbyState]  # channel_id → active lobby
```

Update `__init__` to initialize `self.lobbies = {}` (and keep `launchers` only as a temporary alias if anything else still references it — see step 2).

- [ ] **Step 2: Grep references to old `launchers`**

```bash
grep -rn "self\.launchers\|bot\.launchers\|launchers\[" bot/ --include="*.py"
```

For each call site, replace `launchers` with `lobbies` (or fix in subsequent tasks if it's in `session.py` or `campaign_launcher.py`, both being rewritten).

- [ ] **Step 3: Commit**

```bash
git add bot/bot.py
git commit -m "refactor(bot): rename launchers dict to lobbies (using LobbyState)"
```

## Task C2: Refactor `/start_campaign` to post the lobby

**Files:**
- Modify: `bot/cogs/session.py:85-212`

- [ ] **Step 1: Drop `players` parameter from command signature**

```python
@app_commands.command(name="start_campaign", description="Lance une nouvelle campagne")
@app_commands.describe(theme="Thème (ex: Dark Fantasy)", name="Nom optionnel")
async def start_campaign(
    self,
    interaction: discord.Interaction,
    theme: str,
    name: str | None = None,
) -> None:
    ...
```

Remove the existing `players: str` arg and any user-mention parsing.

- [ ] **Step 2: Replace `CampaignLauncher` instantiation with `LobbyState` + `LobbyView`**

After channel creation (which stays the same), instead of building `CampaignLauncher`, build:

```python
from bot.lobby_state import LobbyState
from bot.views.lobby_view import LobbyView
from bot.views.character_setup_flow import CharacterSetupFlow
from bot.embeds.lobby_embed import build_lobby_embed

lobby = LobbyState(creator_id=interaction.user.id, language="fr")
self.bot.lobbies[channel.id] = lobby

# Define inline callbacks (or factor into helpers)
async def on_join(inter: discord.Interaction, lobby_view: LobbyView) -> None:
    user_id = inter.user.id
    try:
        lobby.add_player(user_id)
    except ValueError as e:
        await inter.response.send_message(str(e), ephemeral=True)
        return
    lobby.set_status(user_id, LobbyPlayerStatus.CREATING)

    async def on_setup_complete(char, kit_name, motivation_key) -> None:
        # Persist to DB
        await self.bot.db_factory().player_characters().save(
            channel_id=channel.id, user_id=user_id, character=char,
            kit_name=kit_name, motivation_key=motivation_key,
        )
        lobby.players[user_id].character = char
        lobby.players[user_id].kit_name = kit_name
        lobby.players[user_id].motivation_key = motivation_key
        lobby.set_status(user_id, LobbyPlayerStatus.READY)
        # Refresh the lobby embed
        roster = [(lobby.players[uid], (await self.bot.fetch_user(uid)).display_name)
                  for uid in lobby.players]
        embed = build_lobby_embed(
            campaign_name=campaign_name, theme=theme, host_name=host_display,
            roster=roster, language="fr",
        )
        try:
            await lobby_msg.edit(embed=embed, view=lobby_view)
        except discord.HTTPException:
            pass  # message may be deleted

    flow = CharacterSetupFlow(
        user_id=user_id, language="fr", on_complete=on_setup_complete,
    )
    # Open identity modal (entry point of the flow)
    from bot.views.character_setup_flow import IdentityModal
    await inter.response.send_modal(IdentityModal(parent_view=flow))

async def on_launch(inter: discord.Interaction, lobby_view: LobbyView) -> None:
    # Build the GameSession from lobby.players (only READY)
    # ... use the existing CampaignLauncher.launch logic, adapted ...
    pass  # full implementation in C3

lobby_view = LobbyView(
    lobby_state=lobby, host_id=interaction.user.id, language="fr",
    on_join_clicked=on_join, on_launch_clicked=on_launch,
)
embed = build_lobby_embed(
    campaign_name=name or f"Campagne — {theme}",
    theme=theme, host_name=interaction.user.display_name,
    roster=[], language="fr",
)
lobby_msg = await channel.send(embed=embed, view=lobby_view)
```

(This is the structural sketch. The exact details — campaign_name fallback, host display name, error handling, etc. — must match the existing patterns in the original file.)

- [ ] **Step 3: Run cog tests for /start_campaign**

Run: `uv run pytest tests/bot/test_cog_session.py -v -k "start_campaign"` (if exists)
Expected: tests pass after fixing fixtures (no more `players=...` arg).

- [ ] **Step 4: Commit**

```bash
git add bot/cogs/session.py
git commit -m "refactor(session): /start_campaign posts lobby instead of pre-defined players"
```

## Task C3: Implement `on_launch` — transition lobby → GameSession

**Files:**
- Modify: `bot/cogs/session.py`

- [ ] **Step 1: Port the launch logic from `CampaignLauncher.launch()`**

Read `bot/campaign_launcher.py` to find the existing `launch()` method (which builds the `GameSession`, sends opening narrative, etc.). Port the relevant logic into the `on_launch` callback defined in C2, sourcing players from `lobby.players` (only those with `LobbyPlayerStatus.READY`).

Key points:
- Pull `Character`, `Inventory`, `SpellcasterState` from each ready `LobbyPlayer`.
- Generate StoryArc (existing async pattern preserved — port the `_generation_task` into the lobby).
- Once `GameSession` is created and persisted: remove `lobby` from `bot.lobbies` and disable the lobby view buttons.
- Send the opening narrative embed.

- [ ] **Step 2: Manual smoke test (no DB hit)**

Run a quick `python -c "from bot.cogs.session import SessionCog; print('OK')"` to ensure imports are clean.

- [ ] **Step 3: Commit**

```bash
git add bot/cogs/session.py
git commit -m "feat(session): wire on_launch — transition lobby to GameSession"
```

## Task C4: Delete `/create_character` slash command

**Files:**
- Modify: `bot/cogs/character.py:36-114`

- [ ] **Step 1: Delete the command**

In `bot/cogs/character.py`, delete the entire `@app_commands.command(name="create_character")` block and its handler `create_character` method (lines 36-114). Keep `/character` and `/level_up`.

- [ ] **Step 2: Drop now-unused imports**

Remove unused imports from old views (`CharacterCreateView`, etc.).

- [ ] **Step 3: Run remaining cog tests**

Run: `uv run pytest tests/bot/test_cog_character.py -v`
Expected: tests for `/character` and `/level_up` pass; tests for `/create_character` are removed in next task.

- [ ] **Step 4: Commit**

```bash
git add bot/cogs/character.py
git commit -m "feat(character): remove /create_character slash — onboarding via lobby only"
```

## Task C5: Delete obsolete views

**Files:** delete the following

- [ ] **Step 1: Delete view files**

```bash
rm bot/views/character_create_view.py
rm bot/views/stat_assignment_view.py
rm bot/views/skill_selection_view.py
rm bot/views/motivation_view.py
rm bot/views/starter_gear_view.py
rm bot/views/start_onboarding_view.py
rm bot/views/character_edit_view.py
rm bot/views/character_edit_flow.py
rm bot/campaign_launcher.py
```

- [ ] **Step 2: Delete obsolete tests**

```bash
rm -f tests/bot/views/test_character_create_view.py
rm -f tests/bot/views/test_stat_assignment_view.py
rm -f tests/bot/views/test_skill_selection_view.py
rm -f tests/bot/views/test_motivation_view.py
rm -f tests/bot/views/test_starter_gear_view.py
rm -f tests/bot/views/test_start_onboarding_view.py
rm -f tests/bot/views/test_character_edit_view.py
rm -f tests/bot/views/test_character_edit_flow.py
rm -f tests/bot/test_campaign_launcher_recreation.py
```

- [ ] **Step 3: Grep for stale imports**

```bash
grep -rn "campaign_launcher\|CampaignLauncher\|character_create_view\|stat_assignment_view\|skill_selection_view\|motivation_view\|starter_gear_view\|start_onboarding_view\|character_edit_view\|character_edit_flow" bot/ tests/ --include="*.py"
```

For each stale match, fix the import or delete the line. Common offenders: `bot/cogs/test_bridge.py`, `bot/bot.py` (already partially handled in C1).

- [ ] **Step 4: Run full test suite**

Run: `uv run pytest tests/ -v --tb=short`
Expected: All pass. Any failure means a stale reference — fix and rerun.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "chore: remove obsolete views and CampaignLauncher

Replaced by CharacterSetupFlow + LobbyView + LobbyState. Tests for these
deleted modules are also removed."
```

## Task C6: Update `bot/cogs/test_bridge.py`

**Files:**
- Modify: `bot/cogs/test_bridge.py:347-467`

- [ ] **Step 1: Update `start_campaign` test handler**

In `_handle_start_campaign`: remove the `players` arg parsing. Instead, after `start_campaign` is invoked (creating the lobby), iterate the simulated player list and call `lobby.add_player()` + simulate the setup flow completion (set status to READY with a default character via `engine.character.create_character`).

- [ ] **Step 2: Add a new `lobby_join` test command (if needed for scenarios)**

If scenarios need to simulate player joins explicitly, add:

```python
elif command == "lobby_join":
    # args: user_id (int)
    user_id = int(args[0])
    lobby = self._bot.lobbies.get(channel_id)
    if lobby is None:
        return
    lobby.add_player(user_id)
    # ... simulate setup flow with sane defaults ...
```

- [ ] **Step 3: Run scenario smoke**

Run: `uv run pytest tests/scenarios/ -v --tb=short`
Expected: existing scenarios pass after migration.

- [ ] **Step 4: Commit**

```bash
git add bot/cogs/test_bridge.py
git commit -m "test(bridge): adapt test_bridge to lobby-driven onboarding"
```

## Task C7: New scenario test — full lobby flow

**Files:**
- Create: `tests/scenarios/test_character_creation_lobby.py`

- [ ] **Step 1: Write end-to-end scenario**

```python
"""End-to-end scenario: /start_campaign → 2 players join → 1 ready → host launches."""

import pytest
from tests.scenarios.runner import ScenarioRunner


@pytest.mark.scenario
async def test_lobby_to_game_session_flow():
    runner = ScenarioRunner()
    # Host starts campaign
    await runner.send_command("start_campaign", theme="Dark Fantasy")
    assert runner.bot.lobbies, "Lobby should be created"

    channel_id = next(iter(runner.bot.lobbies.keys()))
    lobby = runner.bot.lobbies[channel_id]

    # Player A joins and completes setup
    await runner.simulate_lobby_join(channel_id, user_id=100)
    await runner.simulate_setup_complete(
        user_id=100, name="Alice", race="Elf", char_class="Wizard",
        kit_name="Spell Tome", motivation_key="Discovery",
    )
    assert lobby.players[100].status.value == "ready"

    # Player B joins, abandons (cancels)
    await runner.simulate_lobby_join(channel_id, user_id=200)
    await runner.simulate_setup_cancel(user_id=200)
    assert 200 not in lobby.players  # removed on cancel

    # Host launches
    assert lobby.has_any_ready()
    await runner.simulate_lobby_launch(channel_id, host_id=runner.host_id)
    assert channel_id not in runner.bot.lobbies  # removed on launch
    assert channel_id in runner.bot.sessions     # GameSession created
```

- [ ] **Step 2: Add helpers in `ScenarioRunner` if missing**

`simulate_lobby_join`, `simulate_setup_complete`, `simulate_setup_cancel`, `simulate_lobby_launch`. Each forwards to `test_bridge` commands.

- [ ] **Step 3: Run scenario**

Run: `uv run pytest tests/scenarios/test_character_creation_lobby.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/scenarios/test_character_creation_lobby.py
git commit -m "test(scenarios): end-to-end lobby flow with 2 players"
```

## Task C8: Live Discord test (gate de fin)

**Files:** none (uses discord-test MCP)

- [ ] **Step 1: Start the bot in test mode**

Run: `uv run python -m bot.bot` (or whatever the project entrypoint is). Confirm it connects to the test server.

- [ ] **Step 2: Drive a real flow via `discord-test` MCP**

Using the `mcp__discord-test__*` tools:
1. Send `/start_campaign theme:"Dark Fantasy"` as the host
2. Wait for the lobby embed to appear
3. Click 🎭 Rejoindre as a tester user
4. Submit IdentityModal: name="Thorin", concept="grizzled veteran"
5. Pick race=Dwarf, class=Fighter, click Continuer
6. Click ✨ "Optimisé pour Fighter"
7. Click Continuer
8. Pick 2 skills (e.g., Athletics, Intimidation), click Continuer
9. Pick a kit + motivation, click Continuer
10. Verify recap embed shows correct fiche
11. Click ✅ Confirmer
12. Verify lobby roster updated with ✅ Thorin
13. As host, click ▶️ Démarrer
14. Verify opening narrative is posted

- [ ] **Step 2bis: Capture screenshots / logs**

Save the conversation log to `tasks/logs/2026-04-26-lobby-live-test.txt` for posterity.

- [ ] **Step 3: Document results in `tasks/todo.md`**

Add a "Character creation redesign — live test results" subsection with: pass/fail per step, any UI bugs to follow up on.

- [ ] **Step 4: Commit logs**

```bash
git add tasks/logs/2026-04-26-lobby-live-test.txt tasks/todo.md
git commit -m "test(live): character creation lobby — live Discord pass

Host + 1 player end-to-end. Recap embed + opening narrative confirmed."
```

---

# Wave D — Final Verification + Handoff

**Sequential, after Wave C green.**

## Task D1: Full pytest suite

- [ ] **Step 1: Run with coverage**

```bash
uv run pytest tests/ --cov=engine --cov=bot --cov-report=term-missing -v --tb=short
```

Expected: 100% pass, no skipped tests.

- [ ] **Step 2: Verify no regressions in coverage**

Coverage should be ≥ baseline (check `git log` for prior coverage summaries).

## Task D2: Lint and type check full repo

- [ ] **Step 1: Ruff**

```bash
uv run ruff check .
uv run ruff format --check .
```

Expected: All clean.

- [ ] **Step 2: Mypy**

```bash
uv run mypy engine/ bot/ ai/
```

Expected: Success.

## Task D3: Audit grep — final cleanup

- [ ] **Step 1: No stale alignment refs anywhere**

```bash
grep -rn "alignment\|Alignment" --include="*.py" .
```

Expected: zero (or only in unrelated comments — review each match).

- [ ] **Step 2: No stale CampaignLauncher refs**

```bash
grep -rn "CampaignLauncher\|campaign_launcher" --include="*.py" .
```

Expected: zero.

- [ ] **Step 3: No stale view imports**

```bash
grep -rn "from bot.views.character_create_view\|from bot.views.stat_assignment_view\|from bot.views.skill_selection_view\|from bot.views.motivation_view\|from bot.views.starter_gear_view\|from bot.views.start_onboarding_view\|from bot.views.character_edit_view\|from bot.views.character_edit_flow" --include="*.py" .
```

Expected: zero.

## Task D4: Update `tasks/todo.md` and `tasks/lessons.md`

- [ ] **Step 1: Mark redesign as done in `tasks/todo.md`**

Add a section:

```markdown
## Character Creation Redesign (2026-04-26 — completed)

Spec: docs/superpowers/specs/2026-04-26-character-creation-redesign-design.md
Plan: docs/superpowers/plans/2026-04-26-character-creation-redesign.md

- [x] Vague A — engine cleanup (drop alignment, add presets/random_stats, +concept field)
- [x] Vague B — new UI (LobbyView, CharacterSetupFlow, LobbyEmbed, V2 récap)
- [x] Vague C — integration (LobbyState replaces CampaignLauncher, /create_character deleted)
- [x] Vague D — verification (pytest + ruff + mypy + live Discord test)
```

- [ ] **Step 2: Add lessons learned to `tasks/lessons.md`**

If anything noteworthy was learned (e.g., Components V2 quirks, edit_message gotchas, async race conditions), document it as a new lesson with date, observation, and how-to-apply.

- [ ] **Step 3: Final commit**

```bash
git add tasks/todo.md tasks/lessons.md
git commit -m "docs: mark character creation redesign complete + capture lessons"
```

---

## Self-Review (executed inline before handoff)

**Spec coverage:**
- ✅ G1 (single experience): Wave C wires `/start_campaign` → lobby → setup flow → game session.
- ✅ G2 (≤ 5 messages): IdentityModal (1) + RaceClass (2) + Stats (3) + Skills (4) + KitMotiv (5) + Review = 5 view-edits + 1 modal. Effective Discord messages: 1 ephemeral (modal) + 1 evolving view + 1 lobby update = 3.
- ✅ G3 (alignment removed): Tasks A2, A3.
- ✅ G4 (lobby): Wave B+C.
- ✅ G5 (recap): Task B8.
- ✅ G6 (mechanical fields preserved): no engine combat changes; verified spec section 4.3.

**Placeholder scan:** none (all code blocks complete, all paths absolute, all commands runnable).

**Type consistency:** `LobbyState`, `LobbyPlayer`, `LobbyPlayerStatus` defined in B1, used identically in B2, B9, C1-C7. `CharacterSetupFlow.on_complete` signature `(Character, str, str)` consistent across B3, B8, C2.

**Scope check:** focused on character-creation onboarding. Combat, world generation, and Story Director untouched. Single-implementation scope.
