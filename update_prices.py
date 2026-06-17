#!/usr/bin/env python3
"""
NDPMS Dashboard — Weekly Price & Benchmark Updater  v2
- MF NAVs via MFAPI.in (mutual funds + ETFs, all AMFI-registered)
- ETF/equity/REIT prices via Yahoo Finance
- Benchmark returns via MFAPI proxy funds (Nippon Nifty 500 + ICICI Arb)
  → more reliable than niftyindices.com scraping
"""

import json, re, sys, time
import requests
from datetime import date, datetime, timedelta
from dateutil.relativedelta import relativedelta

# ── Config ─────────────────────────────────────────────────────────────────────
MFAPI_BASE  = "https://api.mfapi.in/mf"
HTML_FILE   = "index.html"
MAP_FILE    = "security_map.json"
REQ_GAP     = 0.4   # seconds between MFAPI calls to avoid rate-limiting

# ── File I/O ───────────────────────────────────────────────────────────────────

def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def parse_date(s):
    return datetime.strptime(s, "%d/%m/%Y").date()

# ── MFAPI helpers ──────────────────────────────────────────────────────────────

def mfapi_search(query, is_etf=False):
    """Return best scheme code for a fund/ETF name."""
    try:
        r = requests.get(f"{MFAPI_BASE}/search", params={"q": query}, timeout=15)
        results = r.json()
        if is_etf:
            # ETFs don't need Direct/Growth filter — pick first non-IDCW result
            for item in results:
                name = item.get("schemeName", "").lower()
                if "idcw" not in name and "dividend" not in name:
                    return str(item["schemeCode"])
        else:
            # MFs — prefer Direct Growth
            for item in results:
                name = item.get("schemeName", "").lower()
                if "direct" in name and ("growth" in name or " - g" in name or "-g" in name):
                    return str(item["schemeCode"])
            # Fallback: any direct plan
            for item in results:
                if "direct" in item.get("schemeName", "").lower():
                    return str(item["schemeCode"])
        if results:
            return str(results[0]["schemeCode"])
    except Exception as e:
        print(f"  [WARN] MFAPI search failed for '{query}': {e}")
    return None

def mfapi_latest_nav(code):
    """Return latest NAV (float) for a scheme code."""
    try:
        r = requests.get(f"{MFAPI_BASE}/{code}", timeout=15)
        data = r.json().get("data", [])
        if data:
            return float(data[0]["nav"])
    except Exception as e:
        print(f"  [WARN] NAV fetch failed for code {code}: {e}")
    return None

def mfapi_full_history(code):
    """
    Return full NAV history as {date_obj: float}.
    MFAPI date format in response: "17-06-2026"  (DD-MM-YYYY)
    """
    try:
        r = requests.get(f"{MFAPI_BASE}/{code}", timeout=30)
        hist = {}
        for item in r.json().get("data", []):
            try:
                d   = datetime.strptime(item["date"], "%d-%m-%Y").date()
                nav = float(item["nav"])
                hist[d] = nav
            except Exception:
                pass
        return hist
    except Exception as e:
        print(f"  [WARN] Full history fetch failed for code {code}: {e}")
    return {}

def nav_on_or_after(hist, target):
    """NAV on target date or the next available trading day (up to +10 days). For inception dates."""
    for i in range(10):
        d = target + timedelta(days=i)
        if d in hist:
            return hist[d]
    return None

def nav_on_or_before(hist, target):
    """NAV on target date or the last available trading day (up to -10 days). For period ends."""
    for i in range(10):
        d = target - timedelta(days=i)
        if d in hist:
            return hist[d]
    return None

# ── Yahoo Finance ──────────────────────────────────────────────────────────────

def yahoo_price(ticker):
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period="5d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception as e:
        print(f"  [WARN] Yahoo failed for {ticker}: {e}")
    return None

# ── Benchmark helpers ──────────────────────────────────────────────────────────

def bm_weight(benchmark_name):
    """Extract Nifty 500 weight from benchmark_name string."""
    bn = benchmark_name.lower()
    if "nifty 500" in bn and "100%" in bn:  return 1.0
    if "arbitrage"  in bn and "100%" in bn: return 0.0
    if "80%"        in bn and "nifty 500" in bn: return 0.8
    if "70%"        in bn and "nifty 500" in bn: return 0.7
    return 0.7

def blended_return(n500_hist, arb_hist, start, end, w):
    """Calculate blended % return between two dates using proxy NAV histories."""
    n0 = nav_on_or_after(n500_hist, start)
    n1 = nav_on_or_before(n500_hist, end)
    a0 = nav_on_or_after(arb_hist, start)
    a1 = nav_on_or_before(arb_hist, end)
    if None in (n0, n1, a0, a1):
        return None
    r_n500 = n1 / n0 - 1
    r_arb  = a1 / a0 - 1
    return round((w * r_n500 + (1 - w) * r_arb) * 100, 6)

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

    raw, s_start, s_end = extract_js_array(html, "STRATEGY_DATA")
    if raw is None:
        print("ERROR: STRATEGY_DATA not found"); sys.exit(1)

    strategy_data = json.loads(raw)
    print(f"Loaded {len(strategy_data)} strategies.\n")

    # ── Step 1: Fetch security prices ──────────────────────────────────────────
    print("── Step 1: Fetching security prices ──")
    price_cache = {}

    unique_secs = sorted({
        h["security"]
        for s in strategy_data
        for c in s.get("clients", [])
        for h in c.get("holdings", [])
    })

    for sec in unique_secs:
        entry    = sec_map.get(sec, {})
        sec_type = entry.get("type", "unknown")

        if sec_type in ("skip", "manual"):
            price_cache[sec] = None
            continue

        # MF or ETF → MFAPI
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
                nav = mfapi_latest_nav(code)
                price_cache[sec] = nav
                label = "ETF" if sec_type == "etf" else "MF "
                print(f"  {label} {sec[:60]:<60} NAV={nav}")
                time.sleep(REQ_GAP)
            else:
                price_cache[sec] = None

        # Equity / REIT / InvIT → Yahoo Finance
        elif sec_type in ("equity", "reit", "invit"):
            ticker = entry.get("ticker")
            if ticker:
                price = yahoo_price(ticker)
                price_cache[sec] = price
                tag = f"₹{price:.2f}" if price else "FAILED"
                print(f"  {sec_type.upper():<6} {sec[:60]:<60} {tag}")
            else:
                price_cache[sec] = None
                print(f"  {sec_type.upper():<6} {sec[:60]:<60} no ticker (skipped)")

        else:
            price_cache[sec] = None

    # ── Step 2: Update holdings ────────────────────────────────────────────────
    print("\n── Step 2: Updating holdings & AUM ──")
    updated = 0

    for strategy in strategy_data:
        for client in strategy.get("clients", []):
            total_mv = 0.0

            for h in client.get("holdings", []):
                new_price = price_cache.get(h["security"])
                if new_price is not None:
                    h["price"]        = round(new_price, 4)
                    h["market_value"] = round(h["quantity"] * new_price, 2)
                    h["gain_loss"]    = round(h["market_value"] - h["cost"], 0)
                    if h["cost"] != 0:
                        h["gl_pct"]   = round(h["gain_loss"] / h["cost"] * 100, 2)
                    updated += 1
                total_mv += h["market_value"]

            if total_mv > 0:
                for h in client.get("holdings", []):
                    h["assets_pct"] = round(h["market_value"] / total_mv * 100, 2)

            invested = sum(
                h["cost"] for h in client.get("holdings", [])
                if h["cost"] > 0 and h["asset_class"] != "Cash and Equivalent"
            )
            client["aum"] = round(total_mv, 2)
            if invested > 0:
                client["gl_pct"] = round((total_mv - invested) / invested * 100, 2)

        strategy["total_aum"] = round(
            sum(c["aum"] for c in strategy.get("clients", [])), 2
        )

    print(f"  Updated {updated} holdings.")

    # ── Step 3: Fetch benchmark proxy fund histories ────────────────────────────
    print("\n── Step 3: Fetching benchmark proxy fund histories ──")

    def get_or_discover(key, is_etf=False):
        entry = sec_map.get(key, {})
        code  = entry.get("mfapi_code")
        if not code:
            print(f"  Discovering benchmark proxy: {entry.get('search','')}")
            code = mfapi_search(entry.get("search", ""), is_etf=is_etf)
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
        print(f"  Loading Nifty 500 TRI proxy history (code {n500_code}) …")
        n500_hist = mfapi_full_history(n500_code)
        print(f"    → {len(n500_hist)} NAV dates")
    if arb_code:
        print(f"  Loading Nifty 50 Arbitrage proxy history (code {arb_code}) …")
        time.sleep(REQ_GAP)
        arb_hist = mfapi_full_history(arb_code)
        print(f"    → {len(arb_hist)} NAV dates")

    if not n500_hist or not arb_hist:
        print("  [WARN] Benchmark proxy data incomplete — skipping benchmark updates.")
    else:
        # ── Step 4: Calculate benchmark returns per client ──────────────────────
        print("\n── Step 4: Updating benchmark returns ──")

        for strategy in strategy_data:
            for client in strategy.get("clients", []):
                name      = client["name"]
                inception = parse_date(client["inception"])
                w         = bm_weight(client.get("benchmark_name", ""))
                days      = (today - inception).days

                bsi = blended_return(n500_hist, arb_hist, inception, today, w)
                if bsi is not None: client["bsi"] = bsi

                b1m = blended_return(n500_hist, arb_hist, today - relativedelta(months=1), today, w)
                if b1m is not None: client["b1m"] = b1m

                if days >= 80:
                    b3m = blended_return(n500_hist, arb_hist, today - relativedelta(months=3), today, w)
                    if b3m is not None: client["b3m"] = b3m

                if days >= 170:
                    b6m = blended_return(n500_hist, arb_hist, today - relativedelta(months=6), today, w)
                    if b6m is not None: client["b6m"] = b6m

                if days >= 350:
                    b12m = blended_return(n500_hist, arb_hist, today - relativedelta(months=12), today, w)
                    if b12m is not None: client["b12m"] = b12m

                bsi_str = f"{bsi:+.2f}%" if bsi is not None else "N/A"
                b1m_str = f"{b1m:+.2f}%" if b1m is not None else "N/A"
                print(f"  {name:<35} w={w:.0%}  bsi={bsi_str}  b1m={b1m_str}")

    # ── Step 5: Write back ─────────────────────────────────────────────────────
    print("\n── Step 5: Writing index.html ──")
    new_data = json.dumps(strategy_data, indent=2, ensure_ascii=False)
    new_html = html[:s_start] + new_data + html[s_end:]

    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(new_html)

    save_json(MAP_FILE, sec_map)

    total_clients = sum(len(s.get("clients", [])) for s in strategy_data)
    print(f"\nDone. {total_clients} clients across {len(strategy_data)} strategies updated on {today}.")

if __name__ == "__main__":
    main()
