# Autonomous Playthrough Simulator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a headless autonomous simulator that plays a full RealmAI campaign on its own through the real Ollama LLM pipeline, then emits a deterministic incoherence report.

**Architecture:** Four-component pipeline (`SimulationRunner` → `AutonomousAgent` / `GameDriver` / `IncoherenceChecker` / `Recorder`) layered on top of an extended `ScenarioRunner` with AI enabled. The 4b (`qwen3.5:4b`) drives the agent; the bot's existing 4b + 9b pipeline handles interpretation + narration; deterministic heuristics catch incoherences against the engine state.

**Tech Stack:** Python 3.12, Pydantic v2, pytest + pytest-asyncio + pytest-httpx, existing `ai.client.OllamaClient`, existing `tests.scenarios.ScenarioRunner` (extended), `discord.py` (only via existing cogs).

**Reference spec:** [2026-05-25-autonomous-playthrough-simulator-design.md](../specs/2026-05-25-autonomous-playthrough-simulator-design.md)

---

## File Structure

**New files:**

```
tests/simulation/
├── __init__.py
├── __main__.py              # CLI entry (argparse)
├── runner.py                # SimulationRunner — orchestrator
├── agent.py                 # AutonomousAgent — 4b-driven action chooser
├── driver.py                # GameDriver — wraps ScenarioRunner w/ AI on
├── checker.py               # IncoherenceChecker — rule aggregator
├── recorder.py              # Recorder — JSONL + markdown writer
├── records.py               # Pydantic models
├── conftest.py              # pytest fixtures for the simulator tests
├── rules/
│   ├── __init__.py          # exposes ALL_RULES list
│   ├── hard.py              # R1.* rules
│   ├── soft.py              # R2.* rules
│   └── drift.py             # R3.* rules
├── prompts/
│   ├── agent_system.txt     # system prompt for the agent
│   └── few_shots.json       # example intents per context
├── false_positives.yml      # whitelist of known-noisy patterns (empty initially)
└── tests/
    ├── __init__.py
    ├── test_records.py
    ├── test_rules_hard.py
    ├── test_rules_soft.py
    ├── test_rules_drift.py
    ├── test_checker.py
    ├── test_agent.py
    ├── test_driver.py
    ├── test_recorder.py
    └── test_runner_e2e_mocked_llm.py
```

**Modified files:**

- `tests/scenarios/scenario_runner.py` — add `ai_enabled: bool = False` constructor flag and wire real `Interpreter` / `Narrator` / `StoryDirector` on the created `GameSession` when set.
- `ai/client.py` — add `simulation_mode: bool = False` flag to `OllamaClient` that forces `temperature=0.0` on every `chat_json` call.
- `.gitignore` — append `tests/simulation/runs/`.

---

## Task 1: Scaffolding & .gitignore

**Files:**
- Create: `tests/simulation/__init__.py`
- Create: `tests/simulation/rules/__init__.py`
- Create: `tests/simulation/prompts/.gitkeep`
- Create: `tests/simulation/tests/__init__.py`
- Create: `tests/simulation/false_positives.yml`
- Modify: `.gitignore` (append one line)

- [ ] **Step 1: Create the package directories and empty inits**

```bash
mkdir -p tests/simulation/rules tests/simulation/prompts tests/simulation/tests
touch tests/simulation/__init__.py
touch tests/simulation/rules/__init__.py
touch tests/simulation/tests/__init__.py
touch tests/simulation/prompts/.gitkeep
```

- [ ] **Step 2: Initialize the empty false-positives whitelist**

Write `tests/simulation/false_positives.yml`:

```yaml
# Known-noisy patterns that produce false positives in the IncoherenceChecker.
# Each entry is a {rule_id, pattern, reason} tuple.
# Edit this file as runs reveal patterns that should be ignored.
patterns: []
```

- [ ] **Step 3: Add the runs/ output dir to .gitignore**

Append to `.gitignore`:

```
# Simulation run artifacts (transcripts, reports, per-run DBs)
tests/simulation/runs/
```

- [ ] **Step 4: Verify the scaffold doesn't break anything**

Run: `uv run pytest tests/ --collect-only -q 2>&1 | tail -5`
Expected: pytest collects tests successfully, no import errors, exit code 0.

- [ ] **Step 5: Commit**

```bash
git add tests/simulation/__init__.py tests/simulation/rules/__init__.py tests/simulation/tests/__init__.py tests/simulation/prompts/.gitkeep tests/simulation/false_positives.yml .gitignore
git commit -m "feat(sim): scaffold tests/simulation package"
```

---

## Task 2: Pydantic records

**Files:**
- Create: `tests/simulation/records.py`
- Create: `tests/simulation/tests/test_records.py`

- [ ] **Step 1: Write the failing test**

Write `tests/simulation/tests/test_records.py`:

```python
"""Tests for tests/simulation/records.py — Pydantic models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tests.simulation.records import (
    AgentIntent,
    IncoherenceAlert,
    LLMTimings,
    TurnOutcome,
    TurnRecord,
)


class TestAgentIntent:
    def test_attack_with_target(self) -> None:
        intent = AgentIntent(
            reasoning="goblin is bloodied, finishing it",
            action="attack",
            args={"target": "Goblin_2"},
        )
        assert intent.action == "attack"
        assert intent.args["target"] == "Goblin_2"
        assert intent.raw_text is None

    def test_free_form_requires_raw_text(self) -> None:
        with pytest.raises(ValidationError, match="raw_text"):
            AgentIntent(reasoning="x", action="free_form", args={})

    def test_unknown_action_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AgentIntent(reasoning="x", action="dance", args={})

    def test_reasoning_max_length(self) -> None:
        with pytest.raises(ValidationError):
            AgentIntent(reasoning="x" * 201, action="look", args={})


class TestIncoherenceAlert:
    def test_construct(self) -> None:
        alert = IncoherenceAlert(
            severity="hard",
            category="dead_npc_speaks",
            turn=12,
            rule="R1.npc_status",
            narration_snippet="Garm sourit.",
            expected="Garm marked dead at turn 8",
        )
        assert alert.severity == "hard"
        assert alert.turn == 12

    def test_severity_enum(self) -> None:
        with pytest.raises(ValidationError):
            IncoherenceAlert(
                severity="critical",  # not in enum
                category="x",
                turn=1,
                rule="r",
                narration_snippet="s",
                expected="e",
            )


class TestTurnRecord:
    def test_full_record(self) -> None:
        record = TurnRecord(
            turn=1,
            ts="2026-05-25T16:42:01Z",
            observation="TURN 1\nYou play: Aria",
            intent=AgentIntent(reasoning="look", action="look", args={}),
            outcome=TurnOutcome(
                narration="Vous voyez une grotte.",
                action_resolved={"type": "look"},
                error=None,
                timing_ms=LLMTimings(agent=100, interpreter=200, engine=5, narrator=1500),
            ),
            diff={},
            alerts=[],
            agent_retries=0,
        )
        assert record.turn == 1
        assert record.outcome.error is None

    def test_serializes_to_jsonl_line(self) -> None:
        record = TurnRecord(
            turn=1,
            ts="2026-05-25T16:42:01Z",
            observation="o",
            intent=AgentIntent(reasoning="r", action="look", args={}),
            outcome=TurnOutcome(
                narration="n",
                action_resolved={},
                error=None,
                timing_ms=LLMTimings(agent=1, interpreter=2, engine=3, narrator=4),
            ),
            diff={},
            alerts=[],
            agent_retries=0,
        )
        line = record.model_dump_json()
        assert '"turn":1' in line
        assert "\n" not in line
```

- [ ] **Step 2: Verify the test fails**

Run: `uv run pytest tests/simulation/tests/test_records.py -v`
Expected: `ImportError: cannot import name 'AgentIntent' from 'tests.simulation.records'`

- [ ] **Step 3: Implement records.py**

Write `tests/simulation/records.py`:

```python
"""Pydantic models exchanged between simulator components."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class AgentIntent(BaseModel):
    """A single action chosen by the AutonomousAgent for a turn."""

    reasoning: str = Field(..., max_length=200)
    action: Literal[
        "attack",
        "cast_spell",
        "defend",
        "flee",
        "move",
        "look",
        "talk",
        "search",
        "equip",
        "unequip",
        "use_item",
        "free_form",
        "wait",
    ]
    args: dict[str, str] = Field(default_factory=dict)
    raw_text: str | None = None

    @model_validator(mode="after")
    def _free_form_requires_raw_text(self) -> "AgentIntent":
        if self.action == "free_form" and not self.raw_text:
            raise ValueError("raw_text is required when action == 'free_form'")
        return self


class LLMTimings(BaseModel):
    """Per-phase latency in milliseconds for a single turn."""

    agent: int
    interpreter: int
    engine: int
    narrator: int


class TurnOutcome(BaseModel):
    """What happened when the AgentIntent was executed."""

    narration: str
    action_resolved: dict[str, Any]
    error: str | None
    timing_ms: LLMTimings


class IncoherenceAlert(BaseModel):
    """An incoherence detected by the IncoherenceChecker."""

    severity: Literal["hard", "soft", "drift"]
    category: str
    turn: int
    rule: str
    narration_snippet: str = Field(..., max_length=200)
    expected: str
    source: Literal["heuristic", "story_director"] = "heuristic"


class TurnRecord(BaseModel):
    """The full record persisted to transcript.jsonl per turn."""

    turn: int
    ts: str
    observation: str
    intent: AgentIntent
    outcome: TurnOutcome
    diff: dict[str, list[Any]]  # {path: [old, new]}
    alerts: list[IncoherenceAlert]
    agent_retries: int = 0
```

- [ ] **Step 4: Run the tests until they pass**

Run: `uv run pytest tests/simulation/tests/test_records.py -v`
Expected: all 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/simulation/records.py tests/simulation/tests/test_records.py
git commit -m "feat(sim): Pydantic models for intent, alert, turn record"
```

---

## Task 3: Rule R1.npc_status (dead NPC speaks)

**Files:**
- Create: `tests/simulation/rules/hard.py`
- Create: `tests/simulation/tests/test_rules_hard.py`

- [ ] **Step 1: Write the failing test**

Write `tests/simulation/tests/test_rules_hard.py` (initial version with only R1.npc_status):

```python
"""Tests for tests/simulation/rules/hard.py — R1.* deterministic checks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tests.simulation.rules.hard import check_npc_status


@dataclass
class FakeNPC:
    name: str
    status: str = "alive"
    hp: int = 10


@dataclass
class FakeState:
    """Minimal stand-in for the bits of GameSession the rules need."""

    npcs: dict[str, FakeNPC] = field(default_factory=dict)
    current_location: Any = None
    combat_active: bool = False
    combat_state: Any = None
    inventory: Any = None
    player_hp_ratio: float = 1.0
    player_max_hp: int = 15
    player_hp: int = 15


class TestR1NpcStatus:
    def test_dead_npc_speaks_triggers(self) -> None:
        state = FakeState(npcs={"Garm": FakeNPC(name="Garm", status="dead", hp=0)})
        narration = "Garm sourit et tend la main vers le héros."
        alerts = check_npc_status(narration, state, diff={}, history=[])
        assert len(alerts) == 1
        a = alerts[0]
        assert a.rule == "R1.npc_status"
        assert a.severity == "hard"
        assert "Garm" in a.narration_snippet

    def test_alive_npc_speaks_does_not_trigger(self) -> None:
        state = FakeState(npcs={"Garm": FakeNPC(name="Garm", status="alive", hp=10)})
        narration = "Garm sourit et tend la main vers le héros."
        alerts = check_npc_status(narration, state, diff={}, history=[])
        assert alerts == []

    def test_dead_npc_not_mentioned_does_not_trigger(self) -> None:
        state = FakeState(npcs={"Garm": FakeNPC(name="Garm", status="dead", hp=0)})
        narration = "Le vent souffle dans les arbres."
        alerts = check_npc_status(narration, state, diff={}, history=[])
        assert alerts == []

    def test_dead_npc_mentioned_but_passive_no_trigger(self) -> None:
        state = FakeState(npcs={"Garm": FakeNPC(name="Garm", status="dead", hp=0)})
        narration = "Le corps de Garm gît au sol, sans vie."
        alerts = check_npc_status(narration, state, diff={}, history=[])
        assert alerts == []

    def test_hp_zero_treated_as_dead(self) -> None:
        state = FakeState(npcs={"Garm": FakeNPC(name="Garm", status="alive", hp=0)})
        narration = "Garm attaque !"
        alerts = check_npc_status(narration, state, diff={}, history=[])
        assert len(alerts) == 1
```

- [ ] **Step 2: Verify the test fails**

Run: `uv run pytest tests/simulation/tests/test_rules_hard.py -v`
Expected: `ImportError: cannot import name 'check_npc_status'`.

- [ ] **Step 3: Implement R1.npc_status**

Write `tests/simulation/rules/hard.py` (initial version):

```python
"""Hard incoherence rules (R1.*) — direct contradiction with engine state."""

from __future__ import annotations

import re
from typing import Any

from tests.simulation.records import IncoherenceAlert

# Active-verb patterns (French) that suggest the NPC is acting/speaking.
_NPC_ACTIVE_PATTERN = re.compile(
    r"\b(parle|dit|s'?ad?dresse|attaque|s'avance|sourit|hoche|crie|murmure|"
    r"r[ée]pond|demande|propose|tend|frappe|lance)\b",
    re.IGNORECASE,
)


def _snippet_around(text: str, needle: str, radius: int = 80) -> str:
    """Return up to 200 chars around the first occurrence of needle."""
    idx = text.lower().find(needle.lower())
    if idx < 0:
        return text[:200]
    start = max(0, idx - radius)
    end = min(len(text), idx + len(needle) + radius)
    snippet = text[start:end].strip()
    return snippet[:200]


def check_npc_status(
    narration: str,
    state: Any,
    diff: dict[str, list[Any]],
    history: list[Any],
) -> list[IncoherenceAlert]:
    """R1.npc_status — a dead NPC speaks or acts in the narration."""
    alerts: list[IncoherenceAlert] = []
    for npc in state.npcs.values():
        is_dead = npc.status == "dead" or npc.hp <= 0
        if not is_dead:
            continue
        # NPC name must appear AND an active verb must appear nearby in the same sentence.
        if npc.name.lower() not in narration.lower():
            continue
        # Check each sentence containing the NPC name for an active verb.
        for sentence in re.split(r"[.!?]", narration):
            if npc.name.lower() not in sentence.lower():
                continue
            if _NPC_ACTIVE_PATTERN.search(sentence):
                alerts.append(
                    IncoherenceAlert(
                        severity="hard",
                        category="dead_npc_speaks",
                        turn=getattr(state, "current_turn", 0),
                        rule="R1.npc_status",
                        narration_snippet=_snippet_around(narration, npc.name),
                        expected=f"{npc.name} is dead (status={npc.status}, hp={npc.hp})",
                    )
                )
                break
    return alerts
```

- [ ] **Step 4: Run the tests until they pass**

Run: `uv run pytest tests/simulation/tests/test_rules_hard.py -v`
Expected: all 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/simulation/rules/hard.py tests/simulation/tests/test_rules_hard.py
git commit -m "feat(sim): R1.npc_status — dead NPC speaks detection"
```

---

## Task 4: Rule R1.phantom_npc

**Files:**
- Modify: `tests/simulation/rules/hard.py`
- Modify: `tests/simulation/tests/test_rules_hard.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/simulation/tests/test_rules_hard.py`:

```python
from tests.simulation.rules.hard import check_phantom_npc


class TestR1PhantomNpc:
    def test_unknown_proper_noun_triggers(self) -> None:
        state = FakeState(npcs={"Garm": FakeNPC(name="Garm")})
        narration = "Le héros rencontre Khaalim, un sorcier inconnu."
        alerts = check_phantom_npc(narration, state, diff={}, history=[])
        assert any(a.rule == "R1.phantom_npc" and "Khaalim" in a.expected for a in alerts)

    def test_known_npc_does_not_trigger(self) -> None:
        state = FakeState(npcs={"Garm": FakeNPC(name="Garm")})
        narration = "Garm s'avance."
        alerts = check_phantom_npc(narration, state, diff={}, history=[])
        assert alerts == []

    def test_whitelist_words_ignored(self) -> None:
        # "Dieu", "Roi" etc. are common nouns capitalized; must not trigger
        state = FakeState(npcs={})
        narration = "Le Roi a parlé. Que les Dieux nous protègent."
        alerts = check_phantom_npc(narration, state, diff={}, history=[])
        assert alerts == []

    def test_player_name_not_phantom(self) -> None:
        state = FakeState(npcs={})
        narration = "Aria avance prudemment."
        alerts = check_phantom_npc(
            narration, state, diff={}, history=[{"player_name": "Aria"}]
        )
        assert alerts == []
```

Also extend `FakeState` to support player_names — replace the existing FakeState block at the top of the file:

```python
@dataclass
class FakeState:
    npcs: dict[str, FakeNPC] = field(default_factory=dict)
    current_location: Any = None
    combat_active: bool = False
    combat_state: Any = None
    inventory: Any = None
    player_names: list[str] = field(default_factory=list)
    player_hp_ratio: float = 1.0
    player_max_hp: int = 15
    player_hp: int = 15
    current_turn: int = 0
```

And adapt `TestR1PhantomNpc.test_player_name_not_phantom` to use `state.player_names = ["Aria"]` instead of history:

```python
    def test_player_name_not_phantom(self) -> None:
        state = FakeState(npcs={}, player_names=["Aria"])
        narration = "Aria avance prudemment."
        alerts = check_phantom_npc(narration, state, diff={}, history=[])
        assert alerts == []
```

- [ ] **Step 2: Verify the new tests fail**

Run: `uv run pytest tests/simulation/tests/test_rules_hard.py::TestR1PhantomNpc -v`
Expected: `ImportError: cannot import name 'check_phantom_npc'`.

- [ ] **Step 3: Implement R1.phantom_npc**

Append to `tests/simulation/rules/hard.py`:

```python
# Common French capitalized nouns that are NOT proper names (whitelist).
_PROPER_NOUN_WHITELIST: frozenset[str] = frozenset({
    "Le", "La", "Les", "L", "Un", "Une", "Des", "Du", "De", "Dans", "Sur",
    "Avec", "Sans", "Pour", "Par", "Vers", "Chez", "Vous", "Nous", "Il",
    "Elle", "Ils", "Elles", "Je", "Tu", "On", "Que", "Qui", "Quoi",
    "Dieu", "Dieux", "Roi", "Reine", "Capitaine", "Seigneur", "Dame",
    "Maître", "Madame", "Monsieur", "Père", "Mère", "Frère", "Sœur",
    "Or", "Mais", "Et", "Donc", "Car", "Aussi", "Si", "Alors", "Puis",
    "Tout", "Tous", "Toute", "Toutes", "Cette", "Ce", "Ces", "Ses",
    "Son", "Sa", "Leur", "Leurs", "Mon", "Ma", "Mes", "Notre", "Votre",
})

_PROPER_NOUN_RE = re.compile(r"\b([A-ZÉÈÊÀÂÔÛÎ][a-zéèêàâôûîç']{2,})\b")


def check_phantom_npc(
    narration: str,
    state: Any,
    diff: dict[str, list[Any]],
    history: list[Any],
) -> list[IncoherenceAlert]:
    """R1.phantom_npc — capitalized proper noun absent from NPC registry."""
    alerts: list[IncoherenceAlert] = []
    known_npcs = {n.lower() for n in state.npcs}
    known_players = {p.lower() for p in getattr(state, "player_names", [])}
    seen: set[str] = set()
    for match in _PROPER_NOUN_RE.finditer(narration):
        word = match.group(1)
        if word in _PROPER_NOUN_WHITELIST:
            continue
        # Sentence-start "Le", "Un", etc. would already be excluded by whitelist;
        # additionally skip words that appear in lowercase elsewhere (common nouns).
        lower = word.lower()
        if lower in known_npcs or lower in known_players:
            continue
        if lower in seen:
            continue
        seen.add(lower)
        alerts.append(
            IncoherenceAlert(
                severity="hard",
                category="phantom_npc",
                turn=getattr(state, "current_turn", 0),
                rule="R1.phantom_npc",
                narration_snippet=_snippet_around(narration, word),
                expected=f"Proper noun '{word}' is not in NPC registry or player names",
            )
        )
    return alerts
```

- [ ] **Step 4: Run the tests until they pass**

Run: `uv run pytest tests/simulation/tests/test_rules_hard.py -v`
Expected: all 9 tests pass (5 from R1.npc_status + 4 from R1.phantom_npc).

- [ ] **Step 5: Commit**

```bash
git add tests/simulation/rules/hard.py tests/simulation/tests/test_rules_hard.py
git commit -m "feat(sim): R1.phantom_npc — unknown proper noun detection"
```

---

## Task 5: Rule R1.item_use_without_owning

**Files:**
- Modify: `tests/simulation/rules/hard.py`
- Modify: `tests/simulation/tests/test_rules_hard.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/simulation/tests/test_rules_hard.py`:

```python
from tests.simulation.rules.hard import check_item_use_without_owning


@dataclass
class FakeInventory:
    items: list[str] = field(default_factory=list)


class TestR1ItemUseWithoutOwning:
    def test_uses_item_not_in_inventory_triggers(self) -> None:
        state = FakeState(inventory=FakeInventory(items=["Épée longue"]))
        narration = "Le héros boit la Potion de soin."
        alerts = check_item_use_without_owning(narration, state, diff={}, history=[])
        assert len(alerts) == 1
        assert alerts[0].rule == "R1.item_use_without_owning"
        assert "Potion de soin" in alerts[0].expected

    def test_uses_item_in_inventory_no_trigger(self) -> None:
        state = FakeState(inventory=FakeInventory(items=["Potion de soin"]))
        narration = "Le héros boit la Potion de soin."
        alerts = check_item_use_without_owning(narration, state, diff={}, history=[])
        assert alerts == []

    def test_no_inventory_no_trigger(self) -> None:
        state = FakeState(inventory=None)
        narration = "Le héros boit la Potion de soin."
        alerts = check_item_use_without_owning(narration, state, diff={}, history=[])
        assert alerts == []

    def test_passive_mention_no_trigger(self) -> None:
        state = FakeState(inventory=FakeInventory(items=["Épée longue"]))
        narration = "Une potion de soin trône sur l'étagère."
        alerts = check_item_use_without_owning(narration, state, diff={}, history=[])
        assert alerts == []
```

- [ ] **Step 2: Verify the test fails**

Run: `uv run pytest tests/simulation/tests/test_rules_hard.py::TestR1ItemUseWithoutOwning -v`
Expected: ImportError on `check_item_use_without_owning`.

- [ ] **Step 3: Implement R1.item_use_without_owning**

Append to `tests/simulation/rules/hard.py`:

```python
_ITEM_USE_RE = re.compile(
    r"\b(utilise|boit|consomme|brandit|d[ée]gaine|enfile|active)\s+"
    r"(le|la|les|l'|un|une|des|sa|son|ses|ma|mon|mes|la grande|le grand)\s+"
    r"([A-Za-zÀ-ÿ' -]{3,40})",
    re.IGNORECASE,
)


def check_item_use_without_owning(
    narration: str,
    state: Any,
    diff: dict[str, list[Any]],
    history: list[Any],
) -> list[IncoherenceAlert]:
    """R1.item_use_without_owning — character uses an item missing from inventory."""
    alerts: list[IncoherenceAlert] = []
    inv = getattr(state, "inventory", None)
    if inv is None:
        return alerts
    owned = {item.lower() for item in getattr(inv, "items", [])}
    for match in _ITEM_USE_RE.finditer(narration):
        item_text = match.group(3).strip().rstrip(".").lower()
        if not item_text:
            continue
        # Match if any owned item name appears in the matched span.
        matched_owned = any(o in item_text or item_text in o for o in owned)
        if matched_owned:
            continue
        alerts.append(
            IncoherenceAlert(
                severity="hard",
                category="item_use_without_owning",
                turn=getattr(state, "current_turn", 0),
                rule="R1.item_use_without_owning",
                narration_snippet=_snippet_around(narration, match.group(0)),
                expected=f"Item '{item_text}' is not in inventory (owned: {sorted(owned)})",
            )
        )
    return alerts
```

- [ ] **Step 4: Run the tests until they pass**

Run: `uv run pytest tests/simulation/tests/test_rules_hard.py -v`
Expected: 13 tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/simulation/rules/hard.py tests/simulation/tests/test_rules_hard.py
git commit -m "feat(sim): R1.item_use_without_owning detection"
```

---

## Task 6: Rule R1.hp_mismatch

**Files:**
- Modify: `tests/simulation/rules/hard.py`
- Modify: `tests/simulation/tests/test_rules_hard.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/simulation/tests/test_rules_hard.py`:

```python
from tests.simulation.rules.hard import check_hp_mismatch


class TestR1HpMismatch:
    def test_wounded_narration_full_hp_triggers(self) -> None:
        state = FakeState(player_hp=15, player_max_hp=15, player_hp_ratio=1.0)
        narration = "Aria agonise au sol, grièvement blessée."
        alerts = check_hp_mismatch(narration, state, diff={}, history=[])
        assert len(alerts) == 1
        assert alerts[0].rule == "R1.hp_mismatch"

    def test_wounded_narration_low_hp_no_trigger(self) -> None:
        state = FakeState(player_hp=2, player_max_hp=15, player_hp_ratio=0.13)
        narration = "Aria agonise au sol."
        alerts = check_hp_mismatch(narration, state, diff={}, history=[])
        assert alerts == []

    def test_neutral_narration_no_trigger(self) -> None:
        state = FakeState(player_hp=15, player_max_hp=15, player_hp_ratio=1.0)
        narration = "Aria avance prudemment dans la grotte."
        alerts = check_hp_mismatch(narration, state, diff={}, history=[])
        assert alerts == []
```

- [ ] **Step 2: Verify the test fails**

Run: `uv run pytest tests/simulation/tests/test_rules_hard.py::TestR1HpMismatch -v`
Expected: ImportError on `check_hp_mismatch`.

- [ ] **Step 3: Implement R1.hp_mismatch**

Append to `tests/simulation/rules/hard.py`:

```python
_WOUNDED_RE = re.compile(
    r"\b(agonise|chancelle|s'effondre|gri[èe]vement bless[ée]|au bord de la mort|"
    r"à l'agonie|mourant[e]?)\b",
    re.IGNORECASE,
)


def check_hp_mismatch(
    narration: str,
    state: Any,
    diff: dict[str, list[Any]],
    history: list[Any],
) -> list[IncoherenceAlert]:
    """R1.hp_mismatch — narration claims wounded/dying while player HP ≥ 80%."""
    if not _WOUNDED_RE.search(narration):
        return []
    ratio = getattr(state, "player_hp_ratio", 1.0)
    if ratio < 0.8:
        return []
    return [
        IncoherenceAlert(
            severity="hard",
            category="hp_mismatch",
            turn=getattr(state, "current_turn", 0),
            rule="R1.hp_mismatch",
            narration_snippet=_snippet_around(narration, _WOUNDED_RE.search(narration).group(0)),
            expected=(
                f"Player HP = {state.player_hp}/{state.player_max_hp} "
                f"(ratio {ratio:.2f}), but narration describes wounding"
            ),
        )
    ]
```

- [ ] **Step 4: Run the tests until they pass**

Run: `uv run pytest tests/simulation/tests/test_rules_hard.py -v`
Expected: 16 tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/simulation/rules/hard.py tests/simulation/tests/test_rules_hard.py
git commit -m "feat(sim): R1.hp_mismatch — wounded narration vs full HP"
```

---

## Task 7: Rule R1.location_mismatch

**Files:**
- Modify: `tests/simulation/rules/hard.py`
- Modify: `tests/simulation/tests/test_rules_hard.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/simulation/tests/test_rules_hard.py`:

```python
from tests.simulation.rules.hard import check_location_mismatch


@dataclass
class FakeLocation:
    name: str
    aliases: list[str] = field(default_factory=list)


class TestR1LocationMismatch:
    def test_other_location_mentioned_no_movement_triggers(self) -> None:
        state = FakeState(current_location=FakeLocation(name="Cave entrance"))
        narration = "Le héros traverse la Grande Bibliothèque."
        history = [
            {"location_known": ["Cave entrance", "Grande Bibliothèque"], "moved_this_turn": False}
        ]
        alerts = check_location_mismatch(narration, state, diff={}, history=history)
        assert any(a.rule == "R1.location_mismatch" for a in alerts)

    def test_moved_this_turn_no_trigger(self) -> None:
        state = FakeState(current_location=FakeLocation(name="Cave entrance"))
        narration = "Le héros traverse la Grande Bibliothèque."
        history = [
            {"location_known": ["Cave entrance", "Grande Bibliothèque"], "moved_this_turn": True}
        ]
        alerts = check_location_mismatch(narration, state, diff={}, history=history)
        assert alerts == []

    def test_same_location_no_trigger(self) -> None:
        state = FakeState(current_location=FakeLocation(name="Cave entrance"))
        narration = "Le héros observe l'entrée de la grotte."
        history = [{"location_known": ["Cave entrance"], "moved_this_turn": False}]
        alerts = check_location_mismatch(narration, state, diff={}, history=history)
        assert alerts == []
```

- [ ] **Step 2: Verify the test fails**

Run: `uv run pytest tests/simulation/tests/test_rules_hard.py::TestR1LocationMismatch -v`
Expected: ImportError on `check_location_mismatch`.

- [ ] **Step 3: Implement R1.location_mismatch**

Append to `tests/simulation/rules/hard.py`:

```python
def check_location_mismatch(
    narration: str,
    state: Any,
    diff: dict[str, list[Any]],
    history: list[Any],
) -> list[IncoherenceAlert]:
    """R1.location_mismatch — a known location other than current is described as present."""
    current = getattr(state.current_location, "name", None)
    if current is None:
        return []
    if not history:
        return []
    last = history[-1] if isinstance(history[-1], dict) else {}
    if last.get("moved_this_turn"):
        return []
    known = last.get("location_known", []) or []
    alerts: list[IncoherenceAlert] = []
    narration_lower = narration.lower()
    for loc_name in known:
        if loc_name == current:
            continue
        if loc_name.lower() in narration_lower:
            alerts.append(
                IncoherenceAlert(
                    severity="hard",
                    category="location_mismatch",
                    turn=getattr(state, "current_turn", 0),
                    rule="R1.location_mismatch",
                    narration_snippet=_snippet_around(narration, loc_name),
                    expected=(
                        f"Current location is '{current}' and player did not move "
                        f"this turn, but narration mentions '{loc_name}'"
                    ),
                )
            )
    return alerts
```

- [ ] **Step 4: Run the tests until they pass**

Run: `uv run pytest tests/simulation/tests/test_rules_hard.py -v`
Expected: 19 tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/simulation/rules/hard.py tests/simulation/tests/test_rules_hard.py
git commit -m "feat(sim): R1.location_mismatch detection"
```

---

## Task 8: Rule R1.zone_violation

**Files:**
- Modify: `tests/simulation/rules/hard.py`
- Modify: `tests/simulation/tests/test_rules_hard.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/simulation/tests/test_rules_hard.py`:

```python
from tests.simulation.rules.hard import check_zone_violation


@dataclass
class FakeCombatState:
    zones: list[str] = field(default_factory=lambda: ["front", "back"])


class TestR1ZoneViolation:
    def test_unknown_zone_triggers(self) -> None:
        state = FakeState(
            combat_active=True,
            combat_state=FakeCombatState(zones=["front", "back"]),
        )
        narration = "Le gobelin s'avance vers la zone flanc droit."
        alerts = check_zone_violation(narration, state, diff={}, history=[])
        assert len(alerts) == 1
        assert alerts[0].rule == "R1.zone_violation"

    def test_known_zone_no_trigger(self) -> None:
        state = FakeState(
            combat_active=True,
            combat_state=FakeCombatState(zones=["front", "back"]),
        )
        narration = "Le gobelin s'avance vers la zone front."
        alerts = check_zone_violation(narration, state, diff={}, history=[])
        assert alerts == []

    def test_not_in_combat_no_trigger(self) -> None:
        state = FakeState(combat_active=False)
        narration = "La zone flanc droit est silencieuse."
        alerts = check_zone_violation(narration, state, diff={}, history=[])
        assert alerts == []
```

- [ ] **Step 2: Verify the test fails**

Run: `uv run pytest tests/simulation/tests/test_rules_hard.py::TestR1ZoneViolation -v`
Expected: ImportError on `check_zone_violation`.

- [ ] **Step 3: Implement R1.zone_violation**

Append to `tests/simulation/rules/hard.py`:

```python
_ZONE_RE = re.compile(r"\bzone\s+([a-zà-ÿ]+(?:\s+[a-zà-ÿ]+)?)\b", re.IGNORECASE)


def check_zone_violation(
    narration: str,
    state: Any,
    diff: dict[str, list[Any]],
    history: list[Any],
) -> list[IncoherenceAlert]:
    """R1.zone_violation — narration references a combat zone that doesn't exist."""
    if not state.combat_active or state.combat_state is None:
        return []
    valid = {z.lower() for z in getattr(state.combat_state, "zones", [])}
    alerts: list[IncoherenceAlert] = []
    for match in _ZONE_RE.finditer(narration):
        zone = match.group(1).strip().lower()
        if zone in valid:
            continue
        alerts.append(
            IncoherenceAlert(
                severity="hard",
                category="zone_violation",
                turn=getattr(state, "current_turn", 0),
                rule="R1.zone_violation",
                narration_snippet=_snippet_around(narration, match.group(0)),
                expected=f"Zone '{zone}' not in combat zones {sorted(valid)}",
            )
        )
    return alerts
```

- [ ] **Step 4: Run the tests until they pass**

Run: `uv run pytest tests/simulation/tests/test_rules_hard.py -v`
Expected: 22 tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/simulation/rules/hard.py tests/simulation/tests/test_rules_hard.py
git commit -m "feat(sim): R1.zone_violation — unknown combat zone"
```

---

## Task 9: Rule R1.locked_fact_violation

**Files:**
- Modify: `tests/simulation/rules/hard.py`
- Modify: `tests/simulation/tests/test_rules_hard.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/simulation/tests/test_rules_hard.py`:

```python
from tests.simulation.rules.hard import check_locked_fact_violation


class TestR1LockedFactViolation:
    def test_negated_locked_fact_triggers(self) -> None:
        state = FakeState()
        # Locked facts come from history (last entry's "locked_facts" key)
        history = [{"locked_facts": [{"text": "Le pont de bois est intact"}]}]
        narration = "Le pont de bois n'est plus intact."
        alerts = check_locked_fact_violation(narration, state, diff={}, history=history)
        assert len(alerts) == 1
        assert alerts[0].rule == "R1.locked_fact_violation"

    def test_locked_fact_consistent_no_trigger(self) -> None:
        state = FakeState()
        history = [{"locked_facts": [{"text": "Le pont de bois est intact"}]}]
        narration = "Le pont de bois est intact, vous le traversez."
        alerts = check_locked_fact_violation(narration, state, diff={}, history=history)
        assert alerts == []

    def test_no_locked_facts_no_trigger(self) -> None:
        state = FakeState()
        narration = "Le pont s'effondre."
        alerts = check_locked_fact_violation(narration, state, diff={}, history=[])
        assert alerts == []
```

- [ ] **Step 2: Verify the test fails**

Run: `uv run pytest tests/simulation/tests/test_rules_hard.py::TestR1LockedFactViolation -v`
Expected: ImportError on `check_locked_fact_violation`.

- [ ] **Step 3: Implement R1.locked_fact_violation**

Append to `tests/simulation/rules/hard.py`:

```python
_NEGATION_RE = re.compile(
    r"\b(n['e]\s+\w+\s+(plus|pas|jamais)|n['e]\s+(plus|pas|jamais)|aucun[e]?|"
    r"sans|d[ée]truit[e]?|effondr[ée]|disparu[e]?|ras[ée]|an[ée]anti[e]?)\b",
    re.IGNORECASE,
)


def _fact_subject(fact_text: str) -> str:
    """Extract the noun-phrase subject of a locked fact (first 4 words)."""
    words = fact_text.split()
    return " ".join(words[:4]).rstrip(".").lower()


def check_locked_fact_violation(
    narration: str,
    state: Any,
    diff: dict[str, list[Any]],
    history: list[Any],
) -> list[IncoherenceAlert]:
    """R1.locked_fact_violation — narration negates a locked world fact."""
    if not history:
        return []
    last = history[-1] if isinstance(history[-1], dict) else {}
    facts = last.get("locked_facts", []) or []
    alerts: list[IncoherenceAlert] = []
    narration_lower = narration.lower()
    for fact in facts:
        subject = _fact_subject(fact["text"])
        if not subject or subject not in narration_lower:
            continue
        # Look for negation in a window of 60 chars around the subject mention.
        idx = narration_lower.find(subject)
        window = narration[max(0, idx - 20) : idx + len(subject) + 60]
        if _NEGATION_RE.search(window):
            alerts.append(
                IncoherenceAlert(
                    severity="hard",
                    category="locked_fact_violation",
                    turn=getattr(state, "current_turn", 0),
                    rule="R1.locked_fact_violation",
                    narration_snippet=_snippet_around(narration, subject),
                    expected=f"Locked fact: '{fact['text']}'",
                )
            )
    return alerts
```

- [ ] **Step 4: Run the tests until they pass**

Run: `uv run pytest tests/simulation/tests/test_rules_hard.py -v`
Expected: 25 tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/simulation/rules/hard.py tests/simulation/tests/test_rules_hard.py
git commit -m "feat(sim): R1.locked_fact_violation — world fact contradiction"
```

---

## Task 10: Soft rules (R2.*)

**Files:**
- Create: `tests/simulation/rules/soft.py`
- Create: `tests/simulation/tests/test_rules_soft.py`

- [ ] **Step 1: Write the failing test**

Write `tests/simulation/tests/test_rules_soft.py`:

```python
"""Tests for tests/simulation/rules/soft.py — R2.* heuristics."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tests.simulation.rules.soft import (
    check_npc_name_drift,
    check_repetition,
    check_tense_drift,
    check_unknown_proper_noun,
)


@dataclass
class FakeNPC:
    name: str


@dataclass
class FakeState:
    npcs: dict[str, FakeNPC] = field(default_factory=dict)
    player_names: list[str] = field(default_factory=list)
    locations_known: list[str] = field(default_factory=list)
    factions_known: list[str] = field(default_factory=list)
    current_turn: int = 0


class TestR2Repetition:
    def test_identical_phrase_in_window_triggers(self) -> None:
        history = [
            {"narration": "L'air est lourd de menaces dans cette pièce sombre"},
            {"narration": "Le héros entre."},
            {"narration": "L'air est lourd de menaces dans cette pièce sombre"},
        ]
        narration = "L'air est lourd de menaces dans cette pièce sombre"
        alerts = check_repetition(narration, FakeState(), diff={}, history=history)
        assert len(alerts) == 1
        assert alerts[0].rule == "R2.repetition"

    def test_distinct_narration_no_trigger(self) -> None:
        history = [{"narration": "Le héros entre."}, {"narration": "Il regarde autour."}]
        narration = "Une chouette ulule dans la nuit."
        alerts = check_repetition(narration, FakeState(), diff={}, history=history)
        assert alerts == []


class TestR2NpcNameDrift:
    def test_levenshtein_close_match_triggers(self) -> None:
        state = FakeState(npcs={"Garm": FakeNPC("Garm")})
        narration = "Gorm hoche la tête."
        alerts = check_npc_name_drift(narration, state, diff={}, history=[])
        assert len(alerts) == 1
        assert alerts[0].rule == "R2.npc_name_drift"

    def test_exact_match_no_trigger(self) -> None:
        state = FakeState(npcs={"Garm": FakeNPC("Garm")})
        narration = "Garm hoche la tête."
        alerts = check_npc_name_drift(narration, state, diff={}, history=[])
        assert alerts == []

    def test_far_match_no_trigger(self) -> None:
        state = FakeState(npcs={"Garm": FakeNPC("Garm")})
        narration = "Khaalim hoche la tête."
        alerts = check_npc_name_drift(narration, state, diff={}, history=[])
        assert alerts == []


class TestR2TenseDrift:
    def test_mixed_tense_in_one_sentence_triggers(self) -> None:
        narration = "Le héros a marché vers la grotte et regarde l'entrée."
        # "a marché" = passé composé, "regarde" = présent
        alerts = check_tense_drift(narration, FakeState(), diff={}, history=[])
        assert len(alerts) == 1

    def test_consistent_tense_no_trigger(self) -> None:
        narration = "Le héros marche vers la grotte et regarde l'entrée."
        alerts = check_tense_drift(narration, FakeState(), diff={}, history=[])
        assert alerts == []


class TestR2UnknownProperNoun:
    def test_unknown_capitalized_word_triggers(self) -> None:
        state = FakeState(
            npcs={"Garm": FakeNPC("Garm")},
            locations_known=["Cave entrance"],
            factions_known=["Order of the Phoenix"],
        )
        narration = "Le héros aperçoit le Volcanus au loin."
        alerts = check_unknown_proper_noun(narration, state, diff={}, history=[])
        assert any("Volcanus" in a.expected for a in alerts)

    def test_known_words_no_trigger(self) -> None:
        state = FakeState(
            npcs={"Garm": FakeNPC("Garm")},
            locations_known=["Cave entrance"],
            factions_known=["Order"],
        )
        narration = "Garm parle d'Order et de Cave entrance."
        alerts = check_unknown_proper_noun(narration, state, diff={}, history=[])
        assert alerts == []
```

- [ ] **Step 2: Verify the test fails**

Run: `uv run pytest tests/simulation/tests/test_rules_soft.py -v`
Expected: ImportError on `tests.simulation.rules.soft`.

- [ ] **Step 3: Implement R2.* rules**

Write `tests/simulation/rules/soft.py`:

```python
"""Soft incoherence rules (R2.*) — text-similarity-based heuristics."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

from tests.simulation.records import IncoherenceAlert

# Reuse helpers from hard.py for consistency.
from tests.simulation.rules.hard import (
    _PROPER_NOUN_RE,
    _PROPER_NOUN_WHITELIST,
    _snippet_around,
)


def _levenshtein(a: str, b: str) -> int:
    """Iterative Levenshtein distance."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            curr[j] = min(curr[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost)
        prev = curr
    return prev[-1]


def check_repetition(
    narration: str,
    state: Any,
    diff: dict[str, list[Any]],
    history: list[Any],
) -> list[IncoherenceAlert]:
    """R2.repetition — narration matches a phrase from any of the last 5 turns
    by ≥10 consecutive words."""
    window = history[-5:] if history else []
    words = narration.split()
    for prev in window:
        prev_text = prev.get("narration", "") if isinstance(prev, dict) else ""
        if not prev_text:
            continue
        # Use SequenceMatcher to find longest contiguous match.
        sm = SequenceMatcher(a=prev_text.split(), b=words, autojunk=False)
        match = sm.find_longest_match()
        if match.size >= 10:
            snippet = " ".join(words[match.b : match.b + match.size])
            return [
                IncoherenceAlert(
                    severity="soft",
                    category="repetition",
                    turn=getattr(state, "current_turn", 0),
                    rule="R2.repetition",
                    narration_snippet=snippet[:200],
                    expected="Same ≥10-word phrase appeared in the last 5 turns",
                )
            ]
    return []


def check_npc_name_drift(
    narration: str,
    state: Any,
    diff: dict[str, list[Any]],
    history: list[Any],
) -> list[IncoherenceAlert]:
    """R2.npc_name_drift — proper noun ≤2 edits from a known NPC name but not exact."""
    alerts: list[IncoherenceAlert] = []
    known = list(state.npcs)
    seen: set[str] = set()
    for match in _PROPER_NOUN_RE.finditer(narration):
        word = match.group(1)
        if word in _PROPER_NOUN_WHITELIST or word in known or word.lower() in seen:
            continue
        for npc_name in known:
            if _levenshtein(word.lower(), npc_name.lower()) <= 2 and word != npc_name:
                alerts.append(
                    IncoherenceAlert(
                        severity="soft",
                        category="npc_name_drift",
                        turn=getattr(state, "current_turn", 0),
                        rule="R2.npc_name_drift",
                        narration_snippet=_snippet_around(narration, word),
                        expected=f"'{word}' is 1-2 edits from known NPC '{npc_name}'",
                    )
                )
                seen.add(word.lower())
                break
    return alerts


_PASSE_COMPOSE_RE = re.compile(
    r"\b(a|ont|avons|avez|ai|as)\s+([a-zà-ÿ]+[ée]|fait|pris|vu|dit|allé)\b",
    re.IGNORECASE,
)
_PRESENT_VERB_RE = re.compile(
    r"\b(regarde|marche|parle|attaque|saute|voit|entend|crie|court|se tient)\b",
    re.IGNORECASE,
)


def check_tense_drift(
    narration: str,
    state: Any,
    diff: dict[str, list[Any]],
    history: list[Any],
) -> list[IncoherenceAlert]:
    """R2.tense_drift — passé composé and present verbs in the same sentence."""
    alerts: list[IncoherenceAlert] = []
    for sentence in re.split(r"[.!?]", narration):
        if not sentence.strip():
            continue
        if _PASSE_COMPOSE_RE.search(sentence) and _PRESENT_VERB_RE.search(sentence):
            alerts.append(
                IncoherenceAlert(
                    severity="soft",
                    category="tense_drift",
                    turn=getattr(state, "current_turn", 0),
                    rule="R2.tense_drift",
                    narration_snippet=sentence.strip()[:200],
                    expected="Sentence mixes passé composé and present-tense verbs",
                )
            )
    return alerts


def check_unknown_proper_noun(
    narration: str,
    state: Any,
    diff: dict[str, list[Any]],
    history: list[Any],
) -> list[IncoherenceAlert]:
    """R2.unknown_proper_noun — capitalized word matching no known entity.

    Differs from R1.phantom_npc by being broader: includes locations and factions.
    """
    alerts: list[IncoherenceAlert] = []
    known_names = (
        {n.lower() for n in state.npcs}
        | {p.lower() for p in getattr(state, "player_names", [])}
        | {l.lower() for l in getattr(state, "locations_known", [])}
        | {f.lower() for f in getattr(state, "factions_known", [])}
    )
    # Locations/factions may be multi-word; build a lowered string for membership tests.
    seen: set[str] = set()
    for match in _PROPER_NOUN_RE.finditer(narration):
        word = match.group(1)
        if word in _PROPER_NOUN_WHITELIST or word.lower() in seen:
            continue
        seen.add(word.lower())
        # Check exact, prefix, or substring against known multi-word names.
        if any(word.lower() in name for name in known_names):
            continue
        alerts.append(
            IncoherenceAlert(
                severity="soft",
                category="unknown_proper_noun",
                turn=getattr(state, "current_turn", 0),
                rule="R2.unknown_proper_noun",
                narration_snippet=_snippet_around(narration, word),
                expected=f"'{word}' is not a known NPC, player, location, or faction",
            )
        )
    return alerts
```

- [ ] **Step 4: Run the tests until they pass**

Run: `uv run pytest tests/simulation/tests/test_rules_soft.py -v`
Expected: all 8 tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/simulation/rules/soft.py tests/simulation/tests/test_rules_soft.py
git commit -m "feat(sim): R2.* soft heuristics — repetition, name drift, tense, unknown nouns"
```

---

## Task 11: Drift rules (R3.*)

**Files:**
- Create: `tests/simulation/rules/drift.py`
- Create: `tests/simulation/tests/test_rules_drift.py`

- [ ] **Step 1: Write the failing test**

Write `tests/simulation/tests/test_rules_drift.py`:

```python
"""Tests for tests/simulation/rules/drift.py — R3.* informational drifts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tests.simulation.rules.drift import (
    check_condition_phantom,
    check_disposition_silent_change,
    check_quest_silent_progress,
)


@dataclass
class FakeState:
    current_turn: int = 0


class TestR3DispositionSilentChange:
    def test_disposition_change_no_intent_triggers(self) -> None:
        diff = {"npc.Garm.disposition": ["friendly", "hostile"]}
        intent_action = "look"
        alerts = check_disposition_silent_change(
            narration="", state=FakeState(), diff=diff, history=[{"intent_action": intent_action}]
        )
        assert len(alerts) == 1
        assert alerts[0].rule == "R3.disposition_silent_change"

    def test_disposition_change_with_talk_no_trigger(self) -> None:
        diff = {"npc.Garm.disposition": ["friendly", "hostile"]}
        alerts = check_disposition_silent_change(
            narration="", state=FakeState(), diff=diff, history=[{"intent_action": "talk"}]
        )
        assert alerts == []


class TestR3QuestSilentProgress:
    def test_quest_progress_no_action_triggers(self) -> None:
        diff = {"quests.main.completed_objectives": [0, 1]}
        alerts = check_quest_silent_progress(
            narration="", state=FakeState(), diff=diff, history=[{"intent_action": "wait"}]
        )
        assert len(alerts) == 1
        assert alerts[0].rule == "R3.quest_silent_progress"

    def test_quest_progress_with_relevant_action_no_trigger(self) -> None:
        diff = {"quests.main.completed_objectives": [0, 1]}
        alerts = check_quest_silent_progress(
            narration="", state=FakeState(), diff=diff, history=[{"intent_action": "talk"}]
        )
        assert alerts == []


class TestR3ConditionPhantom:
    def test_condition_added_no_action_triggers(self) -> None:
        diff = {"character.conditions": [[], ["poisoned"]]}
        alerts = check_condition_phantom(
            narration="", state=FakeState(), diff=diff, history=[{"intent_action": "wait"}]
        )
        assert len(alerts) == 1
        assert alerts[0].rule == "R3.condition_phantom"

    def test_condition_added_with_action_no_trigger(self) -> None:
        diff = {"character.conditions": [[], ["poisoned"]]}
        alerts = check_condition_phantom(
            narration="", state=FakeState(), diff=diff, history=[{"intent_action": "attack"}]
        )
        assert alerts == []
```

- [ ] **Step 2: Verify the test fails**

Run: `uv run pytest tests/simulation/tests/test_rules_drift.py -v`
Expected: ImportError on `tests.simulation.rules.drift`.

- [ ] **Step 3: Implement R3.* rules**

Write `tests/simulation/rules/drift.py`:

```python
"""Drift rules (R3.*) — informational alerts on state changes without obvious cause."""

from __future__ import annotations

from typing import Any

from tests.simulation.records import IncoherenceAlert

# Actions that are expected to plausibly cause certain state changes.
_DISPOSITION_CAUSING_ACTIONS = {"talk", "attack", "free_form", "cast_spell"}
_QUEST_CAUSING_ACTIONS = {"talk", "search", "attack", "use_item", "free_form", "move"}
_CONDITION_CAUSING_ACTIONS = {"attack", "cast_spell", "use_item", "defend", "free_form"}


def _last_intent_action(history: list[Any]) -> str | None:
    if not history:
        return None
    last = history[-1]
    return last.get("intent_action") if isinstance(last, dict) else None


def check_disposition_silent_change(
    narration: str,
    state: Any,
    diff: dict[str, list[Any]],
    history: list[Any],
) -> list[IncoherenceAlert]:
    """R3.disposition_silent_change — NPC.disposition changed but no plausible action."""
    alerts: list[IncoherenceAlert] = []
    last_action = _last_intent_action(history)
    if last_action in _DISPOSITION_CAUSING_ACTIONS:
        return []
    for path, change in diff.items():
        if path.startswith("npc.") and path.endswith(".disposition"):
            alerts.append(
                IncoherenceAlert(
                    severity="drift",
                    category="disposition_silent_change",
                    turn=getattr(state, "current_turn", 0),
                    rule="R3.disposition_silent_change",
                    narration_snippet=narration[:200],
                    expected=(
                        f"{path}: {change[0]} → {change[1]} but last action was "
                        f"'{last_action}' (no plausible cause)"
                    ),
                )
            )
    return alerts


def check_quest_silent_progress(
    narration: str,
    state: Any,
    diff: dict[str, list[Any]],
    history: list[Any],
) -> list[IncoherenceAlert]:
    """R3.quest_silent_progress — quest progressed but no plausible action."""
    alerts: list[IncoherenceAlert] = []
    last_action = _last_intent_action(history)
    if last_action in _QUEST_CAUSING_ACTIONS:
        return []
    for path, change in diff.items():
        if path.startswith("quests.") and (
            path.endswith(".completed_objectives") or path.endswith(".status")
        ):
            alerts.append(
                IncoherenceAlert(
                    severity="drift",
                    category="quest_silent_progress",
                    turn=getattr(state, "current_turn", 0),
                    rule="R3.quest_silent_progress",
                    narration_snippet=narration[:200],
                    expected=(
                        f"{path}: {change[0]} → {change[1]} but last action was "
                        f"'{last_action}'"
                    ),
                )
            )
    return alerts


def check_condition_phantom(
    narration: str,
    state: Any,
    diff: dict[str, list[Any]],
    history: list[Any],
) -> list[IncoherenceAlert]:
    """R3.condition_phantom — a condition appeared/disappeared without an action."""
    alerts: list[IncoherenceAlert] = []
    last_action = _last_intent_action(history)
    if last_action in _CONDITION_CAUSING_ACTIONS:
        return []
    for path, change in diff.items():
        if path.endswith(".conditions"):
            alerts.append(
                IncoherenceAlert(
                    severity="drift",
                    category="condition_phantom",
                    turn=getattr(state, "current_turn", 0),
                    rule="R3.condition_phantom",
                    narration_snippet=narration[:200],
                    expected=(
                        f"{path}: {change[0]} → {change[1]} but last action was "
                        f"'{last_action}'"
                    ),
                )
            )
    return alerts
```

- [ ] **Step 4: Run the tests until they pass**

Run: `uv run pytest tests/simulation/tests/test_rules_drift.py -v`
Expected: all 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/simulation/rules/drift.py tests/simulation/tests/test_rules_drift.py
git commit -m "feat(sim): R3.* drift rules — silent disposition/quest/condition changes"
```

---

## Task 12: IncoherenceChecker aggregator

**Files:**
- Modify: `tests/simulation/rules/__init__.py`
- Create: `tests/simulation/checker.py`
- Create: `tests/simulation/tests/test_checker.py`

- [ ] **Step 1: Write the failing test**

Write `tests/simulation/tests/test_checker.py`:

```python
"""Tests for tests/simulation/checker.py — IncoherenceChecker aggregator."""

from __future__ import annotations

from dataclasses import dataclass, field

from tests.simulation.checker import IncoherenceChecker
from tests.simulation.records import IncoherenceAlert
from tests.simulation.rules.hard import check_npc_status


@dataclass
class FakeNPC:
    name: str
    status: str = "alive"
    hp: int = 10


@dataclass
class FakeState:
    npcs: dict[str, FakeNPC] = field(default_factory=dict)
    current_location: object = None
    combat_active: bool = False
    combat_state: object = None
    inventory: object = None
    player_names: list[str] = field(default_factory=list)
    player_hp_ratio: float = 1.0
    player_max_hp: int = 15
    player_hp: int = 15
    current_turn: int = 0
    locations_known: list[str] = field(default_factory=list)
    factions_known: list[str] = field(default_factory=list)


class TestIncoherenceChecker:
    def test_aggregates_alerts_from_all_rules(self) -> None:
        checker = IncoherenceChecker()
        state = FakeState(npcs={"Garm": FakeNPC("Garm", status="dead", hp=0)})
        narration = "Garm sourit malicieusement."
        alerts = checker.check(narration, state, diff={}, history=[])
        assert any(a.rule == "R1.npc_status" for a in alerts)

    def test_no_alerts_for_clean_narration(self) -> None:
        checker = IncoherenceChecker()
        state = FakeState()
        narration = "Le héros avance prudemment."
        alerts = checker.check(narration, state, diff={}, history=[])
        # May still fire R2.unknown_proper_noun on "Le" — but Le is in whitelist.
        # Filter out any soft alerts to focus on hard.
        hard = [a for a in alerts if a.severity == "hard"]
        assert hard == []

    def test_subset_of_rules(self) -> None:
        # Allow injecting a custom rule list (for tests).
        checker = IncoherenceChecker(rules=[check_npc_status])
        state = FakeState(npcs={"Garm": FakeNPC("Garm", status="dead", hp=0)})
        narration = "Garm sourit."
        alerts = checker.check(narration, state, diff={}, history=[])
        assert len(alerts) == 1
        assert alerts[0].rule == "R1.npc_status"

    def test_returns_typed_alerts(self) -> None:
        checker = IncoherenceChecker()
        state = FakeState()
        alerts = checker.check("test", state, diff={}, history=[])
        for a in alerts:
            assert isinstance(a, IncoherenceAlert)
```

- [ ] **Step 2: Verify the test fails**

Run: `uv run pytest tests/simulation/tests/test_checker.py -v`
Expected: ImportError on `tests.simulation.checker`.

- [ ] **Step 3: Populate `tests/simulation/rules/__init__.py`**

Write `tests/simulation/rules/__init__.py`:

```python
"""Exposes the canonical ALL_RULES list — order is checker invocation order."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tests.simulation.records import IncoherenceAlert
from tests.simulation.rules.drift import (
    check_condition_phantom,
    check_disposition_silent_change,
    check_quest_silent_progress,
)
from tests.simulation.rules.hard import (
    check_hp_mismatch,
    check_item_use_without_owning,
    check_location_mismatch,
    check_locked_fact_violation,
    check_npc_status,
    check_phantom_npc,
    check_zone_violation,
)
from tests.simulation.rules.soft import (
    check_npc_name_drift,
    check_repetition,
    check_tense_drift,
    check_unknown_proper_noun,
)

Rule = Callable[[str, Any, dict[str, list[Any]], list[Any]], list[IncoherenceAlert]]

ALL_RULES: list[Rule] = [
    # Hard
    check_npc_status,
    check_phantom_npc,
    check_item_use_without_owning,
    check_hp_mismatch,
    check_location_mismatch,
    check_zone_violation,
    check_locked_fact_violation,
    # Soft
    check_repetition,
    check_npc_name_drift,
    check_tense_drift,
    check_unknown_proper_noun,
    # Drift
    check_disposition_silent_change,
    check_quest_silent_progress,
    check_condition_phantom,
]

__all__ = ["ALL_RULES", "Rule"]
```

- [ ] **Step 4: Implement the checker**

Write `tests/simulation/checker.py`:

```python
"""IncoherenceChecker — aggregates rule outputs into a single alert list."""

from __future__ import annotations

from typing import Any

from tests.simulation.records import IncoherenceAlert
from tests.simulation.rules import ALL_RULES, Rule


class IncoherenceChecker:
    """Runs each rule in order, returns the combined alerts."""

    def __init__(self, rules: list[Rule] | None = None) -> None:
        self._rules: list[Rule] = list(rules) if rules is not None else list(ALL_RULES)

    def check(
        self,
        narration: str,
        state: Any,
        diff: dict[str, list[Any]],
        history: list[Any],
    ) -> list[IncoherenceAlert]:
        alerts: list[IncoherenceAlert] = []
        for rule in self._rules:
            alerts.extend(rule(narration, state, diff, history))
        return alerts
```

- [ ] **Step 5: Run the tests until they pass**

Run: `uv run pytest tests/simulation/tests/test_checker.py -v`
Expected: all 4 tests pass.

- [ ] **Step 6: Commit**

```bash
git add tests/simulation/rules/__init__.py tests/simulation/checker.py tests/simulation/tests/test_checker.py
git commit -m "feat(sim): IncoherenceChecker aggregator + ALL_RULES registry"
```

---

## Task 13: ai/client.py — simulation_mode flag

**Files:**
- Modify: `ai/client.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/simulation/tests/test_records.py` a new test class (or create `tests/simulation/tests/test_ai_client_simulation_mode.py`):

Write `tests/simulation/tests/test_ai_client_simulation_mode.py`:

```python
"""Tests for ai.client.OllamaClient.simulation_mode flag."""

from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from ai.client import OllamaClient

OLLAMA_BASE = "http://localhost:11434"
TAGS_URL = f"{OLLAMA_BASE}/api/tags"
CHAT_URL = f"{OLLAMA_BASE}/api/chat"


def _add_health(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=TAGS_URL, json={"models": []})


def _add_chat(httpx_mock: HTTPXMock, capture: list[dict]) -> None:
    def _intercept(request):
        import json

        capture.append(json.loads(request.content))
        return None  # let the mock match the default response

    httpx_mock.add_callback(callback=_intercept, url=CHAT_URL)
    httpx_mock.add_response(
        url=CHAT_URL,
        json={"message": {"content": '{"ok": true}'}, "done": True},
    )


def test_simulation_mode_forces_temperature_zero(httpx_mock: HTTPXMock) -> None:
    _add_health(httpx_mock)
    captured: list[dict] = []

    def _record_request(request):
        import json

        captured.append(json.loads(request.content))

    httpx_mock.add_response(
        url=CHAT_URL,
        json={"message": {"content": '{"ok": true}'}, "done": True},
    )

    client = OllamaClient(simulation_mode=True)

    # Patch the httpx client to capture
    original_post = client._client.post

    def post_and_capture(*args, **kwargs):
        if "json" in kwargs:
            captured.append(kwargs["json"])
        return original_post(*args, **kwargs)

    client._client.post = post_and_capture

    client.chat_json(
        model="qwen3.5:4b",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.9,  # should be forced to 0.0
    )

    assert captured, "no request captured"
    options = captured[0].get("options") or {}
    assert options.get("temperature") == 0.0, (
        f"expected temperature=0.0, got {options.get('temperature')}"
    )


def test_default_temperature_not_forced(httpx_mock: HTTPXMock) -> None:
    _add_health(httpx_mock)
    captured: list[dict] = []

    httpx_mock.add_response(
        url=CHAT_URL,
        json={"message": {"content": '{"ok": true}'}, "done": True},
    )

    client = OllamaClient(simulation_mode=False)
    original_post = client._client.post

    def post_and_capture(*args, **kwargs):
        if "json" in kwargs:
            captured.append(kwargs["json"])
        return original_post(*args, **kwargs)

    client._client.post = post_and_capture

    client.chat_json(
        model="qwen3.5:4b",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.7,
    )

    options = captured[0].get("options") or {}
    assert options.get("temperature") == 0.7
```

- [ ] **Step 2: Verify the test fails**

Run: `uv run pytest tests/simulation/tests/test_ai_client_simulation_mode.py -v`
Expected: `TypeError: __init__() got an unexpected keyword argument 'simulation_mode'`.

- [ ] **Step 3: Read the current OllamaClient init**

Run: `Read ai/client.py:55-95` (use the Read tool with `offset=55, limit=40`) to see the exact constructor and `chat_json` signature. Modify accordingly:

- Add a `simulation_mode: bool = False` parameter to `__init__`.
- Store it on `self`.
- In `chat_json`, before building the request body, if `self._simulation_mode`, override `temperature = 0.0`.

Concretely, edit `ai/client.py`:

```python
# In __init__ signature, append parameter:
def __init__(
    self,
    base_url: str = DEFAULT_URL,
    timeout: float = DEFAULT_TIMEOUT,
    simulation_mode: bool = False,
) -> None:
    self._base_url = base_url.rstrip("/")
    self._client = httpx.Client(
        timeout=httpx.Timeout(timeout, connect=10.0),
    )
    self._simulation_mode = simulation_mode
    try:
        self._client.get(f"{self._base_url}/api/tags")
    except httpx.ConnectError as exc:
        raise OllamaUnavailableError(
            f"Cannot connect to Ollama at {self._base_url}"
        ) from exc
```

Then locate the body of `chat_json` and at the top, after the signature, add:

```python
if self._simulation_mode:
    temperature = 0.0
```

- [ ] **Step 4: Run the tests until they pass**

Run: `uv run pytest tests/simulation/tests/test_ai_client_simulation_mode.py -v`
Expected: both tests pass.

- [ ] **Step 5: Verify nothing else broke**

Run: `uv run pytest tests/ai/ -q 2>&1 | tail -10`
Expected: existing AI tests still pass.

- [ ] **Step 6: Commit**

```bash
git add ai/client.py tests/simulation/tests/test_ai_client_simulation_mode.py
git commit -m "feat(ai): OllamaClient simulation_mode forces temperature=0"
```

---

## Task 14: ScenarioRunner — ai_enabled flag

**Files:**
- Modify: `tests/scenarios/scenario_runner.py`

- [ ] **Step 1: Read the current ScenarioRunner constructor and start_campaign**

Use Read on `tests/scenarios/scenario_runner.py` to inspect:
- The `__init__` signature (around line 225).
- The `start_campaign` method (around line 365–410) — where `GameSession(campaign=campaign)` is created.

We are about to add an `ai_enabled: bool = False` constructor flag and wire real AI components on the session after creation.

- [ ] **Step 2: Write the failing test**

Write `tests/simulation/tests/test_scenario_runner_ai_enabled.py`:

```python
"""Verifies ScenarioRunner can be created with ai_enabled=True and wires AI."""

from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from ai.client import OllamaClient
from db.database import Base
from tests.scenarios.scenario_runner import ScenarioRunner

OLLAMA_BASE = "http://localhost:11434"
TAGS_URL = f"{OLLAMA_BASE}/api/tags"


@pytest.fixture()
def in_memory_db_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine)
    engine.dispose()


@pytest.fixture()
def ollama_client(httpx_mock: HTTPXMock) -> OllamaClient:
    httpx_mock.add_response(url=TAGS_URL, json={"models": []})
    return OllamaClient(simulation_mode=True)


async def test_ai_enabled_wires_real_interpreter_and_narrator(
    in_memory_db_factory, ollama_client
) -> None:
    runner = ScenarioRunner(
        in_memory_db_factory, ai_enabled=True, ollama_client=ollama_client
    )
    await runner.start_campaign(theme="Test", players=1)
    session = runner.session
    assert session is not None
    assert session.interpreter is not None, "interpreter should be wired"
    assert session.narrator is not None, "narrator should be wired"


async def test_ai_disabled_keeps_existing_behavior(in_memory_db_factory) -> None:
    runner = ScenarioRunner(in_memory_db_factory)
    await runner.start_campaign(theme="Test", players=1)
    session = runner.session
    assert session is not None
    # Default behavior: AI is not wired (None)
    assert session.interpreter is None
    assert session.narrator is None
```

- [ ] **Step 3: Verify the test fails**

Run: `uv run pytest tests/simulation/tests/test_scenario_runner_ai_enabled.py -v`
Expected: `TypeError: __init__() got an unexpected keyword argument 'ai_enabled'`.

- [ ] **Step 4: Modify ScenarioRunner**

In `tests/scenarios/scenario_runner.py`:

1. Add imports near the existing AI imports if not already present:
   ```python
   from ai.client import OllamaClient
   from ai.interpreter import Interpreter
   from ai.narrator import Narrator
   from ai.story_director import StoryDirector
   ```

2. Change the constructor signature:
   ```python
   def __init__(
       self,
       db_factory: sessionmaker[Session],
       *,
       ai_enabled: bool = False,
       ollama_client: OllamaClient | None = None,
   ) -> None:
       ...
       # at the end of the existing __init__ body:
       self.ai_enabled = ai_enabled
       self.ollama_client = ollama_client
   ```

3. Modify `start_campaign` — after the line `session = GameSession(campaign=campaign)` add:

   ```python
   if self.ai_enabled:
       client = self.ollama_client
       if client is None:
           raise RuntimeError(
               "ai_enabled=True requires an ollama_client to be passed"
           )
       session.interpreter = Interpreter(client)
       session.narrator = Narrator(client)
       # story_director is optional and requires semantic_memory, leave None.
       session.story_director = None
   ```

- [ ] **Step 5: Run the tests until they pass**

Run: `uv run pytest tests/simulation/tests/test_scenario_runner_ai_enabled.py -v`
Expected: both tests pass.

- [ ] **Step 6: Verify all existing scenarios still pass**

Run: `uv run pytest tests/scenarios/ -q 2>&1 | tail -10`
Expected: all scenarios still pass (the new flag defaults to False, preserving prior behavior).

- [ ] **Step 7: Commit**

```bash
git add tests/scenarios/scenario_runner.py tests/simulation/tests/test_scenario_runner_ai_enabled.py
git commit -m "feat(scenarios): ScenarioRunner ai_enabled flag wires real AI"
```

---

## Task 15: Agent prompt files

**Files:**
- Create: `tests/simulation/prompts/agent_system.txt`
- Create: `tests/simulation/prompts/few_shots.json`
- Delete: `tests/simulation/prompts/.gitkeep` (now superseded)

- [ ] **Step 1: Write the system prompt**

Write `tests/simulation/prompts/agent_system.txt`:

```
You are an autonomous player in a Dungeons & Dragons-inspired tabletop RPG. Your role is to act in-character as the character described in the observation, choose ONE action per turn, and progress the story naturally.

You MUST respond with EXACTLY ONE JSON object — no prose before or after — matching this schema:

{
  "reasoning": "<one short sentence (≤200 chars) explaining your choice>",
  "action": "<one of: attack, cast_spell, defend, flee, move, look, talk, search, equip, unequip, use_item, free_form, wait>",
  "args": { ... },
  "raw_text": "<required only when action == 'free_form', otherwise null>"
}

Action arguments by type:
- attack         → {"target": "<enemy name from observation>"}
- cast_spell     → {"spell": "<spell name>", "target": "<target name>"}
- defend         → {}
- flee           → {}
- move           → {"direction": "<exit name>"}
- look           → {}
- talk           → {"npc": "<NPC name>"}
- search         → {"target": "<area or object>"}
- equip          → {"item": "<inventory item>", "slot": "<slot>"}
- unequip        → {"slot": "<slot>"}
- use_item       → {"item": "<consumable name>"}
- free_form      → {} and raw_text = "<short natural-language action sentence>"
- wait           → {} (use ONLY if every other action would be illegal)

Rules:
1. NEVER invent enemies, NPCs, items, or directions that are not in the observation.
2. In combat: prefer attack / cast_spell / use_item. Only "flee" if your HP ratio < 0.25.
3. Out of combat: alternate between look, move, search, talk, free_form. Do NOT repeat the same action 3 turns in a row.
4. Use "free_form" once every 3-4 exploration turns to keep the narration alive. raw_text should be a short, focused intent (≤120 chars).
5. If the same (action, args) was your last choice and the corrective hint asks you to vary, pick something different.
```

- [ ] **Step 2: Write the few-shots file**

Write `tests/simulation/prompts/few_shots.json`:

```json
{
  "exploration": [
    {
      "observation": "TURN 1\nYou play: Aria (Elf, Wizard, lvl 1, HP 15/15)\nLocation: Cave entrance. Exits: north → Cave deep\nEquipped: Quarterstaff\nInventory: Healing potion (x2)\nCombat: not in combat\nNPCs present: none\nLast 3 turns: -, -, -",
      "intent": {
        "reasoning": "First turn, observe the surroundings before moving in",
        "action": "look",
        "args": {},
        "raw_text": null
      }
    },
    {
      "observation": "TURN 3\nYou play: Aria (Elf, Wizard, lvl 1, HP 15/15)\nLocation: Cave entrance. Exits: north → Cave deep\nCombat: not in combat\nNPCs present: Garm (friendly)\nLast 3 turns: look, look, look",
      "intent": {
        "reasoning": "Garm is here and I've been idle — engage in conversation",
        "action": "talk",
        "args": {"npc": "Garm"},
        "raw_text": null
      }
    }
  ],
  "combat": [
    {
      "observation": "TURN 5\nYou play: Aria (Elf, Wizard, lvl 1, HP 12/15)\nCombat: IN COMBAT, your turn\n  Enemies: Goblin_1 HP 4/4 AC 12; Goblin_2 HP 1/4 AC 12 (BLOODIED)\nEquipped: Quarterstaff\nLast 3 turns: look, attack(Goblin_2), -",
      "intent": {
        "reasoning": "Goblin_2 is bloodied, finishing it removes one threat",
        "action": "attack",
        "args": {"target": "Goblin_2"},
        "raw_text": null
      }
    }
  ],
  "free_form": [
    {
      "observation": "TURN 8\nYou play: Aria (Elf, Wizard, lvl 1, HP 15/15)\nLocation: Library. Exits: south → Cave deep\nInventory: Healing potion (x2), Old tome\nCombat: not in combat\nNPCs present: none\nLast 3 turns: move(north), look, search(shelves)",
      "intent": {
        "reasoning": "Exploration has been mechanical — try a creative action to engage the Narrator",
        "action": "free_form",
        "args": {},
        "raw_text": "Je m'assieds et feuillette l'Old tome à voix haute."
      }
    }
  ]
}
```

- [ ] **Step 3: Remove the obsolete placeholder**

```bash
rm tests/simulation/prompts/.gitkeep
```

- [ ] **Step 4: Verify nothing broke**

Run: `uv run pytest tests/simulation/ -q 2>&1 | tail -5`
Expected: existing tests still pass; new prompt files don't affect anything yet.

- [ ] **Step 5: Commit**

```bash
git add tests/simulation/prompts/agent_system.txt tests/simulation/prompts/few_shots.json
git rm tests/simulation/prompts/.gitkeep
git commit -m "feat(sim): agent system prompt + few-shot examples"
```

---

## Task 16: AutonomousAgent — observation builder

**Files:**
- Create: `tests/simulation/agent.py` (initial version — just `build_observation`)
- Create: `tests/simulation/tests/test_agent.py`

- [ ] **Step 1: Write the failing test**

Write `tests/simulation/tests/test_agent.py`:

```python
"""Tests for tests/simulation/agent.py — AutonomousAgent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from tests.simulation.agent import build_observation


@dataclass
class FakeCharacter:
    name: str
    race: str
    char_class: str
    level: int
    hp: int
    max_hp: int
    ac: int


@dataclass
class FakeLocation:
    name: str
    exits: dict[str, str] = field(default_factory=dict)


@dataclass
class FakeCombatant:
    name: str
    hp: int
    max_hp: int
    ac: int
    zone: str = "front"


@dataclass
class FakeCombatState:
    is_active: bool = True
    enemies: list[FakeCombatant] = field(default_factory=list)


@dataclass
class FakeSessionLike:
    character: FakeCharacter
    location: FakeLocation
    inventory_items: list[str]
    equipped: dict[str, str]
    combat_active: bool = False
    combat: FakeCombatState | None = None
    npcs_present: list[str] = field(default_factory=list)


class TestBuildObservation:
    def test_exploration_observation(self) -> None:
        sess = FakeSessionLike(
            character=FakeCharacter("Aria", "Elf", "Wizard", 1, 15, 15, 13),
            location=FakeLocation("Cave entrance", {"north": "Cave deep"}),
            inventory_items=["Healing potion (x2)", "Old tome"],
            equipped={"main_hand": "Quarterstaff"},
        )
        obs = build_observation(
            turn=3,
            session=sess,
            last_actions=["look", "move(north)", "look"],
            last_narration="L'air est froid.",
        )
        assert "TURN 3" in obs
        assert "Aria" in obs
        assert "HP 15/15" in obs
        assert "Cave entrance" in obs
        assert "north" in obs
        assert "Quarterstaff" in obs
        assert "Healing potion" in obs
        assert "not in combat" in obs.lower()
        assert "look, move(north), look" in obs

    def test_combat_observation_shows_enemies(self) -> None:
        sess = FakeSessionLike(
            character=FakeCharacter("Aria", "Elf", "Wizard", 1, 12, 15, 13),
            location=FakeLocation("Cave", {}),
            inventory_items=[],
            equipped={},
            combat_active=True,
            combat=FakeCombatState(
                is_active=True,
                enemies=[
                    FakeCombatant("Goblin_1", 4, 4, 12),
                    FakeCombatant("Goblin_2", 1, 4, 12),
                ],
            ),
        )
        obs = build_observation(
            turn=5,
            session=sess,
            last_actions=["attack(Goblin_2)", "attack(Goblin_1)", "look"],
            last_narration="Le gobelin chancelle.",
        )
        assert "IN COMBAT" in obs
        assert "Goblin_1" in obs and "HP 4/4" in obs
        assert "Goblin_2" in obs and "HP 1/4" in obs
        assert "BLOODIED" in obs  # Goblin_2 is below 50% HP


    def test_observation_under_token_budget(self) -> None:
        # The observation should stay well under ~600 words (rough budget).
        sess = FakeSessionLike(
            character=FakeCharacter("Aria", "Elf", "Wizard", 1, 15, 15, 13),
            location=FakeLocation("Cave", {"north": "deep", "south": "exit"}),
            inventory_items=[f"Item_{i}" for i in range(20)],
            equipped={},
        )
        obs = build_observation(
            turn=1,
            session=sess,
            last_actions=[],
            last_narration="",
        )
        assert len(obs.split()) < 600
```

- [ ] **Step 2: Verify the test fails**

Run: `uv run pytest tests/simulation/tests/test_agent.py -v`
Expected: ImportError on `tests.simulation.agent`.

- [ ] **Step 3: Implement build_observation**

Write `tests/simulation/agent.py` (initial version):

```python
"""AutonomousAgent — observes game state and chooses actions via the 4b LLM."""

from __future__ import annotations

from typing import Any


def build_observation(
    *,
    turn: int,
    session: Any,
    last_actions: list[str],
    last_narration: str,
) -> str:
    """Build a compact text observation for the agent prompt.

    Pulls from the session-like object: character, location, inventory, combat.
    """
    char = session.character
    loc = session.location
    lines: list[str] = []
    lines.append(f"TURN {turn}")
    lines.append(
        f"You play: {char.name} ({char.race}, {char.char_class}, lvl {char.level}, "
        f"HP {char.hp}/{char.max_hp}, AC {char.ac})"
    )
    exits_str = (
        ", ".join(f"{d} → {tgt}" for d, tgt in loc.exits.items()) if loc.exits else "none"
    )
    lines.append(f"Location: {loc.name}. Exits: {exits_str}")

    equipped = getattr(session, "equipped", {}) or {}
    if equipped:
        equipped_str = ", ".join(f"{slot}: {item}" for slot, item in equipped.items())
        lines.append(f"Equipped: {equipped_str}")

    inv = getattr(session, "inventory_items", []) or []
    if inv:
        lines.append("Inventory: " + ", ".join(inv[:15]))

    if session.combat_active and session.combat is not None:
        lines.append("Combat: IN COMBAT, your turn")
        for enemy in session.combat.enemies:
            ratio = enemy.hp / enemy.max_hp if enemy.max_hp else 1.0
            bloodied = " (BLOODIED)" if ratio < 0.5 else ""
            lines.append(
                f"  - {enemy.name}: HP {enemy.hp}/{enemy.max_hp} AC {enemy.ac} "
                f"zone \"{enemy.zone}\"{bloodied}"
            )
    else:
        lines.append("Combat: not in combat")

    npcs = getattr(session, "npcs_present", []) or []
    if npcs:
        lines.append("NPCs present: " + ", ".join(npcs))
    else:
        lines.append("NPCs present: none")

    if last_actions:
        lines.append("Last 3 turns: " + ", ".join(last_actions[-3:]))
    else:
        lines.append("Last 3 turns: -")

    if last_narration:
        snippet = last_narration.strip().replace("\n", " ")[:200]
        lines.append(f'Last narration: "{snippet}"')

    return "\n".join(lines)
```

- [ ] **Step 4: Run the tests until they pass**

Run: `uv run pytest tests/simulation/tests/test_agent.py -v`
Expected: all 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/simulation/agent.py tests/simulation/tests/test_agent.py
git commit -m "feat(sim): AutonomousAgent.build_observation"
```

---

## Task 17: AutonomousAgent — decide + retry

**Files:**
- Modify: `tests/simulation/agent.py`
- Modify: `tests/simulation/tests/test_agent.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/simulation/tests/test_agent.py`:

```python
from unittest.mock import MagicMock

from tests.simulation.agent import AutonomousAgent


class TestAutonomousAgentDecide:
    def test_valid_intent_parsed(self) -> None:
        # Mock the LLM client to return a valid intent JSON.
        client = MagicMock()
        client.chat_json.return_value = {
            "reasoning": "look around first",
            "action": "look",
            "args": {},
            "raw_text": None,
        }
        agent = AutonomousAgent(client=client, model="qwen3.5:4b")
        intent = agent.decide(observation="TURN 1\n...")
        assert intent.action == "look"
        assert intent.reasoning == "look around first"
        client.chat_json.assert_called_once()

    def test_invalid_intent_retries_then_returns(self) -> None:
        client = MagicMock()
        # First two calls return garbage, third returns valid.
        client.chat_json.side_effect = [
            {"reasoning": "x", "action": "dance"},  # invalid action
            {"reasoning": "y", "action": "free_form"},  # missing raw_text
            {"reasoning": "z", "action": "look", "args": {}, "raw_text": None},
        ]
        agent = AutonomousAgent(client=client, model="qwen3.5:4b", max_retries=3)
        intent = agent.decide(observation="TURN 1\n...")
        assert intent.action == "look"
        assert client.chat_json.call_count == 3

    def test_exhausted_retries_falls_back_to_safe_default(self) -> None:
        client = MagicMock()
        client.chat_json.return_value = {"action": "dance"}  # always invalid
        agent = AutonomousAgent(client=client, model="qwen3.5:4b", max_retries=2)
        intent = agent.decide(observation="TURN 1\nCombat: not in combat")
        assert intent.action == "look"  # safe default out of combat
        assert intent.reasoning.startswith("fallback")

    def test_exhausted_retries_in_combat_falls_back_to_defend(self) -> None:
        client = MagicMock()
        client.chat_json.return_value = {"action": "dance"}
        agent = AutonomousAgent(client=client, model="qwen3.5:4b", max_retries=2)
        intent = agent.decide(observation="TURN 1\nCombat: IN COMBAT")
        assert intent.action == "defend"
```

- [ ] **Step 2: Verify the test fails**

Run: `uv run pytest tests/simulation/tests/test_agent.py::TestAutonomousAgentDecide -v`
Expected: ImportError on `AutonomousAgent`.

- [ ] **Step 3: Implement AutonomousAgent.decide with retry**

Append to `tests/simulation/agent.py`:

```python
import json
import logging
from pathlib import Path

from pydantic import ValidationError

from tests.simulation.records import AgentIntent

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load_system_prompt() -> str:
    return (_PROMPTS_DIR / "agent_system.txt").read_text(encoding="utf-8")


def _load_few_shots() -> dict[str, list[dict]]:
    return json.loads((_PROMPTS_DIR / "few_shots.json").read_text(encoding="utf-8"))


class AutonomousAgent:
    """Calls the 4b LLM to decide a single AgentIntent per turn.

    On invalid JSON or invalid AgentIntent, retries up to ``max_retries`` times,
    then falls back to a safe default ('look' out of combat, 'defend' in combat).
    """

    def __init__(
        self,
        *,
        client: Any,
        model: str = "qwen3.5:4b",
        max_retries: int = 3,
        temperature: float = 0.3,
    ) -> None:
        self.client = client
        self.model = model
        self.max_retries = max_retries
        self.temperature = temperature
        self._system_prompt = _load_system_prompt()
        self._few_shots = _load_few_shots()

    def _build_messages(
        self,
        observation: str,
        corrective_hint: str | None = None,
    ) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = [
            {"role": "system", "content": self._system_prompt},
        ]
        # One few-shot per context — exploration as a baseline; LLM generalizes.
        for ex in self._few_shots.get("exploration", [])[:1]:
            messages.append({"role": "user", "content": ex["observation"]})
            messages.append(
                {"role": "assistant", "content": json.dumps(ex["intent"])}
            )
        for ex in self._few_shots.get("combat", [])[:1]:
            messages.append({"role": "user", "content": ex["observation"]})
            messages.append(
                {"role": "assistant", "content": json.dumps(ex["intent"])}
            )
        user_content = observation
        if corrective_hint:
            user_content = f"{observation}\n\n[CORRECTION] {corrective_hint}"
        messages.append({"role": "user", "content": user_content})
        return messages

    def decide(self, observation: str) -> AgentIntent:
        """Return a valid AgentIntent, retrying or falling back as needed."""
        hint: str | None = None
        last_err: str | None = None
        for attempt in range(self.max_retries):
            messages = self._build_messages(observation, corrective_hint=hint)
            try:
                raw = self.client.chat_json(
                    model=self.model,
                    messages=messages,
                    temperature=self.temperature,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("LLM call failed attempt=%d: %s", attempt, e)
                hint = f"Previous response could not be parsed. Return strict JSON."
                last_err = str(e)
                continue
            try:
                # raw is dict-like (already parsed JSON) or string — normalize.
                if isinstance(raw, str):
                    raw = json.loads(raw)
                intent = AgentIntent.model_validate(raw)
                return intent
            except (ValidationError, ValueError, json.JSONDecodeError) as e:
                last_err = str(e)
                hint = (
                    "Your previous response was invalid. Return EXACTLY one JSON "
                    "object matching the schema. Errors: " + last_err[:200]
                )
                continue
        logger.warning(
            "Agent exhausted %d retries (last_err=%s) — falling back",
            self.max_retries,
            last_err,
        )
        return self._safe_fallback(observation, reason=last_err or "exhausted_retries")

    @staticmethod
    def _safe_fallback(observation: str, *, reason: str) -> AgentIntent:
        in_combat = "IN COMBAT" in observation
        if in_combat:
            return AgentIntent(
                reasoning=f"fallback: {reason[:100]}",
                action="defend",
                args={},
                raw_text=None,
            )
        return AgentIntent(
            reasoning=f"fallback: {reason[:100]}",
            action="look",
            args={},
            raw_text=None,
        )
```

- [ ] **Step 4: Run the tests until they pass**

Run: `uv run pytest tests/simulation/tests/test_agent.py -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/simulation/agent.py tests/simulation/tests/test_agent.py
git commit -m "feat(sim): AutonomousAgent.decide with retry + safe fallback"
```

---

## Task 18: AutonomousAgent — legality validator + anti-deadlock

**Files:**
- Modify: `tests/simulation/agent.py`
- Modify: `tests/simulation/tests/test_agent.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/simulation/tests/test_agent.py`:

```python
from tests.simulation.agent import is_legal


class FakeStateForLegality:
    def __init__(self, **kwargs) -> None:
        self.combat_active = kwargs.get("combat_active", False)
        self.living_enemies = kwargs.get("living_enemies", [])
        self.location_exits = kwargs.get("location_exits", [])
        self.inventory_items = kwargs.get("inventory_items", [])
        self.consumable_items = kwargs.get("consumable_items", [])
        self.spellbook = kwargs.get("spellbook", [])
        self.mana = kwargs.get("mana", 10)


class TestIsLegal:
    def test_attack_legal_in_combat_with_valid_target(self) -> None:
        state = FakeStateForLegality(combat_active=True, living_enemies=["Goblin_1"])
        intent = AgentIntent(
            reasoning="r", action="attack", args={"target": "Goblin_1"}, raw_text=None
        )
        legal, reason = is_legal(intent, state)
        assert legal is True

    def test_attack_illegal_out_of_combat(self) -> None:
        state = FakeStateForLegality(combat_active=False)
        intent = AgentIntent(
            reasoning="r", action="attack", args={"target": "Goblin_1"}, raw_text=None
        )
        legal, reason = is_legal(intent, state)
        assert legal is False
        assert reason and "combat" in reason.lower()

    def test_move_illegal_in_combat(self) -> None:
        state = FakeStateForLegality(combat_active=True, location_exits=["north"])
        intent = AgentIntent(
            reasoning="r", action="move", args={"direction": "north"}, raw_text=None
        )
        legal, reason = is_legal(intent, state)
        assert legal is False

    def test_move_legal_with_valid_direction(self) -> None:
        state = FakeStateForLegality(combat_active=False, location_exits=["north"])
        intent = AgentIntent(
            reasoning="r", action="move", args={"direction": "north"}, raw_text=None
        )
        legal, reason = is_legal(intent, state)
        assert legal is True

    def test_use_item_requires_consumable(self) -> None:
        state = FakeStateForLegality(
            inventory_items=["Quarterstaff"], consumable_items=[]
        )
        intent = AgentIntent(
            reasoning="r",
            action="use_item",
            args={"item": "Quarterstaff"},
            raw_text=None,
        )
        legal, reason = is_legal(intent, state)
        assert legal is False

    def test_use_item_legal_consumable(self) -> None:
        state = FakeStateForLegality(
            inventory_items=["Healing potion"], consumable_items=["Healing potion"]
        )
        intent = AgentIntent(
            reasoning="r",
            action="use_item",
            args={"item": "Healing potion"},
            raw_text=None,
        )
        legal, reason = is_legal(intent, state)
        assert legal is True

    def test_free_form_legal_with_raw_text(self) -> None:
        state = FakeStateForLegality()
        intent = AgentIntent(
            reasoning="r", action="free_form", args={}, raw_text="je fouille le coffre"
        )
        legal, _ = is_legal(intent, state)
        assert legal is True

    def test_look_always_legal(self) -> None:
        state = FakeStateForLegality()
        intent = AgentIntent(reasoning="r", action="look", args={}, raw_text=None)
        legal, _ = is_legal(intent, state)
        assert legal is True


class TestAntiDeadlockHint:
    def test_repeated_action_triggers_hint(self) -> None:
        client = MagicMock()
        client.chat_json.return_value = {
            "reasoning": "r",
            "action": "look",
            "args": {},
            "raw_text": None,
        }
        agent = AutonomousAgent(client=client, model="qwen3.5:4b")
        # Simulate 4 prior identical look intents → hint should be injected
        history = [{"intent_action": "look", "intent_args": {}}] * 4
        intent = agent.decide(observation="TURN 5\n...", history=history)
        # Check that the messages included the corrective hint
        call_args = client.chat_json.call_args
        messages = call_args.kwargs.get("messages") if call_args.kwargs else call_args.args[1]
        last = messages[-1]["content"]
        assert "repeating" in last.lower() or "vary" in last.lower() or "differ" in last.lower()
```

Note: this requires `decide` to accept an optional `history` argument. We'll update the signature.

- [ ] **Step 2: Verify the test fails**

Run: `uv run pytest tests/simulation/tests/test_agent.py::TestIsLegal -v`
Expected: ImportError on `is_legal`.

- [ ] **Step 3: Implement legality + anti-deadlock**

Append to `tests/simulation/agent.py`:

```python
def is_legal(intent: AgentIntent, state: Any) -> tuple[bool, str | None]:
    """Check whether the intent is legal given the current game state.

    Returns (True, None) if legal, (False, reason) otherwise.
    """
    action = intent.action
    args = intent.args

    if action == "attack":
        if not state.combat_active:
            return False, "attack is only legal in combat"
        target = args.get("target")
        if not target or target not in getattr(state, "living_enemies", []):
            return False, f"target '{target}' is not a living enemy"
        return True, None

    if action == "cast_spell":
        spell = args.get("spell")
        if spell not in getattr(state, "spellbook", []):
            return False, f"spell '{spell}' not in spellbook"
        if state.mana <= 0:
            return False, "insufficient mana"
        return True, None

    if action == "move":
        if state.combat_active:
            return False, "cannot move during combat"
        direction = args.get("direction")
        if direction not in getattr(state, "location_exits", []):
            return False, f"direction '{direction}' is not a valid exit"
        return True, None

    if action in {"equip", "unequip"}:
        item = args.get("item")
        if action == "equip" and item not in getattr(state, "inventory_items", []):
            return False, f"item '{item}' not in inventory"
        return True, None

    if action == "use_item":
        item = args.get("item")
        if item not in getattr(state, "consumable_items", []):
            return False, f"'{item}' is not a usable consumable"
        return True, None

    if action == "free_form":
        if not intent.raw_text or not intent.raw_text.strip():
            return False, "free_form requires non-empty raw_text"
        if len(intent.raw_text) > 200:
            return False, "raw_text too long (>200 chars)"
        return True, None

    # look, talk, search, defend, flee, wait — always legal at this layer.
    return True, None
```

Now modify `AutonomousAgent.decide` to accept an optional `history` parameter and inject an anti-deadlock hint when the last 4 turns were the same action+args. Replace the body of `decide`:

```python
    def decide(
        self,
        observation: str,
        history: list[dict[str, Any]] | None = None,
    ) -> AgentIntent:
        """Return a valid AgentIntent, retrying or falling back as needed."""
        anti_deadlock_hint = self._anti_deadlock_hint(history or [])
        hint: str | None = anti_deadlock_hint
        last_err: str | None = None
        for attempt in range(self.max_retries):
            messages = self._build_messages(observation, corrective_hint=hint)
            try:
                raw = self.client.chat_json(
                    model=self.model,
                    messages=messages,
                    temperature=self.temperature,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("LLM call failed attempt=%d: %s", attempt, e)
                hint = (
                    "Previous response could not be parsed. Return strict JSON. "
                    + (anti_deadlock_hint or "")
                ).strip()
                last_err = str(e)
                continue
            try:
                if isinstance(raw, str):
                    raw = json.loads(raw)
                intent = AgentIntent.model_validate(raw)
                return intent
            except (ValidationError, ValueError, json.JSONDecodeError) as e:
                last_err = str(e)
                hint = (
                    "Your previous response was invalid. Return EXACTLY one JSON "
                    "object matching the schema. Errors: "
                    + last_err[:200]
                    + " "
                    + (anti_deadlock_hint or "")
                ).strip()
                continue
        logger.warning(
            "Agent exhausted %d retries (last_err=%s) — falling back",
            self.max_retries,
            last_err,
        )
        return self._safe_fallback(observation, reason=last_err or "exhausted_retries")

    @staticmethod
    def _anti_deadlock_hint(history: list[dict[str, Any]]) -> str | None:
        """Inject a hint if the last 4 turns chose the same (action, args)."""
        if len(history) < 4:
            return None
        last_four = history[-4:]
        first = (last_four[0].get("intent_action"), last_four[0].get("intent_args"))
        if all(
            (h.get("intent_action"), h.get("intent_args")) == first for h in last_four
        ):
            return (
                "You are repeating the same action 4 turns in a row. "
                "Pick a DIFFERENT action this turn to vary the play."
            )
        return None
```

- [ ] **Step 4: Run the tests until they pass**

Run: `uv run pytest tests/simulation/tests/test_agent.py -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/simulation/agent.py tests/simulation/tests/test_agent.py
git commit -m "feat(sim): AutonomousAgent legality validator + anti-deadlock hint"
```

---

## Task 19: GameDriver

**Files:**
- Create: `tests/simulation/driver.py`
- Create: `tests/simulation/tests/test_driver.py`
- Create: `tests/simulation/conftest.py` (pytest fixtures for the simulator suite)

- [ ] **Step 1: Write the conftest with shared fixtures**

Write `tests/simulation/conftest.py`:

```python
"""Shared fixtures for the simulator's own test suite."""

from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from ai.client import OllamaClient
from db.database import Base
from tests.scenarios.scenario_runner import ScenarioRunner

OLLAMA_BASE = "http://localhost:11434"
TAGS_URL = f"{OLLAMA_BASE}/api/tags"


@pytest.fixture()
def in_memory_db_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine)
    engine.dispose()


@pytest.fixture()
def ollama_mocked(httpx_mock: HTTPXMock) -> OllamaClient:
    httpx_mock.add_response(url=TAGS_URL, json={"models": []})
    return OllamaClient(simulation_mode=True)


@pytest.fixture()
def scenario_ai(in_memory_db_factory, ollama_mocked) -> ScenarioRunner:
    """ScenarioRunner with AI components wired (real Interpreter/Narrator)."""
    return ScenarioRunner(
        in_memory_db_factory, ai_enabled=True, ollama_client=ollama_mocked
    )
```

- [ ] **Step 2: Write the failing test**

Write `tests/simulation/tests/test_driver.py`:

```python
"""Tests for tests/simulation/driver.py — GameDriver."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from tests.simulation.driver import GameDriver
from tests.simulation.records import AgentIntent


@pytest.mark.asyncio
async def test_driver_routes_look_to_runner(scenario_ai) -> None:
    await scenario_ai.start_campaign(theme="Test", players=1)
    scenario_ai.look = AsyncMock(return_value=None)
    driver = GameDriver(scenario_runner=scenario_ai)
    intent = AgentIntent(reasoning="r", action="look", args={}, raw_text=None)
    outcome = await driver.execute(intent)
    scenario_ai.look.assert_awaited_once()
    assert outcome.error is None


@pytest.mark.asyncio
async def test_driver_routes_attack(scenario_ai) -> None:
    await scenario_ai.start_campaign(theme="Test", players=1)
    scenario_ai.attack = AsyncMock(return_value=None)
    driver = GameDriver(scenario_runner=scenario_ai)
    intent = AgentIntent(
        reasoning="r", action="attack", args={"target": "Gobelin"}, raw_text=None
    )
    outcome = await driver.execute(intent)
    scenario_ai.attack.assert_awaited_once_with(target="Gobelin", player_idx=0)
    assert outcome.error is None


@pytest.mark.asyncio
async def test_driver_captures_error(scenario_ai) -> None:
    await scenario_ai.start_campaign(theme="Test", players=1)
    scenario_ai.look = AsyncMock(side_effect=RuntimeError("boom"))
    driver = GameDriver(scenario_runner=scenario_ai)
    intent = AgentIntent(reasoning="r", action="look", args={}, raw_text=None)
    outcome = await driver.execute(intent)
    assert outcome.error is not None
    assert "boom" in outcome.error


@pytest.mark.asyncio
async def test_driver_records_timing(scenario_ai) -> None:
    await scenario_ai.start_campaign(theme="Test", players=1)
    scenario_ai.look = AsyncMock(return_value=None)
    driver = GameDriver(scenario_runner=scenario_ai)
    intent = AgentIntent(reasoning="r", action="look", args={}, raw_text=None)
    outcome = await driver.execute(intent)
    assert outcome.timing_ms.engine >= 0
```

- [ ] **Step 3: Verify the test fails**

Run: `uv run pytest tests/simulation/tests/test_driver.py -v`
Expected: ImportError on `tests.simulation.driver`.

- [ ] **Step 4: Implement GameDriver**

Write `tests/simulation/driver.py`:

```python
"""GameDriver — translates AgentIntent into ScenarioRunner cog calls.

Captures narration, action_resolved, and timing into a TurnOutcome.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from tests.simulation.records import AgentIntent, LLMTimings, TurnOutcome

logger = logging.getLogger(__name__)


class GameDriver:
    def __init__(self, *, scenario_runner: Any, player_idx: int = 0) -> None:
        self.runner = scenario_runner
        self.player_idx = player_idx

    async def execute(self, intent: AgentIntent) -> TurnOutcome:
        """Dispatch the intent to the appropriate ScenarioRunner method.

        Captures errors and timing; never re-raises (the SimulationRunner
        decides whether to bail).
        """
        start = time.perf_counter()
        error: str | None = None
        narration: str = ""
        action_resolved: dict[str, Any] = {"action": intent.action, "args": intent.args}
        try:
            await self._dispatch(intent)
            narration = self._extract_narration()
        except Exception as e:  # noqa: BLE001
            logger.exception("GameDriver dispatch failed: %s", e)
            error = f"{type(e).__name__}: {e}"
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return TurnOutcome(
            narration=narration,
            action_resolved=action_resolved,
            error=error,
            timing_ms=LLMTimings(
                agent=0,  # filled by SimulationRunner
                interpreter=0,
                engine=elapsed_ms,
                narrator=0,
            ),
        )

    async def _dispatch(self, intent: AgentIntent) -> None:
        action = intent.action
        args = intent.args
        idx = self.player_idx
        r = self.runner
        if action == "look":
            await r.look()
        elif action == "attack":
            await r.attack(target=args["target"], player_idx=idx)
        elif action == "cast_spell":
            await r.cast_spell(spell=args["spell"], target=args.get("target", ""), player_idx=idx)
        elif action == "defend":
            await r.defend(player_idx=idx)
        elif action == "flee":
            await r.flee(player_idx=idx)
        elif action == "move":
            await r.move(direction=args["direction"])
        elif action == "talk":
            await r.talk(npc=args["npc"])
        elif action == "search":
            await r.search(target=args.get("target", ""))
        elif action == "equip":
            await r.equip(item=args["item"], slot=args.get("slot", "main_hand"), player_idx=idx)
        elif action == "unequip":
            await r.unequip(slot=args["slot"], player_idx=idx)
        elif action == "use_item":
            await r.use_item(item=args["item"], player_idx=idx)
        elif action == "free_form":
            # If runner exposes a free-form action method, use it; else fall through.
            free_form = getattr(r, "free_form_action", None)
            if free_form is None:
                raise NotImplementedError(
                    "ScenarioRunner.free_form_action is not implemented yet"
                )
            await free_form(text=intent.raw_text or "", player_idx=idx)
        elif action == "wait":
            return  # no-op
        else:
            raise ValueError(f"Unknown action {action!r}")

    def _extract_narration(self) -> str:
        """Pull the narration text from the last captured embed/message."""
        last = self.runner.last_response
        if last is None:
            return ""
        if last.embed is not None and last.embed.description:
            return str(last.embed.description)
        if last.content:
            return str(last.content)
        return ""
```

- [ ] **Step 5: Run the tests until they pass**

Run: `uv run pytest tests/simulation/tests/test_driver.py -v`
Expected: all 4 tests pass.

- [ ] **Step 6: Commit**

```bash
git add tests/simulation/driver.py tests/simulation/tests/test_driver.py tests/simulation/conftest.py
git commit -m "feat(sim): GameDriver — routes AgentIntent to ScenarioRunner"
```

---

## Task 20: Recorder — JSONL + runtime line

**Files:**
- Create: `tests/simulation/recorder.py` (initial — append + runtime print)
- Create: `tests/simulation/tests/test_recorder.py`

- [ ] **Step 1: Write the failing test**

Write `tests/simulation/tests/test_recorder.py`:

```python
"""Tests for tests/simulation/recorder.py — Recorder."""

from __future__ import annotations

import json
from pathlib import Path

from tests.simulation.recorder import Recorder
from tests.simulation.records import (
    AgentIntent,
    IncoherenceAlert,
    LLMTimings,
    TurnOutcome,
    TurnRecord,
)


def _sample_record(turn: int = 1, alerts: list[IncoherenceAlert] | None = None) -> TurnRecord:
    return TurnRecord(
        turn=turn,
        ts="2026-05-25T16:42:01Z",
        observation="TURN 1\nYou play: Aria",
        intent=AgentIntent(reasoning="look", action="look", args={}, raw_text=None),
        outcome=TurnOutcome(
            narration="Vous voyez une grotte.",
            action_resolved={"type": "look"},
            error=None,
            timing_ms=LLMTimings(agent=100, interpreter=200, engine=5, narrator=1500),
        ),
        diff={},
        alerts=alerts or [],
        agent_retries=0,
    )


class TestRecorderJsonl:
    def test_append_writes_one_line_per_record(self, tmp_path: Path) -> None:
        recorder = Recorder(run_dir=tmp_path)
        recorder.append(_sample_record(turn=1))
        recorder.append(_sample_record(turn=2))
        transcript = (tmp_path / "transcript.jsonl").read_text()
        lines = [line for line in transcript.splitlines() if line]
        assert len(lines) == 2
        for line in lines:
            data = json.loads(line)
            assert "turn" in data

    def test_runtime_line_format(self, tmp_path: Path, capsys) -> None:
        recorder = Recorder(run_dir=tmp_path)
        record = _sample_record(turn=3)
        recorder.append(record)
        out = capsys.readouterr().out
        assert "[T03" in out
        assert "look" in out

    def test_alert_runtime_line(self, tmp_path: Path, capsys) -> None:
        recorder = Recorder(run_dir=tmp_path)
        alert = IncoherenceAlert(
            severity="hard",
            category="dead_npc_speaks",
            turn=3,
            rule="R1.npc_status",
            narration_snippet="Garm sourit.",
            expected="Garm dead",
        )
        recorder.append(_sample_record(turn=3, alerts=[alert]))
        out = capsys.readouterr().out
        assert "alerts:1" in out
        assert "R1.npc_status" in out
```

- [ ] **Step 2: Verify the test fails**

Run: `uv run pytest tests/simulation/tests/test_recorder.py -v`
Expected: ImportError on `tests.simulation.recorder`.

- [ ] **Step 3: Implement Recorder.append + runtime line**

Write `tests/simulation/recorder.py`:

```python
"""Recorder — writes transcript.jsonl + final report.md + runtime stdout lines."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from tests.simulation.records import IncoherenceAlert, TurnRecord


class Recorder:
    def __init__(self, *, run_dir: Path) -> None:
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.transcript_path = self.run_dir / "transcript.jsonl"
        # Truncate on open
        self.transcript_path.write_text("")
        self._records: list[TurnRecord] = []

    def append(self, record: TurnRecord) -> None:
        """Append a TurnRecord to transcript.jsonl AND print runtime line."""
        with self.transcript_path.open("a", encoding="utf-8") as f:
            f.write(record.model_dump_json() + "\n")
        self._records.append(record)
        self._print_runtime_line(record)
        for alert in record.alerts:
            if alert.severity == "hard":
                self._print_alert_detail(alert)

    @property
    def records(self) -> list[TurnRecord]:
        return list(self._records)

    @staticmethod
    def _print_runtime_line(record: TurnRecord) -> None:
        secs = (
            record.outcome.timing_ms.agent
            + record.outcome.timing_ms.interpreter
            + record.outcome.timing_ms.engine
            + record.outcome.timing_ms.narrator
        ) / 1000.0
        intent = record.intent
        action_str = intent.action
        if intent.args:
            args_str = ",".join(f"{k}={v}" for k, v in intent.args.items())
            action_str += f"({args_str})"
        elif intent.raw_text:
            action_str = f"@bot {intent.raw_text[:40]}"

        outcome_str = "ok"
        if record.outcome.error:
            outcome_str = f"ERR:{record.outcome.error[:30]}"

        alerts_str = f"alerts:{len(record.alerts)}"
        if record.alerts:
            rules = ",".join(sorted({a.rule for a in record.alerts}))
            alerts_str += f"  ⚠ {rules}"

        line = (
            f"[T{record.turn:02d} {secs:>4.1f}s] {action_str:<35} "
            f"→ {outcome_str:<12} {alerts_str}"
        )
        print(line)

    @staticmethod
    def _print_alert_detail(alert: IncoherenceAlert) -> None:
        print(
            f"   ⚠ {alert.rule} ({alert.severity}): {alert.narration_snippet}",
            file=sys.stderr,
        )
        print(f"     expected: {alert.expected}", file=sys.stderr)
```

- [ ] **Step 4: Run the tests until they pass**

Run: `uv run pytest tests/simulation/tests/test_recorder.py -v`
Expected: all 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/simulation/recorder.py tests/simulation/tests/test_recorder.py
git commit -m "feat(sim): Recorder.append — JSONL writer + runtime line"
```

---

## Task 21: Recorder — finalize (report.md, final_state.json, config.json)

**Files:**
- Modify: `tests/simulation/recorder.py`
- Modify: `tests/simulation/tests/test_recorder.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/simulation/tests/test_recorder.py`:

```python
class TestRecorderFinalize:
    def test_finalize_writes_report_md(self, tmp_path: Path) -> None:
        recorder = Recorder(run_dir=tmp_path)
        recorder.append(_sample_record(turn=1))
        recorder.append(_sample_record(turn=2))
        recorder.finalize(
            outcome_status="max_turns_reached",
            wall_time_s=120.5,
            config={"seed": 42, "policy": "balanced", "max_turns": 30},
            final_state={"character_hp": 12, "location": "Cave deep"},
        )
        report = (tmp_path / "report.md").read_text()
        assert "Outcome" in report
        assert "max_turns_reached" in report
        assert "Turn 1" in report
        assert "Turn 2" in report

    def test_finalize_writes_final_state_and_config(self, tmp_path: Path) -> None:
        recorder = Recorder(run_dir=tmp_path)
        recorder.append(_sample_record(turn=1))
        recorder.finalize(
            outcome_status="max_turns_reached",
            wall_time_s=10.0,
            config={"seed": 7},
            final_state={"hp": 15},
        )
        final = json.loads((tmp_path / "final_state.json").read_text())
        cfg = json.loads((tmp_path / "config.json").read_text())
        assert final["hp"] == 15
        assert cfg["seed"] == 7

    def test_alerts_summarized_in_report(self, tmp_path: Path) -> None:
        recorder = Recorder(run_dir=tmp_path)
        alert = IncoherenceAlert(
            severity="hard",
            category="dead_npc_speaks",
            turn=1,
            rule="R1.npc_status",
            narration_snippet="Garm sourit.",
            expected="Garm dead",
        )
        recorder.append(_sample_record(turn=1, alerts=[alert]))
        recorder.finalize(
            outcome_status="max_turns_reached",
            wall_time_s=5.0,
            config={},
            final_state={},
        )
        report = (tmp_path / "report.md").read_text()
        assert "R1.npc_status" in report
        assert "Garm sourit" in report
```

- [ ] **Step 2: Verify the test fails**

Run: `uv run pytest tests/simulation/tests/test_recorder.py::TestRecorderFinalize -v`
Expected: AttributeError or method-not-found on `recorder.finalize`.

- [ ] **Step 3: Implement Recorder.finalize**

Append to `tests/simulation/recorder.py`:

```python
    def finalize(
        self,
        *,
        outcome_status: str,
        wall_time_s: float,
        config: dict,
        final_state: dict,
    ) -> None:
        """Write report.md, final_state.json, and config.json."""
        (self.run_dir / "final_state.json").write_text(
            json.dumps(final_state, indent=2, default=str),
            encoding="utf-8",
        )
        (self.run_dir / "config.json").write_text(
            json.dumps(config, indent=2, default=str),
            encoding="utf-8",
        )
        report = self._build_report(
            outcome_status=outcome_status, wall_time_s=wall_time_s
        )
        (self.run_dir / "report.md").write_text(report, encoding="utf-8")

    def _build_report(self, *, outcome_status: str, wall_time_s: float) -> str:
        n_turns = len(self._records)
        all_alerts: list[IncoherenceAlert] = [
            a for rec in self._records for a in rec.alerts
        ]
        hard = [a for a in all_alerts if a.severity == "hard"]
        soft = [a for a in all_alerts if a.severity == "soft"]
        drift = [a for a in all_alerts if a.severity == "drift"]

        lines = [
            "# Simulation Run Report",
            "",
            "## Outcome",
            f"- Status: **{outcome_status}**",
            f"- Wall time: {wall_time_s:.1f} s",
            f"- Turns: {n_turns}",
            f"- Alerts: {len(all_alerts)} (hard={len(hard)}, soft={len(soft)}, drift={len(drift)})",
            "",
        ]

        if all_alerts:
            lines.append("## Alerts")
            lines.append("")
            lines.append("| Turn | Severity | Rule | Snippet |")
            lines.append("|------|----------|------|---------|")
            for a in all_alerts:
                snippet = a.narration_snippet.replace("|", "\\|").replace("\n", " ")[:80]
                lines.append(f"| {a.turn} | {a.severity} | {a.rule} | {snippet} |")
            lines.append("")

        lines.append("## Turn-by-turn")
        for rec in self._records:
            intent_args = (
                "(" + ",".join(f"{k}={v}" for k, v in rec.intent.args.items()) + ")"
                if rec.intent.args
                else ""
            )
            lines.extend(
                [
                    f"### Turn {rec.turn} — {rec.intent.action}{intent_args}",
                    f"**Intent reasoning:** {rec.intent.reasoning}",
                    "",
                    f"**Narration:** {rec.outcome.narration.strip()[:500]}",
                    "",
                ]
            )
            if rec.diff:
                lines.append("**Diff:**")
                for path, change in rec.diff.items():
                    lines.append(f"- `{path}`: {change[0]} → {change[1]}")
                lines.append("")
            if rec.alerts:
                lines.append("**Alerts this turn:**")
                for a in rec.alerts:
                    lines.append(f"- `{a.rule}` ({a.severity}): {a.expected}")
                lines.append("")
        return "\n".join(lines)
```

- [ ] **Step 4: Run the tests until they pass**

Run: `uv run pytest tests/simulation/tests/test_recorder.py -v`
Expected: all 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/simulation/recorder.py tests/simulation/tests/test_recorder.py
git commit -m "feat(sim): Recorder.finalize — report.md + final_state + config"
```

---

## Task 22: SimulationRunner orchestrator

**Files:**
- Create: `tests/simulation/runner.py`
- Create: `tests/simulation/tests/test_runner_e2e_mocked_llm.py`

- [ ] **Step 1: Write the failing test (end-to-end with mocked LLM)**

Write `tests/simulation/tests/test_runner_e2e_mocked_llm.py`:

```python
"""End-to-end test of SimulationRunner with a fully mocked LLM."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.simulation.records import AgentIntent
from tests.simulation.runner import SimulationConfig, SimulationRunner


@pytest.fixture()
def mock_agent() -> Any:
    """An agent that always returns 'look'."""
    agent = MagicMock()
    agent.decide.return_value = AgentIntent(
        reasoning="test",
        action="look",
        args={},
        raw_text=None,
    )
    return agent


@pytest.fixture()
def mock_checker() -> Any:
    checker = MagicMock()
    checker.check.return_value = []
    return checker


@pytest.fixture()
def stub_driver() -> Any:
    """A driver whose execute returns a TurnOutcome with no error."""
    from tests.simulation.records import LLMTimings, TurnOutcome

    driver = MagicMock()
    driver.execute = AsyncMock(
        return_value=TurnOutcome(
            narration="Vous voyez une grotte.",
            action_resolved={"type": "look"},
            error=None,
            timing_ms=LLMTimings(agent=10, interpreter=20, engine=5, narrator=30),
        )
    )
    return driver


@pytest.mark.asyncio
async def test_runner_executes_max_turns(
    tmp_path: Path, mock_agent, mock_checker, stub_driver
) -> None:
    config = SimulationConfig(
        seed=42, max_turns=3, run_dir=tmp_path, max_wall_time_s=60
    )
    runner = SimulationRunner(
        config=config,
        agent=mock_agent,
        driver=stub_driver,
        checker=mock_checker,
        session_snapshot=lambda: {"hp": 15, "location": "Cave"},
    )
    status = await runner.run()
    assert status == "max_turns_reached"
    assert stub_driver.execute.await_count == 3
    transcript = (tmp_path / "transcript.jsonl").read_text().splitlines()
    assert len(transcript) == 3
    assert (tmp_path / "report.md").exists()


@pytest.mark.asyncio
async def test_runner_stops_on_pipeline_error(
    tmp_path: Path, mock_agent, mock_checker
) -> None:
    from tests.simulation.records import LLMTimings, TurnOutcome

    driver = MagicMock()
    driver.execute = AsyncMock(
        return_value=TurnOutcome(
            narration="",
            action_resolved={},
            error="RuntimeError: boom",
            timing_ms=LLMTimings(agent=0, interpreter=0, engine=0, narrator=0),
        )
    )
    config = SimulationConfig(seed=1, max_turns=10, run_dir=tmp_path, max_wall_time_s=60)
    runner = SimulationRunner(
        config=config,
        agent=mock_agent,
        driver=driver,
        checker=mock_checker,
        session_snapshot=lambda: {},
    )
    status = await runner.run()
    assert status == "pipeline_error"
    assert driver.execute.await_count == 1


@pytest.mark.asyncio
async def test_runner_stops_on_alert_budget(
    tmp_path: Path, mock_agent, stub_driver
) -> None:
    from tests.simulation.records import IncoherenceAlert

    checker = MagicMock()
    alert = IncoherenceAlert(
        severity="hard",
        category="x",
        turn=0,
        rule="R1.test",
        narration_snippet="s",
        expected="e",
    )
    checker.check.return_value = [alert] * 5  # 5 alerts on the first turn

    config = SimulationConfig(
        seed=1, max_turns=10, run_dir=tmp_path, alert_budget=5, max_wall_time_s=60
    )
    runner = SimulationRunner(
        config=config,
        agent=mock_agent,
        driver=stub_driver,
        checker=checker,
        session_snapshot=lambda: {},
    )
    status = await runner.run()
    assert status == "alert_budget_exceeded"


@pytest.mark.asyncio
async def test_runner_stops_on_agent_stuck(
    tmp_path: Path, mock_checker, stub_driver
) -> None:
    agent = MagicMock()
    agent.decide.return_value = AgentIntent(
        reasoning="stuck", action="wait", args={}, raw_text=None
    )
    config = SimulationConfig(
        seed=1, max_turns=10, run_dir=tmp_path, max_wall_time_s=60
    )
    runner = SimulationRunner(
        config=config,
        agent=agent,
        driver=stub_driver,
        checker=mock_checker,
        session_snapshot=lambda: {},
    )
    status = await runner.run()
    assert status == "agent_stuck"
    assert stub_driver.execute.await_count == 3  # 3 waits then exit
```

- [ ] **Step 2: Verify the test fails**

Run: `uv run pytest tests/simulation/tests/test_runner_e2e_mocked_llm.py -v`
Expected: ImportError on `tests.simulation.runner`.

- [ ] **Step 3: Implement SimulationRunner**

Write `tests/simulation/runner.py`:

```python
"""SimulationRunner — orchestrates the autonomous playthrough loop."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tests.simulation.records import AgentIntent, TurnOutcome, TurnRecord
from tests.simulation.recorder import Recorder

logger = logging.getLogger(__name__)


@dataclass
class SimulationConfig:
    seed: int
    max_turns: int = 30
    run_dir: Path = field(default_factory=lambda: Path("tests/simulation/runs/default"))
    max_wall_time_s: int = 600
    alert_budget: int = 5
    policy: str = "balanced"


class SimulationRunner:
    """Drives the loop: agent.decide → driver.execute → checker.check → recorder.append."""

    def __init__(
        self,
        *,
        config: SimulationConfig,
        agent: Any,
        driver: Any,
        checker: Any,
        session_snapshot: Callable[[], dict],
    ) -> None:
        self.config = config
        self.agent = agent
        self.driver = driver
        self.checker = checker
        self.session_snapshot = session_snapshot
        self.recorder = Recorder(run_dir=config.run_dir)
        self._history: list[dict[str, Any]] = []
        self._wait_streak = 0
        self._hard_alert_count = 0

    async def run(self) -> str:
        """Run the loop until a stop criterion fires. Returns the outcome status."""
        start_wall = time.perf_counter()
        outcome_status: str | None = None
        for turn in range(1, self.config.max_turns + 1):
            if time.perf_counter() - start_wall > self.config.max_wall_time_s:
                outcome_status = "wall_time_exceeded"
                break

            observation = self._build_observation(turn)
            intent: AgentIntent = self.agent.decide(observation, history=self._history) \
                if self._agent_accepts_history() \
                else self.agent.decide(observation)

            outcome: TurnOutcome = await self.driver.execute(intent)

            state_after = self.session_snapshot()
            diff: dict[str, list[Any]] = {}  # state diff would be computed here in full impl
            alerts = self.checker.check(
                outcome.narration, state_after, diff=diff, history=self._history
            )
            self._hard_alert_count += sum(1 for a in alerts if a.severity == "hard")

            record = TurnRecord(
                turn=turn,
                ts=datetime.now(timezone.utc).isoformat(),
                observation=observation,
                intent=intent,
                outcome=outcome,
                diff=diff,
                alerts=alerts,
                agent_retries=0,
            )
            self.recorder.append(record)
            self._history.append(
                {
                    "intent_action": intent.action,
                    "intent_args": intent.args,
                    "narration": outcome.narration,
                }
            )

            if outcome.error is not None:
                outcome_status = "pipeline_error"
                break

            if intent.action == "wait":
                self._wait_streak += 1
                if self._wait_streak >= 3:
                    outcome_status = "agent_stuck"
                    break
            else:
                self._wait_streak = 0

            if self._hard_alert_count >= self.config.alert_budget:
                outcome_status = "alert_budget_exceeded"
                break

        if outcome_status is None:
            outcome_status = "max_turns_reached"

        wall_s = time.perf_counter() - start_wall
        self.recorder.finalize(
            outcome_status=outcome_status,
            wall_time_s=wall_s,
            config={
                "seed": self.config.seed,
                "max_turns": self.config.max_turns,
                "policy": self.config.policy,
                "alert_budget": self.config.alert_budget,
                "max_wall_time_s": self.config.max_wall_time_s,
            },
            final_state=self.session_snapshot(),
        )
        return outcome_status

    def _agent_accepts_history(self) -> bool:
        """True iff agent.decide accepts a history kwarg (anti-deadlock)."""
        import inspect

        try:
            sig = inspect.signature(self.agent.decide)
            return "history" in sig.parameters
        except (TypeError, ValueError):
            return False

    def _build_observation(self, turn: int) -> str:
        """Default observation builder — subclasses or callers can override.

        For the E2E mocked test we don't need a real one; the agent is mocked.
        """
        return f"TURN {turn}\n(observation placeholder — wire build_observation in CLI)"
```

- [ ] **Step 4: Run the tests until they pass**

Run: `uv run pytest tests/simulation/tests/test_runner_e2e_mocked_llm.py -v`
Expected: all 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/simulation/runner.py tests/simulation/tests/test_runner_e2e_mocked_llm.py
git commit -m "feat(sim): SimulationRunner orchestrator with stop criteria"
```

---

## Task 23: CLI entry point

**Files:**
- Create: `tests/simulation/__main__.py`

- [ ] **Step 1: Implement the CLI**

Write `tests/simulation/__main__.py`:

```python
"""CLI: uv run python -m tests.simulation [args]

Wires a full SimulationRunner with real Ollama-backed AutonomousAgent + GameDriver
+ IncoherenceChecker + Recorder and runs the loop end-to-end.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from ai.client import OllamaClient
from db.database import Base
from tests.scenarios.scenario_runner import ScenarioRunner
from tests.simulation.agent import AutonomousAgent, build_observation
from tests.simulation.checker import IncoherenceChecker
from tests.simulation.driver import GameDriver
from tests.simulation.runner import SimulationConfig, SimulationRunner

logger = logging.getLogger(__name__)


def _make_run_dir(seed: int) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    p = Path("tests/simulation/runs") / f"{ts}__seed{seed}"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m tests.simulation")
    parser.add_argument("--max-turns", type=int, default=30)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument(
        "--policy",
        choices=["balanced", "combat_focused", "story_focused"],
        default="balanced",
    )
    parser.add_argument("--agent-temp", type=float, default=0.3)
    parser.add_argument("--max-wall-time", type=int, default=600)
    parser.add_argument("--alert-budget", type=int, default=5)
    parser.add_argument(
        "--fail-on", choices=["none", "hard", "any"], default="none"
    )
    parser.add_argument("--batch", type=int, default=1)
    parser.add_argument("--keep-db", action="store_true")
    parser.add_argument("--mock-llm", action="store_true")
    parser.add_argument("--config", type=str, default=None)
    return parser.parse_args()


def _snapshot_from_session(session) -> dict:
    """Build a JSON-serializable dict from the GameSession.

    This is a thin wrapper; full implementations should pull richer state.
    """
    if session is None:
        return {}
    char = session.characters[0] if session.characters else None
    snap = {
        "campaign_id": session.campaign.id if session.campaign else None,
        "location": getattr(session.current_location, "name", None),
        "combat_active": session.combat_state is not None,
    }
    if char is not None:
        snap["character_name"] = char.name
        snap["character_hp"] = char.hp
        snap["character_max_hp"] = char.max_hp
    return snap


async def _run_once(args: argparse.Namespace, seed: int) -> int:
    random.seed(seed)
    run_dir = _make_run_dir(seed)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(run_dir / "system.log", encoding="utf-8"),
            logging.StreamHandler(sys.stderr),
        ],
    )
    logger.info("Starting run seed=%d run_dir=%s", seed, run_dir)

    # In-memory DB per run
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db_factory = sessionmaker(bind=engine)

    client = OllamaClient(simulation_mode=True)
    scenario = ScenarioRunner(
        db_factory, ai_enabled=True, ollama_client=client
    )
    await scenario.start_campaign(theme="Simulation", players=1)
    await scenario.add_player(
        "Aria", race="Elf", class_="Wizard", player_idx=0
    )

    agent = AutonomousAgent(
        client=client, model="qwen3.5:4b", temperature=args.agent_temp
    )
    driver = GameDriver(scenario_runner=scenario)
    checker = IncoherenceChecker()
    config = SimulationConfig(
        seed=seed,
        max_turns=args.max_turns,
        run_dir=run_dir,
        max_wall_time_s=args.max_wall_time,
        alert_budget=args.alert_budget,
        policy=args.policy,
    )

    runner = SimulationRunner(
        config=config,
        agent=agent,
        driver=driver,
        checker=checker,
        session_snapshot=lambda: _snapshot_from_session(scenario.session),
    )

    # Override the default _build_observation with the real one
    def _real_observation(turn: int) -> str:
        sess = scenario.session
        if sess is None:
            return f"TURN {turn}\n(no session)"
        char = sess.characters[0]
        loc = sess.current_location
        return build_observation(
            turn=turn,
            session=type(
                "_S",
                (),
                {
                    "character": char,
                    "location": loc,
                    "inventory_items": [],
                    "equipped": {},
                    "combat_active": sess.combat_state is not None,
                    "combat": sess.combat_state,
                    "npcs_present": [],
                },
            )(),
            last_actions=[h["intent_action"] for h in runner._history[-3:]],
            last_narration=(runner._history[-1]["narration"] if runner._history else ""),
        )

    runner._build_observation = _real_observation  # type: ignore[assignment]

    status = await runner.run()

    # Determine exit code
    exit_code = 0
    all_alerts = [a for rec in runner.recorder.records for a in rec.alerts]
    if args.fail_on == "any" and all_alerts:
        exit_code = 1
    elif args.fail_on == "hard" and any(a.severity == "hard" for a in all_alerts):
        exit_code = 1
    if status in {"pipeline_error", "wall_time_exceeded"}:
        exit_code = max(exit_code, 1)

    if not args.keep_db:
        engine.dispose()
    print(f"DONE: status={status} run_dir={run_dir} exit_code={exit_code}")
    return exit_code


def main() -> int:
    args = _parse_args()
    if args.config:
        cfg = json.loads(Path(args.config).read_text())
        for key, value in cfg.items():
            if hasattr(args, key) and value is not None:
                setattr(args, key, value)

    base_seed = args.seed if args.seed is not None else random.randint(1, 1_000_000)
    code = 0
    for i in range(args.batch):
        seed = base_seed + i
        c = asyncio.run(_run_once(args, seed))
        code = max(code, c)
    return code


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Verify the CLI parses without crashing**

Run: `uv run python -m tests.simulation --help 2>&1 | head -20`
Expected: argparse usage banner with all flags listed; exit 0.

- [ ] **Step 3: Smoke-test with `--mock-llm` placeholder**

Note: actual `--mock-llm` wiring is light here — the test in Task 22 covers mocked end-to-end. The CLI without `--mock-llm` hits real Ollama and is a manual command.

- [ ] **Step 4: Commit**

```bash
git add tests/simulation/__main__.py
git commit -m "feat(sim): CLI entry point — uv run python -m tests.simulation"
```

---

## Task 24: Spec coverage sweep + documentation

**Files:**
- Modify: `tests/simulation/__init__.py` (add docstring)

- [ ] **Step 1: Add a module docstring**

Write `tests/simulation/__init__.py`:

```python
"""Autonomous playthrough simulator.

Headless tool that plays a full RealmAI campaign on its own through the real
Ollama LLM pipeline (Interpreter + Narrator + Story Director + memory), and
emits a deterministic incoherence report.

See:
  - docs/superpowers/specs/2026-05-25-autonomous-playthrough-simulator-design.md
  - docs/superpowers/plans/2026-05-25-autonomous-playthrough-simulator.md

Entry point:
  uv run python -m tests.simulation --max-turns 30 --seed 42
"""
```

- [ ] **Step 2: Run the full suite to confirm nothing is broken**

Run: `uv run pytest tests/ -q 2>&1 | tail -20`
Expected: all tests pass. The new simulator tests count should be ~50+.

- [ ] **Step 3: Run ruff and mypy**

Run: `uv run ruff check tests/simulation/`
Expected: no errors. If lint errors appear, fix them inline (mostly trailing whitespace, missing types).

Run: `uv run mypy tests/simulation/ --ignore-missing-imports`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add tests/simulation/__init__.py
git commit -m "docs(sim): package docstring + entry-point pointer"
```

---

## Self-Review Notes

This plan was self-reviewed for placeholder text, internal consistency, and spec coverage:

**Spec coverage:**
- §3 Architecture & components → Tasks 16-22 (one component each, plus aggregator).
- §4 Per-turn data flow → Task 22 (SimulationRunner.run loop).
- §5 Stop criteria & reproducibility → Task 22 (test_runner_e2e_mocked_llm covers max_turns, pipeline_error, alert_budget, agent_stuck).
- §6 Agent policy → Tasks 15 (prompts) + 16-18 (observation + decide + retry + legality + anti-deadlock).
- §7 IncoherenceChecker rules → Tasks 3-11 (R1.* + R2.* + R3.*).
- §8 Recorder & outputs → Tasks 20-21.
- §9 Module layout & CLI → Tasks 1 + 23.
- §10 Testing the simulator → embedded in every task (TDD-first).
- §11 Implementation footprint → Tasks 13 (`ai/client.py`) + 14 (`ScenarioRunner`).

**Known follow-ups (deferred to a later plan):**
- `free_form_action` method on `ScenarioRunner` — currently the driver raises `NotImplementedError` for the `free_form` action. The agent prompt encourages this action; in early runs the driver will surface this as a `pipeline_error` and the recorder will log the trace. A short follow-up plan should add `ScenarioRunner.free_form_action` (probably routing through `bot/cogs/action_handler.py`'s `@bot` mention flow).
- Full state diff computation (`state_diff`) — currently the diff is `{}` placeholder, which means R3.* rules will not fire from real runs. A future task should add proper diffing using `GameSession.snapshot()` Pydantic objects.

**Type consistency:** verified that all rule signatures match `Rule = Callable[[str, Any, dict[str, list[Any]], list[Any]], list[IncoherenceAlert]]` and that `AgentIntent` / `TurnRecord` / `TurnOutcome` / `IncoherenceAlert` are referenced consistently across all tasks.

---

## Execution Handoff

**Plan complete and saved to** `docs/superpowers/plans/2026-05-25-autonomous-playthrough-simulator.md`.

Two execution options:

1. **Subagent-Driven (recommended)** — A coordinator dispatches a fresh subagent per task, reviews each task's output between tasks, and you stay in the loop with two-stage reviews. Best for a 24-task plan: keeps the main context window clean and surfaces issues early.

2. **Inline Execution** — Execute tasks in this session using `superpowers:executing-plans`, batched with checkpoints for review every few tasks. Simpler but lengthier context.

**Which approach?**
