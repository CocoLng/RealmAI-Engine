# Task 31 — ActionPipeline : dispatch combat-aware

**Phase** : 3 — Validation & pipeline
**Dépendances** : [20](20_combat_entry_module.md), [30](30_strict_combat_validators.md), [40](40_interpreter_lethal_intent.md)
**Coordinateur** : [tasks/combat/README.md](README.md)

## Contexte

Avec les fondations en place (combat entry, validators stricts, flag d'intention létale), il faut refondre `ActionPipeline._validate` pour :

1. **Détecter systématiquement** si une action déclenche un combat via `detect_combat_trigger`.
2. Si oui, appeler `enter_combat` → passer `start_combat` (qui roule l'initiative et applique la surprise).
3. **Auto-convertir MOVE → FLEE** quand le combat est actif (décision validée dans le plan coordinateur section 3.1).
4. **Dispatcher sur le bon validateur** selon que le combat est actif ou non.
5. **Détecter les intentions létales** via `InterpretedAction.is_lethal_intent` (flag fourni par tâche [40](40_interpreter_lethal_intent.md)) et les router vers le combat entry.

## Scope

1. Refondre `ActionPipeline._validate` selon l'organigramme ci-dessous.
2. Stocker la destination MOVE originale dans `self._pending_flee_destination` pour que `_resolve_flee` (tâche [32](32_flee_resolution.md)) puisse l'utiliser en cas de succès.
3. Poster un embed `⚔️ Combat commence` quand un combat est bootstrap (via `build_combat_start_embed` de la tâche [61](61_combat_start_embed.md), qui peut être un stub au moment de l'implémentation de [31]).

## Organigramme décisionnel

```
_validate(action)
  │
  ├─► 1. Si combat actif ET action_type == MOVE
  │       → convertir en FLEE (stocker target_name)
  │       → poursuivre avec FLEE
  │
  ├─► 2. Si combat_state is None
  │       ├─► détecter trigger via detect_combat_trigger(action, session)
  │       │   ├─► si trigger → enter_combat(trigger) → start_combat(combatants, trigger)
  │       │   │   → poster combat_start embed
  │       │   │   → continuer en mode combat
  │       │   └─► sinon → action_type in EXPLORATION_ACTION_TYPES ?
  │       │       ├─► oui → validate_exploration_action(action, combat_state=None)
  │       │       └─► non → mode combat requis mais pas de state → erreur
  │       └─► (pas de else, tout est couvert ci-dessus)
  │
  └─► 3. Si combat_state actif
          ├─► action_type in EXPLORATION_ACTION_TYPES ?
          │   → validate_exploration_action(action, combat_state)
          │     (rejette la plupart ; laisse LOOK/QUESTION/IMPROVISE passer)
          └─► sinon → validate_action(action, combat_state) — le dispatcher combat
```

## Fichiers à modifier

- [bot/action_pipeline.py](../../bot/action_pipeline.py) — méthode `_validate`, nouvelles méthodes helper.

## Implémentation — esquisse

```python
# bot/action_pipeline.py

from bot.combat_entry import detect_combat_trigger, enter_combat
from engine.combat import start_combat


def _validate(self, action: InterpretedAction) -> ValidationResult:
    """Convert InterpretedAction → Action and dispatch to the right validator.

    Dispatch logic:
    - If combat is active and action is MOVE → auto-convert to FLEE.
    - If no combat, try to detect a combat trigger and bootstrap if needed.
    - If combat active → combat validators.
    - If exploration → exploration validators.
    """
    eng_action = Action(
        actor_name=action.actor_name,
        action_type=action.action_type,
        target_name=action.target_name,
        weapon_name=action.weapon_name,
        spell_name=action.spell_name,
        item_name=action.item_name,
    )

    # --- 1. Auto-convert MOVE → FLEE in active combat ---
    if (
        eng_action.action_type == ActionType.MOVE
        and self.combat_state is not None
        and self.combat_state.is_active
    ):
        logger.info(
            "MOVE auto-converted to FLEE campaign=%s actor=%s target=%s",
            self.campaign_id, self.actor_name, eng_action.target_name,
        )
        self._pending_flee_destination = eng_action.target_name
        action.action_type = ActionType.FLEE
        eng_action = eng_action.model_copy(
            update={"action_type": ActionType.FLEE, "target_name": None},
        )
        # Fall through to combat validators

    # --- 2. If no combat, try to detect a trigger ---
    if self.combat_state is None or not self.combat_state.is_active:
        trigger = detect_combat_trigger(action, self.session) if self.session else None
        if trigger is not None:
            # Bootstrap combat via the new entry path
            from bot.combat_entry import CombatTriggerKind
            logger.info(
                "COMBAT bootstrapped kind=%s campaign=%s aggressor=%s enemies=%s",
                trigger.kind, self.campaign_id,
                trigger.aggressor_name, trigger.enemy_names,
            )
            combatants_state = enter_combat(
                self.session, trigger, db_factory=self.db_factory,
            )
            # Roll initiative (task 21) and apply surprise
            combatants_state = start_combat(
                combatants_state.combatants, trigger=trigger,
            )
            self.session.combat_state = combatants_state
            self.combat_state = combatants_state
            self._pending_combat_start_embed = (combatants_state, trigger)
            # Continue with combat validators below

    # --- 3. Dispatch to the right validator ---
    if self.combat_state is not None and self.combat_state.is_active:
        # Combat active
        if eng_action.action_type in EXPLORATION_ACTION_TYPES:
            return validate_exploration_action(
                eng_action, combat_state=self.combat_state,
            )
        return validate_action(eng_action, self.combat_state)

    # No combat — exploration path
    if eng_action.action_type in EXPLORATION_ACTION_TYPES:
        return validate_exploration_action(eng_action, combat_state=None)

    # Combat action with no combat state → trivial kill check or refuse
    if (
        eng_action.action_type == ActionType.ATTACK
        and eng_action.target_name is not None
        and self.npcs.get(eng_action.target_name) is not None
    ):
        target_npc = self.npcs[eng_action.target_name]
        if self._should_trivial_resolve(target_npc):
            self._trivial_kill(target_npc)
            return ValidationResult(is_valid=True)

    return ValidationResult(
        is_valid=False,
        error_message=(
            f"'{eng_action.action_type.value}' requires combat but no combat state"
        ),
    )
```

**Point d'attention — ordre des étapes 2 et 3 pour ATTACK** : si le joueur attaque un commoner, `detect_combat_trigger` doit retourner `None` (voir tâche [20](20_combat_entry_module.md) : `_is_combat_worthy`). On retombe alors sur le chemin trivial kill existant dans l'étape 3. Cohérent.

**Point d'attention — embed combat start** : le stockage de `self._pending_combat_start_embed` permet au caller (`bot/cogs/action_handler.py`) de poster l'embed au bon moment (après validation mais avant narration). Alternative : faire l'appel `channel.send` directement dans `_validate`, mais ça couple le validateur à Discord. Préférer le stockage.

## Acceptance criteria

- [ ] Pipeline convertit MOVE → FLEE en combat actif, et stocke la destination.
- [ ] Pipeline détecte les triggers via `detect_combat_trigger` et bootstrap correctement.
- [ ] Après bootstrap, le validateur combat est appelé (pas le exploration).
- [ ] Trivial kill reste possible pour les commoners réels.
- [ ] Le pipeline stocke `_pending_combat_start_embed` pour que le caller le poste.
- [ ] Aucun double-bootstrap : un combat déjà actif n'appelle pas `detect_combat_trigger`.

## Tests à ajouter

Dans `tests/bot/test_action_pipeline.py` :

- `test_pipeline_autoconverts_move_to_flee_in_active_combat`.
- `test_pipeline_stores_flee_destination`.
- `test_pipeline_detects_attack_trigger_and_bootstraps`.
- `test_pipeline_detects_lethal_intent_and_bootstraps`.
- `test_pipeline_no_bootstrap_on_neutral_action`.
- `test_pipeline_trivial_kill_still_works_for_commoner`.
- `test_pipeline_trivial_kill_blocked_for_villain` — regression de tâche [00](00_bugfix_villain_trivial_resolve.md).
- `test_pipeline_dispatches_to_combat_validator_when_active`.
- `test_pipeline_dispatches_to_exploration_validator_when_inactive`.
- `test_pipeline_exploration_rejected_in_combat_except_info_actions`.

## Hors scope

- **Ne pas** implémenter `_resolve_flee` — tâche [32](32_flee_resolution.md).
- **Ne pas** poster l'embed combat start (juste le stocker) — l'envoi est fait par le caller.
- **Ne pas** toucher à `_resolve_mechanics` — il continue de dispatcher normalement, sauf ajout pour FLEE (tâche 32).
- **Ne pas** détecter les provocations sociales — tâche [81](81_social_resolution_mid_combat.md).

## Validation finale

```bash
uv run pytest tests/bot/test_action_pipeline.py -v
uv run ruff check bot/action_pipeline.py
uv run mypy bot/action_pipeline.py
```
