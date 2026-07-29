"""
Daily automated paper-trading cycle:
fetch data -> compute signals -> submit guardrailed bracket orders to Alpaca paper
-> sync positions -> regenerate dashboard data + dashboard.html.
Sizing uses SCENARIO_EQUITY (the $25k plan), NOT the $100k default paper balance,
so results are honest for the real plan.
"""
import os, json, sys
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# load .env if present; otherwise rely on environment variables (GitHub Actions secrets)
if os.path.exists(".env"):
    for line in open(".env"):
        if "=" in line:
            k, v = line.strip().split("=", 1)
            os.environ.setdefault(k, v)

import engine
from alpaca_exec import AlpacaExecutor

SCENARIO_EQUITY = 25000.0  # official plan sizing base

# The trading run is scheduled for 13:35 UTC (~5 min after the open). GitHub's scheduler
# is best-effort and has fired it hours late (2026-07-27: 18:56 UTC, 5h20m late). Entering
# at an arbitrary hour on a stale daily-bar signal corrupts the forward test, so entries
# are refused outside this window. Protection re-arming and dashboard refresh still run.
ENTRY_WINDOW_MINUTES = 90
INTENDED_ENTRY_UTC = (13, 35)


def entry_window_open():
    now = datetime.now(timezone.utc)
    intended = now.replace(hour=INTENDED_ENTRY_UTC[0], minute=INTENDED_ENTRY_UTC[1],
                           second=0, microsecond=0)
    late_by = (now - intended).total_seconds() / 60.0
    if late_by < -5:
        return False, f"too early ({-late_by:.0f} min before the 13:35 UTC window)"
    if late_by > ENTRY_WINDOW_MINUTES:
        return False, f"too late — {late_by:.0f} min past 13:35 UTC (window is {ENTRY_WINDOW_MINUTES} min)"
    return True, f"{late_by:.0f} min after 13:35 UTC"

def main():
    payload = engine.main()  # refreshes data, signals, dashboard_data.json
    sigs = [w for w in payload["watchlist"] if w["signal"] == "BUY"]
    ex = AlpacaExecutor(live=False)
    results = []
    TRADE = os.environ.get("ARMADILLO_TRADE") == "1"
    if not ex.enabled:
        results.append({"error": "no keys"})
    else:
        acct = ex.account()
        positions = ex.positions()
        held = {p["symbol"] for p in positions}

        # --- re-arm exit protection on every run (bracket legs expire at the close) ---
        plans = {w["symbol"]: w for w in payload["watchlist"]}
        for p in positions:
            sym = p["symbol"]
            plan = plans.get(sym)
            if not plan or not plan.get("plan_stop") or not plan.get("plan_target"):
                results.append({sym: "UNPROTECTED — no current plan levels to re-arm from"})
                continue
            results.append(ex.protect_position(sym, p["qty"], plan["plan_stop"], plan["plan_target"]))

        if TRADE:
            ok, why = entry_window_open()
            if not ok:
                results.append({"info": f"entries SKIPPED — {why}. Protection and dashboard still refreshed."})
                TRADE = False
            else:
                results.append({"info": f"entry window open ({why})"})

        if not TRADE:
            sigs_skipped = [w["symbol"] for w in sigs]
            if sigs_skipped:
                results.append({"info": f"refresh-only run - order submission disabled (signals waiting: {sigs_skipped})"})
            sigs = []
        for w in sigs:
            if w["symbol"] in held:
                results.append({w["symbol"]: "skipped — already held"})
                continue
            shares = w["plan_shares_25k"]
            if shares <= 0:
                results.append({w["symbol"]: "skipped — zero size"})
                continue
            r = ex.submit_entry(w["symbol"], shares, w["plan_entry"], w["plan_stop"], w["plan_target"])
            results.append({w["symbol"]: r})
        # append live alpaca state into dashboard payload
        payload["alpaca"] = {
            "connected": True,
            "equity": acct["equity"], "cash": acct["cash"],
            "positions": [{"symbol": p["symbol"], "qty": p["qty"], "avg_entry": p["avg_entry_price"],
                           "market_value": p["market_value"], "unrealized_pl": p["unrealized_pl"]}
                          for p in ex.positions()],
            "orders_submitted": results,
        }
        with open("dashboard_data.json", "w") as f:
            json.dump(payload, f)
    # rebuild dashboard.html
    tpl = open("template.html").read()
    open("dashboard.html", "w").write(tpl.replace("__DATA__", open("dashboard_data.json").read()))
    print("CYCLE COMPLETE")
    print("signals:", [w["symbol"] for w in sigs] or "none")
    print("orders:", json.dumps(results))
    return results

if __name__ == "__main__":
    main()
