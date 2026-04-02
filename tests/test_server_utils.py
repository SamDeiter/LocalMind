import pytest
from unittest.mock import patch
from backend.utils.server_utils import estimate_task_complexity

# Deterministic mock tiers
MOCK_TIERS = {
    "light": "mock-light-model",
    "medium": "mock-medium-model",
    "heavy": "mock-heavy-model",
    "ultra": "mock-ultra-model",
}

@patch("backend.utils.server_utils.MODEL_TIERS", MOCK_TIERS)
def test_empty_and_whitespace():
    """Verify that empty or whitespace-only strings return a simple score."""
    res1 = estimate_task_complexity("")
    res2 = estimate_task_complexity("    ")

    # base score 3, length < 30 (-1) -> 2
    assert res1["score"] == 2
    assert res1["tier"] == "light"
    assert res1["model"] == MOCK_TIERS["light"]

    assert res2["score"] == 2
    assert res2["tier"] == "light"
    assert res2["model"] == MOCK_TIERS["light"]

@patch("backend.utils.server_utils.MODEL_TIERS", MOCK_TIERS)
def test_light_greetings():
    """Verify that short greetings decrease the score."""
    res = estimate_task_complexity("hello")
    # base 3, greeting (-2), length < 30 (-1) -> 0
    assert res["score"] == 0
    assert res["tier"] == "light"
    assert res["model"] == MOCK_TIERS["light"]

@patch("backend.utils.server_utils.MODEL_TIERS", MOCK_TIERS)
def test_mixed_signals():
    """Test mixed signals: greeting + heavy keywords."""
    res = estimate_task_complexity("Hello! Please refactor my entire architecture.")
    # base 3, greeting matches (-2)
    # length > 30 (no -1 penalty)
    # deep analysis: "refactor" (+3)
    # So: 3 (base) - 2 (greeting) + 3 (deep_analysis) = 4
    assert res["score"] == 4
    assert res["tier"] == "medium"
    assert res["model"] == MOCK_TIERS["medium"]

@patch("backend.utils.server_utils.MODEL_TIERS", MOCK_TIERS)
def test_extreme_lengths():
    """Test message thousands of characters long but no heavy keywords."""
    long_msg = "a" * 5000
    res = estimate_task_complexity(long_msg)
    # base 3, length > 30 (no penalty)
    # no keywords
    # So: 3
    assert res["score"] == 3
    assert res["tier"] == "light"
    assert res["model"] == MOCK_TIERS["light"]

@patch("backend.utils.server_utils.MODEL_TIERS", MOCK_TIERS)
def test_markdown_special_characters():
    """Verify inputs with backticks or emojis behave predictably."""
    msg = "```python\nprint('hello')\n``` 🚀"
    res = estimate_task_complexity(msg)
    # base 3, length > 30 -> no penalty
    # greeting match? '```python' -> no
    assert res["score"] == 3
    assert res["tier"] == "light"
    assert res["model"] == MOCK_TIERS["light"]

@patch("backend.utils.server_utils.MODEL_TIERS", MOCK_TIERS)
def test_boundary_clamping_min():
    """Test score is clamped to exactly 0."""
    res = estimate_task_complexity("hi")
    # base 3, greeting (-2), length < 30 (-1) -> 0
    assert res["score"] == 0

@patch("backend.utils.server_utils.MODEL_TIERS", MOCK_TIERS)
def test_max_achievable_score():
    """Test the maximum achievable score with current logic."""
    # To get highest score: heavy code (+2) AND deep analysis (+3), length > 40
    # Base 3 + 2 + 3 = 8
    msg = "Please write a script to refactor the architecture of this component. " * 2
    res = estimate_task_complexity(msg)
    assert res["score"] == 8
    assert res["tier"] == "heavy"
    assert res["model"] == MOCK_TIERS["heavy"]

@patch("backend.utils.server_utils.MODEL_TIERS", MOCK_TIERS)
def test_case_sensitivity():
    """Ensure case-insensitive matching for keywords."""
    res1 = estimate_task_complexity("please REFACTOR my code")
    res2 = estimate_task_complexity("please refactor my code")
    assert res1["score"] == res2["score"]

@patch("backend.utils.server_utils.MODEL_TIERS", MOCK_TIERS)
def test_heavy_code_length_condition():
    """Test heavy code keywords only add score if length > 40."""
    # "write a script" is 14 chars. score: 3 - 1 (length < 30) = 2. It doesn't get +2 because len < 40.
    res_short = estimate_task_complexity("write a script")
    assert res_short["score"] == 2

    # "write a script" but with padding to exceed 40 chars
    res_long = estimate_task_complexity("write a script to do something very specific " + "a"*20)
    # base 3, length > 40 (no penalty), heavy code (+2) = 5
    assert res_long["score"] == 5
    assert res_long["tier"] == "medium"
