"""The AI "brain" — turns market context into a structured trading decision.

Provider-agnostic: it receives any LLM client exposing `.complete(system, user)`
(see llm.py) and parses a strict JSON decision.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any

ACTIONS = ("LONG", "SHORT", "CLOSE", "HOLD")


@dataclass
class Signal:
    action: str = "HOLD"          # LONG | SHORT | CLOSE | HOLD
    confidence: float = 0.0       # 0.0 .. 1.0
    stop_loss_pct: float = 0.03   # distance from entry, decimal (0.03 = 3%)
    take_profit_pct: float = 0.06
    leverage: int = 2
    reasoning: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


SYSTEM_PROMPT = (
    "You are Oneechan, a disciplined quantitative trading analyst operating on "
    "Hyperliquid L1 (perpetual futures). You receive structured market data for a "
    "single coin and must return exactly one trading decision as strict JSON.\n\n"
    "Rules:\n"
    "- Only use the fields requested. No extra prose outside the JSON object.\n"
    "- 'action' is one of: LONG, SHORT, CLOSE, HOLD.\n"
    "- Choose CLOSE only when you currently hold a position on this coin and the data "
    "justifies exiting. Otherwise choose HOLD.\n"
    "- 'confidence' is 0.0 to 1.0 and must reflect how strong the signal is.\n"
    "- 'stop_loss_pct' and 'take_profit_pct' are decimals of entry price.\n"
    "- 'leverage' is an integer from 1 to the max shown. Prefer low leverage.\n"
    "- Be risk-aware: no trade is better than a bad trade. Default to HOLD.\n\n"
    "Return ONLY a JSON object with this exact shape:\n"
    '{"action": "HOLD", "confidence": 0.0, "stop_loss_pct": 0.03, '
    '"take_profit_pct": 0.06, "leverage": 2, "reasoning": "one short sentence"}'
)


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"```(?:json)?", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"no JSON object found in model output: {text[:200]!r}")
    return json.loads(text[start:end + 1])


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class Brain:
    """Provider-agnostic signal engine — takes any LLM client from llm.py."""

    def __init__(self, llm: Any):
        self.llm = llm

    def decide(self, coin: str, context: dict, existing_position: dict | None) -> Signal:
        prompt = self._build_prompt(coin, context, existing_position)
        raw = self.llm.complete(SYSTEM_PROMPT, prompt)
        data = _extract_json(raw)
        return self._to_signal(data)

    def _to_signal(self, data: dict) -> Signal:
        action = str(data.get("action", "HOLD")).upper()
        if action not in ACTIONS:
            action = "HOLD"
        return Signal(
            action=action,
            confidence=max(0.0, min(1.0, _safe_float(data.get("confidence"), 0.0))),
            stop_loss_pct=max(0.001, _safe_float(data.get("stop_loss_pct"), 0.03)),
            take_profit_pct=max(0.001, _safe_float(data.get("take_profit_pct"), 0.06)),
            leverage=max(1, min(50, _safe_int(data.get("leverage"), 2))),
            reasoning=str(data.get("reasoning", ""))[:400],
        )

    def _build_prompt(self, coin: str, context: dict, existing: dict | None) -> str:
        lines = [
            f"Coin: {coin}",
            f"Mid price: {context.get('mid')}",
        ]
        ind = context.get("indicators", {})
        lines.append(
            "Indicators: last={last}, sma20={sma20}, sma50={sma50}, ema20={ema20}, "
            "rsi14={rsi14}, atr14={atr14}".format(
                last=_fmt(ind.get("last")), sma20=_fmt(ind.get("sma20")),
                sma50=_fmt(ind.get("sma50")), ema20=_fmt(ind.get("ema20")),
                rsi14=_fmt(ind.get("rsi14")), atr14=_fmt(ind.get("atr14"))))
        lines.append(
            "24h: high={high_24}, low={low_24}, change_pct={change_24pct}, volume={volume_24}".format(
                high_24=_fmt(ind.get("high_24")), low_24=_fmt(ind.get("low_24")),
                change_24pct=_fmt(ind.get("change_24pct")), volume_24=_fmt(ind.get("volume_24"))))
        lines.append(
            "Funding: current={funding}, avg_24h={funding_avg}".format(
                funding=context.get("funding"), funding_avg=context.get("funding_avg")))
        book = context.get("book", {})
        lines.append(
            "Order book: best_bid={best_bid}, best_ask={best_ask}, "
            "spread_pct={spread_pct}, imbalance={imbalance}".format(
                best_bid=_fmt(book.get("best_bid")), best_ask=_fmt(book.get("best_ask")),
                spread_pct=_fmt(book.get("spread_pct")), imbalance=_fmt(book.get("imbalance"))))
        lines.append(
            "Max leverage: {max_lev}".format(max_lev=context.get("max_leverage")))
        if existing:
            lines.append(
                "Current position: side={side}, size={size}, entry={entry}, "
                "unrealized_pct={pnl_pct}".format(
                    side=existing.get("side"), size=existing.get("size"),
                    entry=_fmt(existing.get("entry")), pnl_pct=_fmt(existing.get("pnl_pct"))))
        else:
            lines.append("Current position: none (flat)")
        lines.append("Recent closes (oldest -> newest): " +
                     ", ".join(_fmt(c) for c in context.get("recent_closes", [])))
        lines.append("Return the JSON decision now.")
        return "\n".join(lines)


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


class MockBrain:
    """Deterministic fallback so the full pipeline runs without a DeepSeek key.

    Uses a simple momentum + RSI rule — useful for testing plumbing, not for real
    trading. Switch to the real Brain by passing a key / using --brain deepseek.
    """

    def decide(self, coin: str, context: dict, existing_position: dict | None) -> Signal:
        ind = context.get("indicators", {})
        closes = context.get("recent_closes", [])
        rsi = ind.get("rsi14", 50.0)
        change = ind.get("change_24pct", 0.0)
        if existing_position:
            # Exit a winner/loser on simple mean-reversion triggers.
            return Signal(action="CLOSE", confidence=0.5,
                          reasoning="mock: close existing position to test execution")
        if change > 2.0 and rsi < 70:
            return Signal(action="LONG", confidence=0.55, leverage=2,
                          reasoning="mock: positive momentum, long")
        if change < -2.0 and rsi > 30:
            return Signal(action="SHORT", confidence=0.55, leverage=2,
                          reasoning="mock: negative momentum, short")
        return Signal(action="HOLD", confidence=0.0,
                      reasoning="mock: no edge, hold flat")
