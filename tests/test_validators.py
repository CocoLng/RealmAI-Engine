"""Tests for engine/validators.py — action validation."""

import pytest

from engine.character import (
    Ability,
    AbilityScores,
    Character,
    CharacterClass,
    Race,
    Size,
    apply_racial_bonuses,
    create_character,
)
from engine.combat import CombatSide, CombatState, Combatant
from engine.conditions import ActiveCondition, ConditionType
from engine.inventory import (
    DamageType,
    EquipmentSlot,
    Inventory,
    Item,
    ItemType,
    Weapon,
    WeaponCategory,
    WeaponProperty,
)
from engine.spells import SpellcasterState
from engine.validators import (
    Action,
    ActionType,
    validate_action,
    validate_attack,
    validate_cast_spell,
    validate_defend,
    validate_exploration_action,
    validate_flee,
    validate_use_item,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def fighter_combatant() -> Combatant:
    """A human fighter with a longsword equipped in main hand."""
    scores = AbilityScores(STR=16, DEX=14, CON=14, INT=10, WIS=12, CHA=8)
    scores = apply_racial_bonuses(scores, Race.HUMAN)
    char = create_character("Arden", Race.HUMAN, CharacterClass.FIGHTER, scores)
    longsword = Weapon(
        name="Longsword",
        damage_dice="1d8",
        damage_type=DamageType.SLASHING,
        weapon_category=WeaponCategory.MARTIAL_MELEE,
        weight=3.0,
    )
    healing_potion = Item(
        name="Healing Potion",
        item_type=ItemType.POTION,
        weight=0.5,
        value_gp=50,
    )
    inv = Inventory(
        items=[healing_potion],
        equipped={EquipmentSlot.MAIN_HAND: longsword},
        gold=0,
    )
    return Combatant(
        name="Arden", side=CombatSide.PLAYER, character=char, inventory=inv
    )


@pytest.fixture()
def goblin_combatant() -> Combatant:
    """An enemy goblin with a scimitar."""
    scores = AbilityScores(STR=8, DEX=14, CON=10, INT=10, WIS=8, CHA=8)
    char = Character(
        name="Goblin",
        race=Race.HALFLING,
        char_class=CharacterClass.ROGUE,
        ability_scores=scores,
        hp=7,
        max_hp=7,
        ac=15,
        speed=30,
        proficiency_bonus=2,
        saving_throw_proficiencies=(Ability.DEX, Ability.INT),
        hit_die="1d8",
        size=Size.SMALL,
    )
    scimitar = Weapon(
        name="Scimitar",
        damage_dice="1d6",
        damage_type=DamageType.SLASHING,
        weapon_category=WeaponCategory.MARTIAL_MELEE,
        weight=3.0,
        properties=[WeaponProperty.FINESSE, WeaponProperty.LIGHT],
    )
    inv = Inventory(
        items=[],
        equipped={EquipmentSlot.MAIN_HAND: scimitar},
        gold=0,
    )
    return Combatant(
        name="Goblin", side=CombatSide.ENEMY, character=char, inventory=inv
    )


@pytest.fixture()
def combat_state(
    fighter_combatant: Combatant, goblin_combatant: Combatant
) -> CombatState:
    """Combat with fighter at index 0 (their turn) and goblin at index 1."""
    return CombatState(
        combatants=[fighter_combatant, goblin_combatant],
        round_number=1,
        current_turn_index=0,
    )


@pytest.fixture()
def wizard_combatant() -> Combatant:
    """A wizard with Fire Bolt and Magic Missile known."""
    scores = AbilityScores(STR=8, DEX=14, CON=12, INT=16, WIS=12, CHA=10)
    scores = apply_racial_bonuses(scores, Race.HUMAN)
    char = create_character("Elara", Race.HUMAN, CharacterClass.WIZARD, scores)
    spellcaster = SpellcasterState(
        spellcasting_ability=Ability.INT,
        spells_known=["Fire Bolt", "Magic Missile"],
        spell_slots_max={1: 2},
        spell_slots_remaining={1: 2},
    )
    inv = Inventory()
    return Combatant(
        name="Elara",
        side=CombatSide.PLAYER,
        character=char,
        inventory=inv,
        spellcaster=spellcaster,
    )


# ---------------------------------------------------------------------------
# TestValidateCommon
# ---------------------------------------------------------------------------


class TestValidateCommon:
    """Tests for common validation checks shared by all action types."""

    def test_actor_not_found(self, combat_state: CombatState) -> None:
        action = Action(
            actor_name="Nobody", action_type=ActionType.DEFEND
        )
        result = validate_defend(action, combat_state)
        assert not result.is_valid
        assert "'Nobody' is not in combat" in (result.error_message or "")

    def test_actor_dead(self, combat_state: CombatState) -> None:
        combat_state.combatants[0].is_alive = False
        action = Action(
            actor_name="Arden", action_type=ActionType.DEFEND
        )
        result = validate_defend(action, combat_state)
        assert not result.is_valid
        assert "'Arden' is dead" in (result.error_message or "")

    def test_not_actors_turn(self, combat_state: CombatState) -> None:
        action = Action(
            actor_name="Goblin", action_type=ActionType.DEFEND
        )
        result = validate_defend(action, combat_state)
        assert not result.is_valid
        assert "It is not Goblin's turn" in (result.error_message or "")

    def test_actor_incapacitated(self, combat_state: CombatState) -> None:
        combat_state.combatants[0].conditions.append(
            ActiveCondition(
                condition_type=ConditionType.STUNNED, source="test"
            )
        )
        action = Action(
            actor_name="Arden", action_type=ActionType.DEFEND
        )
        result = validate_defend(action, combat_state)
        assert not result.is_valid
        assert "'Arden' is incapacitated" in (result.error_message or "")


# ---------------------------------------------------------------------------
# TestValidateAttack
# ---------------------------------------------------------------------------


class TestValidateAttack:
    """Tests for attack action validation."""

    def test_valid_attack(self, combat_state: CombatState) -> None:
        action = Action(
            actor_name="Arden",
            action_type=ActionType.ATTACK,
            target_name="Goblin",
            weapon_name="Longsword",
        )
        result = validate_attack(action, combat_state)
        assert result.is_valid
        assert result.error_message is None

    def test_no_target(self, combat_state: CombatState) -> None:
        action = Action(
            actor_name="Arden",
            action_type=ActionType.ATTACK,
            weapon_name="Longsword",
        )
        result = validate_attack(action, combat_state)
        assert not result.is_valid
        assert "Attack requires a target" in (result.error_message or "")

    def test_target_not_found(self, combat_state: CombatState) -> None:
        action = Action(
            actor_name="Arden",
            action_type=ActionType.ATTACK,
            target_name="Dragon",
            weapon_name="Longsword",
        )
        result = validate_attack(action, combat_state)
        assert not result.is_valid
        assert "Target 'Dragon' is not in combat" in (result.error_message or "")

    def test_target_dead(self, combat_state: CombatState) -> None:
        combat_state.combatants[1].is_alive = False
        action = Action(
            actor_name="Arden",
            action_type=ActionType.ATTACK,
            target_name="Goblin",
            weapon_name="Longsword",
        )
        result = validate_attack(action, combat_state)
        assert not result.is_valid
        assert "Target 'Goblin' is already dead" in (result.error_message or "")

    def test_no_weapon(self, combat_state: CombatState) -> None:
        action = Action(
            actor_name="Arden",
            action_type=ActionType.ATTACK,
            target_name="Goblin",
        )
        result = validate_attack(action, combat_state)
        assert not result.is_valid
        assert "Attack requires a weapon" in (result.error_message or "")

    def test_weapon_not_equipped(self, combat_state: CombatState) -> None:
        action = Action(
            actor_name="Arden",
            action_type=ActionType.ATTACK,
            target_name="Goblin",
            weapon_name="Greataxe",
        )
        result = validate_attack(action, combat_state)
        assert not result.is_valid
        assert "Weapon 'Greataxe' is not equipped" in (result.error_message or "")


# ---------------------------------------------------------------------------
# TestValidateCastSpell
# ---------------------------------------------------------------------------


class TestValidateCastSpell:
    """Tests for spell cast validation."""

    def test_not_a_spellcaster(self, combat_state: CombatState) -> None:
        action = Action(
            actor_name="Arden",
            action_type=ActionType.CAST_SPELL,
            spell_name="Fire Bolt",
        )
        result = validate_cast_spell(action, combat_state)
        assert not result.is_valid
        assert "'Arden' is not a spellcaster" in (result.error_message or "")

    def test_no_spell_name(
        self, wizard_combatant: Combatant, goblin_combatant: Combatant
    ) -> None:
        state = CombatState(
            combatants=[wizard_combatant, goblin_combatant],
            round_number=1,
            current_turn_index=0,
        )
        action = Action(
            actor_name="Elara",
            action_type=ActionType.CAST_SPELL,
        )
        result = validate_cast_spell(action, state)
        assert not result.is_valid
        assert "Cast Spell requires a spell name" in (result.error_message or "")

    def test_spell_not_found(
        self, wizard_combatant: Combatant, goblin_combatant: Combatant
    ) -> None:
        state = CombatState(
            combatants=[wizard_combatant, goblin_combatant],
            round_number=1,
            current_turn_index=0,
        )
        action = Action(
            actor_name="Elara",
            action_type=ActionType.CAST_SPELL,
            spell_name="Wish",
        )
        result = validate_cast_spell(action, state)
        assert not result.is_valid
        assert "Spell 'Wish' does not exist" in (result.error_message or "")

    def test_cannot_cast_no_slots(
        self, wizard_combatant: Combatant, goblin_combatant: Combatant
    ) -> None:
        wizard_combatant.spellcaster.spell_slots_remaining = {1: 0}  # type: ignore[union-attr]
        # Remove Fire Bolt so we test a leveled spell
        state = CombatState(
            combatants=[wizard_combatant, goblin_combatant],
            round_number=1,
            current_turn_index=0,
        )
        action = Action(
            actor_name="Elara",
            action_type=ActionType.CAST_SPELL,
            spell_name="Magic Missile",
        )
        result = validate_cast_spell(action, state)
        assert not result.is_valid
        assert "Cannot cast 'Magic Missile'" in (result.error_message or "")

    def test_valid_cast_cantrip(
        self, wizard_combatant: Combatant, goblin_combatant: Combatant
    ) -> None:
        state = CombatState(
            combatants=[wizard_combatant, goblin_combatant],
            round_number=1,
            current_turn_index=0,
        )
        action = Action(
            actor_name="Elara",
            action_type=ActionType.CAST_SPELL,
            spell_name="Fire Bolt",
            target_name="Goblin",
        )
        result = validate_cast_spell(action, state)
        assert result.is_valid

    def test_valid_cast_leveled(
        self, wizard_combatant: Combatant, goblin_combatant: Combatant
    ) -> None:
        state = CombatState(
            combatants=[wizard_combatant, goblin_combatant],
            round_number=1,
            current_turn_index=0,
        )
        action = Action(
            actor_name="Elara",
            action_type=ActionType.CAST_SPELL,
            spell_name="Magic Missile",
            target_name="Goblin",
        )
        result = validate_cast_spell(action, state)
        assert result.is_valid

    def test_damage_spell_requires_target(
        self, wizard_combatant: Combatant, goblin_combatant: Combatant
    ) -> None:
        state = CombatState(
            combatants=[wizard_combatant, goblin_combatant],
            round_number=1,
            current_turn_index=0,
        )
        action = Action(
            actor_name="Elara",
            action_type=ActionType.CAST_SPELL,
            spell_name="Fire Bolt",
            # No target_name
        )
        result = validate_cast_spell(action, state)
        assert not result.is_valid
        assert "requires a target" in (result.error_message or "")

    def test_target_not_in_combat(
        self, wizard_combatant: Combatant, goblin_combatant: Combatant
    ) -> None:
        state = CombatState(
            combatants=[wizard_combatant, goblin_combatant],
            round_number=1,
            current_turn_index=0,
        )
        action = Action(
            actor_name="Elara",
            action_type=ActionType.CAST_SPELL,
            spell_name="Fire Bolt",
            target_name="Dragon",
        )
        result = validate_cast_spell(action, state)
        assert not result.is_valid
        assert "Target 'Dragon' is not in combat" in (result.error_message or "")


# ---------------------------------------------------------------------------
# TestValidateDefend
# ---------------------------------------------------------------------------


class TestValidateDefend:
    """Tests for defend action validation."""

    def test_valid_defend(self, combat_state: CombatState) -> None:
        action = Action(
            actor_name="Arden",
            action_type=ActionType.DEFEND,
        )
        result = validate_defend(action, combat_state)
        assert result.is_valid
        assert result.error_message is None


# ---------------------------------------------------------------------------
# TestValidateFlee
# ---------------------------------------------------------------------------


class TestValidateFlee:
    """Tests for flee action validation."""

    def test_valid_flee(self, combat_state: CombatState) -> None:
        action = Action(
            actor_name="Arden",
            action_type=ActionType.FLEE,
        )
        result = validate_flee(action, combat_state)
        assert result.is_valid

    def test_cannot_move_grappled(self, combat_state: CombatState) -> None:
        combat_state.combatants[0].conditions.append(
            ActiveCondition(
                condition_type=ConditionType.GRAPPLED, source="test"
            )
        )
        action = Action(
            actor_name="Arden",
            action_type=ActionType.FLEE,
        )
        result = validate_flee(action, combat_state)
        assert not result.is_valid
        assert "'Arden' cannot move" in (result.error_message or "")

    def test_cannot_move_restrained(self, combat_state: CombatState) -> None:
        combat_state.combatants[0].conditions.append(
            ActiveCondition(
                condition_type=ConditionType.RESTRAINED, source="test"
            )
        )
        action = Action(
            actor_name="Arden",
            action_type=ActionType.FLEE,
        )
        result = validate_flee(action, combat_state)
        assert not result.is_valid
        assert "'Arden' cannot move" in (result.error_message or "")


# ---------------------------------------------------------------------------
# TestValidateUseItem
# ---------------------------------------------------------------------------


class TestValidateUseItem:
    """Tests for use item action validation."""

    def test_valid_use_carried_item(self, combat_state: CombatState) -> None:
        action = Action(
            actor_name="Arden",
            action_type=ActionType.USE_ITEM,
            item_name="Healing Potion",
        )
        result = validate_use_item(action, combat_state)
        assert result.is_valid

    def test_valid_use_equipped_item(self, combat_state: CombatState) -> None:
        action = Action(
            actor_name="Arden",
            action_type=ActionType.USE_ITEM,
            item_name="Longsword",
        )
        result = validate_use_item(action, combat_state)
        assert result.is_valid

    def test_no_item_name(self, combat_state: CombatState) -> None:
        action = Action(
            actor_name="Arden",
            action_type=ActionType.USE_ITEM,
        )
        result = validate_use_item(action, combat_state)
        assert not result.is_valid
        assert "Use Item requires an item name" in (result.error_message or "")

    def test_item_not_found(self, combat_state: CombatState) -> None:
        action = Action(
            actor_name="Arden",
            action_type=ActionType.USE_ITEM,
            item_name="Scroll of Fireball",
        )
        result = validate_use_item(action, combat_state)
        assert not result.is_valid
        assert "Item 'Scroll of Fireball' not found in inventory" in (
            result.error_message or ""
        )


# ---------------------------------------------------------------------------
# TestValidateAction (dispatch)
# ---------------------------------------------------------------------------


class TestValidateAction:
    """Tests for the top-level dispatch function."""

    def test_dispatches_to_attack(self, combat_state: CombatState) -> None:
        action = Action(
            actor_name="Arden",
            action_type=ActionType.ATTACK,
            target_name="Goblin",
            weapon_name="Longsword",
        )
        result = validate_action(action, combat_state)
        assert result.is_valid

    def test_dispatches_to_defend(self, combat_state: CombatState) -> None:
        action = Action(
            actor_name="Arden",
            action_type=ActionType.DEFEND,
        )
        result = validate_action(action, combat_state)
        assert result.is_valid

    def test_dispatches_to_flee(self, combat_state: CombatState) -> None:
        action = Action(
            actor_name="Arden",
            action_type=ActionType.FLEE,
        )
        result = validate_action(action, combat_state)
        assert result.is_valid

    def test_dispatches_to_use_item(self, combat_state: CombatState) -> None:
        action = Action(
            actor_name="Arden",
            action_type=ActionType.USE_ITEM,
            item_name="Healing Potion",
        )
        result = validate_action(action, combat_state)
        assert result.is_valid

    def test_dispatches_to_cast_spell(
        self, wizard_combatant: Combatant, goblin_combatant: Combatant
    ) -> None:
        state = CombatState(
            combatants=[wizard_combatant, goblin_combatant],
            round_number=1,
            current_turn_index=0,
        )
        action = Action(
            actor_name="Elara",
            action_type=ActionType.CAST_SPELL,
            spell_name="Fire Bolt",
            target_name="Goblin",
        )
        result = validate_action(action, state)
        assert result.is_valid


# ---------------------------------------------------------------------------
# Exploration ActionType values
# ---------------------------------------------------------------------------


class TestExplorationActionTypes:
    """The new exploration ActionType values must be defined."""

    def test_look_action_type_exists(self) -> None:
        assert ActionType.LOOK.value == "Look"

    def test_search_action_type_exists(self) -> None:
        assert ActionType.SEARCH.value == "Search"

    def test_talk_action_type_exists(self) -> None:
        assert ActionType.TALK.value == "Talk"

    def test_move_action_type_exists(self) -> None:
        assert ActionType.MOVE.value == "Move"

    def test_interact_action_type_exists(self) -> None:
        assert ActionType.INTERACT.value == "Interact"

    def test_improvise_action_type_exists(self) -> None:
        assert ActionType.IMPROVISE.value == "Improvise"


# ---------------------------------------------------------------------------
# validate_exploration_action — rule-only checks (entity existence is handled
# upstream by the EntityResolver before validation runs).
# ---------------------------------------------------------------------------


class TestValidateLook:
    """LOOK is always legal — it is a free observation action."""

    def test_look_always_valid(self) -> None:
        action = Action(actor_name="Arden", action_type=ActionType.LOOK)
        result = validate_exploration_action(action)
        assert result.is_valid
        assert result.error_message is None

    def test_look_ignores_target(self) -> None:
        action = Action(
            actor_name="Arden",
            action_type=ActionType.LOOK,
            target_name="nothing in particular",
        )
        result = validate_exploration_action(action)
        assert result.is_valid


class TestValidateMove:
    """MOVE requires a target (the exit name)."""

    def test_move_with_target(self) -> None:
        action = Action(
            actor_name="Arden",
            action_type=ActionType.MOVE,
            target_name="Intérieur de la cathédrale",
        )
        result = validate_exploration_action(action)
        assert result.is_valid

    def test_move_without_target(self) -> None:
        action = Action(actor_name="Arden", action_type=ActionType.MOVE)
        result = validate_exploration_action(action)
        assert not result.is_valid
        assert "Move requires a target" in (result.error_message or "")


class TestValidateTalk:
    """TALK requires a target (the NPC name, already resolved upstream)."""

    def test_talk_with_target(self) -> None:
        action = Action(
            actor_name="Arden",
            action_type=ActionType.TALK,
            target_name="Père Aldric",
        )
        result = validate_exploration_action(action)
        assert result.is_valid

    def test_talk_without_target(self) -> None:
        action = Action(actor_name="Arden", action_type=ActionType.TALK)
        result = validate_exploration_action(action)
        assert not result.is_valid
        assert "Talk requires a target" in (result.error_message or "")


class TestValidateSearch:
    """SEARCH is always valid — narrator arbitrates whether anything is found."""

    def test_search_without_target_valid(self) -> None:
        action = Action(actor_name="Arden", action_type=ActionType.SEARCH)
        result = validate_exploration_action(action)
        assert result.is_valid

    def test_search_with_target_valid(self) -> None:
        action = Action(
            actor_name="Arden",
            action_type=ActionType.SEARCH,
            target_name="Autel de pierre",
        )
        result = validate_exploration_action(action)
        assert result.is_valid


class TestValidateInteract:
    """INTERACT requires a target (the object to manipulate)."""

    def test_interact_with_target_valid(self) -> None:
        action = Action(
            actor_name="Arden",
            action_type=ActionType.INTERACT,
            target_name="levier de pierre",
        )
        result = validate_exploration_action(action)
        assert result.is_valid

    def test_interact_without_target_invalid(self) -> None:
        action = Action(actor_name="Arden", action_type=ActionType.INTERACT)
        result = validate_exploration_action(action)
        assert not result.is_valid
        assert "Interact requires a target" in (result.error_message or "")


class TestValidateImprovise:
    """IMPROVISE is always valid — the narrator arbitrates the outcome."""

    def test_improvise_without_target_valid(self) -> None:
        action = Action(actor_name="Arden", action_type=ActionType.IMPROVISE)
        result = validate_exploration_action(action)
        assert result.is_valid

    def test_improvise_with_description_valid(self) -> None:
        action = Action(
            actor_name="Arden",
            action_type=ActionType.IMPROVISE,
            target_name="the chandelier",
        )
        result = validate_exploration_action(action)
        assert result.is_valid


class TestValidateExplorationDispatch:
    """validate_exploration_action dispatches based on ActionType."""

    def test_rejects_combat_action_type(self) -> None:
        """Combat action types should be routed through validate_action, not here."""
        action = Action(
            actor_name="Arden",
            action_type=ActionType.ATTACK,
            target_name="Goblin",
            weapon_name="Longsword",
        )
        result = validate_exploration_action(action)
        assert not result.is_valid
        assert "not an exploration action" in (result.error_message or "").lower()


# ---------------------------------------------------------------------------
# Concentration conflict info logging (M9)
# ---------------------------------------------------------------------------


class TestConcentrationLogging:
    """Casting a concentration spell while already concentrating is legal but logged."""

    def test_concentration_conflict_still_valid(
        self, wizard_combatant: Combatant, goblin_combatant: Combatant
    ) -> None:
        """Casting a new concentration spell when already concentrating should pass."""
        wizard_combatant.spellcaster.spells_known.append("Bless")  # type: ignore[union-attr]
        wizard_combatant.spellcaster.concentration_spell = "Hunter's Mark"  # type: ignore[union-attr]
        state = CombatState(
            combatants=[wizard_combatant, goblin_combatant],
            round_number=1,
            current_turn_index=0,
        )
        action = Action(
            actor_name="Elara",
            action_type=ActionType.CAST_SPELL,
            spell_name="Bless",
            target_name="Goblin",
        )
        result = validate_cast_spell(action, state)
        assert result.is_valid

    def test_concentration_conflict_logs_info(
        self,
        wizard_combatant: Combatant,
        goblin_combatant: Combatant,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Casting a new concentration spell should log that the old one will drop."""
        import logging

        wizard_combatant.spellcaster.spells_known.append("Bless")  # type: ignore[union-attr]
        wizard_combatant.spellcaster.concentration_spell = "Hunter's Mark"  # type: ignore[union-attr]
        state = CombatState(
            combatants=[wizard_combatant, goblin_combatant],
            round_number=1,
            current_turn_index=0,
        )
        action = Action(
            actor_name="Elara",
            action_type=ActionType.CAST_SPELL,
            spell_name="Bless",
            target_name="Goblin",
        )
        with caplog.at_level(logging.INFO, logger="engine.validators"):
            validate_cast_spell(action, state)
        assert "Hunter's Mark" in caplog.text
        assert "Bless" in caplog.text

    def test_non_concentration_spell_no_log(
        self,
        wizard_combatant: Combatant,
        goblin_combatant: Combatant,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Casting a non-concentration spell while concentrating should not log."""
        import logging

        wizard_combatant.spellcaster.concentration_spell = "Hunter's Mark"  # type: ignore[union-attr]
        state = CombatState(
            combatants=[wizard_combatant, goblin_combatant],
            round_number=1,
            current_turn_index=0,
        )
        action = Action(
            actor_name="Elara",
            action_type=ActionType.CAST_SPELL,
            spell_name="Magic Missile",
            target_name="Goblin",
        )
        with caplog.at_level(logging.INFO, logger="engine.validators"):
            result = validate_cast_spell(action, state)
        assert result.is_valid
        assert "Hunter's Mark" not in caplog.text
