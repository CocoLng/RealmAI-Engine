# Tasks — Orchestrateur de travail

Ce dossier organise le travail d'implémentation du projet RealmAI-Engine. Chaque fichier dans `agents/` est une unité de travail autonome conçue pour être exécutée par un agent Claude Code.

## État actuel

**Chantier en cours** : UX Immersion — amélioration de l'expérience onboarding → lancement
**Chantier précédent** : Refonte du système de personnage ✅ (archivé dans `tasks/archive/2026-04-10-character-system-refactor/`)

## Agents — Ordre d'exécution

Exécution séquentielle — tous modifient `bot/campaign_launcher.py`.

```
01-character-recreation ──→ 02-force-launch ──→ 03-launch-immersion
```

| # | Agent | Scope | Complexité | Status |
|---|-------|-------|------------|--------|
| 01 | [character-recreation](agents/01-character-recreation.md) | Re-clic "Créer mon personnage" pour recommencer | Moyenne | ✅ Terminé |
| 02 | [force-launch](agents/02-force-launch.md) | Bouton pour lancer sans les joueurs manquants | Moyenne | ✅ Terminé |
| 03 | [launch-immersion](agents/03-launch-immersion.md) | Purge channel + countdown + opening crawl | Moyenne | ✅ Terminé |

### Légende status

- ⬜ À faire
- 🔄 En cours
- ✅ Terminé
- ❌ Bloqué

## Règles pour chaque agent

1. **Lire le fichier agent** en entier avant de commencer
2. **Vérifier les dépendances** : l'agent précédent doit être terminé
3. **Valider à la fin** : `uv run pytest` + `uv run ruff check .` + `uv run mypy .` — tout vert
4. **Commiter** avec un message conventionnel (ex: `feat(bot): allow character re-creation before launch`)
5. **Ask User** poser des questions en cas de doute, ne pas deviner
6. **Documenter** toute décision importante dans docs/internal/
7. **Discrétion** Ne pas se mettre en co-auteur de commit, mode undercover agent

## Fichiers associés

| Fichier | Rôle |
|---------|------|
| `tasks/todo.md` | TODO générale du projet (au-delà de ce chantier) |
| `tasks/agents/*.md` | Fiches agent détaillées |
| `tasks/archive/` | Chantiers précédents archivés |

## Items différés (hors scope actuel)

- Item 6 du backlog UX : système de level-up avec choix de stats (nécessite spec dédiée)
- Backgrounds D&D 5e (Acolyte, Criminal, Noble, etc.)
- Feats (ASI-ou-feat aux niveaux 4/8/12/16/19)
- Multiclassing
- Langues, Tool proficiencies, Class features niveau 2+
- Point Buy / 4d6-drop-lowest
- Boutique achat-vente, Catalogue de sorts étendu
