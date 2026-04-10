# Agent 02 — Feature System & Skill Proficiencies

## Objectif

Ajouter deux systèmes au package `engine/character/` :
1. Un système de **Features** générique (traits raciaux, features de classe)
2. Les **18 Skills** D&D 5e avec proficiencies par classe

## Dépendances

- **Agent 01 terminé** (le package `engine/character/` doit exister)

## Partie A — Feature System

### Fichier : `engine/character/features.py`

Créer les modèles Pydantic :

```python
class FeatureSource(StrEnum):
    RACE = "race"
    CLASS = "class"
    BACKGROUND = "background"   # futur
    FEAT = "feat"               # futur

class MechanicalEffect(BaseModel):
    effect_type: str            # "darkvision", "damage_resistance", etc.
    value: int | str | list[str]

class Feature(BaseModel):
    name: str
    source: FeatureSource
    source_name: str            # "Elf", "Barbarian"
    description: str
    effects: list[MechanicalEffect]
    level_requirement: int = 1
```

### Helpers à implémenter dans `features.py`

- `has_feature(character, name) -> bool`
- `get_feature_effects(character, effect_type) -> list[MechanicalEffect]`
- `has_darkvision(character) -> int | None` (retourne la portée ou None)
- `get_damage_resistances(character) -> list[str]`

### Catalogues raciaux : `races.py`

Ajouter `RACIAL_FEATURES: dict[Race, list[Feature]]` avec les traits pour chaque race :

| Race | Features |
|------|----------|
| Human | (aucun trait mécanique spécial — bonus +1 partout déjà géré) |
| Elf | Darkvision (60ft), Keen Senses (perception prof), Fey Ancestry (advantage vs charmed) |
| Dwarf | Darkvision (60ft), Dwarven Resilience (advantage + resistance poison), Stonecunning |
| Halfling | Lucky (reroll natural 1), Brave (advantage vs frightened), Halfling Nimbleness |
| Half-Orc | Darkvision (60ft), Relentless Endurance, Savage Attacks |
| Gnome | Darkvision (60ft), Gnome Cunning (advantage INT/WIS/CHA saves vs magic) |
| Tiefling | Darkvision (60ft), Hellish Resistance (fire resistance), Infernal Legacy (thaumaturgy cantrip) |

### Catalogues de classe : `classes.py`

Ajouter `CLASS_FEATURES: dict[CharacterClass, list[Feature]]` — **niveau 1 seulement** :

| Classe | Features niveau 1 |
|--------|-------------------|
| Fighter | Fighting Style (défensif par défaut), Second Wind |
| Wizard | Arcane Recovery, Spellcasting |
| Rogue | Sneak Attack (1d6), Expertise, Thieves' Cant |
| Cleric | Spellcasting, Divine Domain |
| Ranger | Favored Enemy, Natural Explorer |
| Barbarian | Rage, Unarmored Defense |

### Mise à jour du modèle Character (`models.py`)

```python
class Character(BaseModel):
    # ... champs existants ...
    features: list[Feature] = Field(default_factory=list)
```

## Partie B — Skills

### Fichier : `engine/character/enums.py` (ajout)

```python
class Skill(StrEnum):
    ATHLETICS = "Athletics"
    ACROBATICS = "Acrobatics"
    SLEIGHT_OF_HAND = "Sleight of Hand"
    STEALTH = "Stealth"
    ARCANA = "Arcana"
    HISTORY = "History"
    INVESTIGATION = "Investigation"
    NATURE = "Nature"
    RELIGION = "Religion"
    ANIMAL_HANDLING = "Animal Handling"
    INSIGHT = "Insight"
    MEDICINE = "Medicine"
    PERCEPTION = "Perception"
    SURVIVAL = "Survival"
    DECEPTION = "Deception"
    INTIMIDATION = "Intimidation"
    PERFORMANCE = "Performance"
    PERSUASION = "Persuasion"

SKILL_ABILITY: dict[Skill, Ability] = {
    Skill.ATHLETICS: Ability.STR,
    Skill.ACROBATICS: Ability.DEX,
    Skill.SLEIGHT_OF_HAND: Ability.DEX,
    Skill.STEALTH: Ability.DEX,
    Skill.ARCANA: Ability.INT,
    Skill.HISTORY: Ability.INT,
    Skill.INVESTIGATION: Ability.INT,
    Skill.NATURE: Ability.INT,
    Skill.RELIGION: Ability.INT,
    Skill.ANIMAL_HANDLING: Ability.WIS,
    Skill.INSIGHT: Ability.WIS,
    Skill.MEDICINE: Ability.WIS,
    Skill.PERCEPTION: Ability.WIS,
    Skill.SURVIVAL: Ability.WIS,
    Skill.DECEPTION: Ability.CHA,
    Skill.INTIMIDATION: Ability.CHA,
    Skill.PERFORMANCE: Ability.CHA,
    Skill.PERSUASION: Ability.CHA,
}
```

### Fichier : `engine/character/classes.py` (ajout)

```python
class ClassSkillConfig(BaseModel):
    choose: int                 # nombre de skills à choisir
    options: list[Skill]        # skills disponibles

CLASS_SKILL_CHOICES: dict[CharacterClass, ClassSkillConfig] = {
    CharacterClass.FIGHTER: ClassSkillConfig(choose=2, options=[...]),
    CharacterClass.WIZARD: ClassSkillConfig(choose=2, options=[...]),
    CharacterClass.ROGUE: ClassSkillConfig(choose=4, options=[...]),
    CharacterClass.CLERIC: ClassSkillConfig(choose=2, options=[...]),
    CharacterClass.RANGER: ClassSkillConfig(choose=3, options=[...]),
    CharacterClass.BARBARIAN: ClassSkillConfig(choose=2, options=[...]),
}
```

### Fonction : `engine/character/abilities.py` (ajout)

```python
def compute_skill_modifier(character: Character, skill: Skill) -> int:
    ability = SKILL_ABILITY[skill]
    mod = compute_modifier(character.ability_scores.get(ability))
    if skill in character.skill_proficiencies:
        mod += character.proficiency_bonus
    return mod
```

### Mise à jour du modèle Character (`models.py`)

```python
class Character(BaseModel):
    # ... champs existants ...
    skill_proficiencies: list[Skill] = Field(default_factory=list)
```

## Tests à créer

| Fichier | Ce qu'il teste |
|---------|----------------|
| `tests/test_features.py` | Feature model, catalogues raciaux/classe, has_feature(), has_darkvision(), get_damage_resistances() |
| `tests/test_skills.py` | Skill enum, SKILL_ABILITY mapping complet, compute_skill_modifier() avec/sans proficiency, CLASS_SKILL_CHOICES cohérence |

## Validation

```bash
uv run pytest
uv run ruff check .
uv run mypy .
```

## Estimation

Complexité : Élevée (beaucoup de données à encoder correctement, deux nouveaux systèmes)
