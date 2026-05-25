"""Headless driver for :class:`bot.views.character_setup_flow.CharacterSetupFlow`.

Drives every callback of the real character-creation view through fake
``discord.Interaction`` objects so scenario tests can exercise the
production flow end-to-end without spawning a Discord client. This is the
Lead 4 deliverable of the *Simulator hardening* chantier
(``tasks/todo.md``).

The driver matches the on-screen ordering documented in the view:

    IdentityModal.on_submit
      → _on_race_selected / _on_class_selected
      → transition_to(STATS) → _on_preset_stats | _on_random_stats
      → transition_to(SKILLS) → _on_skills_selected
      → transition_to(KIT_MOTIV) → _on_kit_selected → _on_motivation_selected
      → transition_to(REVIEW) → _on_confirm

The resulting :class:`engine.character.Character` is captured from the
``on_complete`` callback the view is constructed with and exposed via
:attr:`HeadlessCharacterSetupFlow.character`.

Usage
-----

>>> driver = HeadlessCharacterSetupFlow(user_id=1)
>>> character = await driver.run_full_flow(
...     name="Thorin",
...     race=Race.DWARF,
...     char_class=CharacterClass.FIGHTER,
...     skills=[Skill.ATHLETICS, Skill.PERCEPTION],
...     kit_name=get_starter_kits(CharacterClass.FIGHTER)[0].name,
...     motivation_key="Contract",
... )

Individual steps can also be driven manually for tests that probe a
specific transition::

    await driver.submit_identity("Aria", concept="")
    await driver.select_race(Race.HALFLING)
    ...
"""

from __future__ import annotations

from typing import Literal
from unittest.mock import AsyncMock, MagicMock

from bot.views.character_setup_flow import (
    CharacterSetupFlow,
    IdentityModal,
    SetupStep,
)
from engine.character import (
    Character,
    CharacterClass,
    Race,
    Skill,
)


class HeadlessCharacterSetupFlow:
    """Fluent driver over a real :class:`CharacterSetupFlow`.

    Builds a real flow under the hood. Each method fakes a fresh
    ``discord.Interaction`` whose ``response.edit_message`` and
    ``response.send_message`` are :class:`AsyncMock`\\ s, so the view's
    own calls succeed without touching Discord.

    The Character object the view assembles at the REVIEW step is
    surfaced via :attr:`character` after :meth:`confirm` is awaited.
    The kit name and motivation key forwarded to the production
    ``on_complete`` callback land on :attr:`kit_name` and
    :attr:`motivation_key` respectively.
    """

    def __init__(self, *, user_id: int, language: str = "fr") -> None:
        self.flow: CharacterSetupFlow = CharacterSetupFlow(
            user_id=user_id,
            language=language,
            on_complete=self._capture_complete,
        )
        self.character: Character | None = None
        self.kit_name: str | None = None
        self.motivation_key: str | None = None

    async def _capture_complete(
        self, character: Character, kit_name: str, motivation_key: str,
    ) -> None:
        """on_complete callback the view invokes from _on_confirm."""
        self.character = character
        self.kit_name = kit_name
        self.motivation_key = motivation_key

    @staticmethod
    def _fake_interaction() -> MagicMock:
        """Build a fresh fake interaction with async response methods.

        A new interaction per call mirrors Discord behaviour (each
        callback fires from a distinct interaction). The methods the
        view touches — ``response.send_message`` and
        ``response.edit_message`` — are :class:`AsyncMock`\\ s so the
        ``await`` succeeds silently.
        """
        inter = MagicMock()
        inter.response.send_message = AsyncMock()
        inter.response.edit_message = AsyncMock()
        return inter

    # ------------------------------------------------------------------
    # Step 1 — Identity (modal)
    # ------------------------------------------------------------------

    async def submit_identity(self, name: str, concept: str = "") -> None:
        """Drive the real IdentityModal.on_submit handler.

        Sets the underlying ``_value`` on each ``ui.TextInput`` (the
        attribute the public :attr:`TextInput.value` property reads from
        in discord.py 2.x) so the modal sees the desired strings, then
        awaits ``on_submit`` against a fake interaction.
        """
        modal = IdentityModal(parent_view=self.flow)
        # discord.ui.TextInput.value reads `self._value or ''`; setting
        # _value gives us a 1:1 simulation of "user typed X then Submit".
        modal.name._value = name
        modal.concept._value = concept
        await modal.on_submit(self._fake_interaction())

    # ------------------------------------------------------------------
    # Step 2 — Race + Class
    # ------------------------------------------------------------------

    async def select_race(self, race: Race) -> None:
        """Equivalent of picking ``race`` in the race ui.Select."""
        await self.flow._on_race_selected(
            self._fake_interaction(), [race.value],
        )

    async def select_class(self, char_class: CharacterClass) -> None:
        """Equivalent of picking ``char_class`` in the class ui.Select."""
        await self.flow._on_class_selected(
            self._fake_interaction(), [char_class.value],
        )

    # ------------------------------------------------------------------
    # Transitions between steps (the "Continuer" buttons in the view)
    # ------------------------------------------------------------------

    async def advance_to_stats(self) -> None:
        """Click the Continuer button at the end of RACE_CLASS."""
        await self.flow.transition_to(
            self._fake_interaction(), SetupStep.STATS,
        )

    async def advance_to_skills(self) -> None:
        """Click the Continuer button at the end of STATS."""
        await self.flow.transition_to(
            self._fake_interaction(), SetupStep.SKILLS,
        )

    async def advance_to_kit_motiv(self) -> None:
        """Click the Continuer button at the end of SKILLS."""
        await self.flow.transition_to(
            self._fake_interaction(), SetupStep.KIT_MOTIV,
        )

    async def advance_to_review(self) -> None:
        """Click the Continuer button at the end of KIT_MOTIV.

        This step materialises the preview Character via
        :func:`engine.character.create_character` inside
        :meth:`CharacterSetupFlow.transition_to`.
        """
        await self.flow.transition_to(
            self._fake_interaction(), SetupStep.REVIEW,
        )

    # ------------------------------------------------------------------
    # Step 3 — Stats
    # ------------------------------------------------------------------

    async def pick_preset_stats(self) -> None:
        """Click the class-preset button at STATS step."""
        await self.flow._on_preset_stats(self._fake_interaction())

    async def pick_random_stats(self) -> None:
        """Click the 4d6-drop-lowest button at STATS step."""
        await self.flow._on_random_stats(self._fake_interaction())

    # ------------------------------------------------------------------
    # Step 4 — Skills
    # ------------------------------------------------------------------

    async def select_skills(self, skills: list[Skill]) -> None:
        """Equivalent of picking ``skills`` in the multi-select.

        Note that the production select enforces ``min_values =
        max_values = CLASS_SKILL_CHOICES[class].choose`` — callers
        should pass exactly that many skills so the driver mirrors a
        valid submission.
        """
        await self.flow._on_skills_selected(
            self._fake_interaction(), [s.value for s in skills],
        )

    # ------------------------------------------------------------------
    # Step 5 — Kit + Motivation
    # ------------------------------------------------------------------

    async def select_kit(self, kit_name: str) -> None:
        """Equivalent of picking ``kit_name`` in the kit ui.Select."""
        await self.flow._on_kit_selected(
            self._fake_interaction(), [kit_name],
        )

    async def select_motivation(self, motivation_key: str) -> None:
        """Equivalent of picking ``motivation_key`` in the motiv ui.Select.

        Use one of :data:`bot.i18n.MOTIVATION_KEYS` (Contract,
        Personal, Curiosity, Conviction).
        """
        await self.flow._on_motivation_selected(
            self._fake_interaction(), [motivation_key],
        )

    # ------------------------------------------------------------------
    # Step 6 — Confirm
    # ------------------------------------------------------------------

    async def confirm(self) -> Character:
        """Click the Confirmer button — invokes the on_complete callback."""
        await self.flow._on_confirm(self._fake_interaction())
        if self.character is None:
            msg = (
                "on_complete was not invoked — the flow likely tripped a "
                "validation gate (missing fields?)"
            )
            raise RuntimeError(msg)
        return self.character

    # ------------------------------------------------------------------
    # Convenience: drive everything in one call
    # ------------------------------------------------------------------

    async def run_full_flow(
        self,
        *,
        name: str,
        race: Race,
        char_class: CharacterClass,
        skills: list[Skill],
        kit_name: str,
        motivation_key: str,
        concept: str = "",
        stats_method: Literal["preset", "random"] = "preset",
    ) -> Character:
        """Drive every step in order and return the final Character.

        ``stats_method="preset"`` picks
        :func:`engine.character.presets.get_class_preset` (deterministic)
        — the recommended default for scenario tests. ``"random"``
        triggers 4d6-drop-lowest; seed :func:`random.seed` upstream for
        reproducibility.
        """
        await self.submit_identity(name, concept)
        await self.select_race(race)
        await self.select_class(char_class)
        await self.advance_to_stats()
        if stats_method == "preset":
            await self.pick_preset_stats()
        elif stats_method == "random":
            await self.pick_random_stats()
        else:
            msg = f"Unknown stats_method: {stats_method!r}"
            raise ValueError(msg)
        await self.advance_to_skills()
        await self.select_skills(skills)
        await self.advance_to_kit_motiv()
        await self.select_kit(kit_name)
        await self.select_motivation(motivation_key)
        await self.advance_to_review()
        return await self.confirm()
