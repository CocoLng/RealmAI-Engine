# Agent 05 — DB Migration & Cleanup

## Objectif

S'assurer que les personnages existants en base fonctionnent avec le nouveau modèle Character (features + skills), vérifier l'isolation des données, et mettre à jour la documentation projet.

## Dépendances

- **Agents 01-04 terminés** (tout le système character refactoré et le wizard Discord à jour)

## Partie A — Compatibilité DB

### Vérification Pydantic defaults

Le modèle `Character` a deux nouveaux champs avec defaults :
- `features: list[Feature] = Field(default_factory=list)`
- `skill_proficiencies: list[Skill] = Field(default_factory=list)`

Pydantic v2 remplit automatiquement les defaults pour les clés manquantes dans le JSON. **Normalement aucune migration SQL nécessaire** car `character_json` est un TEXT blob.

**À vérifier** : charger un ancien `character_json` (sans features/skills) et confirmer que la désérialisation fonctionne.

### Backfill des features

#### Fichier : `db/mappers.py` (modification)

Ajouter une fonction de backfill appelée à la désérialisation :

```python
def backfill_character_features(character: Character) -> Character:
    """Ajoute les features raciales et de classe si absentes (persos créés avant le feature system)."""
    if not character.features:
        from engine.character.races import RACIAL_FEATURES
        from engine.character.classes import CLASS_FEATURES
        racial = RACIAL_FEATURES.get(character.race, [])
        class_feats = [f for f in CLASS_FEATURES.get(character.char_class, [])
                       if f.level_requirement <= character.level]
        character.features = racial + class_feats
    return character
```

Appeler cette fonction dans le mapper `row_to_character()` (ou équivalent) pour que les vieux persos récupèrent automatiquement leurs features.

## Partie B — Isolation des personnages

### Vérification de sécurité

Le PK de `PlayerCharacterRow` est `(discord_user_id, campaign_id)`. Vérifier :

1. **Repository** : toutes les méthodes `update()`/`save()` de `PlayerCharacterRepository` prennent `discord_user_id` ET `campaign_id` en paramètre — pas de méthode qui update par ID seul
2. **Cog** : les commandes Discord extraient `interaction.user.id` et l'utilisent systématiquement — impossible d'agir sur le perso d'un autre joueur
3. **Pas de endpoint admin** qui bypass la vérification (ou s'il y en a, ils sont protégés)

Si un trou est trouvé, le corriger.

## Partie C — Mise à jour documentation

### Fichier : `tasks/todo.md`

Mettre à jour avec :
- ✅ Marquer comme fait : refonte character, features, skills, standard array, wizard Discord
- Garder les items existants non liés à ce refactor
- Ajouter les items différés :

```markdown
## Différé (à faire plus tard)
- [ ] Backgrounds (Acolyte, Criminal, Noble, etc.) — 2 skill proficiencies + équipements + trait RP
- [ ] Feats (choix ASI-ou-feat aux niveaux 4/8/12/16/19)
- [ ] Multiclassing
- [ ] Système de langues
- [ ] Tool proficiencies
- [ ] Class features de niveau 2+ (progression complète)
- [ ] Point Buy et 4d6-drop-lowest comme méthodes alternatives de stats
- [ ] Boutique / système achat-vente
- [ ] Catalogue de sorts étendu (>20 sorts actuels)
```

## Tests

| Test | Ce qu'il vérifie |
|------|-----------------|
| Test de désérialisation ancien JSON | Charger un character_json sans features/skills → les defaults s'appliquent |
| Test backfill | Un perso sans features → après backfill, a ses features raciales + classe |
| Test isolation | Impossible de save un perso avec un user_id différent de celui en session |

## Validation

```bash
uv run pytest
uv run ruff check .
uv run mypy .
```

## Estimation

Complexité : Faible (vérifications + petits ajustements, pas de nouvelle logique complexe)
