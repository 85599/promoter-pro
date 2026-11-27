"""Seed approximate market caps from screener's public index pages.

An index page lists its constituents with market capitalisation in one request,
so ~50 requests give the crawler a size-ordered front of the market instead of
an arbitrary alphabetical one.  The dashboard therefore shows the Nifty-scale
names within the first few minutes of a cold run.
"""
from __future__ import annotations

from . import client, config, parser, store


def apply_seeds(indices: list[dict]) -> dict:
    """Fetch each index page and mark its members in the companies table."""
    seen_members: set[str] = set()
    for rec in indices:
        url = rec["url"] if "consolidated" in rec["url"] else f"{config.BASE_URL}/company/{rec['key']}/"
        doc = store.get_page(rec["key"]) or client.get(url)
        if not doc:
            continue
        store.save_page(rec["key"], doc)
        _, members = parser.parse_index_constituents(doc)
        for m in members:
            if not m["key"]:
                continue
            seen_members.add(m["key"])
            store.seed_from_index(m["key"], m["mcap"])
    return {"indices": len(indices), "members_seeded": len(seen_members)}
