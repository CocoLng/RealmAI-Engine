# Task 12 — Modèle de zones de combat

**Phase** : 1 — Fondations NPC & engine
**Dépendances** : aucune
**Coordinateur** : [tasks/combat/README.md](README.md)

## Contexte

Le système de combat cible utilise un **positionnement abstrait par zones** plutôt qu'une grille 5-pieds (décision validée dans le plan coordinateur, section 3.1). Chaque `Location` où un combat est probable est découpée en **2 à 4 zones nommées** avec leurs adjacences. Les combattants occupent une zone, peuvent se déplacer vers une zone adjacente (action `Move`), et une zone peut porter des tags (`cover`, `difficult_terrain`, `elevated`, `hazard`) qui influencent les rolls.

Cette tâche **ne fait que créer le modèle data**. Le peuplement côté world generator est la tâche [41](41_world_generator_zones_triggers.md). L'intégration côté engine (mouvement, opportunity attacks) est la tâche [24](24_zone_movement_and_opportunity.md).

## Scope

Créer le modèle Pydantic `Zone` et l'intégrer dans `world/location.py`.

## Fichiers à créer/modifier

- **Créer** `world/combat_zone.py` (préférer un module séparé au lieu d'alourdir `location.py`).
- **Modifier** [world/location.py](../../world/location.py) — ajouter `combat_zones: list[Zone]` (default empty).
- **Modifier** [db/models.py](../../db/models.py) — colonne JSON pour persister les zones.
- **Modifier** [db/mappers.py](../../db/mappers.py) — sérialisation.

## Implémentation — esquisse

```python
# world/combat_zone.py
from enum import StrEnum

from pydantic import BaseModel, Field


class ZoneTag(StrEnum):
    COVER = "cover"              # +2 AC vs ranged attacks targeting creatures here
    DIFFICULT_TERRAIN = "difficult_terrain"  # move cost doubled
    ELEVATED = "elevated"        # advantage on ranged attacks from here
    HAZARD = "hazard"            # 1d4 damage on entering or starting turn here
    OBSCURED = "obscured"        # disadvantage on attacks targeting here


class Zone(BaseModel):
    """A named region within a combat-enabled Location.

    Combatants occupy exactly one zone at a time. Movement between zones
    is validated by adjacency. Tags modulate attack rolls and effects.
    """

    name: str = Field(min_length=1)
    description: str = ""
    adjacent_zone_names: list[str] = Field(default_factory=list)
    tags: list[ZoneTag] = Field(default_factory=list)

    def has_tag(self, tag: ZoneTag) -> bool:
        return tag in self.tags
```

Dans `world/location.py::Location` :

```python
from world.combat_zone import Zone

class Location(BaseModel):
    # ... existing fields ...
    combat_zones: list[Zone] = Field(default_factory=list)
    """Named combat zones with adjacency graph. Empty for locations that
    do not support combat encounters (or have not been generated with
    zones yet).
    """
```

Helpers sur `Location` :

```python
def has_combat_zones(self) -> bool:
    return len(self.combat_zones) > 0

def get_zone(self, name: str) -> Zone | None:
    for z in self.combat_zones:
        if z.name == name:
            return z
    return None

def are_adjacent(self, zone_a: str, zone_b: str) -> bool:
    """Undirected adjacency check."""
    za = self.get_zone(zone_a)
    if za is None:
        return False
    return zone_b in za.adjacent_zone_names
```

**Important — validation d'intégrité** : quand `combat_zones` est non vide, on doit valider que le graphe d'adjacence est **cohérent** :
- Chaque nom dans `adjacent_zone_names` existe dans `combat_zones`
- L'adjacence est symétrique (si A.adjacent contains B, alors B.adjacent contains A)
- Au moins 1 zone (trivial) ; idéalement 2+

Implémenter un `@model_validator(mode="after")` sur `Location` :

```python
from pydantic import model_validator

@model_validator(mode="after")
def _validate_zones_graph(self) -> "Location":
    if not self.combat_zones:
        return self
    zone_names = {z.name for z in self.combat_zones}
    for z in self.combat_zones:
        for adj in z.adjacent_zone_names:
            if adj not in zone_names:
                raise ValueError(
                    f"Zone '{z.name}' references unknown adjacent '{adj}'"
                )
    # Symmetry check
    for z in self.combat_zones:
        for adj in z.adjacent_zone_names:
            other = self.get_zone(adj)
            assert other is not None  # just validated above
            if z.name not in other.adjacent_zone_names:
                raise ValueError(
                    f"Adjacency not symmetric: '{z.name}' ↔ '{adj}'"
                )
    return self
```

## Acceptance criteria

- [ ] `world/combat_zone.py` existe avec `Zone` et `ZoneTag`.
- [ ] `Location.combat_zones` est un champ optionnel (default `[]`) — les locations existantes sans zones fonctionnent comme avant.
- [ ] Le validator Pydantic rejette les graphes incohérents (adjacence inconnue ou asymétrique).
- [ ] Une `Location` avec `combat_zones=[]` reste valide (pas de breaking change).
- [ ] Les helpers `has_combat_zones`, `get_zone`, `are_adjacent` fonctionnent.
- [ ] Les zones sont persistées en DB (JSON column) et roundtrip correctement.

## Tests à ajouter

Dans `tests/test_combat_zone.py` (nouveau) :

- `test_zone_basic_construction` — créer une `Zone` simple, vérifier les champs.
- `test_zone_with_tags` — créer avec plusieurs tags, `has_tag` fonctionne.
- `test_location_without_zones_still_valid` — regression : `Location(..., combat_zones=[])`.
- `test_location_with_valid_zone_graph` — 3 zones connectées en triangle, validator passe.
- `test_location_rejects_unknown_adjacency` — Zone A listant "UnknownZone" → ValueError.
- `test_location_rejects_asymmetric_adjacency` — A dit adjacent à B, mais B ne liste pas A → ValueError.
- `test_location_get_zone_returns_none_if_missing` — `get_zone("ghost")` → None.
- `test_location_are_adjacent` — cas positif, cas négatif, cas zone inconnue.

Dans `tests/test_db_repos.py` :

- `test_location_repository_roundtrips_zones` — save/load une Location avec zones, comparer structurellement.

## Hors scope

- **Ne pas** peupler les zones depuis le world generator — tâche [41](41_world_generator_zones_triggers.md).
- **Ne pas** implémenter le mouvement zone-à-zone dans `engine/combat.py` — tâche [24](24_zone_movement_and_opportunity.md).
- **Ne pas** modifier `Combatant` pour tracker sa zone — tâche [22](22_multi_enemy_combat_state.md).
- **Ne pas** ajouter de mécanique "line of sight" entre zones — reporté (hors scope global).

## Validation finale

```bash
uv run pytest tests/test_combat_zone.py tests/test_db_repos.py -v
uv run ruff check world/combat_zone.py world/location.py
uv run mypy world/combat_zone.py world/location.py
```
