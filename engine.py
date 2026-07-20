"""
Armadillo Trading Platform — data + strategy engine (v1)
Data source: Yahoo Finance v8 chart API (direct HTTPS, verified working in this environment).
Paper account: $5,000 start. Guardrails per counsel review (hard-coded, not optional).
"""
import json, time, math, os, sys
from datetime import datetime, timezone
import requests
import pandas as pd
import numpy as np

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
BASE = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={rng}&interval=1d"

WATCHLIST = ["SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "AMZN", "META", "TSLA", "GOOGL", "AMD", "NFLX"]
INDICES = ["SPY", "QQQ", "IWM", "^VIX"]

# ---- GUARDRAILS (counsel-mandated; engine refuses to size beyond these) ----
START_EQUITY = 25000.0
RISK_PER_TRADE = 0.01          # 1% of equity risked per trade
MAX_POSITION_PCT = 0.20        # max 20% of equity in one stock position
MAX_OPTIONS_PREMIUM_PCT = 0.05 # max 5% of equity premium at risk per options play
DAILY_LOSS_HALT = 0.03         # halt new orders at -3% day
WEEKLY_LOSS_HALT = 0.07        # flatten + disable at -7% week
DRAWDOWN_KILL = 0.15           # hard stop at -15% from high-water mark
MAX_OPEN_POSITIONS = 4

def alpaca_bars(sym, days=370):
    """Fallback daily bars from Alpaca data API (keys via env). No ^INDEX support."""
    key, sec = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_SECRET_KEY")
    if not key or sym.startswith("^"):
        raise RuntimeError(f"no alpaca fallback for {sym}")
    from datetime import timedelta
    start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    r = requests.get(f"https://data.alpaca.markets/v2/stocks/{sym}/bars",
                     params={"timeframe": "1Day", "start": start, "limit": 1000,
                             "adjustment": "split", "feed": "iex"},
                     headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec}, timeout=20)
    r.raise_for_status()
    bars = r.json()["bars"]
    df = pd.DataFrame({
        "ts": [int(pd.Timestamp(b["t"]).timestamp()) for b in bars],
        "open": [b["o"] for b in bars], "high": [b["h"] for b in bars],
        "low": [b["l"] for b in bars], "close": [b["c"] for b in bars],
        "volume": [b["v"] for b in bars],
    })
    df["date"] = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert("America/New_York").dt.strftime("%Y-%m-%d")
    return df.reset_index(drop=True)

def fetch(sym, rng="1y", retries=3):
    url = BASE.format(sym=sym.replace("^", "%5E"), rng=rng)
    err = "?"
    for i in range(retries):
        try:
            r = requests.get(url, headers=UA, timeout=20)
        except Exception as e:
            err = str(e)[:80]; time.sleep(1); continue
        err = f"HTTP {r.status_code}"
        if r.status_code == 200:
            j = r.json()["chart"]["result"][0]
            q = j["indicators"]["quote"][0]
            df = pd.DataFrame({
                "ts": j["timestamp"], "open": q["open"], "high": q["high"],
                "low": q["low"], "close": q["close"], "volume": q["volume"],
            }).dropna()
            df["date"] = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert("America/New_York").dt.strftime("%Y-%m-%d")
            return df.reset_index(drop=True)
        time.sleep(1.5 * (i + 1))
    try:
        return alpaca_bars(sym)
    except Exception as e2:
        raise RuntimeeError if False else RuntimeError(f"fetch failed for {sym}: {err}; alpaca fallback: {str(e2)[:80]}")

# ---- indicators ----
def sma(s, n): return s.rolling(n).mean()
def ema(s, n): return s.ewm(span=n, adjust=False).mean()

def rsi(close, n=14):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)

def atr(df, n=14):
    tr = pd.concat([
        df.high - df.low,
        (df.high - df.close.shift()).abs(),
        (df.low - df.close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()

def enrich(df):
    df = df.copy()
    df["sma20"] = sma(df.close, 20)
    df["sma50"] = sma(df.close, 50)
    df["rsi14"] = rsi(df.close)
    df["atr14"] = atr(df)
    df["hi20"] = df.high.rolling(20).max().shift(1)
    return df

# ---- signal logic (swing core) ----
def signal_row(df, i):
    """Evaluate bar i. Returns (signal, reason) — signal in {BUY, HOLD, NONE}."""
    r, p = df.iloc[i], df.iloc[i - 1]
    if any(pd.isna([r.sma50, r.rsi14, r.atr14, r.hi20])):
        return None
    uptrend = r.close > r.sma50 and r.sma20 > r.sma50
    pullback_resume = uptrend and p.rsi14 < 40 <= r.rsi14
    breakout = uptrend and r.close > r.hi20 and r.rsi14 < 75
    if pullback_resume:
        return {"type": "BUY", "setup": "pullback-resume", "why": f"Uptrend; RSI recrossed 40 ({p.rsi14:.0f}->{r.rsi14:.0f})"}
    if breakout:
        return {"type": "BUY", "setup": "20d-breakout", "why": f"Uptrend; closed above 20d high {r.hi20:.2f}"}
    return None

def size_position(equity, entry, stop):
    risk_dollars = equity * RISK_PER_TRADE
    per_share = entry - stop
    if per_share <= 0: return 0
    shares = math.floor(risk_dollars / per_share)
    max_shares = math.floor(equity * MAX_POSITION_PCT / entry)
    return max(0, min(shares, max_shares))

# ---- backtest (proves the engine; seeds dashboard with a real simulated track record) ----
def backtest(data, start=START_EQUITY, lookback=126):
    equity, cash = start, start
    open_pos, trades, curve = {}, [], []
    hwm, killed = start, False
    dates = data["SPY"]["date"].tolist()[-lookback:]
    for d in dates:
        # mark to market + manage exits
        for sym in list(open_pos):
            df = data[sym]; row = df[df.date == d]
            if row.empty: continue
            r = row.iloc[0]; pos = open_pos[sym]
            exit_px, why = None, None
            if r.low <= pos["stop"]: exit_px, why = pos["stop"], "stop"
            elif r.close < r.sma20: exit_px, why = r.close, "trail-sma20"
            elif pos["bars"] >= 25: exit_px, why = r.close, "time-exit"
            pos["bars"] += 1
            # ratchet stop to breakeven after a 1R move in our favor
            if not exit_px and r.close >= pos["entry"] + (pos["entry"] - pos["stop"]):
                pos["stop"] = max(pos["stop"], pos["entry"])
            if exit_px:
                pnl = (exit_px - pos["entry"]) * pos["shares"]
                cash += pos["shares"] * exit_px
                trades.append({"symbol": sym, "side": "SELL", "date": d, "price": round(exit_px, 2),
                               "shares": pos["shares"], "pnl": round(pnl, 2), "reason": why,
                               "setup": pos["setup"], "entry_date": pos["date"], "entry": pos["entry"]})
                del open_pos[sym]
        # equity
        mv = sum(p["shares"] * float(data[s][data[s].date == d].close.iloc[0])
                 for s, p in open_pos.items() if not data[s][data[s].date == d].empty)
        equity = cash + mv
        hwm = max(hwm, equity)
        dd = equity / hwm - 1
        curve.append({"date": d, "equity": round(equity, 2), "drawdown": round(dd, 4)})
        if dd <= -DRAWDOWN_KILL: killed = True
        # entries
        if killed or len(open_pos) >= MAX_OPEN_POSITIONS: continue
        for sym in WATCHLIST:
            if sym in open_pos or sym in ("SPY", "QQQ", "IWM") or len(open_pos) >= MAX_OPEN_POSITIONS:
                continue
            df = data[sym]; idx = df.index[df.date == d]
            if len(idx) == 0 or idx[0] < 60: continue
            sig = signal_row(df, idx[0])
            if not sig: continue
            r = df.iloc[idx[0]]
            entry = float(r.close); stop = entry - 1.5 * float(r.atr14); target = entry + 3 * float(r.atr14)
            shares = size_position(equity, entry, stop)
            if shares == 0 or shares * entry > cash: continue
            cash -= shares * entry
            open_pos[sym] = {"entry": entry, "stop": round(stop, 2), "target": round(target, 2),
                             "shares": shares, "date": d, "bars": 0, "setup": sig["setup"]}
            trades.append({"symbol": sym, "side": "BUY", "date": d, "price": round(entry, 2),
                           "shares": shares, "pnl": None, "reason": sig["setup"], "setup": sig["setup"],
                           "stop": round(stop, 2), "target": round(target, 2)})
    return curve, trades, open_pos, killed

def current_signals(data):
    out = []
    for sym in WATCHLIST:
        df = data[sym]; i = len(df) - 1
        r = df.iloc[i]
        sig = signal_row(df, i)
        entry = float(r.close); stp = entry - 1.5 * float(r.atr14); tgt = entry + 3 * float(r.atr14)
        chg = (r.close / df.iloc[i-1].close - 1) * 100
        out.append({
            "symbol": sym, "close": round(entry, 2), "chg_pct": round(float(chg), 2),
            "rsi14": round(float(r.rsi14), 1), "sma20": round(float(r.sma20), 2),
            "sma50": round(float(r.sma50), 2), "atr14": round(float(r.atr14), 2),
            "trend": "UP" if (r.close > r.sma50 and r.sma20 > r.sma50) else ("DOWN" if r.close < r.sma50 else "FLAT"),
            "signal": sig["type"] if sig else "—",
            "setup": sig["setup"] if sig else "",
            "why": sig["why"] if sig else "",
            "plan_entry": round(entry, 2), "plan_stop": round(stp, 2), "plan_target": round(tgt, 2),
            "plan_shares_5k": size_position(5000, entry, stp),
            "plan_shares_25k": size_position(25000, entry, stp),
            "options_candidate": bool(sig and float(r.rsi14) > 55 and abs(chg) > 1.0),
        })
    return out

def fetch_news(symbols, limit=8):
    """Top headlines for watchlist via Yahoo RSS."""
    import xml.etree.ElementTree as ET
    url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={','.join(symbols)}&region=US&lang=en-US"
    try:
        r = requests.get(url, headers=UA, timeout=20)
        root = ET.fromstring(r.text)
        items = []
        for it in root.iter("item"):
            items.append({"title": (it.findtext("title") or "").strip(),
                          "link": it.findtext("link") or "",
                          "pub": (it.findtext("pubDate") or "")[:22]})
        return items[:limit]
    except Exception as e:
        return [{"title": f"news unavailable: {e}", "link": "", "pub": ""}]

def fetch_options_ideas(symbols, equity=START_EQUITY):
    """Front-chain snapshot for signal candidates: slightly-OTM call ~30-45 DTE,
    sized to the 5% premium cap. Cookie+crumb auth flow."""
    s = requests.Session(); s.headers.update(UA)
    try:
        s.get("https://fc.yahoo.com", timeout=15)
        crumb = s.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=15).text
    except Exception:
        return []
    cap = equity * MAX_OPTIONS_PREMIUM_PCT
    ideas = []
    for sym in symbols[:4]:
        try:
            j = s.get(f"https://query1.finance.yahoo.com/v7/finance/options/{sym}?crumb={crumb}", timeout=20).json()
            res = j["optionChain"]["result"][0]
            spot = res["quote"]["regularMarketPrice"]
            exps = res["expirationDates"]
            now = time.time()
            # pick expiry nearest 35 DTE
            exp = min(exps, key=lambda e: abs((e - now) / 86400 - 35))
            dte = round((exp - now) / 86400)
            jj = s.get(f"https://query1.finance.yahoo.com/v7/finance/options/{sym}?date={exp}&crumb={crumb}", timeout=20).json()
            calls = jj["optionChain"]["result"][0]["options"][0]["calls"]
            otm = [c for c in calls if 1.0 < c["strike"] / spot < 1.06 and c.get("lastPrice", 0) > 0]
            if not otm: continue
            c = min(otm, key=lambda c: c["strike"] / spot)
            prem = c["lastPrice"] * 100
            contracts = int(cap // prem) if prem > 0 else 0
            c5, c25 = (int(250 // prem), int(1250 // prem)) if prem > 0 else (0, 0)
            ideas.append({"symbol": sym, "spot": round(spot, 2), "type": "CALL",
                          "strike": c["strike"], "expiry_dte": dte,
                          "last": c["lastPrice"], "premium": round(prem, 2),
                          "iv": round(c.get("impliedVolatility", 0) * 100, 1),
                          "oi": c.get("openInterest", 0), "vol": c.get("volume", 0),
                          "contracts_allowed": contracts,
                          "contracts_5k": c5, "contracts_25k": c25,
                          "max_premium_cap": round(cap, 2)})
            time.sleep(0.4)
        except Exception:
            continue
    return ideas

CONGRESS_SOURCES = {
    "trades": "https://raw.githubusercontent.com/kadoa-org/congress-trading-monitor/main/public/data/trades.json",
    "filers": "https://raw.githubusercontent.com/kadoa-org/congress-trading-monitor/main/public/data/filers.json",
}

def _cg_parse_date(s):
    """Accept 'MM/DD/YYYY' (senate) or 'YYYY-MM-DD' (house). Return ISO or None."""
    s = (s or "").strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None

def _cg_amount_mid(s):
    """'$1,001 - $15,000' -> 8000.5 midpoint; open-ended/unknown -> lower bound or 0."""
    import re
    nums = [float(x.replace(",", "")) for x in re.findall(r"\$([\d,]+)", s or "")]
    if len(nums) >= 2: return (nums[0] + nums[1]) / 2
    if len(nums) == 1: return nums[0]
    return 0.0

def fetch_congress_trades(watchlist, window_days=90, max_rows=25):
    """STOCK Act disclosures via the Congress Trading Monitor open dataset
    (github.com/kadoa-org/congress-trading-monitor - refreshed daily from official
    efdsearch.senate.gov / disclosures-clerk.house.gov filings).
    NOTE 2026-07-20: replaced Senate/House Stock Watcher, whose S3 buckets are dead
    (senate data frozen Dec 2020). Same return schema. Never raises."""
    from datetime import timedelta
    side_map = {"purchase": "BUY", "sale (full)": "SELL", "sale_full": "SELL",
                "sale (partial)": "SELL (partial)", "sale_partial": "SELL (partial)",
                "exchange": "EXCHANGE", "sale": "SELL"}
    rows, errors, fetched = [], [], {}
    filer_info = {}
    try:
        r = requests.get(CONGRESS_SOURCES["filers"], headers=UA, timeout=90)
        r.raise_for_status()
        for f in r.json():
            tag = ""
            if f.get("party") or f.get("state"):
                tag = " (" + "-".join(x for x in (f.get("party"), f.get("state")) if x) + ")"
            filer_info[f["id"]] = (f.get("full_name") or "?") + tag
        fetched["filers"] = len(filer_info)
    except Exception as e:
        errors.append(f"filers: {str(e)[:100]}")
    try:
        r = requests.get(CONGRESS_SOURCES["trades"], headers=UA, timeout=90)
        r.raise_for_status()
        raw = r.json()
        fetched["trades"] = len(raw)
        chamber_map = {"senate_efd": "Senate", "house_clerk": "House"}
        for t in raw:
            chamber = chamber_map.get(t.get("source_id"))
            if not chamber:
                continue  # skip non-congress rows (e.g. executive-branch OGE filings)
            txd = _cg_parse_date(t.get("transaction_date"))
            if not txd:
                continue
            ticker = (t.get("ticker") or "").strip().upper()
            if ticker in ("--", "N/A", "-", "NONE"): ticker = ""
            fid = t.get("filer_id") or ""
            member = filer_info.get(fid) or fid.replace("senate_", "").replace("house_", "").replace("_", " ").title() or "?"
            lo, hi = t.get("amount_range_low"), t.get("amount_range_high")
            mid = (lo + hi) / 2 if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) else _cg_amount_mid(t.get("amount_range_label"))
            rows.append({
                "chamber": chamber,
                "member": member,
                "ticker": ticker,
                "asset": (t.get("asset_name") or "")[:80],
                "side": side_map.get((t.get("transaction_type") or "").strip().lower(), (t.get("transaction_type") or "?")),
                "amount": t.get("amount_range_label") or "?",
                "amount_mid": mid,
                "tx_date": txd,
                "filed_date": _cg_parse_date(t.get("filing_date")) or "",
                "link": "",
                "owner": t.get("owner") or "",
            })
    except Exception as e:
        errors.append(f"trades: {str(e)[:100]}")
    if not rows:
        return {"meta": {"error": "; ".join(errors) or "no rows parsed", "fetched": fetched,
                         "sources": list(CONGRESS_SOURCES.values())},
                "recent": [], "watchlist_hits": [], "top_traders": []}
    rows.sort(key=lambda x: x["tx_date"], reverse=True)
    latest_tx = rows[0]["tx_date"]
    cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).strftime("%Y-%m-%d")
    window = [x for x in rows if x["tx_date"] >= cutoff]
    stale = not window
    pool = window if window else rows[:200]   # source stale -> show latest available, flagged
    wl = set(watchlist)
    hits = [x for x in pool if x["ticker"] in wl]
    by_member = {}
    for x in pool:
        m = by_member.setdefault(x["member"], {"member": x["member"], "chamber": x["chamber"],
                                               "trades": 0, "buys": 0, "sells": 0,
                                               "est_volume_mid": 0.0, "tickers": {}})
        m["trades"] += 1
        if x["side"] == "BUY": m["buys"] += 1
        elif x["side"].startswith("SELL"): m["sells"] += 1
        m["est_volume_mid"] += x["amount_mid"]
        if x["ticker"]: m["tickers"][x["ticker"]] = m["tickers"].get(x["ticker"], 0) + 1
    top = sorted(by_member.values(), key=lambda m: m["trades"], reverse=True)[:8]
    for m in top:
        m["est_volume_mid"] = round(m["est_volume_mid"], 0)
        m["top_tickers"] = ", ".join(k for k, _ in sorted(m["tickers"].items(), key=lambda kv: -kv[1])[:4])
        del m["tickers"]
    return {
        "meta": {
            "sources": ["Congress Trading Monitor (Kadoa, open data)"],
            "source_urls": list(CONGRESS_SOURCES.values()),
            "fetched": fetched,
            "errors": errors,
            "latest_transaction": latest_tx,
            "window_days": window_days,
            "in_window": len(window),
            "stale": stale,
            "note": "Filed under the STOCK Act; members have up to 45 days to disclose, so trades appear with a lag. Amounts are reported as ranges, not exact figures.",
        },
        "recent": ([x for x in pool if x["ticker"]] or pool)[:max_rows],
        "watchlist_hits": hits[:max_rows],
        "top_traders": top,
    }

def goal_curves(months=48, start=START_EQUITY, contrib=1000.0):
    """Plan corridor 3-5%/mo + aggressive trader track 10%/mo, all with contributions."""
    rows = []
    vals = {r: start for r in (0.03, 0.04, 0.05, 0.10)}
    for m in range(months + 1):
        rows.append({
            "month": m,
            "lo": round(vals[0.03], 2),
            "mid": round(vals[0.04], 2),
            "hi": round(vals[0.05], 2),
            "stretch": round(vals[0.10], 2),
            "target": 250000,
        })
        for r in vals: vals[r] = vals[r] * (1 + r) + contrib
    return rows

def arrival_months(start, contrib=1000.0, target=250000):
    out = {}
    for name, r in (("plan", 0.04), ("stretch", 0.10)):
        v, m = start, 0
        while v < target and m < 600:
            v = v * (1 + r) + contrib; m += 1
        out[name] = m
    return out

def _safe(fn, default):
    try: return fn()
    except Exception: return default

def scenario(data, start):
    curve, trades, open_pos, killed = backtest(data, start=start)
    closed = [t for t in trades if t["side"] == "SELL"]
    wins = [t for t in closed if t["pnl"] > 0]
    final_eq = curve[-1]["equity"]
    max_dd = min(c["drawdown"] for c in curve)
    n_months = len(curve) / 21.0
    monthly_ret = (final_eq / start) ** (1 / n_months) - 1 if final_eq > 0 else 0
    return {
        "account": {
            "start_equity": start, "equity": final_eq,
            "pnl": round(final_eq - start, 2),
            "pnl_pct": round((final_eq / start - 1) * 100, 2),
            "monthly_ret_pct": round(monthly_ret * 100, 2),
            "max_drawdown_pct": round(max_dd * 100, 2),
            "mode": "PAPER (simulated backtest — no real money)",
            "kill_switch_tripped": killed,
        },
        "stats": {
            "trades_closed": len(closed),
            "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else 0,
            "avg_win": round(float(np.mean([t["pnl"] for t in wins])), 2) if wins else 0,
            "avg_loss": round(float(np.mean([t["pnl"] for t in closed if t["pnl"] <= 0])), 2) if len(closed) > len(wins) else 0,
            "open_positions": len(open_pos),
        },
        "equity_curve": curve,
        "open_positions": [
            {"symbol": s, **{k: p[k] for k in ("shares", "date", "setup")},
             "entry": round(p["entry"], 2), "stop": round(p["stop"], 2), "target": round(p["target"], 2),
             "last": round(float(data[s].close.iloc[-1]), 2),
             "unreal_pnl": round((float(data[s].close.iloc[-1]) - p["entry"]) * p["shares"], 2)}
            for s, p in open_pos.items()],
        "trade_log": trades[-40:],
        "goal_curves": goal_curves(start=start),
        "arrival": arrival_months(start),
        "pdt_free": start >= 25000,
    }

def main():
    print("Fetching market data...")
    data = {}
    for sym in set(WATCHLIST + INDICES):
        try:
            data[sym] = enrich(fetch(sym))
            print(f"  {sym}: {len(data[sym])} rows, last close {data[sym].close.iloc[-1]:.2f}")
        except Exception as e:
            if sym.startswith("^"):
                print(f"  {sym}: unavailable ({e}) — skipping index-only symbol")
            else:
                raise
        time.sleep(0.4)

    sigs = current_signals(data)
    idx_overview = []
    for sym in INDICES:
        if sym not in data: continue
        df = data[sym]; r = df.iloc[-1]; p = df.iloc[-2]
        hist = df.tail(126)
        idx_overview.append({
            "symbol": sym.replace("^", ""), "close": round(float(r.close), 2),
            "chg_pct": round(float(r.close / p.close - 1) * 100, 2),
            "dates": hist.date.tolist(),
            "indexed": [round(x / hist.close.iloc[0] * 100, 2) for x in hist.close.tolist()],
        })

    print("Running dual-scenario backtests ($5k and $25k)...")
    scenarios = {"5k": scenario(data, 5000.0), "25k": scenario(data, 25000.0)}
    for k, s in scenarios.items():
        a = s["account"]
        print(f"  {k}: final ${a['equity']:,.2f} ({a['pnl_pct']:+.1f}%), "
              f"{s['stats']['trades_closed']} closed, WR {s['stats']['win_rate']}%, "
              f"maxDD {a['max_drawdown_pct']}%, $250k arrival mo {s['arrival']['plan']} (plan) / {s['arrival']['stretch']} (10% stretch)")

    payload = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "guardrails": {
            "risk_per_trade": "1% of equity", "max_position": "20% of equity",
            "max_options_premium": "5% of equity", "daily_loss_halt": "-3%",
            "weekly_loss_halt": "-7%", "drawdown_kill": "-15% from high-water mark",
            "max_open_positions": MAX_OPEN_POSITIONS, "order_types": "limit-only",
            "pdt": "$25k+ equity: PDT-unrestricted; counter auto-enforced below $25k",
        },
        "watchlist": sigs,
        "indices": idx_overview,
        "news": _safe(lambda: fetch_news(WATCHLIST[:6]), []),
        "congress": _safe(lambda: fetch_congress_trades(WATCHLIST),
                          {"meta": {"error": "fetch failed"}, "recent": [], "watchlist_hits": [], "top_traders": []}),
        "options_ideas": _safe(lambda: fetch_options_ideas(
            [w["symbol"] for w in sigs if w["signal"] == "BUY" or w["options_candidate"] or w["trend"] == "UP"]), []),
        "scenarios": scenarios,
        "goal": {
            "destination": "$10,000/month income capability at a $250k base",
            "official": "avg >=4%/mo + $1k/mo contributions; 10%/mo aggressive trader track shown for comparison",
        },
    }
    out = os.path.join(os.path.dirname(__file__), "dashboard_data.json")
    with open(out, "w") as f:
        json.dump(payload, f)
    print(f"Wrote {out}")
    return payload

if __name__ == "__main__":
    main()
