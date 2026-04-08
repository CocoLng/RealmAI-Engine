"""End-to-end scenarios for the @mention free-text action flow.

These tests wire the **real** :class:`ActionHandlerCog`,
:class:`ActionPipeline`, :class:`Interpreter` and :class:`Narrator` together,
mocking only Discord (via lightweight fakes) and Ollama (via ``pytest_httpx``).

Each scenario corresponds to one section of the design document:

1. Happy path           — clear LOOK action → narrative embed
2. Disambiguation       — TALK with multiple candidate NPCs → ClarificationView
3. Unknown entity       — TALK to a non-existent NPC → in-character refusal
4. OOC noise filter     — short reaction → no LLM call, polite reply
5. Creative combat IMPRO — IMPROVISE during combat → narrator arbitrates
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_httpx import HTTPXMock

from ai.client import OllamaClient
from ai.interpreter import Interpreter
from ai.narrator import Narrator
from bot.cogs.action_handler import ActionHandlerCog
from bot.views.clarification_view import ClarificationView
from engine.character import (
    AbilityScores,
    CharacterClass,
    Race,
    create_character,
)
from engine.combat import CombatSide, CombatState, Combatant
from engine.inventory import Inventory
from tests.ai.conftest import CHAT_URL, make_ollama_response
from world.campaign import Campaign
from world.location import Location
from world.npc import NPC


# ---------------------------------------------------------------------------
# Fakes (mirror those in test_action_handler_cog.py)
# ---------------------------------------------------------------------------


@dataclass
class FakeMessage:
    content: str
    author: Any
    channel: Any
    mentions: list[Any] = field(default_factory=list)
    reply: AsyncMock = field(default_factory=AsyncMock)


@dataclass
class FakeAuthor:
    id: int
    bot: bool = False
    display_name: str = "Player"


@dataclass
class FakeChannel:
    id: int
    send: AsyncMock = field(default_factory=AsyncMock)


def _build_session(
    *,
    interpreter: Interpreter | None,
    narrator: Narrator | None,
    location: Location | None,
    npcs: dict[str, NPC],
    combat_state: CombatState | None = None,
    player_id: int = 1,
) -> Any:
    scores = AbilityScores(STR=12, DEX=10, CON=10, INT=10, WIS=10, CHA=10)
    char = create_character("Aldric", Race.HUMAN, CharacterClass.FIGHTER, scores)
    session = MagicMock()
    session.campaign = Campaign(
        id="scen-camp", name="Sous l'église", player_names=[str(player_id)],
    )
    session.characters = {player_id: char}
    session.npcs = npcs
    session.current_location = location
    session.combat_state = combat_state
    session.inventories = {}
    session.language = "fr"
    session.interpreter = interpreter
    session.narrator = narrator
    return session


def _build_bot(*, bot_user_id: int = 9999) -> Any:
    bot = MagicMock()
    bot.user = MagicMock()
    bot.user.id = bot_user_id
    bot.sessions = {}
    return bot


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def ability_scores() -> AbilityScores:
    return AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10)


@pytest.fixture()
def cathedral() -> Location:
    return Location(
        name="Place de la Cathédrale",
        description="Une vaste place pavée devant la cathédrale.",
        connections=["Intérieur de la cathédrale"],
        npcs_present=["Père Aldric"],
        items_available=["Autel de pierre"],
    )


def _npc(name: str, location_name: str, scores: AbilityScores, desc: str = "") -> NPC:
    return NPC(
        name=name,
        race=Race.HUMAN,
        char_class=CharacterClass.CLERIC,
        ability_scores=scores,
        hp=10,
        max_hp=10,
        ac=10,
        description=desc,
        location_name=location_name,
    )


@pytest.fixture()
def real_interpreter(ollama_client: OllamaClient) -> Interpreter:
    return Interpreter(ollama_client)


@pytest.fixture()
def real_narrator(ollama_client: OllamaClient) -> Narrator:
    return Narrator(ollama_client)


# ---------------------------------------------------------------------------
# Helper: capture the final embed shown to the player.
# ---------------------------------------------------------------------------


def _final_embed_from(channel: FakeChannel) -> Any:
    """Return the embed of the last edit() call on the progress message."""
    progress_msg = channel.send.return_value
    assert progress_msg.edit.called, "expected at least one edit() call"
    return progress_msg.edit.call_args.kwargs.get("embed")


# ---------------------------------------------------------------------------
# Scenario 1 — Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_happy_path_look(
    httpx_mock: HTTPXMock,
    real_interpreter: Interpreter,
    real_narrator: Narrator,
    cathedral: Location,
    ability_scores: AbilityScores,
) -> None:
    """`@bot je regarde autour` → narration immersive."""
    aldric = _npc("Père Aldric", "Place de la Cathédrale", ability_scores)

    httpx_mock.add_response(
        url=CHAT_URL,
        json=make_ollama_response(
            {
                "action_type": "Look",
                "actor_name": "Aldric",
                "confidence": 0.95,
            },
        ),
    )
    httpx_mock.add_response(
        url=CHAT_URL,
        json=make_ollama_response(
            {
                "narrative": "Tu observes la place déserte. Les pavés résonnent sous tes pas.",
                "tone": "dramatic",
            },
        ),
    )

    session = _build_session(
        interpreter=real_interpreter,
        narrator=real_narrator,
        location=cathedral,
        npcs={aldric.name: aldric},
    )
    bot = _build_bot()
    bot.sessions = {1: session}
    cog = ActionHandlerCog(bot)

    channel = FakeChannel(id=1)
    msg = FakeMessage(
        content="<@9999> je regarde autour",
        author=FakeAuthor(id=1),
        channel=channel,
        mentions=[bot.user],
    )

    await cog.on_message(msg)  # type: ignore[arg-type]

    final_embed = _final_embed_from(channel)
    assert final_embed is not None
    description = final_embed.description or ""
    assert "place" in description.lower()
    # The narrative embed has a "Mecaniques" field
    field_names = [f.name for f in final_embed.fields]
    assert "Mecaniques" in field_names


# ---------------------------------------------------------------------------
# Scenario 2 — Disambiguation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_disambiguation_two_priests(
    httpx_mock: HTTPXMock,
    monkeypatch: pytest.MonkeyPatch,
    real_interpreter: Interpreter,
    real_narrator: Narrator,
    ability_scores: AbilityScores,
) -> None:
    """Two NPCs match 'frere marc' → ClarificationView is attached."""
    location = Location(
        name="Place de la Cathédrale",
        description="d",
        connections=[],
        npcs_present=[],
        items_available=[],
    )
    marc1 = _npc("Frère Marc", "Place de la Cathédrale", ability_scores, "Vieux moine")
    marc2 = _npc(
        "Frère Marc le Sage", "Place de la Cathédrale", ability_scores, "Jeune novice",
    )

    httpx_mock.add_response(
        url=CHAT_URL,
        json=make_ollama_response(
            {
                "action_type": "Talk",
                "actor_name": "Aldric",
                "target_name": "Marc",
                "confidence": 0.4,
            },
        ),
    )

    # Avoid the 2-minute view.wait() in production code.
    async def _instant_wait(self: Any) -> None:
        self.cancelled = True

    monkeypatch.setattr(ClarificationView, "wait", _instant_wait)

    session = _build_session(
        interpreter=real_interpreter,
        narrator=real_narrator,
        location=location,
        npcs={marc1.name: marc1, marc2.name: marc2},
    )
    bot = _build_bot()
    bot.sessions = {1: session}
    cog = ActionHandlerCog(bot)

    channel = FakeChannel(id=1)
    msg = FakeMessage(
        content="<@9999> je parle à Marc",
        author=FakeAuthor(id=1),
        channel=channel,
        mentions=[bot.user],
    )

    await cog.on_message(msg)  # type: ignore[arg-type]

    progress_msg = channel.send.return_value
    edit_calls_with_view = [
        call for call in progress_msg.edit.call_args_list
        if call.kwargs.get("view") is not None
    ]
    assert edit_calls_with_view, "expected an edit() call attaching ClarificationView"
    attached_view = edit_calls_with_view[0].kwargs["view"]
    assert isinstance(attached_view, ClarificationView)
    # Two candidate buttons + one cancel button
    assert len(attached_view.children) == 3


# ---------------------------------------------------------------------------
# Scenario 3 — Unknown entity → in-character refusal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_unknown_entity_dragon(
    httpx_mock: HTTPXMock,
    real_interpreter: Interpreter,
    real_narrator: Narrator,
    cathedral: Location,
    ability_scores: AbilityScores,
) -> None:
    """`@bot je parle au dragon` → narrator gently refuses in-character."""
    aldric = _npc("Père Aldric", "Place de la Cathédrale", ability_scores)

    # 1) Interpreter classifies the action as TALK toward "Dragon"
    httpx_mock.add_response(
        url=CHAT_URL,
        json=make_ollama_response(
            {
                "action_type": "Talk",
                "actor_name": "Aldric",
                "target_name": "Dragon",
                "confidence": 0.5,
            },
        ),
    )
    # 2) Narrator generates the in-character refusal
    httpx_mock.add_response(
        url=CHAT_URL,
        json=make_ollama_response(
            {
                "narrative": "Tu scrutes les alentours mais aucun dragon ne se montre.",
                "tone": "somber",
            },
        ),
    )

    session = _build_session(
        interpreter=real_interpreter,
        narrator=real_narrator,
        location=cathedral,
        npcs={aldric.name: aldric},
    )
    bot = _build_bot()
    bot.sessions = {1: session}
    cog = ActionHandlerCog(bot)

    channel = FakeChannel(id=1)
    msg = FakeMessage(
        content="<@9999> je parle au dragon",
        author=FakeAuthor(id=1),
        channel=channel,
        mentions=[bot.user],
    )

    await cog.on_message(msg)  # type: ignore[arg-type]

    final_embed = _final_embed_from(channel)
    assert final_embed is not None
    description = final_embed.description or ""
    assert "dragon" in description.lower()
    # Mechanics field should hint that the entity was not found
    field_values = " ".join(str(f.value) for f in final_embed.fields)
    assert "Dragon" in field_values or "introuvable" in field_values.lower()


# ---------------------------------------------------------------------------
# Scenario 4 — OOC noise is filtered without an LLM call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_ooc_noise_does_not_call_llm(
    httpx_mock: HTTPXMock,
    real_interpreter: Interpreter,
    real_narrator: Narrator,
    cathedral: Location,
    ability_scores: AbilityScores,
) -> None:
    """`@bot merci !` → polite reply, NO request to Ollama."""
    aldric = _npc("Père Aldric", "Place de la Cathédrale", ability_scores)

    session = _build_session(
        interpreter=real_interpreter,
        narrator=real_narrator,
        location=cathedral,
        npcs={aldric.name: aldric},
    )
    bot = _build_bot()
    bot.sessions = {1: session}
    cog = ActionHandlerCog(bot)

    channel = FakeChannel(id=1)
    msg = FakeMessage(
        content="<@9999> merci",
        author=FakeAuthor(id=1),
        channel=channel,
        mentions=[bot.user],
    )

    await cog.on_message(msg)  # type: ignore[arg-type]

    msg.reply.assert_called_once()
    # No progress embed should have been posted at all
    channel.send.assert_not_called()
    # Crucially: no Ollama HTTP traffic
    chat_requests = [r for r in httpx_mock.get_requests() if r.url == CHAT_URL]
    assert chat_requests == []


# ---------------------------------------------------------------------------
# Scenario 5 — Creative IMPROVISE during combat
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_creative_combat_improvise(
    httpx_mock: HTTPXMock,
    real_interpreter: Interpreter,
    real_narrator: Narrator,
    cathedral: Location,
    ability_scores: AbilityScores,
) -> None:
    """In combat, `@bot je saute sur la table` → IMPROVISE → narration."""
    pc_char = create_character(
        "Aldric", Race.HUMAN, CharacterClass.FIGHTER, ability_scores,
    )
    enemy_char = create_character(
        "Goblin", Race.HALFLING, CharacterClass.ROGUE, ability_scores,
    )
    pc = Combatant(
        name="Aldric",
        side=CombatSide.PLAYER,
        character=pc_char,
        inventory=Inventory(),
    )
    enemy = Combatant(
        name="Goblin",
        side=CombatSide.ENEMY,
        character=enemy_char,
        inventory=Inventory(),
    )
    combat = CombatState(
        combatants=[pc, enemy], round_number=2, current_turn_index=0,
    )

    httpx_mock.add_response(
        url=CHAT_URL,
        json=make_ollama_response(
            {
                "action_type": "Improvise",
                "actor_name": "Aldric",
                "improvise_description": "saute sur la table et la renverse",
                "confidence": 0.8,
            },
        ),
    )
    httpx_mock.add_response(
        url=CHAT_URL,
        json=make_ollama_response(
            {
                "narrative": "Aldric bondit sur la table et la renverse en hurlant.",
                "tone": "tense",
            },
        ),
    )

    session = _build_session(
        interpreter=real_interpreter,
        narrator=real_narrator,
        location=cathedral,
        npcs={},
        combat_state=combat,
    )
    bot = _build_bot()
    bot.sessions = {1: session}
    cog = ActionHandlerCog(bot)

    channel = FakeChannel(id=1)
    msg = FakeMessage(
        content="<@9999> je saute sur la table et je la renverse",
        author=FakeAuthor(id=1),
        channel=channel,
        mentions=[bot.user],
    )

    await cog.on_message(msg)  # type: ignore[arg-type]

    final_embed = _final_embed_from(channel)
    assert final_embed is not None
    description = final_embed.description or ""
    assert "table" in description.lower()
    field_values = " ".join(str(f.value) for f in final_embed.fields)
    assert "improvis" in field_values.lower()
