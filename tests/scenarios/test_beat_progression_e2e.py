"""End-to-end /hint and beat progression on a live test Discord server.

Skipped automatically when DISCORD_TEST_BOT_TOKEN is not set.
"""

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DISCORD_TEST_BOT_TOKEN"),
    reason="Live Discord test requires DISCORD_TEST_BOT_TOKEN",
)


def test_full_beat_progression_via_discord_placeholder():
    """Placeholder scenario — actual implementation requires the discord-test
    fixture which depends on the project's existing tester-bot infrastructure.

    To enable: set DISCORD_TEST_BOT_TOKEN, ensure the tester bot is running,
    and use the discord_tester fixture from conftest.py to drive a real
    /start_campaign → action → /hint → beat advance sequence.
    """
    # When the live test infra is wired, replace this with:
    # 1. await discord_tester.send_command("/start_campaign", args={"theme": "mystery"})
    # 2. Read story_arc state, take an action that should NOT advance
    # 3. Verify beat unchanged
    # 4. /hint level 1 returns text
    # 5. Take an action targeting the beat objective, verify advance
    # 6. Verify Arc Tracker pinned message updates
    pytest.skip("Live e2e scenario implementation deferred — requires manual run")
