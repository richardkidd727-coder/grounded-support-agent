"""List prices for cost reporting.

Everything runs on free tiers, so the *actual* cost is 0. The observability drawer still shows
what each turn would cost at paid list prices - the number you would budget with in production.
Prices are USD per million tokens and are estimates; check the provider's pricing page.
"""

from __future__ import annotations

from support_agent.llm.types import Usage

# (input, cached input, output) USD per 1M tokens
PRICES: dict[str, tuple[float, float, float]] = {
    "openai/gpt-oss-120b": (0.15, 0.075, 0.60),
    "openai/gpt-oss-20b": (0.075, 0.0375, 0.30),
    "qwen/qwen3.8-27b": (0.29, 0.145, 0.59),
    "gemini-2.5-flash": (0.30, 0.03, 2.50),
    "gemini-2.5-flash-lite": (0.10, 0.01, 0.40),
}


def list_price_usd(model: str, usage: Usage) -> float:
    price = PRICES.get(model)
    if price is None:
        return 0.0
    input_price, cached_price, output_price = price
    uncached = max(usage.input_tokens - usage.cached_input_tokens, 0)
    cost = (
        uncached * input_price
        + usage.cached_input_tokens * cached_price
        + usage.output_tokens * output_price
    ) / 1_000_000
    return round(cost, 8)
