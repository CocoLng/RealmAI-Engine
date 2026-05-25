"""Hard incoherence rules (R1.*) — direct contradiction with engine state."""

from __future__ import annotations

import re
from typing import Any

from tests.simulation.records import IncoherenceAlert

# Active-verb patterns (French) that suggest the NPC is acting/speaking.
_NPC_ACTIVE_PATTERN = re.compile(
    r"\b(parle|dit|s'?ad?dresse|attaque|s'avance|sourit|hoche|crie|murmure|"
    r"r[ée]pond|demande|propose|tend|frappe|lance)\b",
    re.IGNORECASE,
)


def _snippet_around(text: str, needle: str, radius: int = 80) -> str:
    """Return up to 200 chars around the first occurrence of needle."""
    idx = text.lower().find(needle.lower())
    if idx < 0:
        return text[:200]
    start = max(0, idx - radius)
    end = min(len(text), idx + len(needle) + radius)
    snippet = text[start:end].strip()
    return snippet[:200]


def check_npc_status(
    narration: str,
    state: Any,
    diff: dict[str, list[Any]],
    history: list[Any],
) -> list[IncoherenceAlert]:
    """R1.npc_status — a dead NPC speaks or acts in the narration."""
    alerts: list[IncoherenceAlert] = []
    for npc in state.npcs.values():
        is_dead = npc.status == "dead" or npc.hp <= 0
        if not is_dead:
            continue
        # NPC name must appear AND an active verb must appear nearby in the same sentence.
        if npc.name.lower() not in narration.lower():
            continue
        # Check each sentence containing the NPC name for an active verb.
        for sentence in re.split(r"[.!?]", narration):
            if npc.name.lower() not in sentence.lower():
                continue
            if _NPC_ACTIVE_PATTERN.search(sentence):
                alerts.append(
                    IncoherenceAlert(
                        severity="hard",
                        category="dead_npc_speaks",
                        turn=getattr(state, "current_turn", 0),
                        rule="R1.npc_status",
                        narration_snippet=_snippet_around(narration, npc.name),
                        expected=f"{npc.name} is dead (status={npc.status}, hp={npc.hp})",
                    )
                )
                break
    return alerts
