# Lot C — Combat Initiation depuis texte libre

> Index : [`README.md`](README.md) · Statut : **TODO** · Pré-requis : **Lot B obligatoire** (sinon impossible de retrouver Jeanne par « villageois »)

## Pourquoi ce lot existe

Lors de la première campagne live (2026-04-07), JeanTest a tapé « j'engage le combat contre les villageurs » et Pedo a tapé « attaquer par derrière le fou qui fait peur au villageur ». **Aucune des deux n'a démarré de combat.** Le 4B a (correctement, vu son prompt) reclassé les deux en `Improvise` parce qu'il voit `In combat: no` dans le scene context et que son system prompt dit « Attack — combat only ». Et même si l'interpreter avait insisté avec ATTACK, le pipeline aurait échoué :

1. [`ai/entity_resolver.py:361-398`](../../ai/entity_resolver.py#L361) `_resolve_combatant` ne cherche que dans `combat_state.combatants`. Hors combat, retourne immédiatement `unknown` avec `reason="Combat target required but combat state or name missing"`.
2. [`bot/action_pipeline.py:329-338`](../../bot/action_pipeline.py#L329) `_validate` : si `action_type` n'est pas dans `EXPLORATION_ACTION_TYPES` et `combat_state is None`, retourne « `Attack` requires combat but no combat state ».

Il n'existe **aucun** chemin « bootstrap combat from attack » dans le code. Combat ne peut être démarré que par appel explicite à [`bot/cogs/combat.py:47-82`](../../bot/cogs/combat.py#L47) `start_combat_encounter`, qui n'est utilisé que par les cogs et jamais déclenché par une action texte libre.

## Mission

« @bot j'attaque le villageois » hors combat doit déclencher un encounter. Concrètement :
1. Le resolver retrouve Jeanne dans `npcs` (pas seulement dans `combat_state.combatants`).
2. Le pipeline détecte ATTACK + pas de combat + cible NPC valide → bootstrap un `CombatState`, promeut le NPC en `Combatant`, fait participer le PJ qui attaque (et idéalement les autres PJ présents), donne l'avantage de surprise à l'attaquant, et continue le tour normal du combat.
3. Le prompt interpreter est mis à jour pour autoriser explicitement ATTACK hors combat.

**Note importante sur la délégation au Lot E** : si la cible est un PNJ trivialement surclassé (commoner, désarmé, disposition pacifique), le bootstrap doit appeler `trivial_resolve` (qui sera implémenté par le Lot E) au lieu de démarrer un vrai combat. Pour ce lot C, prévois le **point d'extension** mais ne l'implémente pas — laisse un TODO clair que Lot E branchera.

## Contexte technique

### Code à lire avant
- [`ai/entity_resolver.py:361-398`](../../ai/entity_resolver.py#L361) — `_resolve_combatant` actuel.
- [`bot/action_pipeline.py:319-338`](../../bot/action_pipeline.py#L319) — `_validate`. Et lignes **340-375** `_resolve_mechanics` pour le cas combat.
- [`bot/cogs/combat.py:47-82`](../../bot/cogs/combat.py#L47) — `start_combat_encounter` qui sait construire un `CombatState` à partir d'une liste de Combatants. **À réutiliser.**
- [`engine/combat.py`](../../engine/combat.py) — modèles `CombatState`, `Combatant`, `CombatSide`, fonctions de résolution. Voir comment un `Combatant` est construit (HP, AC, inventaire, équipement, conditions, side).
- [`world/npc.py`](../../world/npc.py) — schéma NPC. Champs disponibles pour fabriquer un Combatant : `hp`, `max_hp`, `ac`, `disposition`, `name`. **Note** : un NPC n'a pas d'inventaire/équipement actuellement, donc le Combatant fabriqué aura un kit minimal (mains nues ou arme générique).
- [`bot/game_session.py`](../../bot/game_session.py) — comment accéder aux personnages des PJ et au `combat_state`.
- [`ai/prompts/system_interpreter.txt:18`](../../ai/prompts/system_interpreter.txt#L18) — où Attack est marqué « combat only ».
- **Sortie du Lot B** : `_match_candidates_v2`, l'éventuel champ `npc.aliases`, et le fallback LLM. Tu vas réutiliser tout ça pour `_resolve_combatant`.

## Plan d'implémentation

### Étape 1 — Étendre `_resolve_combatant` pour fallback sur les NPC

Dans [`ai/entity_resolver.py:361`](../../ai/entity_resolver.py#L361), modifier la signature pour accepter aussi `location` et `npcs` :

```python
def _resolve_combatant(
    action: InterpretedAction,
    combat_state: CombatState | None,
    location: Location | None = None,
    npcs: dict[str, NPC] | None = None,
    interpreter: Interpreter | None = None,  # pour fallback LLM (Lot B)
) -> ResolutionResult:
```

Logique :
1. Si `combat_state is not None` : matcher dans les enemies vivants comme aujourd'hui (avec `_match_candidates_v2` du Lot B).
2. Si pas trouvé OU `combat_state is None` : matcher dans les NPCs présents au lieu (`[npc for npc in npcs.values() if npc.location_name == location.name and npc.is_alive]`). Utiliser le même `_match_candidates_v2` avec aliases.
3. Si toujours rien et `interpreter` dispo, fallback LLM disambiguation comme dans `_resolve_npc`.
4. Si match → status="resolved", `resolved_entity` = nom du NPC.
5. Si plusieurs → "ambiguous".
6. Sinon → "unknown".

Mettre à jour l'appel dans `EntityResolver.resolve()` pour passer `location`, `npcs`, `interpreter`.

### Étape 2 — Mettre à jour le call site dans le pipeline

Dans [`bot/action_pipeline.py:202`](../../bot/action_pipeline.py#L202) `_continue_from_resolution`, l'appel à `EntityResolver.resolve` reçoit déjà `location` et `npcs` — bien. Ajouter `interpreter=self.interpreter`.

### Étape 3 — Bootstrap combat dans `_validate` ou `_resolve_mechanics`

C'est le cœur du lot. Dans [`bot/action_pipeline.py:319`](../../bot/action_pipeline.py#L319) `_validate` :

```python
def _validate(self, action: InterpretedAction) -> ValidationResult:
    eng_action = Action(...)
    if action.action_type in EXPLORATION_ACTION_TYPES:
        return validate_exploration_action(eng_action)
    
    # NEW: bootstrap combat from a free-text attack against a present NPC
    if (
        action.action_type == ActionType.ATTACK
        and self.combat_state is None
        and action.target_name is not None
        and action.target_name in self.npcs
    ):
        # The target was already resolved to a real NPC by EntityResolver.
        # Decide: trivial resolve (Lot E) or full bootstrap?
        target_npc = self.npcs[action.target_name]
        if self._should_trivial_resolve(target_npc):
            # TODO(Lot E): call trivial_resolve(attacker, target_npc) and return success
            pass  # for now, fall through to full bootstrap
        # Full bootstrap: build a CombatState with all PCs of the session + this NPC
        self.combat_state = self._bootstrap_combat_against(target_npc)
        # Now combat exists, fall through to validate_action below
    
    if self.combat_state is None:
        return ValidationResult(is_valid=False, error_message=...)
    return validate_action(eng_action, self.combat_state)
```

Implémenter `_bootstrap_combat_against(target_npc: NPC) -> CombatState` :
- Récupérer la liste des PJ présents dans la session (depuis `session.characters` si dispo dans le scope, sinon il faut passer la session ou les personnages au pipeline).
- Convertir chaque PJ en `Combatant` côté ALLY.
- Convertir le NPC en `Combatant` côté ENEMY : utiliser `target_npc.hp`, `target_npc.max_hp`, `target_npc.ac`, un inventaire vide ou avec une arme par défaut (poings, dagger), pas de spellcaster.
- Construire `CombatState(combatants=..., round_number=1, current_turn_index=0)`. **L'attaquant doit être en premier dans l'ordre d'initiative** (avantage de surprise).
- **Réutiliser** ce qui existe dans [`bot/cogs/combat.py:47`](../../bot/cogs/combat.py#L47) `start_combat_encounter` — extraire un helper si besoin pour ne pas dupliquer la construction du Combatant.
- Persister le combat_state dans la session (`session.combat_state = self.combat_state`).
- Logger `INFO bot.action_pipeline COMBAT bootstrapped from_action campaign={id} attacker={...} target={...}`.

`_should_trivial_resolve(npc: NPC) -> bool` : pour ce lot C, retourne toujours `False`. Le Lot E le remplira (déséquilibre + disposition + désarmé).

### Étape 4 — Mettre à jour le prompt interpreter

Dans [`ai/prompts/system_interpreter.txt`](../../ai/prompts/system_interpreter.txt) ligne 18, changer :
```
- "Attack"      — strike a hostile target with an equipped weapon (combat only)
```
en :
```
- "Attack"      — strike a target with an equipped weapon. If used outside of combat (e.g. ambushing an NPC), the system will start a combat encounter automatically.
```

Et ligne 32, retirer ou nuancer la directive « For combat... prefer combat ActionTypes » pour autoriser Attack hors combat sans pénaliser la confiance :
```
- For combat (the scene context will say `In combat: yes`), prefer combat ActionTypes (Attack, Cast Spell, Defend, Flee).
- Outside of combat, you may still return Attack if the player explicitly attacks an NPC present in the scene — the system will start a combat encounter from your output.
- Only use Improvise during combat for genuinely creative actions...
```

### Étape 5 — Tests

- `tests/ai/test_entity_resolver.py` (étendu par Lot B) : ajouter cas `_resolve_combatant` :
  - Hors combat, NPC dans scene, query alias → résolu.
  - Hors combat, NPC absent, query inconnue → unknown.
  - En combat, query enemy → résolu (régression).
- `tests/scenarios/test_attack_bootstrap_combat.py` (nouveau) :
  1. Setup : session avec 1 PJ + 1 NPC dans la même location, pas de combat.
  2. Action : pipeline.process(« j'attaque le villageois ») avec mocks d'interpreter qui retourne ATTACK target=villageois.
  3. Assert : à la fin, `session.combat_state is not None`, le NPC est dans `combatants[ENEMY]`, le PJ est dans `combatants[ALLY]`, l'attaque a été validée et résolue (un round joué OU au moins le combat est lancé).
- `uv run pytest tests/scenarios/test_attack_bootstrap_combat.py -v` doit passer.

## Critère de succès

- Tests verts.
- `uv run pytest` global vert.
- `uv run ruff check . && uv run mypy .` verts.
- Test live tester bot : `@bot j'attaque le villageois` (sans combat actif) → un combat embed apparaît, montrant Jeanne en HP/AC, et le tour du PJ est actif.

## Hors scope

- **Ne pas** implémenter `trivial_resolve` — c'est le Lot E qui le fera. Tu laisses juste le hook `_should_trivial_resolve` qui retourne `False`.
- **Ne pas** modifier la résolution mécanique du combat (dégâts, jets) — c'est déjà géré par l'engine et le combat cog.
- **Ne pas** ajouter de système de surprise/initiative custom au-delà de « l'attaquant joue en premier ».
- **Ne pas** gérer le cas où plusieurs PJ ne sont pas dans la même location — pour le MVP, on assume tous les PJ de la session participent.
- **Ne pas** toucher au narrateur ni aux embeds (sauf si le combat embed existant doit être posté — réutiliser l'existant).

## Notes de l'agent

> À remplir avant la fin de session : commit hash, blocages, observations utiles pour les lots suivants.

- **Statut : DONE** (commit à venir).
- **Périmètre livré** :
  - `ai/entity_resolver.py` : `_resolve_combatant` accepte désormais `location`, `npcs`, `interpreter`, `language`. Hors combat (ou si la cible n'est pas dans `combat_state.combatants`), il bascule sur les NPCs présents au lieu via `_match_candidates_v2` (avec `npc.aliases`), puis fallback LLM `interpreter.disambiguate_entity` si vide. Helper privé `_combatant_result` pour factoriser le mapping `matches → ResolutionResult`. Call site dans `EntityResolver.resolve` mis à jour.
  - `bot/cogs/combat.py` : nouveaux helpers module-level `build_pc_combatants(session)` et `build_npc_combatant(npc)`. `start_combat_encounter` passe par `build_pc_combatants` (suppression de la duplication). `build_npc_combatant` fabrique un `Combatant` ENEMY à partir d'un `NPC` (Character par défaut: speed=30, prof=2, hit_die=1d8, sauvegardes STR/CON, taille MEDIUM, classe Fighter si non précisée, inventaire vide).
  - `bot/action_pipeline.py` : nouveau champ optionnel `session: GameSession | None`. `_validate` détecte ATTACK + `combat_state is None` + cible NPC présente + session disponible → bootstrap. `_bootstrap_combat_against` ne fait **pas** d'init roll pour respecter la surprise — l'attaquant est posé en index 0, suivi des autres PJ, puis le NPC. `_should_trivial_resolve` est un hook no-op pour Lot E (TODO marqué). Persistance directe sur `session.combat_state` + log INFO `COMBAT bootstrapped from_action ...`.
  - `bot/cogs/action_handler.py` : passe `session=session` au `_pipeline_factory`.
  - `ai/prompts/system_interpreter.txt` : Attack n'est plus « combat only » et la règle "outside of combat" autorise explicitement Attack contre un NPC présent sans pénaliser la confiance.
  - Tests : 2 nouveaux cas dans `tests/bot/test_entity_resolver.py` (`test_attack_falls_back_to_present_npc`, `test_attack_unknown_npc_returns_unknown`). Nouveau scénario `tests/scenarios/test_attack_bootstrap_combat.py` qui vérifie via `ActionPipeline.process` (Interpreter+Narrator mockés) que `session.combat_state` est créé, que Jeanne est ENEMY, l'attaquant ALLY+index 0, et que le narrator a été appelé (validation OK).
- **Vérifications** :
  - `uv run pytest tests/bot/test_entity_resolver.py tests/scenarios/test_attack_bootstrap_combat.py` → 38 verts.
  - `uv run pytest` → 1175 verts, 1 échec pré-existant non lié (`test_scenario_unknown_entity_dragon` — narrator timeout Ollama, déjà signalé par Lot B).
  - `uv run ruff check .` → clean.
  - `uv run mypy .` → 204 erreurs pré-existantes ; 0 sur les fichiers touchés (`ai/entity_resolver.py`, `bot/action_pipeline.py`, `bot/cogs/combat.py`, `bot/cogs/action_handler.py`).
- **Observations pour Lot E** :
  - Le hook `_should_trivial_resolve(npc) -> bool` est en place dans `bot/action_pipeline.py`. Lot E n'a qu'à le remplir (déséquilibre + désarmé + disposition pacifique) et à implémenter `trivial_resolve(attacker, target_npc)` à appeler à la place du bootstrap (l'emplacement du `# else: TODO(Lot E)` est marqué).
  - `build_npc_combatant` donne aux NPC un kit minimal (mains nues, pas d'arme équipée). Lot E pourra étendre si besoin pour des NPC armés.
  - Le test scénario montre comment monter une session minimale (`SimpleNamespace`) sans toucher Discord ; réutilisable pour Lot E.
- **Limites connues** : pas de vraie initiative (l'attaquant joue toujours en premier hors init roll). Pas de gestion de PJ situés dans une autre location — tous les PJ de la session participent par défaut, conformément au brief.

