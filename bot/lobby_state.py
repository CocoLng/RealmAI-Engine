"""Campaign lobby state — replaces CampaignLauncher.

Tracks players who joined the lobby via the 'Rejoindre' button, their
character setup status, and exposes the predicate used to gate launch.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import discord

    from bot.views.lobby_view import LobbyView
    from engine.character import Character
    from engine.inventory import Inventory
    from engine.spells import SpellcasterState
    from world.location import Location
    from world.story_arc import StoryArc


MAX_PLAYERS_PER_LOBBY = 6


class LobbyPlayerStatus(StrEnum):
    """Per-player lifecycle in the lobby."""

    JOINED = "joined"           # clicked Rejoindre, not started creation
    CREATING = "creating"       # CharacterSetupFlow open, in progress
    READY = "ready"             # creation complete, character persisted
    CANCELLED = "cancelled"     # bailed out mid-creation


class GenerationPhase(IntEnum):
    """Background arc + location generation lifecycle."""

    PENDING = 0   # task not yet started
    ARC = 1       # generating story arc
    LOCATION = 2  # generating starting location
    READY = 3     # both done, cached on lobby
    FAILED = 4    # gave up, on_launch will surface the error


@dataclass
class LobbyPlayer:
    """Per-user state inside a lobby."""

    user_id: int
    status: LobbyPlayerStatus = LobbyPlayerStatus.JOINED
    character: Character | None = None
    inventory: Inventory | None = None
    spellcaster: SpellcasterState | None = None
    kit_name: str | None = None
    motivation_key: str | None = None


@dataclass
class LobbyState:
    """In-memory state for a campaign lobby in a given channel.

    Replaces ``CampaignLauncher`` with a flatter structure: one ``LobbyPlayer``
    per joined user, no separate progress dicts. Background arc + location
    generation runs from /start_campaign so the launch is instant once the
    host clicks Démarrer.
    """

    creator_id: int
    language: str = "fr"
    campaign_name: str = ""
    theme: str = ""
    players: dict[int, LobbyPlayer] = field(default_factory=dict)
    # Reference to the public lobby Discord message so any cog can refresh
    # the embed without having to thread the message through closures.
    lobby_message: discord.Message | None = field(default=None, repr=False)
    # The view attached to that message — lets out-of-band drivers (test
    # bridge) reach the real buttons without going through Discord's
    # private view store.
    lobby_view: LobbyView | None = field(default=None, repr=False)
    # Background generation results — populated by the pre-gen task.
    pregen_phase: GenerationPhase = GenerationPhase.PENDING
    pregen_task: asyncio.Task[None] | None = field(default=None, repr=False)
    story_arc: StoryArc | None = None
    current_location: Location | None = None
    pregen_error: str | None = None  # set on FAILED, surfaced at launch time
    last_atmosphere: str | None = None
    """Atmosphere used for the pre-generated starting location (spec §2.1).
    Handed to the GameSession at launch so the first move gets a different
    ambiance."""

    def add_player(self, user_id: int) -> None:
        """Add a player to the lobby in JOINED state. Idempotent."""
        if user_id in self.players:
            return
        if len(self.players) >= MAX_PLAYERS_PER_LOBBY:
            raise ValueError(f"Lobby is full ({MAX_PLAYERS_PER_LOBBY} players max).")
        self.players[user_id] = LobbyPlayer(user_id=user_id)

    def remove_player(self, user_id: int) -> None:
        """Remove a player from the lobby. No-op if not present."""
        self.players.pop(user_id, None)

    def set_status(self, user_id: int, status: LobbyPlayerStatus) -> None:
        """Update a player's status. Raises KeyError if not in lobby."""
        self.players[user_id].status = status

    def has_any_ready(self) -> bool:
        """True if at least one player has completed character creation."""
        return any(p.status == LobbyPlayerStatus.READY for p in self.players.values())
