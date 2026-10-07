from __future__ import annotations

import json
import math
from typing import Any


RESERVED_PROVIDER_OPTIONS: dict[str, set[str]] = {
    "openai": {
        "input",
        "instructions",
        "max_output_tokens",
        "model",
        "stream",
        "text",
    },
    "anthropic": {
        "max_tokens",
        "messages",
        "model",
        "output_config",
        "stream",
        "system",
    },
}

OPENAI_REASONING_EFFORTS = {
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
}


def validate_provider_options(provider: str, options: dict[str, Any]) -> None:
    """Validate stable provider option shapes while allowing future API keys."""
    try:
        json.dumps(options, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("options must contain only JSON-serializable values") from exc

    if "seed" in options:
        raise ValueError(
            "options.seed is not supported by the OpenAI Responses or Anthropic Messages "
            "endpoint; evaluation.random_seed and prepare --seed select input rows only"
        )

    reserved = RESERVED_PROVIDER_OPTIONS.get(provider, set()) & set(options)
    if reserved:
        raise ValueError(
            f"options contains package-managed {provider} request keys: {sorted(reserved)}"
        )

    if provider == "openai":
        _validate_number(options, "temperature", minimum=0, maximum=2)
        _validate_number(options, "top_p", minimum=0, maximum=1)
        _validate_openai_reasoning(options)
    elif provider == "anthropic":
        _validate_number(options, "temperature", minimum=0, maximum=1)
        _validate_number(options, "top_p", minimum=0, maximum=1)
        _validate_integer(options, "top_k", minimum=0)
        _validate_stop_sequences(options)


def uses_multiple_sampling_controls(options: dict[str, Any]) -> bool:
    return "temperature" in options and "top_p" in options


def _validate_number(
    options: dict[str, Any],
    name: str,
    *,
    minimum: float,
    maximum: float,
) -> None:
    if name not in options:
        return
    value = options[name]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"options.{name} must be a number")
    if (isinstance(value, float) and not math.isfinite(value)) or not minimum <= value <= maximum:
        raise ValueError(f"options.{name} must be between {minimum} and {maximum}")


def _validate_integer(
    options: dict[str, Any],
    name: str,
    *,
    minimum: int,
) -> None:
    if name not in options:
        return
    value = options[name]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"options.{name} must be an integer")
    if value < minimum:
        raise ValueError(f"options.{name} must be at least {minimum}")


def _validate_openai_reasoning(options: dict[str, Any]) -> None:
    if "reasoning" not in options:
        return
    reasoning = options["reasoning"]
    if not isinstance(reasoning, dict):
        raise ValueError("options.reasoning must be an object")
    if "effort" not in reasoning:
        return
    effort = reasoning["effort"]
    if effort not in OPENAI_REASONING_EFFORTS:
        allowed = ", ".join(sorted(OPENAI_REASONING_EFFORTS))
        raise ValueError(f"options.reasoning.effort must be one of: {allowed}")


def _validate_stop_sequences(options: dict[str, Any]) -> None:
    if "stop_sequences" not in options:
        return
    stop_sequences = options["stop_sequences"]
    if not isinstance(stop_sequences, list) or any(
        not isinstance(value, str) for value in stop_sequences
    ):
        raise ValueError("options.stop_sequences must be an array of strings")
