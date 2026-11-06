"""Risk engine — position sizing and safety limits."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Size:
    size: float          # coin units
    notional: float      # USD
    leverage: float      # effective leverage
    stop_px: float
    take_profit_px: float


def position_size(equity: float, price: float, risk_per_trade_pct: float,
                  stop_pct: float, take_profit_pct: float,
                  max_position_pct: float, max_leverage: int,
                  sz_decimals: int, confidence: float = 1.0) -> Size:
    """Size a position so a stop-out loses at most `risk_per_trade_pct` of equity,
    scaled by the signal confidence, and capped by max notional + max leverage."""
    if equity <= 0 or price <= 0:
        return Size(0.0, 0.0, 0.0, 0.0, 0.0)

    stop_pct = max(stop_pct, 0.001)
    risk_budget = equity * (risk_per_trade_pct / 100.0) * max(confidence, 0.0)
    stop_distance = price * stop_pct
    size = risk_budget / stop_distance

    # Cap by max notional (equity * max_position_pct).
    max_notional = equity * (max_position_pct / 100.0)
    if size * price > max_notional:
        size = max_notional / price

    # Cap by max leverage.
    if size * price / equity > max_leverage:
        size = equity * max_leverage / price

    if sz_decimals >= 0:
        factor = 10 ** sz_decimals
        size = round(size * factor) / factor

    notional = size * price
    leverage = notional / equity if equity else 0.0
    stop_px = price * (1 - stop_pct)
    tp_px = price * (1 + take_profit_pct)
    return Size(size, notional, leverage, stop_px, tp_px)
