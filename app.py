#!/usr/bin/env python3
"""
Streamlit UI — Anki Vocab Builder
Run: streamlit run app.py
"""

import os
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

sys.path.insert(0, str(Path(__file__).parent))

from pipeline     import PipelineConfig, PipelineStats, run_pipeline
from card_builder import CARD_CSS


# ── Page config ────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Anki Vocab Builder",
    page_icon="🎴",
    layout="centered",
)

st.markdown("""
<style>
/* Tighter metrics */
[data-testid="metric-container"] { padding: 8px 12px; }
/* Primary button */
div.stButton > button[kind="primary"] { font-weight: 600; }
/* Download button same width */
div.stDownloadButton > button { width: 100%; }
</style>
""", unsafe_allow_html=True)


# ── Session state init ─────────────────────────────────────────────────────────

DEFAULTS = {
    "apkg_bytes":    None,
    "stats":         None,
    "deck_name_out": None,
    "preview_cards": None,
    "review_txt":    None,
    "running":       False,
}
for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v


# ── Helpers ────────────────────────────────────────────────────────────────────

def _count_lines(text: str) -> int:
    return sum(1 for l in text.splitlines() if l.strip() and not l.startswith("#"))


def _read_preview(apkg_bytes: bytes, n: int = 2) -> list[dict]:
    try:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            apkg = p / "d.apkg"
            apkg.write_bytes(apkg_bytes)
            with zipfile.ZipFile(apkg) as zf:
                zf.extract("collection.anki2", p)
            conn = sqlite3.connect(str(p / "collection.anki2"))
            rows = conn.execute("SELECT flds FROM notes LIMIT ?", (n,)).fetchall()
            conn.close()
        result = []
        for (flds,) in rows:
            f = flds.split("\x1f")
            result.append({
                "word":        f[0] if len(f) > 0 else "",
                "ipa":         f[1] if len(f) > 1 else "",
                "senses_html": f[3] if len(f) > 3 else "",
            })
        return result
    except Exception:
        return []


def _preview_html(cards: list[dict], dark: bool = False) -> str:
    night = "nightMode" if dark else ""
    blocks = []
    for c in cards:
        blocks.append(f"""
<div class="{night}">
<div class="card" style="margin-bottom:20px; border:1px solid var(--c-border); border-radius:12px;">
  <div class="front">
    <div class="word">{c["word"]}</div>
    <div class="ipa">{c["ipa"] or "—"}</div>
  </div>
  <hr class="divider">
  <div class="senses">{c["senses_html"]}</div>
</div>
</div>""")
    return (
        f"<style>{CARD_CSS}\n"
        f"body{{margin:0;padding:12px;background:{'#1a1a1a' if dark else '#f5f4f0'}}}</style>"
        + "".join(blocks)
    )


# ── Header ─────────────────────────────────────────────────────────────────────

st.title("🎴 Anki Vocab Builder")
st.caption("Tạo Anki deck từ vocab.txt — IPA · audio · nghĩa đầy đủ · dark mode")
st.divider()


# ── Input section ──────────────────────────────────────────────────────────────

col_file, col_settings = st.columns([1, 1], gap="large")

with col_file:
    st.subheader("📁 Vocab file")
    uploaded = st.file_uploader("Upload vocab.txt", type=["txt"],
                                label_visibility="collapsed")
    if uploaded:
        txt_content = uploaded.getvalue().decode("utf-8")
        n_words = _count_lines(txt_content)
        st.success(f"✅ {n_words} từ")
        with st.expander("Xem nội dung"):
            st.text(txt_content[:600] + ("…" if len(txt_content) > 600 else ""))

with col_settings:
    st.subheader("⚙️ Cài đặt")

    deck_name = st.text_input(
        "Tên deck trong Anki",
        value="Vocab",
        placeholder="IELTS Reading Wk1",
    )

    _env_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if _env_key:
        api_key = _env_key
        st.success("✅ Gemini API key đã được cấu hình")
    else:
        api_key = st.text_input(
            "Gemini API key",
            type="password",
            placeholder="AIza… (hoặc set GEMINI_API_KEY trong Secrets)",
            help="Miễn phí tại aistudio.google.com → Get API key",
        )

    col_v, col_a = st.columns(2)
    with col_v:
        variant_label = st.radio(
            "Giọng audio",
            ["🇬🇧 BR", "🇺🇸 AM"],
            horizontal=False,
        )
        variant = "br" if "BR" in variant_label else "am"
    with col_a:
        st.write("")
        add_audio = st.checkbox("Thêm audio", value=True)
        limit_on  = st.checkbox("Giới hạn số từ", value=False,
                                help="Dùng để test trước khi chạy full")

    limit = 0
    if limit_on:
        limit = st.number_input("Chỉ xử lý N từ đầu", min_value=1,
                                max_value=500, value=5, step=1)

st.divider()


# ── Run button ─────────────────────────────────────────────────────────────────

no_file = uploaded is None
no_key  = not api_key.strip()

run_disabled = no_file
if no_file:
    st.info("⬆️ Upload vocab.txt để bắt đầu.")
elif no_key:
    st.warning("⚠️ Nhập Gemini API key để pipeline có thể generate nghĩa từ.")

clicked = st.button(
    "🚀  Tạo deck",
    disabled=run_disabled,
    use_container_width=True,
    type="primary",
)

if clicked:
    if no_key:
        st.error("Cần Gemini API key.")
        st.stop()

    # ── Setup temp paths ──
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", encoding="utf-8", delete=False
    ) as f:
        f.write(uploaded.getvalue().decode("utf-8"))
        txt_path = Path(f.name)

    tmp_dir     = Path(tempfile.mkdtemp(prefix="ankivocab_"))
    apkg_path   = tmp_dir / f"{deck_name}.apkg"
    media_dir   = tmp_dir / "media"
    review_file = tmp_dir / "review.txt"

    config = PipelineConfig(
        txt_file        = txt_path,
        output_apkg     = apkg_path,
        deck_name       = deck_name,
        variant         = variant,
        gemini_api_key  = api_key.strip(),
        add_audio       = add_audio,
        media_dir       = media_dir,
        review_file     = review_file,
        limit           = int(limit) if limit_on and limit > 0 else None,
    )

    # ── Progress UI ──
    st.subheader("⏳ Đang xử lý…")
    progress_bar = st.progress(0.0)
    status_box   = st.empty()

    def progress_cb(current: int, total: int, word: str):
        progress_bar.progress(current / total)
        status_box.markdown(
            f"**`[{current}/{total}]`** &nbsp; đang xử lý: **{word}**"
        )

    # ── Run ──
    try:
        stats = run_pipeline(config, progress_cb=progress_cb)
        progress_bar.progress(1.0)
        status_box.markdown("✅ **Hoàn thành!**")

        if apkg_path.exists():
            st.session_state["apkg_bytes"]    = apkg_path.read_bytes()
            st.session_state["stats"]         = stats
            st.session_state["deck_name_out"] = deck_name
            st.session_state["preview_cards"] = _read_preview(
                st.session_state["apkg_bytes"]
            )

        if review_file.exists():
            st.session_state["review_txt"] = review_file.read_text(encoding="utf-8")
        else:
            st.session_state["review_txt"] = None

    except Exception as e:
        st.error(f"❌ Lỗi: {e}")
    finally:
        txt_path.unlink(missing_ok=True)
        shutil.rmtree(tmp_dir, ignore_errors=True)

    st.rerun()


# ── Results ────────────────────────────────────────────────────────────────────

if st.session_state["apkg_bytes"]:
    stats:     PipelineStats = st.session_state["stats"]
    deck:      str           = st.session_state["deck_name_out"]
    apkg:      bytes         = st.session_state["apkg_bytes"]
    preview:   list          = st.session_state["preview_cards"] or []
    review_txt: str | None   = st.session_state["review_txt"]

    st.divider()
    st.subheader("✅ Kết quả")

    # Stats
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Tổng từ",    stats.total)
    c2.metric("Thành công", stats.enriched)
    c3.metric("Có audio",   stats.audio_found)
    c4.metric("Lỗi",        stats.failed,
              delta=f"-{stats.failed}" if stats.failed else None,
              delta_color="inverse")
    st.caption(f"⏱ {stats.elapsed_sec:.1f}s")

    # Download
    st.write("")
    dl_col, rv_col, reset_col = st.columns([3, 2, 2])

    with dl_col:
        st.download_button(
            label=f"⬇️  Tải {deck}.apkg",
            data=apkg,
            file_name=f"{deck}.apkg",
            mime="application/octet-stream",
            type="primary",
        )

    with rv_col:
        if review_txt:
            st.download_button(
                label=f"⚠️ review.txt ({stats.failed} từ lỗi)",
                data=review_txt,
                file_name="review.txt",
                mime="text/plain",
            )
        else:
            st.success("Không có từ lỗi 🎉")

    with reset_col:
        if st.button("🔄  Tạo deck mới", use_container_width=True):
            for k in DEFAULTS:
                st.session_state[k] = DEFAULTS[k]
            st.rerun()

    # Failed words detail
    if stats.failed_words:
        with st.expander(f"Xem {stats.failed} từ bị lỗi"):
            st.code("\n".join(stats.failed_words))

    # Card preview
    if preview:
        st.divider()
        st.subheader("👁 Preview")
        st.caption("2 card đầu tiên — đúng như hiển thị trong Anki")

        tab_light, tab_dark = st.tabs(["☀️ Light mode", "🌙 Dark mode"])

        with tab_light:
            h = _preview_html(preview, dark=False)
            components.html(h, height=min(300 + len(h) // 12, 900), scrolling=True)

        with tab_dark:
            h = _preview_html(preview, dark=True)
            components.html(h, height=min(300 + len(h) // 12, 900), scrolling=True)

    # Import guide
    st.divider()
    with st.expander("📖 Hướng dẫn import vào Anki"):
        st.markdown(f"""
1. Mở **Anki** → menu **Tập tin** → **Nhập**
2. Chọn file **`{deck}.apkg`** vừa tải về
3. Anki tự nhận note type **Vocab-EN-VI** và deck **{deck}**
4. Lần sau chạy thêm từ mới rồi import lại → Anki **chỉ thêm từ mới**, không nhân đôi từ cũ

> **Lưu ý:** Để xoá deck cũ trước khi import lần đầu:  
> Anki → Bộ thẻ → Chuột phải deck → Xoá
        """)
