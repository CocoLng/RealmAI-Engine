# Agent 04 — Discord Wizard: Stats, Skills & Flow complet

## Objectif

Ajouter deux nouvelles étapes au wizard de création de personnage Discord (Standard Array + sélection de skills) et mettre à jour le flow complet.

## Dépendances

- **Agents 01-03 terminés** (package character/, features, skills, standard array, create_character() à jour)

## Flow wizard complet (après modifications)

```
[Start Onboarding — bouton "Create Character"]
  → Étape 1 : Race select          (existant — CharacterCreateView)
  → Étape 2 : Class select         (existant — déverrouillé après race)
  → Étape 3 : Alignment select     (existant)
  → Étape 4 : Stat assignment      (NOUVEAU — StatAssignmentView)
  → Étape 5 : Skill selection      (NOUVEAU — SkillSelectionView)
  → Étape 6 : Name modal           (existant — CharacterNameModal)
  → Étape 7 : Starter gear         (existant — StarterGearView)
  → Étape 8 : Character sheet      (existant — embed récap, À ENRICHIR)
```

## Partie A — StatAssignmentView

### Fichier : `bot/views/stat_assignment_view.py` (nouveau)

**UX** : 6 select menus Discord, un par stat (STR, DEX, CON, INT, WIS, CHA).

Comportement :
- Chaque menu propose les valeurs **restantes** du Standard Array `[15, 14, 13, 12, 10, 8]`
- Quand le joueur assigne une valeur à une stat, elle disparaît des autres menus
- Afficher un hint "Recommandé pour [classe]" basé sur les stats primaires :
  - Fighter/Barbarian → STR, CON
  - Wizard → INT
  - Rogue → DEX
  - Cleric → WIS
  - Ranger → DEX, WIS
- Bouton "Confirmer" activé quand les 6 stats sont assignées
- Bouton "Réinitialiser" pour recommencer l'assignation

**Données transmises** : `dict[Ability, int]` (les 6 assignations)

### Implémentation technique

- Hérite de `discord.ui.View`
- 6 `discord.ui.Select` (un par stat)
- Callback sur chaque select : met à jour l'état interne, refresh les options des autres selects
- Utilise `interaction.response.edit_message()` pour rafraîchir dynamiquement

## Partie B — SkillSelectionView

### Fichier : `bot/views/skill_selection_view.py` (nouveau)

**UX** : Un `discord.ui.Select` multi-select.

Comportement :
- Affiche les skills disponibles pour la classe choisie (depuis `CLASS_SKILL_CHOICES`)
- Le joueur doit sélectionner exactement N skills (N = `CLASS_SKILL_CHOICES[class].choose`)
- Chaque option montre : nom du skill + ability associée (ex: "Stealth (DEX)")
- Message d'erreur si trop ou pas assez de skills sélectionnées
- Bouton "Confirmer" actif quand le bon nombre est sélectionné

**Données transmises** : `list[Skill]`

## Partie C — Mise à jour du flow

### Fichier : `bot/cogs/character.py` (modification)

Modifier la commande `/create_character` pour chaîner les nouvelles étapes :
1. Après l'alignement (étape 3) → lancer `StatAssignmentView`
2. Après les stats (étape 4) → lancer `SkillSelectionView`
3. Après les skills (étape 5) → lancer `CharacterNameModal` (existant)
4. Le reste du flow continue comme avant

### Fichier : `bot/views/character_create_view.py` (modification)

- Mettre à jour le callback de l'alignement select pour transitionner vers `StatAssignmentView` au lieu de `CharacterNameModal`
- Passer les données accumulées (race, class, alignment) au nouveau view

### Fichier : `bot/embeds/character_embed.py` (modification)

Enrichir l'embed de fiche de personnage pour afficher :
- Les features (traits raciaux + features de classe) avec descriptions
- Les skill proficiencies avec leurs modifiers calculés
- Les ability scores avec modifiers (format "+2" / "-1")

## Tests

### Fichiers de test existants à mettre à jour

- `tests/test_cog_character.py` — mettre à jour les mocks pour le nouveau flow

### Nouveaux tests

- `tests/test_stat_assignment_view.py` — tester la logique de sélection (pas besoin de Discord, tester la logique pure)
- `tests/test_skill_selection_view.py` — idem

## Validation

```bash
uv run pytest
uv run ruff check .
uv run mypy .
```

Test manuel : lancer le bot sur Discord, créer un personnage via le wizard complet, vérifier que toutes les étapes s'enchaînent et que la fiche finale est correcte.

## Estimation

Complexité : Élevée (UI Discord interactive, coordination de 8 étapes, état partagé entre views)
