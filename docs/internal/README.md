# RealmAI-Engine — Documentation interne

Documentation technique à jour (snapshot 2026-04-09) décrivant ce qui est **réellement implémenté** dans le dépôt, pas seulement ce qui était spécifié. Ces documents sont écrits pour qu'un·e développeur·euse (ou un agent) puisse reprendre le projet sans avoir à tout redécouvrir.

> Les specs historiques dans `docs/superpowers/specs/` et `docs/superpowers/plans/` sont des traces de design — elles peuvent diverger du code. En cas de doute, **le code fait foi**, et ce dossier `docs/internal/` est la synthèse consolidée.

## Index

### Vue d'ensemble
- [ARCHITECTURE.md](ARCHITECTURE.md) — Architecture globale, couches, flux de données, arborescence.
- [STATE.md](STATE.md) — État d'avancement concret : ce qui est fait, en cours, non-commencé.
- [ISSUES.md](ISSUES.md) — Bugs, anomalies, dette technique, points d'amélioration.

### Flux métier
- [CAMPAIGN_LIFECYCLE.md](CAMPAIGN_LIFECYCLE.md) — Initialisation, onboarding, sauvegarde, reprise, fin de campagne.
- [ACTION_PIPELINE.md](ACTION_PIPELINE.md) — Comment une phrase du joueur devient un récit (6 phases).
- [NARRATIVE_COHERENCE.md](NARRATIVE_COHERENCE.md) — Canon, disposition des PNJ, Story Director, story-bible.

### Couches techniques
- [GAME_ENGINE.md](GAME_ENGINE.md) — Moteur de règles déterministe (`engine/`).
- [AI_LAYER.md](AI_LAYER.md) — Couche LLM (`ai/`) : Interpreter, Narrator, NPC agent, générateurs.
- [MEMORY_SYSTEM.md](MEMORY_SYSTEM.md) — Système de mémoire 4 couches (`memory/`).
- [DATABASE.md](DATABASE.md) — Modèles Pydantic ↔ SQLAlchemy, repositories, mappers (`db/`, `world/`).
- [DISCORD_BOT.md](DISCORD_BOT.md) — Bot Discord (`bot/`) : cogs, vues, embeds, sessions.
- [TESTING.md](TESTING.md) — Pytest, ScenarioRunner, MCP Discord, CI.

## Conventions de lecture

- **Chemins** cliquables : `[file](../../path)` style markdown.
- Sévérités dans ISSUES.md : 🔴 bloquant · 🟠 élevé · 🟡 moyen · 🟢 mineur.
- Tout ce qui est marqué « TODO » / « non implémenté » est vrai au snapshot et pointe vers le fichier concerné.

## Règle de mise à jour

Ces docs sont la **synthèse de ce qui est implémenté**. Toute tâche d'un chantier `tasks/*/` qui modifie un comportement, une API publique, un flux, ou un modèle data **doit mettre à jour les fichiers concernés ici**, avec des additions **concises** :

- Paragraphe court (3-8 phrases) ou ligne de table — pas de dissertation.
- Référencer le code (`bot/file.py:line`) plutôt que le dupliquer.
- Corriger les sections obsolètes plutôt que d'ajouter une section "Legacy".
- Mettre à jour `STATE.md` quand un item bascule d'état.
- Retirer les bugs résolus de `ISSUES.md`.

Les chantiers en cours listent leurs obligations d'update dans leur propre README (ex : [tasks/combat/README.md](../../tasks/combat/README.md) section "Documentation").
