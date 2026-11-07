"""Oneechan's multi-agent trading desk.

Instead of a single model making every decision, the team splits the work across
specialist roles, then a Lead Portfolio Manager aggregates their reports into the
final trade:

    Market Analyst      -> trend / momentum / levels (technical)
    Flow & Sentiment    -> funding, order book imbalance, positioning
    Financial Advisor   -> macro risk environment, whether to participate
    Risk Manager        -> approve/veto + caps on stop, take-profit, leverage
    Lead Portfolio Mgr  -> final LONG / SHORT / CLOSE / HOLD

Every role gets the same market snapshot (see brain.build_context). Each specialist
returns a small JSON report; the lead reads all of them and returns a final
`Signal`. The Risk Manager's caps are then enforced as a hard backstop, so a single
over-eager role can't push through an over-sized trade.

Roles are data-driven (see `SPECIALISTS`) so adding a new analyst is one entry.
Any role that fails is skipped and noted — the team never crashes the loop.
"""
from __future__ import annotations

import json
from typing import Any

from .brain import (
    Signal,
    _extract_json,
    _safe_float,
    _safe_int,
    build_context,
    mock_signal,
    signal_from_dict,
)


# --------------------------------------------------------------------------
# Role registry
# --------------------------------------------------------------------------

SPECIALISTS: list[dict[str, Any]] = [
    {
        "id": "market_analyst",
        "name": "Market Analyst",
        "system": (
            "You are the Market Analyst on the Oneechan trading desk, an expert in "
            "technical analysis of perpetual futures. You receive a structured market "
            "snapshot (indicators, funding, order book, recent closes) for a single "
            "coin. Diagnose the price regime and give a directional bias.\n\n"
            "Return ONLY strict JSON with this exact shape:\n"
            '{"bias": "LONG" | "SHORT" | "NEUTRAL", "conviction": 0.0-1.0, '
            '"regime": "trending_up" | "trending_down" | "ranging" | "high_volatility", '
            '"support": number, "resistance": number, '
            '"summary": "one sentence"}\n\n'
            "- bias is your directional lean; NEUTRAL means no clear edge.\n"
            "- conviction reflects how strongly the data supports the bias.\n"
            "- support/resistance are approximate price levels from the recent closes.\n"
            "- Be honest: a weak or mixed picture is NEUTRAL with low conviction."
        ),
    },
    {
        "id": "flow_analyst",
        "name": "Flow & Sentiment Analyst",
        "system": (
            "You are the Flow & Sentiment Analyst on the Oneechan trading desk. You "
            "interpret positioning signals: funding rate, order-book imbalance, and "
            "recent price change. Your job is to spot crowding and short-term sentiment "
            "that technicals may miss.\n\n"
            "Return ONLY strict JSON with this exact shape:\n"
            '{"bias": "LONG" | "SHORT" | "NEUTRAL", "conviction": 0.0-1.0, '
            '"crowding": "crowded_long" | "crowded_short" | "neutral", '
            '"funding_signal": "bullish" | "bearish" | "neutral", '
            '"summary": "one sentence"}\n\n'
            "- Very positive funding + heavy bid-side imbalance often means crowded longs "
            "(mean-reversion risk); very negative funding can mean crowded shorts.\n"
            "- A strong one-sided book with no news is a sentiment signal, not a guarantee."
        ),
    },
    {
        "id": "advisor",
        "name": "Financial Advisor",
        "system": (
            "You are the Financial Advisor on the Oneechan trading desk, responsible for "
            "the macro risk environment. Given the same market snapshot, decide whether "
            "this is an environment to participate in or stand aside from.\n\n"
            "Return ONLY strict JSON with this exact shape:\n"
            '{"risk_on": true | false, "conviction": 0.0-1.0, '
            '"max_leverage": 1-5 (integer), '
            '"recommendation": "participate" | "stand_aside" | "reduce", '
            '"summary": "one sentence"}\n\n'
            "- risk_on=true means market conditions favour taking exposure; false means "
            "stand aside.\n"
            "- max_leverage is your advised ceiling (1-5). Prefer low numbers in high "
            "volatility (wide ATR) or choppy (no trend) conditions.\n"
            "- reduce = take profits / cut risk on any existing position."
        ),
    },
    {
        "id": "risk_manager",
        "name": "Risk Manager",
        "system": (
            "You are the Risk Manager on the Oneechan trading desk. You are independent "
            "and conservative: your only goal is capital preservation. Given the market "
            "snapshot, decide whether any trade is acceptable and set hard limits.\n\n"
            "Return ONLY strict JSON with this exact shape:\n"
            '{"approve": true | false, "max_position_pct": number, '
            '"stop_loss_pct": number, "take_profit_pct": number, '
            '"leverage_cap": 1-5 (integer), "notes": "one sentence"}\n\n'
            "- approve=false vetoes any new position this cycle.\n"
            "- stop_loss_pct / take_profit_pct are decimals of entry price.\n"
            "- max_position_pct is the maximum notional as a % of equity (e.g. 25.0).\n"
            "- leverage_cap is a hard ceiling regardless of what others suggest.\n"
            "- In high volatility, tighten the caps and consider approve=false."
        ),
    },
]

LEAD_SYSTEM = (
    "You are the Lead Portfolio Manager on the Oneechan trading desk. Your specialists "
    "(Market Analyst, Flow & Sentiment, Financial Advisor, Risk Manager) have each "
    "produced a JSON report on the same coin. Weigh their views — including the Risk "
    "Manager's limits — and produce the single final trading decision.\n\n"
    "Return ONLY strict JSON with this exact shape:\n"
    '{"action": "LONG" | "SHORT" | "CLOSE" | "HOLD", "confidence": 0.0-1.0, '
    '"stop_loss_pct": number, "take_profit_pct": number, "leverage": 1-5 (integer), '
    '"reasoning": "one sentence summarising the team"}\n\n'
    "- action CLOSE only if we hold a position and the team thinks exiting is right.\n"
    "- Disagreement or low conviction among specialists => HOLD.\n"
    "- Respect the Risk Manager: never exceed its leverage_cap or max_position_pct; "
    "do not use a tighter stop than its stop_loss_pct or a wider target than its "
    "take_profit_pct.\n"
    "- No trade is better than a bad trade. Default to HOLD."
)


def build_lead_prompt(reports: dict[str, dict], coin: str,
                      context: dict, existing: dict | None) -> str:
    market = build_context(coin, context, existing)
    compact = json.dumps(reports, ensure_ascii=False)
    return (
        "Specialist reports (JSON):\n"
        f"{compact}\n\n"
        "Market snapshot:\n"
        f"{market}\n\n"
        "Return the final JSON decision now."
    )


class Team:
    """Multi-agent decision engine. Exposes `.decide()` like `Brain`."""

    def __init__(self, llm: Any, roles: list[dict] | None = None):
        self.llm = llm
        self.roles = roles if roles is not None else SPECIALISTS
        self.reports: dict[str, dict] = {}

    def decide(self, coin: str, context: dict, existing: dict | None) -> Signal:
        self.reports = {}
        for role in self.roles:
            try:
                raw = self.llm.complete(role["system"], build_context(coin, context, existing))
                self.reports[role["id"]] = _extract_json(raw)
            except Exception as exc:  # a failed specialist must not kill the team
                self.reports[role["id"]] = {"error": str(exc)[:200]}

        try:
            raw = self.llm.complete(LEAD_SYSTEM, build_lead_prompt(self.reports, coin, context, existing))
            signal = signal_from_dict(_extract_json(raw))
        except Exception as exc:
            # Lead failed — do not trade on a broken pipeline. Fall back to a
            # conservative aggregate of the specialist biases.
            return _fallback_signal(self.reports, exc)

        return apply_risk_manager(signal, self.reports.get("risk_manager"))

    def summaries(self) -> list[str]:
        """One-line summaries for logging the team's reasoning."""
        out = []
        for role in self.roles:
            r = self.reports.get(role["id"], {})
            if "error" in r:
                out.append(f"{role['name']}: ERROR {r['error']}")
                continue
            bias = r.get("bias", r.get("recommendation", r.get("approve", "—")))
            conv = r.get("conviction", "")
            summary = r.get("summary") or r.get("notes") or ""
            out.append(f"{role['name']}: {bias} ({conv}) {summary}")
        return out


def apply_risk_manager(signal: Signal, risk: dict | None) -> Signal:
    """Enforce the Risk Manager's caps as a hard backstop on the lead's decision."""
    if not risk or "error" in risk:
        return signal
    if risk.get("approve") is False and signal.action in ("LONG", "SHORT"):
        return Signal(
            action="HOLD", confidence=0.0,
            stop_loss_pct=signal.stop_loss_pct,
            take_profit_pct=signal.take_profit_pct,
            leverage=signal.leverage,
            reasoning="Risk Manager vetoed the trade: " + str(risk.get("notes", ""))[:200],
        )
    lev_cap = _safe_int(risk.get("leverage_cap"), 0)
    if lev_cap >= 1:
        signal.leverage = min(signal.leverage, lev_cap)
    rm_stop = _safe_float(risk.get("stop_loss_pct"), 0.0)
    if rm_stop > 0:
        signal.stop_loss_pct = max(signal.stop_loss_pct, rm_stop)
    rm_tp = _safe_float(risk.get("take_profit_pct"), 0.0)
    if rm_tp > 0:
        signal.take_profit_pct = min(signal.take_profit_pct, rm_tp)
    return signal


def _fallback_signal(reports: dict, exc: Exception) -> Signal:
    """Conservative deterministic fallback when the Lead model fails to answer."""
    longs = shorts = 0
    for r in reports.values():
        bias = str(r.get("bias", "")).upper()
        if bias == "LONG":
            longs += 1
        elif bias == "SHORT":
            shorts += 1
    if longs == 0 and shorts == 0:
        action = "HOLD"
    elif longs > shorts:
        action = "LONG"
    elif shorts > longs:
        action = "SHORT"
    else:
        action = "HOLD"
    return Signal(
        action=action, confidence=0.2,
        reasoning=f"team: lead failed ({str(exc)[:80]}); fell back to specialist vote",
    )


class MockTeam:
    """Deterministic team fallback for zero-key runs — exercises the whole team
    structure (per-role reports + a lead) with no LLM, using the mock rule."""

    def __init__(self, roles: list[dict] | None = None):
        self.roles = roles if roles is not None else SPECIALISTS
        self.reports: dict[str, dict] = {}

    def decide(self, coin: str, context: dict, existing: dict | None) -> Signal:
        ind = context.get("indicators", {})
        rsi = ind.get("rsi14", 50.0)
        change = ind.get("change_24pct", 0.0)
        bias = "LONG" if change > 2.0 else ("SHORT" if change < -2.0 else "NEUTRAL")
        conv = 0.55 if abs(change) > 2.0 else 0.0
        self.reports = {}
        for role in self.roles:
            self.reports[role["id"]] = {
                "bias": bias,
                "conviction": conv,
                "summary": f"mock {role['name']}: rsi={rsi:.0f} change24h={change:+.2f}%",
            }
        sig = mock_signal(context, existing)
        sig.reasoning = "mock team: " + sig.reasoning
        return sig

    def summaries(self) -> list[str]:
        out = []
        for role in self.roles:
            r = self.reports.get(role["id"], {})
            out.append(f"{role['name']}: {r.get('bias')} ({r.get('conviction')}) {r.get('summary')}")
        return out
