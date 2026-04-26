"""Tests for the unified character setup flow (modal + state machine)."""

import discord
from bot.views.character_setup_flow import IdentityModal


def test_identity_modal_has_two_text_inputs():
    modal = IdentityModal(parent_view=None)  # type: ignore[arg-type]
    text_inputs = [c for c in modal.children if isinstance(c, discord.ui.TextInput)]
    assert len(text_inputs) == 2


def test_identity_modal_name_required():
    modal = IdentityModal(parent_view=None)  # type: ignore[arg-type]
    name_field = next(c for c in modal.children if c.label.startswith("Nom"))  # type: ignore[union-attr]
    assert name_field.required is True
    assert name_field.max_length == 32


def test_identity_modal_concept_optional():
    modal = IdentityModal(parent_view=None)  # type: ignore[arg-type]
    concept_field = next(c for c in modal.children if "Concept" in c.label)  # type: ignore[union-attr]
    assert concept_field.required is False
    assert concept_field.max_length == 100
