"""Tests for engine.skill_check — narrative-action → Skill mapper.

Pure deterministic mapper. No LLM, no randomness. Picks the most likely
D&D 5e skill for a free-form action description (FR + EN).
"""

import pytest

from engine.character import (
    AbilityScores,
    CharacterClass,
    Race,
    Skill,
    create_character,
)
from engine.skill_check import (
    DEFAULT_SKILL_DC,
    EASY_DC,
    HARD_DC,
    MODERATE_DC,
    VERY_EASY_DC,
    VERY_HARD_DC,
    compute_contest_dc,
    compute_skill_check_dc,
    infer_difficulty_bias,
    infer_skill_from_text,
)
from world.npc import NPC, NPCDisposition


class TestSleightOfHand:
    @pytest.mark.parametrize(
        "text",
        [
            "Je vole le marchand",
            "je dérobe la bourse du marchand",
            "Je pickpocket le garde",
            "j'essaie de subtiliser la clé",
            "I steal the merchant's purse",
            "I pickpocket the guard",
        ],
    )
    def test_steal_maps_to_sleight_of_hand(self, text: str) -> None:
        assert infer_skill_from_text(text) == Skill.SLEIGHT_OF_HAND


class TestAthletics:
    @pytest.mark.parametrize(
        "text",
        [
            "Je saute par-dessus la crevasse",
            "Je grimpe la falaise",
            "je nage dans le fleuve",
            "I jump over the chasm",
            "I climb the cliff",
            "I swim across the river",
        ],
    )
    def test_jump_climb_swim_map_to_athletics(self, text: str) -> None:
        assert infer_skill_from_text(text) == Skill.ATHLETICS


class TestAcrobatics:
    @pytest.mark.parametrize(
        "text",
        [
            "Je fais un saut acrobatique sur la table",
            "je garde mon équilibre sur la corde",
            "I do a backflip off the wall",
            "I balance on the tightrope",
        ],
    )
    def test_acrobatic_balance_maps_to_acrobatics(self, text: str) -> None:
        assert infer_skill_from_text(text) == Skill.ACROBATICS


class TestPersuasion:
    @pytest.mark.parametrize(
        "text",
        [
            "Je convaincs le garde de me laisser passer",
            "Je persuade le marchand de baisser le prix",
            "I convince the guard",
            "I persuade the merchant",
        ],
    )
    def test_persuade_maps_to_persuasion(self, text: str) -> None:
        assert infer_skill_from_text(text) == Skill.PERSUASION


class TestIntimidation:
    @pytest.mark.parametrize(
        "text",
        [
            "Je menace le garde avec mon regard",
            "j'intimide le bandit",
            "I threaten the bandit",
            "I intimidate the prisoner",
        ],
    )
    def test_threaten_maps_to_intimidation(self, text: str) -> None:
        assert infer_skill_from_text(text) == Skill.INTIMIDATION


class TestDeception:
    @pytest.mark.parametrize(
        "text",
        [
            "Je mens au garde sur ma destination",
            "je bluffe pour passer",
            "Je trompe le marchand",
            "I lie to the guard",
            "I bluff my way through",
        ],
    )
    def test_lie_bluff_maps_to_deception(self, text: str) -> None:
        assert infer_skill_from_text(text) == Skill.DECEPTION


class TestStealth:
    @pytest.mark.parametrize(
        "text",
        [
            "Je me cache derrière le tonneau",
            "je me faufile dans l'ombre",
            "Je m'approche furtivement",
            "I hide behind the barrel",
            "I sneak past the guard",
        ],
    )
    def test_hide_sneak_maps_to_stealth(self, text: str) -> None:
        assert infer_skill_from_text(text) == Skill.STEALTH


class TestPerception:
    @pytest.mark.parametrize(
        "text",
        [
            "Je tends l'oreille pour écouter",
            "j'observe attentivement la pièce",
            "I listen for footsteps",
            "I scan the area",
        ],
    )
    def test_listen_observe_maps_to_perception(self, text: str) -> None:
        assert infer_skill_from_text(text) == Skill.PERCEPTION


class TestInvestigation:
    @pytest.mark.parametrize(
        "text",
        [
            "J'examine la serrure de près",
            "j'inspecte le coffre à la recherche d'un piège",
            "I examine the lock carefully",
            "I investigate the crime scene",
        ],
    )
    def test_examine_maps_to_investigation(self, text: str) -> None:
        assert infer_skill_from_text(text) == Skill.INVESTIGATION


class TestInsight:
    @pytest.mark.parametrize(
        "text",
        [
            "Je discerne les intentions du marchand",
            "j'essaie de lire entre les lignes",
            "I sense the merchant's true motive",
        ],
    )
    def test_discern_intentions_maps_to_insight(self, text: str) -> None:
        assert infer_skill_from_text(text) == Skill.INSIGHT


class TestPerformance:
    @pytest.mark.parametrize(
        "text",
        [
            "Je joue de la flûte pour la foule",
            "Je chante une ballade",
            "I perform a song for tips",
        ],
    )
    def test_perform_maps_to_performance(self, text: str) -> None:
        assert infer_skill_from_text(text) == Skill.PERFORMANCE


class TestMedicine:
    @pytest.mark.parametrize(
        "text",
        [
            "Je soigne les blessures du blessé",
            "je panse la plaie du soldat",
            "I tend to the wounded man",
        ],
    )
    def test_heal_maps_to_medicine(self, text: str) -> None:
        assert infer_skill_from_text(text) == Skill.MEDICINE


class TestSurvival:
    @pytest.mark.parametrize(
        "text",
        [
            "Je piste les empreintes du loup",
            "je traque la bête",
            "I track the beast through the forest",
        ],
    )
    def test_track_maps_to_survival(self, text: str) -> None:
        assert infer_skill_from_text(text) == Skill.SURVIVAL


class TestAnimalHandling:
    @pytest.mark.parametrize(
        "text",
        [
            "Je calme le cheval effrayé",
            "j'apaise le chien grognant",
            "I calm the spooked horse",
        ],
    )
    def test_calm_animal_maps_to_animal_handling(self, text: str) -> None:
        assert infer_skill_from_text(text) == Skill.ANIMAL_HANDLING


class TestArcanaHistoryReligionNature:
    def test_arcana_lore_keyword(self) -> None:
        assert (
            infer_skill_from_text("J'analyse le sort runique")
            == Skill.ARCANA
        )
        assert infer_skill_from_text("I recall arcane lore") == Skill.ARCANA

    def test_history_keyword(self) -> None:
        assert (
            infer_skill_from_text("Je me souviens de l'histoire de cette ruine")
            == Skill.HISTORY
        )
        assert (
            infer_skill_from_text("I recall the history of this kingdom")
            == Skill.HISTORY
        )

    def test_religion_keyword(self) -> None:
        assert (
            infer_skill_from_text("Je reconnais le symbole religieux")
            == Skill.RELIGION
        )

    def test_nature_keyword(self) -> None:
        assert (
            infer_skill_from_text("J'identifie cette plante en forêt")
            == Skill.NATURE
        )


class TestNoMatch:
    @pytest.mark.parametrize(
        "text",
        [
            "Je m'assois sur la chaise",
            "Je mange un morceau de pain",
            "I sit on the chair",
            "I take a deep breath",
            "",
            "   ",
        ],
    )
    def test_trivial_action_returns_none(self, text: str) -> None:
        assert infer_skill_from_text(text) is None

    def test_completely_unrelated_returns_none(self) -> None:
        assert infer_skill_from_text("blablabla zzzzz") is None


class TestPriorityRules:
    """When multiple keywords compete, the more specific one wins."""

    def test_acrobatic_jump_beats_athletic_jump(self) -> None:
        # "saut acrobatique" should pick ACROBATICS (the qualifier wins)
        assert (
            infer_skill_from_text("Je fais un saut acrobatique")
            == Skill.ACROBATICS
        )

    def test_plain_jump_picks_athletics(self) -> None:
        # bare "saute" defaults to ATHLETICS (jump distance)
        assert (
            infer_skill_from_text("Je saute par-dessus la barrière")
            == Skill.ATHLETICS
        )

    def test_noun_form_saut_picks_athletics(self) -> None:
        # The user's canonical example: "je tente un saut risqué".
        assert (
            infer_skill_from_text("Je tente un saut risqué par-dessus la crevasse")
            == Skill.ATHLETICS
        )

    def test_steal_with_stealth_modifier_still_picks_sleight_of_hand(self) -> None:
        # "vole + marchand" compound beats co-occurring "discrètement".
        assert (
            infer_skill_from_text(
                "Je vole la bourse du marchand discrètement",
            )
            == Skill.SLEIGHT_OF_HAND
        )


class TestDCConstants:
    def test_dc_constants_match_srd(self) -> None:
        assert VERY_EASY_DC == 5
        assert EASY_DC == 10
        assert MODERATE_DC == 12
        assert HARD_DC == 15
        assert VERY_HARD_DC == 20
        # The default DC the IMPROVISE branch will use when no scene cue exists
        assert DEFAULT_SKILL_DC == 12


# ---------------------------------------------------------------------------
# Difficulty bias from narrative cues
# ---------------------------------------------------------------------------


class TestInferDifficultyBias:
    """``infer_difficulty_bias`` reads narrative qualifiers that suggest
    the action is unusually easy or hard."""

    def test_no_qualifier_returns_zero(self) -> None:
        assert infer_difficulty_bias("Je saute par-dessus la barrière") == 0

    @pytest.mark.parametrize(
        "text",
        [
            "Je tente un petit saut par-dessus le ruisseau",
            "Un saut facile",
            "J'essaie un truc simple",
            "I take an easy leap",
            "I make a small jump",
        ],
    )
    def test_easy_qualifiers_lower_dc(self, text: str) -> None:
        assert infer_difficulty_bias(text) < 0

    @pytest.mark.parametrize(
        "text",
        [
            "Je tente un saut risqué par-dessus la crevasse",
            "Une escalade dangereuse",
            "Un saut périlleux",
            "Une persuasion difficile",
            "I attempt a risky jump",
            "A hard climb",
            "A difficult persuasion",
        ],
    )
    def test_hard_qualifiers_raise_dc(self, text: str) -> None:
        bias = infer_difficulty_bias(text)
        assert bias > 0
        # Hard, but not impossible.
        assert bias < 8

    @pytest.mark.parametrize(
        "text",
        [
            "Une tentative héroïque presque impossible",
            "Un saut désespéré",
            "A near impossible feat",
            "A heroic leap",
        ],
    )
    def test_very_hard_qualifiers_raise_dc_more(self, text: str) -> None:
        bias = infer_difficulty_bias(text)
        # Should clearly exceed a "merely hard" action.
        assert bias >= 5


# ---------------------------------------------------------------------------
# Contested DC against an NPC
# ---------------------------------------------------------------------------


def _make_npc(
    *,
    name: str = "Marchand",
    wis: int = 10,
    cha: int = 10,
    disposition: NPCDisposition = NPCDisposition.NEUTRAL,
) -> NPC:
    scores = AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=wis, CHA=cha)
    return NPC(
        name=name,
        race=Race.HUMAN,
        char_class=CharacterClass.FIGHTER,
        ability_scores=scores,
        hp=10, max_hp=10, ac=10,
        disposition=disposition,
    )


class TestComputeContestDC:
    """``compute_contest_dc`` derives a contested DC from an NPC's stats."""

    def test_perception_skills_use_npc_wis(self) -> None:
        # WIS 14 → +2. Passive perception ≈ 10 + 2 = 12.
        npc = _make_npc(wis=14)
        for skill in (Skill.STEALTH, Skill.SLEIGHT_OF_HAND):
            assert compute_contest_dc(npc, skill) == 12

    def test_deception_uses_npc_wis_for_insight(self) -> None:
        # Insight = WIS-based. WIS 16 → +3. Passive insight ≈ 13.
        npc = _make_npc(wis=16)
        assert compute_contest_dc(npc, Skill.DECEPTION) == 13

    def test_persuasion_uses_npc_cha(self) -> None:
        # Social pressure resistance scales with NPC's CHA.
        # CHA 12 → +1. DC ≈ 10 + 1 = 11.
        npc = _make_npc(cha=12)
        assert compute_contest_dc(npc, Skill.PERSUASION) == 11

    def test_intimidation_uses_npc_cha(self) -> None:
        npc = _make_npc(cha=8)  # CHA 8 → -1
        assert compute_contest_dc(npc, Skill.INTIMIDATION) == 9

    def test_hostile_disposition_raises_persuasion_dc(self) -> None:
        # A hostile NPC is harder to persuade than a neutral one.
        neutral = _make_npc(cha=10, disposition=NPCDisposition.NEUTRAL)
        hostile = _make_npc(cha=10, disposition=NPCDisposition.HOSTILE)
        assert (
            compute_contest_dc(hostile, Skill.PERSUASION)
            > compute_contest_dc(neutral, Skill.PERSUASION)
        )

    def test_friendly_disposition_lowers_persuasion_dc(self) -> None:
        neutral = _make_npc(cha=10, disposition=NPCDisposition.NEUTRAL)
        friendly = _make_npc(cha=10, disposition=NPCDisposition.FRIENDLY)
        assert (
            compute_contest_dc(friendly, Skill.PERSUASION)
            < compute_contest_dc(neutral, Skill.PERSUASION)
        )

    def test_uncontested_skill_returns_none(self) -> None:
        # Athletics has no NPC-side defense — it's purely environmental.
        npc = _make_npc()
        assert compute_contest_dc(npc, Skill.ATHLETICS) is None
        assert compute_contest_dc(npc, Skill.ACROBATICS) is None
        assert compute_contest_dc(npc, Skill.MEDICINE) is None


# ---------------------------------------------------------------------------
# Top-level DC composition
# ---------------------------------------------------------------------------


class TestComputeSkillCheckDC:
    """``compute_skill_check_dc`` composes bias + (NPC contest or default)."""

    def test_default_dc_when_no_text_and_no_npc(self) -> None:
        # A bare action with no qualifiers and no target → MODERATE_DC.
        rogue_char = create_character(
            "Plain", Race.HUMAN, CharacterClass.ROGUE,
            AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10),
        )
        del rogue_char  # only used to keep imports tidy
        dc = compute_skill_check_dc(
            text="je tente l'action",
            skill=Skill.PERCEPTION,
            target_npc=None,
        )
        assert dc == MODERATE_DC

    def test_easy_qualifier_lowers_default(self) -> None:
        dc = compute_skill_check_dc(
            text="un petit saut facile",
            skill=Skill.ATHLETICS,
            target_npc=None,
        )
        assert dc < MODERATE_DC
        assert dc >= VERY_EASY_DC

    def test_hard_qualifier_raises_default(self) -> None:
        dc = compute_skill_check_dc(
            text="un saut très risqué",
            skill=Skill.ATHLETICS,
            target_npc=None,
        )
        assert dc > MODERATE_DC

    def test_npc_contest_used_when_skill_is_contestable(self) -> None:
        npc = _make_npc(wis=16)  # passive perception 13
        dc = compute_skill_check_dc(
            text="je vole la bourse du marchand",
            skill=Skill.SLEIGHT_OF_HAND,
            target_npc=npc,
        )
        # No qualifier → contested DC dominates: 13.
        assert dc == 13

    def test_npc_contest_combines_with_difficulty_bias(self) -> None:
        npc = _make_npc(wis=10)  # passive perception 10
        easy_dc = compute_skill_check_dc(
            text="je vole un petit objet sans difficulté",
            skill=Skill.SLEIGHT_OF_HAND,
            target_npc=npc,
        )
        hard_dc = compute_skill_check_dc(
            text="je tente un vol risqué et difficile",
            skill=Skill.SLEIGHT_OF_HAND,
            target_npc=npc,
        )
        assert easy_dc < 10 or easy_dc <= 10  # easy bias on top of base 10
        assert hard_dc > 10

    def test_dc_never_below_very_easy_floor(self) -> None:
        # Even a near-impossible-easy combo must not yield DC < 5.
        dc = compute_skill_check_dc(
            text="un saut absolument minuscule, vraiment facile et simple",
            skill=Skill.ATHLETICS,
            target_npc=None,
        )
        assert dc >= VERY_EASY_DC

    def test_uncontested_skill_ignores_npc(self) -> None:
        # Athletics shouldn't contest with the NPC.
        npc = _make_npc(wis=20)  # high WIS — irrelevant for athletics
        dc = compute_skill_check_dc(
            text="je saute par-dessus la crevasse",
            skill=Skill.ATHLETICS,
            target_npc=npc,
        )
        assert dc == MODERATE_DC
