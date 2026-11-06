"""Parameter sweep — finds the honest best stop/take-profit for the rule strategy
across a coin list over a long window. Prints a ranked table. Not a promise of
profit; just data."""
import sys
from oneechan.backtest import fetch_candles, simulate
from oneechan.market import MarketData

COINS = ["BTC", "ETH", "SOL"]
INTERVAL = "1h"
BARS = 2000
NETWORK = "mainnet"
INITIAL = 10000.0
RISK = 1.0
MAX_LEV = 3.0
FEE = 0.00035
STOPS = [0.015, 0.02, 0.03, 0.04, 0.05]
TPS = [0.02, 0.03, 0.04, 0.06, 0.08]


def main():
    market = MarketData(NETWORK)
    data = {}
    for c in COINS:
        candles = fetch_candles(market, c, INTERVAL, BARS)
        data[c] = candles
        print(f"fetched {c}: {len(candles)} bars", file=sys.stderr)

    results = []
    for stop in STOPS:
        for tp in TPS:
            if tp < stop:
                continue
            row = {"stop_pct": stop, "tp_pct": tp, "totals": [], "avg_ret": 0.0,
                   "coins": {}}
            for c in COINS:
                r = simulate(data[c], c, INTERVAL, RISK, stop, tp, MAX_LEV, FEE, INITIAL)
                ret = r.get("total_return_pct", 0.0)
                row["totals"].append(ret)
                row["coins"][c] = {"ret": ret, "wr": r.get("win_rate_pct"), "pf": r.get("profit_factor"), "dd": r.get("max_drawdown_pct"), "trades": r.get("trades")}
            row["avg_ret"] = sum(row["totals"]) / len(row["totals"])
            results.append(row)

    results.sort(key=lambda r: r["avg_ret"], reverse=True)

    print(f"\n=== sweep over {COINS} | {INTERVAL} | {BARS} bars | avg return (3 coins) ===")
    print(f"{'stop':>6} {'tp':>6} {'avg_ret%':>9} | " + " | ".join(f"{c:>10}" for c in COINS))
    for r in results[:15]:
        cells = " | ".join(f"{r['coins'][c]['ret']:>9.1f}%" for c in COINS)
        print(f"{r['stop_pct']*100:>5.1f}% {r['tp_pct']*100:>5.1f}% {r['avg_ret']:>9.2f}% | {cells}")

    print("\n=== top 5 configs, per-coin detail ===")
    for r in results[:5]:
        print(f"\nstop={r['stop_pct']*100:.1f}% tp={r['tp_pct']*100:.1f}%  avg_ret={r['avg_ret']:.2f}%")
        for c in COINS:
            d = r["coins"][c]
            print(f"  {c}: ret={d['ret']:+.2f}%  winrate={d['wr']}%  PF={d['pf']}  maxDD={d['dd']}%  trades={d['trades']}")


if __name__ == "__main__":
    main()
