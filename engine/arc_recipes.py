"""Arc Recipe Engine — code-driven campaign scaffolding.

Generates structured "recipes" that define the skeleton of a campaign arc:
beat sequence, complications, tone, twist position, etc.  The LLM then
fills each beat with creative narrative content.

This ensures structural variety across campaigns without relying on LLM
brainstorming for pacing decisions.

Pure deterministic logic (randomness via random module only).
"""

import random
from enum import StrEnum

from pydantic import BaseModel, model_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class BeatType(StrEnum):
    """High-level encounter type for a story beat."""

    social = "social"
    combat = "combat"
    exploration = "exploration"
    puzzle = "puzzle"
    boss = "boss"


class Archetype(StrEnum):
    """Narrative archetype driving the arc's overall shape."""

    mystery = "mystery"
    heist = "heist"
    siege = "siege"
    diplomacy = "diplomacy"
    survival = "survival"
    revenge = "revenge"
    escape = "escape"
    corruption = "corruption"
    discovery = "discovery"
    betrayal = "betrayal"


class Tone(StrEnum):
    """Narrative tone applied to the arc."""

    sombre = "sombre"
    humoristique = "humoristique"
    epique = "épique"
    intimiste = "intimiste"
    mysterieux = "mystérieux"
    melancolique = "mélancolique"
    tendu = "tendu"
    merveilleux = "merveilleux"
    dramatique = "dramatique"


class VillainArchetype(StrEnum):
    """Villain personality archetype."""

    tyran = "tyran"
    manipulateur = "manipulateur"
    fanatique = "fanatique"
    opportuniste = "opportuniste"
    tragique = "tragique"
    monstre = "monstre"
    rival = "rival"
    corrompu = "corrompu"


# ---------------------------------------------------------------------------
# Archetype definitions
# ---------------------------------------------------------------------------

class ArchetypeTemplate(BaseModel):
    """Template data for a narrative archetype."""

    base_beats: list[BeatType]
    twist_range: tuple[int, int]
    default_tone: Tone


ARCHETYPE_TEMPLATES: dict[Archetype, ArchetypeTemplate] = {
    Archetype.mystery: ArchetypeTemplate(
        base_beats=[
            BeatType.exploration, BeatType.social, BeatType.puzzle,
            BeatType.social, BeatType.exploration, BeatType.puzzle,
            BeatType.combat, BeatType.boss,
        ],
        twist_range=(5, 7),
        default_tone=Tone.mysterieux,
    ),
    Archetype.heist: ArchetypeTemplate(
        base_beats=[
            BeatType.social, BeatType.puzzle, BeatType.exploration,
            BeatType.combat, BeatType.puzzle, BeatType.boss,
        ],
        twist_range=(3, 4),
        default_tone=Tone.tendu,
    ),
    Archetype.siege: ArchetypeTemplate(
        base_beats=[
            BeatType.social, BeatType.combat, BeatType.exploration,
            BeatType.combat, BeatType.combat, BeatType.boss,
        ],
        twist_range=(4, 5),
        default_tone=Tone.epique,
    ),
    Archetype.diplomacy: ArchetypeTemplate(
        base_beats=[
            BeatType.social, BeatType.social, BeatType.exploration,
            BeatType.puzzle, BeatType.social, BeatType.social,
            BeatType.boss,
        ],
        twist_range=(6, 8),
        default_tone=Tone.intimiste,
    ),
    Archetype.survival: ArchetypeTemplate(
        base_beats=[
            BeatType.exploration, BeatType.combat, BeatType.exploration,
            BeatType.puzzle, BeatType.combat, BeatType.exploration,
            BeatType.boss,
        ],
        twist_range=(3, 5),
        default_tone=Tone.sombre,
    ),
    Archetype.revenge: ArchetypeTemplate(
        base_beats=[
            BeatType.social, BeatType.exploration, BeatType.combat,
            BeatType.social, BeatType.combat, BeatType.boss,
        ],
        twist_range=(2, 3),
        default_tone=Tone.dramatique,
    ),
    Archetype.escape: ArchetypeTemplate(
        base_beats=[
            BeatType.combat, BeatType.exploration, BeatType.puzzle,
            BeatType.exploration, BeatType.combat, BeatType.boss,
        ],
        twist_range=(2, 4),
        default_tone=Tone.tendu,
    ),
    Archetype.corruption: ArchetypeTemplate(
        base_beats=[
            BeatType.social, BeatType.exploration, BeatType.social,
            BeatType.puzzle, BeatType.social, BeatType.combat,
            BeatType.boss,
        ],
        twist_range=(4, 6),
        default_tone=Tone.sombre,
    ),
    Archetype.discovery: ArchetypeTemplate(
        base_beats=[
            BeatType.exploration, BeatType.exploration, BeatType.social,
            BeatType.puzzle, BeatType.exploration, BeatType.social,
            BeatType.boss,
        ],
        twist_range=(5, 6),
        default_tone=Tone.merveilleux,
    ),
    Archetype.betrayal: ArchetypeTemplate(
        base_beats=[
            BeatType.social, BeatType.social, BeatType.social,
            BeatType.combat, BeatType.exploration, BeatType.puzzle,
            BeatType.boss,
        ],
        twist_range=(3, 4),
        default_tone=Tone.dramatique,
    ),
}


# ---------------------------------------------------------------------------
# Encounter subtypes
# ---------------------------------------------------------------------------

BEAT_SUBTYPES: dict[BeatType, list[str]] = {
    BeatType.social: [
        "negotiation", "interrogation", "seduction", "deception", "ceremony",
    ],
    BeatType.combat: [
        "ambush", "duel", "siege", "chase", "defense",
    ],
    BeatType.exploration: [
        "tracking", "infiltration", "navigation", "discovery", "survival",
    ],
    BeatType.puzzle: [
        "riddle", "mechanism", "investigation", "ritual", "cipher",
    ],
    BeatType.boss: ["boss"],
}


# ---------------------------------------------------------------------------
# Complication pool (French strings)
# ---------------------------------------------------------------------------

COMPLICATIONS: list[str] = [
    "Trahison d'un allié",
    "Course contre la montre",
    "Dilemme moral",
    "Ressource rare épuisée",
    "Fausse piste",
    "Rival concurrent",
    "Catastrophe naturelle",
    "Maladie ou malédiction",
    "Dette ancienne",
    "Identité secrète",
    "Prophétie ambiguë",
    "Faction alliée devenue hostile",
    "Otage",
    "Prix à payer",
    "Allié ambigu",
]


# ---------------------------------------------------------------------------
# ArcRecipe model
# ---------------------------------------------------------------------------

class ArcRecipe(BaseModel):
    """A fully-specified recipe for one campaign arc."""

    archetype: Archetype
    beat_sequence: list[BeatType]
    beat_subtypes: list[str]
    complications: list[str]
    tone: Tone
    twist_position: int
    num_beats: int
    villain_archetype: VillainArchetype | None = None

    @model_validator(mode="after")
    def _validate_recipe(self) -> "ArcRecipe":
        """Enforce structural constraints on the recipe."""
        # beat_sequence length must match num_beats
        if len(self.beat_sequence) != self.num_beats:
            msg = (
                f"beat_sequence length ({len(self.beat_sequence)}) "
                f"!= num_beats ({self.num_beats})"
            )
            raise ValueError(msg)

        # beat_subtypes length must match num_beats
        if len(self.beat_subtypes) != self.num_beats:
            msg = (
                f"beat_subtypes length ({len(self.beat_subtypes)}) "
                f"!= num_beats ({self.num_beats})"
            )
            raise ValueError(msg)

        # Last beat must be boss
        if self.beat_sequence[-1] != BeatType.boss:
            msg = f"Last beat must be 'boss', got '{self.beat_sequence[-1]}'"
            raise ValueError(msg)

        # No more than 2 consecutive beats of the same type (any type)
        for i in range(len(self.beat_sequence) - 2):
            a, b, c = self.beat_sequence[i], self.beat_sequence[i + 1], self.beat_sequence[i + 2]
            if a == b == c and a != BeatType.boss:
                msg = f"No more than 2 consecutive beats of the same type, got 3× '{a}' at index {i}"
                raise ValueError(msg)

        # At least 1 puzzle
        if sum(1 for b in self.beat_sequence if b == BeatType.puzzle) < 1:
            msg = "Arc must contain at least 1 puzzle beat"
            raise ValueError(msg)

        # At least 2 social beats
        if sum(1 for b in self.beat_sequence if b == BeatType.social) < 2:
            msg = "Arc must contain at least 2 social beats"
            raise ValueError(msg)

        return self


# ---------------------------------------------------------------------------
# Recipe generation
# ---------------------------------------------------------------------------

def _expand_beats(
    base: list[BeatType],
    target_length: int,
) -> list[BeatType]:
    """Expand a base beat sequence to *target_length* beats.

    Inserts extra beats proportionally (matching the archetype's rhythm)
    while keeping the last beat as boss. Avoids creating 3+ consecutive
    beats of the same type during insertion.
    """
    if target_length <= len(base):
        return list(base)

    # Separate the boss ending
    core = list(base[:-1])
    boss = base[-1]

    # Build a weighted pool from the core beats (excluding boss)
    pool = [b for b in core if b != BeatType.boss]
    if not pool:
        pool = [BeatType.social, BeatType.exploration, BeatType.puzzle]

    extra_needed = target_length - len(base)

    for _ in range(extra_needed):
        chosen = random.choice(pool)
        insert_pos = random.randint(0, len(core))

        # Try up to 10 positions to avoid creating 3 consecutive same-type
        for _ in range(10):
            before = core[insert_pos - 1] if insert_pos > 0 else None
            after = core[insert_pos] if insert_pos < len(core) else None
            if before == chosen and after == chosen:
                insert_pos = random.randint(0, len(core))
                continue
            two_before = core[insert_pos - 2] if insert_pos > 1 else None
            two_after = core[insert_pos + 1] if insert_pos + 1 < len(core) else None
            if (before == chosen and two_before == chosen) or \
               (after == chosen and two_after == chosen):
                insert_pos = random.randint(0, len(core))
                continue
            break
        core.insert(insert_pos, chosen)

    return core + [boss]


def _ensure_constraints(beats: list[BeatType]) -> list[BeatType]:
    """Patch a beat list so it satisfies the structural constraints.

    Fixes applied:
    - Break runs of 3+ consecutive beats of the same type
    - Ensure at least 1 puzzle
    - Ensure at least 2 social beats
    - Last beat remains boss
    """
    result = list(beats)

    # --- Fix 3+ consecutive beats of the same type ---
    _non_boss_types = [BeatType.social, BeatType.combat, BeatType.exploration, BeatType.puzzle]
    changed = True
    while changed:
        changed = False
        for i in range(len(result) - 2):
            if result[i] == result[i + 1] == result[i + 2] and result[i] != BeatType.boss:
                # Replace the third with a different type
                alternatives = [t for t in _non_boss_types if t != result[i]]
                result[i + 2] = random.choice(alternatives)
                changed = True

    # --- Ensure at least 1 puzzle ---
    puzzle_count = sum(1 for b in result if b == BeatType.puzzle)
    if puzzle_count < 1:
        # Replace a non-boss, non-social beat with puzzle
        candidates = [
            i for i, b in enumerate(result)
            if b not in (BeatType.boss, BeatType.social, BeatType.puzzle)
        ]
        if candidates:
            result[random.choice(candidates)] = BeatType.puzzle

    # --- Ensure at least 2 social ---
    social_count = sum(1 for b in result if b == BeatType.social)
    while social_count < 2:
        candidates = [
            i for i, b in enumerate(result)
            if b not in (BeatType.boss, BeatType.social, BeatType.puzzle)
        ]
        if candidates:
            result[random.choice(candidates)] = BeatType.social
            social_count += 1
        else:
            break

    # --- Last beat must be boss ---
    result[-1] = BeatType.boss

    return result


def generate_recipe(
    theme: str,
    previous_archetype: str | None = None,
) -> ArcRecipe:
    """Generate a randomised arc recipe.

    Args:
        theme: Campaign theme (stored for context but doesn't drive
            selection — the archetype handles pacing).
        previous_archetype: If provided, this archetype will be excluded
            from selection to encourage variety.

    Returns:
        A fully validated ArcRecipe ready for LLM narrative filling.
    """
    # --- Select archetype (exclude previous) ---
    available = list(Archetype)
    if previous_archetype is not None:
        available = [a for a in available if a.value != previous_archetype]
    archetype = random.choice(available)
    template = ARCHETYPE_TEMPLATES[archetype]

    # --- Determine num_beats ---
    num_beats = random.randint(10, 15)

    # --- Build beat sequence ---
    beats = _expand_beats(template.base_beats, num_beats)
    beats = _ensure_constraints(beats)

    # Length might differ from num_beats after patching; reconcile
    num_beats = len(beats)

    # --- Assign subtypes ---
    subtypes = [random.choice(BEAT_SUBTYPES[b]) for b in beats]

    # --- Pick complications (1-2) ---
    num_complications = random.randint(1, 2)
    complications = random.sample(COMPLICATIONS, num_complications)

    # --- Pick tone ---
    if random.random() < 0.3:
        tone = random.choice(list(Tone))
    else:
        tone = template.default_tone

    # --- Twist position ---
    lo, hi = template.twist_range
    # Clamp to valid beat indices (excluding last boss beat)
    hi = min(hi, num_beats - 2)
    lo = min(lo, hi)
    twist_position = random.randint(lo, hi)

    # --- Villain archetype (optional, ~70% chance) ---
    villain: VillainArchetype | None = None
    if random.random() < 0.7:
        villain = random.choice(list(VillainArchetype))

    return ArcRecipe(
        archetype=archetype,
        beat_sequence=beats,
        beat_subtypes=subtypes,
        complications=complications,
        tone=tone,
        twist_position=twist_position,
        num_beats=num_beats,
        villain_archetype=villain,
    )
