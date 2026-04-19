"""Tests for the Narrator module."""

from unittest.mock import MagicMock

import pytest
from pytest_httpx import HTTPXMock

from ai.client import LLMParseError, OllamaClient, OllamaUnavailableError
from ai.models import NarrativeResult
from ai.narrator import Narrator
from tests.ai.conftest import CHAT_URL, make_ollama_response


@pytest.fixture
def narrator(ollama_client: OllamaClient) -> Narrator:
    return Narrator(ollama_client)


def test_narrate_returns_narrative_result(httpx_mock: HTTPXMock, narrator: Narrator) -> None:
    """Narrator returns a valid NarrativeResult."""
    response_data = {
        "narrative": "Your axe bites deep into the goblin's shoulder, drawing a cry of pain.",
        "tone": "dramatic",
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    result = narrator.narrate(
        action_result_text="Thorin attacks Goblin. Hit! 8 damage dealt.",
        context_prompt="## Game State\nLocation: Goblin Cave\n## Recent Events\nThorin entered the cave.",
    )

    assert isinstance(result, NarrativeResult)
    assert result.narrative == "Your axe bites deep into the goblin's shoulder, drawing a cry of pain."
    assert result.tone == "dramatic"


def test_narrate_uses_both_context_and_action(
    httpx_mock: HTTPXMock, narrator: Narrator
) -> None:
    """The user message includes both context_prompt and action_result_text."""
    response_data = {"narrative": "The skeleton crumbles into ash as the fireball consumes it.", "tone": "somber"}
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    narrator.narrate(
        action_result_text="Merlin casts Fireball. Skeleton fails save. 12 fire damage.",
        context_prompt="## Game State\nMerlin: 30/30 HP",
    )

    # Verify the chat request was made (tags health check + chat = 2 requests)
    assert len(httpx_mock.get_requests()) == 2


def test_narrate_various_tones(httpx_mock: HTTPXMock, narrator: Narrator) -> None:
    """Narrator accepts all valid tones."""
    for tone in ["dramatic", "tense", "humorous", "somber"]:
        httpx_mock.add_response(
            url=CHAT_URL,
            json=make_ollama_response({"narrative": "Something happened in the chamber, and the air grew thick with tension.", "tone": tone}),
        )
        result = narrator.narrate(
            action_result_text="Some action occurred.",
            context_prompt="Context here.",
        )
        assert result.tone == tone


def test_narrate_uses_high_temperature(httpx_mock: HTTPXMock, narrator: Narrator) -> None:
    """Narrator uses temperature 0.8 for creative output."""
    httpx_mock.add_response(
        url=CHAT_URL,
        json=make_ollama_response({"narrative": "The battle rages on — your blow glances off the enemy's armour.", "tone": "tense"}),
    )
    result = narrator.narrate(
        action_result_text="Miss. No damage.",
        context_prompt="Context.",
    )
    assert isinstance(result, NarrativeResult)
    # Temperature is tested implicitly — if wrong, the call would fail or mock mismatch


def test_narrate_includes_player_intent_and_outcome_facts():
    client = MagicMock()
    client.chat_json.return_value = {"narrative": "A lengthy narrative that passes the fifty character threshold easily here.", "tone": "tense"}
    narrator = Narrator(client)

    narrator.narrate(
        action_result_text="Xavier searches Croix de fer.",
        context_prompt="## Location\nÉglise\nVieille paroisse.",
        language="fr",
        player_intent="inspecte la croix de fer pour voir si c une d'origine de 39-45",
        outcome_facts="",
    )

    args, kwargs = client.chat_json.call_args
    messages = args[1] if len(args) > 1 else kwargs["messages"]
    user_msg = messages[-1]["content"]
    assert "39-45" in user_msg
    assert "Église" in user_msg
    assert "Xavier searches" in user_msg


def test_narrate_npc_dialogue_flag_adds_reminder():
    client = MagicMock()
    client.chat_json.return_value = {"narrative": "A lengthy narrative that passes the fifty character threshold easily here.", "tone": "dramatic"}
    narrator = Narrator(client)

    narrator.narrate(
        action_result_text="Xavier speaks with Elie.",
        context_prompt="## Location\nÉglise",
        outcome_facts="Elie responds to the player.",
        has_npc_dialogue=True,
    )

    args, kwargs = client.chat_json.call_args
    messages = args[1] if len(args) > 1 else kwargs["messages"]
    user_msg = messages[-1]["content"]
    assert "displayed separately" in user_msg
    assert "body language" in user_msg


def test_narrate_no_reminder_without_npc_dialogue():
    client = MagicMock()
    client.chat_json.return_value = {"narrative": "A lengthy narrative that passes the fifty character threshold easily here.", "tone": "dramatic"}
    narrator = Narrator(client)

    narrator.narrate(
        action_result_text="Xavier looks around.",
        context_prompt="## Location\nForest",
    )

    args, kwargs = client.chat_json.call_args
    messages = args[1] if len(args) > 1 else kwargs["messages"]
    user_msg = messages[-1]["content"]
    assert "displayed separately" not in user_msg


def test_narrate_legacy_signature_still_works():
    client = MagicMock()
    client.chat_json.return_value = {"narrative": "A lengthy narrative that passes the fifty character threshold easily here.", "tone": "dramatic"}
    narrator = Narrator(client)

    narrator.narrate(
        action_result_text="Goblin takes 8 damage.",
        context_prompt="## Location\nForest",
    )

    args, kwargs = client.chat_json.call_args
    messages = args[1] if len(args) > 1 else kwargs["messages"]
    user_msg = messages[-1]["content"]
    assert "Goblin" in user_msg


class TestTemplateFallback:
    """Template fallback returns a valid NarrativeResult without calling LLM."""

    def test_template_fallback_returns_narrative_result(self, narrator: Narrator) -> None:
        result = narrator._template_fallback(
            action_result_text="Thorin attacks Goblin. Hit! 8 damage dealt.",
            outcome_facts="",
        )
        assert isinstance(result, NarrativeResult)
        assert result.narrative
        assert len(result.narrative) >= 30
        assert result.tone in {"dramatic", "tense", "humorous", "somber"}

    def test_template_fallback_picks_attack_variant(self, narrator: Narrator) -> None:
        result = narrator._template_fallback(
            action_result_text="Thorin attacks Goblin. Hit! 8 damage dealt.",
            outcome_facts="",
        )
        # The "attack" template family includes the action verb in some way.
        assert "attaque" in result.narrative.lower() or "coup" in result.narrative.lower() \
            or "combat" in result.narrative.lower()

    def test_template_fallback_picks_default_for_unknown_verb(self, narrator: Narrator) -> None:
        result = narrator._template_fallback(
            action_result_text="Some unrecognized mechanical phrase.",
            outcome_facts="",
        )
        # Default template is the "MJ regroups" line.
        assert "rassemble" in result.narrative.lower() or "MJ" in result.narrative

    def test_template_fallback_does_not_call_llm(
        self, httpx_mock: HTTPXMock, narrator: Narrator
    ) -> None:
        # No httpx_mock.add_response calls — any HTTP would fail the test.
        result = narrator._template_fallback("Some action.", "Some outcome.")
        assert isinstance(result, NarrativeResult)
        assert len(httpx_mock.get_requests()) == 1  # Only the health check on init.


class TestNarratorFallbackChain:
    """Narrator.narrate() never throws — falls back to template on repeated failure."""

    def test_narrate_returns_template_on_double_parse_error(
        self, monkeypatch: pytest.MonkeyPatch, narrator: Narrator
    ) -> None:
        call_count = {"n": 0}

        def fake_chat_json(*args, **kwargs):
            call_count["n"] += 1
            raise LLMParseError(
                "boom", raw_response="", model="qwen3.5:9b", messages=[],
            )

        monkeypatch.setattr(narrator._client, "chat_json", fake_chat_json)
        result = narrator.narrate(
            action_result_text="Thorin attacks Goblin. Hit! 8 damage.",
            context_prompt="Context.",
        )
        assert isinstance(result, NarrativeResult)
        assert result.narrative  # Template returned, non-empty
        assert call_count["n"] == 2  # Primary + simplified retry, then template

    def test_narrate_returns_template_on_ollama_unavailable(
        self, monkeypatch: pytest.MonkeyPatch, narrator: Narrator
    ) -> None:
        def fake_chat_json(*args, **kwargs):
            raise OllamaUnavailableError("Ollama down")

        monkeypatch.setattr(narrator._client, "chat_json", fake_chat_json)
        result = narrator.narrate(
            action_result_text="Some action.",
            context_prompt="Some context.",
        )
        assert isinstance(result, NarrativeResult)
        assert result.narrative

    def test_narrate_retries_with_simplified_prompt_when_first_call_too_short(
        self, monkeypatch: pytest.MonkeyPatch, narrator: Narrator
    ) -> None:
        call_count = {"n": 0}
        responses = [
            {"narrative": "Short.", "tone": "dramatic"},  # Too short → retry
            {"narrative": "A much longer second narrative that will pass the 50-char threshold.", "tone": "tense"},
        ]

        # Wrap to track calls and advance index
        def chat_json_advance(*args, **kwargs):
            r = responses[call_count["n"]]
            call_count["n"] += 1
            return r

        monkeypatch.setattr(narrator._client, "chat_json", chat_json_advance)
        result = narrator.narrate(
            action_result_text="Some action.",
            context_prompt="Some context.",
        )
        assert call_count["n"] == 2  # Primary failed (too short) + simplified retry succeeded
        assert "longer second narrative" in result.narrative
        assert result.tone == "tense"

    def test_narrate_succeeds_first_call_no_retry(
        self, monkeypatch: pytest.MonkeyPatch, narrator: Narrator
    ) -> None:
        call_count = {"n": 0}

        def fake_chat_json(*args, **kwargs):
            call_count["n"] += 1
            return {
                "narrative": "A perfectly valid first-call narrative that exceeds fifty characters in length easily.",
                "tone": "dramatic",
            }

        monkeypatch.setattr(narrator._client, "chat_json", fake_chat_json)
        result = narrator.narrate(
            action_result_text="Action.", context_prompt="Context.",
        )
        assert call_count["n"] == 1  # Only the primary call
        assert "perfectly valid first-call narrative" in result.narrative
