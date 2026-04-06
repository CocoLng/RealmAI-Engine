"""Tests for bot/utils/channel_manager.py — channel creation, archival, slugify."""

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.utils.channel_manager import (
    ARCHIVE_CATEGORY_NAME,
    _slugify,
    archive_channel,
    create_session_channel,
    get_or_create_category,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_category(name: str) -> MagicMock:
    cat = MagicMock(spec=discord.CategoryChannel)
    cat.name = name
    cat.id = hash(name) & 0xFFFF_FFFF
    return cat


@pytest.fixture()
def mock_guild() -> MagicMock:
    guild = MagicMock(spec=discord.Guild)
    guild.categories = []
    guild.default_role = MagicMock(spec=discord.Role)
    guild.default_role.id = 100
    guild.me = MagicMock(spec=discord.Member)
    guild.me.id = 999
    guild.create_text_channel = AsyncMock()
    guild.create_category_channel = AsyncMock()
    return guild


@pytest.fixture()
def mock_bot_member() -> MagicMock:
    member = MagicMock(spec=discord.Member)
    member.id = 999
    return member


@pytest.fixture()
def mock_player() -> MagicMock:
    member = MagicMock(spec=discord.Member)
    member.id = 500
    return member


@pytest.fixture()
def mock_player2() -> MagicMock:
    member = MagicMock(spec=discord.Member)
    member.id = 501
    return member


@pytest.fixture()
def mock_channel(mock_guild: MagicMock) -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)
    channel.edit = AsyncMock()
    channel.set_permissions = AsyncMock()
    channel.overwrites = {}
    channel.guild = mock_guild
    return channel


# ---------------------------------------------------------------------------
# TestSlugify
# ---------------------------------------------------------------------------


class TestSlugify:
    """_slugify() converts campaign names to Discord-safe channel slugs."""

    def test_basic_latin(self) -> None:
        assert _slugify("Lost Mines") == "campagne-lost-mines"

    def test_french_accents(self) -> None:
        assert _slugify("Donjon des ombres") == "campagne-donjon-des-ombres"

    def test_accented_characters(self) -> None:
        assert _slugify("Château éternel") == "campagne-chateau-eternel"

    def test_special_characters(self) -> None:
        result = _slugify("Quest: The Dragon's Lair!!")
        assert result == "campagne-quest-the-dragon-s-lair"

    def test_multiple_hyphens_collapsed(self) -> None:
        assert _slugify("A  --  B") == "campagne-a-b"

    def test_empty_string(self) -> None:
        assert _slugify("") == "campagne-sans-nom"

    def test_only_special_chars(self) -> None:
        assert _slugify("!!!") == "campagne-sans-nom"

    def test_long_name_truncated(self) -> None:
        result = _slugify("x" * 200)
        assert len(result) <= 100
        assert result.startswith("campagne-")

    def test_non_latin_unicode(self) -> None:
        assert _slugify("冒険") == "campagne-sans-nom"


# ---------------------------------------------------------------------------
# TestGetOrCreateCategory
# ---------------------------------------------------------------------------


class TestGetOrCreateCategory:
    """get_or_create_category() finds or creates a Discord category."""

    @pytest.mark.asyncio()
    async def test_returns_existing_exact_case(self, mock_guild: MagicMock) -> None:
        existing = _make_category("RealmAI Sessions")
        mock_guild.categories = [existing]

        result = await get_or_create_category(mock_guild, "RealmAI Sessions")

        assert result is existing
        mock_guild.create_category_channel.assert_not_called()

    @pytest.mark.asyncio()
    async def test_returns_existing_case_insensitive(self, mock_guild: MagicMock) -> None:
        existing = _make_category("realmai sessions")
        mock_guild.categories = [existing]

        result = await get_or_create_category(mock_guild, "RealmAI Sessions")

        assert result is existing
        mock_guild.create_category_channel.assert_not_called()

    @pytest.mark.asyncio()
    async def test_creates_when_missing(self, mock_guild: MagicMock) -> None:
        mock_guild.categories = []
        created = _make_category("RealmAI Sessions")
        mock_guild.create_category_channel.return_value = created

        result = await get_or_create_category(mock_guild, "RealmAI Sessions")

        assert result is created
        mock_guild.create_category_channel.assert_called_once_with(name="RealmAI Sessions")

    @pytest.mark.asyncio()
    async def test_finds_correct_among_multiple(self, mock_guild: MagicMock) -> None:
        other = _make_category("Other")
        target = _make_category("RealmAI Sessions")
        mock_guild.categories = [other, target]

        result = await get_or_create_category(mock_guild, "RealmAI Sessions")

        assert result is target
        mock_guild.create_category_channel.assert_not_called()


# ---------------------------------------------------------------------------
# TestCreateSessionChannel
# ---------------------------------------------------------------------------


class TestCreateSessionChannel:
    """create_session_channel() creates a private channel with overwrites."""

    @pytest.mark.asyncio()
    async def test_happy_path_two_players(
        self,
        mock_guild: MagicMock,
        mock_bot_member: MagicMock,
        mock_player: MagicMock,
        mock_player2: MagicMock,
    ) -> None:
        category = _make_category("RealmAI Sessions")
        mock_guild.create_category_channel.return_value = category
        expected_channel = MagicMock(spec=discord.TextChannel)
        mock_guild.create_text_channel.return_value = expected_channel

        result = await create_session_channel(
            mock_guild, "Donjon des ombres", [mock_player, mock_player2], mock_bot_member
        )

        assert result is expected_channel
        call_kwargs = mock_guild.create_text_channel.call_args.kwargs
        assert call_kwargs["name"] == "campagne-donjon-des-ombres"
        assert call_kwargs["category"] is category

        overwrites = call_kwargs["overwrites"]
        assert len(overwrites) == 4  # @everyone + bot + 2 players

    @pytest.mark.asyncio()
    async def test_everyone_denied(
        self,
        mock_guild: MagicMock,
        mock_bot_member: MagicMock,
        mock_player: MagicMock,
    ) -> None:
        mock_guild.create_category_channel.return_value = _make_category("RealmAI Sessions")
        mock_guild.create_text_channel.return_value = MagicMock(spec=discord.TextChannel)

        await create_session_channel(mock_guild, "Test", [mock_player], mock_bot_member)

        overwrites = mock_guild.create_text_channel.call_args.kwargs["overwrites"]
        everyone_ow = overwrites[mock_guild.default_role]
        assert everyone_ow.read_messages is False
        assert everyone_ow.send_messages is False

    @pytest.mark.asyncio()
    async def test_bot_gets_manage_messages(
        self,
        mock_guild: MagicMock,
        mock_bot_member: MagicMock,
    ) -> None:
        mock_guild.create_category_channel.return_value = _make_category("RealmAI Sessions")
        mock_guild.create_text_channel.return_value = MagicMock(spec=discord.TextChannel)

        await create_session_channel(mock_guild, "Test", [], mock_bot_member)

        overwrites = mock_guild.create_text_channel.call_args.kwargs["overwrites"]
        bot_ow = overwrites[mock_bot_member]
        assert bot_ow.read_messages is True
        assert bot_ow.send_messages is True
        assert bot_ow.manage_messages is True

    @pytest.mark.asyncio()
    async def test_player_permissions(
        self,
        mock_guild: MagicMock,
        mock_bot_member: MagicMock,
        mock_player: MagicMock,
    ) -> None:
        mock_guild.create_category_channel.return_value = _make_category("RealmAI Sessions")
        mock_guild.create_text_channel.return_value = MagicMock(spec=discord.TextChannel)

        await create_session_channel(mock_guild, "Test", [mock_player], mock_bot_member)

        overwrites = mock_guild.create_text_channel.call_args.kwargs["overwrites"]
        player_ow = overwrites[mock_player]
        assert player_ow.read_messages is True
        assert player_ow.send_messages is True

    @pytest.mark.asyncio()
    async def test_custom_category_name(
        self,
        mock_guild: MagicMock,
        mock_bot_member: MagicMock,
    ) -> None:
        custom_cat = _make_category("My Games")
        mock_guild.categories = [custom_cat]
        mock_guild.create_text_channel.return_value = MagicMock(spec=discord.TextChannel)

        await create_session_channel(
            mock_guild, "Test", [], mock_bot_member, category_name="My Games"
        )

        # Should NOT have created a new category
        mock_guild.create_category_channel.assert_not_called()

    @pytest.mark.asyncio()
    async def test_no_players(
        self,
        mock_guild: MagicMock,
        mock_bot_member: MagicMock,
    ) -> None:
        mock_guild.create_category_channel.return_value = _make_category("RealmAI Sessions")
        mock_guild.create_text_channel.return_value = MagicMock(spec=discord.TextChannel)

        await create_session_channel(mock_guild, "Solo", [], mock_bot_member)

        overwrites = mock_guild.create_text_channel.call_args.kwargs["overwrites"]
        assert len(overwrites) == 2  # @everyone + bot only


# ---------------------------------------------------------------------------
# TestArchiveChannel
# ---------------------------------------------------------------------------


class TestArchiveChannel:
    """archive_channel() moves a channel to archives and sets read-only."""

    @pytest.mark.asyncio()
    async def test_happy_path(
        self,
        mock_guild: MagicMock,
        mock_channel: MagicMock,
        mock_player: MagicMock,
        mock_player2: MagicMock,
    ) -> None:
        archive_cat = _make_category(ARCHIVE_CATEGORY_NAME)
        mock_guild.create_category_channel.return_value = archive_cat

        mock_channel.overwrites = {
            mock_guild.default_role: discord.PermissionOverwrite(read_messages=False),
            mock_guild.me: discord.PermissionOverwrite(read_messages=True, manage_messages=True),
            mock_player: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            mock_player2: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        }

        await archive_channel(mock_channel, mock_guild)

        mock_channel.edit.assert_called_once_with(category=archive_cat)
        assert mock_channel.set_permissions.call_count == 2  # 2 players

    @pytest.mark.asyncio()
    async def test_archive_category_created_if_missing(
        self,
        mock_guild: MagicMock,
        mock_channel: MagicMock,
    ) -> None:
        mock_guild.categories = []
        archive_cat = _make_category(ARCHIVE_CATEGORY_NAME)
        mock_guild.create_category_channel.return_value = archive_cat

        await archive_channel(mock_channel, mock_guild)

        mock_guild.create_category_channel.assert_called_once_with(name=ARCHIVE_CATEGORY_NAME)

    @pytest.mark.asyncio()
    async def test_archive_category_reused_if_exists(
        self,
        mock_guild: MagicMock,
        mock_channel: MagicMock,
    ) -> None:
        existing = _make_category(ARCHIVE_CATEGORY_NAME)
        mock_guild.categories = [existing]

        await archive_channel(mock_channel, mock_guild)

        mock_guild.create_category_channel.assert_not_called()
        mock_channel.edit.assert_called_once_with(category=existing)

    @pytest.mark.asyncio()
    async def test_bot_permissions_not_modified(
        self,
        mock_guild: MagicMock,
        mock_channel: MagicMock,
    ) -> None:
        mock_guild.create_category_channel.return_value = _make_category(ARCHIVE_CATEGORY_NAME)
        mock_channel.overwrites = {
            mock_guild.me: discord.PermissionOverwrite(read_messages=True, manage_messages=True),
        }

        await archive_channel(mock_channel, mock_guild)

        mock_channel.set_permissions.assert_not_called()

    @pytest.mark.asyncio()
    async def test_everyone_permissions_not_modified(
        self,
        mock_guild: MagicMock,
        mock_channel: MagicMock,
    ) -> None:
        mock_guild.create_category_channel.return_value = _make_category(ARCHIVE_CATEGORY_NAME)
        mock_channel.overwrites = {
            mock_guild.default_role: discord.PermissionOverwrite(read_messages=False),
        }

        await archive_channel(mock_channel, mock_guild)

        mock_channel.set_permissions.assert_not_called()

    @pytest.mark.asyncio()
    async def test_no_player_overwrites(
        self,
        mock_guild: MagicMock,
        mock_channel: MagicMock,
    ) -> None:
        mock_guild.create_category_channel.return_value = _make_category(ARCHIVE_CATEGORY_NAME)
        mock_channel.overwrites = {}

        await archive_channel(mock_channel, mock_guild)

        mock_channel.set_permissions.assert_not_called()

    @pytest.mark.asyncio()
    async def test_player_read_preserved_send_denied(
        self,
        mock_guild: MagicMock,
        mock_channel: MagicMock,
        mock_player: MagicMock,
    ) -> None:
        mock_guild.create_category_channel.return_value = _make_category(ARCHIVE_CATEGORY_NAME)
        mock_channel.overwrites = {
            mock_player: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        }

        await archive_channel(mock_channel, mock_guild)

        mock_channel.set_permissions.assert_called_once_with(
            mock_player, read_messages=True, send_messages=False
        )


# ---------------------------------------------------------------------------
# Error Propagation
# ---------------------------------------------------------------------------


class TestErrorPropagation:
    """Discord permission errors propagate without being caught."""

    @pytest.mark.asyncio()
    async def test_create_channel_propagates_forbidden(
        self,
        mock_guild: MagicMock,
        mock_bot_member: MagicMock,
    ) -> None:
        mock_guild.create_category_channel.return_value = _make_category("RealmAI Sessions")
        resp = MagicMock()
        resp.status = 403
        mock_guild.create_text_channel = AsyncMock(
            side_effect=discord.Forbidden(resp, "Missing Access")
        )

        with pytest.raises(discord.Forbidden):
            await create_session_channel(mock_guild, "Test", [], mock_bot_member)

    @pytest.mark.asyncio()
    async def test_archive_channel_propagates_forbidden(
        self,
        mock_guild: MagicMock,
        mock_channel: MagicMock,
    ) -> None:
        mock_guild.create_category_channel.return_value = _make_category(ARCHIVE_CATEGORY_NAME)
        resp = MagicMock()
        resp.status = 403
        mock_channel.edit = AsyncMock(side_effect=discord.Forbidden(resp, "Missing Access"))

        with pytest.raises(discord.Forbidden):
            await archive_channel(mock_channel, mock_guild)
