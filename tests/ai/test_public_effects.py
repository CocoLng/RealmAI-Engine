"""Unit tests for PublicEffects footer rendering."""

from ai.models import PublicEffects


def test_empty_footer_returns_none():
    assert PublicEffects().to_footer_text() is None
    assert PublicEffects().is_empty() is True


def test_hp_delta_negative():
    pe = PublicEffects(hp_delta={"Xavier": -5})
    text = pe.to_footer_text()
    assert text is not None
    assert "Xavier" in text
    assert "-5" in text


def test_hp_delta_positive_sign():
    pe = PublicEffects(hp_delta={"Elara": 3})
    assert "+3" in (pe.to_footer_text() or "")


def test_items_gained_and_lost():
    pe = PublicEffects(items_gained=["Potion"], items_lost=["Torche"])
    text = pe.to_footer_text() or ""
    assert "+ Potion" in text
    assert "- Torche" in text


def test_gold_delta():
    assert "+50 po" in (PublicEffects(gold_delta=50).to_footer_text() or "")
    assert "-10 po" in (PublicEffects(gold_delta=-10).to_footer_text() or "")


def test_location_change():
    assert "Crypte" in (
        PublicEffects(location_change="Crypte").to_footer_text() or ""
    )


def test_xp_and_level_up():
    pe = PublicEffects(xp_gained=120, level_up=True)
    text = pe.to_footer_text() or ""
    assert "+120 XP" in text
    assert "LEVEL UP" in text


def test_combined_footer_separator():
    pe = PublicEffects(
        hp_delta={"Xavier": -5},
        items_gained=["Potion"],
        location_change="Crypte",
    )
    text = pe.to_footer_text() or ""
    # three segments → two separators
    assert text.count("\u2022") == 2
