"""Regression test: rich player framing + canon scene reach the narrator."""

from unittest.mock import MagicMock

import pytest

from ai.models import InterpretedAction, MechanicsOutcome, NarrativeResult
from bot.action_pipeline import ActionPipeline
from engine.character import AbilityScores, Race
from engine.validators import ActionType
from world.location import Location
from world.npc import NPC, NPCDisposition


@pytest.mark.asyncio
async def test_search_passes_player_framing_and_canon_to_narrator() -> None:
    """The 'croix de 39-45' scenario from logs/campaigns/Test2.md.

    Asserts that when a player searches an item with a richly-framed
    raw_input, the narrator receives BOTH the player's framing AND the
    canonical scene description (location, items, item descriptions).
    """
    location = Location(
        name="La Paroisse de Saint-Michel",
        description="L'église paroissiale semble paisible.",
        items_available=["Croix de fer", "Cierge pourri"],
        item_descriptions={
            "Croix de fer": (
                "Vieille croix de forge médiévale, noircie par les ans."
            ),
        },
        npcs_present=["Élie l'Ermite"],
        connections=["Village de Valombre"],
    )
    npc = NPC(
        name="Élie l'Ermite",
        race=Race.HUMAN,
        char_class=None,
        level=1,
        ability_scores=AbilityScores(
            STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10,
        ),
        hp=4,
        max_hp=4,
        ac=10,
        disposition=NPCDisposition.NEUTRAL,
        is_alive=True,
        description="Vieil ermite voûté.",
        personality="Méfiant.",
        location_name="La Paroisse de Saint-Michel",
        aliases=[],
    )

    session = MagicMock()
    session.current_location = location
    session.npcs = {"Élie l'Ermite": npc}
    session.story_arc = None

    interpreted = InterpretedAction(
        action_type=ActionType.SEARCH,
        actor_name="Xavier Dupont de ligonesse",
        target_name="Croix de fer",
        raw_input="inspecte la croix de fer pour voir si c une d'origine de 39-45",
        confidence=0.95,
        search_detail="origine 39-45",
    )

    narrator = MagicMock()
    narrator.narrate.return_value = NarrativeResult(
        narrative="…", tone="tense",
    )
    interpreter = MagicMock()
    interpreter.interpret.return_value = interpreted

    pipeline = ActionPipeline(
        interpreter=interpreter,
        narrator=narrator,
        location=location,
        npcs=session.npcs,
        actor_name="Xavier Dupont de ligonesse",
        language="fr",
        campaign_id="test",
        session=session,
    )

    outcome = await pipeline._resolve_mechanics(interpreted)
    context = pipeline._assemble_context(interpreted)

    assert isinstance(outcome, MechanicsOutcome)
    assert outcome.summary == (
        "Xavier Dupont de ligonesse searches Croix de fer."
    )
    assert "39-45" in outcome.player_intent
    assert "search detail" in outcome.player_intent.lower()

    assert "La Paroisse de Saint-Michel" in context
    assert "Croix de fer" in context
    assert "forge médiévale" in context  # canon description survives
    assert "Cierge pourri" in context  # item without description still listed
    assert "Élie l'Ermite" in context
    assert "Village de Valombre" in context  # exit
