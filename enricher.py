#!/usr/bin/env python3
"""
Module 4 — Gemini enricher
===========================
Nhận word + dictionary senses → generate ra:
  - vi_meaning   : nghĩa tiếng Việt tự nhiên, chính xác
  - usage_note   : khi nào / ngữ cảnh nào dùng từ này (tiếng Việt)
  - collocations : 3–4 cụm từ hay đi kèm nhất
  - example      : câu ví dụ hay nhất (từ dict hoặc AI generate)

Dùng Gemini 2.5 Flash Lite (free tier).
Kết quả cache local để không tốn quota lần sau.

Usage:
    python enricher.py                    # mock test (no API key needed)
    python enricher.py --word gauge       # live test (cần GEMINI_API_KEY)
"""

import json
import os
import re
from cache_store import cache_get, cache_set
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from scraper import DictResult, DictSense


# ── Config ─────────────────────────────────────────────────────────────────────

GEMINI_MODEL = "gemini-2.5-flash-lite"   # đổi ở đây nếu cần thử model khác

MAX_RETRIES  = 3    # số lần retry khi gặp 503
RETRY_DELAY  = 5    # giây chờ giữa mỗi lần retry (×attempt)


# ── Data classes ───────────────────────────────────────────────────────────────

@dataclass
class EnrichedSense:
    pos: str                                           # "v", "n", "adj" …
    vi_meaning: str                                    # nghĩa tiếng Việt
    en_def: str                                        # định nghĩa EN (từ dict)
    usage_note: str                                    # khi nào dùng (VI)
    collocations: list[str] = field(default_factory=list)  # 3–4 cụm
    example: str = ""                                  # câu ví dụ tốt nhất


@dataclass
class EnrichedResult:
    word: str
    senses: list[EnrichedSense]
    ok: bool          # False nếu Gemini call thất bại





def _serialize(result: EnrichedResult) -> dict:
    return asdict(result)


def _deserialize(data: dict) -> EnrichedResult:
    return EnrichedResult(
        word=data["word"],
        ok=data["ok"],
        senses=[EnrichedSense(**s) for s in data["senses"]],
    )


# ── Prompt builder ─────────────────────────────────────────────────────────────

def _build_prompt(word: str, pos_hint: Optional[str], dict_result: DictResult) -> str:
    lines = [
        "You are a Vietnamese-English vocabulary expert creating Anki flashcard content"
        " for Vietnamese learners of English.",
        "",
        f'Word: "{word}"',
    ]

    if pos_hint:
        lines.append(f"Primary POS hint: {pos_hint}")

    if dict_result.found and dict_result.senses:
        lines += ["", "Dictionary data (use as grounding):"]
        for i, s in enumerate(dict_result.senses, 1):
            lines.append(f"  Sense {i} [{s.pos}]: {s.definition}")
            for eg in s.examples:
                lines.append(f"    Example: {eg}")
    else:
        lines += [
            "",
            "No dictionary entry found (may be an idiom, phrase, or specialized term).",
            "Use your own knowledge to generate meanings.",
        ]

    lines += [
        "",
        "For EACH sense above, return a JSON object with these fields:",
        "  pos         : part of speech label from the sense (e.g. 'v', 'n', 'adj')",
        "  vi_meaning  : concise Vietnamese translation — natural, not literal",
        "  en_def      : copy the English definition from the sense above (verbatim)",
        "  usage_note  : 1–2 sentences in Vietnamese explaining WHEN to use this specific",
        "                word/sense vs similar words (context, register, collocates)",
        "  collocations: array of 3–4 most natural English collocations for this sense",
        "  example     : best example sentence (from dict if available, else generate one)",
        "",
        "Return ONLY valid JSON — no markdown, no explanation:",
        '{',
        '  "senses": [',
        '    {',
        '      "pos": "v",',
        '      "vi_meaning": "...",',
        '      "en_def": "...",',
        '      "usage_note": "...",',
        '      "collocations": ["...", "..."],',
        '      "example": "..."',
        '    }',
        '  ]',
        '}',
    ]

    return "\n".join(lines)


# ── Response parser ────────────────────────────────────────────────────────────

def _parse_response(raw: str, word: str, dict_result: DictResult) -> list[EnrichedSense]:
    text = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    text = re.sub(r"\s*```$", "", text)

    data = json.loads(text)
    senses_raw = data.get("senses", [])

    dict_defs = {s.pos: s.definition for s in dict_result.senses}

    result: list[EnrichedSense] = []
    for raw_sense in senses_raw:
        en_def = (
            raw_sense.get("en_def", "").strip()
            or dict_defs.get(raw_sense.get("pos", ""), "")
        )
        result.append(EnrichedSense(
            pos=raw_sense.get("pos", "").strip(),
            vi_meaning=raw_sense.get("vi_meaning", "").strip(),
            en_def=en_def,
            usage_note=raw_sense.get("usage_note", "").strip(),
            collocations=raw_sense.get("collocations", []),
            example=raw_sense.get("example", "").strip(),
        ))

    return result


# ── Main public function ───────────────────────────────────────────────────────

def enrich_word(
    word: str,
    dict_result: DictResult,
    pos_hint: Optional[str] = None,
    api_key: Optional[str] = None,
    use_cache: bool = True,
) -> EnrichedResult:
    """
    Call Gemini để enrich một từ với nghĩa VI + usage notes.
    Tự động retry khi gặp lỗi 503 (server overload).
    Chỉ cache khi thành công — không lưu kết quả lỗi.
    """
    cache_key = word.lower().strip()

    # Cache read
    if use_cache:
        cached = cache_get(f"gemini:{cache_key}")
        if cached:
            print(f"  [gemini] cache hit — {word!r}")
            return _deserialize(cached)

    # Resolve API key
    key = api_key or os.environ.get("GEMINI_API_KEY", "")
    if not key:
        print(f"  [gemini] ERROR: no API key — set GEMINI_API_KEY env var")
        return EnrichedResult(word=word, senses=[], ok=False)

    try:
        from google import genai
        from google.genai import types as genai_types
    except ImportError:
        print("  [gemini] ERROR: pip install google-genai")
        return EnrichedResult(word=word, senses=[], ok=False)

    client = genai.Client(api_key=key)
    prompt = _build_prompt(word, pos_hint, dict_result)
    result = EnrichedResult(word=word, senses=[], ok=False)

    # ── Retry loop ──
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    response_mime_type="application/json",
                ),
            )
            senses = _parse_response(response.text, word, dict_result)
            result = EnrichedResult(word=word, senses=senses, ok=True)
            print(f"  [gemini] {len(senses)} sense(s) enriched — {word!r}")
            break  # thành công

        except Exception as e:
            last_err = e
            err_str  = str(e)

            if "503" in err_str or "UNAVAILABLE" in err_str:
                wait = attempt * RETRY_DELAY
                print(f"  [gemini] 503 server overload — retry {attempt}/{MAX_RETRIES} sau {wait}s...")
                time.sleep(wait)
                continue  # thử lại

            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                print(f"  [gemini] 429 quota exceeded — {word!r}")
                break  # quota hết, retry vô ích

            # Lỗi khác
            print(f"  [gemini] ERROR for {word!r}: {err_str[:120]}")
            break

    if not result.ok and last_err:
        print(f"  [gemini] failed after {attempt} attempt(s) — {word!r}")

    # Cache write — chỉ lưu khi thành công
    if use_cache and result.ok:
        cache_set(f"gemini:{cache_key}", _serialize(result))

    return result


# ── Self-tests ─────────────────────────────────────────────────────────────────

def _mock_test():
    """Test parse logic với hardcoded Gemini response — không cần API key."""
    mock_response = json.dumps({
        "senses": [
            {
                "pos": "v",
                "vi_meaning": "đánh giá, ước lượng",
                "en_def": "To make a judgement about a situation.",
                "usage_note": (
                    "Dùng khi đánh giá mức độ, cảm xúc hoặc tình huống "
                    "dựa trên thông tin gián tiếp. Không dùng cho đo lường vật lý chính xác."
                ),
                "collocations": ["gauge reaction", "gauge the situation", "gauge support"],
                "example": "It was difficult to gauge how serious the problem was.",
            },
            {
                "pos": "n",
                "vi_meaning": "thước đo, dụng cụ đo",
                "en_def": "An instrument used for measuring.",
                "usage_note": "Dùng để chỉ thiết bị đo vật lý cụ thể như áp suất, nhiên liệu.",
                "collocations": ["fuel gauge", "pressure gauge", "rain gauge"],
                "example": "A fuel gauge showed the tank was half full.",
            },
        ]
    })

    from scraper import DictResult, DictSense
    dict_result = DictResult(word="gauge", found=True, ipa="/ɡeɪdʒ/", senses=[
        DictSense("v", "To make a judgement about a situation.", []),
        DictSense("n", "An instrument used for measuring.", []),
    ])

    senses = _parse_response(mock_response, "gauge", dict_result)
    print("\n  Mock test — gauge")
    for s in senses:
        print(f"\n  [{s.pos}]  {s.vi_meaning}")
        print(f"  EN : {s.en_def}")
        print(f"  📌  {s.usage_note}")
        print(f"  💬  {', '.join(s.collocations)}")
        print(f"  ex : {s.example}")


def _live_test(word: str):
    """Live test — cần GEMINI_API_KEY."""
    from scraper import lookup_word
    print(f"\n  Live test — {word!r}")
    dict_result = lookup_word(word, use_cache=False)
    enriched    = enrich_word(word, dict_result, use_cache=False)
    if not enriched.ok:
        print("  → Enrichment failed")
        return
    for s in enriched.senses:
        print(f"\n  [{s.pos}]  {s.vi_meaning}")
        print(f"  EN : {s.en_def}")
        print(f"  📌  {s.usage_note}")
        print(f"  💬  {', '.join(s.collocations)}")
        print(f"  ex : {s.example}")


if __name__ == "__main__":
    import sys
    if "--word" in sys.argv:
        idx  = sys.argv.index("--word")
        _live_test(sys.argv[idx + 1])
    else:
        _mock_test()
