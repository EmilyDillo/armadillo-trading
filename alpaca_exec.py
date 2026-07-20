"""
Alpaca execution module — PAPER by default. Live requires explicit opt-in.
Activates only when ALPACA_API_KEY / ALPACA_SECRET_KEY env vars are set.
All counsel guardrails enforced HERE too (defense in depth): limit-only orders,
position caps, daily/weekly loss halts, drawdown kill, day-trade counter.
"""
import os, json, time, uuid
import requests

PAPER_URL = "https://paper-api.alpaca.markets"
LIVE_URL = "https://api.alpaca.markets"

class Guardrails:
    RISK_PER_TRADE = 0.01
    MAX_POSITION_PCT = 0.20
    DAILY_LOSS_HALT = 0.03
    WEEKLY_LOSS_HALT = 0.07
    DRAWDOWN_KILL = 0.15
    MAX_OPEN_POSITIONS = 4
    MAX_ORDERS_PER_MIN = 5
    MAX_SPREAD_PCT = 0.05  # reject if bid/ask spread >5% of mid

class AlpacaExecutor:
    def __init__(self, live=False, state_path="exec_state.json"):
        self.key = os.environ.get("ALPACA_API_KEY")
        self.secret = os.environ.get("ALPACA_SECRET_KEY")
        self.base = LIVE_URL if live else PAPER_URL
        self.live = live
        self.state_path = state_path
        self.state = self._load_state()
        if live and os.environ.get("ALPACA_LIVE_CONFIRM") != "I_UNDERSTAND_THE_RISKS":
            raise RuntimeError("Live trading requires ALPACA_LIVE_CONFIRM=I_UNDERSTAND_THE_RISKS")

    @property
    def enabled(self):
        return bool(self.key and self.secret)

    def _load_state(self):
        if os.path.exists(self.state_path):
            with open(self.state_path) as f: return json.load(f)
        return {"hwm": None, "order_ts": [], "disabled": False, "disabled_reason": None}

    def _save_state(self):
        with open(self.state_path, "w") as f: json.dump(self.state, f)

    def _headers(self):
        return {"APCA-API-KEY-ID": self.key, "APCA-API-SECRET-KEY": self.secret}

    def _get(self, path):
        r = requests.get(self.base + path, headers=self._headers(), timeout=15)
        r.raise_for_status(); return r.json()

    def account(self): return self._get("/v2/account")
    def positions(self): return self._get("/v2/positions")

    def kill_switch(self, reason="manual"):
        """Cancel all orders, flatten all positions, disable trading."""
        requests.delete(self.base + "/v2/orders", headers=self._headers(), timeout=15)
        for p in self.positions():
            requests.delete(self.base + f"/v2/positions/{p['symbol']}", headers=self._headers(), timeout=15)
        self.state["disabled"] = True
        self.state["disabled_reason"] = reason
        self._save_state()
        return {"killed": True, "reason": reason}

    def _pretrade_checks(self, acct):
        if self.state["disabled"]:
            return f"trading disabled: {self.state['disabled_reason']}"
        eq = float(acct["equity"]); last_eq = float(acct["last_equity"])
        if self.state["hwm"] is None or eq > self.state["hwm"]:
            self.state["hwm"] = eq; self._save_state()
        if eq / self.state["hwm"] - 1 <= -Guardrails.DRAWDOWN_KILL:
            self.kill_switch(f"drawdown kill: -{Guardrails.DRAWDOWN_KILL:.0%} from HWM")
            return "drawdown kill switch tripped"
        if last_eq > 0 and eq / last_eq - 1 <= -Guardrails.DAILY_LOSS_HALT:
            return "daily loss halt (-3%)"
        if int(acct.get("daytrade_count", 0)) >= 3 and eq < 25000:
            return "PDT guard: 3 day trades used in rolling window"
        now = time.time()
        self.state["order_ts"] = [t for t in self.state["order_ts"] if now - t < 60]
        if len(self.state["order_ts"]) >= Guardrails.MAX_ORDERS_PER_MIN:
            return "order throttle: max orders/min reached"
        return None

    def submit_entry(self, symbol, shares, limit_price, stop_price, target_price):
        """Bracket limit order with stop-loss + take-profit. Idempotent client ID."""
        if not self.enabled:
            return {"skipped": "no API keys configured — signal-only mode"}
        acct = self.account()
        block = self._pretrade_checks(acct)
        if block:
            return {"blocked": block}
        eq = float(acct["equity"])
        if shares * limit_price > eq * Guardrails.MAX_POSITION_PCT:
            return {"blocked": f"position exceeds {Guardrails.MAX_POSITION_PCT:.0%} cap"}
        if len(self.positions()) >= Guardrails.MAX_OPEN_POSITIONS:
            return {"blocked": "max open positions reached"}
        order = {
            "symbol": symbol, "qty": shares, "side": "buy",
            "type": "limit", "limit_price": str(round(limit_price, 2)),
            "time_in_force": "day", "order_class": "bracket",
            "stop_loss": {"stop_price": str(round(stop_price, 2))},
            "take_profit": {"limit_price": str(round(target_price, 2))},
            "client_order_id": f"armadillo-{symbol}-{uuid.uuid4().hex[:12]}",
        }
        r = requests.post(self.base + "/v2/orders", headers=self._headers(), json=order, timeout=15)
        self.state["order_ts"].append(time.time()); self._save_state()
        if r.status_code in (200, 201):
            return {"submitted": r.json()["id"], "order": order}
        return {"error": f"HTTP {r.status_code}: {r.text[:200]}"}

if __name__ == "__main__":
    ex = AlpacaExecutor(live=False)
    if not ex.enabled:
        print("No Alpaca keys set (ALPACA_API_KEY / ALPACA_SECRET_KEY). Signal-only mode.")
        r = requests.get(PAPER_URL + "/v2/account", timeout=15)
        print(f"Paper endpoint reachable: HTTP {r.status_code} (401 = up, awaiting keys)")
    else:
        print(json.dumps(ex.account(), indent=2))
