"""Tests for the OpenRouter LLM pipeline."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

from core.game_state import GameState, HeroState, PlayerState
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
