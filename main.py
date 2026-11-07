"""Oneechan CLI entry point."""
from __future__ import annotations

import argparse

from oneechan.agent import Agent
from oneechan.config import Config


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="oneechan",
        description="Oneechan — an AI trading agent for Hyperliquid L1.",
    )
    parser.add_argument("--config", default="config.yaml", help="path to config.yaml")
    parser.add_argument("--coins", nargs="*", help="override the coin list")
    parser.add_argument("--mode", choices=["paper", "live"], help="paper (default) | live")
    parser.add_argument("--network", choices=["mainnet", "testnet"], help="override network")
    parser.add_argument("--interval", help="override candle interval (e.g. 1h, 15m)")
    parser.add_argument("--brain",
                        help="force the brain: 'mock' (no LLM) or a provider name "
                             "(deepseek, openai, anthropic, gemini, openrouter, groq, "
                             "xai, together, mistral, perplexity)")
    parser.add_argument("--team", dest="team", action="store_true", default=None,
                        help="force the multi-agent team on")
    parser.add_argument("--no-team", dest="team", action="store_false",
                        help="force the multi-agent team off (single brain)")
    parser.add_argument("--once", action="store_true",
                        help="run a single decision cycle then exit")
    args = parser.parse_args()

    cfg = Config.load(args.config)
    if args.coins:
        cfg.agent.coins = args.coins
    if args.mode:
        cfg.agent.mode = args.mode
    if args.network:
        cfg.hyperliquid.network = args.network
    if args.interval:
        cfg.agent.interval = args.interval

    agent = Agent(cfg, brain_override=args.brain, team_override=args.team)
    if args.once:
        agent.cycle()
    else:
        agent.run()


if __name__ == "__main__":
    main()
