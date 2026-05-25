"""Verifies ScenarioRunner can be created with ai_enabled=True and wires AI."""

from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from ai.client import OllamaClient
from db.database import Base
from tests.scenarios.scenario_runner import ScenarioRunner

OLLAMA_BASE = "http://localhost:11434"
TAGS_URL = f"{OLLAMA_BASE}/api/tags"


@pytest.fixture()
def in_memory_db_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine)
    engine.dispose()


@pytest.fixture()
def ollama_client(httpx_mock: HTTPXMock) -> OllamaClient:
    httpx_mock.add_response(url=TAGS_URL, json={"models": []})
    return OllamaClient(simulation_mode=True)


async def test_ai_enabled_wires_real_interpreter_and_narrator(
    in_memory_db_factory, ollama_client
) -> None:
    runner = ScenarioRunner(
        in_memory_db_factory, ai_enabled=True, ollama_client=ollama_client
    )
    await runner.start_campaign(theme="Test", players=1)
    session = runner.session
    assert session is not None
    assert session.interpreter is not None, "interpreter should be wired"
    assert session.narrator is not None, "narrator should be wired"


async def test_ai_disabled_keeps_existing_behavior(in_memory_db_factory) -> None:
    runner = ScenarioRunner(in_memory_db_factory)
    await runner.start_campaign(theme="Test", players=1)
    session = runner.session
    assert session is not None
    # Default behavior: AI is not wired (None)
    assert session.interpreter is None
    assert session.narrator is None
