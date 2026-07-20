# Task 80 — Conditions de fin de combat

**Phase** : 8 — Fin de combat & intégration
**Dépendances** : [22](22_multi_enemy_combat_state.md), [32](32_flee_resolution.md)
**Coordinateur** : [tasks/combat/README.md](README.md)

## Contexte

`check_combat_end` (tâche [22](22_multi_enemy_combat_state.md)) détecte déjà les 3 conditions basiques : VICTORY, DEFEAT, FLED. Le plan coordinateur (section 3.4) ajoute deux conditions supplémentaires :

4. **Résolution sociale (TRUCE)** : un joueur utilise `(Talk)` en combat et réussit un check CHA vs `aggression_threshold`. Traité en tâche [81](81_social_resolution_mid_combat.md).
5. **Timeout** : 30 minutes sans action dans le channel → auto-pause du combat.

Et du côté UX :

- Cleanup de `session.combat_state` (preserve les stats mais marque inactif).
- Embed de fin de combat avec récap : résumé narratif, loot (items des NPCs tombés), XP (futur), conditions persistantes à nettoyer.
- Retirer le TurnManager et le timeout watcher.

## Scope

1. Créer `bot/combat_end.py` avec `finalize_combat(session, state, reason) -> CombatEndSummary`.
2. Gérer les 5 conditions de fin (les 3 existantes + TRUCE stub + TIMEOUT).
3. Créer `bot/embeds/combat_end_embed.py` avec `build_combat_end_embed(summary)`.
4. Ajouter `CombatEndReason.TRUCE` et `CombatEndReason.TIMEOUT` à l'enum.
5. Cleanup :
   - Reset `combat_state.is_active = False`.
   - Sauver le state final en DB (pour historique).
   - Clear les conditions temporaires des PCs survivants.
   - Re-trigger `hydrate_scene` si des NPCs ont changé d'état.

## Fichiers à créer/modifier

- **Créer** `bot/combat_end.py`
- **Créer** `bot/embeds/combat_end_embed.py`
- **Modifier** [engine/combat.py](../../engine/combat.py) — ajouter `CombatEndReason.TRUCE` et `TIMEOUT`.

## Implémentation — esquisse

```python
# engine/combat.py

class CombatEndReason(StrEnum):
    VICTORY = "victory"
    DEFEAT = "defeat"
    FLED = "fled"
    TRUCE = "truce"       # task 81
    TIMEOUT = "timeout"
```

```python
# bot/combat_end.py

from dataclasses import dataclass
from typing import TYPE_CHECKING

from engine.combat import CombatEndReason, CombatState, CombatSide

if TYPE_CHECKING:
    from bot.game_session import GameSession


@dataclass
class CombatEndSummary:
    """Data collected at the end of combat for display + cleanup."""
    reason: CombatEndReason
    survivors_pc: list[str]
    survivors_enemy: list[str]
    killed_pcs: list[str]
    killed_enemies: list[str]
    fled_pcs: list[str]
    loot_items: list[str]
    xp_earned: int
    rounds_taken: int
    narrative: str = ""


def finalize_combat(
    session: "GameSession",
    reason: CombatEndReason,
) -> CombatEndSummary:
    """Finalize the current combat: build summary, cleanup state."""
    state = session.combat_state
    assert state is not None

    survivors_pc = [
        c.name for c in state.combatants
        if c.side == CombatSide.PLAYER and c.is_alive and not c.fled
    ]
    killed_pcs = [
        c.name for c in state.combatants
        if c.side == CombatSide.PLAYER and not c.is_alive
    ]
    fled_pcs = [
        c.name for c in state.combatants
        if c.side == CombatSide.PLAYER and c.fled
    ]
    killed_enemies = [
        c.name for c in state.combatants
        if c.side == CombatSide.ENEMY and not c.is_alive
    ]
    survivors_enemy = [
        c.name for c in state.combatants
        if c.side == CombatSide.ENEMY and c.is_alive and not c.fled
    ]

    loot_items: list[str] = []
    # Loot from killed enemies (their primary weapon becomes lootable)
    for enemy_name in killed_enemies:
        enemy = next(
            (c for c in state.combatants if c.name == enemy_name), None,
        )
        if enemy is None:
            continue
        sb = getattr(enemy.character, "stat_block", None)
        if sb is not None and sb.attacks:
            # First attack name is used as loot placeholder (MVP)
            loot_items.append(sb.attacks[0].name)

    # XP: rough formula — 50 XP per killed minion, 150 elite, 500 boss
    from engine.npc_stat_block import NPCTier
    xp = 0
    for name in killed_enemies:
        enemy = next((c for c in state.combatants if c.name == name), None)
        sb = getattr(enemy.character, "stat_block", None) if enemy else None
        if sb is None:
            xp += 25
        elif sb.tier == NPCTier.MINION:
            xp += 50
        elif sb.tier == NPCTier.ELITE:
            xp += 150
        else:  # BOSS
            xp += 500

    summary = CombatEndSummary(
        reason=reason,
        survivors_pc=survivors_pc,
        survivors_enemy=survivors_enemy,
        killed_pcs=killed_pcs,
        killed_enemies=killed_enemies,
        fled_pcs=fled_pcs,
        loot_items=loot_items,
        xp_earned=xp,
        rounds_taken=state.round_number,
    )

    _cleanup_combat_state(session, state)
    return summary


def _cleanup_combat_state(session: "GameSession", state: CombatState) -> None:
    """Clear transient combat state from session, preserve historical data."""
    state.is_active = False
    # Clear temporary conditions on survivors (concentration, buffs)
    from engine.conditions import ConditionType, remove_condition
    transient_conditions = {
        ConditionType.SURPRISED,
        ConditionType.CONCENTRATING,
        # Keep Prone/Poisoned/Frightened if applicable — they persist post-combat
    }
    for c in state.combatants:
        for cond in list(c.conditions):
            if cond.condition_type in transient_conditions:
                remove_condition(c.conditions, cond.condition_type)

    # session.combat_state stays set to the final state for history — it will
    # be reset to None on next combat entry.
```

```python
# bot/embeds/combat_end_embed.py

import discord

from bot.combat_end import CombatEndSummary
from engine.combat import CombatEndReason


_COLORS = {
    CombatEndReason.VICTORY: 0x2ECC71,  # green
    CombatEndReason.DEFEAT: 0xE74C3C,   # red
    CombatEndReason.FLED: 0x95A5A6,     # gray
    CombatEndReason.TRUCE: 0x9B59B6,    # purple
    CombatEndReason.TIMEOUT: 0xF39C12,  # orange
}

_TITLES = {
    CombatEndReason.VICTORY: "\U0001f3c6 Victoire",
    CombatEndReason.DEFEAT: "\U0001f480 Défaite",
    CombatEndReason.FLED: "\U0001f3c3 Fuite réussie",
    CombatEndReason.TRUCE: "\U0001f54a\ufe0f Trêve",
    CombatEndReason.TIMEOUT: "\u23f8\ufe0f Combat en pause",
}


def build_combat_end_embed(summary: CombatEndSummary) -> discord.Embed:
    embed = discord.Embed(
        title=_TITLES[summary.reason],
        description=summary.narrative or _default_narrative(summary),
        color=_COLORS[summary.reason],
    )

    if summary.killed_enemies:
        embed.add_field(
            name="Ennemis vaincus",
            value="\n".join(f"- {n}" for n in summary.killed_enemies),
            inline=True,
        )
    if summary.killed_pcs:
        embed.add_field(
            name="Tombés au combat",
            value="\n".join(f"- {n}" for n in summary.killed_pcs),
            inline=True,
        )
    if summary.fled_pcs:
        embed.add_field(
            name="Ayant fui",
            value="\n".join(f"- {n}" for n in summary.fled_pcs),
            inline=True,
        )

    if summary.loot_items:
        embed.add_field(
            name="Butin",
            value=", ".join(summary.loot_items),
            inline=False,
        )

    if summary.xp_earned > 0:
        embed.add_field(
            name="Expérience gagnée",
            value=f"**{summary.xp_earned}** XP",
            inline=True,
        )

    embed.add_field(
        name="Durée",
        value=f"{summary.rounds_taken} rounds",
        inline=True,
    )
    return embed


def _default_narrative(summary: CombatEndSummary) -> str:
    if summary.reason == CombatEndReason.VICTORY:
        return "Les derniers ennemis tombent. Le silence revient."
    if summary.reason == CombatEndReason.DEFEAT:
        return "Le groupe s'effondre sous les coups. L'aventure s'arrête ici."
    if summary.reason == CombatEndReason.FLED:
        return "Le groupe parvient à s'échapper, haletant."
    if summary.reason == CombatEndReason.TRUCE:
        return "Une trêve improbable met fin à l'affrontement."
    return "Le combat se met en pause — personne n'a réagi à temps."
```

## Acceptance criteria

- [ ] `CombatEndReason.TRUCE` et `TIMEOUT` ajoutés.
- [ ] `finalize_combat` construit correctement le `CombatEndSummary`.
- [ ] Cleanup retire SURPRISED et CONCENTRATING mais préserve les conditions qui font sens post-combat (poisoned, etc.).
- [ ] XP calculé selon le tier des killed enemies.
- [ ] Loot generated pour chaque killed enemy (1 item minimum).
- [ ] `build_combat_end_embed` affiche les 5 reasons avec couleurs et emojis corrects.
- [ ] Champs dynamiques : seulement les champs avec des données apparaissent.

## Tests à ajouter

Dans `tests/bot/test_combat_end.py` (nouveau) :

- `test_finalize_combat_victory_summary`.
- `test_finalize_combat_defeat_summary`.
- `test_finalize_combat_fled_summary`.
- `test_finalize_combat_xp_calculation_by_tier`.
- `test_finalize_combat_loot_from_killed_enemies`.
- `test_finalize_combat_cleanup_removes_transient_conditions`.
- `test_finalize_combat_preserves_persistent_conditions`.
- `test_build_combat_end_embed_victory_color_green`.
- `test_build_combat_end_embed_defeat_color_red`.
- `test_build_combat_end_embed_optional_fields`.

## Hors scope

- **Ne pas** gérer la progression XP réelle des PCs (levelup) — c'est un chantier séparé.
- **Ne pas** implémenter TRUCE detection — tâche [81](81_social_resolution_mid_combat.md).
- **Ne pas** implémenter le loot sophistiqué (tables, RNG) — MVP : placeholder basique.
- **Ne pas** déclencher le cleanup depuis `advance_turn` — le TurnManager (tâche [64](64_turn_ping_and_timeout.md)) appelle `finalize_combat` quand il détecte la fin.

## Validation finale

```bash
uv run pytest tests/bot/test_combat_end.py -v
uv run ruff check bot/combat_end.py bot/embeds/combat_end_embed.py
uv run mypy bot/combat_end.py bot/embeds/combat_end_embed.py
```
