"""Tests for memory/models.py — Pydantic model validation."""

from memory.models import (
    CharacterSummary,
    CombatSummary,
    CompressedSummary,
    ContextBudget,
    ExchangeRole,
    GameStateSummary,
    NarrativeExchange,
    SemanticDocument,
    SemanticDocumentType,
)


class TestCharacterSummary:
    def test_create_minimal(self) -> None:
        cs = CharacterSummary(
            name="Thorin", race="Dwarf", char_class="Fighter",
            level=5, hp=35, max_hp=40, ac=16,
        )
        assert cs.name == "Thorin"
        assert cs.conditions == []

    def test_with_conditions(self) -> None:
        cs = CharacterSummary(
            name="Thorin", race="Dwarf", char_class="Fighter",
            level=5, hp=35, max_hp=40, ac=16,
            conditions=["Poisoned", "Prone"],
        )
        assert cs.conditions == ["Poisoned", "Prone"]


class TestCombatSummary:
    def test_defaults(self) -> None:
        cs = CombatSummary()
        assert cs.is_active is False
        assert cs.round_number == 0
        assert cs.current_turn is None
        assert cs.combatants == []

    def test_active_combat(self) -> None:
        char = CharacterSummary(
            name="Goblin", race="Goblin", char_class="",
            level=1, hp=4, max_hp=7, ac=13,
        )
        cs = CombatSummary(
            is_active=True, round_number=3,
            current_turn="Thorin", combatants=[char],
        )
        assert cs.is_active is True
        assert len(cs.combatants) == 1


class TestGameStateSummary:
    def test_minimal(self) -> None:
        gss = GameStateSummary(campaign_name="Lost Mines")
        assert gss.campaign_name == "Lost Mines"
        assert gss.current_location is None
        assert gss.player_characters == []
        assert gss.combat is None

    def test_full(self) -> None:
        gss = GameStateSummary(
            campaign_name="Lost Mines",
            current_location="Neverwinter",
            location_description="A bustling city",
            nearby_npcs=["Gundren"],
            active_quests=["Find the Lost Mine"],
            inventory_highlights=["Healing Potion x3"],
        )
        assert gss.nearby_npcs == ["Gundren"]


class TestNarrativeExchange:
    def test_create(self) -> None:
        ex = NarrativeExchange(
            campaign_id="c1", role=ExchangeRole.PLAYER,
            content="I attack the goblin.", interaction_number=1,
        )
        assert ex.campaign_id == "c1"
        assert ex.role == ExchangeRole.PLAYER
        assert ex.id  # auto-generated UUID

    def test_all_roles(self) -> None:
        for role in ExchangeRole:
            ex = NarrativeExchange(
                campaign_id="c1", role=role,
                content="test", interaction_number=1,
            )
            assert ex.role == role


class TestCompressedSummary:
    def test_create(self) -> None:
        cs = CompressedSummary(
            campaign_id="c1", summary_text="The party arrived.",
            start_interaction=1, end_interaction=20,
        )
        assert cs.start_interaction == 1
        assert cs.end_interaction == 20
        assert cs.id  # auto-generated UUID


class TestSemanticDocument:
    def test_create(self) -> None:
        sd = SemanticDocument(
            campaign_id="c1", doc_type=SemanticDocumentType.NPC_SHEET,
            content="Gundren is a dwarf prospector.",
            metadata={"npc_name": "Gundren"},
        )
        assert sd.doc_type == SemanticDocumentType.NPC_SHEET
        assert sd.metadata["npc_name"] == "Gundren"

    def test_all_doc_types(self) -> None:
        for dt in SemanticDocumentType:
            sd = SemanticDocument(
                campaign_id="c1", doc_type=dt, content="test",
            )
            assert sd.doc_type == dt


class TestContextBudget:
    def test_defaults(self) -> None:
        cb = ContextBudget()
        assert cb.layer1_max == 450
        assert cb.layer2_max == 700
        assert cb.layer3_max == 400
        assert cb.layer4_max == 350
        assert cb.total_max == 2500

    def test_custom(self) -> None:
        cb = ContextBudget(layer1_max=300, total_max=1500)
        assert cb.layer1_max == 300
        assert cb.total_max == 1500
