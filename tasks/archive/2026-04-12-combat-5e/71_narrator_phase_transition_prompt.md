# Task 71 — Prompt narrateur pour transitions de phase

**Phase** : 7 — Narrateur & cohérence narrative
**Dépendances** : [54](54_phase_transitions.md), [70](70_narrator_combat_context.md)
**Coordinateur** : [tasks/combat/README.md](README.md)

## Contexte

Quand un boss déclenche une phase transition (tâche [54](54_phase_transitions.md)), l'engine ajoute un `PhaseTransitionEvent` sur `state.pending_phase_narrations` avec un `narrative_cue` venu du stat block. C'est un **moment cinématique** qui doit être narré de façon dramatique et spéciale, **pas** fondu dans la narration de combat standard.

Cette tâche ajoute un chemin de narration dédié pour ces événements.

## Scope

1. Helper `narrate_phase_transition(narrator, event, boss, state, language) -> str` qui :
   - Construit un prompt spécifique "Phase Transition".
   - Appelle le narrateur LLM avec ce prompt et l'événement.
   - Retourne la narration dramatique (3-5 phrases).
2. Créer `ai/prompts/system_narrator_phase.txt` — prompt court et ciblé.
3. Hook dans le caller du narrateur (`bot/action_pipeline.py::_narrate` ou équivalent) : après la narration principale, si `state.pending_phase_narrations` contient des entries non-consommées, les narrer séparément via ce chemin, poster comme embed distinct (couleur or), et marquer `consumed=True`.

## Fichiers à créer/modifier

- **Créer** `ai/prompts/system_narrator_phase.txt`
- **Créer** `ai/narrator_phase.py` — helper dédié, OU étendre `ai/narrator.py` avec `narrate_phase_transition`.
- **Modifier** [bot/action_pipeline.py](../../bot/action_pipeline.py) — consommer les phase events après narration principale.

## Implémentation — esquisse

```python
# ai/narrator_phase.py

import logging
from pathlib import Path

from ai.client import OllamaClient
from ai.language import language_instruction
from engine.combat import Combatant, CombatState
from engine.combat_phases import PhaseTransitionEvent

logger = logging.getLogger(__name__)

_PHASE_SYSTEM_PROMPT = (
    Path(__file__).parent / "prompts" / "system_narrator_phase.txt"
).read_text()


def narrate_phase_transition(
    client: OllamaClient,
    event: PhaseTransitionEvent,
    boss: Combatant,
    state: CombatState,
    language: str = "fr",
) -> str:
    """Generate a short dramatic narration for a boss phase transition.

    Returns 3-5 sentences. Uses a dedicated system prompt separate from
    the main narrator to ensure the transition feels distinct.
    """
    user_content = _build_phase_context(event, boss, state)
    lang_prefix = language_instruction(language)
    messages = [
        {"role": "system", "content": lang_prefix + _PHASE_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    resp = client.chat(
        model="qwen3.5:9b",
        messages=messages,
        temperature=0.8,
    )
    return resp.strip()


def _build_phase_context(
    event: PhaseTransitionEvent,
    boss: Combatant,
    state: CombatState,
) -> str:
    lines: list[str] = []
    lines.append(f"# Phase transition event")
    lines.append(f"Boss: {boss.name}")
    lines.append(f"HP: {boss.character.hp}/{boss.character.max_hp}")
    lines.append(f"Round: {state.round_number}")
    lines.append(f"\n## Narrative cue from stat block")
    lines.append(event.narrative_cue)
    lines.append(
        f"\n## Job\n"
        f"Narre ce moment dramatique en 3-5 phrases. Utilise le cue "
        f"ci-dessus comme base et amplifie-le. Aucune information "
        f"mécanique — pure narration cinématique. Ton tendu/sombre."
    )
    return "\n".join(lines)
```

**Prompt** `ai/prompts/system_narrator_phase.txt` :

```
Tu es le narrateur d'une transition de phase d'un boss D&D.

Un moment pivot vient d'arriver : le boss a franchi un seuil HP et révèle
une nouvelle facette de sa puissance. Ta mission : produire une narration
courte (3 à 5 phrases) qui marque ce basculement.

## Règles
1. PAS de description mécanique. Pas de "il regagne X HP", "il gagne +2 attaque",
   etc. Le mécanique reste off-screen.
2. Utilise le `narrative_cue` fourni comme graine et amplifie-le. Si le cue
   dit "Vellus s'effondre à genoux... puis se relève, les yeux blancs",
   élabore : que voit-on, que ressent-on, quelle est l'ambiance sonore ?
3. Ton tendu, sombre, cinématique. Phrases courtes. Verbes d'action.
4. Termine par une phrase qui signale que **le combat continue différemment
   maintenant** — une menace implicite, un sentiment d'urgence renouvelé.
5. Pas de dialogue du boss, sauf si la phase inclut un "moment de parole" évident.
6. Longueur stricte : 3 à 5 phrases. Pas moins, pas plus.

Retourne UNIQUEMENT la prose narrative. Pas de markdown, pas de commentaire.
```

**Intégration dans le pipeline** (dans `action_pipeline.py` ou équivalent) :

```python
async def _narrate(self, outcome: MechanicsOutcome, ...) -> list[discord.Embed]:
    embeds = []
    # Main narration
    main_narration = await self._call_narrator(outcome, ...)
    embeds.append(build_narrative_embed(main_narration, ...))

    # Phase transitions (if any pending)
    if self.combat_state is not None:
        for event in self.combat_state.pending_phase_narrations:
            if event.consumed:
                continue
            boss = _find_by_name(event.boss_name, self.combat_state)
            if boss is None:
                continue
            try:
                phase_narration = await asyncio.to_thread(
                    narrate_phase_transition,
                    client=self.ollama_client,
                    event=event,
                    boss=boss,
                    state=self.combat_state,
                )
                embed = discord.Embed(
                    title=f"\u2728 Phase transition — {boss.name}",
                    description=phase_narration,
                    color=0xF1C40F,  # gold
                )
                embeds.append(embed)
                event.consumed = True
            except Exception as exc:
                logger.warning("Phase narration failed: %s", exc)
                event.consumed = True  # don't retry forever

    return embeds
```

## Acceptance criteria

- [ ] `narrate_phase_transition` existe avec la signature documentée.
- [ ] `ai/prompts/system_narrator_phase.txt` existe avec les règles.
- [ ] Le pipeline consomme les `pending_phase_narrations` après la narration principale.
- [ ] L'embed de phase est distinct (couleur or, titre dédié).
- [ ] `consumed=True` est set pour éviter double narration.
- [ ] Fallback gracieux si le narrateur échoue (log + mark consumed).

## Tests à ajouter

Dans `tests/ai/test_narrator_phase.py` (nouveau) :

- `test_phase_narration_builds_correct_context`.
- `test_phase_narration_uses_dedicated_prompt`.
- `test_phase_narration_with_mocked_llm_returns_string`.

Dans `tests/bot/test_action_pipeline.py` :

- `test_pipeline_consumes_pending_phase_narrations_after_main`.
- `test_pipeline_marks_phase_event_consumed_on_failure`.
- `test_pipeline_skips_already_consumed_phase_events`.

Tests live via discord-test :

- Scénario : combat avec boss, descendre HP à 50%, vérifier qu'un second embed apparaît avec la narration de phase.

## Hors scope

- **Ne pas** persister les phase events en DB — ils vivent dans `session.combat_state` qui est déjà persisté.
- **Ne pas** implémenter la narration spéciale pour la fin de combat (victory/defeat) — tâche [80](80_combat_end_conditions.md).
- **Ne pas** retry automatique sur échec narrateur — mark consumed et skip.

## Validation finale

```bash
uv run pytest tests/ai/test_narrator_phase.py tests/bot/test_action_pipeline.py -v
uv run ruff check ai/narrator_phase.py bot/action_pipeline.py
uv run mypy ai/narrator_phase.py bot/action_pipeline.py
```
