# Combat System — End-to-End Test Results

Gate-of-completion record for the combat system chantier. This document
tracks two layers of verification:

1. **Automated pytest e2e** (`tests/scenarios/test_combat_system_e2e.py`)
   — green, runs on every `uv run pytest` invocation.
2. **Live Discord test** via the `discord-test` MCP — executed manually
   when the game bot is running in `TEST_MODE=true`. The script below
   walks through the Mageta vs Vellus scenario and expected outcomes.

## Layer 1 — Automated pytest e2e (✅ green)

File: [tests/scenarios/test_combat_system_e2e.py](../../tests/scenarios/test_combat_system_e2e.py)

13 tests. Each covers a specific slice of the combat lifecycle:

| # | Test | What it verifies |
|---|------|------------------|
| 1 | `test_combat_starts_with_party_wide_state` | Party + boss bootstrap, round 1, `is_active=True` |
| 2 | `test_vellus_has_boss_stat_block` | Stat block wired: BOSS tier, legendary points, phases |
| 3 | `test_phase_2_triggers_at_50_percent_hp` | Phase transition flips `triggered=True` at 50% HP |
| 4 | `test_phase_2_above_threshold_not_triggered` | No false trigger above the threshold |
| 5 | `test_truce_succeeds_in_phase_1` | CHA SUCCESS → all enemies fled, `finalize_combat(TRUCE)` |
| 6 | `test_truce_refused_after_phase_2_triggered` | Phase-2 auto-refusal, no roll, no Action consumed |
| 7 | `test_truce_failure_below_dc_keeps_combat_active` | Failed CHA → combat continues, Action consumed |
| 8 | `test_killing_vellus_yields_victory_summary` | VICTORY path: XP=500, loot="Lame de sable", state preserved |
| 9 | `test_killing_mageta_yields_defeat_summary` | DEFEAT path: killed PC listed, no XP |
| 10 | `test_trivial_weak_enemy_kill_still_works` | Non-regression: commoner kill loop intact |
| 11 | `test_talk_validation_outside_combat_still_accepted` | TRUCE path doesn't break the dialogue path |
| 12 | `test_move_in_combat_still_blocked` | Only TALK got the TRUCE exception — MOVE stays blocked |
| 13 | `test_double_finalize_does_not_double_xp` | Idempotence: pipeline + TurnManager can both call finalize |

**Invocation**:

```bash
uv run pytest tests/scenarios/test_combat_system_e2e.py -v
```

**Coverage in practice** — these 13 tests collectively exercise:
- `bot.combat_end.finalize_combat` (VICTORY, DEFEAT, TRUCE paths + idempotence)
- `bot.combat_truce.attempt_truce` (success, failure, auto-refusal for phase-2 boss, mindless guard)
- `engine.validators.validate_truce_attempt` (via `validate_exploration_action` delegation)
- `engine.npc_stat_block.NPCStatBlock.mindless` (new field)
- `engine.combat.CombatState._finalized` (idempotence flag)
- `engine.combat_phases.check_phase_transition` (phase 2 trigger)
- `engine.combat.check_combat_end` (VICTORY / DEFEAT / FLED categorisation)
- Scenario runner integration with `bot.combat_end` (new contract: preserve `combat_state`)

## Layer 2 — Live Discord test (manual, via `discord-test` MCP)

**Pre-requisites**:

```bash
# Game bot running in TEST_MODE with a test server / channel configured.
TEST_MODE=true uv run python -c "from bot.bot import run_bot; run_bot()"
```

The `.env` must provide `DISCORD_BOT_TOKEN`, `TESTER_BOT_TOKEN`,
`TESTER_BOT_ID`, `TEST_CHANNEL_ID`, `GAME_BOT_ID` (see
`.env.example`). Both bots must be invited to the test server.

### Test script

Each step is an MCP tool call through `discord-test` and the expected
outcome observable on the real Discord channel.

| # | Command | Expected |
|---|---------|----------|
| 1 | `discord_status()` | `online=True`, `tester_connected=True` |
| 2 | `discord_send_command("start_campaign", {"theme": "désert", "players": "1"})` | Campaign created, opening crawl posted, first beat arrives |
| 3 | `discord_send_command("create_character", {"name": "Mageta", "race": "Human", "class_": "Ranger"})` | Character sheet embed posted |
| 4 | `discord_send_command("look")` | Scene embed posted for the starting location |
| 5 | `@bot j'attaque Vellus le Mentisseur` (via test channel send) | `⚔️ Combat commence` banner + initiative list + combat hub embed + `CombatActionView` buttons |
| 6 | `discord_click_button(hub_msg_id, "Attack")` → `discord_select_option(..., "Vellus le Mentisseur")` | `build_attack_roll_embed` posted with d20 + damage dice breakdown |
| 7 | Wait for Vellus's turn | `📜 Vellus le Mentisseur → Mageta` summary, then either multiattack or a legendary action embed |
| 8 | Keep attacking until Vellus HP ≤ 50% | `🎭 Transition de phase` narration embed posted, phase bonuses visible on next attack roll |
| 9 | `@bot je tente de parler à Vellus pour qu'il se rende` (phase 2) | Validator rejects with "rage absolue" message (phase-2 auto-refusal) |
| 10 | `@bot je me déplace vers le corridor` | Pipeline auto-converts MOVE → FLEE check — not a simple rejection |
| 11 | Keep attacking until Vellus HP = 0 | Combat hub frozen + `🏆 Victoire` end embed with: "Ennemis vaincus: Vellus le Mentisseur", "Butin: Lame de sable", "Expérience gagnée: 500 XP par survivant", "Durée: N rounds" |
| 12 | `discord_get_game_state()` | `combat_state.is_active=False`, `combat_state.end_reason="victory"` — **not** `None` (finalize_combat invariant: state preserved) |
| 13 | `discord_send_command("resume")` after simulating a bot restart | Combat state reloaded intact with `is_active=False` — new combat can bootstrap on next hostile action |

### Non-regression checks

| Scenario | Expected |
|----------|----------|
| Attacking a commoner NPC (hp ≤ 4) outside of a scripted combat beat | Trivial resolve — narrative only, **no** `CombatState` created |
| Beat-social (e.g. NPC dialogue beat) | Normal TALK dispatch, no combat bootstrap |
| TALK outside combat on a friendly NPC | Dialogue flow (`_resolve_talk`), reveals + disposition changes — not a TRUCE check |

### Status

- [x] Layer 1 — pytest e2e, **13/13 green** (run: `uv run pytest tests/scenarios/test_combat_system_e2e.py -v`)
- [ ] Layer 2 — live Discord run. Pending user execution: start the bot
  with `TEST_MODE=true` and walk through the script above. Record
  pass/fail per step below as they're verified.

### Layer 2 results (to be filled in during the live run)

| # | Step | Pass / Fail | Notes |
|---|------|-------------|-------|
| 1 | Status | | |
| 2 | start_campaign | | |
| 3 | create_character | | |
| 4 | look | | |
| 5 | @bot attaque Vellus | | |
| 6 | Attack button + target select | | |
| 7 | Vellus turn response | | |
| 8 | Boss phase transition | | |
| 9 | TALK phase-2 refusal | | |
| 10 | MOVE auto-convert FLEE | | |
| 11 | Victory end embed | | |
| 12 | Preserved combat_state | | |
| 13 | Resume after restart | | |
