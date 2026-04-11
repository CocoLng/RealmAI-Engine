"""Phase transitions — task 54.

D&D 5e bosses can have **phase transitions** triggered by HP thresholds.
When a boss's HP drops to or below a declared percentage, the engine:

1. Flips ``PhaseTransition.triggered = True`` (one-shot per phase).
2. Applies ``attack_bonus`` to every ``NPCAttack`` on the stat block.
3. Adds ``save_bonus`` to ``Combatant.phase_save_bonus`` (cumulative).
4. Unlocks any signatures listed in ``unlock_signatures`` by bumping
   their ``uses_remaining`` from 0 to 1.

The function returns the newly-triggered phases. Callers that have
access to the :class:`~engine.combat.CombatState` append a
:class:`~engine.combat.PhaseTransitionEvent` to
``state.pending_phase_narrations`` so task 71 (narrator) can weave the
``narrative_cue`` into the next round's narration.

Pure Python, no LLM. Dead bosses do not trigger phases. Multiple phase
thresholds may fire on a single big hit (e.g. a crit that drops the
boss past both the 50% and 25% thresholds in one go).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from engine.npc_stat_block import PhaseTransition

if TYPE_CHECKING:
    from engine.combat import Combatant


def check_phase_transition(combatant: "Combatant") -> list[PhaseTransition]:
    """Return newly-triggered phases after an HP change.

    Gate conditions:
    - The combatant has a stat block.
    - The combatant is still alive.
    - The stat block lists at least one phase.

    When the HP ratio has crossed a non-triggered phase threshold, the
    phase is marked ``triggered=True`` and its effects are applied in
    place (attack bonus, save bonus, signature unlocks). The function
    returns the list of phases that just fired — empty if nothing
    changed. Multiple phases may fire on a single call if a heavy hit
    crossed several thresholds at once.
    """
    if combatant.stat_block is None:
        return []
    if not combatant.is_alive:
        return []
    sb = combatant.stat_block
    if not sb.phases:
        return []

    max_hp = max(1, combatant.character.max_hp)
    hp_percent = int((combatant.character.hp / max_hp) * 100)

    triggered_now: list[PhaseTransition] = []
    for phase in sb.phases:
        if phase.triggered:
            continue
        if hp_percent <= phase.trigger_hp_percent:
            phase.triggered = True
            _apply_phase_effects(combatant, phase)
            triggered_now.append(phase)

    return triggered_now


def _apply_phase_effects(combatant: "Combatant", phase: PhaseTransition) -> None:
    """Apply the mechanical bonuses of a phase transition in place.

    - Unlock any signatures listed in ``phase.unlock_signatures`` by
      bumping their ``uses_remaining`` from 0 to 1 (only if they have
      been exhausted; at-will signatures with ``None`` are left alone).
    - Add ``phase.attack_bonus`` to every ``NPCAttack.to_hit_bonus`` on
      the stat block.
    - Add ``phase.save_bonus`` to ``Combatant.phase_save_bonus``.
    """
    sb = combatant.stat_block
    assert sb is not None  # checked by caller

    if phase.unlock_signatures:
        unlock_set = set(phase.unlock_signatures)
        for sig in sb.signature_abilities:
            if sig.name not in unlock_set:
                continue
            if sig.uses_remaining == 0:
                sig.uses_remaining = 1

    if phase.attack_bonus != 0:
        for atk in sb.attacks:
            atk.to_hit_bonus += phase.attack_bonus

    if phase.save_bonus != 0:
        combatant.phase_save_bonus += phase.save_bonus
