"""Central configuration for the Promoter Pro fundamentals engine."""
from __future__ import annotations
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "promoterpro.sqlite3")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

BASE_URL = "https://www.screener.in"
COMPANY_URL = BASE_URL + "/company/{key}/consolidated/"
SITEMAP_INDEX = BASE_URL + "/sitemap.xml"
# One public request that labels the NSE-listed set, so search matches names
# before the crawl has read those pages.  BSE-only companies get their name
# from their own page instead.
NSE_EQUITY_LIST = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"

# --- Politeness / rate control -------------------------------------------
# screener.in answers bursts with HTTP 429. The client below keeps a global
# token bucket and doubles its spacing on every 429 until it stabilises.
MIN_SPACING_S = 0.55          # fastest allowed gap between two requests
MAX_SPACING_S = 12.0          # ceiling after repeated 429s
SPACING_ON_429 = 4.0          # multiplicative penalty per throttle event
MAX_WORKERS = 2               # in-flight requests; >2 reliably triggers 429
REQUEST_TIMEOUT_S = 30
MAX_ATTEMPTS = 4

# --- Scrape scope ---------------------------------------------------------
UNIVERSE_LIMIT = 0            # 0 = every company in the sitemap
STALE_AFTER_DAYS = 7          # re-fetch cached pages older than this

# --- Investment model thresholds -----------------------------------------
PROMOTER_MIN_DEFAULT = 60.0   # the user's headline filter
MIN_MARKET_CAP_CR = 50.0      # ignore illiquid micro/penny counters by default
