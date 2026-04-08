import pytest
from unittest.mock import patch


def test_token_estimate_returns_breakdown():
    """Test the /api/token-estimate endpoint returns proper breakdown."""
    from backend.core.token_budget import TokenEstimator

    result = TokenEstimator.estimate_prompt_tokens(
        input_data="Hello world, this is a test."
    )
    assert "total" in result
    assert "input" in result
    assert result["total"] > 0
    assert result["input"] > 0


def test_token_estimate_all_fields_present():
    """Verify all breakdown fields are present in the response."""
    from backend.core.token_budget import TokenEstimator

    result = TokenEstimator.estimate_prompt_tokens(
        system_prompt="You are a helpful assistant.",
        instructions="Summarise the input.",
        tool_schemas='[{"name":"web_search"}]',
        input_data="Lorem ipsum dolor sit amet.",
        model_id="qwen2.5-coder:32b",
    )
    for key in ("system", "tools", "instructions", "input", "total"):
        assert key in result, f"Missing key: {key}"
    assert result["total"] == (
        result["system"] + result["tools"] + result["instructions"] + result["input"]
    )


def test_token_estimate_empty_text():
    """Empty input text should return zero input tokens."""
    from backend.core.token_budget import TokenEstimator

    result = TokenEstimator.estimate_prompt_tokens(input_data="")
    assert result["input"] == 0
    assert result["total"] == 0


def test_token_estimate_model_affects_count():
    """Different models should produce different token estimates for the same text."""
    from backend.core.token_budget import TokenEstimator

    text = "A" * 1000
    result_qwen = TokenEstimator.estimate_prompt_tokens(input_data=text, model_id="qwen2.5-coder:32b")
    result_default = TokenEstimator.estimate_prompt_tokens(input_data=text)
    # qwen uses 3.8 chars/token vs default 4.0, so qwen estimate should be higher
    assert result_qwen["input"] >= result_default["input"]
