"""Execution layer — places orders in paper mode (default) or live via the SDK."""
from __future__ import annotations

from typing import Any

from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info

from .market import NETWORKS
from .state import PaperAccount


class Executor:
    def __init__(self, mode: str, address: str, network: str,
                 private_key: str | None, fee_rate: float,
                 paper_path: str, paper_initial_usdc: float):
        self.mode = mode
        self.address = address
        self.fee_rate = fee_rate
        self.paper = PaperAccount.load(paper_path, paper_initial_usdc)
        self.paper_path = paper_path
        self.exchange: Exchange | None = None
        self.info: Info | None = None

        if mode == "live":
            if not private_key:
                raise ValueError(
                    "Live mode requires HYPERLIQUID_PRIVATE_KEY in .env. "
                    "Run in paper mode instead, or use an API wallet key."
                )
            account = Account.from_key(private_key)
            base_url = NETWORKS.get(network, network)
            self.exchange = Exchange(account, base_url, account_address=address)
            self.info = Info(base_url, skip_ws=True)
            if not address:
                self.address = account.address

    # ---- position --------------------------------------------------
    def position(self, coin: str, mids: dict[str, str]) -> dict | None:
        if self.mode == "paper":
            p = self.paper.positions.get(coin)
            if not p:
                return None
            px = float(mids.get(coin, p.entry))
            pnl_pct = (px / p.entry - 1) * 100 if p.side == "long" else (p.entry / px - 1) * 100
            return {"side": p.side, "size": p.size, "entry": p.entry,
                    "notional": p.notional, "pnl_pct": pnl_pct}
        # live
        state = self.info.user_state(self.address) if self.info else {}
        for item in state.get("assetPositions", []):
            pos = item.get("position", {})
            if pos.get("coin") == coin:
                szi = float(pos.get("szi", 0))
                if szi == 0:
                    return None
                return {"side": "long" if szi > 0 else "short",
                        "size": abs(szi), "entry": float(pos.get("entryPx", 0)),
                        "notional": float(pos.get("positionValue", 0)),
                        "pnl_pct": float(pos.get("unrealizedPnl", 0))}
        return None

    # ---- orders ----------------------------------------------------
    def open(self, coin: str, side: str, size: float, price: float,
             reason: str = "") -> Any:
        if self.mode == "paper":
            self.paper.open(coin, side, size, price, self.fee_rate, reason)
            self.paper.save(self.paper_path)
            return {"paper": True, "coin": coin, "side": side, "size": size, "px": price}
        assert self.exchange is not None
        is_buy = side == "long"
        return self.exchange.market_open(coin, is_buy, size, px=None, slippage=0.01)

    def close(self, coin: str, size: float, price: float, reason: str = "") -> Any:
        if self.mode == "paper":
            self.paper.close(coin, price, self.fee_rate, reason)
            self.paper.save(self.paper_path)
            return {"paper": True, "coin": coin, "side": "CLOSE", "size": size, "px": price}
        assert self.exchange is not None
        return self.exchange.market_close(coin, size, px=None, slippage=0.01)
