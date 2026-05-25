"""CLI: uv run python -m tests.simulation [args]

Wires a full SimulationRunner with real Ollama-backed AutonomousAgent + GameDriver
+ IncoherenceChecker + Recorder and runs the loop end-to-end.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from ai.client import OllamaClient
from db.database import Base
from tests.scenarios.scenario_runner import ScenarioRunner
from tests.simulation.agent import AutonomousAgent, build_observation
from tests.simulation.checker import IncoherenceChecker
from tests.simulation.driver import GameDriver
from tests.simulation.runner import SimulationConfig, SimulationRunner

logger = logging.getLogger(__name__)


def _make_run_dir(seed: int) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    p = Path("tests/simulation/runs") / f"{ts}__seed{seed}"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m tests.simulation")
    parser.add_argument("--max-turns", type=int, default=30)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument(
        "--policy",
        choices=["balanced", "combat_focused", "story_focused"],
        default="balanced",
    )
    parser.add_argument("--agent-temp", type=float, default=0.3)
    parser.add_argument("--max-wall-time", type=int, default=600)
    parser.add_argument("--alert-budget", type=int, default=5)
    parser.add_argument(
        "--fail-on", choices=["none", "hard", "any"], default="none"
    )
    parser.add_argument("--batch", type=int, default=1)
    parser.add_argument("--keep-db", action="store_true")
    parser.add_argument("--mock-llm", action="store_true")
    parser.add_argument("--config", type=str, default=None)
    return parser.parse_args()


def _snapshot_from_session(session) -> dict:
    """Build a JSON-serializable dict from the GameSession."""
    if session is None:
        return {}
    char = session.characters[0] if session.characters else None
    snap = {
        "campaign_id": session.campaign.id if session.campaign else None,
        "location": getattr(session.current_location, "name", None),
        "combat_active": session.combat_state is not None,
    }
    if char is not None:
        snap["character_name"] = char.name
        snap["character_hp"] = char.hp
        snap["character_max_hp"] = char.max_hp
    return snap


async def _run_once(args: argparse.Namespace, seed: int) -> int:
    random.seed(seed)
    run_dir = _make_run_dir(seed)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(run_dir / "system.log", encoding="utf-8"),
            logging.StreamHandler(sys.stderr),
        ],
    )
    logger.info("Starting run seed=%d run_dir=%s", seed, run_dir)

    # In-memory DB per run
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db_factory = sessionmaker(bind=engine)

    client = OllamaClient(simulation_mode=True)
    scenario = ScenarioRunner(
        db_factory, ai_enabled=True, ollama_client=client
    )
    await scenario.start_campaign(theme="Simulation", players=1)
    await scenario.add_player(
        "Aria", race="Elf", class_="Wizard", player_idx=0
    )

    agent = AutonomousAgent(
        client=client, model="qwen3.5:4b", temperature=args.agent_temp
    )
    driver = GameDriver(scenario_runner=scenario)
    checker = IncoherenceChecker()
    config = SimulationConfig(
        seed=seed,
        max_turns=args.max_turns,
        run_dir=run_dir,
        max_wall_time_s=args.max_wall_time,
        alert_budget=args.alert_budget,
        policy=args.policy,
    )

    runner = SimulationRunner(
        config=config,
        agent=agent,
        driver=driver,
        checker=checker,
        session_snapshot=lambda: _snapshot_from_session(scenario.session),
    )

    # Override the default _build_observation with the real one
    def _real_observation(turn: int) -> str:
        sess = scenario.session
        if sess is None:
            return f"TURN {turn}\n(no session)"
        char = sess.characters[0]
        loc = sess.current_location
        return build_observation(
            turn=turn,
            session=type(
                "_S",
                (),
                {
                    "character": char,
                    "location": loc,
                    "inventory_items": [],
                    "equipped": {},
                    "combat_active": sess.combat_state is not None,
                    "combat": sess.combat_state,
                    "npcs_present": [],
                },
            )(),
            last_actions=[h["intent_action"] for h in runner._history[-3:]],
            last_narration=(runner._history[-1]["narration"] if runner._history else ""),
        )

    runner._build_observation = _real_observation  # type: ignore[assignment]

    status = await runner.run()

    # Determine exit code
    exit_code = 0
    all_alerts = [a for rec in runner.recorder.records for a in rec.alerts]
    if args.fail_on == "any" and all_alerts:
        exit_code = 1
    elif args.fail_on == "hard" and any(a.severity == "hard" for a in all_alerts):
        exit_code = 1
    if status in {"pipeline_error", "wall_time_exceeded"}:
        exit_code = max(exit_code, 1)

    if not args.keep_db:
        engine.dispose()
    print(f"DONE: status={status} run_dir={run_dir} exit_code={exit_code}")
    return exit_code


def main() -> int:
    args = _parse_args()
    if args.config:
        cfg = json.loads(Path(args.config).read_text())
        for key, value in cfg.items():
            if hasattr(args, key) and value is not None:
                setattr(args, key, value)

    base_seed = args.seed if args.seed is not None else random.randint(1, 1_000_000)
    code = 0
    for i in range(args.batch):
        seed = base_seed + i
        c = asyncio.run(_run_once(args, seed))
        code = max(code, c)
    return code


if __name__ == "__main__":
    sys.exit(main())
