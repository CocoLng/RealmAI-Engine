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
    narrator.template_narration.assert_called_once_with(
        "Talk", "Le PNJ est mort.", "fr",
    )


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


def test_snapshot_carries_combat_zone_names_from_real_location() -> None:
    """C1 — the snapshot reads ``Location.combat_zones`` (the real field),
    not the non-existent ``.zones``, so the zone rule gets a populated valid
    set instead of an always-empty one. Uses a REAL Location, not a Mock."""
    from engine.inventory import Inventory
    from world.combat_zone import Zone
    from world.location import Location

    session = MagicMock()
    session.npcs = {}
    session.characters = {}
    session.current_location = Location(
        name="Nef effondrée",
        combat_zones=[
            Zone(name="autel", adjacent_zone_names=["nef"]),
            Zone(name="nef", adjacent_zone_names=["autel"]),
        ],
    )
    session.combat_state = MagicMock(is_active=True)
    session.story_arc = None
    snap = narrate.build_coherence_snapshot(
        session, actor_name="Kael", inventory=Inventory(), moved_this_turn=False,
    )
    assert snap.combat_active is True
    assert set(snap.combat_zones) == {"autel", "nef"}


def test_kill_turn_grace_then_block_on_a_later_turn() -> None:
    """C2 seam — the NPC defeated THIS turn is excluded from the snapshot's
    dead set (its own death narration must pass), and the guard registry is
    refreshed only at end-of-turn, so the SAME NPC acting on a later turn is
    blocked once the registry re-arms."""
    from engine.inventory import Inventory

    narration_guard.reset("camp-seam")
    session = MagicMock()
    gor = MagicMock()
    gor.name = "Gor"
    gor.is_alive = False
    session.npcs = {"Gor": gor}
    session.characters = {}
    session.current_location = None
    session.combat_state = None
    session.story_arc = None

    # Turn N — Gor dies this turn: excluded from the snapshot's dead set,
    # and the guard registry has not been refreshed yet.
    snap_kill = narrate.build_coherence_snapshot(
        session, actor_name="Kael", inventory=Inventory(),
        moved_this_turn=False, freshly_dead=["Gor"],
    )
    assert "Gor" not in snap_kill.dead_npcs
    verdict_kill = narration_guard.check_narration(
        "camp-seam", narrative="Gor s'effondre, mort.",
        snapshot=snap_kill, npcs_mentioned=[],
    )
    assert verdict_kill.blocking == []

    # End of turn N — update_memory_after_turn refreshes the dead registry.
    narration_guard.set_dead_npcs("camp-seam", ["Gor"])

    # Turn N+1 — Gor is no longer freshly dead and the registry re-arms the
    # block, so Gor acting is now caught as a resurrection.
    snap_next = narrate.build_coherence_snapshot(
        session, actor_name="Kael", inventory=Inventory(), moved_this_turn=False,
    )
    assert "Gor" in snap_next.dead_npcs
    verdict_next = narration_guard.check_narration(
        "camp-seam", narrative="Gor attaque avec fureur.",
        snapshot=snap_next, npcs_mentioned=[],
    )
    assert "R1.npc_status" in {v.rule for v in verdict_next.blocking}
    narration_guard.reset("camp-seam")


def test_locked_fact_ids_restricts_snapshot_to_pre_turn_facts() -> None:
    """Mitigation 5a — a fact minted by THIS turn's beat completion is
    excluded from the snapshot (so it is not checked against the very
    narration that reveals it); pre-existing facts stay in scope, and the
    default of ``None`` keeps every fact."""
    from engine.inventory import Inventory
    from world.story_arc import LockedFact

    session = MagicMock()
    session.npcs = {}
    session.characters = {}
    session.current_location = None
    session.combat_state = None
    arc = MagicMock()
    arc.locked_facts = [
        LockedFact(id="beat:1:0", text="Le roi est mort."),    # pre-existing
        LockedFact(id="beat:2:0", text="Le pont est gardé."),  # minted this turn
    ]
    session.story_arc = arc

    snap = narrate.build_coherence_snapshot(
        session, actor_name="Kael", inventory=Inventory(),
        moved_this_turn=False, locked_fact_ids={"beat:1:0"},
    )
    assert [f.id for f in snap.locked_facts] == ["beat:1:0"]

    snap_all = narrate.build_coherence_snapshot(
        session, actor_name="Kael", inventory=Inventory(), moved_this_turn=False,
    )
    assert {f.id for f in snap_all.locked_facts} == {"beat:1:0", "beat:2:0"}
