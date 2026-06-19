#!/usr/bin/env python3
"""
Module 2 — Free Dictionary API scraper + cache
================================================
Lấy definitions, POS, examples từ api.dictionaryapi.dev (free, no key).
Kết quả cache local để không gọi lại.

Usage:
    python scraper.py gauge
    python scraper.py "impair" "mediocre"
"""

import json
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

import requests
from cache_store import cache_get, cache_set


# ── Config ─────────────────────────────────────────────────────────────────────

API_BASE      = "https://api.dictionaryapi.dev/api/v2/entries/en"
REQUEST_DELAY = 0.5   # seconds between requests (API is generous)
MAX_SENSES    = 4     # max total senses to keep per word
MAX_EXAMPLES  = 2     # max examples per sense

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

POS_NORMALIZE = {
    "verb": "v",            "noun": "n",
    "adjective": "adj",     "adverb": "adv",
    "preposition": "prep",  "idiom": "idiom",
    "phrasal verb": "phr v","conjunction": "conj",
    "pronoun": "pron",      "exclamation": "exclam",
    "interjection": "interj",
}


# ── Data classes ───────────────────────────────────────────────────────────────

@dataclass
class DictSense:
    pos: str                                           # "v", "n", "adj" …
    definition: str                                    # EN definition
    examples: list[str] = field(default_factory=list)  # ≤ MAX_EXAMPLES


@dataclass
class DictResult:
    word: str
    senses: list[DictSense]
    ipa: str                  # IPA from API (bonus — already fetched)
    found: bool




def _serialize(result: DictResult) -> dict:
    return asdict(result)


def _deserialize(data: dict) -> DictResult:
    return DictResult(
        word=data["word"],
        found=data["found"],
        ipa=data.get("ipa", ""),
        senses=[DictSense(**s) for s in data["senses"]],
    )


# ── Parser ─────────────────────────────────────────────────────────────────────

def _extract_ipa(entry: dict) -> str:
    """Extract best IPA from phonetics list."""
    for ph in entry.get("phonetics", []):
        text = ph.get("text", "").strip()
        if text:
            return text if text.startswith("/") else f"/{text}/"
    return ""


def _parse_response(data: list, word: str) -> DictResult:
    """Parse Free Dictionary API JSON response into DictResult."""
    if not data or not isinstance(data, list):
        return DictResult(word=word, senses=[], ipa="", found=False)

    entry = data[0]
    ipa = _extract_ipa(entry)
    senses: list[DictSense] = []

    for meaning in entry.get("meanings", []):
        if len(senses) >= MAX_SENSES:
            break

        pos_raw = meaning.get("partOfSpeech", "unknown").lower()
        pos = POS_NORMALIZE.get(pos_raw, pos_raw)

        for defn in meaning.get("definitions", []):
            if len(senses) >= MAX_SENSES:
                break

            definition = defn.get("definition", "").strip()
            if not definition:
                continue

            # Collect examples: from this def + synonymContext
            examples: list[str] = []
            if defn.get("example") and len(examples) < MAX_EXAMPLES:
                examples.append(defn["example"])

            senses.append(DictSense(pos=pos, definition=definition, examples=examples))

    return DictResult(word=word, senses=senses, ipa=ipa, found=bool(senses))


# ── Main public function ───────────────────────────────────────────────────────

def lookup_word(word: str, use_cache: bool = True) -> DictResult:
    """
    Look up a word via Free Dictionary API.
    Returns DictResult with found=False if not found.
    Multi-word phrases / idioms: tries first word as fallback.
    Results cached in cache/freedict.json.
    """
    cache_key = word.lower().strip()

    # Cache read
    if use_cache:
        cached = cache_get(f"freedict:{cache_key}")
        if cached:
            print(f"  [dict] cache hit — {word!r}")
            return _deserialize(cached)

    # Build candidate words to try
    candidates = [word]
    parts = word.split()
    if len(parts) > 1:
        candidates.append(parts[0])  # phrasal verb base: "peer into" → "peer"

    result = DictResult(word=word, senses=[], ipa="", found=False)

    for candidate in candidates:
        slug = candidate.lower().replace(" ", "%20")
        url  = f"{API_BASE}/{slug}"
        try:
            time.sleep(REQUEST_DELAY)
            r = requests.get(url, headers=HEADERS, timeout=8)

            if r.status_code == 404:
                continue
            if r.status_code != 200:
                print(f"  [dict] HTTP {r.status_code} — {word!r}")
                continue

            result = _parse_response(r.json(), word)
            if result.found:
                n = len(result.senses)
                print(f"  [dict] {n} sense(s) found — {word!r}")
                break

        except requests.RequestException as e:
            print(f"  [dict] request error — {word!r}: {e}")
            continue

    if not result.found:
        print(f"  [dict] not found — {word!r}")

    # Cache write
    if use_cache:
        cache_set(f"freedict:{cache_key}", _serialize(result))

    return result


# ── Quick self-test (mock) ─────────────────────────────────────────────────────

def _mock_test():
    """Test parse logic with a hardcoded API response (no network needed)."""
    mock_response = [
        {
            "word": "gauge",
            "phonetics": [{"text": "/ɡeɪdʒ/"}],
            "meanings": [
                {
                    "partOfSpeech": "verb",
                    "definitions": [
                        {
                            "definition": "To measure or determine the amount of something.",
                            "example": "The pilot gauged the distance to the runway.",
                        },
                        {
                            "definition": "To make a judgement about a situation, especially based on indirect information.",
                            "example": "It was difficult to gauge his mood.",
                        },
                    ],
                },
                {
                    "partOfSpeech": "noun",
                    "definitions": [
                        {
                            "definition": "An instrument used for measuring.",
                            "example": "A fuel gauge showed the tank was half full.",
                        },
                        {
                            "definition": "A means of estimating or judging something.",
                            "example": "Sales figures are a gauge of public taste.",
                        },
                    ],
                },
            ],
        }
    ]

    result = _parse_response(mock_response, "gauge")
    print(f"\n  Mock test — word: {result.word!r}  IPA: {result.ipa}  found: {result.found}")
    for i, s in enumerate(result.senses, 1):
        print(f"\n  [{s.pos}] sense {i}: {s.definition}")
        for eg in s.examples:
            print(f"    ex: {eg}")


if __name__ == "__main__":
    import sys

    if "--mock" in sys.argv or len(sys.argv) == 1:
        print("Running mock test (no network)...")
        _mock_test()
    else:
        words = [a for a in sys.argv[1:] if not a.startswith("--")]
        for word in words:
            print(f"\n{'─' * 50}")
            result = lookup_word(word)
            if not result.found:
                print("  → No result")
                continue
            for i, s in enumerate(result.senses, 1):
                print(f"\n  [{s.pos}] sense {i}: {s.definition}")
                for eg in s.examples:
                    print(f"    ex: {eg}")
