"""Tests for the Inventory cog — /inventory, /equip, /unequip, /use_item."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.cogs.inventory import InventoryCog
from bot.game_session import GameSession
from engine.character import (
    AbilityScores,
    Character,
    CharacterClass,
    Race,
    create_character,
)
from engine.inventory import (
    EquipmentSlot,
    Inventory,
    Item,
    ItemType,
    Weapon,
    DamageType,
    WeaponCategory,
    add_item,
    create_inventory,
)
from world.campaign import Campaign


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEFAULT_SCORES = AbilityScores(STR=14, DEX=12, CON=13, INT=10, WIS=15, CHA=8)


def _make_character(name: str = "Thorin", **kwargs: object) -> Character:
    """Shortcut to create a test character with sensible defaults."""
    defaults: dict[str, object] = {
        "race": Race.DWARF,
        "char_class": CharacterClass.FIGHTER,
        "ability_scores": _DEFAULT_SCORES,
    }
    defaults.update(kwargs)
    return create_character(name=name, **defaults)  # type: ignore[arg-type]


def _make_session() -> GameSession:
    """Create a minimal GameSession for testing."""
    campaign = Campaign(name="Test Campaign")
    return GameSession(campaign=campaign)


def _make_inventory_with_sword() -> Inventory:
    """Create an inventory containing a Longsword in the items list."""
    inv = create_inventory()
    sword = Weapon(
        name="Longsword",
        item_type=ItemType.WEAPON,
        weight=3.0,
        value_gp=15,
        damage_dice="1d8",
        damage_type=DamageType.SLASHING,
        weapon_category=WeaponCategory.MARTIAL_MELEE,
        properties=[],
    )
    return add_item(inv, sword)


def _make_inventory_with_potion(quantity: int = 2) -> Inventory:
    """Create an inventory containing a Healing Potion (with heal dice)."""
    inv = create_inventory()
    potion = Item(
        name="Healing Potion",
        item_type=ItemType.POTION,
        weight=0.5,
        value_gp=50,
        description="Heals 2d4+2 hit points.",
        stackable=True,
        quantity=quantity,
        heal_dice="2d4+2",
    )
    return add_item(inv, potion)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

USER_ID = 99999
CHANNEL_ID = 12345


@pytest.fixture()
def bot() -> MagicMock:
    """A mocked RealmBot."""
    b = MagicMock()
    b.sessions = {}
    b.get_session = MagicMock(return_value=None)
    db_session = MagicMock()
    b.db_factory = MagicMock(return_value=db_session)
    return b


@pytest.fixture()
def cog(bot: MagicMock) -> InventoryCog:
    return InventoryCog(bot)


@pytest.fixture()
def interaction() -> AsyncMock:
    """A mocked discord.Interaction with response and followup."""
    inter = AsyncMock(spec=discord.Interaction)
    inter.channel_id = CHANNEL_ID
    inter.user = MagicMock()
    inter.user.id = USER_ID

    inter.response = AsyncMock()
    inter.response.send_message = AsyncMock()

    inter.followup = AsyncMock()
    inter.followup.send = AsyncMock()
    return inter


# ===========================================================================
# /inventory
# ===========================================================================


class TestInventory:
    """Tests for the /inventory command."""

    @pytest.mark.asyncio()
    async def test_no_session(
        self, cog: InventoryCog, interaction: AsyncMock,
    ) -> None:
        """Should reply with an error when no session is active."""
        cog.bot.get_session.return_value = None

        await cog.inventory.callback(cog, interaction, public=False)

        interaction.response.send_message.assert_called_once()
        msg = interaction.response.send_message.call_args
        assert "Aucune session active" in msg[0][0]
        assert msg[1]["ephemeral"] is True

    @pytest.mark.asyncio()
    async def test_no_character(
        self, cog: InventoryCog, interaction: AsyncMock,
    ) -> None:
        """Should reply with an error when user has no character."""
        session = _make_session()
        cog.bot.get_session.return_value = session

        await cog.inventory.callback(cog, interaction, public=False)

        msg = interaction.response.send_message.call_args
        assert "pas de personnage" in str(msg)
        assert msg[1]["ephemeral"] is True

    @pytest.mark.asyncio()
    async def test_no_character_points_at_the_lobby_not_a_dead_command(
        self, cog: InventoryCog, interaction: AsyncMock,
    ) -> None:
        """/create_character was removed — onboarding goes through the lobby."""
        session = _make_session()
        cog.bot.get_session.return_value = session

        await cog.inventory.callback(cog, interaction, public=False)

        text = interaction.response.send_message.call_args[0][0]
        assert "/create_character" not in text
        assert "Rejoindre" in text

    @pytest.mark.asyncio()
    async def test_shows_embed_ephemeral(
        self, cog: InventoryCog, interaction: AsyncMock,
    ) -> None:
        """Default (public=False) should send the embed ephemerally."""
        session = _make_session()
        char = _make_character()
        inv = create_inventory()
        session.characters[USER_ID] = char
        session.inventories[USER_ID] = inv
        cog.bot.get_session.return_value = session

        await cog.inventory.callback(cog, interaction, public=False)

        call_kwargs = interaction.response.send_message.call_args[1]
        assert isinstance(call_kwargs["embed"], discord.Embed)
        assert call_kwargs["ephemeral"] is True

    @pytest.mark.asyncio()
    async def test_shows_embed_public(
        self, cog: InventoryCog, interaction: AsyncMock,
    ) -> None:
        """public=True should send the embed non-ephemerally."""
        session = _make_session()
        char = _make_character()
        inv = create_inventory()
        session.characters[USER_ID] = char
        session.inventories[USER_ID] = inv
        cog.bot.get_session.return_value = session

        await cog.inventory.callback(cog, interaction, public=True)

        call_kwargs = interaction.response.send_message.call_args[1]
        assert isinstance(call_kwargs["embed"], discord.Embed)
        assert call_kwargs["ephemeral"] is False


# ===========================================================================
# /equip
# ===========================================================================


class TestEquip:
    """Tests for the /equip command."""

    @pytest.mark.asyncio()
    async def test_no_session(
        self, cog: InventoryCog, interaction: AsyncMock,
    ) -> None:
        cog.bot.get_session.return_value = None

        await cog.equip.callback(cog, interaction, item="Longsword", slot="Main Hand")

        msg = interaction.response.send_message.call_args
        assert "Aucune session active" in msg[0][0]
        assert msg[1]["ephemeral"] is True

    @pytest.mark.asyncio()
    async def test_no_character(
        self, cog: InventoryCog, interaction: AsyncMock,
    ) -> None:
        session = _make_session()
        cog.bot.get_session.return_value = session

        await cog.equip.callback(cog, interaction, item="Longsword", slot="Main Hand")

        msg = interaction.response.send_message.call_args
        assert "pas de personnage" in str(msg)
        assert msg[1]["ephemeral"] is True

    @pytest.mark.asyncio()
    async def test_invalid_slot(
        self, cog: InventoryCog, interaction: AsyncMock,
    ) -> None:
        """Should reject an invalid equipment slot name."""
        session = _make_session()
        char = _make_character()
        inv = _make_inventory_with_sword()
        session.characters[USER_ID] = char
        session.inventories[USER_ID] = inv
        cog.bot.get_session.return_value = session

        await cog.equip.callback(cog, interaction, item="Longsword", slot="Nose")

        msg = interaction.response.send_message.call_args
        assert "Emplacement invalide" in msg[0][0]
        assert "Nose" in msg[0][0]
        assert msg[1]["ephemeral"] is True

    @pytest.mark.asyncio()
    async def test_item_not_found(
        self, cog: InventoryCog, interaction: AsyncMock,
    ) -> None:
        """Should error when item is not in inventory."""
        session = _make_session()
        char = _make_character()
        inv = create_inventory()  # empty
        session.characters[USER_ID] = char
        session.inventories[USER_ID] = inv
        cog.bot.get_session.return_value = session

        await cog.equip.callback(cog, interaction, item="Ghost Blade", slot="Main Hand")

        msg = interaction.response.send_message.call_args
        assert "Erreur" in msg[0][0]
        assert msg[1]["ephemeral"] is True

    @pytest.mark.asyncio()
    async def test_equip_success(
        self, cog: InventoryCog, interaction: AsyncMock,
    ) -> None:
        """Should equip the item and update AC."""
        session = _make_session()
        char = _make_character()
        inv = _make_inventory_with_sword()
        session.characters[USER_ID] = char
        session.inventories[USER_ID] = inv
        cog.bot.get_session.return_value = session

        await cog.equip.callback(cog, interaction, item="Longsword", slot="Main Hand")

        msg = interaction.response.send_message.call_args
        assert "Longsword" in msg[0][0]
        assert "Main Hand" in msg[0][0]
        assert msg[1]["ephemeral"] is True

        # Verify item is now equipped in session
        updated_inv = session.inventories[USER_ID]
        assert EquipmentSlot.MAIN_HAND in updated_inv.equipped
        assert updated_inv.equipped[EquipmentSlot.MAIN_HAND].name == "Longsword"

    @pytest.mark.asyncio()
    async def test_equip_incompatible_slot(
        self, cog: InventoryCog, interaction: AsyncMock,
    ) -> None:
        """Should error when item type is incompatible with slot."""
        session = _make_session()
        char = _make_character()
        inv = _make_inventory_with_sword()
        session.characters[USER_ID] = char
        session.inventories[USER_ID] = inv
        cog.bot.get_session.return_value = session

        # Weapon cannot go in Armor slot
        await cog.equip.callback(cog, interaction, item="Longsword", slot="Armor")

        msg = interaction.response.send_message.call_args
        assert "Erreur" in msg[0][0]
        assert msg[1]["ephemeral"] is True


# ===========================================================================
# /unequip
# ===========================================================================


class TestUnequip:
    """Tests for the /unequip command."""

    @pytest.mark.asyncio()
    async def test_no_session(
        self, cog: InventoryCog, interaction: AsyncMock,
    ) -> None:
        cog.bot.get_session.return_value = None

        await cog.unequip.callback(cog, interaction, slot="Main Hand")

        msg = interaction.response.send_message.call_args
        assert "Aucune session active" in msg[0][0]
        assert msg[1]["ephemeral"] is True

    @pytest.mark.asyncio()
    async def test_no_character(
        self, cog: InventoryCog, interaction: AsyncMock,
    ) -> None:
        session = _make_session()
        cog.bot.get_session.return_value = session

        await cog.unequip.callback(cog, interaction, slot="Main Hand")

        msg = interaction.response.send_message.call_args
        assert "pas de personnage" in str(msg)
        assert msg[1]["ephemeral"] is True

    @pytest.mark.asyncio()
    async def test_invalid_slot(
        self, cog: InventoryCog, interaction: AsyncMock,
    ) -> None:
        session = _make_session()
        char = _make_character()
        inv = create_inventory()
        session.characters[USER_ID] = char
        session.inventories[USER_ID] = inv
        cog.bot.get_session.return_value = session

        await cog.unequip.callback(cog, interaction, slot="Belly")

        msg = interaction.response.send_message.call_args
        assert "Emplacement invalide" in msg[0][0]
        assert msg[1]["ephemeral"] is True

    @pytest.mark.asyncio()
    async def test_empty_slot(
        self, cog: InventoryCog, interaction: AsyncMock,
    ) -> None:
        """Should error when trying to unequip from an empty slot."""
        session = _make_session()
        char = _make_character()
        inv = create_inventory()
        session.characters[USER_ID] = char
        session.inventories[USER_ID] = inv
        cog.bot.get_session.return_value = session

        await cog.unequip.callback(cog, interaction, slot="Main Hand")

        msg = interaction.response.send_message.call_args
        assert "Erreur" in msg[0][0]
        assert msg[1]["ephemeral"] is True

    @pytest.mark.asyncio()
    async def test_unequip_success(
        self, cog: InventoryCog, interaction: AsyncMock,
    ) -> None:
        """Should unequip an item and return it to the backpack."""
        session = _make_session()
        char = _make_character()
        # Build inventory with an equipped sword
        inv = _make_inventory_with_sword()
        from engine.inventory import equip_item
        inv = equip_item(inv, "Longsword", EquipmentSlot.MAIN_HAND)
        session.characters[USER_ID] = char
        session.inventories[USER_ID] = inv
        cog.bot.get_session.return_value = session

        await cog.unequip.callback(cog, interaction, slot="Main Hand")

        msg = interaction.response.send_message.call_args
        assert "Main Hand" in msg[0][0]
        assert "libere" in msg[0][0]
        assert msg[1]["ephemeral"] is True

        # Verify slot is empty and item is back in items
        updated_inv = session.inventories[USER_ID]
        assert EquipmentSlot.MAIN_HAND not in updated_inv.equipped
        assert any(i.name == "Longsword" for i in updated_inv.items)


# ===========================================================================
# /equip & /unequip during combat (audit H21)
# ===========================================================================


def _make_inventory_sword_equipped_dagger_in_pack() -> Inventory:
    """Longsword equipped MAIN_HAND, Dagger waiting in the backpack."""
    from engine.inventory import WeaponProperty, equip_item

    inv = _make_inventory_with_sword()
    inv = equip_item(inv, "Longsword", EquipmentSlot.MAIN_HAND)
    dagger = Weapon(
        name="Dagger",
        item_type=ItemType.WEAPON,
        weight=1.0,
        value_gp=2,
        damage_dice="1d4",
        damage_type=DamageType.PIERCING,
        weapon_category=WeaponCategory.SIMPLE_MELEE,
        properties=[WeaponProperty.FINESSE, WeaponProperty.LIGHT],
    )
    return add_item(inv, dagger)


def _combat_session(
    char: Character,
    inv: Inventory,
    *,
    player_turn: bool = True,
):
    """Session with an active combat; the PC combatant shares char + inv."""
    from engine.combat import CombatSide, CombatState, Combatant

    session = _make_session()
    session.characters[USER_ID] = char
    session.inventories[USER_ID] = inv

    enemy_char = _make_character("Gobelin")
    pc = Combatant(
        name=char.name, side=CombatSide.PLAYER,
        character=char, inventory=inv,
    )
    enemy = Combatant(
        name="Gobelin", side=CombatSide.ENEMY,
        character=enemy_char, inventory=create_inventory(),
    )
    combatants = [pc, enemy] if player_turn else [enemy, pc]
    session.combat_state = CombatState(
        combatants=combatants, current_turn_index=0, is_active=True,
    )
    return session, pc


class TestEquipDuringCombat:
    """In combat, /equip must respect the ActionValidator (audit H21)."""

    @pytest.mark.asyncio()
    async def test_weapon_swap_on_own_turn_succeeds(
        self, cog: InventoryCog, interaction: AsyncMock,
    ) -> None:
        char = _make_character()
        inv = _make_inventory_sword_equipped_dagger_in_pack()
        session, pc = _combat_session(char, inv, player_turn=True)
        cog.bot.get_session.return_value = session

        await cog.equip.callback(cog, interaction, item="Dagger", slot="Main Hand")

        assert inv.equipped[EquipmentSlot.MAIN_HAND].name == "Dagger"
        assert pc.action_budget.weapon_swapped_this_turn is True
        msg = interaction.response.send_message.call_args
        assert "Dagger" in msg[0][0]

    @pytest.mark.asyncio()
    async def test_swap_refused_off_turn(
        self, cog: InventoryCog, interaction: AsyncMock,
    ) -> None:
        char = _make_character()
        inv = _make_inventory_sword_equipped_dagger_in_pack()
        session, pc = _combat_session(char, inv, player_turn=False)
        cog.bot.get_session.return_value = session

        await cog.equip.callback(cog, interaction, item="Dagger", slot="Main Hand")

        assert inv.equipped[EquipmentSlot.MAIN_HAND].name == "Longsword"
        assert pc.action_budget.weapon_swapped_this_turn is False
        msg = interaction.response.send_message.call_args
        assert msg[1]["ephemeral"] is True

    @pytest.mark.asyncio()
    async def test_second_swap_same_turn_refused(
        self, cog: InventoryCog, interaction: AsyncMock,
    ) -> None:
        char = _make_character()
        inv = _make_inventory_sword_equipped_dagger_in_pack()
        session, pc = _combat_session(char, inv, player_turn=True)
        pc.action_budget.weapon_swapped_this_turn = True
        cog.bot.get_session.return_value = session

        await cog.equip.callback(cog, interaction, item="Dagger", slot="Main Hand")

        assert inv.equipped[EquipmentSlot.MAIN_HAND].name == "Longsword"

    @pytest.mark.asyncio()
    async def test_non_weapon_slot_refused_in_combat(
        self, cog: InventoryCog, interaction: AsyncMock,
    ) -> None:
        char = _make_character()
        inv = _make_inventory_sword_equipped_dagger_in_pack()
        session, _pc = _combat_session(char, inv, player_turn=True)
        cog.bot.get_session.return_value = session
        ac_before = char.ac

        await cog.equip.callback(cog, interaction, item="Dagger", slot="Armor")

        assert char.ac == ac_before
        msg = interaction.response.send_message.call_args
        assert "combat" in msg[0][0].lower()

    @pytest.mark.asyncio()
    async def test_equip_refused_while_action_lock_busy(
        self, cog: InventoryCog, interaction: AsyncMock,
    ) -> None:
        char = _make_character()
        inv = _make_inventory_sword_equipped_dagger_in_pack()
        session, _pc = _combat_session(char, inv, player_turn=True)
        cog.bot.get_session.return_value = session

        await session.action_lock.acquire()
        try:
            await cog.equip.callback(
                cog, interaction, item="Dagger", slot="Main Hand",
            )
        finally:
            session.action_lock.release()

        assert inv.equipped[EquipmentSlot.MAIN_HAND].name == "Longsword"
        msg = interaction.response.send_message.call_args
        assert "en cours" in msg[0][0]


class TestUnequipDuringCombat:
    @pytest.mark.asyncio()
    async def test_unequip_refused_in_combat(
        self, cog: InventoryCog, interaction: AsyncMock,
    ) -> None:
        char = _make_character()
        inv = _make_inventory_sword_equipped_dagger_in_pack()
        session, _pc = _combat_session(char, inv, player_turn=True)
        cog.bot.get_session.return_value = session

        await cog.unequip.callback(cog, interaction, slot="Main Hand")

        assert EquipmentSlot.MAIN_HAND in inv.equipped
        msg = interaction.response.send_message.call_args
        assert "combat" in msg[0][0].lower()
        assert msg[1]["ephemeral"] is True


# ===========================================================================
# /use_item
# ===========================================================================


class TestUseItem:
    """Tests for the /use_item command."""

    @pytest.mark.asyncio()
    async def test_no_session(
        self, cog: InventoryCog, interaction: AsyncMock,
    ) -> None:
        cog.bot.get_session.return_value = None

        await cog.use_item.callback(cog, interaction, item="Healing Potion")

        msg = interaction.response.send_message.call_args
        assert "Aucune session active" in msg[0][0]
        assert msg[1]["ephemeral"] is True

    @pytest.mark.asyncio()
    async def test_no_character(
        self, cog: InventoryCog, interaction: AsyncMock,
    ) -> None:
        session = _make_session()
        cog.bot.get_session.return_value = session

        await cog.use_item.callback(cog, interaction, item="Healing Potion")

        msg = interaction.response.send_message.call_args
        assert "pas de personnage" in str(msg)
        assert msg[1]["ephemeral"] is True

    @pytest.mark.asyncio()
    async def test_item_not_found(
        self, cog: InventoryCog, interaction: AsyncMock,
    ) -> None:
        """Should error when item is not in inventory."""
        session = _make_session()
        char = _make_character()
        inv = create_inventory()  # empty
        session.characters[USER_ID] = char
        session.inventories[USER_ID] = inv
        cog.bot.get_session.return_value = session

        await cog.use_item.callback(cog, interaction, item="Phoenix Down")

        msg = interaction.response.send_message.call_args
        assert "Erreur" in msg[0][0]
        assert msg[1]["ephemeral"] is True

    @pytest.mark.asyncio()
    async def test_healing_potion_heals_and_is_consumed(
        self, cog: InventoryCog, interaction: AsyncMock,
    ) -> None:
        """Drinking a potion must actually heal (audit H22) and show the roll."""
        session = _make_session()
        char = _make_character()
        char.hp = 1
        inv = _make_inventory_with_potion()
        session.characters[USER_ID] = char
        session.inventories[USER_ID] = inv
        cog.bot.get_session.return_value = session

        await cog.use_item.callback(cog, interaction, item="Healing Potion")

        # 2d4+2 → between 4 and 10 HP healed
        assert 5 <= char.hp <= 11
        # Potion had quantity=2, so 1 should remain
        updated_inv = session.inventories[USER_ID]
        potion = next(i for i in updated_inv.items if i.name == "Healing Potion")
        assert potion.quantity == 1
        # The dice roll is shown in an embed
        call_kwargs = interaction.response.send_message.call_args[1]
        embed = call_kwargs["embed"]
        assert isinstance(embed, discord.Embed)
        full_text = (embed.title or "") + (embed.description or "")
        assert "2d4+2" in full_text
        assert call_kwargs["ephemeral"] is False  # healing is public mechanics

    @pytest.mark.asyncio()
    async def test_healing_clamped_at_max_hp(
        self, cog: InventoryCog, interaction: AsyncMock,
    ) -> None:
        session = _make_session()
        char = _make_character()
        char.hp = char.max_hp
        inv = _make_inventory_with_potion()
        session.characters[USER_ID] = char
        session.inventories[USER_ID] = inv
        cog.bot.get_session.return_value = session

        await cog.use_item.callback(cog, interaction, item="Healing Potion")

        assert char.hp == char.max_hp

    @pytest.mark.asyncio()
    async def test_last_potion_removed_entirely(
        self, cog: InventoryCog, interaction: AsyncMock,
    ) -> None:
        session = _make_session()
        char = _make_character()
        char.hp = 1
        inv = _make_inventory_with_potion(quantity=1)
        session.characters[USER_ID] = char
        session.inventories[USER_ID] = inv
        cog.bot.get_session.return_value = session

        await cog.use_item.callback(cog, interaction, item="Healing Potion")

        updated_inv = session.inventories[USER_ID]
        assert not any(i.name == "Healing Potion" for i in updated_inv.items)

    @pytest.mark.asyncio()
    async def test_item_without_effect_refused_and_not_consumed(
        self, cog: InventoryCog, interaction: AsyncMock,
    ) -> None:
        """A sword is not a consumable — refuse instead of destroying it."""
        session = _make_session()
        char = _make_character()
        inv = _make_inventory_with_sword()
        session.characters[USER_ID] = char
        session.inventories[USER_ID] = inv
        cog.bot.get_session.return_value = session

        await cog.use_item.callback(cog, interaction, item="Longsword")

        updated_inv = session.inventories[USER_ID]
        assert any(i.name == "Longsword" for i in updated_inv.items)
        msg = interaction.response.send_message.call_args
        assert "effet" in msg[0][0].lower()
        assert msg[1]["ephemeral"] is True

    @pytest.mark.asyncio()
    async def test_use_item_refused_in_combat(
        self, cog: InventoryCog, interaction: AsyncMock,
    ) -> None:
        """In combat the action costs the turn — route players to the
        combat path instead of bypassing the action economy."""
        char = _make_character()
        char.hp = 1
        inv = _make_inventory_with_potion()
        session, pc = _combat_session(char, inv, player_turn=True)
        cog.bot.get_session.return_value = session

        await cog.use_item.callback(cog, interaction, item="Healing Potion")

        assert char.hp == 1  # nothing applied
        potion = next(i for i in inv.items if i.name == "Healing Potion")
        assert potion.quantity == 2  # nothing consumed
        assert pc.action_budget.action_used is False
        msg = interaction.response.send_message.call_args
        assert "combat" in msg[0][0].lower()

    @pytest.mark.asyncio()
    async def test_use_item_refused_when_lock_busy(
        self, cog: InventoryCog, interaction: AsyncMock,
    ) -> None:
        session = _make_session()
        char = _make_character()
        char.hp = 1
        inv = _make_inventory_with_potion()
        session.characters[USER_ID] = char
        session.inventories[USER_ID] = inv
        cog.bot.get_session.return_value = session

        await session.action_lock.acquire()
        try:
            await cog.use_item.callback(cog, interaction, item="Healing Potion")
        finally:
            session.action_lock.release()

        assert char.hp == 1
        msg = interaction.response.send_message.call_args
        assert "en cours" in msg[0][0]
