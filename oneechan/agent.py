"""The agent — orchestrates data -> brain -> risk -> execution each cycle."""
from __future__ import annotations

import os
import time
from datetime import datetime

from .brain import Brain, MockBrain, Signal
from .config import Config
from .executor import Executor
from .llm import default_key_env, get_llm
from .market import MarketData
from .risk import position_size


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def build_brain(cfg: Config, override: str | None):
    if override == "mock":
        log("brain: mock (deterministic, no LLM)")
        return MockBrain()
    if override:
        # a provider name was passed directly
        cfg.llm.provider = override
    key_env = cfg.llm.api_key_env or default_key_env(cfg.llm.provider)
    if not os.environ.get(key_env):
        log(f"brain: no {key_env} found — falling back to MOCK brain. "
            f"Add a key to .env to use {cfg.llm.provider}.")
        return MockBrain()
    try:
        llm = get_llm(cfg.llm.provider, cfg.llm.model, cfg.llm.base_url,
                      cfg.llm.api_key_env, cfg.llm.temperature, cfg.llm.max_tokens)
    except Exception as exc:
        log(f"brain: failed to init {cfg.llm.provider} ({exc}) — falling back to MOCK.")
        return MockBrain()
    log(f"brain: {cfg.llm.provider}/{cfg.llm.model or 'default'}")
    return Brain(llm)


class Agent:
    def __init__(self, cfg: Config, brain_override: str | None = None):
        self.cfg = cfg
        self.market = MarketData(cfg.hyperliquid.network)
        self.brain = build_brain(cfg, brain_override)
        private_key = os.environ.get("HYPERLIQUID_PRIVATE_KEY") or None
        self.executor = Executor(
            mode=cfg.agent.mode,
            address=cfg.hyperliquid.address,
            network=cfg.hyperliquid.network,
            private_key=private_key,
            fee_rate=cfg.agent.fee_rate,
            paper_path=cfg.agent.paper_state_path,
            paper_initial_usdc=cfg.agent.paper_initial_usdc,
        )
        self._last_trade_ts: dict[str, float] = {}
        log(f"agent '{cfg.agent.name}' ready | mode={cfg.agent.mode} "
            f"| network={cfg.hyperliquid.network} | coins={cfg.agent.coins}")

    # ---- equity ----------------------------------------------------
    def equity(self, mids: dict[str, str]) -> float:
        if self.cfg.agent.mode == "paper":
            return self.executor.paper.equity(mids)
        if self.executor.info:
            st = self.executor.info.user_state(self.executor.address)
            return float(st.get("marginSummary", {}).get("accountValue", 0))
        return 0.0

    # ---- main loop -------------------------------------------------
    def run(self) -> None:
        log(f"starting loop (cadence={self.cfg.agent.cadence_seconds}s). Ctrl+C to stop.")
        try:
            while True:
                self.cycle()
                time.sleep(self.cfg.agent.cadence_seconds)
        except KeyboardInterrupt:
            log("stopped by user.")

    def cycle(self) -> None:
        mids = self.market.mids()
        for coin in self.cfg.agent.coins:
            try:
                self._cycle_coin(coin, mids)
            except Exception as exc:  # keep the loop alive on any single-coin failure
                log(f"[{coin}] error: {exc}")

        if self.cfg.agent.mode == "paper":
            eq = self.executor.paper.equity(mids)
            self.executor.paper.equity_history.append([int(time.time()), eq])
            self.executor.paper.save(self.cfg.agent.paper_state_path)
            open_pos = len(self.executor.paper.positions)
            log(f"paper equity=${eq:,.2f} | open positions={open_pos} "
                f"| trades={len(self.executor.paper.trades)}")

    def _cycle_coin(self, coin: str, mids: dict[str, str]) -> None:
        now = time.time()
        if now - self._last_trade_ts.get(coin, 0.0) < self.cfg.agent.cooldown_seconds:
            return

        series = self.market.ohlcv(coin, self.cfg.agent.interval, self.cfg.agent.lookback)
        if not series["close"]:
            log(f"[{coin}] no candle data, skipping")
            return
        ind = self.market.indicators(series)
        funding = self.market.funding(coin)
        funding_now = float(funding[-1]["fundingRate"]) if funding else 0.0
        funding_avg = (sum(float(f["fundingRate"]) for f in funding) / len(funding)) if funding else 0.0
        book = self.market.book_summary(coin)
        mid = float(mids.get(coin, ind["last"]))
        max_lev = min(self.cfg.agent.max_leverage, self.market.max_leverage(coin))
        existing = self.executor.position(coin, mids)

        context = {
            "mid": mid,
            "indicators": ind,
            "funding": funding_now,
            "funding_avg": funding_avg,
            "book": book,
            "max_leverage": max_lev,
            "recent_closes": series["close"][-30:],
        }

        signal: Signal = self.brain.decide(coin, context, existing)
        log(f"[{coin}] {signal.action} (conf={signal.confidence:.2f}) — {signal.reasoning}")

        if signal.action == "HOLD":
            return

        if signal.action == "CLOSE":
            if existing:
                self.executor.close(coin, existing["size"], mid, signal.reasoning)
                self._last_trade_ts[coin] = now
            return

        # OPEN / FLIP: close any opposite position first.
        if existing and existing["side"] != signal.action.lower():
            self.executor.close(coin, existing["size"], mid, signal.reasoning)

        equity = self.equity(mids)
        size = position_size(
            equity=equity,
            price=mid,
            risk_per_trade_pct=self.cfg.agent.risk_per_trade_pct,
            stop_pct=signal.stop_loss_pct,
            take_profit_pct=signal.take_profit_pct,
            max_position_pct=self.cfg.agent.max_position_pct,
            max_leverage=max_lev,
            sz_decimals=self.market.sz_decimals(coin),
            confidence=signal.confidence,
        )
        if size.size <= 0:
            log(f"[{coin}] size=0, skipping (equity={equity:,.2f})")
            return

        self.executor.open(coin, signal.action.lower(), size.size, mid, signal.reasoning)
        self._last_trade_ts[coin] = now
        log(f"[{coin}] opened {signal.action} {size.size} @ {mid:.4g} "
            f"(notional={size.notional:,.2f}, lev={size.leverage:.1f}x)")
