"""Configuration loading for Oneechan."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import yaml
from dotenv import load_dotenv


@dataclass
class LLMConfig:
    provider: str = "deepseek"      # deepseek | openai | anthropic | gemini | openrouter | groq | xai | together | mistral | perplexity
    model: str = "deepseek-v4-pro"
    base_url: str = ""             # empty = use the provider's default
    api_key_env: str = ""          # empty = use the provider's default key env var
    temperature: float = 0.2
    max_tokens: int = 1400


@dataclass
class HyperliquidConfig:
    network: str = "testnet"  # testnet | mainnet
    address: str = ""


@dataclass
class AgentConfig:
    name: str = "oneechan"
    mode: str = "paper"  # paper | live
    coins: list = field(default_factory=lambda: ["BTC", "ETH", "SOL"])
    interval: str = "1h"
    lookback: int = 96
    cadence_seconds: int = 300
    cooldown_seconds: int = 3600
    max_position_pct: float = 25.0
    max_leverage: int = 3
    risk_per_trade_pct: float = 1.0
    fee_rate: float = 0.00035
    paper_initial_usdc: float = 10000.0
    paper_state_path: str = "state/paper.json"
    team_enabled: bool = True  # run the multi-agent team (vs single Brain)


@dataclass
class TeamConfig:
    # Specialist roles to run before the Lead. Lead always runs. Pass an empty
    # list to run a lead-only desk (closest to the single-brain behaviour).
    roles: list = field(default_factory=lambda: [
        "market_analyst", "flow_analyst", "advisor", "risk_manager",
    ])


@dataclass
class Config:
    agent: AgentConfig = field(default_factory=AgentConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    hyperliquid: HyperliquidConfig = field(default_factory=HyperliquidConfig)
    team: TeamConfig = field(default_factory=TeamConfig)

    @classmethod
    def load(cls, path: str = "config.yaml") -> "Config":
        load_dotenv()
        data: dict[str, Any] = {}
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Config":
        cfg = cls()
        for section in ("agent", "llm", "hyperliquid", "team"):
            target = getattr(cfg, section)
            for key, value in (data.get(section) or {}).items():
                if hasattr(target, key):
                    setattr(target, key, value)
        return cfg
