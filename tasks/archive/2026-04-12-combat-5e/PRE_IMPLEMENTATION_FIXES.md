# Pre-implementation fixes — corrections à appliquer avant/pendant l'implémentation

Après revue finale du plan, j'ai identifié **5 problèmes sérieux** et **8 mineurs** qui demandent une attention particulière pendant l'implémentation. Chacun est documenté avec le contexte, la tâche affectée, et la correction à apporter.

**Lis ce fichier avant de démarrer une tâche qui y est référencée.**

---

## 🔴 Problèmes sérieux (à résoudre dès la première tâche concernée)

### 1. `CombatTrigger` doit vivre dans `engine/`, pas dans `bot/`

**Tâches affectées** : [20](20_combat_entry_module.md), [21](21_initiative_and_surprise.md)

**Problème** : La tâche 20 place `CombatTrigger`, `CombatTriggerKind`, `InitiativeSide` dans `bot/combat_entry.py`. Mais la tâche 21 (qui modifie `engine/combat.py::start_combat`) a besoin d'importer `CombatTrigger` pour connaître le `surprise_side`. `engine/` **ne peut pas importer** de `bot/` (règle d'architecture, confirmée : `grep` montre 0 import de bot/ai dans engine/).

**Correction** : La tâche 20 doit créer `CombatTrigger` et ses enums dans un **nouveau fichier `engine/combat_trigger.py`**. Le module `bot/combat_entry.py` l'importe puis ré-exporte pour confort de lecture. La tâche 21 importe directement depuis `engine/combat_trigger.py`.

```python
# engine/combat_trigger.py — nouveau fichier
from enum import StrEnum
from pydantic import BaseModel, Field

class CombatTriggerKind(StrEnum):
    PLAYER_ATTACK = "player_attack"
    LETHAL_INTENT = "lethal_intent"
    AMBUSH = "ambush"
    PROVOCATION = "provocation"
    SCRIPTED_BEAT = "scripted_beat"

class InitiativeSide(StrEnum):
    PLAYERS = "players"
    NPCS = "npcs"
    BOTH_READY = "both_ready"

class CombatTrigger(BaseModel):
    kind: CombatTriggerKind
    aggressor_name: str
    enemy_names: list[str] = Field(min_length=1)
    surprise_side: InitiativeSide
    narrative_hint: str = ""
```

Puis `bot/combat_entry.py` fait `from engine.combat_trigger import CombatTrigger, ...` pour son propre usage.

---

### 2. `engine/npc_ai/boss_brain.py` ne peut pas importer `ai/`

**Tâche affectée** : [52](52_boss_llm_tactician.md)

**Problème** : La tâche 52 propose de créer `engine/npc_ai/boss_brain.py` qui importe `from ai.npc_tactician import NPCTactician`. C'est une **violation directe** de la règle "engine/ is pure Python, no LLM calls ever" (CLAUDE.md). Même si les dés restent côté engine, l'import dependency fait que `engine/` dépend de `ai/`, ce qui casse l'architecture.

**Correction** : Déplacer `boss_brain.py` dans `bot/npc_ai/boss_brain.py` (nouveau sous-dossier côté bot). Les brains scripted (`scripted.py` minion, `elite.py`) restent dans `engine/npc_ai/` parce qu'ils sont purs. Seul le boss brain (qui appelle le LLM) vit côté bot.

**Alternative** (plus lourde) : définir une interface `NPCBrain(Protocol)` dans `engine/npc_ai/__init__.py`, et injecter une implémentation concrète depuis bot au moment de la résolution. Plus propre mais overkill pour le MVP.

**Retenir** : **déplacer le fichier dans bot/**. La tâche 64 (`TurnManager` dans bot) dispatche vers le bon brain selon le tier — elle est déjà côté bot donc cohérent.

---

### 3. `Combatant.stat_block` manquant — les NPCs perdent leur stat block dans `build_npc_combatant`

**Tâche affectée** : [22](22_multi_enemy_combat_state.md) (et toutes les tâches qui lisent `combatant.stat_block`)

**Problème** : Le code actuel [bot/cogs/combat.py:73-107](../../bot/cogs/combat.py) convertit un `NPC` en `Combatant` en créant un nouveau `Character` vide, **sans préserver** le `stat_block`. Partout dans les tâches 50/51/52/53/54 je référence `combatant.character.stat_block` — ça ne marchera pas.

**Correction** : La tâche 22 doit ajouter un champ `stat_block: NPCStatBlock | None = None` **directement sur `Combatant`** (pas sur `Character`), et `build_npc_combatant` doit le propager :

```python
# engine/combat.py — dans la tâche 22
class Combatant(BaseModel):
    # ... existing ...
    stat_block: NPCStatBlock | None = None
    """Set for ENEMY-side combatants derived from an NPC with a stat block.
    None for PCs (who use their Character + Inventory instead)."""

# bot/cogs/combat.py — update build_npc_combatant (tâche 22 ou 43)
def build_npc_combatant(npc: NPC) -> Combatant:
    # ... existing character construction ...
    return Combatant(
        name=npc.name,
        side=CombatSide.ENEMY,
        character=char,
        inventory=inv,
        stat_block=npc.stat_block,  # ← propager ici
        current_zone=None,
    )
```

Toutes les références `combatant.character.stat_block` dans les tâches 50-54 et 62 doivent être lues comme `combatant.stat_block`. **Mettre à jour mentalement** au moment de l'implémentation.

---

### 4. `resolve_attack` vs `NPCAttack` — décision de design non tranchée

**Tâches affectées** : [22](22_multi_enemy_combat_state.md), [50](50_scripted_minion_ai.md), [51](51_elite_behavior_profiles.md)

**Problème** : `resolve_attack` dans `engine/combat.py` existe avec la signature `(attacker, defender, weapon, state)` et attend un `Weapon` de l'inventaire du PC. Les NPCs utilisent des `NPCAttack` (dés, dmg type, to-hit) qui ne sont pas des Weapons. Les tâches 50/51 le mentionnent mais le laissent "à décider".

**Correction** : Trancher pour **créer un helper `resolve_npc_attack`** parallèle dans `engine/combat.py`, plutôt que d'alourdir `resolve_attack` avec des branches conditionnelles. Signature :

```python
def resolve_npc_attack(
    attacker: Combatant,
    defender: Combatant,
    npc_attack: NPCAttack,
    state: CombatState,
) -> AttackResult:
    """Resolve an NPC attack using its stat block's NPCAttack definition.

    Rolls 1d20 + npc_attack.to_hit_bonus vs defender.character.ac, then
    rolls npc_attack.damage_dice on hit. Mirrors resolve_attack's contract
    (returns AttackResult) but pulls its numbers from NPCAttack instead
    of Weapon.
    """
```

Assigner cette fonction à la **tâche 22** (où `Combatant.stat_block` est ajouté — c'est le point logique d'introduire la symétrie). Les tâches 50/51/52 l'appellent ensuite naturellement.

---

### 5. `fled`, `pending_phase_narrations`, `phase_save_bonus`, `legendary_points_remaining` doivent être ajoutés dans tâche 22

**Tâches affectées** : [22](22_multi_enemy_combat_state.md), [32](32_flee_resolution.md), [53](53_legendary_actions_off_turn.md), [54](54_phase_transitions.md)

**Problème** : Plusieurs tâches en aval ajoutent chacune **un nouveau champ** au modèle `Combatant` ou `CombatState`. Ces extensions séquentielles créent des conflits Pydantic/migration récurrents et forcent les tâches aval à réécrire le modèle.

**Correction** : La **tâche 22** doit introduire tous ces champs d'un coup (même si les features associées viennent plus tard) :

```python
# engine/combat.py — tâche 22 consolidée
class Combatant(BaseModel):
    # ... existing ...
    stat_block: NPCStatBlock | None = None              # fix 3
    fled: bool = False                                   # task 32
    current_zone: str | None = None                      # task 24
    action_budget: ActionBudget = Field(default_factory=ActionBudget)  # task 23
    legendary_points_remaining: int = 0                  # task 53
    phase_save_bonus: int = 0                            # task 54

class CombatState(BaseModel):
    # ... existing ...
    combat_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    end_reason: CombatEndReason | None = None
    pending_phase_narrations: list[PhaseTransitionEvent] = Field(default_factory=list)  # task 54
```

Les tâches 23, 24, 32, 53, 54 **utilisent** ces champs mais n'ont **pas à les créer**. Ça évite les allers-retours de modèle.

---

## 🟡 Problèmes mineurs (à noter pendant l'implémentation, pas bloquants)

### 6. `GameSession.npc_tactician` pas instancié explicitement

**Tâche** : [52](52_boss_llm_tactician.md)

La tâche 52 utilise `session.npc_tactician` sans que ce champ n'existe sur `GameSession`. Ajouter explicitement dans la fiche 52 : "étendre `bot/game_session.py::GameSession` avec `npc_tactician: NPCTactician | None = None`, instancié au démarrage de session avec l'`OllamaClient` partagé".

### 7. Truce : marquer `fled=True` sur les enemies est brittle

**Tâche** : [81](81_social_resolution_mid_combat.md)

`attempt_truce` met `enemy.fled = True` pour ne plus les compter vivants, puis appelle `finalize_combat(TRUCE)`. Mais `check_combat_end` retournerait `VICTORY` sur des enemies tous `fled`, ce qui contredit `TRUCE`.

**Correction** : dans `attempt_truce`, **ne pas** marquer `fled=True`. Appeler directement `finalize_combat(session, CombatEndReason.TRUCE)` avec le flag TRUCE explicite — le cleanup désactive le state peu importe l'état des combattants.

### 8. `ActionType.DISENGAGE` ambigu

**Tâche** : [24](24_zone_movement_and_opportunity.md)

La tâche 24 propose un helper `disengage(combatant)` mais ne crée pas explicitement `ActionType.DISENGAGE`. Le joueur ne peut donc pas la déclencher via le flow normal.

**Correction** : ajouter `DISENGAGE = "Disengage"` à `ActionType` dans la **tâche 24**. La validation et la résolution sont dans le même fichier. Les boutons de la tâche 63 peuvent inclure un bouton "Disengage" séparé ou le fusionner avec "Defend" — décision UX à la tâche 63.

### 9. `_compute_save_mod` helper manquant

**Tâche** : [51](51_elite_behavior_profiles.md)

`execute_signature_ability` appelle `_compute_save_mod(target, ability_enum)` qui n'existe pas. Ajouter dans `engine/character.py` (ou inline dans `elite.py`) :

```python
def compute_save_mod(combatant: Combatant, ability: Ability) -> int:
    """Compute save modifier = ability mod + (proficiency if proficient)."""
    mod = compute_modifier(combatant.character.ability_scores.get(ability))
    if ability in combatant.character.saving_throw_proficiencies:
        mod += combatant.character.proficiency_bonus
    return mod
```

Pour un NPC dérivé d'un stat_block, `saving_throw_proficiencies` reste celui de Character — MVP: ignorer la proficiency pour les NPCs et utiliser juste l'ability mod. Documenter cette simplification.

### 10. Coordination TurnManager ↔ ActionPipeline implicite

**Tâches** : [31](31_action_pipeline_combat_dispatch.md), [64](64_turn_ping_and_timeout.md)

Le flow "action joueur → résolution → advance_turn → prochain tour (PC ou NPC)" est géré en partie par `ActionPipeline._resolve_mechanics` et en partie par `TurnManager.on_turn_advanced`, mais le **point de handoff** n'est pas explicite.

**Correction** : ajouter un court bullet dans la fiche 64 : "`ActionPipeline` appelle `turn_manager.on_turn_advanced(next_combatant)` à la fin de `_narrate` si le combat est toujours actif. Si combat terminé, appelle `finalize_combat` et poste l'embed de fin à la place."

### 11. Log des rounds de combat dans la campaign md

**Tâche** : pas d'owner clair

Le format actuel de `logs/campaigns/{id}.md` log chaque action joueur avec `**Mécaniques:**` et `**Narration:**`. Les tours NPC en combat n'ont pas de format explicité. Ajouter une ligne dans la fiche 64 (`TurnManager`) ou 82 (e2e test) : "chaque tour (PC ou NPC) produit une entrée markdown au format standard, même si le NPC tour est résolu automatiquement".

### 12. Task 22 est lourde — envisager un split 22a/22b si besoin

**Tâche** : [22](22_multi_enemy_combat_state.md)

Avec l'ajout des fixes 3, 4 et 5 ci-dessus, la tâche 22 couvre : multi-enemy turn management, `advance_turn`, `check_combat_end`, persistence, concentration hook, `resolve_npc_attack`, et l'extension complète du modèle Combatant/CombatState. C'est beaucoup pour une seule tâche.

**Options** :
- **Option A** (recommandée) : garder tâche 22 comme "Multi-enemy CombatState complet" et l'attaquer comme une seule PR. C'est cohérent et tout est lié.
- **Option B** : découper en 22a (modèles étendus + persistence) et 22b (turn management + hooks). Si on fait ça, 22a est un prérequis de 22b.

Décision : laisser à l'exécutant du chantier de choisir selon la taille des PRs souhaitée dans le repo.

### 13. Campaign log pour combat — format spécifique ?

**Tâche** : pas d'owner

Les embeds de combat start, dice rolls, phase transitions sont visuellement riches mais **n'apparaissent pas** dans le log markdown actuel. Décision implicite : le log markdown reste textuel et "sérieux" — pas de reproduction des embeds, juste des descriptions mécaniques. À confirmer.

---

## Synthèse — go/no-go pour l'implémentation

**Verdict** : **GO**, avec ces fixes appliqués au fil de l'eau.

Les 5 problèmes sérieux ont chacun une correction simple et **localisée**. Aucun ne demande de re-brainstormer le design. Ils viennent de l'impossibilité de tout modéliser mentalement sans toucher au code — naturel pour un plan de cette taille.

**Ordre d'attaque recommandé pour ces fixes** :
1. Avant la tâche 20 → lire fix #1 (CombatTrigger dans engine/)
2. Avant la tâche 22 → lire fixes #3, #4, #5 (tout le modèle étendu d'un coup)
3. Avant la tâche 52 → lire fix #2 (boss_brain dans bot/)
4. Avant la tâche 64 → lire fix #10 (handoff ActionPipeline↔TurnManager)
5. Avant la tâche 81 → lire fix #7 (truce sans fled=True)

Les autres fixes mineurs peuvent être appliqués à chaud par l'agent qui exécute la tâche.

**Aucun problème de design ou d'architecture** : la vision du chantier tient, les règles CLAUDE.md sont préservées (une fois le fix #2 appliqué), la règle d'or LLM≠referee reste intacte.
