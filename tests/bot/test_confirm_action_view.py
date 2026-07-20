"""Tests de la vue de confirmation basse confiance (Oui / Reformuler)."""

from __future__ import annotations

import pytest

from ai.models import InterpretedAction
from bot.views.confirm_action_view import (
    ConfirmActionView,
    build_confirm_embed,
    describe_action,
)
from engine.validators import ActionType


def _action(**overrides: object) -> InterpretedAction:
    base: dict[str, object] = {
        "action_type": ActionType.IMPROVISE,
        "actor_name": "Aldric",
        "raw_input": "je danse",
        "confidence": 0.3,
    }
    base.update(overrides)
    return InterpretedAction(**base)  # type: ignore[arg-type]


class TestDescribeAction:
    @pytest.mark.parametrize(
        ("kwargs", "expected_fr"),
        [
            (
                {"action_type": ActionType.ATTACK, "target_name": "Gobelin"},
                "Attaque sur Gobelin",
            ),
            (
                {"action_type": ActionType.MOVE, "target_name": "Ruelle nord"},
                "Déplacement vers Ruelle nord",
            ),
            (
                {"action_type": ActionType.TALK, "target_name": "Père Aldric"},
                "Parler à Père Aldric",
            ),
            (
                {"action_type": ActionType.PICKUP, "target_name": "Clé"},
                "Ramasser Clé",
            ),
            (
                {
                    "action_type": ActionType.IMPROVISE,
                    "improvise_description": "escalader le mur",
                },
                "Improvisation : escalader le mur",
            ),
        ],
    )
    def test_french_summaries(self, kwargs: dict, expected_fr: str) -> None:
        assert describe_action(_action(**kwargs), "fr") == expected_fr

    def test_english_attack(self) -> None:
        action = _action(action_type=ActionType.ATTACK, target_name="Goblin")
        assert describe_action(action, "en") == "Attack Goblin"

    def test_generic_fallback_uses_enum_value(self) -> None:
        """Les types sans gabarit dédié restent lisibles : valeur + cible."""
        action = _action(action_type=ActionType.DEFEND, target_name=None)
        assert describe_action(action, "fr") == "Defend"

    def test_improvise_without_description_falls_back_to_raw_input(self) -> None:
        action = _action(improvise_description=None, raw_input="je tente un truc")
        assert describe_action(action, "fr") == "Improvisation : je tente un truc"


class TestConfirmActionView:
    def test_has_two_buttons_and_starts_unconfirmed(self) -> None:
        view = ConfirmActionView(author_id=42)
        labels = {item.label for item in view.children}
        assert labels == {"Oui", "Reformuler"}
        assert view.confirmed is False

    def test_embed_contains_summary(self) -> None:
        action = _action(action_type=ActionType.ATTACK, target_name="Gobelin")
        embed = build_confirm_embed(action, "fr")
        assert "Attaque sur Gobelin" in (embed.description or "")
