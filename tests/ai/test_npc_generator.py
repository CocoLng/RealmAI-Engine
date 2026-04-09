from unittest.mock import MagicMock

from ai.models import NPCSheet
from ai.npc_generator import NPCGenerator


def test_generate_returns_npc_sheet():
    client = MagicMock()
    client.chat_json.return_value = {
        "personality": "Méfiant mais loyal envers les justes. Parle peu.",
        "description": "Un ermite voûté, robe de bure tachée de cendre.",
        "secrets": ["Sait que Dom André est corrompu."],
        "knowledge": [
            "Connaît l'entrée de la crypte sous l'autel.",
            "Le village a été fondé en 1187.",
        ],
    }

    generator = NPCGenerator(client)
    sheet = generator.generate(
        npc_name="Élie l'Ermite",
        location_context="La Paroisse de Saint-Michel — vieille église corrompue.",
        campaign_theme="sous une église",
        language="fr",
    )

    assert isinstance(sheet, NPCSheet)
    assert "Méfiant" in sheet.personality
    assert "corrompu" in sheet.secrets[0]
    assert len(sheet.knowledge) == 2

    # Verify the prompt was sent
    args, _kwargs = client.chat_json.call_args
    messages = args[1]
    user_msg = messages[-1]["content"]
    assert "Élie l'Ermite" in user_msg
    assert "Paroisse de Saint-Michel" in user_msg
    assert "sous une église" in user_msg


def test_generate_fallback_on_empty_secrets_and_knowledge():
    """M11: Empty secrets/knowledge get fallback defaults instead of ValidationError."""
    client = MagicMock()
    client.chat_json.return_value = {
        "personality": "Stoïque.",
        "description": "Sombre.",
        "secrets": [],
        "knowledge": [],
    }
    generator = NPCGenerator(client)
    sheet = generator.generate(
        npc_name="X", location_context="Y", campaign_theme="Z",
    )

    assert isinstance(sheet, NPCSheet)
    assert len(sheet.secrets) == 1
    assert "secret" in sheet.secrets[0].lower()
    assert len(sheet.knowledge) == 1
    assert "environs" in sheet.knowledge[0].lower()


def test_generate_fallback_on_missing_secrets_and_knowledge():
    """M11: Missing secrets/knowledge keys get fallback defaults."""
    client = MagicMock()
    client.chat_json.return_value = {
        "personality": "Jovial.",
        "description": "Grand et souriant.",
    }
    generator = NPCGenerator(client)
    sheet = generator.generate(
        npc_name="Bob", location_context="Tavern", campaign_theme="dark fantasy",
    )

    assert isinstance(sheet, NPCSheet)
    assert len(sheet.secrets) == 1
    assert len(sheet.knowledge) == 1


def test_generate_preserves_valid_secrets_and_knowledge():
    """M11: When secrets/knowledge are non-empty, no fallback is applied."""
    client = MagicMock()
    client.chat_json.return_value = {
        "personality": "Curieux.",
        "description": "Petit et vif.",
        "secrets": ["Knows the dragon's weakness", "Has a hidden treasure map"],
        "knowledge": ["Local herb expert"],
    }
    generator = NPCGenerator(client)
    sheet = generator.generate(
        npc_name="Pip", location_context="Forest", campaign_theme="adventure",
    )

    assert len(sheet.secrets) == 2
    assert len(sheet.knowledge) == 1
    assert "dragon" in sheet.secrets[0].lower()
