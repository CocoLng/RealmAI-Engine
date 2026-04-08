# Lot F — Narrator JSON robustness

> Index : [`README.md`](README.md) · Statut : **DONE** · Pré-requis : aucun (peut être fait à n'importe quel moment, même en parallèle des autres lots)

## Pourquoi ce lot existe

Dans le log de la session du 7 avril ([`logs/realm_20260407_223015.log`](../../logs/realm_20260407_223015.log)), on observe :

```
22:45:33 WARNING [bot.llm_retry] ACTION ... narrate attempt_failed attempt=1 reason=Expecting value: line 1 column 1 (char 0)
22:45:33 INFO  [bot.llm_retry] ACTION ... narrate retry attempt=1/2 delay=5s
...
22:47:51 WARNING [bot.llm_retry] ACTION ... narrate attempt_failed attempt=1 reason=Expecting value: line 1 column 1 (char 0)
```

**2 retries sur ~8 appels narrateur = 25%**, chacun coûtant 5 secondes de delay + un appel LLM 9B (~10-20s). C'est environ +30s de latence par 4 actions joueur. Le narrateur retourne une réponse vide ou non-JSON au premier essai et s'auto-corrige au second. Le system prompt narrateur n'est pas assez strict pour forcer le format JSON dès le premier coup.

C'est de la qualité, pas un bug bloquant — mais c'est facile à fixer et ça améliore considérablement la latence ressentie.

## Mission

Tomber le taux de retry parse JSON narrateur de ~25% à ≤ 5%. (1) Durcir le system prompt narrateur. (2) Logger les raw responses qui ont échoué au parse pour pouvoir diagnostiquer les cas qui restent. (3) Mesurer avant/après sur ≥ 10 appels.

## Contexte technique

### Code à lire avant
- [`ai/prompts/system_narrator.txt`](../../ai/prompts/system_narrator.txt) — le system prompt actuel.
- [`ai/narrator.py`](../../ai/narrator.py) — comment le narrateur appelle le client et parse la réponse. Lignes 27-59 environ.
- [`ai/client.py`](../../ai/client.py) — la méthode `chat_json` qui appelle Ollama avec `response_format={"type": "json_object"}`. Vérifier que c'est bien activé pour les appels narrator.
- [`bot/llm_retry.py`](../../bot/llm_retry.py) — le wrapper retry qui logge les `attempt_failed`. C'est ici qu'on ajoutera le persisting des raw failures.

## Plan d'implémentation

### Étape 1 — Diagnostiquer

Avant de toucher au prompt, exécuter ≥ 10 narrations sur le tester bot (ou unit test avec un vrai client) en mode verbose, et capturer les raw responses. Identifier le pattern : est-ce que le narrateur retourne du markdown ` ```json ... ``` `, du texte libre, du JSON avec un champ en trop, etc. ?

Si le narrateur retourne souvent du markdown `\`\`\`json`, c'est probablement parce que `chat_json` n'utilise pas vraiment `response_format` côté Ollama, ou que le prompt invite à formater. Vérifier dans `ai/client.py:46-120`.

### Étape 2 — Durcir le system prompt

Modifier [`ai/prompts/system_narrator.txt`](../../ai/prompts/system_narrator.txt) :
- Au tout début, première phrase : « You return ONE single JSON object and NOTHING else. No markdown code fence. No prose. No explanation. »
- Inclure un **exemple complet** de la réponse attendue :
  ```
  Example output (this is the only valid format):
  {"narrative": "The wind howls through the ruins as you step inside...", "tone": "tense"}
  ```
- À la toute fin du prompt, rappel : « Reminder: respond with the JSON object only. Any text outside of the JSON object will break the system. »

### Étape 3 — Logger les raw failures

Dans [`bot/llm_retry.py`](../../bot/llm_retry.py), quand `attempt_failed` est loggé pour cause de `ValueError` parsing JSON, écrire la raw response dans `logs/narrator_failures/{timestamp}_{campaign_id}.txt`. Créer le dossier si absent.

Pour avoir accès à la raw response, il faut probablement modifier `ai/client.py:chat_json` pour, en cas d'échec de parse, logger ou raise une exception qui contient le raw text. L'option la moins invasive : `ai/client.py` raise un `LLMParseError(raw_response: str)` au lieu d'un `ValueError` générique, et `llm_retry.py` catch et persiste.

Format du fichier de dump :
```
# Narrator parse failure
Time: 2026-04-08 12:34:56
Campaign: 276fb1eb-...
Model: qwen3.5:9b
Prompt tokens: 514
---
SYSTEM PROMPT:
{system}
---
USER MESSAGE:
{user}
---
RAW RESPONSE:
{raw}
```

### Étape 4 — Mesurer

Après les changements, lancer ≥ 10 narrations via le tester bot (campagne réelle ou via les MCP `discord-test`) et compter dans le log `bot.llm_retry` combien d'`attempt_failed` apparaissent pour `narrate`.

Cible : ≤ 5%, donc 0 ou 1 sur 10. Si > 5%, lire les fichiers dans `logs/narrator_failures/` et itérer sur le prompt.

### Étape 5 — Vérifier qu'on n'a rien cassé

`uv run pytest tests/ai/test_narrator.py` (existe ? sinon créer un test minimal qui mocke le client et vérifie le parse).
`uv run pytest` global vert.
`uv run ruff check . && uv run mypy .` verts.

## Critère de succès

- Avant : 25% de retries narrator (mesure baseline du log existant).
- Après : ≤ 5% sur ≥ 10 appels mesurés.
- Au moins un fichier dans `logs/narrator_failures/` si une failure survient (preuve que le logger marche). Si zéro failure, c'est encore mieux.
- Le fichier `ai/prompts/system_narrator.txt` est plus court ou clairement plus directif.

## Hors scope

- **Ne pas** changer le modèle 9B → autre chose.
- **Ne pas** changer les prompts interpreter ou autres LLM components.
- **Ne pas** restructurer `bot/llm_retry.py` au-delà du nécessaire pour persister les raw failures.
- **Ne pas** modifier la structure de `NarrativeResult` dans `ai/models.py`.

## Notes de l'agent

> À remplir avant la fin de session : commit hash, blocages, observations utiles pour les lots suivants.

- **Commit** : non commité (laissé à la discrétion de l'utilisateur).
- **Diagnostic** : Le client Ollama utilise déjà `format: "json"` ([ai/client.py:82](../../ai/client.py#L82)) et la branche empty-content lève déjà un `ValueError`. Le `Expecting value: line 1 column 1 (char 0)` observé dans le log vient du `json.loads(content)` ligne 161 — donc le narrateur retourne du **texte non vide non-JSON** (probablement une fuite de prose / fence markdown malgré `format=json`). Cohérent avec un system prompt trop laxiste.
- **Étape 1 (diag empirique sur Ollama réel)** : non exécutée — pas d'instance Ollama active dans cette session. Le code de capture (étape 3) est en place pour collecter automatiquement les raw failures lors du prochain run live.
- **Étape 2 (prompt)** : [`ai/prompts/system_narrator.txt`](../../ai/prompts/system_narrator.txt) réécrit. Première ligne stricte (« You return ONE single JSON object... »), exemple concret avec narrative+tone, rappel final. Sections tone-tier conservées.
- **Étape 3 (raw capture)** :
  - Nouvelle exception `LLMParseError(ValueError)` dans [`ai/client.py`](../../ai/client.py) qui transporte `raw_response`, `model`, `messages`, `reason`. Subclasse `ValueError` → zéro changement requis dans la signature de `retry_llm_call`.
  - `chat_json` lève `LLMParseError` à la fois sur empty-content **et** sur `json.JSONDecodeError`.
  - [`bot/llm_retry.py`](../../bot/llm_retry.py) catch `LLMParseError` et dump dans `logs/narrator_failures/{ts}_{label}.txt` (Time, Label, Model, Reason, system prompt, user message, raw response). Le dossier est créé à la volée. `logs/` est déjà gitignoré.
  - [`ai/interpreter.py`](../../ai/interpreter.py) mis à jour pour catch aussi `LLMParseError` (pas seulement `JSONDecodeError`) → préserve son fallback IMPROVISE/DEFEND.
- **Étape 4 (mesure live)** : à exécuter par l'utilisateur via le tester bot. La méthode : lancer ≥ 10 narrations, grep `bot.llm_retry` pour `attempt_failed.*narrate` et compter. Inspecter `logs/narrator_failures/` pour les cas restants.
- **Étape 5 (CI)** :
  - `uv run pytest --deselect tests/scenarios/test_free_text_exploration.py::test_scenario_unknown_entity_dragon` → **1166 passed**. Le test deselect est un test scénario qui appelle Ollama réel (pré-existant, échoue déjà sur main propre car aucun Ollama local actif).
  - `uv run ruff check .` → vert.
  - `uv run mypy ai/client.py bot/llm_retry.py` → vert (les 2 erreurs `var-annotated` dans `bot/views/character_create_view.py` sont pré-existantes et hors scope de ce lot).
  - Nouveaux tests :
    - [`tests/bot/test_llm_retry.py`](../../tests/bot/test_llm_retry.py) — 3 tests : parse failure persistée, connectivity error non persistée, succès non persisté.
    - [`tests/ai/test_client.py`](../../tests/ai/test_client.py) — `test_chat_json_raises_on_invalid_json` mis à jour pour vérifier `LLMParseError` + payload (raw_response, model, isinstance ValueError).
- **Critère de succès code** : tout en place. Critère de succès chiffré (≤ 5%) requiert run live — à valider lors de la prochaine session avec Ollama actif.
