"""Detection-only Unicode projection shared by evidence safety scanners."""

from __future__ import annotations

import unicodedata

_DETECTION_CONFUSABLES = str.maketrans(
    {
        "\u03b1": "a",  # Greek small alpha
        "\u0430": "a",  # Cyrillic small a
        "\u03f2": "c",  # Greek small lunate sigma
        "\u0441": "c",  # Cyrillic small es
        "\u0435": "e",  # Cyrillic small ie
        "\u0261": "g",  # Latin small script g
        "\u04bb": "h",  # Cyrillic small shha
        "\u0456": "i",  # Cyrillic small byelorussian-ukrainian i
        "\u03b9": "i",  # Greek small iota
        "\u03ba": "k",  # Greek small kappa
        "\u043a": "k",  # Cyrillic small ka
        "\u043c": "m",  # Cyrillic small em
        "\u03bf": "o",  # Greek small omicron
        "\u043e": "o",  # Cyrillic small o
        "\u03c1": "p",  # Greek small rho
        "\u0440": "p",  # Cyrillic small er
        "\u03c3": "s",  # Greek small sigma
        "\u03c2": "s",  # Greek small final sigma
        "\u0455": "s",  # Cyrillic small dze
        "\u03c4": "t",  # Greek small tau
        "\u0442": "t",  # Cyrillic small te
        "\u0443": "y",  # Cyrillic small u
    }
)


def detection_match_text(text: str) -> str:
    """Project text for matching without changing bytes retained as evidence."""

    normalized = unicodedata.normalize("NFKD", text)
    visible = "".join(
        character
        for character in normalized
        if not _is_default_ignorable(character)
        and not unicodedata.category(character).startswith("M")
    )
    return visible.casefold().translate(_DETECTION_CONFUSABLES)


def _is_default_ignorable(character: str) -> bool:
    codepoint = ord(character)
    return (
        unicodedata.category(character) == "Cf"
        or 0xFE00 <= codepoint <= 0xFE0F
        or 0xE0100 <= codepoint <= 0xE01EF
    )
