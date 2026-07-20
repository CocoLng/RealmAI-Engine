# Task 11 — Librairie d'archétypes NPCs

**Phase** : 1 — Fondations NPC & engine
**Dépendances** : [10](10_npc_stat_block_model.md)
**Coordinateur** : [tasks/combat/README.md](README.md)

## Contexte

Pour que le moteur de combat ait des adversaires crédibles sans dépendre du LLM à chaque création, on précalcule une **librairie d'archétypes** couvrant les tiers minion/elite/boss. Chaque archétype est une fonction Python qui retourne un `NPCStatBlock` valide, directement utilisable.

Le world generator appellera cette librairie via le nom d'archétype détecté (`"captain"`, `"brute"`, etc.). Le villain d'arc, lui, aura son stat block **custom généré par le LLM** (tâche [42](42_arc_generator_villain_stat_block.md)) — mais en fallback ou pour les elites de scène, la librairie suffit.

## Scope

Créer `engine/npc_library.py` avec :

1. Un dict `ARCHETYPE_BUILDERS: dict[str, Callable[[], NPCStatBlock]]`.
2. Au moins **10 archétypes** couvrant les besoins actuels :
   - **Minion tier** : `commoner`, `guard`, `bandit`, `cultist`
   - **Elite tier** : `soldier`, `captain`, `brute`, `mage`, `assassin`, `shaman`
   - **Boss tier fallback** : `generic_boss` (utilisé si l'arc generator n'a pas produit un stat_block custom pour le villain)
3. Une fonction `get_archetype(name: str) -> NPCStatBlock` qui retourne une copie fraîche (pas de shared state).
4. Une fonction `list_archetypes() -> list[str]` pour la documentation et les prompts LLM.

Chaque archétype doit inclure :
- `tier` + `archetype` nom
- `multiattack_count` (1 minion, 2 elite, 3 boss)
- 1-2 `attacks` basiques (cohérent avec l'arme de l'archétype)
- Pour elite+ : **1 signature ability** (tirée de la liste suggérée dans le coordinateur)
- `behavior_profile` approprié
- `aggression_threshold`
- `legendary_points_per_round > 0` uniquement pour boss

## Suggestions de signatures par archétype

| Archétype | Tier | Signature | Effet |
|---|---|---|---|
| `commoner` | minion | — | — |
| `guard` | minion | — | — |
| `bandit` | minion | — | — |
| `cultist` | minion | — | — |
| `soldier` | elite | "Shield Wall" | +2 AC aux alliés en zone adjacente, 1 round |
| `captain` | elite | "Rally" | heal 1d8+CHA + remove Frightened sur alliés en zone |
| `brute` | elite | "Reckless Charge" | move + attack avec advantage, attaques contre lui avec advantage jusqu'à son prochain tour |
| `mage` | elite | "Counterspell" | réaction, annule sort niveau ≤ 3 lancé par ennemi en range |
| `assassin` | elite | "Death Strike" | 1/combat, critique automatique sur cible avec `Surprised` |
| `shaman` | elite | "Spirit Guardians" | concentration, aoe 2d8 radiant sur ennemis entrant en zone |
| `generic_boss` | boss | 3 custom | multiattack 3, 3 legendary actions génériques, 2 phases |

Stats HP/AC par tier (baseline) :
- Minion : HP 8–15, AC 12–14
- Elite : HP 22–35, AC 14–16
- Boss : HP 45–70, AC 16–18

## Fichiers à créer

- **Créer** `engine/npc_library.py`

## Implémentation — esquisse

```python
# engine/npc_library.py
from collections.abc import Callable

from engine.inventory import DamageType
from engine.npc_stat_block import (
    BehaviorProfile, LegendaryAction, NPCAttack, NPCStatBlock, NPCTier,
    PhaseTransition, SignatureAbility, SignatureAbilityEffect,
)


def _build_commoner() -> NPCStatBlock:
    return NPCStatBlock(
        tier=NPCTier.MINION,
        archetype="commoner",
        multiattack_count=1,
        attacks=[
            NPCAttack(
                name="Club",
                damage_dice="1d4",
                damage_type=DamageType.BLUDGEONING,
                to_hit_bonus=1,
                range_type="melee",
            ),
        ],
        behavior_profile=BehaviorProfile.DEFENSIVE,
        aggression_threshold=18,
    )


def _build_captain() -> NPCStatBlock:
    return NPCStatBlock(
        tier=NPCTier.ELITE,
        archetype="captain",
        multiattack_count=2,
        attacks=[
            NPCAttack(
                name="Longsword",
                damage_dice="1d8+3",
                damage_type=DamageType.SLASHING,
                to_hit_bonus=5,
                range_type="melee",
            ),
        ],
        signature_abilities=[
            SignatureAbility(
                name="Rally",
                description="Encourages allies — heals nearby allies and removes Frightened.",
                usage="per_combat",
                uses_remaining=1,
                action_cost="action",
                effects=[
                    SignatureAbilityEffect(
                        kind="heal",
                        dice="1d8+3",
                        target_scope="all_enemies",  # actually "all_allies_in_zone"; see note
                    ),
                ],
            ),
        ],
        behavior_profile=BehaviorProfile.SUPPORT,
        aggression_threshold=12,
    )


# ... similar builders for each archetype ...


ARCHETYPE_BUILDERS: dict[str, Callable[[], NPCStatBlock]] = {
    "commoner": _build_commoner,
    "guard": _build_guard,
    "bandit": _build_bandit,
    "cultist": _build_cultist,
    "soldier": _build_soldier,
    "captain": _build_captain,
    "brute": _build_brute,
    "mage": _build_mage,
    "assassin": _build_assassin,
    "shaman": _build_shaman,
    "generic_boss": _build_generic_boss,
}


def get_archetype(name: str) -> NPCStatBlock:
    """Return a fresh stat block for the named archetype.

    Raises ``KeyError`` if the archetype does not exist — callers must
    either guard with ``name in ARCHETYPE_BUILDERS`` or catch and default
    to ``commoner``.
    """
    return ARCHETYPE_BUILDERS[name]()


def list_archetypes() -> list[str]:
    return sorted(ARCHETYPE_BUILDERS.keys())
```

**Note sur `target_scope`** : le modèle Pydantic de la tâche 10 définit `target_scope` avec des valeurs limitées. Si l'archétype a besoin d'un `target_scope` non représenté (ex : "all_allies_in_zone"), tu as deux options : (a) étendre l'enum dans la tâche 10 via un petit PR d'ajustement, (b) documenter dans `description` que l'effet cible les alliés et laisser la résolution mécanique (tâches 22/51) interpréter. Préférer (a) si plusieurs archétypes en ont besoin.

## Acceptance criteria

- [ ] `engine/npc_library.py` existe avec les 11 builders listés.
- [ ] Chaque builder retourne un `NPCStatBlock` qui passe la validation Pydantic sans erreur.
- [ ] `get_archetype(name)` retourne une **nouvelle instance** à chaque appel (pas de shared state — utile pour le `uses_remaining` qui se décrémente en combat).
- [ ] Les HP/AC de chaque archétype respectent les fourchettes par tier.
- [ ] Chaque elite a au moins 1 signature ability.
- [ ] `generic_boss` a `legendary_points_per_round=3`, 3 legendary actions, 2 phases.
- [ ] `list_archetypes()` retourne une liste triée.

## Tests à ajouter

Dans `tests/test_npc_library.py` (nouveau) :

- `test_all_archetypes_build_successfully` — pour chaque clé dans `ARCHETYPE_BUILDERS`, appeler le builder et vérifier que le `NPCStatBlock` est valide.
- `test_archetype_tier_consistency` — pour chaque archétype, vérifier que `tier` correspond à la catégorie attendue.
- `test_get_archetype_returns_fresh_instance` — appeler `get_archetype("captain")` deux fois, décrémenter `uses_remaining` sur l'une, vérifier que l'autre n'est pas affectée.
- `test_elite_has_signature` — chaque elite archetype a au moins 1 signature ability.
- `test_boss_has_legendary_actions` — `generic_boss` a 3 legendary actions et phases.
- `test_minion_has_no_signature` — minions n'ont pas de signature ability (cohérence design).
- `test_get_unknown_archetype_raises` — `get_archetype("nonexistent")` raise `KeyError`.
- `test_list_archetypes_sorted` — retour trié.

## Hors scope

- **Ne pas** intégrer avec le world generator — tâche [41](41_world_generator_zones_triggers.md).
- **Ne pas** utiliser ces archétypes dans `scene_hydration` — tâche [43](43_hydration_dispatches_tier.md).
- **Ne pas** implémenter la résolution des signatures en combat — tâche [51](51_elite_behavior_profiles.md).
- **Ne pas** créer d'archétypes "legendary/unique" (Vellus & co) — ces stat blocks sont générés par l'arc generator (tâche 42), pas via cette librairie.

## Validation finale

```bash
uv run pytest tests/test_npc_library.py -v
uv run ruff check engine/npc_library.py
uv run mypy engine/npc_library.py
```
