# Tests et infrastructure QA

~1 260 tests dans 42+ modules, ~18 000 lignes de test, couverture engine ~98%.

## Commandes

```bash
uv run pytest                         # tous les tests
uv run pytest tests/scenarios/        # scénarios end-to-end
uv run pytest -k combat               # filtre par nom
uv run pytest --cov=engine --cov=ai   # coverage
uv run ruff check .                   # lint
uv run mypy .                         # type check
```

Configuration pytest dans [pyproject.toml](../../pyproject.toml) : `asyncio_mode = "auto"`.

## Stratégies de test

### 1. Tests unitaires par module

Chaque module `engine/`, `ai/`, `memory/`, `world/`, `db/`, `bot/` a un `test_<module>.py` correspondant. Pattern :

- Mocks Ollama via `pytest-httpx` (intercepte les calls httpx).
- In-memory SQLite via `StaticPool` pour déterminisme.
- Monkeypatching dés : patcher `engine.combat.roll`, pas `engine.dice.roll` (leçon apprise).

### 2. Tests de scénario (`tests/scenarios/`)

**ScenarioRunner** ([tests/scenarios/scenario_runner.py](../../tests/scenarios/scenario_runner.py), 896 lignes) : orchestrateur de gameplay end-to-end **sans Discord réel**.

- **Real engine + real in-memory DB** (foreign keys ON).
- **Mocked Discord interactions** : `TestInteraction` wrap `discord.Interaction`, capture embeds/buttons/selects via `EmbedCapture`.
- **AI disabled** : flux déterministe sans Ollama.
- **Multiplayer** : `MockMember` avec IDs Discord stables.

**API** :
```python
await scenario.start_campaign(theme="Dungeon", players=1)
await scenario.add_player("Hero", race="Human", class_="Fighter", player_idx=0)
await scenario.start_combat(enemies=[make_weak_enemy()])
await scenario.attack(target="Gobelin faible", player_idx=0)
scenario.assert_in_combat()
scenario.assert_hp("Hero", min_hp=5)
scenario.assert_has_item("Hero", "Longsword")
```

Helpers dans [tests/scenarios/conftest.py](../../tests/scenarios/conftest.py) : `make_enemy()`, `make_weak_enemy()`, `make_strong_enemy()`, `give_starter_weapon()`.

**Fichiers de scénarios** :
- `test_campaign_lifecycle.py` — start/save/resume/end + persistance joueur
- `test_combat_scenarios.py` — bootstrap, attaques, death saves, multi-enemies
- `test_combat_system_e2e.py` — gate de fin du chantier combat. Mageta vs Vellus avec boss stat block complet (phase 2, legendary actions), VICTORY/DEFEAT/TRUCE finalisés via `bot.combat_end.finalize_combat`, idempotence double-finalize, non-régression commoner/TALK hors combat/MOVE bloqué. Fixture `vellus_stat_block` dans `conftest.py`.
- `test_edge_cases.py` — actions invalides, items manquants
- `test_persistence_integrity.py` — save → reload → equality
- `test_free_text_exploration.py` — exploration sans chemins structurés
- `test_attack_bootstrap_combat.py` — Lot C : ATTACK hors combat
- `test_trivial_npc_kill.py` — Lot E : one-shot
- `test_beat_advance.py` — Lot D : progression d'arc

**Note ScenarioRunner** : `_finalize_combat` délègue maintenant à `bot.combat_end.finalize_combat` (même code path que le live bot). `assert_not_in_combat` tolère les deux invariants (`combat_state is None` OU `is_active=False`). `attack(target=...)` no-op gracieusement sur cible morte pour que les boucles `for _ in range(10): await scenario.attack(...)` continuent de fonctionner maintenant que le combat_state est préservé post-finalize.

### 3. Tests live Discord via MCP

Couverts par le serveur MCP `mcp_discord/` (voir plus bas) + l'agent `discord-live-testing` skill. Utilisé pour valider des parcours utilisateur réels dans un serveur Discord de test. Nécessite :
- Un vrai bot Discord de test (`DISCORD_BOT_TOKEN`).
- Un bot testeur (`TESTER_BOT_TOKEN`) qui envoie des `!test` commandes.
- `TEST_MODE=true` pour activer le cog `test_bridge` côté jeu.

### 4. Tests d'observabilité

[test_campaign_launcher_observability.py](../../tests/test_campaign_launcher_observability.py) — vérifie que les états / transitions du `CampaignLauncher` émettent bien les logs structurés attendus.

## MCP Discord Test Server

Dans [mcp_discord/](../../mcp_discord/). Serveur MCP stdio exposé à Claude Code (`.mcp.json`).

### Architecture
- **TesterBot** (`discord_client.py`) : client `discord.py` léger qui envoie `!test <command>` au channel de test et lit les réponses du game bot.
- **server.py** (FastMCP) : expose 7 tools, initialise TesterBot singleton (attente 30s de la connexion).
- **config.py** : charge `.env`.

### Tools MCP exposés

| Tool | Rôle |
|---|---|
| `discord_status()` | Check online + connected + test_channel_id |
| `discord_send_command(command, args, player)` | Envoie `!test` et attend réponse (timeout 15s) |
| `discord_read_messages(limit)` | Lit jusqu'à 50 derniers messages du channel |
| `discord_click_button(message_id, button_label, player)` | Simule un clic bouton |
| `discord_select_option(message_id, value, player)` | Simule un select |
| `discord_wait_for_response(timeout)` | Bloque jusqu'à réponse |
| `discord_get_game_state()` | Renvoie `GameSession` live (campaign, chars, combat, npcs, quests) |

### Variables d'env

Voir [.env.example](../../.env.example) :
```
DISCORD_BOT_TOKEN=...
TESTER_BOT_TOKEN=...
TESTER_BOT_ID=...
TEST_CHANNEL_ID=...
GAME_BOT_ID=...
TEST_MODE=true
```

## Scripts utilitaires

- [scripts/reset_dev_data.py](../../scripts/reset_dev_data.py) — wipe campagnes + tables enfants (cascade), préserve `guild_configs`. `uv run python scripts/reset_dev_data.py`.

## Coverage synthétique

| Zone | Fichiers de tests | État |
|---|---|---|
| `engine/` (dice, character, combat, inventory, spells, conditions, validators, starter_gear) | ~8 fichiers, ~2 300 lignes | 🟢 ~98% |
| `world/` | `test_world_models.py`, `tests/world/` | 🟢 solide |
| `db/` | `test_database.py`, `test_db_repos.py`, `test_mappers.py`, `test_player_character_repo.py`, `test_campaign_channel_repo.py` | 🟢 complet |
| `memory/` | `test_memory_models.py`, `test_memory_repos.py`, `test_memory_state.py`, `test_summarizer.py`, `test_semantic.py`, `test_sliding_window.py`, `test_context_assembler.py`, `test_token_utils.py` | 🟢 solide |
| `ai/` | `tests/ai/test_*.py` (10+ fichiers) | 🟡 unit ok, pas d'e2e vrai Ollama |
| `bot/` | `test_cog_*`, `tests/bot/test_action_pipeline*.py`, `test_views.py`, `test_embeds.py`, etc. | 🟡 unit ok, integration light |
| Scénarios | 8 fichiers dans `tests/scenarios/` | 🟢 couvrent gameplay principal |

## Lessons captured (`tasks/lessons.md`)

Parmi les plus importantes :

1. `DamageType` vit dans `inventory.py`, pas `combat.py`.
2. **Ne pas utiliser le tool-calling natif Ollama** avec Qwen 3.5 — cassé. Utiliser `format: json`.
3. Documenter mutation vs immutabilité par module, rester cohérent.
4. `ActiveCondition.duration_rounds` ∈ `{None, ≥1}`, jamais `0`.
5. Monkeypatch `engine.combat.roll` (import local), pas `engine.dice.roll`.
6. Subagents parallèles possibles pour modules indépendants (ex. spells + conditions).
7. `__init__.py` minimalistes pour éviter circular imports.
8. Token estimation : clamp final après boucle pour corriger rounding.
9. ChromaDB `EphemeralClient()` est par process — utiliser `Settings(allow_reset=True)` en fixture.
10. Repos ne commit pas, le caller commit.
11. Valider explicitement les hypothèses pour les batch ops (même `campaign_id`).
12. `pytest-httpx` intercepte httpx global, y compris les calls OpenAI SDK vers Ollama.

## CI/CD

Pas encore de CI GitHub Actions (phase 4 planifiée). Les quality gates sont manuels : `pytest`, `ruff check .`, `mypy .`.

## Gaps

- Pas de tests end-to-end avec un vrai Ollama (tout mock).
- Pas de tests de charge / stress.
- Pas de tests de migration DB (création + ajout de colonne).
- Entity resolver : lemmatisation FR non-exhaustive sur edge cases.
- Silent-fail paths (SemanticMemory indispo, WorldGenerator filtre silencieux) peu testés.
