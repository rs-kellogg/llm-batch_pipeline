from __future__ import annotations

import pytest

from kellogg_llm_batch.config import load_config
from kellogg_llm_batch.pricing import PRICES, estimate_cost
from kellogg_llm_batch.scaffold import scaffold_project


def _config(tmp_path, **provider_overrides):
    root = scaffold_project(tmp_path / "project")
    config = load_config(root / "project.yaml")
    if provider_overrides:
        updated = config.providers["openai"].model_copy(update=provider_overrides)
        config = config.model_copy(update={"providers": {**config.providers, "openai": updated}})
    return config


def test_batch_estimate_uses_default_price_table(tmp_path):
    config = _config(tmp_path)
    estimate = estimate_cost(config, "openai", ["hello world"], "batch")
    price = PRICES[("openai", config.providers["openai"].model, "batch")]
    assert (estimate.input_price_per_million, estimate.output_price_per_million) == price
    assert estimate.pricing_as_of == "2026-09-09"
    assert estimate.estimated_usd == pytest.approx(
        estimate.estimated_input_tokens / 1_000_000 * price[0] + estimate.maximum_output_tokens / 1_000_000 * price[1]
    )


def test_sync_estimate_uses_default_price_table(tmp_path):
    config = _config(tmp_path)
    estimate = estimate_cost(config, "openai", ["hello world"], "sync")
    price = PRICES[("openai", config.providers["openai"].model, "sync")]
    assert (estimate.input_price_per_million, estimate.output_price_per_million) == price
    assert estimate.pricing_as_of == "2026-09-09"


def test_batch_override_takes_priority_over_default_table(tmp_path):
    config = _config(tmp_path, input_price_per_million=9.0, output_price_per_million=18.0)
    estimate = estimate_cost(config, "openai", ["hello world"], "batch")

    assert (estimate.input_price_per_million, estimate.output_price_per_million) == (9.0, 18.0)
    assert estimate.pricing_as_of == "project.yaml override"
    default_price = PRICES[("openai", config.providers["openai"].model, "batch")]
    assert (estimate.input_price_per_million, estimate.output_price_per_million) != default_price


def test_sync_override_is_independent_of_batch_override(tmp_path):
    config = _config(
        tmp_path,
        input_price_per_million=9.0,
        output_price_per_million=18.0,
        sync_input_price_per_million=99.0,
        sync_output_price_per_million=198.0,
    )
    batch_estimate = estimate_cost(config, "openai", ["hello world"], "batch")
    sync_estimate = estimate_cost(config, "openai", ["hello world"], "sync")

    assert (batch_estimate.input_price_per_million, batch_estimate.output_price_per_million) == (9.0, 18.0)
    assert batch_estimate.pricing_as_of == "project.yaml override"
    assert (sync_estimate.input_price_per_million, sync_estimate.output_price_per_million) == (99.0, 198.0)
    assert sync_estimate.pricing_as_of == "project.yaml sync override"


def test_sync_override_does_not_leak_into_batch_pricing(tmp_path):
    config = _config(tmp_path, sync_input_price_per_million=99.0, sync_output_price_per_million=198.0)
    batch_estimate = estimate_cost(config, "openai", ["hello world"], "batch")

    default_price = PRICES[("openai", config.providers["openai"].model, "batch")]
    assert (batch_estimate.input_price_per_million, batch_estimate.output_price_per_million) == default_price
    assert batch_estimate.pricing_as_of == "2026-09-09"


def test_unknown_model_without_override_has_no_price(tmp_path):
    config = _config(tmp_path, model="gpt-unreleased-model")
    estimate = estimate_cost(config, "openai", ["hello world"], "batch")

    assert estimate.estimated_usd is None
    assert estimate.input_price_per_million is None
    assert estimate.output_price_per_million is None
    assert estimate.pricing_as_of is None


def test_invalid_execution_mode_is_rejected(tmp_path):
    config = _config(tmp_path)
    with pytest.raises(ValueError, match="Unsupported execution mode"):
        estimate_cost(config, "openai", ["hello world"], "weekly")
