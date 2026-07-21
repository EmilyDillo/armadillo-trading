"""
Daily automated paper-trading cycle:
fetch data -> compute signals -> submit guardrailed bracket orders to Alpaca paper
-> sync positions -> regenerate dashboard data + dashboard.html.
Sizing uses SCENARIO_EQUITY (the $25k plan), NOT the $100k default paper balance,
so results are honest for the real plan.
"""
import os, json, sys
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
        held = {p["symbol"] for p in ex.positions()}
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
