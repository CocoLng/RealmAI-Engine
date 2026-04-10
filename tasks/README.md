# Tasks — Orchestrateur de travail

Ce dossier organise le travail d'implémentation du projet RealmAI-Engine. Chaque fichier dans `agents/` est une unité de travail autonome conçue pour être exécutée par un agent Claude Code.

## État actuel

**Chantier en cours** : Refonte du système de personnage
**Spec de référence** : `docs/superpowers/specs/2026-04-10-character-system-refactor-design.md`

## Agents — Ordre d'exécution

Les agents doivent être exécutés **dans l'ordre**. Chaque agent dépend du précédent.

```
01-package-split ──→ 02-features-and-skills ──→ 03-standard-array-and-creation ──→ 04-discord-wizard ──→ 05-db-migration-and-cleanup
```

| # | Agent | Scope | Complexité | Status |
|---|-------|-------|------------|--------|
| 01 | [package-split](agents/01-package-split.md) | Découper `character.py` → package `character/` | Moyenne | ✅ Terminé |
| 02 | [features-and-skills](agents/02-features-and-skills.md) | Feature system + 18 Skills D&D 5e | Élevée | ✅ Terminé |
| 03 | [standard-array-and-creation](agents/03-standard-array-and-creation.md) | Standard Array + refonte `create_character()` | Moyenne | ✅ Terminé |
| 04 | [discord-wizard](agents/04-discord-wizard.md) | Wizard Discord : stats + skills + flow complet | Élevée | ✅ Terminé |
| 05 | [db-migration-and-cleanup](agents/05-db-migration-and-cleanup.md) | Backfill DB, isolation, documentation | Faible | ✅ Terminé |

### Légende status

- ⬜ À faire
- 🔄 En cours
- ✅ Terminé
- ❌ Bloqué

## Règles pour chaque agent

1. **Lire le fichier agent** en entier avant de commencer
2. **Vérifier les dépendances** : l'agent précédent doit être terminé
3. **Lire le spec** (`docs/superpowers/specs/2026-04-10-character-system-refactor-design.md`) pour le contexte complet
4. **Valider à la fin** : `uv run pytest` + `uv run ruff check .` + `uv run mypy .` — tout vert
5. **Commiter** avec un message conventionnel (ex: `refactor: split character.py into character/ package`)
6. **Ask User** poser des questions en cas de doute, ne pas deviner — mieux vaut demander que faire une mauvaise implémentation
7. **Documenter** toute décision importante ou changement de design relevante dans la doc du code docs/internal/
8. **Discrétion** Ne pas se mettre en co-auteur de commit, mode undercover agent

## Fichiers associés

| Fichier | Rôle |
|---------|------|
| `tasks/todo.md` | TODO générale du projet (au-delà de ce chantier) |
| `tasks/agents/*.md` | Fiches agent détaillées |
| `docs/superpowers/specs/2026-04-10-character-system-refactor-design.md` | Spec de design approuvée |

## Items différés (hors scope actuel)

Ces items sont notés pour des phases ultérieures :

- Backgrounds D&D 5e (Acolyte, Criminal, Noble, etc.)
- Feats (ASI-ou-feat aux niveaux 4/8/12/16/19)
- Multiclassing
- Langues
- Tool proficiencies
- Class features niveau 2+
- Point Buy / 4d6-drop-lowest
- Boutique achat-vente
- Catalogue de sorts étendu
