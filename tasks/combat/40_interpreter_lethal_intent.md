# Task 40 — Interprète : détection d'intention létale

**Phase** : 4 — Interprète & générateurs LLM (parallèle)
**Dépendances** : aucune
**Coordinateur** : [tasks/combat/README.md](README.md)

## Contexte

Un joueur peut vouloir attaquer un PNJ sans utiliser l'action `(Attack)` explicite :

- `(Improvise) je sors mon épée et je charge le marchand`
- `(Improvise) je poignarde Vellus dans le dos`
- `"je veux le tuer"`
- `"je sors mon arme"` (menace crédible)

Ces phrases doivent déclencher le combat comme si le joueur avait tapé `(Attack)`. Pour ça, l'interprète LLM doit détecter cette intention et flaguer l'action.

**Règle de détection** : une phrase est "intention létale" si elle exprime **explicitement** une volonté de faire du mal physique à une créature nommée OU visible dans la scène. Pas juste "je brandis mon épée fièrement" (posture) — il faut une **cible** et une **intention**.

## Scope

1. Ajouter un champ `is_lethal_intent: bool = False` sur `InterpretedAction` ([ai/models.py](../../ai/models.py)).
2. Étendre le prompt de l'interprète ([ai/prompts/system_interpreter.txt](../../ai/prompts/system_interpreter.txt)) avec une section "Détection d'intention létale" qui liste des exemples positifs et négatifs.
3. Étendre le parser dans [ai/interpreter.py](../../ai/interpreter.py) pour lire ce champ depuis le JSON LLM.
4. Tests unitaires sur les deux en isolation + integration avec Ollama (fake) pour vérifier le parsing.

## Fichiers à modifier

- [ai/models.py](../../ai/models.py) — modèle `InterpretedAction`.
- [ai/prompts/system_interpreter.txt](../../ai/prompts/system_interpreter.txt) — prompt.
- [ai/interpreter.py](../../ai/interpreter.py) — parser (généralement déjà générique avec `model_validate`).

## Implémentation — esquisse

```python
# ai/models.py

class InterpretedAction(BaseModel):
    # ... existing fields ...
    is_lethal_intent: bool = False
    """True if the interpreter detected explicit lethal intent toward a
    creature. Used by the combat entry module to bootstrap combat even
    when the action_type is IMPROVISE or TALK."""
```

Le parser `ai/interpreter.py` utilise probablement déjà `InterpretedAction.model_validate(json_data)` donc le nouveau champ est automatiquement parsé si le LLM le fournit.

**Prompt — section à ajouter** (au format du `system_interpreter.txt` actuel) :

```
## Détection d'intention létale

Quand une action exprime une volonté explicite de blesser, tuer ou
neutraliser par la force une créature nommée ou visible, tu DOIS inclure
`"is_lethal_intent": true` dans le JSON output.

Exemples POSITIFS (is_lethal_intent = true) :
- "Je sors mon épée et je charge le marchand"
- "Je poignarde Vellus dans le dos"
- "Je veux tuer le garde"
- "Je frappe Kaelen avec ma hache"
- "J'assomme le prisonnier d'un coup de pommeau"
- "Je tire une flèche sur l'ennemi"
- "Je lance une boule de feu sur les bandits"

Exemples NÉGATIFS (is_lethal_intent = false) :
- "Je brandis mon épée fièrement devant la foule" — posture, pas d'attaque
- "Je menace le garde avec mon arme" — intimidation, TALK
- "Je sors mon arme pour la nettoyer" — pas d'intention d'attaque
- "Je regarde mon épée" — observation
- "Je vais chercher le dragon pour le combattre" — intention future, pas immédiate
- "J'attaque la porte" — pas une créature

Règles :
1. L'intention doit cibler une CRÉATURE (PNJ ou monstre), pas un objet.
2. L'intention doit être IMMÉDIATE (ce tour), pas future ou hypothétique.
3. La VIOLENCE doit être explicite ("frapper", "poignarder", "tirer", "tuer",
   "assommer", "charger", "lancer un sort de dégâts"). La simple présence
   d'une arme ne compte pas.
4. Quand `is_lethal_intent = true` ET l'action n'est pas déjà ATTACK,
   tu dois aussi remplir `target_name` avec le nom de la créature ciblée.
5. Si tu hésites, laisse `is_lethal_intent = false`. Mieux vaut un faux
   négatif (le joueur peut retaper avec Attack explicite) qu'un faux
   positif (bootstrap de combat non désiré).
```

## Acceptance criteria

- [ ] `InterpretedAction.is_lethal_intent` existe, default False.
- [ ] Le prompt de l'interprète contient la section de détection avec exemples.
- [ ] Pour chaque exemple positif du prompt, un appel à l'interprète retourne `is_lethal_intent=True` avec un `target_name` rempli.
- [ ] Pour chaque exemple négatif, l'interprète retourne `is_lethal_intent=False`.
- [ ] Le champ est rétro-compatible : une ancienne row DB ou un ancien JSON sans ce champ produit `is_lethal_intent=False` par défaut (Pydantic default).

## Tests à ajouter

Dans `tests/ai/test_interpreter.py` :

- `test_parse_json_with_lethal_intent_flag` — JSON en input avec le flag, parse ok.
- `test_parse_legacy_json_defaults_lethal_intent_false` — JSON sans le flag, default False.

Dans `tests/ai/test_interpreter_integration.py` (ou nouveau, avec Ollama mocké) :

- `test_interpreter_detects_lethal_sword_charge` — input "je sors mon épée et charge X" → flag True, target="X".
- `test_interpreter_detects_lethal_stab` — "je poignarde X" → flag True.
- `test_interpreter_detects_lethal_spell` — "je lance une boule de feu sur les bandits" → flag True, target="bandits".
- `test_interpreter_rejects_threat_as_talk` — "je menace le garde avec mon arme" → flag False, action_type = TALK probablement.
- `test_interpreter_rejects_object_target` — "j'attaque la porte" → flag False (pas une créature).
- `test_interpreter_rejects_future_intent` — "je vais chercher le dragon pour le combattre" → flag False.

Pour les tests integration, mocker `OllamaClient.chat_json` avec des réponses JSON pré-construites selon la phrase en input.

## Hors scope

- **Ne pas** câbler dans `action_pipeline.py` — tâche [31](31_action_pipeline_combat_dispatch.md) le fera via `detect_combat_trigger`.
- **Ne pas** détecter les provocations sociales ("je vole tes pièces") — tâche [81](81_social_resolution_mid_combat.md).
- **Ne pas** gérer les intentions létales contre objets ou structures — hors scope, on reste sur les créatures.

## Validation finale

```bash
uv run pytest tests/ai/test_interpreter.py tests/ai/test_interpreter_integration.py -v
uv run ruff check ai/models.py ai/interpreter.py
uv run mypy ai/models.py ai/interpreter.py
```
