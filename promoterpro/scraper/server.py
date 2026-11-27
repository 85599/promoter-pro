"""Local HTTP server: JSON API plus the static dashboard.

Stdlib only, so `python -m scraper.server` is the entire install step.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import config, runner, score, store, universe

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")

_MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}

_cache_lock = threading.Lock()
_results_cache: list[dict] = []
_cache_size = 0


def rebuild_results() -> list[dict]:
    global _results_cache, _cache_size
    fresh = runner.refresh_scores()
    with _cache_lock:
        _results_cache = fresh
        _cache_size = store.counts()["parsed"]
    return fresh


def _refresh_if_stale() -> None:
    """Re-score whenever new pages have landed since the last build."""
    global _cache_size
    live = store.counts()["parsed"]
    if live != _cache_size:
        rebuild_results()


def to_csv(rows: list[dict]) -> str:
    """The screened table as CSV, one row per company, columns self-describing."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "symbol", "name", "track", "verdict", "quality_score", "confidence_pct",
        "promoter_pct", "promoter_change_1y_pct", "market_cap_cr", "price_rs",
        "pe", "peg", "earnings_yield_pct", "returns_pct", "roce", "debt_equity",
        "sales_cagr_5_pct", "profit_cagr_5_pct",
        "fair_value_low_rs", "fair_value_mid_rs", "fair_value_high_rs",
        "gap_to_fair_value_pct", "valuation_basis", "severe_red_flags", "all_red_flags",
    ])
    for r in rows:
        m, fv = r["metrics"], r["fair_value"]
        w.writerow([
            r["key"], r["name"], r["track"], r["verdict"], r["score"], r["confidence"],
            m.get("promoter_pct"), m.get("promoter_chg_1y"), m.get("market_cap"),
            m.get("price"), m.get("pe"), m.get("peg"), m.get("earnings_yield"),
            m.get("return_ratio"), m.get("roce"), m.get("debt_equity"),
            m.get("sales_cagr_5"), m.get("profit_cagr_5"),
            fv.get("low"), fv.get("mid"), fv.get("high"), fv.get("gap_to_mid_pct"),
            fv.get("basis") or fv.get("method"),
            "; ".join(f["text"] for f in r["red_flags"] if f["severe"]),
            " | ".join(f["text"] for f in r["red_flags"]),
        ])
    return buf.getvalue()


def _filtered(params: dict) -> dict:
    _refresh_if_stale()
    with _cache_lock:
        rows = list(_results_cache)
    if not rows:
        rows = rebuild_results()

    def num(key, default=None):
        raw = params.get(key, [""])
        try:
            return float(raw[0]) if raw and raw[0] != "" else default
        except ValueError:
            return default

    min_promoter = num("min_promoter", config.PROMOTER_MIN_DEFAULT)
    min_score = num("min_score", 0)
    min_mcap = num("min_mcap", 0)
    max_pe = num("max_pe", 0)
    q = (params.get("q", [""])[0] or "").strip().lower()
    verdict = (params.get("verdict", [""])[0] or "").strip().upper()
    sort = params.get("sort", ["score"])[0]
    order = params.get("order", ["desc"])[0]
    limit = int(num("limit", 200))

    out = []
    for r in rows:
        m = r["metrics"]
        p = m.get("promoter_pct")
        if min_promoter and (p is None or p < min_promoter):
            continue
        if min_score and r["score"] < min_score:
            continue
        if min_mcap and (m.get("market_cap") or 0) < min_mcap:
            continue
        if max_pe and not (0 < (m.get("pe") or 0) <= max_pe):
            continue
        if verdict and verdict not in (r["verdict"], "ALL"):
            continue
        if q and q not in (r["name"] or "").lower() and q not in (r["key"] or "").lower():
            continue
        out.append(r)

    key_map = {
        "score": lambda r: r["score"],
        "promoter": lambda r: r["metrics"].get("promoter_pct") or 0,
        "mcap": lambda r: r["metrics"].get("market_cap") or 0,
        "pe": lambda r: r["metrics"].get("pe") or 9e9,
        "roce": lambda r: r["metrics"].get("roce") or 0,
        "value": lambda r: (r["fair_value"].get("gap_to_mid_pct") or -9e9),
        "name": lambda r: (r["name"] or "").lower(),
    }
    fn = key_map.get(sort, key_map["score"])
    out.sort(key=fn, reverse=(order != "asc"))
    return {"total_matched": len(out), "rows": out[:limit] if limit else out}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # keep the console readable
        pass

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)
        if path == "/api/status":
            st = runner.state()
            st["counts"] = store.counts()
            st["scored"] = len(_results_cache)
            return self._json(st)
        if path == "/api/results":
            return self._json(_filtered(params))
        if path == "/api/export":
            all_params = dict(params)
            all_params["limit"] = ["0"]          # every match, not one page
            rows = _filtered(all_params)["rows"]
            name = f"promoter-screen-{time.strftime('%Y%m%d-%H%M')}.csv"
            # BOM so Excel on Windows reads the rupee sign without mangling it.
            body = "﻿".encode("utf-8") + to_csv(rows).encode("utf-8")
            return self._send(200, body, "text/csv; charset=utf-8",
                              {"Content-Disposition": f'attachment; filename="{name}"'})
        if path == "/api/search":
            term = (params.get("q", [""])[0] or "").strip()
            if len(term) < 2:
                return self._json({"matches": []})
            return self._json({"matches": store.search_universe(term)})
        if path.startswith("/api/company/"):
            key = path.rsplit("/", 1)[-1]
            row = store.conn().execute(
                "SELECT payload FROM fundamentals WHERE key=?", (key,)
            ).fetchone()
            if not row:
                return self._json({"error": "not scraped yet"}, 404)
            rec = json.loads(row["payload"])
            return self._json({"record": rec, "analysis": score.evaluate(rec)})
        if path.startswith("/api/"):
            return self._json({"error": "unknown endpoint"}, 404)
        return self._static(path)

    def do_POST(self):  # noqa: N802
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(body or b"{}")
        except json.JSONDecodeError:
            payload = {}
        if parsed.path == "/api/scrape":
            st = runner.start(limit=int(payload.get("limit", 0) or 0))
            return self._json(st)
        if parsed.path == "/api/scrape/stop":
            return self._json(runner.stop())
        if parsed.path == "/api/rebuild":
            n = len(rebuild_results())
            return self._json({"scored": n})
        if parsed.path == "/api/universe":
            n = runner.build_universe()
            universe.backfill_names()
            return self._json({"universe": n, "counts": store.counts()})
        if parsed.path == "/api/fetch":
            key = str(payload.get("key") or "").strip()
            rec = runner.fetch_one(key) if key else None
            if rec is None:
                return self._json({"error": "No readable company page for that symbol."}, 404)
            rebuild_results()
            return self._json({"record": rec, "analysis": score.evaluate(rec)})
        return self._json({"error": "unknown endpoint"}, 404)

    def _static(self, path: str) -> None:
        rel = "index.html" if path in {"/", ""} else path.lstrip("/")
        full = os.path.normpath(os.path.join(WEB_DIR, rel))
        if not full.startswith(WEB_DIR) or not os.path.isfile(full):
            full = os.path.join(WEB_DIR, "index.html")
        if not os.path.isfile(full):
            return self._send(404, b"frontend missing", "text/plain")
        ext = os.path.splitext(full)[1].lower()
        with open(full, "rb") as fh:
            self._send(200, fh.read(), _MIME.get(ext, "application/octet-stream"))


def _ensure_names() -> None:
    """Label the universe once, so search can match names on day one.

    The crawl never rewrites companies.name - it fills fundamentals - so this
    fires only on a fresh database rather than on every start.
    """
    known = store.conn().execute(
        "SELECT COUNT(*) FROM companies WHERE name IS NOT NULL AND name <> ''"
    ).fetchone()[0]
    if not known and store.counts()["total"]:
        universe.backfill_names()


def serve(port: int = 8757) -> None:
    _ensure_names()
    rebuild_results()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"ScreenerPro dashboard -> http://127.0.0.1:{port}/  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()
