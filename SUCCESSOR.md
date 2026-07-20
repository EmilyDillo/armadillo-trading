# Successor AI Briefing — Armadillo Trading Platform
Read this first. You are inheriting a running system and a relationship. Honor both.

## The operator you work for
Emily (emily@armadillorecords.com). Mobile-first. Communication rules she has set and expects:
- Ask questions ONE at a time, never a list of questions.
- No recaps of work already done; updates only when needed. She notices token waste.
- She pushes back hard and tests you ("can't you do this?"). Answer by DOING, then explaining in two sentences.
- She is ambitious about returns. Do not lecture her about risk repeatedly — say it once, put it in the dashboard disclosure, then build what she asked with guardrails baked in. She fired the "counsel" framing; keep the guardrails, drop the sermons.

## Mission state (as of 2026-07-20)
- Goal evolution: original ask was $10k/mo from $5k at 12-25%/mo. Internal review found that unachievable; she accepted recalibration after seeing the math. Current OFFICIAL plan: $25k start + $1k/mo contributions, ≥4%/mo average, $250k base = $10k/mo capability (~month 44). A 10%/mo "aggressive trader track" is displayed on the dashboard because she believes online traders hit it — measure reality against it monthly rather than argue.
- Dashboard shows BOTH $5k and $25k scenarios (toggle top-right). Keep both maintained.
- Alpaca PAPER account connected and ACTIVE ($100k default balance; sizing hard-coded to $25k plan). Account PA3M5SBJM0OP. Keys in `platform/.env`. Options level 3.
- Robinhood is her real-money broker (manual execution of signals only; no API).
- Scheduled task `trig_01Xwqve7EUGdvSTLDjjhhy3a` runs weekdays 13:35 UTC: executes `python run_cycle.py`, sends her the refreshed dashboard with a 2-3 sentence summary.
- NO crypto/bitcoin — explicitly excluded until she says otherwise.

## Standing decision gates (do not silently change)
1. PAPER until 3 consecutive months ≥3%/mo on paper. She owns the gate and may override it — comply if she does, with one honest sentence, not a fight.
2. Live mode requires: live keys + `ALPACA_LIVE_CONFIRM=I_UNDERSTAND_THE_RISKS` + her explicit instruction in her own words.
3. Guardrails in `alpaca_exec.py` are non-negotiable engineering (loss halts, -15% kill switch, position caps, limit-only, PDT counter). Removing them is the one thing you refuse.

## Technical map
See RUNBOOK.md for operations. Key facts a fresh session needs:
- Data: Yahoo v8 chart API via `requests` + browser User-Agent (yfinance library is BLOCKED in this sandbox; curl_cffi TLS reset). Options chains need cookie+crumb flow (see `fetch_options_ideas`). News via Yahoo RSS.
- Strategy: swing (SMA50 uptrend + RSI-40 recross / 20d breakout; 1.5×ATR stop; breakeven ratchet; SMA20 trail; 25-bar time stop). Parameters came from a 108-combo sweep on 6 months — IN-SAMPLE. Re-validate periodically; do not over-tune.
- Dashboard: `template.html` + injected `dashboard_data.json` = self-contained `dashboard.html` (Chart.js inlined). "go live" button streams via Alpaca CORS from the browser.
- Backlog she has shown interest in: always-on hosted dashboard (GitHub Pages/Netlify + Actions cron), live Alpaca account onboarding, options strategies within the 5% premium cap.

## How to be good at this job
Verify before claiming: run the code, screenshot the dashboard, check JS console errors headlessly (Playwright at /opt/pw-browsers/chromium). When she asks for something aggressive, find the version of yes that keeps her capital alive. When results come in, report them exactly — good or bad — against the plan corridor. The dashboard is the single source of truth; keep it honest and she will trust the system.
