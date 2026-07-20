# Design : Porte de cohérence narrative en production

**Date** : 2026-07-20
**Statut** : Validé
**Specs amont** : audit narration H17 (`memory/narration_guard.py`),
harnais simulateur (`tests/simulation/rules/`), audit robustesse du
2026-07-20 (session « futures améliorations »)

---

## Contexte

Le projet possède deux systèmes de détection d'incohérences narratives
qui ne partagent aucun code :

1. **Le simulateur** (`tests/simulation/rules/`) : 13 règles
   déterministes — 7 dures (`hard.py`), 4 souples (`soft.py`), 2 de
   drift (`drift.py`) — éprouvées sur les runs de simulation. Elles ne
   tournent **que** dans les tests : `IncoherenceChecker` n'est importé
   nulle part hors de `tests/`.
2. **La production** (`memory/narration_guard.py`) : exactement 2 checks
   — PNJ mort par regex de nom, anti-répétition difflib sur 2
   narrations.

En jeu réel, un PNJ fantôme, un objet non possédé, un mauvais lieu, une
violation de zone de combat ou la négation d'un locked fact passent sans
détection. La bonne volonté des LLM (narrateur 9b, NPC 4b, director 9b)
est la seule défense. Ce design ferme l'écart : **une seule
implémentation des règles, deux consommateurs** (prod + simulateur).

Décisions actées avec l'utilisateur :

- **Enforcement** : jamais publier une incohérence dure — retry
  correctif unique, puis bascule sur le template tier-3 existant.
- **Architecture** : extraction du noyau pur (approche A), pas de
  déplacement tel quel ni de réécriture parallèle.
- **Périmètre** : portage des règles (phase 1) + locked facts génériques
  via `BeatEffects` (phase 2), implémentables séparément.

---

## Phase 1 — Portage des règles

### 1.1 Nouveau module `memory/coherence_rules.py`

Fonctions pures, zéro état, zéro LLM, zéro import Discord/DB.

```python
class CoherenceSnapshot(BaseModel):
    """Contrat d'entrée neutre — construit par chaque consommateur."""
    dead_npcs: list[str]          # noms des PNJ morts
    known_npc_names: list[str]    # tous les PNJ enregistrés (registry)
    player_names: list[str]
    current_location: str | None
    known_locations: list[str]    # noms de tous les lieux de la campagne
    moved_this_turn: bool
    actor_inventory: list[str]    # noms d'items de l'acteur du tour
    player_hp_ratio: float        # HP courant / max de l'acteur
    combat_active: bool
    combat_zones: list[str]       # vide hors combat
    locked_facts: list[LockedFactSnapshot]   # (id, text)

class CoherenceViolation(BaseModel):
    rule: str        # id stable, identique au simulateur ("R1.npc_status", …)
    severity: Literal["hard", "soft", "drift"]
    snippet: str     # extrait de narration fautif (≤ 200 chars)
    expected: str    # état moteur contredit — sert au prompt de retry
```

Chaque règle est une fonction `(narration: str, snapshot:
CoherenceSnapshot) -> list[CoherenceViolation]`. Les ids de règles
restent **identiques à ceux du simulateur** (continuité de télémétrie).

Registre avec mode d'application par règle :

```python
class RuleMode(StrEnum):
    BLOCK = "block"      # violation → retry correctif → template tier-3
    OBSERVE = "observe"  # violation → log structuré, zéro impact joueur

RULES: dict[str, tuple[RuleFn, RuleMode]] = { ... }
```

### 1.2 Modes initiaux

| Règle | Mode initial | Justification |
|---|---|---|
| `R1.npc_status` (PNJ mort qui agit) | BLOCK | Déjà bloquant en prod ; le portage **fusionne** les deux variantes : verbe actif dans la même phrase (simulateur, évite le faux positif « mentionner le cadavre ») + formes courtes de nom et `npcs_mentioned` auto-déclaré (prod) |
| `R1.item_use_without_owning` | BLOCK | Ancré dans l'inventaire moteur |
| `R1.hp_mismatch` | BLOCK | « Agonisant » avec HP ≥ 80 % : signal sûr |
| `R1.location_mismatch` | BLOCK | Gardé par `moved_this_turn` |
| `R1.locked_fact_violation` | BLOCK | Fenêtre de négation 60 chars, déjà calibrée |
| `R1.zone_violation` | BLOCK | Zones du `combat_state`, déterministe |
| `R1.phantom_npc` | OBSERVE | Heuristique de noms propres bruyante ; 1 alerte douteuse déjà vue en live (T09) — promotion après données réelles |
| `R2.repetition` (fenêtre élargie) | OBSERVE | Faux positif R2 connu (re-look statique) ; le check difflib actuel du guard (2 narrations, 8 mots) reste BLOCK tel quel |
| `R2.npc_name_drift` · `R2.tense_drift` · `R2.unknown_proper_noun` | OBSERVE | Souples par nature |
| `R3.disposition_silent_change` · `R3.condition_phantom` | OBSERVE | Drift, non bloquant |

Changer un mode = une ligne dans `RULES`. Aucune option de configuration
externe : le registre **est** la configuration.

### 1.3 Orchestration (`memory/narration_guard.py`)

Le guard garde son état par campagne (registre module-level : dead set,
narrations récentes) et son API existante, et gagne :

```python
def check_narration(
    campaign_id: str,
    narrative: str,
    snapshot: CoherenceSnapshot,
    npcs_mentioned: list[str],
) -> GuardVerdict:
    """Exécute toutes les règles ; sépare bloquant / observé."""
```

`GuardVerdict` porte `blocking: list[CoherenceViolation]` et
`observed: list[CoherenceViolation]`. Les violations observées sont
loggées via un logger dédié `memory.coherence` (une ligne par violation :
rule, mode, campaign_id, snippet, expected) — c'est la base de données
de promotion OBSERVE → BLOCK.

`find_dead_npc_violations` et `find_repetition` restent, réimplémentés
au-dessus du noyau partagé (pas de logique dupliquée).

### 1.4 Câblage production (`bot/pipeline/narrate.py`)

Un builder `build_coherence_snapshot(session, action, result) ->
CoherenceSnapshot` construit le snapshot depuis ce que le pipeline a
déjà : PNJ de la session (vivants/morts), noms des PJ, lieu courant,
noms de tous les lieux de la campagne (accessor exact fixé au plan —
locations chargées en session, sinon `LocationRepository`),
`moved_this_turn` = l'action résolue est un MOVE/FLEE réussi,
inventaire de l'acteur, ratio HP de l'acteur, zones du `combat_state`
actif, `locked_facts` de l'arc.

Flux dans `call_narrator` (extension du mécanisme existant
`narrate.py:307-350`) :

```
narration tier-1 → check_narration
  ├─ aucune violation BLOCK → publier (violations OBSERVE loggées)
  ├─ violations BLOCK → 1 retry avec contrainte listant chaque
  │    violation (champ `expected`) → re-check
  │      ├─ propre → publier
  │      └─ encore violé → template tier-3 (narration factuelle
  │           dérivée de l'ActionResult, jamais d'incohérence publiée)
```

Coût : ~0 ms sur le chemin nominal (regex pures), +15-30 s uniquement
sur violation réelle — prix accepté de la décision d'enforcement.

### 1.5 Le simulateur devient adaptateur

`tests/simulation/rules/hard.py`, `soft.py`, `drift.py` sont réduits à :
state simulateur → `CoherenceSnapshot`, `CoherenceViolation` →
`IncoherenceAlert` (le type du harnais et ses champs `turn`,
`category`, etc. ne bougent pas — aucun changement d'API pour
`IncoherenceChecker` ni les rapports de simulation). Les règles
elles-mêmes disparaissent des fichiers de test au profit d'imports du
module prod.

---

## Phase 2 — Locked facts génériques

Aujourd'hui un seul écrivain produit des locked facts
(`_sync_locked_facts`, uniquement `npc_dead:<nom>`), et seuls les morts
sont vérifiés. Extension **déterministe, écrite par le moteur** :

### 2.1 `BeatEffects.locked_facts`

`world/story_arc.py::BeatEffects` gagne
`locked_facts: list[str] = []`. Le sanitizer existant des beats clampe :
entrées vides éliminées, doublons retirés, **max 2 faits par beat**,
**max 200 chars par fait**.

À la complétion d'un beat (site existant d'application des
`BeatEffects`), chaque fait devient
`LockedFact(id=f"beat:{beat_id}:{i}", text=...)` ajouté à
`StoryArc.locked_facts` — idempotent (id déjà présent → ignoré). Le
contenu est **autorisé** par l'arc generator (prompt étendu : champ
optionnel par beat, exemples fournis) et les recipes ; l'écriture est
**arbitrée** par le code au moment exact de la complétion.

### 2.2 Effets immédiats

- La règle `R1.locked_fact_violation` (portée en phase 1) protège ces
  faits dès leur création — négation détectée → retry → template.
- Le bloc `[LOCKED FACTS]` du prompt narrateur est plafonné à **15
  lignes** (les plus récentes d'abord, morts prioritaires) pour borner
  la croissance du prompt sur les longues campagnes.

### 2.3 Hors périmètre, à dessein

- Extraction de « promesses » depuis les dialogues TALK : non
  déterministe, contraire à « le code arbitre ».
- Étanchéité des secrets PNJ (fuite via RAG) : chantier séparé (axe
  quick wins RAG).
- Vérification sémantique des faits (au-delà de la négation lexicale) :
  nécessiterait un LLM juge — pas dans ce chantier.

---

## Ce qui ne change pas

- Le pipeline 6 étapes, la chaîne narrateur 3 tiers, le Story Director
  (consultatif), `BeatProgressionEngine`.
- L'API et les rapports du simulateur (`IncoherenceAlert`,
  `IncoherenceChecker`, règles listées dans `rules/__init__.py`).
- Le comportement joueur en l'absence de violation : zéro latence
  ajoutée.

---

## Vérification

- `tests/memory/test_coherence_rules.py` : chaque règle portée, cas
  positifs/négatifs adaptés des tests simulateur existants +
  `CoherenceSnapshot` (validation Pydantic, valeurs par défaut).
- `tests/bot/` (narrate) : violation BLOCK → retry → re-check →
  template tier-3 ; violation OBSERVE → log, pas de retry ; snapshot
  builder sur session avec/sans combat.
- **Non-régression du portage** : la suite `tests/simulation/` complète
  reste verte avec les règles importées du module prod — c'est la
  preuve que le portage n'a pas changé la sémantique des règles.
- Phase 2 : sanitizer (clamps), idempotence de l'ajout, plafond du bloc
  prompt, arc generator produisant des `locked_facts` valides sur
  fixture.
- Gates : `pytest` complet, `ruff check .`, `mypy .` verts.
