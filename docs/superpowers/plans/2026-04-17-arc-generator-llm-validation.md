# Arc Generator — LLM Validation Robustness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Éliminer les fallbacks `generic_boss` et les erreurs de validation Pydantic lors de la génération d'arc en renforçant le prompt LLM et en ajoutant une étape de sanitisation des données brutes avant validation.

**Architecture:** Deux couches de défense — (1) le prompt est rendu plus explicite pour réduire les erreurs à la source ; (2) `ArcGenerator._sanitize_arc_data()` coerce les valeurs malformées connues avant que Pydantic ne valide, rendant la génération tolérante aux hallucinations mineures du LLM.

**Tech Stack:** Python 3.12, Pydantic v2, pytest, `ai/arc_generator.py`, `ai/prompts/system_arc_generator.txt`

---

## Contexte

Trois types d'erreurs sont observés dans les logs :

1. **`state_flags` non-booleans** — Le LLM retourne `{"location_explored": "place_centrale"}` au lieu de `{"location_explored": true}`. Le modèle confond la clé (nom du flag) avec la valeur (toujours un booléen).

2. **`damage_type` synonyme** — Le LLM retourne `"Electricity"` au lieu de `"Lightning"` (enum exact dans `engine/inventory.py`).

3. **`target_scope` hybride** — Le LLM retourne `"all_enemies_in_zone"` (inexistant) au lieu de `"all_enemies"` ou `"zone"` (valeurs du `TargetScope` Literal dans `engine/npc_stat_block.py`).

Le retry dans `bot/llm_retry.py` relance sans feedback d'erreur → même résultat.

---

## Fichiers modifiés

| Fichier | Action |
|---------|--------|
| `ai/prompts/system_arc_generator.txt` | Modifier — durcir les instructions sur `state_flags` et enums |
| `ai/arc_generator.py` | Modifier — ajouter `_sanitize_arc_data()` appelé avant `model_validate` |
| `tests/ai/test_arc_generator.py` | Modifier — ajouter classe `TestSanitizeArcData` |

---

## Task 1 — Hardening du prompt

**Fichiers :**
- Modifier : `ai/prompts/system_arc_generator.txt`

- [ ] **Step 1 : Ajouter une section `state_flags` explicite dans le prompt**

Remplacer la ligne 67 :
```
"state_flags": {"<flag_name>": true},
```
par :
```
"state_flags": {"<flag_name>": true},
```
Puis, **après la ligne 41** (après la liste des `completion_trigger.type`), ajouter le bloc suivant :

```
- state_flags values are ALWAYS boolean true or false — NEVER strings like "place_centrale" or "compromised".
  * BAD:  "state_flags": {"location_explored": "place_centrale", "door_open": "yes"}
  * GOOD: "state_flags": {"location_explored": true, "door_open": true}
  The flag NAME carries the meaning; the value is always true (flag is set) or false (flag is cleared).
```

- [ ] **Step 2 : Durcir la section enums du villain stat block (lignes 158-163)**

Remplacer :
```
- All `damage_type`, `condition_name`, `save_ability`, `target_scope`, and
  `kind` values MUST use the engine's exact enum casing:
  * `damage_type` is **TitleCase** (`"Slashing"`, not `"slashing"`).
  * `save_ability` is **UPPERCASE** three-letter code (`"WIS"`, `"DEX"`).
  * `target_scope` and `kind` are **lowercase** (`"single"`, `"damage"`).
  * `condition_name` is **TitleCase** (`"Frightened"`, `"Poisoned"`).
```
par :
```
- All `damage_type`, `condition_name`, `save_ability`, `target_scope`, and
  `kind` values MUST use the engine's exact enum casing. NO synonyms allowed:
  * `damage_type` is **TitleCase** — use EXACTLY one of:
    Slashing, Piercing, Bludgeoning, Fire, Cold, Lightning, Thunder, Poison, Radiant, Necrotic, Force
    BAD: "Electricity", "Electric" → use "Lightning". BAD: "Holy" → use "Radiant".
  * `save_ability` is **UPPERCASE** three-letter code: STR, DEX, CON, INT, WIS, CHA
  * `target_scope` is **lowercase** — use EXACTLY one of: single, zone, all_enemies, all_allies_in_zone, self
    BAD: "all_enemies_in_zone" (does not exist) → use "all_enemies" or "zone".
  * `kind` is **lowercase** — use exactly one of: damage, heal, condition, move, buff, debuff, aoe_damage
  * `condition_name` is **TitleCase** (`"Frightened"`, `"Poisoned"`).
```

- [ ] **Step 3 : Vérifier que le fichier est valide (pas de coupure accidentelle)**

```bash
uv run python -c "from pathlib import Path; p = Path('ai/prompts/system_arc_generator.txt'); print(f'OK — {len(p.read_text())} chars')"
```
Expected : `OK — <N> chars` (N > 1800)

- [ ] **Step 4 : Commit**

```bash
git add ai/prompts/system_arc_generator.txt
git commit -m "fix(arc-generator): harden LLM prompt — explicit boolean state_flags + exact enum values"
```

---

## Task 2 — Sanitisation des données brutes avant validation

**Fichiers :**
- Modifier : `ai/arc_generator.py` (lignes 73-85, méthode `generate`)

- [ ] **Step 1 : Écrire les tests qui échouent (régression) avant d'implémenter**

Dans `tests/ai/test_arc_generator.py`, ajouter la classe suivante **après** `TestBeatCompletionModels` :

```python
class TestSanitizeArcData:
    """Unit tests for ArcGenerator._sanitize_arc_data()."""

    def test_state_flags_string_coerced_to_true(self):
        """Non-bool truthy string in state_flags → True."""
        data = {
            "beats": [
                {
                    "beat_number": 1,
                    "on_complete": {
                        "state_flags": {
                            "location_explored": "place_centrale",
                            "door_open": "yes",
                        }
                    },
                }
            ],
            "villain_stat_block": None,
        }
        ArcGenerator._sanitize_arc_data(data)
        flags = data["beats"][0]["on_complete"]["state_flags"]
        assert flags == {"location_explored": True, "door_open": True}

    def test_state_flags_empty_string_coerced_to_false(self):
        """Empty string in state_flags → False."""
        data = {
            "beats": [{"beat_number": 1, "on_complete": {"state_flags": {"flag": ""}}}],
            "villain_stat_block": None,
        }
        ArcGenerator._sanitize_arc_data(data)
        assert data["beats"][0]["on_complete"]["state_flags"] == {"flag": False}

    def test_state_flags_bool_untouched(self):
        """Existing booleans pass through unchanged."""
        data = {
            "beats": [
                {"beat_number": 1, "on_complete": {"state_flags": {"a": True, "b": False}}}
            ],
            "villain_stat_block": None,
        }
        ArcGenerator._sanitize_arc_data(data)
        assert data["beats"][0]["on_complete"]["state_flags"] == {"a": True, "b": False}

    def test_state_flags_missing_on_complete_no_crash(self):
        """Beats without on_complete or state_flags don't crash."""
        data = {
            "beats": [{"beat_number": 1}, {"beat_number": 2, "on_complete": {}}],
            "villain_stat_block": None,
        }
        ArcGenerator._sanitize_arc_data(data)  # must not raise

    def test_damage_type_electricity_normalized(self):
        """'Electricity' synonym is normalized to 'Lightning' in attacks."""
        data = {
            "beats": [],
            "villain_stat_block": {
                "attacks": [{"damage_type": "Electricity"}],
                "signature_abilities": [],
                "legendary_actions": [],
            },
        }
        ArcGenerator._sanitize_arc_data(data)
        assert data["villain_stat_block"]["attacks"][0]["damage_type"] == "Lightning"

    def test_damage_type_signature_effects_normalized(self):
        """'Electricity' in signature ability effects is normalized."""
        data = {
            "beats": [],
            "villain_stat_block": {
                "attacks": [],
                "signature_abilities": [
                    {"effects": [{"damage_type": "Electricity"}]}
                ],
                "legendary_actions": [],
            },
        }
        ArcGenerator._sanitize_arc_data(data)
        effect = data["villain_stat_block"]["signature_abilities"][0]["effects"][0]
        assert effect["damage_type"] == "Lightning"

    def test_damage_type_legendary_effects_normalized(self):
        """'Electricity' in legendary action effects is normalized."""
        data = {
            "beats": [],
            "villain_stat_block": {
                "attacks": [],
                "signature_abilities": [],
                "legendary_actions": [
                    {"effects": [{"damage_type": "Electricity"}]}
                ],
            },
        }
        ArcGenerator._sanitize_arc_data(data)
        effect = data["villain_stat_block"]["legendary_actions"][0]["effects"][0]
        assert effect["damage_type"] == "Lightning"

    def test_target_scope_all_enemies_in_zone_normalized(self):
        """'all_enemies_in_zone' (invalid hybrid) → 'all_enemies'."""
        data = {
            "beats": [],
            "villain_stat_block": {
                "attacks": [],
                "signature_abilities": [
                    {"effects": [{"target_scope": "all_enemies_in_zone"}]}
                ],
                "legendary_actions": [],
            },
        }
        ArcGenerator._sanitize_arc_data(data)
        effect = data["villain_stat_block"]["signature_abilities"][0]["effects"][0]
        assert effect["target_scope"] == "all_enemies"

    def test_target_scope_legendary_normalized(self):
        """'all_enemies_in_zone' in legendary action effects → 'all_enemies'."""
        data = {
            "beats": [],
            "villain_stat_block": {
                "attacks": [],
                "signature_abilities": [],
                "legendary_actions": [
                    {"effects": [{"target_scope": "all_enemies_in_zone"}]}
                ],
            },
        }
        ArcGenerator._sanitize_arc_data(data)
        effect = data["villain_stat_block"]["legendary_actions"][0]["effects"][0]
        assert effect["target_scope"] == "all_enemies"

    def test_no_villain_stat_block_no_crash(self):
        """Absent villain_stat_block doesn't crash the sanitizer."""
        data = {"beats": [], "villain_stat_block": None}
        ArcGenerator._sanitize_arc_data(data)  # must not raise
```

- [ ] **Step 2 : Vérifier que les tests échouent (AttributeError car méthode pas encore là)**

```bash
uv run pytest tests/ai/test_arc_generator.py::TestSanitizeArcData -v 2>&1 | head -20
```
Expected : `AttributeError: type object 'ArcGenerator' has no attribute '_sanitize_arc_data'`

- [ ] **Step 3 : Implémenter `_sanitize_arc_data()` dans `ai/arc_generator.py`**

Ajouter la constante de synonymes et la méthode après `_resolve_villain_stat_block` (après ligne 113) :

```python
# Synonyms the LLM occasionally emits instead of the exact engine enum values.
_DAMAGE_TYPE_SYNONYMS: dict[str, str] = {
    "Electricity": "Lightning",
    "Electric": "Lightning",
    "Holy": "Radiant",
    "Unholy": "Necrotic",
    "Shadow": "Necrotic",
    "Acid": "Poison",
}

_TARGET_SCOPE_SYNONYMS: dict[str, str] = {
    "all_enemies_in_zone": "all_enemies",
    "all_allies": "all_allies_in_zone",
    "enemies": "all_enemies",
}


@staticmethod
def _sanitize_arc_data(data: dict[str, Any]) -> None:
    """Repair known LLM output quirks in-place before Pydantic validation.

    Handles:
    - state_flags values that are strings instead of booleans.
    - damage_type synonym normalization (e.g. "Electricity" → "Lightning").
    - target_scope invalid hybrids (e.g. "all_enemies_in_zone" → "all_enemies").
    """
    # --- state_flags coercion ---
    for beat in data.get("beats") or []:
        on_complete = beat.get("on_complete")
        if not isinstance(on_complete, dict):
            continue
        flags = on_complete.get("state_flags")
        if not isinstance(flags, dict):
            continue
        on_complete["state_flags"] = {
            k: (v if isinstance(v, bool) else bool(v))
            for k, v in flags.items()
        }

    # --- villain stat block enum normalization ---
    stat = data.get("villain_stat_block")
    if not isinstance(stat, dict):
        return

    def _fix_effect(effect: Any) -> None:
        if not isinstance(effect, dict):
            return
        dt = effect.get("damage_type")
        if isinstance(dt, str) and dt in ArcGenerator._DAMAGE_TYPE_SYNONYMS:
            effect["damage_type"] = ArcGenerator._DAMAGE_TYPE_SYNONYMS[dt]
        ts = effect.get("target_scope")
        if isinstance(ts, str) and ts in ArcGenerator._TARGET_SCOPE_SYNONYMS:
            effect["target_scope"] = ArcGenerator._TARGET_SCOPE_SYNONYMS[ts]

    for attack in stat.get("attacks") or []:
        if isinstance(attack, dict):
            dt = attack.get("damage_type")
            if isinstance(dt, str) and dt in ArcGenerator._DAMAGE_TYPE_SYNONYMS:
                attack["damage_type"] = ArcGenerator._DAMAGE_TYPE_SYNONYMS[dt]

    for ability in stat.get("signature_abilities") or []:
        if isinstance(ability, dict):
            for effect in ability.get("effects") or []:
                _fix_effect(effect)

    for action in stat.get("legendary_actions") or []:
        if isinstance(action, dict):
            for effect in action.get("effects") or []:
                _fix_effect(effect)
```

- [ ] **Step 4 : Appeler `_sanitize_arc_data` dans `generate()` avant `model_validate`**

Dans `ai/arc_generator.py`, modifier la méthode `generate()` — remplacer les lignes 73-79 :

```python
        # --- Villain stat block parsing with generic_boss fallback (task 42) ---
        # Validate the stat block separately so we can fallback cleanly when the
        # LLM emits an invalid or missing payload, without losing the rest of
        # the arc.
        data["villain_stat_block"] = self._resolve_villain_stat_block(data).model_dump()

        arc = StoryArc.model_validate(data)
```
par :
```python
        # Repair known LLM output quirks before validation.
        self._sanitize_arc_data(data)

        # Validate the stat block separately so we can fallback cleanly when the
        # LLM emits an invalid or missing payload, without losing the rest of
        # the arc.
        data["villain_stat_block"] = self._resolve_villain_stat_block(data).model_dump()

        arc = StoryArc.model_validate(data)
```

- [ ] **Step 5 : Lancer les tests de la nouvelle classe**

```bash
uv run pytest tests/ai/test_arc_generator.py::TestSanitizeArcData -v
```
Expected : tous les tests `PASSED`

- [ ] **Step 6 : Lancer toute la suite pour détecter les régressions**

```bash
uv run pytest tests/ai/test_arc_generator.py -v
```
Expected : toute la suite `PASSED`

- [ ] **Step 7 : Lancer ruff + mypy**

```bash
uv run ruff check ai/arc_generator.py && uv run mypy ai/arc_generator.py
```
Expected : pas d'erreurs

- [ ] **Step 8 : Commit**

```bash
git add ai/arc_generator.py tests/ai/test_arc_generator.py
git commit -m "fix(arc-generator): sanitize LLM output before Pydantic validation — coerce state_flags booleans, normalize damage_type/target_scope synonyms"
```

---

## Vérification end-to-end

```bash
# Suite complète
uv run pytest tests/ -v --tb=short 2>&1 | tail -20
```
Expected : `passed` sans aucun `FAILED`.

Pour tester en conditions réelles, lancer le bot et déclencher `/start_campaign` avec un thème quelconque. Les logs ne doivent plus contenir :
- `WARNING bot.llm_retry: GENERATION ... attempt_failed`
- `WARNING ai.arc_generator: Invalid villain_stat_block ... falling back to generic_boss`
