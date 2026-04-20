"""Tests for ArcTrackerManager — pinned message lifecycle."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from bot.utils.arc_tracker import ArcTrackerManager, ArcTrackerData


class TestArcTrackerManager:
    @pytest.mark.asyncio
    async def test_ensure_pinned_creates_new_pin_when_no_existing_id(self) -> None:
        sent_msg = MagicMock()
        sent_msg.id = 12345
        sent_msg.pin = AsyncMock()
        channel = MagicMock()
        channel.send = AsyncMock(return_value=sent_msg)
        store = MagicMock()
        store.get_message_id = MagicMock(return_value=None)
        store.set_message_id = MagicMock()

        manager = ArcTrackerManager(store=store)
        data = ArcTrackerData(
            chapter_title="Ch.1",
            current_objective="X",
            recent_beats=[], active_quests=[],
            last_updated_relative="now",
        )
        msg_id = await manager.ensure_pinned(
            channel=channel, campaign_id="cmp_1", channel_id=999, data=data,
        )
        assert msg_id == 12345
        channel.send.assert_awaited_once()
        sent_msg.pin.assert_awaited_once()
        store.set_message_id.assert_called_once_with(999, 12345)

    @pytest.mark.asyncio
    async def test_ensure_pinned_returns_existing_id_when_present(self) -> None:
        channel = MagicMock()
        channel.send = AsyncMock()
        store = MagicMock()
        store.get_message_id = MagicMock(return_value=99999)

        manager = ArcTrackerManager(store=store)
        data = ArcTrackerData(
            chapter_title="X", current_objective="Y",
            recent_beats=[], active_quests=[], last_updated_relative="now",
        )
        msg_id = await manager.ensure_pinned(
            channel=channel, campaign_id="cmp_1", channel_id=999, data=data,
        )
        assert msg_id == 99999
        channel.send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_update_edits_existing_pin(self) -> None:
        existing_msg = MagicMock()
        existing_msg.edit = AsyncMock()
        channel = MagicMock()
        channel.fetch_message = AsyncMock(return_value=existing_msg)
        store = MagicMock()
        store.get_message_id = MagicMock(return_value=12345)

        manager = ArcTrackerManager(store=store)
        data = ArcTrackerData(
            chapter_title="X", current_objective="Y",
            recent_beats=[], active_quests=[], last_updated_relative="now",
        )
        await manager.update(
            channel=channel, campaign_id="cmp_1", channel_id=999, data=data,
        )
        existing_msg.edit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_update_no_existing_pin_creates_one(self) -> None:
        sent_msg = MagicMock()
        sent_msg.id = 55555
        sent_msg.pin = AsyncMock()
        channel = MagicMock()
        channel.send = AsyncMock(return_value=sent_msg)
        store = MagicMock()
        store.get_message_id = MagicMock(return_value=None)
        store.set_message_id = MagicMock()

        manager = ArcTrackerManager(store=store)
        data = ArcTrackerData(
            chapter_title="X", current_objective="Y",
            recent_beats=[], active_quests=[], last_updated_relative="now",
        )
        await manager.update(
            channel=channel, campaign_id="cmp_1", channel_id=999, data=data,
        )
        channel.send.assert_awaited_once()
        store.set_message_id.assert_called_once_with(999, 55555)

    @pytest.mark.asyncio
    async def test_remove_unpins_and_clears_id(self) -> None:
        existing_msg = MagicMock()
        existing_msg.unpin = AsyncMock()
        existing_msg.delete = AsyncMock()
        channel = MagicMock()
        channel.fetch_message = AsyncMock(return_value=existing_msg)
        store = MagicMock()
        store.get_message_id = MagicMock(return_value=12345)
        store.set_message_id = MagicMock()

        manager = ArcTrackerManager(store=store)
        await manager.remove(channel=channel, channel_id=999)
        existing_msg.unpin.assert_awaited_once()
        store.set_message_id.assert_called_once_with(999, None)
