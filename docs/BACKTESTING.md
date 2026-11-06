# Backtesting methodology

This document explains how Oneechan's backtesting works, what it measures, and
how to reproduce the numbers in the README.

## What gets tested

`oneechan/backtest.py` replays a **deterministic trend-following strategy** over
real Hyperliquid candles. It is deliberately simple and transparent so it can act
as an honest baseline:

- **Entry** — long when `close > SMA20 > SMA50` and `RSI14 < 70`; short when
  `close < SMA20 < SMA50` and `RSI14 > 30`.
- **Exit** — fixed stop / take-profit, or a trend break (close crosses back over SMA20).
- **Sizing** — risks `risk_per_trade_pct` of equity to the stop, capped by
  `max_leverage` and a max-notional guard.
- **Costs** — a taker fee (`0.035%`) is charged on every open and close.

The same indicator logic is shared with the live agent's `MarketData`, so the
backtest exercises the real code path, not a separate re-implementation.

## No look-ahead

Indicators (`sma`, `rsi`, `atr`) are causal — the value at bar *i* only uses data
up to and including bar *i*. Entries are evaluated at the close and fills are taken
at that close; stops/take-profits are checked against the next bars' high/low.
This is an approximation, not a perfect fill simulation.

## Reproduce

```bash
python -m oneechan.backtest --coin BTC --interval 1h --bars 2000 --network mainnet
python sweep.py            # grid-search stop/take-profit across BTC/ETH/SOL
python scripts/gen_charts.py   # regenerate docs/images/backtest.png + metrics JSON
```

The README chart uses: `network=mainnet`, `interval=1h`, `bars=2000`,
`risk=1.0%`, `stop=3%`, `tp=6%`, `max_leverage=3`, `fee=0.035%`, `initial=$10,000`.

## Honest interpretation

The baseline strategy **loses money after fees** in the tested window. This is the
point: it gives you a transparent, reproducible floor to compare against, and it is
a reminder that backtests do not guarantee future results.

## Walk-forward (out-of-sample) validation

The single most important question is: *"if I tune this on the past, does it work
on the future?"* `research.py` answers it honestly using a train/test split:

1. split history into **in-sample** (past 60%) and **out-of-sample** (future 40%),
2. sweep a grid of ~100 parameter "patterns" on the **in-sample** window only,
3. take whichever config looks best in-sample,
4. run that exact config on the **out-of-sample** window it has never seen.

```bash
python research.py
```

Results (Hyperliquid mainnet, 1h, 4,000 bars ≈ 167 days):

| Coin | Best in-sample (curve-fit) | Out-of-sample (the "future") | Verdict |
| --- | --- | --- | --- |
| BTC | +0.27% | **−5.82%** | overfit → loses |
| ETH | +16.36% | **−8.01%** | overfit → loses |
| SOL | +16.32% | **−11.50%** | overfit → loses |

Even with no curve-fitting (fixed 2% stop / 6% take-profit / ADX≥20), the
out-of-sample returns are negative (−4% to −14%).

**This is the honest answer.** A strategy that looks +16% on the past collapses to
−8% on the future. The gap *is* overfitting — the market does not repeat patterns
that precisely. This is why Oneechan refuses to promise profit: no parameter
search changes it, only hides it until real money is on the line.

To actually improve the odds you would need to, in order of impact:

1. Run the **LLM brain** (DeepSeek/Claude/…) in paper mode for weeks and let the
   model adapt to regime — that is Oneechan's real differentiator, not the rule.
2. Add a **trend/regime filter** (e.g. ADX, volatility band) to stop trading chop.
3. Do **walk-forward** validation and never tune parameters on the same window you
   report on.

> Nothing here is financial advice. Perpetual futures carry substantial risk of loss.
