# Rk-Kalshi-Agent

Paper-trading agent for Kalshi tennis markets. This repo is **paper-trade only**:
it reads public Kalshi REST market data, computes a transparent edge, and
simulates fills. It does **not** send live orders.

Kalshi tennis contracts are binary YES/NO event contracts (typically “player X
wins the match”). Bankroll target is about **$100**. Job-hunt / JobPilot code
does not belong here.

## Honesty first

Turning $100 into $100/day on tennis mids is not a realistic plan. This bot
polls the public REST book. That cannot beat informed, colocated, or
WebSocket-speed flow. Most scans should produce **no** trade. The signal is a
microstructure heuristic (last print / EMA vs mid, after spread and fee) — not
a match-winner model. Treat paper P&L as an audit of costs and risk, not an
edge proof.

`can_size_up` stays **locked off** (`allow_size_up: false`,
`min_fills_before_size_up: 200`). Do not size up until paper P&L is positive on
a large sample, and even then only after a deliberate config/code change.

## Architecture

```
Kalshi public REST  →  TennisSignalEngine  →  RiskManager  →  PaperExecution
     (no auth)            (edges+thesis)     (caps/kill)     (fill @ YES mid)
                                                                  ↓
                                                         CSV + JSONL journal
```

1. **Signal** (`rk_kalshi/signal.py`) is separate from execution. It ingests
   live tennis markets, estimates fair YES from last trade + EMA, subtracts
   half-spread and a Kalshi-style quadratic fee
   (`0.07 × P × (1−P)`, rounded up to the next cent), and emits buy/sell only
   when net edge ≥ `edge_threshold_cents` (default 3¢). Wide or stale mids are
   skipped. A last print more than `max_last_dislocation_cents` (default 8¢)
   from mid is treated as a stale tape, not fair value.
2. **Paper execution** (`rk_kalshi/execution.py`) fills at the live YES mid.
   `LiveKalshiExecution` always raises; live trading is disabled.
3. **Risk** (`rk_kalshi/risk.py`): max **$5** notional per ticker (default),
   daily loss kill-switch **$15** (mark-to-market), **no martingale** (size
   never increases after a losing close — enforced in code even if config is
   flipped).
4. **Latency**: each cycle records `latency_ms` from the Kalshi HTTP scan.

## Setup

```bash
python3 -m pip install -r requirements.txt
```

Dependencies are intentionally small: `httpx`, `pyyaml`, plus `fastapi` and
`uvicorn` for the local dashboard.

## CLI

```bash
# Open ATP / WTA / ITF match markets (public REST, no key)
python3 -m rk_kalshi list-tennis-markets

# One scan; paper-fill only if net edge clears the threshold
python3 -m rk_kalshi paper-run --once

# Repeat N cycles (sleep from config, override with --sleep)
python3 -m rk_kalshi paper-run --cycles 3 --sleep 5

# Summarize data/fills.csv
python3 -m rk_kalshi show-pnl

# Local web dashboard (localhost only, paper mode locked)
python3 -m rk_kalshi dashboard
```

Optional: `python3 -m pip install -e .` then `rk-kalshi list-tennis-markets`.

## Dashboard (Windows)

The dashboard is a local FastAPI app: a **Start** page sets paper bankroll
(default $100), max $ per trade, and daily loss, then polls tennis markets
until **Stop**. It **cannot** place live orders. `can_size_up` stays locked.
Chosen amounts are written to `data/dashboard_session.json` and
`config.yaml` so the next paper-run uses them. Default bind is
`127.0.0.1:8765`.

From Command Prompt:

```bat
cd path\to\Rk-Kalshi-Agent
python -m venv .venv
.venv\Scripts\activate.bat
python -m pip install -r requirements.txt
python -m rk_kalshi dashboard
```

From PowerShell:

```powershell
cd path\to\Rk-Kalshi-Agent
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m rk_kalshi dashboard
```

Then open [http://127.0.0.1:8765](http://127.0.0.1:8765) in a browser.

Equivalent: `python -m rk_kalshi serve`. Optional `--port 8765`, `--open` to
launch the browser, `--host 127.0.0.1`. Do not point this UI at a public
interface unless you understand it still paper-trades only.

Existing CLI commands (`list-tennis-markets`, `paper-run`, `show-pnl`) are
unchanged.

## Config

Defaults live in `config.yaml`:

| Knob | Default | Meaning |
| --- | --- | --- |
| `bankroll.starting_cash` | `100` | Paper cash |
| `risk.max_dollars_per_ticker` | `5` | Exposure cap per market ticker |
| `risk.daily_loss_limit` | `15` | Kill new trades for the UTC day |
| `risk.allow_martingale` | `false` | Ignored if true — code still refuses |
| `sizing.allow_size_up` | `false` | **Locked** |
| `sizing.min_fills_before_size_up` | `200` | Unlock floor (still needs +P&L) |
| `signal.edge_threshold_cents` | `3.0` | Net edge after spread + fee |
| `kalshi.series_tickers` | `KXATPMATCH`, `KXWTAMATCH`, `KXITFWMATCH` | Match series |
| `live.enabled` | `false` | Cannot enable the live stub |

Public base URL: `https://external-api.kalshi.com/trade-api/v2`. Reads do not
need API keys.

## Paper fill log (CSV + JSONL)

Written to `data/fills.csv` and `data/fills.jsonl`. Field names are locked in
`rk_kalshi/schema.py`.

**Audit-core (exact names):** `timestamp`, `ticker`, `side`, `fill_price`,
`live_mid`, `edge_thesis`, `running_pnl`.

**Also required:** `event_name`, `match_id` (Kalshi `event_ticker`),
`edge_cents` (net edge on a 0–100¢ scale).

**Also logged:** `edge_bps` (`edge_cents × 100`), `contracts`, `fee`,
`cash_after`, `mode`, `latency_ms`, `can_size_up`, `realized_delta`.

`fill_price` equals the live YES mid on the paper path. `can_size_up` is
`false` while sizing is locked.

## Live trading (not implemented)

`LiveKalshiExecution` is a stub that raises `LiveTradingDisabledError`. When a
human later enables live orders, Kalshi expects:

1. API key id + RSA private key from account settings.
2. RSA-PSS SHA-256 signature of `timestamp_ms + METHOD + path` (path only; no
   query string). MGF1-SHA256, salt length = digest length.
3. Headers: `KALSHI-ACCESS-KEY`, `KALSHI-ACCESS-TIMESTAMP`,
   `KALSHI-ACCESS-SIGNATURE`.
4. `POST /trade-api/v2/portfolio/events/orders` (Create Order V2) with
   `ticker`, `side` (`bid`/`ask`), fixed-point `count` and `price`,
   `time_in_force`, `self_trade_prevention_type`. The legacy
   `POST /portfolio/orders` (yes/no + buy/sell) was removed.

Do not implement or arm that path until paper P&L is positive on a large
sample.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Coverage includes ticker dollar caps, no-martingale, daily kill-switch, fill
logging, locked schema field names, and `can_size_up` staying false.
