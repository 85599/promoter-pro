"""Resumable scrape runner.

Two workers is the ceiling screener tolerates before it starts answering 429,
and the shared client backs off further when that happens.  Pages are cached in
SQLite so a re-run only pays for what is stale or previously failed.
"""
from __future__ import annotations

import queue as queue_mod
import threading
import time

from . import client, config, parser, prioritize, score, store, universe

_state = {
    "running": False,
    "target": 0,
    "done": 0,
    "failed": 0,
    "skipped": 0,
    "started_at": None,
    "last_key": None,
    "eta_s": None,
    "phase": "idle",
}
_stop = threading.Event()
_lock = threading.Lock()


def state() -> dict:
    with _lock:
        s = dict(_state)
    s["http"] = client.snapshot()
    if not s["http"]["requests"]:
        # This process is not the crawler; report the crawl's real throttle
        # history rather than an empty local counter that reads as "no backoffs".
        shared = store.crawl_http()
        if shared:
            s["http"] = {
                "requests": shared["requests"],
                "throttled": shared["throttled"],
                "errors": shared["errors"],
                "spacing_s": shared["spacing"],
            }
    s["db"] = store.counts()
    # A crawl started by another process (or an earlier server run) is still a
    # live crawl as far as the dashboard is concerned, but this process cannot
    # see its clock - measure its throughput from the database instead.
    recent = store.recent_activity(120)
    if recent and not s["running"]:
        s["running"] = True
        s["target"] = s["db"]["total"]
        s["done"] = s["db"]["ok"]
        s["failed"] = s["db"]["failed"]
        s["phase"] = "scraping"
        s["per_min"] = round(recent / 2, 1)
        remaining = max(0, s["target"] - s["done"] - s["failed"])
        s["eta_s"] = int(remaining / (recent / 120)) if recent else None
    elif s["done"] > 5 and s["started_at"]:
        elapsed = time.time() - s["started_at"]
        rate = s["done"] / elapsed if elapsed else 0
        s["per_min"] = round(rate * 60, 1)
        remaining = max(0, s["target"] - s["done"] - s["skipped"])
        s["eta_s"] = int(remaining / rate) if rate > 0 else None
    return s


def _bump(**kw) -> None:
    with _lock:
        for k, v in kw.items():
            _state[k] = _state[k] + v if isinstance(v, int) and k in ("done", "failed", "skipped") else v
    # The dashboard usually runs in a different process, so the crawl's own
    # backoff counters only reach it through the database.
    store.save_crawl_http(client.snapshot())


def _worker(q: "queue_mod.Queue") -> None:
    while not _stop.is_set():
        try:
            key, url = q.get(timeout=1.0)
        except queue_mod.Empty:
            continue
        try:
            cached = store.get_page(key, max_age_s=config.STALE_AFTER_DAYS * 86400)
            if cached is not None:
                html, from_cache = cached, True
            else:
                html = client.get(url)
                from_cache = False
                if html:
                    store.save_page(key, html)
            if not html or len(html) < 4000:
                store.mark(key, "failed", "empty or throttled response")
                _bump(failed=1)
                continue
            try:
                rec = parser.parse_company(html, key)
                if not rec["financials"]["net_profit"] and not rec["shareholding"].get("promoters"):
                    # Index / sector page, not a company - nothing to score.
                    store.mark(key, "skipped", "not a company page")
                    _bump(skipped=1)
                    continue
                rec["from_cache"] = from_cache
                store.save_fundamentals(rec)
                store.mark(key, "ok")
                _bump(done=1, last_key=key)
            except Exception as exc:  # a page-layout change must not kill the run
                store.mark(key, "failed", f"parse: {exc}")
                _bump(failed=1)
        finally:
            q.task_done()


def fetch_one(key: str) -> dict | None:
    """Read one company immediately, for search on names not crawled yet.

    Cache-first, and it shares the client's throttle, so a lookup while a crawl
    is running costs one polite request rather than restarting anything.
    """
    row = store.conn().execute("SELECT url FROM companies WHERE key=?", (key,)).fetchone()
    if not row:
        return None
    html = store.get_page(key, max_age_s=config.STALE_AFTER_DAYS * 86400)
    fresh = html is not None
    if not fresh:
        html = client.get(row["url"])
        if html:
            store.save_page(key, html)
    if not html or len(html) < 4000:
        return None
    rec = parser.parse_company(html, key)
    if not rec["financials"]["net_profit"] and not rec["shareholding"].get("promoters"):
        return None
    rec["from_cache"] = fresh
    store.save_fundamentals(rec)
    store.mark(key, "ok")
    return rec


def build_universe() -> int:
    """Index every listed company, then seed size ordering from the index pages."""
    with _lock:
        _state["phase"] = "discovering universe"
    companies, indices = universe.collect()
    store.upsert_universe(companies)
    store.save_indices(indices)
    prioritize.apply_seeds(indices)
    with _lock:
        _state["phase"] = "universe seeded"
    return len(companies)


def start(limit: int = 0) -> dict:
    if _state["running"]:
        return state()
    _stop.clear()
    with _lock:
        _state.update(running=True, done=0, failed=0, skipped=0,
                      started_at=time.time(), phase="scraping", eta_s=None)

    def main() -> None:
        try:
            if not store.counts()["total"]:
                build_universe()
            todo = store.pending(limit=limit)
            with _lock:
                _state["target"] = len(todo)
            q: "queue_mod.Queue" = queue_mod.Queue(maxsize=config.MAX_WORKERS * 6)
            workers = [
                threading.Thread(target=_worker, args=(q,), daemon=True)
                for _ in range(config.MAX_WORKERS)
            ]
            for w in workers:
                w.start()
            for item in todo:
                if _stop.is_set():
                    break
                q.put((item["key"], item["url"]))
            q.join()
            _stop.set()
        finally:
            with _lock:
                _state["running"] = False
                _state["phase"] = "stopped" if _stop.is_set() else "complete"
            store.log_run(_state["target"], _state["done"], _state["failed"], "run")

    threading.Thread(target=main, daemon=True).start()
    return state()


def stop() -> dict:
    _stop.set()
    with _lock:
        _state["phase"] = "stopping"
    return state()


def reparse_all() -> int:
    """Re-run the parser over every cached page - free, and needed after a parser change."""
    rows = store.conn().execute("SELECT key FROM fundamentals").fetchall()
    n = 0
    for row in rows:
        html = store.get_page(row["key"])
        if not html:
            continue
        try:
            rec = parser.parse_company(html, row["key"])
            if not rec["financials"]["net_profit"]:
                continue
            store.save_fundamentals(rec)
            n += 1
        except Exception:
            continue
    return n


def refresh_scores() -> list[dict]:
    """Score every cached company; returns the ranked result set."""
    results = [score.evaluate(rec) for rec in store.all_fundamentals()]
    results.sort(key=lambda r: (r["score"], r["metrics"].get("market_cap") or 0), reverse=True)
    return results
