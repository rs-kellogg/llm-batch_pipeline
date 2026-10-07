from __future__ import annotations

from datetime import date

import pytest

from kellogg_llm_batch.provider_options import validate_provider_options


@pytest.mark.parametrize(
    ("provider", "options"),
    [
        ("openai", {"temperature": 0, "top_p": 1, "reasoning": {"effort": "low"}}),
        ("anthropic", {"temperature": 0, "top_p": 1, "top_k": 0, "stop_sequences": ["END"]}),
        ("openai", {"new_provider_option": {"enabled": True}}),
        ("anthropic", {"new_provider_option": {"enabled": True}}),
    ],
)
def test_valid_provider_options(provider, options):
    validate_provider_options(provider, options)


@pytest.mark.parametrize(
    ("provider", "options", "message"),
    [
        ("openai", {"temperature": 2.1}, "between 0 and 2"),
        ("openai", {"top_p": True}, "must be a number"),
        ("openai", {"reasoning": "low"}, "must be an object"),
        ("openai", {"reasoning": {"effort": "extreme"}}, "must be one of"),
        ("anthropic", {"temperature": 1.1}, "between 0 and 1"),
        ("anthropic", {"top_k": 1.5}, "must be an integer"),
        ("anthropic", {"stop_sequences": "END"}, "array of strings"),
        ("openai", {"seed": 42}, "select input rows only"),
        ("anthropic", {"seed": 42}, "select input rows only"),
        ("openai", {"input": "replacement"}, "package-managed"),
        ("anthropic", {"messages": []}, "package-managed"),
        ("openai", {"metadata_date": date(2026, 10, 7)}, "JSON-serializable"),
        ("openai", {"temperature": float("nan")}, "JSON-serializable"),
    ],
)
def test_invalid_provider_options(provider, options, message):
    with pytest.raises(ValueError, match=message):
        validate_provider_options(provider, options)
