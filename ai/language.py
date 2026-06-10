"""Language support for AI prompt injection."""

import logging

logger = logging.getLogger(__name__)

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
        code: ISO 639-1 language code (e.g. "fr", "en"). Unknown codes fall
            back to French explicitly (with a warning) instead of being
            swallowed silently (M10).

    Returns:
        Instruction string to prepend to the system prompt.
    """
    name = LANGUAGE_NAMES.get(code)
    if name is None:
        logger.warning("Unknown language code %r — falling back to French", code)
        name = "French"
    return f"IMPORTANT: You MUST write ALL narrative text, dialogue, and descriptions in {name}.\n\n"
