"""Language support for AI prompt injection."""

LANGUAGE_NAMES: dict[str, str] = {
    "fr": "French",
    "en": "English",
    "es": "Spanish",
    "de": "German",
    "pt": "Portuguese",
}


def language_instruction(code: str) -> str:
    """Build a system prompt prefix that instructs the LLM to use a language.

    Args:
        code: ISO 639-1 language code (e.g. "fr", "en").

    Returns:
        Instruction string to prepend to the system prompt.
    """
    name = LANGUAGE_NAMES.get(code, "French")
    return f"IMPORTANT: You MUST write ALL narrative text, dialogue, and descriptions in {name}.\n\n"
