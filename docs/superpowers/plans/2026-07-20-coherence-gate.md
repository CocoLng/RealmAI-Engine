# Porte de cohérence narrative — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Porter les règles d'incohérence du simulateur dans un noyau pur partagé, l'appliquer en production post-narration (retry correctif → template tier-3), et faire écrire des locked facts génériques par le moteur à la complétion des beats.

**Architecture:** Nouveau module `memory/coherence_rules.py` (fonctions pures + Pydantic, 11 règles, registre BLOCK/OBSERVE). `memory/narration_guard.py` orchestre (état par campagne + `check_narration`). `bot/pipeline/narrate.py` construit le snapshot prod et applique la politique dans `call_narrator`. `tests/simulation/rules/{hard,soft}.py` deviennent des adaptateurs minces du noyau ; `drift.py` ne bouge pas.

**Tech Stack:** Python 3.12, Pydantic v2, pytest, `uv run` pour toute commande.

**Spec:** `docs/superpowers/specs/2026-07-20-coherence-gate-design.md` (amendée : R3 non portées, `hp_mismatch`/`location_mismatch` en OBSERVE, phase 2 sans extension de prompt).

## Global Constraints

- Tout modèle de données : Pydantic v2 `BaseModel`, types stricts ; type hints partout (mypy 0 erreur).
- `memory/coherence_rules.py` : **aucun appel LLM, aucune I/O, aucun import Discord/DB** — fonctions pures uniquement.
- Ids de règles **identiques au simulateur** (`R1.npc_status`, …) ; messages `expected` **byte-identiques au simulateur quand l'état le permet** (minimise la casse des tests sim).
- Commandes : toujours `uv run pytest …`, `uv run ruff check .`, `uv run mypy .`.
- Commits : conventional commits, en français, **sans** mention IA/Claude. Jamais `git add -A` (arbre partagé) — toujours une liste explicite de fichiers.
- Chaque tâche se termine gates verts : pytest ciblé + `uv run ruff check .` + `uv run mypy .`.

---

### Task 1: Noyau — modèles + 7 règles dures

**Files:**
- Create: `memory/coherence_rules.py`
- Create: `tests/memory/test_coherence_rules.py`

**Interfaces:**
- Consumes: rien (module feuille).
- Produces (utilisés par les tâches 2-6) :
  - `LockedFactSnapshot(BaseModel)` : `id: str`, `text: str`
  - `CoherenceSnapshot(BaseModel)` : champs listés dans le code ci-dessous, tous avec défauts
  - `CoherenceViolation(BaseModel)` : `rule: str`, `severity: Literal["hard","soft"]`, `snippet: str (≤200)`, `expected: str`
  - `RuleMode(StrEnum)` : `BLOCK`, `OBSERVE`
  - 7 fonctions `check_*(narration: str, snap: CoherenceSnapshot) -> list[CoherenceViolation]`

- [ ] **Step 1: Écrire les tests qui échouent**

Créer `tests/memory/test_coherence_rules.py` :

```python
"""Unit tests for the shared coherence-rule core (hard rules)."""

from memory.coherence_rules import (
    CoherenceSnapshot,
    LockedFactSnapshot,
    check_hp_mismatch,
    check_item_use_without_owning,
    check_location_mismatch,
    check_locked_fact_violation,
    check_npc_status,
    check_phantom_npc,
    check_zone_violation,
)


class TestCheckNpcStatus:
    def test_dead_npc_acting_is_flagged(self) -> None:
        snap = CoherenceSnapshot(dead_npcs=["Aldric"])
        violations = check_npc_status("Aldric sourit et vous tend la main.", snap)
        assert len(violations) == 1
        assert violations[0].rule == "R1.npc_status"
        assert violations[0].severity == "hard"
        assert "Aldric" in violations[0].expected

    def test_mentioning_corpse_without_active_verb_is_fine(self) -> None:
        snap = CoherenceSnapshot(dead_npcs=["Aldric"])
        assert check_npc_status("Le cadavre d'Aldric gît près de l'autel.", snap) == []

    def test_short_form_of_multiword_name_is_caught(self) -> None:
        snap = CoherenceSnapshot(dead_npcs=["Père Aldric"])
        assert len(check_npc_status("Aldric murmure une prière.", snap)) == 1

    def test_self_reported_mention_flags_without_verb(self) -> None:
        snap = CoherenceSnapshot(dead_npcs=["Aldric"], npcs_mentioned=["Aldric"])
        assert len(check_npc_status("Une silhouette familière attend.", snap)) == 1

    def test_no_dead_npcs_means_no_violation(self) -> None:
        snap = CoherenceSnapshot(known_npc_names=["Aldric"])
        assert check_npc_status("Aldric sourit.", snap) == []


class TestCheckPhantomNpc:
    def test_unknown_proper_noun_is_flagged(self) -> None:
        snap = CoherenceSnapshot(
            known_npc_names=["Elara, la Gardienne"], player_names=["Kael"],
            known_locations=["Salle des échos"],
        )
        violations = check_phantom_npc("Soudain, Baldur surgit de l'ombre.", snap)
        assert len(violations) == 1
        assert violations[0].rule == "R1.phantom_npc"

    def test_known_short_form_and_location_words_pass(self) -> None:
        snap = CoherenceSnapshot(
            known_npc_names=["Elara, la Gardienne"], player_names=["Kael"],
            known_locations=["Salle des échos"],
        )
        assert check_phantom_npc("Elara guide Kael vers la Salle.", snap) == []

    def test_whitelist_words_pass(self) -> None:
        snap = CoherenceSnapshot()
        assert check_phantom_npc("Mais Vous hésitez. Alors Tout bascule.", snap) == []


class TestCheckItemUse:
    def test_using_unowned_item_is_flagged(self) -> None:
        snap = CoherenceSnapshot(actor_inventory=["Épée courte"])
        violations = check_item_use_without_owning(
            "Tu brandis la torche enflammée.", snap,
        )
        assert len(violations) == 1
        assert violations[0].rule == "R1.item_use_without_owning"

    def test_using_owned_item_passes(self) -> None:
        snap = CoherenceSnapshot(actor_inventory=["Épée courte"])
        assert check_item_use_without_owning("Tu dégaines l'épée courte.", snap) == []


class TestCheckHpMismatch:
    def test_wounded_prose_with_full_hp_is_flagged(self) -> None:
        snap = CoherenceSnapshot(player_hp_ratio=1.0)
        violations = check_hp_mismatch("Tu chancelles, grièvement blessé.", snap)
        assert len(violations) == 1
        assert violations[0].rule == "R1.hp_mismatch"

    def test_wounded_prose_with_low_hp_passes(self) -> None:
        snap = CoherenceSnapshot(player_hp_ratio=0.3)
        assert check_hp_mismatch("Tu chancelles, grièvement blessé.", snap) == []


class TestCheckLocationMismatch:
    def test_other_known_location_without_move_is_flagged(self) -> None:
        snap = CoherenceSnapshot(
            current_location="Crypte", known_locations=["Crypte", "Taverne du Sanglier"],
            moved_this_turn=False,
        )
        violations = check_location_mismatch(
            "La Taverne du Sanglier bruisse autour de vous.", snap,
        )
        assert len(violations) == 1

    def test_move_turn_passes(self) -> None:
        snap = CoherenceSnapshot(
            current_location="Crypte", known_locations=["Crypte", "Taverne du Sanglier"],
            moved_this_turn=True,
        )
        assert check_location_mismatch("Vous rejoignez la Taverne du Sanglier.", snap) == []


class TestCheckZoneViolation:
    def test_unknown_zone_in_combat_is_flagged(self) -> None:
        snap = CoherenceSnapshot(combat_active=True, combat_zones=["autel", "nef"])
        violations = check_zone_violation("Tu recules vers la zone balcon.", snap)
        assert len(violations) == 1
        assert violations[0].rule == "R1.zone_violation"

    def test_out_of_combat_passes(self) -> None:
        snap = CoherenceSnapshot(combat_active=False)
        assert check_zone_violation("Tu recules vers la zone balcon.", snap) == []


class TestCheckLockedFactViolation:
    def test_negating_a_locked_fact_is_flagged(self) -> None:
        snap = CoherenceSnapshot(locked_facts=[
            LockedFactSnapshot(id="beat:3:hint", text="Le pont de pierre est effondré."),
        ])
        violations = check_locked_fact_violation(
            "Le pont de pierre n'est plus effondré, la voie est libre.", snap,
        )
        assert len(violations) == 1
        assert violations[0].rule == "R1.locked_fact_violation"

    def test_fact_subject_absent_passes(self) -> None:
        snap = CoherenceSnapshot(locked_facts=[
            LockedFactSnapshot(id="beat:3:hint", text="Le pont de pierre est effondré."),
        ])
        assert check_locked_fact_violation("La forêt s'étend devant vous.", snap) == []
```

- [ ] **Step 2: Vérifier l'échec**

Run: `uv run pytest tests/memory/test_coherence_rules.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'memory.coherence_rules'`

- [ ] **Step 3: Implémenter le module**

Créer `memory/coherence_rules.py`. Les helpers et regex sont **copiés depuis `tests/simulation/rules/hard.py`** (source de vérité du portage) et adaptés au snapshot :

```python
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
})

_PROPER_NOUN_RE = re.compile(r"\b([A-ZÉÈÊÀÂÔÛÎ][a-zéèêàâôûîç']{2,})\b")

_ITEM_USE_RE = re.compile(
    r"\b(utilise|boit|consomme|brandit|d[ée]gaine|enfile|active)\s+"
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
```

(La partie règles souples + registre arrive en tâche 2 — le module se termine ici pour l'instant.)

- [ ] **Step 4: Vérifier que les tests passent**

Run: `uv run pytest tests/memory/test_coherence_rules.py -v`
Expected: PASS (17 tests)

- [ ] **Step 5: Gates + commit**

Run: `uv run ruff check . && uv run mypy .`
Expected: clean / 0 erreur

```bash
git add memory/coherence_rules.py tests/memory/test_coherence_rules.py
git commit -m "feat(coherence): noyau pur des règles dures partagées prod/simulateur"
```

---

### Task 2: Noyau — 4 règles souples + registre RULES + run_rules

**Files:**
- Modify: `memory/coherence_rules.py` (append à la fin)
- Modify: `tests/memory/test_coherence_rules.py` (append)

**Interfaces:**
- Consumes: modèles + règles dures de la tâche 1.
- Produces :
  - `check_repetition`, `check_npc_name_drift`, `check_tense_drift`, `check_unknown_proper_noun` — même signature que les règles dures
  - `RuleFn = Callable[[str, CoherenceSnapshot], list[CoherenceViolation]]`
  - `RULES: dict[str, tuple[RuleFn, RuleMode]]` — ordre d'exécution = ordre d'insertion
  - `run_rules(narration: str, snap: CoherenceSnapshot) -> list[CoherenceViolation]`

- [ ] **Step 1: Écrire les tests qui échouent**

Append à `tests/memory/test_coherence_rules.py` :

```python
from memory.coherence_rules import (  # noqa: E402
    RULES,
    RuleMode,
    check_npc_name_drift,
    check_repetition,
    check_tense_drift,
    check_unknown_proper_noun,
    run_rules,
)


class TestCheckRepetition:
    def test_eight_identical_words_are_flagged(self) -> None:
        prev = "La lourde porte de chêne s'ouvre dans un grincement sinistre ce soir."
        snap = CoherenceSnapshot(recent_narrations=[prev])
        violations = check_repetition(
            "La lourde porte de chêne s'ouvre dans un grincement sinistre encore.",
            snap,
        )
        assert len(violations) == 1
        assert violations[0].rule == "R2.repetition"
        assert violations[0].severity == "soft"

    def test_fresh_narration_passes(self) -> None:
        snap = CoherenceSnapshot(recent_narrations=["Le vent souffle sur la lande."])
        assert check_repetition("Un corbeau croasse au loin.", snap) == []


class TestCheckNpcNameDrift:
    def test_two_edit_variant_is_flagged(self) -> None:
        snap = CoherenceSnapshot(known_npc_names=["Elara"])
        violations = check_npc_name_drift("Elera vous fait signe.", snap)
        assert len(violations) == 1
        assert violations[0].rule == "R2.npc_name_drift"

    def test_exact_name_passes(self) -> None:
        snap = CoherenceSnapshot(known_npc_names=["Elara"])
        assert check_npc_name_drift("Elara vous fait signe.", snap) == []


class TestCheckTenseDrift:
    def test_mixed_tenses_in_one_sentence_flagged(self) -> None:
        violations = check_tense_drift(
            "Tu as ouvert la porte et le garde crie aussitôt.",
            CoherenceSnapshot(),
        )
        assert len(violations) == 1
        assert violations[0].rule == "R2.tense_drift"


class TestCheckUnknownProperNoun:
    def test_substring_of_known_name_passes(self) -> None:
        snap = CoherenceSnapshot(known_npc_names=["Elara, la Gardienne"])
        assert check_unknown_proper_noun("Gardienne des lieux, Elara veille.", snap) == []


class TestRegistry:
    def test_registry_has_the_eleven_ported_rules(self) -> None:
        assert set(RULES) == {
            "R1.npc_status", "R1.phantom_npc", "R1.item_use_without_owning",
            "R1.hp_mismatch", "R1.location_mismatch", "R1.zone_violation",
            "R1.locked_fact_violation",
            "R2.repetition", "R2.npc_name_drift", "R2.tense_drift",
            "R2.unknown_proper_noun",
        }

    def test_initial_modes_match_the_spec(self) -> None:
        blocking = {rid for rid, (_, mode) in RULES.items() if mode is RuleMode.BLOCK}
        assert blocking == {
            "R1.npc_status", "R1.item_use_without_owning",
            "R1.zone_violation", "R1.locked_fact_violation",
        }

    def test_run_rules_aggregates_all_rules(self) -> None:
        snap = CoherenceSnapshot(dead_npcs=["Aldric"])
        violations = run_rules("Aldric sourit puis Baldur attaque.", snap)
        rules_hit = {v.rule for v in violations}
        assert "R1.npc_status" in rules_hit
        assert "R1.phantom_npc" in rules_hit
```

- [ ] **Step 2: Vérifier l'échec**

Run: `uv run pytest tests/memory/test_coherence_rules.py -v`
Expected: FAIL — `ImportError: cannot import name 'RULES'`

- [ ] **Step 3: Implémenter**

Append à `memory/coherence_rules.py` :

```python
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
    # Hard — anchored in engine state.
    "R1.npc_status": (check_npc_status, RuleMode.BLOCK),
    "R1.item_use_without_owning": (check_item_use_without_owning, RuleMode.BLOCK),
    "R1.zone_violation": (check_zone_violation, RuleMode.BLOCK),
    "R1.locked_fact_violation": (check_locked_fact_violation, RuleMode.BLOCK),
    # Hard but noisy in prod conditions — observe first (spec, amendé).
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
```

- [ ] **Step 4: Vérifier que tout passe**

Run: `uv run pytest tests/memory/test_coherence_rules.py -v`
Expected: PASS (26 tests)

- [ ] **Step 5: Gates + commit**

Run: `uv run ruff check . && uv run mypy .`
Expected: clean / 0 erreur

```bash
git add memory/coherence_rules.py tests/memory/test_coherence_rules.py
git commit -m "feat(coherence): règles souples + registre RULES avec modes BLOCK/OBSERVE"
```

---

### Task 3: Le simulateur devient adaptateur du noyau

**Files:**
- Modify: `tests/simulation/rules/hard.py` (réécriture complète)
- Modify: `tests/simulation/rules/soft.py` (réécriture complète)
- Test: `tests/simulation/` (suite existante = non-régression du portage)

**Interfaces:**
- Consumes: noyau complet (tâches 1-2).
- Produces: les MÊMES 11 fonctions publiques avec la MÊME signature simulateur `(narration, state, diff, history) -> list[IncoherenceAlert]` — `checker.py`, `rules/__init__.py` et `drift.py` ne changent pas d'une ligne.

- [ ] **Step 1: Réécrire `tests/simulation/rules/hard.py`**

```python
"""Hard incoherence rules (R1.*) — thin adapters over the shared prod core.

The rule logic lives in ``memory/coherence_rules.py`` (chantier « porte de
cohérence »). These wrappers keep the simulator-facing signature
``(narration, state, diff, history)`` and the ``IncoherenceAlert`` output
so the checker, runner and reports stay untouched.
"""

from __future__ import annotations

from typing import Any

from memory.coherence_rules import (
    CoherenceSnapshot,
    CoherenceViolation,
    LockedFactSnapshot,
)
from memory.coherence_rules import check_hp_mismatch as _core_hp_mismatch
from memory.coherence_rules import (
    check_item_use_without_owning as _core_item_use,
)
from memory.coherence_rules import (
    check_location_mismatch as _core_location_mismatch,
)
from memory.coherence_rules import (
    check_locked_fact_violation as _core_locked_fact,
)
from memory.coherence_rules import check_npc_status as _core_npc_status
from memory.coherence_rules import check_phantom_npc as _core_phantom_npc
from memory.coherence_rules import check_zone_violation as _core_zone_violation
from tests.simulation.records import IncoherenceAlert


def snapshot_from_sim(state: Any, history: list[Any]) -> CoherenceSnapshot:
    """Map the simulator's state + history tail onto the neutral snapshot."""
    last = history[-1] if history and isinstance(history[-1], dict) else {}
    inv = getattr(state, "inventory", None)
    combat_state = getattr(state, "combat_state", None)
    facts = [
        LockedFactSnapshot(id=str(f.get("id", "")), text=str(f.get("text", "")))
        for f in (last.get("locked_facts", []) or [])
        if isinstance(f, dict)
    ]
    recent = [
        h.get("narration", "")
        for h in (history[-5:] if history else [])
        if isinstance(h, dict) and h.get("narration")
    ]
    return CoherenceSnapshot(
        dead_npcs=[
            npc.name for npc in state.npcs.values()
            if npc.status == "dead" or npc.hp <= 0
        ],
        known_npc_names=list(state.npcs),
        player_names=list(getattr(state, "player_names", [])),
        current_location=getattr(state.current_location, "name", None),
        known_locations=list(
            last.get("location_known", [])
            or getattr(state, "locations_known", [])
            or []
        ),
        moved_this_turn=bool(last.get("moved_this_turn")),
        actor_inventory=list(getattr(inv, "items", [])) if inv is not None else [],
        player_hp_ratio=float(getattr(state, "player_hp_ratio", 1.0)),
        combat_active=bool(getattr(state, "combat_active", False)),
        combat_zones=(
            list(getattr(combat_state, "zones", []))
            if combat_state is not None else []
        ),
        locked_facts=facts,
        recent_narrations=recent,
    )


def _to_alerts(
    violations: list[CoherenceViolation], state: Any, category: str,
) -> list[IncoherenceAlert]:
    return [
        IncoherenceAlert(
            severity=v.severity,
            category=category,
            turn=getattr(state, "current_turn", 0),
            rule=v.rule,
            narration_snippet=v.snippet,
            expected=v.expected,
        )
        for v in violations
    ]


def check_npc_status(
    narration: str, state: Any, diff: dict[str, list[Any]], history: list[Any],
) -> list[IncoherenceAlert]:
    """R1.npc_status — a dead NPC speaks or acts in the narration."""
    snap = snapshot_from_sim(state, history)
    return _to_alerts(_core_npc_status(narration, snap), state, "dead_npc_speaks")


def check_item_use_without_owning(
    narration: str, state: Any, diff: dict[str, list[Any]], history: list[Any],
) -> list[IncoherenceAlert]:
    """R1.item_use_without_owning — item used while missing from inventory."""
    if getattr(state, "inventory", None) is None:
        return []
    snap = snapshot_from_sim(state, history)
    return _to_alerts(
        _core_item_use(narration, snap), state, "item_use_without_owning",
    )


def check_hp_mismatch(
    narration: str, state: Any, diff: dict[str, list[Any]], history: list[Any],
) -> list[IncoherenceAlert]:
    """R1.hp_mismatch — wounded prose while player HP ≥ 80 %."""
    snap = snapshot_from_sim(state, history)
    return _to_alerts(_core_hp_mismatch(narration, snap), state, "hp_mismatch")


def check_location_mismatch(
    narration: str, state: Any, diff: dict[str, list[Any]], history: list[Any],
) -> list[IncoherenceAlert]:
    """R1.location_mismatch — another known location described as present."""
    if not history:
        return []
    snap = snapshot_from_sim(state, history)
    return _to_alerts(
        _core_location_mismatch(narration, snap), state, "location_mismatch",
    )


def check_phantom_npc(
    narration: str, state: Any, diff: dict[str, list[Any]], history: list[Any],
) -> list[IncoherenceAlert]:
    """R1.phantom_npc — capitalized proper noun absent from known entities."""
    snap = snapshot_from_sim(state, history)
    return _to_alerts(_core_phantom_npc(narration, snap), state, "phantom_npc")


def check_locked_fact_violation(
    narration: str, state: Any, diff: dict[str, list[Any]], history: list[Any],
) -> list[IncoherenceAlert]:
    """R1.locked_fact_violation — narration negates a locked world fact."""
    if not history:
        return []
    snap = snapshot_from_sim(state, history)
    return _to_alerts(
        _core_locked_fact(narration, snap), state, "locked_fact_violation",
    )


def check_zone_violation(
    narration: str, state: Any, diff: dict[str, list[Any]], history: list[Any],
) -> list[IncoherenceAlert]:
    """R1.zone_violation — narration references a nonexistent combat zone."""
    snap = snapshot_from_sim(state, history)
    return _to_alerts(_core_zone_violation(narration, snap), state, "zone_violation")
```

- [ ] **Step 2: Réécrire `tests/simulation/rules/soft.py`**

```python
"""Soft incoherence rules (R2.*) — thin adapters over the shared prod core."""

from __future__ import annotations

from typing import Any

from memory.coherence_rules import check_npc_name_drift as _core_name_drift
from memory.coherence_rules import check_repetition as _core_repetition
from memory.coherence_rules import check_tense_drift as _core_tense_drift
from memory.coherence_rules import (
    check_unknown_proper_noun as _core_unknown_noun,
)
from tests.simulation.records import IncoherenceAlert
from tests.simulation.rules.hard import _to_alerts, snapshot_from_sim


def check_repetition(
    narration: str, state: Any, diff: dict[str, list[Any]], history: list[Any],
) -> list[IncoherenceAlert]:
    """R2.repetition — ≥ 8 consecutive words shared with the last 5 turns."""
    snap = snapshot_from_sim(state, history)
    return _to_alerts(_core_repetition(narration, snap), state, "repetition")


def check_npc_name_drift(
    narration: str, state: Any, diff: dict[str, list[Any]], history: list[Any],
) -> list[IncoherenceAlert]:
    """R2.npc_name_drift — proper noun ≤ 2 edits from a known NPC name."""
    snap = snapshot_from_sim(state, history)
    return _to_alerts(_core_name_drift(narration, snap), state, "npc_name_drift")


def check_tense_drift(
    narration: str, state: Any, diff: dict[str, list[Any]], history: list[Any],
) -> list[IncoherenceAlert]:
    """R2.tense_drift — passé composé and present verbs in one sentence."""
    snap = snapshot_from_sim(state, history)
    return _to_alerts(_core_tense_drift(narration, snap), state, "tense_drift")


def check_unknown_proper_noun(
    narration: str, state: Any, diff: dict[str, list[Any]], history: list[Any],
) -> list[IncoherenceAlert]:
    """R2.unknown_proper_noun — capitalized word matching no known entity."""
    snap = snapshot_from_sim(state, history)
    return _to_alerts(_core_unknown_noun(narration, snap), state, "unknown_proper_noun")
```

Note : le simulateur avait `factions_known` dans `check_unknown_proper_noun` ;
le noyau couvre PNJ + joueurs + lieux. Si un test sim utilise une faction,
l'adaptateur peut la faire passer par `known_locations` (même rôle de
« nom connu ») — ajuster `snapshot_from_sim` en conséquence à ce moment-là.

- [ ] **Step 3: Non-régression du portage**

Run: `uv run pytest tests/simulation/ -v`
Expected: PASS. Deux causes d'échec ACCEPTABLES à corriger dans les tests
sim (et uniquement celles-là — tout autre échec = bug de portage à corriger
dans le noyau) :
1. `check_npc_status` détecte désormais les **formes courtes** des noms
   multi-mots (fusion voulue par la spec §1.2) — un test sim qui assertait
   « pas de détection » sur une forme courte devient une détection.
2. `expected` de `R1.npc_status` est désormais `"{name} is dead"` (le
   snapshot ne porte plus status/hp) — un test qui assertait l'ancien
   suffixe `(status=…, hp=…)` doit matcher le préfixe.

- [ ] **Step 4: Gates + commit**

Run: `uv run pytest tests/memory/test_coherence_rules.py tests/simulation/ -q && uv run ruff check . && uv run mypy .`
Expected: tout vert

```bash
git add tests/simulation/rules/hard.py tests/simulation/rules/soft.py
git commit -m "refactor(simulation): les règles hard/soft deviennent des adaptateurs du noyau partagé"
```

(Si le step 3 a modifié des tests sim, les ajouter au commit par chemin explicite.)

---

### Task 4: Orchestration dans narration_guard — check_narration + GuardVerdict

**Files:**
- Modify: `memory/narration_guard.py`
- Modify: `tests/memory/test_narration_guard.py`

**Interfaces:**
- Consumes: `RULES`, `RuleMode`, `CoherenceSnapshot`, `CoherenceViolation`, `check_npc_status` du noyau.
- Produces (utilisés par la tâche 5) :
  - `GuardVerdict` (dataclass) : `blocking: list[CoherenceViolation]`, `observed: list[CoherenceViolation]`
  - `check_narration(campaign_id: str, *, narrative: str, snapshot: CoherenceSnapshot | None, npcs_mentioned: list[str]) -> GuardVerdict`
  - `find_dead_npc_violations` / `find_repetition` / `set_dead_npcs` / `record_narration` / `reset` : signatures inchangées.
  - Sémantique modifiée (voulue, spec §1.2) : `find_dead_npc_violations` exige désormais un **verbe actif dans la phrase** OU une mention auto-déclarée — la simple mention du cadavre ne flague plus.

- [ ] **Step 1: Adapter/écrire les tests**

Dans `tests/memory/test_narration_guard.py` :
1. Mettre à jour les tests existants de `find_dead_npc_violations` au nouveau
   contrat : une narration qui **mentionne** un mort sans verbe actif
   (« Le cadavre d'Aldric gît là. ») ne retourne plus le nom ; une narration
   où il **agit** (« Aldric sourit. ») le retourne ; un nom dans
   `npcs_mentioned` le retourne toujours.
2. Ajouter :

```python
from memory.coherence_rules import CoherenceSnapshot, LockedFactSnapshot
from memory.narration_guard import GuardVerdict, check_narration


class TestCheckNarration:
    def test_blocking_and_observed_are_split_by_mode(self) -> None:
        narration_guard.reset("c1")
        narration_guard.set_dead_npcs("c1", ["Aldric"])
        snap = CoherenceSnapshot(known_npc_names=["Elara"])
        verdict = check_narration(
            "c1",
            narrative="Aldric sourit tandis que Baldur observe.",
            snapshot=snap,
            npcs_mentioned=[],
        )
        assert [v.rule for v in verdict.blocking] == ["R1.npc_status"]
        assert "R1.phantom_npc" in {v.rule for v in verdict.observed}

    def test_guard_state_merges_into_snapshot(self) -> None:
        # dead_npcs du registre + recent_narrations de la deque sont fusionnés
        # même quand le snapshot fourni est vide.
        narration_guard.reset("c2")
        narration_guard.set_dead_npcs("c2", ["Mira"])
        verdict = check_narration(
            "c2", narrative="Mira attaque sans hésiter.",
            snapshot=None, npcs_mentioned=[],
        )
        assert [v.rule for v in verdict.blocking] == ["R1.npc_status"]

    def test_clean_narration_yields_empty_verdict(self) -> None:
        narration_guard.reset("c3")
        verdict = check_narration(
            "c3", narrative="Le vent souffle.", snapshot=None, npcs_mentioned=[],
        )
        assert verdict.blocking == [] and verdict.observed == []


class TestRecentNarrationsWindow:
    def test_deque_keeps_five_but_find_repetition_checks_last_two(self) -> None:
        narration_guard.reset("c4")
        eight = "un deux trois quatre cinq six sept huit"
        narration_guard.record_narration("c4", eight)          # n-3
        narration_guard.record_narration("c4", "toto")          # n-2
        narration_guard.record_narration("c4", "titi")          # n-1
        # La répétition vs n-3 n'est PLUS bloquante (fenêtre legacy = 2)…
        assert narration_guard.find_repetition("c4", eight) is None
        # …mais reste visible du noyau via check_narration (R2 en OBSERVE).
        verdict = check_narration(
            "c4", narrative=eight, snapshot=None, npcs_mentioned=[],
        )
        assert "R2.repetition" in {v.rule for v in verdict.observed}
```

- [ ] **Step 2: Vérifier l'échec**

Run: `uv run pytest tests/memory/test_narration_guard.py -v`
Expected: FAIL — `ImportError: cannot import name 'check_narration'`

- [ ] **Step 3: Implémenter dans `memory/narration_guard.py`**

Changements :

```python
# En tête de module, ajouter :
import logging
from memory.coherence_rules import (
    RULES,
    CoherenceSnapshot,
    CoherenceViolation,
    RuleMode,
    check_npc_status,
)

_COHERENCE_LOGGER = logging.getLogger("memory.coherence")

# Constante modifiée :
_RECENT_NARRATIONS_KEPT = 5
"""5 narrations gardées pour le noyau (R2.repetition, fenêtre simulateur) ;
``find_repetition`` — le check BLOQUANT historique — ne compare qu'aux 2
dernières, comportement inchangé."""
```

`find_repetition` : remplacer la boucle `for prev in state.recent_narrations:`
par `for prev in list(state.recent_narrations)[-2:]:` (le reste ne change pas).

`find_dead_npc_violations` : réimplémenter au-dessus du noyau (une passe par
nom pour attribuer les violations à leur PNJ) :

```python
def find_dead_npc_violations(
    campaign_id: str,
    *,
    narrative: str,
    npcs_mentioned: list[str],
) -> list[str]:
    """Names of dead NPCs that the narration brings back to life.

    Backed by the shared core rule (R1.npc_status): a violation now
    requires an ACTIVE VERB in the same sentence as the name (or a
    self-reported mention) — mentioning the corpse is legitimate.
    """
    state = _STATES.get(campaign_id)
    if state is None or not state.dead_npcs:
        return []
    violations: list[str] = []
    for name in sorted(state.dead_npcs):
        snap = CoherenceSnapshot(dead_npcs=[name], npcs_mentioned=npcs_mentioned)
        if check_npc_status(narrative, snap):
            violations.append(name)
    return violations
```

Supprimer `_name_patterns` (absorbé par le noyau — `_name_variants`).

Ajouter à la fin du module :

```python
@dataclass
class GuardVerdict:
    """Split of one narration's violations by enforcement mode."""

    blocking: list[CoherenceViolation]
    observed: list[CoherenceViolation]


def check_narration(
    campaign_id: str,
    *,
    narrative: str,
    snapshot: CoherenceSnapshot | None,
    npcs_mentioned: list[str],
) -> GuardVerdict:
    """Run every registered coherence rule against one narration.

    Merges the per-campaign guard state (dead set, recent narrations)
    into the caller-provided snapshot, then splits violations by the
    registry's BLOCK/OBSERVE mode. Observed violations are logged on the
    dedicated ``memory.coherence`` logger — the promotion dataset.
    """
    state = _state(campaign_id)
    base = snapshot if snapshot is not None else CoherenceSnapshot()
    effective = base.model_copy(update={
        "dead_npcs": sorted(set(base.dead_npcs) | state.dead_npcs),
        "recent_narrations": list(state.recent_narrations),
        "npcs_mentioned": list(npcs_mentioned),
    })
    blocking: list[CoherenceViolation] = []
    observed: list[CoherenceViolation] = []
    for rule_fn, mode in RULES.values():
        target = blocking if mode is RuleMode.BLOCK else observed
        target.extend(rule_fn(narrative, effective))
    for violation in observed:
        _COHERENCE_LOGGER.info(
            "COHERENCE observe campaign=%s rule=%s expected=%r snippet=%r",
            campaign_id, violation.rule, violation.expected, violation.snippet,
        )
    return GuardVerdict(blocking=blocking, observed=observed)
```

- [ ] **Step 4: Vérifier que tout passe**

Run: `uv run pytest tests/memory/test_narration_guard.py tests/memory/test_coherence_rules.py tests/simulation/ -q`
Expected: PASS

- [ ] **Step 5: Gates + commit**

Run: `uv run ruff check . && uv run mypy .`
Expected: clean / 0 erreur

```bash
git add memory/narration_guard.py tests/memory/test_narration_guard.py
git commit -m "feat(coherence): check_narration orchestre les règles avec verdict BLOCK/OBSERVE"
```

---

### Task 5: Câblage production — snapshot builder + politique dans call_narrator

**Files:**
- Modify: `ai/narrator.py` (méthode publique `template_narration`)
- Modify: `bot/pipeline/narrate.py` (builder + rework de `call_narrator`)
- Modify: `bot/pipeline/orchestrator.py:676` (construction + passage du snapshot)
- Create: `tests/bot/pipeline/test_narrate_coherence.py`
- Test: `tests/ai/test_narrator.py` (append), suites existantes `tests/bot/pipeline/`

**Interfaces:**
- Consumes: `check_narration`/`GuardVerdict` (tâche 4), `CoherenceSnapshot`.
- Produces :
  - `Narrator.template_narration(action_result_text: str, outcome_facts: str, language: str) -> NarrativeResult`
  - `narrate.build_coherence_snapshot(session, *, actor_name: str, inventory, moved_this_turn: bool) -> CoherenceSnapshot`
  - `narrate.call_narrator(..., snapshot: CoherenceSnapshot | None = None)` — nouveau paramètre optionnel, défaut `None` (compat totale avec les appels existants).

- [ ] **Step 1: Tests qui échouent**

Append à `tests/ai/test_narrator.py` :

```python
class TestTemplateNarration:
    def test_public_template_never_calls_llm(self) -> None:
        client = MagicMock()
        narrator = Narrator(client)
        result = narrator.template_narration("Attaque réussie", "8 dégâts", "fr")
        client.chat.assert_not_called()
        assert "8 dégâts" in result.narrative
        assert result.tone == "dramatic"
```

Créer `tests/bot/pipeline/test_narrate_coherence.py` :

```python
"""call_narrator × porte de cohérence : retry correctif puis template tier-3."""

from unittest.mock import MagicMock

import pytest

from ai.models import MechanicsOutcome, NarrativeResult
from bot.pipeline import narrate
from memory import narration_guard
from memory.coherence_rules import CoherenceSnapshot


def _result(text: str) -> NarrativeResult:
    return NarrativeResult(narrative=text, tone="dramatic")


def _narrator_returning(*texts: str) -> MagicMock:
    narrator = MagicMock()
    narrator.narrate.side_effect = [_result(t) for t in texts]
    narrator.template_narration.return_value = _result("[template] Le récit reprend.")
    return narrator


@pytest.fixture(autouse=True)
def _clean_guard():
    narration_guard.reset("camp-1")
    yield
    narration_guard.reset("camp-1")


async def test_clean_narration_passes_through() -> None:
    narrator = _narrator_returning("Le vent souffle sur la lande déserte.")
    result = await narrate.call_narrator(
        narrator=narrator,
        outcome=MechanicsOutcome(summary="Look"),
        context_prompt="ctx", language="fr", campaign_id="camp-1",
        snapshot=CoherenceSnapshot(),
    )
    assert result.narrative == "Le vent souffle sur la lande déserte."
    assert narrator.narrate.call_count == 1


async def test_blocking_violation_retries_with_constraint() -> None:
    narration_guard.set_dead_npcs("camp-1", ["Aldric"])
    narrator = _narrator_returning(
        "Aldric sourit et vous parle doucement.",   # tier 1 : violation
        "Le silence répond, près du corps d'Aldric.",  # retry : propre
    )
    result = await narrate.call_narrator(
        narrator=narrator,
        outcome=MechanicsOutcome(summary="Talk"),
        context_prompt="ctx", language="fr", campaign_id="camp-1",
        snapshot=CoherenceSnapshot(),
    )
    assert "silence" in result.narrative
    assert narrator.narrate.call_count == 2
    # La contrainte du retry contient le fait attendu.
    amended = narrator.narrate.call_args_list[1].kwargs["action_result_text"]
    assert "CONTRAINTE" in amended and "Aldric" in amended


async def test_double_failure_falls_back_to_template() -> None:
    narration_guard.set_dead_npcs("camp-1", ["Aldric"])
    narrator = _narrator_returning(
        "Aldric sourit et vous parle.",     # tier 1 : violation
        "Aldric attaque avec fureur.",       # retry : violation encore
    )
    result = await narrate.call_narrator(
        narrator=narrator,
        outcome=MechanicsOutcome(summary="Talk", outcome_facts="Le PNJ est mort."),
        context_prompt="ctx", language="fr", campaign_id="camp-1",
        snapshot=CoherenceSnapshot(),
    )
    assert result.narrative == "[template] Le récit reprend."
    narrator.template_narration.assert_called_once()


async def test_observe_only_violation_never_retries() -> None:
    narrator = _narrator_returning("Soudain, Baldur surgit de nulle part.")
    result = await narrate.call_narrator(
        narrator=narrator,
        outcome=MechanicsOutcome(summary="Look"),
        context_prompt="ctx", language="fr", campaign_id="camp-1",
        snapshot=CoherenceSnapshot(known_npc_names=["Elara"]),
    )
    assert "Baldur" in result.narrative       # publié tel quel
    assert narrator.narrate.call_count == 1   # zéro retry


def test_build_coherence_snapshot_reads_session_state() -> None:
    from engine.inventory import Inventory
    session = MagicMock()
    npc_dead = MagicMock(); npc_dead.name = "Aldric"; npc_dead.is_alive = False
    npc_alive = MagicMock(); npc_alive.name = "Elara"; npc_alive.is_alive = True
    session.npcs = {"Aldric": npc_dead, "Elara": npc_alive}
    pc = MagicMock(); pc.name = "Kael"; pc.hp = 10; pc.max_hp = 20
    session.characters = {1: pc}
    session.current_location.name = "Crypte"
    session.current_location.connections = ["Nef"]
    session.current_location.zones = []
    session.combat_state = None
    session.story_arc = None
    snap = narrate.build_coherence_snapshot(
        session, actor_name="Kael", inventory=Inventory(), moved_this_turn=False,
    )
    assert snap.dead_npcs == ["Aldric"]
    assert snap.known_npc_names == ["Aldric", "Elara"]
    assert snap.player_names == ["Kael"]
    assert snap.current_location == "Crypte"
    assert snap.known_locations == ["Crypte", "Nef"]
    assert snap.player_hp_ratio == 0.5
    assert snap.combat_active is False
```

(Si le repo n'active pas `asyncio_mode = auto`, décorer les tests async avec
`@pytest.mark.asyncio` comme le font les autres fichiers de `tests/bot/pipeline/`
— reprendre la convention du fichier voisin `test_narrate_memory.py`.)

- [ ] **Step 2: Vérifier l'échec**

Run: `uv run pytest tests/bot/pipeline/test_narrate_coherence.py tests/ai/test_narrator.py -v`
Expected: FAIL — `AttributeError: template_narration` / `TypeError: unexpected keyword argument 'snapshot'`

- [ ] **Step 3: Implémenter**

**`ai/narrator.py`** — sous `narrate()`, ajouter :

```python
    def template_narration(
        self, action_result_text: str, outcome_facts: str, language: str = "fr"
    ) -> NarrativeResult:
        """Public tier-3 template — used by the coherence gate when a
        corrective retry still violates a blocking rule. Never raises,
        never calls the LLM."""
        return self._template_fallback(action_result_text, outcome_facts, language)
```

**`bot/pipeline/narrate.py`** — ajouter le builder (après `_render_locked_facts`) :

```python
def build_coherence_snapshot(
    session: "GameSession",
    *,
    actor_name: str,
    inventory: "Inventory | None",
    moved_this_turn: bool,
) -> "CoherenceSnapshot":
    """Map the live session onto the coherence-rule input contract.

    isinstance guards mirror the rest of this module: tests drive the
    pipeline with MagicMock sessions."""
    from memory.coherence_rules import CoherenceSnapshot, LockedFactSnapshot

    npcs = getattr(session, "npcs", None)
    npcs = npcs if isinstance(npcs, dict) else {}
    characters = getattr(session, "characters", None)
    characters = characters if isinstance(characters, dict) else {}

    actor = next((c for c in characters.values() if c.name == actor_name), None)
    max_hp = getattr(actor, "max_hp", 0) if actor is not None else 0
    ratio = (actor.hp / max_hp) if actor is not None and max_hp else 1.0

    loc = getattr(session, "current_location", None)
    loc_name = getattr(loc, "name", None) if loc is not None else None
    connections = list(getattr(loc, "connections", []) or []) if loc is not None else []

    combat = getattr(session, "combat_state", None)
    combat_active = combat is not None and bool(getattr(combat, "is_active", False))
    zones: list[str] = []
    if combat_active and loc is not None:
        zones = [z.name for z in (getattr(loc, "zones", []) or [])]

    arc = getattr(session, "story_arc", None)
    raw_facts = getattr(arc, "locked_facts", None) if arc is not None else None
    facts = (
        [LockedFactSnapshot(id=f.id, text=f.text) for f in raw_facts]
        if isinstance(raw_facts, list) else []
    )

    inv_names: list[str] = []
    if inventory is not None:
        inv_names = [item.name for item in inventory.items]
        inv_names += [item.name for item in inventory.equipped.values()]

    return CoherenceSnapshot(
        dead_npcs=[n.name for n in npcs.values() if not n.is_alive],
        known_npc_names=[n.name for n in npcs.values()],
        player_names=[c.name for c in characters.values()],
        current_location=loc_name,
        known_locations=([loc_name, *connections] if loc_name else []),
        moved_this_turn=moved_this_turn,
        actor_inventory=inv_names,
        player_hp_ratio=ratio,
        combat_active=combat_active,
        combat_zones=zones,
        locked_facts=facts,
    )
```

(Ajouter `CoherenceSnapshot` au bloc `TYPE_CHECKING` du module :
`from memory.coherence_rules import CoherenceSnapshot`.)

**`bot/pipeline/narrate.py`** — remplacer intégralement le bloc guard de
`call_narrator` (l'actuel `narrate.py:304-350`, de `if not guard:` à la fin
de la fonction) par :

```python
    if not guard:
        return result

    from memory import narration_guard

    def _inspect(res: NarrativeResult) -> tuple["narration_guard.GuardVerdict", str | None]:
        verdict = narration_guard.check_narration(
            campaign_id,
            narrative=res.narrative,
            snapshot=snapshot,
            npcs_mentioned=res.npcs_mentioned,
        )
        repeated = narration_guard.find_repetition(campaign_id, res.narrative)
        return verdict, repeated

    verdict, repeated = _inspect(result)
    if not verdict.blocking and repeated is None:
        if result.locked_facts_used:
            logger.info(
                "NARRATE locked_facts_used campaign=%s ids=%s",
                campaign_id, result.locked_facts_used,
            )
        return result

    constraints: list[str] = [
        "CONTRAINTE ABSOLUE (cohérence) : la narration contredit l'état du "
        f"jeu — {violation.expected}. Réécris en respectant strictement ce fait."
        for violation in verdict.blocking
    ]
    if repeated is not None:
        constraints.append(
            "CONTRAINTE DE VARIATION : ta narration répète presque mot pour "
            f"mot un passage récent (« {repeated[:120]} »). Reformule avec "
            "des images, un rythme et un vocabulaire différents, sans "
            "changer les faits."
        )
    logger.warning(
        "NARRATION guard: %d blocking violation(s) campaign=%s rules=%s — retrying once",
        len(verdict.blocking) + (1 if repeated is not None else 0),
        campaign_id,
        [violation.rule for violation in verdict.blocking],
    )
    amended = "\n\n".join([outcome.summary, *constraints])
    retry_result = await retry_llm_call(
        lambda: _do(amended),
        log_label=f"ACTION campaign={campaign_id} narrate-guard-retry",
    )

    verdict2, repeated2 = _inspect(retry_result)
    if not verdict2.blocking and repeated2 is None:
        return retry_result

    logger.error(
        "NARRATION guard: retry still violates campaign=%s rules=%s — template fallback",
        campaign_id, [violation.rule for violation in verdict2.blocking],
    )
    return narrator.template_narration(
        outcome.summary, outcome.outcome_facts, language,
    )
```

et étendre la signature :

```python
async def call_narrator(
    narrator: "Narrator",
    outcome: MechanicsOutcome,
    context_prompt: str,
    language: str,
    campaign_id: str,
    has_npc_dialogue: bool = False,
    director_note: "DirectorNote | None" = None,
    guard: bool = True,
    snapshot: "CoherenceSnapshot | None" = None,
) -> NarrativeResult:
```

**`bot/pipeline/orchestrator.py`** — au site d'appel (`orchestrator.py:676`),
avant `narration = await narrate.call_narrator(...)` :

```python
        from engine.validators import ActionType
        moved_this_turn = interpreted.action_type in (ActionType.MOVE, ActionType.FLEE)
        coherence_snapshot = (
            narrate.build_coherence_snapshot(
                self.session,
                actor_name=self.actor_name,
                inventory=self.inventory,
                moved_this_turn=moved_this_turn,
            )
            if self.session is not None else None
        )
```

puis ajouter `snapshot=coherence_snapshot,` à l'appel `narrate.call_narrator(...)`.
(Si `ActionType` est déjà importé en tête de module — vérifier — supprimer
l'import local.)

- [ ] **Step 4: Vérifier que tout passe**

Run: `uv run pytest tests/bot/pipeline/ tests/ai/test_narrator.py tests/memory/ -q`
Expected: PASS. Échec ACCEPTABLE à corriger : un test existant de
`call_narrator` qui assertait l'ancien texte de contrainte dead-NPC
(« CONTRAINTE ABSOLUE (faits verrouillés) ») — l'adapter au nouveau format
(« CONTRAINTE ABSOLUE (cohérence) »). Tout autre échec = régression à corriger.

- [ ] **Step 5: Gates + commit**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy .`
Expected: suite complète verte / clean / 0 erreur

```bash
git add ai/narrator.py bot/pipeline/narrate.py bot/pipeline/orchestrator.py tests/bot/pipeline/test_narrate_coherence.py tests/ai/test_narrator.py
git commit -m "feat(coherence): porte de cohérence en production — retry correctif puis template tier-3"
```

---

### Task 6: Phase 2 — locked facts génériques à la complétion des beats

**Files:**
- Modify: `world/story_arc.py` (champ + helper pur)
- Modify: `ai/arc_generator.py:203-211` (sanitize `locked_facts`)
- Modify: `bot/pipeline/orchestrator.py:613,772` (`_apply_beat_effects`)
- Modify: `bot/pipeline/narrate.py:109-122` (`_render_locked_facts` plafonné)
- Test: `tests/world/test_world_models.py` (append), `tests/ai/test_arc_generator.py` (append), `tests/bot/pipeline/test_narrate_memory.py` (append)

**Interfaces:**
- Consumes: `BeatEffects`, `LockedFact`, `StoryArc` existants.
- Produces :
  - `BeatEffects.locked_facts: list[str]` (défaut `[]`)
  - `append_beat_locked_facts(arc: StoryArc, effects: BeatEffects, beat_number: int) -> None` (fonction module-level dans `world/story_arc.py`)

- [ ] **Step 1: Tests qui échouent**

Append à `tests/world/test_world_models.py` :

```python
class TestAppendBeatLockedFacts:
    def _arc(self) -> StoryArc:
        return StoryArc(
            campaign_id="c1", theme="t", premise="Une longue prémisse valide.",
            beats=[_make_beat(n) for n in range(1, 9)],
            villain_name="V", villain_motivation="m",
        )

    def test_explicit_facts_and_hint_are_locked(self) -> None:
        from world.story_arc import BeatEffects, append_beat_locked_facts
        arc = self._arc()
        effects = BeatEffects(
            locked_facts=["Le pont de pierre est effondré."],
            narrative_hint="La herse de la crypte est levée.",
        )
        append_beat_locked_facts(arc, effects, beat_number=3)
        ids = [f.id for f in arc.locked_facts]
        assert ids == ["beat:3:0", "beat:3:hint"]
        assert arc.locked_facts[1].text == "La herse de la crypte est levée."

    def test_append_is_idempotent(self) -> None:
        from world.story_arc import BeatEffects, append_beat_locked_facts
        arc = self._arc()
        effects = BeatEffects(narrative_hint="La herse est levée.")
        append_beat_locked_facts(arc, effects, beat_number=3)
        append_beat_locked_facts(arc, effects, beat_number=3)
        assert len(arc.locked_facts) == 1

    def test_empty_effects_add_nothing(self) -> None:
        from world.story_arc import BeatEffects, append_beat_locked_facts
        arc = self._arc()
        append_beat_locked_facts(arc, BeatEffects(), beat_number=3)
        assert arc.locked_facts == []
```

(`_make_beat` : reprendre la fabrique de beats déjà utilisée dans ce fichier
de tests ; si elle n'existe pas, construire un `StoryBeat` minimal valide —
`beat_number=n, title="B", description="d", location_hint="l",
encounter_type="social"`.)

Append à `tests/ai/test_arc_generator.py` :

```python
class TestSanitizeLockedFacts:
    def test_locked_facts_are_clamped_and_deduped(self) -> None:
        data = {
            "villain_name": "V",
            "beats": [{
                "on_complete": {
                    "locked_facts": [
                        "  Un fait valide.  ",
                        "un fait valide.",          # doublon (casse près)
                        "x" * 500,                   # trop long
                        42,                          # mauvais type
                        "Un second fait valide.",
                        "Un troisième — au-delà du cap de 2.",
                    ],
                },
            }],
        }
        ArcGenerator._sanitize_arc_data(data)
        facts = data["beats"][0]["on_complete"]["locked_facts"]
        assert facts[0] == "Un fait valide."
        assert len(facts) == 2
        assert all(len(f) <= 200 for f in facts)
```

Append à `tests/bot/pipeline/test_narrate_memory.py` (classe existante ou
nouvelle) :

```python
class TestRenderLockedFactsCap:
    def test_render_caps_at_15_lines_deaths_first(self) -> None:
        from bot.pipeline.narrate import _render_locked_facts
        from world.story_arc import LockedFact
        session = MagicMock()
        facts = [LockedFact(id=f"npc_dead:N{i}", text=f"N{i} est mort.") for i in range(4)]
        facts += [LockedFact(id=f"beat:{i}:hint", text=f"Fait {i}.") for i in range(20)]
        session.story_arc.locked_facts = facts
        rendered = _render_locked_facts(session)
        lines = rendered.splitlines()
        assert lines[0] == "[LOCKED FACTS]"
        assert len(lines) == 1 + 15
        # Les 4 morts sont tous là, puis les 11 faits de beat les plus récents.
        assert sum("npc_dead:" in line for line in lines) == 4
        assert "[beat:19:hint]" in rendered and "[beat:8:hint]" not in rendered
```

- [ ] **Step 2: Vérifier l'échec**

Run: `uv run pytest tests/world/test_world_models.py tests/ai/test_arc_generator.py tests/bot/pipeline/test_narrate_memory.py -v`
Expected: FAIL — `locked_facts` inconnu de `BeatEffects` / import manquant

- [ ] **Step 3: Implémenter**

**`world/story_arc.py`** — dans `BeatEffects` :

```python
    locked_facts: list[str] = Field(default_factory=list)
    """World facts to lock into ``StoryArc.locked_facts`` when this beat
    completes (max 2, sanitized by the arc generator). Engine-authored
    channel — the LLM prompt does not expose this field."""
```

Après la classe `LockedFact`, ajouter :

```python
def append_beat_locked_facts(
    arc: "StoryArc", effects: BeatEffects, beat_number: int,
) -> None:
    """Lock a completed beat's consequences into the arc — idempotent.

    Sources: explicit ``effects.locked_facts`` entries (``beat:{n}:{i}``)
    and the beat's ``narrative_hint`` (``beat:{n}:hint``) when present.
    """
    existing = {fact.id for fact in arc.locked_facts}
    entries = [
        (f"beat:{beat_number}:{i}", text)
        for i, text in enumerate(effects.locked_facts)
    ]
    if effects.narrative_hint:
        entries.append((f"beat:{beat_number}:hint", effects.narrative_hint))
    for fact_id, text in entries:
        if fact_id not in existing:
            arc.locked_facts.append(LockedFact(id=fact_id, text=text))
```

**`ai/arc_generator.py`** — dans `_sanitize_arc_data`, dans le bloc
`if isinstance(on_complete, dict):` (après la coercion `state_flags`,
`arc_generator.py:204-210`) :

```python
                raw_facts = on_complete.get("locked_facts")
                if isinstance(raw_facts, list):
                    cleaned: list[str] = []
                    seen_facts: set[str] = set()
                    for entry in raw_facts:
                        if not isinstance(entry, str):
                            continue
                        text = entry.strip()[:200]
                        if text and text.lower() not in seen_facts:
                            seen_facts.add(text.lower())
                            cleaned.append(text)
                    on_complete["locked_facts"] = cleaned[:2]
```

**`bot/pipeline/orchestrator.py`** — signature et fin de
`_apply_beat_effects` (`orchestrator.py:772`) :

```python
    async def _apply_beat_effects(
        self, effects: BeatEffects, *, beat_number: int,
    ) -> str:
```

et juste avant le `return effects.narrative_hint` final (les DEUX returns —
celui du cas `loc is None` et celui de fin) insérer l'écriture des faits ;
pour éviter la duplication, restructurer la fin ainsi :

```python
        self._lock_beat_facts(effects, beat_number)

        loc = self.location
        if loc is None:
            return effects.narrative_hint
        ...  # (mutations de lieu existantes, inchangées)
        return effects.narrative_hint

    def _lock_beat_facts(self, effects: BeatEffects, beat_number: int) -> None:
        """Phase 2 porte de cohérence — beat consequences become locked facts."""
        from world.story_arc import append_beat_locked_facts

        session = getattr(self, "session", None)
        arc = getattr(session, "story_arc", None) if session is not None else None
        if arc is None or not isinstance(getattr(arc, "locked_facts", None), list):
            return
        append_beat_locked_facts(arc, effects, beat_number)
```

Au call site (`orchestrator.py:613`) :

```python
                hint = await self._apply_beat_effects(
                    old_beat.on_complete, beat_number=old_beat.beat_number,
                )
```

**`bot/pipeline/narrate.py`** — `_render_locked_facts` plafonné :

```python
_LOCKED_FACTS_MAX_LINES = 15
"""Cap on the [LOCKED FACTS] prompt block — deaths first, then the most
recent beat facts. Bounds prompt growth on long campaigns (spec §2.2)."""


def _render_locked_facts(session: "GameSession") -> str:
    arc = getattr(session, "story_arc", None)
    facts = getattr(arc, "locked_facts", None) if arc is not None else None
    # isinstance guard: tests drive the pipeline with MagicMock sessions
    if not isinstance(facts, list) or not facts:
        return ""
    deaths = [f for f in facts if f.id.startswith("npc_dead:")]
    others = [f for f in facts if not f.id.startswith("npc_dead:")]
    kept = deaths[:_LOCKED_FACTS_MAX_LINES]
    remaining = _LOCKED_FACTS_MAX_LINES - len(kept)
    if remaining > 0 and others:
        kept += others[-remaining:]
    lines = ["[LOCKED FACTS]"]
    lines += [f"- [{fact.id}] {fact.text}" for fact in kept]
    return "\n".join(lines)
```

- [ ] **Step 4: Vérifier que tout passe**

Run: `uv run pytest tests/world/ tests/ai/test_arc_generator.py tests/bot/pipeline/ -q`
Expected: PASS

- [ ] **Step 5: Gates + commit**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy .`
Expected: suite complète verte / clean / 0 erreur

```bash
git add world/story_arc.py ai/arc_generator.py bot/pipeline/orchestrator.py bot/pipeline/narrate.py tests/world/test_world_models.py tests/ai/test_arc_generator.py tests/bot/pipeline/test_narrate_memory.py
git commit -m "feat(coherence): locked facts génériques écrits par le moteur à la complétion des beats"
```

---

### Task 7: Passe finale — gates complets + board

**Files:**
- Modify: `tasks/todo.md`

- [ ] **Step 1: Gates complets**

Run: `uv run pytest -q`
Expected: 0 failed (≈ 3030+ passed — la suite de départ était à 2979 + les ~50 nouveaux tests)

Run: `uv run ruff check . && uv run mypy .`
Expected: clean / 0 erreur sur ≥ 352 fichiers

- [ ] **Step 2: Vérifier le câblage réel (leçon « une fonction testée n'est pas câblée »)**

Run: `grep -rn "check_narration\|build_coherence_snapshot\|append_beat_locked_facts\|template_narration" --include="*.py" bot/ ai/ | grep -v test`
Expected: chaque symbole apparaît au moins une fois hors tests
(`narrate.py`/`orchestrator.py`/`narrator.py`).

- [ ] **Step 3: Mettre à jour `tasks/todo.md`**

Ajouter au board (section chantiers clos / correctness selon la structure du
fichier) une entrée datée 2026-07-20 : porte de cohérence en prod — 11 règles
partagées (4 BLOCK / 7 OBSERVE), politique retry → template, locked facts de
beats, logger `memory.coherence` comme source de promotion OBSERVE→BLOCK.
Mentionner le suivi ouvert : « après N sessions réelles, dépouiller les logs
`memory.coherence` et statuer sur la promotion de `R1.phantom_npc`,
`R1.hp_mismatch`, `R1.location_mismatch` ».

- [ ] **Step 4: Commit final**

```bash
git add tasks/todo.md
git commit -m "docs(todo): porte de cohérence câblée — 11 règles partagées, 4 bloquantes, télémétrie de promotion"
```

---

## Self-review du plan (fait à la rédaction)

- **Couverture spec** : §1.1 noyau → T1-T2 ; §1.2 modes → registre T2 (amendé) ;
  §1.3 orchestration → T4 ; §1.4 câblage/politique → T5 ; §1.5 adaptateurs →
  T3 ; §2.1-2.2 phase 2 → T6 ; « Vérification » → steps de chaque tâche + T7.
- **R3/drift** : non portées (amendement spec) — `drift.py` et
  `rules/__init__.py` intacts, vérifié par la suite sim en T3.
- **Cohérence des types** : `CoherenceSnapshot`/`CoherenceViolation`/`RuleMode`
  définis en T1-T2 et consommés avec les mêmes noms en T3-T5 ;
  `GuardVerdict.blocking/observed` (T4) consommés en T5 ;
  `append_beat_locked_facts(arc, effects, beat_number)` (T6) unique définition.
- **Placeholders** : aucun TBD ; les deux « échecs acceptables » (T3 step 3,
  T5 step 4) sont bornés et justifiés — tout autre échec est une régression.
