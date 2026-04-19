"""Tests for the Opening Reframer module."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from pytest_httpx import HTTPXMock

from ai.client import OllamaClient
from ai.opening_reframer import (
    OpeningReframer,
    PartyMember,
    ReframedOpening,
)
from tests.ai.conftest import CHAT_URL, make_ollama_response


PROMPT_PATH = (
    Path(__file__).parent.parent.parent / "ai" / "prompts" / "system_opening_reframer.txt"
)


def _valid_reframed() -> dict[str, str]:
    """A schema-valid reframed opening for mocking the LLM."""
    return {
        "premise": "La cathédrale se dresse dans le brouillard, pierre noircie par des siècles d'oubli. La ville en contrebas a cessé d'en prononcer le nom.",
        "situation": "Depuis trois lunes, des portes scellées s'entrouvrent seules la nuit et des mercenaires envoyés en reconnaissance ne reviennent pas.",
        "call_to_action": "Un notable de la ville basse vous a payé cinquante pièces d'argent pour pénétrer la cathédrale et récupérer un coffret dans la crypte. Vous disposez de trois jours, pas un de plus.",
        "arrival_hook": "Vous franchissez le Porche d'Entrée à la nuit tombée, la lettre de commande glissée sous votre ceinture, la nef ouverte devant vous.",
        "party_premise": "Une lame de l'ombre payée pour fouiller une cathédrale que personne d'autre n'ose approcher.",
    }


def _single_mercenary_party() -> list[PartyMember]:
    return [
        PartyMember(
            name="Roub",
            race="Human",
            char_class="Rogue",
            kit="Shadow Blade",
            motivation="Contract",
        ),
    ]


# ---------------------------------------------------------------------------
# Prompt sanity
# ---------------------------------------------------------------------------


class TestSystemPrompt:
    def test_prompt_file_exists(self) -> None:
        assert PROMPT_PATH.exists(), f"Missing system prompt: {PROMPT_PATH}"
        assert len(PROMPT_PATH.read_text()) > 500, "System prompt is suspiciously short"

    def test_prompt_bans_chosen_one_tropes(self) -> None:
        """The prompt must name the forbidden tropes explicitly — deleting
        the ban list by mistake would silently regress the fix."""
        content = PROMPT_PATH.read_text().lower()
        # The phrases the user specifically called out.
        assert "élu" in content
        assert "dernier gardien" in content or "last guardian" in content
        assert "prophétie" in content or "prophecy" in content

    def test_prompt_maps_shadow_blade_to_mercenary(self) -> None:
        """The Shadow Blade kit must be explicitly mapped to the mercenary
        archetype — this is the exact regression the fix addresses."""
        content = PROMPT_PATH.read_text().lower()
        assert "shadow blade" in content
        assert "mercenary" in content or "mercenaire" in content or "sellsword" in content


# ---------------------------------------------------------------------------
# Reframer mechanics
# ---------------------------------------------------------------------------


@pytest.fixture
def reframer(ollama_client: OllamaClient) -> OpeningReframer:
    return OpeningReframer(ollama_client)


class TestReframeHappyPath:
    def test_returns_parsed_reframed_opening(
        self, httpx_mock: HTTPXMock, reframer: OpeningReframer,
    ) -> None:
        httpx_mock.add_response(
            url=CHAT_URL, json=make_ollama_response(_valid_reframed()),
        )

        result = reframer.reframe(
            original_premise="La cathédrale est sacrée.",
            original_situation="Des événements étranges surviennent.",
            original_call_to_action="Vous êtes les derniers gardiens, entrez et purifiez.",
            original_arrival_hook="Votre destin vous appelle au Porche d'Entrée.",
            location_name="Le Porche d'Entrée",
            villain_name="L'Élu Corrompu",
            first_beat_description="Enquêter sur le moine gardien pour obtenir l'accès au temple.",
            party=_single_mercenary_party(),
        )

        assert isinstance(result, ReframedOpening)
        assert result.party_premise.startswith("Une lame de l'ombre")
        assert "payé" in result.call_to_action

    def test_party_composition_appears_in_user_message(
        self, httpx_mock: HTTPXMock, reframer: OpeningReframer,
    ) -> None:
        """The kit name and motivation key must reach the LLM — otherwise the
        reframer has no basis to make the roles consistent."""
        httpx_mock.add_response(
            url=CHAT_URL, json=make_ollama_response(_valid_reframed()),
        )

        reframer.reframe(
            original_premise="x" * 20,
            original_situation="y" * 20,
            original_call_to_action="z" * 20,
            original_arrival_hook="w" * 20,
            location_name="Lieu",
            villain_name="Méchant",
            first_beat_description="Parler au gardien.",
            party=_single_mercenary_party(),
        )

        # Inspect the payload sent to Ollama — the party block must be there.
        request = httpx_mock.get_requests(url=CHAT_URL)[0]
        body = json.loads(request.content)
        user_msg = next(
            m["content"] for m in body["messages"] if m["role"] == "user"
        )
        assert "Shadow Blade" in user_msg
        assert "Contract" in user_msg
        assert "Roub" in user_msg


class TestReframeErrors:
    def test_empty_party_raises(self, reframer: OpeningReframer) -> None:
        with pytest.raises(ValueError, match="at least one party member"):
            reframer.reframe(
                original_premise="x" * 20,
                original_situation="y" * 20,
                original_call_to_action="z" * 20,
                original_arrival_hook="w" * 20,
                location_name="Lieu",
                villain_name="Méchant",
                first_beat_description="desc",
                party=[],
            )

    def test_invalid_llm_output_raises_validation_error(
        self, httpx_mock: HTTPXMock, reframer: OpeningReframer,
    ) -> None:
        """The reframer must NOT silently accept a malformed response —
        callers need to see the ValidationError to fall back to the
        original arc text."""
        httpx_mock.add_response(
            url=CHAT_URL,
            json=make_ollama_response({"premise": "too", "extra": "junk"}),
        )

        with pytest.raises(ValidationError):
            reframer.reframe(
                original_premise="x" * 20,
                original_situation="y" * 20,
                original_call_to_action="z" * 20,
                original_arrival_hook="w" * 20,
                location_name="Lieu",
                villain_name="Méchant",
                first_beat_description="desc",
                party=_single_mercenary_party(),
            )
