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


def _make_inventory_with_potion() -> Inventory:
    """Create an inventory containing a Healing Potion."""
    inv = create_inventory()
    potion = Item(
        name="Healing Potion",
        item_type=ItemType.POTION,
        weight=0.5,
        value_gp=50,
        description="Heals 2d4+2 hit points.",
        stackable=True,
        quantity=2,
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
    async def test_use_item_success(
        self, cog: InventoryCog, interaction: AsyncMock,
    ) -> None:
        """Should remove the item from inventory on use."""
        session = _make_session()
        char = _make_character()
        inv = _make_inventory_with_potion()
        session.characters[USER_ID] = char
        session.inventories[USER_ID] = inv
        cog.bot.get_session.return_value = session

        await cog.use_item.callback(cog, interaction, item="Healing Potion")

        msg = interaction.response.send_message.call_args
        assert "Healing Potion" in msg[0][0]
        assert "utilise" in msg[0][0]
        assert msg[1]["ephemeral"] is True

        # Potion had quantity=2, so 1 should remain
        updated_inv = session.inventories[USER_ID]
        potion = next(i for i in updated_inv.items if i.name == "Healing Potion")
        assert potion.quantity == 1

    @pytest.mark.asyncio()
    async def test_use_last_item_removes_it(
        self, cog: InventoryCog, interaction: AsyncMock,
    ) -> None:
        """Using the last of a non-stackable item should remove it entirely."""
        session = _make_session()
        char = _make_character()
        inv = _make_inventory_with_sword()  # single non-stackable item
        session.characters[USER_ID] = char
        session.inventories[USER_ID] = inv
        cog.bot.get_session.return_value = session

        await cog.use_item.callback(cog, interaction, item="Longsword")

        msg = interaction.response.send_message.call_args
        assert "Longsword" in msg[0][0]
        assert "utilise" in msg[0][0]

        updated_inv = session.inventories[USER_ID]
        assert not any(i.name == "Longsword" for i in updated_inv.items)
