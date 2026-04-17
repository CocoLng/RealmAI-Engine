"""Tests for the Arc Generator module."""

from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

from ai.arc_generator import ArcGenerator
from ai.client import OllamaClient
from engine.arc_recipes import ArcRecipe, Archetype, BeatType, Tone, VillainArchetype
from tests.ai.conftest import CHAT_URL, make_ollama_response
from world.story_arc import StoryArc, StoryBeat


def _make_arc_data(beat_count: int = 10) -> dict:
    """Build a valid arc JSON dict with the given number of beats."""
    encounter_types = ["social", "exploration", "combat", "puzzle"]
    beats = []
    for i in range(1, beat_count + 1):
        is_last = i == beat_count
        beats.append({
            "beat_number": i,
            "title": f"Acte {i}",
            "description": f"Description du beat {i}. Une aventure se dessine. Les heros avancent.",
            "location_hint": f"Lieu {i}",
            "npc_names": [f"PNJ_{i}"] if i % 2 == 0 else [],
            "encounter_type": "boss" if is_last else encounter_types[i % len(encounter_types)],
            "is_twist": i == 7,
        })
    return {
        "campaign_id": "",
        "theme": "dark fantasy",
        "premise": "Un ancien mal se reveille dans les profondeurs de la terre, menacant le royaume.",
        "beats": beats,
        "villain_name": "Seigneur Malachar",
        "villain_motivation": "Dominer le monde en liberant une armee de morts-vivants.",
    }


def _make_recipe() -> ArcRecipe:
    """Build a valid ArcRecipe for testing."""
    return ArcRecipe(
        archetype=Archetype.mystery,
        beat_sequence=[
            BeatType.exploration, BeatType.social, BeatType.puzzle,
            BeatType.social, BeatType.exploration, BeatType.puzzle,
            BeatType.combat, BeatType.exploration, BeatType.social,
            BeatType.boss,
        ],
        beat_subtypes=[
            "tracking", "negotiation", "riddle",
            "interrogation", "infiltration", "investigation",
            "ambush", "discovery", "ceremony",
            "boss",
        ],
        complications=["Trahison d'un allié", "Course contre la montre"],
        tone=Tone.mysterieux,
        twist_position=5,
        num_beats=10,
        villain_archetype=VillainArchetype.manipulateur,
    )


@pytest.fixture
def generator(ollama_client: OllamaClient) -> ArcGenerator:
    return ArcGenerator(ollama_client)


def test_system_prompt_file_exists() -> None:
    """The system prompt file for the arc generator must exist."""
    prompt_path = Path(__file__).parent.parent.parent / "ai" / "prompts" / "system_arc_generator.txt"
    assert prompt_path.exists(), f"Missing system prompt: {prompt_path}"
    content = prompt_path.read_text()
    assert len(content) > 100, "System prompt seems too short"


def test_generate_returns_valid_story_arc(
    httpx_mock: HTTPXMock, generator: ArcGenerator
) -> None:
    """ArcGenerator.generate() returns a valid StoryArc with correct theme."""
    arc_data = _make_arc_data(10)
    # Single call (no recipe)
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(arc_data))

    result = generator.generate(theme="dark fantasy", player_count=4)

    assert isinstance(result, StoryArc)
    assert result.theme == "dark fantasy"
    assert result.villain_name == "Seigneur Malachar"
    assert len(result.beats) == 10


def test_generate_with_recipe(
    httpx_mock: HTTPXMock, generator: ArcGenerator
) -> None:
    """ArcGenerator.generate() with a recipe uses single-call generation."""
    arc_data = _make_arc_data(10)
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(arc_data))

    recipe = _make_recipe()
    result = generator.generate(theme="dark fantasy", player_count=4, recipe=recipe)

    assert isinstance(result, StoryArc)
    assert result.theme == "dark fantasy"
    assert len(result.beats) == 10


def test_generate_beats_have_correct_structure(
    httpx_mock: HTTPXMock, generator: ArcGenerator
) -> None:
    """Each beat in the generated arc has the required fields."""
    arc_data = _make_arc_data(10)
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(arc_data))

    result = generator.generate(theme="dark fantasy", player_count=3)

    for beat in result.beats:
        assert beat.beat_number >= 1
        assert len(beat.title) > 0
        assert len(beat.description) > 0
        assert beat.encounter_type in ("social", "combat", "exploration", "puzzle", "boss")


def test_generate_last_beat_is_boss(
    httpx_mock: HTTPXMock, generator: ArcGenerator
) -> None:
    """The final beat of the generated arc must be a boss encounter."""
    arc_data = _make_arc_data(12)
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(arc_data))

    result = generator.generate(theme="pirate adventure", player_count=5)

    assert result.beats[-1].encounter_type == "boss"


def test_generate_contains_twist(
    httpx_mock: HTTPXMock, generator: ArcGenerator
) -> None:
    """The generated arc contains at least one twist beat."""
    arc_data = _make_arc_data(10)
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(arc_data))

    result = generator.generate(theme="dark fantasy", player_count=4)

    twist_beats = [b for b in result.beats if b.is_twist]
    assert len(twist_beats) >= 1


def test_generate_current_beat_index_starts_at_zero(
    httpx_mock: HTTPXMock, generator: ArcGenerator
) -> None:
    """A freshly generated arc starts at beat index 0."""
    arc_data = _make_arc_data(10)
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(arc_data))

    result = generator.generate(theme="mystery", player_count=2)

    assert result.current_beat_index == 0


def test_build_user_message_with_recipe_contains_recipe_fields(
    generator: ArcGenerator,
) -> None:
    """The recipe-based user message includes archetype, tone, beats, etc."""
    recipe = _make_recipe()
    msg = generator._build_user_message_with_recipe("dark fantasy", 4, recipe)

    assert "dark fantasy" in msg
    assert "4" in msg
    assert "mystery" in msg  # archetype
    assert "mystérieux" in msg  # tone
    assert "manipulateur" in msg  # villain archetype
    assert "Trahison d'un allié" in msg  # complication
    assert "[TWIST]" in msg
    assert "Beat 1:" in msg
    assert f"Beat {recipe.num_beats}:" in msg


def test_build_user_message_with_recipe_no_villain(
    generator: ArcGenerator,
) -> None:
    """When villain_archetype is None, the message shows 'au choix'."""
    recipe = _make_recipe().model_copy(update={"villain_archetype": None})
    msg = generator._build_user_message_with_recipe("dark fantasy", 4, recipe)

    assert "au choix" in msg


class TestBeatCompletionModels:
    """Tests for CompletionTrigger and BeatEffects on StoryBeat."""

    def test_story_beat_with_completion_trigger(self):
        from world.story_arc import CompletionTrigger, BeatEffects

        beat = StoryBeat(
            beat_number=1,
            title="The Wall That Sighs",
            description="Balance the mechanism.",
            location_hint="The bone barrier",
            npc_names=["Barnabé"],
            encounter_type="puzzle",
            completion_trigger=CompletionTrigger(
                type="interact",
                target="Le levier de l'Échiquier",
            ),
            on_complete=BeatEffects(
                unlock_exits=["La cour intérieure"],
                state_flags={"breach_open": True},
                narrative_hint="A breach opens in the bone wall.",
            ),
        )
        assert beat.completion_trigger is not None
        assert beat.completion_trigger.type == "interact"
        assert beat.completion_trigger.target == "Le levier de l'Échiquier"
        assert beat.on_complete.unlock_exits == ["La cour intérieure"]
        assert beat.on_complete.state_flags == {"breach_open": True}

    def test_story_beat_without_trigger_defaults_none(self):
        beat = StoryBeat(
            beat_number=1,
            title="Arrival",
            description="Arrive at the village.",
            location_hint="Village entrance",
            npc_names=[],
            encounter_type="exploration",
        )
        assert beat.completion_trigger is None
        assert beat.on_complete.unlock_exits == []

    def test_completion_trigger_types(self):
        from world.story_arc import CompletionTrigger

        for t in ("interact", "defeat", "talk", "arrive", "search", "pickup"):
            trigger = CompletionTrigger(type=t, target="some target")
            assert trigger.type == t

    def test_beat_effects_serialization(self):
        from world.story_arc import BeatEffects

        effects = BeatEffects(
            unlock_exits=["Exit A"],
            add_npcs=["Guard"],
            remove_items=["Key"],
            add_items=["Reward"],
            state_flags={"door_open": True},
            narrative_hint="The door swings open.",
        )
        data = effects.model_dump()
        restored = BeatEffects.model_validate(data)
        assert restored == effects

    def test_generated_beats_have_completion_triggers(self, httpx_mock, generator):
        """Beats should include completion_trigger and on_complete."""
        arc_data = _make_arc_data()
        for beat in arc_data["beats"]:
            beat["completion_trigger"] = {"type": "interact", "target": "some object"}
            beat["on_complete"] = {
                "unlock_exits": ["Next Area"],
                "state_flags": {"puzzle_solved": True},
                "narrative_hint": "Something changes.",
            }
        httpx_mock.add_response(json=make_ollama_response(arc_data))
        arc = generator.generate("test theme", 1)
        for beat in arc.beats:
            assert beat.completion_trigger is not None
            assert beat.on_complete.unlock_exits == ["Next Area"]


# ---------------------------------------------------------------------------
# Villain stat block
# ---------------------------------------------------------------------------


def _make_valid_villain_stat_block() -> dict:
    """Return a minimal valid villain stat block payload."""
    return {
        "tier": "boss",
        "archetype": "mentisseur",
        "multiattack_count": 3,
        "attacks": [
            {
                "name": "Lame d'obsidienne",
                "damage_dice": "1d8+4",
                "damage_type": "Slashing",
                "to_hit_bonus": 7,
                "range_type": "melee",
                "range_value": None,
            }
        ],
        "signature_abilities": [
            {
                "name": "Chant du Silence Éternel",
                "description": "Impose Frightened to all enemies in range.",
                "usage": "per_combat",
                "uses_remaining": 1,
                "is_reaction": False,
                "action_cost": "action",
                "effects": [
                    {
                        "kind": "condition",
                        "condition_name": "Frightened",
                        "condition_duration_rounds": 2,
                        "save_ability": "WIS",
                        "save_dc": 15,
                        "target_scope": "all_enemies",
                    }
                ],
            }
        ],
        "legendary_actions": [
            {
                "name": "Morsure du Sable",
                "cost": 1,
                "description": "Quick melee strike as a legendary action.",
                "effects": [
                    {
                        "kind": "damage",
                        "dice": "1d8+4",
                        "damage_type": "Slashing",
                        "target_scope": "single",
                    }
                ],
            },
            {
                "name": "Voile de Poussière",
                "cost": 2,
                "description": "Clouds a zone with obscuring dust.",
                "effects": [],
            },
            {
                "name": "Fracas Éternel",
                "cost": 3,
                "description": "A devastating AoE strike.",
                "effects": [
                    {
                        "kind": "aoe_damage",
                        "dice": "3d10",
                        "damage_type": "Thunder",
                        "target_scope": "zone",
                    }
                ],
            },
        ],
        "legendary_points_per_round": 3,
        "phases": [
            {
                "trigger_hp_percent": 50,
                "narrative_cue": "Ses yeux se teignent d'or liquide.",
                "unlock_signatures": ["Chant du Silence Éternel"],
                "attack_bonus": 2,
                "save_bonus": 1,
            }
        ],
        "behavior_profile": "tactical",
        "aggression_threshold": 20,
    }


def test_parses_villain_stat_block_with_signatures(
    httpx_mock: HTTPXMock, generator: ArcGenerator
) -> None:
    """A valid villain_stat_block with signatures is parsed into StoryArc."""
    arc_data = _make_arc_data(10)
    arc_data["villain_stat_block"] = _make_valid_villain_stat_block()
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(arc_data))

    arc = generator.generate(theme="dark fantasy", player_count=4)

    assert arc.villain_stat_block is not None
    assert arc.villain_stat_block.tier == "boss"
    assert arc.villain_stat_block.archetype == "mentisseur"
    assert len(arc.villain_stat_block.signature_abilities) == 1
    assert (
        arc.villain_stat_block.signature_abilities[0].name
        == "Chant du Silence Éternel"
    )


def test_parses_villain_stat_block_with_legendary_actions(
    httpx_mock: HTTPXMock, generator: ArcGenerator
) -> None:
    """Legendary actions with costs 1/2/3 are preserved."""
    arc_data = _make_arc_data(10)
    arc_data["villain_stat_block"] = _make_valid_villain_stat_block()
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(arc_data))

    arc = generator.generate(theme="dark fantasy", player_count=4)

    assert arc.villain_stat_block is not None
    actions = arc.villain_stat_block.legendary_actions
    assert len(actions) == 3
    assert {a.cost for a in actions} == {1, 2, 3}
    assert arc.villain_stat_block.legendary_points_per_round == 3


def test_parses_villain_stat_block_with_phases(
    httpx_mock: HTTPXMock, generator: ArcGenerator
) -> None:
    """Phase transitions are parsed with their HP trigger and bonuses."""
    arc_data = _make_arc_data(10)
    arc_data["villain_stat_block"] = _make_valid_villain_stat_block()
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(arc_data))

    arc = generator.generate(theme="dark fantasy", player_count=4)

    assert arc.villain_stat_block is not None
    phases = arc.villain_stat_block.phases
    assert len(phases) == 1
    assert phases[0].trigger_hp_percent == 50
    assert phases[0].attack_bonus == 2
    assert phases[0].triggered is False


def test_invalid_stat_block_falls_back_to_generic_boss(
    httpx_mock: HTTPXMock, generator: ArcGenerator
) -> None:
    """An invalid stat block (missing tier) falls back to generic_boss."""
    arc_data = _make_arc_data(10)
    bad = _make_valid_villain_stat_block()
    bad.pop("tier")  # invalid — tier is required
    arc_data["villain_stat_block"] = bad
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(arc_data))

    arc = generator.generate(theme="dark fantasy", player_count=4)

    assert arc.villain_stat_block is not None
    # Fallback keeps tier boss and tags archetype with the villain name.
    assert arc.villain_stat_block.tier == "boss"
    assert arc.villain_stat_block.archetype.startswith("generic_boss:")
    assert arc.villain_name in arc.villain_stat_block.archetype


def test_missing_stat_block_falls_back_to_generic_boss(
    httpx_mock: HTTPXMock, generator: ArcGenerator
) -> None:
    """If villain_stat_block is absent, fallback kicks in."""
    arc_data = _make_arc_data(10)
    # Do NOT add villain_stat_block at all.
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(arc_data))

    arc = generator.generate(theme="dark fantasy", player_count=4)

    assert arc.villain_stat_block is not None
    assert arc.villain_stat_block.tier == "boss"
    assert arc.villain_stat_block.archetype.startswith("generic_boss:")


def test_fallback_archetype_name_includes_villain_name(
    httpx_mock: HTTPXMock, generator: ArcGenerator
) -> None:
    """The fallback archetype string contains the villain's name for tracing."""
    arc_data = _make_arc_data(10)
    arc_data["villain_name"] = "Nyxa Voix-des-Cendres"
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(arc_data))

    arc = generator.generate(theme="dark fantasy", player_count=4)

    assert arc.villain_stat_block is not None
    assert "Nyxa Voix-des-Cendres" in arc.villain_stat_block.archetype


def test_villain_stat_block_backward_compat_legacy_arc() -> None:
    """Direct model_validate on a legacy arc dict (no stat block) defaults to None."""
    legacy = _make_arc_data(10)
    # Legacy JSON: no villain_stat_block key at all.
    arc = StoryArc.model_validate(legacy)
    assert arc.villain_stat_block is None


# ---------------------------------------------------------------------------
# Sanitization
# ---------------------------------------------------------------------------


class TestSanitizeArcData:
    """Unit tests for ArcGenerator._sanitize_arc_data()."""

    def test_state_flags_string_coerced_to_true(self) -> None:
        """Non-bool truthy string in state_flags → True."""
        data: dict = {
            "beats": [
                {
                    "beat_number": 1,
                    "on_complete": {
                        "state_flags": {
                            "location_explored": "place_centrale",
                            "door_open": "yes",
                        }
                    },
                }
            ],
            "villain_stat_block": None,
        }
        ArcGenerator._sanitize_arc_data(data)
        flags = data["beats"][0]["on_complete"]["state_flags"]
        assert flags == {"location_explored": True, "door_open": True}

    def test_state_flags_empty_string_coerced_to_false(self) -> None:
        """Empty string in state_flags → False."""
        data: dict = {
            "beats": [{"beat_number": 1, "on_complete": {"state_flags": {"flag": ""}}}],
            "villain_stat_block": None,
        }
        ArcGenerator._sanitize_arc_data(data)
        assert data["beats"][0]["on_complete"]["state_flags"] == {"flag": False}

    def test_state_flags_bool_untouched(self) -> None:
        """Existing booleans pass through unchanged."""
        data: dict = {
            "beats": [
                {"beat_number": 1, "on_complete": {"state_flags": {"a": True, "b": False}}}
            ],
            "villain_stat_block": None,
        }
        ArcGenerator._sanitize_arc_data(data)
        assert data["beats"][0]["on_complete"]["state_flags"] == {"a": True, "b": False}

    def test_state_flags_missing_on_complete_no_crash(self) -> None:
        """Beats without on_complete or state_flags don't crash."""
        data: dict = {
            "beats": [{"beat_number": 1}, {"beat_number": 2, "on_complete": {}}],
            "villain_stat_block": None,
        }
        ArcGenerator._sanitize_arc_data(data)  # must not raise

    def test_damage_type_electricity_normalized(self) -> None:
        """'Electricity' synonym is normalized to 'Lightning' in attacks."""
        data: dict = {
            "beats": [],
            "villain_stat_block": {
                "attacks": [{"damage_type": "Electricity"}],
                "signature_abilities": [],
                "legendary_actions": [],
            },
        }
        ArcGenerator._sanitize_arc_data(data)
        assert data["villain_stat_block"]["attacks"][0]["damage_type"] == "Lightning"

    def test_damage_type_signature_effects_normalized(self) -> None:
        """'Electricity' in signature ability effects is normalized."""
        data: dict = {
            "beats": [],
            "villain_stat_block": {
                "attacks": [],
                "signature_abilities": [
                    {"effects": [{"damage_type": "Electricity"}]}
                ],
                "legendary_actions": [],
            },
        }
        ArcGenerator._sanitize_arc_data(data)
        effect = data["villain_stat_block"]["signature_abilities"][0]["effects"][0]
        assert effect["damage_type"] == "Lightning"

    def test_damage_type_legendary_effects_normalized(self) -> None:
        """'Electricity' in legendary action effects is normalized."""
        data: dict = {
            "beats": [],
            "villain_stat_block": {
                "attacks": [],
                "signature_abilities": [],
                "legendary_actions": [
                    {"effects": [{"damage_type": "Electricity"}]}
                ],
            },
        }
        ArcGenerator._sanitize_arc_data(data)
        effect = data["villain_stat_block"]["legendary_actions"][0]["effects"][0]
        assert effect["damage_type"] == "Lightning"

    def test_target_scope_all_enemies_in_zone_normalized(self) -> None:
        """'all_enemies_in_zone' (invalid hybrid) → 'all_enemies'."""
        data: dict = {
            "beats": [],
            "villain_stat_block": {
                "attacks": [],
                "signature_abilities": [
                    {"effects": [{"target_scope": "all_enemies_in_zone"}]}
                ],
                "legendary_actions": [],
            },
        }
        ArcGenerator._sanitize_arc_data(data)
        effect = data["villain_stat_block"]["signature_abilities"][0]["effects"][0]
        assert effect["target_scope"] == "all_enemies"

    def test_target_scope_legendary_normalized(self) -> None:
        """'all_enemies_in_zone' in legendary action effects → 'all_enemies'."""
        data: dict = {
            "beats": [],
            "villain_stat_block": {
                "attacks": [],
                "signature_abilities": [],
                "legendary_actions": [
                    {"effects": [{"target_scope": "all_enemies_in_zone"}]}
                ],
            },
        }
        ArcGenerator._sanitize_arc_data(data)
        effect = data["villain_stat_block"]["legendary_actions"][0]["effects"][0]
        assert effect["target_scope"] == "all_enemies"

    def test_no_villain_stat_block_no_crash(self) -> None:
        """Absent villain_stat_block doesn't crash the sanitizer."""
        data: dict = {"beats": [], "villain_stat_block": None}
        ArcGenerator._sanitize_arc_data(data)  # must not raise
