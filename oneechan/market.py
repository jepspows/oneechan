"""Market data layer — wraps the Hyperliquid Info API and adds indicators."""
from __future__ import annotations

import time
from typing import Any

from hyperliquid.info import Info
from hyperliquid.utils import constants

NETWORKS = {
    "mainnet": constants.MAINNET_API_URL,
    "testnet": constants.TESTNET_API_URL,
}

INTERVAL_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
    "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000,
    "4h": 14_400_000, "8h": 28_800_000, "12h": 43_200_000,
    "1d": 86_400_000, "1w": 604_800_000,
}


def sma(values: list[float], period: int) -> list[float]:
    out = [0.0] * len(values)
    if len(values) < period:
        return out
    window = sum(values[:period])
    out[period - 1] = window / period
    for i in range(period, len(values)):
        window += values[i] - values[i - period]
        out[i] = window / period
    return out


def ema(values: list[float], period: int) -> list[float]:
    out = [0.0] * len(values)
    if not values:
        return out
    k = 2.0 / (period + 1)
    out[0] = values[0]
    for i in range(1, len(values)):
        out[i] = values[i] * k + out[i - 1] * (1 - k)
    return out


def rsi(values: list[float], period: int = 14) -> float:
    if len(values) < period + 1:
        return 50.0
    gains = losses = 0.0
    for i in range(1, period + 1):
        delta = values[i] - values[i - 1]
        gains += max(delta, 0.0)
        losses += max(-delta, 0.0)
    avg_gain = gains / period
    avg_loss = losses / period
    for i in range(period + 1, len(values)):
        delta = values[i] - values[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(delta, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-delta, 0.0)) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float:
    if len(closes) < 2:
        return 0.0
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i],
                 abs(highs[i] - closes[i - 1]),
                 abs(lows[i] - closes[i - 1]))
        trs.append(tr)
    if not trs:
        return 0.0
    return sum(trs[-period:]) / min(period, len(trs))


class MarketData:
    """Read-only access to Hyperliquid market data (no keys required)."""

    def __init__(self, network: str = "testnet"):
        self.base_url = NETWORKS.get(network, network)
        self.info = Info(self.base_url, skip_ws=True)
        self._meta = None

    # ---- metadata -------------------------------------------------
    def meta(self) -> dict:
        if self._meta is None:
            self._meta = self.info.meta()
        return self._meta

    def _universe(self, coin: str) -> dict | None:
        for item in self.meta().get("universe", []):
            if item.get("name") == coin:
                return item
        return None

    def sz_decimals(self, coin: str) -> int:
        item = self._universe(coin)
        return int(item.get("szDecimals", 0)) if item else 0

    def max_leverage(self, coin: str) -> int:
        item = self._universe(coin)
        return int(item.get("maxLeverage", 50)) if item else 1

    # ---- raw endpoints --------------------------------------------
    def mids(self) -> dict[str, str]:
        return self.info.all_mids()

    def candles(self, coin: str, interval: str = "1h", lookback: int = 96) -> list[dict]:
        end = int(time.time() * 1000)
        step = INTERVAL_MS.get(interval, 3_600_000)
        start = end - lookback * step
        return self.info.candles_snapshot(coin, interval, start, end)

    def funding(self, coin: str, hours: int = 24) -> list[dict]:
        end = int(time.time() * 1000)
        start = end - hours * 3_600_000
        return self.info.funding_history(coin, start, end)

    def book(self, coin: str) -> dict:
        return self.info.l2_snapshot(coin)

    def user_state(self, address: str) -> dict:
        return self.info.user_state(address)

    # ---- derived series -------------------------------------------
    def ohlcv(self, coin: str, interval: str, lookback: int) -> dict[str, list[float]]:
        candles = self.candles(coin, interval, lookback)
        series: dict[str, list[float]] = {"open": [], "high": [], "low": [], "close": [], "volume": []}
        for k in candles:
            series["open"].append(float(k["o"]))
            series["high"].append(float(k["h"]))
            series["low"].append(float(k["l"]))
            series["close"].append(float(k["c"]))
            series["volume"].append(float(k["v"]))
        return series

    def indicators(self, series: dict[str, list[float]]) -> dict[str, float]:
        closes = series["close"]
        sma20 = sma(closes, 20)
        sma50 = sma(closes, 50)
        ema20 = ema(closes, 20)
        return {
            "last": closes[-1] if closes else 0.0,
            "sma20": sma20[-1],
            "sma50": sma50[-1],
            "ema20": ema20[-1],
            "rsi14": rsi(closes, 14),
            "atr14": atr(series["high"], series["low"], closes, 14),
            "high_24": max(series["high"][-24:]) if closes else 0.0,
            "low_24": min(series["low"][-24:]) if closes else 0.0,
            "volume_24": sum(series["volume"][-24:]) if closes else 0.0,
            "change_24pct": (closes[-1] / closes[-25] - 1) * 100 if len(closes) >= 25 else 0.0,
        }

    def book_summary(self, coin: str) -> dict[str, Any]:
        book = self.book(coin)
        levels = book.get("levels", [])
        bids = levels[0] if len(levels) > 0 else []
        asks = levels[1] if len(levels) > 1 else []

        def sz_sum(entries) -> float:
            return sum(float(e.get("sz", 0)) for e in entries)

        best_bid = float(bids[0]["px"]) if bids else 0.0
        best_ask = float(asks[0]["px"]) if asks else 0.0
        bid_sz = sz_sum(bids[:10])
        ask_sz = sz_sum(asks[:10])
        spread = 0.0
        if best_ask > 0 and best_bid > 0:
            spread = (best_ask - best_bid) / ((best_bid + best_ask) / 2)
        total = bid_sz + ask_sz
        imbalance = (bid_sz - ask_sz) / total if total > 0 else 0.0
        return {"best_bid": best_bid, "best_ask": best_ask,
                "spread_pct": spread * 100, "imbalance": imbalance}
