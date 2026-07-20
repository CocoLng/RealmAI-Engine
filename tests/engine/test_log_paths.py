"""Runtime log sinks must be redirectable away from the production tree."""

from __future__ import annotations

import json

from engine.log_paths import DEFAULT_LOG_DIR, LOG_DIR_ENV, log_dir


class TestLogDir:
    def test_defaults_to_logs(self, monkeypatch) -> None:
        monkeypatch.delenv(LOG_DIR_ENV, raising=False)
        assert log_dir().name == DEFAULT_LOG_DIR

    def test_env_override_wins(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv(LOG_DIR_ENV, str(tmp_path))
        assert log_dir() == tmp_path

    def test_resolved_at_call_time(self, monkeypatch, tmp_path) -> None:
        """Setting the env var after import must still take effect."""
        monkeypatch.setenv(LOG_DIR_ENV, str(tmp_path / "a"))
        first = log_dir()
        monkeypatch.setenv(LOG_DIR_ENV, str(tmp_path / "b"))
        assert log_dir() != first


class TestBeatProgressionTelemetryIsRedirected:
    """`log_decision` must never touch real production telemetry from tests."""

    def _result(self):
        from engine.beat_progression import BeatProgress, BeatProgressionResult
        from world.story_arc import StoryBeat

        return BeatProgressionResult(
            decision="STAY",
            progress=BeatProgress(
                beat=StoryBeat(
                    beat_number=1,
                    title="X",
                    description="A placeholder beat for telemetry testing.",
                    location_hint="Somewhere",
                    encounter_type="exploration",
                ),
                objective_states={},
                progress_score=0,
                last_action_advanced=False,
            ),
            reasons=["no_match"],
        )

    def test_writes_under_the_configured_log_dir(self, monkeypatch, tmp_path) -> None:
        from engine.beat_progression import log_decision

        monkeypatch.setenv(LOG_DIR_ENV, str(tmp_path))
        log_decision(campaign_id="c1", beat_number=1, result=self._result())

        target = tmp_path / "beat_progression.jsonl"
        assert target.exists(), "telemetry did not follow REALM_LOG_DIR"
        record = json.loads(target.read_text().strip())
        assert record["campaign_id"] == "c1"
        assert record["decision"] == "STAY"

    def test_does_not_write_to_the_default_tree(self, monkeypatch, tmp_path) -> None:
        from engine.beat_progression import log_decision

        monkeypatch.setenv(LOG_DIR_ENV, str(tmp_path))
        log_decision(campaign_id="c2", beat_number=2, result=self._result())

        assert not (tmp_path.parent / "logs").exists()
