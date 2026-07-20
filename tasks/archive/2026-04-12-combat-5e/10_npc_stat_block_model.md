# Task 10 — NPCStatBlock : modèle data pour NPCs de combat

**Phase** : 1 — Fondations NPC & engine
**Dépendances** : aucune
**Coordinateur** : [tasks/combat/README.md](README.md)

## Contexte

Le `NPC` actuel ([world/npc.py](../../world/npc.py)) est délibérément léger — il représente un PNJ narratif (`name, race, hp, ac, disposition, personality, secrets, knowledge, dialogue_history`). C'est suffisant pour un commoner avec qui on parle.

Pour un combat fidèle à D&D 5e, il faut un **vrai stat block** : inventaire, liste d'attaques, capacités signatures, behavior profile, multiattack, legendary actions, phases HP. Ce stat block doit être **optionnel** sur le `NPC` (les commoners n'en ont pas) et **persistable** en JSON côté DB.

## Scope

Créer un nouveau module `engine/npc_stat_block.py` avec les modèles Pydantic suivants :

- `NPCAttack` — une action d'attaque nommée (nom, dés de dégât, damage type, range, to-hit bonus).
- `SignatureAbility` — une capacité unique utilisée par les elites et boss (nom, description, usage limit, effects).
- `LegendaryAction` — une action légendaire (nom, cost in points, effects).
- `PhaseTransition` — un trigger HP + narrative cue + nouvelle signature débloquée + buffs.
- `BehaviorProfile` — enum `AGGRESSIVE`, `DEFENSIVE`, `SUPPORT`, `TACTICAL`.
- `NPCTier` — enum `MINION`, `ELITE`, `BOSS`.
- `NPCStatBlock` — le conteneur : tier, attacks, signatures, legendary actions, phases, behavior profile, multiattack count, aggression_threshold, inventory refs.

Étendre `world/npc.py::NPC` avec un champ optionnel `stat_block: NPCStatBlock | None = None`.

Étendre `db/models.py::NPCRow` + `db/mappers.py` + `db/repositories/npc_repo.py` pour persister le stat_block en JSON (colonne `stat_block_json: str | None`).

## Fichiers à créer/modifier

- **Créer** `engine/npc_stat_block.py`
- **Modifier** [world/npc.py](../../world/npc.py) — ajouter `stat_block`
- **Modifier** [db/models.py](../../db/models.py) — ajouter colonne JSON sur la table NPC
- **Modifier** [db/mappers.py](../../db/mappers.py) — sérialiser/désérialiser
- **Modifier** [db/repositories/npc_repo.py](../../db/repositories/npc_repo.py) — roundtrip
- **Créer** migration Alembic si le projet en utilise, sinon le schéma SQLite auto-update suffit (vérifier la convention du repo)

## Implémentation — esquisse

```python
# engine/npc_stat_block.py
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from engine.inventory import DamageType


class BehaviorProfile(StrEnum):
    AGGRESSIVE = "aggressive"
    DEFENSIVE = "defensive"
    SUPPORT = "support"
    TACTICAL = "tactical"


class NPCTier(StrEnum):
    MINION = "minion"
    ELITE = "elite"
    BOSS = "boss"


class NPCAttack(BaseModel):
    name: str
    damage_dice: str  # "1d8+2"
    damage_type: DamageType
    to_hit_bonus: int = 0
    range_type: Literal["melee", "ranged", "reach"] = "melee"
    range_value: int | None = None  # feet for ranged


class SignatureAbilityEffect(BaseModel):
    """Structured effect so the engine can resolve it deterministically."""
    kind: Literal[
        "damage", "heal", "condition", "move", "buff", "debuff", "aoe_damage"
    ]
    dice: str | None = None  # damage/heal
    damage_type: DamageType | None = None
    condition_name: str | None = None
    condition_duration_rounds: int | None = None
    save_ability: Literal["STR", "DEX", "CON", "INT", "WIS", "CHA"] | None = None
    save_dc: int | None = None
    target_scope: Literal["single", "zone", "all_enemies", "self"] = "single"


class SignatureAbility(BaseModel):
    name: str
    description: str
    usage: Literal["at_will", "per_combat", "per_day", "recharge_5_6"]
    uses_remaining: int | None = None
    is_reaction: bool = False
    action_cost: Literal["action", "bonus", "reaction"] = "action"
    effects: list[SignatureAbilityEffect] = Field(default_factory=list)


class LegendaryAction(BaseModel):
    name: str
    cost: int = Field(ge=1, le=3)
    description: str
    effects: list[SignatureAbilityEffect] = Field(default_factory=list)


class PhaseTransition(BaseModel):
    trigger_hp_percent: int = Field(ge=1, le=99)  # typically 50
    narrative_cue: str
    unlock_signatures: list[str] = Field(default_factory=list)
    attack_bonus: int = 0
    save_bonus: int = 0
    triggered: bool = False


class NPCStatBlock(BaseModel):
    tier: NPCTier
    archetype: str  # "commoner", "guard", "captain", "brute", "villain", etc.
    multiattack_count: int = Field(default=1, ge=1, le=5)
    attacks: list[NPCAttack] = Field(default_factory=list)
    signature_abilities: list[SignatureAbility] = Field(default_factory=list)
    legendary_actions: list[LegendaryAction] = Field(default_factory=list)
    legendary_points_per_round: int = Field(default=0, ge=0, le=5)
    phases: list[PhaseTransition] = Field(default_factory=list)
    behavior_profile: BehaviorProfile = BehaviorProfile.AGGRESSIVE
    aggression_threshold: int = Field(default=15, ge=1, le=30)
    """DC for social challenges before this NPC becomes hostile."""
```

Dans `world/npc.py::NPC`, ajouter :

```python
from engine.npc_stat_block import NPCStatBlock

class NPC(BaseModel):
    # ... existing fields ...
    stat_block: NPCStatBlock | None = None
```

Dans `db/models.py`, ajouter une colonne `stat_block_json: Mapped[str | None]`. Dans `db/mappers.py`, sérialiser via `model_dump_json()` et désérialiser via `NPCStatBlock.model_validate_json(...)`.

## Acceptance criteria

- [ ] `engine/npc_stat_block.py` existe avec tous les modèles listés.
- [ ] `NPC.stat_block` est optionnel, default `None`.
- [ ] Un `NPC` sans stat_block (commoner) s'instancie comme avant — pas de breaking change.
- [ ] Un `NPC` avec stat_block roundtrip correctement via `NPCRepository` (save → load → compare).
- [ ] `legendary_points_per_round=0` et `phases=[]` par défaut → un minion a juste `tier=MINION, attacks=[NPCAttack(...)]` et c'est tout.
- [ ] Validation Pydantic : `multiattack_count >= 1`, `phases[].trigger_hp_percent in [1, 99]`, etc.

## Tests à ajouter

Dans `tests/test_npc_stat_block.py` (nouveau) :

- `test_minion_stat_block_defaults` — un minion minimal (1 attack) construit sans erreur.
- `test_elite_stat_block_with_signature` — un elite avec 1 signature ability.
- `test_boss_stat_block_full` — un boss avec multiattack=3, 3 signatures, 3 legendary actions, 2 phases.
- `test_phase_transition_validation` — rejet si `trigger_hp_percent=0` ou `>= 100`.
- `test_npc_without_stat_block_still_valid` — regression : `NPC(name="X", ...)` sans stat_block fonctionne.
- `test_legendary_action_cost_bounds` — `cost` doit être 1-3.

Dans `tests/test_db_repos.py` :

- `test_npc_repository_roundtrips_stat_block` — sauvegarde et chargement avec stat_block, comparaison structurelle.
- `test_npc_repository_roundtrips_without_stat_block` — regression : commoner sans stat_block.

## Hors scope

- **Ne pas** peupler la librairie d'archétypes — tâche [11](11_npc_library_archetypes.md).
- **Ne pas** implémenter la résolution mécanique des `SignatureAbilityEffect` — tâche [22](22_multi_enemy_combat_state.md) ou [51](51_elite_behavior_profiles.md).
- **Ne pas** toucher à `scene_hydration` — tâche [43](43_hydration_dispatches_tier.md).
- **Ne pas** ajouter de champ à `story_arc.py` — tâche [42](42_arc_generator_villain_stat_block.md) se chargera d'attacher le stat_block du villain côté arc.

## Validation finale

```bash
uv run pytest tests/test_npc_stat_block.py tests/test_db_repos.py -v
uv run ruff check engine/npc_stat_block.py world/npc.py db/
uv run mypy engine/npc_stat_block.py world/npc.py db/
```
