"""Combat cog — manages combat lifecycle and turn resolution."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from bot.embeds.combat_embed import build_combat_embed
from bot.embeds.narrative_embed import build_narrative_embed
from bot.story_bible_logger import record_turn_and_maybe_check
from bot.views.combat_view import CombatView
from bot.views.spell_select import SpellSelectView
from bot.views.target_select import TargetSelectView
from engine.character import add_xp, check_level_up
from engine.combat import (
    CombatSide,
    Combatant,
    advance_turn,
    get_current_combatant,
    is_combat_over,
    resolve_attack,
    resolve_spell,
    start_combat,
)
from engine.character import (
    Ability,
    Character,
    CharacterClass,
    Size,
)
from engine.inventory import (
    EquipmentSlot,
    Weapon,
    add_item,
    create_inventory,
    default_weapon_for_class,
    equip_item,
)
from engine.spells import SPELL_CATALOG, can_cast_spell
from world.npc import NPC

if TYPE_CHECKING:
    from bot.bot import RealmBot
    from bot.game_session import GameSession

logger = logging.getLogger(__name__)

XP_PER_ENEMY = 100


def build_pc_combatants(session: "GameSession") -> list[Combatant]:
    """Convert all PCs of a session into PLAYER-side Combatants."""
    combatants: list[Combatant] = []
    for user_id, char in session.characters.items():
        inv = session.inventories.get(user_id)
        spell = session.spellcasters.get(user_id)
        combatants.append(
            Combatant(
                name=char.name,
                side=CombatSide.PLAYER,
                character=char,
                inventory=inv if inv is not None else create_inventory(),
                spellcaster=spell,
            )
        )
    return combatants


def build_npc_combatant(npc: NPC) -> Combatant:
    """Wrap an NPC into an ENEMY-side Combatant for bootstrap encounters.

    NPCs do not carry full Character stats — fill in sensible defaults so
    the engine can drive a fight (Lot C). Inventory is empty (NPC fights
    barehanded for now); a future lot may attach default weapons.
    """
    char_class = npc.char_class or CharacterClass.FIGHTER
    char = Character(
        name=npc.name,
        race=npc.race,
        char_class=char_class,
        level=npc.level,
        ability_scores=npc.ability_scores,
        hp=npc.hp,
        max_hp=npc.max_hp,
        ac=npc.ac,
        speed=30,
        proficiency_bonus=2,
        saving_throw_proficiencies=(Ability.STR, Ability.CON),
        hit_die="1d8",
        size=Size.MEDIUM,
    )
    # Equip a default weapon based on class
    weapon = default_weapon_for_class(char_class)
    inv = create_inventory()
    inv = add_item(inv, weapon)
    inv = equip_item(inv, weapon.name, EquipmentSlot.MAIN_HAND)

    return Combatant(
        name=npc.name,
        side=CombatSide.ENEMY,
        character=char,
        inventory=inv,
    )


class CombatCog(commands.Cog):
    """Combat lifecycle: start, turns, resolution."""

    def __init__(self, bot: RealmBot) -> None:
        self.bot = bot

    async def start_combat_encounter(
        self,
        channel: discord.TextChannel,
        session: GameSession,
        enemies: list[Combatant],
    ) -> None:
        """Start a combat encounter. Called by other cogs (e.g. exploration)."""
        combatants: list[Combatant] = build_pc_combatants(session)
        combatants.extend(enemies)
        state = start_combat(combatants)
        session.combat_state = state

        player_count = sum(1 for c in state.combatants if c.side == CombatSide.PLAYER)
        enemy_count = sum(1 for c in state.combatants if c.side == CombatSide.ENEMY)
        logger.info(
            "COMBAT start enemies=%d players=%d campaign=%s",
            enemy_count, player_count, session.campaign.id,
        )

        embed = build_combat_embed(state)
        await channel.send("Combat !", embed=embed)
        await self._prompt_turn(channel, session)

    async def _prompt_turn(
        self,
        channel: discord.TextChannel,
        session: GameSession,
    ) -> None:
        """Prompt the current combatant for their action."""
        state = session.combat_state
        if state is None or not state.is_active:
            return

        current = get_current_combatant(state)

        if current.side == CombatSide.ENEMY:
            await self._resolve_enemy_turn(channel, session, current)
            return

        user_id = self._find_user_id(session, current.name)
        if user_id is None:
            advance_turn(state)
            if is_combat_over(state):
                await self._end_combat(channel, session)
            else:
                await self._prompt_turn(channel, session)
            return

        view = CombatView(session, user_id)
        await channel.send(
            f"<@{user_id}>, c'est ton tour ! Choisis ton action:",
            view=view,
        )

        timed_out = await view.wait()
        if timed_out:
            await channel.send(f"**{current.name}** defend par defaut (timeout).")
            advance_turn(state)
            if is_combat_over(state):
                await self._end_combat(channel, session)
            else:
                await self._prompt_turn(channel, session)
            return

        await self._resolve_player_action(channel, session, current, user_id, view.action)

    async def _resolve_player_action(
        self,
        channel: discord.TextChannel,
        session: GameSession,
        combatant: Combatant,
        user_id: int,
        action: str | None,
    ) -> None:
        """Resolve the player's chosen action."""
        state = session.combat_state
        if state is None:
            return

        if action == "attack":
            await self._handle_attack(channel, session, combatant, user_id)
        elif action == "cast_spell":
            await self._handle_cast_spell(channel, session, combatant, user_id)
        elif action == "defend":
            mechanics = f"{combatant.name} se met en defense."
            narrative, tone = await self._narrate(session, mechanics)
            embed = build_narrative_embed(narrative, tone=tone, footer_override=mechanics)
            await channel.send(embed=embed)
            await record_turn_and_maybe_check(
                session,
                user_name=combatant.name,
                command="combat:defend",
                args="",
                mechanics=mechanics,
                narrative=narrative,
            )
        elif action == "flee":
            mechanics = f"{combatant.name} tente de fuir !"
            narrative, tone = await self._narrate(session, mechanics)
            embed = build_narrative_embed(narrative, tone=tone, footer_override=mechanics)
            await channel.send(embed=embed)
            await record_turn_and_maybe_check(
                session,
                user_name=combatant.name,
                command="combat:flee",
                args="",
                mechanics=mechanics,
                narrative=narrative,
            )

        advance_turn(state)
        if is_combat_over(state):
            await self._end_combat(channel, session)
        else:
            embed = build_combat_embed(state)
            await channel.send(embed=embed)
            await self._prompt_turn(channel, session)

    async def _handle_attack(
        self,
        channel: discord.TextChannel,
        session: GameSession,
        combatant: Combatant,
        user_id: int,
    ) -> None:
        """Handle attack: target select then resolve."""
        state = session.combat_state
        if state is None:
            return

        targets = [
            (c.name, f"HP: {c.character.hp}/{c.character.max_hp}")
            for c in state.combatants
            if c.is_alive and c.side == CombatSide.ENEMY
        ]
        if not targets:
            await channel.send("Aucune cible disponible.")
            return

        view = TargetSelectView(targets)
        await channel.send("Choisis ta cible:", view=view)
        timed_out = await view.wait()
        if timed_out or not view.selected_target:
            await channel.send("Pas de cible selectionnee — action annulee.")
            return

        target = next(
            (c for c in state.combatants if c.name == view.selected_target and c.is_alive),
            None,
        )
        if target is None:
            await channel.send("Cible introuvable.")
            return

        weapon = self._get_equipped_weapon(session, user_id)
        if weapon is None:
            await channel.send("Aucune arme equipee !")
            return

        result = resolve_attack(combatant, target, weapon)
        mechanics = f"{combatant.name} attaque {target.name}: "
        if result.hit:
            mechanics += f"Touche ({result.outcome.value}) — {result.damage} degats"
        else:
            mechanics += f"Rate ({result.outcome.value})"

        hit_str = "CRIT" if result.critical else ("HIT" if result.hit else "MISS")
        logger.info(
            "COMBAT action=attack player=%s target=%s roll=%d vs AC=%d -> %s damage=%d outcome=%s",
            combatant.name, target.name,
            result.attack_roll, target.character.ac, hit_str, result.damage,
            result.outcome.value,
        )

        narrative, tone = await self._narrate(session, mechanics)
        embed = build_narrative_embed(narrative, tone=tone, footer_override=mechanics)
        await channel.send(embed=embed)
        await record_turn_and_maybe_check(
            session,
            user_name=combatant.name,
            command="combat:attack",
            args=f'target="{target.name}"',
            mechanics=mechanics,
            narrative=narrative,
        )

    async def _handle_cast_spell(
        self,
        channel: discord.TextChannel,
        session: GameSession,
        combatant: Combatant,
        user_id: int,
    ) -> None:
        """Handle spell cast: spell select, target select, resolve."""
        state = session.combat_state
        if state is None:
            return

        spellcaster = session.spellcasters.get(user_id)
        if spellcaster is None:
            await channel.send("Tu n'es pas un lanceur de sorts.")
            return

        castable = []
        for spell_name in spellcaster.spells_known:
            spell = SPELL_CATALOG.get(spell_name)
            if spell and can_cast_spell(spellcaster, spell):
                desc = f"Niv. {spell.level}"
                if spell.damage_dice:
                    desc += f" — {spell.damage_dice}"
                if spell.healing_dice:
                    desc += f" — Soin {spell.healing_dice}"
                castable.append((spell_name, desc))

        if not castable:
            await channel.send("Aucun sort disponible.")
            return

        spell_view = SpellSelectView(castable)
        await channel.send("Choisis ton sort:", view=spell_view)
        timed_out = await spell_view.wait()
        if timed_out or not spell_view.selected_spell:
            return

        spell = SPELL_CATALOG.get(spell_view.selected_spell)
        if spell is None:
            return

        target = None
        if spell.damage_dice or spell.saving_throw:
            enemies = [
                (c.name, f"HP: {c.character.hp}/{c.character.max_hp}")
                for c in state.combatants
                if c.is_alive and c.side == CombatSide.ENEMY
            ]
            if enemies:
                target_view = TargetSelectView(enemies)
                await channel.send("Choisis ta cible:", view=target_view)
                timed_out = await target_view.wait()
                if timed_out or not target_view.selected_target:
                    return
                target = next(
                    (c for c in state.combatants if c.name == target_view.selected_target),
                    None,
                )

        result = resolve_spell(combatant, spell, target, spell.level)
        logger.info(
            "COMBAT action=cast_spell player=%s spell=%s target=%s damage=%d healing=%d",
            combatant.name, spell.name,
            target.name if target else "self",
            result.damage, result.healing,
        )
        mechanics = f"{combatant.name} lance {spell.name}"
        if result.damage:
            mechanics += f" — {result.damage} degats"
        if result.healing:
            mechanics += f" — {result.healing} PV soignes"

        narrative, tone = await self._narrate(session, mechanics)
        embed = build_narrative_embed(narrative, tone=tone, footer_override=mechanics)
        await channel.send(embed=embed)
        await record_turn_and_maybe_check(
            session,
            user_name=combatant.name,
            command="combat:cast_spell",
            args=f'spell="{spell.name}"'
            + (f' target="{target.name}"' if target else ""),
            mechanics=mechanics,
            narrative=narrative,
        )

    async def _resolve_enemy_turn(
        self,
        channel: discord.TextChannel,
        session: GameSession,
        enemy: Combatant,
    ) -> None:
        """Simple enemy AI: attack the first living player."""
        state = session.combat_state
        if state is None:
            return

        players = [c for c in state.combatants if c.is_alive and c.side == CombatSide.PLAYER]
        if not players:
            advance_turn(state)
            return

        target = players[0]
        weapon = self._get_combatant_weapon(enemy)
        if weapon is None:
            mechanics = f"{enemy.name} n'a pas d'arme et passe son tour."
            await channel.send(mechanics)
            advance_turn(state)
        else:
            result = resolve_attack(enemy, target, weapon)
            hit_str = "HIT" if result.hit else "MISS"
            logger.info(
                "COMBAT enemy=%s target=%s roll=%d vs AC=%d -> %s damage=%d outcome=%s",
                enemy.name, target.name,
                result.attack_roll, target.character.ac, hit_str, result.damage,
                result.outcome.value,
            )
            mechanics = f"{enemy.name} attaque {target.name}: "
            if result.hit:
                mechanics += f"Touche ({result.outcome.value}) — {result.damage} degats"
            else:
                mechanics += f"Rate ({result.outcome.value})"

            narrative, tone = await self._narrate(session, mechanics)
            embed = build_narrative_embed(narrative, tone=tone, footer_override=mechanics)
            await channel.send(embed=embed)
            await record_turn_and_maybe_check(
                session,
                user_name=enemy.name,
                command="combat:enemy_turn",
                args=f'target="{target.name}"',
                mechanics=mechanics,
                narrative=narrative,
            )
            advance_turn(state)

        if is_combat_over(state):
            await self._end_combat(channel, session)
        else:
            embed = build_combat_embed(state)
            await channel.send(embed=embed)
            await self._prompt_turn(channel, session)

    async def _end_combat(
        self,
        channel: discord.TextChannel,
        session: GameSession,
    ) -> None:
        """End combat and distribute XP."""
        state = session.combat_state
        if state is None:
            return

        dead_enemies = sum(
            1 for c in state.combatants
            if c.side == CombatSide.ENEMY and not c.is_alive
        )
        xp_total = dead_enemies * XP_PER_ENEMY
        survivors = [c for c in state.combatants if c.side == CombatSide.PLAYER and c.is_alive]
        xp_each = xp_total // max(len(survivors), 1)

        level_ups = []
        for combatant in survivors:
            add_xp(combatant.character, xp_each)
            if check_level_up(combatant.character):
                level_ups.append(combatant.name)

        session.combat_state = None

        logger.info(
            "COMBAT end xp=%d survivors=%d levelups=%s",
            xp_each, len(survivors), level_ups,
        )

        msg = f"Combat termine ! {xp_each} XP par survivant."
        if level_ups:
            msg += f"\nNiveau disponible pour: {', '.join(level_ups)}"
        await channel.send(msg)

    async def _narrate(
        self, session: GameSession, mechanics: str,
    ) -> tuple[str, str]:
        """Narrate with AI or fallback to raw mechanics."""
        if session.narrator is None:
            return mechanics, "dramatic"
        try:
            result = await asyncio.to_thread(
                session.narrator.narrate, mechanics, "", session.language,
            )
            return result.narrative, result.tone
        except Exception:
            return mechanics, "dramatic"

    @staticmethod
    def _find_user_id(session: GameSession, character_name: str) -> int | None:
        """Find Discord user ID from character name."""
        for uid, char in session.characters.items():
            if char.name == character_name:
                return uid
        return None

    @staticmethod
    def _get_equipped_weapon(session: GameSession, user_id: int) -> Weapon | None:
        """Get the weapon equipped in main hand."""
        inv = session.inventories.get(user_id)
        if inv is None:
            return None
        main_hand = inv.equipped.get(EquipmentSlot.MAIN_HAND)
        if isinstance(main_hand, Weapon):
            return main_hand
        return None

    @staticmethod
    def _get_combatant_weapon(combatant: Combatant) -> Weapon | None:
        """Get weapon from a combatant's inventory."""
        main_hand = combatant.inventory.equipped.get(EquipmentSlot.MAIN_HAND)
        if isinstance(main_hand, Weapon):
            return main_hand
        return None


async def setup(bot: commands.Bot) -> None:
    """Register the cog with the bot."""
    await bot.add_cog(CombatCog(bot))  # type: ignore[arg-type]
