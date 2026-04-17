"""Arc Generator --- creates campaign story arcs using the LLM."""

import logging
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ai.client import OllamaClient
from ai.language import language_instruction
from engine.arc_recipes import ArcRecipe
from engine.npc_library import get_archetype
from engine.npc_stat_block import NPCStatBlock
from world.story_arc import StoryArc

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "system_arc_generator.txt").read_text()


class ArcGenerator:
    """Generates a complete story arc for a campaign.

    Output is a fully-formed StoryArc (world/story_arc.py).
    The caller is responsible for persisting the arc.

    When an ArcRecipe is provided, uses a single LLM call with the recipe
    as structured context.  Falls back to a simple prompt when no recipe
    is given (legacy path).
    """

    MODEL = "qwen3.5:9b"

    def __init__(self, client: OllamaClient) -> None:
        self._client = client

    def generate(
        self,
        theme: str,
        player_count: int,
        language: str = "fr",
        recipe: ArcRecipe | None = None,
    ) -> StoryArc:
        """Generate a new story arc for the campaign.

        Args:
            theme: The campaign theme (e.g. "dark fantasy", "pirate adventure").
            player_count: Number of players in the campaign.
            language: ISO 639-1 language code for narrative output.
            recipe: Optional ArcRecipe providing structural scaffolding
                (archetype, beat sequence, complications, tone, etc.).
                When provided, the LLM fills in creative narrative content
                guided by the recipe constraints.

        Returns:
            A StoryArc ready to be saved.
        """
        lang_prefix = language_instruction(language)
        system_prompt = lang_prefix + _SYSTEM_PROMPT

        if recipe:
            user_content = self._build_user_message_with_recipe(theme, player_count, recipe)
        else:
            user_content = self._build_user_message(theme, player_count)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        data = self._client.chat_json(self.MODEL, messages, temperature=0.9, think=False)

        # Repair known LLM output quirks before validation.
        self._sanitize_arc_data(data)

        # --- Villain stat block parsing with generic_boss fallback (task 42) ---
        # Validate the stat block separately so we can fallback cleanly when the
        # LLM emits an invalid or missing payload, without losing the rest of
        # the arc.
        data["villain_stat_block"] = self._resolve_villain_stat_block(data).model_dump()

        arc = StoryArc.model_validate(data)
        logger.info(
            "ARC theme=%r beats=%d villain=%r stat_block=%s",
            arc.theme, len(arc.beats), arc.villain_name,
            arc.villain_stat_block.archetype if arc.villain_stat_block else "none",
        )
        return arc

    # Synonyms the LLM occasionally emits instead of the exact engine enum values.
    _DAMAGE_TYPE_SYNONYMS: dict[str, str] = {
        "Electricity": "Lightning",
        "Electric": "Lightning",
        "Holy": "Radiant",
        "Unholy": "Necrotic",
        "Shadow": "Necrotic",
        "Acid": "Poison",
    }

    _TARGET_SCOPE_SYNONYMS: dict[str, str] = {
        "all_enemies_in_zone": "all_enemies",
        "all_allies": "all_allies_in_zone",
        "enemies": "all_enemies",
    }

    @staticmethod
    def _sanitize_arc_data(data: dict[str, Any]) -> None:
        """Repair known LLM output quirks in-place before Pydantic validation.

        Handles:
        - state_flags values that are strings instead of booleans.
        - damage_type synonym normalization (e.g. "Electricity" → "Lightning").
        - target_scope invalid hybrids (e.g. "all_enemies_in_zone" → "all_enemies").
        """
        for beat in data.get("beats") or []:
            on_complete = beat.get("on_complete")
            if not isinstance(on_complete, dict):
                continue
            flags = on_complete.get("state_flags")
            if not isinstance(flags, dict):
                continue
            on_complete["state_flags"] = {
                k: (v if isinstance(v, bool) else bool(v))
                for k, v in flags.items()
            }

        stat = data.get("villain_stat_block")
        if not isinstance(stat, dict):
            return

        def _fix_effect(effect: Any) -> None:
            if not isinstance(effect, dict):
                return
            dt = effect.get("damage_type")
            if isinstance(dt, str) and dt in ArcGenerator._DAMAGE_TYPE_SYNONYMS:
                effect["damage_type"] = ArcGenerator._DAMAGE_TYPE_SYNONYMS[dt]
            ts = effect.get("target_scope")
            if isinstance(ts, str) and ts in ArcGenerator._TARGET_SCOPE_SYNONYMS:
                effect["target_scope"] = ArcGenerator._TARGET_SCOPE_SYNONYMS[ts]

        for attack in stat.get("attacks") or []:
            if isinstance(attack, dict):
                dt = attack.get("damage_type")
                if isinstance(dt, str) and dt in ArcGenerator._DAMAGE_TYPE_SYNONYMS:
                    attack["damage_type"] = ArcGenerator._DAMAGE_TYPE_SYNONYMS[dt]

        for ability in stat.get("signature_abilities") or []:
            if isinstance(ability, dict):
                for effect in ability.get("effects") or []:
                    _fix_effect(effect)

        for action in stat.get("legendary_actions") or []:
            if isinstance(action, dict):
                for effect in action.get("effects") or []:
                    _fix_effect(effect)

    @staticmethod
    def _resolve_villain_stat_block(data: dict[str, Any]) -> NPCStatBlock:
        """Validate ``data['villain_stat_block']`` or fallback on generic_boss.

        Strategy:
          1. Try ``NPCStatBlock.model_validate`` on the raw payload.
          2. On any :class:`ValidationError` (or missing payload), log and
             return a fresh ``get_archetype('generic_boss')`` instance whose
             ``archetype`` field is tagged with the villain name so the
             hydration layer can trace the fallback.
        """
        raw_stat_block = data.get("villain_stat_block")
        villain_name = str(data.get("villain_name") or "unknown")

        if raw_stat_block is not None:
            try:
                return NPCStatBlock.model_validate(raw_stat_block)
            except ValidationError as exc:
                logger.warning(
                    "Invalid villain_stat_block from arc generator for %r, "
                    "falling back to generic_boss. Error: %s",
                    villain_name, exc,
                )

        fallback = get_archetype("generic_boss")
        fallback.archetype = f"generic_boss:{villain_name}"
        return fallback

    def _build_user_message(self, theme: str, player_count: int) -> str:
        """Build the user message for the LLM prompt (legacy, no recipe).

        Args:
            theme: The campaign theme.
            player_count: Number of players.

        Returns:
            Formatted user message string.
        """
        return (
            f"Campaign theme: {theme}\n"
            f"Number of players: {player_count}\n\n"
            f"Generate a compelling story arc with 10-15 story beats."
        )

    @staticmethod
    def _build_user_message_with_recipe(
        theme: str,
        player_count: int,
        recipe: ArcRecipe,
    ) -> str:
        """Build the user message incorporating an ArcRecipe.

        The recipe provides structural scaffolding (archetype, beat types,
        complications, tone) so the LLM focuses on creative narrative content.

        Args:
            theme: The campaign theme.
            player_count: Number of players.
            recipe: The arc recipe to use as scaffolding.

        Returns:
            Formatted user message string with recipe context.
        """
        lines: list[str] = [
            f"Campaign theme: {theme}",
            f"Number of players: {player_count}",
            "",
            "## Narrative Recipe",
            f"Archetype: {recipe.archetype.value}",
            f"Tone: {recipe.tone.value}",
            f"Complications: {', '.join(recipe.complications)}",
            f"Villain archetype: {recipe.villain_archetype.value if recipe.villain_archetype else 'au choix'}",
            "",
            f"## Beat Sequence ({recipe.num_beats} beats)",
        ]

        for i, (beat, subtype) in enumerate(zip(recipe.beat_sequence, recipe.beat_subtypes, strict=True)):
            marker = " [TWIST]" if i == recipe.twist_position else ""
            lines.append(f"Beat {i + 1}: {beat.value} ({subtype}){marker}")

        lines.append("")
        lines.append("Fill each beat with creative narrative content. Generate the full story arc.")

        return "\n".join(lines)
