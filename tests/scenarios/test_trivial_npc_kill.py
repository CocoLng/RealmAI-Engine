"""Lot E — trivial NPC death scenario.

Direct ActionPipeline test (mocked Interpreter/Narrator). Ensures that
attacking a peaceful low-HP NPC skips the combat-state machinery, kills
the target, removes them from the location, flips friendly witnesses
HOSTILE, and writes a world-fact line + story bible event.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from ai.models import InterpretedAction, NarrativeResult
from bot.action_pipeline import ActionPipeline
from bot.story_bible_logger import StoryBibleLogger
from engine.character import (
    AbilityScores,
    CharacterClass,
    Race,
    create_character,
)
from engine.inventory import (
    DamageType,
    EquipmentSlot,
    Inventory,
    Weapon,
    WeaponCategory,
)
from world.location import Location
from world.npc import NPC, NPCDisposition


@pytest.mark.asyncio
async def test_trivial_kill_propagates_death(tmp_path, monkeypatch) -> None:
    # Run from a temp cwd so the world-fact markdown is written there.
    monkeypatch.chdir(tmp_path)

    scores = AbilityScores(STR=16, DEX=12, CON=12, INT=10, WIS=10, CHA=10)
    pc = create_character("Aldric", Race.HUMAN, CharacterClass.FIGHTER, scores)
    sword = Weapon(
        name="Longsword",
        damage_dice="1d8",
        damage_type=DamageType.SLASHING,
        weapon_category=WeaponCategory.MARTIAL_MELEE,
        weight=3.0,
    )
    inventory = Inventory(equipped={EquipmentSlot.MAIN_HAND: sword})

    jeanne = NPC(
        name="Jeanne",
        race=Race.HUMAN,
        char_class=None,
        ability_scores=AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10),
        hp=4,
        max_hp=4,
        ac=10,
        disposition=NPCDisposition.FRIENDLY,
        description="Une villageoise désarmée.",
        location_name="Place de la Cathédrale",
    )
    pere_thomas = NPC(
        name="Père Thomas",
        race=Race.HUMAN,
        char_class=None,
        ability_scores=AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=12, CHA=12),
        hp=6,
        max_hp=6,
        ac=10,
        disposition=NPCDisposition.FRIENDLY,
        description="Le prêtre du village.",
        location_name="Place de la Cathédrale",
    )

    location = Location(
        name="Place de la Cathédrale",
        description="Une vaste place pavée.",
        connections=[],
        npcs_present=["Jeanne", "Père Thomas"],
        items_available=[],
    )

    npcs = {jeanne.name: jeanne, pere_thomas.name: pere_thomas}

    story_bible = StoryBibleLogger("cmp-trivial", log_dir=tmp_path / "bible")

    session = SimpleNamespace(
        characters={1: pc},
        inventories={1: inventory},
        spellcasters={1: None},
        combat_state=None,
        current_location=location,
        npcs=npcs,
        language="fr",
        campaign=SimpleNamespace(id="cmp-trivial"),
        story_bible=story_bible,
    )

    interpreter = MagicMock()
    interpreter.interpret.return_value = InterpretedAction(
        action_type="Attack",  # type: ignore[arg-type]
        actor_name="Aldric",
        target_name="Jeanne",
        weapon_name="Longsword",
        raw_input="je tue Jeanne",
        confidence=0.95,
    )

    narrator = MagicMock()
    narrator.narrate.return_value = NarrativeResult(
        narrative="Jeanne s'effondre dans une mare de sang.",
        tone="somber",
    )

    pipeline = ActionPipeline(
        interpreter=interpreter,
        narrator=narrator,
        location=location,
        npcs=npcs,
        actor_name="Aldric",
        language="fr",
        campaign_id="cmp-trivial",
        combat_state=None,
        inventory=inventory,
        session=session,  # type: ignore[arg-type]
        db_factory=None,
    )

    result = await pipeline.process("je tue Jeanne")

    # No combat state was bootstrapped — the whole point of trivial resolve.
    assert session.combat_state is None
    # Pipeline succeeded.
    assert result.__class__.__name__ == "ActionPipelineResult"
    # Narrator received the trivial-kill mechanics text, not a generic Attack stub.
    call_kwargs = narrator.narrate.call_args.kwargs
    mechanics_arg = call_kwargs.get("action_result_text", "")
    assert "Jeanne" in mechanics_arg
    assert "Aldric" in mechanics_arg

    # Jeanne is dead and gone from the scene.
    assert jeanne.is_alive is False
    assert jeanne.hp == 0
    assert "Jeanne" not in location.npcs_present
    assert jeanne.name not in pipeline.npcs

    # Friendly witness flipped HOSTILE.
    assert pere_thomas.disposition == NPCDisposition.HOSTILE

    # World fact markdown was written under logs/campaigns/.
    facts_path = Path("logs/campaigns") / "cmp-trivial_facts.md"
    assert facts_path.exists()
    facts_content = facts_path.read_text(encoding="utf-8")
    assert "Aldric" in facts_content
    assert "Jeanne" in facts_content

    # Story bible logged the event.
    bible_content = story_bible.path.read_text(encoding="utf-8")
    assert "MEURTRE" in bible_content
    assert "Jeanne" in bible_content
