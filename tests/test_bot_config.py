"""Tests for bot/config.py — GuildConfig model."""

from pydantic import ValidationError
import pytest

from bot.config import GuildConfig


class TestGuildConfig:
    """GuildConfig Pydantic model tests."""

    def test_default_category_name(self) -> None:
        config = GuildConfig(guild_id=123456789)
        assert config.category_name == "RealmAI Sessions"

    def test_custom_category_name(self) -> None:
        config = GuildConfig(guild_id=123456789, category_name="My Category")
        assert config.category_name == "My Category"

    def test_empty_category_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GuildConfig(guild_id=123456789, category_name="")

    def test_category_name_too_long_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GuildConfig(guild_id=123456789, category_name="x" * 101)

    def test_serialization_round_trip(self) -> None:
        config = GuildConfig(guild_id=987654321, category_name="Test")
        data = config.model_dump()
        restored = GuildConfig.model_validate(data)
        assert restored == config
