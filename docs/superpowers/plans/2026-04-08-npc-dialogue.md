# NPC Dialogue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make NPCs actually speak when the player TALKs to them — in character, with persistent personality and conversation memory, instead of remaining mute while the narrator describes the ambiance.

**Architecture:** Wire the existing-but-orphaned `NPCAgent` into the action pipeline's TALK branch. Lazily generate canon NPC sheets (personality / description / secrets / knowledge) on first interaction via a new `NPCGenerator` LLM call. Persist dialogue history per NPC in DB so subsequent conversations build on previous reveals. The narrator receives the actual dialogue verbatim through `MechanicsOutcome.outcome_facts` and is instructed to put it in quotes inside the narrative.

**Tech Stack:** Python 3.12, Pydantic v2, pytest, Ollama (qwen3.5:4b NPC + qwen3.5:9b narrator). Same project conventions as the rich-interaction-context plan.

---

## Background

The user reported that TALK with `Elie l'Ermite` produces atmospheric narration with the NPC remaining silent. Investigation showed three cumulative gaps:

1. **`bot/action_pipeline.py:638-641`** — TALK branch produces only `"X approaches Y to speak."`. `NPCAgent` (which exists at `ai/npc_agent.py` and works) is never invoked from the action pipeline. It's only called by the deprecated `/talk` slash command at `bot/cogs/exploration.py:208`.
2. **`bot/scene_hydration.py:37-54`** — Hydrated NPCs have `personality=""` and `description=""`. The world generator only emits NPC names. So even if `NPCAgent` were wired, the prompt sent to the LLM would have an empty personality field.
3. **No conversation memory.** Each TALK is fresh — the NPC re-discovers the player on every turn.

User-approved scope (full): **wire NPCAgent + lazy canon generation + memory persisted on the NPC model in DB**.

Reuses heavily: this plan mirrors the structure of [2026-04-08-rich-interaction-context.md](2026-04-08-rich-interaction-context.md) which solved the same problem for items.

## File map

| File | Action | Responsibility |
|---|---|---|
| [world/npc.py](../../../world/npc.py) | Modify | Add `DialogueExchange` model, fields `secrets`, `knowledge`, `dialogue_history` |
| [ai/models.py](../../../ai/models.py) | Modify | Add `NPCSheet` model (output of NPC generator) |
| [ai/prompts/system_npc_generator.txt](../../../ai/prompts/system_npc_generator.txt) | Create | LLM prompt for canon NPC backstory generation |
| [ai/npc_generator.py](../../../ai/npc_generator.py) | Create | `NPCGenerator.generate(name, location_ctx, campaign_theme, language)` → `NPCSheet` |
| [ai/npc_agent.py](../../../ai/npc_agent.py) | Modify | Include `dialogue_history` and `revealed_so_far` in the prompt; pull from `npc.dialogue_history` |
| [ai/prompts/system_npc_agent.txt](../../../ai/prompts/system_npc_agent.txt) | Modify | Reference history; tell the LLM not to repeat already-revealed info |
| [bot/game_session.py](../../../bot/game_session.py) | Modify | Instantiate `npc_generator` alongside existing `npc_agent` (in `create_ai_services`) |
| [bot/action_pipeline.py](../../../bot/action_pipeline.py) | Modify | TALK branch: lookup NPC, lazy-generate sheet if empty, call `npc_agent.respond()`, persist NPC, build rich `MechanicsOutcome` with dialogue in `outcome_facts` |
| [ai/prompts/system_narrator.txt](../../../ai/prompts/system_narrator.txt) | Modify | Add clause: when `outcome_facts` contains `NPC says: "..."`, the dialogue inside the quotes is canon and MUST appear verbatim in the narration |
| [db/models.py](../../../db/models.py) | Modify | `NPCRow`: add `secrets`, `knowledge`, `dialogue_history` JSON columns |
| [db/mappers.py](../../../db/mappers.py) | Modify | Round-trip the new fields |
| [db/database.py](../../../db/database.py) | Modify | Add migration for the 3 new `npcs` columns |
| [tests/world/test_npc.py](../../../tests/world/test_npc.py) | Modify or create | Test new NPC fields default + populated |
| [tests/ai/test_npc_generator.py](../../../tests/ai/test_npc_generator.py) | Create | Test NPC generator with mocked LLM |
| [tests/ai/test_npc_agent.py](../../../tests/ai/test_npc_agent.py) | Modify or create | Test that history is included in prompt |
| [tests/bot/test_action_pipeline_dialogue.py](../../../tests/bot/test_action_pipeline_dialogue.py) | Create | Regression test: TALK invokes NPCAgent, dialogue reaches `outcome_facts`, history grows |
| [tests/db/test_npc_mappers.py](../../../tests/db/test_npc_mappers.py) | Modify or create | Round-trip dialogue_history |

`bot/cogs/exploration.py` `/talk` command keeps working unchanged — it already uses NPCAgent. The fix is purely additive on the free-text path.

---

## Task 1: Extend `NPC` and add `DialogueExchange`

**Files:**
- Modify: `world/npc.py`
- Test: `tests/world/test_npc.py`

- [ ] **Step 1: Write the failing tests**

```python
from world.npc import NPC, DialogueExchange, NPCDisposition
from engine.character import AbilityScores, Race


def _base_kwargs(name: str = "Test") -> dict:
    return dict(
        name=name, race=Race.HUMAN, level=1,
        ability_scores=AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10),
        hp=10, max_hp=10, ac=10, disposition=NPCDisposition.NEUTRAL,
    )


def test_npc_new_fields_default_empty():
    npc = NPC(**_base_kwargs())
    assert npc.secrets == []
    assert npc.knowledge == []
    assert npc.dialogue_history == []


def test_dialogue_exchange_model():
    ex = DialogueExchange(
        player_said="Bonjour",
        npc_said="Salutations, voyageur.",
        revealed=["village name: Valombre"],
    )
    assert ex.player_said == "Bonjour"
    assert "Valombre" in ex.revealed[0]


def test_npc_dialogue_history_round_trip():
    npc = NPC(
        **_base_kwargs(),
        secrets=["A pact was made."],
        knowledge=["The cathedral was built in 1187."],
        dialogue_history=[
            DialogueExchange(
                player_said="hi",
                npc_said="hello",
                revealed=[],
            ),
        ],
    )
    assert npc.secrets == ["A pact was made."]
    assert len(npc.dialogue_history) == 1
    assert npc.dialogue_history[0].npc_said == "hello"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/world/test_npc.py -v
```

Expected: ImportError on `DialogueExchange` or pydantic validation error on the new fields.

- [ ] **Step 3: Implement**

In `world/npc.py`, add **before** `class NPC`:

```python
class DialogueExchange(BaseModel):
    """One round of dialogue between a player and an NPC.

    Stored on ``NPC.dialogue_history`` so subsequent conversations can
    avoid repeating reveals and build narrative continuity.
    """

    player_said: str
    npc_said: str
    revealed: list[str] = Field(default_factory=list)
```

Then add to `class NPC` (after `aliases`):

```python
    secrets: list[str] = Field(default_factory=list)
    knowledge: list[str] = Field(default_factory=list)
    dialogue_history: list[DialogueExchange] = Field(default_factory=list)
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/world/test_npc.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add world/npc.py tests/world/test_npc.py
git commit -m "feat(npc): add DialogueExchange and secrets/knowledge/history fields"
```

---

## Task 2: Add `NPCSheet` model

**Files:**
- Modify: `ai/models.py`
- Test: `tests/ai/test_models.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/ai/test_models.py`:

```python
def test_npc_sheet_minimal():
    from ai.models import NPCSheet
    sheet = NPCSheet(
        personality="Vieil ermite méfiant.",
        description="Un homme voûté en robe de bure.",
    )
    assert sheet.personality.startswith("Vieil")
    assert sheet.secrets == []
    assert sheet.knowledge == []


def test_npc_sheet_full():
    from ai.models import NPCSheet
    sheet = NPCSheet(
        personality="Méfiant mais loyal envers les justes.",
        description="Un ermite voûté, robe de bure tachée de cendre.",
        secrets=["Sait que Dom André est corrompu."],
        knowledge=["Connaît l'entrée de la crypte sous l'autel."],
    )
    assert "corrompu" in sheet.secrets[0]
    assert len(sheet.knowledge) == 1
```

- [ ] **Step 2: Run + verify failure**

```bash
uv run pytest tests/ai/test_models.py -v
```

- [ ] **Step 3: Implement**

Append to `ai/models.py`:

```python
class NPCSheet(BaseModel):
    """Canon backstory generated for an NPC by the NPCGenerator.

    Persisted onto the NPC entity once generated. The agent reads it
    when producing dialogue. ``secrets`` are things the NPC knows but
    won't volunteer easily; ``knowledge`` are things the NPC will share
    when asked appropriately.
    """

    personality: str
    description: str
    secrets: list[str] = Field(default_factory=list)
    knowledge: list[str] = Field(default_factory=list)
```

- [ ] **Step 4: Run tests**

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add ai/models.py tests/ai/test_models.py
git commit -m "feat(ai): add NPCSheet model for canon NPC backstory"
```

---

## Task 3: Add `NPCGenerator` + prompt

**Files:**
- Create: `ai/prompts/system_npc_generator.txt`
- Create: `ai/npc_generator.py`
- Test: `tests/ai/test_npc_generator.py`

- [ ] **Step 1: Write the prompt**

Create `ai/prompts/system_npc_generator.txt`:

```
You return ONE single JSON object and NOTHING else. No markdown code fence. No prose. No explanation.

You are a D&D 5e NPC architect. Given an NPC name, the location they inhabit, and the campaign theme, generate a canon backstory for that character. The output is the unshakeable truth about who they are — the NPC dialogue agent will read it on every interaction.

You will receive:
- NPC name (this is canon, use it exactly)
- Location context (where they live, the surrounding atmosphere)
- Campaign theme (the broader story arc)

Output schema (the only valid format):
{
  "personality": "<2-3 sentences: temperament, speech style, fears, desires>",
  "description": "<2-3 sentences: physical appearance, clothing, demeanor>",
  "secrets": ["<1-3 things the NPC knows but won't reveal easily>"],
  "knowledge": ["<2-4 things the NPC will share if asked appropriately>"]
}

Rules:
- Make personality SPECIFIC and playable. Avoid bland NPCs ("a kind old man"). Give them quirks, contradictions, hooks.
- Secrets must connect to the campaign theme — they are narrative hooks the GM can pull.
- Knowledge is local lore the NPC genuinely possesses (rumors, history, relationships).
- All fields must be in the requested language.
- Return valid JSON only. Reminder: any text outside the JSON breaks the system.
```

- [ ] **Step 2: Write the failing test**

Create `tests/ai/test_npc_generator.py`:

```python
from unittest.mock import MagicMock

from ai.models import NPCSheet
from ai.npc_generator import NPCGenerator


def test_generate_returns_npc_sheet():
    client = MagicMock()
    client.chat_json.return_value = {
        "personality": "Méfiant mais loyal envers les justes. Parle peu.",
        "description": "Un ermite voûté, robe de bure tachée de cendre.",
        "secrets": ["Sait que Dom André est corrompu."],
        "knowledge": [
            "Connaît l'entrée de la crypte sous l'autel.",
            "Le village a été fondé en 1187.",
        ],
    }

    generator = NPCGenerator(client)
    sheet = generator.generate(
        npc_name="Élie l'Ermite",
        location_context="La Paroisse de Saint-Michel — vieille église corrompue.",
        campaign_theme="sous une église",
        language="fr",
    )

    assert isinstance(sheet, NPCSheet)
    assert "Méfiant" in sheet.personality
    assert "corrompu" in sheet.secrets[0]
    assert len(sheet.knowledge) == 2

    # Verify the prompt was sent
    args, _kwargs = client.chat_json.call_args
    messages = args[1]
    user_msg = messages[-1]["content"]
    assert "Élie l'Ermite" in user_msg
    assert "Paroisse de Saint-Michel" in user_msg
    assert "sous une église" in user_msg


def test_generate_handles_missing_fields():
    client = MagicMock()
    client.chat_json.return_value = {
        "personality": "Stoïque.",
        "description": "Sombre.",
    }
    generator = NPCGenerator(client)
    sheet = generator.generate(
        npc_name="X", location_context="Y", campaign_theme="Z",
    )
    assert sheet.secrets == []
    assert sheet.knowledge == []
```

- [ ] **Step 3: Run + verify failure**

```bash
uv run pytest tests/ai/test_npc_generator.py -v
```

Expected: ImportError.

- [ ] **Step 4: Implement**

Create `ai/npc_generator.py`:

```python
"""NPC Generator — produces canon backstory sheets for newly-encountered NPCs."""

import logging
from pathlib import Path

from ai.client import OllamaClient
from ai.language import language_instruction
from ai.models import NPCSheet

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    Path(__file__).parent / "prompts" / "system_npc_generator.txt"
).read_text()


class NPCGenerator:
    """Lazily generate canon backstories for NPCs that have empty sheets.

    The output is persisted onto the NPC entity by the caller so this
    expensive LLM call only happens once per NPC.
    """

    MODEL = "qwen3.5:4b"

    def __init__(self, client: OllamaClient) -> None:
        self._client = client

    def generate(
        self,
        npc_name: str,
        location_context: str,
        campaign_theme: str,
        language: str = "fr",
    ) -> NPCSheet:
        """Generate a backstory sheet for ``npc_name``.

        Args:
            npc_name: Canonical NPC name (used verbatim in the prompt).
            location_context: Where the NPC is encountered — name + ambiance.
            campaign_theme: Broader campaign theme so secrets can hook in.
            language: ISO 639-1 language code for output.

        Returns:
            An :class:`NPCSheet` with personality, description, secrets,
            and knowledge ready to persist on the NPC entity.
        """
        user_content = (
            f"NPC name: {npc_name}\n\n"
            f"Location context:\n{location_context}\n\n"
            f"Campaign theme: {campaign_theme}"
        )
        system_prompt = language_instruction(language) + _SYSTEM_PROMPT
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        data = self._client.chat_json(self.MODEL, messages, temperature=0.8)
        sheet = NPCSheet(
            personality=str(data.get("personality", "")).strip(),
            description=str(data.get("description", "")).strip(),
            secrets=[str(s).strip() for s in data.get("secrets", []) if str(s).strip()],
            knowledge=[str(k).strip() for k in data.get("knowledge", []) if str(k).strip()],
        )
        logger.info(
            "NPCGEN name=%r secrets=%d knowledge=%d",
            npc_name, len(sheet.secrets), len(sheet.knowledge),
        )
        return sheet
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/ai/test_npc_generator.py -v
```

Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add ai/prompts/system_npc_generator.txt ai/npc_generator.py tests/ai/test_npc_generator.py
git commit -m "feat(ai): add NPCGenerator for lazy canon backstory generation"
```

---

## Task 4: Update `NPCAgent` to use dialogue history

**Files:**
- Modify: `ai/npc_agent.py`
- Modify: `ai/prompts/system_npc_agent.txt`
- Test: `tests/ai/test_npc_agent.py`

- [ ] **Step 1: Write failing test**

Create or modify `tests/ai/test_npc_agent.py`:

```python
from unittest.mock import MagicMock

from ai.npc_agent import NPCAgent
from world.npc import NPC, DialogueExchange, NPCDisposition
from engine.character import AbilityScores, Race


def _make_npc(history: list[DialogueExchange] | None = None) -> NPC:
    return NPC(
        name="Élie l'Ermite",
        race=Race.HUMAN,
        level=1,
        ability_scores=AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=12, CHA=10),
        hp=4, max_hp=4, ac=10,
        disposition=NPCDisposition.NEUTRAL,
        description="Un ermite voûté.",
        personality="Méfiant mais loyal.",
        secrets=["Dom André est corrompu."],
        knowledge=["L'entrée de la crypte est sous l'autel."],
        dialogue_history=history or [],
    )


def test_respond_includes_personality_and_secrets_in_prompt():
    client = MagicMock()
    client.chat_json.return_value = {
        "dialogue": "Approche, étranger.",
        "disposition_change": 1,
        "revealed_info": [],
    }
    agent = NPCAgent(client)
    npc = _make_npc()

    agent.respond(npc, player_input="Bonjour vénérable", context_prompt="## Location")

    args, _kwargs = client.chat_json.call_args
    user_msg = args[1][-1]["content"]
    assert "Méfiant mais loyal" in user_msg
    assert "Dom André est corrompu" in user_msg  # secrets must reach the LLM
    assert "L'entrée de la crypte" in user_msg


def test_respond_includes_dialogue_history_when_present():
    client = MagicMock()
    client.chat_json.return_value = {
        "dialogue": "Je t'ai déjà parlé de cela.",
        "disposition_change": 0,
        "revealed_info": [],
    }
    agent = NPCAgent(client)
    npc = _make_npc(history=[
        DialogueExchange(
            player_said="Que sais-tu de la crypte ?",
            npc_said="Elle est sous l'autel.",
            revealed=["L'entrée de la crypte est sous l'autel."],
        ),
    ])

    agent.respond(npc, player_input="Et la crypte ?", context_prompt="")

    user_msg = client.chat_json.call_args[0][1][-1]["content"]
    assert "Que sais-tu de la crypte" in user_msg
    assert "sous l'autel" in user_msg
    assert "Already revealed" in user_msg or "déjà révélé" in user_msg.lower()


def test_respond_returns_npc_response():
    client = MagicMock()
    client.chat_json.return_value = {
        "dialogue": "Salutations.",
        "disposition_change": 1,
        "revealed_info": ["Le village s'appelle Valombre."],
    }
    agent = NPCAgent(client)
    response = agent.respond(_make_npc(), player_input="hi", context_prompt="")
    assert response.dialogue == "Salutations."
    assert response.disposition_change == 1
    assert response.revealed_info == ["Le village s'appelle Valombre."]
```

- [ ] **Step 2: Run + verify failure**

- [ ] **Step 3: Update `_build_user_message` in `ai/npc_agent.py`**

Replace the method:

```python
    def _build_user_message(
        self, npc: NPC, player_input: str, context_prompt: str,
    ) -> str:
        """Build the user message with NPC sheet, history, and player input."""
        npc_sheet_lines = [
            f"Character: {npc.name}",
            f"Race: {npc.race.value}",
            f"Disposition: {npc.disposition.value}",
            f"Personality: {npc.personality}",
            f"Description: {npc.description}",
            f"HP: {npc.hp}/{npc.max_hp}",
        ]
        if npc.secrets:
            npc_sheet_lines.append("Secrets (do NOT volunteer; reveal only if pressed and trust is high):")
            for secret in npc.secrets:
                npc_sheet_lines.append(f"  - {secret}")
        if npc.knowledge:
            npc_sheet_lines.append("Knowledge (share if asked appropriately):")
            for fact in npc.knowledge:
                npc_sheet_lines.append(f"  - {fact}")
        npc_sheet = "\n".join(npc_sheet_lines)

        sections = [context_prompt, f"## Your Character\n{npc_sheet}"]

        if npc.dialogue_history:
            history_lines = ["## Conversation so far"]
            for ex in npc.dialogue_history[-5:]:
                history_lines.append(f"Player: {ex.player_said}")
                history_lines.append(f"You: {ex.npc_said}")
            already_revealed = [
                r for ex in npc.dialogue_history for r in ex.revealed
            ]
            if already_revealed:
                history_lines.append("")
                history_lines.append("Already revealed (do NOT repeat verbatim):")
                for r in already_revealed:
                    history_lines.append(f"  - {r}")
            sections.append("\n".join(history_lines))

        sections.append(f"## Player says\n{player_input}")
        return "\n\n".join(s for s in sections if s.strip())
```

- [ ] **Step 4: Update prompt**

Edit `ai/prompts/system_npc_agent.txt`. Replace the rules section with:

```
Rules:
- disposition_change: +2 very positive, +1 somewhat positive, 0 neutral, -1 somewhat negative, -2 very negative
- Reveal information from your "Knowledge" list when the player asks appropriately. Add the revealed item to revealed_info verbatim.
- Reveal items from your "Secrets" list ONLY if the player has earned your trust (current disposition friendly+ AND multiple positive exchanges) OR explicitly presses on the right topic. When you reveal a secret, include it in revealed_info verbatim.
- If a "Conversation so far" section is present, you have ALREADY met this player. Do not greet them again, do not re-introduce yourself, and do not repeat anything from "Already revealed".
- If hostile (disposition < 0), be threatening or dismissive. Refuse to share knowledge or secrets.
- dialogue is what your character SAYS — 1 to 3 sentences, in first person, in-character, in the requested language. No stage directions.
- revealed_info is a list of strings; can be empty.
- Return valid JSON only.
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/ai/test_npc_agent.py -v
```

Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add ai/npc_agent.py ai/prompts/system_npc_agent.txt tests/ai/test_npc_agent.py
git commit -m "feat(npc-agent): include secrets/knowledge/history in dialogue prompt"
```

---

## Task 5: Persist new NPC fields in DB

**Files:**
- Modify: `db/models.py`
- Modify: `db/mappers.py`
- Modify: `db/database.py`
- Test: `tests/db/test_npc_mappers.py` (modify or create)

- [ ] **Step 1: Write failing round-trip test**

Add to `tests/db/test_npc_mappers.py` (or create if missing — base on existing mapper test patterns; if no `tests/db/` directory exists, place in `tests/test_npc_mappers.py`):

```python
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.database import Base, _migrate_schema
from db.mappers import npc_from_db, npc_to_db
from db.models import CampaignRow
from db.repositories.npc_repo import NPCRepository
from world.npc import NPC, DialogueExchange, NPCDisposition
from engine.character import AbilityScores, Race


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    _migrate_schema(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    # Create a campaign FK target
    session.add(CampaignRow(id="c1", name="t", player_names_json="[]"))
    session.commit()
    yield session
    session.close()


def test_npc_round_trip_with_dialogue_history(db_session):
    npc = NPC(
        name="Élie",
        race=Race.HUMAN,
        level=1,
        ability_scores=AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10),
        hp=4, max_hp=4, ac=10,
        disposition=NPCDisposition.FRIENDLY,
        description="Vieil ermite.",
        personality="Méfiant mais loyal.",
        secrets=["Dom André est corrompu."],
        knowledge=["L'entrée de la crypte."],
        dialogue_history=[
            DialogueExchange(
                player_said="bonjour",
                npc_said="approche",
                revealed=[],
            ),
        ],
    )
    repo = NPCRepository(db_session)
    repo.save(npc, "c1")
    db_session.commit()

    loaded = repo.get_by_name("Élie", "c1")
    assert loaded is not None
    assert loaded.secrets == ["Dom André est corrompu."]
    assert loaded.knowledge == ["L'entrée de la crypte."]
    assert len(loaded.dialogue_history) == 1
    assert loaded.dialogue_history[0].player_said == "bonjour"
```

(If the campaign FK schema differs, adapt the fixture; the goal is round-trip coverage of the new fields.)

- [ ] **Step 2: Run + verify failure**

- [ ] **Step 3: Add columns**

In `db/models.py` `NPCRow`, after the `aliases` line, add:

```python
    secrets: Mapped[list] = mapped_column(JSON, default=list)  # type: ignore[type-arg]
    knowledge: Mapped[list] = mapped_column(JSON, default=list)  # type: ignore[type-arg]
    dialogue_history: Mapped[list] = mapped_column(JSON, default=list)  # type: ignore[type-arg]
```

- [ ] **Step 4: Update mappers**

In `db/mappers.py`, find `npc_to_db` and `npc_from_db`. Update them to round-trip the new fields. The `dialogue_history` requires serializing `DialogueExchange` objects to dicts and back.

```python
def npc_to_db(npc: NPC, campaign_id: str) -> NPCRow:
    """Convert an NPC domain model to a DB row."""
    return NPCRow(
        # ... existing fields unchanged ...
        secrets=list(npc.secrets),
        knowledge=list(npc.knowledge),
        dialogue_history=[ex.model_dump() for ex in npc.dialogue_history],
    )


def npc_from_db(row: NPCRow) -> NPC:
    """Convert an NPCRow to an NPC domain model."""
    from world.npc import DialogueExchange
    return NPC(
        # ... existing fields unchanged ...
        secrets=list(row.secrets) if row.secrets else [],
        knowledge=list(row.knowledge) if row.knowledge else [],
        dialogue_history=[
            DialogueExchange(**ex) for ex in (row.dialogue_history or [])
        ],
    )
```

(Locate the exact existing `npc_to_db` / `npc_from_db` functions and add the new arguments alongside the existing ones — do NOT delete unrelated lines.)

- [ ] **Step 5: Add migration**

In `db/database.py`, append to `_migrate_schema` after the existing `aliases` migration block:

```python
        # Add secrets/knowledge/dialogue_history columns to npcs (NPC dialogue lot)
        if npc_columns:
            if "secrets" not in npc_columns:
                conn.execute(
                    text("ALTER TABLE npcs ADD COLUMN secrets JSON DEFAULT '[]'")
                )
                conn.commit()
            if "knowledge" not in npc_columns:
                conn.execute(
                    text("ALTER TABLE npcs ADD COLUMN knowledge JSON DEFAULT '[]'")
                )
                conn.commit()
            if "dialogue_history" not in npc_columns:
                conn.execute(
                    text("ALTER TABLE npcs ADD COLUMN dialogue_history JSON DEFAULT '[]'")
                )
                conn.commit()
```

(Reuse the `npc_columns` variable that the existing `aliases` block already populates.)

- [ ] **Step 6: Run tests**

```bash
uv run pytest tests/db/ tests/test_npc_mappers.py 2>/dev/null -v
uv run pytest tests/ -k "npc" -v
```

Expected: green; no regressions on existing NPC tests.

- [ ] **Step 7: Commit**

```bash
git add db/models.py db/mappers.py db/database.py tests/db/test_npc_mappers.py tests/test_npc_mappers.py
git commit -m "feat(db): persist NPC secrets/knowledge/dialogue_history"
```

---

## Task 6: Instantiate `NPCGenerator` on the session

**Files:**
- Modify: `bot/game_session.py`

- [ ] **Step 1: Read existing `create_ai_services`**

```bash
grep -n "create_ai_services\|npc_agent" bot/game_session.py
```

Locate the function that wires `npc_agent`. Add an `npc_generator` attribute alongside it.

- [ ] **Step 2: Add `npc_generator` field**

In `bot/game_session.py`, find the `GameSession` model class. Add:

```python
    npc_generator: Any = None  # ai.npc_generator.NPCGenerator at runtime
```

(use whatever pattern the existing `npc_agent` field follows — likely `Any` or a forward-ref string).

In `create_ai_services`, after the `npc_agent` line, add:

```python
    from ai.npc_generator import NPCGenerator
    session.npc_generator = NPCGenerator(client)
```

- [ ] **Step 3: Run session tests**

```bash
uv run pytest tests/bot/test_game_session.py -v 2>/dev/null || \
    uv run pytest tests/ -k "game_session" -v
```

Expected: green.

- [ ] **Step 4: Commit**

```bash
git add bot/game_session.py
git commit -m "feat(session): instantiate NPCGenerator alongside NPCAgent"
```

---

## Task 7: Wire NPCAgent into action_pipeline TALK branch

**Files:**
- Modify: `bot/action_pipeline.py`
- Test: `tests/bot/test_action_pipeline_dialogue.py` (create)

This is the heart of the lot. The TALK branch must:
1. Resolve the target NPC from `self.npcs` (entity resolver already did the name → canonical match before this point)
2. If `npc.personality` is empty, call `session.npc_generator.generate(...)`, copy fields onto the NPC, persist
3. Call `session.npc_agent.respond(npc, player_input=action.raw_input, context_prompt=scene_context)`
4. Apply `disposition_change` to NPC
5. Append a new `DialogueExchange(player_said, npc_said, revealed)` to `npc.dialogue_history`
6. Persist the mutated NPC via `NPCRepository`
7. Build `MechanicsOutcome`:
   - `summary`: `f"{actor} speaks with {npc.name}." + disposition delta if any`
   - `player_intent`: as built by `_build_player_intent`
   - `outcome_facts`: structured as
     ```
     {npc.name} says: "{response.dialogue}"
     [Reveals: ... ; ...]   ← only if reveals non-empty
     [Disposition shift: +1] ← only if non-zero
     ```

- [ ] **Step 1: Write the failing regression test**

Create `tests/bot/test_action_pipeline_dialogue.py`:

```python
"""TALK action invokes NPCAgent and surfaces dialogue to the narrator."""

from unittest.mock import MagicMock

import pytest

from ai.models import (
    InterpretedAction, MechanicsOutcome, NarrativeResult, NPCResponse, NPCSheet,
)
from bot.action_pipeline import ActionPipeline
from engine.character import AbilityScores, Race
from engine.validators import ActionType
from world.location import Location
from world.npc import NPC, NPCDisposition, DialogueExchange


def _npc(name: str, *, personality: str = "", description: str = "") -> NPC:
    return NPC(
        name=name, race=Race.HUMAN, level=1,
        ability_scores=AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10),
        hp=4, max_hp=4, ac=10, disposition=NPCDisposition.NEUTRAL,
        description=description, personality=personality,
        location_name="La Paroisse", aliases=[],
    )


@pytest.mark.asyncio
async def test_talk_invokes_npc_agent_and_threads_dialogue_to_outcome():
    location = Location(
        name="La Paroisse",
        description="Une vieille église.",
        npcs_present=["Elie"],
    )
    npc = _npc("Elie", personality="Méfiant mais loyal.", description="Vieil ermite.")
    session = MagicMock()
    session.current_location = location
    session.npcs = {"Elie": npc}
    session.story_arc = None
    session.advance_beat_if_ready = lambda: None
    session.campaign.id = "test"
    session.npc_agent = MagicMock()
    session.npc_agent.respond.return_value = NPCResponse(
        dialogue="Approche, étranger. Que cherches-tu ici ?",
        disposition_change=1,
        revealed_info=["Le village s'appelle Valombre."],
    )
    session.npc_generator = MagicMock()  # not used since personality is set

    interpreted = InterpretedAction(
        action_type=ActionType.TALK,
        actor_name="Xavier",
        target_name="Elie",
        raw_input="je m'approche d'Elie et lui demande ce qui se passe",
        confidence=0.95,
        talk_topic="ce qui se passe ici",
    )

    pipeline = ActionPipeline(
        campaign_id="test",
        actor_name="Xavier",
        interpreter=MagicMock(),
        narrator=MagicMock(),
        session=session,
        language="fr",
        location=location,
        npcs=session.npcs,
    )

    outcome = await pipeline._resolve_mechanics(interpreted)

    # NPCAgent.respond was called with the right NPC and player input.
    session.npc_agent.respond.assert_called_once()
    call_kwargs = session.npc_agent.respond.call_args
    assert call_kwargs.kwargs.get("npc") is npc or call_kwargs.args[0] is npc
    player_input = call_kwargs.kwargs.get("player_input") or call_kwargs.args[1]
    assert "Elie" in player_input or "ce qui se passe" in player_input

    # Dialogue and reveal reach outcome_facts so the narrator can render them.
    assert isinstance(outcome, MechanicsOutcome)
    assert "Approche, étranger" in outcome.outcome_facts
    assert "Valombre" in outcome.outcome_facts

    # Disposition change applied.
    assert npc.disposition == NPCDisposition.FRIENDLY  # NEUTRAL + 1

    # Dialogue history grew by one entry.
    assert len(npc.dialogue_history) == 1
    assert "Approche" in npc.dialogue_history[0].npc_said


@pytest.mark.asyncio
async def test_talk_lazy_generates_npc_sheet_when_personality_empty():
    location = Location(name="La Paroisse", description="…", npcs_present=["Elie"])
    npc = _npc("Elie")  # empty personality + description
    session = MagicMock()
    session.current_location = location
    session.npcs = {"Elie": npc}
    session.story_arc = None
    session.campaign.id = "test"
    session.campaign.name = "sous une église"

    session.npc_generator = MagicMock()
    session.npc_generator.generate.return_value = NPCSheet(
        personality="Méfiant.", description="Vieil ermite voûté.",
        secrets=["Dom André est corrompu."],
        knowledge=["L'entrée de la crypte est sous l'autel."],
    )
    session.npc_agent = MagicMock()
    session.npc_agent.respond.return_value = NPCResponse(
        dialogue="Hmpf.", disposition_change=0, revealed_info=[],
    )

    interpreted = InterpretedAction(
        action_type=ActionType.TALK,
        actor_name="Xavier", target_name="Elie",
        raw_input="bonjour", confidence=0.95,
    )

    pipeline = ActionPipeline(
        campaign_id="test", actor_name="Xavier",
        interpreter=MagicMock(), narrator=MagicMock(),
        session=session, language="fr",
        location=location, npcs=session.npcs,
    )
    await pipeline._resolve_mechanics(interpreted)

    session.npc_generator.generate.assert_called_once()
    assert npc.personality == "Méfiant."
    assert "Dom André" in npc.secrets[0]
```

- [ ] **Step 2: Run + verify failure**

- [ ] **Step 3: Implement the new TALK branch**

In `bot/action_pipeline.py`, **replace** the existing TALK branch in `_resolve_mechanics` (currently at l. 638-641):

```python
        if at == ActionType.TALK:
            return await asyncio.to_thread(self._resolve_talk, action)
```

Then add the helper method on the same class (place it near `_resolve_pickup`):

```python
    def _resolve_talk(self, action: InterpretedAction) -> "MechanicsOutcome":
        """Run TALK through the NPC agent, persist state, build outcome."""
        from ai.models import MechanicsOutcome
        from world.npc import DialogueExchange, NPCDisposition

        intent = self._build_player_intent(action)
        target = action.target_name or ""

        if (
            self.session is None
            or not target
            or target not in (self.session.npcs or {})
        ):
            return MechanicsOutcome(
                summary=f"{action.actor_name} approaches {target} to speak.",
                player_intent=intent,
            )

        npc = self.session.npcs[target]
        agent = getattr(self.session, "npc_agent", None)
        generator = getattr(self.session, "npc_generator", None)

        # Lazy canon generation when the NPC sheet is empty.
        if generator is not None and not (npc.personality or npc.description):
            try:
                location_ctx = ""
                if self.session.current_location is not None:
                    loc = self.session.current_location
                    location_ctx = f"{loc.name} — {loc.description}"
                campaign_theme = getattr(self.session.campaign, "name", "")
                sheet = generator.generate(
                    npc_name=npc.name,
                    location_context=location_ctx,
                    campaign_theme=campaign_theme,
                    language=self.language,
                )
                npc.personality = sheet.personality
                npc.description = sheet.description
                npc.secrets = list(sheet.secrets)
                npc.knowledge = list(sheet.knowledge)
                logger.info(
                    "NPC lazy-generated name=%s secrets=%d knowledge=%d",
                    npc.name, len(npc.secrets), len(npc.knowledge),
                )
            except Exception:
                logger.exception(
                    "NPC sheet generation failed for %s", npc.name,
                )

        if agent is None:
            return MechanicsOutcome(
                summary=f"{action.actor_name} speaks with {npc.name}.",
                player_intent=intent,
            )

        # Build a small scene context for the dialogue agent.
        try:
            from bot.scene_hydration import describe_scene_for_narrator
            agent_context = describe_scene_for_narrator(
                self.session, actor_name=action.actor_name,
            )
        except Exception:
            agent_context = ""

        try:
            response = agent.respond(
                npc=npc,
                player_input=action.raw_input,
                context_prompt=agent_context,
                language=self.language,
            )
        except Exception:
            logger.exception("NPC agent failed for %s", npc.name)
            return MechanicsOutcome(
                summary=f"{action.actor_name} speaks with {npc.name}.",
                player_intent=intent,
            )

        # Apply disposition delta (clamped to NPCDisposition order).
        if response.disposition_change:
            order = [
                NPCDisposition.HOSTILE, NPCDisposition.UNFRIENDLY,
                NPCDisposition.NEUTRAL, NPCDisposition.FRIENDLY,
                NPCDisposition.ALLIED,
            ]
            try:
                idx = order.index(npc.disposition) + response.disposition_change
                idx = max(0, min(len(order) - 1, idx))
                npc.disposition = order[idx]
            except ValueError:
                pass

        # Append the exchange to history.
        npc.dialogue_history.append(
            DialogueExchange(
                player_said=action.raw_input,
                npc_said=response.dialogue,
                revealed=list(response.revealed_info),
            ),
        )

        # Persist the mutated NPC.
        if self.db_factory is not None:
            try:
                from db.repositories.npc_repo import NPCRepository
                db_session = self.db_factory()
                try:
                    NPCRepository(db_session).update(npc, self.campaign_id)
                    db_session.commit()
                finally:
                    db_session.close()
            except Exception:
                logger.exception("NPC persist failed for %s", npc.name)

        # Build the outcome facts the narrator will render.
        facts_lines = [f'{npc.name} says: "{response.dialogue}"']
        if response.revealed_info:
            facts_lines.append(
                "Reveals: " + " ; ".join(response.revealed_info),
            )
        if response.disposition_change:
            facts_lines.append(
                f"Disposition shift: {response.disposition_change:+d}",
            )

        summary = f"{action.actor_name} speaks with {npc.name}."
        if response.disposition_change:
            summary += f" (disposition: {response.disposition_change:+d})"

        return MechanicsOutcome(
            summary=summary,
            player_intent=intent,
            outcome_facts="\n".join(facts_lines),
        )
```

Note: `_resolve_mechanics` is `async`, but `_resolve_talk` does sync I/O (DB + LLM via `npc_agent`). The dispatcher uses `asyncio.to_thread(self._resolve_talk, action)`. If you find that the existing `npc_agent.respond` is already async-aware in this codebase, adapt accordingly — read the existing PICKUP wrapper at l. 667-668 for the same pattern.

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/bot/test_action_pipeline_dialogue.py -v
uv run pytest tests/bot/test_action_pipeline.py tests/bot/test_action_handler_cog.py -v
```

Expected: new tests pass; no regressions on existing pipeline tests (the previous TALK behavior was a generic string, now it's the same string when no agent is present — fallback path preserved for tests that don't mock `session.npc_agent`).

If existing tests fail because their session mock lacks `npc_agent` / `npc_generator`, the fallback paths in `_resolve_talk` already handle `agent is None` — but `getattr(self.session, "npc_agent", None)` should return None on a `MagicMock` only if the test explicitly sets it. **MagicMock auto-creates attributes**, so `session.npc_agent` will be a `MagicMock` object, not `None`. Add an explicit `if not callable(getattr(agent, "respond", None))` guard, OR have failing tests do `session.npc_agent = None` / `session.npc_generator = None` in their fixtures.

- [ ] **Step 5: Commit**

```bash
git add bot/action_pipeline.py tests/bot/test_action_pipeline_dialogue.py
git commit -m "feat(pipeline): wire NPCAgent into TALK with lazy sheet generation"
```

---

## Task 8: Update narrator system prompt for verbatim dialogue

**Files:**
- Modify: `ai/prompts/system_narrator.txt`

- [ ] **Step 1: Add verbatim-dialogue clause**

In `ai/prompts/system_narrator.txt`, find the existing "Canon faithfulness" block (added in the rich-interaction-context lot). Append a new bullet at the end of that block, before the "Output schema" section:

```
- If the "State changes" section contains a line of the form `<NPCName> says: "..."`, the dialogue inside the quotes is canon. You MUST include those exact words inside quotation marks in your narration. You may frame them with body language, atmosphere, and pacing, but the spoken words themselves must appear verbatim.
- If "State changes" contains a "Reveals:" line, you MAY weave the revealed information into the surrounding narration so the player notices it, but do not invent additional reveals beyond what's listed.
```

- [ ] **Step 2: Smoke test**

```bash
uv run pytest tests/ai/test_narrator.py tests/bot/test_action_pipeline_dialogue.py -v
```

Expected: green.

- [ ] **Step 3: Commit**

```bash
git add ai/prompts/system_narrator.txt
git commit -m "feat(narrator): require verbatim NPC dialogue from outcome_facts"
```

---

## Task 9: Full verification

- [ ] **Step 1: Full test suite**

```bash
uv run pytest -q
```

Expected: same number of pre-existing failures as on `main` (the 4 `test_free_text_exploration.py` failures), no new regressions.

- [ ] **Step 2: Lint + types**

```bash
uv run ruff check .
uv run mypy . 2>&1 | tail -3
```

Expected: ruff clean; mypy error count unchanged from baseline (213 last measured).

- [ ] **Step 3: Commit any cleanup**

If lint or types caught something, fix and commit.

- [ ] **Step 4: Live Discord smoke test**

Use the discord-test MCP tools (game bot must be running with `TEST_MODE=true`):

1. `start_campaign theme=test_pnj players=1` (or `resume` if a session exists)
2. `create_character name=Xavier race=Tiefling class_=Rogue player=1`
3. `inject_scene name=La~Paroisse description=Une~vieille~eglise items=Croix~de~fer desc_Croix~de~fer=Vieille~croix~medievale npcs=Elie~l~Ermite`
4. First TALK (triggers lazy sheet generation):
   `narrate text=je~m~approche~d~Elie~l~Ermite~et~lui~demande~ce~qui~se~passe~dans~cette~eglise`
5. Second TALK (continuity check — agent should NOT re-greet):
   `narrate text=et~la~crypte~tu~en~sais~quoi`
6. Third TALK with hostile framing (disposition test):
   `narrate text=je~le~menace~de~mon~poignard~s~il~ne~parle~pas`

Acceptance criteria:
- (a) The bot's narration after step 4 contains a quoted line spoken by Elie (not just atmospheric description).
- (b) Step 5's response treats Elie as already-met and surfaces a different topic / new reveal.
- (c) Step 6's response shows a colder NPC (disposition shift visible in the mechanics field) and refuses to share secrets.
- (d) Bot logs show `NPCGEN`, `NPC name=Elie ... disposition_change=...` and `NPC lazy-generated` lines on the first call only.

If (a) fails (narrator paraphrases instead of quoting), strengthen the verbatim clause in `system_narrator.txt` and re-test.

---

## Out of scope (follow-ups)

- **Eager NPC generation at world-gen time** — currently lazy. A future plan can have the world generator emit NPC sheets alongside `npcs_present` for the starting scene so the first TALK isn't slowed by an extra LLM call.
- **NPC-driven plot beats** — once NPCs have secrets, the story director should be able to use them as quest hooks. Out of scope here.
- **Multi-NPC conversations** — currently TALK targets a single NPC. Group dialogue is a separate plan.
- **Voice persistence across campaign saves** — `dialogue_history` is now persisted, but the NPC personality string was generated by an LLM call; if you regenerate the world, the same NPC name might get a different personality. A "canonized at first interaction" guarantee already exists since we only generate when fields are empty.
