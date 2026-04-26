"""Campaign lobby state — replaces CampaignLauncher.

Tracks players who joined the lobby via the 'Rejoindre' button, their
character setup status, and exposes the predicate used to gate launch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engine.character import Character
    from engine.inventory import Inventory
    from engine.spells import SpellcasterState


MAX_PLAYERS_PER_LOBBY = 6


class LobbyPlayerStatus(StrEnum):
    """Per-player lifecycle in the lobby."""

    JOINED = "joined"           # clicked Rejoindre, not started creation
    CREATING = "creating"       # CharacterSetupFlow open, in progress
    READY = "ready"             # creation complete, character persisted
    CANCELLED = "cancelled"     # bailed out mid-creation


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
    per joined user, no separate progress dicts.
    """

    creator_id: int
    language: str = "fr"
    players: dict[int, LobbyPlayer] = field(default_factory=dict)

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
