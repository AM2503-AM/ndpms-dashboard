#!/usr/bin/env python3
"""
NDPMS Dashboard — Weekly Price & Benchmark Updater
Updates: MF NAVs, ETF/equity prices, benchmark period returns (b1m/b3m/b6m/b12m/bsi)
Does NOT update portfolio returns (p1m etc.) — those need transaction history.
"""

import json, re, sys, time, os
import requests
from datetime import date, datetime, timedelta
from dateutil.relativedelta import relativedelta

# ── Config ────────────────────────────────────────────────────────────────────
MFAPI_BASE   = "https://api.mfapi.in/mf"
NIFTY_URL    = "https://niftyindices.com/Backpage.aspx/getHistoricaldatatabletoString"
INDEX_N500   = "NIFTY 500 Total Returns Index"
INDEX_ARB    = "Nifty50 Arbitrage"
HTML_FILE    = "index.html"
MAP_FILE     = "security_map.json"
REQUEST_GAP  = 0.35   # seconds between MFAPI calls

# ── Helpers ───────────────────────────────────────────────────────────────────

def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def parse_date(s):
    return datetime.strptime(s, "%d/%m/%Y").date()

def fmt_date_nse(d):
    return d.strftime("%d-%b-%Y")   # 01-Jan-2025

def fmt_date_hist(d):
    return d.strftime("%d %b %Y")   # 01 Jan 2025

# ── MFAPI ─────────────────────────────────────────────────────────────────────

def mfapi_search(query):
    """Return best matching scheme code for a fund name."""
    try:
        r = requests.get(f"{MFAPI_BASE}/search",
                         params={"q": query}, timeout=15)
        results = r.json()
        for item in results:
            name = item.get("schemeName", "").lower()
            if "direct" in name and ("growth" in name or "-g" in name or " g)" in name):
                return str(item["schemeCode"])
        if results:
            return str(results[0]["schemeCode"])
    except Exception as e:
        print(f"  [WARN] MFAPI search failed for '{query}': {e}")
    return None

def mfapi_latest_nav(code):
    """Return latest NAV float for a scheme code."""
    try:
        r = requests.get(f"{MFAPI_BASE}/{code}", timeout=15)
        data = r.json().get("data", [])
        if data:
            return float(data[0]["nav"])
    except Exception as e:
        print(f"  [WARN] MFAPI NAV fetch failed for code {code}: {e}")
    return None

# ── Yahoo Finance ─────────────────────────────────────────────────────────────

def yahoo_price(ticker):
    """Return latest closing price from Yahoo Finance."""
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period="5d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception as e:
        print(f"  [WARN] Yahoo fetch failed for {ticker}: {e}")
    return None

# ── Nifty Indices ─────────────────────────────────────────────────────────────

NIFTY_HEADERS = {
    "Accept":           "application/json, text/javascript, */*; q=0.01",
    "Accept-Language":  "en-US,en;q=0.9",
    "Content-Type":     "application/json",
    "Origin":           "https://niftyindices.com",
    "Referer":          "https://niftyindices.com/",
    "User-Agent":       "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
}

def fetch_index_history(index_name, start, end):
    """
    Returns dict { "DD Mon YYYY" -> float closing value }
    e.g. { "01 Jan 2025" -> 23456.78 }
    """
    payload = {
        "name":      index_name,
        "startDate": fmt_date_nse(start),
        "endDate":   fmt_date_nse(end),
    }
    try:
        r = requests.post(NIFTY_URL, headers=NIFTY_HEADERS,
                          json=payload, timeout=45)
        outer = r.json()
        rows  = json.loads(outer["d"])
        result = {}
        for row in rows:
            key = row.get("Index Date", "")          # "01 Jan 2025"
            val = row.get("Closing Index Value", "0").replace(",", "")
            try:
                result[key] = float(val)
            except ValueError:
                pass
        return result
    except Exception as e:
        print(f"  [WARN] niftyindices fetch failed for '{index_name}': {e}")
        return {}

def closest_value(hist, target):
    """Walk back up to 10 calendar days to find a trading day's value."""
    for i in range(10):
        key = fmt_date_hist(target - timedelta(days=i))
        if key in hist:
            return hist[key]
    return None

def blended_return(n500_hist, arb_hist, start, end, n500_weight):
    """Return blended benchmark % return between two dates."""
    n0 = closest_value(n500_hist, start)
    n1 = closest_value(n500_hist, end)
    a0 = closest_value(arb_hist, start)
    a1 = closest_value(arb_hist, end)
    if None in (n0, n1, a0, a1):
        return None
    r_n500 = n1 / n0 - 1
    r_arb  = a1 / a0 - 1
    return round((n500_weight * r_n500 + (1 - n500_weight) * r_arb) * 100, 6)

def bm_weight(benchmark_name):
    """Extract N500 weight from benchmark_name string."""
    bn = benchmark_name.lower()
    if "nifty 500" in bn and "100%" in bn:  return 1.0
    if "arbitrage" in bn and "100%" in bn:  return 0.0
    if "80%" in bn and "nifty 500" in bn:   return 0.8
    if "70%" in bn and "nifty 500" in bn:   return 0.7
    return 0.7   # safe default

# ── HTML extraction ───────────────────────────────────────────────────────────

def extract_js_array(html, var_name):
    """
    Robustly extract a JS array assigned to var_name.
    Handles multi-line assignments like: const FOO = [ ... ];
    Returns (json_string, start_pos, end_pos) in the original html.
    """
    marker = f"const {var_name} = "
    idx = html.find(marker)
    if idx == -1:
        return None, -1, -1
    start = idx + len(marker)
    depth = 0
    i = start
    while i < len(html):
        c = html[i]
        if c == "[":  depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return html[start:i+1], start, i+1
        i += 1
    return None, -1, -1

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    today = date.today()
    print(f"=== NDPMS Price Update — {today} ===\n")

    # Load files
    with open(HTML_FILE, encoding="utf-8") as f:
        html = f.read()

    sec_map = load_json(MAP_FILE)

    # Extract STRATEGY_DATA
    raw, s_start, s_end = extract_js_array(html, "STRATEGY_DATA")
    if raw is None:
        print("ERROR: STRATEGY_DATA not found in HTML"); sys.exit(1)
    strategy_data = json.loads(raw)
    print(f"Loaded {len(strategy_data)} strategies.\n")

    # ── Step 1: Collect unique securities & fetch prices ──────────────────────
    print("── Step 1: Fetching security prices ──")
    price_cache = {}

    all_holdings = [
        h
        for s in strategy_data
        for c in s.get("clients", [])
        for h in c.get("holdings", [])
    ]

    unique_securities = {h["security"] for h in all_holdings}

    for sec_name in sorted(unique_securities):
        if sec_name in price_cache:
            continue

        entry    = sec_map.get(sec_name, {})
        sec_type = entry.get("type", "unknown")

        if sec_type in ("skip", "manual"):
            price_cache[sec_name] = None
            continue

        if sec_type == "mf":
            code = entry.get("mfapi_code")
            if not code:
                print(f"  Discovering MFAPI code: {sec_name[:55]}")
                code = mfapi_search(entry.get("search", sec_name))
                if code:
                    entry["mfapi_code"] = code
                    sec_map[sec_name]   = entry
                    print(f"    → found code {code}")
                else:
                    print(f"    → NOT FOUND, will skip")
            if code:
                nav = mfapi_latest_nav(code)
                price_cache[sec_name] = nav
                print(f"  MF  {sec_name[:55]:<55} NAV={nav}")
                time.sleep(REQUEST_GAP)
            else:
                price_cache[sec_name] = None

        elif sec_type in ("equity", "etf", "reit", "invit"):
            ticker = entry.get("ticker")
            if ticker:
                price = yahoo_price(ticker)
                price_cache[sec_name] = price
                status = f"₹{price:.2f}" if price else "FAILED"
                print(f"  {sec_type.upper():<6} {sec_name[:55]:<55} {status}")
            else:
                price_cache[sec_name] = None
                print(f"  {sec_type.upper():<6} {sec_name[:55]:<55} no ticker (skipped)")

        else:
            price_cache[sec_name] = None
            print(f"  UNKNOWN type for: {sec_name}")

    # ── Step 2: Update holdings ───────────────────────────────────────────────
    print("\n── Step 2: Updating holdings & AUM ──")
    updated_count = 0

    for strategy in strategy_data:
        for client in strategy.get("clients", []):
            new_total_mv = 0.0

            for h in client.get("holdings", []):
                new_price = price_cache.get(h["security"])
                if new_price is not None:
                    h["price"]        = round(new_price, 4)
                    h["market_value"] = round(h["quantity"] * new_price, 2)
                    h["gain_loss"]    = round(h["market_value"] - h["cost"], 0)
                    if h["cost"] != 0:
                        h["gl_pct"]   = round(h["gain_loss"] / h["cost"] * 100, 2)
                    updated_count += 1
                new_total_mv += h["market_value"]

            # Recalculate assets_pct
            if new_total_mv > 0:
                for h in client.get("holdings", []):
                    h["assets_pct"] = round(h["market_value"] / new_total_mv * 100, 2)

            # Recalculate client AUM & overall G/L
            invested = sum(
                h["cost"] for h in client.get("holdings", [])
                if h["asset_class"] not in ("Cash and Equivalent",)
                and h["cost"] > 0
            )
            client["aum"]    = round(new_total_mv, 2)
            if invested > 0:
                client["gl_pct"] = round((new_total_mv - invested) / invested * 100, 2)

        # Strategy-level totals
        strategy["total_aum"] = round(
            sum(c["aum"] for c in strategy.get("clients", [])), 2
        )

    print(f"  Updated {updated_count} holding price(s).")

    # ── Step 3: Fetch benchmark index history ─────────────────────────────────
    print("\n── Step 3: Fetching benchmark index history ──")

    all_inceptions = [
        parse_date(c["inception"])
        for s in strategy_data
        for c in s.get("clients", [])
    ]
    oldest_inception = min(all_inceptions)
    fetch_start = oldest_inception - timedelta(days=15)   # buffer for weekends

    print(f"  Fetching {INDEX_N500} from {fetch_start} to {today} …")
    n500_hist = fetch_index_history(INDEX_N500, fetch_start, today)
    print(f"  → {len(n500_hist)} trading days")

    time.sleep(1)

    print(f"  Fetching {INDEX_ARB} from {fetch_start} to {today} …")
    arb_hist = fetch_index_history(INDEX_ARB, fetch_start, today)
    print(f"  → {len(arb_hist)} trading days")

    if not n500_hist or not arb_hist:
        print("  [WARN] Index data unavailable — skipping benchmark updates.")
    else:
        # ── Step 4: Calculate benchmark returns per client ─────────────────────
        print("\n── Step 4: Updating benchmark returns ──")

        for strategy in strategy_data:
            for client in strategy.get("clients", []):
                name      = client["name"]
                inception = parse_date(client["inception"])
                w         = bm_weight(client.get("benchmark_name", ""))
                days_live = (today - inception).days

                # Since Inception
                bsi = blended_return(n500_hist, arb_hist, inception, today, w)
                if bsi is not None:
                    client["bsi"] = bsi

                # 1M (always try)
                d1m = today - relativedelta(months=1)
                b1m = blended_return(n500_hist, arb_hist, d1m, today, w)
                if b1m is not None:
                    client["b1m"] = b1m

                # 3M
                if days_live >= 80:
                    d3m = today - relativedelta(months=3)
                    b3m = blended_return(n500_hist, arb_hist, d3m, today, w)
                    if b3m is not None:
                        client["b3m"] = b3m

                # 6M
                if days_live >= 170:
                    d6m = today - relativedelta(months=6)
                    b6m = blended_return(n500_hist, arb_hist, d6m, today, w)
                    if b6m is not None:
                        client["b6m"] = b6m

                # 12M
                if days_live >= 350:
                    d12m = today - relativedelta(months=12)
                    b12m = blended_return(n500_hist, arb_hist, d12m, today, w)
                    if b12m is not None:
                        client["b12m"] = b12m

                print(f"  {name:<35} w={w:.0%}  bsi={bsi:+.2f}%  b1m={b1m:+.2f}%" if bsi and b1m else f"  {name} — incomplete data")

    # ── Step 5: Write back ────────────────────────────────────────────────────
    print("\n── Step 5: Writing index.html ──")

    new_data_str = json.dumps(strategy_data, indent=2, ensure_ascii=False)
    new_html     = html[:s_start] + new_data_str + html[s_end:]

    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(new_html)

    # Save updated security map (persists newly discovered MFAPI codes)
    save_json(MAP_FILE, sec_map)

    total_clients = sum(len(s.get("clients", [])) for s in strategy_data)
    print(f"\nDone. {total_clients} clients across {len(strategy_data)} strategies updated.")
    print(f"Run date: {today}")

if __name__ == "__main__":
    main()
