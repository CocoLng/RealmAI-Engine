"""Arc Generator --- creates campaign story arcs using the LLM."""

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from ai.client import OllamaClient
from ai.language import language_instruction
from ai.objective_recipes import _slugify, scaffold_objectives
from engine.arc_recipes import ArcRecipe
from engine.npc_library import get_archetype
from engine.npc_stat_block import NPCStatBlock
from world.story_arc import (
    AdvanceRule,
    GateKind,
    ObjectiveKind,
    StoryArc,
)

if TYPE_CHECKING:
    from memory.indexer import SemanticIndexer

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

    def __init__(
        self,
        client: OllamaClient,
        indexer: "SemanticIndexer | None" = None,
    ) -> None:
        self._client = client
        self._indexer = indexer

    def generate(
        self,
        theme: str,
        player_count: int,
        language: str = "fr",
        recipe: ArcRecipe | None = None,
        campaign_id: str = "",
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
            campaign_id: Campaign identifier forwarded to the SemanticIndexer
                when one is provided.  Defaults to ``""`` so existing callers
                that omit it continue to work unchanged.

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

        if self._indexer is not None:
            for beat in arc.beats:
                self._indexer.index_beat(campaign_id, beat)
            if arc.villain_name and arc.villain_stat_block is not None:
                from ai.models import NPCSheet
                villain_sheet = NPCSheet(
                    personality=getattr(arc.villain_stat_block, "personality", None) or "Antagonist",
                    description=(
                        f"Villain: {arc.villain_name}. "
                        f"Archetype: {arc.villain_stat_block.archetype}."
                    ),
                    secrets=["[Stat block hidden — for the engine.]"],
                    knowledge=[f"Knows the campaign theme: {arc.theme}"],
                )
                self._indexer.index_npc(campaign_id, arc.villain_name, villain_sheet)
            self._indexer.index_lore(
                campaign_id,
                content=f"Campaign theme: {arc.theme}",
                metadata={"source": "arc_generator", "category": "theme"},
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

    _NULL_STRINGS: frozenset[str] = frozenset({"null", "none", ""})

    _OPTIONAL_NULLABLE_EFFECT_FIELDS: tuple[str, ...] = (
        "dice", "damage_type", "condition_name",
        "condition_duration_rounds", "save_ability", "save_dc",
    )

    _OPTIONAL_NULLABLE_ATTACK_FIELDS: tuple[str, ...] = ("range_value",)

    @staticmethod
    def _coerce_null_strings(d: dict[str, Any], keys: tuple[str, ...]) -> None:
        """Coerce string 'null'/'None'/'' to real None for the listed optional fields.

        The LLM occasionally emits the literal string "null" instead of JSON null
        for fields it wants to leave blank. Pydantic then rejects the payload with
        an enum / Literal validation error. This helper normalizes the quirk in
        place before validation.

        Only touches the fields listed in ``keys`` so required fields are never
        clobbered.
        """
        for k in keys:
            v = d.get(k)
            if isinstance(v, str) and v.strip().lower() in ArcGenerator._NULL_STRINGS:
                d[k] = None

    @staticmethod
    def _sanitize_arc_data(data: dict[str, Any]) -> None:
        """Repair known LLM output quirks in-place before Pydantic validation.

        Handles:
        - state_flags values that are strings instead of booleans.
        - native ``objectives[]`` arrays per beat: missing kinds, duplicate ids,
          gate type mismatches, missing ``advance_rule`` etc. — and scaffolds a
          deterministic recipe-based fallback when the list is empty.
        - damage_type synonym normalization (e.g. "Electricity" → "Lightning").
        - target_scope invalid hybrids (e.g. "all_enemies_in_zone" → "all_enemies").
        - string "null"/"None"/"" coerced to real None on optional effect/attack
          fields (damage_type, save_ability, etc.).
        """
        villain_name = str(data.get("villain_name") or "")
        for beat in data.get("beats") or []:
            on_complete = beat.get("on_complete")
            if isinstance(on_complete, dict):
                flags = on_complete.get("state_flags")
                if isinstance(flags, dict):
                    on_complete["state_flags"] = {
                        k: (v if isinstance(v, bool) else bool(v))
                        for k, v in flags.items()
                    }
            ArcGenerator._sanitize_beat_objectives(beat, villain_name=villain_name)

        stat = data.get("villain_stat_block")
        if not isinstance(stat, dict):
            return

        def _fix_effect(effect: Any) -> None:
            if not isinstance(effect, dict):
                return
            ArcGenerator._coerce_null_strings(
                effect, ArcGenerator._OPTIONAL_NULLABLE_EFFECT_FIELDS,
            )
            dt = effect.get("damage_type")
            if isinstance(dt, str) and dt in ArcGenerator._DAMAGE_TYPE_SYNONYMS:
                effect["damage_type"] = ArcGenerator._DAMAGE_TYPE_SYNONYMS[dt]
            ts = effect.get("target_scope")
            if isinstance(ts, str) and ts in ArcGenerator._TARGET_SCOPE_SYNONYMS:
                effect["target_scope"] = ArcGenerator._TARGET_SCOPE_SYNONYMS[ts]

        for attack in stat.get("attacks") or []:
            if isinstance(attack, dict):
                ArcGenerator._coerce_null_strings(
                    attack, ArcGenerator._OPTIONAL_NULLABLE_ATTACK_FIELDS,
                )
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

    # ------------------------------------------------------------------
    # Native objectives sanitization
    # ------------------------------------------------------------------

    _VALID_OBJECTIVE_KINDS: frozenset[str] = frozenset(k.value for k in ObjectiveKind)
    _VALID_GATE_KINDS: frozenset[str] = frozenset(k.value for k in GateKind)
    _VALID_ADVANCE_RULES: frozenset[str] = frozenset(r.value for r in AdvanceRule)

    # Gates that REQUIRE an integer value. Other gates take a string.
    _INT_VALUED_GATES: frozenset[str] = frozenset(
        {GateKind.MIN_REVEALS.value, GateKind.MIN_DISPOSITION.value},
    )

    @staticmethod
    def _sanitize_beat_objectives(beat: dict[str, Any], *, villain_name: str) -> None:
        """Repair / scaffold a single beat's ``objectives`` list in-place.

        Strategy:
          1. If ``objectives`` is missing/empty, scaffold from the recipe
             matching ``encounter_type`` + ``encounter_subtype``.
          2. Otherwise, sanitize the LLM-emitted list: validate each
             objective's kind/gate, drop irreparable entries, ensure ids are
             unique and stable, fill missing fields with sensible defaults.
          3. If sanitization wipes out the list entirely, scaffold from the
             recipe so the beat is still completable.
          4. Default ``advance_rule`` to ``ALL_REQUIRED`` and fill
             ``judge_rubric`` / ``player_visible_hint`` from the recipe when
             the LLM didn't.
        """
        beat_number = int(beat.get("beat_number") or 0) or 1
        encounter_type = str(beat.get("encounter_type") or "social")
        encounter_subtype = beat.get("encounter_subtype")
        if encounter_subtype is not None:
            encounter_subtype = str(encounter_subtype)
        npc_names = [
            str(n) for n in (beat.get("npc_names") or []) if isinstance(n, str)
        ]
        location_hint = str(beat.get("location_hint") or "")
        beat_title = str(beat.get("title") or "")

        raw_objectives = beat.get("objectives")
        objectives_list: list[dict[str, Any]] = []
        if isinstance(raw_objectives, list):
            for item in raw_objectives:
                if isinstance(item, dict):
                    objectives_list.append(item)

        cleaned = ArcGenerator._clean_objective_list(
            objectives_list,
            beat_number=beat_number,
        )

        # Boss beat must end on DEFEAT villain — enforce regardless of LLM output.
        if encounter_type == "boss":
            cleaned = ArcGenerator._ensure_boss_defeat_objective(
                cleaned,
                beat_number=beat_number,
                villain_name=villain_name,
            )

        if not cleaned:
            # Scaffold from recipe — guaranteed valid + calibrated gates.
            scaffolded, advance_rule, threshold, rubric, hint = scaffold_objectives(
                beat_number=beat_number,
                encounter_type=encounter_type,
                encounter_subtype=encounter_subtype,
                npc_names=npc_names,
                villain_name=villain_name,
                location_hint=location_hint,
                beat_title=beat_title,
            )
            beat["objectives"] = [obj.model_dump(mode="json") for obj in scaffolded]
            beat.setdefault("advance_rule", advance_rule.value)
            if threshold is not None:
                beat.setdefault("advance_threshold", threshold)
            beat.setdefault("judge_rubric", rubric)
            beat.setdefault("player_visible_hint", hint)
            return

        beat["objectives"] = cleaned

        # Default advance_rule + threshold based on objective count.
        rule_str = beat.get("advance_rule")
        if not isinstance(rule_str, str) or rule_str not in ArcGenerator._VALID_ADVANCE_RULES:
            beat["advance_rule"] = AdvanceRule.ALL_REQUIRED.value
        if beat.get("advance_rule") == AdvanceRule.M_OF_N.value:
            threshold = beat.get("advance_threshold")
            if not isinstance(threshold, int) or threshold < 1:
                # Sensible default: majority of the objectives.
                beat["advance_threshold"] = max(1, (len(cleaned) + 1) // 2)

        # Backfill rubric/hint from the recipe so /hint and the BeatJudge always
        # have substance. Only fills when the LLM left them empty — never overwrites.
        if not beat.get("judge_rubric") or not beat.get("player_visible_hint"):
            _scaffold_objs, _rule, _threshold, recipe_rubric, recipe_hint = scaffold_objectives(
                beat_number=beat_number,
                encounter_type=encounter_type,
                encounter_subtype=encounter_subtype,
                npc_names=npc_names,
                villain_name=villain_name,
                location_hint=location_hint,
                beat_title=beat_title,
            )
            if not beat.get("judge_rubric"):
                beat["judge_rubric"] = recipe_rubric
            if not beat.get("player_visible_hint"):
                beat["player_visible_hint"] = recipe_hint

    @staticmethod
    def _clean_objective_list(
        objectives: list[dict[str, Any]],
        *,
        beat_number: int,
    ) -> list[dict[str, Any]]:
        """Filter and normalise a raw LLM objective list.

        Drops objectives with unknown ``kind`` (the engine can't match them).
        Coerces ``required`` to bool. Coerces gate values to the correct type.
        Generates stable unique ids when the LLM omits or duplicates them.
        """
        seen_ids: set[str] = set()
        cleaned: list[dict[str, Any]] = []

        for idx, obj in enumerate(objectives, start=1):
            kind = obj.get("kind")
            if not isinstance(kind, str) or kind not in ArcGenerator._VALID_OBJECTIVE_KINDS:
                logger.info(
                    "ARC sanitize dropped objective with invalid kind=%r (beat %d)",
                    kind, beat_number,
                )
                continue

            target = obj.get("target")
            if not isinstance(target, str) or not target.strip():
                logger.info(
                    "ARC sanitize dropped objective with empty target (beat %d, kind=%s)",
                    beat_number, kind,
                )
                continue
            target = target.strip()

            description = obj.get("description")
            if not isinstance(description, str) or not description.strip():
                description = f"{kind} {target}"

            required_raw = obj.get("required", True)
            if isinstance(required_raw, bool):
                required = required_raw
            elif isinstance(required_raw, str):
                required = required_raw.strip().lower() not in ("false", "0", "no", "non")
            else:
                required = bool(required_raw)

            fuzzy_threshold_raw = obj.get("fuzzy_threshold", 0.7)
            try:
                fuzzy_threshold = float(fuzzy_threshold_raw)
                if not 0.0 <= fuzzy_threshold <= 1.0:
                    fuzzy_threshold = 0.7
            except (TypeError, ValueError):
                fuzzy_threshold = 0.7

            gate = ArcGenerator._sanitize_gate(obj.get("gate"), beat_number=beat_number)

            oid = obj.get("id")
            if not isinstance(oid, str) or not oid.strip() or oid in seen_ids:
                oid = ArcGenerator._make_objective_id(
                    beat_number=beat_number,
                    kind=kind,
                    target=target,
                    index=idx,
                )
                # Defensive uniqueness loop — unlikely to fire but cheap.
                base = oid
                suffix = 2
                while oid in seen_ids:
                    oid = f"{base}_{suffix}"
                    suffix += 1
            seen_ids.add(oid)

            entry: dict[str, Any] = {
                "id": oid,
                "kind": kind,
                "target": target,
                "description": description.strip(),
                "required": required,
                "fuzzy_threshold": fuzzy_threshold,
            }
            if gate is not None:
                entry["gate"] = gate
            cleaned.append(entry)

        return cleaned

    @staticmethod
    def _sanitize_gate(
        raw_gate: Any, *, beat_number: int,
    ) -> dict[str, Any] | None:
        """Validate / coerce a gate dict. Returns None if irreparable."""
        if raw_gate is None:
            return None
        if not isinstance(raw_gate, dict):
            return None
        kind = raw_gate.get("kind")
        if not isinstance(kind, str) or kind not in ArcGenerator._VALID_GATE_KINDS:
            logger.info(
                "ARC sanitize dropped gate with invalid kind=%r (beat %d)",
                kind, beat_number,
            )
            return None
        value = raw_gate.get("value")
        if kind in ArcGenerator._INT_VALUED_GATES:
            try:
                ivalue = int(value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                logger.info(
                    "ARC sanitize dropped gate %s with non-int value=%r (beat %d)",
                    kind, value, beat_number,
                )
                return None
            if ivalue < 1:
                ivalue = 1
            return {"kind": kind, "value": ivalue}
        # String-valued gates (HAS_ITEM, FLAG_SET).
        if not isinstance(value, str) or not value.strip():
            logger.info(
                "ARC sanitize dropped gate %s with empty value (beat %d)",
                kind, beat_number,
            )
            return None
        return {"kind": kind, "value": value.strip()}

    @staticmethod
    def _make_objective_id(
        *, beat_number: int, kind: str, target: str, index: int,
    ) -> str:
        """Build a stable, deterministic objective id."""
        return f"b{beat_number}_{kind}_{_slugify(target)[:24]}_{index}"

    @staticmethod
    def _ensure_boss_defeat_objective(
        objectives: list[dict[str, Any]],
        *,
        beat_number: int,
        villain_name: str,
    ) -> list[dict[str, Any]]:
        """Boss beat invariant — always include a DEFEAT villain_name objective.

        If the LLM emitted a DEFEAT objective with another target (or none),
        we prepend the canonical one. The other objectives remain (e.g. a
        secondary social or examine task).
        """
        if not villain_name:
            return objectives
        for obj in objectives:
            if (
                obj.get("kind") == ObjectiveKind.DEFEAT.value
                and isinstance(obj.get("target"), str)
                and villain_name.lower() in obj["target"].lower()
            ):
                return objectives
        canonical = {
            "id": ArcGenerator._make_objective_id(
                beat_number=beat_number,
                kind=ObjectiveKind.DEFEAT.value,
                target=villain_name,
                index=0,
            ),
            "kind": ObjectiveKind.DEFEAT.value,
            "target": villain_name,
            "description": f"Vaincre {villain_name}, l'antagoniste de l'arc.",
            "required": True,
            "fuzzy_threshold": 0.7,
        }
        return [canonical, *objectives]

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
