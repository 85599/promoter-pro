"""Turn a screener.in company page into a structured fundamentals record.

Everything the model needs lives in one server-rendered page: header ratios,
P&L, balance sheet, cash flow, ratios and the quarterly/yearly shareholding
tables.  Only the *names* behind each shareholder bucket are lazy-loaded, which
the model does not require.
"""
from __future__ import annotations

import html as htmlmod
import re
from typing import Optional

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_SECTION = "section id=\"{sid}\""


def _text(fragment: str) -> str:
    return _WS.sub(" ", htmlmod.unescape(_TAG.sub(" ", fragment))).strip()


def _num(raw: str) -> Optional[float]:
    """Parse screener's display numbers: '1,234', '12%', '-4', '2.87', '-', ''."""
    s = (raw or "").strip()
    if not s or s in {"-", "--", "—", "NA"}:
        return None
    s = s.replace(",", "").replace("%", "").replace("₹", "").replace("Rs.", "").strip()
    neg = s.startswith("-")
    s = s.lstrip("+-")
    if s.upper().endswith("CR"):
        s = s[:-2].strip()
    m = re.match(r"^\d*\.?\d+", s)
    if not m:
        return None
    val = float(m.group(0))
    return -val if neg else val


def _section(doc: str, sid: str) -> str:
    start = doc.find('id="%s"' % sid)
    if start < 0:
        return ""
    end = doc.find("<section ", start)
    return doc[start:end] if end > 0 else doc[start:]


def _cells(raw_tr: str) -> list[str]:
    return re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", raw_tr, re.S)


def _rows(fragment: str) -> list[list[str]]:
    out = []
    for tr in re.finditer(r"<tr[^>]*>(.*?)</tr>", fragment, re.S):
        cells = [_text(c) for c in _cells(tr.group(1))]
        cells = [re.sub(r"\s+", " ", c).strip() for c in cells]
        if any(cells):
            out.append(cells)
    return out


def _series(fragment: str) -> tuple[list[str], dict[str, list[Optional[float]]]]:
    """Parse one financial table into {row label: values aligned to period headers}."""
    rows = _rows(fragment)
    if not rows:
        return [], {}
    periods = [c for c in rows[0][1:]]
    data: dict[str, list[Optional[float]]] = {}
    for row in rows[1:]:
        label = _clean_label(row[0])
        vals = [_num(v) for v in row[1:]]
        if len(vals) < len(periods):
            vals += [None] * (len(periods) - len(vals))
        data[label] = vals[: len(periods)]
    return periods, data


def _clean_label(label: str) -> str:
    return _WS.sub(" ", label.replace("+", " ")).strip()


def _top_ratios(doc: str) -> dict:
    """Read the header metric list (market cap, price, P/E, book value...)."""
    start = doc.find('id="top-ratios"')
    if start < 0:
        return {}
    block = doc[start : doc.find("</ul>", start)]
    out = {}
    for li in re.finditer(r"<li[^>]*>(.*?)</li>", block, re.S):
        inner = li.group(1)
        name = re.search(r'class="name"[^>]*>(.*?)</span>', inner, re.S)
        if not name:
            continue
        label = _clean_label(_text(name.group(1)))
        tail = inner[name.end():]
        raw = _text(tail)
        if label in {"High / Low", "High/Low"}:
            parts = raw.split("/")
            out["high"] = _num(parts[0]) if parts else None
            out["low"] = _num(parts[1]) if len(parts) > 1 else None
        else:
            out[label] = _num(raw)
    return out


def _shareholding(doc: str) -> dict:
    """Latest promoter / FII / DII / public percentages plus their history."""
    frag = _section(doc, "shareholding")
    yearly = frag.find('<div id="yearly-shp"')
    quarterly = frag[:yearly] if yearly > 0 else frag
    periods, data = _series(quarterly)
    out: dict = {"periods": periods, "yearly": {}}
    for key, row in data.items():
        low = key.lower()
        if low.startswith("promoter"):
            out["promoters"] = row
        elif low.startswith("fii"):
            out["fiis"] = row
        elif low.startswith("dii"):
            out["diis"] = row
        elif low.startswith("public"):
            out["public"] = row
        elif "shareholder" in low:
            out["shareholders"] = row
    if yearly > 0:
        _, ydata = _series(frag[yearly:])
        out["yearly"] = {k: v for k, v in ydata.items() if k}
    return out


def _growth_blocks(doc: str) -> dict:
    """The 'Compounded Sales/Profit Growth / Stock Price CAGR / ROE' summary."""
    frag = _section(doc, "profit-loss")
    out = {}
    current = None
    for row in _rows(frag):
        if len(row) == 1:
            current = _clean_label(row[0])
            if current:
                out.setdefault(current, {})
            continue
        label = _clean_label(row[0])
        if ":" in label and current:
            out[current][label.split(":")[0].strip()] = _num(row[1])
    return out


def _name_and_about(doc: str) -> tuple[str, str]:
    title = re.search(r"<title>\s*(.*?)\s*share price", doc, re.S)
    name = _text(title.group(1)) if title else ""
    about = ""
    m = re.search(r'id="about-company"[^>]*>(.*?)</div>', doc, re.S)
    if m:
        about = _text(m.group(1))
    if not about:
        m = re.search(r"<div class=\"profile-company\".*?<p[^>]*>(.*?)</p>", doc, re.S)
        if m:
            about = _text(m.group(1))
    return name, about


def _insights(doc: str) -> dict:
    out = {"pros": [], "cons": []}
    for kind in ("pros", "cons"):
        for block in re.finditer(r'class="%s"(.*?)</ul>' % kind, doc, re.S):
            out[kind] += [
                _text(li) for li in re.findall(r"<li[^>]*>(.*?)</li>", block.group(1), re.S)
            ]
    return {k: [v for v in vals if v] for k, vals in out.items()}


def _peers(doc: str) -> list[dict]:
    """Peer-comparison table: cheap bulk snapshot of sector multiples."""
    frag = _section(doc, "peers")
    rows = _rows(frag)
    if len(rows) < 2:
        return []
    header = rows[0]
    peers = []
    for row in rows[1:]:
        rec = {}
        for i, cell in enumerate(row):
            key = header[i] if i < len(header) else f"c{i}"
            rec[_clean_label(key)] = cell
        peers.append(rec)
    return peers


def _last(values: Optional[list]) -> Optional[float]:
    if not values:
        return None
    for v in reversed(values):
        if v is not None:
            return v
    return None


def _series_named(data: dict, *candidates: str) -> Optional[list]:
    for key in data:
        for cand in candidates:
            if key.lower().replace(" ", "") == cand.lower().replace(" ", ""):
                return data[key]
    for key in data:
        for cand in candidates:
            if cand.lower().split()[0] in key.lower():
                return data[key]
    return None


def parse_index_constituents(doc: str) -> tuple[str, list[dict]]:
    """Read an index page (Sensex / BSE 500 / sector pack) and its constituent rows.

    Indexes are the cheapest way to learn approximate market caps before the
    per-company crawl, which lets the scraper do large caps first.
    """
    title = re.search(r"<title>\s*(.*?)\s*value", doc, re.S)
    name = _text(title.group(1)) if title else ""
    frag = _section(doc, "constituents")
    trs = re.findall(r"<tr[^>]*>(.*?)</tr>", frag, re.S)
    if len(trs) < 2:
        return name, []
    header = [_clean_label(_text(c)) for c in _cells(trs[0])]
    out = []
    for raw in trs[1:]:
        cells = _cells(raw)
        if len(cells) < 3:
            continue
        link = re.search(r'href="/company/([A-Za-z0-9]+)', raw)
        if not link:
            continue
        rec = {header[i]: _text(c) for i, c in enumerate(cells) if i < len(header)}
        mcap = next((v for k, v in rec.items() if "Mar Cap" in k), None)
        out.append({"key": link.group(1), "name": rec.get("Company", ""), "mcap": _num(mcap)})
    return name, out


def parse_company(doc: str, key: str = "") -> dict:
    """Full structured record for one company page."""
    name, about = _name_and_about(doc)
    header = _top_ratios(doc)

    pl_periods, pl = _series(_section(doc, "profit-loss"))
    bs_periods, bs = _series(_section(doc, "balance-sheet"))
    cf_periods, cf = _series(_section(doc, "cash-flow"))
    rt_periods, rt = _series(_section(doc, "ratios"))

    tables = {
        "profit_loss": {"periods": pl_periods, "rows": pl},
        "balance_sheet": {"periods": bs_periods, "rows": bs},
        "cash_flow": {"periods": cf_periods, "rows": cf},
        "ratios": {"periods": rt_periods, "rows": rt},
    }

    def grab(rows: dict, *names: str) -> Optional[list]:
        return _series_named(rows, *names)

    financials = {
        "sales": grab(pl, "Sales", "Revenue from operations", "Revenue"),
        "operating_profit": grab(pl, "Operating Profit", "EBITDA", "Financing Profit"),
        "opm": grab(pl, "OPM %", "Financing Margin %", "Net Interest Margin %"),
        "other_income": grab(pl, "Other Income"),
        "interest": grab(pl, "Interest"),
        "expenses": grab(pl, "Expenses", "Operating Expenses"),
        "depreciation": grab(pl, "Depreciation"),
        "pbt": grab(pl, "Profit before tax"),
        "tax_pct": grab(pl, "Tax %"),
        "net_profit": grab(pl, "Net Profit"),
        "eps": grab(pl, "EPS in Rs"),
        "dividend_payout": grab(pl, "Dividend Payout %"),
        "equity": grab(bs, "Equity Capital", "Share Capital"),
        "reserves": grab(bs, "Reserves"),
        "borrowings": grab(bs, "Borrowings", "Borrowing"),
        "deposits": grab(bs, "Deposits"),
        "other_liab": grab(bs, "Other Liabilities"),
        "total_liab": grab(bs, "Total Liabilities"),
        "fixed_assets": grab(bs, "Fixed Assets"),
        "cwip": grab(bs, "CWIP"),
        "investments": grab(bs, "Investments"),
        "other_assets": grab(bs, "Other Assets"),
        "total_assets": grab(bs, "Total Assets"),
        "cfo": grab(cf, "Cash from Operating Activity", "Operating Cash Flow"),
        "cfi": grab(cf, "Cash from Investing Activity"),
        "cff": grab(cf, "Cash from Financing Activity"),
        "net_cf": grab(cf, "Net Cash Flow"),
        "fcf": grab(cf, "Free Cash Flow"),
        "cfo_ebitda": grab(cf, "CFO/EBITDA"),
        "roce": grab(rt, "ROCE %", "Return on Capital Employed"),
        "roe": grab(rt, "ROE %", "Return on Equity"),
        "debtor_days": grab(rt, "Debtor Days"),
        "inventory_days": grab(rt, "Inventory Days"),
        "cash_cycle": grab(rt, "Cash Conversion Cycle"),
        "working_cap_days": grab(rt, "Working Capital Days"),
    }

    shp = _shareholding(doc)
    latest_promoter = _last(shp.get("promoters"))
    prev_year_promoter = None
    promoters = shp.get("promoters") or []
    if len(promoters) > 4:
        prev_year_promoter = promoters[-5]

    pledged = None
    m = re.search(r"Unpledged promoter holding[^%]{0,40}?([\d.]+)\s*%", doc)
    if m:
        pledged = max(0.0, 100.0 - float(m.group(1)))

    return {
        "key": key,
        "name": name,
        "about": about,
        "scraped_at": None,
        "header": header,
        "market_cap": header.get("Market Cap"),
        "current_price": header.get("Current Price"),
        "pe": header.get("Stock P/E"),
        "book_value": header.get("Book Value"),
        "dividend_yield": header.get("Dividend Yield"),
        "face_value": header.get("Face Value"),
        "high_52": header.get("high"),
        "low_52": header.get("low"),
        "financials": financials,
        "tables": tables,
        "shareholding": {
            "periods": shp.get("periods", []),
            "yearly": shp.get("yearly", {}),
            "promoters": shp.get("promoters"),
            "fiis": shp.get("fiis"),
            "diis": shp.get("diis"),
            "public": shp.get("public"),
            "shareholders": shp.get("shareholders"),
            "latest_promoter_pct": latest_promoter,
            "promoter_change_1y": (
                round(latest_promoter - prev_year_promoter, 2)
                if latest_promoter is not None and prev_year_promoter is not None
                else None
            ),
            "pledged_pct": pledged,
        },
        "growth": _growth_blocks(doc),
        "insights": _insights(doc),
        "peers": _peers(doc),
    }
