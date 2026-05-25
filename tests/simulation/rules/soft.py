"""Soft incoherence rules (R2.*) — text-similarity-based heuristics."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

from tests.simulation.records import IncoherenceAlert

# Reuse helpers from hard.py for consistency.
from tests.simulation.rules.hard import (
    _PROPER_NOUN_RE,
    _PROPER_NOUN_WHITELIST,
    _snippet_around,
)


def _levenshtein(a: str, b: str) -> int:
    """Iterative Levenshtein distance."""
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


def check_repetition(
    narration: str,
    state: Any,
    diff: dict[str, list[Any]],
    history: list[Any],
) -> list[IncoherenceAlert]:
    """R2.repetition — narration matches a phrase from any of the last 5 turns
    by ≥10 consecutive words."""
    window = history[-5:] if history else []
    words = narration.split()
    for prev in window:
        prev_text = prev.get("narration", "") if isinstance(prev, dict) else ""
        if not prev_text:
            continue
        # Use SequenceMatcher to find longest contiguous match.
        sm = SequenceMatcher(a=prev_text.split(), b=words, autojunk=False)
        match = sm.find_longest_match()
        if match.size >= 8:
            snippet = " ".join(words[match.b : match.b + match.size])
            return [
                IncoherenceAlert(
                    severity="soft",
                    category="repetition",
                    turn=getattr(state, "current_turn", 0),
                    rule="R2.repetition",
                    narration_snippet=snippet[:200],
                    expected="Same ≥10-word phrase appeared in the last 5 turns",
                )
            ]
    return []


def check_npc_name_drift(
    narration: str,
    state: Any,
    diff: dict[str, list[Any]],
    history: list[Any],
) -> list[IncoherenceAlert]:
    """R2.npc_name_drift — proper noun ≤2 edits from a known NPC name but not exact."""
    alerts: list[IncoherenceAlert] = []
    known = list(state.npcs)
    seen: set[str] = set()
    for match in _PROPER_NOUN_RE.finditer(narration):
        word = match.group(1)
        if word in _PROPER_NOUN_WHITELIST or word in known or word.lower() in seen:
            continue
        for npc_name in known:
            if _levenshtein(word.lower(), npc_name.lower()) <= 2 and word != npc_name:
                alerts.append(
                    IncoherenceAlert(
                        severity="soft",
                        category="npc_name_drift",
                        turn=getattr(state, "current_turn", 0),
                        rule="R2.npc_name_drift",
                        narration_snippet=_snippet_around(narration, word),
                        expected=f"'{word}' is 1-2 edits from known NPC '{npc_name}'",
                    )
                )
                seen.add(word.lower())
                break
    return alerts


_PASSE_COMPOSE_RE = re.compile(
    r"\b(a|ont|avons|avez|ai|as)\s+([a-zà-ÿ]+[ée]|fait|pris|vu|dit|allé)\b",
    re.IGNORECASE,
)
_PRESENT_VERB_RE = re.compile(
    r"\b(regarde|marche|parle|attaque|saute|voit|entend|crie|court|se tient)\b",
    re.IGNORECASE,
)


def check_tense_drift(
    narration: str,
    state: Any,
    diff: dict[str, list[Any]],
    history: list[Any],
) -> list[IncoherenceAlert]:
    """R2.tense_drift — passé composé and present verbs in the same sentence."""
    alerts: list[IncoherenceAlert] = []
    for sentence in re.split(r"[.!?]", narration):
        if not sentence.strip():
            continue
        if _PASSE_COMPOSE_RE.search(sentence) and _PRESENT_VERB_RE.search(sentence):
            alerts.append(
                IncoherenceAlert(
                    severity="soft",
                    category="tense_drift",
                    turn=getattr(state, "current_turn", 0),
                    rule="R2.tense_drift",
                    narration_snippet=sentence.strip()[:200],
                    expected="Sentence mixes passé composé and present-tense verbs",
                )
            )
    return alerts


def check_unknown_proper_noun(
    narration: str,
    state: Any,
    diff: dict[str, list[Any]],
    history: list[Any],
) -> list[IncoherenceAlert]:
    """R2.unknown_proper_noun — capitalized word matching no known entity.

    Differs from R1.phantom_npc by being broader: includes locations and factions.
    """
    alerts: list[IncoherenceAlert] = []
    known_names = (
        {n.lower() for n in state.npcs}
        | {p.lower() for p in getattr(state, "player_names", [])}
        | {loc.lower() for loc in getattr(state, "locations_known", [])}
        | {f.lower() for f in getattr(state, "factions_known", [])}
    )
    seen: set[str] = set()
    for match in _PROPER_NOUN_RE.finditer(narration):
        word = match.group(1)
        if word in _PROPER_NOUN_WHITELIST or word.lower() in seen:
            continue
        seen.add(word.lower())
        # Check exact, prefix, or substring against known multi-word names.
        if any(word.lower() in name for name in known_names):
            continue
        alerts.append(
            IncoherenceAlert(
                severity="soft",
                category="unknown_proper_noun",
                turn=getattr(state, "current_turn", 0),
                rule="R2.unknown_proper_noun",
                narration_snippet=_snippet_around(narration, word),
                expected=f"'{word}' is not a known NPC, player, location, or faction",
            )
        )
    return alerts
