"""Tests for bot/config.py — GuildConfig model."""

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from bot.config import GuildConfig
from db.models import GuildConfigRow


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


class TestGuildConfigRow:
    """GuildConfigRow SQLAlchemy model tests."""

    def test_row_creation(self, db_session: Session) -> None:
        row = GuildConfigRow(guild_id=123456789, category_name="Test Category")
        db_session.add(row)
        db_session.commit()

        result = db_session.get(GuildConfigRow, 123456789)
        assert result is not None
        assert result.guild_id == 123456789
        assert result.category_name == "Test Category"

    def test_default_category_name(self, db_session: Session) -> None:
        row = GuildConfigRow(guild_id=999)
        db_session.add(row)
        db_session.commit()

        result = db_session.get(GuildConfigRow, 999)
        assert result is not None
        assert result.category_name == "RealmAI Sessions"

    def test_duplicate_guild_id_rejected(self, db_session: Session) -> None:
        from sqlalchemy.exc import IntegrityError

        db_session.add(GuildConfigRow(guild_id=111))
        db_session.commit()
        db_session.add(GuildConfigRow(guild_id=111))
        with pytest.raises(IntegrityError):
            db_session.commit()
