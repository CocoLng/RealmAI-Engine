# Agent 03 — Standard Array & Refonte create_character()

## Objectif

Implémenter le Standard Array comme méthode d'attribution des stats et mettre à jour `create_character()` pour intégrer les features et skills.

## Dépendances

- **Agent 01 terminé** (package character/ existe)
- **Agent 02 terminé** (Feature system et Skills existent)

## Partie A — Standard Array

### Fichier : `engine/character/abilities.py` (ajout)

```python
STANDARD_ARRAY: tuple[int, ...] = (15, 14, 13, 12, 10, 8)

def assign_standard_array(
    assignments: dict[Ability, int],
    race: Race,
) -> AbilityScores:
    """Assigne les valeurs du Standard Array aux abilities, puis applique les bonus raciaux.

    Validations :
    - Exactement 6 assignments (une par Ability)
    - Chaque valeur du Standard Array utilisée exactement une fois
    - Toutes les 6 Abilities couvertes

    Returns AbilityScores avec bonus raciaux appliqués.
    Raises ValueError si les assignments sont invalides.
    """
```

### Garder `roll_ability_scores()`

La fonction reste disponible comme utilitaire mais n'est plus utilisée dans le flow de création par défaut.

## Partie B — Refonte create_character()

### Fichier : `engine/character/creation.py`

Mettre à jour la signature :

```python
def create_character(
    name: str,
    race: Race,
    char_class: CharacterClass,
    ability_scores: AbilityScores,
    alignment: Alignment = Alignment.TRUE_NEUTRAL,
    skill_proficiencies: list[Skill] | None = None,
) -> Character:
    """Crée un personnage niveau 1 avec stats dérivées, features et skills.

    - ability_scores : déjà avec bonus raciaux appliqués
    - skill_proficiencies : si None, liste vide (le wizard Discord les fournira)
    - Features raciales et de classe automatiquement ajoutées
    """
```

La fonction doit :
1. Calculer les stats dérivées (HP, AC, speed, etc.) — comme avant
2. Récupérer les features raciales depuis `RACIAL_FEATURES[race]`
3. Récupérer les features de classe niveau 1 depuis `CLASS_FEATURES[char_class]`
4. Combiner dans `Character.features`
5. Stocker `skill_proficiencies`

### Compatibilité

L'ancienne signature fonctionne toujours (skill_proficiencies est optionnel). Les tests existants ne cassent pas.

## Tests à créer

| Fichier | Ce qu'il teste |
|---------|----------------|
| `tests/test_standard_array.py` | `assign_standard_array()` : assignation valide, doublons rejetés, valeurs hors array rejetées, bonus raciaux appliqués |
| `tests/test_creation_flow.py` | `create_character()` end-to-end : perso avec features + skills, vérifier que les features raciales/classe sont bien là, vérifier HP/AC/speed corrects |

## Validation

```bash
uv run pytest
uv run ruff check .
uv run mypy .
```

## Estimation

Complexité : Moyenne (logique simple, mais doit bien s'intégrer avec les deux agents précédents)
