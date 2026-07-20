"""ScenarioRunner — orchestrates multi-step gameplay scenarios for testing.

Wraps cog handlers behind a clean API, manages mock Discord context,
and captures bot responses for assertions. Uses real engine + DB,
mocked Discord interactions, AI disabled.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
from sqlalchemy.orm import Session, sessionmaker

from bot.action_pipeline import ActionPipeline
from bot.cogs.character import CharacterCog
from bot.cogs.combat import CombatCog
from bot.cogs.inventory import InventoryCog
from bot.cogs.rolls import RollsCog
from bot.cogs.session import SessionCog
from bot.game_session import GameSession
from ai.client import OllamaClient
from ai.interpreter import Interpreter
from ai.narrator import Narrator
from engine.character import (
    Character,
    CharacterClass,
    Race,
    apply_racial_bonuses,
    create_character,
    roll_ability_scores,
)
from engine.combat import CombatSide, Combatant
from engine.inventory import (
    EquipmentSlot,
    Inventory,
    Weapon,
    create_inventory,
)
from engine.spells import create_spellcaster_state
from world.campaign import Campaign
from world.location import Location


def _resolve_direction(loc: Location, direction: str) -> str | None:
    """Map ``direction`` to one of ``loc.connections``.

    Checks aliases first (so ``"nord"`` resolves through
    ``exit_aliases``), then falls back to a case-insensitive exact
    match against the connection name itself. Returns ``None`` when
    nothing matches.
    """
    d = (direction or "").strip().lower()
    if not d:
        return None
    for conn, aliases in (loc.exit_aliases or {}).items():
        for alias in aliases or []:
            if alias.lower() == d:
                return conn
    for conn in loc.connections or []:
        if conn.lower() == d:
            return conn
    return None


# ---------------------------------------------------------------------------
# EmbedCapture — captures what the bot would send to Discord
# ---------------------------------------------------------------------------


@dataclass
class EmbedCapture:
    """Captures a single bot response (embed + view + text)."""

    content: str | None = None
    embed: discord.Embed | None = None
    view: discord.ui.View | None = None
    ephemeral: bool = False

    def has_field(self, name: str) -> bool:
        """Check if the embed has a field with the given name."""
        if self.embed is None:
            return False
        return any(f.name == name for f in self.embed.fields)

    def get_field_value(self, name: str) -> str | None:
        """Get the value of an embed field by name."""
        if self.embed is None:
            return None
        for f in self.embed.fields:
            if f.name == name:
                return str(f.value) if f.value is not None else None
        return None

    def button_labels(self) -> list[str]:
        """Get labels of all buttons in the attached view."""
        if self.view is None:
            return []
        labels: list[str] = []
        for child in self.view.children:
            if isinstance(child, discord.ui.Button) and child.label:
                labels.append(child.label)
        return labels

    def select_options(self) -> list[str]:
        """Get option labels from all selects in the attached view."""
        if self.view is None:
            return []
        options: list[str] = []
        for child in self.view.children:
            if isinstance(child, discord.ui.Select):
                options.extend(opt.label for opt in child.options)
        return options


# ---------------------------------------------------------------------------
# TestInteraction — fake discord.Interaction that captures responses
# ---------------------------------------------------------------------------


class _TestResponse:
    """Captures interaction.response.send_message() calls."""

    def __init__(self, capture: EmbedCapture) -> None:
        self._capture = capture
        self._responded = False

    async def send_message(
        self,
        content: str | None = None,
        *,
        embed: discord.Embed | None = None,
        view: discord.ui.View | None = None,
        ephemeral: bool = False,
        **kwargs: Any,
    ) -> None:
        self._capture.content = content
        self._capture.embed = embed
        self._capture.view = view
        self._capture.ephemeral = ephemeral
        self._responded = True

    async def defer(self, *, ephemeral: bool = False, **kwargs: Any) -> None:
        self._responded = True

    def is_done(self) -> bool:
        return self._responded


class _TestFollowup:
    """Captures interaction.followup.send() calls."""

    def __init__(self, capture: EmbedCapture) -> None:
        self._capture = capture

    async def send(
        self,
        content: str | None = None,
        *,
        embed: discord.Embed | None = None,
        view: discord.ui.View | None = None,
        ephemeral: bool = False,
        **kwargs: Any,
    ) -> None:
        self._capture.content = content
        self._capture.embed = embed
        self._capture.view = view
        self._capture.ephemeral = ephemeral


class TestInteraction:
    """Fake discord.Interaction that captures responses for testing."""

    def __init__(
        self,
        bot: MagicMock,
        guild: MagicMock,
        channel: MagicMock,
        user: MagicMock,
        namespace: dict[str, Any] | None = None,
    ) -> None:
        self._capture = EmbedCapture()
        self.client = bot
        self.guild = guild
        self.guild_id = guild.id
        self.channel = channel
        self.channel_id = channel.id
        self.user = user
        self.response = _TestResponse(self._capture)
        self.followup = _TestFollowup(self._capture)
        # Build namespace object for slash command args
        if namespace:
            ns = MagicMock()
            for k, v in namespace.items():
                setattr(ns, k, v)
            self.namespace = ns

    @property
    def captured(self) -> EmbedCapture:
        """Get the captured response."""
        return self._capture


# ---------------------------------------------------------------------------
# MockMember — consistent virtual player identity
# ---------------------------------------------------------------------------


@dataclass
class MockMember:
    """A virtual player with a stable Discord user ID."""

    id: int
    name: str
    display_name: str
    mention: str = ""

    def __post_init__(self) -> None:
        if not self.mention:
            self.mention = f"<@{self.id}>"

    def __str__(self) -> str:
        return self.name


# ---------------------------------------------------------------------------
# ChannelCapture — captures channel.send() calls
# ---------------------------------------------------------------------------


@dataclass
class ChannelCapture:
    """Tracks all messages sent to the mock channel."""

    messages: list[EmbedCapture] = field(default_factory=list)

    @property
    def last(self) -> EmbedCapture:
        """Most recent message sent to the channel."""
        if not self.messages:
            return EmbedCapture()
        return self.messages[-1]


# ---------------------------------------------------------------------------
# ScenarioRunner — the orchestrator
# ---------------------------------------------------------------------------


class ScenarioRunner:
    """Orchestrates multi-step gameplay scenarios against real engine + DB.

    Uses real cog handlers with TestInteraction wrappers. Real in-memory
    SQLite, real engine, AI disabled.
    """

    def __init__(
        self,
        db_factory: sessionmaker[Session],
        *,
        ai_enabled: bool = False,
        ollama_client: OllamaClient | None = None,
    ) -> None:
        # Mock Discord context
        self.guild = MagicMock()
        self.guild.id = 999_000_001
        self.guild.name = "Test Guild"
        self.guild.me = MagicMock()
        self.guild.categories = []

        self.channel = MagicMock()
        self.channel.id = 999_000_100
        self.channel.name = "test-campaign"

        self.channel_capture = ChannelCapture()

        # Wire channel.send to capture messages
        async def _capture_send(
            content: str | None = None,
            *,
            embed: discord.Embed | None = None,
            view: discord.ui.View | None = None,
            **kwargs: Any,
        ) -> MagicMock:
            cap = EmbedCapture(content=content, embed=embed, view=view)
            self.channel_capture.messages.append(cap)
            msg = MagicMock()
            msg.id = len(self.channel_capture.messages)
            return msg

        self.channel.send = _capture_send

        # Virtual players
        self.players: list[MockMember] = []

        # Bot mock
        self.bot = MagicMock()
        self.bot.sessions = {}
        self.bot.lobbies = {}
        self.bot.get_session = lambda cid: self.bot.sessions.get(cid)
        self.bot.db_factory = db_factory

        # Cogs
        self._session_cog = SessionCog(self.bot)
        self._character_cog = CharacterCog(self.bot)
        self._combat_cog = CombatCog(self.bot)
        self._inventory_cog = InventoryCog(self.bot)
        self._rolls_cog = RollsCog(self.bot)

        # Responses history
        self.responses: list[EmbedCapture] = []

        # AI flag
        self.ai_enabled = ai_enabled
        self.ollama_client = ollama_client

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def session(self) -> GameSession | None:
        """Get the active game session."""
        return self.bot.sessions.get(self.channel.id)

    @property
    def last_response(self) -> EmbedCapture:
        """Most recent captured interaction response."""
        if not self.responses:
            return EmbedCapture()
        return self.responses[-1]

    @property
    def last_channel_message(self) -> EmbedCapture:
        """Most recent message sent to the channel."""
        return self.channel_capture.last

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_player(self, idx: int) -> MockMember:
        """Create or get a virtual player by index."""
        while len(self.players) <= idx:
            pid = 100_000_000 + len(self.players)
            p = MockMember(
                id=pid,
                name=f"Player{len(self.players) + 1}",
                display_name=f"Player {len(self.players) + 1}",
            )
            self.players.append(p)
            # Register in guild.get_member
            member_map: dict[int, Any] = {}
            for existing_p in self.players:
                m = MagicMock()
                m.id = existing_p.id
                m.name = existing_p.name
                m.display_name = existing_p.display_name
                m.mention = existing_p.mention
                member_map[existing_p.id] = m
            self.guild.get_member = lambda uid, _mm=member_map: _mm.get(uid)
        return self.players[idx]

    def _make_user_mock(self, player: MockMember) -> MagicMock:
        """Create a MagicMock user from a MockMember."""
        user = MagicMock()
        user.id = player.id
        user.name = player.name
        user.display_name = player.display_name
        user.mention = player.mention
        user.__str__ = lambda s, _p=player: _p.name
        return user

    def _make_interaction(
        self, player_idx: int = 0, **namespace: Any
    ) -> TestInteraction:
        """Create a TestInteraction for the given player."""
        player = self._make_player(player_idx)
        return TestInteraction(
            bot=self.bot,
            guild=self.guild,
            channel=self.channel,
            user=self._make_user_mock(player),
            namespace=namespace if namespace else None,
        )

    def _record(self, interaction: TestInteraction) -> EmbedCapture:
        """Record the interaction's captured response."""
        cap = interaction.captured
        self.responses.append(cap)
        return cap

    # ------------------------------------------------------------------
    # Campaign lifecycle
    # ------------------------------------------------------------------

    async def start_campaign(
        self, theme: str = "Test Dungeon", players: int = 1,
    ) -> EmbedCapture:
        """Start a new campaign with N virtual players."""
        # Ensure players exist
        for i in range(players):
            self._make_player(i)

        # Build campaign directly (bypasses channel creation)
        campaign = Campaign(
            id=str(uuid.uuid4()),
            name=theme,
            created_at=datetime.now(timezone.utc),
            player_names=[str(p.id) for p in self.players[:players]],
        )

        # Persist campaign
        db_session = self.bot.db_factory()
        try:
            from db.repositories import CampaignRepository, CampaignChannelRepository

            CampaignRepository(db_session).save(campaign)
            db_session.flush()  # FK: campaign must exist before channel mapping
            CampaignChannelRepository(db_session).save(
                self.channel.id, campaign.id, self.guild.id,
            )
            db_session.commit()
        finally:
            db_session.close()

        # Create in-memory session (AI disabled by default)
        session = GameSession(campaign=campaign)
        if self.ai_enabled:
            if self.ollama_client is None:
                raise RuntimeError(
                    "ai_enabled=True requires an ollama_client to be passed"
                )
            session.ollama_client = self.ollama_client
            session.interpreter = Interpreter(self.ollama_client)
            session.narrator = Narrator(self.ollama_client)
            session.story_director = None
        self.bot.sessions[self.channel.id] = session

        cap = EmbedCapture(content=f"Campagne lancee: {theme}")
        self.responses.append(cap)
        return cap

    async def save(self) -> EmbedCapture:
        """Save the current session via the SessionCog handler."""
        inter = self._make_interaction()
        await self._session_cog.save.callback(self._session_cog, inter)  # type: ignore[arg-type]
        return self._record(inter)

    async def resume(self) -> EmbedCapture:
        """Resume the saved session via the SessionCog handler."""
        inter = self._make_interaction()
        await self._session_cog.resume.callback(self._session_cog, inter)  # type: ignore[arg-type]
        return self._record(inter)

    async def end_campaign(self) -> EmbedCapture:
        """End the campaign via the SessionCog handler."""
        inter = self._make_interaction()
        # Patch archive_channel to avoid real Discord calls
        from unittest.mock import patch

        with patch("bot.cogs.session.archive_channel", new_callable=AsyncMock):
            await self._session_cog.end_campaign.callback(self._session_cog, inter)  # type: ignore[arg-type]
        return self._record(inter)

    def clear_session(self) -> None:
        """Clear the in-memory session (simulates bot restart)."""
        if self.channel.id in self.bot.sessions:
            del self.bot.sessions[self.channel.id]

    # ------------------------------------------------------------------
    # Character
    # ------------------------------------------------------------------

    async def add_player(
        self,
        name: str,
        race: str = "human",
        class_: str = "fighter",
        player_idx: int = 0,
    ) -> EmbedCapture:
        """Add a character for a player (bypasses multi-step view flow).

        Directly calls engine functions and registers in session + DB.
        """
        session = self.session
        if session is None:
            msg = "No active session"
            raise RuntimeError(msg)

        player = self._make_player(player_idx)
        race_enum = Race(race.capitalize())
        class_enum = CharacterClass(class_.capitalize())

        scores = roll_ability_scores()
        scores = apply_racial_bonuses(scores, race_enum)
        character = create_character(
            name=name,
            race=race_enum,
            char_class=class_enum,
            ability_scores=scores,
        )
        inventory = create_inventory()
        spellcaster = create_spellcaster_state(class_enum, 1)

        # Register in session
        session.characters[player.id] = character
        session.inventories[player.id] = inventory
        session.spellcasters[player.id] = spellcaster

        # Persist to DB
        db_session = self.bot.db_factory()
        try:
            from db.repositories import PlayerCharacterRepository

            pc_repo = PlayerCharacterRepository(db_session)
            pc_repo.save(
                player.id, session.campaign.id, character, inventory, spellcaster,
            )
            db_session.commit()
        finally:
            db_session.close()

        cap = EmbedCapture(content=f"{name} cree !")
        self.responses.append(cap)
        return cap

    async def character(self, player_idx: int = 0) -> EmbedCapture:
        """View character sheet via the CharacterCog handler."""
        inter = self._make_interaction(player_idx)
        await self._character_cog.character.callback(self._character_cog, inter, public=False)  # type: ignore[arg-type]
        return self._record(inter)

    # ------------------------------------------------------------------
    # Exploration (MVP stubs — produce a neutral narration so the
    # simulator can complete a turn; real exploration mechanics live in
    # the cogs and would need their own scenario wrappers later)
    # ------------------------------------------------------------------

    async def look(self, player_idx: int = 0) -> EmbedCapture:
        """No-op exploration: emit a neutral 'look around' narration.

        State is supposed to have been updated already by ``move`` /
        engine paths; ``look`` itself never mutates the world.
        """
        session = self.session
        if session is not None and session.current_location is not None:
            loc = session.current_location
            description = loc.description or loc.arrival_hook or "Vous observez votre environnement."
            return await self._exploration_stub(description)
        return await self._exploration_stub("Vous observez votre environnement.")

    async def move(self, direction: str, player_idx: int = 0) -> EmbedCapture:
        """Resolve ``direction`` and delegate to ``change_location``.

        ``direction`` is matched against the current location's
        ``exit_aliases`` (e.g. ``"nord"`` -> ``"Salle des échos"``) and,
        if that fails, against the raw ``connections`` list. The actual
        mutation (DB lookup or stub hydration via Ollama, NPC reload,
        campaign update) is owned by :func:`bot.world_navigation.change_location`
        — production code path; the runner stays a thin wrapper so
        scenario tests exercise the same logic the live cogs use.

        Falls back to a neutral stub embed (no state mutation) when no
        session/location is active, when the direction maps to nothing,
        or when ``change_location`` raises :class:`LocationChangeError`
        (e.g. mock-LLM runs without a pre-seeded destination).
        """
        session = self.session
        if session is None or session.current_location is None:
            return await self._exploration_stub(
                f"Vous vous déplacez vers {direction}.",
            )

        destination = _resolve_direction(session.current_location, direction)
        if destination is None:
            return await self._exploration_stub(
                f"Aucun passage évident vers « {direction} » depuis "
                f"{session.current_location.name}.",
            )

        from bot.world_navigation import LocationChangeError, change_location

        try:
            new_loc = await change_location(
                session, destination, db_factory=self.bot.db_factory,
            )
        except LocationChangeError as exc:
            return await self._exploration_stub(
                f"Vous tentez de gagner « {destination} », mais le passage "
                f"reste hors d'atteinte ({exc.reason}).",
            )

        embed = discord.Embed(
            title=f"Arrivée : {new_loc.name}",
            description=new_loc.description or new_loc.arrival_hook or "",
        )
        cap = EmbedCapture(content=None, embed=embed, view=None)
        self.channel_capture.messages.append(cap)
        self.responses.append(cap)
        return cap

    async def talk(self, npc: str, player_idx: int = 0) -> EmbedCapture:
        """No-op exploration: emit a neutral 'talk' narration."""
        return await self._exploration_stub(f"Vous engagez la conversation avec {npc}.")

    async def search(self, target: str = "", player_idx: int = 0) -> EmbedCapture:
        """No-op exploration: emit a neutral 'search' narration."""
        suffix = f" {target}" if target else ""
        return await self._exploration_stub(f"Vous fouillez{suffix}.")

    async def _exploration_stub(self, text: str) -> EmbedCapture:
        embed = discord.Embed(description=text)
        cap = EmbedCapture(content=None, embed=embed, view=None)
        self.channel_capture.messages.append(cap)
        self.responses.append(cap)
        return cap

    # ------------------------------------------------------------------
    # Combat
    # ------------------------------------------------------------------

    async def start_combat(
        self, enemies: list[Combatant],
    ) -> EmbedCapture:
        """Start a combat encounter with the given enemies (engine-direct).

        The legacy ``CombatCog.start_combat_encounter`` entry point was
        removed during the combat refactor — the runner now assembles the
        party-wide ``CombatState`` via :func:`engine.combat.start_combat`
        directly so the scenario suite stays decoupled from the Discord
        UI layer. Player actions are still driven by the runner's
        explicit attack/defend/flee helpers.
        """
        from bot.combat_entry import build_pc_combatants
        from bot.embeds.combat_embed import build_combat_embed
        from engine.combat import start_combat

        session = self.session
        if session is None:
            msg = "No active session"
            raise RuntimeError(msg)

        pcs = build_pc_combatants(session)
        state = start_combat(pcs + enemies)
        session.combat_state = state

        embed = build_combat_embed(state)
        cap = EmbedCapture(content="Combat !", embed=embed)
        self.channel_capture.messages.append(cap)
        return cap

    def _find_player_combatant(
        self, player_idx: int,
    ) -> Combatant:
        """Find the Combatant object for a player in the active combat."""
        session = self.session
        if session is None or session.combat_state is None:
            msg = "No active combat"
            raise RuntimeError(msg)

        player = self._make_player(player_idx)
        char = session.characters.get(player.id)
        if char is None:
            msg = f"No character for player {player_idx}"
            raise RuntimeError(msg)

        combatant = next(
            (c for c in session.combat_state.combatants
             if c.character.name == char.name and c.is_alive),
            None,
        )
        if combatant is None:
            msg = f"Player {player_idx} ({char.name}) not found in combat"
            raise RuntimeError(msg)
        return combatant

    async def attack(
        self, target: str, player_idx: int = 0,
    ) -> EmbedCapture:
        """Resolve an attack action for a player (engine-direct).

        Bypasses the TurnManager and drives
        :func:`engine.combat.resolve_attack` directly so multi-step
        scenarios stay fast. Player weapon lookup is done inline against
        the inventory to avoid any dependency on the combat cog.

        Returns a no-op capture (instead of raising) when combat is
        already finalised or when the target is dead — lets
        ``for _ in range(10): await scenario.attack(...)`` loops keep
        working without tracking ``combat_state`` manually. The current
        invariant preserves ``combat_state`` with ``is_active=False``,
        so the old "break on combat_state is None" pattern no longer
        short-circuits the loop.
        """
        session = self.session
        if session is None or session.combat_state is None:
            msg = "No active combat"
            raise RuntimeError(msg)
        if not session.combat_state.is_active:
            cap = EmbedCapture(content="Combat déjà terminé.")
            self.responses.append(cap)
            return cap

        attacker = self._find_player_combatant(player_idx)
        player = self._make_player(player_idx)

        # Find target combatant — unknown target still raises (edge-case
        # tests rely on this). Dead target is a gentle no-op so naive
        # scenario loops that hammer the same target don't crash once it
        # drops (new task 80 contract: combat_state stays around).
        target_ever = next(
            (c for c in session.combat_state.combatants if c.name == target),
            None,
        )
        if target_ever is None:
            msg = f"Target '{target}' not found or dead"
            raise ValueError(msg)
        if not target_ever.is_alive:
            cap = EmbedCapture(
                content=f"'{target}' est déjà mort.",
            )
            self.responses.append(cap)
            return cap
        target_combatant = target_ever

        weapon = self._lookup_player_weapon(session, player.id)
        if weapon is None:
            weapon = self._lookup_combatant_weapon(attacker)

        if weapon is None:
            cap = EmbedCapture(content="Aucune arme equipee !")
            self.responses.append(cap)
            self.channel_capture.messages.append(cap)
            return cap

        from engine.combat import resolve_attack, advance_turn, is_combat_over

        result = resolve_attack(attacker, target_combatant, weapon)
        mechanics = f"{attacker.name} attaque {target_combatant.name}: "
        if result.hit:
            mechanics += f"Touche — {result.damage} degats"
            if result.critical:
                mechanics += " (CRITIQUE !)"
        else:
            mechanics += "Rate"

        from bot.embeds.narrative_embed import build_narrative_embed

        embed = build_narrative_embed(mechanics, tone="dramatic", footer_override=mechanics)
        cap = EmbedCapture(embed=embed)
        self.responses.append(cap)
        self.channel_capture.messages.append(cap)

        advance_turn(session.combat_state)

        # Auto-resolve enemy turns
        await self._auto_resolve_enemies()

        if is_combat_over(session.combat_state):
            await self._finalize_combat(session)

        return cap

    @staticmethod
    def _lookup_player_weapon(session: GameSession, user_id: int) -> Weapon | None:
        inv = session.inventories.get(user_id)
        if inv is None:
            return None
        main_hand = inv.equipped.get(EquipmentSlot.MAIN_HAND)
        if isinstance(main_hand, Weapon):
            return main_hand
        return None

    @staticmethod
    def _lookup_combatant_weapon(combatant: Combatant) -> Weapon | None:
        main_hand = combatant.inventory.equipped.get(EquipmentSlot.MAIN_HAND)
        if isinstance(main_hand, Weapon):
            return main_hand
        return None

    async def _finalize_combat(self, session: GameSession) -> None:
        """Delegate to :func:`bot.combat_end.finalize_combat`.

        XP / condition cleanup / summary construction all live in a
        single engine entry point. The runner defers to it so scenario
        tests exercise the same code path as the live Discord flow.
        ``session.combat_state`` is preserved (current invariant — tests
        assert via ``assert_not_in_combat`` which tolerates both
        ``None`` and ``is_active=False``).
        """
        if session.combat_state is None:
            return
        from bot.combat_end import finalize_combat
        from engine.combat import CombatEndReason, check_combat_end

        reason = (
            session.combat_state.end_reason
            or check_combat_end(session.combat_state)
            or CombatEndReason.VICTORY  # degenerate fallback
        )
        finalize_combat(session, reason)

    async def cast_spell(
        self, spell: str, target: str, player_idx: int = 0,
    ) -> EmbedCapture:
        """Resolve a spell cast action for a player."""
        session = self.session
        if session is None or session.combat_state is None:
            msg = "No active combat"
            raise RuntimeError(msg)

        from engine.combat import resolve_spell, advance_turn, is_combat_over
        from engine.spells import SPELL_CATALOG, can_cast_spell

        caster = self._find_player_combatant(player_idx)
        player = self._make_player(player_idx)

        spell_obj = SPELL_CATALOG.get(spell)
        if spell_obj is None:
            msg = f"Spell '{spell}' not found in catalog"
            raise ValueError(msg)

        spellcaster = session.spellcasters.get(player.id)
        if spellcaster is None or not can_cast_spell(spellcaster, spell_obj):
            cap = EmbedCapture(content="Impossible de lancer ce sort.")
            self.responses.append(cap)
            return cap

        target_combatant = next(
            (c for c in session.combat_state.combatants
             if c.name == target and c.is_alive),
            None,
        )

        result = resolve_spell(caster, spell_obj, target_combatant, spell_obj.level)
        mechanics = f"{caster.name} lance {spell_obj.name}"
        if result.damage:
            mechanics += f" — {result.damage} degats"
        if result.healing:
            mechanics += f" — {result.healing} PV soignes"

        from bot.embeds.narrative_embed import build_narrative_embed

        embed = build_narrative_embed(mechanics, tone="dramatic", footer_override=mechanics)
        cap = EmbedCapture(embed=embed)
        self.responses.append(cap)
        self.channel_capture.messages.append(cap)

        advance_turn(session.combat_state)
        await self._auto_resolve_enemies()

        if is_combat_over(session.combat_state):
            await self._finalize_combat(session)

        return cap

    async def defend(self, player_idx: int = 0) -> EmbedCapture:
        """Resolve a defend action for a player."""
        session = self.session
        if session is None or session.combat_state is None:
            msg = "No active combat"
            raise RuntimeError(msg)

        from engine.combat import advance_turn, is_combat_over

        defender = self._find_player_combatant(player_idx)
        mechanics = f"{defender.name} se met en defense."

        from bot.embeds.narrative_embed import build_narrative_embed

        embed = build_narrative_embed(mechanics, tone="dramatic", footer_override=mechanics)
        cap = EmbedCapture(embed=embed)
        self.responses.append(cap)
        self.channel_capture.messages.append(cap)

        advance_turn(session.combat_state)
        await self._auto_resolve_enemies()

        if is_combat_over(session.combat_state):
            await self._finalize_combat(session)

        return cap

    async def flee(self, player_idx: int = 0) -> EmbedCapture:
        """Resolve a flee action for a player."""
        session = self.session
        if session is None or session.combat_state is None:
            msg = "No active combat"
            raise RuntimeError(msg)

        from engine.combat import advance_turn, is_combat_over

        runner = self._find_player_combatant(player_idx)
        mechanics = f"{runner.name} tente de fuir !"

        from bot.embeds.narrative_embed import build_narrative_embed

        embed = build_narrative_embed(mechanics, tone="dramatic", footer_override=mechanics)
        cap = EmbedCapture(embed=embed)
        self.responses.append(cap)
        self.channel_capture.messages.append(cap)

        advance_turn(session.combat_state)
        await self._auto_resolve_enemies()

        if is_combat_over(session.combat_state):
            await self._finalize_combat(session)

        return cap

    async def free_form_action(
        self, *, text: str, player_idx: int = 0
    ) -> None:
        """Route a free-form text through the @bot mention pipeline.

        Mirrors the work of bot/cogs/action_handler.py:_run_pipeline minus the
        Discord-specific message filtering. Requires ai_enabled=True (otherwise
        session.interpreter / session.narrator are None and this raises).
        """
        session = self.session
        if session is None:
            msg = "No active session — call start_campaign first"
            raise RuntimeError(msg)
        if session.interpreter is None or session.narrator is None:
            msg = (
                "free_form_action requires ai_enabled=True so the session has "
                "real Interpreter/Narrator wired"
            )
            raise RuntimeError(msg)
        player = self._make_player(player_idx)
        actor = session.characters.get(player.id)
        if actor is None:
            msg = (
                f"Player {player.id} has no character — call add_player first"
            )
            raise RuntimeError(msg)
        actor_name = actor.name
        inventory = session.inventories.get(player.id)

        pipeline = ActionPipeline(
            interpreter=session.interpreter,
            narrator=session.narrator,
            location=session.current_location,
            npcs=session.npcs,
            actor_name=actor_name,
            language=getattr(session, "language", "fr"),
            campaign_id=session.campaign.id,
            combat_state=session.combat_state,
            inventory=inventory,
            session=session,
            db_factory=self.bot.db_factory,
        )
        result = await pipeline.process(text)

        # Confiance basse : le runner headless auto-confirme — un scénario
        # ne peut pas cliquer « Oui ». Le simulateur exerce ainsi le même
        # chemin de reprise que le vrai bouton.
        from bot.action_pipeline import LowConfidenceResult

        if isinstance(result, LowConfidenceResult):
            result = await pipeline.process_interpreted_action(
                result.interpreted_action,
            )

        # Surface the narration via channel_capture / responses so that
        # GameDriver._extract_narration finds it.
        narration_text = self._extract_pipeline_narration(result)
        if narration_text:
            embed = discord.Embed(description=narration_text)
            cap = EmbedCapture(content=None, embed=embed, view=None)
            self.channel_capture.messages.append(cap)
            self.responses.append(cap)

    @staticmethod
    def _extract_pipeline_narration(result: Any) -> str:
        """Pull the final narration text out of a PipelineOutput-like object.

        ``ActionPipelineResult.narrative`` is the primary field.
        ``UnknownEntityResult.refusal_narrative`` is the fallback for rule/entity
        failures. ``AmbiguityResult`` has no narration text.
        """
        # Primary: ActionPipelineResult
        narrative = getattr(result, "narrative", None)
        if isinstance(narrative, str) and narrative.strip():
            return narrative
        # Fallback: UnknownEntityResult (rule failure or entity not found)
        refusal = getattr(result, "refusal_narrative", None)
        if isinstance(refusal, str) and refusal.strip():
            return refusal
        # Generic fallback scan for other attribute names
        for attr in ("narration", "text", "final_narration"):
            value = getattr(result, attr, None)
            if isinstance(value, str) and value.strip():
                return value
        return ""

    async def _auto_resolve_enemies(self) -> None:
        """Auto-resolve all consecutive enemy turns."""
        session = self.session
        if session is None or session.combat_state is None:
            return

        from engine.combat import (
            get_current_combatant,
            advance_turn,
            is_combat_over,
            resolve_attack,
        )

        while (
            session.combat_state is not None
            and session.combat_state.is_active
            and not is_combat_over(session.combat_state)
        ):
            current = get_current_combatant(session.combat_state)
            if current.side != CombatSide.ENEMY:
                break

            # Simple AI: attack first living player
            players = [
                c for c in session.combat_state.combatants
                if c.is_alive and c.side == CombatSide.PLAYER
            ]
            if not players:
                advance_turn(session.combat_state)
                continue

            target_c = players[0]
            weapon = self._lookup_combatant_weapon(current)
            if weapon is None:
                advance_turn(session.combat_state)
                continue

            result = resolve_attack(current, target_c, weapon)
            mechanics = f"{current.name} attaque {target_c.name}: "
            if result.hit:
                mechanics += f"Touche — {result.damage} degats"
            else:
                mechanics += "Rate"

            from bot.embeds.narrative_embed import build_narrative_embed

            embed = build_narrative_embed(mechanics, tone="dramatic", footer_override=mechanics)
            cap = EmbedCapture(embed=embed)
            self.channel_capture.messages.append(cap)

            advance_turn(session.combat_state)


    # ------------------------------------------------------------------
    # Inventory
    # ------------------------------------------------------------------

    async def equip(
        self, item: str, slot: str, player_idx: int = 0,
    ) -> EmbedCapture:
        """Equip an item via the InventoryCog handler."""
        inter = self._make_interaction(player_idx, item=item, slot=slot)
        await self._inventory_cog.equip.callback(self._inventory_cog, inter, item, slot)  # type: ignore[arg-type]
        return self._record(inter)

    async def unequip(self, slot: str, player_idx: int = 0) -> EmbedCapture:
        """Unequip a slot via the InventoryCog handler."""
        inter = self._make_interaction(player_idx, slot=slot)
        await self._inventory_cog.unequip.callback(self._inventory_cog, inter, slot)  # type: ignore[arg-type]
        return self._record(inter)

    async def use_item(self, item: str, player_idx: int = 0) -> EmbedCapture:
        """Use an item via the InventoryCog handler."""
        inter = self._make_interaction(player_idx, item=item)
        await self._inventory_cog.use_item.callback(self._inventory_cog, inter, item)  # type: ignore[arg-type]
        return self._record(inter)

    async def inventory(self, player_idx: int = 0) -> EmbedCapture:
        """View inventory via the InventoryCog handler."""
        inter = self._make_interaction(player_idx)
        await self._inventory_cog.inventory.callback(self._inventory_cog, inter, public=False)  # type: ignore[arg-type]
        return self._record(inter)

    # ------------------------------------------------------------------
    # Rolls
    # ------------------------------------------------------------------

    async def roll(self, expression: str, player_idx: int = 0) -> EmbedCapture:
        """Roll dice via the RollsCog handler."""
        inter = self._make_interaction(player_idx, expression=expression)
        await self._rolls_cog.roll_dice.callback(self._rolls_cog, inter, expression)  # type: ignore[arg-type]
        return self._record(inter)

    # ------------------------------------------------------------------
    # Assertions
    # ------------------------------------------------------------------

    def get_character(self, player_idx: int = 0) -> Character:
        """Get a player's character from the active session."""
        session = self.session
        if session is None:
            msg = "No active session"
            raise RuntimeError(msg)
        player = self._make_player(player_idx)
        char = session.characters.get(player.id)
        if char is None:
            msg = f"No character for player {player_idx}"
            raise RuntimeError(msg)
        return char

    def get_inventory(self, player_idx: int = 0) -> Inventory:
        """Get a player's inventory from the active session."""
        session = self.session
        if session is None:
            msg = "No active session"
            raise RuntimeError(msg)
        player = self._make_player(player_idx)
        inv = session.inventories.get(player.id)
        if inv is None:
            msg = f"No inventory for player {player_idx}"
            raise RuntimeError(msg)
        return inv

    def assert_hp(self, player_idx: int, expected: int) -> None:
        """Assert a player's HP equals the expected value."""
        char = self.get_character(player_idx)
        assert char.hp == expected, f"Expected HP={expected}, got {char.hp}"

    def assert_in_combat(self) -> None:
        """Assert that combat is currently active."""
        session = self.session
        assert session is not None, "No active session"
        assert session.combat_state is not None, "No active combat"
        assert session.combat_state.is_active, "Combat is not active"

    def assert_not_in_combat(self) -> None:
        """Assert that no combat is active.

        ``combat_state`` is preserved after finalize for history —
        so ``is_active=False`` counts as "not in combat" just like
        ``combat_state is None``.
        """
        session = self.session
        if session is None:
            return  # No session means no combat
        if session.combat_state is None:
            return
        assert session.combat_state.is_active is False, (
            "Combat is still active"
        )

    def assert_has_item(self, player_idx: int, item_name: str) -> None:
        """Assert a player has an item in their inventory."""
        inv = self.get_inventory(player_idx)
        names = [i.name for i in inv.items]
        assert item_name in names, f"'{item_name}' not in items: {names}"

    def assert_condition(self, player_idx: int, condition: str) -> None:
        """Assert a player's combatant has a specific condition."""
        combatant = self._find_player_combatant(player_idx)
        conditions = [c.condition_type.value for c in combatant.conditions]
        assert condition in conditions, (
            f"'{condition}' not in conditions: {conditions}"
        )
