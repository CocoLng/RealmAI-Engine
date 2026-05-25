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
