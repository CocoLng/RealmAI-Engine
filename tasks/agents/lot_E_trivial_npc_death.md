# Lot E — Trivial NPC Death

> Index : [`README.md`](README.md) · Statut : **TODO** · Pré-requis : **Lot C obligatoire** (sans le bootstrap combat il n'y a pas de point d'extension où brancher la résolution triviale)

## Pourquoi ce lot existe

Le user l'a explicitement demandé : « si les villageois ne se défendent pas, ils doivent mourir ». Aujourd'hui, même après les Lots B et C qui permettront « j'attaque le villageois » → bootstrap combat, le système va démarrer un **vrai** combat avec initiative, jets d'attaque, etc., contre une pauvre Jeanne qui n'a pas d'arme et 4 PV. C'est lent, c'est ridicule, et ça ne reflète pas la fiction (un guerrier niveau 1 qui frappe une villageoise non armée n'a pas besoin d'un système d'initiative).

Aussi : l'engine actuel n'a aucun moyen de muter `NPC.is_alive`, de retirer un PNJ d'une `location.npcs_present`, ni de logger un meurtre dans la story bible. Tuer un PNJ aujourd'hui, c'est invisible pour le système.

## Mission

(1) Implémenter `engine.combat.trivial_resolve(attacker, target_npc)` qui auto-résout une attaque écrasante : un jet, dégâts, mort. (2) Brancher ce helper dans le bootstrap combat du Lot C via `_should_trivial_resolve`. (3) Propager la mort : `NPC.is_alive=False`, retirer de `location.npcs_present`, ajouter un world_fact « X tué par Y au turn N », logger dans la story bible. (4) Bonus : si un PNJ FRIENDLY est témoin du meurtre, sa disposition bascule en HOSTILE envers l'auteur.

## Contexte technique

### Code à lire avant
- [`engine/combat.py`](../../engine/combat.py) — où ajouter `trivial_resolve`. Voir comment les dégâts sont calculés (`apply_damage`, `roll_damage` ou équivalent).
- [`engine/dice.py`](../../engine/dice.py) — pour rouler un d20 + un dé de dégât.
- [`engine/character.py`](../../engine/character.py) — pour récupérer le bonus d'attaque et de dégât d'un PJ.
- [`world/npc.py`](../../world/npc.py) — modèle NPC. Le champ `is_alive` existe-t-il ? Sinon l'ajouter (`is_alive: bool = True`). Ajouter aussi un helper `kill()` qui met `hp=0, is_alive=False`.
- [`world/location.py`](../../world/location.py) — `npcs_present: list[str]`. Tu vas en retirer le nom du PNJ tué.
- [`bot/story_bible_logger.py`](../../bot/story_bible_logger.py) — comment écrire dans la bible. Voir s'il y a déjà un format pour les « events » ou si tu dois l'ajouter.
- [`bot/action_pipeline.py`](../../bot/action_pipeline.py) — où Lot C aura mis le hook `_should_trivial_resolve`. C'est ce hook que tu remplis.
- [`db/repositories/npc_repo.py`](../../db/repositories/) — pour persister `NPC.is_alive`.
- [`world/facts.py`](../../world/) (s'il existe) — pour ajouter un world_fact.
- **Brief Lot C** : [`lot_C_combat_initiation.md`](lot_C_combat_initiation.md) section « Étape 3 » qui définit où `_should_trivial_resolve` est appelé.

## Plan d'implémentation

### Étape 1 — Ajouter `is_alive` et helpers à NPC

Dans [`world/npc.py`](../../world/npc.py) :
```python
class NPC(BaseModel):
    ...
    is_alive: bool = True
    
    def kill(self) -> None:
        """Mark this NPC as dead. Idempotent."""
        self.hp = 0
        self.is_alive = False
```

S'assurer que la sérialisation DB du repo NPC inclut bien `is_alive`. Ajouter une migration douce (default True pour les NPC existants).

### Étape 2 — `trivial_resolve` dans `engine/combat.py`

```python
@dataclass
class TrivialResolveResult:
    hit: bool
    damage: int
    target_killed: bool
    description: str  # short mechanical summary for the narrator

def trivial_resolve(
    attacker: Character,
    target_npc: NPC,
    weapon: Weapon | None = None,
) -> TrivialResolveResult:
    """Auto-resolve an attack against a defenseless NPC.
    
    Assumes the attacker has overwhelming advantage. Rolls one attack and 
    one damage. The target is killed if HP reaches 0.
    """
```

Logique :
1. Roll d20 + attacker.attack_bonus vs target_npc.ac. En cas de raté (rare contre AC ~10), retourner `hit=False, damage=0, target_killed=False, description="Le coup manque de peu — la cible s'enfuit en panique."` (et on laisse la suite gérer).
2. En cas de touche : roll damage du weapon (ou 1d4+STR si mains nues). Appliquer à `target_npc.hp`.
3. Si `target_npc.hp <= 0` : `target_npc.kill()`, `target_killed=True`.
4. Description : `"{attacker.name} frappe {target_npc.name} d'un coup décisif ({damage} dégâts)."` ou similaire pour la mort.
5. **Ne pas** créer de `CombatState` — c'est tout l'intérêt.

### Étape 3 — `_should_trivial_resolve`

Dans [`bot/action_pipeline.py`](../../bot/action_pipeline.py), remplir le hook laissé par le Lot C :

```python
def _should_trivial_resolve(self, target_npc: NPC) -> bool:
    """A trivial resolution applies when the attacker overwhelmingly outclasses
    a peaceful, unarmed NPC. No combat round is needed."""
    if target_npc.disposition not in (NPCDisposition.PEACEFUL, NPCDisposition.NEUTRAL, NPCDisposition.FRIENDLY):
        return False
    # NPCs without combat stats or with very low HP qualify
    if target_npc.max_hp >= 10:
        return False
    # Check at least one PC in the session has a level >= 1 (always true for MVP)
    # Future: compare attacker.level vs target threat
    return True
```

Et dans le bootstrap (étape 3 du Lot C), au lieu de `pass`, faire :

```python
if self._should_trivial_resolve(target_npc):
    result = trivial_resolve(attacker_pc, target_npc, weapon=attacker_pc.equipped_weapon)
    self._handle_npc_death(target_npc, killer=attacker_pc, result=result)
    # Build a synthetic ValidationResult OK and a synthetic mechanics_text 
    # so the rest of the pipeline narrates the kill instead of starting combat
    self._trivial_kill_mechanics = result.description
    return ValidationResult(is_valid=True)
```

Et adapter `_resolve_mechanics` pour, si `self._trivial_kill_mechanics` est set, le retourner directement.

### Étape 4 — `_handle_npc_death`

Méthode nouvelle dans `ActionPipeline` (ou helper dans `bot/world_navigation.py` ou dans `bot/game_session.py`) :

```python
def _handle_npc_death(self, npc: NPC, killer: Character, result: TrivialResolveResult) -> None:
    """Propagate an NPC death across world state."""
```

Logique :
1. `npc.kill()` (déjà fait par trivial_resolve, mais idempotent).
2. Persister via `npc_repo.update(npc)`.
3. Retirer le nom du NPC de `self.location.npcs_present` et persister la location.
4. Retirer aussi de `self.npcs` (filtrage in-memory, pour que le scene context suivant ne le mentionne plus).
5. Ajouter un world_fact : `world_facts_repo.add(WorldFact(text=f"{npc.name} a été tué(e) par {killer.name} dans {self.location.name}.", campaign_id=self.campaign_id))`. Si le module `world/facts.py` n'existe pas, créer un fichier markdown append-only `logs/campaigns/{id}_facts.md` en attendant.
6. Logger dans la story bible : `story_bible_logger.log_event(campaign_id, f"⚔️ MEURTRE — {killer.name} a tué {npc.name}.", turn=current_turn)`. Étendre `bot/story_bible_logger.py` si besoin pour exposer un `log_event(text)` distinct de `log_turn`.
7. **Témoins HOSTILE** : pour chaque autre NPC dans `self.location.npcs_present` avec `disposition == FRIENDLY`, basculer sa disposition à `HOSTILE` envers le killer. Persister.
8. Logger `INFO bot.action_pipeline NPC killed campaign={id} npc={...} killer={...} witnesses_turned_hostile={n}`.

### Étape 5 — Tests

- `tests/test_combat.py` (étendu) : tests unitaires sur `trivial_resolve` :
  - Touche → dégâts → mort.
  - Touche → dégâts → survit (si HP > damage).
  - Raté → pas de dégâts.
- `tests/scenarios/test_trivial_npc_kill.py` (nouveau) :
  1. Setup : session avec 1 PJ niveau 1 + 1 NPC pacifique HP=4 disposition=PEACEFUL + 1 NPC témoin disposition=FRIENDLY.
  2. Action : pipeline.process(« je tue le villageois »).
  3. Assert : pas de `combat_state` créé, le NPC tué a `is_alive=False`, il est retiré de `location.npcs_present`, un world_fact a été ajouté, le témoin a basculé HOSTILE.
  4. Vérifier le contenu narré : doit mentionner la mort, pas un combat round par round.
- `tests/test_story_bible_logger.py` (existe déjà) : ajouter cas `log_event`.

## Critère de succès

- Tests verts.
- `uv run pytest` global vert.
- `uv run ruff check . && uv run mypy .` verts.
- Test live tester bot :
  1. Lancer une campagne, attendre l'embed scène.
  2. `@bot je tue Jeanne` → narrative embed mentionne le coup décisif et la mort, **pas** d'embed combat.
  3. `@bot regarde autour` → l'embed scène suivant ne liste plus Jeanne.
  4. Lire `logs/campaigns/{id}.md` : trouver l'événement « MEURTRE — ... a tué Jeanne ».
  5. `@bot parle au Père Thomas` → le narrateur doit mentionner sa nouvelle hostilité (vu que Père Thomas était friendly et témoin).

## Hors scope

- **Ne pas** retoucher l'entity resolver (Lot B fait), ni le bootstrap (Lot C fait — tu y branches juste).
- **Ne pas** créer de système de moralité / alignement / karma global. Juste : témoins → HOSTILE.
- **Ne pas** propager le meurtre à toute la ville (futurs PNJ générés ailleurs ne savent pas). Just ce qui est dans la même location.
- **Ne pas** gérer la résurrection ni les sorts de soin sur PNJ morts.
- **Ne pas** déclencher de quête « les autorités sont à votre poursuite » — laisser ça à la story director.

## Notes de l'agent

> À remplir avant la fin de session : commit hash, blocages, observations utiles pour les lots suivants.

- **Statut** : DONE (sur main, à committer ; HEAD parent : `5775aaf`).
- **Implémentation** :
  - `world/npc.py` — helper `NPC.kill()` idempotent (le champ `is_alive` existait déjà).
  - `engine/combat.py` — nouveau `TrivialResolveResult` (Pydantic, comme les autres Result du module) + `trivial_resolve(attacker, npc, weapon)` qui jette 1d20+STR+prof contre l'AC, applique 1d8/1d4+STR, mute `npc.hp` et appelle `npc.kill()`. **Aucun `CombatState` créé.**
  - `bot/story_bible_logger.py` — nouvelle méthode `log_event(text, *, turn_number=None)` indépendante de `log_turn`.
  - `bot/action_pipeline.py` :
    - Champs `db_factory` (Callable optionnel) et `_trivial_kill_mechanics`.
    - `_should_trivial_resolve` : True si NPC vivant, disposition non-HOSTILE/UNFRIENDLY, max_hp < 10.
    - `_trivial_kill` lookup attacker via `session.characters`, arme via `inventories[uid].equipped[MAIN_HAND]`, appelle `trivial_resolve`, propage la mort si kill.
    - `_handle_npc_death` : kill, retire de `location.npcs_present` + `self.npcs`, flip témoins FRIENDLY/ALLIED → HOSTILE, persiste via `db_factory()` (NPCRepository.update + LocationRepository.update + commit), append `logs/campaigns/{id}_facts.md`, story bible `log_event`, log INFO.
    - `_resolve_mechanics` retourne directement `_trivial_kill_mechanics` si set, donc le narrator décrit la mort au lieu d'un stub Attack.
    - `_validate` retourne `ValidationResult(is_valid=True)` après une résolution triviale (pas de combat_state à valider).
  - `bot/cogs/action_handler.py` — passe `db_factory=self.bot.db_factory` au pipeline.
- **Divergences vs brief** :
  - Pas de `NPCDisposition.PEACEFUL` dans l'enum. On utilise NEUTRAL/FRIENDLY/ALLIED comme cibles légitimes ; HOSTILE/UNFRIENDLY exclus.
  - Pas de `world/facts.py` ni `WorldFactRepo` créés — on garde le fallback markdown append-only `logs/campaigns/{id}_facts.md` (KISS, comme prévu).
  - Pas extrait `_get_equipped_weapon` du combat cog : la lookup directe `inventories[uid].equipped[MAIN_HAND]` est triviale et locale au pipeline, pas d'over-engineering.
- **Tests** :
  - `tests/test_combat.py::TestTrivialResolve` : 4 tests (kill, survive, miss nat-1, kill idempotent).
  - `tests/scenarios/test_trivial_npc_kill.py` (nouveau) : scénario complet (Aldric tue Jeanne, Père Thomas → HOSTILE, world fact écrit, story bible logge, pas de combat_state).
  - `tests/test_story_bible_logger.py::TestLogEvent` : 2 tests (avec et sans turn_number).
  - **Adapté** `tests/scenarios/test_attack_bootstrap_combat.py` : Jeanne passe à hp=15 pour rester sur la branche bootstrap (sinon mon `_should_trivial_resolve` la routerait en trivial kill).
- **Quality gates** :
  - `uv run pytest tests/test_combat.py tests/scenarios/test_trivial_npc_kill.py tests/test_story_bible_logger.py tests/scenarios/test_attack_bootstrap_combat.py` → **67 passed**.
  - `uv run pytest --deselect tests/scenarios/test_free_text_exploration.py` → **1178 passed**. (Le fichier dé-sélectionné est cassé pour des raisons indépendantes : Lot D `new_beat` MagicMock → ValidationError pydantic, présent avant Lot E.)
  - `uv run ruff check .` → All checks passed.
  - `uv run mypy` sur les fichiers touchés → 0 erreurs nouvelles (les 208 erreurs globales sont pré-existantes dans `scenario_runner.py`, `action_handler_cog`, `combat_scenarios`, `character_create_view`, etc.).
- **À signaler à Lot suivant / suivi** :
  - Le hook `_should_trivial_resolve` est maintenant *non*-trivial (max_hp < 10 + dispo passive). Tout test qui voulait du bootstrap combat sur un PNJ < 10 PV doit soit donner plus de PV soit le rendre HOSTILE.
  - Le live test (Discord) reste à faire (Ollama indispo dans la sandbox). Procédure dans la section « Critère de succès » ci-dessus.
