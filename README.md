# Rk-Kalshi-Agent

Paper-trading agent for Kalshi tennis and Bitcoin markets, with **optional**
live order placement under small hard caps. **Paper is the default.** Live is
opt-in from the dashboard after Connect (local `.env` credentials) and a
real-money confirmation. Connecting by itself does **not** send orders.

The paper signal is **Avellaneda–Stoikov reservation price + order-book
imbalance**, after Kalshi fees and spread. Hybrid / ChatGPT research may
inform signals; **risk gates always win**. This is **not financial advice**,
**not** a match-winner or Bitcoin price model, and there is **no guaranteed
profitable edge**. **Live losses are real.**

Kalshi tennis contracts are binary YES/NO event contracts (typically “player X
wins the match”). Bankroll target is about **$100**. Job-hunt / JobPilot code
does not belong here.

## Honesty first

Turning $100 into $100/day on tennis mids is not a realistic plan. This bot
polls the public REST book. That cannot beat informed, colocated, or
WebSocket-speed flow. Most scans should produce **no** trade.

Research on prediction-market microstructure (implementable on REST) supports
inventory-aware quotes (Avellaneda–Stoikov), short-horizon order-book
imbalance, and always subtracting fees plus spread. That is a **heuristic**,
not a proven alpha. There is no guaranteed profitable model for Kalshi-style
binary contracts.

`can_size_up` stays **locked off** (`allow_size_up: false`,
`min_fills_before_size_up: 200`). Do not size up until paper P&L is positive on
a large sample, and even then only after a deliberate config/code change.

## Architecture

```
Kalshi public REST  →  SignalEngine  →  RiskManager  →  PaperExecution
     (no auth)         (AS + OBI)      (caps/kill)     (fill @ YES mid)
                                                                  ↓
                                                         CSV + JSONL journal

Kalshi signed REST  →  same signals   →  RiskManager  →  LiveKalshiExecution
     (opt-in Live)                     (hard caps)      POST /portfolio/events/orders
                                                                  ↓
                                                    Live account view (Kalshi fills)
```

1. **Signal** (`rk_kalshi/signal.py`) is separate from execution. For each
   ticker it tracks a rolling mid history (σ in probability space) and paper
   inventory `q` (signed YES contracts). The Avellaneda–Stoikov reservation is
   `r = mid − q · γ · σ² · T_frac` (`T_frac` from time-to-close, else 1).
   Order-book imbalance `OBI = (bid_size − ask_size) / (bid_size + ask_size)`
   (Kalshi `yes_bid_size_fp` / `yes_ask_size_fp`; 0 if missing) tilts fair:
   `fair = r + κ · OBI · spread`. Half-spread and a Kalshi-style quadratic fee
   (`0.07 × P × (1−P)`, rounded up to the next cent) are subtracted; buy/sell
   only when net edge ≥ `edge_threshold_cents` (default 3¢). Wide or stale mids
   are skipped. Optional EMA / last-print fair (`use_ema_fallback`) is used
   only when inventory and OBI are idle. **Not a match pick.**
2. **Paper execution** (`rk_kalshi/execution.py`) fills at the live YES mid
   and never calls `create_order`. **Live execution** (opt-in) RSA-PSS-signs
   `POST /trade-api/v2/portfolio/events/orders` (Create Order V2: `bid`/`ask`,
   fixed-point `count`/`price`, `immediate_or_cancel`). Paper journal stays
   separate; live fills/orders show in the Live account panel.
3. **Risk** (`rk_kalshi/risk.py`): paper defaults max **$5** notional per
   ticker and daily loss kill-switch **$15**. Live clamps to default **$5**
   per trade (hard ceiling **$10** even if the UI asks higher) and default
   **$10** daily loss (hard ceiling **$25**). **No martingale**. `allow_size_up`
   stays **false** on the live path.
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

# Read-only live Kalshi portfolio (local API keys; no orders)
python3 -m rk_kalshi account
```

Optional: `python3 -m pip install -e .` then `rk-kalshi list-tennis-markets`.

## Dashboard (Windows)

The dashboard is a local FastAPI app. Defaults for **run research now**:
trading mode **Active**, paper signal **Hybrid** (AS+OBI filter, ChatGPT
anticipation confirm). Pick a **category** (ATP / WTA / Challenger / ITF /
Bitcoin 15m / daily), then a **live match or market**, then Start.
Modes (**Safe**, **Conservative**, **Active**, **Aggressive**) change
paper knobs only. Paste a Kalshi URL as an optional fallback.
API keys are never entered in the UI (`.env` only).

To run Hybrid research on Windows, put this in `.env` next to `config.yaml`
(never paste keys in the dashboard):

```
KALSHI_API_KEY_ID=...
KALSHI_PRIVATE_KEY_PATH=C:\Users\Rk\.kalshi\kalshi.key
KALSHI_ENVIRONMENT=prod
OPENAI_API_KEY=sk-...
```

Then `python -m rk_kalshi dashboard` → pick category + live match → leave
**Active** and **Hybrid** → Start (paper). ChatGPT is asked to anticipate score /
momentum swings and how those map to YES/NO mids. It is slow versus the
book, costs tokens, and is **not** a guaranteed edge.

To place **one real order path** on that same match: Connect first, check
**Enable live trading** and **I understand this spends real money**, then
Start. Caps apply ($5 default / $10 hard ceiling per trade). Uncheck Live
to return to paper. Never paste keys in the UI or in chat.

Example Kalshi URLs:

```
https://kalshi.com/markets/kxatpmatch/atp-tennis-match/kxatpmatch-26sep06cerblo
https://kalshi.com/markets/kxatpchallengermatch/challenger-atp-/kxatpchallengermatch-26sep06kimtam
https://kalshi.com/markets/kxbtc15m/bitcoin-price-up-down/kxbtc15m-26sep061845
```

A bare event ticker such as `KXATPCHALLENGERMATCH-26SEP06KIMTAM` also
works. Series pages (`/markets/kxatpmatch`) and non-Kalshi links are
rejected with a red error on the contract box.

**Stop** ends polling and leaves the paper book on screen so you can read
it. **Clear** (shown after Stop / while idle) archives then wipes the
*local* paper session: fills journal, bankroll state, P&L, live log, and
the selected contract. It does **not** touch a live Kalshi account.
Upcoming books are shown but not paper-traded while “Live matches only”
is on (unless a specific match is selected). Live order placement is
**opt-in** (Connect + confirmation). `can_size_up` stays locked.
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
interface unless you understand paper is default and live still requires
the on-screen confirmation.

Existing CLI commands (`list-tennis-markets`, `paper-run`, `show-pnl`) are
unchanged.

## Connect Kalshi (portfolio view)

The dashboard **Connect** button and `python -m rk_kalshi account` load your
real Kalshi balance, open positions, recent fills, and orders from **local
credentials only**. There is no API-key form in the UI. Connecting does
**not** turn on live order placement. Enable Live with the dashboard toggle
plus the real-money checkbox after Connect.

### Create API keys (demo + production)

Kalshi demo and production credentials are **not** interchangeable.

1. Log in:
   - Production: [https://kalshi.com](https://kalshi.com) → Account & security → API Keys
     (`https://kalshi.com/account/profile`)
   - Demo: [https://demo.kalshi.co](https://demo.kalshi.co) → same API Keys page
2. Click **Create Key** / **Create New API Key**.
3. Save both:
   - **API Key ID** (UUID shown on screen)
   - **Private key** (downloaded `.key` PEM — Kalshi cannot show it again)

### Windows: local `.env` only

Prefer a file path. Never commit `.key`, `.pem`, `.env`, or
`data/kalshi_account.json`. Never paste keys into the dashboard.

Command Prompt:

```bat
mkdir %USERPROFILE%\.kalshi
move %USERPROFILE%\Downloads\kalshi-key.key %USERPROFILE%\.kalshi\kalshi.key

cd path\to\Rk-Kalshi-Agent
copy .env.example .env
notepad .env
```

In `.env` (edit locally; the UI will not accept keys):

```
KALSHI_API_KEY_ID=a952bcbe-ec3b-4b5b-b8f9-11dae589608c
KALSHI_PRIVATE_KEY_PATH=C:\Users\Rk\.kalshi\kalshi.key
KALSHI_ENVIRONMENT=prod
```

Use `KALSHI_ENVIRONMENT=demo` with a demo key. Recommended REST roots:

| Environment | Base URL |
| --- | --- |
| Production | `https://external-api.kalshi.com/trade-api/v2` |
| Demo | `https://external-api.demo.kalshi.co/trade-api/v2` (`demo-api.kalshi.co` still works) |

Then `python -m rk_kalshi dashboard` → **Connect**. Status shows
Connected / Disconnected / Error. If keys are missing, the UI says to set
`.env` locally. **Refresh** (optional auto-refresh) reloads the live trades
panel. Public market reads still work without keys. **Disconnect** drops the
in-memory session (it does not delete your `.env`).

```bat
python -m rk_kalshi account
```

prints the same read-only snapshot in the terminal.

### Optional ChatGPT paper research

Set `OPENAI_API_KEY` in the same local `.env`. There is **no** OpenAI key
field on the dashboard. In `config.yaml` (or the Start panel):

- `signal.mode: as_obi` — local Avellaneda–Stoikov + order-book imbalance
- `signal.mode: hybrid` — AS+OBI proposes; ChatGPT confirms/skips with
  forward-looking (anticipation) research (default in `config.yaml`)
- `signal.mode: llm` — ChatGPT proposes paper buy/sell (still fee- and risk-gated)

Default model is `gpt-4o-mini` (`signal.llm_model`). Calls are rate-limited
(`llm_min_interval_s`). In hybrid/llm the model is asked to anticipate near-term
tennis score/momentum swings (or short-horizon Bitcoin drift) and map those to
YES/NO mid moves — not just restate the current odds. That research is
**advisory**, **slow** versus the book, costs API tokens, and is **not** a
guaranteed edge or a match predictor. On the live path those research notes
still cannot bypass risk caps, the daily kill-switch, or `allow_size_up: false`.

Start-panel trading modes (paper knobs only): **Safe**, **Conservative**,
**Active** (default), **Aggressive**.

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
| `signal.gamma` | `0.25` | Avellaneda–Stoikov risk aversion (inventory skew) |
| `signal.kappa` | `1.5` | Order-book imbalance weight (`obi_weight` alias) |
| `signal.sigma_floor` | `0.04` | Minimum mid volatility in probability space |
| `signal.use_ema_fallback` | `false` | Optional last-print / EMA fair when OBI and inventory are idle |
| `signal.mode` | `hybrid` | `as_obi` (local default in code), `hybrid`, or `llm` (ChatGPT; key from `.env`) |
| `signal.llm_model` | `gpt-4o-mini` | OpenAI model for llm/hybrid paper research (`gpt-4.1-mini` also fine) |
| `signal.llm_min_interval_s` | `20` | Minimum seconds between ChatGPT calls |
| `signal.llm_max_markets_per_call` | `6` | Cap markets sent to ChatGPT per cycle |
| `kalshi.series_tickers` | `KXATPMATCH`, `KXWTAMATCH`, `KXITFWMATCH`, `KXITFMMATCH`, `KXATPCHALLENGERMATCH` | Match series |
| `live.enabled` | `false` | YAML default; dashboard still requires Connect + confirmation |
| `live.max_dollars_per_ticker` | `5` | Live default $/trade (hard ceiling $10 in code) |
| `live.daily_loss_limit` | `10` | Live daily kill-switch (hard ceiling $25 in code) |
| `account.environment` | `prod` | Default demo/prod if `.env` omits `KALSHI_ENVIRONMENT` — live start **refuses** a blank/ambiguous env |

Public market-data base URL: `https://external-api.kalshi.com/trade-api/v2`.
Those reads do not need API keys. Authenticated portfolio GETs use RSA-PSS
headers and the demo or prod Trade API host you selected.

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

## Live trading (opt-in, hard caps)

Paper stays the default and stays selectable. Live must be armed in the
dashboard:

1. Set `KALSHI_API_KEY_ID`, `KALSHI_PRIVATE_KEY_PATH`, and
   `KALSHI_ENVIRONMENT` (`demo` or `prod`) in a **local `.env`**. Never paste
   keys in the UI or in chat.
2. **Connect**. Confirm the Live account panel shows your real balance.
3. Check **Enable live trading** and **I understand this spends real money**.
4. Pick one category + match (Active + Hybrid is fine). Start.

Live posts Kalshi **Create Order V2**:
`POST /trade-api/v2/portfolio/events/orders` with RSA-PSS headers already
used for account reads (`timestamp_ms + METHOD + path`, path only). Body
uses `side` `bid`/`ask`, fixed-point `count`/`price`,
`time_in_force=immediate_or_cancel`, `self_trade_prevention_type=taker_at_cross`.
Cancel uses `DELETE /portfolio/events/orders/{order_id}` when Stop or the
kill-switch hits a resting remainder.

**Non-bypassable live caps** (code, not YAML):

- Max **$5** per trade/ticker by default; UI/style values above **$10** are
  clamped to $10.
- Daily loss kill-switch default **$10** (hard ceiling $25). New live orders
  stop for the UTC day.
- `allow_size_up: false` and no martingale, even if config is flipped.
- Contract count is limited so notional + fee stay under the dollar cap.
- Live start is refused if credentials are missing or `KALSHI_ENVIRONMENT` is
  blank / not `demo`|`prod`, or demo/prod does not match the host.

Live fills and orders appear in **Live account view** (Kalshi). The paper
fill CSV/JSONL is not used for live fills.

**Losses are real.** Uncheck Live (or Disconnect) to return to paper. CLI
`paper-run` never posts live orders.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Coverage includes ticker dollar caps, no-martingale, daily kill-switch, fill
logging, locked schema field names, `can_size_up` staying false, live disabled
by default, live hard ceilings, paper never calling `create_order`, and
RSA-PSS signing for Create Order V2.
