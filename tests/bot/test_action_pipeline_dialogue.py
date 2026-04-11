"""TALK action invokes NPCAgent and surfaces dialogue to the narrator."""

from unittest.mock import MagicMock

import pytest

from ai.models import (
    InterpretedAction,
    MechanicsOutcome,
    NPCResponse,
    NPCSheet,
)
from bot.action_pipeline import ActionPipeline
from engine.character import AbilityScores, Race
from engine.validators import ActionType
from world.location import Location
from world.npc import NPC, NPCDisposition


def _npc(name: str, *, personality: str = "", description: str = "") -> NPC:
    return NPC(
        name=name,
        race=Race.HUMAN,
        level=1,
        ability_scores=AbilityScores(
            STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10,
        ),
        hp=4,
        max_hp=4,
        ac=10,
        disposition=NPCDisposition.NEUTRAL,
        description=description,
        personality=personality,
        location_name="La Paroisse",
        aliases=[],
    )


@pytest.mark.asyncio
async def test_talk_invokes_npc_agent_and_threads_dialogue_to_outcome() -> None:
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

    session.npc_agent.respond.assert_called_once()
    call_kwargs = session.npc_agent.respond.call_args
    assert (
        call_kwargs.kwargs.get("npc") is npc
        or (call_kwargs.args and call_kwargs.args[0] is npc)
    )
    player_input = call_kwargs.kwargs.get("player_input") or (
        call_kwargs.args[1] if len(call_kwargs.args) > 1 else ""
    )
    assert "Elie" in player_input or "ce qui se passe" in player_input

    assert isinstance(outcome, MechanicsOutcome)
    # Dialogue is now carried separately, not in outcome_facts.
    assert outcome.npc_name == "Elie"
    assert outcome.npc_dialogue == "Approche, étranger. Que cherches-tu ici ?"
    # Revealed info still lives in outcome_facts.
    assert "Valombre" in outcome.outcome_facts
    # outcome_facts should NOT contain verbatim dialogue anymore.
    assert "Approche, étranger" not in outcome.outcome_facts

    assert npc.disposition == NPCDisposition.FRIENDLY  # NEUTRAL + 1

    assert len(npc.dialogue_history) == 1
    assert "Approche" in npc.dialogue_history[0].npc_said


@pytest.mark.asyncio
async def test_talk_lazy_generates_npc_sheet_when_personality_empty() -> None:
    location = Location(
        name="La Paroisse", description="…", npcs_present=["Elie"],
    )
    npc = _npc("Elie")  # empty personality + description
    session = MagicMock()
    session.current_location = location
    session.npcs = {"Elie": npc}
    session.story_arc = None
    session.campaign.id = "test"
    session.campaign.name = "sous une église"

    session.npc_generator = MagicMock()
    session.npc_generator.generate.return_value = NPCSheet(
        personality="Méfiant.",
        description="Vieil ermite voûté.",
        secrets=["Dom André est corrompu."],
        knowledge=["L'entrée de la crypte est sous l'autel."],
    )
    session.npc_agent = MagicMock()
    session.npc_agent.respond.return_value = NPCResponse(
        dialogue="Hmpf.", disposition_change=0, revealed_info=[],
    )

    interpreted = InterpretedAction(
        action_type=ActionType.TALK,
        actor_name="Xavier",
        target_name="Elie",
        raw_input="bonjour",
        confidence=0.95,
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
    await pipeline._resolve_mechanics(interpreted)

    session.npc_generator.generate.assert_called_once()
    assert npc.personality == "Méfiant."
    assert "Dom André" in npc.secrets[0]
