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


# Common French capitalized nouns that are NOT proper names (whitelist).
_PROPER_NOUN_WHITELIST: frozenset[str] = frozenset({
    "Le", "La", "Les", "L", "Un", "Une", "Des", "Du", "De", "Dans", "Sur",
    "Avec", "Sans", "Pour", "Par", "Vers", "Chez", "Vous", "Nous", "Il",
    "Elle", "Ils", "Elles", "Je", "Tu", "On", "Que", "Qui", "Quoi",
    "Dieu", "Dieux", "Roi", "Reine", "Capitaine", "Seigneur", "Dame",
    "Maître", "Madame", "Monsieur", "Père", "Mère", "Frère", "Sœur",
    "Or", "Mais", "Et", "Donc", "Car", "Aussi", "Si", "Alors", "Puis",
    "Tout", "Tous", "Toute", "Toutes", "Cette", "Ce", "Ces", "Ses",
    "Son", "Sa", "Leur", "Leurs", "Mon", "Ma", "Mes", "Notre", "Votre",
})

_PROPER_NOUN_RE = re.compile(r"\b([A-ZÉÈÊÀÂÔÛÎ][a-zéèêàâôûîç']{2,})\b")


_ITEM_USE_RE = re.compile(
    r"\b(utilise|boit|consomme|brandit|d[ée]gaine|enfile|active)\s+"
    r"(le|la|les|l'|un|une|des|sa|son|ses|ma|mon|mes|la grande|le grand)\s+"
    r"([A-Za-zÀ-ÿ' -]{3,40})",
    re.IGNORECASE,
)


def check_item_use_without_owning(
    narration: str,
    state: Any,
    diff: dict[str, list[Any]],
    history: list[Any],
) -> list[IncoherenceAlert]:
    """R1.item_use_without_owning — character uses an item missing from inventory."""
    alerts: list[IncoherenceAlert] = []
    inv = getattr(state, "inventory", None)
    if inv is None:
        return alerts
    owned = {item.lower() for item in getattr(inv, "items", [])}
    for match in _ITEM_USE_RE.finditer(narration):
        item_raw = match.group(3).strip().rstrip(".")
        item_text = item_raw.lower()
        if not item_text:
            continue
        # Match if any owned item name appears in the matched span.
        matched_owned = any(o in item_text or item_text in o for o in owned)
        if matched_owned:
            continue
        alerts.append(
            IncoherenceAlert(
                severity="hard",
                category="item_use_without_owning",
                turn=getattr(state, "current_turn", 0),
                rule="R1.item_use_without_owning",
                narration_snippet=_snippet_around(narration, match.group(0)),
                expected=f"Item '{item_raw}' is not in inventory (owned: {sorted(owned)})",
            )
        )
    return alerts


_WOUNDED_RE = re.compile(
    r"\b(agonise|chancelle|s'effondre|gri[èe]vement bless[ée]|au bord de la mort|"
    r"à l'agonie|mourant[e]?)\b",
    re.IGNORECASE,
)


def check_hp_mismatch(
    narration: str,
    state: Any,
    diff: dict[str, list[Any]],
    history: list[Any],
) -> list[IncoherenceAlert]:
    """R1.hp_mismatch — narration claims wounded/dying while player HP ≥ 80%."""
    match = _WOUNDED_RE.search(narration)
    if not match:
        return []
    ratio = getattr(state, "player_hp_ratio", 1.0)
    if ratio < 0.8:
        return []
    return [
        IncoherenceAlert(
            severity="hard",
            category="hp_mismatch",
            turn=getattr(state, "current_turn", 0),
            rule="R1.hp_mismatch",
            narration_snippet=_snippet_around(narration, match.group(0)),
            expected=(
                f"Player HP = {state.player_hp}/{state.player_max_hp} "
                f"(ratio {ratio:.2f}), but narration describes wounding"
            ),
        )
    ]


def check_location_mismatch(
    narration: str,
    state: Any,
    diff: dict[str, list[Any]],
    history: list[Any],
) -> list[IncoherenceAlert]:
    """R1.location_mismatch — a known location other than current is described as present."""
    current = getattr(state.current_location, "name", None)
    if current is None:
        return []
    if not history:
        return []
    last = history[-1] if isinstance(history[-1], dict) else {}
    if last.get("moved_this_turn"):
        return []
    known = last.get("location_known", []) or []
    alerts: list[IncoherenceAlert] = []
    narration_lower = narration.lower()
    for loc_name in known:
        if loc_name == current:
            continue
        if loc_name.lower() in narration_lower:
            alerts.append(
                IncoherenceAlert(
                    severity="hard",
                    category="location_mismatch",
                    turn=getattr(state, "current_turn", 0),
                    rule="R1.location_mismatch",
                    narration_snippet=_snippet_around(narration, loc_name),
                    expected=(
                        f"Current location is '{current}' and player did not move "
                        f"this turn, but narration mentions '{loc_name}'"
                    ),
                )
            )
    return alerts


def check_phantom_npc(
    narration: str,
    state: Any,
    diff: dict[str, list[Any]],
    history: list[Any],
) -> list[IncoherenceAlert]:
    """R1.phantom_npc — capitalized proper noun absent from NPC registry."""
    alerts: list[IncoherenceAlert] = []
    known_npcs = {n.lower() for n in state.npcs}
    known_players = {p.lower() for p in getattr(state, "player_names", [])}
    seen: set[str] = set()
    for match in _PROPER_NOUN_RE.finditer(narration):
        word = match.group(1)
        if word in _PROPER_NOUN_WHITELIST:
            continue
        lower = word.lower()
        if lower in known_npcs or lower in known_players:
            continue
        if lower in seen:
            continue
        seen.add(lower)
        alerts.append(
            IncoherenceAlert(
                severity="hard",
                category="phantom_npc",
                turn=getattr(state, "current_turn", 0),
                rule="R1.phantom_npc",
                narration_snippet=_snippet_around(narration, word),
                expected=f"Proper noun '{word}' is not in NPC registry or player names",
            )
        )
    return alerts
