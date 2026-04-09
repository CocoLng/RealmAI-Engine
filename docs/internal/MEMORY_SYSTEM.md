# Système de mémoire — 4 couches

Défini dans `memory/`. Objectif : fournir un contexte riche et à jour au Narrator (et au Story Director) **sans dépasser ~2500 tokens par appel**.

## Vue d'ensemble

| # | Couche | Fichier | Source | Budget | Priorité truncation |
|---|---|---|---|---|---|
| 1 | État structuré | [state.py](../../memory/state.py) | SQLite (repos) + in-memory | 450 | **jamais tronqué** |
| 2 | Fenêtre glissante | [sliding_window.py](../../memory/sliding_window.py) | table `exchanges` | 700 | 3ème à tronquer |
| 3 | Résumés compressés | [summarizer.py](../../memory/summarizer.py) | table `summaries` | 400 | 2ème à tronquer |
| 4 | RAG sémantique | [semantic.py](../../memory/semantic.py) | ChromaDB | 350 | **1ère à tronquer** |

Total budget : **2500 tokens** (≥ layer1_max, enforced par `ContextBudget` Pydantic validator).

## Layer 1 — État structuré

**But** : dire au LLM **exactement** ce qui est vrai *maintenant*.

**Construction** : `StateBuilder.build(...)` lit :
- `Campaign` via `CampaignRepository`
- `Location` courante via `LocationRepository`
- PNJs de la location via `NPCRepository.list_by_location()`
- Quests actives via `QuestRepository.list_by_campaign()`
- Personnages joueurs + inventaires + combat state (passés en in-memory)

**Sortie** : `GameStateSummary` Pydantic avec :
- `campaign_name`, `current_location`
- `player_characters: list[CharacterSummary]` (name, race, class, level, hp/max, ac, conditions)
- `nearby_npcs: list[NPC]`
- `active_quests: list[Quest]`
- `combat: CombatSummary | None` (round, current turn, combatants)
- `inventory_highlights`
- `story_context`

**Rendu** : `.render()` → texte structuré. **Jamais tronqué** — si cette couche dépasse son budget, elle est postée telle quelle et les couches 2/3/4 sont compressées/droppées.

## Layer 2 — Sliding window

**But** : continuité narrative immédiate.

`SlidingWindow(window_size=12)` persiste chaque exchange via `ExchangeRepository` dans la table `exchanges`. Modèle :

```python
NarrativeExchange(
    id: str,
    campaign_id: str,
    role: Literal["PLAYER", "NARRATOR", "SYSTEM"],
    content: str,
    interaction_number: int,
    created_at: datetime,
)
```

`build(campaign_id)` → `get_recent(limit=12)` ordonné par `interaction_number`. Rendu comme dialogue :

```
PLAYER: j'attaque le gobelin
NARRATOR: Thorin bondit, l'épée sifflant...
PLAYER: ...
```

Budget 700 tokens. Tronqué en retirant les exchanges les plus anciens d'abord.

## Layer 3 — Résumés compressés

**But** : mémoire long terme compactée.

`Summarizer.should_summarize(campaign_id)` → True si ≥ 20 exchanges non-résumés. Déclenché par `ContextAssembler` en side-effect lors de l'assemblage, ou explicitement.

### Fonctionnement

1. Récupère les exchanges non-résumés via `ExchangeRepository.get_unsummarized()`.
2. Appelle `OllamaClient.chat_json(model="qwen3.5:9b", …)` avec un prompt système demandant un résumé concis en français.
3. Parse `{"summary": "…"}` via un `_SummaryResponse` Pydantic interne.
4. Persiste `CompressedSummary(start_interaction, end_interaction, summary_text)` via `SummaryRepository`.
5. Les exchanges résumés restent en DB (pas de soft-delete).

`build(campaign_id)` → `get_recent(limit=4)` des résumés les plus récents, concaténés chronologiquement. Budget 400 tokens.

## Layer 4 — RAG sémantique

**But** : info contextuelle récupérée par similarité (lore, fiches PNJ, événements passés).

### Fonctionnement

`SemanticMemory` utilise ChromaDB `PersistentClient` pointant vers `data/chromadb`. **Une collection par campagne** nommée `campaign_{campaign_id}`. Embedding par défaut : `all-MiniLM-L6-v2`.

### Documents

```python
SemanticDocument(
    id: str,
    campaign_id: str,
    doc_type: Literal["WORLD_LORE", "NPC_SHEET", "PAST_EVENT", "LOCATION_DETAIL", "QUEST_DETAIL"],
    content: str,
    metadata: dict,
)
```

Documents indexés :
- Fiches PNJ (à la création par `NPCGenerator`).
- Lore et location details (par `WorldGenerator`).
- Événements notables (morts, décisions majeures).
- Notes du `StoryDirector` (post-coherence-check).

### Query

`build(campaign_id, query: str, top_k=5)` → top matches par cosine similarity, optionnellement filtré par `doc_type`. Concaténés en texte, budget 350 tokens.

Le query string par défaut est la dernière action joueur (ou le nom de la location courante). Cela permet d'injecter les fiches des PNJs présents + le lore lié à la scène.

## Context Assembler

`memory.context_assembler.ContextAssembler` orchestre les 4 couches :

```python
def assemble(
    self,
    campaign_id: str,
    game_state_inputs: StateBuilderInputs,
    query: str,
) -> str:
    layer1 = self._state_builder.build(...).render()
    layer2 = self._sliding_window.build(campaign_id)
    layer3 = self._summarizer.build(campaign_id)
    layer4 = self._semantic_memory.build(campaign_id, query)

    # Token estimation (word_count * 1.3)
    # Respect ContextBudget
    # Truncate in priority order: layer4, layer3, layer2 (never layer1)

    return "\n\n".join([layer1, layer2, layer3, layer4])
```

`ContextBudget` Pydantic :
```python
ContextBudget(
    layer1_max: int = 450,
    layer2_max: int = 700,
    layer3_max: int = 400,
    layer4_max: int = 350,
    total_max: int = 2500,
)
# @model_validator: total_max >= layer1_max
```

**Estimation de tokens** : [memory/token_utils.py](../../memory/token_utils.py) — heuristique `word_count * 1.3`. Pas de tokenizer réel. Erreur estimée ±10-20%. Un clamp final garde la marge.

### Side-effects

- Si `Summarizer.should_summarize()` est True pendant `assemble()` → déclenche la summarization immédiatement.
- L'exchange courant (player text) est appendé en Layer 2 **après** le narration (pas pendant l'assemblage).

## Persistance — tables concernées

- `exchanges` — Layer 2. Indexé par `interaction_number`.
- `summaries` — Layer 3. Indexé par `end_interaction`.
- ChromaDB `data/chromadb/campaign_<id>/` — Layer 4 (hors SQL).
- `campaigns.interaction_count` — compteur incrémenté à chaque tour pour déclencher la summarization et le story director.

## Anomalies connues

- Estimation de tokens heuristique, pas de tokenizer Qwen.
- Pas d'index SQL sur `(campaign_id, interaction_number)` pour `exchanges` → scan O(n) sur campagnes longues.
- `SemanticMemory` : pas de cleanup des collections orphelines si une campagne est supprimée — fuite de storage potentielle.
- `Summarizer` suppose que Ollama est up — pas de fallback si down (exchanges s'accumulent jusqu'à reprise).
- `SemanticMemory` silencieusement désactivé si ChromaDB indispo à l'init — le Layer 4 retourne une string vide sans avertir le caller.

Voir [ISSUES.md](ISSUES.md) pour le détail des sévérités.
