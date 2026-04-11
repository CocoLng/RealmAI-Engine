# Task 60 — Module d'embeds de jets de dés

**Phase** : 6 — Discord UI
**Dépendances** : aucune
**Coordinateur** : [tasks/combat/README.md](README.md)

## Contexte

Le joueur a explicitement demandé que **les jets de dés soient visibles** pendant le combat : "on veut voir des embeds de jets de dés, que les joueurs comprennent qu'il y a eu un jet de dés". Aujourd'hui, les rolls sont affichés uniquement en texte dans le footer de l'embed narratif ("5 dégâts"), sans montrer le d20, le modifier, le DC, le résultat tier.

Cette tâche crée un nouveau module `bot/embeds/dice_embed.py` avec des helpers pour construire des embeds de jets visuellement distincts.

## Scope

Créer `bot/embeds/dice_embed.py` avec :

1. `build_attack_roll_embed(result: AttackResult, attacker_name: str) -> discord.Embed`
2. `build_damage_roll_embed(dice_expression: str, result: DiceResult, damage_type: DamageType) -> discord.Embed`
3. `build_save_check_embed(check: D20CheckResult, label: str, actor_name: str, ability: str) -> discord.Embed`
4. `build_generic_check_embed(check: D20CheckResult, label: str, actor_name: str) -> discord.Embed`
5. Constantes de couleur par outcome : vert = success, rouge = miss, or = critical.
6. Formatters clairs : "`1d20+5` → **19** vs AC **15** → ⚔️ **Touché**".

## Fichiers à créer

- **Créer** `bot/embeds/dice_embed.py`

## Implémentation — esquisse

```python
# bot/embeds/dice_embed.py

import discord

from engine.combat import AttackResult
from engine.dice import DiceResult, D20CheckResult, RollOutcome
from engine.inventory import DamageType


_COLOR_HIT = 0x2ECC71       # green
_COLOR_MISS = 0xE74C3C      # red
_COLOR_CRIT = 0xF1C40F      # gold
_COLOR_NEUTRAL = 0x4A90D9   # blue


_OUTCOME_LABELS: dict[RollOutcome, str] = {
    RollOutcome.CRITICAL_SUCCESS: "\u2b50 Succès critique",
    RollOutcome.SUCCESS: "\u2714 Succès",
    RollOutcome.NEAR_SUCCESS: "\u2714 Succès de justesse",
    RollOutcome.NEAR_FAILURE: "\u2716 Échec de peu",
    RollOutcome.FAILURE: "\u2716 Échec",
    RollOutcome.CRITICAL_FAILURE: "\U0001f480 Échec critique",
}


_DAMAGE_TYPE_FR: dict[DamageType, str] = {
    DamageType.SLASHING: "tranchant",
    DamageType.PIERCING: "perforant",
    DamageType.BLUDGEONING: "contondant",
    DamageType.FIRE: "feu",
    DamageType.COLD: "froid",
    DamageType.LIGHTNING: "foudre",
    DamageType.THUNDER: "tonnerre",
    DamageType.POISON: "poison",
    DamageType.ACID: "acide",
    DamageType.NECROTIC: "nécrotique",
    DamageType.RADIANT: "radiant",
    DamageType.PSYCHIC: "psychique",
    DamageType.FORCE: "force",
}


def build_attack_roll_embed(result: AttackResult, attacker_name: str) -> discord.Embed:
    """Build an embed displaying an attack roll against AC.

    Shows the d20 roll, the total, the target AC, hit/miss, and on
    hit, the damage dealt and type.
    """
    if result.critical and result.hit:
        color = _COLOR_CRIT
        title = "\u2694\ufe0f Attaque critique"
    elif result.hit:
        color = _COLOR_HIT
        title = "\u2694\ufe0f Attaque réussie"
    else:
        color = _COLOR_MISS
        title = "\u2694\ufe0f Attaque ratée"

    embed = discord.Embed(title=title, color=color)

    # The attack_total already includes the bonus; display as 1d20 + bonus = total
    bonus = result.attack_total - result.attack_roll
    bonus_str = f"+{bonus}" if bonus >= 0 else str(bonus)
    embed.add_field(
        name="Jet d'attaque",
        value=f"`1d20{bonus_str}` → **{result.attack_total}**",
        inline=True,
    )
    embed.add_field(name="Armure cible", value=f"**{result.ac}**", inline=True)
    embed.add_field(
        name="Résultat",
        value=_OUTCOME_LABELS.get(result.outcome, result.outcome.value),
        inline=True,
    )

    if result.hit:
        dmg_type_fr = _DAMAGE_TYPE_FR.get(
            result.damage_type, result.damage_type.value,
        )
        embed.add_field(
            name="Dégâts",
            value=f"**{result.damage}** ({dmg_type_fr})",
            inline=False,
        )

    embed.set_footer(
        text=f"{attacker_name} → {result.defender} | nat {result.attack_roll}"
    )
    return embed


def build_save_check_embed(
    check: D20CheckResult,
    label: str,
    actor_name: str,
    ability: str,
) -> discord.Embed:
    """Build an embed for a D20 save/check (save throw, skill check, flee, etc.).

    ``label`` describes what the check is for (e.g. "Save CON concentration",
    "Check DEX flee", "Check CHA persuasion"). ``ability`` is the ability
    score name ("CON", "DEX", etc.) for display.
    """
    is_success = check.outcome in (
        RollOutcome.CRITICAL_SUCCESS,
        RollOutcome.SUCCESS,
        RollOutcome.NEAR_SUCCESS,
    )
    if check.outcome == RollOutcome.CRITICAL_SUCCESS:
        color = _COLOR_CRIT
    elif is_success:
        color = _COLOR_HIT
    else:
        color = _COLOR_MISS

    embed = discord.Embed(title=f"\U0001f3b2 {label}", color=color)

    bonus = check.total - check.rolls[0]
    bonus_str = f"+{bonus}" if bonus >= 0 else str(bonus)
    embed.add_field(
        name=f"Jet ({ability})",
        value=f"`1d20{bonus_str}` → **{check.total}**",
        inline=True,
    )
    embed.add_field(name="DC", value=f"**{check.dc}**", inline=True)
    embed.add_field(
        name="Résultat",
        value=_OUTCOME_LABELS.get(check.outcome, check.outcome.value),
        inline=True,
    )
    embed.set_footer(text=f"{actor_name} | nat {check.rolls[0]} | marge {check.margin:+d}")
    return embed


def build_damage_roll_embed(
    dice_expression: str,
    result: DiceResult,
    damage_type: DamageType,
    source_name: str = "",
) -> discord.Embed:
    """Standalone damage roll (e.g. for Signature AoE)."""
    dmg_type_fr = _DAMAGE_TYPE_FR.get(damage_type, damage_type.value)
    embed = discord.Embed(
        title=f"\U0001f4a5 Jet de dégâts ({dmg_type_fr})",
        color=_COLOR_NEUTRAL,
    )
    rolls_str = " + ".join(str(r) for r in result.rolls)
    mod_str = f" + {result.modifier}" if result.modifier else ""
    embed.add_field(
        name="Jet",
        value=f"`{dice_expression}` → [{rolls_str}]{mod_str} = **{result.total}**",
        inline=False,
    )
    if source_name:
        embed.set_footer(text=source_name)
    return embed


def build_generic_check_embed(
    check: D20CheckResult,
    label: str,
    actor_name: str,
) -> discord.Embed:
    """Generic check (no ability label)."""
    return build_save_check_embed(check, label, actor_name, ability="—")
```

## Acceptance criteria

- [ ] `bot/embeds/dice_embed.py` existe avec les 4 fonctions.
- [ ] Chaque fonction retourne un `discord.Embed` valide (pas de champ trop long, pas de couleur invalide).
- [ ] Les couleurs sont correctes : vert hit, rouge miss, or crit.
- [ ] Le nat 20 apparaît dans le footer.
- [ ] Le damage type est traduit en français.
- [ ] L'outcome tier est traduit et emoji-fié.

## Tests à ajouter

Dans `tests/bot/test_dice_embed.py` (nouveau) :

- `test_attack_roll_embed_hit`.
- `test_attack_roll_embed_miss`.
- `test_attack_roll_embed_critical_hit_is_gold`.
- `test_save_check_embed_success`.
- `test_save_check_embed_failure`.
- `test_damage_roll_embed_shows_individual_rolls`.
- `test_generic_check_embed_defaults_dash_ability`.
- `test_embed_color_matches_outcome`.
- `test_embed_footer_includes_nat_roll`.

Utiliser `discord.Embed.to_dict()` pour comparer structurellement les champs dans les tests.

## Hors scope

- **Ne pas** câbler ces embeds dans `action_pipeline.py` — ça vient dans les tâches qui consomment (notamment [22](22_multi_enemy_combat_state.md) pour les attacks et [32](32_flee_resolution.md) pour les flee checks).
- **Ne pas** styler les embeds avec des images ou thumbnails — simple et rapide suffit.
- **Ne pas** supporter les jets avec advantage/disadvantage (deux rolls affichés) — reporté si besoin.

## Validation finale

```bash
uv run pytest tests/bot/test_dice_embed.py -v
uv run ruff check bot/embeds/dice_embed.py
uv run mypy bot/embeds/dice_embed.py
```
