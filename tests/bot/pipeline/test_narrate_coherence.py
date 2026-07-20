"""call_narrator × porte de cohérence : retry correctif puis template tier-3."""

from unittest.mock import MagicMock

import pytest

from ai.models import MechanicsOutcome, NarrativeResult
from bot.pipeline import narrate
from memory import narration_guard
from memory.coherence_rules import CoherenceSnapshot


def _result(text: str) -> NarrativeResult:
    return NarrativeResult(narrative=text, tone="dramatic")


def _narrator_returning(*texts: str) -> MagicMock:
    narrator = MagicMock()
    narrator.narrate.side_effect = [_result(t) for t in texts]
    narrator.template_narration.return_value = _result("[template] Le récit reprend.")
    return narrator


@pytest.fixture(autouse=True)
def _clean_guard():
    narration_guard.reset("camp-1")
    yield
    narration_guard.reset("camp-1")


@pytest.mark.asyncio
async def test_clean_narration_passes_through() -> None:
    narrator = _narrator_returning("Le vent souffle sur la lande déserte.")
    result = await narrate.call_narrator(
        narrator=narrator,
        outcome=MechanicsOutcome(summary="Look"),
        context_prompt="ctx", language="fr", campaign_id="camp-1",
        snapshot=CoherenceSnapshot(),
    )
    assert result.narrative == "Le vent souffle sur la lande déserte."
    assert narrator.narrate.call_count == 1


@pytest.mark.asyncio
async def test_blocking_violation_retries_with_constraint() -> None:
    narration_guard.set_dead_npcs("camp-1", ["Aldric"])
    narrator = _narrator_returning(
        "Aldric sourit et vous parle doucement.",   # tier 1 : violation
        "Un silence pesant s'installe, près du corps d'Aldric.",  # retry : propre
    )
    result = await narrate.call_narrator(
        narrator=narrator,
        outcome=MechanicsOutcome(summary="Talk"),
        context_prompt="ctx", language="fr", campaign_id="camp-1",
        snapshot=CoherenceSnapshot(),
    )
    assert "silence" in result.narrative
    assert narrator.narrate.call_count == 2
    # La contrainte du retry contient le fait attendu.
    amended = narrator.narrate.call_args_list[1].kwargs["action_result_text"]
    assert "CONTRAINTE" in amended and "Aldric" in amended


@pytest.mark.asyncio
async def test_double_failure_falls_back_to_template() -> None:
    narration_guard.set_dead_npcs("camp-1", ["Aldric"])
    narrator = _narrator_returning(
        "Aldric sourit et vous parle.",     # tier 1 : violation
        "Aldric attaque avec fureur.",       # retry : violation encore
    )
    result = await narrate.call_narrator(
        narrator=narrator,
        outcome=MechanicsOutcome(summary="Talk", outcome_facts="Le PNJ est mort."),
        context_prompt="ctx", language="fr", campaign_id="camp-1",
        snapshot=CoherenceSnapshot(),
    )
    assert result.narrative == "[template] Le récit reprend."
    narrator.template_narration.assert_called_once()


@pytest.mark.asyncio
async def test_observe_only_violation_never_retries() -> None:
    narrator = _narrator_returning("Soudain, Baldur surgit de nulle part.")
    result = await narrate.call_narrator(
        narrator=narrator,
        outcome=MechanicsOutcome(summary="Look"),
        context_prompt="ctx", language="fr", campaign_id="camp-1",
        snapshot=CoherenceSnapshot(known_npc_names=["Elara"]),
    )
    assert "Baldur" in result.narrative       # publié tel quel
    assert narrator.narrate.call_count == 1   # zéro retry


def test_build_coherence_snapshot_reads_session_state() -> None:
    from engine.inventory import Inventory
    session = MagicMock()
    npc_dead = MagicMock()
    npc_dead.name = "Aldric"
    npc_dead.is_alive = False
    npc_alive = MagicMock()
    npc_alive.name = "Elara"
    npc_alive.is_alive = True
    session.npcs = {"Aldric": npc_dead, "Elara": npc_alive}
    pc = MagicMock()
    pc.name = "Kael"
    pc.hp = 10
    pc.max_hp = 20
    session.characters = {1: pc}
    session.current_location.name = "Crypte"
    session.current_location.connections = ["Nef"]
    session.current_location.zones = []
    session.combat_state = None
    session.story_arc = None
    snap = narrate.build_coherence_snapshot(
        session, actor_name="Kael", inventory=Inventory(), moved_this_turn=False,
    )
    assert snap.dead_npcs == ["Aldric"]
    assert snap.known_npc_names == ["Aldric", "Elara"]
    assert snap.player_names == ["Kael"]
    assert snap.current_location == "Crypte"
    assert snap.known_locations == ["Crypte", "Nef"]
    assert snap.player_hp_ratio == 0.5
    assert snap.combat_active is False
