"""SQLite cache: raw pages, parsed fundamentals, scrape bookkeeping."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time

from . import config

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    key         TEXT PRIMARY KEY,
    name        TEXT,
    url         TEXT,
    consolidated INTEGER DEFAULT 0,
    status      TEXT DEFAULT 'pending',      -- pending | ok | failed
    attempts    INTEGER DEFAULT 0,
    fetched_at  INTEGER DEFAULT 0,
    error       TEXT
);

CREATE TABLE IF NOT EXISTS pages (
    key        TEXT PRIMARY KEY,
    html       TEXT,
    saved_at   INTEGER
);

CREATE TABLE IF NOT EXISTS fundamentals (
    key            TEXT PRIMARY KEY,
    name           TEXT,
    scraped_at     INTEGER,
    market_cap     REAL,
    current_price  REAL,
    pe             REAL,
    promoter_pct   REAL,
    payload        TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  INTEGER,
    finished_at INTEGER,
    target      INTEGER,
    done        INTEGER,
    failed      INTEGER,
    note        TEXT
);

CREATE TABLE IF NOT EXISTS index_members (
    idx_key    TEXT,
    comp_key   TEXT,
    mcap       REAL,
    PRIMARY KEY (idx_key, comp_key)
);

CREATE TABLE IF NOT EXISTS indices (
    key   TEXT PRIMARY KEY,
    url   TEXT
);

CREATE TABLE IF NOT EXISTS crawl_http (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    spacing    REAL,
    requests   INTEGER,
    throttled  INTEGER,
    errors     INTEGER,
    updated_at INTEGER
);
"""

# Columns added after the first release; sqlite has no declarative diff.
ADDITIONS = {
    "companies": {"mcap_seed": "REAL", "in_index": "INTEGER DEFAULT 0"},
}


def _migrate(c: sqlite3.Connection) -> None:
    for table, cols in ADDITIONS.items():
        have = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
        for name, decl in cols.items():
            if name not in have:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def conn() -> sqlite3.Connection:
    c = getattr(_local, "c", None)
    if c is None:
        os.makedirs(config.DATA_DIR, exist_ok=True)
        c = sqlite3.connect(config.DB_PATH, timeout=60, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.executescript(SCHEMA)
        _migrate(c)
        _local.c = c
    return c


def upsert_universe(records: list[dict]) -> int:
    c = conn()
    now = time.time()
    c.execute("BEGIN")
    for r in records:
        c.execute(
            "INSERT INTO companies(key,name,url,consolidated) VALUES(?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET url=excluded.url, consolidated=excluded.consolidated",
            (r["key"], r.get("name"), r["url"], 1 if r.get("consolidated") else 0),
        )
    c.execute("COMMIT")
    return len(records)


def search_universe(term: str, limit: int = 30) -> list[dict]:
    """Symbol or name matches across every listed company, scraped or not.

    A name is known either because the page has been read (fundamentals) or
    because the NSE symbol list backfilled it, so search works before the crawl
    reaches a company.  SQLite's LIKE is ASCII case-insensitive on both sides.
    """
    pat = "%" + term.replace("%", r"\%").replace("_", r"\_") + "%"
    prefix = term.replace("%", r"\%").replace("_", r"\_") + "%"
    # Prefer screener's own title-case name once the page has been read; the NSE
    # backfill label is all caps and only fills the not-yet-crawled gap.
    name = "COALESCE(NULLIF(f.name, ''), NULLIF(c.name, ''))"
    sql = (
        f"SELECT c.key AS key, {name} AS name, c.status AS status, "
        f"       COALESCE(c.mcap_seed, f.market_cap) AS mcap, "
        f"       (f.key IS NOT NULL) AS scraped, "
        f"       CASE WHEN c.key = ? THEN 0 "
        f"            WHEN c.key LIKE ? ESCAPE '\\' THEN 1 "
        f"            WHEN {name} LIKE ? ESCAPE '\\' THEN 2 ELSE 3 END AS rank "
        f"FROM companies c LEFT JOIN fundamentals f ON f.key = c.key "
        f"WHERE c.key LIKE ? ESCAPE '\\' OR {name} LIKE ? ESCAPE '\\' "
        f"ORDER BY rank ASC, scraped DESC, mcap DESC, c.key ASC LIMIT ?"
    )
    c = conn()
    rows = c.execute(sql, (term.upper(), prefix, prefix, pat, pat, limit)).fetchall()
    if not rows and len(term) > 4:
        # Middle of a name: 'part' inside UNIPARTS, once the exact forms miss.
        loose = "%" + term.replace("%", r"\%").replace("_", r"\_")[1:-1] + "%"
        rows = c.execute(sql, (term.upper(), loose, loose, loose, loose, limit)).fetchall()
    return [dict(r) for r in rows]


def set_names(pairs: list[tuple[str, str]]) -> int:
    """Label companies the crawl has not reached yet; never overwrites a real name."""
    c = conn()
    c.execute("BEGIN")
    for key, name in pairs:
        c.execute(
            "UPDATE companies SET name=? WHERE key=? AND (name IS NULL OR name='')",
            (name, key),
        )
    c.execute("COMMIT")
    return len(pairs)


def pending(limit: int = 0, include_failed: bool = True) -> list[dict]:
    """Next scrape targets: seeded index members by size first, then the tail."""
    q = (
        "SELECT key, url, consolidated, attempts FROM companies "
        "WHERE (status='pending' OR status IN ('failed','skipped'))"
    )
    if not include_failed:
        q = "SELECT key, url, consolidated, attempts FROM companies WHERE status='pending'"
    q += " ORDER BY in_index DESC, COALESCE(mcap_seed,0) DESC, attempts ASC, key ASC"
    if limit:
        q += f" LIMIT {int(limit)}"
    return [dict(r) for r in conn().execute(q)]


def save_indices(records: list[dict]) -> int:
    c = conn()
    c.execute("BEGIN")
    for r in records:
        c.execute("INSERT OR REPLACE INTO indices(key,url) VALUES(?,?)", (r["key"], r["url"]))
    c.execute("COMMIT")
    return len(records)


def all_indices() -> list[dict]:
    return [dict(r) for r in conn().execute("SELECT key,url FROM indices")]


def seed_from_index(comp_key: str, mcap: float | None) -> None:
    """Record that a company appears in a public index, keeping the largest sighting."""
    c = conn()
    c.execute(
        "UPDATE companies SET in_index=1, mcap_seed=MAX(COALESCE(mcap_seed,0), ?) WHERE key=?",
        (mcap or 0, comp_key),
    )


def mark(key: str, status: str, error: str = "") -> None:
    conn().execute(
        "UPDATE companies SET status=?, attempts=attempts+1, fetched_at=?, error=? WHERE key=?",
        (status, int(time.time()), error[:300], key),
    )


def save_page(key: str, html: str) -> None:
    conn().execute(
        "INSERT OR REPLACE INTO pages(key,html,saved_at) VALUES(?,?,?)",
        (key, html, int(time.time())),
    )


def get_page(key: str, max_age_s: float | None = None) -> str | None:
    row = conn().execute("SELECT html,saved_at FROM pages WHERE key=?", (key,)).fetchone()
    if not row:
        return None
    if max_age_s is not None and time.time() - row["saved_at"] > max_age_s:
        return None
    return row["html"]


def save_fundamentals(rec: dict) -> None:
    sh = rec.get("shareholding") or {}
    conn().execute(
        "INSERT OR REPLACE INTO fundamentals(key,name,scraped_at,market_cap,current_price,pe,promoter_pct,payload) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (
            rec["key"],
            rec.get("name"),
            int(time.time()),
            rec.get("market_cap"),
            rec.get("current_price"),
            rec.get("pe"),
            sh.get("latest_promoter_pct"),
            json.dumps(rec, ensure_ascii=False),
        ),
    )


def all_fundamentals() -> list[dict]:
    out = []
    for row in conn().execute("SELECT payload FROM fundamentals"):
        try:
            out.append(json.loads(row["payload"]))
        except json.JSONDecodeError:
            continue
    return out


def counts() -> dict:
    c = conn()
    d = {r["status"]: r["n"] for r in c.execute("SELECT status, COUNT(*) n FROM companies GROUP BY status")}
    total = sum(d.values())
    return {
        "total": total,
        "ok": d.get("ok", 0),
        "pending": d.get("pending", 0),
        "failed": d.get("failed", 0),
        "parsed": c.execute("SELECT COUNT(*) n FROM fundamentals").fetchone()["n"],
    }


def save_crawl_http(snap: dict) -> None:
    """Publish the crawler process's throttle state for other processes to read."""
    conn().execute(
        "INSERT OR REPLACE INTO crawl_http(id,spacing,requests,throttled,errors,updated_at) "
        "VALUES(1,?,?,?,?,?)",
        (snap.get("spacing_s"), snap.get("requests"), snap.get("throttled"),
         snap.get("errors"), int(time.time())),
    )


def crawl_http() -> dict | None:
    r = conn().execute("SELECT * FROM crawl_http WHERE id=1").fetchone()
    return dict(r) if r else None


def recent_activity(window_s: int = 90) -> int:
    """Companies touched in the last window - lets any process see a live crawl."""
    row = conn().execute(
        "SELECT COUNT(*) n FROM companies WHERE fetched_at > ?",
        (int(time.time()) - window_s,),
    ).fetchone()
    return row["n"]


def log_run(target: int, done: int, failed: int, note: str = "") -> None:
    conn().execute(
        "INSERT INTO runs(started_at,finished_at,target,done,failed,note) VALUES(?,?,?,?,?,?)",
        (int(time.time()), int(time.time()), target, done, failed, note),
    )
