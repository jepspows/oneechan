"""Backtest harness — replays a deterministic trend-following strategy over real
Hyperliquid candles and reports HONEST metrics (return, win rate, profit factor,
max drawdown, Sharpe). Losses are part of the output; there is no way to make a
strategy "always profitable", so this prints the real numbers.

Strategy (transparent, no LLM): trend + MA cross + RSI filter, with an ATR-free
fixed stop/take-profit and a trend-break exit.

Usage:
    python -m oneechan.backtest --coin BTC --interval 1h --bars 720 --network mainnet
"""
from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

from .market import INTERVAL_MS, MarketData, rsi, sma

BARS_PER_YEAR = {"1m": 525600, "5m": 105120, "15m": 35040, "30m": 17520,
                 "1h": 8760, "4h": 2190, "1d": 365}


def fetch_candles(market: MarketData, coin: str, interval: str, count: int) -> list[dict]:
    """Fetch `count` candles in chunks (Hyperliquid caps each snapshot call)."""
    step = INTERVAL_MS.get(interval, 3_600_000)
    end = int(time.time() * 1000)
    wanted = count + 60  # extra buffer for warmup / indicators
    out: dict[int, dict] = {}
    guard = 0
    while len(out) < wanted and guard < 60:
        start = end - step * wanted
        batch = market.info.candles_snapshot(coin, interval, start, end) or []
        if not batch:
            break
        for c in batch:
            out[c["t"]] = c
        earliest = min(c["t"] for c in batch)
        if earliest >= end:
            break
        end = earliest - 1
        guard += 1
    candles = [out[t] for t in sorted(out)]
    return candles[-count:]


def run(coin: str, interval: str, bars: int, network: str,
        risk_pct: float, stop_pct: float, tp_pct: float,
        max_lev: float, fee_rate: float, initial: float) -> dict[str, Any]:
    market = MarketData(network)
    candles = fetch_candles(market, coin, interval, bars)
    if len(candles) < 80:
        return {"coin": coin, "error": f"only {len(candles)} candles returned"}
    return simulate(candles, coin, interval, risk_pct, stop_pct, tp_pct,
                    max_lev, fee_rate, initial)


def simulate(candles: list[dict], coin: str, interval: str,
             risk_pct: float, stop_pct: float, tp_pct: float,
             max_lev: float, fee_rate: float, initial: float) -> dict[str, Any]:
    closes = [float(c["c"]) for c in candles]
    highs = [float(c["h"]) for c in candles]
    lows = [float(c["l"]) for c in candles]
    opens = [float(c["o"]) for c in candles]
    times = [c["t"] for c in candles]

    s20 = sma(closes, 20)
    s50 = sma(closes, 50)
    rsi14 = [rsi(closes[: i + 1], 14) for i in range(len(closes))]

    equity = initial
    pos: dict[str, float] | None = None
    trades: list[dict] = []
    curve: list[list[float]] = []

    warmup = 50
    for i in range(warmup, len(closes)):
        close = closes[i]
        high = highs[i]
        low = lows[i]
        open_ = opens[i]

        # ---- manage open position ---------------------------------
        if pos is not None:
            exit_px = None
            if pos["side"] == "long":
                if low <= pos["stop"]:
                    exit_px = pos["stop"] if open_ > pos["stop"] else open_
                elif high >= pos["tp"]:
                    exit_px = pos["tp"] if open_ < pos["tp"] else open_
                elif close < s20[i]:
                    exit_px = close
            else:  # short
                if high >= pos["stop"]:
                    exit_px = pos["stop"] if open_ < pos["stop"] else open_
                elif low <= pos["tp"]:
                    exit_px = pos["tp"] if open_ > pos["tp"] else open_
                elif close > s20[i]:
                    exit_px = close

            if exit_px is not None:
                if pos["side"] == "long":
                    pnl = pos["size"] * (exit_px - pos["entry"])
                else:
                    pnl = pos["size"] * (pos["entry"] - exit_px)
                fee = pos["size"] * exit_px * fee_rate
                equity += pnl - fee
                trades.append({
                    "coin": coin, "side": pos["side"], "entry": pos["entry"],
                    "exit": exit_px, "pnl": round(pnl - fee, 4),
                    "reason": "stop/tp/trend",
                })
                pos = None

        # ---- new entry ---------------------------------------------
        if pos is None and s20[i] > 0 and s50[i] > 0:
            side = None
            if close > s20[i] and s20[i] > s50[i] and rsi14[i] < 70:
                side = "long"
            elif close < s20[i] and s20[i] < s50[i] and rsi14[i] > 30:
                side = "short"

            if side:
                risk_budget = equity * (risk_pct / 100.0)
                stop_dist = close * stop_pct
                size = risk_budget / stop_dist if stop_dist > 0 else 0.0
                if size * close > equity * max_lev:
                    size = equity * max_lev / close
                if size * close > equity * 25:  # max 25x notional guard
                    size = equity * 25 / close
                fee = size * close * fee_rate
                equity -= fee
                pos = {
                    "side": side, "size": size, "entry": close,
                    "stop": close * (1 - stop_pct) if side == "long" else close * (1 + stop_pct),
                    "tp": close * (1 + tp_pct) if side == "long" else close * (1 - tp_pct),
                }

        curve.append([times[i], equity + (pos and unrealized(pos, close) or 0)])

    # close any still-open position at the last close
    if pos is not None:
        last = closes[-1]
        pnl = pos["size"] * (last - pos["entry"]) if pos["side"] == "long" else pos["size"] * (pos["entry"] - last)
        equity += pnl - pos["size"] * last * fee_rate
        trades.append({"coin": coin, "side": pos["side"], "entry": pos["entry"],
                       "exit": last, "pnl": round(pnl - pos["size"] * last * fee_rate, 4),
                       "reason": "end of data"})

    summary = summarize(coin, interval, len(candles), equity, initial, trades, curve)
    summary["equity_curve"] = [[t, round(e, 2)] for t, e in curve]
    return summary


def unrealized(pos: dict, px: float) -> float:
    return pos["size"] * (px - pos["entry"]) if pos["side"] == "long" else pos["size"] * (pos["entry"] - px)


def summarize(coin, interval, bars, final, initial, trades, curve):
    n = len(trades)
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    equities = [c[1] for c in curve]
    peak = -1e18
    max_dd = 0.0
    for e in equities:
        peak = max(peak, e)
        if peak > 0:
            max_dd = max(max_dd, (peak - e) / peak)
    rets = []
    for k in range(1, len(equities)):
        prev = equities[k - 1]
        if prev > 0:
            rets.append(equities[k] / prev - 1)
    mean = sum(rets) / len(rets) if rets else 0.0
    var = sum((r - mean) ** 2 for r in rets) / len(rets) if rets else 0.0
    std = var ** 0.5
    sharpe = (mean / std * (BARS_PER_YEAR.get(interval, 8760) ** 0.5)) if std > 0 else 0.0

    return {
        "coin": coin, "interval": interval, "bars": bars, "network": "—",
        "initial_equity": initial, "final_equity": round(final, 2),
        "total_return_pct": round((final / initial - 1) * 100, 2),
        "trades": n,
        "win_rate_pct": round(len(wins) / n * 100, 1) if n else 0.0,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "avg_win": round(gross_win / len(wins), 2) if wins else 0.0,
        "avg_loss": round(-gross_loss / len(losses), 2) if losses else 0.0,
        "max_drawdown_pct": round(max_dd * 100, 2),
        "sharpe": round(sharpe, 2),
    }


def main():
    ap = argparse.ArgumentParser(description="Backtest the Oneechan rule strategy.")
    ap.add_argument("--coin", default="BTC")
    ap.add_argument("--coins", nargs="*", help="backtest several coins")
    ap.add_argument("--interval", default="1h")
    ap.add_argument("--bars", type=int, default=720)
    ap.add_argument("--network", choices=["mainnet", "testnet"], default="mainnet")
    ap.add_argument("--risk-pct", type=float, default=1.0)
    ap.add_argument("--stop-pct", type=float, default=0.02)
    ap.add_argument("--tp-pct", type=float, default=0.04)
    ap.add_argument("--max-lev", type=float, default=3.0)
    ap.add_argument("--fee", type=float, default=0.00035)
    ap.add_argument("--initial", type=float, default=10000.0)
    ap.add_argument("--save", action="store_true", help="save results JSON to state/")
    args = ap.parse_args()

    coins = args.coins or [args.coin]
    for coin in coins:
        res = run(coin, args.interval, args.bars, args.network,
                  args.risk_pct, args.stop_pct, args.tp_pct,
                  args.max_lev, args.fee, args.initial)
        print(json.dumps(res, indent=2))
        if args.save and "error" not in res:
            os.makedirs("state", exist_ok=True)
            path = f"state/backtest_{coin}_{args.interval}.json"
            with open(path, "w") as f:
                json.dump(res, f, indent=2)
            print(f"saved -> {path}")


if __name__ == "__main__":
    main()
