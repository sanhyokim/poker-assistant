"""Tests for the OpenRouter LLM pipeline."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests
import pytest

from core.game_state import ActionRecord, GameState, HeroState, PlayerState
from strategy.llm_pipeline import LLMPipeline


TEST_CONFIG = {
    "llm": {
        "timeout_sec": 15,
        "retry_count": 0,
        "total_timeout_sec": 15,
    },
    "game": {
        "blind_bb": 100,
    },
}


def make_state(pot: int = 200, stack: int = 3000) -> GameState:
    """Create a minimal GameState for LLM tests."""
    return GameState(
        phase="flop",
        hero=HeroState(position="CO", cards=["Ah", "Kh"], stack=stack),
        board=["8c", "7d", "8d"],
        pot=pot,
        active_player_count=2,
    )


def make_pipeline() -> LLMPipeline:
    """Create a pipeline with deterministic environment model names."""
    with patch.dict(
        os.environ,
        {
            "OPENROUTER_API_KEY": "test-key",
            "LLM_MODEL_DEFAULT": "default-model",
            "LLM_MODEL_PREMIUM": "premium-model",
        },
        clear=False,
    ):
        return LLMPipeline(TEST_CONFIG)


def test_init_loads_config() -> None:
    """Pipeline reads timeout, retry count, and blind size from config."""
    pipeline = make_pipeline()

    assert pipeline.timeout_sec == 15
    assert pipeline.total_timeout_sec == 15.0
    assert pipeline.retry_count == 0
    assert pipeline.blind_bb == 100


def test_init_loads_baseline_ranges() -> None:
    """Pipeline loads the baseline ranges JSON file."""
    pipeline = make_pipeline()

    assert pipeline.baseline_ranges["RFI"]["UTG"] == "77+,ATs+,AJo+,KQs,KJs"


def test_load_baseline_ranges_missing_file() -> None:
    """Missing baseline range file returns an empty dictionary."""
    pipeline = make_pipeline()
    with patch("strategy.llm_pipeline.BASELINE_RANGES_PATH", Path("missing.json")):
        assert pipeline._load_baseline_ranges() == {}


def test_get_baseline_range_rfi() -> None:
    """Baseline range lookup returns the configured UTG RFI range."""
    assert make_pipeline().get_baseline_range("UTG", "RFI") == "77+,ATs+,AJo+,KQs,KJs"


def test_get_baseline_range_not_found() -> None:
    """Unknown baseline range keys return an empty string."""
    assert make_pipeline().get_baseline_range("XYZ", "RFI") == ""


def test_validate_range_valid() -> None:
    """Valid range strings pass shallow validation."""
    assert LLMPipeline._validate_range("AA,AKs,QQ-88")


def test_validate_range_empty() -> None:
    """Empty range strings fail validation."""
    assert not LLMPipeline._validate_range("")


def test_validate_range_too_long() -> None:
    """Overly long range strings fail validation."""
    assert not LLMPipeline._validate_range("A" * 1001)


def test_validate_range_invalid_chars() -> None:
    """Range strings with disallowed characters fail validation."""
    assert not LLMPipeline._validate_range("AA; DROP TABLE")


def test_parse_json_response_clean() -> None:
    """Clean JSON response text is parsed directly."""
    assert make_pipeline()._parse_json_response('{"range_oop":"AA"}') == {
        "range_oop": "AA"
    }


def test_parse_json_response_with_markdown() -> None:
    """Markdown-wrapped JSON response text is extracted and parsed."""
    text = '```json\n{"range_oop":"AA"}\n```'

    assert make_pipeline()._parse_json_response(text) == {"range_oop": "AA"}


def test_parse_json_response_invalid() -> None:
    """Invalid response text returns None."""
    assert make_pipeline()._parse_json_response("not json") is None


def test_select_model_normal() -> None:
    """Normal spots use the default model."""
    assert make_pipeline()._select_model(make_state(pot=200, stack=3000)) == "default-model"


def test_select_model_low_spr() -> None:
    """Low SPR spots use the premium model."""
    assert make_pipeline()._select_model(make_state(pot=1000, stack=500)) == "premium-model"


def test_select_model_big_pot() -> None:
    """Pots over 50BB use the premium model."""
    assert make_pipeline()._select_model(make_state(pot=6000, stack=5000)) == "premium-model"


@patch("strategy.llm_pipeline.requests.post")
def test_estimate_ranges_success(mock_post: MagicMock) -> None:
    """Valid API range response is returned with source llm."""
    mock_post.return_value = make_response(
        '{"range_oop":"QQ+,AKs","range_ip":"22+,A2s+",'
        '"adjustments_made":"tight"}'
    )
    result = make_pipeline().estimate_ranges(
        make_state(),
        {"total_hands": 50, "position": "BTN"},
        "AA",
        "KK",
    )

    assert result == {
        "range_oop": "QQ+,AKs",
        "range_ip": "22+,A2s+",
        "adjustments_made": "tight",
        "source": "llm",
    }


def test_estimate_ranges_api_failure() -> None:
    """API failure falls back to baseline ranges."""
    pipeline = make_pipeline()
    with patch.object(pipeline, "_call_api", return_value=None):
        result = pipeline.estimate_ranges(make_state(), None, "AA", "KK")

    assert result["source"] == "baseline_fallback"
    assert result["range_oop"] == "AA"
    assert result["range_ip"] == "KK"


def test_estimate_ranges_invalid_json() -> None:
    """Invalid API text falls back to baseline ranges."""
    pipeline = make_pipeline()
    with patch.object(pipeline, "_call_api", return_value="not json"):
        result = pipeline.estimate_ranges(make_state(), None, "AA", "KK")

    assert result["source"] == "baseline_fallback"


def test_estimate_ranges_invalid_range() -> None:
    """Invalid LLM range strings fall back to baseline ranges."""
    pipeline = make_pipeline()
    with patch.object(
        pipeline,
        "_call_api",
        return_value='{"range_oop":"AA;bad","range_ip":"KK"}',
    ):
        result = pipeline.estimate_ranges(make_state(), None, "AA", "KK")

    assert result["source"] == "baseline_fallback"


def test_suggest_exploit_success() -> None:
    """Valid exploit adjustment API response is returned."""
    pipeline = make_pipeline()
    with patch.object(
        pipeline,
        "_call_api",
        return_value='{"adjusted_action":"bet","adjusted_size":"60%",'
        '"confidence":"medium","reasoning":"Villain overfolds."}',
    ):
        result = pipeline.suggest_exploit(
            {
                "root_strategy": {
                    "actions": ["Check", "Bet 120"],
                    "average_strategy": {"Check": 0.5, "Bet 120": 0.5},
                    "equity": [0.55],
                    "ev": [12.0],
                }
            },
            make_state(),
            {"total_hands": 50, "fold_to_cbet": 70},
        )

    assert result["adjusted_action"] == "bet"
    assert result["adjusted_size"] == "60%"


def test_anonymize_stats_removes_name() -> None:
    """opponent_stats player names are removed for LLM privacy."""
    stats = {"player_name": "JohnDoe", "vpip": 25.0, "pfr": 18.0}

    result = LLMPipeline._anonymize_stats(stats, seat=3)

    assert "player_name" not in result
    assert "JohnDoe" not in str(result)
    assert result["identifier"] == "seat_3"
    assert result["vpip"] == 25.0
    assert stats["player_name"] == "JohnDoe"


def test_anonymize_stats_none_input() -> None:
    """None input returns an empty dictionary."""
    assert LLMPipeline._anonymize_stats(None) == {}


def test_anonymize_stats_no_name_field() -> None:
    """Stats without name fields are copied and tagged with seat identifier."""
    stats = {"vpip": 30.0}

    result = LLMPipeline._anonymize_stats(stats, seat=5)

    assert result["vpip"] == 30.0
    assert result["identifier"] == "seat_5"


def test_anonymize_game_state_for_llm_removes_player_names() -> None:
    """GameState anonymization strips player names from player summaries."""
    state = make_state()
    state.players["2"] = PlayerState(
        name="PrivateName",
        stack=3000,
        bet=100,
        is_seated=True,
    )

    result = LLMPipeline._anonymize_game_state_for_llm(state)

    assert "PrivateName" not in str(result)
    assert "seat_2" in result
    assert "name" not in result["seat_2"]


def test_estimate_ranges_prompt_contains_no_player_name() -> None:
    """Range-estimation prompt does not include player names from stats."""
    pipeline = make_pipeline()
    with patch.object(
        pipeline,
        "_call_api",
        return_value='{"range_oop":"AA","range_ip":"KK","adjustments_made":"tight"}',
    ) as mock_call:
        pipeline.estimate_ranges(
            make_state(),
            {"player_name": "SecretVillain", "total_hands": 50, "position": "BTN"},
            "AA",
            "KK",
        )

    prompt = mock_call.call_args.args[0]
    assert "SecretVillain" not in prompt
    assert "player_name" not in prompt


def test_suggest_exploit_prompt_contains_no_player_name() -> None:
    """Exploit-adjustment prompt does not include player names from stats."""
    pipeline = make_pipeline()
    with patch.object(
        pipeline,
        "_call_api",
        return_value='{"adjusted_action":"check","adjusted_size":null,'
        '"confidence":"low","reasoning":"No change."}',
    ) as mock_call:
        pipeline.suggest_exploit(
            {
                "root_strategy": {
                    "actions": ["Check"],
                    "average_strategy": {"Check": 1.0},
                    "equity": [0.5],
                    "ev": [0.0],
                }
            },
            make_state(),
            {"name": "SecretVillain", "total_hands": 50, "fold_to_cbet": 70},
        )

    prompt = mock_call.call_args.args[0]
    assert "SecretVillain" not in prompt
    assert "name" not in prompt


def test_suggest_exploit_no_stats() -> None:
    """Missing stats returns the low-confidence baseline fallback."""
    result = make_pipeline().suggest_exploit({"root_strategy": {}}, make_state(), None)

    assert result["confidence"] == "low"
    assert result["adjusted_action"] is None


def test_decide_multiway_success() -> None:
    """Valid multiway API response is returned."""
    pipeline = make_pipeline()
    with patch.object(
        pipeline,
        "_call_api",
        return_value='{"action":"check","size":null,"confidence":"medium",'
        '"reasoning":"Pot control."}',
    ):
        result = pipeline.decide_multiway(make_state(), 0.45, [{"vpip": 30}])

    assert result["action"] == "check"
    assert result["confidence"] == "medium"


def test_decide_multiway_prompt_contains_no_player_name() -> None:
    """Multiway prompt anonymizes opponent profile names before API calls."""
    pipeline = make_pipeline()
    with patch.object(
        pipeline,
        "_call_api",
        return_value='{"action":"check","size":null,"confidence":"medium",'
        '"reasoning":"Pot control."}',
    ) as mock_call:
        pipeline.decide_multiway(
            make_state(),
            0.45,
            [{"player": "SecretVillain", "vpip": 30}],
        )

    prompt = mock_call.call_args.args[0]
    assert "SecretVillain" not in prompt
    assert "seat_2" in prompt


def test_decide_multiway_prompt_contains_enriched_context() -> None:
    """Multiway prompt includes effective call, SPR, IP/OOP, and split history."""
    pipeline = make_pipeline()
    state = make_state(pot=13360, stack=5442)
    state.hero.position = "BTN"
    preflop_actions = [
        ActionRecord(seat=5, action="RAISE", amount=2580, confidence="high"),
        ActionRecord(seat=1, action="CALL", amount=2580, confidence="high"),
    ]
    current_actions = [
        ActionRecord(seat=5, action="ALL_IN", amount=42976, confidence="high")
    ]

    with patch.object(
        pipeline,
        "_call_api",
        return_value='{"action":"call","size":5442,"confidence":"medium",'
        '"reasoning":"Effective all-in call."}',
    ) as mock_call:
        pipeline.decide_multiway(
            state,
            0.45,
            [{"vpip": 30}],
            call_amount=5442,
            facing_bet=42976,
            pot_after_call=18802,
            required_equity=5442 / 18802,
            raw_call_amount=42976,
            effective_call_amount=5442,
            hero_call_is_all_in=True,
            spr=5442 / 13360,
            hero_ip_or_oop="likely IP",
            preflop_actions=preflop_actions,
            current_street_actions=current_actions,
        )

    prompt = mock_call.call_args.args[0]
    assert "Hero IP/OOP" in prompt
    assert "likely IP" in prompt
    assert "SPR" in prompt
    assert "0.4" in prompt
    assert "Raw Call Amount: 42976 chips" in prompt
    assert "Effective Call Amount: 5442 chips" in prompt
    assert "Hero Call Is All-In: True" in prompt
    assert "## Preflop Action History" in prompt
    assert "RAISE" in prompt
    assert "CALL" in prompt
    assert "## Current Street Action History" in prompt
    assert "ALL_IN" in prompt


def test_request_preflop_delta_success() -> None:
    """Preflop delta request validates and returns parsed JSON."""
    pipeline = make_pipeline()
    raw = (
        '{"delta_probs":{"raise":0.05,"call":-0.02,"fold":-0.03},'
        '"confidence":0.8,"reason":"Opponent overfolds."}'
    )
    with patch.object(pipeline, "_call_api", return_value=raw):
        result = pipeline.request_preflop_delta(
            {
                "hero_position": "BTN",
                "hero_hand": "AKo",
                "scenario": "RFI",
                "chart_anchor_probs": {"raise": 1.0, "call": 0.0, "fold": 0.0},
                "villain_stats": {"player_name": "SecretVillain", "total_hands": 80},
                "effective_stack_bb": 75.0,
                "action_prefix": [],
            }
        )

    assert result is not None
    assert result["delta_probs"]["raise"] == 0.05


def test_preflop_delta_prompt_contains_no_player_name() -> None:
    """Preflop delta prompt anonymizes opponent stats."""
    pipeline = make_pipeline()

    prompt = pipeline._build_delta_prompt(
        {
            "hero_position": "BTN",
            "hero_hand": "AKo",
            "scenario": "RFI",
            "chart_anchor_probs": {"raise": 1.0, "call": 0.0, "fold": 0.0},
            "villain_stats": {"player_name": "SecretVillain", "total_hands": 80},
            "effective_stack_bb": 75.0,
            "action_prefix": [],
        }
    )

    assert "SecretVillain" not in prompt
    assert "player_name" not in prompt
    assert "total_hands" in prompt


def test_generate_reason_success() -> None:
    """Reason generation returns the first LLM response line."""
    pipeline = make_pipeline()
    with patch.object(pipeline, "_call_api", return_value="強いドローで継続推奨\nextra"):
        assert pipeline.generate_reason("call", "good draw", "AhKh", "8c7d8d") == (
            "強いドローで継続推奨"
        )


def test_generate_reason_api_failure() -> None:
    """Reason generation falls back when the API fails."""
    pipeline = make_pipeline()
    with patch.object(pipeline, "_call_api", return_value=None):
        assert pipeline.generate_reason("bet", "value", "AhKh", "8c7d8d") == "GTO推奨: bet"


def test_call_api_no_key() -> None:
    """Missing API key makes _call_api return None without HTTP calls."""
    with patch.dict(os.environ, {}, clear=True):
        pipeline = LLMPipeline(TEST_CONFIG)

    assert pipeline._call_api("prompt", 10) is None


@patch("strategy.llm_pipeline.requests.post")
def test_call_api_single_failure_returns_none(mock_post: MagicMock) -> None:
    """_call_api returns None immediately on failure without retrying."""
    mock_post.side_effect = requests.RequestException("boom")

    assert make_pipeline()._call_api("prompt", 10) is None
    assert mock_post.call_count == 1


@patch("strategy.llm_pipeline.requests.post")
def test_call_api_disables_reasoning(mock_post: MagicMock) -> None:
    """_call_api sends the OpenRouter reasoning-disable parameter."""
    mock_post.return_value = make_response("done")

    assert make_pipeline()._call_api("prompt", 10) == "done"

    request_body = mock_post.call_args.kwargs["json"]
    assert request_body["reasoning"] == {"effort": "none"}


@patch("strategy.llm_pipeline.requests.post")
def test_call_api_includes_provider_env(mock_post: MagicMock) -> None:
    """OpenRouter provider env values are included in the request payload."""
    mock_post.return_value = make_response("done")
    with patch.dict(
        os.environ,
        {
            "OPENROUTER_API_KEY": "test-key",
            "LLM_MODEL_DEFAULT": "default-model",
            "LLM_MODEL_PREMIUM": "premium-model",
            "OPENROUTER_PROVIDER_ORDER": "OpenAI",
            "OPENROUTER_ALLOW_FALLBACKS": "false",
            "OPENROUTER_REQUIRE_PARAMETERS": "false",
        },
        clear=True,
    ):
        pipeline = LLMPipeline(TEST_CONFIG)
        assert pipeline._call_api("prompt", 10) == "done"

    request_body = mock_post.call_args.kwargs["json"]
    assert request_body["provider"] == {
        "order": ["OpenAI"],
        "allow_fallbacks": False,
        "require_parameters": False,
    }


@patch("strategy.llm_pipeline.requests.post")
def test_call_api_omits_provider_when_env_unset(mock_post: MagicMock) -> None:
    """Provider payload is omitted when provider env values are not set."""
    mock_post.return_value = make_response("done")
    with patch.dict(
        os.environ,
        {
            "OPENROUTER_API_KEY": "test-key",
            "LLM_MODEL_DEFAULT": "default-model",
            "LLM_MODEL_PREMIUM": "premium-model",
        },
        clear=True,
    ):
        pipeline = LLMPipeline(TEST_CONFIG)
        assert pipeline._call_api("prompt", 10) == "done"

    request_body = mock_post.call_args.kwargs["json"]
    assert "provider" not in request_body


@patch("strategy.llm_pipeline.requests.post")
def test_call_api_omits_response_format_when_strict_json_off(
    mock_post: MagicMock,
) -> None:
    """Strict JSON response_format is omitted when the env flag is false."""
    mock_post.return_value = make_response("done")
    with patch.dict(
        os.environ,
        {
            "OPENROUTER_API_KEY": "test-key",
            "LLM_MODEL_DEFAULT": "default-model",
            "LLM_MODEL_PREMIUM": "premium-model",
            "OPENROUTER_USE_STRICT_JSON_SCHEMA": "false",
        },
        clear=True,
    ):
        pipeline = LLMPipeline(TEST_CONFIG)
        assert pipeline._call_api("prompt", 10, task_name="multiway_decision") == "done"

    request_body = mock_post.call_args.kwargs["json"]
    assert "response_format" not in request_body


@patch("strategy.llm_pipeline.requests.post")
def test_call_api_includes_response_format_for_multiway_when_strict_json_on(
    mock_post: MagicMock,
) -> None:
    """Strict JSON response_format is included for supported JSON tasks."""
    mock_post.return_value = make_response("done")
    with patch.dict(
        os.environ,
        {
            "OPENROUTER_API_KEY": "test-key",
            "LLM_MODEL_DEFAULT": "default-model",
            "LLM_MODEL_PREMIUM": "premium-model",
            "OPENROUTER_USE_STRICT_JSON_SCHEMA": "true",
        },
        clear=True,
    ):
        pipeline = LLMPipeline(TEST_CONFIG)
        assert pipeline._call_api("prompt", 10, task_name="multiway_decision") == "done"

    response_format = mock_post.call_args.kwargs["json"]["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    assert response_format["json_schema"]["name"] == "multiway_decision"
    assert "schema" in response_format["json_schema"]


@patch("strategy.llm_pipeline.requests.post")
def test_call_api_omits_response_format_for_reason_generation(
    mock_post: MagicMock,
) -> None:
    """Free-text reason generation does not use strict JSON response_format."""
    mock_post.return_value = make_response("done")
    with patch.dict(
        os.environ,
        {
            "OPENROUTER_API_KEY": "test-key",
            "LLM_MODEL_DEFAULT": "default-model",
            "LLM_MODEL_PREMIUM": "premium-model",
            "OPENROUTER_USE_STRICT_JSON_SCHEMA": "true",
        },
        clear=True,
    ):
        pipeline = LLMPipeline(TEST_CONFIG)
        assert pipeline._call_api("prompt", 10, task_name="reason_generation") == "done"

    request_body = mock_post.call_args.kwargs["json"]
    assert "response_format" not in request_body


@patch("strategy.llm_pipeline.requests.post")
def test_call_api_logs_400_response_body(
    mock_post: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """HTTP 400 responses log the response text and still fall back."""
    mock_post.return_value = make_error_response(400, '{"error":"bad request detail"}')
    pipeline = make_pipeline()

    with caplog.at_level(logging.WARNING):
        result = pipeline._call_api("prompt", 10, task_name="test_task")

    assert result is None
    assert "LLM API error response" in caplog.text
    assert "bad request detail" in caplog.text


@patch("strategy.llm_pipeline.requests.post")
def test_call_api_strict_json_400_returns_none(mock_post: MagicMock) -> None:
    """Strict JSON schema API errors still return None for existing fallback flow."""
    mock_post.return_value = make_error_response(400, '{"error":"schema rejected"}')
    with patch.dict(
        os.environ,
        {
            "OPENROUTER_API_KEY": "test-key",
            "LLM_MODEL_DEFAULT": "default-model",
            "LLM_MODEL_PREMIUM": "premium-model",
            "OPENROUTER_USE_STRICT_JSON_SCHEMA": "true",
        },
        clear=True,
    ):
        pipeline = LLMPipeline(TEST_CONFIG)
        result = pipeline._call_api("prompt", 10, task_name="multiway_decision")

    assert result is None
    assert "response_format" in mock_post.call_args.kwargs["json"]


@patch("strategy.llm_pipeline.requests.post")
def test_call_api_empty_content_returns_none(mock_post: MagicMock) -> None:
    """Empty/None content is treated as an API failure."""
    mock_post.return_value = make_response(None)

    assert make_pipeline()._call_api("prompt", 10) is None


def test_format_opponent_stats_none() -> None:
    """None stats are formatted with prompt-safe defaults."""
    stats = make_pipeline()._format_opponent_stats(None)

    assert stats["vpip"] == "N/A"
    assert stats["total_hands"] == 0
    assert stats["long_term_style"] == "Unknown"


def test_format_opponent_stats_small_sample() -> None:
    """Small samples include a freshness warning."""
    stats = make_pipeline()._format_opponent_stats({"total_hands": 5, "vpip": 40})

    assert stats["vpip"] == 40
    assert "Small sample size" in stats["freshness_warning"]


def test_format_stats_uses_went_to_showdown_as_wtsd() -> None:
    """went_to_showdown from DB rows is exposed as wtsd for prompts."""
    stats = make_pipeline()._format_opponent_stats(
        {"total_hands": 20, "went_to_showdown": 35.5, "vpip": 28.0}
    )

    assert stats["wtsd"] == 35.5


def test_format_stats_fold_to_cbet_default_na() -> None:
    """fold_to_cbet defaults to N/A because it is not persisted yet."""
    stats = make_pipeline()._format_opponent_stats({"total_hands": 20, "vpip": 28.0})

    assert stats["fold_to_cbet"] == "N/A"


class TestLLMLatencyLogging:
    """Tests for LLM latency tracking logs."""

    @patch("strategy.llm_pipeline.requests.post")
    def test_call_api_logs_request_start(self, mock_post: MagicMock, caplog) -> None:
        """_call_api logs request start with task_name, model, prompt_chars, max_tokens."""
        mock_post.return_value = make_response("ok")
        pipeline = make_pipeline()

        with caplog.at_level(logging.INFO):
            pipeline._call_api("test prompt here", max_tokens=100, task_name="test_task")

        assert "LLM request start:" in caplog.text
        assert "task=test_task" in caplog.text
        assert "model=default-model" in caplog.text
        assert "prompt_chars=16" in caplog.text
        assert "max_tokens=100" in caplog.text

    @patch("strategy.llm_pipeline.requests.post")
    def test_call_api_does_not_log_prompt_body(self, mock_post: MagicMock, caplog) -> None:
        """_call_api never logs the prompt text itself."""
        mock_post.return_value = make_response("ok")
        pipeline = make_pipeline()
        secret_prompt = "SECRET_STRATEGY_DETAILS_12345"

        with caplog.at_level(logging.INFO):
            pipeline._call_api(secret_prompt, max_tokens=10, task_name="test_task")

        assert secret_prompt not in caplog.text

    @patch("strategy.llm_pipeline.requests.post")
    def test_call_api_does_not_log_api_key(self, mock_post: MagicMock, caplog) -> None:
        """_call_api never logs the API key."""
        mock_post.return_value = make_response("ok")
        pipeline = make_pipeline()

        with caplog.at_level(logging.INFO):
            pipeline._call_api("prompt", max_tokens=10, task_name="test_task")

        assert "test-key" not in caplog.text

    @patch("strategy.llm_pipeline.requests.post")
    def test_call_api_logs_response_with_elapsed(self, mock_post: MagicMock, caplog) -> None:
        """_call_api logs API response with elapsed_ms and status code."""
        mock_post.return_value = make_response("ok")
        pipeline = make_pipeline()

        with caplog.at_level(logging.INFO):
            pipeline._call_api("prompt", max_tokens=10, task_name="test_task")

        assert "LLM API response:" in caplog.text
        assert "task=test_task" in caplog.text
        assert "model=default-model" in caplog.text
        assert "elapsed_ms=" in caplog.text
        assert "status=200" in caplog.text

    @patch("strategy.llm_pipeline.requests.post")
    def test_call_api_failure_logs_elapsed(self, mock_post: MagicMock, caplog) -> None:
        """_call_api failure includes elapsed_ms in the warning log."""
        mock_post.side_effect = requests.RequestException("boom")
        pipeline = make_pipeline()

        with caplog.at_level(logging.WARNING):
            pipeline._call_api("prompt", max_tokens=10, task_name="test_task")

        assert "LLM API failed:" in caplog.text
        assert "task=test_task" in caplog.text
        assert "elapsed_ms=" in caplog.text
        assert "error=boom" in caplog.text

    @patch("strategy.llm_pipeline.requests.post")
    def test_decide_multiway_logs_task_complete(self, mock_post: MagicMock, caplog) -> None:
        """decide_multiway emits LLM task complete log with timing."""
        mock_post.return_value = make_response(
            '{"action":"check","size":null,"confidence":"medium",'
            '"reasoning":"Pot control."}'
        )
        pipeline = make_pipeline()

        with caplog.at_level(logging.INFO):
            pipeline.decide_multiway(make_state(), 0.45, [{"vpip": 30}])

        assert "LLM task complete:" in caplog.text
        assert "task=multiway_decision" in caplog.text
        assert "total_ms=" in caplog.text
        assert "parsed=" in caplog.text
        assert "validated=" in caplog.text
        assert "fallback=" in caplog.text

    def test_decide_multiway_fallback_behavior_unchanged(self) -> None:
        """decide_multiway fallback still returns safe check when API fails."""
        pipeline = make_pipeline()
        with patch.object(pipeline, "_call_api", return_value=None):
            result = pipeline.decide_multiway(make_state(), 0.45, [])

        assert result["action"] == "check"
        assert result["confidence"] == "low"
        assert "LLM利用不可" in result["reasoning"]

    @patch("strategy.llm_pipeline.requests.post")
    def test_estimate_ranges_logs_task_complete(self, mock_post: MagicMock, caplog) -> None:
        """estimate_ranges emits LLM task complete log."""
        mock_post.return_value = make_response(
            '{"range_oop":"AA","range_ip":"KK","adjustments_made":"none"}'
        )
        pipeline = make_pipeline()

        with caplog.at_level(logging.INFO):
            pipeline.estimate_ranges(make_state(), {"total_hands": 50}, "AA", "KK")

        assert "LLM task complete:" in caplog.text
        assert "task=range_estimation" in caplog.text
        assert "total_ms=" in caplog.text

    @patch("strategy.llm_pipeline.requests.post")
    def test_validation_logs_elapsed(self, mock_post: MagicMock, caplog) -> None:
        """_validate_llm_response logs elapsed_ms on success."""
        mock_post.return_value = make_response(
            '{"action":"check","size":null,"confidence":"medium",'
            '"reasoning":"Pot control."}'
        )
        pipeline = make_pipeline()

        with caplog.at_level(logging.INFO):
            pipeline.decide_multiway(make_state(), 0.45, [{"vpip": 30}])

        assert "LLM validation passed (multiway_decision):" in caplog.text
        assert "elapsed_ms=" in caplog.text


def make_response(content: str | None) -> MagicMock:
    """Build a mocked OpenRouter success response."""
    response = MagicMock()
    response.status_code = 200
    response.text = '{"choices":[]}'
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": content,
                }
            }
        ]
    }
    return response


def make_error_response(status_code: int, text: str) -> MagicMock:
    """Build a mocked OpenRouter error response."""
    response = MagicMock()
    response.status_code = status_code
    response.text = text
    response.raise_for_status.side_effect = requests.HTTPError(text)
    return response
