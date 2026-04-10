# Agent 01 — Package Split: character.py → character/

## Objectif

Transformer le monolithe `engine/character.py` (369 lignes) en un package `engine/character/` avec des modules spécialisés. Zéro changement de comportement — pure réorganisation.

## Dépendances

- Aucune. C'est le premier agent à exécuter.

## Fichiers à créer

| Fichier | Contenu déplacé depuis character.py |
|---------|-------------------------------------|
| `engine/character/__init__.py` | Re-exports de tous les noms publics (backward compat) |
| `engine/character/enums.py` | `Ability`, `Race`, `CharacterClass`, `Size`, `Alignment` |
| `engine/character/models.py` | `AbilityScores`, `Character` |
| `engine/character/races.py` | `RACIAL_ABILITY_BONUSES`, `RACIAL_SIZE`, `RACIAL_SPEED` |
| `engine/character/classes.py` | `CLASS_HIT_DIE`, `CLASS_SAVING_THROWS`, `_HIT_DIE_MAX` |
| `engine/character/abilities.py` | `compute_modifier()`, `apply_racial_bonuses()`, `roll_ability_scores()` |
| `engine/character/progression.py` | `XP_THRESHOLDS`, `PROFICIENCY_BONUS_BY_LEVEL`, `compute_proficiency_bonus()`, `compute_max_hp()`, `check_level_up()`, `add_xp()`, `level_up()` |
| `engine/character/creation.py` | `create_character()` |

## Fichier à supprimer

- `engine/character.py` (remplacé par le package)

## Règles critiques

1. `__init__.py` DOIT re-exporter tous les noms publics actuels pour que `from engine.character import Character, Race, ...` continue de fonctionner
2. Les imports internes entre modules du package utilisent des imports relatifs (ex: `from .enums import Ability`)
3. Aucun changement de logique — copier-coller exact du code existant
4. Mettre à jour tous les imports dans le reste du projet si nécessaire (grep `from engine.character`)

## Fichiers impactés (imports à mettre à jour)

- `engine/combat.py`
- `engine/inventory.py`
- `engine/spells.py`
- `engine/conditions.py`
- `engine/validators.py`
- `engine/starter_gear.py`
- `bot/cogs/character.py`
- `bot/cogs/combat.py`
- `bot/views/character_create_view.py`
- `db/mappers.py`
- `world/npc.py`
- `tests/test_character.py` et autres tests

## Validation

```bash
uv run pytest
uv run ruff check .
uv run mypy .
```

Tout doit être vert. Si un seul test casse, c'est un bug d'import à corriger avant de continuer.

## Estimation

Complexité : Moyenne (beaucoup de fichiers touchés, mais aucune logique nouvelle)
