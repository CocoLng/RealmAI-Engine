"""World Generator — creates dynamic locations using the LLM."""

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from ai.client import OllamaClient
from ai.language import language_instruction
from world.combat_trigger_def import CombatTriggerDef
from world.combat_zone import Zone
from world.location import Location

if TYPE_CHECKING:
    from memory.indexer import SemanticIndexer

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "system_world_generator.txt").read_text()


class WorldGenerator:
    """Generates dynamic locations based on campaign context.

    Output is a fully-formed Location (world/location.py).
    The caller is responsible for persisting the location via LocationRepository.

    Uses a single LLM call with enriched context (atmosphere, beat context,
    NPC hints) to produce varied, story-coherent locations.
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
        campaign_context: str,
        location_type: str,
        location_name: str | None = None,
        language: str = "fr",
        location_hints: list[str] | None = None,
        atmosphere: str | None = None,
        beat_context: str | None = None,
        npc_count_hint: int | None = None,
        required_connections: list[str] | None = None,
        campaign_id: str = "",
    ) -> Location:
        """Generate a new location for the campaign.

        Args:
            campaign_context: Assembled context describing the campaign state.
            location_type: Type of location to generate (e.g. "tavern", "dungeon").
            location_name: Optional specific name for the location.
            language: ISO 639-1 language code for narrative output.
            location_hints: Optional list of canonical location names from the
                story arc. When provided, the LLM is instructed to reuse these
                exact names for any locations it references (connections, name).
            atmosphere: Optional atmosphere/mood suggestion for the location.
            beat_context: Optional text from story arc beats that reference
                this location, giving the LLM narrative direction.
            npc_count_hint: Optional minimum number of NPCs the location
                should contain.
            required_connections: Names that MUST appear verbatim in the
                output ``connections`` list — used to guarantee bidirectional
                links when hydrating a stub back toward its parent location.
            campaign_id: Campaign identifier forwarded to the SemanticIndexer
                when one is provided.  Defaults to ``""`` so existing callers
                that omit it continue to work unchanged.

        Returns:
            A Location ready to be saved.
        """
        user_content = self._build_user_message(
            campaign_context, location_type, location_name, location_hints,
            atmosphere, beat_context, npc_count_hint, required_connections,
        )
        lang_prefix = language_instruction(language)
        system_prompt = lang_prefix + _SYSTEM_PROMPT
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        data = self._client.chat_json(self.MODEL, messages, temperature=0.9, think=False)

        # --- Item description filtering (safety: drop hallucinated items) ---
        items_available = list(data.get("items_available", []))
        raw_descriptions = data.get("item_descriptions") or {}
        # Keep only descriptions whose key actually appears in items_available,
        # so a stray LLM hallucination cannot leak past canon.
        item_descriptions = {
            str(name): str(desc).strip()
            for name, desc in raw_descriptions.items()
            if name in items_available and str(desc).strip()
        }
        filtered_keys = set(raw_descriptions.keys()) - set(item_descriptions.keys())
        if filtered_keys:
            logger.warning(
                "Filtered %d item descriptions not in items_available: %s",
                len(filtered_keys),
                filtered_keys,
            )

        # --- Connections with mandatory back-links ---
        connections = list(data.get("connections", []))
        if required_connections:
            for req in required_connections:
                if req and req not in connections:
                    logger.warning(
                        "WORLD required connection %r missing from LLM output, "
                        "force-inserting", req,
                    )
                    connections.append(req)

        # --- Exit alias filtering (same pattern as item_descriptions) ---
        raw_exit_aliases = data.get("exit_aliases") or {}
        exit_aliases: dict[str, list[str]] = {}
        for key, value in raw_exit_aliases.items():
            if key not in connections:
                continue
            if not isinstance(value, list):
                continue
            cleaned = [str(a).strip() for a in value if str(a).strip()]
            if cleaned:
                exit_aliases[str(key)] = cleaned
        orphan_keys = set(raw_exit_aliases.keys()) - set(exit_aliases.keys())
        if orphan_keys:
            logger.warning(
                "Filtered %d exit_aliases not in connections: %s",
                len(orphan_keys),
                orphan_keys,
            )

        # --- npc_details.role extraction for hydration dispatch (task 43) ---
        raw_npc_details = data.get("npc_details") or []
        npc_roles: dict[str, str] = {}
        if isinstance(raw_npc_details, list):
            for entry in raw_npc_details:
                if not isinstance(entry, dict):
                    continue
                name = entry.get("name")
                role = entry.get("role")
                if (
                    isinstance(name, str)
                    and name.strip()
                    and isinstance(role, str)
                    and role.strip()
                ):
                    npc_roles[name.strip()] = role.strip()

        # --- Combat zones parsing (task 41) ---
        raw_zones = data.get("combat_zones") or []
        combat_zones_parsed: list[Zone] = []
        if isinstance(raw_zones, list):
            for raw in raw_zones:
                if not isinstance(raw, dict):
                    continue
                try:
                    combat_zones_parsed.append(Zone.model_validate(raw))
                except ValidationError as exc:
                    logger.warning(
                        "Dropping invalid combat zone: %s (error: %s)", raw, exc,
                    )

        # Repair the zone graph IN PLACE so the Location validator accepts it:
        # prune dangling/self references, add missing reverse edges (LLMs
        # routinely emit A→B without B→A).
        _symmetrize_zone_graph(combat_zones_parsed)

        # --- Combat triggers parsing (task 41) ---
        raw_triggers = data.get("combat_triggers") or {}
        triggers_parsed: dict[str, CombatTriggerDef] = {}
        if isinstance(raw_triggers, dict):
            for key, raw in raw_triggers.items():
                if not isinstance(raw, dict):
                    continue
                payload = {k: v for k, v in raw.items() if k != "item_name"}
                try:
                    triggers_parsed[str(key)] = CombatTriggerDef(
                        item_name=str(key), **payload,
                    )
                except ValidationError as exc:
                    logger.warning(
                        "Dropping invalid combat trigger %s: %s", key, exc,
                    )

        try:
            location = Location(
                name=str(data["name"]),
                description=str(data["description"]),
                arrival_hook=str(data.get("arrival_hook", "")).strip(),
                connections=connections,
                exit_aliases=exit_aliases,
                npcs_present=list(data.get("npcs_present", [])),
                items_available=items_available,
                item_descriptions=item_descriptions,
                combat_zones=combat_zones_parsed,
                combat_triggers=triggers_parsed,
                npc_roles=npc_roles,
            )
        except ValidationError as exc:
            # The zone adjacency graph is validated globally by Location.
            # If the LLM hallucinates a broken graph, fall back to building
            # the location without combat_zones so we don't lose the rest
            # of the generation.
            logger.warning(
                "Zone graph invalid, falling back to empty combat_zones: %s",
                exc,
            )
            location = Location(
                name=str(data["name"]),
                description=str(data["description"]),
                arrival_hook=str(data.get("arrival_hook", "")).strip(),
                connections=connections,
                exit_aliases=exit_aliases,
                npcs_present=list(data.get("npcs_present", [])),
                items_available=items_available,
                item_descriptions=item_descriptions,
                combat_zones=[],
                combat_triggers=triggers_parsed,
                npc_roles=npc_roles,
            )
        logger.info(
            "WORLD name=%r type=%s connections=%d aliases=%d zones=%d triggers=%d",
            location.name, location_type,
            len(location.connections), len(location.exit_aliases),
            len(location.combat_zones), len(location.combat_triggers),
        )

        if self._indexer is not None and campaign_id:
            self._indexer.index_location(campaign_id, location)

        return location

    def _build_user_message(
        self,
        campaign_context: str,
        location_type: str,
        location_name: str | None,
        location_hints: list[str] | None = None,
        atmosphere: str | None = None,
        beat_context: str | None = None,
        npc_count_hint: int | None = None,
        required_connections: list[str] | None = None,
    ) -> str:
        """Build the user message for the LLM prompt.

        Args:
            campaign_context: Assembled context describing the campaign state.
            location_type: Type of location to generate.
            location_name: Optional specific name for the location.
            location_hints: Optional canonical location names from the story arc.
            atmosphere: Optional atmosphere/mood suggestion.
            beat_context: Optional story arc beat text for this location.
            npc_count_hint: Optional minimum NPC count.
            required_connections: Names that MUST appear verbatim in the
                output connections list.

        Returns:
            Formatted user message string.
        """
        parts = [campaign_context, f"Location type: {location_type}"]
        if location_name:
            parts.append(f"Suggested name: {location_name}")
        if location_hints:
            hint_list = ", ".join(location_hints)
            parts.append(
                f"Canonical location names from the story arc: {hint_list}\n"
                "You MUST reuse these exact names when they match the location "
                "you are generating or when listing connections. Do NOT invent "
                "alternative names for locations that already appear in this list."
            )
        if required_connections:
            req_list = ", ".join(f'"{name}"' for name in required_connections)
            parts.append(
                f"Required connections to preserve: {req_list}\n"
                "These names MUST appear verbatim in the `connections` list "
                "you return — they are back-links to already-known locations. "
                "Add other connections alongside them if relevant, but never "
                "omit or rename these."
            )
        if atmosphere:
            parts.append(f"Atmosphere suggestion: {atmosphere}")
        if beat_context:
            parts.append(f"Story context for this location: {beat_context}")
        if npc_count_hint is not None:
            parts.append(
                f"This location needs at least {npc_count_hint} NPCs "
                "with story-relevant information."
            )
        return "\n\n".join(parts)


def _symmetrize_zone_graph(zones: list[Zone]) -> None:
    """Repair a zone adjacency graph in place so the Location validator accepts it.

    Repairs applied:
      1. Prune self-loops (``A → A``).
      2. Prune dangling references (``A → Inexistante``).
      3. Add missing reverse edges so every ``A → B`` is matched by ``B → A``.

    LLMs routinely emit graphs that violate one or more of these invariants.
    Rather than dropping the entire ``combat_zones`` payload on a single
    defect, we repair it and preserve as much structure as the LLM intended.
    """
    if not zones:
        return

    known_names = {z.name for z in zones}
    pruned = 0
    added_reverse = 0

    # Pass 1: prune self-loops and dangling references.
    for zone in zones:
        kept: list[str] = []
        for adj in zone.adjacent_zone_names:
            if adj == zone.name or adj not in known_names:
                pruned += 1
                continue
            kept.append(adj)
        zone.adjacent_zone_names = kept

    # Pass 2: add missing reverse edges to restore symmetry.
    zones_by_name = {z.name: z for z in zones}
    for zone in zones:
        for adj in zone.adjacent_zone_names:
            neighbor = zones_by_name[adj]
            if zone.name not in neighbor.adjacent_zone_names:
                neighbor.adjacent_zone_names.append(zone.name)
                added_reverse += 1

    if pruned or added_reverse:
        logger.info(
            "WORLD zones symmetrized: pruned=%d dangling/self, added=%d reverse edges",
            pruned, added_reverse,
        )
