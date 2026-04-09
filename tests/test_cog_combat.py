"""Tests for the Combat cog — combat lifecycle and resolution."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.cogs.combat import CombatCog, XP_PER_ENEMY, build_npc_combatant
from bot.game_session import GameSession
from engine.character import (
    AbilityScores,
    CharacterClass,
    Race,
    create_character,
)
from engine.combat import CombatSide, CombatState, Combatant
from engine.inventory import (
    DamageType,
    EquipmentSlot,
    Weapon,
    WeaponCategory,
    add_item,
    create_inventory,
    equip_item,
)
from world.campaign import Campaign
from world.npc import NPC


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SCORES = AbilityScores(STR=16, DEX=14, CON=13, INT=10, WIS=12, CHA=8)


def _make_weapon() -> Weapon:
    return Weapon(
        name="Longsword",
        item_type="Weapon",
        weight=3.0,
        value_gp=15,
        damage_dice="1d8",
        damage_type=DamageType.SLASHING,
        weapon_category=WeaponCategory.MARTIAL_MELEE,
        properties=[],
    )


def _make_session_with_character() -> GameSession:
    campaign = Campaign(id="camp-1", name="Test", player_names=["Alice"])
    session = GameSession(campaign=campaign)
    char = create_character("Thorin", Race.DWARF, CharacterClass.FIGHTER, _SCORES)
    inv = create_inventory()
    weapon = _make_weapon()
    inv = add_item(inv, weapon)
    inv = equip_item(inv, "Longsword", EquipmentSlot.MAIN_HAND)
    session.characters[100] = char
    session.inventories[100] = inv
    session.spellcasters[100] = None
    return session


def _make_enemy() -> Combatant:
    scores = AbilityScores(STR=12, DEX=10, CON=10, INT=6, WIS=8, CHA=6)
    enemy_char = create_character("Goblin", Race.HUMAN, CharacterClass.FIGHTER, scores)
    enemy_char.hp = 7
    enemy_char.max_hp = 7
    enemy_inv = create_inventory()
    weapon = _make_weapon()
    enemy_inv = add_item(enemy_inv, weapon)
    enemy_inv = equip_item(enemy_inv, "Longsword", EquipmentSlot.MAIN_HAND)
    return Combatant(
        name="Goblin",
        side=CombatSide.ENEMY,
        character=enemy_char,
        inventory=enemy_inv,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def bot() -> MagicMock:
    b = MagicMock()
    b.sessions = {}
    b.get_session = MagicMock(return_value=None)
    return b


@pytest.fixture()
def cog(bot: MagicMock) -> CombatCog:
    return CombatCog(bot)


@pytest.fixture()
def channel() -> AsyncMock:
    ch = AsyncMock()
    ch.send = AsyncMock()
    return ch


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFindUserId:
    """Test _find_user_id helper."""

    def test_finds_matching_user(self) -> None:
        session = _make_session_with_character()
        assert CombatCog._find_user_id(session, "Thorin") == 100

    def test_returns_none_for_unknown(self) -> None:
        session = _make_session_with_character()
        assert CombatCog._find_user_id(session, "Unknown") is None


class TestGetEquippedWeapon:
    """Test _get_equipped_weapon helper."""

    def test_finds_weapon(self) -> None:
        session = _make_session_with_character()
        weapon = CombatCog._get_equipped_weapon(session, 100)
        assert weapon is not None
        assert weapon.name == "Longsword"

    def test_returns_none_no_inventory(self) -> None:
        session = _make_session_with_character()
        assert CombatCog._get_equipped_weapon(session, 999) is None

    def test_returns_none_empty_main_hand(self) -> None:
        session = _make_session_with_character()
        session.inventories[100] = create_inventory()  # no equipped items
        assert CombatCog._get_equipped_weapon(session, 100) is None


class TestNarrate:
    """Test _narrate fallback behavior."""

    @pytest.mark.asyncio()
    async def test_no_narrator_returns_mechanics(self, cog: CombatCog) -> None:
        session = _make_session_with_character()
        session.narrator = None
        narrative, tone = await cog._narrate(session, "Thorin hits Goblin")
        assert narrative == "Thorin hits Goblin"
        assert tone == "dramatic"

    @pytest.mark.asyncio()
    async def test_narrator_used_when_available(self, cog: CombatCog) -> None:
        session = _make_session_with_character()
        mock_narrator = MagicMock()
        mock_result = MagicMock()
        mock_result.narrative = "The dwarf's blade finds its mark!"
        mock_result.tone = "tense"
        mock_narrator.narrate.return_value = mock_result
        session.narrator = mock_narrator

        narrative, tone = await cog._narrate(session, "Thorin hits Goblin")
        assert narrative == "The dwarf's blade finds its mark!"
        assert tone == "tense"

    @pytest.mark.asyncio()
    async def test_narrator_exception_falls_back(self, cog: CombatCog) -> None:
        session = _make_session_with_character()
        mock_narrator = MagicMock()
        mock_narrator.narrate.side_effect = RuntimeError("Ollama down")
        session.narrator = mock_narrator

        narrative, tone = await cog._narrate(session, "Thorin hits Goblin")
        assert narrative == "Thorin hits Goblin"
        assert tone == "dramatic"


class TestEndCombat:
    """Test _end_combat XP distribution."""

    @pytest.mark.asyncio()
    async def test_xp_distributed(self, cog: CombatCog, channel: AsyncMock) -> None:
        session = _make_session_with_character()
        enemy = _make_enemy()
        enemy.is_alive = False
        state = CombatState(
            combatants=[
                Combatant(
                    name="Thorin",
                    side=CombatSide.PLAYER,
                    character=session.characters[100],
                    inventory=session.inventories[100],
                ),
                enemy,
            ],
        )
        session.combat_state = state

        old_xp = session.characters[100].xp
        await cog._end_combat(channel, session)

        assert session.combat_state is None
        assert session.characters[100].xp == old_xp + XP_PER_ENEMY
        channel.send.assert_called_once()
        msg = channel.send.call_args[0][0]
        assert "Combat termine" in msg
        assert str(XP_PER_ENEMY) in msg

    @pytest.mark.asyncio()
    async def test_no_state_noop(self, cog: CombatCog, channel: AsyncMock) -> None:
        session = _make_session_with_character()
        session.combat_state = None
        await cog._end_combat(channel, session)
        channel.send.assert_not_called()


class TestStartCombatEncounter:
    """Test start_combat_encounter sets up state."""

    @pytest.mark.asyncio()
    async def test_creates_combat_state(self, cog: CombatCog, channel: AsyncMock) -> None:
        session = _make_session_with_character()
        enemy = _make_enemy()

        # Patch _prompt_turn to prevent recursive turn loop
        with patch.object(cog, "_prompt_turn", new_callable=AsyncMock):
            await cog.start_combat_encounter(channel, session, [enemy])

        assert session.combat_state is not None
        assert session.combat_state.is_active is True
        assert len(session.combat_state.combatants) == 2
        channel.send.assert_called()  # "Combat !" embed


class TestGetCombatantWeapon:
    """Test _get_combatant_weapon helper."""

    def test_finds_weapon(self) -> None:
        enemy = _make_enemy()
        weapon = CombatCog._get_combatant_weapon(enemy)
        assert weapon is not None
        assert weapon.name == "Longsword"

    def test_no_weapon(self) -> None:
        scores = AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10)
        char = create_character("Unarmed", Race.HUMAN, CharacterClass.FIGHTER, scores)
        combatant = Combatant(
            name="Unarmed",
            side=CombatSide.ENEMY,
            character=char,
            inventory=create_inventory(),
        )
        assert CombatCog._get_combatant_weapon(combatant) is None


# ---------------------------------------------------------------------------
# build_npc_combatant tests
# ---------------------------------------------------------------------------


class TestBuildNpcCombatant:
    """Tests for build_npc_combatant() — NPC bootstrap with default weapon."""

    def _make_npc(
        self, char_class: CharacterClass = CharacterClass.FIGHTER,
    ) -> NPC:
        return NPC(
            name="Test NPC",
            race=Race.HUMAN,
            char_class=char_class,
            level=3,
            ability_scores=AbilityScores(
                STR=14, DEX=12, CON=13, INT=10, WIS=10, CHA=8,
            ),
            hp=20,
            max_hp=20,
            ac=14,
        )

    def test_combatant_has_equipped_weapon(self) -> None:
        """build_npc_combatant() equips a weapon in MAIN_HAND."""
        npc = self._make_npc()
        combatant = build_npc_combatant(npc)

        assert EquipmentSlot.MAIN_HAND in combatant.inventory.equipped
        assert isinstance(
            combatant.inventory.equipped[EquipmentSlot.MAIN_HAND], Weapon,
        )

    def test_fighter_npc_gets_longsword(self) -> None:
        npc = self._make_npc(CharacterClass.FIGHTER)
        combatant = build_npc_combatant(npc)
        weapon = combatant.inventory.equipped[EquipmentSlot.MAIN_HAND]
        assert weapon.name == "Longsword"

    def test_rogue_npc_gets_shortsword(self) -> None:
        npc = self._make_npc(CharacterClass.ROGUE)
        combatant = build_npc_combatant(npc)
        weapon = combatant.inventory.equipped[EquipmentSlot.MAIN_HAND]
        assert weapon.name == "Shortsword"

    def test_npc_without_class_defaults_to_fighter(self) -> None:
        """NPC with char_class=None should default to Fighter → Longsword."""
        npc = NPC(
            name="Bandit",
            race=Race.HUMAN,
            char_class=None,
            ability_scores=AbilityScores(
                STR=12, DEX=10, CON=10, INT=8, WIS=8, CHA=8,
            ),
            hp=10,
            max_hp=10,
            ac=12,
        )
        combatant = build_npc_combatant(npc)
        assert EquipmentSlot.MAIN_HAND in combatant.inventory.equipped

    @pytest.mark.parametrize("char_class", list(CharacterClass))
    def test_every_class_produces_armed_combatant(
        self, char_class: CharacterClass,
    ) -> None:
        """Every CharacterClass produces a combatant with an equipped weapon."""
        npc = self._make_npc(char_class)
        combatant = build_npc_combatant(npc)
        assert EquipmentSlot.MAIN_HAND in combatant.inventory.equipped
        assert isinstance(
            combatant.inventory.equipped[EquipmentSlot.MAIN_HAND], Weapon,
        )
