"""ArcTrackerManager — owns the pinned Arc Tracker message lifecycle.

Operates against a generic ``store`` interface (Protocol) so tests can
mock without touching the DB. In production the store wraps
:class:`db.repositories.campaign_channel_repo.CampaignChannelRepository`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol

import discord

from bot.embeds.arc_tracker_embed import build_arc_tracker_embed

logger = logging.getLogger(__name__)


@dataclass
class ArcTrackerData:
    """Plain-data payload for the Arc Tracker embed."""

    chapter_title: str
    current_objective: str
    recent_beats: list[str] = field(default_factory=list)
    active_quests: list[str] = field(default_factory=list)
    last_updated_relative: str = "à l'instant"


class ArcTrackerStore(Protocol):
    """Storage interface for the Arc Tracker message ID per channel."""

    def get_message_id(self, channel_id: int) -> int | None: ...
    def set_message_id(self, channel_id: int, message_id: int | None) -> None: ...


class ArcTrackerManager:
    """Manages the pinned Arc Tracker message for a campaign channel."""

    def __init__(self, *, store: ArcTrackerStore) -> None:
        self._store = store

    async def ensure_pinned(
        self,
        *,
        channel: discord.abc.Messageable,
        campaign_id: str,
        channel_id: int,
        data: ArcTrackerData,
    ) -> int:
        """Create the pinned message if none exists; return its ID."""
        existing = self._store.get_message_id(channel_id)
        if existing is not None:
            return existing

        embed = build_arc_tracker_embed(
            chapter_title=data.chapter_title,
            current_objective=data.current_objective,
            recent_beats=data.recent_beats,
            active_quests=data.active_quests,
            last_updated_relative=data.last_updated_relative,
        )
        msg = await channel.send(embed=embed)
        try:
            await msg.pin()
        except discord.Forbidden:
            logger.warning(
                "Cannot pin Arc Tracker in channel=%s — missing permissions",
                channel_id,
            )
        self._store.set_message_id(channel_id, msg.id)
        return msg.id

    async def update(
        self,
        *,
        channel: discord.abc.Messageable,
        campaign_id: str,
        channel_id: int,
        data: ArcTrackerData,
    ) -> None:
        """Edit the existing pinned message in-place. If absent, create one."""
        existing = self._store.get_message_id(channel_id)
        embed = build_arc_tracker_embed(
            chapter_title=data.chapter_title,
            current_objective=data.current_objective,
            recent_beats=data.recent_beats,
            active_quests=data.active_quests,
            last_updated_relative=data.last_updated_relative,
        )
        if existing is None:
            msg = await channel.send(embed=embed)
            try:
                await msg.pin()
            except discord.Forbidden:
                logger.warning(
                    "Cannot pin Arc Tracker in channel=%s — missing permissions",
                    channel_id,
                )
            self._store.set_message_id(channel_id, msg.id)
            return

        try:
            msg = await channel.fetch_message(existing)
            await msg.edit(embed=embed)
        except (discord.NotFound, discord.Forbidden):
            logger.warning(
                "Arc Tracker message %s not found in channel=%s — recreating",
                existing,
                channel_id,
            )
            new_msg = await channel.send(embed=embed)
            try:
                await new_msg.pin()
            except discord.Forbidden:
                pass
            self._store.set_message_id(channel_id, new_msg.id)

    async def remove(
        self,
        *,
        channel: discord.abc.Messageable,
        channel_id: int,
    ) -> None:
        """Unpin and delete the Arc Tracker message; clear the stored ID."""
        existing = self._store.get_message_id(channel_id)
        if existing is None:
            return
        try:
            msg = await channel.fetch_message(existing)
            await msg.unpin()
            try:
                await msg.delete()
            except (discord.NotFound, discord.Forbidden):
                pass
        except (discord.NotFound, discord.Forbidden):
            logger.warning(
                "Arc Tracker message %s already gone in channel=%s",
                existing,
                channel_id,
            )
        self._store.set_message_id(channel_id, None)
