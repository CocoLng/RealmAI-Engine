# Task 61 — Embed "Combat commence"

**Phase** : 6 — Discord UI
**Dépendances** : [21](21_initiative_and_surprise.md)
**Coordinateur** : [tasks/combat/README.md](README.md)

## Contexte

Quand le combat démarre (via trigger détecté en tâche [31](31_action_pipeline_combat_dispatch.md)), le joueur doit voir un embed **fortement visuel** qui signale : "tu es en combat maintenant, voici qui agit et dans quel ordre". C'est un moment de bascule mécanique qui doit être **inratable** à l'écran.

## Scope

Créer `bot/embeds/combat_start_embed.py` avec `build_combat_start_embed(state, trigger, language='fr') -> discord.Embed`.

Le contenu :

- **Titre** : `⚔️ Combat commence` + (si trigger scripted beat) nom du beat.
- **Description** : `trigger.narrative_hint` ou un texte par défaut "Préparez vos armes, le combat engagé !".
- **Field "Ordre d'initiative"** : liste formatée `> **Nom** — init {valeur}` pour chaque combattant, combattant actif mis en évidence.
- **Field "Surprise"** : uniquement si applicable. Mentionne explicitement qui est surpris (skippe son premier tour).
- **Field "À vous de jouer"** : nom du premier combattant à agir + exemples de commandes (`@bot (Attack) je frappe`, `@bot (Cast Spell) Shield`, boutons dispos).
- **Couleur** : rouge vif (`0xCC0000`) pour l'urgence.

## Fichiers à créer

- **Créer** `bot/embeds/combat_start_embed.py`

## Implémentation — esquisse

```python
# bot/embeds/combat_start_embed.py

import discord

from engine.combat import CombatSide, CombatState
from engine.combat_trigger import CombatTrigger, InitiativeSide
from engine.conditions import is_surprised


_COLOR = 0xCC0000


def build_combat_start_embed(
    state: CombatState,
    trigger: CombatTrigger,
    language: str = "fr",
) -> discord.Embed:
    """Build the 'Combat starts' announce embed.

    Shows initiative order, surprise status, and the first combatant
    to act with command examples.
    """
    title = "\u2694\ufe0f Combat commence"
    if trigger.kind.value == "scripted_beat":
        title += " — encounter scripté"

    description = (
        trigger.narrative_hint
        or "Préparez vos armes — le combat engagé s'impose à vous."
    )

    embed = discord.Embed(title=title, description=description, color=_COLOR)

    # Initiative order
    init_lines: list[str] = []
    for idx, c in enumerate(state.combatants):
        is_active = idx == state.current_turn_index
        marker = "\u27a1\ufe0f" if is_active else "\u2003"
        side_marker = (
            "\U0001f9ba" if c.side == CombatSide.PLAYER else "\U0001f479"
        )
        suffix = " *(surpris)*" if is_surprised(c.conditions) else ""
        line = f"{marker} {side_marker} **{c.name}** — init {c.initiative}{suffix}"
        if is_active:
            line = f"**{line}**"
        init_lines.append(line)

    embed.add_field(
        name="Ordre d'initiative",
        value="\n".join(init_lines) if init_lines else "*(aucun combattant)*",
        inline=False,
    )

    # Surprise announcement
    surprise_msg = _surprise_announcement(trigger, state)
    if surprise_msg:
        embed.add_field(name="\u26a0\ufe0f Surprise", value=surprise_msg, inline=False)

    # Next to act
    if state.combatants:
        active = state.combatants[state.current_turn_index]
        embed.add_field(
            name="\u27a1\ufe0f À votre tour",
            value=(
                f"**{active.name}** agit en premier.\n"
                "Utilisez les boutons ci-dessous, ou tapez :\n"
                "`@bot (Attack) je frappe X`\n"
                "`@bot (Cast Spell) Magic Missile sur X`\n"
                "`@bot (Flee) je tente de fuir`"
            ),
            inline=False,
        )

    return embed


def _surprise_announcement(
    trigger: CombatTrigger,
    state: CombatState,
) -> str:
    if trigger.surprise_side == InitiativeSide.PLAYERS:
        targets = [
            c.name for c in state.combatants
            if c.side == CombatSide.ENEMY and is_surprised(c.conditions)
        ]
        if not targets:
            return ""
        return (
            f"Vous avez surpris **{', '.join(targets)}**. "
            f"Ils ne peuvent pas agir à leur premier tour."
        )
    if trigger.surprise_side == InitiativeSide.NPCS:
        targets = [
            c.name for c in state.combatants
            if c.side == CombatSide.PLAYER and is_surprised(c.conditions)
        ]
        if not targets:
            return ""
        return (
            f"Vous êtes surpris par l'attaque. "
            f"{', '.join(targets)} ne peuvent pas agir à leur premier tour."
        )
    return ""  # BOTH_READY → no surprise announcement
```

## Acceptance criteria

- [ ] `build_combat_start_embed` existe et retourne un `discord.Embed` valide.
- [ ] L'ordre d'initiative est affiché avec le combattant actif mis en évidence.
- [ ] Les combattants PC vs NPC sont visuellement distincts (emoji).
- [ ] L'annonce de surprise est présente et correcte pour cas 1 et 2.
- [ ] En cas BOTH_READY, pas de field surprise (ou field vide).
- [ ] Les suggestions de commandes guident le joueur.
- [ ] Couleur rouge.

## Tests à ajouter

Dans `tests/bot/test_combat_start_embed.py` (nouveau) :

- `test_build_combat_start_embed_shows_initiative_order`.
- `test_build_combat_start_embed_highlights_active_combatant`.
- `test_build_combat_start_embed_player_surprise_announcement`.
- `test_build_combat_start_embed_npc_surprise_announcement`.
- `test_build_combat_start_embed_both_ready_no_surprise_field`.
- `test_build_combat_start_embed_uses_narrative_hint_from_trigger`.
- `test_build_combat_start_embed_fallback_description_if_no_hint`.
- `test_build_combat_start_embed_color_is_red`.

## Hors scope

- **Ne pas** poster l'embed — tâche [31](31_action_pipeline_combat_dispatch.md) stocke déjà `_pending_combat_start_embed` et le caller (`action_handler.py`) le poste.
- **Ne pas** ajouter les boutons interactifs — tâche [63](63_combat_action_views.md).
- **Ne pas** traduire en autres langues que le français — focus français pour le MVP.

## Validation finale

```bash
uv run pytest tests/bot/test_combat_start_embed.py -v
uv run ruff check bot/embeds/combat_start_embed.py
uv run mypy bot/embeds/combat_start_embed.py
```
