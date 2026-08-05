"""Format-agnostic chunk (heading-bounded section) detection over an assembled Markdown blob.

Runs uniformly over PDF/JATS/HTML output once all three converge on genuine ATX headings in
their rendered Markdown. See
docs/requirement-specification/02-storage.md#per-document-structure-manifestsqlite-replaces-structurejsonl
(entry kind 2, "chunk").
"""

import re

_ATX_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$")
_FENCE = re.compile(r"^```")


def _line_offsets(markdown: str) -> list[int]:
    r"""Character offset at which each line (split on '\n') starts."""
    offsets = [0]
    for line in markdown.split("\n")[:-1]:
        offsets.append(offsets[-1] + len(line) + 1)
    return offsets


def detect_chunks(markdown: str) -> list[dict]:
    """Detect heading-bounded chunks over `markdown`, skipping headings inside fenced code blocks.

    Returns one entry per heading occurrence - a subsection and its enclosing section both get
    their own (overlapping) entry, since each heading's span runs to the next heading at the
    same-or-shallower level, not to its own next sibling only.
    """
    lines = markdown.split("\n")
    offsets = _line_offsets(markdown)
    headings: list[dict] = []
    in_fence = False
    for line, offset in zip(lines, offsets, strict=True):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _ATX_HEADING.match(line)
        if match is None:
            continue
        headings.append({"level": len(match.group(1)), "key": match.group(2), "start": offset})

    chunks: list[dict] = []
    for i, heading in enumerate(headings):
        end = len(markdown)
        for later in headings[i + 1 :]:
            if later["level"] <= heading["level"]:
                end = later["start"]
                break
        chunks.append({"key": heading["key"], "start": heading["start"], "length": end - heading["start"]})
    return chunks
