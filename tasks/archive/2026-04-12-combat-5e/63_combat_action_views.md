# Task 63 — Vues d'actions de combat (boutons Discord)

**Phase** : 6 — Discord UI
**Dépendances** : [31](31_action_pipeline_combat_dispatch.md), [62](62_combat_state_embed.md)
**Coordinateur** : [tasks/combat/README.md](README.md)

## Contexte

Le plan coordinateur (section 3.2) valide une **UI hybride** : embed de combat + `discord.ui.View` avec boutons pour les actions communes, + texte libre pour les actions improvisées. Les boutons doivent apparaître **uniquement quand c'est le tour du joueur**.

5 actions principales à supporter via boutons :
1. **Attack** → ouvre un select menu de cibles
2. **Cast Spell** → ouvre un select menu de sorts puis cibles
3. **Defend** (= Dodge) → action instantanée
4. **Flee** → action instantanée
5. **Move to zone** → ouvre un select menu de zones adjacentes

## Scope

1. Créer `bot/views/combat_action_view.py` avec la classe `CombatActionView(discord.ui.View)`.
2. Créer `bot/views/target_select_view.py` avec `TargetSelectView`.
3. Créer `bot/views/zone_select_view.py` avec `ZoneSelectView`.
4. Créer `bot/views/spell_select_view.py` avec `SpellSelectView`.
5. Chaque callback construit une `InterpretedAction` synthétique et la dispatche via le pipeline existant (`ActionPipeline.handle_action` ou équivalent).

## Fichiers à créer

- **Créer** `bot/views/combat_action_view.py`
- **Créer** `bot/views/target_select_view.py`
- **Créer** `bot/views/zone_select_view.py`
- **Créer** `bot/views/spell_select_view.py`

## Implémentation — esquisse

```python
# bot/views/combat_action_view.py

from typing import TYPE_CHECKING

import discord

from ai.models import InterpretedAction
from engine.validators import ActionType

if TYPE_CHECKING:
    from bot.game_session import GameSession
    from bot.action_pipeline import ActionPipeline


class CombatActionView(discord.ui.View):
    """Action buttons shown to the active combatant during their turn.

    Buttons dispatch to the existing ActionPipeline via a synthetic
    InterpretedAction with the chosen action_type. Secondary selects
    (target, zone, spell) open their own views.
    """

    def __init__(
        self,
        pipeline: "ActionPipeline",
        actor_name: str,
        user_id: int,
        timeout: float = 300.0,  # 5 minutes (see task 64)
    ) -> None:
        super().__init__(timeout=timeout)
        self.pipeline = pipeline
        self.actor_name = actor_name
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Only the acting player can press buttons."""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Ce n'est pas votre tour.", ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Attaquer", style=discord.ButtonStyle.danger, emoji="\u2694\ufe0f")
    async def attack_btn(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        from bot.views.target_select_view import TargetSelectView
        targets = self._get_valid_attack_targets()
        if not targets:
            await interaction.response.send_message(
                "Aucune cible en range.", ephemeral=True,
            )
            return
        view = TargetSelectView(
            pipeline=self.pipeline,
            actor_name=self.actor_name,
            user_id=self.user_id,
            targets=targets,
            action_type=ActionType.ATTACK,
        )
        await interaction.response.send_message(
            "Choisissez une cible :", view=view, ephemeral=True,
        )

    @discord.ui.button(label="Sort", style=discord.ButtonStyle.primary, emoji="\u2728")
    async def cast_btn(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        from bot.views.spell_select_view import SpellSelectView
        # Fetch the actor's known spells (requires session access)
        spells = self._get_known_spells()
        if not spells:
            await interaction.response.send_message(
                "Vous ne connaissez aucun sort.", ephemeral=True,
            )
            return
        view = SpellSelectView(
            pipeline=self.pipeline,
            actor_name=self.actor_name,
            user_id=self.user_id,
            spells=spells,
        )
        await interaction.response.send_message(
            "Choisissez un sort :", view=view, ephemeral=True,
        )

    @discord.ui.button(label="Défendre", style=discord.ButtonStyle.secondary, emoji="\U0001f6e1\ufe0f")
    async def defend_btn(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        action = InterpretedAction(
            actor_name=self.actor_name,
            action_type=ActionType.DEFEND,
        )
        await self._dispatch(interaction, action)

    @discord.ui.button(label="Fuir", style=discord.ButtonStyle.secondary, emoji="\U0001f3c3")
    async def flee_btn(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        action = InterpretedAction(
            actor_name=self.actor_name,
            action_type=ActionType.FLEE,
        )
        await self._dispatch(interaction, action)

    @discord.ui.button(label="Se déplacer", style=discord.ButtonStyle.secondary, emoji="\U0001f9ed")
    async def move_btn(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        from bot.views.zone_select_view import ZoneSelectView
        zones = self._get_adjacent_zones()
        if not zones:
            await interaction.response.send_message(
                "Aucune zone adjacente.", ephemeral=True,
            )
            return
        view = ZoneSelectView(
            pipeline=self.pipeline,
            actor_name=self.actor_name,
            user_id=self.user_id,
            zones=zones,
        )
        await interaction.response.send_message(
            "Choisissez une zone :", view=view, ephemeral=True,
        )

    async def _dispatch(
        self,
        interaction: discord.Interaction,
        action: InterpretedAction,
    ) -> None:
        # Defer and hand off to pipeline
        await interaction.response.defer()
        await self.pipeline.handle_action(action)
        self.stop()

    def _get_valid_attack_targets(self) -> list[str]:
        # Query session.combat_state for living enemies in range
        state = self.pipeline.combat_state
        if state is None:
            return []
        return [c.name for c in state.combatants if c.is_alive and c.side.value == "Enemy"]

    def _get_known_spells(self) -> list[str]:
        # Query session for the actor's spellcaster state
        if self.pipeline.session is None:
            return []
        for c in self.pipeline.combat_state.combatants:
            if c.name == self.actor_name and c.spellcaster is not None:
                return list(c.spellcaster.known_spells)
        return []

    def _get_adjacent_zones(self) -> list[str]:
        if self.pipeline.location is None or not self.pipeline.location.has_combat_zones():
            return []
        state = self.pipeline.combat_state
        if state is None:
            return []
        for c in state.combatants:
            if c.name == self.actor_name and c.current_zone is not None:
                zone = self.pipeline.location.get_zone(c.current_zone)
                if zone is not None:
                    return zone.adjacent_zone_names
        return []
```

```python
# bot/views/target_select_view.py

import discord

from ai.models import InterpretedAction
from engine.validators import ActionType


class TargetSelectView(discord.ui.View):
    def __init__(
        self,
        pipeline,
        actor_name: str,
        user_id: int,
        targets: list[str],
        action_type: ActionType,
        spell_name: str | None = None,
    ) -> None:
        super().__init__(timeout=60)
        self.pipeline = pipeline
        self.actor_name = actor_name
        self.user_id = user_id
        self.action_type = action_type
        self.spell_name = spell_name

        select = discord.ui.Select(
            placeholder="Choisir une cible",
            options=[
                discord.SelectOption(label=t, value=t)
                for t in targets[:25]  # Discord limit
            ],
        )
        select.callback = self._on_select
        self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    async def _on_select(self, interaction: discord.Interaction) -> None:
        target_name = interaction.data["values"][0]  # type: ignore
        action = InterpretedAction(
            actor_name=self.actor_name,
            action_type=self.action_type,
            target_name=target_name,
            spell_name=self.spell_name,
        )
        await interaction.response.defer()
        await self.pipeline.handle_action(action)
        self.stop()
```

(Patterns similaires pour `ZoneSelectView` et `SpellSelectView`.)

## Acceptance criteria

- [ ] `CombatActionView` existe avec 5 boutons (Attack, Cast, Defend, Flee, Move).
- [ ] `interaction_check` bloque les autres joueurs (seul le joueur actif peut cliquer).
- [ ] Attack ouvre un TargetSelectView avec les cibles valides.
- [ ] Cast ouvre un SpellSelectView avec les sorts connus.
- [ ] Move ouvre un ZoneSelectView avec les zones adjacentes.
- [ ] Defend et Flee dispatch direct sans select.
- [ ] Chaque dispatch construit une `InterpretedAction` synthétique et l'envoie au pipeline.
- [ ] Les views ont un timeout 5 min (voir tâche [64](64_turn_ping_and_timeout.md) pour le handling).
- [ ] Les selects respectent la limite Discord de 25 options.

## Tests à ajouter

Dans `tests/bot/test_combat_action_view.py` (nouveau) :

Tests unitaires : instancier la view, vérifier le nombre de boutons, simuler les interactions via mock `discord.Interaction`.

- `test_combat_action_view_has_five_buttons`.
- `test_attack_button_opens_target_select`.
- `test_attack_button_error_when_no_targets`.
- `test_cast_button_opens_spell_select`.
- `test_defend_button_dispatches_direct`.
- `test_flee_button_dispatches_direct`.
- `test_move_button_opens_zone_select`.
- `test_interaction_check_blocks_other_players`.

Tests via discord-test MCP (live) :

- Ouvrir une session avec combat actif, vérifier que les boutons apparaissent.
- Clic Attack → select menu → dispatch action correcte.

## Hors scope

- **Ne pas** implémenter le ping + timeout handling — tâche [64](64_turn_ping_and_timeout.md).
- **Ne pas** gérer les sorts avec AoE (cible multiple) — MVP = cible unique.
- **Ne pas** supporter les Bonus Actions séparément — tout passe par l'Action principale pour le MVP.

## Validation finale

```bash
uv run pytest tests/bot/test_combat_action_view.py -v
uv run ruff check bot/views/
uv run mypy bot/views/
```
