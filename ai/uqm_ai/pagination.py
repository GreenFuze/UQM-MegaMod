"""Breaks generated prose into subtitle pages.

The game already pages subtitles: SplitSubPages in libs/sound/trackplayer.c
treats every newline as a page break, times each page from its length, and
adds leading/trailing ellipses when a page does not end on punctuation. The
canonical dialogue files rely on exactly this - each line of spathi.txt is
one page.

So generated text only has to be broken the same way. Emitting one long
unbroken line is what makes it overflow the subtitle box.
"""

from __future__ import annotations

import re

# Canonical pages in the shipped dialogue run roughly 60-90 characters.
# Staying inside that keeps generated text visually indistinguishable from
# the original, at any font size the HD content uses.
DEFAULT_PAGE_WIDTH = 78

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def paginate(text: str, width: int = DEFAULT_PAGE_WIDTH) -> str:
    """Return text with newlines inserted at page boundaries.

    Breaks on sentence boundaries where possible, since a page that ends
    mid-clause reads worse than a slightly short one, and falls back to word
    wrapping for sentences longer than a page.
    """
    collapsed = " ".join(text.split())
    if not collapsed:
        return ""

    pages: list[str] = []
    current = ""

    for sentence in _SENTENCE_END.split(collapsed):
        if not sentence:
            continue

        # A sentence that fits on the current page joins it.
        candidate = f"{current} {sentence}".strip()
        if len(candidate) <= width:
            current = candidate
            continue

        if current:
            pages.append(current)
            current = ""

        # A sentence longer than one page is word-wrapped across pages.
        if len(sentence) <= width:
            current = sentence
        else:
            for chunk in _wrap_words(sentence, width):
                pages.append(chunk)
            current = pages.pop() if pages else ""

    if current:
        pages.append(current)

    return "\n".join(pages)


def _wrap_words(sentence: str, width: int) -> list[str]:
    """Greedy word wrap. A single word longer than width is left intact
    rather than split mid-word, which would be unreadable."""
    lines: list[str] = []
    current = ""

    for word in sentence.split():
        candidate = f"{current} {word}".strip()
        if len(candidate) <= width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word

    if current:
        lines.append(current)
    return lines
