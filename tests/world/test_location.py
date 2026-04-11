from world.combat_trigger_def import CombatTriggerDef
from world.location import Location


def test_location_item_descriptions_default_empty():
    loc = Location(name="Crypte", description="Sombre.")
    assert loc.item_descriptions == {}


def test_location_item_descriptions_populated():
    loc = Location(
        name="Église",
        description="Vieille paroisse.",
        items_available=["Croix de fer"],
        item_descriptions={"Croix de fer": "Vieille croix de forge, noircie."},
    )
    assert loc.item_descriptions["Croix de fer"].startswith("Vieille")


def test_location_combat_triggers_default_empty_dict():
    loc = Location(name="Crypte", description="Sombre.")
    assert loc.combat_triggers == {}


def test_location_combat_triggers_populated():
    trigger = CombatTriggerDef(
        item_name="Urne scellée",
        spawn_npcs=["Spectre"],
        reveal_narration="Un spectre en jaillit.",
    )
    loc = Location(
        name="Crypte",
        description="Sombre.",
        combat_triggers={"Urne scellée": trigger},
    )
    assert "Urne scellée" in loc.combat_triggers
    assert loc.combat_triggers["Urne scellée"].spawn_npcs == ["Spectre"]
    assert loc.combat_triggers["Urne scellée"].consumed is False
