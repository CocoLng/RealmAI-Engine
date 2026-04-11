# Task 64 — Ping de tour et timeout

**Phase** : 6 — Discord UI
**Dépendances** : [63](63_combat_action_views.md)
**Coordinateur** : [tasks/combat/README.md](README.md)

## Contexte

Dans un combat multijoueur, il faut que le bot **notifie le joueur dont c'est le tour** (via @ping Discord) et gère un **timeout** si ce joueur ne répond pas. Le plan coordinateur (section 3.6) valide : 5 minutes d'attente max, puis fallback automatique sur `Dodge` (choix safe qui garde le personnage en jeu).

## Scope

1. Helper `post_turn_notification(channel, active_combatant, view)` qui poste l'embed de combat + la view CombatActionView + mentionne l'user si c'est un PC (via `user_id` stocké sur le `Combatant`).
2. Task asyncio de timeout : après 5 minutes sans action, auto-dispatch `InterpretedAction(action_type=DEFEND)` pour le combattant.
3. Pour les NPCs turns, pas de ping et pas de timeout — l'engine résout immédiatement via `decide_minion_action` / `decide_elite_action` / `decide_boss_action`.
4. Gestion du désabonnement : si le joueur agit avant le timeout, annuler le timer.

## Fichiers à créer/modifier

- **Modifier** [bot/cogs/combat.py](../../bot/cogs/combat.py) — ajouter `TurnManager` ou refondre les helpers existants.

## Implémentation — esquisse

```python
# bot/cogs/combat.py (or new bot/combat_turn_manager.py)

import asyncio
import logging
from typing import TYPE_CHECKING

import discord

from ai.models import InterpretedAction
from engine.combat import Combatant, CombatSide
from engine.validators import ActionType

if TYPE_CHECKING:
    from bot.action_pipeline import ActionPipeline
    from bot.game_session import GameSession

logger = logging.getLogger(__name__)


class TurnManager:
    """Manages turn notifications, view lifecycle, and timeouts for active
    combatants during a combat.

    Each time a new combatant becomes active, the manager:
    - Posts the updated combat state embed.
    - If it's a PC: @pings the user, posts CombatActionView, starts a
      5-minute timeout task that auto-dispatches Dodge on expiry.
    - If it's an NPC: immediately calls the NPC brain (minion/elite/boss)
      to resolve their turn without waiting.
    """

    TIMEOUT_SECONDS = 300

    def __init__(
        self,
        channel: discord.TextChannel,
        pipeline: "ActionPipeline",
        session: "GameSession",
    ) -> None:
        self.channel = channel
        self.pipeline = pipeline
        self.session = session
        self._current_timeout_task: asyncio.Task | None = None

    async def on_turn_advanced(self, combatant: Combatant) -> None:
        """Called after advance_turn — set up the next turn."""
        # Cancel any pending timeout from the previous turn
        self._cancel_timeout()

        # Post updated state embed
        from bot.embeds.combat_embed import build_combat_embed
        embed = build_combat_embed(
            self.session.combat_state, self.session.current_location,
        )
        await self.channel.send(embed=embed)

        if combatant.side == CombatSide.PLAYER:
            await self._on_pc_turn(combatant)
        else:
            await self._on_npc_turn(combatant)

    async def _on_pc_turn(self, combatant: Combatant) -> None:
        user_id = self._find_user_id(combatant.name)
        mention = f"<@{user_id}>" if user_id else combatant.name

        from bot.views.combat_action_view import CombatActionView
        view = CombatActionView(
            pipeline=self.pipeline,
            actor_name=combatant.name,
            user_id=user_id or 0,
            timeout=self.TIMEOUT_SECONDS,
        )
        await self.channel.send(
            f"{mention}, c'est votre tour ! Choisissez votre action :",
            view=view,
        )

        # Start timeout watcher
        self._current_timeout_task = asyncio.create_task(
            self._timeout_watcher(combatant.name),
        )

    async def _on_npc_turn(self, combatant: Combatant) -> None:
        """Resolve an NPC turn immediately via the brain."""
        sb = getattr(combatant.character, "stat_block", None)
        if sb is None:
            logger.warning(
                "NPC %s has no stat_block, skipping turn", combatant.name,
            )
            return

        from engine.npc_ai.scripted import decide_minion_action, execute_action_plan
        from engine.npc_ai.elite import decide_elite_action
        from engine.npc_stat_block import NPCTier

        if sb.tier == NPCTier.MINION:
            plan = decide_minion_action(
                combatant, self.session.combat_state, self.session.current_location,
            )
        elif sb.tier == NPCTier.ELITE:
            plan = decide_elite_action(
                combatant, self.session.combat_state, self.session.current_location,
            )
        else:  # BOSS
            from engine.npc_ai.boss_brain import decide_boss_action
            plan = decide_boss_action(
                combatant,
                self.session.combat_state,
                self.session.current_location,
                tactician=self.session.npc_tactician,
                party_context=self._build_party_context(),
                recent_events=self._get_recent_events(),
            )

        summary = execute_action_plan(
            combatant, plan, self.session.combat_state, self.session.current_location,
        )
        await self.channel.send(f"*{summary}*")

        # Advance turn automatically after NPC
        from engine.combat import advance_turn
        advance_turn(self.session.combat_state)
        next_combatant = self.session.combat_state.combatants[
            self.session.combat_state.current_turn_index
        ]
        if self.session.combat_state.is_active:
            await self.on_turn_advanced(next_combatant)

    async def _timeout_watcher(self, actor_name: str) -> None:
        """Wait TIMEOUT_SECONDS then auto-dispatch Dodge."""
        try:
            await asyncio.sleep(self.TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            return

        await self.channel.send(
            f"\u23f1\ufe0f {actor_name} n'a pas agi à temps — Dodge automatique."
        )
        action = InterpretedAction(
            actor_name=actor_name,
            action_type=ActionType.DEFEND,
        )
        await self.pipeline.handle_action(action)

    def _cancel_timeout(self) -> None:
        if self._current_timeout_task and not self._current_timeout_task.done():
            self._current_timeout_task.cancel()
        self._current_timeout_task = None

    def _find_user_id(self, character_name: str) -> int | None:
        for user_id, char in self.session.characters.items():
            if char.name == character_name:
                return int(user_id)
        return None

    def _build_party_context(self) -> str:
        # For the LLM tactician — brief narrative context
        loc = self.session.current_location
        return f"Location: {loc.name if loc else 'unknown'}"

    def _get_recent_events(self) -> list[str]:
        # For the LLM tactician — last 3 mechanical events
        # Stored in session or retrieved from action_pipeline history
        return getattr(self.session, "_recent_events", [])
```

**Intégration** : dans `ActionPipeline.handle_action`, après `advance_turn` au post-combat-step, appeler `turn_manager.on_turn_advanced(new_current)`. Ou inversement, le TurnManager observe les avances du state.

Important : le TurnManager doit être **instancié au démarrage du combat** (dans tâche [31](31_action_pipeline_combat_dispatch.md) `_validate` après enter_combat) et détruit quand le combat se termine.

## Acceptance criteria

- [ ] `TurnManager` existe et gère les ping/timeout/resolve.
- [ ] Pour un tour de PC, l'user est ping via `<@user_id>`.
- [ ] La `CombatActionView` est postée avec un timeout de 5 minutes.
- [ ] Si le joueur n'agit pas dans les 5 min, un Dodge est auto-dispatché.
- [ ] Si le joueur agit avant, le timeout est annulé.
- [ ] Pour un tour de NPC, l'engine résout immédiatement sans UI.
- [ ] NPCs minion → `decide_minion_action`. Elite → `decide_elite_action`. Boss → `decide_boss_action` avec LLM-tactician.
- [ ] Après un tour NPC, `advance_turn` est appelé et `on_turn_advanced` récursif.
- [ ] Combat end detection : si `check_combat_end` retourne non-None, le TurnManager s'arrête et poste un embed de fin (tâche [80](80_combat_end_conditions.md)).

## Tests à ajouter

Dans `tests/bot/test_turn_manager.py` (nouveau) :

Tests unitaires avec mock `discord.TextChannel` et `ActionPipeline` :

- `test_turn_manager_pings_pc_on_their_turn`.
- `test_turn_manager_posts_combat_action_view`.
- `test_turn_manager_cancels_timeout_on_user_action`.
- `test_turn_manager_auto_dodge_on_timeout`.
- `test_turn_manager_resolves_minion_turn_without_view`.
- `test_turn_manager_advances_turn_after_npc_resolution`.
- `test_turn_manager_stops_on_combat_end`.

Tests live via discord-test MCP :

- Scénario : combat 1 PC vs 1 minion goblin. Clic Attack → dispatch → goblin turn auto → back to PC.

## Hors scope

- **Ne pas** implémenter la résolution des ambushes — gérée en amont par `enter_combat` tâche [20](20_combat_entry_module.md).
- **Ne pas** gérer les inputs texte libre — ils passent par `action_handler.py` comme avant, le TurnManager ne gère que les views.
- **Ne pas** implémenter un système de "ready action" (5e RAW : action préparée qui se déclenche sur une condition) — reporté.

## Validation finale

```bash
uv run pytest tests/bot/test_turn_manager.py -v
uv run ruff check bot/cogs/combat.py
uv run mypy bot/cogs/combat.py
```
