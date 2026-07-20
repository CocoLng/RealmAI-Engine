# Task 52 — Boss : LLM tactician

**Phase** : 5 — IA tactique (NPC brains)
**Dépendances** : [51](51_elite_behavior_profiles.md), [42](42_arc_generator_villain_stat_block.md)
**Coordinateur** : [tasks/combat/README.md](README.md)

## Contexte

Pour les boss (villains d'arc, minibosses uniques), les heuristiques scripted sont trop prévisibles. On veut qu'un boss soit **tactiquement créatif** et réagisse au contexte narratif. Le plan coordinateur valide l'usage d'un **LLM-tactician** dédié : appel LLM par tour de boss, qui produit un JSON structuré `{action, target, reasoning, legendary_use}` ; l'engine **valide** et **roule les dés**.

**Règle d'or préservée** : le LLM ne touche jamais aux dés. Il produit seulement l'intention. L'engine arbitre.

## Scope

1. Créer `ai/npc_tactician.py` avec la classe `NPCTactician` qui encapsule l'appel LLM.
2. Créer `ai/prompts/system_npc_tactician.txt` avec le prompt dédié.
3. Étendre `ai/models.py` avec `TacticalDecision` (Pydantic) pour le schema JSON strict.
4. Intégrer dans `engine/npc_ai/` via un nouveau fichier `engine/npc_ai/boss_brain.py` qui route vers le tactician pour les boss.
5. **Fallback mécanisme** : si le LLM produit un JSON invalide 2 fois d'affilée, fallback sur `decide_elite_action` (comportement scripted AGGRESSIVE).

## Fichiers à créer

- **Créer** `ai/npc_tactician.py`
- **Créer** `ai/prompts/system_npc_tactician.txt`
- **Créer** `engine/npc_ai/boss_brain.py`
- **Modifier** `ai/models.py` — `TacticalDecision`

## Implémentation — esquisse

```python
# ai/models.py

class TacticalDecision(BaseModel):
    """Structured output from the NPC tactician LLM.

    The LLM chooses an action and target, and optionally flags a legendary
    action spending. The engine validates legality and rolls all dice.
    """
    action_type: Literal["attack", "signature", "move", "dodge", "disengage"]
    target_name: str | None = None
    weapon_name: str | None = None
    signature_name: str | None = None
    move_to_zone: str | None = None
    reasoning: str = Field(min_length=5)
    legendary_action_name: str | None = None
    """Optional: legendary action to spend off-turn. Handled by task 53."""
```

```python
# ai/npc_tactician.py

import json
import logging
from pathlib import Path

from ai.client import OllamaClient
from ai.language import language_instruction
from ai.models import TacticalDecision
from engine.combat import CombatState, Combatant

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    Path(__file__).parent / "prompts" / "system_npc_tactician.txt"
).read_text()


class NPCTactician:
    """Choose a boss NPC's action on its turn via a dedicated LLM call.

    The tactician sees the full stat block, the combat state, the party
    HP, and the last few turn events. It outputs a structured JSON
    TacticalDecision. The engine validates the decision and rolls all
    dice — this class never touches dice.
    """

    MODEL = "qwen3.5:4b"  # same fast model as interpreter

    def __init__(self, client: OllamaClient) -> None:
        self._client = client

    def decide(
        self,
        boss: Combatant,
        state: CombatState,
        party_context: str,
        recent_events: list[str],
        language: str = "fr",
    ) -> TacticalDecision:
        """Ask the LLM what the boss should do this turn.

        Raises ``ValueError`` if the LLM output cannot be parsed after retries.
        """
        user_content = self._build_context(boss, state, party_context, recent_events)
        lang_prefix = language_instruction(language)
        messages = [
            {"role": "system", "content": lang_prefix + _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        data = self._client.chat_json(
            self.MODEL, messages, temperature=0.7, think=False,
        )
        try:
            decision = TacticalDecision.model_validate(data)
        except Exception as exc:
            logger.warning(
                "NPC tactician output invalid, raising: %s", exc,
            )
            raise ValueError(f"Invalid tactician output: {exc}") from exc

        # Post-validation: check that the decision references real things
        if decision.target_name is not None:
            found = any(c.name == decision.target_name for c in state.combatants)
            if not found:
                raise ValueError(
                    f"Tactician targeted unknown combatant: {decision.target_name}"
                )
        if decision.signature_name is not None:
            if boss.character.stat_block is None:
                raise ValueError("Boss has no stat_block")
            sig_names = [s.name for s in boss.character.stat_block.signature_abilities]
            if decision.signature_name not in sig_names:
                raise ValueError(
                    f"Tactician referenced unknown signature: {decision.signature_name}"
                )
        return decision

    def _build_context(
        self,
        boss: Combatant,
        state: CombatState,
        party_context: str,
        recent_events: list[str],
    ) -> str:
        sb = boss.character.stat_block
        lines: list[str] = []
        lines.append(f"# You are {boss.name}")
        lines.append(f"HP: {boss.character.hp}/{boss.character.max_hp} | AC: {boss.character.ac}")
        lines.append(f"Current zone: {boss.current_zone or 'unzoned'}")

        if sb is not None:
            lines.append("\n## Your attacks")
            for atk in sb.attacks:
                lines.append(
                    f"- {atk.name} ({atk.damage_dice} {atk.damage_type.value}, "
                    f"{atk.range_type}, +{atk.to_hit_bonus})"
                )
            lines.append("\n## Your signature abilities")
            for sig in sb.signature_abilities:
                uses = (
                    "(at will)" if sig.usage == "at_will"
                    else f"(uses left: {sig.uses_remaining})"
                )
                lines.append(f"- {sig.name} {uses}: {sig.description}")

        lines.append("\n## Current combat state")
        for c in state.combatants:
            if c.name == boss.name:
                continue
            status = "dead" if not c.is_alive else f"{c.character.hp}/{c.character.max_hp} HP"
            side = "ENEMY" if c.side != boss.side else "ALLY"
            zone = c.current_zone or "unzoned"
            lines.append(f"- {side} {c.name}: {status}, zone={zone}")

        lines.append(f"\n## Round {state.round_number}")
        lines.append(f"\n## Party context\n{party_context}")

        if recent_events:
            lines.append("\n## Recent events")
            for ev in recent_events[-3:]:
                lines.append(f"- {ev}")

        lines.append(
            "\n## Your job\n"
            "Decide your next action. Return ONLY a JSON object matching "
            "the TacticalDecision schema. Do not roll dice — the engine "
            "handles that. Just pick what you want to do and why."
        )
        return "\n".join(lines)
```

**Prompt** `ai/prompts/system_npc_tactician.txt` :

```
You are the tactical brain of a D&D 5e-style boss monster during combat.
Your job is to decide this NPC's best action for its current turn.

## Rules
1. You NEVER roll dice. The engine rolls everything. You only pick
   what to do.
2. You MUST output valid JSON matching this schema:
   {
     "action_type": "attack" | "signature" | "move" | "dodge" | "disengage",
     "target_name": "<combatant name>" | null,
     "weapon_name": "<attack name>" | null,
     "signature_name": "<signature name>" | null,
     "move_to_zone": "<zone name>" | null,
     "reasoning": "<1-2 sentences explaining your choice>",
     "legendary_action_name": "<legendary action name>" | null
   }
3. Your target_name, weapon_name, signature_name, move_to_zone MUST
   reference entities that exist in the provided context.
4. Consider HP, position, threats, signature cooldowns. Be creative
   but plausible. A boss doesn't always attack the weakest — sometimes
   they target the biggest threat, sometimes they reposition.
5. If you use a signature ability, set action_type to "signature" and
   fill signature_name. Do not also fill weapon_name.
6. If you want to move AND attack, prefer the bigger impact — just attack.
   Movement is separate, handled before the action.

## Style
- Reasoning in French if the campaign is in French.
- Reasoning short, tactical, in-character when possible.
- No narration — leave that to the narrator LLM.

Return ONLY the JSON. No markdown, no commentary.
```

```python
# engine/npc_ai/boss_brain.py

from engine.npc_ai.scripted import NPCActionPlan, decide_minion_action
from engine.npc_ai.elite import decide_elite_action
from engine.validators import ActionType
from ai.npc_tactician import NPCTactician
from ai.models import TacticalDecision


def decide_boss_action(
    combatant: Combatant,
    state: CombatState,
    location: Location | None,
    tactician: NPCTactician,
    party_context: str,
    recent_events: list[str],
) -> NPCActionPlan:
    """Call the LLM tactician for a boss turn, with fallback to elite scripted."""
    try:
        decision = tactician.decide(
            combatant, state, party_context, recent_events,
        )
    except (ValueError, Exception) as exc:
        logger.warning(
            "Tactician failed for %s, falling back to AGGRESSIVE elite: %s",
            combatant.name, exc,
        )
        return decide_elite_action(combatant, state, location)

    return _decision_to_plan(decision)


def _decision_to_plan(decision: TacticalDecision) -> NPCActionPlan:
    """Convert LLM decision to engine plan."""
    mapping = {
        "attack": ActionType.ATTACK,
        "signature": ActionType.ATTACK,  # signature resolved via execute_signature
        "move": ActionType.MOVE,
        "dodge": ActionType.DEFEND,
        "disengage": ActionType.DEFEND,  # or new DISENGAGE ActionType
    }
    return NPCActionPlan(
        action_type=mapping[decision.action_type],
        target_name=decision.target_name,
        weapon_name=decision.weapon_name,
        move_to_zone=decision.move_to_zone,
        rationale=decision.reasoning,
        # Add: signature_to_execute=decision.signature_name
    )
```

**Retry logic** : la tâche spécifie "2 retries puis fallback". Ajouter dans `decide_boss_action` un try-except avec compteur :

```python
def decide_boss_action(...) -> NPCActionPlan:
    for attempt in range(2):
        try:
            decision = tactician.decide(...)
            return _decision_to_plan(decision)
        except ValueError as exc:
            logger.warning("Tactician attempt %d failed: %s", attempt + 1, exc)
    logger.warning("Tactician giving up, falling back to scripted")
    return decide_elite_action(combatant, state, location)
```

## Acceptance criteria

- [ ] `NPCTactician` existe et expose `decide(boss, state, party_context, events, language)`.
- [ ] Le prompt système est chargé depuis `ai/prompts/system_npc_tactician.txt`.
- [ ] `TacticalDecision` Pydantic valide le schema.
- [ ] La validation post-LLM vérifie que `target_name`, `signature_name`, `weapon_name` référencent de vrais objets.
- [ ] `decide_boss_action` retry 2 fois puis fallback sur scripted elite.
- [ ] Le LLM ne touche jamais aux dés (vérification manuelle du prompt).
- [ ] Les tests avec OllamaClient mocké passent.

## Tests à ajouter

Dans `tests/ai/test_npc_tactician.py` (nouveau) :

- `test_tactician_parses_valid_json_decision`.
- `test_tactician_rejects_unknown_target`.
- `test_tactician_rejects_unknown_signature`.
- `test_tactician_rejects_malformed_json`.
- `test_tactician_builds_context_with_stat_block`.
- `test_tactician_includes_recent_events_in_prompt`.

Dans `tests/test_boss_brain.py` (nouveau) :

- `test_boss_brain_uses_llm_decision_when_valid`.
- `test_boss_brain_retries_on_invalid_output`.
- `test_boss_brain_falls_back_to_scripted_after_retries`.
- `test_boss_brain_decision_to_plan_mapping`.

## Hors scope

- **Ne pas** implémenter les legendary actions — tâche [53](53_legendary_actions_off_turn.md).
- **Ne pas** implémenter les phase transitions — tâche [54](54_phase_transitions.md).
- **Ne pas** gérer la narration du tour boss — le narrateur général s'en charge via le Mechanics summary.

## Validation finale

```bash
uv run pytest tests/ai/test_npc_tactician.py tests/test_boss_brain.py -v
uv run ruff check ai/npc_tactician.py engine/npc_ai/boss_brain.py
uv run mypy ai/npc_tactician.py engine/npc_ai/boss_brain.py
```
