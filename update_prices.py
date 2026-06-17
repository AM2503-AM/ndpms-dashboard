#!/usr/bin/env python3
"""
NDPMS Dashboard — Weekly Price & Benchmark Updater  v3
- MF/ETF NAVs + full history via MFAPI.in
- Equity/REIT history via yfinance (5y)
- Portfolio period returns calculated from holding-level historical prices
  (buy-and-hold assumption: same quantities throughout each period)
- Manual prices (AIFs, InvITs) via manual_prices.json
- Benchmark returns via MFAPI proxy funds (Nippon Nifty 500 + ICICI Arb)
"""

import json, re, sys, time
import requests
from datetime import date, datetime, timedelta
from dateutil.relativedelta import relativedelta

MFAPI_BASE  = "https://api.mfapi.in/mf"
HTML_FILE   = "index.html"
MAP_FILE    = "security_map.json"
MANUAL_FILE = "manual_prices.json"
REQ_GAP     = 0.3

def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def parse_date(s):
    return datetime.strptime(s, "%d/%m/%Y").date()

# ── MFAPI ──────────────────────────────────────────────────────────────────────

def mfapi_search(query, is_etf=False):
    try:
        r = requests.get(f"{MFAPI_BASE}/search", params={"q": query}, timeout=15)
        results = r.json()
        if is_etf:
            for item in results:
                name = item.get("schemeName", "").lower()
                if "idcw" not in name and "dividend" not in name:
                    return str(item["schemeCode"])
        else:
            for item in results:
                name = item.get("schemeName", "").lower()
                if "direct" in name and ("growth" in name or " - g" in name or "-g" in name):
                    return str(item["schemeCode"])
            for item in results:
                if "direct" in item.get("schemeName", "").lower():
                    return str(item["schemeCode"])
        if results:
            return str(results[0]["schemeCode"])
    except Exception as e:
        print(f"  [WARN] MFAPI search failed for '{query}': {e}")
    return None

def mfapi_full_history(code):
    """Return full NAV history as {date_obj: float}."""
    try:
        r = requests.get(f"{MFAPI_BASE}/{code}", timeout=30)
        hist = {}
        for item in r.json().get("data", []):
            try:
                d = datetime.strptime(item["date"], "%d-%m-%Y").date()
                hist[d] = float(item["nav"])
            except Exception:
                pass
        return hist
    except Exception as e:
        print(f"  [WARN] Full history fetch failed for code {code}: {e}")
    return {}

def nav_on_or_after(hist, target):
    """NAV on or after target date (up to +30 days). For inception/start dates."""
    for i in range(30):
        d = target + timedelta(days=i)
        if d in hist:
            return hist[d]
    return None

def nav_on_or_before(hist, target):
    """NAV on or before target date (up to -10 days). For period ends."""
    for i in range(10):
        d = target - timedelta(days=i)
        if d in hist:
            return hist[d]
    return None

# ── Yahoo Finance ──────────────────────────────────────────────────────────────

def yahoo_full_history(ticker):
    """Return price history {date_obj: float} for up to 5 years."""
    try:
        import yfinance as yf
        raw = yf.Ticker(ticker).history(period="5y")
        if not raw.empty:
            return {d.date(): float(p) for d, p in zip(raw.index, raw["Close"])}
    except Exception as e:
        print(f"  [WARN] Yahoo history failed for {ticker}: {e}")
    return {}

# ── Benchmark ─────────────────────────────────────────────────────────────────

def bm_weight(benchmark_name):
    bn = benchmark_name.lower()
    if "nifty 500" in bn and "100%" in bn:  return 1.0
    if "arbitrage" in bn and "100%" in bn:  return 0.0
    if "80%" in bn and "nifty 500" in bn:   return 0.8
    if "70%" in bn and "nifty 500" in bn:   return 0.7
    return 0.7

def blended_return(n500_hist, arb_hist, start, end, w):
    n0 = nav_on_or_after(n500_hist, start)
    n1 = nav_on_or_before(n500_hist, end)
    a0 = nav_on_or_after(arb_hist, start)
    a1 = nav_on_or_before(arb_hist, end)
    if None in (n0, n1, a0, a1):
        return None
    r_n500 = n1 / n0 - 1
    r_arb  = a1 / a0 - 1
    return round((w * r_n500 + (1 - w) * r_arb) * 100, 6)

# ── Portfolio return helpers ───────────────────────────────────────────────────

def portfolio_value_at(holdings, hist_cache, target_date, is_start=False):
    """
    Calculate portfolio value at a historical date.
    is_start=True: use nav_on_or_after (for inception/period-start dates).
    For securities with no historical price, falls back to current market_value.
    Returns (value, coverage) where coverage = fraction of value that was historically priced.
    """
    priced   = 0.0
    fallback = 0.0
    for h in holdings:
        hist = hist_cache.get(h["security"], {})
        current_mv = h.get("market_value", 0) or 0
        if hist:
            price = nav_on_or_after(hist, target_date) if is_start else nav_on_or_before(hist, target_date)
            if price is not None:
                priced += (h.get("quantity", 0) or 0) * price
            else:
                fallback += current_mv
        else:
            fallback += current_mv
    total    = priced + fallback
    coverage = priced / total if total > 0 else 0.0
    return total, coverage

# ── HTML extraction ────────────────────────────────────────────────────────────

def extract_js_array(html, var_name):
    marker = f"const {var_name} = "
    idx = html.find(marker)
    if idx == -1:
        return None, -1, -1
    start = idx + len(marker)
    depth, i = 0, start
    while i < len(html):
        if html[i] == "[":   depth += 1
        elif html[i] == "]":
            depth -= 1
            if depth == 0:
                return html[start:i+1], start, i+1
        i += 1
    return None, -1, -1

# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    today = date.today()
    print(f"=== NDPMS Price Update — {today} ===\n")

    with open(HTML_FILE, encoding="utf-8") as f:
        html = f.read()

    sec_map = load_json(MAP_FILE)

    try:
        manual_prices = load_json(MANUAL_FILE)
    except FileNotFoundError:
        manual_prices = {}

    raw, s_start, s_end = extract_js_array(html, "STRATEGY_DATA")
    if raw is None:
        print("ERROR: STRATEGY_DATA not found"); sys.exit(1)

    strategy_data = json.loads(raw)
    print(f"Loaded {len(strategy_data)} strategies.\n")

    # ── Step 1: Fetch prices + full histories ───────────────────────────────────
    print("── Step 1: Fetching prices & histories ──")
    hist_cache = {}

    unique_secs = sorted({
        h["security"]
        for s in strategy_data
        for c in s.get("clients", [])
        for h in c.get("holdings", [])
    })

    for sec in unique_secs:
        entry    = sec_map.get(sec, {})
        sec_type = entry.get("type", "unknown")

        if sec_type == "skip":
            hist_cache[sec] = {}
            continue

        if sec_type == "manual":
            mp    = manual_prices.get(sec, {})
            price = mp.get("price")
            if price:
                hist_cache[sec] = {today: float(price)}
                print(f"  MANUAL {sec[:60]:<60} price={price} (as of {mp.get('as_of','?')})")
            else:
                hist_cache[sec] = {}
            continue

        if sec_type in ("mf", "etf"):
            code = entry.get("mfapi_code")
            if not code:
                print(f"  Discovering: {sec[:60]}")
                code = mfapi_search(entry.get("search", sec), is_etf=(sec_type == "etf"))
                if code:
                    entry["mfapi_code"] = code
                    sec_map[sec] = entry
                    print(f"    → code {code}")
                else:
                    print(f"    → NOT FOUND (will keep old price)")
            if code:
                hist = mfapi_full_history(code)
                hist_cache[sec] = hist
                nav = nav_on_or_before(hist, today) if hist else None
                label = "ETF" if sec_type == "etf" else "MF "
                print(f"  {label} {sec[:60]:<60} NAV={nav}")
                time.sleep(REQ_GAP)
            else:
                hist_cache[sec] = {}

        elif sec_type in ("equity", "reit", "invit"):
            ticker = entry.get("ticker")
            if ticker:
                hist = yahoo_full_history(ticker)
                hist_cache[sec] = hist
                price = nav_on_or_before(hist, today) if hist else None
                tag = f"₹{price:.2f}" if price else "FAILED"
                print(f"  {sec_type.upper():<6} {sec[:60]:<60} {tag}")
            else:
                hist_cache[sec] = {}
                print(f"  {sec_type.upper():<6} {sec[:60]:<60} no ticker (skipped)")

        else:
            hist_cache[sec] = {}

    # ── Step 2: Update holdings & AUM ─────────────────────────────────────────
    print("\n── Step 2: Updating holdings & AUM ──")
    updated = 0
    AUTO_TYPES   = {"mf", "etf", "equity", "reit", "invit"}
    MANUAL_TYPES = {"manual"}

    for strategy in strategy_data:
        for client in strategy.get("clients", []):
            total_mv  = 0.0
            auto_mv   = 0.0
            manual_mv = 0.0
            cash_mv   = 0.0

            for h in client.get("holdings", []):
                hist = hist_cache.get(h["security"], {})
                new_price = nav_on_or_before(hist, today) if hist else None
                if new_price is not None:
                    h["price"]        = round(new_price, 4)
                    h["market_value"] = round(h["quantity"] * new_price, 2)
                    h["gain_loss"]    = round(h["market_value"] - h["cost"], 0)
                    if h["cost"] != 0:
                        h["gl_pct"]   = round(h["gain_loss"] / h["cost"] * 100, 2)
                    updated += 1
                mv = h.get("market_value", 0) or 0
                total_mv += mv
                sec_type = sec_map.get(h["security"], {}).get("type", "unknown")
                if h.get("asset_class") == "Cash and Equivalent" or sec_type == "skip":
                    cash_mv += mv
                elif sec_type in MANUAL_TYPES:
                    manual_mv += mv
                else:
                    auto_mv += mv

            if total_mv > 0:
                for h in client.get("holdings", []):
                    h["assets_pct"] = round(h["market_value"] / total_mv * 100, 2)

            invested = sum(
                h["cost"] for h in client.get("holdings", [])
                if h["cost"] > 0 and h["asset_class"] != "Cash and Equivalent"
            )
            client["aum"]        = round(total_mv, 2)
            client["auto_aum"]   = round(auto_mv, 2)
            client["manual_aum"] = round(manual_mv, 2)
            client["cash_aum"]   = round(cash_mv, 2)
            if invested > 0:
                client["gl_pct"] = round((total_mv - invested) / invested * 100, 2)

        strategy["total_aum"] = round(
            sum(c["aum"] for c in strategy.get("clients", [])), 2
        )
        strategy["total_auto_aum"] = round(
            sum(c.get("auto_aum", 0) for c in strategy.get("clients", [])), 2
        )

    print(f"  Updated {updated} holdings.")

    # ── Step 3: Calculate portfolio period returns ─────────────────────────────
    print("\n── Step 3: Calculating portfolio period returns ──")

    for strategy in strategy_data:
        for client in strategy.get("clients", []):
            name      = client["name"]
            inception = parse_date(client["inception"])
            days      = (today - inception).days
            holdings  = client.get("holdings", [])

            mv_today, _ = portfolio_value_at(holdings, hist_cache, today)
            if mv_today <= 0:
                continue

            def port_ret(start_date, is_start=False):
                mv_start, cov = portfolio_value_at(holdings, hist_cache, start_date, is_start)
                if mv_start <= 0 or cov < 0.3:
                    return None
                return round((mv_today / mv_start - 1) * 100, 6)

            psi  = port_ret(inception, is_start=True)
            p1m  = port_ret(today - relativedelta(months=1))
            p3m  = port_ret(today - relativedelta(months=3))  if days >= 80  else None
            p6m  = port_ret(today - relativedelta(months=6))  if days >= 170 else None
            p12m = port_ret(today - relativedelta(months=12)) if days >= 350 else None

            if psi  is not None: client["psi"]  = psi
            if p1m  is not None: client["p1m"]  = p1m
            if p3m  is not None: client["p3m"]  = p3m
            if p6m  is not None: client["p6m"]  = p6m
            if p12m is not None: client["p12m"] = p12m

            psi_str = f"{psi:+.2f}%" if psi is not None else "N/A"
            p1m_str = f"{p1m:+.2f}%" if p1m is not None else "N/A"
            print(f"  {name:<35} psi={psi_str}  p1m={p1m_str}")

    # ── Step 4: Fetch benchmark proxy histories ─────────────────────────────────
    print("\n── Step 4: Fetching benchmark proxy histories ──")

    def get_or_discover(key):
        entry = sec_map.get(key, {})
        code  = entry.get("mfapi_code")
        if not code:
            print(f"  Discovering benchmark proxy: {entry.get('search','')}")
            code = mfapi_search(entry.get("search", ""))
            if code:
                entry["mfapi_code"] = code
                sec_map[key] = entry
                print(f"    → code {code}")
        return code

    n500_code = get_or_discover("_benchmark_n500")
    time.sleep(REQ_GAP)
    arb_code  = get_or_discover("_benchmark_arb")

    n500_hist, arb_hist = {}, {}
    if n500_code:
        print(f"  Loading Nifty 500 TRI proxy (code {n500_code}) …")
        n500_hist = mfapi_full_history(n500_code)
        print(f"    → {len(n500_hist)} NAV dates")
    if arb_code:
        print(f"  Loading Nifty 50 Arbitrage proxy (code {arb_code}) …")
        time.sleep(REQ_GAP)
        arb_hist = mfapi_full_history(arb_code)
        print(f"    → {len(arb_hist)} NAV dates")

    if not n500_hist or not arb_hist:
        print("  [WARN] Benchmark proxy data incomplete — skipping benchmark updates.")
    else:
        print("\n── Step 5: Updating benchmark returns ──")
        for strategy in strategy_data:
            for client in strategy.get("clients", []):
                name      = client["name"]
                inception = parse_date(client["inception"])
                w         = bm_weight(client.get("benchmark_name", ""))
                days      = (today - inception).days

                bsi  = blended_return(n500_hist, arb_hist, inception, today, w)
                b1m  = blended_return(n500_hist, arb_hist, today - relativedelta(months=1), today, w)
                b3m  = blended_return(n500_hist, arb_hist, today - relativedelta(months=3), today, w) if days >= 80  else None
                b6m  = blended_return(n500_hist, arb_hist, today - relativedelta(months=6), today, w) if days >= 170 else None
                b12m = blended_return(n500_hist, arb_hist, today - relativedelta(months=12), today, w) if days >= 350 else None

                if bsi  is not None: client["bsi"]  = bsi
                if b1m  is not None: client["b1m"]  = b1m
                if b3m  is not None: client["b3m"]  = b3m
                if b6m  is not None: client["b6m"]  = b6m
                if b12m is not None: client["b12m"] = b12m

                bsi_str = f"{bsi:+.2f}%" if bsi is not None else "N/A"
                b1m_str = f"{b1m:+.2f}%" if b1m is not None else "N/A"
                print(f"  {name:<35} w={w:.0%}  bsi={bsi_str}  b1m={b1m_str}")

    # ── Step 6: Write back ─────────────────────────────────────────────────────
    print("\n── Step 6: Writing index.html ──")
    new_data = json.dumps(strategy_data, indent=2, ensure_ascii=False)
    new_html = html[:s_start] + new_data + html[s_end:]

    day_str = f"{today.day} {today.strftime('%b')} {today.year}"
    new_html = re.sub(
        r'const PRICE_DATE = "[^"]*"',
        f'const PRICE_DATE = "{day_str}"',
        new_html
    )

    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(new_html)

    save_json(MAP_FILE, sec_map)

    total_clients = sum(len(s.get("clients", [])) for s in strategy_data)
    print(f"\nDone. {total_clients} clients across {len(strategy_data)} strategies updated on {today}.")

if __name__ == "__main__":
    main()
