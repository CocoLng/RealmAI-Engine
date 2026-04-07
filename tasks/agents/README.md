# Plan post-mortem campagne `276fb1eb` — index des lots

Première campagne live (2026-04-07 22:31, « un donjon satanique », 3 joueurs). 7 actions jouées en 22 min, **zéro** n'a vraiment muté l'état du jeu. Ce dossier découpe la remédiation en 6 lots à exécuter **séquentiellement**, un agent par session.

## Preuves

- Log technique : [`logs/realm_20260407_223015.log`](../../logs/realm_20260407_223015.log)
- Bible de campagne : [`logs/campaigns/276fb1eb-e000-4c77-8e42-21b10cd84595.md`](../../logs/campaigns/276fb1eb-e000-4c77-8e42-21b10cd84595.md)

## Trois racines

1. **Résolution d'entité trop littérale** — PNJ « Jeanne, la Villageoise Terrifiée » jamais matché par « villageur » (token-subset strict, zéro lemmatisation FR, zéro alias).
2. **Architecture incomplète** — `ATTACK` exige déjà un `combat_state`, `MOVE` narre sans changer la location, `current_beat_index` jamais incrémenté nulle part.
3. **Zéro UX d'onboarding** — aucun embed scène ne dit aux joueurs qui/quoi/où, ils inventent → resolver échoue → narrator hallucine.

## Décisions actées

- **MOVE en texte libre fait le vrai changement de location en DB.** `/move` devient deprecated.
- **Fallback LLM autorisé** pour la résolution d'entité quand le matching Python échoue (1 appel 4B ~300 tokens).
- **Lots séquentiels**, un agent par session, dans l'ordre ci-dessous.

## Status board

| # | Lot | Brief | Pré-requis | Statut | Note |
|---|-----|-------|-----------|--------|------|
| 1 | **A — Scene Awareness** | [`lot_A_scene_awareness.md`](lot_A_scene_awareness.md) | — | DONE | `4bd2cb7` ; scene embed au launch + post-MOVE, narrateur refusal grounded sur `npcs_present`/`connections` ; PNJ traités comme `list[str]` (cf. notes) ; smoke MCP en attente (tester bot offline) |
| 2 | **B — Entity Resolution** | [`lot_B_entity_resolution.md`](lot_B_entity_resolution.md) | — | DONE | Lemmatisation FR + fuzzy + alias NPC + fallback LLM 4B ; tests/bot/test_entity_resolver.py 35 verts |
| 3 | **D — Story Progression** | [`lot_D_story_progression.md`](lot_D_story_progression.md) | A, B (idéal) | TODO | |
| 4 | **C — Combat Initiation** | [`lot_C_combat_initiation.md`](lot_C_combat_initiation.md) | **B** | DONE | Bootstrap CombatState depuis ATTACK texte libre + fallback NPC dans `_resolve_combatant` ; helpers `build_pc_combatants`/`build_npc_combatant` partagés avec le cog ; hook `_should_trivial_resolve` no-op pour Lot E ; tests verts (entity_resolver 38 + scénario bootstrap). |
| 5 | **E — Trivial NPC Death** | [`lot_E_trivial_npc_death.md`](lot_E_trivial_npc_death.md) | **C** | TODO | |
| 6 | **F — Narrator JSON** | [`lot_F_narrator_json.md`](lot_F_narrator_json.md) | — | DONE | Prompt durci + `LLMParseError` + dump auto dans `logs/narrator_failures/` ; tests/bot/test_llm_retry.py verts. Mesure live ≤ 5% à valider sur tester bot. |

**Convention** : avant de finir sa session, chaque agent met à jour la colonne `Statut` (TODO / IN_PROGRESS / DONE / BLOCKED) et écrit une ligne dans `Note` (commit hash, blocage rencontré, etc.). Il remplit aussi la section « Notes de l'agent » de son brief.

## Vérification end-to-end (après tous les lots)

- `uv run pytest`, `uv run ruff check .`, `uv run mypy .` verts.
- Rejeu live sur tester bot (MCP `discord-test`) du script de la session du 7 avril :
  1. `/start_campaign` → embed scène avec Jeanne et Père Thomas nommés explicitement.
  2. « @bot on parle au villageois » → Talk résolu vers Jeanne.
  3. « @bot j'attaque le villageois » → combat démarre OU mort triviale, jamais Improvise.
  4. « @bot on entre dans le donjon » → location change en DB, embed nouvelle scène, bible logge « Beat 2/13 ».
  5. Journal de la nouvelle campagne montre les marqueurs de beat qui changent et les faits de meurtre éventuels.
- Taux de retries narrator ≤ 5%.

## Hors scope global

- Pas de changement de modèle 4B/9B.
- Pas de refacto memory/ChromaDB.
