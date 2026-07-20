# Axe robustesse interpreter — Design

**Date** : 2026-07-20
**Statut** : approuvé (fallback IMPROVISE + confirmation, exécution séquentielle
bornée, confirmation Oui/Reformuler à seuil 0.6 — les trois options
recommandées retenues telles quelles)

## Problème

Trois faiblesses du maillon interpreter (Qwen 3.5 4b), constatées sur le
pipeline actuel :

1. **Échec dur** — un `action_type` inconnu ou un JSON invalide lève
   `LLMParseError` ; après les 2 retries de `retry_llm_call`, le joueur
   reçoit un embed `FAILED` générique. Tour perdu, aucune explication en jeu.
2. **Multi-intentions ignorées** — « je ramasse la clé et je vais au nord »
   n'est pas géré : le modèle choisit une intention arbitrairement, l'autre
   est perdue en silence.
3. **Confiance jamais consommée** — `InterpretedAction.confidence` est
   calibrée dans le prompt (≥ 0.85 clair, ≤ 0.5 flou) mais aucun code aval ne
   la lit : une interprétation douteuse s'exécute comme une certaine.

**Principe directeur** : aucun tour perdu en silence (leçon H11 — un fallback
silencieux vers DEFEND consommait le tour du joueur). Les trois mécanismes
composent : le fallback IMPROVISE produit une confidence basse → qui
déclenche le gate de confirmation → qui protège aussi le chaînage
multi-intentions.

## 1. Contrat & prompt — multi-intentions

- `engine/contracts.py` : `InterpretedAction` gagne
  `pending_intents: list[str] = Field(default_factory=list)` — les phrases
  brutes des intentions **non encore exécutées**, dans l'ordre.
- `ai/prompts/system_interpreter.txt` : nouvelle règle — si le joueur exprime
  plusieurs actions séquentielles distinctes, classifier **la première
  seulement** et recopier les phrases restantes telles quelles dans
  `pending_intents`. Exemples positifs (« je ramasse la clé et je vais au
  nord » → Pick Up + `["je vais au nord"]`) et négatifs (« je regarde autour
  de moi et j'écoute » = une seule intention d'observation — ne pas
  sur-découper ; une énumération descriptive n'est pas une séquence
  d'actions).
- **Pas de classification anticipée** de la 2ᵉ intention : elle sera
  ré-interprétée contre le **nouveau** contexte de scène après exécution de
  la 1ʳᵉ (un Move change la scène ; un Pick Up change les objets visibles).
- `ai/interpreter.py` : parse `pending_intents` défensivement (liste de
  strings uniquement, entrées non-string ignorées, tronqué à 3 entrées),
  `NUM_PREDICT` 384 → 448 (marge pour le champ ajouté).

## 2. Fallback IMPROVISE

Dans `bot/pipeline/interpret.py:call_interpreter` : quand `retry_llm_call`
épuise ses retries sur `LLMParseError` — et **uniquement** elle,
`OllamaUnavailableError` continue de propager (serveur down = vraie panne,
un fallback serait mensonger) — forger :

```python
InterpretedAction(
    action_type=ActionType.IMPROVISE,
    actor_name=actor_name,
    improvise_description=player_text,
    raw_input=player_text,
    confidence=FALLBACK_IMPROVISE_CONFIDENCE,  # 0.3
)
```

`0.3 < 0.6` → le fallback passe **automatiquement** par le gate de
confirmation (§3) : le joueur valide avant que quoi que ce soit s'exécute.
Le comportement de retry actuel est intégralement préservé — le fallback
n'intervient qu'après épuisement. La docstring H11 de `Interpreter.interpret`
est mise à jour pour documenter la division du travail (le raise reste
nécessaire pour déclencher les retries ; le filet est en aval).

## 3. Gate de confiance basse

- `bot/pipeline/orchestrator.py` : nouveau modèle
  `LowConfidenceResult(interpreted_action: InterpretedAction)` ajouté à
  l'union `PipelineOutput`.
- Dans `PipelineRunner.process()`, juste après `call_interpreter` : si
  `confidence < CONFIDENCE_CLARIFY_THRESHOLD` (0.6) **et**
  `action_type != QUESTION` (une question est gratuite et sans effet d'état —
  la confirmer serait de la friction pure) → retour immédiat de
  `LowConfidenceResult`, pipeline en pause **avant** résolution d'entités
  (pas de concurrence avec le flux `AmbiguityResult`).
- `process_interpreted_action()` ne gate **jamais** → c'est la voie de
  reprise après « Oui », zéro risque de boucle. (C'est aussi la voie des
  boutons combat et de l'auto-Dodge, déjà structurés — le gate n'a pas de
  sens là.)
- Nouveau `bot/views/confirm_action_view.py` :
  - Embed « J'ai compris : *[description lisible]*. C'est bien ça ? » +
    boutons **Oui** / **Reformuler**.
  - Calqué sur `ClarificationView` : author-only (`interaction_check`),
    timeout 120 s traité comme Reformuler, tour non consommé dans tous les
    cas sauf Oui.
  - Helper `describe_action(action, language) -> str` FR/EN pour le résumé
    lisible (« Attaque sur X », « Improvisation : … », « Déplacement vers
    X », …).
- `bot/cogs/action_handler.py` : nouvelle branche miroir de
  `_render_ambiguity` — **Oui** → `pipeline.process_interpreted_action(action)`
  puis rendu du résultat final (le résultat FINAL est ce que la main de
  combat doit voir, même contrainte que la désambiguïsation) ;
  **Reformuler** → édition du message de progression en « Action annulée —
  reformule », aucun état modifié.

## 4. Chaînage multi-intentions (côté cog)

Après un rendu réussi (`ActionPipelineResult`), `action_handler` lit
`result.interpreted_action.pending_intents` :

- **Cap : `MAX_CHAINED_INTENTS = 2` actions exécutées au total** par message
  joueur (la 1ʳᵉ + 1 chaînée), quel que soit le découpage du modèle. Le cap
  est appliqué côté cog — une intention chaînée elle-même multi-intention ne
  peut pas dépasser le budget.
- La 2ᵉ intention repasse par le **flux complet** (nouveau message de
  progression, interpret → validate → resolve → narrate) — réutilisation
  totale, y compris son propre gate de confiance éventuel. La ré-interprétation
  se fait contre la scène mise à jour.
- **Stop conditions** : combat actif après la 1ʳᵉ action (« j'attaque le
  garde et je fouille » → l'attaque bootstrap un combat → pas de chaînage),
  ou cap atteint, ou la 1ʳᵉ action a échoué / été annulée.
- Toute intention abandonnée est **annoncée** : embed court « ⏭ Intention non
  exécutée : *…* — retape-la pour la jouer ». Jamais de perte silencieuse.
- En combat dès le départ : jamais de chaînage, annonce directe.
- Latence assumée : un chaînage = ~2 tours de narration (~50-90 s) — accepté
  hors combat, c'est pour ça que le cap est à 2.

## 5. Constantes

| Constante | Valeur | Emplacement |
|---|---|---|
| `CONFIDENCE_CLARIFY_THRESHOLD` | 0.6 | `bot/pipeline/orchestrator.py` |
| `FALLBACK_IMPROVISE_CONFIDENCE` | 0.3 | `bot/pipeline/interpret.py` |
| `MAX_CHAINED_INTENTS` | 2 | `bot/cogs/action_handler.py` |

(Regroupement dans un module dédié seulement si l'implémentation en fait
apparaître le besoin — YAGNI.)

## 6. Cas limites

- **Fallback + combat** : l'IMPROVISE forgé passe par la confirmation →
  jamais de tour de combat consommé sans validation explicite.
- **Question à confiance basse** : exécutée sans gate (gratuite, sans effet).
- **Simulateur autonome** (`tests/simulation`) : le driver doit
  auto-confirmer les `LowConfidenceResult` (comme il gère déjà
  `AmbiguityResult`) pour ne pas bloquer les playthroughs — à câbler à
  l'implémentation.
- **Multi-joueur** : `interaction_check` author-only, pattern existant.
- **Timeout de la vue** (120 s) : équivalent Reformuler, tour non consommé.

## 7. Tests

- **Unitaires** :
  - parsing `pending_intents` (liste valide, entrées non-string ignorées,
    clamp à 3) ;
  - forge du fallback : `LLMParseError` épuisée → IMPROVISE confidence 0.3 ;
    `OllamaUnavailableError` → propagation inchangée ;
  - gate : 0.59 → `LowConfidenceResult`, 0.60 → passe, QUESTION → jamais
    gaté, `process_interpreted_action` → jamais gaté ;
  - `describe_action` FR + EN pour chaque ActionType pertinent.
- **Cog / scénario** (ScenarioRunner, skill discord-live-testing) :
  confirmation Oui → resume et rendu final ; Reformuler → annulation propre ;
  chaînage de 2 intentions hors combat avec ré-interprétation sur scène mise
  à jour ; stop du chaînage quand la 1ʳᵉ action bootstrap un combat ;
  annonce des intentions abandonnées.
- **Qualité** : `pytest` vert, `ruff check .`, `mypy .` — comme CI.
