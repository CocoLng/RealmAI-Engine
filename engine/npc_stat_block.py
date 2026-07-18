"""NPC stat block — D&D 5e-style combat data for NPCs.

An ``NPCStatBlock`` is the optional combat payload attached to an ``NPC``
domain object. Commoner NPCs (lore, dialogue, no combat) leave it ``None``.
Combat-capable NPCs (minions, elites, bosses) carry the structured data the
engine needs to resolve attacks, signature abilities, legendary actions, and
HP-based phase transitions — all deterministically, with the LLM confined to
narration.

Pure Python, no LLM calls.
"""

import logging
import re
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from engine.inventory import DamageType

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LLM-number clamps (audit M12)
# ---------------------------------------------------------------------------
#
# Stat blocks are authored by the 9b narrator model — every number on them
# is untrusted. Out-of-band values are CLAMPED, not rejected, so one wild
# field never voids a whole generated stat block.

MAX_DICE_COUNT: int = 10
MAX_DIE_SIZE: int = 12
MAX_DICE_MODIFIER: int = 10
MIN_TO_HIT_BONUS: int = 0
MAX_TO_HIT_BONUS: int = 15
MIN_SAVE_DC: int = 8
MAX_SAVE_DC: int = 20

_FALLBACK_DICE: str = "1d6"

# Digit runs capped at 9 so absurd LLM output never reaches a costly int().
_STAT_DICE_RE = re.compile(r"^(\d{1,9})d(\d{1,9})([+-]\d{1,9})?$")


def _clamp_dice_expression(value: str) -> str:
    """Coerce a dice string into ≤ 10 dice of ≤ d12 with a ±10 bonus.

    Non-dice strings fall back on ``1d6`` — a degraded attack beats a
    rejected stat block.
    """
    cleaned = value.replace(" ", "")
    match = _STAT_DICE_RE.match(cleaned)
    if not match:
        logger.warning(
            "Stat block dice %r is not dice notation — replaced with %s",
            value,
            _FALLBACK_DICE,
        )
        return _FALLBACK_DICE

    count = min(max(int(match.group(1)), 1), MAX_DICE_COUNT)
    sides = min(max(int(match.group(2)), 1), MAX_DIE_SIZE)
    modifier = int(match.group(3)) if match.group(3) else 0
    modifier = min(max(modifier, -MAX_DICE_MODIFIER), MAX_DICE_MODIFIER)

    rebuilt = f"{count}d{sides}{modifier:+d}" if modifier else f"{count}d{sides}"
    if rebuilt != cleaned:
        logger.warning("Stat block dice %r clamped to %r", value, rebuilt)
    return rebuilt


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class BehaviorProfile(StrEnum):
    """High-level tactical stance used by the NPC AI (minion/elite brains).

    Boss NPCs use the LLM tactician and ignore this profile.
    """

    AGGRESSIVE = "aggressive"
    DEFENSIVE = "defensive"
    SUPPORT = "support"
    TACTICAL = "tactical"


class NPCTier(StrEnum):
    """Combat tier. Determines AI strategy, action economy, and signature budget."""

    MINION = "minion"
    ELITE = "elite"
    BOSS = "boss"


SignatureAbilityKind = Literal[
    "damage",
    "heal",
    "condition",
    "move",
    "buff",
    "debuff",
    "aoe_damage",
]

TargetScope = Literal[
    "single",
    "zone",
    "all_enemies",
    "all_allies_in_zone",
    "self",
]

SaveAbilityCode = Literal["STR", "DEX", "CON", "INT", "WIS", "CHA"]

RangeType = Literal["melee", "ranged", "reach"]

ActionCost = Literal["action", "bonus", "reaction"]

SignatureUsage = Literal["at_will", "per_combat", "per_day", "recharge_5_6"]

_LIMITED_USAGES: frozenset[str] = frozenset({"per_combat", "per_day", "recharge_5_6"})
"""Usages that must carry a finite budget — everything but ``at_will``."""


# ---------------------------------------------------------------------------
# Models — building blocks
# ---------------------------------------------------------------------------


class NPCAttack(BaseModel):
    """A named weapon attack entry on an NPC's stat block.

    The engine rolls 1d20 + ``to_hit_bonus`` versus the defender's AC, then
    rolls ``damage_dice`` of ``damage_type`` on hit. ``range_type`` and
    ``range_value`` drive melee-vs-ranged validation (zone adjacency, line
    of sight, opportunity attacks).
    """

    name: str = Field(min_length=1)
    damage_dice: str  # e.g. "1d8+2", parsed by engine.dice
    damage_type: DamageType
    to_hit_bonus: int = 0
    range_type: RangeType = "melee"
    range_value: int | None = None  # feet for ranged attacks

    @field_validator("damage_dice")
    @classmethod
    def _clamp_damage_dice(cls, value: str) -> str:
        return _clamp_dice_expression(value)

    @field_validator("to_hit_bonus")
    @classmethod
    def _clamp_to_hit(cls, value: int) -> int:
        return min(max(value, MIN_TO_HIT_BONUS), MAX_TO_HIT_BONUS)


class SignatureAbilityEffect(BaseModel):
    """One deterministic effect produced by a signature ability.

    The engine resolves each effect from top to bottom: roll dice, apply
    damage/healing, queue a condition, or move a combatant. The LLM never
    picks numbers — it only narrates the outcome the engine computed.
    """

    kind: SignatureAbilityKind
    dice: str | None = None  # damage or heal dice
    damage_type: DamageType | None = None
    condition_name: str | None = None
    condition_duration_rounds: int | None = None
    save_ability: SaveAbilityCode | None = None
    save_dc: int | None = None
    target_scope: TargetScope = "single"

    @field_validator("dice")
    @classmethod
    def _clamp_effect_dice(cls, value: str | None) -> str | None:
        return None if value is None else _clamp_dice_expression(value)

    @field_validator("save_dc")
    @classmethod
    def _clamp_save_dc(cls, value: int | None) -> int | None:
        if value is None:
            return None
        return min(max(value, MIN_SAVE_DC), MAX_SAVE_DC)


class SignatureAbility(BaseModel):
    """A named tactical ability used by elite and boss NPCs.

    Minions never carry signature abilities — they are a tier marker.
    ``uses_remaining`` is mutated by the engine as the ability is spent and
    must be serialised as part of the ``NPCStatBlock`` JSON on persist.
    """

    name: str = Field(min_length=1)
    description: str = ""
    usage: SignatureUsage
    uses_remaining: int | None = None
    is_reaction: bool = False
    action_cost: ActionCost = "action"
    effects: list[SignatureAbilityEffect] = Field(default_factory=list)

    @model_validator(mode="after")
    def _default_limited_uses(self) -> "SignatureAbility":
        """Give limited-usage signatures a concrete budget (audit H19).

        LLM-generated stat blocks routinely omit ``uses_remaining``. The
        executor only decrements integers, so ``None`` on a per_combat /
        per_day / recharge_5_6 signature meant unlimited uses. ``0`` is
        preserved — that is how phase-locked signatures are represented
        before unlock.

        ``recharge_5_6`` is budgeted like ``per_combat`` on purpose: the
        turn-start recharge roll is not implemented anywhere in the
        engine, so leaving it unbounded made the "recharges on 5-6" nuke
        fire every single round. Once-per-combat is the conservative
        reading until the recharge roll lands.
        """
        if self.usage in _LIMITED_USAGES and self.uses_remaining is None:
            self.uses_remaining = 1
        return self


class LegendaryAction(BaseModel):
    """An off-turn action that a boss NPC can spend legendary points on.

    Legendary points replenish at the start of the boss's turn. Each action
    costs 1-3 points (``cost``) and is resolved by the engine immediately
    after any other creature finishes its turn.
    """

    name: str = Field(min_length=1)
    cost: int = Field(ge=1, le=3)
    description: str = ""
    effects: list[SignatureAbilityEffect] = Field(default_factory=list)


class PhaseTransition(BaseModel):
    """An HP-threshold trigger that modifies a boss NPC mid-fight.

    When the boss's current HP drops to ``trigger_hp_percent`` or below of
    its max, the engine flips ``triggered=True``, applies the stat bonuses,
    unlocks any new signature abilities by name, and queues the narrative
    cue for the narrator.
    """

    trigger_hp_percent: int = Field(ge=1, le=99)  # typically 50
    narrative_cue: str = ""
    unlock_signatures: list[str] = Field(default_factory=list)
    attack_bonus: int = 0
    save_bonus: int = 0
    triggered: bool = False


# ---------------------------------------------------------------------------
# Top-level stat block
# ---------------------------------------------------------------------------


class NPCStatBlock(BaseModel):
    """Full D&D 5e-style combat stat block for an NPC.

    Attached optionally to ``world.npc.NPC.stat_block``. Absence (``None``)
    marks the NPC as purely narrative — they have HP/AC for the sake of the
    narrative layer but cannot enter the combat system as a combatant.
    """

    tier: NPCTier
    archetype: str = Field(min_length=1)
    multiattack_count: int = Field(default=1, ge=1, le=5)
    attacks: list[NPCAttack] = Field(default_factory=list)
    signature_abilities: list[SignatureAbility] = Field(default_factory=list)
    legendary_actions: list[LegendaryAction] = Field(default_factory=list)
    legendary_points_per_round: int = Field(default=0, ge=0, le=5)
    phases: list[PhaseTransition] = Field(default_factory=list)
    behavior_profile: BehaviorProfile = BehaviorProfile.AGGRESSIVE
    aggression_threshold: int = Field(default=15, ge=1, le=30)
    """DC for social checks before this NPC escalates to hostility."""
    mindless: bool = False
    """Marker for non-sentient creatures (zombies, enraged beasts, elementals,
    constructs) that cannot be reasoned with. Blocks any TRUCE attempt
    (``bot.combat_truce.attempt_truce`` refuses automatically). Default
    ``False`` — sentient NPCs (all current archetypes) stay negotiable."""
