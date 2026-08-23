#!/usr/bin/env python3
"""Report Hebrew-document lines that do not open with a Hebrew word.

A renderer decides paragraph direction from the first strong character, so a block
that opens in Latin flips to left-to-right. This script flags the lines that start a
block - headings, list items, table rows and the first line of each paragraph - whose
first meaningful character is not Hebrew.

Fenced code blocks, blank lines, horizontal rules, table separator rows and paragraph
continuation lines are skipped: only the character that actually sets the direction of
a block matters.

Usage:
    python3 check_rtl.py docs/QA.md [more.md ...]

Exit code:
    0 when every checked file is clean, 1 when at least one violation was found.
"""

import re
import sys

HEBREW = re.compile(r"[֐-׿]")
# Leading markdown that is not itself content: heading hashes, bullets, ordered-list
# numbers, blockquote markers and the opening pipe of a table row.
MARKERS = re.compile(r"^[\s>|]*(?:#{1,6}\s*|[-*+]\s+|\d+[.)]\s+)?[\s|]*")
RULE = re.compile(r"^\s*([-*_])\1{2,}\s*$")          # --- *** ___
TABLE_SEP = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$")  # |---|---|


def violations(path):
    """Yield (line_number, line) for every block-opening line that is not Hebrew-first.

    Args:
        path: Path to a markdown file.

    Yields:
        tuple[int, str]: The 1-based line number and the offending line.
    """
    in_code = False
    previous_blank = True          # the first line of the file starts a block
    for number, raw in enumerate(open(path, encoding="utf-8"), start=1):
        line = raw.rstrip("\n")
        if line.lstrip().startswith("```"):
            in_code = not in_code
            previous_blank = False
            continue
        if in_code:
            continue
        if not line.strip():
            previous_blank = True
            continue
        if RULE.match(line) or TABLE_SEP.match(line):
            previous_blank = False
            continue

        starts_block = (
            previous_blank                       # first line of a paragraph
            or line.lstrip().startswith("#")     # heading
            or re.match(r"^\s*([-*+]\s+|\d+[.)]\s+)", line)  # list item
            or line.lstrip().startswith("|")     # table row
        )
        previous_blank = False
        if not starts_block:
            continue                             # a wrapped continuation line

        content = MARKERS.sub("", line).lstrip("*_`~ ")
        if content and not HEBREW.match(content[0]):
            yield number, line


def main():
    """Check every path given on the command line and print a report."""
    paths = sys.argv[1:] or ["docs/QA.md"]
    total = 0
    for path in paths:
        found = list(violations(path))
        total += len(found)
        print(f"\n{path}: {len(found)} violation(s)")
        for number, line in found:
            print(f"  {number:>5}: {line[:100]}")
    print(f"\ntotal: {total}")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
