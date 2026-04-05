# Phase 1 — Game Engine (No AI, No Discord)

Build `engine/` with full test coverage. Playable in terminal.

## Module Order

- [x] **dice.py** — Dice expressions ("2d6+3") → DiceResult
- [x] **character.py** — Classes, races, ability scores, levels, HP, AC
- [x] **inventory.py** — Items, equipment, weight, attunement
- [ ] **spells.py** — Spell definitions, slots, casting, effects
- [ ] **conditions.py** — Status conditions and mechanical effects
- [ ] **combat.py** — Initiative, attacks, damage, turns, death saves
- [ ] **validators.py** — Action legality checks (ActionValidator)

## Quality Gates (per module)

- [ ] pytest coverage >80%
- [ ] ruff check clean
- [ ] mypy clean
- [ ] Conventional commit per module

## Milestone

When all modules pass: build a simple terminal REPL (`main.py`) that runs a
solo combat encounter with hardcoded enemies — proves the engine works end-to-end
without any AI or Discord dependency.
