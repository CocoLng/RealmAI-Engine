"""Tests for bot/config.py — GuildConfig model."""

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from bot.config import GuildConfig
from db.mappers import guild_config_from_db, guild_config_to_db
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


class TestGuildConfigMappers:
    """GuildConfig mapper round-trip tests."""

    def test_to_db(self) -> None:
        config = GuildConfig(guild_id=123456789, category_name="Custom")
        row = guild_config_to_db(config)
        assert row.guild_id == 123456789
        assert row.category_name == "Custom"

    def test_from_db(self) -> None:
        row = GuildConfigRow(guild_id=987654321, category_name="Test")
        config = guild_config_from_db(row)
        assert config.guild_id == 987654321
        assert config.category_name == "Test"

    def test_round_trip(self) -> None:
        original = GuildConfig(guild_id=555, category_name="Round Trip")
        row = guild_config_to_db(original)
        restored = guild_config_from_db(row)
        assert restored == original

    def test_default_category_round_trip(self) -> None:
        original = GuildConfig(guild_id=777)
        row = guild_config_to_db(original)
        restored = guild_config_from_db(row)
        assert restored.category_name == "RealmAI Sessions"


from db.repositories.guild_config_repo import GuildConfigRepository


class TestGuildConfigRepository:
    """GuildConfigRepository CRUD tests."""

    def test_save_and_get(self, db_session: Session) -> None:
        repo = GuildConfigRepository(db_session)
        config = GuildConfig(guild_id=123456789, category_name="Test")
        repo.save(config)
        db_session.commit()

        result = repo.get(123456789)
        assert result is not None
        assert result.guild_id == 123456789
        assert result.category_name == "Test"

    def test_get_missing_returns_none(self, db_session: Session) -> None:
        repo = GuildConfigRepository(db_session)
        assert repo.get(999999) is None

    def test_upsert_insert(self, db_session: Session) -> None:
        repo = GuildConfigRepository(db_session)
        config = GuildConfig(guild_id=111, category_name="New")
        repo.upsert(config)
        db_session.commit()

        result = repo.get(111)
        assert result is not None
        assert result.category_name == "New"

    def test_upsert_update(self, db_session: Session) -> None:
        repo = GuildConfigRepository(db_session)
        repo.save(GuildConfig(guild_id=222, category_name="Original"))
        db_session.commit()

        repo.upsert(GuildConfig(guild_id=222, category_name="Updated"))
        db_session.commit()

        result = repo.get(222)
        assert result is not None
        assert result.category_name == "Updated"

    def test_delete(self, db_session: Session) -> None:
        repo = GuildConfigRepository(db_session)
        repo.save(GuildConfig(guild_id=333))
        db_session.commit()

        repo.delete(333)
        db_session.commit()

        assert repo.get(333) is None

    def test_delete_missing_is_noop(self, db_session: Session) -> None:
        repo = GuildConfigRepository(db_session)
        repo.delete(999)  # should not raise
