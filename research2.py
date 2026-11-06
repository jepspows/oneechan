"""Exhaustive honest search: 4 strategy families x 6 coins x walk-forward.

For each (coin, strategy) it optimizes a stop/take-profit grid on the past 60%,
then reports how the best config does on the future 40% it never saw. This is the
honest answer to "find the best results at all cost".

Usage:  python research2.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oneechan.backtest import fetch_candles
from oneechan.market import MarketData, adx, rsi_series, sma

COINS = ["BTC", "ETH", "SOL", "AVAX", "LINK", "DOGE"]
INTERVAL = "1h"
BARS = 4000
NETWORK = "mainnet"
SPLIT = 0.6
INITIAL = 10000.0
FEE = 0.00035
RISK = 1.0
MAX_LEV = 3.0

STOPS = [0.02, 0.04, 0.06]
TPS = [0.03, 0.06, 0.10]


def indicators(candles):
    closes = [float(c["c"]) for c in candles]
    highs = [float(c["h"]) for c in candles]
    lows = [float(c["l"]) for c in candles]
    opens = [float(c["o"]) for c in candles]
    return {
        "close": closes, "high": highs, "low": lows, "open": opens,
        "s20": sma(closes, 20), "s50": sma(closes, 50),
        "rsi": rsi_series(closes, 14), "adx": adx(highs, lows, closes, 14),
    }


def signal_trend(d, i):
    c, s20, s50, r, a = d["close"], d["s20"], d["s50"], d["rsi"], d["adx"]
    if a[i] < 20:
        return None
    if c[i] > s20[i] and s20[i] > s50[i] and r[i] < 70:
        return "long"
    if c[i] < s20[i] and s20[i] < s50[i] and r[i] > 30:
        return "short"
    return None


def signal_meanrev(d, i):
    r = d["rsi"][i]
    return "long" if r < 30 else ("short" if r > 70 else None)


def signal_breakout(d, i):
    c, h, l = d["close"], d["high"], d["low"]
    if i < 20:
        return None
    hh = max(h[i - 20:i])
    ll = min(l[i - 20:i])
    if c[i] > hh:
        return "long"
    if c[i] < ll:
        return "short"
    return None


def signal_momentum(d, i):
    c = d["close"]
    if i < 20:
        return None
    roc = c[i] / c[i - 20] - 1
    if roc > 0.03:
        return "long"
    if roc < -0.03:
        return "short"
    return None


STRATEGIES = {
    "trend (MA+ADX)": signal_trend,
    "mean-reversion (RSI)": signal_meanrev,
    "breakout (Donchian)": signal_breakout,
    "momentum (ROC)": signal_momentum,
}


def backtest(candles, signal_fn, stop_pct, tp_pct):
    d = indicators(candles)
    c, h, l, o = d["close"], d["high"], d["low"], d["open"]
    equity = INITIAL
    pos = None
    trades = []
    for i in range(50, len(c)):
        close = c[i]
        if pos:
            exit_px = None
            if pos["side"] == "long":
                if l[i] <= pos["stop"]:
                    exit_px = pos["stop"] if o[i] > pos["stop"] else o[i]
                elif h[i] >= pos["tp"]:
                    exit_px = pos["tp"] if o[i] < pos["tp"] else o[i]
                elif close < d["s20"][i]:
                    exit_px = close
            else:
                if h[i] >= pos["stop"]:
                    exit_px = pos["stop"] if o[i] < pos["stop"] else o[i]
                elif l[i] <= pos["tp"]:
                    exit_px = pos["tp"] if o[i] > pos["tp"] else o[i]
                elif close > d["s20"][i]:
                    exit_px = close
            if exit_px:
                pnl = pos["size"] * (exit_px - pos["entry"]) if pos["side"] == "long" else pos["size"] * (pos["entry"] - exit_px)
                equity += pnl - pos["size"] * exit_px * FEE
                trades.append(pnl - pos["size"] * exit_px * FEE)
                pos = None
        if pos is None:
            side = signal_fn(d, i)
            if side:
                risk_budget = equity * RISK / 100.0
                stop_dist = close * stop_pct
                size = risk_budget / stop_dist if stop_dist > 0 else 0.0
                if size * close > equity * MAX_LEV:
                    size = equity * MAX_LEV / close
                if size * close > equity * 25:
                    size = equity * 25 / close
                equity -= size * close * FEE
                pos = {"side": side, "size": size, "entry": close,
                       "stop": close * (1 - stop_pct) if side == "long" else close * (1 + stop_pct),
                       "tp": close * (1 + tp_pct) if side == "long" else close * (1 - tp_pct)}
    if pos:
        last = c[-1]
        pnl = pos["size"] * (last - pos["entry"]) if pos["side"] == "long" else pos["size"] * (pos["entry"] - last)
        equity += pnl - pos["size"] * last * FEE
        trades.append(pnl - pos["size"] * last * FEE)
    wins = [t for t in trades if t > 0]
    return {"ret": round((equity / INITIAL - 1) * 100, 2),
            "trades": len(trades),
            "winrate": round(len(wins) / len(trades) * 100, 1) if trades else 0.0}


def main():
    market = MarketData(NETWORK)
    out = {}
    print("Exhaustive walk-forward: 4 strategies x 6 coins x 60/40 train/test\n")

    for name, fn in STRATEGIES.items():
        out[name] = {}
        oos_returns = []
        print(f"=== {name} ===")
        for coin in COINS:
            candles = fetch_candles(market, coin, INTERVAL, BARS)
            split = int(len(candles) * SPLIT)
            ins, oos = candles[:split], candles[split:]
            best = None
            best_ret = -1e18
            for stop in STOPS:
                for tp in TPS:
                    if tp < stop:
                        continue
                    r = backtest(ins, fn, stop, tp)
                    if r["ret"] > best_ret:
                        best_ret = r["ret"]
                        best = (stop, tp, r)
            oosr = backtest(oos, fn, best[0], best[1])
            oos_returns.append(oosr["ret"])
            out[name][coin] = {"best_params": [best[0], best[1]],
                               "in_sample_ret": best[2]["ret"],
                               "out_of_sample_ret": oosr["ret"]}
            print(f"  {coin:5}  in-sample {best[2]['ret']:+7.2f}%  ->  out-of-sample {oosr['ret']:+7.2f}%  ({oosr['trades']} trades)")
        avg = sum(oos_returns) / len(oos_returns)
        print(f"  >>> average out-of-sample across 6 coins: {avg:+.2f}%\n")

    os.makedirs("docs", exist_ok=True)
    with open("docs/research2_results.json", "w") as f:
        json.dump(out, f, indent=2)
    print("saved -> docs/research2_results.json")


if __name__ == "__main__":
    main()
