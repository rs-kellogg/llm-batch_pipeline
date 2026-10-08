from __future__ import annotations

from .config import ProjectConfig
from .models import CostEstimate


PRICING_AS_OF = "2026-10-08"
PRICES: dict[tuple[str, str, str], tuple[float, float]] = {
    ("openai", "gpt-5-mini", "batch"): (0.125, 1.0),
    ("openai", "gpt-5-mini", "sync"): (0.25, 2.0),
    ("openai", "gpt-5.4-mini", "batch"): (0.375, 2.25),
    ("openai", "gpt-5.4-mini", "sync"): (0.75, 4.5),
    ("anthropic", "claude-haiku-4-5", "batch"): (0.5, 2.5),
    ("anthropic", "claude-haiku-4-5", "sync"): (1.0, 5.0),
}


def token_prices(
    config: ProjectConfig,
    provider: str,
    execution: str,
) -> tuple[float | None, float | None, str | None]:
    """Resolve configured or built-in token prices for one execution mode."""
    if execution not in {"batch", "sync"}:
        raise ValueError(f"Unsupported execution mode: {execution}")
    settings = config.providers[provider]
    if execution == "sync" and settings.sync_input_price_per_million is not None:
        prices = (settings.sync_input_price_per_million, settings.sync_output_price_per_million)
        as_of = "project.yaml sync override"
    elif execution == "batch" and settings.input_price_per_million is not None:
        prices = (settings.input_price_per_million, settings.output_price_per_million)
        as_of = "project.yaml override"
    else:
        prices = PRICES.get((provider, settings.model, execution))
        as_of = PRICING_AS_OF if prices else None
    if prices is None:
        return None, None, as_of
    return prices[0], prices[1], as_of


def estimate_cost(config: ProjectConfig, provider: str, request_texts: list[str], execution: str = "batch") -> CostEstimate:
    settings = config.providers[provider]
    input_tokens = sum(max(1, (len(text) + 3) // 4) for text in request_texts)
    output_tokens = len(request_texts) * config.task.max_output_tokens
    input_price, output_price, as_of = token_prices(config, provider, execution)
    prices = None if input_price is None or output_price is None else (input_price, output_price)
    usd = None if prices is None else input_tokens / 1_000_000 * prices[0] + output_tokens / 1_000_000 * prices[1]
    return CostEstimate(
        provider=provider,
        model=settings.model,
        execution=execution,
        request_count=len(request_texts),
        estimated_input_tokens=input_tokens,
        maximum_output_tokens=output_tokens,
        estimated_usd=usd,
        input_price_per_million=prices[0] if prices else None,
        output_price_per_million=prices[1] if prices else None,
        pricing_as_of=as_of,
    )
