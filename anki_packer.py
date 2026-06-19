#!/usr/bin/env python3
"""
Module 7 — Anki packer
========================
Tạo .apkg từ list[CardData].

Cấu trúc .apkg (là một ZIP):
  collection.anki2  ← SQLite database (Anki 2 schema)
  media             ← JSON map  {"0": "oxford_br_gauge.mp3", ...}
  0, 1, 2, …        ← media files đặt tên bằng index số

Usage:
    python anki_packer.py    # mock test → output/test_deck.apkg
"""

import hashlib
import json
import shutil
import sqlite3
import time
import zipfile
from pathlib import Path
from typing import Optional

from card_builder import CardData, FRONT_TMPL, BACK_TMPL, CARD_CSS


# ── Anki schema SQL ────────────────────────────────────────────────────────────

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS col (
    id      integer primary key,
    crt     integer not null,
    mod     integer not null,
    scm     integer not null,
    ver     integer not null,
    dty     integer not null,
    usn     integer not null,
    ls      integer not null,
    conf    text not null,
    models  text not null,
    decks   text not null,
    dconf   text not null,
    tags    text not null
);
CREATE TABLE IF NOT EXISTS notes (
    id      integer primary key,
    guid    text not null,
    mid     integer not null,
    mod     integer not null,
    usn     integer not null,
    tags    text not null,
    flds    text not null,
    sfld    integer not null,
    csum    integer not null,
    flags   integer not null,
    data    text not null
);
CREATE TABLE IF NOT EXISTS cards (
    id      integer primary key,
    nid     integer not null,
    did     integer not null,
    ord     integer not null,
    mod     integer not null,
    usn     integer not null,
    type    integer not null,
    queue   integer not null,
    due     integer not null,
    ivl     integer not null,
    factor  integer not null,
    reps    integer not null,
    lapses  integer not null,
    left    integer not null,
    odue    integer not null,
    odid    integer not null,
    flags   integer not null,
    data    text not null
);
CREATE TABLE IF NOT EXISTS revlog (
    id      integer primary key,
    cid     integer not null,
    usn     integer not null,
    ease    integer not null,
    ivl     integer not null,
    lastIvl integer not null,
    factor  integer not null,
    time    integer not null,
    type    integer not null
);
CREATE TABLE IF NOT EXISTS graves (
    usn     integer not null,
    oid     integer not null,
    type    integer not null
);
CREATE INDEX IF NOT EXISTS ix_notes_usn   ON notes(usn);
CREATE INDEX IF NOT EXISTS ix_cards_usn   ON cards(usn);
CREATE INDEX IF NOT EXISTS ix_cards_nid   ON cards(nid);
CREATE INDEX IF NOT EXISTS ix_cards_sched ON cards(did, queue, due);
CREATE INDEX IF NOT EXISTS ix_revlog_usn  ON revlog(usn);
CREATE INDEX IF NOT EXISTS ix_revlog_cid  ON revlog(cid);
"""


# ── Helper functions ───────────────────────────────────────────────────────────

def _guid(word: str) -> str:
    """Deterministic GUID dựa trên từ — cùng từ = cùng GUID.
    Đảm bảo Anki update note thay vì tạo bản sao khi import lại.
    """
    h = hashlib.sha1(("vocab-en-vi:" + word.lower().strip()).encode()).hexdigest()
    return h[:10]


def _csum(text: str) -> int:
    """Anki field checksum: first 8 hex chars of SHA-1, converted to int."""
    return int(hashlib.sha1(text.encode("utf-8")).hexdigest()[:8], 16)


# ── JSON config builders ───────────────────────────────────────────────────────

def _build_model(model_id: int, deck_id: int, ts: int) -> dict:
    """Build the Anki note type (model) JSON object."""
    return {
        str(model_id): {
            "id":     model_id,
            "name":   "Vocab-EN-VI",
            "type":   0,
            "mod":    ts,
            "usn":    -1,
            "sortf":  0,           # sort by Word field
            "did":    deck_id,
            "tmpls": [{
                "name":  "Card 1",
                "ord":   0,
                "qfmt":  FRONT_TMPL,
                "afmt":  BACK_TMPL,
                "bqfmt": "",
                "bafmt": "",
                "did":   None,
                "bfont": "",
                "bsize": 0,
            }],
            "flds": [
                {"name": "Word",   "ord": 0, "sticky": False, "rtl": False, "font": "Arial", "size": 20, "media": []},
                {"name": "IPA",    "ord": 1, "sticky": False, "rtl": False, "font": "Arial", "size": 20, "media": []},
                {"name": "Audio",  "ord": 2, "sticky": False, "rtl": False, "font": "Arial", "size": 20, "media": []},
                {"name": "Senses", "ord": 3, "sticky": False, "rtl": False, "font": "Arial", "size": 20, "media": []},
            ],
            "css":       CARD_CSS,
            "latexPre":  (
                "\\documentclass[12pt]{article}\n"
                "\\special{papersize=3in,5in}\n"
                "\\usepackage[utf8]{inputenc}\n"
                "\\usepackage{amssymb,amsmath}\n"
                "\\pagestyle{empty}\n"
                "\\setlength{\\parindent}{0in}\n"
                "\\begin{document}\n"
            ),
            "latexPost": "\\end{document}",
            "tags":      [],
            "vers":      [],
            # req: card 0 needs any of [field 0 (Word)] to be non-empty
            "req":       [[0, "any", [0]]],
        }
    }


def _build_deck(deck_id: int, deck_name: str, ts: int) -> dict:
    """Build decks JSON — always include default deck (id=1) plus our deck."""
    deck_entry = {
        "id":         deck_id,
        "name":       deck_name,
        "desc":       "",
        "extendNew":  10,
        "extendRev":  50,
        "collapsed":  False,
        "newToday":   [0, 0],
        "revToday":   [0, 0],
        "lrnToday":   [0, 0],
        "timeToday":  [0, 0],
        "dyn":        0,
        "conf":       1,
        "usn":        -1,
        "mod":        ts,
    }
    default = {**deck_entry, "id": 1, "name": "Default"}
    return {"1": default, str(deck_id): deck_entry}


def _build_dconf(ts: int) -> dict:
    """Build deck config (scheduling settings)."""
    return {
        "1": {
            "id":       1,
            "name":     "Default",
            "new": {
                "delays":        [1, 10],
                "ints":          [1, 4, 7],
                "initialFactor": 2500,
                "order":         1,
                "perDay":        20,
            },
            "lapse": {
                "delays":      [10],
                "mult":        0,
                "minInt":      1,
                "leechFails":  8,
                "leechAction": 0,
            },
            "rev": {
                "perDay":  100,
                "ease4":   1.3,
                "fuzz":    0.05,
                "minSpace": 1,
                "ivlFct":  1,
                "maxIvl":  36500,
                "bury":    False,
            },
            "maxTaken":  60,
            "timer":     0,
            "autoplay":  True,
            "replayq":   True,
            "mod":       ts,
            "usn":       0,
        }
    }


def _build_conf(model_id: int, deck_id: int) -> dict:
    return {
        "nextPos":      1,
        "estTimes":     True,
        "activeDecks":  [deck_id],
        "sortType":     "noteFld",
        "timeLim":      0,
        "sortBackwards": False,
        "addToCur":     True,
        "curDeck":      deck_id,
        "newBury":      True,
        "newSpread":    0,
        "dueCounts":    True,
        "curModel":     str(model_id),
        "collapseTime": 1200,
    }


# ── Database builder ───────────────────────────────────────────────────────────

def _create_collection(
    db_path: Path,
    cards: list[CardData],
    deck_name: str,
) -> None:
    """Create a valid collection.anki2 SQLite file."""
    ts       = int(time.time())
    ts_ms    = int(time.time() * 1000)
    # Deterministic IDs: cùng tên deck/model = cùng ID qua các lần chạy
    # Anki dùng ID để map model khi import — ID ổn định tránh tạo duplicate note type
    model_id = int(hashlib.sha1(b"Vocab-EN-VI-model").hexdigest()[:12], 16) % (10**13)
    deck_id  = int(hashlib.sha1(deck_name.encode()).hexdigest()[:12], 16) % (10**13)

    conn = sqlite3.connect(str(db_path))
    cur  = conn.cursor()
    cur.executescript(_SCHEMA_SQL)

    # ── col row ──
    cur.execute(
        "INSERT INTO col VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            1,                                              # id
            ts,                                            # crt
            ts,                                            # mod
            ts_ms,                                         # scm
            11,                                            # ver (Anki 2.1)
            0,                                             # dty
            -1,                                            # usn
            0,                                             # ls
            json.dumps(_build_conf(model_id, deck_id)),   # conf
            json.dumps(_build_model(model_id, deck_id, ts)),  # models
            json.dumps(_build_deck(deck_id, deck_name, ts)),  # decks
            json.dumps(_build_dconf(ts)),                  # dconf
            "{}",                                          # tags
        ),
    )

    # ── notes + cards ──
    for i, card in enumerate(cards):
        # Note ID deterministic: hash của word, đảm bảo stable across runs
        note_id = int(hashlib.sha1(("note:" + card.word.lower()).encode()).hexdigest()[:12], 16) % (10**13)
        card_id = int(hashlib.sha1(("card:" + card.word.lower()).encode()).hexdigest()[:12], 16) % (10**13)

        # Fields joined by \x1f (ASCII 31)
        flds = "\x1f".join([
            card.word,
            card.ipa,
            card.audio_tag,
            card.senses_html,
        ])

        cur.execute(
            "INSERT INTO notes VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                note_id,           # id
                _guid(card.word),  # guid — deterministic
                model_id,          # mid
                ts,                # mod
                -1,                # usn
                "",                # tags
                flds,              # flds
                card.word,         # sfld (sort field text)
                _csum(card.word),  # csum
                0,                 # flags
                "",                # data
            ),
        )

        cur.execute(
            "INSERT INTO cards VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                card_id,   # id
                note_id,   # nid
                deck_id,   # did
                0,         # ord (template index)
                ts,        # mod
                -1,        # usn
                0,         # type  (0 = new)
                0,         # queue (0 = new)
                i + 1,     # due   (position in new queue)
                0,         # ivl
                0,         # factor
                0,         # reps
                0,         # lapses
                0,         # left
                0,         # odue
                0,         # odid
                0,         # flags
                "",        # data
            ),
        )

    conn.commit()
    conn.close()


# ── ZIP packer ─────────────────────────────────────────────────────────────────

def pack_apkg(
    cards: list[CardData],
    deck_name: str,
    output_path: Path,
) -> None:
    """
    Main function: pack a list of CardData into a valid .apkg file.

    Args:
        cards       : list of CardData from card_builder.build_card()
        deck_name   : deck name shown in Anki (can include "::" for subdecks)
        output_path : where to write the .apkg file
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    import tempfile, os
    tmp = Path(tempfile.mkdtemp(prefix="anki_pack_"))

    try:
        # ── 1. Create SQLite collection ──
        db_path = tmp / "collection.anki2"
        _create_collection(db_path, cards, deck_name)
        print(f"  [packer] {len(cards)} notes written to SQLite")

        # ── 2. Collect media files ──
        media_map: dict[str, str] = {}   # {"0": "oxford_br_gauge.mp3", ...}
        idx = 0

        for card in cards:
            if card.audio_file and card.audio_file.exists():
                fname = card.audio_file.name
                # Avoid adding same file twice
                if fname not in media_map.values():
                    media_map[str(idx)] = fname
                    shutil.copy(card.audio_file, tmp / str(idx))
                    idx += 1

        print(f"  [packer] {len(media_map)} media files collected")

        # ── 3. Write media manifest ──
        (tmp / "media").write_text(
            json.dumps(media_map, ensure_ascii=False), encoding="utf-8"
        )

        # ── 4. ZIP everything into .apkg ──
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(db_path, "collection.anki2")
            zf.write(tmp / "media", "media")
            for idx_str in media_map:
                zf.write(tmp / idx_str, idx_str)

        size_kb = output_path.stat().st_size // 1024
        print(f"  [packer] ✅ {output_path}  ({size_kb} KB)")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── Mock test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from enricher import EnrichedResult, EnrichedSense
    from card_builder import build_card

    def _mock_card(word: str, ipa: str, pos: str, vi: str, usage: str) -> CardData:
        enriched = EnrichedResult(
            word=word, ok=True,
            senses=[EnrichedSense(
                pos=pos,
                vi_meaning=vi,
                en_def=f"(mock definition for {word})",
                usage_note=usage,
                collocations=[f"{word} well", f"greatly {word}", f"{word} significantly"],
                example=f"The project will {word} the outcome.",
            )],
        )
        return build_card(
            word=word,
            ipa=ipa,
            audio_filename=None,  # no audio in mock
            enriched=enriched,
        )

    mock_cards = [
        _mock_card("gauge",     "/ɡeɪdʒ/",  "v",   "đánh giá, ước lượng",
                   "Dùng khi đánh giá mức độ hoặc tình huống dựa trên thông tin gián tiếp."),
        _mock_card("impair",    "/ɪmˈpeər/", "v",   "làm suy giảm khả năng",
                   "Dùng khi nói đến sức khỏe, chức năng hoặc khả năng bị suy giảm."),
        _mock_card("mediocre",  "/ˌmiːdiˈoʊkər/", "adj", "tầm thường, không nổi bật",
                   "Dùng để nhận xét chất lượng hoặc kết quả ở mức trung bình, thiếu xuất sắc."),
        _mock_card("undermine", "/ˌʌndərˈmaɪn/", "v", "làm xói mòn, làm suy yếu",
                   "Dùng khi làm suy yếu niềm tin, quyền lực hoặc uy tín từ bên dưới/bên trong."),
    ]

    out = Path("output/test_deck.apkg")
    print(f"Packing {len(mock_cards)} cards → {out}")
    pack_apkg(mock_cards, "Vocab Test Deck", out)

    # Verify ZIP contents
    print("\nZIP contents:")
    with zipfile.ZipFile(out) as zf:
        for name in zf.namelist():
            size = zf.getinfo(name).file_size
            print(f"  {name:30s}  {size:>8,} bytes")

    # Verify SQLite
    print("\nSQLite check:")
    import tempfile, os
    with zipfile.ZipFile(out) as zf:
        tmp = Path(tempfile.mkdtemp())
        zf.extract("collection.anki2", tmp)
        conn = sqlite3.connect(str(tmp / "collection.anki2"))
        n_notes = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        n_cards = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
        model   = json.loads(conn.execute("SELECT models FROM col").fetchone()[0])
        conn.close()
        shutil.rmtree(tmp)

    model_name = list(model.values())[0]["name"]
    print(f"  notes : {n_notes}")
    print(f"  cards : {n_cards}")
    print(f"  model : {model_name}")
    print(f"\n  → Import {out} vào Anki để kiểm tra!")
