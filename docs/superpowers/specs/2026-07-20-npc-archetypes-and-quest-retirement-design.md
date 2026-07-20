# Design : Bibliothèque d'archétypes NPC + Retrait du sous-système quêtes

**Date** : 2026-07-20
**Statut** : Validé (décisions déléguées, audit specs↔code §4.2)
**Specs amont** : `2026-04-11-world-generation-variety-design.md` §3 (archétypes),
audit `tasks/todo.md` §4.2 (quêtes)

---

## Contexte

L'audit specs↔code du 2026-07-20 a laissé deux décisions ouvertes :

1. **La bibliothèque d'archétypes NPC** (spec variety §3) n'a jamais été
   écrite. Le prompt `system_npc_generator.txt` attend déjà un archétype
   (traits contradictoires, hook, pattern de dialogue), le paramètre
   `archetype_context` de `NPCGenerator.generate` existe — mais rien ne le
   fournit en production. C'est de l'écriture de contenu de jeu.
2. **Le sous-système quêtes est dormant** : aucun code de prod ne crée de
   `Quest`. Il faut trancher — générer des quêtes, ou acter que les beats
   d'arc les remplacent.

---

## Décision 1 — Écrire la bibliothèque, la câbler, tuer les fallbacks génériques

### 1.1 Charte de ton (choix d'auteur)

Le contenu est écrit en français (langue de contenu établie du projet) avec
une ligne éditoriale unique pour les 20 archétypes :

- **Personne n'est ce qu'il annonce.** Chaque archétype est construit sur un
  écart entre la fonction sociale et la vie intérieure. Pas de méchants, pas
  de sages : des gens coincés dans des compromis.
- **Le hook est une scène, pas un attribut.** « Cache quelqu'un dans sa
  cave » se joue ; « a un lourd passé » ne se joue pas. Chaque hook doit
  pouvoir déclencher une interaction concrète à la table.
- **Le pattern de dialogue est performable par un 4b.** Un tic simple,
  répétable, reconnaissable en une réplique — pas une psychologie complexe
  que le petit modèle ne tiendra pas trois échanges.
- **Anti-clichés actifs** : pas de tavernier bourru, pas de vieil ermite
  mystérieux, pas de marchand cupide sans autre dimension. Les interdits du
  prompt s'appliquent aussi à la bibliothèque.

### 1.2 Structure (`engine/npc_archetypes.py`, nouveau)

Conforme à la spec variety §3 : **20 archétypes, 5 catégories × 4**.
Catégories : `AUTHORITY`, `TRADE`, `LORE`, `FRINGE`, `FOLK` (identifiants
anglais comme `arc_recipes`, contenu français).

```python
class ArchetypeCategory(StrEnum): ...

class NPCArchetype(BaseModel):
    id: str                    # "juge_qui_negocie"
    category: ArchetypeCategory
    label: str                 # étiquette courte, ex. "Juge qui négocie"
    traits: list[str]          # 2-3 traits contradictoires
    hook: str                  # 1 hook narratif jouable
    dialogue_pattern: str      # 1 tic de langage performable

def draw_archetypes(
    count: int,
    exclude: Collection[str] = (),
    rng: random.Random | None = None,
) -> list[NPCArchetype]: ...

def format_archetype_context(archetype: NPCArchetype) -> str: ...
```

`draw_archetypes` fait un tirage **sans remise, équilibré par catégorie**
(round-robin sur les catégories mélangées) : un lieu avec 4 PNJ ne reçoit
jamais deux archétypes de la même catégorie tant qu'il en reste ailleurs, et
jamais deux fois le même id. `exclude` permet d'écarter les ids déjà
attribués. Si `count` dépasse le stock restant, le tirage recycle (20
archétypes pour des scènes de 2-5 PNJ : cas théorique).

### 1.3 Câblage (les deux hooks dormants)

Sites de production qui génèrent des sheets :

| Site | Changement |
|------|-----------|
| `bot/npc_prefetch.py::prefetch_npc_sheets` | Tire `len(pending)` archétypes en une passe (anti-doublon garanti par lieu), en passe un par PNJ au générateur. |
| `bot/pipeline/resolve.py` (TALK lazy, chemin de course rare) | Tire 1 archétype au hasard. Pas d'anti-doublon inter-PNJ ici : le prefetch couvre le cas nominal, documenté. |

`NPCGenerator.generate` : le paramètre mort `archetype_context: str` est
**remplacé** par `archetype: NPCArchetype | None`. Le générateur formate
lui-même le bloc prompt (`format_archetype_context`). `ai/` importe déjà
`engine/` (cf. `beat_judge`) — pas de nouvelle dépendance de sens interdit.

### 1.4 Fallbacks (déviation assumée vs spec §5.3)

La spec demandait « pas de fallback générique : re-tirer et retenter ». Un
retry double la latence sur un modèle local 4b pour un cas d'échec rare. À
la place : quand un archétype est fourni et que le LLM rend des listes
vides, le fallback est **dérivé du contenu écrit** — `secrets` ←
`archetype.hook`, `knowledge` ← premier trait + lieu. C'est du contenu
d'auteur garanti non générique, sans second appel. Les fallbacks
génériques (« A un secret qu'il/elle ne révèle pas facilement ») ne
subsistent que pour le chemin sans archétype (appels hors prod, tests).

---

## Décision 2 — Les beats remplacent les quêtes, définitivement

### 2.1 Verdict

**On ne génère pas de quêtes. Le sous-système est retiré.**

Arguments, par poids décroissant :

1. **Les beats subsument le modèle `Quest`.** `BeatObjective` offre déjà :
   objectifs vérifiables déterministes, objectifs optionnels
   (`required=False`) pour le contenu annexe, gates mécaniques, effets de
   complétion (`BeatEffects` : items, PNJ, flags), arbitre LLM de secours
   (BeatJudge), affichage (arc tracker, `/hint`). Une quête serait un
   deuxième moteur de progression parallèle — soit dupliqué de
   `BeatProgressionEngine`, soit non arbitré, en violation de « le code
   arbitre ».
2. **Le code a déjà voté.** `ai/quest_generator.py` a été supprimé comme
   module mort (commit f5d69bf) ; `StoryDirectorReport.stale_quest_ids` n'a
   aucun consommateur ; l'arc tracker reçoit `active_quests=[]` en dur
   partout. Le sous-système ne survivait que par inertie.
3. **Le contenu émergent a maintenant un vrai véhicule.** Les hooks
   narratifs de la bibliothèque d'archétypes (décision 1) + les
   `suggested_hooks` du Story Director couvrent le rôle narratif des quêtes
   annexes — sans table, sans statut, sans deuxième moteur.

Si les sessions réelles de la Phase 4 font émerger un vrai besoin de
contenu annexe *suivi*, la primitive à étendre est `BeatObjective`
optionnel — pas une résurrection de la table `quests`.

### 2.2 Périmètre du retrait

| Surface | Action |
|---------|--------|
| `world/quest.py` + exports `world/__init__.py` | Supprimé |
| `db/repositories/quest_repo.py` + export | Supprimé |
| `db/models.py::QuestRow`, `db/mappers.py::quest_to_db/from_db` | Supprimés. La table `quests` des DB existantes n'est **pas** droppée (vide par construction, inoffensive) ; les nouvelles DB ne la créent plus. |
| `bot/game_session.py::quests` | Champ supprimé |
| `bot/persistence.py` | Bloc de save + `_index_quests` supprimés |
| `bot/cogs/session.py` (resume) | Chargement quêtes + compteur de log supprimés |
| `memory/state.py` / `memory/models.py` | `QuestRepository`, bloc `active_quests`, `StateSnapshot.active_quests`, ligne de contexte « Active Quests » supprimés |
| `memory/indexer.py::index_quest` + `SemanticDocumentType.QUEST_DETAIL` | Supprimés (le type n'est référencé par aucune requête de lecture ; aucune collection réelle ne contient de doc `quest_detail` puisqu'aucune quête n'a jamais existé) |
| `bot/utils/arc_tracker.py` + `bot/embeds/arc_tracker_embed.py` | Champ `active_quests` + section « 📋 Quêtes actives » supprimés |
| `ai/models.py::stale_quest_ids`, `ai/story_director.py`, `system_story_director.txt` | Supprimés |
| `bot/cogs/test_bridge.py` (game_state), `mcp_discord/server.py` (docstring) | Clé `quests` retirée |
| `tests/simulation/rules/drift.py::R3.quest_silent_progress` | Supprimée (surveillait un chemin qui ne peut plus exister) |
| `scripts/reset_dev_data.py` | Commentaire mis à jour |
| Tests (`conftest.sample_quest`, world, db, memory, persistence, director…) | Supprimés/adaptés |
| Docs : `CLAUDE.md` (arbre), specs amont | Note de supersession dans `world-db-design` et `world-generation-variety` ; `memory-system-design` si concerné |

### 2.3 Ce qui ne change pas

- Le pipeline en 6 étapes, `BeatProgressionEngine`, BeatJudge, arc tracker
  (hors section quêtes), `/hint`.
- Les DB existantes restent lisibles telles quelles (aucune migration
  destructive ; la table orpheline vide est ignorée).

---

## Vérification

- `tests/engine/test_npc_archetypes.py` : 20 archétypes, 5 catégories × 4,
  ids uniques, 2-3 traits/hook/pattern non vides, tirage sans doublon,
  équilibre par catégorie, respect d'`exclude`, sur-tirage sans crash,
  déterminisme avec rng seedé.
- `tests/ai/test_npc_generator.py` : nouveau paramètre `archetype`, bloc
  prompt injecté, fallbacks dérivés du hook.
- `tests/bot/test_npc_prefetch.py` : archétypes distincts sur un batch.
- Retrait quêtes : suite complète verte après suppression — aucun test ne
  doit être « adapté » en le vidant de son sens ; ceux qui testaient le
  sous-système disparaissent avec lui.
- `pytest` complet, `ruff check .`, `mypy .` : verts.
