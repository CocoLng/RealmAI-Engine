"""Pure narration-coherence rules shared by production and the simulator.

Ported from ``tests/simulation/rules/{hard,soft}.py`` (chantier « porte de
cohérence »). Rule ids and ``expected`` messages stay identical to the
simulator's so telemetry and simulation reports remain comparable.

No LLM calls, no I/O, no Discord/DB imports — pure functions only.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class LockedFactSnapshot(BaseModel):
    """(id, text) view of a locked fact — decoupled from world.story_arc."""

    id: str
    text: str


class CoherenceSnapshot(BaseModel):
    """Neutral input contract — built by each consumer (prod or simulator)."""

    dead_npcs: list[str] = Field(default_factory=list)
    known_npc_names: list[str] = Field(default_factory=list)
    player_names: list[str] = Field(default_factory=list)
    current_location: str | None = None
    known_locations: list[str] = Field(default_factory=list)
    moved_this_turn: bool = False
    actor_inventory: list[str] = Field(default_factory=list)
    player_hp_ratio: float = 1.0
    combat_active: bool = False
    combat_zones: list[str] = Field(default_factory=list)
    locked_facts: list[LockedFactSnapshot] = Field(default_factory=list)
    recent_narrations: list[str] = Field(default_factory=list)
    """Up to 5 previous narrations, oldest first (R2.repetition)."""
    npcs_mentioned: list[str] = Field(default_factory=list)
    """Narrator self-report for THIS narration — consumed by R1.npc_status only."""


class CoherenceViolation(BaseModel):
    rule: str
    severity: Literal["hard", "soft"]
    snippet: str = Field(max_length=200)
    expected: str


class RuleMode(StrEnum):
    BLOCK = "block"
    OBSERVE = "observe"


# --- Helpers (ported verbatim from tests/simulation/rules/hard.py) ---

_NPC_ACTIVE_PATTERN = re.compile(
    r"\b(parle|dit|s'?ad?dresse|attaque|s'avance|sourit|hoche|crie|murmure|"
    r"r[ée]pond|demande|propose|tend|frappe|lance)\b",
    re.IGNORECASE,
)

_PROPER_NOUN_WHITELIST: frozenset[str] = frozenset({
    "Le", "La", "Les", "L", "Un", "Une", "Des", "Du", "De", "Dans", "Sur",
    "Avec", "Sans", "Pour", "Par", "Vers", "Chez", "Vous", "Nous", "Il",
    "Elle", "Ils", "Elles", "Je", "Tu", "On", "Que", "Qui", "Quoi",
    "Dieu", "Dieux", "Roi", "Reine", "Capitaine", "Seigneur", "Dame",
    "Maître", "Madame", "Monsieur", "Père", "Mère", "Frère", "Sœur",
    "Or", "Mais", "Et", "Donc", "Car", "Aussi", "Si", "Alors", "Puis",
    "Tout", "Tous", "Toute", "Toutes", "Cette", "Ce", "Ces", "Ses",
    "Son", "Sa", "Leur", "Leurs", "Mon", "Ma", "Mes", "Notre", "Votre",
    "Soudain", "Cependant", "Surtout", "Toujours",
})

_PROPER_NOUN_RE = re.compile(r"\b([A-ZÉÈÊÀÂÔÛÎ][a-zéèêàâôûîç']{2,})\b")

_ITEM_USE_RE = re.compile(
    r"\b(utilise|boit|consomme|brandi[ts]|d[ée]gaine|enfile|active)\s+"
    r"(le|la|les|l'|un|une|des|sa|son|ses|ma|mon|mes|la grande|le grand)\s+"
    r"([A-Za-zÀ-ÿ' -]{3,40})",
    re.IGNORECASE,
)

_WOUNDED_RE = re.compile(
    r"\b(agonise|chancelle|s'effondre|gri[èe]vement bless[ée]|au bord de la mort|"
    r"à l'agonie|mourant[e]?)\b",
    re.IGNORECASE,
)

_NEGATION_RE = re.compile(
    r"\b(n['']\w*\s+(plus|pas|jamais)|n['']\s*(plus|pas|jamais)|aucun[e]?|"
    r"sans|d[ée]truit[e]?|effondr[ée]|disparu[e]?|ras[ée]|an[ée]anti[e]?)\b",
    re.IGNORECASE,
)

_ZONE_RE = re.compile(r"\bzone\s+([a-zà-ÿ]+(?:\s+[a-zà-ÿ]+)?)\b", re.IGNORECASE)


def _snippet_around(text: str, needle: str, radius: int = 80) -> str:
    """Return up to 200 chars around the first occurrence of needle."""
    idx = text.lower().find(needle.lower())
    if idx < 0:
        return text[:200]
    start = max(0, idx - radius)
    end = min(len(text), idx + len(needle) + radius)
    return text[start:end].strip()[:200]


def _canonical_names(names: list[str]) -> set[str]:
    """Lowercase set of names + first-word short forms (« Elara, la… » → « elara »)."""
    result: set[str] = set()
    for n in names:
        result.add(n.lower())
        words = n.split()
        if words:
            head = words[0].rstrip(",.;:!?")
            if head:
                result.add(head.lower())
    return result


def _location_words(locations: list[str]) -> set[str]:
    """Every word of every known location name, plus the full names."""
    result: set[str] = set()
    for loc in locations:
        result.add(loc.lower())
        for token in loc.split():
            cleaned = token.rstrip(",.;:!?")
            if cleaned:
                result.add(cleaned.lower())
    return result


def _name_variants(name: str) -> list[str]:
    """Full name + longest word ≥ 4 chars for multi-word names.

    Mirrors memory/narration_guard._name_patterns so « Père Aldric »
    also catches a narration that says just « Aldric »."""
    variants = {name}
    words = [w for w in name.split() if len(w) >= 4]
    if len(name.split()) > 1 and words:
        variants.add(max(words, key=len))
    return sorted(variants)


def _fact_subject(fact_text: str) -> str:
    """Noun-phrase subject of a locked fact (first 4 words, lowercased)."""
    return " ".join(fact_text.split()[:4]).rstrip(".").lower()


# --- Hard rules (R1.*) ---

def check_npc_status(
    narration: str, snap: CoherenceSnapshot,
) -> list[CoherenceViolation]:
    """R1.npc_status — a dead NPC speaks or acts.

    Fusion of the simulator rule (active verb required in the same
    sentence — mentioning the corpse is fine) and the production guard
    (short-form names + narrator self-reported ``npcs_mentioned``)."""
    violations: list[CoherenceViolation] = []
    mentioned_lower = {m.lower() for m in snap.npcs_mentioned}
    for name in snap.dead_npcs:
        if name.lower() in mentioned_lower:
            violations.append(CoherenceViolation(
                rule="R1.npc_status", severity="hard",
                snippet=_snippet_around(narration, name),
                expected=f"{name} is dead",
            ))
            continue
        patterns = [
            re.compile(rf"\b{re.escape(v)}\b", re.IGNORECASE)
            for v in _name_variants(name)
        ]
        for sentence in re.split(r"[.!?]", narration):
            if not any(p.search(sentence) for p in patterns):
                continue
            if _NPC_ACTIVE_PATTERN.search(sentence):
                violations.append(CoherenceViolation(
                    rule="R1.npc_status", severity="hard",
                    snippet=_snippet_around(narration, name),
                    expected=f"{name} is dead",
                ))
                break
    return violations


def check_phantom_npc(
    narration: str, snap: CoherenceSnapshot,
) -> list[CoherenceViolation]:
    """R1.phantom_npc — capitalized proper noun absent from known entities."""
    violations: list[CoherenceViolation] = []
    known_npcs = _canonical_names(snap.known_npc_names)
    known_players = {p.lower() for p in snap.player_names}
    known_locations = _location_words(snap.known_locations)
    seen: set[str] = set()
    for match in _PROPER_NOUN_RE.finditer(narration):
        word = match.group(1)
        if word in _PROPER_NOUN_WHITELIST:
            continue
        lower = word.lower()
        if lower in known_npcs or lower in known_players or lower in known_locations:
            continue
        if lower in seen:
            continue
        seen.add(lower)
        violations.append(CoherenceViolation(
            rule="R1.phantom_npc", severity="hard",
            snippet=_snippet_around(narration, word),
            expected=f"Proper noun '{word}' is not in NPC registry or player names",
        ))
    return violations


def check_item_use_without_owning(
    narration: str, snap: CoherenceSnapshot,
) -> list[CoherenceViolation]:
    """R1.item_use_without_owning — actor uses an item missing from inventory."""
    violations: list[CoherenceViolation] = []
    owned = {item.lower() for item in snap.actor_inventory}
    for match in _ITEM_USE_RE.finditer(narration):
        item_raw = match.group(3).strip().rstrip(".")
        item_text = item_raw.lower()
        if not item_text:
            continue
        if any(o in item_text or item_text in o for o in owned):
            continue
        violations.append(CoherenceViolation(
            rule="R1.item_use_without_owning", severity="hard",
            snippet=_snippet_around(narration, match.group(0)),
            expected=f"Item '{item_raw}' is not in inventory (owned: {sorted(owned)})",
        ))
    return violations


def check_hp_mismatch(
    narration: str, snap: CoherenceSnapshot,
) -> list[CoherenceViolation]:
    """R1.hp_mismatch — wounded/dying prose while actor HP ≥ 80 %."""
    match = _WOUNDED_RE.search(narration)
    if match is None or snap.player_hp_ratio < 0.8:
        return []
    return [CoherenceViolation(
        rule="R1.hp_mismatch", severity="hard",
        snippet=_snippet_around(narration, match.group(0)),
        expected=(
            f"Actor HP ratio is {snap.player_hp_ratio:.2f}, "
            "but narration describes wounding"
        ),
    )]


def check_location_mismatch(
    narration: str, snap: CoherenceSnapshot,
) -> list[CoherenceViolation]:
    """R1.location_mismatch — another known location described while not moving."""
    if snap.current_location is None or snap.moved_this_turn:
        return []
    violations: list[CoherenceViolation] = []
    narration_lower = narration.lower()
    for loc_name in snap.known_locations:
        if loc_name == snap.current_location:
            continue
        if loc_name.lower() in narration_lower:
            violations.append(CoherenceViolation(
                rule="R1.location_mismatch", severity="hard",
                snippet=_snippet_around(narration, loc_name),
                expected=(
                    f"Current location is '{snap.current_location}' and player "
                    f"did not move this turn, but narration mentions '{loc_name}'"
                ),
            ))
    return violations


def check_zone_violation(
    narration: str, snap: CoherenceSnapshot,
) -> list[CoherenceViolation]:
    """R1.zone_violation — narration references a combat zone that doesn't exist."""
    if not snap.combat_active:
        return []
    valid = {z.lower() for z in snap.combat_zones}
    violations: list[CoherenceViolation] = []
    for match in _ZONE_RE.finditer(narration):
        zone = match.group(1).strip().lower()
        if zone in valid:
            continue
        violations.append(CoherenceViolation(
            rule="R1.zone_violation", severity="hard",
            snippet=_snippet_around(narration, match.group(0)),
            expected=f"Zone '{zone}' not in combat zones {sorted(valid)}",
        ))
    return violations


def check_locked_fact_violation(
    narration: str, snap: CoherenceSnapshot,
) -> list[CoherenceViolation]:
    """R1.locked_fact_violation — narration negates a locked world fact."""
    violations: list[CoherenceViolation] = []
    narration_lower = narration.lower()
    for fact in snap.locked_facts:
        subject = _fact_subject(fact.text)
        if not subject or subject not in narration_lower:
            continue
        idx = narration_lower.find(subject)
        window = narration[max(0, idx - 20): idx + len(subject) + 60]
        if _NEGATION_RE.search(window):
            violations.append(CoherenceViolation(
                rule="R1.locked_fact_violation", severity="hard",
                snippet=_snippet_around(narration, subject),
                expected=f"Locked fact: '{fact.text}'",
            ))
    return violations
