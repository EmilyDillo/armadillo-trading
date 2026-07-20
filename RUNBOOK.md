# Armadillo Trading Platform — Operator Runbook

Self-contained. No Claude required. Python 3.10+ and `pip install requests pandas numpy`.

## Files
- `engine.py` — data (Yahoo Finance v8 API), indicators, swing strategy, dual $5k/$25k backtests, signal generation. Writes `dashboard_data.json`.
- `alpaca_exec.py` — Alpaca order execution. Paper by default. All guardrails enforced in code.
- `run_cycle.py` — THE DAILY JOB: fetch → signals → submit guardrailed bracket orders (paper) → sync positions → rebuild `dashboard.html`.
- `template.html` — dashboard template (Chart.js inlined). `run_cycle.py` injects fresh data into it.
- `.env` — Alpaca keys (`ALPACA_API_KEY`, `ALPACA_SECRET_KEY`). Paper keys today. NEVER commit this file anywhere public.
- `dashboard.html` — the output. Open in any browser. "⚡ go live" streams real-time via your keys.

## Daily operation
```
python run_cycle.py
```
Run once per weekday, ideally a few minutes after 9:30 AM ET open. Output: signals found, orders submitted, dashboard.html rebuilt.

## Scheduling without Claude (pick one)
- **Any computer (Mac/Linux)**: `crontab -e` → `35 9 * * 1-5 cd /path/to/platform && python run_cycle.py` (system local time = ET assumed; adjust otherwise).
- **Windows**: Task Scheduler → daily 9:35 AM → `python run_cycle.py`.
- **GitHub Actions (free, no computer needed)**: private repo, add keys as repo Secrets, workflow with `schedule: cron '35 13 * * 1-5'` (UTC) running the same command. Commit dashboard.html as an artifact or publish via GitHub Pages for an always-on URL.

## Strategy parameters (engine.py)
Entry: uptrend (close>SMA50, SMA20>SMA50) + RSI-40 recross OR 20-day breakout.
Exit: 1.5×ATR stop → breakeven ratchet after 1R → trail below SMA20 → 25-bar time stop.
Sizing: 1% equity risk per trade, hard-capped at 20% of equity per position, max 4 positions.

## Guardrails (DO NOT REMOVE — they are the system's survival mechanism)
- Daily loss halt −3% · weekly halt −7% · drawdown kill switch −15% from high-water mark (flattens everything and disables trading)
- Limit orders only, bracket (stop + target attached at entry)
- Options premium ≤5% of equity per play · PDT counter enforced below $25k equity
- Live trading requires BOTH: live keys in `.env` AND env var `ALPACA_LIVE_CONFIRM=I_UNDERSTAND_THE_RISKS`

## Going live (real money) — checklist
1. Paper record: ≥3 consecutive months ≥3%/mo (check dashboard monthly returns).
2. Open + fund live Alpaca account; generate LIVE keys; put in `.env`.
3. In `run_cycle.py` change `AlpacaExecutor(live=False)` → `live=True`; set the confirm env var.
4. Start at half-size (halve RISK_PER_TRADE to 0.005) for the first month.
5. Kill switch is `AlpacaExecutor().kill_switch("manual")` — cancels all orders, flattens all positions, disables trading.

## The plan (for whoever operates this)
$25k start + $1k/mo contributions · target ≥4%/mo average · $250k base ≈ $10k/mo capability (~month 44; month ~22 at a sustained 10%/mo). $5k scenario: same engine, arrival ~month 57 / ~30. Dashboard toggles both. Measure reality against the corridor monthly; recalibrate on data, not hope.

Not financial advice. Trading involves risk of total loss.
