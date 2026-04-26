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

    # engine-truth progress fields (task F1)
    progress_score: int = 0  # 0-100
    objective_status_lines: list[str] = field(default_factory=list)
    """Pre-formatted lines like '✅ Examiner la cape', '◐ Parler à Kaelen', '◯ Interroger un témoin'."""
    relevant_locations: list[str] = field(default_factory=list)
    relevant_npcs: list[str] = field(default_factory=list)


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
            progress_score=data.progress_score,
            objective_status_lines=data.objective_status_lines,
            relevant_locations=data.relevant_locations,
            relevant_npcs=data.relevant_npcs,
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
            progress_score=data.progress_score,
            objective_status_lines=data.objective_status_lines,
            relevant_locations=data.relevant_locations,
            relevant_npcs=data.relevant_npcs,
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


def build_arc_tracker_data_from_progress(
    *,
    arc: object,
    progress: object,  # engine.beat_progression.BeatProgress | None
    recent_beats: list[str] | None = None,
    active_quests: list[str] | None = None,
) -> ArcTrackerData:
    """Build an ArcTrackerData from engine truth.

    ``arc`` is a :class:`world.story_arc.StoryArc` instance.
    ``progress`` is the latest :class:`engine.beat_progression.BeatProgress`
    (or ``None`` to fall back to a minimal view).
    """
    chapter_title = ""
    current_objective = ""
    progress_score = 0
    status_lines: list[str] = []
    locations: list[str] = []
    npcs: list[str] = []

    if arc is not None and arc.current_beat_index < len(arc.beats):  # type: ignore[union-attr]
        beat = arc.beats[arc.current_beat_index]  # type: ignore[union-attr]
        chapter_title = beat.title
        current_objective = beat.description.split(".", 1)[0] + "."

        if progress is not None:
            progress_score = progress.progress_score  # type: ignore[union-attr]
            for obj in beat.objectives:
                state = progress.objective_states.get(obj.id)  # type: ignore[union-attr]
                marker = "◯"
                if state is not None:
                    if state.status == "completed":
                        marker = "✅"
                    elif state.status == "partial":
                        marker = "◐"
                status_lines.append(f"{marker} {obj.description}")

        # Extract relevant locations and NPCs from objectives
        from world.story_arc import ObjectiveKind  # local import — avoids circular

        for obj in beat.objectives:
            if obj.kind == ObjectiveKind.ARRIVE and obj.target not in locations:
                locations.append(obj.target)
            elif obj.kind == ObjectiveKind.TALK and obj.target not in npcs:
                npcs.append(obj.target)

    return ArcTrackerData(
        chapter_title=chapter_title,
        current_objective=current_objective,
        recent_beats=recent_beats or [],
        active_quests=active_quests or [],
        last_updated_relative="à l'instant",
        progress_score=progress_score,
        objective_status_lines=status_lines,
        relevant_locations=locations,
        relevant_npcs=npcs,
    )
