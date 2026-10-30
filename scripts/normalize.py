"""Arabic normalization used only for matching and alignment."""

from __future__ import annotations

import unicodedata


_ALIF_VARIANTS = str.maketrans({
    "\u0622": "\u0627",
    "\u0623": "\u0627",
    "\u0625": "\u0627",
    "\u0671": "\u0627",
})


def normalize_arabic(text: str) -> str:
    """Remove reading marks and normalize common Arabic orthographic variants."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    text = unicodedata.normalize("NFC", text).translate(_ALIF_VARIANTS)
    text = text.replace("\u0640", "").replace("\u0649", "\u064a").replace("\u0629", "\u0647")
    return "".join(char for char in text if unicodedata.category(char) != "Mn")