"""Location domain model.

Represents places in the game world that players can visit.
"""

import logging

from pydantic import BaseModel, Field, model_validator

from world.combat_trigger_def import CombatTriggerDef
from world.combat_zone import Zone

logger = logging.getLogger(__name__)


class Location(BaseModel):
    """A location in the game world."""

    name: str
    description: str = ""
    arrival_hook: str = ""
    """1-2 sentences in second-person plural ("vous") bridging the party to
    this location at the moment of arrival. Rendered in the opening scene
    embed only; empty for locations that are not the starting area or were
    generated before this field existed."""
    connections: list[str] = Field(default_factory=list)
    exit_aliases: dict[str, list[str]] = Field(default_factory=dict)
    npcs_present: list[str] = Field(default_factory=list)
    items_available: list[str] = Field(default_factory=list)
    item_descriptions: dict[str, str] = Field(default_factory=dict)
    state_flags: dict[str, bool] = Field(default_factory=dict)
    unlocked_exits: list[str] = Field(default_factory=list)
    generated: bool = True
    """False for lightweight stubs awaiting first-visit hydration."""
    combat_zones: list[Zone] = Field(default_factory=list)
    """Named combat zones with an adjacency graph.

    Empty for locations that do not (yet) support combat encounters. When
    non-empty, the adjacency graph is validated to be consistent and
    symmetric on instantiation.
    """
    combat_triggers: dict[str, CombatTriggerDef] = Field(default_factory=dict)
    """Ambush triggers keyed by item/mechanism name.

    Empty for locations with no scripted ambush. The
    :mod:`bot.combat_entry` module consumes a matching entry when the
    player interacts with the keyed item, spawning the associated NPCs
    into combat. Consumed triggers remain in place with ``consumed=True``
    to keep the mechanism idempotent.
    """
    npc_roles: dict[str, str] = Field(default_factory=dict)
    """World-generator-provided archetype hints keyed by NPC name.

    Populated from ``npc_details[*].role`` when the world generator
    tags an NPC with a role from :mod:`engine.npc_library`. Consumed by
    :mod:`bot.scene_hydration` to dispatch the NPC to the right stat block
    (``captain``, ``soldier``, ``mage``, ...) instead of the default
    ``commoner``. Unknown roles are simply ignored by the hydration layer.
    """

    # ------------------------------------------------------------------
    # Zone helpers
    # ------------------------------------------------------------------

    def has_combat_zones(self) -> bool:
        """Return ``True`` if this location defines any combat zones."""
        return len(self.combat_zones) > 0

    def get_zone(self, name: str) -> Zone | None:
        """Look up a zone by name, or return ``None`` if absent."""
        for z in self.combat_zones:
            if z.name == name:
                return z
        return None

    def are_adjacent(self, zone_a: str, zone_b: str) -> bool:
        """Return ``True`` if ``zone_a`` is directly adjacent to ``zone_b``.

        Returns ``False`` if either zone is unknown or if they are not
        listed as neighbours. This is an undirected check — the graph
        validator enforces symmetry at construction time.
        """
        za = self.get_zone(zone_a)
        if za is None:
            return False
        return zone_b in za.adjacent_zone_names

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @model_validator(mode="after")
    def _deduplicate_npc_items(self) -> "Location":
        """Remove items whose name collides with an NPC (NPCs take priority)."""
        npc_names = set(self.npcs_present)
        collisions = [i for i in self.items_available if i in npc_names]
        if collisions:
            logger.warning(
                "LOCATION '%s': names appear in both npcs_present and "
                "items_available, removing from items: %s",
                self.name, collisions,
            )
            self.items_available = [
                i for i in self.items_available if i not in npc_names
            ]
            for name in collisions:
                self.item_descriptions.pop(name, None)
        return self

    @model_validator(mode="after")
    def _validate_zones_graph(self) -> "Location":
        """Validate that the zone adjacency graph is consistent and symmetric."""
        if not self.combat_zones:
            return self

        zone_names = {z.name for z in self.combat_zones}
        if len(zone_names) != len(self.combat_zones):
            raise ValueError(
                f"Location '{self.name}' has duplicate zone names in combat_zones"
            )

        for z in self.combat_zones:
            for adj in z.adjacent_zone_names:
                if adj not in zone_names:
                    raise ValueError(
                        f"Zone '{z.name}' references unknown adjacent '{adj}'"
                    )
                if adj == z.name:
                    raise ValueError(
                        f"Zone '{z.name}' cannot be adjacent to itself"
                    )
                other = self.get_zone(adj)
                # Just validated above; other is guaranteed non-None.
                assert other is not None
                if z.name not in other.adjacent_zone_names:
                    raise ValueError(
                        f"Adjacency not symmetric: '{z.name}' -> '{adj}' "
                        f"but '{adj}' does not list '{z.name}'"
                    )
        return self
