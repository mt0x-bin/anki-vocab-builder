#!/usr/bin/env python3
"""
Module 8 — Pipeline orchestrator
==================================
Nối tất cả module lại: vocab.txt → .apkg

Flow cho mỗi từ:
  parse → scrape (dict + IPA) → enrich (Gemini) → audio → build card

Kết quả:
  output/<deck_name>.apkg   ← deck Anki hoàn chỉnh
  output/review.txt         ← từ bị lỗi cần kiểm tra lại

Usage:
    # Set API key trước
    export GEMINI_API_KEY="your_key"

    # Chạy full
    python pipeline.py vocab.txt

    # Test nhanh với 5 từ đầu
    python pipeline.py vocab.txt --limit 5

    # Không thêm audio (nhanh hơn)
    python pipeline.py vocab.txt --no-audio

    # Đặt tên deck
    python pipeline.py vocab.txt --deck "IELTS Reading Wk1"
"""

import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from parser   import VocabEntry, parse_vocab_txt
from scraper  import DictResult,   lookup_word
from enricher import EnrichedResult, enrich_word
from audio    import fetch_audio
from card_builder import CardData, build_card
from anki_packer  import pack_apkg


# ── Config & Stats ─────────────────────────────────────────────────────────────

@dataclass
class PipelineConfig:
    txt_file:      Path
    output_apkg:   Path
    deck_name:     str  = "Vocab"
    variant:       str  = "br"          # "br" (British) or "am" (American)
    gemini_api_key: str = ""            # falls back to GEMINI_API_KEY env var
    add_audio:     bool = True
    media_dir:     Path = Path("output/media")
    review_file:   Path = Path("output/review.txt")
    limit:         Optional[int] = None  # only process first N entries (testing)


@dataclass
class PipelineStats:
    total:         int = 0
    enriched:      int = 0
    audio_found:   int = 0
    failed:        int = 0
    failed_words:  list[str] = field(default_factory=list)
    output:        Optional[Path] = None
    elapsed_sec:   float = 0.0

    def summary(self) -> str:
        lines = [
            f"  Tổng từ xử lý  : {self.total}",
            f"  Enrich thành công: {self.enriched}",
            f"  Audio tìm được  : {self.audio_found}",
            f"  Thất bại        : {self.failed}",
        ]
        if self.failed_words:
            lines.append(f"  Từ lỗi          : {', '.join(self.failed_words[:5])}"
                         + (" ..." if len(self.failed_words) > 5 else ""))
        if self.output:
            lines.append(f"  Output          : {self.output}")
        lines.append(f"  Thời gian       : {self.elapsed_sec:.1f}s")
        return "\n".join(lines)


# ── Single-word processor ──────────────────────────────────────────────────────

def _process_word(
    entry: VocabEntry,
    config: PipelineConfig,
    stats: PipelineStats,
    api_key: str = "",
) -> Optional[CardData]:
    """
    Process one word through the full pipeline.
    Returns CardData on success, None on failure.
    Failures are non-fatal — the pipeline continues.
    """
    word = entry.word

    # ── Step 1: Dictionary lookup ──
    dict_result = lookup_word(word)
    ipa = dict_result.ipa  # may be "" if not found — that's ok

    # ── Step 2: Gemini enrichment ──
    enriched = enrich_word(
        word=word,
        dict_result=dict_result,
        pos_hint=entry.pos,
        api_key=api_key,
    )

    if not enriched.ok or not enriched.senses:
        print(f"  ✗ enrich failed — {word!r}")
        stats.failed += 1
        stats.failed_words.append(word)
        return None

    stats.enriched += 1

    # ── Step 3: Audio ──
    audio_filename: Optional[str] = None
    audio_path: Optional[Path]    = None

    if config.add_audio:
        audio_filename = fetch_audio(word, config.media_dir, config.variant)
        if audio_filename:
            audio_path = config.media_dir / audio_filename
            stats.audio_found += 1

    # ── Step 4: Build card ──
    card = build_card(
        word=word,
        ipa=ipa,
        audio_filename=audio_filename,
        enriched=enriched,
    )
    card.audio_file = audio_path  # hand the actual path to the packer

    return card


# ── Main pipeline ──────────────────────────────────────────────────────────────

def run_pipeline(
    config: PipelineConfig,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
) -> PipelineStats:
    """
    Run full pipeline: vocab.txt → .apkg

    Args:
        config      : PipelineConfig
        progress_cb : optional callback(current, total, word) — used by Streamlit UI

    Returns PipelineStats with counts and output path.
    """
    t_start = time.time()
    stats   = PipelineStats()

    # ── Resolve API key một lần ──
    api_key = config.gemini_api_key or os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        print("  ⚠️  Không có Gemini API key — tất cả từ sẽ thất bại ở bước enrich")

    # ── Parse ──
    entries = parse_vocab_txt(config.txt_file)
    if config.limit:
        entries = entries[: config.limit]

    stats.total = len(entries)
    print(f"\n{'═'*52}")
    print(f"  Pipeline: {config.deck_name!r}")
    print(f"  Từ: {stats.total}  |  Audio: {'BR' if config.variant=='br' else 'AM'}  |  Variant: {config.variant}")
    print(f"{'═'*52}\n")

    if stats.total == 0:
        print("  ⚠️  Không có từ nào để xử lý.")
        return stats

    # ── Process each word ──
    cards: list[CardData] = []

    for i, entry in enumerate(entries, 1):
        print(f"[{i:>3}/{stats.total}] {entry.word}")

        if progress_cb:
            progress_cb(i, stats.total, entry.word)

        card = _process_word(entry, config, stats, api_key)
        if card:
            cards.append(card)


    # ── Pack .apkg ──
    print(f"\n{'─'*52}")
    print(f"  Packing {len(cards)} cards → {config.output_apkg}")

    if cards:
        pack_apkg(cards, config.deck_name, config.output_apkg)
        stats.output = config.output_apkg
    else:
        print("  ⚠️  Không có card nào để pack.")

    # ── Write review file ──
    if stats.failed_words:
        config.review_file.parent.mkdir(parents=True, exist_ok=True)
        config.review_file.write_text(
            "\n".join(stats.failed_words), encoding="utf-8"
        )
        print(f"  ⚠️  {stats.failed} từ lỗi → {config.review_file}")

    stats.elapsed_sec = time.time() - t_start

    # ── Summary ──
    print(f"\n{'═'*52}")
    print("  KẾT QUẢ:")
    print(stats.summary())
    print(f"{'═'*52}\n")

    return stats


# ── CLI entry point ────────────────────────────────────────────────────────────

def main():
    import argparse

    p = argparse.ArgumentParser(
        description="Tạo Anki deck từ vocab.txt",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ví dụ:
  export GEMINI_API_KEY="your_key"
  python pipeline.py vocab.txt
  python pipeline.py vocab.txt --deck "IELTS Week 1" --limit 5
  python pipeline.py vocab.txt --no-audio --variant am
        """,
    )
    p.add_argument("txt_file",             help="Đường dẫn đến vocab.txt")
    p.add_argument("--deck",   default="Vocab",
                   help="Tên deck trong Anki (default: Vocab)")
    p.add_argument("--output", default=None,
                   help="Đường dẫn output .apkg (default: output/<deck_name>.apkg)")
    p.add_argument("--variant", choices=["br", "am"], default="br",
                   help="Giọng đọc: br (British) hoặc am (American) — default: br")
    p.add_argument("--no-audio", action="store_true",
                   help="Bỏ qua bước tải audio")
    p.add_argument("--limit",  type=int, default=None,
                   help="Chỉ xử lý N từ đầu (để test)")
    p.add_argument("--api-key", default="",
                   help="Gemini API key (hoặc set env GEMINI_API_KEY)")

    args = p.parse_args()

    txt_file = Path(args.txt_file)
    if not txt_file.exists():
        print(f"[ERROR] File không tồn tại: {txt_file}")
        sys.exit(1)

    deck_name = args.deck
    output    = Path(args.output) if args.output else Path(f"output/{deck_name}.apkg")

    config = PipelineConfig(
        txt_file       = txt_file,
        output_apkg    = output,
        deck_name      = deck_name,
        variant        = args.variant,
        gemini_api_key = args.api_key,
        add_audio      = not args.no_audio,
        limit          = args.limit,
    )

    run_pipeline(config)


if __name__ == "__main__":
    main()
