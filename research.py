"""Walk-forward / train-test research — the HONEST way to test a strategy.

The right way to answer "does this strategy actually work?" is NOT to optimize on
all the data and show the best curve (that's curve-fitting / overfitting). It is:

  1. split history into IN-SAMPLE (past) and OUT-OF-SAMPLE (the "future"),
  2. sweep a grid of parameters (hundreds of "patterns") on the in-sample window,
  3. pick whichever looks best in-sample,
  4. then run THAT config on the out-of-sample window it has never seen.

If a strategy has a real edge, its out-of-sample return is close to in-sample.
If the in-sample looks great but out-of-sample collapses, it was overfit.

Usage:  python research.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oneechan.backtest import fetch_candles
from oneechan.market import MarketData, adx, rsi_series, sma

COINS = ["BTC", "ETH", "SOL"]
INTERVAL = "1h"
BARS = 4000            # ~167 days of history
NETWORK = "mainnet"
SPLIT = 0.6            # 60% in-sample, 40% out-of-sample
INITIAL = 10000.0
FEE = 0.00035
RISK = 1.0
MAX_LEV = 3.0

# the "1000 patterns" grid
STOPS = [0.015, 0.02, 0.03, 0.05, 0.08]
TPS = [0.02, 0.03, 0.05, 0.08, 0.12]
ADXS = [0, 15, 20, 25, 30]      # regime filter: ADX threshold


def simulate(candles, stop_pct, tp_pct, adx_min):
    closes = [float(c["c"]) for c in candles]
    highs = [float(c["h"]) for c in candles]
    lows = [float(c["l"]) for c in candles]
    opens = [float(c["o"]) for c in candles]
    s20 = sma(closes, 20)
    s50 = sma(closes, 50)
    r14 = rsi_series(closes, 14)
    a14 = adx(highs, lows, closes, 14)

    equity = INITIAL
    pos = None
    trades = []

    for i in range(50, len(closes)):
        close = closes[i]
        # manage position
        if pos:
            exit_px = None
            if pos["side"] == "long":
                if lows[i] <= pos["stop"]:
                    exit_px = pos["stop"] if opens[i] > pos["stop"] else opens[i]
                elif highs[i] >= pos["tp"]:
                    exit_px = pos["tp"] if opens[i] < pos["tp"] else opens[i]
                elif close < s20[i]:
                    exit_px = close
            else:
                if highs[i] >= pos["stop"]:
                    exit_px = pos["stop"] if opens[i] < pos["stop"] else opens[i]
                elif lows[i] <= pos["tp"]:
                    exit_px = pos["tp"] if opens[i] > pos["tp"] else opens[i]
                elif close > s20[i]:
                    exit_px = close
            if exit_px:
                pnl = pos["size"] * (exit_px - pos["entry"]) if pos["side"] == "long" else pos["size"] * (pos["entry"] - exit_px)
                equity += pnl - pos["size"] * exit_px * FEE
                trades.append(pnl - pos["size"] * exit_px * FEE)
                pos = None

        # entry (with ADX regime filter)
        if pos is None and s20[i] > 0 and s50[i] > 0 and a14[i] >= adx_min:
            side = None
            if close > s20[i] and s20[i] > s50[i] and r14[i] < 70:
                side = "long"
            elif close < s20[i] and s20[i] < s50[i] and r14[i] > 30:
                side = "short"
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
        last = closes[-1]
        pnl = pos["size"] * (last - pos["entry"]) if pos["side"] == "long" else pos["size"] * (pos["entry"] - last)
        equity += pnl - pos["size"] * last * FEE
        trades.append(pnl - pos["size"] * last * FEE)

    ret = (equity / INITIAL - 1) * 100
    wins = [t for t in trades if t > 0]
    return {"ret": round(ret, 2),
            "trades": len(trades),
            "winrate": round(len(wins) / len(trades) * 100, 1) if trades else 0.0,
            "equity": round(equity, 2)}


def main():
    market = MarketData(NETWORK)
    report = {}
    print(f"Walk-forward train/test | {INTERVAL} | {BARS} bars | split {int(SPLIT*100)}/{int((1-SPLIT)*100)}\n")

    for coin in COINS:
        candles = fetch_candles(market, coin, INTERVAL, BARS)
        n = len(candles)
        split = int(n * SPLIT)
        insample, outsample = candles[:split], candles[split:]

        # sweep the grid on IN-SAMPLE only
        best = None
        best_ret = -1e18
        for stop in STOPS:
            for tp in TPS:
                if tp < stop:
                    continue
                for adx_min in ADXS:
                    r = simulate(insample, stop, tp, adx_min)
                    if r["ret"] > best_ret:
                        best_ret = r["ret"]
                        best = {"stop": stop, "tp": tp, "adx_min": adx_min, "in": r}

        oos = simulate(outsample, best["stop"], best["tp"], best["adx_min"])
        report[coin] = {
            "best_params": {"stop": best["stop"], "tp": best["tp"], "adx_min": best["adx_min"]},
            "in_sample": best["in"],
            "out_of_sample": oos,
        }
        print(f"{coin}: best in-sample params {best['stop']*100:.1f}%/{best['tp']*100:.1f}% ADX>={best['adx_min']}")
        print(f"   IN-SAMPLE   (past, curve-fit):  ret={best['in']['ret']:+.2f}%  trades={best['in']['trades']}  winrate={best['in']['winrate']}%")
        print(f"   OUT-OF-SAMPLE (future, honest):  ret={oos['ret']:+.2f}%  trades={oos['trades']}  winrate={oos['winrate']}%\n")

    # also: what if we DON'T curve-fit and just use sensible defaults?
    print("--- no curve-fitting: fixed sensible defaults (2% stop / 6% tp / ADX>=20) on the OUT-OF-SAMPLE window ---")
    for coin in COINS:
        candles = fetch_candles(market, coin, INTERVAL, BARS)
        outsample = candles[int(len(candles) * SPLIT):]
        r = simulate(outsample, 0.02, 0.06, 20)
        report[coin]["defaults_ood"] = r
        print(f"   {coin}: ret={r['ret']:+.2f}%  trades={r['trades']}  winrate={r['winrate']}%")

    os.makedirs("docs", exist_ok=True)
    with open("docs/research_results.json", "w") as f:
        json.dump(report, f, indent=2)
    print("\nsaved -> docs/research_results.json")


if __name__ == "__main__":
    main()
