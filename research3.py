"""KNN pattern-matching market predictor — the honest test of "analyze all past
patterns and predict the market".

How it works (exactly what was asked):
  1. every historical bar becomes a "pattern" = the last L bars of returns,
     labelled with what the price actually did over the next H bars;
  2. to predict, take the current pattern and find its K most similar patterns
     in the past, then average their outcomes.

The honest question is: does the predicted move correlate with the actual move?
A value near zero means the patterns do NOT predict anything.

Usage:  python research3.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oneechan.backtest import fetch_candles
from oneechan.market import MarketData

L = 20          # pattern length (bars of returns)
H = 12          # prediction horizon (bars ahead)
K = 30          # number of nearest neighbours
COINS = ["BTC", "ETH", "SOL", "AVAX", "LINK", "DOGE"]
SPLIT = 0.6     # 60% patterns to "learn", 40% to predict


def load(market, coin):
    candles = fetch_candles(market, coin, "1h", 4000)
    return np.array([float(c["c"]) for c in candles])


def build(closes):
    rets = np.diff(closes) / closes[:-1]
    n = len(closes)
    pats, labs = [], []
    for i in range(L, n - H):
        pats.append(rets[i - L:i])
        labs.append(closes[i + H] / closes[i] - 1)
    return np.array(pats), np.array(labs)


def main():
    market = MarketData("mainnet")
    print(f"Pattern matcher: last {L} bars -> predict next {H} bars, K={K} neighbours\n")

    for coin in COINS:
        pats, labs = build(load(market, coin))
        split = int(len(pats) * SPLIT)
        tr_p, tr_l = pats[:split], labs[:split]
        te_p, te_l = pats[split:], labs[split:]

        preds = []
        for q in te_p:
            d = np.linalg.norm(tr_p - q, axis=1)
            k = np.argsort(d)[:K]
            preds.append(tr_l[k].mean())
        preds = np.array(preds)

        corr = float(np.corrcoef(preds, te_l)[0, 1]) if len(preds) > 1 else 0.0
        hit = float(np.mean((preds > 0) == (te_l > 0)) * 100)

        # simple strategy: trade every H bars in the direction of the prediction
        strat = 0.0
        n_trades = 0
        for t in range(0, len(te_l) - H, H):
            if preds[t] > 0.001:
                strat += te_l[t]
                n_trades += 1
            elif preds[t] < -0.001:
                strat -= te_l[t]
                n_trades += 1

        print(f"{coin:5}  prediction correlation = {corr:+.3f}   "
              f"directional hit rate = {hit:.1f}%   "
              f"strategy return (no fees) = {strat * 100:+.2f}% over {n_trades} trades")

    print("\n(0.00 correlation = the past patterns carry no predictive signal for the future)")


if __name__ == "__main__":
    main()
