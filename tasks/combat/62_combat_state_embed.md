# Task 62 — Refonte de l'embed d'état combat

**Phase** : 6 — Discord UI
**Dépendances** : [22](22_multi_enemy_combat_state.md), [12](12_zone_model.md)
**Coordinateur** : [tasks/combat/README.md](README.md)

## Contexte

[bot/embeds/combat_embed.py](../../bot/embeds/combat_embed.py) existe avec `build_combat_embed(combat_state)` mais il est limité à une liste plate de combattants avec leurs HP bars. Avec l'arrivée :
- Des zones ([tâche 12](12_zone_model.md))
- Des multi-enemies ([tâche 22](22_multi_enemy_combat_state.md))
- Des conditions variées (SURPRISED, PRONE, FRIGHTENED, etc.)
- Des legendary points restants pour le boss

…il faut refondre l'affichage.

## Scope

Refondre `build_combat_embed(state, location)` pour produire un embed qui affiche :

- **Titre** : `Combat — Round N` avec nom du beat ou zone principale.
- **Description** : résumé du tour actif (`À [Nom] de jouer`).
- **Field par zone** (si `location.combat_zones` non vide) : liste des combattants présents dans chaque zone avec HP bars et conditions. Si pas de zones, un seul field "Combattants".
- **Field "Boss"** (optionnel, si un boss avec legendary_points est présent) : nom, HP bar, legendary points remaining.
- **Footer** : `Tour actuel : [Nom]`.

## Fichiers à modifier

- [bot/embeds/combat_embed.py](../../bot/embeds/combat_embed.py)

## Implémentation — esquisse

```python
# bot/embeds/combat_embed.py

import discord

from engine.combat import CombatSide, CombatState, Combatant
from engine.conditions import ActiveCondition, ConditionType
from engine.npc_stat_block import NPCTier
from world.location import Location


_COLOR = 0xCC0000
_BAR_LENGTH = 10
_FILLED = "\u2588"
_EMPTY = "\u2591"


def _hp_bar(hp: int, max_hp: int) -> str:
    if max_hp <= 0:
        ratio = 0.0
    else:
        ratio = max(0.0, min(1.0, hp / max_hp))
    filled = round(ratio * _BAR_LENGTH)
    empty = _BAR_LENGTH - filled
    return f"[{_FILLED * filled}{_EMPTY * empty}] {hp}/{max_hp}"


def build_combat_embed(
    state: CombatState,
    location: Location | None = None,
) -> discord.Embed:
    """Build the main combat state embed.

    Displays zones (if present), combatant HP bars, conditions, and the
    active turn indicator. Boss combatants show their legendary points.
    """
    active = state.combatants[state.current_turn_index] if state.combatants else None
    title = f"Combat — Round {state.round_number}"

    embed = discord.Embed(
        title=title,
        color=_COLOR,
        description=f"\u27a1\ufe0f **{active.name}** joue" if active else "—",
    )

    # Group combatants by zone (or single group if no zones)
    if location is not None and location.has_combat_zones():
        for zone in location.combat_zones:
            in_zone = [
                c for c in state.combatants
                if c.current_zone == zone.name and c.is_alive and not c.fled
            ]
            if not in_zone:
                continue
            lines = [_combatant_line(c, active) for c in in_zone]
            embed.add_field(
                name=f"\U0001f4cd {zone.name}",
                value="\n".join(lines),
                inline=False,
            )

        # Unzoned combatants (shouldn't happen in proper zoned combat, but defensive)
        unzoned = [
            c for c in state.combatants
            if c.current_zone is None and c.is_alive and not c.fled
        ]
        if unzoned:
            lines = [_combatant_line(c, active) for c in unzoned]
            embed.add_field(
                name="Hors zone",
                value="\n".join(lines),
                inline=False,
            )
    else:
        # Single flat list
        alive = [c for c in state.combatants if c.is_alive and not c.fled]
        lines = [_combatant_line(c, active) for c in alive]
        if lines:
            embed.add_field(
                name="Combattants",
                value="\n".join(lines),
                inline=False,
            )

    # Boss field (legendary points)
    boss = _find_boss(state)
    if boss is not None and boss.legendary_points_remaining > 0:
        embed.add_field(
            name=f"\U0001f451 {boss.name}",
            value=(
                f"**Actions légendaires** : "
                f"{boss.legendary_points_remaining} points restants"
            ),
            inline=False,
        )

    if active:
        embed.set_footer(text=f"Tour de : {active.name}")
    return embed


def _combatant_line(c: Combatant, active: Combatant | None) -> str:
    """Format a single combatant line."""
    marker = "\u27a1\ufe0f" if active is not None and c.name == active.name else "\u2003"
    side_icon = "\U0001f9ba" if c.side == CombatSide.PLAYER else "\U0001f479"
    bar = _hp_bar(c.character.hp, c.character.max_hp)
    conditions_str = _format_conditions(c.conditions)
    line = f"{marker} {side_icon} **{c.name}** — {bar}"
    if conditions_str:
        line += f"\n    *{conditions_str}*"
    return line


def _format_conditions(conditions: list[ActiveCondition]) -> str:
    if not conditions:
        return ""
    names: list[str] = []
    for c in conditions:
        name = c.condition_type.value
        if c.duration_rounds is not None and c.duration_rounds > 0:
            names.append(f"{name}({c.duration_rounds}r)")
        else:
            names.append(name)
    return ", ".join(names)


def _find_boss(state: CombatState) -> Combatant | None:
    for c in state.combatants:
        sb = c.character.stat_block if hasattr(c.character, "stat_block") else None
        if sb is not None and sb.tier == NPCTier.BOSS and c.is_alive:
            return c
    return None
```

**Note backward-compat** : la signature existante est `build_combat_embed(combat_state)`. Cette tâche ajoute un param optionnel `location`. Les call sites existants continuent de fonctionner (affichage flat sans zones), mais tous les nouveaux appels devront passer la location pour bénéficier de l'affichage par zones.

## Acceptance criteria

- [ ] `build_combat_embed(state, location)` fonctionne avec et sans `location`.
- [ ] Quand `location` a des zones, chaque zone est un field séparé.
- [ ] HP bar graphique pour chaque combattant.
- [ ] Conditions actives listées sous chaque combattant.
- [ ] Combattant actif mis en évidence (`➡️` + **bold**).
- [ ] PC et NPC distinguables par emoji.
- [ ] Boss avec legendary points → field dédié.
- [ ] Combattants morts ou fuits NE sont PAS affichés.

## Tests à ajouter

Dans `tests/bot/test_combat_embed.py` :

- `test_build_combat_embed_flat_without_location`.
- `test_build_combat_embed_grouped_by_zone`.
- `test_build_combat_embed_shows_active_marker`.
- `test_build_combat_embed_shows_conditions`.
- `test_build_combat_embed_skips_dead_combatants`.
- `test_build_combat_embed_skips_fled_combatants`.
- `test_build_combat_embed_shows_boss_legendary_points`.
- `test_build_combat_embed_no_boss_field_when_none_present`.
- `test_build_combat_embed_footer_includes_active_name`.

## Hors scope

- **Ne pas** créer l'embed de combat start — tâche [61](61_combat_start_embed.md).
- **Ne pas** ajouter des images/thumbnails.
- **Ne pas** afficher les AC (caché intentionnellement pour l'immersion).
- **Ne pas** afficher les scores d'ability exposés.

## Validation finale

```bash
uv run pytest tests/bot/test_combat_embed.py -v
uv run ruff check bot/embeds/combat_embed.py
uv run mypy bot/embeds/combat_embed.py
```
