"""Pure narration-coherence rules shared by production and the simulator.

Ported from ``tests/simulation/rules/{hard,soft}.py`` (chantier « porte de
cohérence »). Rule ids and ``expected`` messages stay identical to the
simulator's so telemetry and simulation reports remain comparable.

No LLM calls, no I/O, no Discord/DB imports — pure functions only.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from difflib import SequenceMatcher
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
    known_factions: list[str] = Field(default_factory=list)
    """Known faction names (R2.unknown_proper_noun) — empty in prod for now."""
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

_SECOND_PERSON_RE = re.compile(
    r"\b(tu|te|ta|ton|tes|vous|vos|votre)\b|\bt['']",
    re.IGNORECASE,
)


def _sentence_at(text: str, offset: int) -> str:
    """Return the sentence (bounded by ``. ! ?``) that contains ``offset``."""
    start = 0
    for match in re.finditer(r"[.!?]", text):
        if match.start() >= offset:
            return text[start:match.start()]
        start = match.end()
    return text[start:]


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
    (short-form names + narrator self-reported ``npcs_mentioned``).

    When the dead name is visible in the narration text, the active-verb
    sentence heuristic alone decides — a self-report adds nothing and must
    not double-flag a legitimate corpse mention. The narrator self-report
    flags on its own ONLY when the name is absent from the text, which
    catches pronoun-only resurrections (« il vous sourit » about a corpse).
    """
    violations: list[CoherenceViolation] = []
    mentioned_lower = {m.lower() for m in snap.npcs_mentioned}
    for name in snap.dead_npcs:
        patterns = [
            re.compile(rf"\b{re.escape(v)}\b", re.IGNORECASE)
            for v in _name_variants(name)
        ]
        name_in_text = any(p.search(narration) for p in patterns)
        if not name_in_text:
            # Name absent from the prose — the self-report is the only
            # signal we have; trust it (pronoun-only resurrection).
            if name.lower() in mentioned_lower:
                violations.append(CoherenceViolation(
                    rule="R1.npc_status", severity="hard",
                    snippet=_snippet_around(narration, name),
                    expected=f"{name} is dead",
                ))
            continue
        # Name is visible — the active-verb heuristic is authoritative.
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
    """R1.item_use_without_owning — actor uses an item missing from inventory.

    Only sentences whose subject is the player (second-person address, or
    an explicit player name) are checked: an NPC drawing its own weapon
    (« Le garde dégaine son épée. ») is legitimate scene colour and must
    not be measured against the player's inventory.
    """
    violations: list[CoherenceViolation] = []
    owned = {item.lower() for item in snap.actor_inventory}
    player_names = {p.lower() for p in snap.player_names}
    for match in _ITEM_USE_RE.finditer(narration):
        item_raw = match.group(3).strip().rstrip(".")
        item_text = item_raw.lower()
        if not item_text:
            continue
        if any(o in item_text or item_text in o for o in owned):
            continue
        sentence = _sentence_at(narration, match.start())
        sentence_lower = sentence.lower()
        is_player_subject = bool(_SECOND_PERSON_RE.search(sentence)) or any(
            name and name in sentence_lower for name in player_names
        )
        if not is_player_subject:
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
        # A fact that is itself a destruction/negation statement (« Le pont
        # est effondré ») cannot be lexically "negated" by destruction
        # words — a faithful re-statement would false-flag as a violation.
        if _NEGATION_RE.search(fact.text):
            continue
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


# --- Soft rules (R2.*) ---

def _levenshtein(a: str, b: str) -> int:
    """Iterative Levenshtein distance (ported from the simulator)."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            curr[j] = min(curr[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost)
        prev = curr
    return prev[-1]


_PASSE_COMPOSE_RE = re.compile(
    r"\b(a|ont|avons|avez|ai|as)\s+([a-zà-ÿ]+[ée]|fait|pris|vu|dit|allé)\b",
    re.IGNORECASE,
)
_PRESENT_VERB_RE = re.compile(
    r"\b(regarde|marche|parle|attaque|saute|voit|entend|crie|court|se tient)\b",
    re.IGNORECASE,
)


def check_repetition(
    narration: str, snap: CoherenceSnapshot,
) -> list[CoherenceViolation]:
    """R2.repetition — ≥ 8 consecutive words shared with a recent narration."""
    words = narration.split()
    for prev_text in snap.recent_narrations:
        if not prev_text:
            continue
        sm = SequenceMatcher(a=prev_text.split(), b=words, autojunk=False)
        match = sm.find_longest_match()
        if match.size >= 8:
            snippet = " ".join(words[match.b: match.b + match.size])
            return [CoherenceViolation(
                rule="R2.repetition", severity="soft",
                snippet=snippet[:200],
                expected="Same ≥10-word phrase appeared in the last 5 turns",
            )]
    return []


def check_npc_name_drift(
    narration: str, snap: CoherenceSnapshot,
) -> list[CoherenceViolation]:
    """R2.npc_name_drift — proper noun ≤ 2 edits from a known NPC name."""
    violations: list[CoherenceViolation] = []
    known_canonical = _canonical_names(snap.known_npc_names)
    targets: list[str] = []
    for n in snap.known_npc_names:
        targets.append(n)
        words = n.split()
        if words:
            head = words[0].rstrip(",.;:!?")
            if head and head.lower() != n.lower():
                targets.append(head)
    seen: set[str] = set()
    for match in _PROPER_NOUN_RE.finditer(narration):
        word = match.group(1)
        if word in _PROPER_NOUN_WHITELIST:
            continue
        if word.lower() in known_canonical or word.lower() in seen:
            continue
        for npc_name in targets:
            if (
                _levenshtein(word.lower(), npc_name.lower()) <= 2
                and word.lower() != npc_name.lower()
            ):
                violations.append(CoherenceViolation(
                    rule="R2.npc_name_drift", severity="soft",
                    snippet=_snippet_around(narration, word),
                    expected=f"'{word}' is 1-2 edits from known NPC '{npc_name}'",
                ))
                seen.add(word.lower())
                break
    return violations


def check_tense_drift(
    narration: str, snap: CoherenceSnapshot,
) -> list[CoherenceViolation]:
    """R2.tense_drift — passé composé and present verbs in the same sentence."""
    violations: list[CoherenceViolation] = []
    for sentence in re.split(r"[.!?]", narration):
        if not sentence.strip():
            continue
        if _PASSE_COMPOSE_RE.search(sentence) and _PRESENT_VERB_RE.search(sentence):
            violations.append(CoherenceViolation(
                rule="R2.tense_drift", severity="soft",
                snippet=sentence.strip()[:200],
                expected="Sentence mixes passé composé and present-tense verbs",
            ))
    return violations


def check_unknown_proper_noun(
    narration: str, snap: CoherenceSnapshot,
) -> list[CoherenceViolation]:
    """R2.unknown_proper_noun — broader phantom check incl. locations."""
    violations: list[CoherenceViolation] = []
    known_names = (
        {n.lower() for n in snap.known_npc_names}
        | {p.lower() for p in snap.player_names}
        | {loc.lower() for loc in snap.known_locations}
        | {f.lower() for f in snap.known_factions}
    )
    seen: set[str] = set()
    for match in _PROPER_NOUN_RE.finditer(narration):
        word = match.group(1)
        if word in _PROPER_NOUN_WHITELIST or word.lower() in seen:
            continue
        seen.add(word.lower())
        if any(word.lower() in name for name in known_names):
            continue
        violations.append(CoherenceViolation(
            rule="R2.unknown_proper_noun", severity="soft",
            snippet=_snippet_around(narration, word),
            expected=f"'{word}' is not a known NPC, player, location, or faction",
        ))
    return violations


# --- Registry ---

RuleFn = Callable[[str, CoherenceSnapshot], list[CoherenceViolation]]

RULES: dict[str, tuple[RuleFn, RuleMode]] = {
    # Day-1 BLOCK set — the two rules anchored tightly enough in engine
    # state to enforce from the first real session (user decision, supersedes
    # the spec's day-1 table).
    "R1.npc_status": (check_npc_status, RuleMode.BLOCK),
    "R1.zone_violation": (check_zone_violation, RuleMode.BLOCK),
    # Hard but noisy in prod conditions — observe first (spec, amendé).
    # item_use and locked_fact join this tier while the mitigations keep the
    # promotion telemetry clean (user decision).
    "R1.item_use_without_owning": (check_item_use_without_owning, RuleMode.OBSERVE),
    "R1.locked_fact_violation": (check_locked_fact_violation, RuleMode.OBSERVE),
    "R1.hp_mismatch": (check_hp_mismatch, RuleMode.OBSERVE),
    "R1.location_mismatch": (check_location_mismatch, RuleMode.OBSERVE),
    "R1.phantom_npc": (check_phantom_npc, RuleMode.OBSERVE),
    # Soft — heuristics.
    "R2.repetition": (check_repetition, RuleMode.OBSERVE),
    "R2.npc_name_drift": (check_npc_name_drift, RuleMode.OBSERVE),
    "R2.tense_drift": (check_tense_drift, RuleMode.OBSERVE),
    "R2.unknown_proper_noun": (check_unknown_proper_noun, RuleMode.OBSERVE),
}


def run_rules(
    narration: str, snap: CoherenceSnapshot,
) -> list[CoherenceViolation]:
    """Run every registered rule in order and aggregate the violations."""
    violations: list[CoherenceViolation] = []
    for rule_fn, _mode in RULES.values():
        violations.extend(rule_fn(narration, snap))
    return violations
