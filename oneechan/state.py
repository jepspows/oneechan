"""Paper-account state — simulated equity, positions and trade history."""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field


@dataclass
class Position:
    coin: str
    side: str          # "long" | "short"
    size: float        # coin units (positive)
    entry: float
    notional: float
    opened_at: int


@dataclass
class Trade:
    ts: int
    coin: str
    side: str          # LONG | SHORT | CLOSE_LONG | CLOSE_SHORT
    size: float
    px: float
    notional: float
    fee: float
    reason: str


@dataclass
class PaperAccount:
    cash: float = 10000.0
    positions: dict = field(default_factory=dict)   # coin -> Position
    trades: list = field(default_factory=list)
    equity_history: list = field(default_factory=list)

    # ---- pnl ------------------------------------------------------
    def unrealized(self, mids: dict[str, str]) -> float:
        pnl = 0.0
        for coin, p in self.positions.items():
            px = float(mids.get(coin, p.entry))
            pnl += p.size * (px - p.entry) if p.side == "long" else p.size * (p.entry - px)
        return pnl

    def equity(self, mids: dict[str, str]) -> float:
        return self.cash + self.unrealized(mids)

    # ---- actions --------------------------------------------------
    def open(self, coin: str, side: str, size: float, px: float,
             fee_rate: float, reason: str = "") -> Trade:
        if size <= 0 or px <= 0:
            raise ValueError("invalid order size/price")
        fee = size * px * fee_rate
        self.cash -= fee
        self.positions[coin] = Position(coin, side, size, px, size * px, int(time.time()))
        t = Trade(int(time.time()), coin, side.upper(), size, px, size * px, fee, reason)
        self.trades.append(t)
        return t

    def close(self, coin: str, px: float, fee_rate: float, reason: str = "") -> Trade | None:
        p = self.positions.pop(coin, None)
        if not p:
            return None
        pnl = p.size * (px - p.entry) if p.side == "long" else p.size * (p.entry - px)
        fee = p.size * px * fee_rate
        self.cash += pnl - fee
        side = "CLOSE_LONG" if p.side == "long" else "CLOSE_SHORT"
        t = Trade(int(time.time()), coin, side, p.size, px, p.size * px, fee, reason)
        self.trades.append(t)
        return t

    # ---- persistence ----------------------------------------------
    @classmethod
    def load(cls, path: str, initial_usdc: float) -> "PaperAccount":
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            acct = cls(
                cash=data.get("cash", initial_usdc),
                positions={c: Position(**p) for c, p in data.get("positions", {}).items()},
                trades=[Trade(**t) for t in data.get("trades", [])],
                equity_history=data.get("equity_history", []),
            )
            return acct
        return cls(cash=initial_usdc)

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "cash": self.cash,
                "positions": {c: asdict(p) for c, p in self.positions.items()},
                "trades": [asdict(t) for t in self.trades[-500:]],
                "equity_history": self.equity_history[-5000:],
            }, f, indent=2)
