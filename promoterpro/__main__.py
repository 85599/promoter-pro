"""Command line entry point.

    python -m promoterpro universe            # index every listed company
    python -m promoterpro scrape --limit 300  # pull fundamentals pages
    python -m promoterpro report              # top promoter-led ideas in the terminal
    python -m promoterpro serve --port 8757   # dashboard
"""
from __future__ import annotations

import argparse
import sys

from .scraper import runner, store


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="promoterpro")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("universe")

    sc = sub.add_parser("scrape")
    sc.add_argument("--limit", type=int, default=0, help="0 = everything still pending")

    sub.add_parser("report")
    sub.add_parser("rebuild")
    sub.add_parser("reparse")

    sv = sub.add_parser("serve")
    sv.add_argument("--port", type=int, default=8757)

    args = ap.parse_args(argv)

    if args.cmd == "universe":
        n = runner.build_universe()
        print(f"Universe indexed: {n} companies")
        print(store.counts())
        return 0

    if args.cmd == "scrape":
        st = runner.start(limit=args.limit)
        print("scrape started:", st["target"], "queued")
        while runner.state()["running"]:
            s = runner.state()
            sys.stdout.write(
                f"\r{s['done'] + s['failed']}/{s['target']}  ok={s['done']} "
                f"fail={s['failed']}  spacing={s['http']['spacing_s']}s  "
                f"eta={s.get('eta_s')}s   "
            )
            sys.stdout.flush()
            import time

            time.sleep(2)
        print("\nrun finished:", runner.state())
        print(store.counts())
        return 0

    if args.cmd == "reparse":
        n = runner.reparse_all()
        print(f"re-parsed {n} cached pages")
        return 0

    if args.cmd == "rebuild":
        rows = runner.refresh_scores()
        print(f"scored {len(rows)} companies")
        return 0

    if args.cmd == "report":
        rows = [r for r in runner.refresh_scores()
                if (r["metrics"].get("promoter_pct") or 0) >= 60]
        print(f"{len(rows)} stocks with promoter holding above 60%\n")
        print(f"{'#':>3} {'name':<30} {'prom%':>6} {'score':>6} {'pe':>6} {'mcap':>8}  verdict")
        for i, r in enumerate(rows[:40], 1):
            m = r["metrics"]
            print(
                f"{i:>3} {(r['name'] or '')[:29]:<30} "
                f"{m['promoter_pct']:>6.1f} {r['score']:>6.1f} "
                f"{(m['pe'] or 0):>6.1f} {(m['market_cap'] or 0):>8.0f}  {r['verdict']}"
            )
        return 0

    if args.cmd == "serve":
        from .scraper import server

        server.serve(port=args.port)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
