"""Adaptive, self-throttling HTTP client for screener.in.

screener.in rate-limits aggressively (HTTP 429 on any burst), so every request
goes through one process-wide gate that spaces calls and backs off multiplicatively
whenever a 429 is seen. 429 responses are never counted as data failures.
"""
from __future__ import annotations

import gzip
import http.client
import json
import random
import threading
import time
import urllib.error
import urllib.request
from typing import Optional

from . import config

_lock = threading.Lock()
_next_allowed = 0.0
_spacing = config.MIN_SPACING_S
_stats = {"requests": 0, "throttled": 0, "errors": 0}


def _headers(extra: Optional[dict] = None) -> dict:
    h = {
        "User-Agent": config.USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-IN,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "close",
    }
    if extra:
        h.update(extra)
    return h


def _wait_for_slot() -> float:
    """Serialise callers onto the shared spacing schedule."""
    global _next_allowed
    with _lock:
        now = time.monotonic()
        wait = max(0.0, _next_allowed - now)
        # Jitter avoids a machine-gun periodic signature.
        _next_allowed = max(now, _next_allowed) + _spacing + random.uniform(0, 0.15)
    if wait:
        time.sleep(wait)
    return wait


def _penalise() -> None:
    global _spacing
    with _lock:
        _spacing = min(config.MAX_SPACING_S, max(_spacing * 2, config.SPACING_ON_429))
        _stats["throttled"] += 1
    time.sleep(_spacing)


def _restore() -> None:
    global _spacing
    with _lock:
        _spacing = max(config.MIN_SPACING_S, _spacing * 0.85)


def _decode(resp) -> str:
    raw = resp.read()
    if (resp.headers.get("Content-Encoding") or "").lower() == "gzip":
        raw = gzip.decompress(raw)
    charset = resp.headers.get_content_charset() or "utf-8"
    return raw.decode(charset, errors="replace")


def get(url: str, timeout: int = config.REQUEST_TIMEOUT_S) -> Optional[str]:
    """Return the decoded body, or None if every attempt was throttled/failed.

    urlopen raises HTTPError for any 4xx/5xx, so a 429 never reaches the
    `resp.status` branch below - it has to be caught as an exception ahead of
    the generic OSError handler, or the throttle is invisible and the crawler
    keeps hammering the limit that is already telling it to slow down.
    """
    for attempt in range(1, config.MAX_ATTEMPTS + 1):
        _wait_for_slot()
        req = urllib.request.Request(url, headers=_headers())
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    with _lock:
                        _stats["requests"] += 1
                    _restore()
                    return _decode(resp)
                with _lock:
                    _stats["errors"] += 1
                time.sleep(min(30, 2 ** attempt))
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                _penalise()
                continue
            if exc.code == 404:
                return None
            with _lock:
                _stats["errors"] += 1
            time.sleep(min(30, 2 ** attempt))
        except (http.client.IncompleteRead, urllib.error.URLError, TimeoutError, OSError):
            with _lock:
                _stats["errors"] += 1
            time.sleep(min(30, 2 ** attempt) + random.uniform(0, 1))
    return None


def get_json(url: str) -> Optional[dict]:
    body = get(url)
    if not body:
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return None


def snapshot() -> dict:
    with _lock:
        out = dict(_stats)
    out["spacing_s"] = round(_spacing, 3)
    return out
