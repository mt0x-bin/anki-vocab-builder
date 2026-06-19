#!/usr/bin/env python3
"""
Module 6 — Card HTML builder
==============================
Tổng hợp word + IPA + audio + EnrichedResult → CardData.

Usage:
    python card_builder.py    # mock test + tạo card_preview.html
"""

import html as html_mod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from enricher import EnrichedResult, EnrichedSense


# ── Data class ─────────────────────────────────────────────────────────────────

@dataclass
class CardData:
    word: str
    ipa: str
    audio_tag: str
    senses_html: str
    audio_file: Optional[Path]


# ── Anki card templates ────────────────────────────────────────────────────────

FRONT_TMPL = """\
<div class="front">
  <div class="word">{{Word}}</div>
  <div class="ipa">{{IPA}}</div>
  <div class="audio">{{Audio}}</div>
</div>"""

BACK_TMPL = """\
{{FrontSide}}
<hr class="divider">
<div class="senses">{{Senses}}</div>"""

# ── CSS: dùng variables để hỗ trợ cả light và dark mode ──────────────────────
# Anki tự thêm class .nightMode vào thẻ khi bật dark theme
CARD_CSS = (
    "/* == Light mode (default) == */\n"
    ".card {\n"
    "  --c-text:     #2c2c2a;\n"
    "  --c-sub:      #5f5e5a;\n"
    "  --c-hint:     #888780;\n"
    "  --c-bg:       #ffffff;\n"
    "  --c-border:   #e8e6df;\n"
    "  --c-pos-bg:   #e6f1fb;\n"
    "  --c-pos-text: #0c447c;\n"
    "  --c-use-bg:   #e1f5ee;\n"
    "  --c-use-text: #0f6e56;\n"
    "  --c-pill:     #d3d1c7;\n"
    "}\n"
    "\n"
    "/* == Dark mode — Anki thêm .nightMode khi bật dark theme == */\n"
    ".nightMode .card,\n"
    ".nightMode.card {\n"
    "  --c-text:     #e8e6e0;\n"
    "  --c-sub:      #a8a69e;\n"
    "  --c-hint:     #6e6c66;\n"
    "  --c-bg:       #1e1e1e;\n"
    "  --c-border:   #3a3a38;\n"
    "  --c-pos-bg:   #1a3550;\n"
    "  --c-pos-text: #7ab8f5;\n"
    "  --c-use-bg:   #0e2a1e;\n"
    "  --c-use-text: #5ecfa0;\n"
    "  --c-pill:     #454540;\n"
    "}\n"
    "\n"
    ".card {\n"
    "  font-family: Arial, sans-serif;\n"
    "  font-size: 16px;\n"
    "  color: var(--c-text);\n"
    "  background-color: var(--c-bg);\n"
    "  max-width: 580px;\n"
    "  margin: 0 auto;\n"
    "  padding: 16px;\n"
    "}\n"
    "\n"
    "/* == Mặt trước == */\n"
    ".front { text-align: center; padding: 20px 0; }\n"
    ".word  { font-size: 28px; font-weight: 500; margin: 0 0 6px; color: var(--c-text); }\n"
    ".ipa   { font-size: 15px; color: var(--c-sub); font-family: Georgia, serif; margin: 0 0 12px; }\n"
    ".audio { font-size: 13px; }\n"
    "\n"
    "hr.divider { border: none; border-top: 1px solid var(--c-border); margin: 12px 0; }\n"
    "\n"
    "/* == Mặt sau == */\n"
    ".senses { text-align: left; }\n"
    ".sense { padding: 12px 0; border-bottom: 1px solid var(--c-border); }\n"
    ".sense:last-child { border-bottom: none; }\n"
    ".sense-header { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }\n"
    "\n"
    ".pos-badge {\n"
    "  font-size: 11px; padding: 2px 8px; border-radius: 99px;\n"
    "  background: var(--c-pos-bg); color: var(--c-pos-text);\n"
    "  font-weight: bold; display: inline-block;\n"
    "}\n"
    "\n"
    ".vi-meaning { font-size: 16px; font-weight: 500; color: var(--c-text); margin: 4px 0; }\n"
    ".en-def { font-size: 13px; color: var(--c-sub); margin: 4px 0; line-height: 1.5; font-style: italic; }\n"
    ".usage-row { display: flex; align-items: flex-start; gap: 6px; margin: 6px 0; }\n"
    "\n"
    ".usage-label {\n"
    "  font-size: 11px; color: var(--c-use-text); background: var(--c-use-bg);\n"
    "  border-radius: 99px; padding: 2px 8px; white-space: nowrap;\n"
    "  font-weight: bold; flex-shrink: 0; margin-top: 2px;\n"
    "}\n"
    "\n"
    ".usage-text { font-size: 12px; color: var(--c-sub); line-height: 1.5; }\n"
    ".example { font-size: 12px; color: var(--c-hint); margin: 6px 0 0; font-style: italic; }\n"
    ".collocations { margin-top: 6px; }\n"
    "\n"
    ".colloc {\n"
    "  font-size: 11px; padding: 2px 8px;\n"
    "  border: 1px solid var(--c-pill); border-radius: 99px;\n"
    "  color: var(--c-sub); display: inline-block; margin: 2px;\n"
    "}"
)


# ── HTML helpers ───────────────────────────────────────────────────────────────

def _esc(text: str) -> str:
    return html_mod.escape(text)


def _sense_html(sense: EnrichedSense) -> str:
    colloc_pills = "".join(
        f'<span class="colloc">{_esc(c)}</span>'
        for c in sense.collocations
    )
    example_block = (
        f'<div class="example">"{_esc(sense.example)}"</div>'
        if sense.example else ""
    )
    return (
        f'<div class="sense">'
        f'<div class="sense-header"><span class="pos-badge">{_esc(sense.pos)}</span></div>'
        f'<div class="vi-meaning">{_esc(sense.vi_meaning)}</div>'
        f'<div class="en-def">{_esc(sense.en_def)}</div>'
        f'<div class="usage-row">'
        f'<span class="usage-label">dùng khi</span>'
        f'<span class="usage-text">{_esc(sense.usage_note)}</span>'
        f'</div>'
        f'<div class="collocations">{colloc_pills}</div>'
        f'{example_block}'
        f'</div>'
    )


def _build_senses_html(enriched: EnrichedResult) -> str:
    if not enriched.senses:
        return '<div class="sense"><div class="vi-meaning">—</div></div>'
    return "\n".join(_sense_html(s) for s in enriched.senses)


# ── Main public function ───────────────────────────────────────────────────────

def build_card(
    word: str,
    ipa: str,
    audio_filename: Optional[str],
    enriched: EnrichedResult,
) -> CardData:
    audio_tag = f"[sound:{audio_filename}]" if audio_filename else ""
    return CardData(
        word=word,
        ipa=ipa,
        audio_tag=audio_tag,
        senses_html=_build_senses_html(enriched),
        audio_file=None,
    )


# ── Mock test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from enricher import EnrichedResult, EnrichedSense

    mock_enriched = EnrichedResult(
        word="gauge", ok=True,
        senses=[
            EnrichedSense(
                pos="v", vi_meaning="đánh giá, ước lượng",
                en_def="To make a judgement about a situation.",
                usage_note="Dùng khi đánh giá mức độ dựa trên thông tin gián tiếp.",
                collocations=["gauge reaction", "gauge the situation", "gauge support"],
                example="It was difficult to gauge how serious the problem was.",
            ),
            EnrichedSense(
                pos="n", vi_meaning="thước đo, dụng cụ đo",
                en_def="An instrument used for measuring.",
                usage_note="Dùng để chỉ thiết bị đo vật lý như áp suất, nhiên liệu.",
                collocations=["fuel gauge", "pressure gauge", "rain gauge"],
                example="A fuel gauge showed the tank was half full.",
            ),
        ],
    )

    card = build_card("gauge", "/ɡeɪdʒ/", "oxford_br_gauge.mp3", mock_enriched)
    print(f"word={card.word}  ipa={card.ipa}  audio={card.audio_tag}")
    print("senses_html length:", len(card.senses_html))
    print("nightMode in CSS:", "nightMode" in CARD_CSS)
    print("var(--c-text) in CSS:", "var(--c-text)" in CARD_CSS)

    # Preview HTML — light + dark side by side
    preview = Path("output/card_preview.html")
    preview.parent.mkdir(parents=True, exist_ok=True)
    preview.write_text(
        "<!DOCTYPE html><html><head><meta charset='utf-8'><style>\n"
        "body{display:flex;gap:20px;padding:24px;background:#f0efeb;font-family:sans-serif}\n"
        "h3{font-size:11px;color:#999;margin:0 0 8px;text-transform:uppercase;letter-spacing:1px}\n"
        ".wrap{flex:1}\n"
        ".dark-bg{background:#1a1a1a;padding:16px;border-radius:12px}\n"
        + CARD_CSS + "\n</style></head><body>\n"
        "<div class='wrap'><h3>Light mode</h3>"
        "<div class='card'>"
        f"<div class='front'><div class='word'>{card.word}</div>"
        f"<div class='ipa'>{card.ipa}</div></div>"
        "<hr class='divider'>"
        f"<div class='senses'>{card.senses_html}</div></div></div>\n"
        "<div class='wrap'><h3>Dark mode</h3>"
        "<div class='dark-bg nightMode'><div class='card'>"
        f"<div class='front'><div class='word'>{card.word}</div>"
        f"<div class='ipa'>{card.ipa}</div></div>"
        "<hr class='divider'>"
        f"<div class='senses'>{card.senses_html}</div></div></div></div>\n"
        "</body></html>",
        encoding="utf-8",
    )
    print(f"\nPreview → {preview}  (mở trong browser để xem light + dark)")
