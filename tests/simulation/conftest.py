"""Shared fixtures for the simulator's own test suite."""

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
def ollama_mocked(httpx_mock: HTTPXMock) -> OllamaClient:
    httpx_mock.add_response(url=TAGS_URL, json={"models": []})
    return OllamaClient(simulation_mode=True)


@pytest.fixture()
def scenario_ai(in_memory_db_factory, ollama_mocked) -> ScenarioRunner:
    """ScenarioRunner with AI components wired (real Interpreter/Narrator)."""
    return ScenarioRunner(
        in_memory_db_factory, ai_enabled=True, ollama_client=ollama_mocked
    )
