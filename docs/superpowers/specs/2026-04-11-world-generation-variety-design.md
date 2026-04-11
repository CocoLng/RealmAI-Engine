# Design : Variété et Performance de la Génération de Monde

**Date** : 2026-04-11
**Statut** : Draft

---

## Contexte

La génération actuelle de campagnes souffre de trois problèmes :

1. **Répétitivité structurelle** — Les story arcs suivent toujours le même schéma (social → exploration → combat → ...), même avec des thèmes différents.
2. **Répétitivité de contenu** — NPCs, lieux, et quêtes se ressemblent d'une campagne à l'autre. Le LLM converge vers les mêmes archétypes (taverne, vieux sage, seigneur maléfique).
3. **Temps de génération excessif** — 4 appels LLM séquentiels avec thinking mode au lancement, pour un résultat prévisible.

### Causes racines identifiées

| Cause | Impact | Fichiers concernés |
|-------|--------|-------------------|
| Pattern brainstorm identique (3 options, mêmes slots) pour tous les générateurs | Le LLM génère des micro-variations, pas de vraies alternatives | `ai/arc_generator.py`, `ai/world_generator.py`, `ai/quest_generator.py` |
| Zéro mécanisme de variété dans les prompts | Aucune directive anti-pattern, aucun seed aléatoire | `ai/prompts/brainstorm_*.txt` |
| Enum encounter_type trop petit (5 types) | Séquences de beats prévisibles | `world/story_arc.py` |
| NPC generation faible (prompt 16 lignes, fallbacks génériques) | NPCs interchangeables | `ai/npc_generator.py`, `ai/prompts/system_npc_generator.txt` |
| Pas de cohérence inter-générateurs | Arc ne contraint pas NPCs, lieux ne contraignent pas quêtes | Tous les générateurs |

### Approche choisie : Hybride (Code-driven scaffolding + Enhanced prompts)

Le code Python génère un cadre structurel (recipe) avec variété garantie. Le LLM remplit ce cadre avec du contenu créatif original. Le brainstorm LLM est supprimé — la variété vient du code, la créativité vient du LLM.

**Principe directeur** : "Le code arbitre la structure, le LLM narre le contenu."

---

## 1. Arc Recipe Engine

### 1.1 Bibliothèque de structures narratives

Nouveau fichier : `engine/arc_recipes.py`

~8-10 archétypes narratifs, chacun définissant une séquence de beats avec un rythme distinct :

| Archétype | Séquence typique de beats | Twist position | Ton par défaut |
|-----------|--------------------------|----------------|----------------|
| `mystery` | exploration → social → puzzle → social → exploration → puzzle → combat → boss | Beat 5-7 | mystérieux |
| `heist` | social → puzzle → exploration → combat → puzzle → boss | Beat 3-4 | tendu |
| `siege` | social → combat → exploration → combat → combat → boss | Beat 4-5 | épique |
| `diplomacy` | social → social → exploration → puzzle → social → social → boss | Beat 6-8 | intimiste |
| `survival` | exploration → combat → exploration → puzzle → combat → exploration → boss | Beat 3-5 | sombre |
| `revenge` | social → exploration → combat → social → combat → boss | Beat 2-3 | dramatique |
| `escape` | combat → exploration → puzzle → exploration → combat → boss | Beat 2-4 | tendu |
| `corruption` | social → exploration → social → puzzle → social → combat → boss | Beat 4-6 | sombre |
| `discovery` | exploration → exploration → social → puzzle → exploration → social → boss | Beat 5-6 | merveilleux |
| `betrayal` | social → social → social → combat → exploration → puzzle → boss | Beat 3-4 | dramatique |

### 1.2 Recipe Generator

Fonction `generate_recipe(theme: str, previous_archetype: str | None = None) -> ArcRecipe` qui :

1. **Sélectionne un archétype** — Weighted random, excluant `previous_archetype`
2. **Randomise le nombre de beats** — Entre 10 et 15
3. **Étend la séquence** — Insère des beats supplémentaires en respectant le ratio de l'archétype
4. **Injecte des complications** — 1-2 tirées d'un pool de ~15 :
   - Trahison d'un allié, course contre la montre, dilemme moral, ressource rare épuisée, fausse piste, rival concurrent, catastrophe naturelle, maladie/malédiction, dette ancienne, identité secrète, prophétie ambiguë, faction alliée hostile, otage, prix à payer, allié ambigu
5. **Choisit un ton** — Parmi : sombre, humoristique, épique, intimiste, mystérieux, mélancolique, tendu, merveilleux
6. **Valide les contraintes** :
   - Pas plus de 2 combats consécutifs
   - Au moins 1 puzzle dans l'arc
   - Au moins 2 beats sociaux
   - Le dernier beat est toujours `boss`

### 1.3 Modèle ArcRecipe

```python
class ArcRecipe(BaseModel):
    archetype: str                    # "mystery", "heist", etc.
    beat_sequence: list[str]          # ["exploration", "social", "puzzle", ...]
    beat_subtypes: list[str]          # ["tracking", "negotiation", "riddle", ...]
    complications: list[str]          # ["trahison d'un allié", "course contre la montre"]
    tone: str                         # "sombre"
    twist_position: int               # Index du beat twist principal
    num_beats: int                    # 10-15
    villain_archetype: str | None     # Tiré d'un pool : tyran, manipulateur, fanatique,
                                      # opportuniste, tragique, monstre, rival, corrompu
```

### 1.4 Changement de l'appel LLM

**Avant** (2 appels) :
1. Brainstorm : `think=True`, `thinking_budget=2048` → 3 concepts
2. Generate : `think=False` → StoryArc JSON

**Après** (1 appel) :
1. Generate : `think=False`, `temperature=0.9` → StoryArc JSON
   - Le prompt reçoit l'`ArcRecipe` comme contexte structurant
   - Instruction : "Tu reçois un squelette narratif. Remplis-le avec du contenu créatif. Tu PEUX ajuster l'ordre des beats si la narration le demande, mais respecte le ratio de types d'encounters et la position du twist."

**Impact** : -1 appel LLM, suppression du thinking mode, temps réduit de ~40-50%.

---

## 2. World & Location Generation améliorée

### 2.1 Location Context Builder

Nouveau code dans `ai/world_generator.py` : avant l'appel LLM, le code construit un contexte enrichi :

- **Beat hints** — Extrait des beats de l'arc qui mentionnent ce lieu pour cohérence arc ↔ monde
- **Atmosphère** — Tirée aléatoirement d'un pool de ~12 options : oppressante, féerique, délabrée, vivante, silencieuse, chaotique, sacrée, industrielle, souterraine, maritime, aérienne, volcanique
- **Contraintes NPC** — Dérivées des beats (si un beat "social" se passe ici → au moins 2 NPCs avec informations utiles)
- **Budget items** — Calibré à 2-4 items (évite les listes interminables)

### 2.2 Appel LLM unique

Même pattern que l'arc : 1 seul appel, `think=False`, `temperature=0.9`. Le prompt reçoit le contexte enrichi au lieu de brainstormer.

**Avant** : 2 appels (brainstorm + generate)
**Après** : 1 appel avec contexte enrichi

### 2.3 Impact sur le lancement

| Métrique | Avant | Après |
|----------|-------|-------|
| Appels LLM au lancement | 4 | 2 |
| Thinking mode | Oui (2048 tokens × 2) | Non |
| Temps estimé | ~2-3 min | ~45-90s |

---

## 3. NPC Archetype Library

### 3.1 Pool d'archétypes

Nouveau fichier : `engine/npc_archetypes.py`

~20 archétypes organisés en 5 catégories :

**Autorité** : maire corrompu, capitaine usé, prêtresse dissidente, juge partial
**Commerce** : marchand endetté, contrebandier moral, artisan obsédé, prêteur patient
**Savoir** : bibliothécaire paranoïaque, oracle frauduleux, herboriste ermite, cartographe aveugle
**Trouble** : voleur repenti, espion double, noble en exil, déserteur traqué
**Peuple** : enfant débrouillard, vétéran traumatisé, barde menteur, veuve vengeresse

Chaque archétype fournit :
- **Traits contradictoires** (2-3) — ex: "généreux mais paranoïaque"
- **Hook narratif** (1) — ex: "cache quelqu'un dans sa cave"
- **Pattern de dialogue** (1) — ex: "parle en métaphores culinaires"

### 3.2 Sélection et injection

1. Python choisit un archétype (weighted random, évitant les doublons dans le même lieu)
2. Le prompt NPC reçoit l'archétype comme base
3. Le LLM enrichit avec détails spécifiques au contexte de la campagne
4. Suppression des fallbacks génériques actuels (lignes 64-69 de `npc_generator.py`)

### 3.3 Impact

- NPCs immédiatement plus distincts et mémorables
- Chaque lieu a des NPCs aux profils variés (pas 3 marchands dans la même taverne)
- Les hooks narratifs créent des opportunités de quêtes émergentes

---

## 4. Encounter Subtype Expansion

### 4.1 Sous-types par catégorie

| Type principal | Sous-types |
|---------------|------------|
| `social` | negotiation, interrogation, seduction, deception, ceremony |
| `combat` | ambush, duel, siege, chase, defense |
| `exploration` | tracking, infiltration, navigation, discovery, survival |
| `puzzle` | riddle, mechanism, investigation, ritual, cipher |
| `boss` | (inchangé, toujours terminal) |

### 4.2 Changement du modèle StoryBeat

Ajout d'un champ optionnel `encounter_subtype: str | None` dans `StoryBeat`.
- Le code engine continue d'utiliser `encounter_type` pour la logique mécanique
- Le `encounter_subtype` est passé dans le prompt du narrateur pour enrichir la description
- Le recipe generator assigne automatiquement un sous-type cohérent avec l'archétype d'arc

---

## 5. Changements aux Prompts

### 5.1 Suppression des prompts brainstorm

Fichiers supprimés :
- `ai/prompts/brainstorm_arc.txt`
- `ai/prompts/brainstorm_world.txt`
- `ai/prompts/brainstorm_quest.txt`

Le brainstorm_story_check.txt est conservé (Story Director, usage différent).

### 5.2 Réécriture des prompts de génération

Les prompts `system_arc_generator.txt`, `system_world_generator.txt`, `system_quest_generator.txt` sont réécrits pour :
- Accepter une recipe/contexte enrichi comme input
- Inclure des **directives anti-pattern** explicites : "Évite : le tavernier bourru classique, le sage dans sa tour, le seigneur maléfique générique, le voleur au coeur d'or, le prêtre vertueux"
- Donner **2-3 exemples variés** de contenu de qualité
- Instruction de **flexibilité** : "Tu peux réordonner les beats si la narration l'exige, mais respecte les contraintes structurelles"

### 5.3 Réécriture du prompt NPC

Le `system_npc_generator.txt` est enrichi :
- Reçoit l'archétype comme base
- Directives anti-pattern pour éviter les NPCs génériques
- Exemples de NPCs mémorables
- Pas de fallback générique — si le LLM produit du contenu vide, on re-tire un archétype et on retente

---

## 6. Fichiers impactés

### Nouveaux fichiers
| Fichier | Rôle |
|---------|------|
| `engine/arc_recipes.py` | Bibliothèque d'archétypes narratifs + recipe generator |
| `engine/npc_archetypes.py` | Pool d'archétypes NPC avec traits, hooks, patterns |

### Fichiers modifiés
| Fichier | Nature du changement |
|---------|---------------------|
| `ai/arc_generator.py` | Suppression brainstorm, accept `ArcRecipe`, single call |
| `ai/world_generator.py` | Suppression brainstorm, contexte enrichi, single call |
| `ai/quest_generator.py` | Suppression brainstorm, contexte enrichi, single call |
| `ai/npc_generator.py` | Injection archétype, suppression fallbacks génériques |
| `ai/prompts/system_arc_generator.txt` | Réécriture complète |
| `ai/prompts/system_world_generator.txt` | Réécriture complète |
| `ai/prompts/system_quest_generator.txt` | Réécriture complète |
| `ai/prompts/system_npc_generator.txt` | Enrichissement significatif |
| `world/story_arc.py` | Ajout `encounter_subtype` à `StoryBeat` |
| `bot/campaign_launcher.py` | Passe `ArcRecipe` au générateur, plus de brainstorm |
| `db/models.py` | Migration pour `encounter_subtype` nullable |

### Fichiers supprimés
| Fichier | Raison |
|---------|--------|
| `ai/prompts/brainstorm_arc.txt` | Remplacé par ArcRecipe |
| `ai/prompts/brainstorm_world.txt` | Remplacé par contexte enrichi |
| `ai/prompts/brainstorm_quest.txt` | Remplacé par contexte enrichi |

---

## 7. Vérification

### Tests unitaires
- `tests/test_arc_recipes.py` — Valide que chaque archétype produit une recipe conforme aux contraintes (pas 3 combats consécutifs, au moins 1 puzzle, etc.)
- `tests/test_npc_archetypes.py` — Valide que la sélection évite les doublons et couvre les catégories
- `tests/test_arc_generator.py` — Mise à jour pour le nouveau flow single-call
- `tests/test_world_generator.py` — Mise à jour pour le contexte enrichi

### Tests d'intégration
- Lancer 5 campagnes avec des thèmes variés (donjon, pirate, forêt, cité, désert)
- Comparer les structures d'arcs — vérifier qu'elles utilisent des archétypes différents
- Comparer les NPCs — vérifier qu'ils ont des personnalités distinctes
- Mesurer le temps de génération — vérifier la réduction de ~50%

### Test live Discord
- `/start_campaign` avec un thème → vérifier que l'arc est cohérent et varié
- Jouer 5-10 tours → vérifier que la narration est fluide avec les nouveaux beats
- Vérifier que les NPCs sont mémorables et distincts

---

## 8. Ce qui ne change PAS

- Le **6-step action pipeline** reste identique
- Le **4-layer memory system** reste identique
- Le **narrator** et **interpreter** ne sont pas modifiés
- Le **Story Director** garde son brainstorm (usage différent : analyse, pas génération)
- La **base de données** et les **repositories** restent identiques (sauf ajout `encounter_subtype`)
- Le **NPC Agent** (dialogue) n'est pas modifié — il bénéficie indirectement de meilleurs NPC sheets
