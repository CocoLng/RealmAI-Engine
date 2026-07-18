# Contributing to RealmAI Engine

Thanks for your interest! This project is an AI-powered RPG Game Master Discord
bot built on one hard rule:

> **The LLM narrates. The code arbitrates. No exceptions.**

Please keep that principle in mind — most of the conventions below exist to
protect it.

## Ground rules (the non-negotiables)

- **No LLM calls in `engine/`.** Everything in `engine/` is pure, deterministic
  Python — dice, damage, validators, advancement. If you're tempted to let the
  model decide a mechanical outcome, that's a bug. The boss brain calls the LLM
  tactician via an **injected** dependency, never by constructing an `ai/`
  service inside `engine/`.
- **JSON mode everywhere.** Every Ollama call sets
  `response_format={"type": "json_object"}` (centralised in `ai/client.py`).
  Ollama's native tool-calling is not used — it's broken with Qwen 3.5.
- **Pydantic v2 at the boundaries.** No raw dicts cross module seams. Use
  `Enum` for fixed sets, `dataclass` only for internal state that needs no
  validation.
- **Anti-cheat by design.** Player actions pass through `ActionValidator`
  before the engine resolves them, and Discord shows both the narrative *and*
  the raw mechanics.

See [`ARCHITECTURE.md`](ARCHITECTURE.md) §6 for the full list of design
invariants and how each is enforced.

## Development setup

Prerequisites: **Python 3.12+**, [uv](https://docs.astral.sh/uv/),
[Ollama](https://ollama.com/).

```bash
git clone https://github.com/CocoLng/RealmAI-Engine.git
cd RealmAI-Engine
uv sync                      # create .venv + install from uv.lock
ollama pull qwen3.5:9b       # narrator
ollama pull qwen3.5:4b       # interpreter
cp .env.example .env         # then set DISCORD_BOT_TOKEN
```

Run everything through `uv run` — never activate the venv manually. Add runtime
deps with `uv add`, dev deps with `uv add --dev`.

## Quality gates

A change is **not done** until all three are green:

```bash
uv run pytest                 # full suite (~2 900 tests)
uv run ruff check .           # linting
uv run mypy                   # type checking — zero errors, keep it that way
```

`mypy` reads its configuration from `pyproject.toml`, so pass no arguments:
the `files` key already covers every checked package. The gate is currently
**green at zero errors** — a new error means your change, not pre-existing
debt.

Two deliberate exemptions, both documented inline in `pyproject.toml`:

- `tests.*` is exempt. Test doubles, monkeypatched attributes and half-built
  Discord objects are the point of a fake, and pytest is the gate that
  actually proves them.
- `method-assign` is tolerated under `bot/views/`. Building a Select/Button
  and assigning its `.callback` is discord.py's documented idiom for dynamic
  components; subclassing every component to satisfy mypy would be a large,
  behaviour-risking refactor for no runtime benefit.

Reach for `cast()` over `# type: ignore` when you know an invariant the type
system cannot see, and say *why* in a comment. Prefer neither: an
`isinstance` gate added purely to please mypy can silently skip real work.

Useful subsets:

```bash
uv run pytest tests/engine -q                                # engine only
uv run pytest tests/scenarios                                # end-to-end scenarios
uv run python -m tests.simulation --mock-llm --max-turns 20  # playthrough smoke
```

### Testing expectations

- Every mechanical change needs a test. If it rolls dice, deals damage, or
  validates an action, it has pytest coverage. Engine coverage target is ≥98%.
- Tests mirror the source layout under `tests/`.
- Prefer the mock-LLM simulator/scenario paths for fast, deterministic checks;
  reach for real Ollama only when fidelity matters.
- For Discord-visible behaviour, see [`docs/internal/TESTING.md`](docs/internal/TESTING.md).

## Commits & pull requests

- **Conventional Commits**: `feat:`, `fix:`, `test:`, `docs:`, `refactor:`,
  `chore:`. Keep commits focused and the working tree green.
- Keep changes minimal and root-cause-driven — touch only what's necessary, no
  drive-by refactors unrelated to your goal.
- Before opening a PR: run the three quality gates, and update
  [`tasks/todo.md`](tasks/todo.md) / [`docs/internal/`](docs/internal/) if your
  change moves the architecture.
- In your PR description, say what changed and how you verified it (paste the
  relevant test output).

## Project layout

`engine/` (pure rules) · `ai/` (LLM I/O, JSON only) · `memory/` (4-layer
context) · `world/` (domain models) · `bot/` (Discord) · `db/` (persistence) ·
`mcp_discord/` (test rig) · `tests/`. Start from
[`ARCHITECTURE.md`](ARCHITECTURE.md), then dive into
[`docs/internal/`](docs/internal/README.md) for module-by-module detail.

## Code of Conduct

By participating you agree to abide by our
[Code of Conduct](CODE_OF_CONDUCT.md).
