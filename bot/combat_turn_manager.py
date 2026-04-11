"""Combat TurnManager (task 64).

Drives the turn lifecycle of a combat encounter inside a Discord channel:

- Posts the "⚔️ Combat commence" banner once when combat bootstraps.
- Maintains a single "combat hub" :class:`discord.Message` edited in place
  round after round (modern discord.py 2.7 pattern — no channel spam).
- On PC turns, pings the player, posts the ``CombatActionView``, and arms
  a 5-minute asyncio timeout watcher. Expiry dispatches an auto-Dodge via
  the same pipeline path a button click uses.
- On NPC turns, dispatches the right brain by tier (minion → scripted,
  elite → behavior profile, boss → LLM tactician with scripted fallback),
  executes the plan, posts a dice embed + compact summary, and advances
  the turn.
- On combat end, edits the hub to a "Combat terminé" state, posts a small
  XP stub (ported from legacy ``bot/cogs/combat.py._end_combat`` — Phase 8
  task 80 will replace it with the full end-of-combat flow), clears
  ``session.combat_turn_manager``.

The TurnManager does **not** decide dice or mutate HP itself — every
mechanical change goes through :class:`bot.action_pipeline.ActionPipeline`
or through the scripted NPC executor. That keeps Phase 6 honest about the
golden rule: the LLM narrates, the engine arbitrates.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Callable

import discord

from ai.models import InterpretedAction, PublicEffects
from bot.action_pipeline import (
    ActionPipeline,
    ActionPipelineResult,
    AmbiguityResult,
    UnknownEntityResult,
)
from bot.embeds.combat_embed import build_combat_embed
from bot.embeds.combat_start_embed import build_combat_start_embed
from bot.embeds.dice_embed import (
    build_attack_roll_embed,
    build_generic_check_embed,
    build_save_check_embed,
)
from bot.embeds.narrative_embed import build_narrative_embed
from bot.story_bible_logger import record_turn_and_maybe_check
from bot.views.combat_action_view import CombatActionView
from engine.character import add_xp, check_level_up
from engine.combat import (
    CombatEndReason,
    CombatSide,
    Combatant,
    CombatState,
    PhaseTransitionEvent,
    advance_turn,
    check_combat_end,
    get_current_combatant,
    record_combat_event,
    resolve_npc_attack,
)
from ai.narrator_phase import narrate_phase_transition
from engine.combat_trigger import CombatTrigger
from engine.npc_ai.scripted import (
    NPCActionPlan,
    decide_minion_action,
    execute_action_plan,
)
from engine.npc_stat_block import NPCTier
from engine.validators import ActionType

if TYPE_CHECKING:
    from bot.game_session import GameSession

logger = logging.getLogger(__name__)


_TIMEOUT_SECONDS = 300
_XP_PER_ENEMY = 100


class TurnManager:
    """Own a combat encounter's Discord lifecycle end to end.

    One instance per active combat. Created by ``CombatCog.build_turn_manager``
    and stored on ``session.combat_turn_manager`` by the ActionHandlerCog
    right after the pipeline bootstraps a fresh ``CombatState``.
    """

    def __init__(
        self,
        *,
        channel: discord.abc.Messageable,
        session: "GameSession",
        pipeline_factory: Callable[..., ActionPipeline],
    ) -> None:
        self.channel = channel
        self.session = session
        self.pipeline_factory = pipeline_factory
        self.hub_message: discord.Message | None = None
        self.pending_timeout: asyncio.Task[None] | None = None
        self.current_view: CombatActionView | None = None
        self._finalized = False

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    async def start(self, trigger: CombatTrigger) -> None:
        """Post the combat-start banner. Called once after bootstrap."""
        if self.session.combat_state is None:
            logger.warning("TurnManager.start with no combat_state — ignoring")
            return
        banner = build_combat_start_embed(self.session.combat_state, trigger)
        try:
            await self.channel.send(embed=banner)
        except discord.HTTPException as exc:
            logger.warning("TurnManager start banner failed: %s", exc)

    async def on_action_resolved(
        self,
        result: ActionPipelineResult | None = None,
    ) -> None:
        """Called after any pipeline.process() finishes while combat is live.

        Free-text actions come in via :class:`bot.cogs.action_handler.ActionHandlerCog`,
        which already posted the narrative embed; button clicks come in via
        :meth:`dispatch_action`, which posted the narrative embed itself.
        Either way, the action is done, the turn must advance, pending
        off-turn cues must be flushed, and the next turn prompted.
        """
        del result  # Only used by callers for diagnostics; we read state directly.
        self._cancel_timeout()

        state = self.session.combat_state
        if state is None or not state.is_active:
            await self._finalize()
            return

        advance_turn(state)
        await self._flush_pending_cues(state)

        if check_combat_end(state) is not None or not state.is_active:
            await self._finalize()
            return

        current = get_current_combatant(state)
        await self._prompt_turn(current)

    async def dispatch_action(self, action: InterpretedAction) -> None:
        """Run one interpreted action through the pipeline and advance the turn.

        Used by the :class:`CombatActionView` button callbacks and by the
        auto-Dodge timeout watcher. Free-text actions come in through
        :class:`ActionHandlerCog` and bypass this method — the cog handles
        them directly and then calls :meth:`on_action_resolved`.
        """
        self._cancel_timeout()

        if self.session.combat_state is None:
            return

        async with self.session.action_lock:
            pipeline = self._build_pipeline()
            try:
                result = await pipeline.process_interpreted_action(action)
            except Exception as exc:
                logger.exception(
                    "TurnManager dispatch_action failed: %s", exc,
                )
                await self._safe_send("❌ L'action n'a pas pu être résolue.")
                return

            await self._render_pipeline_result(pipeline, result, action)

        await self.on_action_resolved()

    # ------------------------------------------------------------------
    # Turn prompting
    # ------------------------------------------------------------------

    async def _prompt_turn(self, combatant: Combatant) -> None:
        """Post or edit the hub for ``combatant``'s turn."""
        state = self.session.combat_state
        if state is None:
            return

        if combatant.side == CombatSide.PLAYER:
            await self._prompt_pc_turn(combatant, state)
            return

        await self._prompt_npc_turn(combatant, state)

    async def _prompt_pc_turn(
        self, combatant: Combatant, state: CombatState,
    ) -> None:
        user_id = self._find_user_id(combatant.name)
        if user_id is None:
            logger.warning(
                "TurnManager: no user_id for PC combatant %s — skipping",
                combatant.name,
            )
            advance_turn(state)
            if check_combat_end(state) is not None:
                await self._finalize()
                return
            next_combatant = get_current_combatant(state)
            await self._prompt_turn(next_combatant)
            return

        target_names = [
            c.name
            for c in state.combatants
            if c.is_alive and not c.fled and c.side == CombatSide.ENEMY
        ]
        spell_names = self._get_castable_spell_names(user_id)
        adjacent_zones = self._get_adjacent_zones(combatant)

        view = CombatActionView(
            user_id=user_id,
            actor_name=combatant.name,
            target_names=target_names,
            spell_names=spell_names,
            adjacent_zone_names=adjacent_zones,
            dispatch_callback=self.dispatch_action,
        )
        self.current_view = view

        embed = build_combat_embed(state, location=self.session.current_location)
        content = f"<@{user_id}> — c'est ton tour."
        await self._upsert_hub(content=content, embed=embed, view=view)

        self.pending_timeout = asyncio.create_task(
            self._timeout_watcher(combatant.name),
        )

    async def _prompt_npc_turn(
        self, combatant: Combatant, state: CombatState,
    ) -> None:
        embed = build_combat_embed(state, location=self.session.current_location)
        content = f"👹 Tour de **{combatant.name}**"
        await self._upsert_hub(content=content, embed=embed, view=None)
        # Small pause so players can read the hub before the NPC acts.
        await asyncio.sleep(0.8)
        await self._resolve_npc_turn(combatant)

    # ------------------------------------------------------------------
    # NPC resolution
    # ------------------------------------------------------------------

    async def _resolve_npc_turn(self, combatant: Combatant) -> None:
        """Dispatch brain, execute plan, post dice embed + mechanics summary."""
        state = self.session.combat_state
        if state is None:
            return

        plan = self._dispatch_npc_brain(combatant, state)

        dice_embed: discord.Embed | None = None
        if plan.action_type == ActionType.ATTACK and plan.signature_name is None:
            dice_embed = self._resolve_npc_attack_with_embed(combatant, plan)

        if dice_embed is None:
            summary = execute_action_plan(
                combatant, plan, state, self.session.current_location,
            )
        else:
            # _resolve_npc_attack_with_embed already consumed the action and
            # produced a summary; reuse it below.
            summary = getattr(self, "_last_npc_summary", "") or (
                f"{combatant.name} → {plan.target_name or '?'}"
            )

        await self._safe_send(content=f"📜 {summary}")
        if dice_embed is not None:
            await self._safe_send(embed=dice_embed)

        # Task 70 — expose the NPC's action to the narrator via recent_events.
        if summary:
            record_combat_event(state, summary)

        await record_turn_and_maybe_check(
            self.session,
            user_name=combatant.name,
            command=f"combat:npc:{plan.action_type.value.lower()}",
            args=plan.rationale,
            mechanics=summary,
            narrative=summary,
        )

        await self.on_action_resolved()

    def _resolve_npc_attack_with_embed(
        self,
        combatant: Combatant,
        plan: NPCActionPlan,
    ) -> discord.Embed | None:
        """Roll the NPC attack manually so we can surface the dice embed.

        Mirrors :func:`engine.npc_ai.scripted._execute_attack` step by step,
        but captures the :class:`~engine.combat.AttackResult` to build a
        :func:`build_attack_roll_embed`. Returns ``None`` when the plan is
        malformed — the caller falls back on ``execute_action_plan`` which
        produces a descriptive text summary without mutating state.
        """
        state = self.session.combat_state
        if state is None or combatant.stat_block is None:
            return None

        target = next(
            (c for c in state.combatants if c.name == plan.target_name and c.is_alive),
            None,
        )
        if target is None:
            return None

        npc_attack = next(
            (a for a in combatant.stat_block.attacks if a.name == plan.weapon_name),
            None,
        )
        if npc_attack is None:
            return None

        from engine.combat import consume_action

        consume_action(combatant)
        result = resolve_npc_attack(combatant, target, npc_attack)

        if result.hit:
            summary = (
                f"{combatant.name} touche {target.name} avec {npc_attack.name} "
                f"— {result.damage} dégâts"
            )
        else:
            summary = (
                f"{combatant.name} rate {target.name} avec {npc_attack.name}"
            )
        self._last_npc_summary = summary

        return build_attack_roll_embed(result, combatant.name)

    def _dispatch_npc_brain(
        self, combatant: Combatant, state: CombatState,
    ) -> NPCActionPlan:
        """Pick the right brain for the combatant's tier.

        Minions use the scripted heuristic, elites use the behavior-profile
        brain, and bosses use the LLM tactician with a scripted fallback
        (requires an Ollama client on the session). NPCs without a stat
        block fall back on minion logic for legacy safety.
        """
        location = self.session.current_location
        if combatant.stat_block is None:
            return decide_minion_action(combatant, state, location)

        tier = combatant.stat_block.tier
        if tier == NPCTier.MINION:
            return decide_minion_action(combatant, state, location)

        if tier == NPCTier.ELITE:
            from engine.npc_ai.elite import decide_elite_action

            return decide_elite_action(combatant, state, location)

        # Boss tier — LLM tactician when available, scripted fallback
        # otherwise. The boss_brain module handles retries internally.
        return self._decide_boss_action(combatant, state)

    def _decide_boss_action(
        self, combatant: Combatant, state: CombatState,
    ) -> NPCActionPlan:
        from engine.npc_ai.boss_brain import decide_boss_action
        from engine.npc_ai.elite import decide_elite_action

        client = getattr(self.session, "ollama_client", None)
        if client is None:
            return decide_elite_action(combatant, state, self.session.current_location)

        try:
            from ai.npc_tactician import NPCTactician

            tactician = NPCTactician(client)
            party_context = (
                self.session.current_location.name
                if self.session.current_location is not None
                else ""
            )
            return decide_boss_action(
                combatant=combatant,
                state=state,
                location=self.session.current_location,
                tactician=tactician,
                party_context=party_context,
                recent_events=[],
                language=self.session.language,
            )
        except Exception as exc:
            logger.warning(
                "Boss tactician failed (%s) — falling back on elite", exc,
            )
            return decide_elite_action(combatant, state, self.session.current_location)

    # ------------------------------------------------------------------
    # Pipeline result rendering
    # ------------------------------------------------------------------

    async def _render_pipeline_result(
        self,
        pipeline: ActionPipeline,
        result: Any,
        action: InterpretedAction,
    ) -> None:
        """Post the right embed for a ``pipeline.process_interpreted_action`` output."""
        if isinstance(result, ActionPipelineResult):
            embed = build_narrative_embed(
                narrative=result.narrative,
                public_effects=result.public_effects,
                tone=result.tone,
                npc_name=result.npc_name,
                npc_dialogue=result.npc_dialogue,
            )
            await self._safe_send(embed=embed)
            await self._flush_dice_embeds(pipeline, action.actor_name)

            await record_turn_and_maybe_check(
                self.session,
                user_name=action.actor_name,
                command=f"combat:{action.action_type.value.lower()}",
                args=action.raw_input[:120],
                mechanics=result.mechanics_text,
                narrative=result.narrative,
            )
        elif isinstance(result, UnknownEntityResult):
            embed = build_narrative_embed(
                narrative=result.refusal_narrative,
                tone=result.tone,
                footer_override=(
                    f"⚠️ {result.field_name}: "
                    f"'{result.raw_value}' introuvable."
                ),
            )
            await self._safe_send(embed=embed)
        elif isinstance(result, AmbiguityResult):
            # Buttons pre-resolve targets, so ambiguity should be very rare.
            await self._safe_send(
                "⚠️ L'action est ambiguë — réessaie via une commande texte.",
            )

    async def _flush_dice_embeds(
        self, pipeline: ActionPipeline, actor_name: str,
    ) -> None:
        """Surface any D20CheckResult stashed on the pipeline as an embed."""
        dice_embeds = getattr(pipeline, "_pending_dice_embeds", None) or []
        for entry in dice_embeds:
            if not isinstance(entry, tuple) or len(entry) < 2:
                continue
            kind = entry[0]
            check = entry[1]
            name = entry[2] if len(entry) >= 3 else actor_name
            if kind == "flee_check":
                embed = build_save_check_embed(
                    check, label="Tentative de fuite", actor_name=name, ability="DEX",
                )
            else:
                embed = build_generic_check_embed(
                    check, label=str(kind).replace("_", " ").title(), actor_name=name,
                )
            await self._safe_send(embed=embed)
        pipeline._pending_dice_embeds.clear()

    # ------------------------------------------------------------------
    # Hub management
    # ------------------------------------------------------------------

    async def _upsert_hub(
        self,
        *,
        content: str,
        embed: discord.Embed,
        view: discord.ui.View | None,
    ) -> None:
        send_view: Any = view if view is not None else discord.utils.MISSING
        if self.hub_message is None:
            try:
                self.hub_message = await self.channel.send(
                    content=content, embed=embed, view=send_view,
                )
            except discord.HTTPException as exc:
                logger.warning("TurnManager hub send failed: %s", exc)
            return

        try:
            await self.hub_message.edit(
                content=content, embed=embed, view=send_view,
            )
        except discord.NotFound:
            # Hub was deleted — repost a fresh one.
            logger.info("TurnManager hub was deleted, reposting")
            try:
                self.hub_message = await self.channel.send(
                    content=content, embed=embed, view=send_view,
                )
            except discord.HTTPException as exc:
                logger.warning("TurnManager hub repost failed: %s", exc)
        except discord.HTTPException as exc:
            logger.warning("TurnManager hub edit failed: %s", exc)

    async def _flush_pending_cues(self, state: CombatState) -> None:
        """Post compact messages for legendary + phase cues queued on state.

        Legendary summaries stay as plain text (they are short recaps of
        off-turn actions). Phase transitions now go through the dedicated
        narrator path (task 71) and are posted as gold embeds. If the
        narrator call fails we fall back to the raw ``narrative_cue`` so
        the moment still shows up in chat, and we always mark the event
        ``consumed=True`` — even on failure — to avoid infinite retries on
        subsequent flushes.
        """
        for cue in state.pending_legendary_summaries:
            await self._safe_send(f"⚡ {cue}")
        state.pending_legendary_summaries.clear()

        for event in state.pending_phase_narrations:
            if event.consumed:
                continue
            event.consumed = True  # mark BEFORE the LLM call — no retries
            boss = next(
                (
                    c for c in state.combatants
                    if c.name == event.combatant_name
                ),
                None,
            )
            if boss is None:
                continue
            await self._post_phase_transition_embed(event, boss, state)

        # Events are kept on ``pending_phase_narrations`` (flagged consumed)
        # so a serialized state round-trip after a mid-flush crash does not
        # re-narrate them. Clearing here would defeat that safety; the list
        # is small (one or two entries per round at most) and naturally dies
        # with the combat state.

    async def _post_phase_transition_embed(
        self,
        event: PhaseTransitionEvent,
        boss: Combatant,
        state: CombatState,
    ) -> None:
        """Narrate one phase-transition event and post it as a gold embed."""
        client = getattr(self.session, "ollama_client", None)
        narration = ""
        if client is not None:
            try:
                narration = await asyncio.to_thread(
                    narrate_phase_transition,
                    client=client,
                    event=event,
                    boss=boss,
                    state=state,
                    language=self.session.language,
                )
            except Exception as exc:  # noqa: BLE001 — fallback on any LLM error
                logger.warning(
                    "Phase narration failed for %s phase %d: %s",
                    event.combatant_name, event.phase_index, exc,
                )
        if not narration:
            narration = event.narrative_cue or (
                f"{event.combatant_name} entre dans une nouvelle phase."
            )
        embed = discord.Embed(
            title=f"✨ Phase transition — {boss.name}",
            description=narration,
            color=0xF1C40F,  # gold
        )
        await self._safe_send(embed=embed)

    # ------------------------------------------------------------------
    # Timeout watcher
    # ------------------------------------------------------------------

    async def _timeout_watcher(self, actor_name: str) -> None:
        try:
            await asyncio.sleep(_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            return

        await self._safe_send(
            f"⏱️ **{actor_name}** n'a pas agi à temps — Défense automatique.",
        )
        auto_action = InterpretedAction(
            action_type=ActionType.DEFEND,
            actor_name=actor_name,
            raw_input="(auto-dodge sur timeout)",
        )
        try:
            await self.dispatch_action(auto_action)
        except Exception as exc:
            logger.exception("Auto-dodge dispatch failed: %s", exc)

    def _cancel_timeout(self) -> None:
        if self.pending_timeout is not None and not self.pending_timeout.done():
            self.pending_timeout.cancel()
        self.pending_timeout = None

    # ------------------------------------------------------------------
    # Finalize
    # ------------------------------------------------------------------

    async def _finalize(self) -> None:
        """Close the encounter: XP stub, hub frozen, session cleanup.

        Phase 8 task 80 will replace this with the full end-of-combat flow
        (loot, level-ups, truce handling). For Phase 6 we port the minimal
        XP split from the legacy cog so playtests can still level up.
        """
        if self._finalized:
            return
        self._finalized = True
        self._cancel_timeout()

        state = self.session.combat_state
        end_reason = state.end_reason if state is not None else None
        xp_each, level_ups = self._apply_xp_stub(state)

        reason_labels: dict[CombatEndReason, str] = {
            CombatEndReason.VICTORY: "🏆 Victoire",
            CombatEndReason.DEFEAT: "💀 Défaite",
            CombatEndReason.FLED: "🏃 Échappés",
            CombatEndReason.TRUCE: "🕊️ Trêve",
        }
        reason_label = (
            reason_labels[end_reason]
            if end_reason is not None
            else "Combat terminé"
        )

        lines = [f"**{reason_label}**"]
        if xp_each > 0:
            lines.append(f"XP gagné : **{xp_each}** par survivant")
        if level_ups:
            lines.append("Niveau disponible pour : " + ", ".join(level_ups))

        await self._safe_send(content="\n".join(lines))

        if self.hub_message is not None and state is not None:
            try:
                frozen = build_combat_embed(
                    state, location=self.session.current_location,
                )
                await self.hub_message.edit(
                    content=f"{reason_label} — combat terminé.",
                    embed=frozen,
                    view=None,
                )
            except discord.HTTPException as exc:
                logger.debug("TurnManager hub freeze failed: %s", exc)

        self.session.combat_turn_manager = None
        self.session.combat_state = None

    def _apply_xp_stub(
        self, state: CombatState | None,
    ) -> tuple[int, list[str]]:
        """Mirror the legacy XP_PER_ENEMY split — minimal port for Phase 6."""
        if state is None:
            return 0, []
        dead_enemies = sum(
            1
            for c in state.combatants
            if c.side == CombatSide.ENEMY and not c.is_alive
        )
        xp_total = dead_enemies * _XP_PER_ENEMY
        survivors = [
            c
            for c in state.combatants
            if c.side == CombatSide.PLAYER and c.is_alive
        ]
        if not survivors:
            return 0, []
        xp_each = xp_total // max(len(survivors), 1)
        level_ups: list[str] = []
        for combatant in survivors:
            if xp_each > 0:
                add_xp(combatant.character, xp_each)
            if check_level_up(combatant.character):
                level_ups.append(combatant.name)
        return xp_each, level_ups

    # ------------------------------------------------------------------
    # Session helpers
    # ------------------------------------------------------------------

    def _find_user_id(self, character_name: str) -> int | None:
        for uid, char in self.session.characters.items():
            if char.name == character_name:
                return uid
        return None

    def _get_castable_spell_names(self, user_id: int) -> list[str]:
        state = self.session.combat_state
        if state is None:
            return []
        spellcaster = self.session.spellcasters.get(user_id)
        if spellcaster is None:
            return []
        from engine.spells import SPELL_CATALOG, can_cast_spell

        castable: list[str] = []
        for spell_name in spellcaster.spells_known:
            spell = SPELL_CATALOG.get(spell_name)
            if spell is not None and can_cast_spell(spellcaster, spell):
                castable.append(spell_name)
        return castable

    def _get_adjacent_zones(self, combatant: Combatant) -> list[str]:
        location = self.session.current_location
        if location is None or not location.has_combat_zones():
            return []
        if combatant.current_zone is None:
            return []
        current = location.get_zone(combatant.current_zone)
        if current is None:
            return []
        return list(current.adjacent_zone_names)

    def _build_pipeline(self) -> ActionPipeline:
        user_id = self._find_user_id(
            get_current_combatant(self.session.combat_state).name,  # type: ignore[arg-type]
        ) if self.session.combat_state is not None else None

        inventory = (
            self.session.inventories.get(user_id) if user_id is not None else None
        )
        return self.pipeline_factory(
            interpreter=self.session.interpreter,
            narrator=self.session.narrator,
            location=self.session.current_location,
            npcs=self.session.npcs,
            actor_name=(
                get_current_combatant(self.session.combat_state).name
                if self.session.combat_state is not None
                else ""
            ),
            language=self.session.language,
            campaign_id=self.session.campaign.id,
            combat_state=self.session.combat_state,
            inventory=inventory,
            session=self.session,
            db_factory=None,
        )

    async def _safe_send(
        self,
        content: str | None = None,
        *,
        embed: discord.Embed | None = None,
    ) -> None:
        """Send a channel message swallowing HTTP failures."""
        send_content: Any = content if content is not None else discord.utils.MISSING
        send_embed: Any = embed if embed is not None else discord.utils.MISSING
        try:
            await self.channel.send(content=send_content, embed=send_embed)
        except discord.HTTPException as exc:
            logger.warning("TurnManager channel send failed: %s", exc)


# Exported so ``bot.cogs.combat`` can depend on the class without a
# circular path via ``bot.views``.
__all__ = ["TurnManager"]

# Silence the unused-import warning on PublicEffects — the import is
# kept because downstream type-checkers appreciate the explicit symbol.
_PE = PublicEffects
