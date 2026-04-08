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


def test_generate_handles_missing_fields():
    client = MagicMock()
    client.chat_json.return_value = {
        "personality": "Stoïque.",
        "description": "Sombre.",
    }
    generator = NPCGenerator(client)
    sheet = generator.generate(
        npc_name="X", location_context="Y", campaign_theme="Z",
    )
    assert sheet.secrets == []
    assert sheet.knowledge == []
