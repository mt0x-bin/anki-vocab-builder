#!/usr/bin/env python3
"""
Module 1 — Vocab txt parser
============================
Parse vocab.txt format:
    word (pos): vietnamese hint
    phrase: vietnamese hint
    idiom (idiom): vietnamese hint
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class VocabEntry:
    word: str            # English word or phrase
    pos: Optional[str]   # "n", "v", "adj", "adv", "idiom", etc. or None
    vi_hint: str         # original VI from txt (used as hint only)
    raw_line: str        # original line for debugging


# Matches:  word (pos): vi_hint
#   OR:     phrase: vi_hint
#   OR:     multi word phrase (pos): vi_hint
_LINE_RE = re.compile(
    r"^(.+?)"                   # group 1: word/phrase  (non-greedy)
    r"\s*(?:\(([^)]+)\))?\s*"  # group 2: optional (pos)
    r":\s*"                     # colon separator
    r"(.+)$"                    # group 3: Vietnamese hint
)

_POS_MAP = {
    "n": "n",           "noun": "n",
    "v": "v",           "verb": "v",
    "adj": "adj",       "adjective": "adj",
    "adv": "adv",       "adverb": "adv",
    "prep": "prep",     "preposition": "prep",
    "idiom": "idiom",
    "phr": "phr v",     "phrasal verb": "phr v",
    "conj": "conj",     "conjunction": "conj",
}


def _normalize_pos(pos: Optional[str]) -> Optional[str]:
    if not pos:
        return None
    return _POS_MAP.get(pos.strip().lower(), pos.strip().lower())


def parse_vocab_txt(path: Path, encoding: str = "utf-8") -> list[VocabEntry]:
    """
    Parse a vocab txt file, return list of VocabEntry.
    Skips blank lines and comment lines (starting with #).
    """
    entries: list[VocabEntry] = []
    skipped = 0

    for line_num, raw in enumerate(
        Path(path).read_text(encoding=encoding).splitlines(), 1
    ):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        m = _LINE_RE.match(line)
        if not m:
            print(f"  [parser] Line {line_num} skipped (bad format): {line!r}")
            skipped += 1
            continue

        entries.append(VocabEntry(
            word=m.group(1).strip(),
            pos=_normalize_pos(m.group(2)),
            vi_hint=m.group(3).strip(),
            raw_line=raw,
        ))

    print(f"  [parser] {len(entries)} entries parsed, {skipped} skipped — {Path(path).name}")
    return entries


# ── Quick self-test ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    test_lines = [
        "ironies (n): nhiều điều trớ trêu",
        "mediocre (adj): tầm thường",
        "peer into: nhìn vào",
        "come up roses (idiom): mọi việc diễn ra suôn sẻ",
        "the net benefit: lợi ích ròng - chênh lệch giữa tổng chi phí và tổng lợi ích",
        "white-water canoeing: chèo thuyền vượt thác",
        "take into account: tính đến, xét đến",
        "supplante (v): thay thế",
        "",                         # blank → skip
        "# this is a comment",     # comment → skip
        "bad line no colon",       # bad format → skip
    ]

    # Write temp file and parse (tempfile để chạy được trên cả Windows)
    import tempfile
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", encoding="utf-8", delete=False
    ) as f:
        f.write("\n".join(test_lines))
        tmp = Path(f.name)
    entries = parse_vocab_txt(tmp)
    tmp.unlink(missing_ok=True)

    print()
    for e in entries:
        print(f"  word={e.word!r:35s}  pos={str(e.pos):8s}  vi={e.vi_hint[:40]!r}")
    print(f"\n  Total: {len(entries)} entries")
