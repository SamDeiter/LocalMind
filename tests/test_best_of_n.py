"""
Tests for backend/inference/best_of_n.py — Best-of-N sampling with scoring.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.inference.best_of_n import (
    BestOfNSampler,
    SampleResult,
    _Candidate,
    _is_valid_json,
    _parse_judge_response,
)


# =========================================================================
# SampleResult dataclass
# =========================================================================


class TestSampleResult:
    def test_to_dict_returns_all_keys(self):
        result = SampleResult(
            response="hello",
            score=8.0,
            reasoning="good",
            tokens_in=100,
            tokens_out=50,
            n_generated=4,
            duration_ms=1234,
        )
        d = result.to_dict()
        assert d == {
            "response": "hello",
            "score": 8.0,
            "reasoning": "good",
            "tokens_in": 100,
            "tokens_out": 50,
            "n_generated": 4,
            "duration_ms": 1234,
        }

    def test_to_dict_default_values(self):
        result = SampleResult(response="x", score=1.0, reasoning="ok")
        d = result.to_dict()
        assert d["tokens_in"] == 0
        assert d["tokens_out"] == 0
        assert d["n_generated"] == 0
        assert d["duration_ms"] == 0


# =========================================================================
# _Candidate dataclass
# =========================================================================


class TestCandidate:
    def test_default_values(self):
        c = _Candidate(response="test", tool_calls=[])
        assert c.tokens_in == 0
        assert c.tokens_out == 0
        assert c.score == 0.0
        assert c.score_reasoning == ""

    def test_explicit_values(self):
        c = _Candidate(
            response="hello",
            tool_calls=[{"name": "f"}],
            tokens_in=10,
            tokens_out=20,
            score=7.5,
            score_reasoning="nice",
        )
        assert c.response == "hello"
        assert len(c.tool_calls) == 1
        assert c.score == 7.5


# =========================================================================
# _parse_judge_response
# =========================================================================


class TestParseJudgeResponse:
    def test_valid_json(self):
        text = '{"score": 8, "reasoning": "good"}'
        score, reasoning = _parse_judge_response(text)
        assert score == 8.0
        assert reasoning == "good"

    def test_json_in_markdown_fences(self):
        text = '```json\n{"score": 7, "reasoning": "decent"}\n```'
        score, reasoning = _parse_judge_response(text)
        assert score == 7.0
        assert reasoning == "decent"

    def test_json_in_plain_fences(self):
        text = '```\n{"score": 6, "reasoning": "okay"}\n```'
        score, reasoning = _parse_judge_response(text)
        assert score == 6.0
        assert reasoning == "okay"

    def test_plain_text_slash_notation(self):
        text = "I would rate this 7/10 because it is clear."
        score, reasoning = _parse_judge_response(text)
        assert score == 7.0

    def test_plain_text_score_colon(self):
        text = "Score: 9. The answer is complete."
        score, reasoning = _parse_judge_response(text)
        assert score == 9.0

    def test_score_clamped_high(self):
        text = '{"score": 15, "reasoning": "amazing"}'
        score, _ = _parse_judge_response(text)
        assert score == 10.0

    def test_score_clamped_low(self):
        text = '{"score": -3, "reasoning": "terrible"}'
        score, _ = _parse_judge_response(text)
        assert score == 0.0

    def test_completely_unparseable(self):
        text = "No numbers or scores anywhere in this text whatsoever"
        score, reasoning = _parse_judge_response(text)
        assert score == 5.0
        assert "could not parse" in reasoning.lower()

    def test_empty_string(self):
        score, reasoning = _parse_judge_response("")
        assert score == 5.0
        assert "could not parse" in reasoning.lower()

    def test_json_missing_score_key_defaults(self):
        text = '{"reasoning": "no score field"}'
        score, reasoning = _parse_judge_response(text)
        assert score == 5.0
        assert reasoning == "no score field"


# =========================================================================
# _is_valid_json
# =========================================================================


class TestIsValidJson:
    def test_valid_json_object(self):
        assert _is_valid_json('{"key": "value"}') is True

    def test_valid_json_array(self):
        assert _is_valid_json('[1, 2, 3]') is True

    def test_plain_text(self):
        assert _is_valid_json("hello world") is False

    def test_empty_string(self):
        assert _is_valid_json("") is False

    def test_json_number_not_object_or_array(self):
        assert _is_valid_json("42") is False

    def test_json_string_not_object_or_array(self):
        assert _is_valid_json('"hello"') is False

    def test_whitespace_around_valid_json(self):
        assert _is_valid_json('  {"a": 1}  ') is True


# =========================================================================
# _score_single_heuristic
# =========================================================================


class TestScoreSingleHeuristic:
    """Test the heuristic scoring via the sampler instance."""

    def _make_sampler(self):
        return BestOfNSampler(ollama_url="http://localhost:11434")

    def test_empty_response_low_score(self):
        sampler = self._make_sampler()
        c = _Candidate(response="", tool_calls=[])
        sampler._score_single_heuristic(c)
        assert c.score <= 2.0
        assert "empty" in c.score_reasoning

    def test_very_short_response_penalty(self):
        sampler = self._make_sampler()
        c = _Candidate(response="ok fine", tool_calls=[])
        sampler._score_single_heuristic(c)
        assert c.score < 5.0
        assert "very short" in c.score_reasoning

    def test_good_length_bonus(self):
        sampler = self._make_sampler()
        text = "A " * 200  # 400 chars — in the 100-2000 sweet spot
        c = _Candidate(response=text, tool_calls=[])
        sampler._score_single_heuristic(c)
        assert c.score >= 5.5
        assert "good length" in c.score_reasoning

    def test_valid_json_response_bonus(self):
        sampler = self._make_sampler()
        text = json.dumps({"action": "read_file", "path": "/tmp/test.py"})
        c = _Candidate(response=text, tool_calls=[])
        sampler._score_single_heuristic(c)
        assert "valid JSON" in c.score_reasoning

    def test_tool_calls_bonus(self):
        sampler = self._make_sampler()
        c = _Candidate(
            response="I will use a tool.",
            tool_calls=[{"name": "read_file"}, {"name": "write_file"}],
        )
        sampler._score_single_heuristic(c)
        assert "2 tool call(s)" in c.score_reasoning

    def test_truncated_response_penalty(self):
        sampler = self._make_sampler()
        text = "A " * 200 + "..."
        c = _Candidate(response=text, tool_calls=[])
        sampler._score_single_heuristic(c)
        assert "possibly truncated" in c.score_reasoning

    def test_error_refusal_penalty(self):
        sampler = self._make_sampler()
        text = "I cannot help with that request because it is disallowed."
        c = _Candidate(response=text, tool_calls=[])
        sampler._score_single_heuristic(c)
        assert "error/refusal" in c.score_reasoning

    def test_score_clamped_floor(self):
        sampler = self._make_sampler()
        # empty + error markers should push very negative, but clamped to 0
        c = _Candidate(response="", tool_calls=[])
        sampler._score_single_heuristic(c)
        assert c.score >= 0.0

    def test_score_clamped_ceiling(self):
        sampler = self._make_sampler()
        # valid JSON, good length, tool calls — should not exceed 10
        long_json = json.dumps({"data": "x" * 500})
        c = _Candidate(
            response=long_json,
            tool_calls=[{"a": 1}, {"b": 2}, {"c": 3}, {"d": 4}, {"e": 5}],
        )
        sampler._score_single_heuristic(c)
        assert c.score <= 10.0

    def test_short_response_mild_penalty(self):
        sampler = self._make_sampler()
        text = "This is about fifty chars of text to be short."
        c = _Candidate(response=text, tool_calls=[])
        sampler._score_single_heuristic(c)
        assert "short" in c.score_reasoning


# =========================================================================
# Helper: build mocked httpx client
# =========================================================================


def _make_mock_client(response_json, status_code=200):
    """Create a mock httpx.AsyncClient that returns a given response."""
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.json.return_value = response_json
    mock_response.text = json.dumps(response_json)

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


def _ollama_ok(content="response text", tool_calls=None, tokens_in=100, tokens_out=50):
    """Standard successful Ollama /api/chat response."""
    return {
        "message": {
            "content": content,
            "tool_calls": tool_calls or [],
        },
        "prompt_eval_count": tokens_in,
        "eval_count": tokens_out,
    }


# =========================================================================
# _call_ollama
# =========================================================================


class TestCallOllama:
    @pytest.mark.asyncio
    async def test_successful_response(self):
        data = _ollama_ok("hello world")
        mock_client = _make_mock_client(data)
        sampler = BestOfNSampler(ollama_url="http://localhost:11434")
        with patch(
            "backend.inference.best_of_n.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await sampler._call_ollama(
                messages=[{"role": "user", "content": "hi"}],
                tools=None,
                model="test-model",
                temperature=0.7,
                candidate_idx=0,
            )
        assert result is not None
        assert result.response == "hello world"
        assert result.tokens_in == 100
        assert result.tokens_out == 50

    @pytest.mark.asyncio
    async def test_http_error_returns_none(self):
        mock_client = _make_mock_client({"error": "bad"}, status_code=500)
        sampler = BestOfNSampler(ollama_url="http://localhost:11434")
        with patch(
            "backend.inference.best_of_n.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await sampler._call_ollama(
                messages=[{"role": "user", "content": "hi"}],
                tools=None,
                model="test-model",
                temperature=0.7,
                candidate_idx=0,
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_timeout_returns_none(self):
        import httpx as httpx_mod

        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx_mod.TimeoutException("timed out")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        sampler = BestOfNSampler(ollama_url="http://localhost:11434")
        with patch(
            "backend.inference.best_of_n.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await sampler._call_ollama(
                messages=[{"role": "user", "content": "hi"}],
                tools=None,
                model="test-model",
                temperature=0.7,
                candidate_idx=0,
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_response_with_tool_calls(self):
        tools = [{"function": {"name": "search", "arguments": {"q": "test"}}}]
        data = _ollama_ok("using tool", tool_calls=tools)
        mock_client = _make_mock_client(data)
        sampler = BestOfNSampler(ollama_url="http://localhost:11434")
        with patch(
            "backend.inference.best_of_n.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await sampler._call_ollama(
                messages=[{"role": "user", "content": "search"}],
                tools=[{"type": "function", "function": {"name": "search"}}],
                model="test-model",
                temperature=0.7,
                candidate_idx=0,
            )
        assert result is not None
        assert len(result.tool_calls) == 1

    @pytest.mark.asyncio
    async def test_response_with_error_key(self):
        data = {"error": "model not found"}
        mock_client = _make_mock_client(data, status_code=200)
        sampler = BestOfNSampler(ollama_url="http://localhost:11434")
        with patch(
            "backend.inference.best_of_n.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await sampler._call_ollama(
                messages=[{"role": "user", "content": "hi"}],
                tools=None,
                model="bad-model",
                temperature=0.7,
                candidate_idx=0,
            )
        assert result is None


# =========================================================================
# BestOfNSampler.sample()
# =========================================================================


class TestSample:
    def _messages(self):
        return [{"role": "user", "content": "Write a hello world function"}]

    @pytest.mark.asyncio
    async def test_n1_single_call(self):
        data = _ollama_ok("def hello(): print('hi')")
        mock_client = _make_mock_client(data)
        sampler = BestOfNSampler(ollama_url="http://localhost:11434")
        with patch(
            "backend.inference.best_of_n.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await sampler.sample(
                messages=self._messages(),
                tools=None,
                model="test-model",
                n=1,
            )
        assert isinstance(result, SampleResult)
        assert result.response == "def hello(): print('hi')"
        assert result.n_generated == 1

    @pytest.mark.asyncio
    async def test_n4_returns_best_scored(self):
        """With heuristic scoring, the longest reasonable response wins."""
        responses = [
            "ok",  # very short -> penalty
            "A decent response " * 20,  # good length
            "A " * 300,  # good length
            "",  # empty -> big penalty
        ]
        call_count = 0

        async def _fake_call_ollama(messages, tools, model, temperature, candidate_idx):
            nonlocal call_count
            idx = call_count
            call_count += 1
            return _Candidate(
                response=responses[idx % len(responses)],
                tool_calls=[],
                tokens_in=50,
                tokens_out=25,
            )

        sampler = BestOfNSampler(ollama_url="http://localhost:11434")
        with patch.object(sampler, "_call_ollama", side_effect=_fake_call_ollama):
            result = await sampler.sample(
                messages=self._messages(),
                tools=None,
                model="test-model",
                n=4,
            )
        assert result.n_generated == 4
        # The winner should NOT be the empty response
        assert result.response != ""
        assert result.score > 1.0

    @pytest.mark.asyncio
    async def test_all_candidates_fail_raises(self):
        sampler = BestOfNSampler(ollama_url="http://localhost:11434")

        async def _fail(*args, **kwargs):
            return None

        with patch.object(sampler, "_call_ollama", side_effect=_fail):
            with pytest.raises(RuntimeError, match="All .* failed"):
                await sampler.sample(
                    messages=self._messages(),
                    tools=None,
                    model="test-model",
                    n=4,
                )

    @pytest.mark.asyncio
    async def test_some_candidates_fail_returns_best_survivor(self):
        call_idx = 0

        async def _partial_fail(messages, tools, model, temperature, candidate_idx):
            nonlocal call_idx
            idx = call_idx
            call_idx += 1
            if idx % 2 == 0:
                return None  # fail every other
            return _Candidate(
                response="Survivor response content is good " * 5,
                tool_calls=[],
                tokens_in=50,
                tokens_out=25,
            )

        sampler = BestOfNSampler(ollama_url="http://localhost:11434")
        with patch.object(sampler, "_call_ollama", side_effect=_partial_fail):
            result = await sampler.sample(
                messages=self._messages(),
                tools=None,
                model="test-model",
                n=4,
            )
        assert result.n_generated == 2  # only 2 of 4 survived
        assert "Survivor" in result.response

    @pytest.mark.asyncio
    async def test_with_scorer_model_uses_llm_judge(self):
        """When scorer_model is set, LLM-as-judge scoring should be used."""
        candidate = _Candidate(
            response="good answer " * 20,
            tool_calls=[],
            tokens_in=100,
            tokens_out=50,
        )

        async def _one_candidate(*args, **kwargs):
            return candidate

        judge_response = _ollama_ok('{"score": 9, "reasoning": "excellent"}')

        sampler = BestOfNSampler(
            ollama_url="http://localhost:11434",
            scorer_model="gemma4:e4b",
        )

        mock_client = _make_mock_client(judge_response)

        with patch.object(sampler, "_call_ollama", side_effect=_one_candidate):
            with patch(
                "backend.inference.best_of_n.httpx.AsyncClient",
                return_value=mock_client,
            ):
                result = await sampler.sample(
                    messages=self._messages(),
                    tools=None,
                    model="test-model",
                    n=1,
                )
        assert result.score == 9.0
        assert "excellent" in result.reasoning

    @pytest.mark.asyncio
    async def test_without_scorer_model_uses_heuristic(self):
        candidate = _Candidate(
            response="A solid response with enough length " * 10,
            tool_calls=[],
            tokens_in=100,
            tokens_out=50,
        )

        async def _one_candidate(*args, **kwargs):
            return candidate

        sampler = BestOfNSampler(
            ollama_url="http://localhost:11434",
            scorer_model=None,
        )
        with patch.object(sampler, "_call_ollama", side_effect=_one_candidate):
            result = await sampler.sample(
                messages=self._messages(),
                tools=None,
                model="test-model",
                n=1,
            )
        # Heuristic score — should be around 6 for good length text
        assert result.score > 0.0
        assert "good length" in result.reasoning

    @pytest.mark.asyncio
    async def test_llm_judge_fails_falls_back_to_heuristic(self):
        candidate = _Candidate(
            response="A reasonable answer " * 20,
            tool_calls=[],
            tokens_in=100,
            tokens_out=50,
        )

        async def _one_candidate(*args, **kwargs):
            return candidate

        sampler = BestOfNSampler(
            ollama_url="http://localhost:11434",
            scorer_model="gemma4:e4b",
        )

        # Make the judge call raise an exception
        async def _judge_explodes(candidates, original_messages):
            raise RuntimeError("Judge model crashed")

        with patch.object(sampler, "_call_ollama", side_effect=_one_candidate):
            with patch.object(
                sampler, "_score_with_llm_judge", side_effect=_judge_explodes
            ):
                result = await sampler.sample(
                    messages=self._messages(),
                    tools=None,
                    model="test-model",
                    n=1,
                )
        # Should have fallen back to heuristic scoring
        assert result.score > 0.0
        assert "good length" in result.reasoning

    @pytest.mark.asyncio
    async def test_n_less_than_1_treated_as_1(self):
        candidate = _Candidate(
            response="response " * 30,
            tool_calls=[],
            tokens_in=50,
            tokens_out=25,
        )

        async def _one_candidate(*args, **kwargs):
            return candidate

        sampler = BestOfNSampler(ollama_url="http://localhost:11434")
        with patch.object(sampler, "_call_ollama", side_effect=_one_candidate):
            result = await sampler.sample(
                messages=self._messages(),
                tools=None,
                model="test-model",
                n=-5,
            )
        assert result.n_generated == 1

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrency(self):
        """Verify that the sampler creates a semaphore with the given limit."""
        sampler = BestOfNSampler(
            ollama_url="http://localhost:11434",
            max_concurrent=2,
        )
        # The semaphore internal value should be 2
        assert sampler._semaphore._value == 2

    @pytest.mark.asyncio
    async def test_ollama_returns_error_candidate_skipped(self):
        """If Ollama returns a 200 with an 'error' key, that candidate is None."""
        call_count = 0

        async def _error_then_ok(messages, tools, model, temperature, candidate_idx):
            nonlocal call_count
            idx = call_count
            call_count += 1
            if idx == 0:
                return None  # simulates error response -> None
            return _Candidate(
                response="good " * 50,
                tool_calls=[],
                tokens_in=50,
                tokens_out=25,
            )

        sampler = BestOfNSampler(ollama_url="http://localhost:11434")
        with patch.object(sampler, "_call_ollama", side_effect=_error_then_ok):
            result = await sampler.sample(
                messages=self._messages(),
                tools=None,
                model="test-model",
                n=2,
            )
        assert result.n_generated == 1

    @pytest.mark.asyncio
    async def test_token_counts_aggregated(self):
        """Token counts should be summed across all candidates."""
        candidates = [
            _Candidate(
                response="response one " * 20,
                tool_calls=[],
                tokens_in=100,
                tokens_out=50,
            ),
            _Candidate(
                response="response two " * 20,
                tool_calls=[],
                tokens_in=200,
                tokens_out=80,
            ),
        ]
        idx = 0

        async def _multi(messages, tools, model, temperature, candidate_idx):
            nonlocal idx
            c = candidates[idx]
            idx += 1
            return c

        sampler = BestOfNSampler(ollama_url="http://localhost:11434")
        with patch.object(sampler, "_call_ollama", side_effect=_multi):
            result = await sampler.sample(
                messages=self._messages(),
                tools=None,
                model="test-model",
                n=2,
            )
        assert result.tokens_in == 300  # 100 + 200
        assert result.tokens_out == 130  # 50 + 80

    @pytest.mark.asyncio
    async def test_duration_ms_is_positive(self):
        candidate = _Candidate(
            response="answer " * 30,
            tool_calls=[],
            tokens_in=50,
            tokens_out=25,
        )

        async def _one(*args, **kwargs):
            return candidate

        sampler = BestOfNSampler(ollama_url="http://localhost:11434")
        with patch.object(sampler, "_call_ollama", side_effect=_one):
            result = await sampler.sample(
                messages=self._messages(),
                tools=None,
                model="test-model",
                n=1,
            )
        assert result.duration_ms >= 0

    @pytest.mark.asyncio
    async def test_url_trailing_slash_stripped(self):
        sampler = BestOfNSampler(ollama_url="http://localhost:11434/")
        assert sampler._ollama_url == "http://localhost:11434"

    @pytest.mark.asyncio
    async def test_exception_in_gather_skips_candidate(self):
        """If _call_ollama raises an unexpected exception, it's caught by gather."""
        call_count = 0

        async def _raise_then_ok(messages, tools, model, temperature, candidate_idx):
            nonlocal call_count
            idx = call_count
            call_count += 1
            if idx == 0:
                raise ValueError("unexpected crash")
            return _Candidate(
                response="survived " * 30,
                tool_calls=[],
                tokens_in=50,
                tokens_out=25,
            )

        sampler = BestOfNSampler(ollama_url="http://localhost:11434")
        with patch.object(sampler, "_call_ollama", side_effect=_raise_then_ok):
            result = await sampler.sample(
                messages=self._messages(),
                tools=None,
                model="test-model",
                n=2,
            )
        assert result.n_generated == 1
        assert "survived" in result.response
