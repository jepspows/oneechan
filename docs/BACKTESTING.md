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

To actually improve the odds you would need to, in order of impact:

1. Run the **LLM brain** (DeepSeek/Claude/…) in paper mode for weeks and let the
   model adapt to regime — that is Oneechan's real differentiator, not the rule.
2. Add a **trend/regime filter** (e.g. ADX, volatility band) to stop trading chop.
3. Do **walk-forward** validation and never tune parameters on the same window you
   report on.

> Nothing here is financial advice. Perpetual futures carry substantial risk of loss.
