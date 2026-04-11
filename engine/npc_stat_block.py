"""NPC stat block — D&D 5e-style combat data for NPCs.

An ``NPCStatBlock`` is the optional combat payload attached to an ``NPC``
domain object. Commoner NPCs (lore, dialogue, no combat) leave it ``None``.
Combat-capable NPCs (minions, elites, bosses) carry the structured data the
engine needs to resolve attacks, signature abilities, legendary actions, and
HP-based phase transitions — all deterministically, with the LLM confined to
narration.

Pure Python, no LLM calls.
"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from engine.inventory import DamageType


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
