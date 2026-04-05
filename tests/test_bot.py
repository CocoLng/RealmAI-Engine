"""Tests for bot/bot.py — RealmBot setup and lifecycle."""

import logging
from unittest.mock import AsyncMock, patch

import pytest

from bot.bot import EXTENSIONS, RealmBot


class TestRealmBot:
    """RealmBot instantiation and configuration tests."""

    def test_intents_message_content_enabled(self) -> None:
        with patch("bot.bot.get_engine"), patch("bot.bot.init_db"), patch("bot.bot.get_session_factory"):
            bot = RealmBot()
        assert bot.intents.message_content is True

    def test_intents_members_enabled(self) -> None:
        with patch("bot.bot.get_engine"), patch("bot.bot.init_db"), patch("bot.bot.get_session_factory"):
            bot = RealmBot()
        assert bot.intents.members is True

    def test_db_factory_is_set(self) -> None:
        with patch("bot.bot.get_engine") as mock_engine, \
             patch("bot.bot.init_db"), \
             patch("bot.bot.get_session_factory", return_value="fake_factory") as mock_sf:
            bot = RealmBot()
        assert bot.db_factory == "fake_factory"
        mock_sf.assert_called_once_with(mock_engine.return_value)

    def test_extensions_list_is_defined(self) -> None:
        assert isinstance(EXTENSIONS, list)


class TestRealmBotSetupHook:
    """setup_hook extension loading tests."""

    @pytest.mark.asyncio()
    async def test_setup_hook_loads_extensions(self) -> None:
        with patch("bot.bot.get_engine"), patch("bot.bot.init_db"), patch("bot.bot.get_session_factory"):
            bot = RealmBot()
        bot.load_extension = AsyncMock()
        bot.tree.sync = AsyncMock()

        with patch("bot.bot.EXTENSIONS", ["bot.cogs.fake_cog"]):
            await bot.setup_hook()

        bot.load_extension.assert_called_once_with("bot.cogs.fake_cog")

    @pytest.mark.asyncio()
    async def test_setup_hook_syncs_tree(self) -> None:
        with patch("bot.bot.get_engine"), patch("bot.bot.init_db"), patch("bot.bot.get_session_factory"):
            bot = RealmBot()
        bot.load_extension = AsyncMock()
        bot.tree.sync = AsyncMock()

        await bot.setup_hook()

        bot.tree.sync.assert_called_once()


class TestRealmBotOnReady:
    """on_ready logging tests."""

    @pytest.mark.asyncio()
    async def test_on_ready_logs_info(self, caplog: pytest.LogCaptureFixture) -> None:
        with patch("bot.bot.get_engine"), patch("bot.bot.init_db"), patch("bot.bot.get_session_factory"):
            bot = RealmBot()
        # guilds is a read-only property; patch it at the class level for this test
        fake_guilds = [AsyncMock(), AsyncMock()]
        with patch.object(type(bot), "guilds", new_callable=lambda: property(lambda self: fake_guilds)), \
             patch.object(type(bot), "user", new_callable=lambda: property(lambda self: "TestBot#1234")), \
             caplog.at_level(logging.INFO, logger="bot.bot"):
            await bot.on_ready()

        assert "TestBot#1234" in caplog.text
        assert "2 guilds" in caplog.text


class TestRunBot:
    """run_bot() entry point tests."""

    def test_run_bot_requires_token_env(self) -> None:
        from bot.bot import run_bot

        with patch("bot.bot.load_dotenv"), \
             patch.dict("os.environ", {}, clear=True), \
             pytest.raises(KeyError, match="DISCORD_BOT_TOKEN"):
            run_bot()
