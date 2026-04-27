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
    """Build a valid arc JSON dict with the given number of beats.

    Beats include npc_names for every social slot so the sanitizer's
    scaffolded TALK objective gets a real target. The shape stays minimal:
    no objectives, advance_rule, or completion_trigger — the sanitizer is
    expected to scaffold them from the encounter (sub)type.
    """
    encounter_types = ["social", "exploration", "combat", "puzzle"]
    beats = []
    for i in range(1, beat_count + 1):
        is_last = i == beat_count
        etype = "boss" if is_last else encounter_types[i % len(encounter_types)]
        # Always provide an NPC name on social/boss beats so scaffolded TALK/DEFEAT
        # objectives get a real target instead of the fallback placeholder.
        if etype in ("social", "boss"):
            npc_names = [f"PNJ_{i}"]
        elif i % 2 == 0:
            npc_names = [f"PNJ_{i}"]
        else:
            npc_names = []
        beats.append({
            "beat_number": i,
            "title": f"Acte {i}",
            "description": f"Description du beat {i}. Une aventure se dessine. Les heros avancent.",
            "location_hint": f"Lieu {i}",
            "npc_names": npc_names,
            "encounter_type": etype,
            "is_twist": i == 7,
        })
    return {
        "campaign_id": "",
        "theme": "dark fantasy",
        "premise": "Un ancien mal se reveille dans les profondeurs de la terre, menacant le royaume.",
        "situation": "Les villages frontaliers se vident la nuit, des murmures montent des failles.",
        "call_to_action": "Le Conseil vous paie pour trouver la source des disparitions et rapporter des preuves.",
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


def test_generate_parses_situation_and_call_to_action(
    httpx_mock: HTTPXMock, generator: ArcGenerator
) -> None:
    """New opening-chain fields are parsed from the LLM output."""
    arc_data = _make_arc_data(10)
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(arc_data))

    result = generator.generate(theme="dark fantasy", player_count=4)

    assert "frontaliers se vident" in result.situation
    assert "Le Conseil vous paie" in result.call_to_action


def test_generate_legacy_arc_without_opening_fields(
    httpx_mock: HTTPXMock, generator: ArcGenerator
) -> None:
    """LLM output missing situation/call_to_action yields empty-string defaults."""
    arc_data = _make_arc_data(10)
    arc_data.pop("situation", None)
    arc_data.pop("call_to_action", None)
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(arc_data))

    result = generator.generate(theme="dark fantasy", player_count=2)

    assert result.situation == ""
    assert result.call_to_action == ""


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

    def test_null_string_damage_type_on_effect_coerced_to_none(self) -> None:
        """LLM emits 'null' (string) for optional damage_type → coerce to None."""
        data: dict = {
            "beats": [],
            "villain_stat_block": {
                "attacks": [],
                "signature_abilities": [
                    {"effects": [{"damage_type": "null", "kind": "condition"}]}
                ],
                "legendary_actions": [],
            },
        }
        ArcGenerator._sanitize_arc_data(data)
        effect = data["villain_stat_block"]["signature_abilities"][0]["effects"][0]
        assert effect["damage_type"] is None

    def test_null_string_save_ability_on_effect_coerced_to_none(self) -> None:
        """LLM emits 'null' (string) for optional save_ability → coerce to None."""
        data: dict = {
            "beats": [],
            "villain_stat_block": {
                "attacks": [],
                "signature_abilities": [],
                "legendary_actions": [
                    {"effects": [{"save_ability": "null", "kind": "damage"}]}
                ],
            },
        }
        ArcGenerator._sanitize_arc_data(data)
        effect = data["villain_stat_block"]["legendary_actions"][0]["effects"][0]
        assert effect["save_ability"] is None

    def test_null_string_variants_all_coerced(self) -> None:
        """Variants 'None', 'NULL', empty string all coerce to None."""
        data: dict = {
            "beats": [],
            "villain_stat_block": {
                "attacks": [],
                "signature_abilities": [
                    {"effects": [
                        {"damage_type": "None", "save_ability": "NULL",
                         "save_dc": "", "kind": "damage"},
                    ]},
                ],
                "legendary_actions": [],
            },
        }
        ArcGenerator._sanitize_arc_data(data)
        effect = data["villain_stat_block"]["signature_abilities"][0]["effects"][0]
        assert effect["damage_type"] is None
        assert effect["save_ability"] is None
        assert effect["save_dc"] is None

    def test_valid_damage_type_preserved_when_mixed_with_null(self) -> None:
        """A valid damage_type in one effect is not clobbered when another has 'null'."""
        data: dict = {
            "beats": [],
            "villain_stat_block": {
                "attacks": [],
                "signature_abilities": [
                    {"effects": [
                        {"damage_type": "Fire", "kind": "damage"},
                        {"damage_type": "null", "kind": "condition"},
                    ]},
                ],
                "legendary_actions": [],
            },
        }
        ArcGenerator._sanitize_arc_data(data)
        effects = data["villain_stat_block"]["signature_abilities"][0]["effects"]
        assert effects[0]["damage_type"] == "Fire"
        assert effects[1]["damage_type"] is None

    def test_null_string_range_value_on_attack_coerced(self) -> None:
        """Attack-level 'null' range_value coerced to None (field is optional)."""
        data: dict = {
            "beats": [],
            "villain_stat_block": {
                "attacks": [{"name": "Slash", "damage_dice": "1d8",
                             "damage_type": "Slashing", "range_value": "null"}],
                "signature_abilities": [],
                "legendary_actions": [],
            },
        }
        ArcGenerator._sanitize_arc_data(data)
        assert data["villain_stat_block"]["attacks"][0]["range_value"] is None
        # damage_type is REQUIRED on NPCAttack — must NOT be touched
        assert data["villain_stat_block"]["attacks"][0]["damage_type"] == "Slashing"


# ---------------------------------------------------------------------------
# SemanticIndexer integration
# ---------------------------------------------------------------------------


class TestArcGeneratorIndexing:
    """Verify SemanticIndexer is called when provided to ArcGenerator."""

    def _arc_data_with_stat_block(self, beat_count: int = 10) -> dict:
        """Minimal valid arc JSON with a valid villain stat block (min 8 beats)."""
        data = _make_arc_data(beat_count)
        data["villain_stat_block"] = _make_valid_villain_stat_block()
        return data

    def test_arc_generator_invokes_indexer_when_provided(
        self,
        httpx_mock: HTTPXMock,
        ollama_client: OllamaClient,
    ) -> None:
        """When an indexer is supplied, beats + villain NPC + theme lore are indexed."""
        from unittest.mock import MagicMock
        from memory.indexer import SemanticIndexer

        indexer: SemanticIndexer = MagicMock(spec=SemanticIndexer)

        arc_data = self._arc_data_with_stat_block(beat_count=10)
        httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(arc_data))

        gen = ArcGenerator(ollama_client, indexer=indexer)
        arc = gen.generate(
            theme="dark fantasy",
            player_count=4,
            campaign_id="cmp_test",
        )

        # All beats must be indexed.
        assert indexer.index_beat.call_count == 10
        # The villain must be indexed as an NPC.
        villain_names = [call.args[1] for call in indexer.index_npc.call_args_list]
        assert arc.villain_name in villain_names
        # The arc theme must be indexed as lore.
        assert indexer.index_lore.called

    def test_arc_generator_no_indexer_no_indexing(
        self,
        httpx_mock: HTTPXMock,
        ollama_client: OllamaClient,
    ) -> None:
        """When no indexer is provided (default None), generation works without errors."""
        arc_data = self._arc_data_with_stat_block(beat_count=10)
        httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(arc_data))

        gen = ArcGenerator(ollama_client)  # no indexer kwarg
        result = gen.generate(theme="dark fantasy", player_count=4)

        assert isinstance(result, StoryArc)
        assert len(result.beats) == 10

    def test_arc_generator_indexing_with_campaign_id(
        self,
        httpx_mock: HTTPXMock,
        ollama_client: OllamaClient,
    ) -> None:
        """campaign_id is forwarded to index_beat calls."""
        from unittest.mock import MagicMock
        from memory.indexer import SemanticIndexer

        indexer: SemanticIndexer = MagicMock(spec=SemanticIndexer)

        arc_data = self._arc_data_with_stat_block(beat_count=10)
        httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(arc_data))

        gen = ArcGenerator(ollama_client, indexer=indexer)
        gen.generate(theme="dark fantasy", player_count=2, campaign_id="my_campaign")

        # The campaign_id must be the first positional arg to index_beat.
        call_campaign_id = indexer.index_beat.call_args_list[0].args[0]
        assert call_campaign_id == "my_campaign"


# ---------------------------------------------------------------------------
# Native objectives sanitization + scaffolding (refactor 2026-04-27)
# ---------------------------------------------------------------------------


class TestNativeObjectivesSanitization:
    """Pure-Python tests for ``_sanitize_beat_objectives``.

    These verify the deterministic guarantees the sanitizer makes — every beat
    leaves the LLM with rich, calibrated objectives, regardless of LLM quality.
    """

    def _social_beat(self, **overrides) -> dict:
        """Helper: minimal social/negotiation beat dict."""
        beat = {
            "beat_number": 1,
            "title": "Audience royale",
            "description": "Le groupe rencontre la régente.",
            "location_hint": "Salle du trône",
            "npc_names": ["Régente Elinor"],
            "encounter_type": "social",
            "encounter_subtype": "negotiation",
            "is_twist": False,
        }
        beat.update(overrides)
        return beat

    def test_social_beat_without_objectives_gets_scaffolded(self) -> None:
        """No objectives → recipe-based scaffold with MIN_REVEALS gate."""
        beat = self._social_beat()
        ArcGenerator._sanitize_beat_objectives(beat, villain_name="Vellus")

        assert beat["objectives"]
        primary = beat["objectives"][0]
        assert primary["kind"] == "talk"
        assert primary["target"] == "Régente Elinor"
        assert primary["gate"] == {"kind": "min_reveals", "value": 2}
        assert beat["advance_rule"] == "all_required"
        assert beat["judge_rubric"]
        assert beat["player_visible_hint"]

    def test_combat_beat_scaffolds_defeat_objective(self) -> None:
        beat = {
            "beat_number": 3,
            "title": "Embuscade",
            "description": "...",
            "location_hint": "Forêt",
            "npc_names": ["Capitaine brigand"],
            "encounter_type": "combat",
            "encounter_subtype": "ambush",
            "is_twist": False,
        }
        ArcGenerator._sanitize_beat_objectives(beat, villain_name="Vellus")
        assert beat["objectives"][0]["kind"] == "defeat"
        assert beat["objectives"][0]["target"] == "Capitaine brigand"
        # No gate on defeat — it's binary.
        assert beat["objectives"][0].get("gate") is None

    def test_boss_beat_always_targets_villain(self) -> None:
        beat = {
            "beat_number": 10,
            "title": "Confrontation",
            "description": "...",
            "location_hint": "Sanctum",
            "npc_names": [],
            "encounter_type": "boss",
            "encounter_subtype": "boss",
            "is_twist": False,
        }
        ArcGenerator._sanitize_beat_objectives(beat, villain_name="Vellus l'Ombre")
        defeat_obj = next(o for o in beat["objectives"] if o["kind"] == "defeat")
        assert defeat_obj["target"] == "Vellus l'Ombre"

    def test_boss_beat_inserts_villain_defeat_when_llm_omits_it(self) -> None:
        """LLM emitted objectives but missed the defeat villain — engine inserts it."""
        beat = {
            "beat_number": 10,
            "title": "Confrontation finale",
            "description": "...",
            "location_hint": "Sanctum",
            "npc_names": ["Garde du sceau"],
            "encounter_type": "boss",
            "encounter_subtype": "boss",
            "is_twist": False,
            "objectives": [
                {
                    "id": "interrupt_ritual",
                    "kind": "interact",
                    "target": "autel ardent",
                    "description": "Briser le rituel avant la fusion.",
                    "required": True,
                    "fuzzy_threshold": 0.7,
                    "gate": None,
                },
            ],
        }
        ArcGenerator._sanitize_beat_objectives(beat, villain_name="Vellus l'Ombre")
        kinds_targets = [(o["kind"], o["target"]) for o in beat["objectives"]]
        assert ("defeat", "Vellus l'Ombre") in kinds_targets
        # Original objective preserved.
        assert ("interact", "autel ardent") in kinds_targets

    def test_boss_beat_keeps_llm_defeat_when_villain_target_matches(self) -> None:
        """LLM's defeat objective targeting the villain — keep it, don't duplicate."""
        beat = {
            "beat_number": 10,
            "title": "Confrontation",
            "description": "...",
            "location_hint": "Sanctum",
            "npc_names": [],
            "encounter_type": "boss",
            "encounter_subtype": "boss",
            "is_twist": False,
            "objectives": [
                {
                    "id": "kill_villain",
                    "kind": "defeat",
                    "target": "Vellus l'Ombre, Maître du Pacte",
                    "description": "Faire tomber le villain.",
                    "required": True,
                    "fuzzy_threshold": 0.7,
                    "gate": None,
                },
            ],
        }
        ArcGenerator._sanitize_beat_objectives(beat, villain_name="Vellus l'Ombre")
        defeat_objs = [o for o in beat["objectives"] if o["kind"] == "defeat"]
        assert len(defeat_objs) == 1, "should not duplicate the LLM's defeat objective"

    def test_invalid_objective_kind_dropped(self) -> None:
        """Objectives with unknown kinds are filtered — sanitizer scaffolds replacement."""
        beat = self._social_beat(objectives=[
            {
                "id": "bad",
                "kind": "tap_dance",  # not a real ObjectiveKind
                "target": "X",
                "description": "...",
                "required": True,
            },
        ])
        ArcGenerator._sanitize_beat_objectives(beat, villain_name="Vellus")
        # No valid objectives left → scaffold from recipe.
        assert all(o["kind"] in {"talk", "defeat", "arrive", "examine", "interact",
                                  "search", "pickup", "possess", "flag"}
                   for o in beat["objectives"])

    def test_objective_with_empty_target_dropped(self) -> None:
        beat = self._social_beat(objectives=[
            {
                "id": "x",
                "kind": "talk",
                "target": "   ",
                "description": "Talk to the regent.",
                "required": True,
            },
        ])
        ArcGenerator._sanitize_beat_objectives(beat, villain_name="Vellus")
        # Empty target → dropped → scaffold replaces.
        assert beat["objectives"][0]["target"] == "Régente Elinor"

    def test_duplicate_objective_ids_get_unique_ids(self) -> None:
        """Two objectives with the same id → second one gets a fresh id."""
        beat = self._social_beat(objectives=[
            {
                "id": "talk1",
                "kind": "talk",
                "target": "Régente Elinor",
                "description": "Greet the regent.",
                "required": True,
            },
            {
                "id": "talk1",  # duplicate
                "kind": "talk",
                "target": "Régente Elinor",
                "description": "Negotiate the terms.",
                "required": False,
            },
        ])
        ArcGenerator._sanitize_beat_objectives(beat, villain_name="Vellus")
        ids = [o["id"] for o in beat["objectives"]]
        assert len(set(ids)) == len(ids)

    def test_min_reveals_string_value_coerced_to_int(self) -> None:
        """LLM emits "2" instead of 2 for MIN_REVEALS → coerce."""
        beat = self._social_beat(objectives=[
            {
                "id": "talk_regent",
                "kind": "talk",
                "target": "Régente Elinor",
                "description": "...",
                "required": True,
                "gate": {"kind": "min_reveals", "value": "2"},
            },
        ])
        ArcGenerator._sanitize_beat_objectives(beat, villain_name="Vellus")
        gate = beat["objectives"][0]["gate"]
        assert gate == {"kind": "min_reveals", "value": 2}

    def test_min_reveals_garbage_value_drops_gate(self) -> None:
        """Non-coercible value on MIN_REVEALS → drop gate, keep objective."""
        beat = self._social_beat(objectives=[
            {
                "id": "talk_regent",
                "kind": "talk",
                "target": "Régente Elinor",
                "description": "...",
                "required": True,
                "gate": {"kind": "min_reveals", "value": "lots"},
            },
        ])
        ArcGenerator._sanitize_beat_objectives(beat, villain_name="Vellus")
        primary = beat["objectives"][0]
        assert primary["target"] == "Régente Elinor"
        assert primary.get("gate") is None

    def test_has_item_gate_with_non_string_value_dropped(self) -> None:
        beat = self._social_beat(objectives=[
            {
                "id": "ritual",
                "kind": "interact",
                "target": "autel",
                "description": "Effectuer le rituel.",
                "required": True,
                "gate": {"kind": "has_item", "value": 42},  # int — invalid
            },
        ])
        ArcGenerator._sanitize_beat_objectives(beat, villain_name="Vellus")
        primary = beat["objectives"][0]
        assert primary.get("gate") is None

    def test_has_item_gate_with_string_value_kept(self) -> None:
        beat = self._social_beat(objectives=[
            {
                "id": "ritual",
                "kind": "interact",
                "target": "autel",
                "description": "Effectuer le rituel.",
                "required": True,
                "gate": {"kind": "has_item", "value": "calice ardent"},
            },
        ])
        ArcGenerator._sanitize_beat_objectives(beat, villain_name="Vellus")
        primary = beat["objectives"][0]
        assert primary["gate"] == {"kind": "has_item", "value": "calice ardent"}

    def test_invalid_gate_kind_dropped_objective_kept(self) -> None:
        beat = self._social_beat(objectives=[
            {
                "id": "talk_regent",
                "kind": "talk",
                "target": "Régente Elinor",
                "description": "...",
                "required": True,
                "gate": {"kind": "min_charisma", "value": 12},
            },
        ])
        ArcGenerator._sanitize_beat_objectives(beat, villain_name="Vellus")
        primary = beat["objectives"][0]
        assert primary["target"] == "Régente Elinor"
        assert primary.get("gate") is None

    def test_invalid_advance_rule_falls_back_to_all_required(self) -> None:
        beat = self._social_beat(
            advance_rule="majority_vote",
            objectives=[
                {
                    "id": "talk_regent",
                    "kind": "talk",
                    "target": "Régente Elinor",
                    "description": "Speak with the regent.",
                    "required": True,
                },
            ],
        )
        ArcGenerator._sanitize_beat_objectives(beat, villain_name="Vellus")
        assert beat["advance_rule"] == "all_required"

    def test_m_of_n_without_threshold_gets_default(self) -> None:
        beat = self._social_beat(
            advance_rule="m_of_n",
            objectives=[
                {
                    "id": "objA", "kind": "talk", "target": "Régente Elinor",
                    "description": "...", "required": True,
                },
                {
                    "id": "objB", "kind": "talk", "target": "Régente Elinor",
                    "description": "...", "required": True,
                },
                {
                    "id": "objC", "kind": "talk", "target": "Régente Elinor",
                    "description": "...", "required": False,
                },
            ],
        )
        ArcGenerator._sanitize_beat_objectives(beat, villain_name="Vellus")
        assert beat["advance_rule"] == "m_of_n"
        # Default = ceil(N/2) → 3 objectives → threshold 2.
        assert beat["advance_threshold"] == 2

    def test_required_field_string_true_coerced_to_bool(self) -> None:
        beat = self._social_beat(objectives=[
            {
                "id": "talk_regent",
                "kind": "talk",
                "target": "Régente Elinor",
                "description": "...",
                "required": "true",
            },
        ])
        ArcGenerator._sanitize_beat_objectives(beat, villain_name="Vellus")
        assert beat["objectives"][0]["required"] is True

    def test_required_field_string_false_coerced_to_bool(self) -> None:
        beat = self._social_beat(objectives=[
            {
                "id": "talk_regent",
                "kind": "talk",
                "target": "Régente Elinor",
                "description": "...",
                "required": "false",
            },
        ])
        ArcGenerator._sanitize_beat_objectives(beat, villain_name="Vellus")
        assert beat["objectives"][0]["required"] is False

    def test_missing_id_gets_generated(self) -> None:
        beat = self._social_beat(objectives=[
            {
                "kind": "talk",
                "target": "Régente Elinor",
                "description": "Speak with the regent.",
                "required": True,
            },
        ])
        ArcGenerator._sanitize_beat_objectives(beat, villain_name="Vellus")
        oid = beat["objectives"][0]["id"]
        assert oid.startswith("b1_talk_")

    def test_judge_rubric_backfilled_from_recipe_when_empty(self) -> None:
        beat = self._social_beat(objectives=[
            {
                "id": "talk_regent",
                "kind": "talk",
                "target": "Régente Elinor",
                "description": "Speak with the regent.",
                "required": True,
            },
        ])
        ArcGenerator._sanitize_beat_objectives(beat, villain_name="Vellus")
        assert beat["judge_rubric"]  # filled

    def test_existing_judge_rubric_preserved(self) -> None:
        beat = self._social_beat(
            judge_rubric="Custom rubric — keep me!",
            objectives=[
                {
                    "id": "talk_regent",
                    "kind": "talk",
                    "target": "Régente Elinor",
                    "description": "Speak with the regent.",
                    "required": True,
                },
            ],
        )
        ArcGenerator._sanitize_beat_objectives(beat, villain_name="Vellus")
        assert beat["judge_rubric"] == "Custom rubric — keep me!"


class TestEndToEndNativeObjectives:
    """End-to-end: ArcGenerator.generate() with native objectives in the LLM payload."""

    def _make_arc_with_native_objectives(self) -> dict:
        """Arc payload where the LLM emits proper objectives natively."""
        data = _make_arc_data(beat_count=10)
        # First social beat — full native objectives shape.
        data["beats"][0]["encounter_subtype"] = "negotiation"
        data["beats"][0]["objectives"] = [
            {
                "id": "talk_pnj_1",
                "kind": "talk",
                "target": "PNJ_1",
                "description": "Négocier avec PNJ_1.",
                "required": True,
                "fuzzy_threshold": 0.7,
                "gate": {"kind": "min_reveals", "value": 2},
            },
        ]
        data["beats"][0]["advance_rule"] = "all_required"
        data["beats"][0]["judge_rubric"] = "Avancer si la négociation est substantielle."
        data["beats"][0]["player_visible_hint"] = "Creusez la négociation."
        return data

    def test_native_objectives_preserved_through_generate(
        self, httpx_mock: HTTPXMock, generator: ArcGenerator,
    ) -> None:
        arc_data = self._make_arc_with_native_objectives()
        httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(arc_data))

        arc = generator.generate(theme="dark fantasy", player_count=4)

        first = arc.beats[0]
        assert first.objectives, "first beat should have objectives"
        primary = first.objectives[0]
        assert primary.kind.value == "talk"
        assert primary.target == "PNJ_1"
        assert primary.gate is not None
        assert primary.gate.kind.value == "min_reveals"
        assert primary.gate.value == 2
        assert first.judge_rubric == "Avancer si la négociation est substantielle."
        assert first.player_visible_hint == "Creusez la négociation."

    def test_every_beat_has_native_objectives_after_generate(
        self, httpx_mock: HTTPXMock, generator: ArcGenerator,
    ) -> None:
        """Even when the LLM omits objectives entirely, the sanitizer scaffolds
        a valid recipe-based objective list per beat."""
        arc_data = _make_arc_data(beat_count=10)  # no native objectives
        httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(arc_data))

        arc = generator.generate(theme="dark fantasy", player_count=4)

        for beat in arc.beats:
            assert beat.objectives, f"beat {beat.beat_number} has empty objectives"
            for obj in beat.objectives:
                assert obj.kind.value in {
                    "talk", "defeat", "arrive", "examine", "interact",
                    "search", "pickup", "possess", "flag",
                }

    def test_boss_beat_always_has_defeat_villain_after_generate(
        self, httpx_mock: HTTPXMock, generator: ArcGenerator,
    ) -> None:
        arc_data = _make_arc_data(beat_count=10)
        # Force the LLM to omit objectives on the boss beat.
        arc_data["beats"][-1].pop("objectives", None)
        httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(arc_data))

        arc = generator.generate(theme="dark fantasy", player_count=4)

        boss = arc.beats[-1]
        defeat_objs = [o for o in boss.objectives if o.kind.value == "defeat"]
        assert defeat_objs, "boss beat must have a defeat objective"
        # Target may be the villain name OR the recipe scaffold.
        assert any(arc.villain_name in o.target for o in defeat_objs)

    def test_social_negotiation_gets_min_reveals_gate_when_omitted(
        self, httpx_mock: HTTPXMock, generator: ArcGenerator,
    ) -> None:
        """A social/negotiation beat without objectives gets MIN_REVEALS gate."""
        arc_data = _make_arc_data(beat_count=10)
        # Pick a social beat (i=4 is social, since i % 4 == 0 → "social" in our cycle).
        # Actually let's force beat 1 to be social/negotiation explicitly.
        arc_data["beats"][0]["encounter_type"] = "social"
        arc_data["beats"][0]["encounter_subtype"] = "negotiation"
        arc_data["beats"][0]["npc_names"] = ["L'Émissaire"]
        arc_data["beats"][0].pop("objectives", None)

        httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(arc_data))
        arc = generator.generate(theme="dark fantasy", player_count=4)

        first = arc.beats[0]
        gates = [o.gate for o in first.objectives if o.gate is not None]
        gate_kinds = {g.kind.value for g in gates}
        assert "min_reveals" in gate_kinds
        # MIN_DISPOSITION is the secondary objective for negotiation.
        assert "min_disposition" in gate_kinds

    def test_puzzle_ritual_gets_has_item_gate(
        self, httpx_mock: HTTPXMock, generator: ArcGenerator,
    ) -> None:
        arc_data = _make_arc_data(beat_count=10)
        # Force beat 3 (was puzzle anyway) to be puzzle/ritual.
        arc_data["beats"][2]["encounter_type"] = "puzzle"
        arc_data["beats"][2]["encounter_subtype"] = "ritual"
        arc_data["beats"][2].pop("objectives", None)

        httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(arc_data))
        arc = generator.generate(theme="dark fantasy", player_count=4)

        target_beat = arc.beats[2]
        gates = [o.gate for o in target_beat.objectives if o.gate is not None]
        gate_kinds = {g.kind.value for g in gates}
        assert "has_item" in gate_kinds

    def test_exploration_discovery_gets_m_of_n(
        self, httpx_mock: HTTPXMock, generator: ArcGenerator,
    ) -> None:
        arc_data = _make_arc_data(beat_count=10)
        arc_data["beats"][1]["encounter_type"] = "exploration"
        arc_data["beats"][1]["encounter_subtype"] = "discovery"
        arc_data["beats"][1].pop("objectives", None)

        httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(arc_data))
        arc = generator.generate(theme="dark fantasy", player_count=4)

        target_beat = arc.beats[1]
        assert target_beat.advance_rule.value == "m_of_n"
        assert target_beat.advance_threshold == 2


class TestNoLegacyMigrationNeeded:
    """REGRESSION (the whole point of this refactor): newly generated arcs
    should never need the legacy CompletionTrigger migration to function.

    The migration in ``StoryArc._migrate_legacy_completion_triggers`` only
    fires when ``objectives`` is empty AND ``completion_trigger`` is set.
    After this refactor, fresh arcs always have non-empty ``objectives``.
    """

    def test_freshly_generated_arc_has_no_completion_trigger_dependency(
        self, httpx_mock: HTTPXMock, generator: ArcGenerator,
    ) -> None:
        arc_data = _make_arc_data(beat_count=10)
        # Explicitly do NOT include completion_trigger anywhere.
        for beat in arc_data["beats"]:
            beat.pop("completion_trigger", None)

        httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(arc_data))
        arc = generator.generate(theme="dark fantasy", player_count=4)

        for beat in arc.beats:
            # The beat is functionally complete WITHOUT any completion_trigger.
            assert beat.completion_trigger is None
            assert beat.objectives, (
                f"beat {beat.beat_number} has empty objectives — would need "
                f"legacy migration"
            )
