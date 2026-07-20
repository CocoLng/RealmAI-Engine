# Task 41 — World generator : zones de combat et triggers

**Phase** : 4 — Interprète & générateurs LLM (parallèle)
**Dépendances** : [12](12_zone_model.md)
**Coordinateur** : [tasks/combat/README.md](README.md)

## Contexte

Le world generator ([ai/world_generator.py](../../ai/world_generator.py)) produit aujourd'hui des `Location` avec `name, description, connections, npcs_present, items_available`. Il ne produit **pas** :

- Les **combat_zones** nécessaires à la tâche [24](24_zone_movement_and_opportunity.md) pour le mouvement zone-à-zone.
- Les **combat_triggers** nécessaires à la tâche [20](20_combat_entry_module.md) pour déclencher des ambushes via INTERACT.

Cette tâche étend le prompt et le parser pour produire ces données structurées.

## Scope

1. Étendre `ai/prompts/system_world_generator.txt` pour demander `combat_zones` et `combat_triggers` dans le JSON output.
2. Étendre `ai/world_generator.py::WorldGenerator.generate` pour parser ces champs et les propager vers `Location`.
3. Créer le modèle `CombatTriggerDef` (Pydantic) dans `world/combat_trigger_def.py` — représente un mechanism ou item qui déclenche un combat quand interagit.

## Règles de génération

- **Zones** : 2 à 4 zones par location. Chaque zone a un nom court en français (2-5 mots), une description d'une ligne, une liste d'adjacences (symétrique !), et des tags optionnels.
- **Triggers** : 0 à 2 triggers par location, et uniquement si la description suggère des éléments plausibles (levier, piège, porte mystérieuse, statue, etc.). Chaque trigger a un `item_name` (doit matcher un entry de `items_available` ou `mechanisms_present`), une liste de `spawn_npcs` (noms d'NPCs qui apparaissent), et une `reveal_narration`.
- Pour les **locations non-combat** (taverne paisible, campement allié), les deux peuvent être vides.

## Fichiers à créer/modifier

- **Modifier** [ai/prompts/system_world_generator.txt](../../ai/prompts/system_world_generator.txt) — ajouter les sections.
- **Modifier** [ai/world_generator.py](../../ai/world_generator.py) — parser.
- **Créer** `world/combat_trigger_def.py`
- **Modifier** [world/location.py](../../world/location.py) — ajouter `combat_triggers: dict[str, CombatTriggerDef]`.

## Implémentation — esquisse

```python
# world/combat_trigger_def.py
from pydantic import BaseModel, Field


class CombatTriggerDef(BaseModel):
    """A mechanism or item in a location that triggers combat when interacted with.

    Stored on Location.combat_triggers keyed by the item/mechanism name the
    interpreter would resolve for an INTERACT action. When a player interacts
    with the keyed entity, the combat entry module spawns the NPCs listed
    in spawn_npcs and starts an ambush combat.
    """
    item_name: str = Field(min_length=1)
    spawn_npcs: list[str] = Field(default_factory=list)
    reveal_narration: str = ""
    consumed: bool = False
    """True after the trigger has fired — idempotent. Prevents the same
    mechanism from spawning the ambush twice."""
```

```python
# world/location.py
from world.combat_trigger_def import CombatTriggerDef

class Location(BaseModel):
    # ... existing fields ...
    combat_triggers: dict[str, CombatTriggerDef] = Field(default_factory=dict)
```

**Prompt — sections à ajouter** dans `system_world_generator.txt` :

```
## Combat zones

Pour les locations où un combat est plausible (dungeon, temple, arène,
forêt hostile, rue, place publique, etc.), tu DOIS produire une liste
`combat_zones` avec 2 à 4 zones nommées. Chaque zone est un point de
référence tactique dans la scène.

Format :
"combat_zones": [
  {
    "name": "<nom court en français, 2-5 mots>",
    "description": "<une ligne décrivant ce qu'on voit dans cette zone>",
    "adjacent_zone_names": ["<autres zones accessibles en 1 mouvement>"],
    "tags": ["cover" | "difficult_terrain" | "elevated" | "hazard" | "obscured"]
  }
]

Règles :
- 2 à 4 zones par location-combat. Moins = pas d'intérêt tactique. Plus =
  confusion.
- Les zones sont nommées (ex : "Autel central", "Promontoire rocheux",
  "Alcôve sud") — pas de coordonnées.
- L'adjacence est SYMÉTRIQUE : si A dit adjacent à B, B doit dire adjacent à A.
- Les tags influencent les rolls (cover = +2 AC ranged, difficult_terrain =
  mouvement doublé, elevated = advantage ranged, hazard = dégâts à l'entrée).
- Pour les locations paisibles (taverne, campement allié), combat_zones
  peut être vide `[]`.

## Combat triggers (ambushes)

Si la description contient un mécanisme plausible (levier, piège, porte
mystérieuse, statue, sceau, urne, coffre piégé), tu PEUX produire des
`combat_triggers` qui déclenchent des ambushes quand interagis.

Format :
"combat_triggers": {
  "<nom exact de l'item ou mechanism>": {
    "spawn_npcs": ["<nom NPC 1>", "<nom NPC 2>"],
    "reveal_narration": "<une phrase décrivant ce qui se passe quand activé>"
  }
}

Règles :
- 0 à 2 triggers par location, jamais plus.
- Le `nom` doit matcher un item existant dans `items_available` OU un
  nom de mechanism explicite.
- `spawn_npcs` liste des PNJs qui apparaissent au déclenchement. Ils
  peuvent être des noms existants OU des nouveaux — le runtime les
  créera.
- `reveal_narration` décrit le moment précis où le piège se révèle.
- Si rien ne justifie un piège dans la location, retourne un dict vide `{}`.
```

**Parser** dans `ai/world_generator.py::WorldGenerator.generate` :

```python
# Après le parsing existant des champs basiques
raw_zones = data.get("combat_zones", []) or []
combat_zones_parsed: list[Zone] = []
for raw in raw_zones:
    try:
        zone = Zone.model_validate(raw)
        combat_zones_parsed.append(zone)
    except ValidationError as exc:
        logger.warning(
            "Dropping invalid combat zone: %s (error: %s)", raw, exc,
        )

raw_triggers = data.get("combat_triggers", {}) or {}
triggers_parsed: dict[str, CombatTriggerDef] = {}
for key, raw in raw_triggers.items():
    if not isinstance(raw, dict):
        continue
    try:
        td = CombatTriggerDef(item_name=key, **raw)
        triggers_parsed[key] = td
    except ValidationError as exc:
        logger.warning(
            "Dropping invalid combat trigger %s: %s", key, exc,
        )

# Construct the Location including these
location = Location(
    # ... existing fields ...
    combat_zones=combat_zones_parsed,
    combat_triggers=triggers_parsed,
)
```

**Validation côté Location** : la tâche [12](12_zone_model.md) a déjà ajouté un `@model_validator` qui rejette les adjacences incohérentes. Ici, si le LLM produit un graphe cassé, le parser catch la `ValidationError` et **drop** les zones plutôt que de faire planter la génération entière. Log le problème pour analyse ultérieure.

## Acceptance criteria

- [ ] `CombatTriggerDef` existe en Pydantic dans `world/combat_trigger_def.py`.
- [ ] `Location.combat_triggers: dict[str, CombatTriggerDef]` field, default vide.
- [ ] Le prompt du world generator contient les deux sections Combat zones et Combat triggers.
- [ ] Le parser tolère les locations sans zones ni triggers (back-compat).
- [ ] Le parser drop silencieusement les zones invalides (adjacence cassée) avec warning.
- [ ] Une location "taverne paisible" a `combat_zones=[]` et `combat_triggers={}`.
- [ ] Une location "temple sombre avec autel" produit 2-4 zones et potentiellement 1 trigger.

## Tests à ajouter

Dans `tests/ai/test_world_generator.py` :

- `test_parses_combat_zones_from_json`.
- `test_drops_invalid_zones_with_asymmetric_adjacency`.
- `test_parses_combat_triggers_from_json`.
- `test_empty_zones_and_triggers_accepted` — location paisible.
- `test_zone_validation_error_does_not_crash_generation`.

Dans `tests/test_location.py` (ou équivalent) :

- `test_location_combat_triggers_default_empty_dict`.

## Hors scope

- **Ne pas** spawner réellement les NPCs au moment du trigger — tâche [20](20_combat_entry_module.md) consomme le trigger.
- **Ne pas** implémenter l'idempotence `consumed=True` dans le runtime — la tâche [20] ou [31] gère le set.
- **Ne pas** taguer les archétypes des NPCs spawnés — le world generator produit des noms, la tâche [43](43_hydration_dispatches_tier.md) s'occupe de l'hydration avec archetype.

## Validation finale

```bash
uv run pytest tests/ai/test_world_generator.py tests/test_location.py -v
uv run ruff check ai/world_generator.py world/combat_trigger_def.py world/location.py
uv run mypy ai/world_generator.py world/combat_trigger_def.py world/location.py
```
