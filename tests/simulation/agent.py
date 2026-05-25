"""AutonomousAgent — observes game state and chooses actions via the 4b LLM."""

from __future__ import annotations

from typing import Any


def build_observation(
    *,
    turn: int,
    session: Any,
    last_actions: list[str],
    last_narration: str,
) -> str:
    """Build a compact text observation for the agent prompt.

    Pulls from the session-like object: character, location, inventory, combat.
    """
    char = session.character
    loc = session.location
    lines: list[str] = []
    lines.append(f"TURN {turn}")
    lines.append(
        f"You play: {char.name} ({char.race}, {char.char_class}, lvl {char.level}, "
        f"HP {char.hp}/{char.max_hp}, AC {char.ac})"
    )
    exits_str = (
        ", ".join(f"{d} → {tgt}" for d, tgt in loc.exits.items()) if loc.exits else "none"
    )
    lines.append(f"Location: {loc.name}. Exits: {exits_str}")

    equipped = getattr(session, "equipped", {}) or {}
    if equipped:
        equipped_str = ", ".join(f"{slot}: {item}" for slot, item in equipped.items())
        lines.append(f"Equipped: {equipped_str}")

    inv = getattr(session, "inventory_items", []) or []
    if inv:
        lines.append("Inventory: " + ", ".join(inv[:15]))

    if session.combat_active and session.combat is not None:
        lines.append("Combat: IN COMBAT, your turn")
        for enemy in session.combat.enemies:
            ratio = enemy.hp / enemy.max_hp if enemy.max_hp else 1.0
            bloodied = " (BLOODIED)" if ratio < 0.5 else ""
            lines.append(
                f"  - {enemy.name}: HP {enemy.hp}/{enemy.max_hp} AC {enemy.ac} "
                f"zone \"{enemy.zone}\"{bloodied}"
            )
    else:
        lines.append("Combat: not in combat")

    npcs = getattr(session, "npcs_present", []) or []
    if npcs:
        lines.append("NPCs present: " + ", ".join(npcs))
    else:
        lines.append("NPCs present: none")

    if last_actions:
        lines.append("Last 3 turns: " + ", ".join(last_actions[-3:]))
    else:
        lines.append("Last 3 turns: -")

    if last_narration:
        snippet = last_narration.strip().replace("\n", " ")[:200]
        lines.append(f'Last narration: "{snippet}"')

    return "\n".join(lines)
