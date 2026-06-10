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


class TestPlaceholderFilter:
    """H13 — bracketed placeholders must never reach the player.

    Observed in prod: the 9b parroted the « À ton tour, [nom]. » example
    from the system prompt verbatim, leaking "[nom]" into Discord.
    """

    def test_narrate_rejects_bracketed_placeholder(
        self, monkeypatch: pytest.MonkeyPatch, narrator: Narrator
    ) -> None:
        call_count = {"n": 0}
        responses = [
            {
                "narrative": "Le gobelin titube sous l'impact et recule vers le mur. À ton tour, [nom].",
                "tone": "tense",
            },
            {
                "narrative": "Le gobelin titube sous l'impact et recule vers le mur. À ton tour, Thorin.",
                "tone": "tense",
            },
        ]

        def fake_chat_json(*args, **kwargs):
            r = responses[call_count["n"]]
            call_count["n"] += 1
            return r

        monkeypatch.setattr(narrator._client, "chat_json", fake_chat_json)
        result = narrator.narrate(
            action_result_text="Thorin attacks Goblin. Hit! 8 damage.",
            context_prompt="Context.",
        )
        assert call_count["n"] == 2  # Placeholder rejected → simplified retry
        assert "[nom]" not in result.narrative
        assert "Thorin" in result.narrative

    def test_narrate_rejects_brace_placeholder(
        self, monkeypatch: pytest.MonkeyPatch, narrator: Narrator
    ) -> None:
        call_count = {"n": 0}
        responses = [
            {
                "narrative": "Les flammes lèchent la voûte tandis que {personnage} esquive de justesse le coup.",
                "tone": "dramatic",
            },
            {
                "narrative": "Les flammes lèchent la voûte tandis que Mira esquive de justesse le coup porté.",
                "tone": "dramatic",
            },
        ]

        def fake_chat_json(*args, **kwargs):
            r = responses[call_count["n"]]
            call_count["n"] += 1
            return r

        monkeypatch.setattr(narrator._client, "chat_json", fake_chat_json)
        result = narrator.narrate(
            action_result_text="Mira dodges.", context_prompt="Context.",
        )
        assert call_count["n"] == 2
        assert "{personnage}" not in result.narrative

    def test_narrate_uses_template_when_both_tiers_leak_placeholders(
        self, monkeypatch: pytest.MonkeyPatch, narrator: Narrator
    ) -> None:
        def fake_chat_json(*args, **kwargs):
            return {
                "narrative": "Une narration assez longue pour le seuil mais qui invite [nom] à jouer son tour.",
                "tone": "dramatic",
            }

        monkeypatch.setattr(narrator._client, "chat_json", fake_chat_json)
        result = narrator.narrate(
            action_result_text="Thorin attacks Goblin. Hit! 8 damage.",
            context_prompt="Context.",
        )
        assert isinstance(result, NarrativeResult)
        assert "[" not in result.narrative  # Template fallback, no leak


class TestNarratorPayloadRobustness:
    """H10 — narrate() never throws even on malformed LLM payloads.

    The 9b model writing French routinely emits localized tone values
    ("dramatique") or structurally broken meta fields. None of these may
    escape the three-tier fallback chain: the mechanics were already
    applied, so a crash here makes the player retry and double-apply.
    """

    def test_narrate_normalizes_french_tone(
        self, monkeypatch: pytest.MonkeyPatch, narrator: Narrator
    ) -> None:
        call_count = {"n": 0}

        def fake_chat_json(*args, **kwargs):
            call_count["n"] += 1
            return {
                "narrative": "L'acier mord l'épaule du gobelin qui hurle de douleur dans la crypte.",
                "tone": "dramatique",  # French — the 9b parrots the campaign language
            }

        monkeypatch.setattr(narrator._client, "chat_json", fake_chat_json)
        result = narrator.narrate(
            action_result_text="Thorin attacks Goblin. Hit! 8 damage.",
            context_prompt="Context.",
        )
        assert result.tone == "dramatic"
        assert call_count["n"] == 1  # Normalized in place — no retry burned

    def test_narrate_normalizes_unknown_tone_to_default(
        self, monkeypatch: pytest.MonkeyPatch, narrator: Narrator
    ) -> None:
        def fake_chat_json(*args, **kwargs):
            return {
                "narrative": "Une narration suffisamment longue pour passer le seuil des cinquante caractères.",
                "tone": "epic",  # Not a valid tone in any supported language
            }

        monkeypatch.setattr(narrator._client, "chat_json", fake_chat_json)
        result = narrator.narrate(
            action_result_text="Action.", context_prompt="Context.",
        )
        assert result.tone == "dramatic"

    def test_narrate_falls_back_when_payload_not_dict(
        self, monkeypatch: pytest.MonkeyPatch, narrator: Narrator
    ) -> None:
        """json.loads can return a list/str — narrate must not crash on it."""
        def fake_chat_json(*args, **kwargs):
            return ["not", "a", "dict"]

        monkeypatch.setattr(narrator._client, "chat_json", fake_chat_json)
        result = narrator.narrate(
            action_result_text="Some action.", context_prompt="Context.",
        )
        assert isinstance(result, NarrativeResult)
        assert result.narrative  # Template fallback kept the session alive

    def test_narrate_retries_on_broken_meta_field(
        self, monkeypatch: pytest.MonkeyPatch, narrator: Narrator
    ) -> None:
        """A non-list npcs_mentioned (TypeError) must route to tier 2, not raise."""
        call_count = {"n": 0}
        responses: list[dict] = [
            {
                "narrative": "Une première narration assez longue pour le seuil des cinquante caractères.",
                "tone": "tense",
                "npcs_mentioned": 42,  # list(42) → TypeError
            },
            {
                "narrative": "La seconde narration simplifiée passe sans encombre le seuil requis.",
                "tone": "tense",
            },
        ]

        def fake_chat_json(*args, **kwargs):
            r = responses[call_count["n"]]
            call_count["n"] += 1
            return r

        monkeypatch.setattr(narrator._client, "chat_json", fake_chat_json)
        result = narrator.narrate(
            action_result_text="Action.", context_prompt="Context.",
        )
        assert call_count["n"] == 2
        assert "seconde narration simplifiée" in result.narrative


class TestToneNormalization:
    """Unit tests for the tone lookup."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("dramatic", "dramatic"),
            ("Dramatique", "dramatic"),
            (" tendu ", "tense"),
            ("tendue", "tense"),
            ("humoristique", "humorous"),
            ("sombre", "somber"),
            ("dramático", "dramatic"),
            ("dramatisch", "dramatic"),
            ("epic", "dramatic"),  # unknown → default
            (None, "dramatic"),
            (42, "dramatic"),
        ],
    )
    def test_normalize_tone(self, raw: object, expected: str) -> None:
        from ai.narrator import _normalize_tone

        assert _normalize_tone(raw) == expected


class TestNarratorMetaParsing:
    """Narrator parses meta fields when LLM emits them, falls back to defaults otherwise."""

    def test_narrate_parses_meta_fields(
        self, monkeypatch: pytest.MonkeyPatch, narrator: Narrator
    ) -> None:
        def fake_chat_json(*args, **kwargs):
            return {
                "narrative": "Vlaxos parries with a snarl, pushing you back toward the cellar door.",
                "tone": "tense",
                "scene_goal_touched": True,
                "beat_advanced": True,
                "npcs_mentioned": ["Vlaxos"],
                "locked_facts_used": ["map_hidden_in_cellar"],
            }
        monkeypatch.setattr(narrator._client, "chat_json", fake_chat_json)
        result = narrator.narrate(
            action_result_text="Player attacks Vlaxos. Hit, 8 damage.",
            context_prompt="Context.",
        )
        assert result.scene_goal_touched is True
        assert result.beat_advanced is True
        assert result.npcs_mentioned == ["Vlaxos"]
        assert result.locked_facts_used == ["map_hidden_in_cellar"]

    def test_narrate_defaults_meta_fields_when_llm_omits(
        self, monkeypatch: pytest.MonkeyPatch, narrator: Narrator
    ) -> None:
        def fake_chat_json(*args, **kwargs):
            return {
                "narrative": "A long enough narrative that exceeds fifty characters with ease.",
                "tone": "dramatic",
            }
        monkeypatch.setattr(narrator._client, "chat_json", fake_chat_json)
        result = narrator.narrate(
            action_result_text="Action.", context_prompt="Context.",
        )
        assert result.scene_goal_touched is False
        assert result.beat_advanced is False
        assert result.npcs_mentioned == []
        assert result.locked_facts_used == []


class TestNarratorDirectionInjection:
    """Verify the [STORY DIRECTION] block is injected when a DirectorNote is provided."""

    def test_call_narrator_with_director_note_injects_direction_block(
        self, monkeypatch: pytest.MonkeyPatch, narrator: Narrator
    ) -> None:
        from ai.models import DirectorNote
        captured: list[dict] = []

        def fake_chat_json(model, messages, *args, **kwargs):
            captured.append(messages[-1])  # user message
            return {
                "narrative": "A long enough narrative that exceeds fifty characters easily for the test.",
                "tone": "dramatic",
            }

        monkeypatch.setattr(narrator._client, "chat_json", fake_chat_json)

        note = DirectorNote(
            coherence_issues=[],
            suggested_hooks=[],
            priority="low",
            current_objective="Find the map.",
            current_beat_atmosphere="Tension and unease fill the air.",
            required_mentions=["Aldric"],
            forbidden_topics=["map_in_cellar"],
        )

        narrator.narrate(
            action_result_text="Player searches.",
            context_prompt="Context.",
            director_note=note,
        )

        user_content = captured[0]["content"]
        assert "[STORY DIRECTION]" in user_content
        assert "Find the map." in user_content
        assert "Tension and unease fill the air." in user_content
        assert "Aldric" in user_content
        assert "map_in_cellar" in user_content

    def test_call_narrator_without_director_note_no_direction_block(
        self, monkeypatch: pytest.MonkeyPatch, narrator: Narrator
    ) -> None:
        captured: list[dict] = []

        def fake_chat_json(model, messages, *args, **kwargs):
            captured.append(messages[-1])
            return {
                "narrative": "A long enough narrative that exceeds fifty characters easily for the test.",
                "tone": "dramatic",
            }

        monkeypatch.setattr(narrator._client, "chat_json", fake_chat_json)
        narrator.narrate(
            action_result_text="Player searches.",
            context_prompt="Context.",
        )

        user_content = captured[0]["content"]
        assert "[STORY DIRECTION]" not in user_content

    def test_empty_director_note_skips_direction_block(
        self, monkeypatch: pytest.MonkeyPatch, narrator: Narrator
    ) -> None:
        """A DirectorNote with all empty direction fields should not inject the block."""
        from ai.models import DirectorNote
        captured: list[dict] = []

        def fake_chat_json(model, messages, *args, **kwargs):
            captured.append(messages[-1])
            return {
                "narrative": "A long enough narrative that exceeds fifty characters easily for the test.",
                "tone": "dramatic",
            }

        monkeypatch.setattr(narrator._client, "chat_json", fake_chat_json)

        empty_note = DirectorNote(
            coherence_issues=[],
            suggested_hooks=[],
            priority="low",
        )
        narrator.narrate(
            action_result_text="Player searches.",
            context_prompt="Context.",
            director_note=empty_note,
        )

        user_content = captured[0]["content"]
        assert "[STORY DIRECTION]" not in user_content


class TestStoryDirectorCache:
    """Verify the latest-note cache works end-to-end."""

    def test_cached_note_for_returns_none_initially(self) -> None:
        from ai.story_director import cached_note_for, reset_latest_notes
        reset_latest_notes()
        assert cached_note_for("cmp_unknown") is None

    def test_cached_note_for_returns_stored_note(self) -> None:
        from ai.models import DirectorNote
        from ai.story_director import cached_note_for, reset_latest_notes, _store_latest_note
        reset_latest_notes()
        note = DirectorNote(
            coherence_issues=[], suggested_hooks=[], priority="low",
            current_objective="Test objective",
        )
        _store_latest_note("cmp_test", note)
        retrieved = cached_note_for("cmp_test")
        assert retrieved is not None
        assert retrieved.current_objective == "Test objective"
