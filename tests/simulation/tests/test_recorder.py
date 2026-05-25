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
