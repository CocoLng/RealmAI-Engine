"""Entity resolver — matches raw textual references to concrete game entities.

Pure Python (no LLM by default; an optional ``Interpreter`` may be passed in
for a 4B fallback when Python matching fails — see Lot B post-mortem).

Three possible outcomes:
- ``resolved``: exactly one canonical match found
- ``ambiguous``: multiple matches — caller must ask the user to disambiguate
- ``unknown``: no match — caller should narrate an in-character refusal

Actions that do not reference external entities (``LOOK``, ``IMPROVISE``,
``DEFEND``, ``FLEE``) return ``not_applicable``.

Matching strategy (per field, stop-early), v2 — French-aware:
1. Exact normalized match.
2. Lemma-overlap match against (candidate name + aliases). French suffix
   rules cover gender/number variants (villageur ↔ villageoise ↔ villageois…).
3. Fuzzy fallback (``difflib`` ratio ≥ 0.75) against name or any alias.
4. Optional LLM disambiguator (Interpreter.disambiguate_entity), only when
   the Python pipeline returns no matches at all.
"""

from __future__ import annotations

import difflib
import logging
import unicodedata
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from ai.models import InterpretedAction
from engine.combat import CombatSide, CombatState
from engine.inventory import Inventory
from engine.validators import ActionType
from world.location import Location
from world.npc import NPC

if TYPE_CHECKING:
    from ai.interpreter import Interpreter

logger = logging.getLogger(__name__)

FUZZY_THRESHOLD = 0.75


ResolutionStatus = Literal["resolved", "ambiguous", "unknown", "not_applicable"]


@dataclass(frozen=True)
class EntityCandidate:
    """A resolution candidate with display info for clarification buttons."""

    id: str
    label: str
    description: str = ""


@dataclass
class ResolutionResult:
    """Outcome of entity resolution for a single action."""

    status: ResolutionStatus
    field_name: str | None = None
    raw_value: str | None = None
    resolved_entity: str | None = None
    candidates: list[EntityCandidate] = field(default_factory=list)
    reason: str | None = None
    reclassified_action_type: ActionType | None = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class EntityResolver:
    """Dispatch entity resolution based on action type.

    Stateless; all methods are pure functions of ``(action, scene data)``.
    """

    @staticmethod
    def resolve(
        action: InterpretedAction,
        *,
        location: Location | None,
        npcs: dict[str, NPC],
        combat_state: CombatState | None = None,
        inventory: Inventory | None = None,
        interpreter: "Interpreter | None" = None,
        language: str = "fr",
    ) -> ResolutionResult:
        """Resolve the raw entity references in ``action`` to canonical names.

        Args:
            action: Interpreted player action (output of the Interpreter).
            location: The current location, or None if unset.
            npcs: The full NPC registry; only NPCs matching
                ``location.name`` are considered present.
            combat_state: Active combat, if any (used for ATTACK/CAST_SPELL).
            inventory: The acting character's inventory (used for USE_ITEM).

        Returns:
            A ResolutionResult describing the outcome.
        """
        at = action.action_type
        if at in (
            ActionType.LOOK,
            ActionType.DEFEND,
            ActionType.FLEE,
            ActionType.IMPROVISE,
            ActionType.QUESTION,
        ):
            return ResolutionResult(status="not_applicable")

        if at == ActionType.TALK:
            return _resolve_npc(
                action, location, npcs,
                interpreter=interpreter, language=language,
            )
        if at == ActionType.MOVE:
            return _resolve_exit(action, location)
        if at == ActionType.SEARCH:
            return _resolve_search(action, location)
        if at == ActionType.INTERACT:
            return _resolve_object(action, location)
        if at in (ActionType.ATTACK, ActionType.CAST_SPELL):
            return _resolve_combatant(
                action, combat_state,
                location=location, npcs=npcs,
                interpreter=interpreter, language=language,
            )
        if at == ActionType.USE_ITEM:
            return _resolve_item(action, inventory, location)
        if at == ActionType.PICKUP:
            return _resolve_pickup(action, location)

        # Should be unreachable — any ActionType must be covered above.
        return ResolutionResult(
            status="not_applicable",
            reason=f"EntityResolver has no handler for {at!r}",
        )


# ---------------------------------------------------------------------------
# Helpers — normalization + matching
# ---------------------------------------------------------------------------


_STOPWORDS_FR = {
    "le", "la", "les", "l", "un", "une", "des", "du", "de", "d",
    "au", "aux", "et", "ou", "a", "ce", "cet", "cette", "ces",
    "mon", "ma", "mes", "ton", "ta", "tes", "son", "sa", "ses",
    "the", "a", "an",
}


def _strip_diacritics(text: str) -> str:
    """Strip diacritics from text (NFKD decomposition)."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


_PUNCT_TRANS = str.maketrans({c: " " for c in ",.;:!?'\"()[]{}«»—–-"})


def _normalize(text: str) -> str:
    """Lowercase, strip diacritics + punctuation, and trim whitespace."""
    return _strip_diacritics(text).translate(_PUNCT_TRANS).lower().strip()


# French suffix → variant table. Each entry maps a recognised ending to the
# set of equivalent endings (gender + number variants). The lemma key is
# always the *stem* (everything before the suffix); we then re-attach every
# variant to that stem to produce a lemma family.
_FR_SUFFIX_GROUPS: list[tuple[str, ...]] = [
    ("eur", "euse", "eurs", "euses"),
    ("eux", "euse", "euses"),
    ("ois", "oise", "oises"),
    ("ais", "aise", "aises"),
    ("ien", "ienne", "iens", "iennes"),
    ("er", "ere", "ers", "eres"),
    ("on", "onne", "ons", "onnes"),
    ("eau", "eaux", "elle", "elles"),
    ("teur", "trice", "teurs", "trices"),
]


def _lemmatize_fr(token: str) -> set[str]:
    """Return all morphological variants of a French token (rule-based).

    Cheap rule-based stemming: strip a known suffix, then re-attach every
    variant of that suffix family. Also drops generic plural ``s``/``x`` so
    "villageurs" → contains "villageur" → expands to villageoise/villageois…
    """
    if not token:
        return set()
    variants: set[str] = {token}

    # Generic plural strip.
    if len(token) > 3 and token.endswith(("s", "x")):
        variants.add(token[:-1])

    expanded: set[str] = set()
    for v in variants:
        expanded.add(v)
        for group in _FR_SUFFIX_GROUPS:
            for suffix in group:
                if v.endswith(suffix) and len(v) > len(suffix) + 1:
                    stem = v[: -len(suffix)]
                    for alt in group:
                        expanded.add(stem + alt)
                    break  # one matching group per variant is enough
    return expanded


def _tokens(text: str) -> list[str]:
    """Return non-stopword tokens of a normalized string."""
    return [t for t in _normalize(text).split() if t and t not in _STOPWORDS_FR]


def _expand_query(query: str) -> set[str]:
    """Return the union of French lemma variants for every token in query."""
    lemmas: set[str] = set()
    for tok in _tokens(query):
        lemmas |= _lemmatize_fr(tok)
    return lemmas


def _candidate_lemmas(name: str, aliases: list[str] | None = None) -> set[str]:
    """Return the lemma family of a candidate (name tokens + alias tokens)."""
    lemmas: set[str] = set()
    for tok in _tokens(name):
        lemmas |= _lemmatize_fr(tok)
    for alias in aliases or []:
        for tok in _tokens(alias):
            lemmas |= _lemmatize_fr(tok)
    return lemmas


def _fuzzy_ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def _match_candidates_v2(
    query: str,
    candidates: list[str],
    aliases_by_candidate: dict[str, list[str]] | None = None,
) -> list[str]:
    """Match a player query against candidate names.

    Steps (stop-early):
    1. Exact normalized name match.
    2. French lemma overlap against (name + aliases). When several candidates
       overlap, keep those with the largest intersection.
    3. Fuzzy fallback (``difflib`` ratio ≥ FUZZY_THRESHOLD) against name and
       aliases.
    """
    if not query or not candidates:
        return []

    nq = _normalize(query)
    if not nq:
        return []

    # 1. Exact normalized match.
    exact = [c for c in candidates if _normalize(c) == nq]
    if exact:
        return exact

    aliases_by_candidate = aliases_by_candidate or {}

    # 2. Lemma overlap.
    query_lemmas = _expand_query(query)
    if query_lemmas:
        scored: list[tuple[int, str]] = []
        for c in candidates:
            cand_lemmas = _candidate_lemmas(c, aliases_by_candidate.get(c))
            inter = query_lemmas & cand_lemmas
            if inter:
                scored.append((len(inter), c))
        if scored:
            top = max(s for s, _ in scored)
            return [c for s, c in scored if s == top]

    # 3. Fuzzy fallback.
    fuzzy: list[tuple[float, str]] = []
    for c in candidates:
        names_to_try = [_normalize(c)] + [
            _normalize(a) for a in aliases_by_candidate.get(c, [])
        ]
        best = max(_fuzzy_ratio(nq, n) for n in names_to_try if n)
        if best >= FUZZY_THRESHOLD:
            fuzzy.append((best, c))
    if fuzzy:
        top_score = max(s for s, _ in fuzzy)
        return [c for s, c in fuzzy if s == top_score]

    return []


# Backwards-compat shim — the old name is still used by a couple of helpers
# below until they all migrate to the v2 signature.
def _match_candidates(query: str, candidates: list[str]) -> list[str]:
    return _match_candidates_v2(query, candidates)


# ---------------------------------------------------------------------------
# NPC resolution (TALK)
# ---------------------------------------------------------------------------


def _resolve_npc(
    action: InterpretedAction,
    location: Location | None,
    npcs: dict[str, NPC],
    *,
    interpreter: "Interpreter | None" = None,
    language: str = "fr",
) -> ResolutionResult:
    raw = action.target_name or ""
    if not raw:
        return ResolutionResult(
            status="unknown",
            field_name="target_name",
            raw_value="",
            reason="TALK action missing target_name",
        )

    location_name = location.name if location is not None else None
    present = [
        npc for npc in npcs.values()
        if npc.location_name is not None and npc.location_name == location_name
    ]
    present_names = [npc.name for npc in present]
    aliases_by_name = {npc.name: list(npc.aliases) for npc in present}

    matches = _match_candidates_v2(raw, present_names, aliases_by_name)

    # LLM fallback (Lot B): only when Python pipeline is fully empty.
    if not matches and interpreter is not None and present_names:
        try:
            llm_pick = interpreter.disambiguate_entity(
                raw_reference=raw,
                candidates=[
                    (npc.name, list(npc.aliases)) for npc in present
                ],
                language=language,
            )
        except Exception:  # noqa: BLE001 — defensive: never break gameplay
            logger.warning("LLM disambiguator failed", exc_info=True)
            llm_pick = None
        if llm_pick and llm_pick in present_names:
            matches = [llm_pick]

    if not matches:
        return ResolutionResult(
            status="unknown",
            field_name="target_name",
            raw_value=raw,
            reason=f"No NPC matches '{raw}' in {location_name!r}",
        )
    if len(matches) == 1:
        return ResolutionResult(
            status="resolved",
            field_name="target_name",
            raw_value=raw,
            resolved_entity=matches[0],
        )

    by_name = {npc.name: npc for npc in present}
    return ResolutionResult(
        status="ambiguous",
        field_name="target_name",
        raw_value=raw,
        candidates=[
            EntityCandidate(
                id=name,
                label=name,
                description=by_name[name].description or "",
            )
            for name in matches
        ],
    )


# ---------------------------------------------------------------------------
# Exit resolution (MOVE)
# ---------------------------------------------------------------------------


def _resolve_exit(
    action: InterpretedAction,
    location: Location | None,
) -> ResolutionResult:
    raw = action.target_name or ""
    if location is None:
        return ResolutionResult(
            status="unknown",
            field_name="target_name",
            raw_value=raw,
            reason="MOVE without current location",
        )
    if not raw:
        return ResolutionResult(
            status="unknown",
            field_name="target_name",
            raw_value="",
            reason="MOVE action missing target_name",
        )

    matches = _match_candidates(raw, list(location.connections))
    if not matches:
        return ResolutionResult(
            status="unknown",
            field_name="target_name",
            raw_value=raw,
            reason=f"No exit matches '{raw}'",
        )
    if len(matches) == 1:
        return ResolutionResult(
            status="resolved",
            field_name="target_name",
            raw_value=raw,
            resolved_entity=matches[0],
        )
    return ResolutionResult(
        status="ambiguous",
        field_name="target_name",
        raw_value=raw,
        candidates=[EntityCandidate(id=m, label=m) for m in matches],
    )


# ---------------------------------------------------------------------------
# Object resolution (SEARCH — permissive, INTERACT — strict)
# ---------------------------------------------------------------------------


def _available_objects(location: Location | None) -> list[str]:
    if location is None:
        return []
    return list(location.items_available)


def _resolve_search(
    action: InterpretedAction,
    location: Location | None,
) -> ResolutionResult:
    """SEARCH is permissive: a general search (no target) is allowed, and a
    search for an unlisted object is allowed too — the narrator arbitrates
    whether anything is found."""
    raw = action.target_name
    if raw is None:
        return ResolutionResult(status="not_applicable")

    matches = _match_candidates(raw, _available_objects(location))
    if len(matches) == 1:
        return ResolutionResult(
            status="resolved",
            field_name="target_name",
            raw_value=raw,
            resolved_entity=matches[0],
        )
    if len(matches) > 1:
        return ResolutionResult(
            status="ambiguous",
            field_name="target_name",
            raw_value=raw,
            candidates=[EntityCandidate(id=m, label=m) for m in matches],
        )
    # Permissive fallback — pass through the raw value.
    return ResolutionResult(
        status="resolved",
        field_name="target_name",
        raw_value=raw,
        resolved_entity=raw,
    )


def _resolve_object(
    action: InterpretedAction,
    location: Location | None,
) -> ResolutionResult:
    raw = action.target_name or ""
    if not raw:
        return ResolutionResult(
            status="unknown",
            field_name="target_name",
            raw_value="",
            reason="INTERACT action missing target_name",
        )

    matches = _match_candidates(raw, _available_objects(location))
    if not matches:
        return ResolutionResult(
            status="unknown",
            field_name="target_name",
            raw_value=raw,
            reason=f"No object matches '{raw}'",
        )
    if len(matches) == 1:
        return ResolutionResult(
            status="resolved",
            field_name="target_name",
            raw_value=raw,
            resolved_entity=matches[0],
        )
    return ResolutionResult(
        status="ambiguous",
        field_name="target_name",
        raw_value=raw,
        candidates=[EntityCandidate(id=m, label=m) for m in matches],
    )


# ---------------------------------------------------------------------------
# Pickup resolution (PICKUP)
# ---------------------------------------------------------------------------


def _resolve_pickup(
    action: InterpretedAction,
    location: Location | None,
) -> ResolutionResult:
    """Resolve a PICKUP target against ``location.items_available``.

    Accepts ``target_name`` first, falls back to ``item_name``. Returns
    ``unknown`` if the location has no matching item — the player either
    referenced an item not in the scene, or asked to pick up something that
    only exists in their head.
    """
    raw = action.target_name or action.item_name or ""
    if not raw:
        return ResolutionResult(
            status="unknown",
            field_name="target_name",
            raw_value="",
            reason="PICKUP action missing target_name/item_name",
        )

    matches = _match_candidates(raw, _available_objects(location))
    if not matches:
        return ResolutionResult(
            status="unknown",
            field_name="target_name",
            raw_value=raw,
            reason=f"No scene item matches '{raw}'",
        )
    if len(matches) == 1:
        return ResolutionResult(
            status="resolved",
            field_name="target_name",
            raw_value=raw,
            resolved_entity=matches[0],
        )
    return ResolutionResult(
        status="ambiguous",
        field_name="target_name",
        raw_value=raw,
        candidates=[EntityCandidate(id=m, label=m) for m in matches],
    )


# ---------------------------------------------------------------------------
# Combat target resolution (ATTACK / CAST_SPELL)
# ---------------------------------------------------------------------------


def _resolve_combatant(
    action: InterpretedAction,
    combat_state: CombatState | None,
    *,
    location: Location | None = None,
    npcs: dict[str, NPC] | None = None,
    interpreter: "Interpreter | None" = None,
    language: str = "fr",
) -> ResolutionResult:
    """Resolve an ATTACK / CAST_SPELL target.

    Searches first the active combat (alive ENEMY combatants), then falls
    back to NPCs present in the current location so a free-text attack
    against a non-combatant can bootstrap a new encounter (Lot C).
    """
    raw = action.target_name or ""
    if not raw:
        return ResolutionResult(
            status="unknown",
            field_name="target_name",
            raw_value=raw,
            reason="Combat target required but target name missing",
        )

    matches: list[str] = []

    # 1. Try active combat first.
    if combat_state is not None:
        enemy_names = [
            c.name for c in combat_state.combatants
            if c.side == CombatSide.ENEMY and c.is_alive
        ]
        enemy_aliases: dict[str, list[str]] = {}
        matches = _match_candidates_v2(raw, enemy_names, enemy_aliases)
        if matches:
            return _combatant_result(raw, matches)

    # 2. Fallback: NPCs present in the current location (Lot C).
    location_name = location.name if location is not None else None
    present: list[NPC] = []
    if npcs and location_name is not None:
        present = [
            npc for npc in npcs.values()
            if npc.location_name == location_name and npc.is_alive
        ]
    if present:
        present_names = [npc.name for npc in present]
        aliases_by_name = {npc.name: list(npc.aliases) for npc in present}
        matches = _match_candidates_v2(raw, present_names, aliases_by_name)

        # 3. LLM fallback (Lot B): only when Python pipeline is empty.
        if not matches and interpreter is not None:
            try:
                llm_pick = interpreter.disambiguate_entity(
                    raw_reference=raw,
                    candidates=[
                        (npc.name, list(npc.aliases)) for npc in present
                    ],
                    language=language,
                )
            except Exception:  # noqa: BLE001 — defensive: never break gameplay
                logger.warning("LLM disambiguator failed", exc_info=True)
                llm_pick = None
            if llm_pick and llm_pick in present_names:
                matches = [llm_pick]

    if not matches:
        return ResolutionResult(
            status="unknown",
            field_name="target_name",
            raw_value=raw,
            reason=f"No combat target matches '{raw}'",
        )
    return _combatant_result(raw, matches)


def _combatant_result(raw: str, matches: list[str]) -> ResolutionResult:
    if len(matches) == 1:
        return ResolutionResult(
            status="resolved",
            field_name="target_name",
            raw_value=raw,
            resolved_entity=matches[0],
        )
    return ResolutionResult(
        status="ambiguous",
        field_name="target_name",
        raw_value=raw,
        candidates=[EntityCandidate(id=m, label=m) for m in matches],
    )


# ---------------------------------------------------------------------------
# Inventory resolution (USE_ITEM)
# ---------------------------------------------------------------------------


def _resolve_item(
    action: InterpretedAction,
    inventory: Inventory | None,
    location: Location | None = None,
) -> ResolutionResult:
    raw = action.item_name or ""

    # --- inventory lookup (primary) ---
    if inventory is not None and raw:
        item_names = [i.name for i in inventory.items] + [
            i.name for i in inventory.equipped.values()
        ]
        matches = _match_candidates(raw, item_names)
        if len(matches) == 1:
            return ResolutionResult(
                status="resolved",
                field_name="item_name",
                raw_value=raw,
                resolved_entity=matches[0],
            )
        if len(matches) > 1:
            return ResolutionResult(
                status="ambiguous",
                field_name="item_name",
                raw_value=raw,
                candidates=[EntityCandidate(id=m, label=m) for m in matches],
            )

    # --- fallback: scene object (reclassify USE_ITEM → INTERACT) ---
    if raw:
        scene_matches = _match_candidates(raw, _available_objects(location))
        if len(scene_matches) == 1:
            return ResolutionResult(
                status="resolved",
                field_name="target_name",
                raw_value=raw,
                resolved_entity=scene_matches[0],
                reclassified_action_type=ActionType.INTERACT,
            )
        if len(scene_matches) > 1:
            return ResolutionResult(
                status="ambiguous",
                field_name="target_name",
                raw_value=raw,
                candidates=[
                    EntityCandidate(id=m, label=m) for m in scene_matches
                ],
                reclassified_action_type=ActionType.INTERACT,
            )

    return ResolutionResult(
        status="unknown",
        field_name="item_name",
        raw_value=raw,
        reason=f"No item matches '{raw}'",
    )
