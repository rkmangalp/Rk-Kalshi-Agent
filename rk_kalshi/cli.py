from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rk_kalshi.config import load_config
from rk_kalshi.journal import read_fills, summarize_pnl
from rk_kalshi.models import MarketSnapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rk-kalshi",
        description="Paper-trade Kalshi tennis markets. No live orders.",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to YAML config (default: config.yaml)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list-tennis-markets", help="List open tennis markets from Kalshi public REST")

    run = sub.add_parser("paper-run", help="Scan markets and paper-fill at live YES mid")
    run.add_argument("--once", action="store_true", help="Run a single scan cycle (default)")
    run.add_argument("--cycles", type=int, default=None, help="Run N scan cycles")
    run.add_argument("--sleep", type=float, default=None, help="Override seconds between cycles")

    sub.add_parser("show-pnl", help="Summarize paper fill logs")

    sub.add_parser(
        "account",
        help="Show connected Kalshi account portfolio (read-only; no live orders)",
    )

    dash = sub.add_parser(
        "dashboard",
        aliases=["serve"],
        help="Local paper-trading web dashboard (localhost, no live orders)",
    )
    dash.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address (default: 127.0.0.1 localhost only)",
    )
    dash.add_argument("--port", type=int, default=8765, help="Port (default: 8765)")
    dash.add_argument(
        "--open",
        action="store_true",
        help="Open the dashboard URL in a browser",
    )

    args = parser.parse_args(argv)
    config_path = Path(args.config) if Path(args.config).exists() else None
    cfg = load_config(config_path) if config_path else load_config()

    if args.command == "list-tennis-markets":
        return _cmd_list(cfg)
    if args.command == "paper-run":
        cycles = 1 if args.once or args.cycles is None else args.cycles
        if args.cycles is not None and args.cycles < 1:
            print("error: --cycles must be >= 1", file=sys.stderr)
            return 2
        return _cmd_paper_run(cfg, cycles=cycles, sleep_s=args.sleep)
    if args.command == "show-pnl":
        return _cmd_show_pnl(cfg)
    if args.command == "account":
        return _cmd_account(cfg)
    if args.command in {"dashboard", "serve"}:
        return _cmd_dashboard(
            cfg,
            host=args.host,
            port=args.port,
            open_browser=args.open,
            config_path=config_path,
        )
    parser.error(f"unknown command {args.command}")
    return 2


def _cmd_list(cfg) -> int:
    from rk_kalshi.client import KalshiPublicClient

    with KalshiPublicClient(cfg) as client:
        markets, latency_ms = client.list_tennis_markets()
    print(
        f"open markets: {len(markets)}  "
        f"series={','.join(cfg.enabled_series_tickers())}  "
        f"latency_ms={latency_ms:.1f}"
    )
    if not markets:
        print("no open markets returned (off-season or empty series filter)")
        return 0
    print(
        f"{'ticker':<42} {'match':<36} {'bid':>6} {'ask':>6} {'mid':>6} {'last':>6} {'spr¢':>5} {'vol':>8}"
    )
    for market in markets:
        print(_format_market(market))
    return 0


def _cmd_paper_run(cfg, cycles: int, sleep_s: float | None) -> int:
    from rk_kalshi.execution import LiveKalshiExecution
    from rk_kalshi.runner import PaperRunner

    if cfg.live_enabled:
        print("warning: live.enabled is ignored; LiveKalshiExecution stays disabled", file=sys.stderr)
        try:
            LiveKalshiExecution(enabled=True).submit()
        except Exception as exc:  # noqa: BLE001 — surface the stub
            print(f"live stub: {exc}", file=sys.stderr)

    runner = PaperRunner(cfg)
    try:
        fills = runner.run_cycles(cycles, sleep_s=sleep_s)
    finally:
        runner.close()

    print(
        f"paper-run cycles={cycles} fills={len(fills)} "
        f"can_size_up=false (locked: allow_size_up={cfg.allow_size_up}, "
        f"min_fills_before_size_up={cfg.min_fills_before_size_up})"
    )
    if not fills:
        print("no paper fills: net edge after spread+fee did not clear threshold, or risk blocked")
        return 0
    for fill in fills:
        print(
            f"{fill.timestamp} {fill.side:4} {fill.ticker} mid={fill.live_mid:.4f} "
            f"edge={fill.edge_cents:.2f}¢ fee={fill.fee:.2f} pnl={fill.running_pnl:.4f} "
            f"latency_ms={fill.latency_ms:.1f} can_size_up={fill.can_size_up}"
        )
        print(f"  thesis: {fill.edge_thesis}")
    return 0


def _cmd_show_pnl(cfg) -> int:
    rows = read_fills(cfg.fill_log_csv)
    summary = summarize_pnl(rows)
    print(f"fills={summary['fills']}  running_pnl={summary['running_pnl']:.4f}  "
          f"realized_sum={summary['realized_delta_sum']:.4f}  "
          f"can_size_up={summary['can_size_up']}")
    if summary["cash_after"] is not None:
        print(f"cash_after={summary['cash_after']:.4f}  log={cfg.fill_log_csv}")
    if not rows:
        print("no paper fills logged yet — run: python -m rk_kalshi paper-run --once")
        return 0
    print(f"{'ticker':<42} {'fills':>5} {'realized':>10}")
    for ticker, bucket in sorted(summary["by_ticker"].items()):
        print(f"{ticker:<42} {bucket['fills']:>5} {bucket['realized_delta']:>10.4f}")
    return 0


def _format_market(market: MarketSnapshot) -> str:
    mid = market.yes_mid
    spr = market.spread_cents
    return (
        f"{market.ticker:<42} {_clip(market.event_name, 36):<36} "
        f"{market.yes_bid:6.3f} {market.yes_ask:6.3f} "
        f"{(mid if mid is not None else 0):6.3f} {market.last_price:6.3f} "
        f"{(spr if spr is not None else 0):5.1f} {market.volume:8.1f}"
    )


def _cmd_account(cfg) -> int:
    from rk_kalshi.account import AccountService, default_store_path, format_account_cli

    service = AccountService(
        store_path=default_store_path(cfg.state_path.parent),
        timeout_s=cfg.request_timeout_s,
    )
    try:
        status = service.snapshot()
        if status["status"] == "disconnected" and not status.get("api_key_id_suffix"):
            print(format_account_cli(None, status))
            print(
                "not connected — set KALSHI_API_KEY_ID + KALSHI_PRIVATE_KEY_PATH in .env"
            )
            return 1
        try:
            portfolio = service.portfolio()
        except Exception as exc:  # noqa: BLE001 — surface in CLI
            print(format_account_cli(None, service.snapshot()))
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(format_account_cli(portfolio, portfolio.get("account") or status))
        return 0
    finally:
        service.close()


def _cmd_dashboard(cfg, host: str, port: int, open_browser: bool, config_path=None) -> int:
    try:
        from rk_kalshi.dashboard import serve
    except ImportError:
        print(
            "error: dashboard extras missing — pip install -r requirements.txt "
            "(fastapi, uvicorn)",
            file=sys.stderr,
        )
        return 2
    if port < 1 or port > 65535:
        print("error: --port must be 1..65535", file=sys.stderr)
        return 2
    serve(cfg, host=host, port=port, open_browser=open_browser, config_path=config_path)
    return 0


def _clip(text: str, width: int) -> str:
    text = text.replace("\n", " ")
    return text if len(text) <= width else text[: width - 1] + "…"
