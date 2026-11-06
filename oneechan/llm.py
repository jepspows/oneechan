"""Multi-provider LLM abstraction — one interface, many providers.

OpenAI-compatible providers (OpenAI, DeepSeek, Gemini, OpenRouter, Groq, xAI,
Together, Mistral, Perplexity, ...) go through the `openai` SDK. Anthropic
(Claude) goes through the `anthropic` SDK. Each provider has a preset
(base URL + key env var + sensible default model); everything is overridable
from config.yaml.
"""
from __future__ import annotations

import os
from typing import Any

# provider -> preset. `base_url` may be "" (provider default / SDK native).
PROVIDERS: dict[str, dict[str, str]] = {
    "deepseek":   {"base_url": "https://api.deepseek.com", "key_env": "DEEPSEEK_API_KEY", "default_model": "deepseek-v4-pro", "backend": "openai"},
    "openai":     {"base_url": "https://api.openai.com/v1", "key_env": "OPENAI_API_KEY", "default_model": "gpt-4o", "backend": "openai"},
    "anthropic":  {"base_url": "", "key_env": "ANTHROPIC_API_KEY", "default_model": "claude-sonnet-5-5", "backend": "anthropic"},
    "gemini":     {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai/", "key_env": "GEMINI_API_KEY", "default_model": "gemini-3-flash-preview", "backend": "openai"},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "key_env": "OPENROUTER_API_KEY", "default_model": "anthropic/claude-sonnet-5-5", "backend": "openai"},
    "groq":       {"base_url": "https://api.groq.com/openai/v1", "key_env": "GROQ_API_KEY", "default_model": "llama-3.3-70b-versatile", "backend": "openai"},
    "xai":        {"base_url": "https://api.x.ai/v1", "key_env": "XAI_API_KEY", "default_model": "grok-2-latest", "backend": "openai"},
    "together":   {"base_url": "https://api.together.xyz/v1", "key_env": "TOGETHER_API_KEY", "default_model": "meta-llama/Llama-3.3-70B-Instruct-Turbo", "backend": "openai"},
    "mistral":    {"base_url": "https://api.mistral.ai/v1", "key_env": "MISTRAL_API_KEY", "default_model": "mistral-large-latest", "backend": "openai"},
    "perplexity": {"base_url": "https://api.perplexity.ai", "key_env": "PERPLEXITY_API_KEY", "default_model": "sonar-pro", "backend": "openai"},
    "cohere":     {"base_url": "https://api.cohere.com/v1", "key_env": "CO_API_KEY", "default_model": "command-r-plus", "backend": "openai"},
    "fireworks":  {"base_url": "https://api.fireworks.ai/inference/v1", "key_env": "FIREWORKS_API_KEY", "default_model": "accounts/fireworks/models/llama-v3p3-70b-instruct", "backend": "openai"},
    "cerebras":   {"base_url": "https://api.cerebras.ai/v1", "key_env": "CEREBRAS_API_KEY", "default_model": "llama3.3-70b", "backend": "openai"},
    "qwen":       {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "key_env": "DASHSCOPE_API_KEY", "default_model": "qwen-max", "backend": "openai"},
}


class OpenAICompatLLM:
    def __init__(self, model: str, base_url: str, api_key: str,
                 temperature: float, max_tokens: int):
        from openai import OpenAI
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.client = OpenAI(api_key=api_key, base_url=base_url)

    def complete(self, system: str, user: str) -> str:
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        return (resp.choices[0].message.content or "").strip()


class AnthropicLLM:
    def __init__(self, model: str, api_key: str, temperature: float, max_tokens: int):
        from anthropic import Anthropic
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.client = Anthropic(api_key=api_key)

    def complete(self, system: str, user: str) -> str:
        resp = self.client.messages.create(
            model=self.model,
            system=system,
            messages=[{"role": "user", "content": user}],
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        return (resp.content[0].text or "").strip()


def get_llm(provider: str, model: str, base_url: str, api_key_env: str,
            temperature: float, max_tokens: int):
    key = provider.lower().strip()
    preset = PROVIDERS.get(key)
    if not preset:
        raise ValueError(f"unknown provider '{provider}'. Supported: {sorted(PROVIDERS)}")

    key_env = api_key_env or preset["key_env"]
    api_key = os.environ.get(key_env)
    if not api_key:
        raise ValueError(f"{key_env} is not set. Add it to .env to use provider '{provider}'.")

    model = model or preset["default_model"]
    if preset["backend"] == "anthropic":
        return AnthropicLLM(model, api_key, temperature, max_tokens)
    return OpenAICompatLLM(model, base_url or preset["base_url"],
                           api_key, temperature, max_tokens)


def default_key_env(provider: str) -> str:
    preset = PROVIDERS.get(provider.lower().strip())
    return preset["key_env"] if preset else ""
