# Task 82 — Test end-to-end Discord live

**Phase** : 8 — Fin de combat & intégration
**Dépendances** : **TOUTES les tâches précédentes**
**Coordinateur** : [tasks/combat/README.md](README.md)

## Contexte

Cette tâche est le **gate de fin** du chantier combat. Elle reproduit le scénario Mageta vs Vellus le Mentisseur avec le nouveau système complet et vérifie que **tous les comportements attendus** fonctionnent en live Discord via le MCP `discord-test`. Tant que ce test n'est pas vert, la feature n'est **pas considérée livrée**.

Pas de code produit ici : c'est un scénario de validation qui écrit des **tests d'intégration** et documente les résultats.

## Scope

1. Créer un scénario pytest `tests/scenarios/test_combat_system_e2e.py` qui :
   - Setup un bot test avec un LLM mocké (ou réel si Ollama dispo).
   - Crée une campagne `/start_campaign theme="désert"`.
   - Force l'arc à avoir un beat 1 `combat` contre un villain Vellus le Mentisseur avec un stat block complet (via fixture).
   - Simule un joueur Mageta attaquant Vellus.
   - Vérifie toute la chaîne :
     - [ ] Détection de trigger ATTACK
     - [ ] Combat bootstrap party-wide
     - [ ] Initiative roulée, ordre correct
     - [ ] Surprise appliquée selon le contexte
     - [ ] Embed `⚔️ Combat commence` posté
     - [ ] Boutons CombatActionView affichés
     - [ ] Dice embed d'attaque visible à chaque tour
     - [ ] NPC tactician appelé pour le tour de Vellus (mock LLM)
     - [ ] Legendary actions déclenchées après les tours PCs
     - [ ] Phase transition à 50% HP de Vellus
     - [ ] Narration de phase postée (embed or)
     - [ ] MOVE rejeté (auto-converti en FLEE)
     - [ ] TALK accepté si Vellus est encore en phase 1 (truce possible)
     - [ ] Fin de combat avec embed VICTORY quand Vellus tombe
     - [ ] Loot + XP affichés
     - [ ] `session.combat_state.is_active` = False

2. Créer un scénario Discord live via `discord-test` MCP qui reproduit un sous-ensemble sur le vrai bot. Documenter les résultats dans un fichier `docs/internal/combat_system_e2e_results.md`.

3. Scénarios de non-regression :
   - Trivial kill d'un commoner (faible HP) fonctionne toujours (pas de combat pour un paysan).
   - Beat social sans combat déclenché reste peaceful.
   - TALK hors combat fonctionne comme avant.

## Fichiers à créer

- **Créer** `tests/scenarios/test_combat_system_e2e.py`
- **Créer** `docs/internal/combat_system_e2e_results.md`

## Implémentation — esquisse

```python
# tests/scenarios/test_combat_system_e2e.py

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.game_session import GameSession
from bot.action_pipeline import ActionPipeline
from engine.combat import CombatSide, CombatState, CombatEndReason
from engine.npc_stat_block import NPCStatBlock, NPCTier, BehaviorProfile
from world.story_arc import StoryArc, StoryBeat


@pytest.fixture
def mageta_session():
    """Build a test session with Mageta vs Vellus starting scenario."""
    session = GameSession(...)  # fill with test fixtures
    # Arc with combat beat 1
    session.story_arc = StoryArc(
        theme="désert",
        premise="...",
        beats=[
            StoryBeat(
                beat_number=1,
                title="L'Écume du Vent",
                description="Combat contre Vellus dans les salicornes.",
                location_hint="Champ des Salicornes",
                npc_names=["Vellus le Mentisseur"],
                encounter_type="combat",
            ),
            # ... more beats ...
        ],
        villain_name="Vellus le Mentisseur",
        villain_motivation="...",
        villain_stat_block=_build_vellus_stat_block(),
    )
    return session


def _build_vellus_stat_block() -> NPCStatBlock:
    """Construct a realistic villain stat block for testing."""
    return NPCStatBlock(
        tier=NPCTier.BOSS,
        archetype="desert_sorcerer",
        multiattack_count=3,
        attacks=[
            NPCAttack(
                name="Lame de sable",
                damage_dice="1d8+3",
                damage_type="slashing",
                to_hit_bonus=6,
            ),
        ],
        signature_abilities=[
            SignatureAbility(
                name="Chant du Silence Éternel",
                usage="per_combat",
                uses_remaining=1,
                effects=[...],
            ),
            SignatureAbility(
                name="Morsure du Sable",
                usage="recharge_5_6",
                effects=[...],
            ),
        ],
        legendary_actions=[
            LegendaryAction(name="Coup rapide", cost=1, effects=[...]),
            LegendaryAction(name="Glissement ombreux", cost=2, effects=[]),
            LegendaryAction(name="Fracas éternel", cost=3, effects=[...]),
        ],
        legendary_points_per_round=3,
        phases=[
            PhaseTransition(
                trigger_hp_percent=50,
                narrative_cue="Vellus s'effondre... puis se relève, les yeux blancs.",
                unlock_signatures=["Rage du Désert"],
                attack_bonus=2,
                save_bonus=2,
            ),
        ],
        behavior_profile=BehaviorProfile.TACTICAL,
        aggression_threshold=25,
    )


@pytest.mark.asyncio
async def test_combat_bootstrap_on_attack(mageta_session):
    """Mageta attacks Vellus → combat starts with initiative rolled."""
    pipeline = ActionPipeline(
        session=mageta_session,
        actor_name="Mageta",
        # ... other args ...
    )
    action = InterpretedAction(
        actor_name="Mageta",
        action_type=ActionType.ATTACK,
        target_name="Vellus le Mentisseur",
    )
    await pipeline.handle_action(action)

    assert mageta_session.combat_state is not None
    assert mageta_session.combat_state.is_active
    assert mageta_session.combat_state.round_number == 1
    assert len(mageta_session.combat_state.combatants) >= 2  # Mageta + Vellus


@pytest.mark.asyncio
async def test_combat_initiative_surprise_case_3(mageta_session):
    """Beat combat scripted → face-to-face (case 3), no surprise."""
    # ... setup ...
    assert not any(
        is_surprised(c.conditions)
        for c in mageta_session.combat_state.combatants
    )


@pytest.mark.asyncio
async def test_move_autoconverts_to_flee_in_combat(mageta_session):
    """Mageta tries MOVE → pipeline auto-converts to FLEE."""
    # ... setup combat active ...
    action = InterpretedAction(
        actor_name="Mageta",
        action_type=ActionType.MOVE,
        target_name="Corridor des Illusions",
    )
    await pipeline.handle_action(action)
    # Verify FLEE was dispatched (check logs or result)


@pytest.mark.asyncio
async def test_vellus_phase_2_triggered_at_50_percent_hp(mageta_session):
    """Deal damage to Vellus until 50% HP → phase 2 triggers."""
    # ... setup, damage Vellus ...
    # Verify pending_phase_narrations has an event
    assert len(mageta_session.combat_state.pending_phase_narrations) >= 1


@pytest.mark.asyncio
async def test_combat_victory_when_vellus_dies(mageta_session):
    """Kill Vellus → combat ends with VICTORY."""
    # ... kill loop ...
    assert not mageta_session.combat_state.is_active
    assert mageta_session.combat_state.end_reason == CombatEndReason.VICTORY


@pytest.mark.asyncio
async def test_non_regression_trivial_kill_commoner(mageta_session):
    """Attack a commoner → still trivial resolve, no combat."""
    # ... add a commoner NPC with hp=4 ...
    action = InterpretedAction(
        actor_name="Mageta",
        action_type=ActionType.ATTACK,
        target_name="Paysan",
    )
    await pipeline.handle_action(action)
    # Combat should NOT be active
    assert mageta_session.combat_state is None or not mageta_session.combat_state.is_active


# ... more scenarios ...
```

**Scénario Discord live** (via `discord-test` MCP) — à exécuter manuellement et documenter :

```
1. /start_campaign theme="désert" language="fr"
2. Créer le perso Mageta (Human Ranger)
3. Attendre le lancement — vérifier l'opening crawl
4. @bot (Attack) j'attaque Vellus le Mentisseur
5. Vérifier : embed combat start + boutons
6. Clic Attack → select Vellus → vérifier embed de dice roll (attack roll)
7. Attendre le tour de Vellus — vérifier qu'un embed ou message narre son action
8. Continuer quelques tours jusqu'à 50% HP — vérifier phase transition embed
9. Tenter (Move) corridor des illusions → vérifier FLEE check embed
10. Terminer le combat en réduisant Vellus à 0 HP
11. Vérifier embed de fin VICTORY avec loot et XP
```

Documenter screenshots + pass/fail de chaque étape dans `docs/internal/combat_system_e2e_results.md`.

## Acceptance criteria

- [ ] Tous les tests pytest dans `test_combat_system_e2e.py` passent.
- [ ] Le scénario Discord live est documenté avec résultats de chaque étape.
- [ ] Les tests de non-regression (commoner trivial, social beat peaceful, TALK hors combat) passent.
- [ ] Les logs de campagne post-test contiennent bien les rounds, les jets de dés, pas de ligne unique "s'effondre, mort".
- [ ] `session.combat_state` est cleanup proprement après la fin.
- [ ] La narration mentionne explicitement les tours, jets, refuse les évasions passives.

## Tests à ajouter

Le test pytest lui-même est l'output de cette tâche. Pas de tests unitaires supplémentaires.

## Hors scope

- **Ne pas** implémenter de nouvelles features — ici on valide uniquement.
- **Ne pas** couvrir les edge cases exotiques (PvP, companion NPCs) — ils sont hors scope du chantier complet.

## Validation finale

```bash
uv run pytest tests/scenarios/test_combat_system_e2e.py -v
uv run ruff check tests/scenarios/
```

Plus : documenter les résultats du test live Discord dans `docs/internal/combat_system_e2e_results.md`.
