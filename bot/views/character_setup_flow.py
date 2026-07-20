"""Unified character setup flow — single auto-modifying view, 6 steps.

Replaces CharacterCreateView, StatAssignmentView, SkillSelectionView,
StarterGearView, MotivationView. State transitions edit the same message
via discord.Interaction.response.edit_message.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import IntEnum
from typing import TYPE_CHECKING, Any

import discord
from discord import TextStyle, ui

from bot.views.base import LoggedView

if TYPE_CHECKING:
    from engine.character import (
        AbilityScores,
        Character,
        CharacterClass,
        Race,
        Skill,
    )

# (rest of the implementation lands in B4-B10)


class SetupStep(IntEnum):
    """Stages of the unified character setup flow."""

    IDENTITY = 0
    RACE_CLASS = 1
    STATS = 2
    SKILLS = 3
    KIT_MOTIV = 4
    REVIEW = 5


class IdentityModal(ui.Modal, title="Ton aventurier"):
    """Captures name + concept in one submit."""

    name: ui.TextInput[Any] = ui.TextInput(
        label="Nom du personnage",
        placeholder="Ex: Thorin Forgefort",
        min_length=1,
        max_length=32,
        required=True,
    )
    concept: ui.TextInput[Any] = ui.TextInput(
        label="Concept (optionnel)",
        placeholder="Ex: Un voleur repenti cherchant la rédemption",
        max_length=100,
        required=False,
        style=TextStyle.paragraph,
    )

    def __init__(self, parent_view: CharacterSetupFlow) -> None:
        super().__init__()
        self.parent_view = parent_view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.parent_view.name = str(self.name.value)
        self.parent_view.concept = str(self.concept.value or "")
        # Open the rest of the flow in a NEW ephemeral message owned by the
        # player. We must not call transition_to here: a modal-submit
        # interaction's `edit_message` would target the lobby message (the
        # one whose button opened the modal), erasing the public lobby.
        self.parent_view.state = SetupStep.RACE_CLASS
        self.parent_view._build_race_class_components()
        await interaction.response.send_message(
            content="**Étape 2/6** — Choisis ta race et ta classe.",
            view=self.parent_view,
            ephemeral=True,
        )


class CharacterSetupFlow(LoggedView):
    """Stub — full implementation in tasks B4-B10."""

    timeout = 600.0  # 10 minutes for the whole flow

    def __init__(
        self,
        user_id: int,
        language: str,
        on_complete: Callable[[Character, str, str], Awaitable[None]],
        on_cancel: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        super().__init__(timeout=self.timeout)
        self.user_id = user_id
        self.language = language
        self._on_complete = on_complete
        # Fired when the player bails out (Annuler) or lets the flow expire,
        # so the lobby can move them out of CREATING. Optional: unit tests
        # build flows with no lobby behind them.
        self._on_cancel_callback = on_cancel
        self.state: SetupStep = SetupStep.IDENTITY
        # Accumulators (filled across steps)
        self.name: str | None = None
        self.concept: str | None = None
        self.race: Race | None = None
        self.char_class: CharacterClass | None = None
        self.ability_scores: AbilityScores | None = None
        self.skill_proficiencies: list[Skill] | None = None
        self.kit_name: str | None = None
        self.motivation_key: str | None = None
        # Built only at REVIEW step (preview, awaiting Confirm)
        self._preview_character: Character | None = None

    async def transition_to(
        self, interaction: discord.Interaction, next_step: SetupStep,
    ) -> None:
        """Rebuild components for next_step and edit the message."""
        self.state = next_step
        if next_step == SetupStep.RACE_CLASS:
            self._build_race_class_components()
            await interaction.response.edit_message(
                content="**Étape 2/6** — Choisis ta race et ta classe.",
                view=self,
            )
        elif next_step == SetupStep.STATS:
            self._build_stats_components()
            await interaction.response.edit_message(
                content=self._stats_status_text(),
                view=self,
            )
        elif next_step == SetupStep.SKILLS:
            self._build_skills_components()
            from engine.character.classes import CLASS_SKILL_CHOICES
            assert self.char_class is not None
            config = CLASS_SKILL_CHOICES[self.char_class]
            await interaction.response.edit_message(
                content=(
                    f"**Étape 4/6** — Choisis {config.choose} compétence"
                    f"{'s' if config.choose > 1 else ''} pour ton {self.char_class.value}."
                ),
                view=self,
            )
        elif next_step == SetupStep.KIT_MOTIV:
            self._build_kit_motiv_components()
            await interaction.response.edit_message(
                content="**Étape 5/6** — Choisis ton équipement et ta motivation.",
                view=self,
            )
        elif next_step == SetupStep.REVIEW:
            # Build the Character object NOW (preview, not yet committed)
            from engine.character import apply_racial_bonuses, create_character
            assert self.race is not None
            assert self.char_class is not None
            assert self.ability_scores is not None
            raw = self.ability_scores
            boosted = apply_racial_bonuses(raw, self.race)
            char = create_character(
                name=self.name or "Anonyme",
                race=self.race,
                char_class=self.char_class,
                ability_scores=boosted,
                skill_proficiencies=self.skill_proficiencies or [],
                concept=self.concept or "",
            )
            self._preview_character = char  # cached for confirm

            self._build_review_components()
            from bot.embeds.character_setup_v2 import build_setup_recap_embed
            embed = build_setup_recap_embed(
                char,
                self.kit_name or "",
                self.motivation_key or "",
                self.concept or "",
                self.language,
            )
            await interaction.response.edit_message(
                content="**Étape 6/6** — Vérifie ta fiche avant de la valider.",
                embed=embed,
                view=self,
            )
        else:
            raise ValueError(f"Cannot transition to {next_step} from external call")

    def _build_race_class_components(self) -> None:
        """Clear children and add race+class selects + Continuer button."""
        from engine.character import CharacterClass, Race

        from bot.i18n import CLASS_LABELS, RACE_LABELS, get_label

        self.clear_items()

        # Race select with descriptions (1-line trait per race)
        race_descriptions = {
            Race.HUMAN:    "Polyvalent, +1 à toutes les caractéristiques",
            Race.ELF:      "Agile, vision sombre, immunité au sommeil charme",
            Race.DWARF:    "Robuste, résistance aux poisons, +CON",
            Race.HALFLING: "Chanceux, petit, agile",
            Race.HALF_ORC: "Endurant, +STR / +CON, fureur du sang",
            Race.GNOME:    "Curieux, malin, résistance magique mentale",
            Race.TIEFLING: "Infernal, résistance au feu, +CHA",
        }
        race_options = [
            discord.SelectOption(
                label=get_label(RACE_LABELS, self.language, r.value),
                value=r.value,
                description=race_descriptions[r],
                default=(self.race == r),
            )
            for r in Race
        ]
        race_select: ui.Select[Any] = ui.Select(
            placeholder="Choisis ta race...",
            options=race_options,
            custom_id="setup_race",
        )

        async def race_callback(interaction: discord.Interaction) -> None:
            await self._on_race_selected(interaction, race_select.values)
        race_select.callback = race_callback
        self.add_item(race_select)

        # Class select with descriptions (role per class)
        class_descriptions = {
            CharacterClass.FIGHTER:   "Guerrier polyvalent, fort en combat rapproché",
            CharacterClass.BARBARIAN: "Berserker, encaisse et frappe fort",
            CharacterClass.WIZARD:    "Mage savant, sorts puissants",
            CharacterClass.CLERIC:    "Soigneur divin, soutien et combat",
            CharacterClass.ROGUE:     "Rusé, attaques sournoises, infiltration",
            CharacterClass.RANGER:    "Pisteur, arc et nature",
        }
        class_options = [
            discord.SelectOption(
                label=get_label(CLASS_LABELS, self.language, c.value),
                value=c.value,
                description=class_descriptions[c],
                default=(self.char_class == c),
            )
            for c in CharacterClass
        ]
        class_select: ui.Select[Any] = ui.Select(
            placeholder="Choisis ta classe...",
            options=class_options,
            custom_id="setup_class",
        )

        async def class_callback(interaction: discord.Interaction) -> None:
            await self._on_class_selected(interaction, class_select.values)
        class_select.callback = class_callback
        self.add_item(class_select)

        # Continue button
        continue_btn: ui.Button[Any] = ui.Button(
            label="Continuer",
            emoji="➡️",
            style=discord.ButtonStyle.success,
            disabled=not (self.race and self.char_class),
            custom_id="setup_race_class_continue",
        )

        async def continue_callback(interaction: discord.Interaction) -> None:
            await self.transition_to(interaction, SetupStep.STATS)
        continue_btn.callback = continue_callback
        self.add_item(continue_btn)

    async def _on_race_selected(
        self, interaction: discord.Interaction, values: list[str],
    ) -> None:
        from engine.character import Race
        self.race = Race(values[0])
        self._build_race_class_components()
        await interaction.response.edit_message(view=self)

    async def _on_class_selected(
        self, interaction: discord.Interaction, values: list[str],
    ) -> None:
        from engine.character import CharacterClass
        self.char_class = CharacterClass(values[0])
        self._build_race_class_components()
        await interaction.response.edit_message(view=self)

    def _refresh_continue_state(self) -> None:
        """Sync the disabled state of the Continuer button to current selections."""
        for child in self.children:
            if isinstance(child, ui.Button) and child.label and "Continuer" in child.label:
                child.disabled = not (self.race and self.char_class)

    def _stats_status_text(self) -> str:
        from engine.character.random_stats import CLASS_STAT_PRIORITY

        class_name = self.char_class.value if self.char_class else ""
        priority = (
            CLASS_STAT_PRIORITY[self.char_class] if self.char_class else []
        )
        primary = " · ".join(a.name for a in priority[:2])
        header = f"**Étape 3/6 — Stats {class_name}**  🔥 {primary}"

        if self.ability_scores is None:
            return f"{header}\n_Choisis une méthode :_"
        s = self.ability_scores
        return (
            f"{header}\n"
            f"```STR {s.STR:2d}  DEX {s.DEX:2d}  CON {s.CON:2d}\n"
            f"INT {s.INT:2d}  WIS {s.WIS:2d}  CHA {s.CHA:2d}```"
            f"_Confirme ou change de méthode._"
        )

    def _build_stats_components(self) -> None:
        self.clear_items()

        # Preset button
        preset_btn: ui.Button[Any] = ui.Button(
            label=f"Optimisé pour {self.char_class.value if self.char_class else ''}",
            emoji="✨",
            style=discord.ButtonStyle.primary,
            custom_id="setup_stats_preset",
        )
        preset_btn.callback = lambda interaction: self._on_preset_stats(interaction)
        self.add_item(preset_btn)

        # Random button
        random_btn: ui.Button[Any] = ui.Button(
            label="Aléatoire (4d6)",
            emoji="🎲",
            style=discord.ButtonStyle.secondary,
            custom_id="setup_stats_random",
        )
        random_btn.callback = lambda interaction: self._on_random_stats(interaction)
        self.add_item(random_btn)

        # Continue button (only enabled if scores chosen)
        continue_btn: ui.Button[Any] = ui.Button(
            label="Continuer",
            emoji="➡️",
            style=discord.ButtonStyle.success,
            disabled=(self.ability_scores is None),
            custom_id="setup_stats_continue",
        )
        continue_btn.callback = lambda interaction: self.transition_to(interaction, SetupStep.SKILLS)
        self.add_item(continue_btn)

    async def _on_preset_stats(self, interaction: discord.Interaction) -> None:
        from engine.character import Ability, AbilityScores
        from engine.character.presets import get_class_preset

        assert self.char_class is not None
        preset = get_class_preset(self.char_class)
        self.ability_scores = AbilityScores(
            STR=preset[Ability.STR], DEX=preset[Ability.DEX], CON=preset[Ability.CON],
            INT=preset[Ability.INT], WIS=preset[Ability.WIS], CHA=preset[Ability.CHA],
        )
        self._build_stats_components()
        await interaction.response.edit_message(content=self._stats_status_text(), view=self)

    async def _on_random_stats(self, interaction: discord.Interaction) -> None:
        from engine.character import Ability, AbilityScores
        from engine.character.random_stats import auto_assign_random, roll_4d6_drop_lowest

        assert self.char_class is not None
        rolls = roll_4d6_drop_lowest()
        assignment = auto_assign_random(self.char_class, rolls)
        self.ability_scores = AbilityScores(
            STR=assignment[Ability.STR], DEX=assignment[Ability.DEX], CON=assignment[Ability.CON],
            INT=assignment[Ability.INT], WIS=assignment[Ability.WIS], CHA=assignment[Ability.CHA],
        )
        self._build_stats_components()
        await interaction.response.edit_message(content=self._stats_status_text(), view=self)

    def _build_skills_components(self) -> None:
        from engine.character import SKILL_ABILITY
        from engine.character.classes import CLASS_SKILL_CHOICES

        skill_descriptions = {
            # Compact 1-line per skill — domain knowledge
            "Athletics":    "Force pour grimper, sauter, lutter",
            "Acrobatics":   "Dextérité pour équilibre, esquive",
            "Sleight of Hand": "Dextérité pour pickpocket, tour de main",
            "Stealth":      "Dextérité pour se cacher",
            "Arcana":       "Intelligence pour magie, créatures",
            "History":      "Intelligence pour évènements, royaumes",
            "Investigation": "Intelligence pour indices, déduction",
            "Nature":       "Intelligence pour terrains, plantes, animaux",
            "Religion":     "Intelligence pour dieux, rites",
            "Insight":      "Sagesse pour lire les intentions",
            "Medicine":     "Sagesse pour stabiliser, diagnostiquer",
            "Perception":   "Sagesse pour repérer, écouter",
            "Survival":     "Sagesse pour pister, s'orienter",
            "Animal Handling": "Sagesse pour calmer, monter",
            "Deception":    "Charisme pour mentir, déguiser",
            "Intimidation": "Charisme pour menacer",
            "Performance":  "Charisme pour divertir",
            "Persuasion":   "Charisme pour convaincre",
        }

        self.clear_items()
        assert self.char_class is not None
        config = CLASS_SKILL_CHOICES[self.char_class]
        options = [
            discord.SelectOption(
                label=f"{s.value} ({SKILL_ABILITY[s].name})",
                value=s.value,
                description=skill_descriptions.get(s.value, ""),
                default=bool(self.skill_proficiencies and s in self.skill_proficiencies),
            )
            for s in config.options
        ]
        select: ui.Select[Any] = ui.Select(
            placeholder=f"Choisis {config.choose} compétences...",
            options=options,
            min_values=config.choose,
            max_values=config.choose,
            custom_id="setup_skills",
        )

        async def cb(interaction: discord.Interaction) -> None:
            await self._on_skills_selected(interaction, select.values)
        select.callback = cb
        self.add_item(select)

        continue_btn: ui.Button[Any] = ui.Button(
            label="Continuer",
            emoji="➡️",
            style=discord.ButtonStyle.success,
            disabled=(not self.skill_proficiencies),
            custom_id="setup_skills_continue",
        )
        continue_btn.callback = lambda interaction: self.transition_to(interaction, SetupStep.KIT_MOTIV)
        self.add_item(continue_btn)

    async def _on_skills_selected(
        self, interaction: discord.Interaction, values: list[str],
    ) -> None:
        from engine.character import Skill
        self.skill_proficiencies = [Skill(v) for v in values]
        self._build_skills_components()
        await interaction.response.edit_message(view=self)

    def _build_kit_motiv_components(self) -> None:
        from engine.starter_gear import get_starter_kits

        from bot.i18n import MOTIVATION_KEYS, get_kit_label, get_motivation_label

        self.clear_items()
        assert self.char_class is not None
        kits = get_starter_kits(self.char_class)
        # Labels are localized for display; ``value`` stays the canonical
        # English kit name because the engine looks kits up by that key.
        kit_options = [
            discord.SelectOption(
                label=get_kit_label(self.language, k.name, "name"),
                value=k.name,
                description=(
                    get_kit_label(self.language, k.name, "description")
                    or k.description
                )[:100] or None,
                default=(self.kit_name == k.name),
            )
            for k in kits
        ]
        kit_select: ui.Select[Any] = ui.Select(
            placeholder="Choisis ton kit de départ...",
            options=kit_options,
            custom_id="setup_kit",
        )
        kit_select.callback = lambda interaction: self._on_kit_selected(interaction, kit_select.values)
        self.add_item(kit_select)

        motiv_options = [
            discord.SelectOption(
                label=get_motivation_label(self.language, m),
                value=m,
                default=(self.motivation_key == m),
            )
            for m in MOTIVATION_KEYS
        ]
        motiv_select: ui.Select[Any] = ui.Select(
            placeholder="Choisis ta motivation...",
            options=motiv_options,
            custom_id="setup_motivation",
        )
        motiv_select.callback = lambda interaction: self._on_motivation_selected(interaction, motiv_select.values)
        self.add_item(motiv_select)

        continue_btn: ui.Button[Any] = ui.Button(
            label="Continuer",
            emoji="➡️",
            style=discord.ButtonStyle.success,
            disabled=not (self.kit_name and self.motivation_key),
            custom_id="setup_kit_motiv_continue",
        )
        continue_btn.callback = lambda interaction: self.transition_to(interaction, SetupStep.REVIEW)
        self.add_item(continue_btn)

    async def _on_kit_selected(
        self, interaction: discord.Interaction, values: list[str],
    ) -> None:
        self.kit_name = values[0]
        self._build_kit_motiv_components()
        await interaction.response.edit_message(view=self)

    async def _on_motivation_selected(
        self, interaction: discord.Interaction, values: list[str],
    ) -> None:
        self.motivation_key = values[0]
        self._build_kit_motiv_components()
        await interaction.response.edit_message(view=self)

    def _build_review_components(self) -> None:
        self.clear_items()
        confirm_btn: ui.Button[Any] = ui.Button(
            label="Confirmer", emoji="✅",
            style=discord.ButtonStyle.success, custom_id="setup_confirm",
        )
        confirm_btn.callback = lambda interaction: self._on_confirm(interaction)
        self.add_item(confirm_btn)

        edit_btn: ui.Button[Any] = ui.Button(
            label="Recommencer", emoji="✏️",
            style=discord.ButtonStyle.secondary, custom_id="setup_restart",
        )
        edit_btn.callback = lambda interaction: self._on_restart(interaction)
        self.add_item(edit_btn)

        cancel_btn: ui.Button[Any] = ui.Button(
            label="Annuler", emoji="❌",
            style=discord.ButtonStyle.danger, custom_id="setup_cancel",
        )
        cancel_btn.callback = lambda interaction: self._on_cancel(interaction)
        self.add_item(cancel_btn)

    async def _on_confirm(self, interaction: discord.Interaction) -> None:
        """Persist the previewed character via on_complete callback."""
        char = self._preview_character
        if char is None:
            # Confirm reached without a built preview — a stale view, or a
            # REVIEW transition that failed. Never call on_complete with
            # None: it would persist a broken roster entry.
            await interaction.response.edit_message(
                content=(
                    "❌ La fiche n'a pas pu être finalisée. "
                    "Relance la création depuis le lobby."
                ),
                embed=None, view=None,
            )
            self.stop()
            return

        await self._on_complete(char, self.kit_name or "", self.motivation_key or "")
        self.stop()
        await interaction.response.edit_message(
            content=f"✅ **{char.name}** a rejoint la campagne ! Voir le lobby.",
            embed=None, view=None,
        )

    async def _on_restart(self, interaction: discord.Interaction) -> None:
        """Reset accumulators and go back to RACE_CLASS (keep name+concept)."""
        self.race = None
        self.char_class = None
        self.ability_scores = None
        self.skill_proficiencies = None
        self.kit_name = None
        self.motivation_key = None
        await self.transition_to(interaction, SetupStep.RACE_CLASS)

    async def _on_cancel(self, interaction: discord.Interaction) -> None:
        """Abort the flow. on_complete is NOT called, on_cancel is."""
        self.stop()
        # Answer the player first (3 s interaction deadline), then tell the
        # lobby — that second call edits the public message, not this one.
        await interaction.response.edit_message(
            content="❌ Création annulée. Tu peux relancer via le bouton _Rejoindre_ du lobby.",
            embed=None, view=None,
        )
        if self._on_cancel_callback is not None:
            await self._on_cancel_callback()

    async def on_timeout(self) -> None:
        """An expired flow is an abandon too — release the lobby slot."""
        if self._on_cancel_callback is not None:
            await self._on_cancel_callback()
