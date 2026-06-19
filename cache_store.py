#!/usr/bin/env python3
"""
Cache store — lớp cache trung gian
=====================================
Tự động chọn backend dựa trên env vars:
  - Upstash Redis  : nếu có UPSTASH_REDIS_REST_URL + UPSTASH_REDIS_REST_TOKEN
  - Local JSON file: fallback cho local dev

Usage:
    from cache_store import cache_get, cache_set, backend_name

    data = cache_get("gemini:gauge")          # None nếu chưa có
    cache_set("gemini:gauge", {"senses": ...})
    print(backend_name())                      # "Upstash Redis" hoặc "Local JSON"
"""

import json
import os
from pathlib import Path
from typing import Optional

import requests


# ── Config ─────────────────────────────────────────────────────────────────────

LOCAL_CACHE_FILE = Path("cache/store.json")   # một file duy nhất cho tất cả namespaces
REDIS_TIMEOUT    = 5                          # seconds


# ── Upstash Redis backend ──────────────────────────────────────────────────────

def _redis_url() -> Optional[str]:
    return os.environ.get("UPSTASH_REDIS_REST_URL", "").strip() or None


def _redis_token() -> Optional[str]:
    return os.environ.get("UPSTASH_REDIS_REST_TOKEN", "").strip() or None


def _use_redis() -> bool:
    """True nếu cả URL và token đều có trong env vars."""
    return bool(_redis_url() and _redis_token())


def _redis_cmd(*args) -> object:
    """Gọi một Redis command qua Upstash REST API."""
    r = requests.post(
        _redis_url(),
        headers={
            "Authorization": f"Bearer {_redis_token()}",
            "Content-Type": "application/json",
        },
        json=list(args),
        timeout=REDIS_TIMEOUT,
    )
    r.raise_for_status()
    return r.json().get("result")


def _redis_get(key: str) -> Optional[dict]:
    try:
        raw = _redis_cmd("GET", key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as e:
        print(f"  [cache/redis] GET error: {e}")
        return None


def _redis_set(key: str, data: dict) -> None:
    try:
        _redis_cmd("SET", key, json.dumps(data, ensure_ascii=False))
    except Exception as e:
        print(f"  [cache/redis] SET error: {e}")


# ── Local JSON backend ─────────────────────────────────────────────────────────

def _local_load() -> dict:
    if LOCAL_CACHE_FILE.exists():
        try:
            return json.loads(LOCAL_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _local_get(key: str) -> Optional[dict]:
    return _local_load().get(key)


def _local_set(key: str, data: dict) -> None:
    LOCAL_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    store = _local_load()
    store[key] = data
    LOCAL_CACHE_FILE.write_text(
        json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ── Public interface ───────────────────────────────────────────────────────────

def cache_get(key: str) -> Optional[dict]:
    """
    Lấy cached data theo key.
    Trả về dict nếu có, None nếu chưa cache.
    
    Key convention: "namespace:word"
    Ví dụ: "freedict:gauge", "gemini:ironies"
    """
    if _use_redis():
        return _redis_get(key)
    return _local_get(key)


def cache_set(key: str, data: dict) -> None:
    """
    Lưu data vào cache.
    Tự động chọn Redis hoặc local JSON.
    """
    if _use_redis():
        _redis_set(key, data)
    else:
        _local_set(key, data)


def backend_name() -> str:
    """Trả về tên backend đang dùng — để hiển thị trong UI."""
    return "Upstash Redis" if _use_redis() else "Local JSON"


# ── Self-test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"Backend: {backend_name()}")

    test_key  = "_test:cache_store"
    test_data = {"word": "test", "ok": True, "values": [1, 2, 3]}

    print(f"SET {test_key!r} ...")
    cache_set(test_key, test_data)

    print(f"GET {test_key!r} ...")
    result = cache_get(test_key)

    if result == test_data:
        print("✅ Cache round-trip OK")
    else:
        print(f"❌ Mismatch: {result}")

    missing = cache_get("_test:does_not_exist")
    if missing is None:
        print("✅ Missing key returns None")
    else:
        print(f"❌ Expected None, got: {missing}")
