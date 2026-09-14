from __future__ import annotations

from .config import ProjectConfig
from .models import CostEstimate


PRICING_AS_OF = "2026-09-09"
PRICES: dict[tuple[str, str, str], tuple[float, float]] = {
    ("openai", "gpt-5-mini", "batch"): (0.125, 1.0),
    ("openai", "gpt-5-mini", "sync"): (0.25, 2.0),
    ("anthropic", "claude-haiku-4-5", "batch"): (0.5, 2.5),
    ("anthropic", "claude-haiku-4-5", "sync"): (1.0, 5.0),
}


def estimate_cost(config: ProjectConfig, provider: str, request_texts: list[str], execution: str = "batch") -> CostEstimate:
    if execution not in {"batch", "sync"}:
        raise ValueError(f"Unsupported execution mode: {execution}")
    settings = config.providers[provider]
    input_tokens = sum(max(1, (len(text) + 3) // 4) for text in request_texts)
    output_tokens = len(request_texts) * config.task.max_output_tokens
    if execution == "sync" and settings.sync_input_price_per_million is not None:
        prices = (settings.sync_input_price_per_million, settings.sync_output_price_per_million)
        as_of = "project.yaml sync override"
    elif execution == "batch" and settings.input_price_per_million is not None:
        prices = (settings.input_price_per_million, settings.output_price_per_million)
        as_of = "project.yaml override"
    else:
        prices = PRICES.get((provider, settings.model, execution))
        as_of = PRICING_AS_OF if prices else None
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
