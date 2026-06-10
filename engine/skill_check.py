"""Narrative-action → D&D 5e skill inference.

Pure deterministic mapper. Given a free-form action description (FR or EN),
returns the most likely :class:`engine.character.Skill` for a contested check,
or ``None`` if the action does not warrant a skill check (trivial actions
like sitting down or eating bread).

This module never calls an LLM and never rolls dice. It only picks the
skill bucket. The caller (``bot.pipeline.resolve``) is responsible for
computing the modifier, rolling the d20, and surfacing the result.

Design notes
------------
- Both languages share the same matcher: keywords from FR and EN are listed
  side-by-side per skill. Players may switch languages mid-campaign.
- A keyword matches as a whole word (regex word boundary), so "voler" hits
  but "envoler" / "survoler" do not.
- When several skills could match, ranking is decided by:
    1. Skills with a *qualified* keyword (e.g. "saut acrobatique" wins over
       a bare "saut") — qualifiers are stored separately as
       ``QUALIFIED_KEYWORDS`` and probed first.
    2. Otherwise, the longest keyword match wins (e.g. "pickpocket" beats
       "vole" in "I pickpocket the merchant").
- Empty strings, whitespace-only inputs, and texts with no listed keyword
  return ``None`` so the caller can fall back to the legacy "narrator
  arbitrates without a roll" behaviour for trivial actions.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from typing import TYPE_CHECKING

from engine.character import Ability, Skill, compute_modifier

if TYPE_CHECKING:
    from world.npc import NPC

__all__ = [
    "DEFAULT_SKILL_DC",
    "EASY_DC",
    "HARD_DC",
    "MODERATE_DC",
    "VERY_EASY_DC",
    "VERY_HARD_DC",
    "compute_contest_dc",
    "compute_skill_check_dc",
    "infer_skill_from_text",
]


# ---------------------------------------------------------------------------
# DC tiers (SRD 5e, simplified)
# ---------------------------------------------------------------------------

VERY_EASY_DC: int = 5
EASY_DC: int = 10
MODERATE_DC: int = 12
HARD_DC: int = 15
VERY_HARD_DC: int = 20

DEFAULT_SKILL_DC: int = MODERATE_DC
"""DC used by ``resolve_improvise_skill_check`` when no scene cue overrides it.

12 sits between SRD "Easy" (10) and "Hard" (15) — adventuring-grade
challenge for a non-trivial improvised action.
"""

# Per-skill defensive ability — the NPC stat that "fights back" when the
# player rolls this skill against them. None means the skill has no
# inherent NPC opposition (the difficulty is environmental, not social).
_DEFENSIVE_ABILITY: dict[Skill, Ability | None] = {
    # Stealth / theft are noticed via passive Perception — WIS-based.
    Skill.STEALTH: Ability.WIS,
    Skill.SLEIGHT_OF_HAND: Ability.WIS,
    # Lies are seen through with Insight — WIS-based.
    Skill.DECEPTION: Ability.WIS,
    Skill.INSIGHT: Ability.CHA,  # contested by the target's deception
    # Social pressure resistance scales with the target's poise (CHA).
    Skill.PERSUASION: Ability.CHA,
    Skill.INTIMIDATION: Ability.CHA,
    # Animal Handling: the animal pushes back with WIS (its alertness).
    Skill.ANIMAL_HANDLING: Ability.WIS,
}

# Disposition bias for social skills. A friendly NPC capitulates more
# easily; a hostile one digs in. Applied AFTER the base 10 + ability mod
# computation, only for the social skills (Persuasion, Intimidation,
# Deception). Stealth / Sleight of Hand are physical perception, so
# disposition does not change the alertness floor.
_DISPOSITION_BIAS: dict[str, int] = {
    "allied":     -3,
    "friendly":   -2,
    "neutral":     0,
    "unfriendly": +2,
    "hostile":    +4,
}

_DISPOSITION_AFFECTED_SKILLS: frozenset[Skill] = frozenset({
    Skill.PERSUASION,
    Skill.INTIMIDATION,
    Skill.DECEPTION,
})


# ---------------------------------------------------------------------------
# Keyword tables — bare keywords (single skill match)
# ---------------------------------------------------------------------------

# Order in the dict does not matter; matching is by longest-keyword-wins.
# All keywords are lowercased and accent-stripped before matching.
_KEYWORDS: dict[Skill, tuple[str, ...]] = {
    # DEX: nimble fingers, theft, palming
    Skill.SLEIGHT_OF_HAND: (
        "vole", "voler", "derobe", "derober", "subtilise", "subtiliser",
        "pickpocket", "pickpockete", "chaparde", "chaparder",
        "steal", "stole", "stolen", "filch", "swipe", "palm",
    ),
    # STR: brute physical effort. Includes the noun-form "saut" so that
    # "je tente un saut risqué" still trips the matcher even when the
    # player phrases the action as a noun.
    Skill.ATHLETICS: (
        "saute", "sauter", "saut", "grimpe", "grimper", "escalade", "escalader",
        "nage", "nager", "soulever", "souleve", "pousse",
        "jump", "jumps", "leap", "climb", "swim", "lift", "shove",
    ),
    # CHA: sway hearts/minds with sincerity
    Skill.PERSUASION: (
        "convaincs", "convainc", "convaincre", "persuade", "persuader",
        "supplie", "implore", "negocie", "negocier",
        "convince", "persuade", "plead", "negotiate",
    ),
    # CHA: sway hearts/minds with fear
    Skill.INTIMIDATION: (
        "menace", "menacer", "intimide", "intimider", "terrifie", "terrifier",
        "threaten", "threatens", "intimidate", "scare", "terrify",
    ),
    # CHA: deceive
    Skill.DECEPTION: (
        "ment", "mens", "mentir", "bluffe", "bluffer", "trompe", "tromper",
        "ruse",
        "lie", "lies", "lying", "bluff", "deceive", "trick",
    ),
    # DEX: hide / move unseen
    Skill.STEALTH: (
        "cache", "cacher", "faufile", "faufiler", "furtivement",
        "discretement", "tapis", "se tapir",
        "hide", "hides", "sneak", "sneaks", "stealthily", "creep",
    ),
    # WIS: notice with senses
    Skill.PERCEPTION: (
        "ecoute", "ecouter", "observe", "observer", "remarque", "remarquer",
        "scrute", "scruter", "guette",
        "listen", "listens", "scan", "scans", "watch", "spot", "notice",
    ),
    # INT: methodical search / deduction
    Skill.INVESTIGATION: (
        "examine", "examiner", "inspecte", "inspecter", "fouille", "fouiller",
        "analyse", "analyser", "deduis",
        "examine", "examines", "inspect", "investigate", "deduce",
    ),
    # WIS: read intentions / sincerity
    Skill.INSIGHT: (
        "discerne", "discerner", "intentions", "lire entre les lignes",
        "sincerite",
        "sense motive", "true motive", "read between the lines",
    ),
    # CHA: entertain
    Skill.PERFORMANCE: (
        "joue de", "joue la", "chante", "chanter", "danse", "danser",
        "recite", "reciter", "spectacle",
        "perform", "performs", "sing", "sings", "play music", "dance",
    ),
    # WIS: heal injuries / diagnose
    Skill.MEDICINE: (
        "soigne", "soigner", "panse", "panser", "guerit", "guerir",
        "tend to", "tends to", "treat wound", "first aid", "heal",
    ),
    # WIS: wilderness / tracking
    Skill.SURVIVAL: (
        "piste", "pister", "traque", "traquer", "suit la trace",
        "track", "tracks", "tracking", "forage",
    ),
    # WIS: handle non-hostile beasts.
    # Bare verb-only fallbacks; combined verb+animal pairs are detected
    # in :data:`_COMPOUND_PATTERNS` first (they win over bare keywords).
    Skill.ANIMAL_HANDLING: (
        "dompte", "dompter",
        "tame",
    ),
    # INT: arcane lore
    Skill.ARCANA: (
        "sort runique", "rune magique", "magie arcanique",
        "arcane lore", "arcane symbol", "magical glyph",
    ),
    # INT: historical lore
    Skill.HISTORY: (
        "histoire de", "historique", "annales",
        "history of", "historical", "chronicle",
    ),
    # INT: religious lore
    Skill.RELIGION: (
        "symbole religieux", "rite religieux", "divinite",
        "religious symbol", "religious rite",
    ),
    # INT: natural lore (plants, weather, terrain)
    Skill.NATURE: (
        "identifie cette plante", "identifie l'animal",
        "identify this plant", "identify the herb",
    ),
}


# Qualified keywords trump bare ones. Probed FIRST.
# Useful when a bare verb is ambiguous: "saut" alone → ATHLETICS, but
# "saut acrobatique" → ACROBATICS.
_QUALIFIED_KEYWORDS: dict[Skill, tuple[str, ...]] = {
    Skill.ACROBATICS: (
        "saut acrobatique", "vrille", "salto", "equilibre", "se rattraper",
        "amortir la chute",
        "backflip", "tumble", "balance on", "tightrope", "stay balanced",
    ),
}


# ---------------------------------------------------------------------------
# Compound patterns — (Skill, verb tokens, object tokens)
# ---------------------------------------------------------------------------
#
# A compound pattern fires when the normalized text contains AT LEAST one
# verb token AND one object token from the same row (in any order, with any
# words in between). Probed FIRST — beats both qualified and bare matchers.
# Useful when a bare verb has many valid objects ("calme" + horse / dog / …).

_COMPOUND_PATTERNS: tuple[tuple[Skill, tuple[str, ...], tuple[str, ...]], ...] = (
    # ANIMAL_HANDLING: calm verb + animal noun anywhere in the text.
    (
        Skill.ANIMAL_HANDLING,
        (
            "calme", "calmer", "apaise", "apaiser", "rassure", "rassurer",
            "calm", "calms", "soothe", "soothes", "pet", "pets",
        ),
        (
            "cheval", "chien", "bete", "animal", "monture", "destrier",
            "horse", "dog", "beast", "animal", "mount", "steed", "hound",
        ),
    ),
    # SLEIGHT_OF_HAND: theft verb + target noun. Wins over a co-occurring
    # "discretement" / "furtivement" (which would otherwise pull the match
    # to STEALTH on longest-keyword tie-break) — when a player both
    # *steals* and *sneaks*, the explicit action verb defines the contested
    # check. Stealth applies when sneaking without a theft target.
    (
        Skill.SLEIGHT_OF_HAND,
        (
            "vole", "voler", "derobe", "derober", "subtilise", "subtiliser",
            "pickpocket", "pickpockete", "chaparde", "chaparder",
            "steal", "steals", "stole", "stolen", "filch", "swipe",
        ),
        (
            "bourse", "or", "argent", "piece", "pieces", "monnaie", "cle",
            "marchand", "garde", "victime", "cible", "poche", "sac",
            "purse", "coin", "coins", "money", "key", "merchant", "guard",
            "victim", "target", "pocket", "wallet",
        ),
    ),
)


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def _strip_accents(text: str) -> str:
    """Remove accents/diacritics to make matching robust to "élève" / "eleve"."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _normalize(text: str) -> str:
    """Lowercase + accent-strip + collapse whitespace."""
    return re.sub(r"\s+", " ", _strip_accents(text or "").lower()).strip()


def _whole_word_search(text_norm: str, keyword_norm: str) -> bool:
    """Find ``keyword_norm`` as a whole word/phrase inside ``text_norm``.

    Single-token keywords use ``\\b`` boundaries so "vole" does not match
    "envoler". Multi-token keywords already carry their own context, so
    we use plain substring search for them.
    """
    if " " in keyword_norm or "'" in keyword_norm:
        return keyword_norm in text_norm
    pattern = rf"\b{re.escape(keyword_norm)}\b"
    return re.search(pattern, text_norm) is not None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _all_hits(
    text_norm: str, table: dict[Skill, tuple[str, ...]],
) -> list[tuple[Skill, str]]:
    """Return every ``(skill, keyword)`` pair whose keyword appears in ``text_norm``.

    Multiple keywords / multiple skills can match the same input — the
    caller picks the winner.
    """
    hits: list[tuple[Skill, str]] = []
    for skill, keywords in table.items():
        for kw in keywords:
            kw_norm = _normalize(kw)
            if _whole_word_search(text_norm, kw_norm):
                hits.append((skill, kw_norm))
    return hits


def infer_skill_from_text(
    text: str,
    *,
    extra_texts: Iterable[str] = (),
) -> Skill | None:
    """Pick the most likely D&D 5e :class:`Skill` for a free-form action.

    Args:
        text: The player's action description (FR or EN). Empty / whitespace
            inputs return ``None``.
        extra_texts: Additional snippets to search alongside ``text``
            (e.g. ``raw_input`` plus ``improvise_description``). Concatenated
            with a space before matching.

    Returns:
        The matched :class:`Skill`, or ``None`` if no recognised verb appears.

    Match priority:
        1. Qualified keywords (e.g. "saut acrobatique" → ACROBATICS) win
           over bare keywords with the same root.
        2. Among bare keywords, the longest match wins (so "pickpocket"
           beats "vole" in "I pickpocket the merchant").
        3. Ties break by enum declaration order — deterministic but rarely
           reached in practice.
    """
    pieces = [t for t in (text, *extra_texts) if t]
    text_norm = _normalize(" ".join(pieces))
    if not text_norm:
        return None

    # 1. Compound patterns (verb + object) — beat everything else.
    for skill, verbs, objects in _COMPOUND_PATTERNS:
        if any(_whole_word_search(text_norm, _normalize(v)) for v in verbs) and any(
            _whole_word_search(text_norm, _normalize(o)) for o in objects
        ):
            return skill

    # 2. Qualified keywords — they beat any bare match.
    qualified_hits = _all_hits(text_norm, _QUALIFIED_KEYWORDS)
    if qualified_hits:
        # Longest match wins (rare tie-break).
        qualified_hits.sort(key=lambda pair: len(pair[1]), reverse=True)
        return qualified_hits[0][0]

    # 3. Bare keywords — longest match wins.
    bare_hits = _all_hits(text_norm, _KEYWORDS)
    if not bare_hits:
        return None
    bare_hits.sort(key=lambda pair: len(pair[1]), reverse=True)
    return bare_hits[0][0]


# ---------------------------------------------------------------------------
# DC computation
# ---------------------------------------------------------------------------


def compute_contest_dc(npc: "NPC", skill: Skill) -> int | None:
    """Return a contested DC for ``skill`` against ``npc``.

    Formula: ``10 + npc_ability_mod + disposition_bias_if_applicable``.

    The defensive ability is taken from :data:`_DEFENSIVE_ABILITY`:

    - Stealth, Sleight of Hand, Deception → NPC's WIS (alertness/insight)
    - Persuasion, Intimidation → NPC's CHA (poise / social resistance)
    - Animal Handling → NPC's WIS

    Disposition bias only applies to social skills (Persuasion,
    Intimidation, Deception): friendly NPCs are more permissive, hostile
    NPCs more guarded.

    Returns ``None`` when the skill is not "contestable" against an NPC
    (Athletics, Acrobatics, Medicine, Survival, the lore skills, …) —
    these stay environmental and the caller should fall back to the
    static-DC path.
    """
    ability = _DEFENSIVE_ABILITY.get(skill)
    if ability is None:
        return None
    mod = compute_modifier(npc.ability_scores.get(ability))
    dc = 10 + mod
    if skill in _DISPOSITION_AFFECTED_SKILLS:
        dc += _DISPOSITION_BIAS.get(npc.disposition.value, 0)
    return max(dc, VERY_EASY_DC)


def compute_skill_check_dc(
    *,
    text: str,
    skill: Skill,
    target_npc: "NPC | None" = None,
) -> int:
    """Top-level DC composer for an improvised skill check.

    The DC derives from ENGINE CONTEXT ONLY: :data:`DEFAULT_SKILL_DC`
    when no NPC contest applies, otherwise :func:`compute_contest_dc`
    (NPC ability score + disposition) for an NPC-contested skill.

    ``text`` is accepted for call-site compatibility but deliberately
    never shapes the DC. An earlier version lowered the DC when the
    player wrote "facile" / "simple" — a free, self-served difficulty
    buff (anti-cheat audit, low finding). Player wording must never
    move a mechanical outcome.

    The result is clamped to ``[VERY_EASY_DC, VERY_HARD_DC + 2]`` as a
    safety net for future DC sources.
    """
    del text  # player wording must never influence the DC
    base = compute_contest_dc(target_npc, skill) if target_npc is not None else None
    if base is None:
        base = DEFAULT_SKILL_DC
    return max(VERY_EASY_DC, min(base, VERY_HARD_DC + 2))
