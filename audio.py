#!/usr/bin/env python3
"""
Module 5 — Audio fetcher
=========================
Lấy audio cho từ: Oxford Learner's Dictionary → TTS fallback.
Lưu file vào media_dir, trả về tên file để dùng trong Anki [sound:...].

Usage:
    python audio.py gauge br
    python audio.py "come up roses" br
"""

import re
import sys
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup


# ── Config ─────────────────────────────────────────────────────────────────────

OXFORD_BASE = "https://www.oxfordlearnersdictionaries.com/definition/english"
TTS_VOICES  = {"br": "en-GB-SoniaNeural", "am": "en-US-JennyNeural"}
GTTS_TLD    = {"br": "co.uk",             "am": "com"}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}


# ── Filename helpers ───────────────────────────────────────────────────────────

def _oxford_fname(word: str, variant: str) -> str:
    slug = re.sub(r"[^a-z0-9]", "_", word.lower())
    return f"oxford_{variant}_{slug}.mp3"


def _tts_fname(word: str, variant: str) -> str:
    slug = re.sub(r"[^a-z0-9]", "_", word.lower())
    return f"tts_{variant}_{slug}.mp3"


def _is_phrase(word: str) -> bool:
    """Phrase/idiom có khoảng trắng → Oxford không có, xuống TTS thẳng."""
    return " " in word.strip()


# ── Oxford audio lookup ────────────────────────────────────────────────────────

def _oxford_audio_url(word: str, variant: str = "br") -> Optional[str]:
    """
    Try to find Oxford audio URL for a single word (không phải phrase).
    Returns URL string or None.
    """
    target_cls = "phons_br"   if variant == "br" else "phons_n_am"
    other_cls  = "phons_n_am" if variant == "br" else "phons_br"

    base = re.sub(r"[^a-z0-9\-]", "-", word.lower().strip()).strip("-")
    slugs = [f"{base}_1", f"{base}_2", f"{base}_3", base]

    for slug in slugs:
        try:
            r = requests.get(
                f"{OXFORD_BASE}/{slug}", headers=HEADERS, timeout=10
            )
            if r.status_code != 200:
                continue

            soup = BeautifulSoup(r.text, "html.parser")

            for cls in (target_cls, other_cls):
                div = soup.find("div", class_=cls)
                if div:
                    tag = div.find("div", attrs={"data-src-mp3": True})
                    if tag:
                        return tag["data-src-mp3"]

        except Exception:
            continue

    return None


def _download(url: str, dest: Path) -> bool:
    """Download URL to dest. Returns True on success."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=15, stream=True)
        if r.status_code != 200:
            return False
        dest.write_bytes(r.content)
        return dest.stat().st_size > 0
    except Exception:
        return False


# ── TTS fallback ───────────────────────────────────────────────────────────────

def _tts(text: str, dest: Path, variant: str = "br") -> bool:
    """Generate TTS audio. edge-tts first (chạy trong thread riêng tránh
    xung đột asyncio với Streamlit), gTTS fallback."""

    # Try edge-tts (Microsoft Neural — higher quality)
    try:
        import asyncio
        import threading
        import edge_tts

        result = {"ok": False}

        def _run_in_thread():
            async def _gen():
                await edge_tts.Communicate(
                    text, TTS_VOICES.get(variant, "en-GB-SoniaNeural")
                ).save(str(dest))
            asyncio.run(_gen())
            result["ok"] = dest.exists() and dest.stat().st_size > 0

        t = threading.Thread(target=_run_in_thread)
        t.start()
        t.join(timeout=30)

        if result["ok"]:
            return True
    except ImportError:
        pass
    except Exception as e:
        print(f"    [audio] edge-tts: {e}")

    # Fallback: gTTS
    try:
        from gtts import gTTS
        gTTS(text, lang="en", tld=GTTS_TLD.get(variant, "co.uk")).save(str(dest))
        if dest.exists() and dest.stat().st_size > 0:
            return True
    except ImportError:
        print("    [audio] TTS unavailable — pip install edge-tts")
    except Exception as e:
        print(f"    [audio] gTTS: {e}")

    return False


# ── Main public function ───────────────────────────────────────────────────────

def fetch_audio(word: str, media_dir: Path, variant: str = "br") -> Optional[str]:
    """
    Fetch audio for word, save into media_dir.
    Returns filename (no path) like "oxford_br_gauge.mp3", or None if all fail.

    Priority:
      1. Oxford Learner's Dictionary (best quality, real human voice)
      2. TTS — edge-tts (Microsoft Neural)
      3. TTS — gTTS (Google)
    """
    media_dir.mkdir(parents=True, exist_ok=True)

    # ── 1. Oxford (chỉ thử với single word, bỏ qua phrase/idiom) ──
    if not _is_phrase(word):
        oxford_file = media_dir / _oxford_fname(word, variant)

        if oxford_file.exists() and oxford_file.stat().st_size > 0:
            print(f"  [audio] cached — {oxford_file.name}")
            return oxford_file.name

        audio_url = _oxford_audio_url(word, variant)
        if audio_url:
            if _download(audio_url, oxford_file):
                print(f"  [audio] Oxford — {oxford_file.name}")
                return oxford_file.name
            oxford_file.unlink(missing_ok=True)
    else:
        print(f"  [audio] phrase detected — skip Oxford, dùng TTS: {word!r}")

    # ── 2. TTS fallback ──
    tts_file = media_dir / _tts_fname(word, variant)

    if tts_file.exists() and tts_file.stat().st_size > 0:
        print(f"  [audio] TTS cached — {tts_file.name}")
        return tts_file.name

    if _tts(word, tts_file, variant):
        print(f"  [audio] TTS — {tts_file.name}")
        return tts_file.name

    tts_file.unlink(missing_ok=True)
    print(f"  [audio] FAILED — {word!r}")
    return None


# ── CLI test ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    word    = sys.argv[1] if len(sys.argv) > 1 else "gauge"
    variant = sys.argv[2] if len(sys.argv) > 2 else "br"
    out_dir = Path("output/media_test")

    print(f"Fetching audio: {word!r} [{variant}]")
    fname = fetch_audio(word, out_dir, variant)

    if fname:
        fpath = out_dir / fname
        print(f"  → {fpath}  ({fpath.stat().st_size:,} bytes)")
    else:
        print("  → Failed")
