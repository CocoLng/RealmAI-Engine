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
from engine.character import AbilityScores, Race
from engine.inventory import (
    DamageType,
    EquipmentSlot,
    ItemType,
    Rarity,
    Weapon,
    WeaponCategory,
    add_item,
    equip_item,
)
from tests.scenarios.scenario_runner import ScenarioRunner
from tests.simulation.agent import AutonomousAgent, build_observation
from tests.simulation.checker import IncoherenceChecker
from tests.simulation.driver import GameDriver
from tests.simulation.runner import SimulationConfig, SimulationRunner
from world.location import Location
from world.npc import NPC, NPCDisposition

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


def _seed_starting_world(scenario: ScenarioRunner, player_idx: int = 0) -> None:
    """Populate a minimal starting world so the agent has context to act on.

    Creates a starting Location with one NPC and one connection, equips the
    character with a starter weapon, and registers everything on the session.
    """
    session = scenario.session
    if session is None:
        raise RuntimeError("seed_starting_world: no active session")

    # Starting location
    start = Location(
        name="Entrée de la grotte",
        description=(
            "Une vaste cavité humide s'ouvre devant vous. Des stalactites "
            "luisent à la lueur d'une torche oubliée. Un passage étroit "
            "s'enfonce vers le nord."
        ),
        arrival_hook=(
            "Vous franchissez le seuil de la grotte, le bruit du vent "
            "remplace soudain le calme de l'extérieur."
        ),
        connections=["Salle des échos"],
        exit_aliases={"Salle des échos": ["nord", "north"]},
        npcs_present=["Garm"],
    )
    session.current_location = start

    # NPC: Garm — friendly survivor
    garm = NPC(
        name="Garm",
        race=Race.HUMAN,
        ability_scores=AbilityScores(STR=10, DEX=10, CON=10, INT=12, WIS=12, CHA=14),
        hp=8,
        max_hp=8,
        ac=10,
        disposition=NPCDisposition.FRIENDLY,
        description="Un voyageur usé par la route, blessé à l'épaule.",
        personality=(
            "Cordial mais nerveux. Garde un œil sur l'entrée comme si "
            "quelque chose le poursuivait."
        ),
        location_name="Entrée de la grotte",
        knowledge=["Il y a des gobelins plus profond dans la grotte."],
    )
    session.npcs[garm.name] = garm

    # Equip a starter weapon
    player = scenario._make_player(player_idx)
    inv = session.inventories.get(player.id)
    if inv is not None:
        sword = Weapon(
            name="Épée courte",
            item_type=ItemType.WEAPON,
            weight=2.0,
            rarity=Rarity.COMMON,
            value_gp=10,
            damage_dice="1d6",
            damage_type=DamageType.PIERCING,
            weapon_category=WeaponCategory.MARTIAL_MELEE,
        )
        inv = add_item(inv, sword)
        inv = equip_item(inv, sword.name, EquipmentSlot.MAIN_HAND)
        session.inventories[player.id] = inv


def _snapshot_from_session(session) -> dict:
    """Build a JSON-serializable dict from the GameSession."""
    if session is None:
        return {}
    char = next(iter(session.characters.values()), None) if session.characters else None
    npcs = {n.name: {"status": "alive" if n.is_alive else "dead", "hp": n.hp,
                     "disposition": n.disposition.value}
            for n in (session.npcs.values() if session.npcs else [])}
    snap = {
        "campaign_id": session.campaign.id if session.campaign else None,
        "location": getattr(session.current_location, "name", None),
        "combat_active": session.combat_state is not None,
        "npcs": npcs,
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

    if args.mock_llm:
        from tests.simulation.mock_llm import MockOllamaClient
        client = MockOllamaClient(simulation_mode=True)  # type: ignore[assignment]
    else:
        client = OllamaClient(simulation_mode=True)
    scenario = ScenarioRunner(
        db_factory, ai_enabled=True, ollama_client=client
    )
    await scenario.start_campaign(theme="Simulation", players=1)
    await scenario.add_player(
        "Aria", race="Elf", class_="Wizard", player_idx=0
    )
    _seed_starting_world(scenario, player_idx=0)

    agent = AutonomousAgent(
        client=client,
        model="qwen3.5:4b",
        temperature=args.agent_temp,
        policy=args.policy,
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
        char = next(iter(sess.characters.values()), None) if sess.characters else None
        loc = sess.current_location
        if char is None or loc is None:
            return f"TURN {turn}\n(no character or location)"

        # Pull inventory + equipment from the real session.
        player = scenario._make_player(0)
        inv = sess.inventories.get(player.id)
        inventory_items: list[str] = []
        equipped: dict[str, str] = {}
        if inv is not None:
            inventory_items = [item.name for item in inv.items]
            for slot, item in inv.equipped.items():
                if item is None:
                    continue
                slot_label = slot.value if hasattr(slot, "value") else str(slot)
                item_label = item.name if hasattr(item, "name") else str(item)
                equipped[slot_label] = item_label

        # NPCs present in the current location (intersect names with registry).
        npcs_present = [
            n for n in loc.npcs_present if n in sess.npcs and sess.npcs[n].is_alive
        ]

        # Map location.connections / exit_aliases into a {direction: target} dict
        # for the observation builder. Use the first alias of each connection if
        # available, otherwise the connection name itself.
        location_with_exits = type(
            "_Loc",
            (),
            {
                "name": loc.name,
                "exits": {
                    (loc.exit_aliases.get(target, [target])[0] if loc.exit_aliases.get(target) else target): target
                    for target in loc.connections
                },
            },
        )()

        return build_observation(
            turn=turn,
            session=type(
                "_S",
                (),
                {
                    "character": char,
                    "location": location_with_exits,
                    "inventory_items": inventory_items,
                    "equipped": equipped,
                    "combat_active": sess.combat_state is not None,
                    "combat": sess.combat_state,
                    "npcs_present": npcs_present,
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
