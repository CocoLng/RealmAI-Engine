"""Narrator — converts mechanical action results into immersive narrative."""

import logging
import re
from pathlib import Path

from pydantic import ValidationError

from ai.client import LLMParseError, OllamaClient, OllamaUnavailableError
from ai.language import language_instruction
from ai.models import DirectorNote, NarrativeResult, Tone
from ai.prompt_safety import PLAYER_DATA_INSTRUCTION, delimited_player_block

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "system_narrator.txt").read_text()

_DEFAULT_TONE: Tone = "dramatic"

_TONE_ALIASES: dict[str, Tone] = {
    # Canonical values
    "dramatic": "dramatic",
    "tense": "tense",
    "humorous": "humorous",
    "somber": "somber",
    # French — the 9b writing French routinely localizes the enum
    "dramatique": "dramatic",
    "tendu": "tense",
    "tendue": "tense",
    "humoristique": "humorous",
    "drôle": "humorous",
    "sombre": "somber",
    # Spanish / Portuguese
    "dramático": "dramatic",
    "dramatico": "dramatic",
    "tenso": "tense",
    "tensa": "tense",
    "humorístico": "humorous",
    "humoristico": "humorous",
    "sombrío": "somber",
    "sombrio": "somber",
    # German
    "dramatisch": "dramatic",
    "angespannt": "tense",
    "humorvoll": "humorous",
    "düster": "somber",
    "duster": "somber",
}


def _normalize_tone(raw: object) -> Tone:
    """Map an LLM-emitted tone to a canonical value. Never raises."""
    if isinstance(raw, str):
        canonical = _TONE_ALIASES.get(raw.strip().lower())
        if canonical is not None:
            return canonical
        logger.warning("Narrator emitted unknown tone %r — defaulting to %s", raw, _DEFAULT_TONE)
    return _DEFAULT_TONE


_PLACEHOLDER_RE = re.compile(r"[\[{][^\]}]{0,40}[\]}]")
"""Bracketed tokens like ``[nom]`` or ``{personnage}`` — small models parrot
them verbatim from prompt examples (observed in prod, H13)."""

_DAMAGE_KEYWORD_RE = re.compile(
    r"\b(dégâts?|dommages?|damage|blessures?|santé|vies?|hp|pv"
    r"|hit\s+points?|health|wounds?)\b",
    re.IGNORECASE,
)
"""Words signalling that a nearby number is a damage/HP claim."""

_DIGIT_RE = re.compile(r"\b(\d{1,3})\b")

_NUMBER_WORDS: dict[str, int] = {
    # French 2-20 ("un/une" excluded — articles would flood false positives)
    "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6, "sept": 7,
    "huit": 8, "neuf": 9, "dix": 10, "onze": 11, "douze": 12, "treize": 13,
    "quatorze": 14, "quinze": 15, "seize": 16, "dix-sept": 17,
    "dix-huit": 18, "dix-neuf": 19, "vingt": 20,
    # English 2-20 ("one/a" excluded for the same reason)
    "two": 2, "three": 3, "four": 4, "five": 5, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
}
# Longest alternatives first so "dix-sept" wins over "dix".
_NUMBER_WORD_RE = re.compile(
    r"\b(" + "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

_DAMAGE_WINDOW = 40
"""Chars scanned on each side of a damage keyword for numeric claims."""


def _numbers_in(text: str) -> set[int]:
    """Every number (digits or fr/en words) appearing anywhere in ``text``."""
    found = {int(m.group(1)) for m in _DIGIT_RE.finditer(text)}
    found.update(
        _NUMBER_WORDS[m.group(1).lower()] for m in _NUMBER_WORD_RE.finditer(text)
    )
    return found


def _claimed_damage_numbers(narrative: str) -> set[int]:
    """Numbers appearing within ``_DAMAGE_WINDOW`` chars of a damage keyword."""
    claims: set[int] = set()
    for kw in _DAMAGE_KEYWORD_RE.finditer(narrative):
        window = narrative[
            max(0, kw.start() - _DAMAGE_WINDOW): kw.end() + _DAMAGE_WINDOW
        ]
        claims |= _numbers_in(window)
    return claims


def _invented_damage_claim(narrative: str, allowed_text: str) -> str | None:
    """Detect damage/HP numbers the engine never produced (H12).

    Observed in prod: the 9b narrated an enemy riposte dealing « douze de
    votre santé » while the engine had resolved a MISS. Any number claimed
    near a damage keyword must appear somewhere in the engine-provided
    text (action result, outcome facts, or context). Conservative by
    design: numbers without a damage keyword nearby are ignored.
    """
    claims = _claimed_damage_numbers(narrative)
    if not claims:
        return None
    invented = claims - _numbers_in(allowed_text)
    if not invented:
        return None
    return f"damage numbers {sorted(invented)} absent from engine results"


def _quality_issue(narrative: str, allowed_text: str = "") -> str | None:
    """Return why a narrative is unfit for the player, or ``None`` if fine."""
    if len(narrative) < 50:
        return f"too short ({len(narrative)} chars)"
    match = _PLACEHOLDER_RE.search(narrative)
    if match is not None:
        return f"placeholder {match.group(0)!r}"
    claim = _invented_damage_claim(narrative, allowed_text)
    if claim is not None:
        return claim
    return None


class Narrator:
    """Narrates action results as immersive text using the 9B model.

    Receives the assembled context prompt and a mechanical summary of what
    happened (ActionResult). Describes it — never decides mechanics.
    """

    MODEL = "qwen3.5:9b"

    NUM_PREDICT = 1024
    """Generation cap — 2-4 sentences plus JSON meta fields never need
    more; unbounded output (-1) used to overflow the Discord embed (M7)."""

    def __init__(self, client: OllamaClient) -> None:
        self._client = client

    def narrate(
        self,
        action_result_text: str,
        context_prompt: str,
        language: str = "fr",
        player_intent: str = "",
        outcome_facts: str = "",
        has_npc_dialogue: bool = False,
        director_note: DirectorNote | None = None,
    ) -> NarrativeResult:
        """Generate an immersive narrative description of a resolved action.

        Three-tier fallback chain — never throws:
          1. Primary call with full prompt
          2. Retry with a simplified prompt (only action + context)
          3. Hardcoded template fallback (always succeeds)
        """
        logger.info("NARRATE input=%r intent=%r", action_result_text[:100], player_intent[:100])

        # Engine-provided text — the only legitimate source of numeric
        # damage/HP claims in the narrative (H12 guard).
        allowed_text = " ".join((action_result_text, outcome_facts, context_prompt))

        # --- Tier 1: primary call ---
        try:
            result = self._call_llm(
                action_result_text=action_result_text,
                context_prompt=context_prompt,
                language=language,
                player_intent=player_intent,
                outcome_facts=outcome_facts,
                has_npc_dialogue=has_npc_dialogue,
                simplified=False,
                director_note=director_note,
            )
            issue = _quality_issue(result.narrative, allowed_text)
            if issue is None:
                return result
            logger.warning(
                "Narrator primary rejected (%s), retrying simplified", issue,
            )
        except (LLMParseError, OllamaUnavailableError, ValidationError, TypeError) as exc:
            logger.warning("Narrator primary call failed (%s), retrying simplified", exc)

        # --- Tier 2: simplified retry ---
        try:
            result = self._call_llm(
                action_result_text=action_result_text,
                context_prompt=context_prompt,
                language=language,
                player_intent="",
                outcome_facts="",
                has_npc_dialogue=False,
                simplified=True,
                director_note=director_note,
            )
            issue = _quality_issue(result.narrative, allowed_text)
            if issue is None:
                return result
            logger.error(
                "Narrator simplified retry rejected (%s), using template", issue,
            )
        except (LLMParseError, OllamaUnavailableError, ValidationError, TypeError) as exc:
            logger.error("Narrator simplified retry failed (%s), using template", exc)

        # --- Tier 3: template fallback (never raises) ---
        return self._template_fallback(action_result_text, outcome_facts)

    def _call_llm(
        self,
        *,
        action_result_text: str,
        context_prompt: str,
        language: str,
        player_intent: str,
        outcome_facts: str,
        has_npc_dialogue: bool,
        simplified: bool,
        director_note: DirectorNote | None = None,
    ) -> NarrativeResult:
        """Issue one LLM call and parse the response. May raise.

        ``simplified=True`` strips optional sections from the user message —
        useful when the primary call failed and we suspect the prompt may
        have confused the model.
        """
        sections: list[str] = []
        if director_note is not None and (
            director_note.current_objective
            or director_note.current_beat_atmosphere
            or director_note.required_mentions
            or director_note.forbidden_topics
        ):
            sections.append(self._format_direction_block(director_note))
        sections.extend([context_prompt, f"## What happened\n{action_result_text}"])
        if not simplified:
            if player_intent:
                sections.append(
                    f"## Player framing\n{delimited_player_block(player_intent)}"
                )
            if outcome_facts:
                sections.append(f"## State changes\n{outcome_facts}")
            if has_npc_dialogue:
                sections.append(
                    "## Important\n"
                    "NPC dialogue will be displayed separately. "
                    "Describe ONLY atmosphere and body language around the "
                    "speech. Do NOT write any spoken words."
                )
        user_content = "\n\n".join(sections)
        system_prompt = (
            language_instruction(language)
            + _SYSTEM_PROMPT
            + "\n\n"
            + PLAYER_DATA_INSTRUCTION
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        data = self._client.chat_json(
            self.MODEL, messages, temperature=0.8, num_predict=self.NUM_PREDICT,
        )
        if not isinstance(data, dict):
            raise LLMParseError(
                f"narrator payload is {type(data).__name__}, expected JSON object",
                raw_response=str(data),
                model=self.MODEL,
                messages=messages,
            )
        result = NarrativeResult(
            narrative=str(data.get("narrative", "")),
            tone=_normalize_tone(data.get("tone")),
            scene_goal_touched=bool(data.get("scene_goal_touched", False)),
            beat_advanced=bool(data.get("beat_advanced", False)),
            npcs_mentioned=list(data.get("npcs_mentioned") or []),
            locked_facts_used=list(data.get("locked_facts_used") or []),
        )
        logger.info("NARRATE tone=%s output=%r", result.tone, result.narrative[:200])
        logger.debug("NARRATE full_output=%s", result.narrative)
        return result

    @staticmethod
    def _format_direction_block(note: DirectorNote) -> str:
        """Format the Story Director's direction fields into a prompt block."""
        lines = ["[STORY DIRECTION]"]
        if note.current_objective:
            lines.append(f"Current objective: {note.current_objective}")
        if note.current_beat_atmosphere:
            lines.append(f"Beat atmosphere: {note.current_beat_atmosphere}")
        if note.required_mentions:
            lines.append("Re-mention if natural: " + ", ".join(note.required_mentions))
        if note.forbidden_topics:
            lines.append("Do NOT re-reveal: " + ", ".join(note.forbidden_topics))
        return "\n".join(lines)

    _TEMPLATES: dict[str, list[str]] = {
        "attack": [
            "Le combat se poursuit dans la confusion. {action}.",
            "Les coups pleuvent autour de toi. {action}.",
            "L'affrontement reprend de plus belle. {action}.",
        ],
        "move": [
            "Le décor change autour de toi. {action}.",
            "Tes pas te portent ailleurs. {action}.",
        ],
        "talk": [
            "Les mots échangés résonnent encore dans l'air. {action}.",
            "La conversation suit son cours. {action}.",
        ],
        "search": [
            "Tu fouilles avec attention les environs. {action}.",
            "Tes mains parcourent l'endroit. {action}.",
        ],
        "default": [
            "Le MJ rassemble ses idées un instant. {action}.",
            "L'instant se prolonge avant la suite. {action}.",
        ],
    }

    def _template_fallback(
        self, action_result_text: str, outcome_facts: str
    ) -> NarrativeResult:
        """Return a hardcoded short narrative. Never raises.

        Used as the last-resort fallback when both the primary LLM call and
        the simplified retry have failed. The narrative is intentionally
        short and in-universe — its job is to keep the session alive, not to
        be invisible.
        """
        category = self._pick_template_category(action_result_text)
        variants = self._TEMPLATES.get(category, self._TEMPLATES["default"])
        # Deterministic-ish pick: use the length of action_result_text mod len(variants).
        # Avoids importing random for a 3-element list.
        template = variants[len(action_result_text) % len(variants)]
        narrative = template.format(action=action_result_text.rstrip("."))
        if outcome_facts:
            narrative = f"{narrative} {outcome_facts}"
        return NarrativeResult(narrative=narrative, tone="dramatic")

    @staticmethod
    def _pick_template_category(action_result_text: str) -> str:
        """Map the mechanical action verb to a template category."""
        lower = action_result_text.lower()
        if "attack" in lower or "attaque" in lower or "damage" in lower or "dégât" in lower:
            return "attack"
        if "move" in lower or "déplace" in lower or "go to" in lower:
            return "move"
        if "talk" in lower or "parle" in lower or "dialogue" in lower:
            return "talk"
        if "search" in lower or "fouille" in lower or "look" in lower:
            return "search"
        return "default"
