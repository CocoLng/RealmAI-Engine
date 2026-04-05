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
