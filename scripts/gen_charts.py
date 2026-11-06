"""Generate the backtest chart + metrics JSON for the README/docs.

Runs the rule strategy over real Hyperliquid data for BTC/ETH/SOL and renders an
honest equity-curve / drawdown chart to docs/images/backtest.png.

Usage:
    python scripts/gen_charts.py
"""
import json
import os
import sys
from datetime import datetime, timezone

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oneechan.backtest import fetch_candles, simulate
from oneechan.market import MarketData

COINS = ["BTC", "ETH", "SOL"]
INTERVAL = "1h"
BARS = 2000
NETWORK = "mainnet"
INITIAL = 10000.0
RISK = 1.0
STOP = 0.03
TP = 0.06
MAX_LEV = 3.0
FEE = 0.00035

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMG_DIR = os.path.join(ROOT, "docs", "images")


def main():
    market = MarketData(NETWORK)
    results = {}
    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(10, 7), sharex=True,
        gridspec_kw={"height_ratios": [3, 1], "hspace": 0.08},
    )

    for coin in COINS:
        candles = fetch_candles(market, coin, INTERVAL, BARS)
        r = simulate(candles, coin, INTERVAL, RISK, STOP, TP, MAX_LEV, FEE, INITIAL)
        results[coin] = {k: v for k, v in r.items() if k != "equity_curve"}

        curve = r.get("equity_curve", [])
        xs = [datetime.fromtimestamp(t / 1000, tz=timezone.utc) for t, _ in curve]
        ys = [e / INITIAL * 100 for _, e in curve]
        ax1.plot(xs, ys, linewidth=1.6, label=f"{coin}  {r['total_return_pct']:+.2f}%")

        # drawdown
        peak = -1e18
        dd = []
        for _, e in curve:
            peak = max(peak, e)
            dd.append((e / peak - 1) * 100)
        ax2.fill_between(xs, dd, 0, alpha=0.35, linewidth=0)
        ax2.plot(xs, dd, linewidth=1.0)

    ax1.axhline(100, color="#999", linewidth=0.8, linestyle="--", alpha=0.7)
    ax1.set_ylabel("Equity (start = 100)", fontsize=10)
    ax1.set_title(
        f"Oneechan rule-strategy backtest — Hyperliquid {NETWORK}, {INTERVAL}, "
        f"last {BARS} bars (real data)",
        fontsize=12, fontweight="bold", loc="left",
    )
    ax1.legend(loc="upper right", fontsize=9, frameon=False)
    ax1.grid(True, alpha=0.25)

    ax2.set_ylabel("Drawdown %", fontsize=10)
    ax2.set_xlabel("Time (UTC)", fontsize=10)
    ax2.grid(True, alpha=0.25)

    os.makedirs(IMG_DIR, exist_ok=True)
    out = os.path.join(IMG_DIR, "backtest.png")
    fig.savefig(out, dpi=150, bbox_inches="tight")
    print(f"saved chart -> {out}")

    # save metrics json
    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "network": NETWORK, "interval": INTERVAL, "bars": BARS,
        "initial_equity": INITIAL, "risk_pct": RISK, "stop_pct": STOP,
        "tp_pct": TP, "max_leverage": MAX_LEV, "fee_rate": FEE,
        "coins": results,
    }
    json_path = os.path.join(ROOT, "docs", "backtest_results.json")
    with open(json_path, "w") as f:
        json.dump(meta, f, indent=2)
    print(f"saved metrics -> {json_path}")
    for c, r in results.items():
        print(f"  {c}: ret={r['total_return_pct']:+.2f}%  winrate={r['win_rate_pct']}%  "
              f"PF={r['profit_factor']}  maxDD={r['max_drawdown_pct']}%  trades={r['trades']}")


if __name__ == "__main__":
    main()
