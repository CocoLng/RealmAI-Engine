# Beat Progression Engine — Design Spec

**Date** : 2026-04-25
**Status** : design (en attente d'implémentation)
**Author** : brainstorming session avec Claude Opus 4.7
**Replaces** : éléments des specs `2026-04-11-beat-progression-and-state-design.md` et `2026-04-20-directors-cut-design.md` qui se chevauchent (voir section "Migration").

---

## 1. Contexte & problème

Le système actuel de progression des Beats (transition d'une étape de quête à la suivante) est **instable**. Symptômes observés en partie réelle :

- **Joueur bloqué silencieusement** : action légitime qui devrait avancer le beat ne le fait pas (gate cachée comme `talk_reveals_count = 0`), sans feedback.
- **Double-avance / saut de beat** : une seule action fait avancer de deux beats à cause de chemins de décision concurrents.
- **Avance imprévisible** : même action, parfois ça marche, parfois non (fallback LLM avec threshold fixe 0.85, sans contexte par beat).
- **Direction narrative incohérente** : narrateur parfois en avance/retard sur l'état réel du beat.

### Cause racine

Trois chemins indépendants peuvent avancer un beat sans coordination :

1. **Déterministe** ([orchestrator.py:495-518](../../bot/pipeline/orchestrator.py)) : `completion_trigger` (talk/defeat/arrive/...) avec fuzzy match.
2. **Location-based** ([game_session.py:106-141](../../engine/game_session.py)) : fuzzy match du `location_hint` (seuil 0.7).
3. **Fallback LLM** ([orchestrator.py:519-564](../../bot/pipeline/orchestrator.py)) : si action IMPROVISE + location ~match, threshold confidence 0.85.

Ces trois chemins sont appelés **en série** dans le pipeline, sans verrouillage. Une action peut satisfaire plusieurs simultanément, ou aucun. Le Story Director, lui, ne participe pas à la décision (consultatif narratif uniquement) — son flag `beat_advanced` au narrateur est peu fiable car émis par LLM.

---

## 2. Objectifs

- **G1** : un seul point de décision pour l'avancement de beat (élimination des chemins concurrents).
- **G2** : déterminisme par défaut, LLM uniquement sur cas ambigus avec rubrique structurée.
- **G3** : feedback joueur explicite sur l'état de progression (Arc Tracker + `/hint`).
- **G4** : observabilité (logs structurés, métriques agrégées).
- **G5** : migration non-destructive (compatibilité avec arcs existants).

### Non-objectifs

- Refonte du Story Director (juridiction clarifiée mais structure conservée).
- Refonte du système d'arc generation (`ai/arc_generator.py` non touché — les nouveaux champs sont optionnels).
- Refonte du combat (`bot/cogs/combat.py` non touché).
- Introduction d'une ressource D&D Inspiration (pas implémentée, hors scope).

---

## 3. Architecture cible

```
Action joueur
    │
    ▼
[INTERPRETER 4b]  →  [VALIDATOR Python]  →  [ENGINE Python (mécaniques)]
                                                    │
                                                    ▼
                                       [BeatProgressionEngine]  ← UN SEUL POINT
                                       │
                                       ├─ Évalue conditions structurées
                                       ├─ Calcule progress_score (0-100)
                                       └─ Décide : ADVANCE | STAY | NEEDS_JUDGE
                                                    │
                                       Si NEEDS_JUDGE → [BeatJudge LLM 4b]
                                                    │
                                                    ▼
                                       [Story Director 9b] (async, cadencé)
                                                    │
                                                    ▼
                                       [NARRATOR 9b]  → Discord (embed enrichi)
```

**Principes** :
- `BeatProgressionEngine` est dans `engine/` (pure Python, anti-cheat zone, testable sans LLM).
- `BeatJudge` est dans `ai/` (LLM 4b, rubrique stricte JSON).
- `Story Director` reste séparé : cohérence narrative cross-beat, **pas** de décision de progression.
- `/hint` slash command déterministe (niveau 1-2) ou via BeatJudge verbose (niveau 3).

---

## 4. Modèle de données

### 4.1 Nouveau : `BeatObjective`

Localisation : `world/story_arc.py`

```python
class ObjectiveKind(str, Enum):
    TALK = "talk"
    DEFEAT = "defeat"
    ARRIVE = "arrive"
    EXAMINE = "examine"
    POSSESS = "possess"
    FLAG = "flag"

class GateKind(str, Enum):
    MIN_REVEALS = "min_reveals"
    MIN_DISPOSITION = "min_disposition"
    HAS_ITEM = "has_item"
    FLAG_SET = "flag_set"

class ObjectiveGate(BaseModel):
    kind: GateKind
    value: int | str

class BeatObjective(BaseModel):
    id: str                          # "talk_kaelen", "find_blood_cape"
    kind: ObjectiveKind
    target: str                      # "Kaelen", "wolf", "marketplace"
    description: str                 # phrase joueur-friendly
    required: bool = True
    fuzzy_threshold: float = 0.7
    gate: ObjectiveGate | None = None

class AdvanceRule(str, Enum):
    ALL_REQUIRED = "all_required"
    ANY = "any"
    M_OF_N = "m_of_n"
```

### 4.2 Modifié : `StoryBeat`

```python
class StoryBeat(BaseModel):
    beat_number: int
    title: str
    description: str
    location_hint: str | None = None

    # NOUVEAU
    objectives: list[BeatObjective] = []
    advance_rule: AdvanceRule = AdvanceRule.ALL_REQUIRED
    advance_threshold: int | None = None         # pour M_OF_N
    player_visible_hint: str | None = None       # pour /hint niveau 1
    judge_rubric: str | None = None              # pour BeatJudge

    on_complete: BeatEffects = BeatEffects()

    # COMPAT (deprecated, auto-migré au load)
    completion_trigger: CompletionTrigger | None = None
```

**Migration en lecture** (dans `StoryArc.model_validator`) : si `objectives == []` et `completion_trigger is not None`, génère automatiquement un `BeatObjective` unique :
```python
BeatObjective(
    id=f"legacy_{completion_trigger.type}_{completion_trigger.target}",
    kind=ObjectiveKind(completion_trigger.type),
    target=completion_trigger.target,
    description=f"{completion_trigger.type} {completion_trigger.target}",
    required=True,
)
```

### 4.3 Nouveau : état runtime (pas en DB)

```python
class ObjectiveState(BaseModel):
    status: Literal["pending", "partial", "completed"]
    last_attempt_action_id: str | None = None
    last_attempt_score: float = 0.0
    completed_at_turn: int | None = None

class BeatProgress(BaseModel):
    beat: StoryBeat
    objective_states: dict[str, ObjectiveState]
    progress_score: int                          # 0-100
    last_action_advanced: bool
```

### 4.4 Persistance — table `hint_usage`

Pour le tracking `/hint` (niveau 2 max 1×/beat, niveau 3 cooldown 5 tours) :

```python
class HintUsageRow(Base):
    __tablename__ = "hint_usage"
    campaign_id: Mapped[str] = mapped_column(primary_key=True)
    beat_number: Mapped[int] = mapped_column(primary_key=True)
    level1_uses: Mapped[int] = mapped_column(default=0)
    level2_used: Mapped[bool] = mapped_column(default=False)
    level3_last_used_turn: Mapped[int | None] = mapped_column(default=None)
```

Reset à zéro à chaque ADVANCE de beat (suppression de la ligne).

---

## 5. `BeatProgressionEngine`

Localisation : `engine/beat_progression.py` (nouveau, pure Python).

### 5.1 API publique

```python
class BeatProgressionResult(BaseModel):
    decision: Literal["ADVANCE", "STAY", "NEEDS_JUDGE"]
    progress: BeatProgress
    new_beat: StoryBeat | None = None
    judge_request: JudgeRequest | None = None
    reasons: list[str]

class BeatHistory(BaseModel):
    """Fenêtre glissante des 5 derniers tours pour détection stagnation."""
    recent_decisions: list[Literal["ADVANCE", "STAY", "NEEDS_JUDGE"]]   # max 5
    current_beat_turns: int   # nb de tours sur le beat courant

class BeatProgressionEngine:
    def evaluate(
        self,
        arc: StoryArc,
        interpreted: InterpretedAction,
        outcome: ActionOutcome,
        location: Location | None,
        history: BeatHistory,
    ) -> BeatProgressionResult:
        ...
```

**Note implémentation** : le fuzzy match utilise `difflib.SequenceMatcher.ratio()` après normalisation (lowercase, accents retirés via `unicodedata.normalize("NFKD", ...)`), conforme à l'usage existant dans [engine/game_session.py:_normalize_location](../../engine/game_session.py).

### 5.2 Algorithme

```
INPUT: arc, interpreted, outcome, location, history

1. current_beat = arc.beats[arc.current_beat_index]
   (si current_beat_index >= len(arc.beats), retourne STAY avec reason="arc_complete")

2. Pour chaque objectif `obj` du current_beat :
   a. Calcul match_score selon obj.kind :
      - TALK    : interpreted.action_type == TALK ET fuzzy(interpreted.target, obj.target)
      - DEFEAT  : outcome.target_defeated == obj.target (déterministe combat)
      - ARRIVE  : fuzzy(location.name, obj.target)
      - EXAMINE : interpreted.action_type == EXAMINE ET fuzzy(interpreted.target, obj.target)
      - POSSESS : obj.target in player.inventory.items
      - FLAG    : world_state.flags.get(obj.target) == True

   b. Si match_score >= obj.fuzzy_threshold :
      - Vérifier gate (si présente) :
        * MIN_REVEALS     : outcome.talk_reveals_count >= gate.value
        * MIN_DISPOSITION : outcome.talk_disposition_change >= gate.value
        * HAS_ITEM        : gate.value in player.inventory
        * FLAG_SET        : world_state.flags.get(gate.value) == True
      - Si gate ok → state = "completed", reason = "match_full"
      - Sinon → state = "partial", reason = "gate_failed:{gate.kind}"
   c. Si match_score >= 0.5 ET < obj.fuzzy_threshold → state = "partial", reason = "match_below_threshold"
   d. Sinon → état inchangé

   Note : si un objectif passe en "partial" pendant ce tour (cas b ou c), il est inclus
   dans `judge_request.objectives` avec le motif (`gate_failed` ou `match_below_threshold`).
   Le BeatJudge raisonne sur les deux cas avec le même contrat.

3. Calculer progress_score = (count(completed) / count(total)) * 100

4. Évaluer advance_rule :
   - ALL_REQUIRED : tous les `required` completed → ADVANCE
   - ANY          : ≥1 completed → ADVANCE
   - M_OF_N       : count(completed) >= advance_threshold → ADVANCE

5. Si pas d'ADVANCE :
   - Si ≥1 objective passé en "partial" pendant ce tour → NEEDS_JUDGE
   - Sinon → STAY

6. Retour : BeatProgressionResult(decision, progress, [new_beat], [judge_request], reasons)
```

### 5.3 Suppression des chemins legacy

| Code legacy | Action |
|---|---|
| `_check_beat_completion()` ([orchestrator.py:704](../../bot/pipeline/orchestrator.py)) | Supprimé, logique migrée dans `BeatProgressionEngine.evaluate()` |
| `session.advance_beat_if_ready()` ([game_session.py:106-141](../../engine/game_session.py)) | Supprimé, location devient un objectif `kind=arrive` |
| Fallback LLM inline ([orchestrator.py:519-564](../../bot/pipeline/orchestrator.py)) | Supprimé, remplacé par `BeatJudge` (section 6) |
| Lot D — second beat check ([orchestrator.py:622-658](../../bot/pipeline/orchestrator.py)) | Supprimé, un seul check par tour |

Le pipeline final fait un seul appel :
```python
result = beat_engine.evaluate(arc, interpreted, outcome, location, history)
if result.decision == "ADVANCE":
    arc, new_beat, fact_lines = apply_advance(arc, result)
elif result.decision == "NEEDS_JUDGE":
    judge_result = await beat_judge.evaluate(result.judge_request)
    if judge_result.passed and judge_result.confidence >= 0.7:
        arc, new_beat, fact_lines = apply_advance_partial(arc, result, judge_result)
```

---

## 6. `BeatJudge` LLM

Localisation : `ai/beat_judge.py` (nouveau).

### 6.1 Modèle

`qwen3.5:4b` (interpreter-class). Pas le 9b — éviter le swap de modèle pour un job léger.

### 6.2 Contrats

```python
class ObjectivePartialMatch(BaseModel):
    id: str
    kind: ObjectiveKind
    target: str
    description: str
    match_score: float           # 0-1
    gate_failed: bool
    gate_kind: GateKind | None

class JudgeRequest(BaseModel):
    beat_title: str
    beat_description: str
    beat_judge_rubric: str | None
    objectives: list[ObjectivePartialMatch]
    player_action_text: str
    interpreted_action: dict
    outcome_summary: str
    location_name: str | None
    npcs_present: list[str]

class JudgeResponse(BaseModel):
    passed: bool
    confidence: float
    objectives_satisfied: list[str]
    reasoning: str
    suggested_next_action: str | None
```

### 6.3 Politique d'acceptation

| Réponse LLM | Action |
|---|---|
| `passed=True` ET `confidence >= 0.7` | Marquer `objectives_satisfied` comme completed, ré-évaluer `advance_rule` |
| `passed=True` ET `confidence < 0.7` | Marquer comme `partial`, n'avance pas |
| `passed=False` | Garder l'état, stocker `reasoning` pour `/hint` niveau 3 |
| JSON invalide / timeout | `passed=False, reasoning="judge_error"` |

### 6.4 Garde-fous

- **Whitelist** : tout `objective_id` retourné qui n'est pas dans la liste d'entrée → rejeté en post-process (Pydantic + check explicite).
- **Cooldown** : 1 appel BeatJudge maximum par tour de joueur.
- **Timeout** : 5s. Si dépassé → `passed=False, reasoning="judge_timeout"`. Le tour continue.
- **Logs** : input hash, output, latence, modèle. Métriques agrégées (section 9).

---

## 7. Story Director — juridiction clarifiée

### 7.1 Modifications minimales

1. **Retirer `next_beat_hint`** ([ai/models.py:62](../../ai/models.py)) du `DirectorNote`. Remplacé par `current_beat_atmosphere` (descriptif, pas prescriptif).
2. **Alimenter `current_objective` depuis `BeatProgressionEngine`** (déterministe), pas depuis le LLM. Le Director peut le reformuler stylistiquement, pas en inventer un nouveau.
3. **Cadence inchangée** : tous les 6 tours OU `combat_just_ended` OU `drift_detected` OU `force` flag.
4. **Drift detection refondé** : `DriftTracker` lit désormais le `decision` du `BeatProgressionEngine` (5 STAY consécutifs sur même beat = drift), pas `narration.beat_advanced` du LLM.

### 7.2 Conservé

- `coherence_issues`, `suggested_hooks`, `forbidden_topics`, `required_mentions`, `stale_quest_ids` : tout ce qui est juridiction narrative cross-beat reste inchangé.

### 7.3 Communication BeatProgressionEngine → Story Director

Le Director reçoit en input le `BeatProgress` du tour courant. Il sait :
- Quel beat est actif
- État de chaque objectif (pending/partial/completed)
- `progress_score` (utile pour adapter le ton : stagnation → tension croissante)
- Si la décision était `NEEDS_JUDGE` (peut faire un hook subtil sans révéler l'objectif)

---

## 8. `/hint` slash command

Localisation : `bot/cogs/hint.py` (nouveau cog).

### 8.1 Trois niveaux

| Niveau | Source | Coût | Visibilité par défaut |
|---|---|---|---|
| 1 — vague | `beat.player_visible_hint` (déterministe) | Gratuit, illimité | Éphémère |
| 2 — précis | Liste des `BeatObjective.description` en pending/partial (déterministe) | 1× max par beat | Éphémère |
| 3 — explicite | `BeatJudge` mode verbose, retourne `suggested_next_action` | Cooldown 5 tours après usage | Éphémère (flag `public:true` optionnel) |

### 8.2 Cas particuliers

- Niveau 1 sans `player_visible_hint` → fallback déterministe sur premier paragraphe court de `beat.description`.
- Niveau 3 en cooldown → message éphémère "indisponible pendant N tours". Pas d'appel LLM.
- Reset entre beats : ADVANCE → suppression ligne `hint_usage` correspondante.
- Hint en plein combat : autorisé, n'interrompt pas le tour.
- Pas d'objectif pending (rare, signal de bug) → message diagnostic.

### 8.3 Pourquoi cette gradation

- **Niveaux 1-2 gratuits** → l'exploration n'est jamais punie.
- **Cooldown niveau 3 uniquement** → le joueur qui spam des actions random ne peut pas spammer "donne-moi la solution"; il doit essayer entre deux niveau 3.
- **Pas de ressource D&D inventée** → pas de complexité ajoutée.

---

## 9. Arc Tracker enrichi

Le pin de campagne ([bot/utils/arc_tracker.py](../../bot/utils/arc_tracker.py)) lit désormais le `BeatProgress`, pas le `DirectorNote`.

```
📜 Acte 2 — La disparition au marché aux poissons
Beat 3/7 · Progression ████████░░ 60%

🎯 Objectif courant
Trouver qui a vu la victime en dernier

État des objectifs :
  ✅ Examiner la cape ensanglantée
  ◐  Parler à Kaelen au forge (commencé)
  ◯  Interroger un témoin au marché

🗺️ Lieux pertinents : Forge, Marché aux poissons
👥 Vivants pertinents : Kaelen, Mère Olwen
─────────────────
⏱ Tour 17 · /hint disponible (niveau 1-2)
```

Source de vérité : `BeatProgressionEngine` (déterministe). Update à chaque changement d'état (ADVANCE ou objectif partial→completed).

---

## 10. Tests

### 10.1 Coverage cible

- `engine/beat_progression.py` : **90%+** (anti-cheat zone, comme `engine/dice.py`).
- `ai/beat_judge.py` : 80%+ (mocks LLM).
- Scénarios end-to-end : couverture des 4 symptômes initiaux.

### 10.2 Suites à créer

- `tests/engine/test_beat_progression.py` (~25 tests) :
  - Chaque `ObjectiveKind` : match positif / négatif / fuzzy edge (0.69 vs 0.71)
  - Chaque `AdvanceRule` : ALL_REQUIRED, ANY, M_OF_N (avec threshold)
  - Chaque `ObjectiveGate` : MIN_REVEALS, MIN_DISPOSITION, HAS_ITEM, FLAG_SET
  - Cas limites : 0 objectif, beat null, current_beat_index hors bornes
  - **Anti-régression** : test "pas de double-avance" reproduisant le scénario actuel
  - Migration : `completion_trigger` legacy → `BeatObjective` unique

- `tests/ai/test_beat_judge.py` (~10 tests, mocks LLM) :
  - JSON valide / invalide / timeout / hallucination d'`objective_id` (whitelist enforcée)
  - Politique d'acceptation par confidence threshold (0.7 boundary)
  - Cooldown 1 appel par tour

- `tests/scenarios/test_blocked_player_recovery.py` (~5 scénarios pytest) :
  - Joueur bloqué par gate `min_reveals` → `/hint` niveau 1-2-3 fonctionnels
  - Joueur explore librement → niveaux 1-2 sans coût, niveau 3 cooldown observé

- `tests/scenarios/test_beat_progression_e2e.py` (live Discord testing via `discord-test` MCP) :
  - 3 scénarios complets de progression sur arc d'onboarding
  - Validation Arc Tracker mise à jour en temps réel

---

## 11. Telemetry & observabilité

### 11.1 Logs structurés

Fichier : `logs/beat_progression.jsonl`

```json
{"ts": "2026-04-25T14:32:11Z", "campaign_id": "abc", "beat_number": 3,
 "decision": "NEEDS_JUDGE", "judge_passed": true, "judge_confidence": 0.82,
 "objectives_updated": ["talk_kaelen"], "progress_score_before": 40, "after": 70,
 "latency_ms": 1250, "model": "qwen3.5:4b"}
```

### 11.2 Métriques agrégées

- `beat.advance_total`, `beat.stay_total`, `beat.judge_total`
- `beat.judge_pass_rate`, `beat.judge_latency_p95`
- `beat.stagnation_5turns_total` (alerte design)
- `hint.usage_per_level` par beat (révèle les beats opaques)

### 11.3 Outil de revue post-session

Script `scripts/review_beat_progression.py` qui agrège les logs d'une campagne et produit un rapport :
- Beats où `progress_score` n'a jamais dépassé 50%
- Beats où `/hint` niveau 3 a été utilisé > 1×
- Beats où `BeatJudge` a refusé > 3×
- Signaux de design défaillant à corriger côté `arc_generator`.

---

## 12. Migration & déploiement

### Phase A — modèle augmenté, code legacy intact (1-2 jours)

- Ajouter `BeatObjective`, `ObjectiveGate`, `AdvanceRule` dans `world/story_arc.py`.
- Ajouter `objectives`, `advance_rule`, `player_visible_hint`, `judge_rubric` à `StoryBeat`.
- Migration en lecture (auto-conversion `completion_trigger` → `BeatObjective`).
- **Aucune modification du code de progression.** Tests existants passent à l'identique.

### Phase B — `BeatProgressionEngine` en shadow mode (3-5 jours)

- Créer `engine/beat_progression.py`.
- Brancher dans `orchestrator.py` après le code legacy : log de la décision shadow dans `logs/beat_progression_shadow.jsonl` (avec `legacy_decision`, `shadow_decision`, divergence flag), **sans appliquer**.
- Comparer décisions shadow vs legacy pendant ~1 semaine de parties test.
- Detection automatique de divergences via `scripts/compare_shadow.py` qui agrège le log et liste les cas où legacy et shadow divergent (avec replay possible).
- Ajuster algorithme jusqu'à divergence < 5% (les 5% restants = vrais bugs corrigés par la nouvelle logique).

### Phase C — bascule (2-3 jours)

- Remplacer les 3 chemins concurrents par l'appel unique à `BeatProgressionEngine`.
- Suppression code legacy : `_check_beat_completion()`, `advance_beat_if_ready()`, fallback LLM inline, second beat check.
- Création `BeatJudge` (`ai/beat_judge.py`).
- Création `/hint` cog.
- Modification Arc Tracker pour lire `BeatProgress`.
- Modification Story Director (retirer `next_beat_hint`, refondre `DriftTracker`).
- **Migration DB** : ajouter table `hint_usage`.

### Phase D — tuning (en continu)

- Activer telemetry agrégée.
- Script de revue post-session.
- Itérer sur `judge_rubric` des beats où le BeatJudge échoue souvent.

---

## 13. Risques & mitigations

| Risque | Mitigation |
|---|---|
| Arcs existants en prod incompatibles | Migration auto au load, tests sur fixtures legacy |
| BeatJudge LLM 4b trop faible pour rubrique complexe | Tester en shadow mode avant bascule, fallback `passed=False` propre |
| `/hint` niveau 3 spammé via cooldown bypass | Persistence DB du cooldown, vérifié à chaque tour |
| Story Director désynchronisé | Remplacement DriftTracker par signal Engine (déterministe) |
| Latence ajoutée par BeatJudge | LLM 4b (1-2s typique), uniquement sur ~20% d'actions ambigües, timeout 5s |

---

## 14. Decisions log

- **Hybride structuré (et pas déterministe pur ni LLM-first)** : un D&D narratif vit de la créativité du joueur — impossible d'énumérer toutes les conditions à l'avance. Mais on garde le déterminisme par défaut pour la prévisibilité.
- **BeatJudge sur 4b et pas 9b** : éviter le swap de modèle pour un job léger; le 4b est suffisant pour un jugement binaire avec rubrique.
- **Story Director séparé du BeatProgressionEngine** : deux juridictions distinctes (mécanique vs narrative) — fusionner aurait fait porter au LLM 9b deux jobs concurrents et dilué les responsabilités.
- **`/hint` à 3 niveaux progressifs avec cooldown niveau 3 seulement** : autorise l'exploration libre (niveaux 1-2 gratuits), pénalise progressivement le spam (cooldown niveau 3). Pas de ressource D&D Inspiration introduite (hors scope).
- **Migration en 3 phases (A/B/C)** : non-destructive, shadow mode pour valider avant bascule.

---

## 15. Références

- Spec antérieure : [2026-04-11-beat-progression-and-state-design.md](2026-04-11-beat-progression-and-state-design.md)
- Spec antérieure : [2026-04-20-directors-cut-design.md](2026-04-20-directors-cut-design.md)
- Spec antérieure : [2026-04-06-onboarding-story-arc-design.md](2026-04-06-onboarding-story-arc-design.md)
- Code actuel : [bot/pipeline/orchestrator.py](../../bot/pipeline/orchestrator.py), [engine/game_session.py](../../engine/game_session.py), [world/story_arc.py](../../world/story_arc.py)
