from engine.character import AbilityScores, Race
from world.npc import NPC, DialogueExchange, NPCDisposition


def _base_kwargs(name: str = "Test") -> dict:
    return dict(
        name=name,
        race=Race.HUMAN,
        level=1,
        ability_scores=AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10),
        hp=10,
        max_hp=10,
        ac=10,
        disposition=NPCDisposition.NEUTRAL,
    )


def test_npc_new_fields_default_empty():
    npc = NPC(**_base_kwargs())
    assert npc.secrets == []
    assert npc.knowledge == []
    assert npc.dialogue_history == []


def test_dialogue_exchange_model():
    ex = DialogueExchange(
        player_said="Bonjour",
        npc_said="Salutations, voyageur.",
        revealed=["village name: Valombre"],
    )
    assert ex.player_said == "Bonjour"
    assert "Valombre" in ex.revealed[0]


def test_npc_dialogue_history_round_trip():
    npc = NPC(
        **_base_kwargs(),
        secrets=["A pact was made."],
        knowledge=["The cathedral was built in 1187."],
        dialogue_history=[
            DialogueExchange(
                player_said="hi",
                npc_said="hello",
                revealed=[],
            ),
        ],
    )
    assert npc.secrets == ["A pact was made."]
    assert len(npc.dialogue_history) == 1
    assert npc.dialogue_history[0].npc_said == "hello"
