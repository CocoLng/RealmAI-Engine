"""Tests for bot embed builders."""

import discord
import pytest

from bot.embeds.character_embed import build_character_embed, build_party_card_embed
from bot.embeds.combat_embed import build_combat_embed
from bot.embeds.inventory_embed import build_inventory_embed
from ai.models import PublicEffects
from bot.embeds.narrative_embed import build_countdown_embed, build_narrative_embed
from engine.character import (
    AbilityScores,
    Character,
    CharacterClass,
    Race,
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
    Rarity,
    Weapon,
    WeaponCategory,
    add_item,
    create_inventory,
    equip_item,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def fighter() -> Character:
    """A level-1 Human Fighter."""
    return create_character(
        name="Thorin",
        race=Race.DWARF,
        char_class=CharacterClass.FIGHTER,
        ability_scores=AbilityScores(STR=16, DEX=12, CON=14, INT=10, WIS=13, CHA=8),
    )


@pytest.fixture()
def wizard() -> Character:
    """A level-1 Elf Wizard."""
    return create_character(
        name="Elara",
        race=Race.ELF,
        char_class=CharacterClass.WIZARD,
        ability_scores=AbilityScores(STR=8, DEX=14, CON=12, INT=16, WIS=10, CHA=13),
    )


@pytest.fixture()
def inventory_with_items(fighter) -> Inventory:
    """An inventory with gold, equipped sword, and backpack items."""
    inv = create_inventory()
    inv.gold = 50

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
    inv = add_item(inv, sword)
    inv = equip_item(inv, "Longsword", EquipmentSlot.MAIN_HAND)

    potion = Item(
        name="Healing Potion",
        item_type=ItemType.POTION,
        weight=0.5,
        value_gp=50,
        stackable=True,
        quantity=3,
    )
    inv = add_item(inv, potion)

    rope = Item(
        name="Rope (50ft)",
        item_type=ItemType.ADVENTURING_GEAR,
        weight=10.0,
        value_gp=1,
    )
    inv = add_item(inv, rope)

    return inv


@pytest.fixture()
def combat_state(fighter, wizard) -> CombatState:
    """A combat state with two combatants (no random initiative)."""
    c1 = Combatant(
        name="Thorin",
        side=CombatSide.PLAYER,
        character=fighter,
        inventory=create_inventory(),
        initiative=15,
    )
    c2_char = create_character(
        name="Goblin",
        race=Race.HUMAN,
        char_class=CharacterClass.FIGHTER,
        ability_scores=AbilityScores(STR=8, DEX=14, CON=10, INT=10, WIS=8, CHA=8),
    )
    c2 = Combatant(
        name="Goblin",
        side=CombatSide.ENEMY,
        character=c2_char,
        inventory=create_inventory(),
        initiative=10,
        conditions=[
            ActiveCondition(
                condition_type=ConditionType.POISONED,
                source="poison arrow",
                duration_rounds=3,
            ),
        ],
    )
    return CombatState(
        combatants=[c1, c2],
        round_number=2,
        current_turn_index=0,
    )


# ---------------------------------------------------------------------------
# Character embed
# ---------------------------------------------------------------------------


class TestCharacterEmbed:
    """Tests for build_character_embed."""

    def test_title_format(self, fighter):
        embed = build_character_embed(fighter)
        assert "Thorin" in embed.title
        assert "Dwarf" in embed.title
        assert "Fighter" in embed.title
        assert "Niv. 1" in embed.title

    def test_color_fighter(self, fighter):
        embed = build_character_embed(fighter)
        assert embed.color == discord.Color(0xCC0000)

    def test_color_wizard(self, wizard):
        embed = build_character_embed(wizard)
        assert embed.color == discord.Color(0x3366CC)

    def test_ability_score_fields(self, fighter):
        embed = build_character_embed(fighter)
        field_names = [f.name for f in embed.fields]
        for ability in ("STR", "DEX", "CON", "INT", "WIS", "CHA"):
            assert ability in field_names

    def test_ability_modifier_format(self, fighter):
        """STR 16 should show (+3)."""
        embed = build_character_embed(fighter)
        str_field = next(f for f in embed.fields if f.name == "STR")
        assert "16" in str_field.value
        assert "(+3)" in str_field.value

    def test_negative_modifier(self, fighter):
        """CHA 8 should show (-1)."""
        embed = build_character_embed(fighter)
        cha_field = next(f for f in embed.fields if f.name == "CHA")
        assert "8" in cha_field.value
        assert "(-1)" in cha_field.value

    def test_hp_ac_proficiency_fields(self, fighter):
        embed = build_character_embed(fighter)
        field_names = [f.name for f in embed.fields]
        assert "HP" in field_names
        assert "AC" in field_names
        assert "Proficiency" in field_names

    def test_saving_throws_field(self, fighter):
        embed = build_character_embed(fighter)
        saves_field = next(f for f in embed.fields if f.name == "Saving Throws")
        assert "STR" in saves_field.value
        assert "CON" in saves_field.value

    def test_footer_xp_and_hit_die(self, fighter):
        embed = build_character_embed(fighter)
        assert "XP: 0" in embed.footer.text
        assert "Fighter" in embed.footer.text
        assert "1d10" in embed.footer.text

    def test_field_count(self, fighter):
        """6 abilities + HP + AC + Proficiency + Saving Throws + Features = 11 fields."""
        embed = build_character_embed(fighter)
        assert len(embed.fields) == 11


# ---------------------------------------------------------------------------
# Inventory embed
# ---------------------------------------------------------------------------


class TestInventoryEmbed:
    """Tests for build_inventory_embed."""

    def test_title(self, fighter, inventory_with_items):
        embed = build_inventory_embed(inventory_with_items, fighter)
        assert embed.title == "Inventaire de Thorin"

    def test_color(self, fighter, inventory_with_items):
        embed = build_inventory_embed(inventory_with_items, fighter)
        assert embed.color == discord.Color(0xDAA520)

    def test_gold_field(self, fighter, inventory_with_items):
        embed = build_inventory_embed(inventory_with_items, fighter)
        gold_field = next(f for f in embed.fields if f.name == "Or")
        assert "50 po" in gold_field.value

    def test_weight_field(self, fighter, inventory_with_items):
        embed = build_inventory_embed(inventory_with_items, fighter)
        weight_field = next(f for f in embed.fields if f.name == "Poids")
        assert "lb" in weight_field.value

    def test_equipped_field(self, fighter, inventory_with_items):
        embed = build_inventory_embed(inventory_with_items, fighter)
        equip_field = next(f for f in embed.fields if f.name == "Equipe")
        assert "Main Hand" in equip_field.value
        assert "Longsword" in equip_field.value

    def test_backpack_field(self, fighter, inventory_with_items):
        embed = build_inventory_embed(inventory_with_items, fighter)
        backpack_field = next(f for f in embed.fields if f.name == "Sac a dos")
        assert "Healing Potion x3" in backpack_field.value
        assert "Rope (50ft)" in backpack_field.value

    def test_no_attunement_field_when_empty(self, fighter, inventory_with_items):
        embed = build_inventory_embed(inventory_with_items, fighter)
        field_names = [f.name for f in embed.fields]
        assert not any("Harmonise" in n for n in field_names)

    def test_attunement_field_when_present(self, fighter):
        """Manually build an inventory with an attuned item."""
        magic_ring = Item(
            name="Ring of Protection",
            item_type=ItemType.ADVENTURING_GEAR,
            weight=0.0,
            requires_attunement=True,
            magical=True,
            rarity=Rarity.RARE,
        )
        inv = Inventory(
            items=[magic_ring],
            attuned=[magic_ring],
            gold=0,
        )
        embed = build_inventory_embed(inv, fighter)
        field_names = [f.name for f in embed.fields]
        assert any("Harmonise" in n for n in field_names)
        attuned_field = next(f for f in embed.fields if "Harmonise" in f.name)
        assert "Ring of Protection" in attuned_field.value
        assert "1/3" in attuned_field.name

    def test_empty_inventory(self, fighter):
        inv = create_inventory()
        embed = build_inventory_embed(inv, fighter)
        assert embed.title == "Inventaire de Thorin"
        # Gold and weight fields always present
        field_names = [f.name for f in embed.fields]
        assert "Or" in field_names
        assert "Poids" in field_names

    def test_field_count_with_equipped_and_backpack(self, fighter, inventory_with_items):
        """Or + Poids + Equipe + Sac a dos = 4 fields (no attunement)."""
        embed = build_inventory_embed(inventory_with_items, fighter)
        assert len(embed.fields) == 4


# ---------------------------------------------------------------------------
# Combat embed
# ---------------------------------------------------------------------------


class TestCombatEmbed:
    """Tests for build_combat_embed."""

    def test_title(self, combat_state):
        embed = build_combat_embed(combat_state)
        assert embed.title == "Combat — Round 2"

    def test_color(self, combat_state):
        embed = build_combat_embed(combat_state)
        assert embed.color == discord.Color(0xCC0000)

    def test_active_combatant_marker(self, combat_state):
        embed = build_combat_embed(combat_state)
        assert "> **Thorin**" in embed.description

    def test_inactive_combatant_no_marker(self, combat_state):
        embed = build_combat_embed(combat_state)
        # Goblin should not have the > marker at the line start
        lines = embed.description.split("\n")
        goblin_line = next(line for line in lines if "Goblin" in line)
        assert not goblin_line.startswith(">")

    def test_initiative_shown(self, combat_state):
        embed = build_combat_embed(combat_state)
        assert "(15)" in embed.description
        assert "(10)" in embed.description

    def test_hp_bar_present(self, combat_state):
        embed = build_combat_embed(combat_state)
        # Should contain the block characters
        assert "\u2588" in embed.description or "\u2591" in embed.description

    def test_conditions_shown(self, combat_state):
        embed = build_combat_embed(combat_state)
        assert "Poisoned" in embed.description

    def test_footer_active_combatant(self, combat_state):
        embed = build_combat_embed(combat_state)
        assert embed.footer.text == "Tour de: Thorin"

    def test_no_fields(self, combat_state):
        """Combat embed uses description, not fields."""
        embed = build_combat_embed(combat_state)
        assert len(embed.fields) == 0


# ---------------------------------------------------------------------------
# Narrative embed
# ---------------------------------------------------------------------------


class TestNarrativeEmbed:
    """Tests for build_narrative_embed."""

    def test_description(self):
        embed = build_narrative_embed(
            narrative="The sword cleaves through the goblin's armor.",
        )
        assert "The sword cleaves" in embed.description

    def test_no_fields(self):
        embed = build_narrative_embed(narrative="text")
        assert len(embed.fields) == 0

    def test_no_footer_when_public_effects_none(self):
        embed = build_narrative_embed(narrative="text")
        assert embed.footer.text is None or embed.footer.text == ""

    def test_no_footer_when_public_effects_empty(self):
        embed = build_narrative_embed(
            narrative="text", public_effects=PublicEffects(),
        )
        assert embed.footer.text is None or embed.footer.text == ""

    def test_footer_from_public_effects(self):
        pe = PublicEffects(hp_delta={"Xavier": -5}, items_gained=["Potion"])
        embed = build_narrative_embed(narrative="text", public_effects=pe)
        assert embed.footer.text is not None
        assert "Xavier" in embed.footer.text
        assert "Potion" in embed.footer.text

    def test_footer_override_wins(self):
        pe = PublicEffects(hp_delta={"A": -1})
        embed = build_narrative_embed(
            narrative="text",
            public_effects=pe,
            footer_override="custom",
        )
        assert embed.footer.text == "custom"

    def test_no_mechanics_field_ever(self):
        """Regression: never render a field named 'Mecaniques'."""
        pe = PublicEffects(hp_delta={"X": -1})
        embed = build_narrative_embed(narrative="n", public_effects=pe)
        assert not any(f.name == "Mecaniques" for f in embed.fields)

    def test_default_tone_color(self):
        embed = build_narrative_embed(narrative="x")
        assert embed.color == discord.Color(0xDAA520)

    def test_tense_tone_color(self):
        embed = build_narrative_embed(narrative="x", tone="tense")
        assert embed.color == discord.Color(0xCC0000)

    def test_humorous_tone_color(self):
        embed = build_narrative_embed(narrative="x", tone="humorous")
        assert embed.color == discord.Color(0x339933)

    def test_somber_tone_color(self):
        embed = build_narrative_embed(narrative="x", tone="somber")
        assert embed.color == discord.Color(0x663399)

    def test_unknown_tone_falls_back_to_default(self):
        embed = build_narrative_embed(narrative="x", tone="unknown")
        assert embed.color == discord.Color(0xDAA520)

    # --- NPC dialogue separation ---

    def test_no_author_when_no_npc(self):
        embed = build_narrative_embed(narrative="x")
        assert embed.author.name is None or embed.author.name == ""

    def test_no_field_when_no_npc(self):
        embed = build_narrative_embed(narrative="x")
        assert len(embed.fields) == 0

    def test_author_set_when_npc_dialogue(self):
        embed = build_narrative_embed(
            narrative="He leans forward.",
            npc_name="Thibault",
            npc_dialogue="The village hides many secrets.",
        )
        assert embed.author.name is not None
        assert "Thibault" in embed.author.name

    def test_dialogue_field_when_npc_dialogue(self):
        embed = build_narrative_embed(
            narrative="He leans forward.",
            npc_name="Thibault",
            npc_dialogue="The village hides many secrets.",
        )
        assert len(embed.fields) == 1
        assert "Thibault" in embed.fields[0].name
        assert "village hides many secrets" in embed.fields[0].value

    def test_dialogue_field_is_italic(self):
        embed = build_narrative_embed(
            narrative="n",
            npc_name="Elie",
            npc_dialogue="Approche.",
        )
        assert embed.fields[0].value.startswith("*")
        assert embed.fields[0].value.endswith("*")


# ---------------------------------------------------------------------------
# Countdown embed
# ---------------------------------------------------------------------------


class TestCountdownEmbed:
    """Tests for build_countdown_embed."""

    def test_step_3_color_gold(self):
        embed = build_countdown_embed(3, "My Campaign")
        assert embed.color == discord.Color(0xDAA520)

    def test_step_2_color_orange(self):
        embed = build_countdown_embed(2, "My Campaign")
        assert embed.color == discord.Color(0xCC7000)

    def test_step_1_color_red(self):
        embed = build_countdown_embed(1, "My Campaign")
        assert embed.color == discord.Color(0xCC0000)

    def test_title_contains_step_number(self):
        for step in (3, 2, 1):
            embed = build_countdown_embed(step, "Test")
            assert str(step) in embed.title

    def test_title_uses_styled_brackets(self):
        embed = build_countdown_embed(3, "Test")
        assert "\u300c" in embed.title
        assert "\u300d" in embed.title

    def test_description_is_italic(self):
        embed = build_countdown_embed(3, "Test")
        assert embed.description.startswith("*")
        assert embed.description.endswith("*")

    def test_footer_is_campaign_name(self):
        embed = build_countdown_embed(3, "Épopée Dorée")
        assert embed.footer.text == "Épopée Dorée"

    def test_french_descriptions(self):
        embed3 = build_countdown_embed(3, "C", language="fr")
        assert "aventuriers" in embed3.description

        embed2 = build_countdown_embed(2, "C", language="fr")
        assert "destins" in embed2.description

        embed1 = build_countdown_embed(1, "C", language="fr")
        assert "commence" in embed1.description


# ---------------------------------------------------------------------------
# Party card embed
# ---------------------------------------------------------------------------


class TestPartyCardEmbed:
    """Tests for build_party_card_embed."""

    def test_title_format(self, fighter):
        embed = build_party_card_embed(fighter, "PlayerOne")
        assert "Thorin" in embed.title
        assert "Nain" in embed.title  # Dwarf → Nain in French
        assert "Guerrier" in embed.title  # Fighter → Guerrier

    def test_description_contains_level_hp_ac(self, fighter):
        embed = build_party_card_embed(fighter, "PlayerOne")
        assert "Niveau" in embed.description
        assert "PV" in embed.description
        assert "CA" in embed.description

    def test_color_matches_class(self, fighter):
        embed = build_party_card_embed(fighter, "P")
        assert embed.color == discord.Color(0xCC0000)  # Fighter red

    def test_wizard_color(self, wizard):
        embed = build_party_card_embed(wizard, "P")
        assert embed.color == discord.Color(0x3366CC)  # Wizard blue

    def test_footer_is_member_name(self, fighter):
        embed = build_party_card_embed(fighter, "JoueurUn")
        assert embed.footer.text == "JoueurUn"

    def test_ability_scores_field_exists(self, fighter):
        embed = build_party_card_embed(fighter, "P")
        assert len(embed.fields) == 1

    def test_ability_scores_in_code_block(self, fighter):
        embed = build_party_card_embed(fighter, "P")
        field_value = embed.fields[0].value
        assert "```" in field_value

    def test_french_ability_labels(self, fighter):
        embed = build_party_card_embed(fighter, "P", language="fr")
        field_value = embed.fields[0].value
        assert "FOR" in field_value  # STR → FOR in French
        assert "SAG" in field_value  # WIS → SAG in French

    def test_ability_scores_contain_modifiers(self, fighter):
        embed = build_party_card_embed(fighter, "P")
        field_value = embed.fields[0].value
        # STR 16 → (+3)
        assert "(+3)" in field_value
        # CHA 8 → (-1)
        assert "(-1)" in field_value


# ---------------------------------------------------------------------------
# State embed
# ---------------------------------------------------------------------------


class TestStateEmbed:
    """Tests for build_state_embed (question responses)."""

    def test_state_embed_color_is_blue(self):
        from bot.embeds.narrative_embed import build_state_embed
        embed = build_state_embed(
            narrative="You see a cathedral.",
            location_name="Place de la Cathédrale",
            items=["Autel de pierre"],
            npcs=["Père Aldric"],
            exits=["Ruelle nord"],
        )
        assert embed.color == discord.Color(0x4A90D9)

    def test_state_embed_has_title(self):
        from bot.embeds.narrative_embed import build_state_embed
        embed = build_state_embed(
            narrative="You see a cathedral.",
            location_name="Place de la Cathédrale",
            items=[], npcs=[], exits=[],
        )
        assert embed.title is not None
        assert "Place de la Cathédrale" in embed.title

    def test_state_embed_has_fields(self):
        from bot.embeds.narrative_embed import build_state_embed
        embed = build_state_embed(
            narrative="You observe.",
            location_name="Barrier",
            items=["Lever", "Sand bag"],
            npcs=["Guard"],
            exits=["North gate", "South gate"],
        )
        field_names = [f.name for f in embed.fields]
        assert any("Objets" in n or "Items" in n for n in field_names)
        assert any("PNJ" in n or "NPC" in n for n in field_names)
        assert any("Sorties" in n or "Exits" in n for n in field_names)

    def test_state_embed_omits_empty_sections(self):
        from bot.embeds.narrative_embed import build_state_embed
        embed = build_state_embed(
            narrative="Nothing here.",
            location_name="Empty Room",
            items=[], npcs=[], exits=["Door"],
        )
        field_names = [f.name for f in embed.fields]
        assert not any("Objets" in n or "Items" in n for n in field_names)
        assert not any("PNJ" in n or "NPC" in n for n in field_names)

    def test_state_embed_shows_beat_info(self):
        from bot.embeds.narrative_embed import build_state_embed
        embed = build_state_embed(
            narrative="You look around.",
            location_name="Barrier",
            items=[], npcs=[], exits=[],
            beat_title="Le Mur qui Soupire",
        )
        assert any("Mur qui Soupire" in (f.value or "") for f in embed.fields)
