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
