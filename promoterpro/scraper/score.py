"""Fundamental scoring model.

Six weighted pillars, hard red-flag gates, and a clearly-labelled modelled fair
value range.  Every pillar returns its own sub-score plus human-readable reasons
so the UI can show *why* a stock got its verdict instead of a bare number.

This is a screening aid built on public reported fundamentals.  It is not
investment advice and it does not know about news, governance events beyond
pledging, or order books.
"""
from __future__ import annotations

from typing import Optional

WEIGHTS = {
    "promoter": 18,
    "balance_sheet": 18,
    "profitability": 20,
    "growth": 17,
    "cash": 12,
    "valuation": 15,
}

# Banks, NBFCs and insurers have no EBITDA, no debt/equity and no free cash flow.
# Scoring them on the industrial template reads a Rs 4 lakh-crore liability book
# as "almost debt free", so they get their own pillars.
WEIGHTS_FINANCIAL = {
    "promoter": 20,
    "capital": 18,
    "quality": 20,
    "profitability": 18,
    "growth": 12,
    "valuation": 12,
}

# Cost of equity used to justify a bank's price-to-book.
COE_FINANCIAL = 11.5

# Rough 10-year Government of India bond yield used as the risk-free hurdle.
RISK_FREE_YIELD = 6.6

# Equity risk premium added to the G-Sec to get the default required return.
EQUITY_RISK_PREMIUM = 3.0

# Quality and growth can lower the hurdle, but never below this: no listed
# business is safe enough to be capitalised at a bond-like yield.
REQUIRED_RETURN_FLOOR = 8.0


def _last(series: Optional[list]) -> Optional[float]:
    if not series:
        return None
    for v in reversed(series):
        if v is not None:
            return v
    return None


def _at(series: Optional[list], back: int) -> Optional[float]:
    """Value `back` periods from the most recent non-null entry."""
    if not series:
        return None
    vals = [v for v in series if v is not None]
    if len(vals) <= back:
        return None
    return vals[-1 - back]


def _mean(series: Optional[list], n: int = 3) -> Optional[float]:
    if not series:
        return None
    vals = [v for v in series if v is not None][-n:]
    return sum(vals) / len(vals) if vals else None


def _cagr(start: Optional[float], end: Optional[float], years: int) -> Optional[float]:
    if not start or not end or start <= 0 or end <= 0 or years <= 0:
        return None
    return round(((end / start) ** (1 / years) - 1) * 100, 2)


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _scale(value, bands, scores):
    """Piecewise-linear map of `value` onto 0-100.

    `bands[i]` is an input range and `scores[i]` the matching output range; the
    two ends are clamped so out-of-range readings saturate rather than explode.
    """
    if value is None:
        return None
    if value <= bands[0][0]:
        return scores[0][0]
    for (lo, hi), (slo, shi) in zip(bands, scores):
        if value <= hi:
            return _clamp(slo + (value - lo) / (hi - lo) * (shi - slo)) if hi > lo else shi
    return scores[-1][1]


def detect_track(rec: dict) -> str:
    """'financial' when the balance sheet is a deposit / lending book."""
    bs = (rec.get("tables") or {}).get("balance_sheet", {}).get("rows", {})
    pl = (rec.get("tables") or {}).get("profit_loss", {}).get("rows", {})
    if any(k.lower().startswith("deposits") for k in bs):
        return "financial"
    if any(k.lower().startswith("financing profit") for k in pl):
        return "financial"
    return "industrial"


def derive(rec: dict) -> dict:
    """Flatten the scraped record into the single-period metrics the model uses."""
    f = rec.get("financials") or {}
    sh = rec.get("shareholding") or {}
    growth = rec.get("growth") or {}
    price = rec.get("current_price")
    mcap = rec.get("market_cap")

    # P&L series end with a TTM column; the one before it is the last full FY.
    sales_ttm = _last(f.get("sales"))
    np_ttm = _last(f.get("net_profit"))
    sales_fy = _at(f.get("sales"), 1)
    np_fy = _at(f.get("net_profit"), 1)

    equity = _last(f.get("equity")) or 0
    reserves = _last(f.get("reserves")) or 0
    borrowings = _last(f.get("borrowings")) or 0
    net_worth = equity + reserves
    ebitda = _last(f.get("operating_profit"))
    interest = _last(f.get("interest"))

    d = {
        "price": price,
        "market_cap": mcap,
        "pe": rec.get("pe"),
        "book_value": rec.get("book_value"),
        "book_value": rec.get("book_value"),
        "dividend_yield": rec.get("dividend_yield"),
        "promoter_pct": sh.get("latest_promoter_pct"),
        "promoter_chg_1y": sh.get("promoter_change_1y"),
        "promoter_chg_3y": None,
        "pledged_pct": sh.get("pledged_pct"),
        "fii_pct": _last(sh.get("fiis")),
        "dii_pct": _last(sh.get("diis")),
        "public_pct": _last(sh.get("public")),
        "net_worth": net_worth or None,
        "borrowings": borrowings,
        "debt_equity": round(borrowings / net_worth, 3) if net_worth else None,
        "interest_coverage": round(ebitda / interest, 2) if ebitda is not None and interest else None,
        "roce": _last(f.get("roce")),
        "roce_3y": _mean(f.get("roce"), 3),
        "roe": _last(f.get("roe")),
        "opm": _last(f.get("opm")),
        "opm_3y_ago": _at(f.get("opm"), 3),
        "cfo": _last(f.get("cfo")),
        "cfo_3y": _mean(f.get("cfo"), 3),
        "fcf": _last(f.get("fcf")),
        "fcf_3y": _mean(f.get("fcf"), 3),
        "cfo_ebitda": _last(f.get("cfo_ebitda")),
        "debtor_days": _last(f.get("debtor_days")),
        "debtor_days_3y_ago": _at(f.get("debtor_days"), 3),
        "cash_cycle": _last(f.get("cash_cycle")),
        "sales_ttm": sales_ttm,
        "np_ttm": np_ttm,
        "sales_fy": sales_fy,
        "np_fy": np_fy,
        "eps_ttm": _last(f.get("eps")),
        "dividend_payout": _last(f.get("dividend_payout")),
        "equity_5y_ago": _at(f.get("equity"), 5),
        "cagr": {
            "sales_10": (growth.get("Compounded Sales Growth") or {}).get("10 Years"),
            "sales_5": (growth.get("Compounded Sales Growth") or {}).get("5 Years"),
            "sales_3": (growth.get("Compounded Sales Growth") or {}).get("3 Years"),
            "sales_ttm": (growth.get("Compounded Sales Growth") or {}).get("TTM"),
            "profit_10": (growth.get("Compounded Profit Growth") or {}).get("10 Years"),
            "profit_5": (growth.get("Compounded Profit Growth") or {}).get("5 Years"),
            "profit_3": (growth.get("Compounded Profit Growth") or {}).get("3 Years"),
            "profit_ttm": (growth.get("Compounded Profit Growth") or {}).get("TTM"),
            "price_10": (growth.get("Stock Price CAGR") or {}).get("10 Years"),
            "price_5": (growth.get("Stock Price CAGR") or {}).get("5 Years"),
            "price_3": (growth.get("Stock Price CAGR") or {}).get("3 Years"),
            "roe_10": (growth.get("Return on Equity") or {}).get("10 Years"),
            "roe_last": (growth.get("Return on Equity") or {}).get("Last Year"),
        },
    }

    yearly_promoters = (sh.get("yearly") or {}).get("Promoters")
    if yearly_promoters:
        clean = [v for v in yearly_promoters if v is not None]
        if len(clean) >= 4:
            d["promoter_chg_3y"] = round(clean[-1] - clean[-4], 2)

    # Through-cycle readings.  Capitalising a single peak year, or trusting a
    # CAGR that starts from a loss, is what turns a cyclical into a false BUY.
    def _tail(series: Optional[list], n: int) -> list[float]:
        vals = [v for v in (series or []) if v is not None]
        return vals[-n:]

    np_hist = _tail(f.get("net_profit"), 9)
    roce_hist = _tail(f.get("roce"), 9)
    eps_hist = _tail(f.get("eps"), 6)
    d["eps_normalised"] = round(sum(eps_hist[-3:]) / len(eps_hist[-3:]), 2) if eps_hist else None
    d["np_mean_5"] = _mean(f.get("net_profit"), 5)
    d["roce_mean_10"] = round(sum(roce_hist) / len(roce_hist), 1) if roce_hist else None
    d["roce_min_10"] = min(roce_hist) if roce_hist else None
    d["roce_negative_years"] = sum(1 for v in roce_hist if v < 0)
    d["loss_years_9"] = sum(1 for v in np_hist if v is not None and v < 0)
    if len(np_hist) >= 6:
        mean = sum(np_hist) / len(np_hist)
        spread = max(np_hist) - min(np_hist)
        d["earnings_range_pct"] = round(spread / abs(mean) * 100, 1) if mean else None
        d["peak_vs_mean"] = round(np_hist[-1] / d["np_mean_5"], 2) if d["np_mean_5"] and d["np_mean_5"] > 0 else None
    else:
        d["earnings_range_pct"] = None
        d["peak_vs_mean"] = None

    # A CAGR measured from a loss-making, depressed, or near-zero base is
    # arithmetic, not growth - the base year has to look like a normal year.
    def _clean_cagr(series: Optional[list], years: int) -> Optional[float]:
        vals = [v for v in (series or []) if v is not None]
        if len(vals) <= years:
            return None
        window = vals[-years - 1:]
        if min(window) <= 0 or window[0] <= 0:
            return None
        healthy = [v for v in vals if v > 0]
        typical = sum(healthy[-10:]) / len(healthy[-10:])
        if window[0] < 0.5 * typical:
            return None
        return _cagr(window[0], window[-1], years)

    g = d["cagr"]
    g["profit_5"] = _clean_cagr(f.get("net_profit"), 5)
    g["profit_3"] = _clean_cagr(f.get("net_profit"), 3)
    g["sales_5"] = _clean_cagr(f.get("sales"), 5) or g.get("sales_5")
    g["sales_3"] = _clean_cagr(f.get("sales"), 3) or g.get("sales_3")
    g["profit_10"] = _clean_cagr(f.get("net_profit"), 10)
    if g["profit_5"] is None and d["loss_years_9"]:
        risks_note = f"Profit CAGR is not measurable - {d['loss_years_9']} loss years in the last nine."
    else:
        risks_note = ""
    d["growth_note"] = risks_note

    # Financial-only readings.
    deposits = _last(f.get("deposits"))
    total_assets = _last(f.get("total_assets"))
    d["track"] = detect_track(rec)
    d["deposits"] = deposits
    d["deposits_3y_ago"] = _at(f.get("deposits"), 3)
    d["total_assets"] = total_assets
    d["roe_series"] = [v for v in (f.get("roe") or []) if v is not None]
    d["pbt"] = _last(f.get("pbt"))
    d["revenue_ttm"] = sales_ttm
    d["price_to_book"] = round(mcap / net_worth, 2) if mcap and net_worth else None
    d["equity_to_assets"] = (
        round(net_worth / total_assets * 100, 2) if net_worth and total_assets else None
    )
    # Year-on-year profit swings are the closest visible proxy for credit stress.
    np_series = [v for v in (f.get("net_profit") or []) if v is not None]
    drops = [
        round((np_series[i] / np_series[i - 1] - 1) * 100, 1)
        for i in range(max(1, len(np_series) - 5), len(np_series))
        if np_series[i - 1]
    ]
    d["worst_profit_drop_5y"] = min(drops) if drops else None
    d["deposit_cagr_5"] = _cagr(_at(f.get("deposits"), 5), deposits, 5)

    d["earnings_yield"] = (
        round(100 / d["pe"], 2) if d["pe"] and d["pe"] > 0 else None
    )
    d["peg"] = (
        round(d["pe"] / g["profit_5"], 2)
        if d["pe"] and d["pe"] > 0 and g["profit_5"] and g["profit_5"] > 0
        else None
    )
    # What today's price pays for each year of history. One trailing P/E hides
    # whether the multiple is compressing because earnings grew or because the
    # price fell, so the ladder is shown per fiscal year.
    periods = [p for p in ((rec.get("tables") or {}).get("profit_loss") or {}).get("periods") or []
               if p != "TTM"]
    eps_all = [v for v in (f.get("eps") or []) if v]
    ladder = []
    if price and eps_all and periods:
        for label, e in zip(periods[-len(eps_all):], eps_all):
            ladder.append({"period": label, "eps": e, "pe_at_price": round(price / e, 1)})
    d["eps_ladder"] = ladder[-10:]
    return d


def _pillar_promoter(d: dict, reasons: list, risks: list) -> Optional[float]:
    p = d["promoter_pct"]
    if p is None:
        risks.append("No promoter holding data published.")
        return None
    score = _scale(p, [(0, 50), (50, 65), (65, 80), (80, 100)], [(0, 45), (45, 82), (82, 92), (92, 70)])
    if score is None:
        return None
    reasons.append(f"Promoters hold {p:.1f}% - {'strong' if p >= 70 else 'adequate'} skin in the game.")
    ch = d["promoter_chg_1y"]
    if ch is not None:
        if ch <= -1:
            score -= 22
            risks.append(f"Promoter holding fell {abs(ch):.2f}% in the last year.")
        elif ch >= 1:
            score += 8
            reasons.append(f"Promoters increased stake by {ch:.2f}% over the last year.")
    ch3 = d["promoter_chg_3y"]
    if ch3 is not None and ch3 <= -3:
        score -= 10
        risks.append(f"Promoter holding down {abs(ch3):.2f}% over three years.")
    if d.get("pledged_pct"):
        score -= 40
    return _clamp(score)


def _pillar_balance_sheet(d: dict, rec: dict, reasons: list, risks: list) -> Optional[float]:
    de = d["debt_equity"]
    if de is None:
        return None
    score = _scale(de, [(0, 0.05), (0.05, 0.3), (0.3, 0.7), (0.7, 1.5), (1.5, 20)],
                   [(100, 100), (100, 82), (82, 60), (60, 30), (30, 0)])
    if score is None:
        return None
    if de < 0.05:
        reasons.append("Almost debt free - balance sheet carries no meaningful leverage.")
    elif de < 0.3:
        reasons.append(f"Comfortable leverage at {de:.2f}x debt/equity.")
    else:
        risks.append(f"Leverage is high at {de:.2f}x debt/equity.")

    ic = d["interest_coverage"]
    if ic is not None:
        if ic >= 6:
            score = _clamp(score + 10)
            reasons.append(f"EBITDA covers interest {ic:.1f}x.")
        elif ic < 2.5:
            score = _clamp(score - 25)
            risks.append(f"Weak interest coverage at {ic:.1f}x.")
        if ic < 1.5 and d["borrowings"] > 0:
            score = _clamp(score - 20)
            risks.append("Operating profit barely services the interest bill.")

    borrowings = rec.get("financials", {}).get("borrowings") or []
    now, then = _last(borrowings), _at(borrowings, 3)
    if now is not None and then is not None and then > 0:
        if now < then * 0.85:
            score = _clamp(score + 10)
            reasons.append(f"Borrowings cut {100 - now / then * 100:.0f}% over three years.")
        elif now > then * 1.6 and de > 0.5:
            score = _clamp(score - 12)
            risks.append("Debt is building up faster than the net worth behind it.")

    net_worth = d["net_worth"] or 0
    if net_worth and d["market_cap"] and d["market_cap"] / net_worth < 1:
        reasons.append(f"Trades below book value ({d['market_cap'] / net_worth:.2f}x P/B).")
    return _clamp(score)


def _pillar_profitability(d: dict, reasons: list, risks: list) -> Optional[float]:
    roce = d["roce"]
    if roce is None:
        return None
    # Judge a cyclical on what it earns across a whole cycle, not the good year.
    cycle = d["roce_mean_10"]
    basis = min(roce, cycle) if cycle is not None else roce
    if cycle is not None and cycle < roce - 6:
        reasons.append(f"ROCE {roce:.0f}% now, but only {cycle:.0f}% averaged over ten years.")
    score = _scale(basis, [(0, 8), (8, 13), (13, 20), (20, 30), (30, 200)],
                   [(10, 10), (10, 45), (45, 78), (78, 95), (95, 82)])
    if score is None:
        return None
    reasons.append(f"Returns {basis:.0f}% on capital employed on a through-cycle basis.")
    if d["loss_years_9"]:
        score = _clamp(score - 9 * d["loss_years_9"])
        risks.append(f"{d['loss_years_9']} loss year(s) in the last nine - the earnings stream is not dependable.")
    if d["roce_negative_years"]:
        score = _clamp(score - 6)
    if d["roce_3y"] is not None and d["roce_3y"] > roce:
        score = _clamp(score - 8)
        risks.append("Recent three-year returns are running below the latest year - momentum is fading.")
    elif d["roce_3y"] is not None and d["roce_3y"] < roce:
        score = _clamp(score + 6)
        reasons.append("Returns are trending up over three years.")
    opm, opm_prev = d["opm"], d["opm_3y_ago"]
    if opm is not None and opm_prev is not None:
        if opm - opm_prev >= 2:
            score = _clamp(score + 10)
            reasons.append(f"Margins expanded {opm - opm_prev:.1f} pts in three years.")
        elif opm - opm_prev <= -2:
            score = _clamp(score - 14)
            risks.append(f"Margins contracted {abs(opm - opm_prev):.1f} pts in three years.")
    dd, dd_prev = d["debtor_days"], d["debtor_days_3y_ago"]
    if dd is not None and dd_prev:
        if dd / dd_prev > 1.35 and dd - dd_prev > 20:
            score = _clamp(score - 16)
            risks.append(f"Collection days stretched from {dd_prev:.0f} to {dd:.0f} - receivables risk.")
    return _clamp(score)


def _pillar_growth(d: dict, reasons: list, risks: list) -> Optional[float]:
    g = d["cagr"]
    s5, p5 = g.get("sales_5"), g.get("profit_5")
    if s5 is None and p5 is None:
        return None

    parts = []
    if s5 is not None:
        parts.append(_clamp(45 + (s5 - 6) * 3.0))
        if s5 >= 8:
            reasons.append(f"5-year sales CAGR {s5:.1f}%.")
        else:
            risks.append(f"Slow 5-year sales CAGR of {s5:.1f}%.")
    if p5 is not None:
        parts.append(_clamp(45 + p5 * 2.4))
        if p5 >= 12:
            reasons.append(f"5-year profit CAGR {p5:.1f}%.")
        elif p5 < 0:
            risks.append(f"Profits shrank {-p5:.1f}% a year over five years.")
    score = sum(parts) / len(parts)

    if g.get("profit_3") is not None and g["profit_3"] > 20 and (p5 or 0) > 5:
        score = _clamp(score + 8)
        reasons.append(f"Earnings accelerated ({g['profit_3']:.0f}% 3-year CAGR).")
    if g.get("sales_ttm") is not None and g["sales_ttm"] < -5:
        score = _clamp(score - 12)
        risks.append(f"Revenue is shrinking ({g['sales_ttm']:.1f}% over the last twelve months).")
    return _clamp(score)


def _pillar_cash(d: dict, reasons: list, risks: list) -> Optional[float]:
    fcf, np = d["fcf"], d["np_ttm"]
    if fcf is None:
        return None
    score = 50.0
    if fcf > 0:
        score = 72.0
        reasons.append("Business generates positive free cash flow.")
    else:
        score = 24.0
        risks.append("Free cash flow is negative - growth is being funded, not earned.")
    if d["fcf_3y"] is not None and d["fcf_3y"] > 0:
        score += 10
    if np and np > 0:
        conv = (d["cfo"] or 0) / np
        if conv >= 1.0:
            score += 14
            reasons.append(f"Operating cash is {conv:.2f}x net profit - profits are cash-backed.")
        elif conv < 0.55:
            score -= 20
            risks.append(f"Only {conv:.2f}x of net profit converted to operating cash.")
    ce = d["cfo_ebitda"]
    if ce is not None and ce < 60:
        score -= 12
        risks.append(f"CFO/EBITDA at {ce:.0f}% signals weak accrual quality.")
    return _clamp(score)


def _pillar_valuation(d: dict, reasons: list, risks: list) -> Optional[float]:
    pe = d["pe"]
    if not pe or pe <= 0:
        risks.append("No positive earnings to value the business on.")
        return 20.0
    ey = d["earnings_yield"]
    if ey > 40:
        # Nothing durable earns 40% of its market cap a year; the price or this
        # year's profit is an artefact, so there is no valuation opinion to make.
        risks.append(
            f"Earnings yield of {ey:.0f}% is not achievable from a going concern - "
            "the quote or the reported profit is a data artefact."
        )
        return None
    score = _scale(ey, [(0, 3), (3, 5), (5, 7), (7, 10), (10, 100)],
                   [(8, 8), (8, 32), (32, 58), (58, 86), (86, 96)])
    if score is None:
        return None
    if ey < RISK_FREE_YIELD:
        risks.append(f"Earnings yield {ey:.1f}% is below the {RISK_FREE_YIELD}% 10-year G-Sec hurdle.")
    else:
        reasons.append(f"Earnings yield {ey:.1f}% clears the {RISK_FREE_YIELD}% risk-free hurdle.")
    roce = d["roce"]
    fv = fair_value(d)
    if fv["available"]:
        gap = fv["gap_to_mid_pct"]
        if gap is not None:
            score = _clamp(score + max(-25, min(18, gap * 0.45)))
            if gap <= -20:
                risks.append(
                    f"Price sits {abs(gap):.0f}% above the modelled fair value of "
                    f"Rs {fv['mid']:.0f} ({fv['justified_pe']}x earnings)."
                )
            elif gap >= 15:
                reasons.append(
                    f"Price sits {gap:.0f}% below the modelled fair value of "
                    f"Rs {fv['mid']:.0f} ({fv['justified_pe']}x earnings)."
                )
    elif roce and pe:
        justified = max(4.0, roce * 0.9)
        if pe > justified * 1.6:
            score = _clamp(score - 20)
            risks.append(f"P/E {pe:.0f} looks rich against a {roce:.0f}% ROCE.")
        elif pe <= justified:
            score = _clamp(score + 14)
            reasons.append(f"P/E {pe:.0f} is at or below the multiple its returns justify.")
    if d["peak_vs_mean"] is not None and d["peak_vs_mean"] > 1.5:
        score = _clamp(score - 22)
        risks.append(
            f"Earnings are {d['peak_vs_mean']:.1f}x their five-year average; the cheap-looking "
            "multiple is being measured against a peak year."
        )
    peg = d["peg"]
    if peg is not None:
        if peg < 1.0:
            score = _clamp(score + 12)
            reasons.append(f"PEG {peg:.2f} - growth is being bought cheaply.")
        elif peg > 2.0:
            score = _clamp(score - 14)
            risks.append(f"PEG {peg:.2f} prices in a lot of future growth.")
    dy = d["dividend_yield"]
    if dy and dy >= 2:
        score = _clamp(score + 6)
        reasons.append(f"Dividend yield {dy:.1f}% adds a cash floor.")
    return _clamp(score)


def _pillar_capital(d: dict, rec: dict, reasons: list, risks: list) -> Optional[float]:
    """Capital cushion of a lending book: equity against assets, and its trend."""
    ea = d["equity_to_assets"]
    if ea is None:
        return None
    score = _scale(ea, [(0, 4), (4, 7), (7, 11), (11, 100)], [(15, 15), (15, 55), (55, 82), (82, 92)])
    if score is None:
        return None
    reasons.append(f"Equity funds {ea:.1f}% of the balance sheet.")
    if ea < 5:
        risks.append("Thin capital layer for a deposit-funded book.")
    reserves = [v for v in (rec["financials"].get("reserves") or []) if v is not None]
    deposits = [v for v in (rec["financials"].get("deposits") or []) if v is not None]
    if len(reserves) >= 4 and len(deposits) >= 4:
        rg = reserves[-1] / reserves[-4] - 1
        dg = deposits[-1] / deposits[-4] - 1
        if rg > dg + 0.05:
            score = _clamp(score + 12)
            reasons.append("Reserves are compounding faster than the deposit book.")
        elif dg > rg + 0.15:
            score = _clamp(score - 14)
            risks.append("Deposits are growing much faster than capital - watch for a dilutive raise.")
    return _clamp(score)


def _pillar_quality(d: dict, reasons: list, risks: list) -> Optional[float]:
    """Asset quality read from what the public accounts actually disclose."""
    drops = d["worst_profit_drop_5y"]
    if drops is None:
        return None
    score = _scale(drops, [(-100, -25), (-25, -8), (-8, 3), (3, 100)], [(5, 5), (5, 45), (45, 72), (72, 88)])
    if drops <= -25:
        risks.append(f"Earnings fell {abs(drops):.0f}% in one of the last five years - provisioning stress shows up here first.")
    elif drops >= 0:
        reasons.append("No profit contraction in five years of reporting.")
    margin = d["opm"]
    if margin is not None:
        if margin >= 40:
            score = _clamp(score + 8)
        elif margin < 20:
            score = _clamp(score - 10)
            risks.append(f"Spread income is only {margin:.0f}% of total revenue.")
    tax = d["np_ttm"]
    pbt = d["pbt"]
    if tax and pbt and pbt > 0 and tax / pbt > 1.0:
        score = _clamp(score - 12)
        risks.append("Reported profit exceeds pre-tax profit in the latest period - check the provisions.")
    return _clamp(score)


def _pillar_profitability_fin(d: dict, reasons: list, risks: list) -> Optional[float]:
    series = d["roe_series"]
    if not series:
        return None
    roe = series[-1]
    avg = sum(series[-3:]) / len(series[-3:])
    score = _scale(avg, [(0, 8), (8, 12), (12, 16), (16, 100)], [(10, 10), (10, 48), (48, 80), (80, 92)])
    if score is None:
        return None
    reasons.append(f"Three-year average ROE {avg:.1f}%, latest {roe:.1f}%.")
    if avg < COE_FINANCIAL:
        score = _clamp(score - 14)
        risks.append(f"ROE of {avg:.1f}% is below the ~{COE_FINANCIAL}% cost of equity for a bank.")
    if len(series) >= 4 and series[-1] > series[-4]:
        score = _clamp(score + 8)
        reasons.append("Returns on equity are recovering.")
    return _clamp(score)


def _pillar_growth_fin(d: dict, reasons: list, risks: list) -> Optional[float]:
    dg, pg = d["deposit_cagr_5"], d["cagr"].get("profit_5")
    if dg is None and pg is None:
        return None
    parts = []
    if dg is not None:
        parts.append(_clamp(45 + (dg - 8) * 3.2))
        reasons.append(f"Deposits compounded at {dg:.1f}% a year over five years.")
    if pg is not None:
        parts.append(_clamp(45 + pg * 2.2))
        if pg >= 12:
            reasons.append(f"Profit CAGR {pg:.1f}%.")
        elif pg < 0:
            risks.append(f"Profits shrinking {-pg:.1f}% a year.")
    return _clamp(sum(parts) / len(parts))


def _justified_pb(roe_pct: float, growth5: Optional[float]) -> float:
    """Gordon-growth price to book, clamped so the ROE-COE spread cannot explode.

    Left unclamped this divides by a near-zero denominator and prints absurd
    justified multiples for a mid-tier lender.
    """
    roe = roe_pct / 100
    g = max(0.0, min(0.06, (growth5 or 0) / 100))
    coe = COE_FINANCIAL / 100
    return max(0.5, min(2.8, (roe - g) / max(0.045, coe - g)))


def _pillar_valuation_fin(d: dict, reasons: list, risks: list) -> Optional[float]:
    """Price-to-book justified by the spread between ROE and cost of equity."""
    pb = d["price_to_book"]
    series = d["roe_series"]
    if not pb or not series:
        return None
    roe = sum(series[-3:]) / len(series[-3:])
    justified = _justified_pb(roe, d["cagr"].get("profit_5"))
    gap = (justified / pb - 1) * 100
    score = _scale(gap, [(-60, -20), (-20, 0), (0, 25), (25, 200)], [(8, 8), (8, 42), (42, 74), (74, 92)])
    if score is None:
        return None
    reasons.append(f"P/B {pb:.2f}x against a justified {justified:.2f}x for a {roe:.1f}% ROE book.")
    if gap < -20:
        risks.append(f"Trading {abs(gap):.0f}% above the price-to-book its returns justify.")
    elif gap > 25:
        reasons.append(f"Trading {gap:.0f}% below the price-to-book its returns justify.")
    dy = d["dividend_yield"]
    if dy and dy >= 2.5:
        score = _clamp(score + 8)
        reasons.append(f"Dividend yield {dy:.1f}% supports the floor.")
    d["_fin_justified_pb"] = round(justified, 2)
    d["_fin_pb_gap"] = round(gap, 1)
    return _clamp(score)


def _red_flags(d: dict, rec: dict) -> list[dict]:
    """Hard gates. `severe` caps the verdict regardless of score."""
    flags = []
    fin = d["track"] == "financial"

    if d["promoter_pct"] is None:
        flags.append({"flag": "promoter_unknown", "severe": True,
                      "text": "Promoter holding could not be read - filter cannot be verified."})
    elif d["promoter_pct"] < 60:
        flags.append({"flag": "promoter_below_gate", "severe": True,
                      "text": f"Promoter holding {d['promoter_pct']:.1f}% is under the 60% gate."})
    if d["pledged_pct"]:
        flags.append({"flag": "pledged", "severe": d["pledged_pct"] > 1,
                      "text": f"{d['pledged_pct']:.1f}% of promoter holding is pledged."})
    if d["np_ttm"] is not None and d["np_ttm"] <= 0:
        flags.append({"flag": "loss_making", "severe": True,
                      "text": "Loss-making on a trailing-twelve-month basis."})
    if d["promoter_chg_1y"] is not None and d["promoter_chg_1y"] <= -3:
        flags.append({"flag": "promoter_selling", "severe": True,
                      "text": f"Promoters sold {abs(d['promoter_chg_1y']):.2f}% in a year."})
    if d["promoter_chg_3y"] is not None and d["promoter_chg_3y"] <= -4:
        flags.append({"flag": "promoter_selling_3y", "severe": True,
                      "text": f"Promoters down {abs(d['promoter_chg_3y']):.2f}% over three years."})
    if d["market_cap"] is not None and d["market_cap"] < 100:
        flags.append({"flag": "micro_cap", "severe": False,
                      "text": f"Market cap of Rs {d['market_cap']:.0f} Cr - liquidity and data risk."})
    # A sub-1.5x P/E is never a bargain. It means the quote is stale or the year
    # carried a one-off gain, and neither is a repeatable earnings stream.
    if d["pe"] is not None and 0 < d["pe"] < 1.5:
        flags.append({"flag": "implausible_multiple", "severe": True,
                      "text": f"Trailing P/E of {d['pe']:.2f}x implies a stale quote or a one-off "
                              "gain - the earnings cannot be capitalised."})

    if fin:
        # For a lender, 'Interest' is money paid to depositors and borrowings are
        # the product, not a distress signal - judge the capital layer instead.
        if d["equity_to_assets"] is not None and d["equity_to_assets"] < 4:
            flags.append({"flag": "thin_capital", "severe": True,
                          "text": f"Equity funds only {d['equity_to_assets']:.1f}% of a levered lending book."})
        if d["worst_profit_drop_5y"] is not None and d["worst_profit_drop_5y"] <= -45:
            flags.append({"flag": "earnings_collapse", "severe": True,
                          "text": f"Earnings fell {abs(d['worst_profit_drop_5y']):.0f}% in a single year."})
        if d["deposits"] is not None and d["deposits_3y_ago"] and d["deposits"] < d["deposits_3y_ago"]:
            flags.append({"flag": "deposit_shrink", "severe": True,
                          "text": "Deposit book has shrunk over three years - funding is leaving."})
        roe = d["roe_series"]
        if roe:
            avg = sum(roe[-3:]) / len(roe[-3:])
            if avg < 8:
                flags.append({"flag": "roe_below_coe", "severe": False,
                              "text": f"ROE {avg:.1f}% sits under the ~{COE_FINANCIAL}% cost of equity."})
    else:
        if d["interest_coverage"] is not None and d["interest_coverage"] < 1.5 and d["borrowings"] > 0:
            flags.append({"flag": "interest_cover", "severe": True,
                          "text": f"Interest coverage {d['interest_coverage']:.1f}x - solvency risk."})
        if d["debt_equity"] is not None and d["debt_equity"] > 2:
            flags.append({"flag": "overlevered", "severe": True,
                          "text": f"Debt/equity {d['debt_equity']:.2f}x is aggressive for a promoter-led book."})
        if d["roce"] is not None and d["roce"] < 8:
            flags.append({"flag": "low_roce", "severe": False,
                          "text": f"ROCE {d['roce']:.0f}% destroys value after the cost of capital."})
        if d["loss_years_9"] >= 2:
            flags.append({"flag": "chronic_losses", "severe": True,
                          "text": f"{d['loss_years_9']} loss years in the last nine - a cyclical, not a compounder."})
        if d["peak_vs_mean"] is not None and d["peak_vs_mean"] > 1.8:
            flags.append({"flag": "peak_of_cycle", "severe": d["peak_vs_mean"] > 2.2,
                          "text": f"Latest profit is {d['peak_vs_mean']:.1f}x its five-year average - this looks like a cycle peak."})
    return flags


def fair_value(d: dict) -> dict:
    """Modelled value range from a required-return (earnings-yield) anchor.

    A quality business earns a lower required return than the market default, so
    its justified multiple is EPS x 100/required_yield.  The band around the
    anchor reflects estimation error, not a price target.
    """
    eps, roce = d["eps_normalised"] or d["eps_ttm"], d["roce"]

    if d["track"] == "financial":
        # A lending book is worth book value times the P/B its ROE justifies.
        bvps, series = d["book_value"], d["roe_series"]
        if not bvps or bvps <= 0 or not series:
            return {"available": False, "method": "No book value or ROE history to value a financial."}
        roe = sum(series[-3:]) / len(series[-3:])
        justified_pb = _justified_pb(roe, d["cagr"].get("profit_5"))
        g_disp = max(0.0, min(6.0, d["cagr"].get("profit_5") or 0.0))
        mid = bvps * justified_pb
        price = d["price"] or 0
        return {
            "available": True,
            "method": (
                f"Gordon form on a {roe:.1f}% return on equity against a "
                f"{COE_FINANCIAL}% cost of equity. A valuation yardstick, not a target."
            ),
            "required_return_pct": COE_FINANCIAL,
            "justified_pe": round(justified_pb, 2),
            "basis": "price-to-book",
            "low": round(mid * 0.7, 2),
            "mid": round(mid, 2),
            "high": round(mid * 1.4, 2),
            "gap_to_mid_pct": round((mid / price - 1) * 100, 1) if price else None,
            "build": {
                "kind": "price_to_book",
                "rows": [
                    {"label": "Book value per share", "value": f"₹{bvps:.2f}"},
                    {"label": "3-year average ROE", "value": f"{roe:.1f}%"},
                    {"label": "Cost of equity assumed", "value": f"{COE_FINANCIAL:.1f}%"},
                    {"label": "Perpetual growth assumed", "value": f"{g_disp:.1f}%"},
                    {"label": "Justified price-to-book", "value": f"{justified_pb:.2f}x"},
                    {"label": "Current price-to-book",
                     "value": f"{d['price_to_book']:.2f}x" if d.get("price_to_book") else "n/a"},
                ],
                "note": "Gordon form (ROE - g) / (COE - g), clamped to 0.5x-2.8x so a thin "
                        "ROE-COE spread cannot print an absurd multiple.",
            },
        }

    peak = d["peak_vs_mean"]
    if not eps or eps <= 0:
        return {"available": False, "method": "No positive trailing EPS to capitalise."}

    base = RISK_FREE_YIELD + EQUITY_RISK_PREMIUM
    # Through-cycle returns set the hurdle; a peak year earns no valuation credit.
    cycle = d["roce_mean_10"] if d["roce_mean_10"] is not None else roce
    g5 = d["cagr"].get("profit_5")
    quality = max(-1.5, min(2.5, (cycle - 12) / 6)) if cycle else 0.0
    growth = max(-1.5, min(1.5, (g5 - 6) / 4)) if g5 is not None else 0.0
    leverage = -1.0 if (d["debt_equity"] or 0) > 1 else 0.0
    promoter = 0.4 if (d["promoter_pct"] or 0) >= 60 else 0.0
    loss_years = min(3, d["loss_years_9"] or 0)
    losses = -float(loss_years) if loss_years else 0.0
    bonus = quality + growth + leverage + promoter + losses
    uncapped = base - bonus
    required = max(REQUIRED_RETURN_FLOOR, uncapped)

    justified_pe = round(100 / required, 1)
    mid = eps * justified_pe
    price = d["price"] or 0
    basis = "normalised three-year EPS" if d["eps_normalised"] else "trailing EPS"
    return {
        "available": True,
        "method": (
            f"Hurdle rate built from a 10-year G-Sec plus an equity premium, adjusted for "
            f"through-cycle returns, growth, leverage and owner stake. "
            "A valuation yardstick, not a target."
        ),
        "required_return_pct": round(required, 2),
        "justified_pe": justified_pe,
        "eps_basis": eps,
        "peak_of_cycle": bool(peak and peak > 1.5),
        "low": round(mid * 0.65, 2),
        "mid": round(mid, 2),
        "high": round(mid * 1.45, 2),
        "gap_to_mid_pct": round((mid / price - 1) * 100, 1) if price else None,
        "build": {
            "kind": "required_return",
            "rows": [
                {"label": "10-year G-Sec", "points": RISK_FREE_YIELD},
                {"label": "Equity risk premium", "points": EQUITY_RISK_PREMIUM},
                {"label": f"Through-cycle returns ({cycle:.0f}% mean ROCE)" if cycle
                          else "Through-cycle returns (no ROCE history)",
                 "points": round(quality, 2)},
                {"label": f"Earnings growth ({g5:.1f}% 5-year CAGR)" if g5 is not None
                          else "Earnings growth (not measurable)",
                 "points": round(growth, 2)},
                {"label": "Leverage above 1x debt/equity", "points": leverage},
                {"label": "Promoter stake above 60%", "points": promoter},
                {"label": f"{d['loss_years_9']} loss years in the last nine" if d["loss_years_9"]
                          else "No loss years in the last nine",
                 "points": losses},
            ],
            "total_pct": round(uncapped, 2),
            "floor_pct": REQUIRED_RETURN_FLOOR,
            "floor_applied": uncapped < REQUIRED_RETURN_FLOOR,
            "required_return_pct": round(required, 2),
            "justified_pe": justified_pe,
            "eps_used": eps,
            "eps_basis": basis,
            "current_pe": d["pe"],
            "note": "Each line is a percentage point off the hurdle rate. Lower required "
                    "return means a higher justified P/E, so the multiple is auditable.",
        },
    }


def evaluate(rec: dict) -> dict:
    d = derive(rec)
    reasons: list[str] = []
    risks: list[str] = []

    if d["track"] == "financial":
        weights = WEIGHTS_FINANCIAL
        builders = {
            "promoter": lambda: _pillar_promoter(d, reasons, risks),
            "capital": lambda: _pillar_capital(d, rec, reasons, risks),
            "quality": lambda: _pillar_quality(d, reasons, risks),
            "profitability": lambda: _pillar_profitability_fin(d, reasons, risks),
            "growth": lambda: _pillar_growth_fin(d, reasons, risks),
            "valuation": lambda: _pillar_valuation_fin(d, reasons, risks),
        }
    else:
        weights = WEIGHTS
        builders = {
            "promoter": lambda: _pillar_promoter(d, reasons, risks),
            "balance_sheet": lambda: _pillar_balance_sheet(d, rec, reasons, risks),
            "profitability": lambda: _pillar_profitability(d, reasons, risks),
            "growth": lambda: _pillar_growth(d, reasons, risks),
            "cash": lambda: _pillar_cash(d, reasons, risks),
            "valuation": lambda: _pillar_valuation(d, reasons, risks),
        }

    pillars = {}
    earned = 0.0
    filled_weight = 0.0
    for name, fn in builders.items():
        s = fn()
        w = weights[name]
        if s is None:
            # A missing pillar scores neutral, not absent: re-weighting only the
            # pillars that had data would flatter thin-disclosure companies.
            pillars[name] = {"score": None, "weight": w, "note": "insufficient data - counted as neutral"}
            earned += 50.0 * w / 100
            continue
        pillars[name] = {"score": round(s, 1), "weight": w}
        earned += s * w / 100
        filled_weight += w

    score = round(earned, 1)

    flags = _red_flags(d, rec)
    severe = [f for f in flags if f["severe"]]
    minor = [f for f in flags if not f["severe"]]

    val = pillars.get("valuation", {}).get("score")

    if severe:
        verdict = "AVOID"
    elif score >= 78 and val is not None and val >= 60:
        verdict = "STRONG BUY"
    elif score >= 68:
        verdict = "BUY"
    elif score >= 55:
        verdict = "HOLD"
    else:
        verdict = "AVOID"

    # A sound business at an unsound price belongs on a watchlist, not in a
    # portfolio. Without this cap the 15%-weighted valuation pillar lets a 90x
    # P/E quality name sail through as BUY while the table prints -90% vs fair.
    gap = fair_value(d).get("gap_to_mid_pct")
    overpriced = gap is not None and gap < -40
    if verdict in ("BUY", "STRONG BUY") and (
        (val is not None and val < 35) or overpriced
    ):
        verdict = "HOLD"
        reasons.append(
            "Verdict capped at HOLD: the fundamentals clear every gate, but the "
            "price sits well above the modelled value range - the valuation is the "
            "problem, not the business."
            if overpriced else
            "Verdict capped at HOLD: the fundamentals clear every gate, but the "
            "valuation pillar is weak - the price is the problem, not the business."
        )

    confidence = round(filled_weight / sum(weights.values()) * 100)

    return {
        "key": rec.get("key"),
        "name": rec.get("name"),
        "track": d["track"],
        "verdict": verdict,
        "score": score,
        "confidence": confidence,
        "pillars": pillars,
        "red_flags": flags,
        "severe_flags": len(severe),
        "minor_flags": len(minor),
        "reasons": reasons,
        "risks": risks,
        "metrics": {
            "return_ratio": d["roce"] if d["track"] == "industrial" else (
                sum(d["roe_series"][-3:]) / len(d["roe_series"][-3:]) if d["roe_series"] else None),
            "promoter_pct": d["promoter_pct"],
            "promoter_chg_1y": d["promoter_chg_1y"],
            "market_cap": d["market_cap"],
            "price": d["price"],
            "pe": d["pe"],
            "peg": d["peg"],
            "earnings_yield": d["earnings_yield"],
            "eps_ladder": d["eps_ladder"],
            "roce": d["roce"],
            "roe": (sum(d["roe_series"][-3:]) / len(d["roe_series"][-3:])) if d["roe_series"] else None,
            "opm": d["opm"],
            "debt_equity": d["debt_equity"],
            "interest_coverage": d["interest_coverage"],
            "fcf": d["fcf"],
            "sales_cagr_5": d["cagr"].get("sales_5"),
            "profit_cagr_5": d["cagr"].get("profit_5"),
            "dividend_yield": d["dividend_yield"],
            "fii_pct": d["fii_pct"],
            "price_to_book": d["price_to_book"],
            "equity_to_assets": d["equity_to_assets"],
            "deposits": d["deposits"],
            "worst_profit_drop_5y": d["worst_profit_drop_5y"],
        },
        "fair_value": fair_value(d),
    }
