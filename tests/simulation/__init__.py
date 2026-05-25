"""Autonomous playthrough simulator.

Headless tool that plays a full RealmAI campaign on its own through the real
Ollama LLM pipeline (Interpreter + Narrator + Story Director + memory), and
emits a deterministic incoherence report.

See:
  - docs/superpowers/specs/2026-05-25-autonomous-playthrough-simulator-design.md
  - docs/superpowers/plans/2026-05-25-autonomous-playthrough-simulator.md

Entry point:
  uv run python -m tests.simulation --max-turns 30 --seed 42
"""
