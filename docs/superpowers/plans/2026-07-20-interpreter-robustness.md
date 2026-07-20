# Interpreter Robustness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Aucun tour joueur perdu en silence — fallback IMPROVISE après échec interpreter, exécution séquentielle bornée des multi-intentions, confirmation Oui/Reformuler sous le seuil de confiance.

**Architecture:** Trois mécanismes composés : (1) `call_interpreter` forge un IMPROVISE à confidence 0.3 quand les retries LLM sont épuisés ; (2) l'orchestrator gate toute interprétation `confidence < 0.6` (hors QUESTION) en retournant un `LowConfidenceResult` que le cog transforme en vue de confirmation, avec reprise via `process_interpreted_action` ; (3) l'interpreter renvoie les intentions restantes en texte brut dans `pending_intents`, et le cog chaîne au plus 1 intention supplémentaire hors combat, en annonçant toute intention abandonnée.

**Tech Stack:** Python 3.12, Pydantic v2, discord.py 2.4+, pytest (+pytest-asyncio, pytest-httpx), uv.

**Spec:** `docs/superpowers/specs/2026-07-20-interpreter-robustness-design.md`

## Global Constraints

- Tout s'exécute via `uv run` (jamais d'activation manuelle du venv).
- `pytest` vert, `ruff check .` propre, `mypy .` 0 erreur — avant chaque commit final de tâche.
- Pydantic v2 partout, type hints partout, docstrings sur les fonctions publiques.
- `engine/` reste 100 % déterministe, aucune importation depuis `ai/` ou `bot/`.
- Commits en français, format conventionnel (`feat:`, `fix:`, `test:` …), **jamais** de mention IA/Claude (mode undercover).
- Constantes de la spec : `CONFIDENCE_CLARIFY_THRESHOLD = 0.6` (orchestrator), `FALLBACK_IMPROVISE_CONFIDENCE = 0.3` (interpret.py), `MAX_CHAINED_INTENTS = 2` (action_handler).
- Seuil strict : `confidence < 0.6` gate, `0.6` exactement passe. `ActionType.QUESTION` n'est jamais gaté.
- `OllamaUnavailableError` propage toujours (pas de fallback quand le serveur est down).

---

### Task 1: Contrat `pending_intents` + parsing interpreter + prompt multi-intentions

**Files:**
- Modify: `engine/contracts.py:21-40` (classe `InterpretedAction`)
- Modify: `ai/interpreter.py` (parsing + `NUM_PREDICT`)
- Modify: `ai/prompts/system_interpreter.txt`
- Test: `tests/ai/test_interpreter.py`

**Interfaces:**
- Produces: `InterpretedAction.pending_intents: list[str]` (défaut `[]`) — phrases brutes des intentions non exécutées, max 3 entrées après clamp. Consommé par les Tasks 6 (chaînage cog) et 7 (ScenarioRunner).

- [ ] **Step 1: Écrire les tests qui échouent**

Ajouter à la fin de `tests/ai/test_interpreter.py` (réutilise les fixtures `interpreter` et `cathedral_scene` déjà définies en tête de fichier) :

```python
# ---------------------------------------------------------------------------
# Multi-intentions — pending_intents
# ---------------------------------------------------------------------------


def test_interpret_parses_pending_intents(
    httpx_mock: HTTPXMock,
    interpreter: Interpreter,
    cathedral_scene: SceneContext,
) -> None:
    response_data = {
        "action_type": "Pick Up",
        "actor_name": "Aldric",
        "target_name": "Autel de pierre",
        "pending_intents": ["je vais dans la ruelle nord"],
        "confidence": 0.9,
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    result = interpreter.interpret(
        player_text="je fouille l'autel et je vais dans la ruelle nord",
        actor_name="Aldric",
        scene_context=cathedral_scene,
    )

    assert result.pending_intents == ["je vais dans la ruelle nord"]


def test_interpret_pending_intents_defaults_to_empty(
    httpx_mock: HTTPXMock,
    interpreter: Interpreter,
    cathedral_scene: SceneContext,
) -> None:
    response_data = {
        "action_type": "Look",
        "actor_name": "Aldric",
        "confidence": 0.95,
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    result = interpreter.interpret(
        player_text="je regarde autour de moi",
        actor_name="Aldric",
        scene_context=cathedral_scene,
    )

    assert result.pending_intents == []


def test_interpret_pending_intents_clamps_garbage_and_overflow(
    httpx_mock: HTTPXMock,
    interpreter: Interpreter,
    cathedral_scene: SceneContext,
) -> None:
    """Entrées non-string ignorées, liste tronquée à 3, non-liste → []."""
    response_data = {
        "action_type": "Look",
        "actor_name": "Aldric",
        "pending_intents": ["a", 42, "b", None, "c", "d", "e"],
        "confidence": 0.9,
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    result = interpreter.interpret(
        player_text="je regarde",
        actor_name="Aldric",
        scene_context=cathedral_scene,
    )

    assert result.pending_intents == ["a", "b", "c"]
```

- [ ] **Step 2: Vérifier l'échec**

Run: `uv run pytest tests/ai/test_interpreter.py -k pending_intents -v`
Expected: FAIL — `InterpretedAction` n'a pas de champ `pending_intents` (les deux premiers échouent sur `AttributeError`/champ ignoré, le troisième aussi).

- [ ] **Step 3: Implémenter**

Dans `engine/contracts.py`, classe `InterpretedAction`, après `improvise_description: str | None = None` :

```python
    # Multi-intentions : phrases brutes des intentions exprimées APRÈS la
    # première ("je ramasse la clé et je vais au nord" → l'action classifiée
    # est le Pick Up, pending_intents = ["je vais au nord"]). Le cog les
    # ré-interprète une par une contre la scène mise à jour — jamais de
    # classification anticipée ici.
    pending_intents: list[str] = Field(default_factory=list)
```

Dans `ai/interpreter.py` :

1. Constante de module après `_ACTION_TYPES_BY_KEY` :

```python
_MAX_PENDING_INTENTS = 3
"""Clamp défensif — le 4b ne doit jamais imposer plus de 3 intentions."""


def _parse_pending_intents(raw: object) -> list[str]:
    """Ne garde que les entrées string non vides, tronque à _MAX_PENDING_INTENTS."""
    if not isinstance(raw, list):
        return []
    cleaned = [item for item in raw if isinstance(item, str) and item.strip()]
    return cleaned[:_MAX_PENDING_INTENTS]
```

2. `NUM_PREDICT = 384` → `NUM_PREDICT = 448` et sa docstring devient :

```python
    NUM_PREDICT = 448
    """Generation cap — one flat JSON action object + pending_intents (M7)."""
```

3. Dans la construction de `InterpretedAction` (le bloc `try:` de `interpret`), ajouter après `improvise_description=...` :

```python
                pending_intents=_parse_pending_intents(data.get("pending_intents")),
```

Dans `ai/prompts/system_interpreter.txt` :

1. Dans le schéma JSON (après la ligne `"improvise_description"`) :

```
  "pending_intents": [<raw phrases of any FURTHER actions the player chained after the first one, else empty list>],
```

2. Nouvelle section avant `## Détection d'intention létale` :

```
## Multi-intentions (actions enchaînées)

Quand le joueur exprime PLUSIEURS actions séquentielles distinctes dans le même message, classifie UNIQUEMENT la première et recopie les phrases restantes, telles quelles et dans l'ordre, dans `pending_intents`. Ne classifie jamais les actions suivantes — le système les ré-interprétera une par une après exécution de la première (la scène aura changé).

- "je ramasse la clé et je vais au nord" → action = Pick Up (la clé), pending_intents = ["je vais au nord"]
- "j'attaque le garde puis je fouille son corps" → action = Attack (le garde), pending_intents = ["je fouille son corps"]
- Ne PAS sur-découper : une description riche d'une SEULE action reste une seule intention. "je regarde autour de moi et j'écoute attentivement" → Look, pending_intents = []. "je m'approche de l'autel en dégainant discrètement" → une seule intention, pending_intents = [].
- Une énumération d'objets n'est pas une séquence d'actions : "je ramasse la clé et la lanterne" → Pick Up, pending_intents = [].
- En cas de doute, pending_intents = [].
```

- [ ] **Step 4: Vérifier le vert**

Run: `uv run pytest tests/ai/test_interpreter.py tests/ai/test_models.py tests/engine -q`
Expected: PASS (aucune régression sur le contrat).

- [ ] **Step 5: Commit**

```bash
git add engine/contracts.py ai/interpreter.py ai/prompts/system_interpreter.txt tests/ai/test_interpreter.py
git commit -m "feat(interpreter): champ pending_intents — le 4b découpe les multi-intentions sans les classifier"
```

---

### Task 2: Fallback IMPROVISE après épuisement des retries

**Files:**
- Modify: `bot/pipeline/interpret.py:152-178` (`call_interpreter`)
- Modify: `ai/interpreter.py:66-75` (docstring H11 de `interpret`)
- Test: `tests/bot/pipeline/test_interpret_fallback.py` (create)

**Interfaces:**
- Consumes: `InterpretedAction` (contrat existant), `LLMParseError` / `OllamaUnavailableError` (`ai/client.py`), `retry_llm_call` (`bot/llm_retry.py`).
- Produces: `FALLBACK_IMPROVISE_CONFIDENCE: float = 0.3` (constante module `bot/pipeline/interpret.py`) ; `call_interpreter` ne lève plus `LLMParseError` — il retourne un IMPROVISE forgé à la place. Consommé par la Task 3 (le gate attrape la confidence 0.3).

- [ ] **Step 1: Écrire les tests qui échouent**

Créer `tests/bot/pipeline/test_interpret_fallback.py` :

```python
"""Fallback IMPROVISE quand l'interpreter épuise ses retries (axe robustesse)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from ai.client import LLMParseError, OllamaUnavailableError
from ai.scene_context import SceneContext
from bot.pipeline import interpret
from engine.validators import ActionType


def _scene() -> SceneContext:
    return SceneContext(location_name="Crypte", location_description="Sombre.")


def _parse_error() -> LLMParseError:
    return LLMParseError(
        "unknown action_type 'Dance'",
        raw_response="{}",
        model="qwen3.5:4b",
        messages=[],
    )


@pytest.mark.asyncio
async def test_fallback_improvise_on_parse_error_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLMParseError après retries → IMPROVISE forgé, confidence 0.3."""

    async def _exhausted(fn: Any, **kwargs: Any) -> Any:
        raise _parse_error()

    monkeypatch.setattr(interpret, "retry_llm_call", _exhausted)

    action = await interpret.call_interpreter(
        interpreter=MagicMock(),  # jamais appelé : retry_llm_call est court-circuité
        player_text="je danse avec le squelette",
        scene=_scene(),
        actor_name="Aldric",
        language="fr",
    )

    assert action.action_type is ActionType.IMPROVISE
    assert action.improvise_description == "je danse avec le squelette"
    assert action.raw_input == "je danse avec le squelette"
    assert action.actor_name == "Aldric"
    assert action.confidence == interpret.FALLBACK_IMPROVISE_CONFIDENCE
    assert action.confidence == 0.3


@pytest.mark.asyncio
async def test_ollama_unavailable_still_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Serveur down = vraie panne : pas de fallback mensonger."""

    async def _down(fn: Any, **kwargs: Any) -> Any:
        raise OllamaUnavailableError("connection refused")

    monkeypatch.setattr(interpret, "retry_llm_call", _down)

    with pytest.raises(OllamaUnavailableError):
        await interpret.call_interpreter(
            interpreter=MagicMock(),
            player_text="je regarde",
            scene=_scene(),
            actor_name="Aldric",
            language="fr",
        )
```

- [ ] **Step 2: Vérifier l'échec**

Run: `uv run pytest tests/bot/pipeline/test_interpret_fallback.py -v`
Expected: FAIL — premier test : `LLMParseError` propage au lieu du fallback ; l'import de `FALLBACK_IMPROVISE_CONFIDENCE` échoue aussi (`AttributeError`).

- [ ] **Step 3: Implémenter**

Dans `bot/pipeline/interpret.py`, après le bloc d'imports (avant la section Side-channel) :

```python
FALLBACK_IMPROVISE_CONFIDENCE = 0.3
"""Confidence du IMPROVISE forgé après épuisement des retries interpreter.

Volontairement sous CONFIDENCE_CLARIFY_THRESHOLD (orchestrator) : le fallback
passe TOUJOURS par le gate de confirmation — le joueur valide avant que le
tour soit consommé (leçon H11 : jamais de fallback silencieux).
"""
```

Ajouter `LLMParseError` à l'import existant `from ai.client import ...` (ou créer `from ai.client import LLMParseError` si absent — vérifier le bloc d'imports du fichier).

Remplacer le `return await retry_llm_call(...)` de `call_interpreter` par :

```python
    try:
        return await retry_llm_call(
            _do,
            log_label=f"ACTION campaign={campaign_id} interpret",
        )
    except LLMParseError:
        logger.warning(
            "ACTION campaign=%s interpret fallback→IMPROVISE raw=%r",
            campaign_id, player_text[:100],
        )
        return InterpretedAction(
            action_type=ActionType.IMPROVISE,
            actor_name=actor_name,
            improvise_description=player_text,
            raw_input=player_text,
            confidence=FALLBACK_IMPROVISE_CONFIDENCE,
        )
```

Et compléter la docstring de `call_interpreter` :

```python
    """Call the Interpreter LLM and return its structured result.

    Retries are handled by :func:`bot.llm_retry.retry_llm_call`. When every
    retry fails on ``LLMParseError`` (sortie 4b inexploitable), un IMPROVISE
    de secours est forgé avec ``FALLBACK_IMPROVISE_CONFIDENCE`` — le gate de
    confiance de l'orchestrator le soumet alors à confirmation du joueur.
    ``OllamaUnavailableError`` propage toujours : serveur down = vraie panne.
    """
```

Dans `ai/interpreter.py`, compléter la note H11 de la docstring de `interpret` (paragraphe `Raises:`) — remplacer la dernière phrase par :

```
                exceptions, and a silent fallback used to convert a 4b
                hiccup into a DEFEND that consumed the player's turn (H11).
                Le filet de sécurité vit en aval : après épuisement des
                retries, bot.pipeline.interpret.call_interpreter forge un
                IMPROVISE basse confidence soumis à confirmation du joueur.
```

- [ ] **Step 4: Vérifier le vert**

Run: `uv run pytest tests/bot/pipeline/ -q`
Expected: PASS (nouveaux tests + zéro régression sur les tests pipeline existants).

- [ ] **Step 5: Commit**

```bash
git add bot/pipeline/interpret.py ai/interpreter.py tests/bot/pipeline/test_interpret_fallback.py
git commit -m "feat(pipeline): fallback IMPROVISE basse confidence après épuisement des retries interpreter"
```

---

### Task 3: Gate de confiance basse dans l'orchestrator

**Files:**
- Modify: `bot/pipeline/orchestrator.py` (modèle + constante + gate dans `process()` + union `PipelineOutput`)
- Modify: `bot/action_pipeline.py` (ré-export façade)
- Test: `tests/bot/pipeline/test_low_confidence_gate.py` (create)

**Interfaces:**
- Consumes: `InterpretedAction` (avec `confidence`), `interpret.call_interpreter` (Task 2).
- Produces: `LowConfidenceResult(interpreted_action: InterpretedAction)` (BaseModel, membre de `PipelineOutput`) ; `CONFIDENCE_CLARIFY_THRESHOLD: float = 0.6` (constante module orchestrator) ; ré-exports dans `bot.action_pipeline`. Consommé par les Tasks 5 (cog) et 7 (ScenarioRunner). `PipelineRunner.process_interpreted_action` ne gate JAMAIS (voie de reprise).

- [ ] **Step 1: Écrire les tests qui échouent**

Créer `tests/bot/pipeline/test_low_confidence_gate.py` :

```python
"""Gate de confiance basse — process() pause avant résolution d'entités."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from ai.models import InterpretedAction
from bot.pipeline import interpret, orchestrator
from bot.pipeline.orchestrator import (
    CONFIDENCE_CLARIFY_THRESHOLD,
    LowConfidenceResult,
    PipelineRunner,
)
from engine.validators import ActionType

_CONTINUED = object()
"""Sentinelle : _continue_from_resolution a été atteint (pas de gate)."""


def _action(action_type: ActionType, confidence: float) -> InterpretedAction:
    return InterpretedAction(
        action_type=action_type,
        actor_name="Aldric",
        raw_input="peu importe",
        confidence=confidence,
    )


def _runner(monkeypatch: pytest.MonkeyPatch, action: InterpretedAction) -> PipelineRunner:
    async def _fake_interpret(**kwargs: Any) -> InterpretedAction:
        return action

    async def _fake_continue(self: Any, interpreted: Any, progress_callback: Any) -> Any:
        return _CONTINUED

    monkeypatch.setattr(interpret, "call_interpreter", _fake_interpret)
    monkeypatch.setattr(
        PipelineRunner, "_continue_from_resolution", _fake_continue,
    )
    return PipelineRunner(
        interpreter=MagicMock(),
        narrator=MagicMock(),
        location=None,
        npcs={},
        actor_name="Aldric",
    )


@pytest.mark.asyncio
async def test_below_threshold_returns_low_confidence_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    action = _action(ActionType.IMPROVISE, 0.59)
    result = await _runner(monkeypatch, action).process("je tente un truc")

    assert isinstance(result, LowConfidenceResult)
    assert result.interpreted_action is action


@pytest.mark.asyncio
async def test_at_threshold_passes_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """0.6 exactement passe : le gate est strict (< 0.6)."""
    action = _action(ActionType.ATTACK, CONFIDENCE_CLARIFY_THRESHOLD)
    result = await _runner(monkeypatch, action).process("j'attaque")

    assert result is _CONTINUED


@pytest.mark.asyncio
async def test_question_never_gated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Une question est gratuite et sans effet d'état — jamais de friction."""
    action = _action(ActionType.QUESTION, 0.2)
    result = await _runner(monkeypatch, action).process("que vois-je ?")

    assert result is _CONTINUED


@pytest.mark.asyncio
async def test_process_interpreted_action_never_gated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La voie de reprise (après Oui) ne repasse jamais par le gate."""
    action = _action(ActionType.IMPROVISE, 0.1)
    runner = _runner(monkeypatch, action)
    result = await runner.process_interpreted_action(action)

    assert result is _CONTINUED


def test_facade_reexports_low_confidence_result() -> None:
    from bot.action_pipeline import LowConfidenceResult as FacadeLCR

    assert FacadeLCR is orchestrator.LowConfidenceResult
```

- [ ] **Step 2: Vérifier l'échec**

Run: `uv run pytest tests/bot/pipeline/test_low_confidence_gate.py -v`
Expected: FAIL — `ImportError: cannot import name 'LowConfidenceResult'`.

- [ ] **Step 3: Implémenter**

Dans `bot/pipeline/orchestrator.py` :

1. Constante de module, juste au-dessus de `class ActionPipelineResult` :

```python
CONFIDENCE_CLARIFY_THRESHOLD = 0.6
"""Sous ce seuil (strict), l'interprétation est soumise à confirmation du
joueur avant toute exécution — le prompt interpreter calibre <= 0.5 comme
« flou », 0.6 absorbe la zone grise. Les QUESTION ne sont jamais gatées
(gratuites, sans effet d'état)."""
```

2. Nouveau modèle, après `class UnknownEntityResult` :

```python
class LowConfidenceResult(BaseModel):
    """Interpreter confidence sous le seuil — le caller doit faire confirmer
    l'action au joueur avant de reprendre via ``process_interpreted_action``."""

    interpreted_action: InterpretedAction

    model_config = {"arbitrary_types_allowed": True}
```

3. Étendre l'union :

```python
PipelineOutput = (
    ActionPipelineResult
    | AmbiguityResult
    | UnknownEntityResult
    | LowConfidenceResult
)
```

4. Dans `PipelineRunner.process()`, entre le `interpreted = await interpret.call_interpreter(...)` et le `return await self._continue_from_resolution(...)` :

```python
        if (
            interpreted.confidence < CONFIDENCE_CLARIFY_THRESHOLD
            and interpreted.action_type is not ActionType.QUESTION
        ):
            logger.info(
                "INTERPRET low-confidence gate campaign=%s action=%s confidence=%.2f",
                self.campaign_id,
                interpreted.action_type.value,
                interpreted.confidence,
            )
            return LowConfidenceResult(interpreted_action=interpreted)
```

(`ActionType` est déjà importé depuis `engine.validators` ; `logger` existe déjà dans le module.)

Dans `bot/action_pipeline.py` : ajouter `LowConfidenceResult` à l'import depuis `bot.pipeline.orchestrator` (bloc lignes 15-21, ordre alphabétique) et à `__all__`.

- [ ] **Step 4: Vérifier le vert**

Run: `uv run pytest tests/bot/pipeline/ tests/bot/test_action_pipeline.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/pipeline/orchestrator.py bot/action_pipeline.py tests/bot/pipeline/test_low_confidence_gate.py
git commit -m "feat(pipeline): gate de confiance basse — LowConfidenceResult pause le pipeline avant exécution"
```

---

### Task 4: ConfirmActionView + describe_action + embed

**Files:**
- Create: `bot/views/confirm_action_view.py`
- Test: `tests/bot/test_confirm_action_view.py` (create)

**Interfaces:**
- Consumes: `InterpretedAction`, `ActionType`.
- Produces: `ConfirmActionView(author_id: int)` avec attribut `confirmed: bool` (False par défaut, True après clic Oui ; timeout/Reformuler → False) ; `build_confirm_embed(action: InterpretedAction, language: str = "fr") -> discord.Embed` ; `describe_action(action: InterpretedAction, language: str = "fr") -> str`. Consommé par la Task 5.

- [ ] **Step 1: Écrire les tests qui échouent**

Créer `tests/bot/test_confirm_action_view.py` :

```python
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
```

- [ ] **Step 2: Vérifier l'échec**

Run: `uv run pytest tests/bot/test_confirm_action_view.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.views.confirm_action_view'`.

- [ ] **Step 3: Implémenter**

Créer `bot/views/confirm_action_view.py` :

```python
"""Confirmation Oui/Reformuler quand l'interpreter doute (confiance basse).

Affichée quand le pipeline retourne un ``LowConfidenceResult`` : le joueur
valide l'interprétation avant qu'elle s'exécute — un tour n'est jamais
consommé sur une lecture douteuse (leçon H11). Timeout et Reformuler laissent
``confirmed`` à False : le cog annule sans toucher à l'état du jeu.
"""

from __future__ import annotations

import discord
from discord import ui

from ai.models import InterpretedAction
from engine.validators import ActionType

_CONFIRM_COLOR = 0xF5A623

_FR_TEMPLATES: dict[ActionType, str] = {
    ActionType.ATTACK: "Attaque sur {target}",
    ActionType.MOVE: "Déplacement vers {target}",
    ActionType.TALK: "Parler à {target}",
    ActionType.PICKUP: "Ramasser {target}",
    ActionType.USE_ITEM: "Utiliser {target}",
    ActionType.SEARCH: "Fouiller {target}",
    ActionType.INTERACT: "Interagir avec {target}",
}

_EN_TEMPLATES: dict[ActionType, str] = {
    ActionType.ATTACK: "Attack {target}",
    ActionType.MOVE: "Move to {target}",
    ActionType.TALK: "Talk to {target}",
    ActionType.PICKUP: "Pick up {target}",
    ActionType.USE_ITEM: "Use {target}",
    ActionType.SEARCH: "Search {target}",
    ActionType.INTERACT: "Interact with {target}",
}


def describe_action(action: InterpretedAction, language: str = "fr") -> str:
    """Résumé humain d'une InterpretedAction pour l'embed de confirmation."""
    if action.action_type is ActionType.IMPROVISE:
        detail = action.improvise_description or action.raw_input
        prefix = "Improvisation : " if language == "fr" else "Improvise: "
        return f"{prefix}{detail}"

    templates = _FR_TEMPLATES if language == "fr" else _EN_TEMPLATES
    target = action.target_name or action.item_name
    template = templates.get(action.action_type)
    if template is not None and target:
        return template.format(target=target)
    if target:
        return f"{action.action_type.value} → {target}"
    return action.action_type.value


def build_confirm_embed(
    action: InterpretedAction, language: str = "fr",
) -> discord.Embed:
    """Embed « J'ai compris : X. C'est bien ça ? » de la vue de confirmation."""
    summary = describe_action(action, language)
    if language == "fr":
        title = "Confirme ton action"
        description = f"J'ai compris : **{summary}**\n\nC'est bien ça ?"
    else:
        title = "Confirm your action"
        description = f"I understood: **{summary}**\n\nIs that right?"
    return discord.Embed(
        title=title, description=description, color=_CONFIRM_COLOR,
    )


class ConfirmActionView(ui.View):
    """Boutons Oui / Reformuler. Seul l'auteur de l'action peut cliquer."""

    timeout: float = 120.0  # 2 minutes, aligné sur ClarificationView

    def __init__(self, author_id: int) -> None:
        super().__init__(timeout=self.timeout)
        self.author_id = author_id
        self.confirmed: bool = False

        yes_button: ui.Button["ConfirmActionView"] = ui.Button(
            label="Oui",
            style=discord.ButtonStyle.success,
            emoji="✅",
            custom_id="confirm_yes",
        )
        yes_button.callback = self._on_yes
        self.add_item(yes_button)

        redo_button: ui.Button["ConfirmActionView"] = ui.Button(
            label="Reformuler",
            style=discord.ButtonStyle.secondary,
            emoji="✏️",
            custom_id="confirm_redo",
        )
        redo_button.callback = self._on_redo
        self.add_item(redo_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Seul le joueur qui a lancé l'action peut confirmer.",
                ephemeral=True,
            )
            return False
        return True

    async def _on_yes(self, interaction: discord.Interaction) -> None:
        self.confirmed = True
        await interaction.response.defer()
        self.stop()

    async def _on_redo(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        self.stop()
```

- [ ] **Step 4: Vérifier le vert**

Run: `uv run pytest tests/bot/test_confirm_action_view.py -v`
Expected: PASS (tous).

- [ ] **Step 5: Commit**

```bash
git add bot/views/confirm_action_view.py tests/bot/test_confirm_action_view.py
git commit -m "feat(views): vue de confirmation Oui/Reformuler pour les interprétations douteuses"
```

---

### Task 5: Branche cog — _render_low_confidence (pause → confirmation → reprise)

**Files:**
- Modify: `bot/cogs/action_handler.py` (import, dispatch dans `_process_and_render`, nouvelle méthode `_render_low_confidence`)
- Test: `tests/bot/test_action_handler_cog.py`

**Interfaces:**
- Consumes: `LowConfidenceResult` (Task 3), `ConfirmActionView` / `build_confirm_embed` (Task 4), `pipeline.process_interpreted_action(action, progress_callback=...)` (méthode existante de `PipelineRunner`).
- Produces: `_render_low_confidence(...) -> PipelineOutput | None` — retourne le résultat FINAL après reprise (contrainte main de combat, identique à `_render_ambiguity`), ou `None` si Reformuler/timeout/erreur (tour non consommé).

- [ ] **Step 1: Écrire les tests qui échouent**

Dans `tests/bot/test_action_handler_cog.py` :

1. Ajouter `LowConfidenceResult` à l'import `from bot.action_pipeline import (...)` en tête de fichier.

2. Étendre `FakePipelineFactory.__call__` — après la définition de `resume`, ajouter :

```python
        async def process_interpreted(
            action: Any, progress_callback: Any = None,
        ) -> Any:
            self.process_interpreted_calls.append(action)
            return self.resume_output if self.resume_output is not None else self.output

        pipeline.process_interpreted_action = process_interpreted
```

et dans `FakePipelineFactory.__init__`, ajouter :

```python
        self.process_interpreted_calls: list[Any] = []
        self.resume_output: Any = None
```

3. Nouvelle classe de tests en fin de fichier :

```python
# ---------------------------------------------------------------------------
# Confiance basse — confirmation Oui/Reformuler
# ---------------------------------------------------------------------------


def _low_confidence_output() -> LowConfidenceResult:
    return LowConfidenceResult(
        interpreted_action=InterpretedAction(
            action_type=ActionType.IMPROVISE,
            actor_name="Aldric",
            improvise_description="je danse",
            raw_input="je danse",
            confidence=0.3,
        ),
    )


def _success_output() -> ActionPipelineResult:
    return ActionPipelineResult(
        narrative="Tu danses.",
        tone="humorous",
        mechanics_text="",
        interpreted_action=InterpretedAction(
            action_type=ActionType.IMPROVISE,
            actor_name="Aldric",
            raw_input="je danse",
        ),
    )


def _campaign_message(bot: Any) -> FakeMessage:
    return FakeMessage(
        content="<@9999> je danse",
        author=FakeAuthor(id=1),
        channel=FakeChannel(id=1),
        mentions=[bot.user],
    )


class TestLowConfidenceFlow:
    @pytest.mark.asyncio
    async def test_confirm_resumes_via_process_interpreted_action(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        session = _make_session(player_id=1)
        bot = _make_bot(sessions={1: session})
        cog = _make_cog(bot)
        factory = FakePipelineFactory(_low_confidence_output())
        factory.resume_output = _success_output()
        cog._pipeline_factory = factory

        from bot.views import confirm_action_view as cav_module

        async def _instant_confirm(self: Any) -> None:
            self.confirmed = True

        monkeypatch.setattr(
            cav_module.ConfirmActionView, "wait", _instant_confirm,
        )

        await cog.on_message(_campaign_message(bot))  # type: ignore[arg-type]

        assert len(factory.process_interpreted_calls) == 1
        resumed = factory.process_interpreted_calls[0]
        assert resumed.action_type is ActionType.IMPROVISE
        assert resumed.confidence == 0.3

    @pytest.mark.asyncio
    async def test_reformulate_drops_action_without_resume(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        session = _make_session(player_id=1)
        bot = _make_bot(sessions={1: session})
        cog = _make_cog(bot)
        factory = FakePipelineFactory(_low_confidence_output())
        cog._pipeline_factory = factory

        from bot.views import confirm_action_view as cav_module

        async def _instant_timeout(self: Any) -> None:
            self.confirmed = False  # Reformuler et timeout : même chemin

        monkeypatch.setattr(
            cav_module.ConfirmActionView, "wait", _instant_timeout,
        )

        await cog.on_message(_campaign_message(bot))  # type: ignore[arg-type]

        assert factory.process_interpreted_calls == []
```

- [ ] **Step 2: Vérifier l'échec**

Run: `uv run pytest tests/bot/test_action_handler_cog.py::TestLowConfidenceFlow -v`
Expected: FAIL — le cog ne gère pas `LowConfidenceResult` (aucun appel à `process_interpreted_action` ; le premier test échoue sur l'assertion, le dispatch tombe dans aucun branch).

- [ ] **Step 3: Implémenter**

Dans `bot/cogs/action_handler.py` :

1. Imports : ajouter `LowConfidenceResult` au bloc `from bot.action_pipeline import (...)` et :

```python
from bot.views.confirm_action_view import (
    ConfirmActionView,
    build_confirm_embed,
)
```

2. Dans `_process_and_render`, section « 4. Dispatch on result type », ajouter une branche après le `elif isinstance(result, AmbiguityResult):` (avant `UnknownEntityResult`) :

```python
        elif isinstance(result, LowConfidenceResult):
            # Même contrainte que la désambiguïsation : c'est le résultat
            # FINAL (après confirmation + reprise) que la main de combat
            # doit voir. ``None`` si le joueur reformule / timeout.
            result = await self._render_low_confidence(
                progress_msg, result, message.author.id, pipeline,
                actor_name=actor_name, raw_text=raw_text, start=start,
                session=session,
            )
```

3. Nouvelle méthode, placée juste après `_render_ambiguity` :

```python
    async def _render_low_confidence(
        self,
        progress_msg: discord.Message,
        low_confidence: LowConfidenceResult,
        author_id: int,
        pipeline: Any,
        *,
        actor_name: str,
        raw_text: str,
        start: float,
        session: Any,
    ) -> PipelineOutput | None:
        """Confirmation Oui/Reformuler puis reprise du pipeline.

        Retourne le résultat FINAL après ``process_interpreted_action``
        (jamais l'intermédiaire), ou ``None`` quand l'action est abandonnée
        (Reformuler, timeout, reprise en erreur) — le tour n'est alors pas
        consommé et l'appelant n'avance pas la rotation de combat.
        """
        embed = build_confirm_embed(
            low_confidence.interpreted_action, session.language,
        )
        view = ConfirmActionView(author_id=author_id)
        await progress_msg.edit(embed=embed, view=view)

        await view.wait()

        if not view.confirmed:
            # Reformuler ou timeout — même sortie : rien n'est exécuté.
            cancel_text = (
                "✏️ Action annulée — reformule ton action."
                if session.language == "fr"
                else "✏️ Action cancelled — rephrase your action."
            )
            await progress_msg.edit(
                embed=discord.Embed(description=cancel_text, color=0x95A5A6),
                view=None,
            )
            return None

        async def update_progress(phase: PipelinePhase) -> None:
            try:
                await progress_msg.edit(
                    embed=build_action_progress_embed(
                        actor_name=actor_name,
                        raw_text=raw_text,
                        current_phase=phase,
                        elapsed_seconds=time.monotonic() - start,
                    ),
                    view=None,
                )
            except discord.HTTPException:
                logger.warning(
                    "ACTION progress edit failed (confirm) campaign=%s phase=%s",
                    session.campaign.id, phase.name,
                )

        try:
            result = await pipeline.process_interpreted_action(
                low_confidence.interpreted_action,
                progress_callback=update_progress,
            )
        except Exception as exc:
            logger.exception(
                "ACTION confirm-resume failed campaign=%s reason=%s",
                session.campaign.id, exc,
            )
            await progress_msg.edit(
                embed=build_action_progress_embed(
                    actor_name=actor_name,
                    raw_text=raw_text,
                    current_phase=PipelinePhase.FAILED,
                    elapsed_seconds=time.monotonic() - start,
                ),
                view=None,
            )
            return None

        if isinstance(result, ActionPipelineResult):
            await self._render_success(progress_msg, result, session=session)
        elif isinstance(result, AmbiguityResult):
            return await self._render_ambiguity(
                progress_msg, result, author_id, pipeline,
                actor_name=actor_name, raw_text=raw_text, start=start,
                session=session,
            )
        elif isinstance(result, UnknownEntityResult):
            await self._render_unknown(progress_msg, result)
        return result
```

(La reprise peut légitimement produire une `AmbiguityResult` — la résolution d'entités n'a pas encore tourné au moment du gate ; on délègue alors au flux de clarification existant.)

- [ ] **Step 4: Vérifier le vert**

Run: `uv run pytest tests/bot/test_action_handler_cog.py -q`
Expected: PASS (nouveaux + anciens).

- [ ] **Step 5: Commit**

```bash
git add bot/cogs/action_handler.py tests/bot/test_action_handler_cog.py
git commit -m "feat(bot): confirmation joueur avant exécution d'une interprétation basse confiance"
```

---

### Task 6: Chaînage multi-intentions côté cog

**Files:**
- Modify: `bot/cogs/action_handler.py` (`_run_pipeline` signature + hook, nouvelles fonctions `_chain_pending_intents` et `_build_dropped_intents_embed`)
- Test: `tests/bot/test_action_handler_cog.py`

**Interfaces:**
- Consumes: `ActionPipelineResult.interpreted_action.pending_intents` (Task 1), `_run_pipeline` (existant).
- Produces: `MAX_CHAINED_INTENTS: int = 2` (constante module `bot/cogs/action_handler.py`) ; `_run_pipeline(message, session, raw_text, *, chain_budget: int = MAX_CHAINED_INTENTS - 1)` — le budget décompte les actions exécutées au-delà de la première.

- [ ] **Step 1: Écrire les tests qui échouent**

Dans `tests/bot/test_action_handler_cog.py`, nouvelle classe en fin de fichier :

```python
# ---------------------------------------------------------------------------
# Multi-intentions — chaînage borné
# ---------------------------------------------------------------------------


def _result_with_pending(pending: list[str]) -> ActionPipelineResult:
    return ActionPipelineResult(
        narrative="Fait.",
        tone="dramatic",
        mechanics_text="",
        interpreted_action=InterpretedAction(
            action_type=ActionType.PICKUP,
            actor_name="Aldric",
            target_name="Clé",
            raw_input="je ramasse la clé et je vais au nord",
            pending_intents=pending,
        ),
    )


class SequencedPipelineFactory(FakePipelineFactory):
    """Renvoie un output différent par appel à process (1er, 2e, ...)."""

    def __init__(self, outputs: list[Any]) -> None:
        super().__init__(outputs[0])
        self._outputs = outputs

    def __call__(self, **kwargs: Any) -> Any:
        pipeline = super().__call__(**kwargs)
        original_process = pipeline.process

        async def process(player_text: str, progress_callback: Any = None) -> Any:
            index = min(len(self.process_calls), len(self._outputs) - 1)
            self.output = self._outputs[index]
            return await original_process(player_text, progress_callback)

        pipeline.process = process
        return pipeline


class TestPendingIntentChaining:
    @pytest.mark.asyncio
    async def test_chains_one_pending_intent_out_of_combat(self) -> None:
        session = _make_session(player_id=1)
        bot = _make_bot(sessions={1: session})
        cog = _make_cog(bot)
        factory = SequencedPipelineFactory([
            _result_with_pending(["je vais au nord"]),
            _success_output(),  # la 2e action ne chaîne plus rien
        ])
        cog._pipeline_factory = factory

        await cog.on_message(_campaign_message(bot))  # type: ignore[arg-type]

        assert factory.process_calls == [
            "je danse",  # raw_text du message (mention strippée)
            "je vais au nord",
        ]

    @pytest.mark.asyncio
    async def test_budget_caps_total_at_two_actions(self) -> None:
        """Même si chaque action re-déclare des intentions, cap global à 2."""
        session = _make_session(player_id=1)
        bot = _make_bot(sessions={1: session})
        cog = _make_cog(bot)
        factory = SequencedPipelineFactory([
            _result_with_pending(["intention 2"]),
            _result_with_pending(["intention 3"]),  # budget épuisé → drop
        ])
        cog._pipeline_factory = factory

        await cog.on_message(_campaign_message(bot))  # type: ignore[arg-type]

        assert len(factory.process_calls) == 2

    @pytest.mark.asyncio
    async def test_no_chaining_when_combat_active_after_first_action(self) -> None:
        session = _make_session(player_id=1)
        bot = _make_bot(sessions={1: session})
        cog = _make_cog(bot)

        combat_state = MagicMock()
        combat_state.is_active = True

        class CombatStartingFactory(FakePipelineFactory):
            def __call__(self, **kwargs: Any) -> Any:
                pipeline = super().__call__(**kwargs)
                original_process = pipeline.process

                async def process(
                    player_text: str, progress_callback: Any = None,
                ) -> Any:
                    session.combat_state = combat_state  # l'action bootstrap un combat
                    return await original_process(player_text, progress_callback)

                pipeline.process = process
                return pipeline

        factory = CombatStartingFactory(
            _result_with_pending(["je fouille son corps"]),
        )
        cog._pipeline_factory = factory
        session.combat_turn_manager = None

        msg = _campaign_message(bot)
        await cog.on_message(msg)  # type: ignore[arg-type]

        assert factory.process_calls == ["je danse"]  # pas de 2e passe
        # L'intention abandonnée est annoncée.
        assert msg.channel.send.await_count >= 2  # progress + annonce drop
```

Note : `_success_output()` et `_campaign_message()` sont ajoutés à ce même fichier par la Task 5 — ne pas les redéfinir. L'annonce des intentions abandonnées est couverte par le 3e test (assertion sur `msg.channel.send.await_count`).

- [ ] **Step 2: Vérifier l'échec**

Run: `uv run pytest tests/bot/test_action_handler_cog.py::TestPendingIntentChaining -v`
Expected: FAIL — `process_calls` ne contient que le premier texte (aucun chaînage).

- [ ] **Step 3: Implémenter**

Dans `bot/cogs/action_handler.py` :

1. Constante de module (près de `looks_like_action`) :

```python
MAX_CHAINED_INTENTS = 2
"""Nombre MAXIMAL d'actions exécutées pour un seul message joueur
(la première + les intentions chaînées). Cap appliqué côté cog : quel que
soit le découpage du 4b, jamais plus de 2 narrations par message."""
```

2. Helper de module (après `_strip_bot_mention`) :

```python
def _build_dropped_intents_embed(
    dropped: list[str], language: str,
) -> discord.Embed:
    """Annonce des intentions non exécutées — jamais de perte silencieuse."""
    if language == "fr":
        title = "⏭ Intention(s) non exécutée(s)"
        hint = "Retape-la pour la jouer."
    else:
        title = "⏭ Unplayed intent(s)"
        hint = "Type it again to play it."
    lines = "\n".join(f"• {intent}" for intent in dropped)
    return discord.Embed(
        title=title,
        description=f"{lines}\n\n{hint}",
        color=0x95A5A6,
    )
```

3. Signature de `_run_pipeline` :

```python
    async def _run_pipeline(
        self,
        message: discord.Message,
        session: Any,
        raw_text: str,
        *,
        chain_budget: int = MAX_CHAINED_INTENTS - 1,
    ) -> None:
```

4. Tout à la fin de `_run_pipeline`, après le log `"ACTION done ..."` :

```python
        await self._chain_pending_intents(
            message, session, result, chain_budget,
        )
```

5. Nouvelle méthode après `_run_pipeline` :

```python
    async def _chain_pending_intents(
        self,
        message: discord.Message,
        session: Any,
        result: PipelineOutput | None,
        chain_budget: int,
    ) -> None:
        """Exécute la prochaine intention en attente, ou annonce l'abandon.

        Règles (spec 2026-07-20-interpreter-robustness) :
        - cap global ``MAX_CHAINED_INTENTS`` actions par message joueur ;
        - jamais de chaînage quand un combat est actif (y compris un combat
          bootstrappé par la première action) ;
        - toute intention abandonnée est annoncée — pas de perte silencieuse.
        """
        if not isinstance(result, ActionPipelineResult):
            return
        pending = [
            intent
            for intent in result.interpreted_action.pending_intents
            if intent.strip()
        ]
        if not pending:
            return

        in_combat = (
            session.combat_state is not None and session.combat_state.is_active
        )
        next_intent = (
            pending[0] if (chain_budget > 0 and not in_combat) else None
        )
        dropped = pending[1:] if next_intent is not None else pending

        if dropped:
            try:
                await message.channel.send(
                    embed=_build_dropped_intents_embed(
                        dropped, session.language,
                    ),
                )
            except _SEND_ERRORS:
                logger.warning(
                    "ACTION dropped-intents send failed campaign=%s",
                    session.campaign.id,
                )

        if next_intent is not None:
            logger.info(
                "ACTION chained intent campaign=%s budget=%d text=%r",
                session.campaign.id, chain_budget, next_intent[:100],
            )
            await self._run_pipeline(
                message, session, next_intent,
                chain_budget=chain_budget - 1,
            )
```

- [ ] **Step 4: Vérifier le vert**

Run: `uv run pytest tests/bot/test_action_handler_cog.py tests/bot/test_action_handler_resilience.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/cogs/action_handler.py tests/bot/test_action_handler_cog.py
git commit -m "feat(bot): chaînage borné des multi-intentions hors combat, abandon toujours annoncé"
```

---

### Task 7: ScenarioRunner auto-confirm + gates qualité + handoff

**Files:**
- Modify: `tests/scenarios/scenario_runner.py` (auto-confirm après `pipeline.process`)
- Modify: `tasks/todo.md` (progress + review)
- Test: suites complètes (`uv run pytest`, `ruff`, `mypy`)

**Interfaces:**
- Consumes: `LowConfidenceResult` (Task 3), `pipeline.process_interpreted_action` (existant).
- Produces: rien de nouveau — le runner headless ne bloque jamais sur une confirmation.

- [ ] **Step 1: Implémenter l'auto-confirm**

Dans `tests/scenarios/scenario_runner.py`, dans la méthode qui fait `result = await pipeline.process(text)` (~ligne 968), juste après cet appel :

```python
        # Confiance basse : le runner headless auto-confirme — un scénario
        # ne peut pas cliquer « Oui ». Le simulateur exerce ainsi le même
        # chemin de reprise que le vrai bouton.
        from bot.action_pipeline import LowConfidenceResult

        if isinstance(result, LowConfidenceResult):
            result = await pipeline.process_interpreted_action(
                result.interpreted_action,
            )
```

- [ ] **Step 2: Suite scénarios verte**

Run: `uv run pytest tests/scenarios -q`
Expected: PASS — les scénarios mock fixent des confidences hautes ; l'auto-confirm ne change rien pour eux mais protège les runs simulateur réels.

- [ ] **Step 3: Gates qualité complets**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy .`
Expected: pytest tout vert (2913+ tests), ruff sans sortie, mypy `Success: no issues found`.
Corriger toute régression avant de continuer — aucune tâche n'est « done » avec un gate rouge.

- [ ] **Step 4: Handoff**

Mettre à jour `tasks/todo.md` : section de review de l'axe robustesse interpreter (ce qui a été livré : fallback, gate, chaînage ; renvoyer vers la spec et ce plan ; noter comme suite possible la vérification live Discord via le tester bot — skill discord-live-testing — notamment le rendu réel de la vue de confirmation et d'un chaînage).

- [ ] **Step 5: Commit**

```bash
git add tests/scenarios/scenario_runner.py tasks/todo.md
git commit -m "test(scenarios): auto-confirmation des interprétations basse confiance dans le runner headless"
```
