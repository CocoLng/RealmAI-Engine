"""Tests for BeatJudge — LLM 4b judge with whitelist post-process and cooldown."""

from unittest.mock import MagicMock


from ai.beat_judge import BeatJudge
from ai.client import LLMParseError
from ai.models import InterpretedAction
from engine.beat_progression import JudgeRequest, ObjectivePartialMatch
from engine.validators import ActionType
from world.story_arc import ObjectiveKind


def _request(objective_ids: list[str]) -> JudgeRequest:
    return JudgeRequest(
        beat_title="Find Kaelen",
        beat_description="Players need to interrogate Kaelen at the forge.",
        beat_judge_rubric="Accept any creative way to make Kaelen reveal info.",
        objectives=[
            ObjectivePartialMatch(
                id=oid, kind=ObjectiveKind.TALK, target="Kaelen",
                description="Speak with Kaelen",
                match_score=0.6, gate_failed=False, gate_kind=None,
            )
            for oid in objective_ids
        ],
        player_action_text="I bribe Kaelen with gold to talk",
        interpreted_action=InterpretedAction(
            action_type=ActionType.TALK, actor_name="hero",
            target_name="Kaelen", raw_input="I bribe Kaelen with gold to talk",
        ),
        outcome_summary="Kaelen accepts the bribe",
        location_name="Forge",
        npcs_present=["Kaelen"],
    )


def test_judge_passes_when_llm_says_passed_high_confidence():
    client = MagicMock()
    client.chat_json.return_value = {
        "passed": True,
        "confidence": 0.85,
        "objectives_satisfied": ["talk_kaelen"],
        "reasoning": "The bribe got Kaelen to speak.",
        "suggested_next_action": None,
    }
    judge = BeatJudge(client)
    judge.begin_turn(turn_id="t1")
    resp = judge.evaluate(_request(["talk_kaelen"]))
    assert resp.passed is True
    assert resp.confidence == 0.85
    assert resp.objectives_satisfied == ["talk_kaelen"]


def test_judge_strips_hallucinated_objective_ids():
    """If the LLM returns an objective_id not in the input, it must be removed."""
    client = MagicMock()
    client.chat_json.return_value = {
        "passed": True,
        "confidence": 0.8,
        "objectives_satisfied": ["talk_kaelen", "HALLUCINATED_ID"],
        "reasoning": "...",
        "suggested_next_action": None,
    }
    judge = BeatJudge(client)
    judge.begin_turn(turn_id="t1")
    resp = judge.evaluate(_request(["talk_kaelen"]))
    assert resp.objectives_satisfied == ["talk_kaelen"]
    assert "HALLUCINATED_ID" not in resp.objectives_satisfied


def test_judge_rejects_passed_with_low_confidence():
    """passed=True but confidence<0.7 must be reported faithfully — caller applies threshold."""
    client = MagicMock()
    client.chat_json.return_value = {
        "passed": True,
        "confidence": 0.5,
        "objectives_satisfied": ["talk_kaelen"],
        "reasoning": "Maybe.",
        "suggested_next_action": None,
    }
    judge = BeatJudge(client)
    judge.begin_turn(turn_id="t1")
    resp = judge.evaluate(_request(["talk_kaelen"]))
    # The class doesn't downgrade — it returns the raw response, and the
    # CALLER applies the >=0.7 threshold. Verify both fields are reported faithfully.
    assert resp.passed is True
    assert resp.confidence == 0.5
    # The downstream policy uses both:
    accepted = resp.passed and resp.confidence >= 0.7
    assert accepted is False


def test_judge_handles_llm_parse_error():
    client = MagicMock()
    client.chat_json.side_effect = LLMParseError(
        "bad json",
        raw_response="not json",
        model="qwen3.5:4b",
        messages=[],
    )
    judge = BeatJudge(client)
    judge.begin_turn(turn_id="t1")
    resp = judge.evaluate(_request(["talk_kaelen"]))
    assert resp.passed is False
    assert resp.reasoning == "judge_error"


def test_judge_handles_timeout():
    client = MagicMock()
    client.chat_json.side_effect = TimeoutError()
    judge = BeatJudge(client)
    judge.begin_turn(turn_id="t1")
    resp = judge.evaluate(_request(["talk_kaelen"]))
    assert resp.passed is False
    assert resp.reasoning == "judge_timeout"


def test_judge_cooldown_returns_cached_or_skip():
    """Two evaluate() calls in the same turn should only fire ONE LLM call."""
    client = MagicMock()
    client.chat_json.return_value = {
        "passed": False,
        "confidence": 0.0,
        "objectives_satisfied": [],
        "reasoning": "no",
        "suggested_next_action": None,
    }
    judge = BeatJudge(client)
    judge.begin_turn(turn_id="t1")
    judge.evaluate(_request(["talk_kaelen"]))
    judge.evaluate(_request(["talk_kaelen"]))
    # Only one LLM call this turn.
    assert client.chat_json.call_count == 1


def test_judge_cooldown_resets_on_new_turn():
    """begin_turn() with a new turn_id should reset the cooldown."""
    client = MagicMock()
    client.chat_json.return_value = {
        "passed": False, "confidence": 0.0,
        "objectives_satisfied": [],
        "reasoning": "no", "suggested_next_action": None,
    }
    judge = BeatJudge(client)
    judge.begin_turn(turn_id="t1")
    judge.evaluate(_request(["talk_kaelen"]))
    judge.begin_turn(turn_id="t2")
    judge.evaluate(_request(["talk_kaelen"]))
    assert client.chat_json.call_count == 2
