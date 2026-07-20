# Task 20 — Module d'entrée en combat

**Phase** : 2 — Moteur de combat
**Dépendances** : [10](10_npc_stat_block_model.md), [11](11_npc_library_archetypes.md), [13](13_surprised_condition.md)
**Coordinateur** : [tasks/combat/README.md](README.md)

## Contexte

Aujourd'hui, le combat ne s'active qu'**une seule façon** : via `_bootstrap_combat_against` appelé depuis `ActionPipeline._validate` quand un joueur tape explicitement `(Attack)`. Pas de support pour :

- **Ambush** : un joueur `(Interact)` avec un piège, N ennemis apparaissent et attaquent.
- **Provocation** : un joueur insulte un garde, le garde devient hostile et attaque.
- **Scripted beat entry** : beat 1 est un combat beat scripté avec ennemis pré-positionnés.
- **Intention létale implicite** : `(Improvise) je sors mon épée contre le marchand` doit être traité comme une attaque.

De plus, le bootstrap actuel **n'embarque pas la party complète** — il assemble seulement l'attaquant et la cible plus les autres PCs comme "followers". Il faut un vrai modèle party-wide.

## Scope

Créer un nouveau module `bot/combat_entry.py` exposant :

1. `CombatTrigger` (Pydantic) — représente **pourquoi** le combat démarre : type, cible(s), aggresseur, quel camp a l'initiative (pour décider de la surprise).
2. `detect_combat_trigger(action, session) -> CombatTrigger | None` — fonction pure qui examine une action interprétée et un state de session, et retourne un trigger si l'action doit démarrer un combat. Retourne `None` sinon.
3. `enter_combat(session, trigger, db_factory) -> CombatState` — construit un `CombatState` party-wide, persiste sur `session.combat_state`.

**L'initiative elle-même** (rolls, surprise application) est la tâche [21](21_initiative_and_surprise.md) ; ici on structure le trigger et on assemble la liste des combattants.

## Fichiers à créer/modifier

- **Créer** `bot/combat_entry.py`

## Implémentation — esquisse

```python
# bot/combat_entry.py
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from ai.models import InterpretedAction
from engine.combat import CombatState, Combatant
from engine.validators import ActionType
from world.npc import NPC, NPCDisposition

if TYPE_CHECKING:
    from bot.game_session import GameSession


class CombatTriggerKind(StrEnum):
    PLAYER_ATTACK = "player_attack"          # explicit Attack action
    LETHAL_INTENT = "lethal_intent"           # Improvise/text parsed as lethal
    AMBUSH = "ambush"                         # interact with a trap
    PROVOCATION = "provocation"               # social action pushes NPC past threshold
    SCRIPTED_BEAT = "scripted_beat"           # combat beat at campaign start / progression


class InitiativeSide(StrEnum):
    PLAYERS = "players"     # PCs go first, NPCs start SURPRISED
    NPCS = "npcs"           # NPCs go first, PCs start SURPRISED
    BOTH_READY = "both_ready"  # normal initiative roll, no surprise


class CombatTrigger(BaseModel):
    kind: CombatTriggerKind
    aggressor_name: str
    """Name of the PC or NPC that triggered the combat."""
    enemy_names: list[str] = Field(min_length=1)
    """NPCs that should be added as enemies to the combat."""
    surprise_side: InitiativeSide
    """Which side has surprise — see cases 1/2/3 in the coordinator plan."""
    narrative_hint: str = ""
    """Short text the narrator can use to open the combat description."""


def detect_combat_trigger(
    action: InterpretedAction,
    session: "GameSession",
) -> CombatTrigger | None:
    """Examine an interpreted action and decide if it triggers combat.

    Returns None if the action does not trigger combat (most actions).
    """
    # CASE 1a — Explicit Attack action
    if action.action_type == ActionType.ATTACK:
        target = _resolve_target_npc(action, session)
        if target is None:
            return None
        if not _is_combat_worthy(target):
            return None  # commoner trivial kill path handles this
        return CombatTrigger(
            kind=CombatTriggerKind.PLAYER_ATTACK,
            aggressor_name=action.actor_name,
            enemy_names=[target.name],
            surprise_side=_compute_surprise_for_attack(target, session),
            narrative_hint=f"{action.actor_name} attaque {target.name}.",
        )

    # CASE 1b — Lethal intent via Improvise/text (depends on task 40)
    if (
        action.action_type == ActionType.IMPROVISE
        and getattr(action, "is_lethal_intent", False)
    ):
        target = _resolve_target_npc(action, session)
        if target is None or not _is_combat_worthy(target):
            return None
        return CombatTrigger(
            kind=CombatTriggerKind.LETHAL_INTENT,
            aggressor_name=action.actor_name,
            enemy_names=[target.name],
            surprise_side=InitiativeSide.PLAYERS,  # unannounced attack
        )

    # CASE 2 — Interact with a combat trigger
    if action.action_type == ActionType.INTERACT:
        location = session.current_location
        if location is None or not hasattr(location, "combat_triggers"):
            return None
        trigger_def = location.combat_triggers.get(action.target_name or "")
        if trigger_def is None:
            return None
        return CombatTrigger(
            kind=CombatTriggerKind.AMBUSH,
            aggressor_name=trigger_def.spawn_npcs[0] if trigger_def.spawn_npcs else "?",
            enemy_names=trigger_def.spawn_npcs,
            surprise_side=InitiativeSide.NPCS,  # ambush surprises the party
            narrative_hint=trigger_def.reveal_narration or "",
        )

    # CASE 3 — Social provocation
    if action.action_type == ActionType.TALK:
        # Reserved for task 81 — social provocation is resolved during talk,
        # and if it exceeds the aggression_threshold, a trigger is produced.
        # For the initial implementation, return None here and let task 81
        # add the logic.
        return None

    return None


def enter_combat(
    session: "GameSession",
    trigger: CombatTrigger,
    db_factory,
) -> CombatState:
    """Build a party-wide CombatState from a validated trigger.

    - All PCs in the session join the combat.
    - All NPCs in trigger.enemy_names are resolved and added as enemies.
    - If a PC is ambushed (trigger.surprise_side == NPCS), the PCs are
      tagged with SURPRISED (handled in task 21).
    - If NPCs are surprised (trigger.surprise_side == PLAYERS), they get
      SURPRISED.
    - Initiative order is NOT rolled here — task 21 owns that.

    The returned CombatState is persisted on ``session.combat_state`` and
    also to the DB via db_factory.
    """
    from bot.cogs.combat import build_pc_combatants, build_npc_combatant

    pcs = build_pc_combatants(session)
    enemies: list[Combatant] = []
    for name in trigger.enemy_names:
        npc = session.npcs.get(name) if session.npcs else None
        if npc is None:
            continue
        enemies.append(build_npc_combatant(npc))

    if not enemies:
        raise ValueError(
            f"Cannot enter combat: no valid enemies found for trigger {trigger!r}"
        )

    # Provisional CombatState — task 21 will re-order via initiative and
    # apply SURPRISED conditions.
    state = CombatState(
        combatants=pcs + enemies,
        round_number=1,
        current_turn_index=0,
        is_active=True,
    )
    session.combat_state = state
    # Persist (see task 22 for details on DB schema)
    return state


# ---------- helpers ----------

def _resolve_target_npc(
    action: InterpretedAction,
    session: "GameSession",
) -> NPC | None:
    if action.target_name is None or not session.npcs:
        return None
    return session.npcs.get(action.target_name)


def _is_combat_worthy(npc: NPC) -> bool:
    """True if this NPC is worth a full combat — not a trivially killable commoner."""
    if npc.stat_block is not None:
        return True
    # Legacy NPCs without stat_block: hostile or strong ones go to combat
    if npc.disposition == NPCDisposition.HOSTILE:
        return True
    if npc.max_hp >= 10 or npc.ac > 12:
        return True
    return False


def _compute_surprise_for_attack(
    target: NPC,
    session: "GameSession",
) -> InitiativeSide:
    """An attack against a non-hostile NPC gives players surprise.
    An attack against an already-hostile NPC is face-to-face (case 3).
    """
    if target.disposition in (NPCDisposition.HOSTILE, NPCDisposition.UNFRIENDLY):
        return InitiativeSide.BOTH_READY
    return InitiativeSide.PLAYERS
```

## Acceptance criteria

- [ ] `bot/combat_entry.py` existe avec les 3 exports principaux (`CombatTrigger`, `detect_combat_trigger`, `enter_combat`).
- [ ] `detect_combat_trigger` retourne correctement :
  - ATTACK sur NPC fort → `PLAYER_ATTACK`, surprise selon disposition.
  - ATTACK sur commoner → `None` (le trivial path s'en charge).
  - IMPROVISE avec `is_lethal_intent=True` → `LETHAL_INTENT`.
  - INTERACT sur un combat_trigger → `AMBUSH`, surprise NPCs.
  - Action neutre → `None`.
- [ ] `enter_combat` assemble une `CombatState` avec TOUS les PCs et les enemies demandés.
- [ ] Raise `ValueError` si aucun enemy n'est trouvable.
- [ ] `session.combat_state` est set.
- [ ] Pas d'appel LLM dans ce module — pure Python.

## Tests à ajouter

Dans `tests/bot/test_combat_entry.py` (nouveau) :

- `test_detect_attack_hostile_npc_returns_both_ready` — attaque contre HOSTILE → case 3.
- `test_detect_attack_neutral_npc_returns_player_surprise` — attaque contre NEUTRAL → case 1.
- `test_detect_attack_commoner_returns_none` — attaque contre commoner → None (trivial path).
- `test_detect_improvise_lethal_intent` — IMPROVISE + flag → trigger.
- `test_detect_improvise_without_lethal_flag_returns_none`.
- `test_detect_interact_on_trap_trigger` — INTERACT sur mechanism flagué → AMBUSH.
- `test_detect_look_returns_none` — LOOK ne déclenche jamais.
- `test_enter_combat_builds_party_wide_state` — session avec 2 PCs, 1 enemy → CombatState avec 3 combatants.
- `test_enter_combat_raises_when_no_enemies_found` — trigger avec enemy_names=["ghost"] mais pas dans session.npcs.
- `test_enter_combat_persists_on_session` — après appel, `session.combat_state is state`.

## Hors scope

- **Ne pas** roll initiative — tâche [21](21_initiative_and_surprise.md).
- **Ne pas** appliquer la condition `SURPRISED` — tâche [21](21_initiative_and_surprise.md).
- **Ne pas** détecter les provocations sociales (CASE 3 for TALK) — tâche [81](81_social_resolution_mid_combat.md).
- **Ne pas** implémenter `combat_triggers` sur `Location` — tâche [41](41_world_generator_zones_triggers.md). Cette tâche peut utiliser un stub `dict[str, CombatTriggerDef]` qui sera progressivement rempli.
- **Ne pas** changer le dispatch dans `action_pipeline.py` — tâche [31](31_action_pipeline_combat_dispatch.md).

## Validation finale

```bash
uv run pytest tests/bot/test_combat_entry.py -v
uv run ruff check bot/combat_entry.py
uv run mypy bot/combat_entry.py
```
