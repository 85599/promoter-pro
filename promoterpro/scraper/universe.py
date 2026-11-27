"""Discover the full listed-company universe from screener's public sitemaps.

The sitemap is the only robots-allowed source of a complete company list; the
JSON search API is single-term only and there is no bulk endpoint.
"""
from __future__ import annotations

import csv
import re
from typing import Iterable

from . import client, config, store

_URL_RE = re.compile(r"<loc>\s*(.*?)\s*</loc>")
_COMPANY_RE = re.compile(r"/company/([A-Za-z0-9]+)(?:/(consolidated|))?/?$")
_TITLE_RE = re.compile(r"<title>\s*(.*?)(?:\s+value|\s+and)", re.S)


def _shards() -> list[str]:
    index = client.get(config.SITEMAP_INDEX)
    if not index:
        return []
    return [
        u
        for u in _URL_RE.findall(index)
        if "sitemap-companies" in u
    ]


def _normalise(url: str) -> tuple[str, bool]:
    """Return (key, is_consolidated) for a company sitemap URL."""
    m = _COMPANY_RE.search(url)
    if not m:
        return "", False
    return m.group(1), m.group(2) == "consolidated"


def _is_index(key: str) -> bool:
    """screener models indices (Sensex, Nifty, sector packs) as companies.

    Their ids sit in a low numeric block; real BSE codes start at 5xxxxx, and
    everything else is an alphabetic ticker.
    """
    return key.isdigit() and int(key) < 100000


def collect() -> tuple[list[dict], list[dict]]:
    """Return (companies, indices) discovered from the sitemaps."""
    seen: dict[str, dict] = {}
    indices: dict[str, dict] = {}
    for shard in _shards():
        body = client.get(shard)
        if not body:
            continue
        for loc in _URL_RE.findall(body):
            key, consolidated = _normalise(loc.split("?")[0])
            if not key:
                continue
            target = indices if _is_index(key) else seen
            rec = target.setdefault(key, {"key": key, "consolidated": False, "url": loc})
            if consolidated and not rec["consolidated"]:
                rec["consolidated"] = True
                rec["url"] = loc
    records = sorted(seen.values(), key=lambda r: r["key"])
    if config.UNIVERSE_LIMIT:
        records = records[: config.UNIVERSE_LIMIT]
    return records, sorted(indices.values(), key=lambda r: r["key"])


def iter_universe() -> Iterable[dict]:
    """Yield one record per company, preferring the consolidated view."""
    return collect()[0]


def backfill_names() -> int:
    """Label companies the crawl has not reached, from NSE's public equity list.

    Screener's sitemap publishes symbols only, so until a page is read the
    universe has no name to match on.  One request covers the NSE-listed set.
    """
    body = client.get(config.NSE_EQUITY_LIST)
    if not body:
        return 0
    pairs = []
    for cols in csv.reader(body.splitlines()):
        if len(cols) > 1 and cols[0].strip() and cols[1].strip():
            pairs.append((cols[0].strip().upper(), cols[1].strip()))
    pairs = [p for p in pairs if p[0] != "SYMBOL"]
    return store.set_names(pairs)
