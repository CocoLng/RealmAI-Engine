"""Tests for ai/entity_resolver.py — pure Python entity matching."""

from __future__ import annotations

import pytest

from ai.entity_resolver import EntityCandidate, EntityResolver, ResolutionResult
from ai.models import InterpretedAction
from engine.character import AbilityScores, CharacterClass, Race, create_character
from engine.combat import CombatSide, CombatState, Combatant
from engine.inventory import (
    DamageType,
    EquipmentSlot,
    Inventory,
    Item,
    ItemType,
    Weapon,
    WeaponCategory,
)
from engine.validators import ActionType
from world.location import Location
from world.npc import NPC


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def ability_scores() -> AbilityScores:
    return AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10)


@pytest.fixture()
def cathedral() -> Location:
    return Location(
        name="Place de la Cathédrale",
        description="Une vaste place pavée.",
        connections=["Intérieur de la cathédrale", "Ruelle nord"],
        npcs_present=["Père Aldric", "Frère Corin"],
        items_available=["Autel de pierre", "Statue de saint"],
    )


def _make_npc(
    name: str,
    location_name: str | None,
    scores: AbilityScores,
    description: str = "",
    aliases: list[str] | None = None,
) -> NPC:
    return NPC(
        name=name,
        race=Race.HUMAN,
        char_class=CharacterClass.CLERIC,
        ability_scores=scores,
        hp=10,
        max_hp=10,
        ac=10,
        description=description,
        location_name=location_name,
        aliases=aliases or [],
    )


@pytest.fixture()
def present_npcs(ability_scores: AbilityScores) -> dict[str, NPC]:
    """Three priests in the cathedral for ambiguity tests."""
    aldric = _make_npc(
        "Père Aldric",
        "Place de la Cathédrale",
        ability_scores,
        "Un vieil homme en prière près de l'autel.",
    )
    corin = _make_npc(
        "Frère Corin",
        "Place de la Cathédrale",
        ability_scores,
        "Un jeune novice qui range les cierges.",
    )
    return {aldric.name: aldric, corin.name: corin}


# ---------------------------------------------------------------------------
# ResolutionResult dataclass
# ---------------------------------------------------------------------------


class TestResolutionResult:
    def test_default_fields(self) -> None:
        res = ResolutionResult(status="not_applicable")
        assert res.status == "not_applicable"
        assert res.field_name is None
        assert res.resolved_entity is None
        assert res.candidates == []
        assert res.reason is None


# ---------------------------------------------------------------------------
# not_applicable — action types that need no resolution
# ---------------------------------------------------------------------------


class TestNotApplicable:
    def test_look_is_not_applicable(
        self, cathedral: Location, present_npcs: dict[str, NPC],
    ) -> None:
        action = InterpretedAction(
            action_type=ActionType.LOOK,
            actor_name="Arden",
            raw_input="je regarde",
        )
        res = EntityResolver.resolve(
            action, location=cathedral, npcs=present_npcs,
        )
        assert res.status == "not_applicable"

    def test_improvise_is_not_applicable(
        self, cathedral: Location, present_npcs: dict[str, NPC],
    ) -> None:
        action = InterpretedAction(
            action_type=ActionType.IMPROVISE,
            actor_name="Arden",
            raw_input="je saute sur la table",
        )
        res = EntityResolver.resolve(
            action, location=cathedral, npcs=present_npcs,
        )
        assert res.status == "not_applicable"


# ---------------------------------------------------------------------------
# TALK — strict NPC resolution
# ---------------------------------------------------------------------------


class TestResolveTalk:
    def test_exact_match_single_npc(
        self, cathedral: Location, present_npcs: dict[str, NPC],
    ) -> None:
        action = InterpretedAction(
            action_type=ActionType.TALK,
            actor_name="Arden",
            target_name="Père Aldric",
            raw_input="je parle à Père Aldric",
        )
        res = EntityResolver.resolve(
            action, location=cathedral, npcs=present_npcs,
        )
        assert res.status == "resolved"
        assert res.resolved_entity == "Père Aldric"
        assert res.field_name == "target_name"

    def test_partial_match_resolves(
        self, cathedral: Location, present_npcs: dict[str, NPC],
    ) -> None:
        action = InterpretedAction(
            action_type=ActionType.TALK,
            actor_name="Arden",
            target_name="Aldric",
            raw_input="je parle à Aldric",
        )
        res = EntityResolver.resolve(
            action, location=cathedral, npcs=present_npcs,
        )
        assert res.status == "resolved"
        assert res.resolved_entity == "Père Aldric"

    def test_accent_insensitive_match(
        self, cathedral: Location, present_npcs: dict[str, NPC],
    ) -> None:
        action = InterpretedAction(
            action_type=ActionType.TALK,
            actor_name="Arden",
            target_name="pere aldric",  # no accents, lowercase
            raw_input="je parle a pere aldric",
        )
        res = EntityResolver.resolve(
            action, location=cathedral, npcs=present_npcs,
        )
        assert res.status == "resolved"
        assert res.resolved_entity == "Père Aldric"

    def test_ambiguous_on_multiple_matches(
        self, cathedral: Location, ability_scores: AbilityScores,
    ) -> None:
        """Two NPCs named 'Frère Marc' in the same location."""
        marc1 = _make_npc(
            "Frère Marc",
            "Place de la Cathédrale",
            ability_scores,
            "Un moine vêtu de bure brune.",
        )
        marc2 = _make_npc(
            "Frère Marc le Sage",
            "Place de la Cathédrale",
            ability_scores,
            "Un vieux moine chenu.",
        )
        action = InterpretedAction(
            action_type=ActionType.TALK,
            actor_name="Arden",
            target_name="Marc",
            raw_input="je parle à Marc",
        )
        res = EntityResolver.resolve(
            action,
            location=cathedral,
            npcs={marc1.name: marc1, marc2.name: marc2},
        )
        assert res.status == "ambiguous"
        assert res.field_name == "target_name"
        assert res.raw_value == "Marc"
        assert len(res.candidates) == 2
        candidate_labels = {c.label for c in res.candidates}
        assert "Frère Marc" in candidate_labels
        assert "Frère Marc le Sage" in candidate_labels

    def test_unknown_when_no_match(
        self, cathedral: Location, present_npcs: dict[str, NPC],
    ) -> None:
        action = InterpretedAction(
            action_type=ActionType.TALK,
            actor_name="Arden",
            target_name="Dragon",
            raw_input="je parle au dragon",
        )
        res = EntityResolver.resolve(
            action, location=cathedral, npcs=present_npcs,
        )
        assert res.status == "unknown"
        assert res.raw_value == "Dragon"

    def test_unknown_when_target_none(
        self, cathedral: Location, present_npcs: dict[str, NPC],
    ) -> None:
        action = InterpretedAction(
            action_type=ActionType.TALK,
            actor_name="Arden",
            target_name=None,
            raw_input="je parle",
        )
        res = EntityResolver.resolve(
            action, location=cathedral, npcs=present_npcs,
        )
        assert res.status == "unknown"
        assert res.raw_value == ""

    def test_candidates_exclude_npcs_in_other_locations(
        self,
        cathedral: Location,
        ability_scores: AbilityScores,
    ) -> None:
        """An NPC in another location is invisible — even if its name matches
        exactly, the resolver does not consider it."""
        distant = _make_npc(
            "Père Marc",
            "Forge du village",
            ability_scores,
        )
        action = InterpretedAction(
            action_type=ActionType.TALK,
            actor_name="Arden",
            target_name="Père Marc",
            raw_input="je parle à Père Marc",
        )
        res = EntityResolver.resolve(
            action,
            location=cathedral,
            npcs={distant.name: distant},
        )
        assert res.status == "unknown"
        assert res.raw_value == "Père Marc"


# ---------------------------------------------------------------------------
# MOVE — strict exit resolution
# ---------------------------------------------------------------------------


class TestResolveMove:
    def test_exact_exit_match(
        self, cathedral: Location, present_npcs: dict[str, NPC],
    ) -> None:
        action = InterpretedAction(
            action_type=ActionType.MOVE,
            actor_name="Arden",
            target_name="Intérieur de la cathédrale",
            raw_input="j'entre dans la cathédrale",
        )
        res = EntityResolver.resolve(
            action, location=cathedral, npcs=present_npcs,
        )
        assert res.status == "resolved"
        assert res.resolved_entity == "Intérieur de la cathédrale"

    def test_partial_exit_match(
        self, cathedral: Location, present_npcs: dict[str, NPC],
    ) -> None:
        action = InterpretedAction(
            action_type=ActionType.MOVE,
            actor_name="Arden",
            target_name="cathédrale",
            raw_input="j'entre dans la cathédrale",
        )
        res = EntityResolver.resolve(
            action, location=cathedral, npcs=present_npcs,
        )
        assert res.status == "resolved"
        assert res.resolved_entity == "Intérieur de la cathédrale"

    def test_unknown_exit(
        self, cathedral: Location, present_npcs: dict[str, NPC],
    ) -> None:
        action = InterpretedAction(
            action_type=ActionType.MOVE,
            actor_name="Arden",
            target_name="La Lune",
            raw_input="je vais sur la Lune",
        )
        res = EntityResolver.resolve(
            action, location=cathedral, npcs=present_npcs,
        )
        assert res.status == "unknown"
        assert res.raw_value == "La Lune"

    def test_move_without_location(
        self, present_npcs: dict[str, NPC],
    ) -> None:
        action = InterpretedAction(
            action_type=ActionType.MOVE,
            actor_name="Arden",
            target_name="North",
            raw_input="je vais au nord",
        )
        res = EntityResolver.resolve(
            action, location=None, npcs=present_npcs,
        )
        assert res.status == "unknown"


# ---------------------------------------------------------------------------
# SEARCH / INTERACT — object resolution
# ---------------------------------------------------------------------------


class TestResolveSearch:
    def test_search_without_target_is_not_applicable(
        self, cathedral: Location, present_npcs: dict[str, NPC],
    ) -> None:
        action = InterpretedAction(
            action_type=ActionType.SEARCH,
            actor_name="Arden",
            target_name=None,
            raw_input="je fouille",
        )
        res = EntityResolver.resolve(
            action, location=cathedral, npcs=present_npcs,
        )
        assert res.status == "not_applicable"

    def test_search_matches_visible_object(
        self, cathedral: Location, present_npcs: dict[str, NPC],
    ) -> None:
        action = InterpretedAction(
            action_type=ActionType.SEARCH,
            actor_name="Arden",
            target_name="autel",
            raw_input="je fouille l'autel",
        )
        res = EntityResolver.resolve(
            action, location=cathedral, npcs=present_npcs,
        )
        assert res.status == "resolved"
        assert res.resolved_entity == "Autel de pierre"

    def test_search_permissive_on_unlisted_object(
        self, cathedral: Location, present_npcs: dict[str, NPC],
    ) -> None:
        """Searching for something not in items_available is allowed
        (narrator may reveal hidden details)."""
        action = InterpretedAction(
            action_type=ActionType.SEARCH,
            actor_name="Arden",
            target_name="trappe secrète",
            raw_input="je cherche une trappe secrète",
        )
        res = EntityResolver.resolve(
            action, location=cathedral, npcs=present_npcs,
        )
        # Permissive: the raw value passes through so the narrator can decide.
        assert res.status == "resolved"
        assert res.resolved_entity == "trappe secrète"


class TestResolveInteract:
    def test_interact_matches_visible_object(
        self, cathedral: Location, present_npcs: dict[str, NPC],
    ) -> None:
        action = InterpretedAction(
            action_type=ActionType.INTERACT,
            actor_name="Arden",
            target_name="statue",
            raw_input="je touche la statue",
        )
        res = EntityResolver.resolve(
            action, location=cathedral, npcs=present_npcs,
        )
        assert res.status == "resolved"
        assert res.resolved_entity == "Statue de saint"

    def test_interact_unknown_object(
        self, cathedral: Location, present_npcs: dict[str, NPC],
    ) -> None:
        action = InterpretedAction(
            action_type=ActionType.INTERACT,
            actor_name="Arden",
            target_name="bouton secret",
            raw_input="j'appuie sur un bouton secret",
        )
        res = EntityResolver.resolve(
            action, location=cathedral, npcs=present_npcs,
        )
        assert res.status == "unknown"
        assert res.raw_value == "bouton secret"


# ---------------------------------------------------------------------------
# Combat targets — ATTACK
# ---------------------------------------------------------------------------


class TestResolveCombatTarget:
    def _combat_state(self, scores: AbilityScores) -> CombatState:
        pc = create_character(
            "Arden", Race.HUMAN, CharacterClass.FIGHTER, scores,
        )
        goblin = create_character(
            "Goblin", Race.HALFLING, CharacterClass.ROGUE, scores,
        )
        sword = Weapon(
            name="Longsword",
            damage_dice="1d8",
            damage_type=DamageType.SLASHING,
            weapon_category=WeaponCategory.MARTIAL_MELEE,
            weight=3.0,
        )
        return CombatState(
            combatants=[
                Combatant(
                    name="Arden",
                    side=CombatSide.PLAYER,
                    character=pc,
                    inventory=Inventory(equipped={EquipmentSlot.MAIN_HAND: sword}),
                ),
                Combatant(
                    name="Goblin",
                    side=CombatSide.ENEMY,
                    character=goblin,
                    inventory=Inventory(),
                ),
            ],
            round_number=1,
            current_turn_index=0,
        )

    def test_attack_resolves_enemy_combatant(
        self, cathedral: Location, ability_scores: AbilityScores,
    ) -> None:
        combat = self._combat_state(ability_scores)
        action = InterpretedAction(
            action_type=ActionType.ATTACK,
            actor_name="Arden",
            target_name="Goblin",
            weapon_name="Longsword",
            raw_input="j'attaque le gobelin",
        )
        res = EntityResolver.resolve(
            action,
            location=cathedral,
            npcs={},
            combat_state=combat,
        )
        assert res.status == "resolved"
        assert res.resolved_entity == "Goblin"

    def test_attack_unknown_combatant(
        self, cathedral: Location, ability_scores: AbilityScores,
    ) -> None:
        combat = self._combat_state(ability_scores)
        action = InterpretedAction(
            action_type=ActionType.ATTACK,
            actor_name="Arden",
            target_name="Dragon",
            weapon_name="Longsword",
            raw_input="j'attaque le dragon",
        )
        res = EntityResolver.resolve(
            action,
            location=cathedral,
            npcs={},
            combat_state=combat,
        )
        assert res.status == "unknown"
        assert res.raw_value == "Dragon"

    def test_attack_falls_back_to_present_npc(
        self, cathedral: Location, ability_scores: AbilityScores,
    ) -> None:
        """Lot C: out of combat, attacking a present NPC must resolve."""
        jeanne = _make_npc(
            "Jeanne",
            "Place de la Cathédrale",
            ability_scores,
            "Une villageoise terrifiée.",
            aliases=["villageoise", "paysanne"],
        )
        action = InterpretedAction(
            action_type=ActionType.ATTACK,
            actor_name="Arden",
            target_name="la villageoise",
            raw_input="j'attaque la villageoise",
        )
        res = EntityResolver.resolve(
            action,
            location=cathedral,
            npcs={jeanne.name: jeanne},
            combat_state=None,
        )
        assert res.status == "resolved"
        assert res.resolved_entity == "Jeanne"

    def test_attack_unknown_npc_returns_unknown(
        self, cathedral: Location, ability_scores: AbilityScores,
    ) -> None:
        """Lot C: no NPC present and no combat → unknown."""
        action = InterpretedAction(
            action_type=ActionType.ATTACK,
            actor_name="Arden",
            target_name="le dragon",
            raw_input="j'attaque le dragon",
        )
        res = EntityResolver.resolve(
            action,
            location=cathedral,
            npcs={},
            combat_state=None,
        )
        assert res.status == "unknown"


# ---------------------------------------------------------------------------
# USE_ITEM — inventory resolution
# ---------------------------------------------------------------------------


class TestResolveUseItem:
    def test_use_item_exact_match(
        self, cathedral: Location, present_npcs: dict[str, NPC],
    ) -> None:
        potion = Item(
            name="Healing Potion",
            item_type=ItemType.POTION,
            weight=0.5,
        )
        inventory = Inventory(items=[potion])
        action = InterpretedAction(
            action_type=ActionType.USE_ITEM,
            actor_name="Arden",
            item_name="Healing Potion",
            raw_input="je bois la potion de soin",
        )
        res = EntityResolver.resolve(
            action,
            location=cathedral,
            npcs=present_npcs,
            inventory=inventory,
        )
        assert res.status == "resolved"
        assert res.field_name == "item_name"
        assert res.resolved_entity == "Healing Potion"

    def test_use_item_partial_match(
        self, cathedral: Location, present_npcs: dict[str, NPC],
    ) -> None:
        potion = Item(
            name="Healing Potion",
            item_type=ItemType.POTION,
            weight=0.5,
        )
        inventory = Inventory(items=[potion])
        action = InterpretedAction(
            action_type=ActionType.USE_ITEM,
            actor_name="Arden",
            item_name="potion",
            raw_input="j'utilise la potion",
        )
        res = EntityResolver.resolve(
            action,
            location=cathedral,
            npcs=present_npcs,
            inventory=inventory,
        )
        assert res.status == "resolved"
        assert res.resolved_entity == "Healing Potion"

    def test_use_item_unknown(
        self, cathedral: Location, present_npcs: dict[str, NPC],
    ) -> None:
        inventory = Inventory(items=[])
        action = InterpretedAction(
            action_type=ActionType.USE_ITEM,
            actor_name="Arden",
            item_name="Elixir of Speed",
            raw_input="je bois un élixir de vitesse",
        )
        res = EntityResolver.resolve(
            action,
            location=cathedral,
            npcs=present_npcs,
            inventory=inventory,
        )
        assert res.status == "unknown"

    def test_use_item_falls_back_to_scene_object(
        self, present_npcs: dict[str, NPC],
    ) -> None:
        """USE_ITEM with an item not in inventory but present as a scene
        object should resolve as INTERACT with reclassified_action_type."""
        location = Location(
            name="Le cellier",
            description="Un cellier sombre.",
            connections=["Sacristie"],
            npcs_present=[],
            items_available=["Bière de Sainte-Croix", "Croix en fer"],
        )
        inventory = Inventory(items=[])
        action = InterpretedAction(
            action_type=ActionType.USE_ITEM,
            actor_name="Temps Test",
            item_name="Bière",
            raw_input="je bois la bière",
        )
        res = EntityResolver.resolve(
            action,
            location=location,
            npcs=present_npcs,
            inventory=inventory,
        )
        assert res.status == "resolved"
        assert res.field_name == "target_name"
        assert res.resolved_entity == "Bière de Sainte-Croix"
        assert res.reclassified_action_type == ActionType.INTERACT

    def test_use_item_inventory_takes_priority_over_scene(
        self, present_npcs: dict[str, NPC],
    ) -> None:
        """When an item matches both inventory and scene, inventory wins."""
        location = Location(
            name="Le cellier",
            description="Un cellier sombre.",
            connections=[],
            npcs_present=[],
            items_available=["Potion de force"],
        )
        potion = Item(
            name="Potion de force",
            item_type=ItemType.POTION,
            weight=0.5,
        )
        inventory = Inventory(items=[potion])
        action = InterpretedAction(
            action_type=ActionType.USE_ITEM,
            actor_name="Temps Test",
            item_name="Potion",
            raw_input="je bois la potion",
        )
        res = EntityResolver.resolve(
            action,
            location=location,
            npcs=present_npcs,
            inventory=inventory,
        )
        assert res.status == "resolved"
        assert res.field_name == "item_name"
        assert res.resolved_entity == "Potion de force"
        assert res.reclassified_action_type is None


# ---------------------------------------------------------------------------
# EntityCandidate — smoke test
# ---------------------------------------------------------------------------


class TestFrenchLemmatization:
    """Lot B — French gender/number lemmatization, aliases, fuzzy fallback."""

    @pytest.fixture()
    def village_square(self) -> Location:
        return Location(
            name="Place du village",
            description="Une place poussiéreuse.",
            connections=["Donjon satanique"],
            npcs_present=[
                "Jeanne, la Villageoise Terrifiée",
                "Père Thomas, le Moine Loyal",
            ],
            items_available=[],
        )

    @pytest.fixture()
    def jeanne_and_thomas(
        self, ability_scores: AbilityScores,
    ) -> dict[str, NPC]:
        jeanne = _make_npc(
            "Jeanne, la Villageoise Terrifiée",
            "Place du village",
            ability_scores,
            "Une jeune femme tremblante.",
            aliases=[
                "villageoise", "villageois", "villageur",
                "paysanne", "femme", "habitante",
            ],
        )
        thomas = _make_npc(
            "Père Thomas, le Moine Loyal",
            "Place du village",
            ability_scores,
            "Un moine en bure.",
            aliases=["moine", "prêtre", "religieux", "homme"],
        )
        return {jeanne.name: jeanne, thomas.name: thomas}

    def _talk(self, target: str) -> InterpretedAction:
        return InterpretedAction(
            action_type=ActionType.TALK,
            actor_name="Arden",
            target_name=target,
            raw_input=f"je parle à {target}",
        )

    def test_exact_match_full_name(
        self, village_square: Location,
        jeanne_and_thomas: dict[str, NPC],
    ) -> None:
        res = EntityResolver.resolve(
            self._talk("Jeanne, la Villageoise Terrifiée"),
            location=village_square, npcs=jeanne_and_thomas,
        )
        assert res.status == "resolved"
        assert res.resolved_entity == "Jeanne, la Villageoise Terrifiée"

    def test_token_match_first_name(
        self, village_square: Location,
        jeanne_and_thomas: dict[str, NPC],
    ) -> None:
        res = EntityResolver.resolve(
            self._talk("Jeanne"),
            location=village_square, npcs=jeanne_and_thomas,
        )
        assert res.status == "resolved"
        assert res.resolved_entity == "Jeanne, la Villageoise Terrifiée"

    def test_alias_match(
        self, village_square: Location,
        jeanne_and_thomas: dict[str, NPC],
    ) -> None:
        res = EntityResolver.resolve(
            self._talk("villageoise"),
            location=village_square, npcs=jeanne_and_thomas,
        )
        assert res.status == "resolved"
        assert res.resolved_entity == "Jeanne, la Villageoise Terrifiée"

    def test_lemma_gender_villageur_finds_jeanne(
        self, village_square: Location,
        jeanne_and_thomas: dict[str, NPC],
    ) -> None:
        """The original bug: 'le villageur' must reach 'la Villageoise'."""
        res = EntityResolver.resolve(
            self._talk("le villageur"),
            location=village_square, npcs=jeanne_and_thomas,
        )
        assert res.status == "resolved"
        assert res.resolved_entity == "Jeanne, la Villageoise Terrifiée"

    def test_lemma_number_villageurs_pluriel(
        self, village_square: Location,
        jeanne_and_thomas: dict[str, NPC],
    ) -> None:
        res = EntityResolver.resolve(
            self._talk("les villageurs"),
            location=village_square, npcs=jeanne_and_thomas,
        )
        assert res.status == "resolved"
        assert res.resolved_entity == "Jeanne, la Villageoise Terrifiée"

    def test_fuzzy_typo(
        self, village_square: Location,
        jeanne_and_thomas: dict[str, NPC],
    ) -> None:
        res = EntityResolver.resolve(
            self._talk("jean villageoise"),
            location=village_square, npcs=jeanne_and_thomas,
        )
        assert res.status == "resolved"
        assert res.resolved_entity == "Jeanne, la Villageoise Terrifiée"

    def test_ambiguous_two_villagers(
        self, ability_scores: AbilityScores,
    ) -> None:
        loc = Location(
            name="Place du village",
            description="",
            connections=[],
            npcs_present=["Jeanne, la Villageoise", "Pierre, le Villageois"],
        )
        jeanne = _make_npc(
            "Jeanne, la Villageoise", "Place du village", ability_scores,
            aliases=["villageoise", "villageois", "villageur"],
        )
        pierre = _make_npc(
            "Pierre, le Villageois", "Place du village", ability_scores,
            aliases=["villageois", "villageoise", "villageur"],
        )
        res = EntityResolver.resolve(
            self._talk("villageois"),
            location=loc,
            npcs={jeanne.name: jeanne, pierre.name: pierre},
        )
        assert res.status == "ambiguous"
        assert len(res.candidates) == 2

    def test_unknown_truly_absent(
        self, village_square: Location,
        jeanne_and_thomas: dict[str, NPC],
    ) -> None:
        res = EntityResolver.resolve(
            self._talk("dragon"),
            location=village_square, npcs=jeanne_and_thomas,
        )
        assert res.status == "unknown"

    def test_llm_fallback_resolves_when_python_fails(
        self, village_square: Location,
        jeanne_and_thomas: dict[str, NPC],
    ) -> None:
        """When the matcher returns nothing, an injected interpreter mock
        should be consulted and its valid pick honoured."""

        class _FakeInterpreter:
            def __init__(self) -> None:
                self.calls: list[str] = []

            def disambiguate_entity(
                self,
                raw_reference: str,
                candidates: list[tuple[str, list[str]]],
                language: str = "fr",
            ) -> str | None:
                self.calls.append(raw_reference)
                return "Jeanne, la Villageoise Terrifiée"

        fake = _FakeInterpreter()
        res = EntityResolver.resolve(
            self._talk("la fille apeurée"),  # no lemma/alias overlap
            location=village_square,
            npcs=jeanne_and_thomas,
            interpreter=fake,  # type: ignore[arg-type]
        )
        assert fake.calls == ["la fille apeurée"]
        assert res.status == "resolved"
        assert res.resolved_entity == "Jeanne, la Villageoise Terrifiée"

    def test_llm_fallback_unknown_when_llm_returns_none(
        self, village_square: Location,
        jeanne_and_thomas: dict[str, NPC],
    ) -> None:
        class _NullInterpreter:
            def disambiguate_entity(
                self, raw_reference: str,
                candidates: list[tuple[str, list[str]]],
                language: str = "fr",
            ) -> str | None:
                return None

        res = EntityResolver.resolve(
            self._talk("la créature mystérieuse"),
            location=village_square,
            npcs=jeanne_and_thomas,
            interpreter=_NullInterpreter(),  # type: ignore[arg-type]
        )
        assert res.status == "unknown"


class TestEntityCandidate:
    def test_candidate_construction(self) -> None:
        c = EntityCandidate(id="x", label="X", description="desc")
        assert c.id == "x"
        assert c.label == "X"
        assert c.description == "desc"
