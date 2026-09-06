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

    args = parser.parse_args(argv)
    cfg = load_config(Path(args.config)) if Path(args.config).exists() else load_config()

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
    parser.error(f"unknown command {args.command}")
    return 2


def _cmd_list(cfg) -> int:
    from rk_kalshi.client import KalshiPublicClient

    with KalshiPublicClient(cfg) as client:
        markets, latency_ms = client.list_tennis_markets()
    print(
        f"open tennis markets: {len(markets)}  "
        f"series={','.join(cfg.series_tickers)}  "
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


def _clip(text: str, width: int) -> str:
    text = text.replace("\n", " ")
    return text if len(text) <= width else text[: width - 1] + "…"
