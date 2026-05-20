"""Tests for the temporary solver request comparison diagnostic script."""

from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

import pytest

from scripts.compare_solver_requests import (
    batch_result_filename,
    build_batch_summary,
    build_grid_probe_request,
    build_grid_summary,
    build_repeatability_summary,
    build_resident_timing_summary,
    build_single_size_request,
    build_single_size_summary,
    build_sizing_teacher_item,
    build_teacher_request,
    build_teacher_summary,
    build_summary,
    build_fast_middle_probe_request,
    build_light_probe_request,
    build_llm_diagnostic_summary,
    build_llm_flop_prompt,
    build_middle_probe_request,
    call_openrouter_llm,
    compare_solver_requests_repeat,
    compare_solver_requests_resident,
    compare_solver_requests_teacher,
    compare_solver_requests_llm,
    compare_solver_requests_grid,
    compare_solver_requests_batch,
    compare_solver_requests,
    discover_primary_request_files,
    extract_action_summary,
    find_compare_request_for_primary,
    grid_profile_id,
    grid_score,
    load_env_file,
    load_solver_request,
    openrouter_provider_config,
    parse_solver_action,
    evaluate_llm_decision,
    parse_llm_decision_json,
    probability_summary,
    repeatability_item_summary,
    resident_item_summary,
    result_filename,
    sample_id,
    teacher_request_config,
)


@pytest.fixture
def workspace_tmp() -> Path:
    """Return a workspace-local temporary directory."""
    path = Path(".test_tmp") / f"compare_solver_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


class FakeBridge:
    """Solver bridge test double that records solve/stop sequencing."""

    responses: list[dict[str, Any]] = []
    events: list[str] = []
    created_count: int = 0
    started_count: int = 0

    def __init__(self) -> None:
        self.index = FakeBridge.created_count
        self.solve_count = 0
        FakeBridge.created_count += 1
        FakeBridge.events.append(f"create:{self.index}")

    def start(self) -> None:
        """Record resident process start."""
        FakeBridge.started_count += 1
        FakeBridge.events.append(f"start:{self.index}")

    def solve(self, request: dict[str, Any], timeout: float = 12.0) -> dict[str, Any]:
        """Return the next configured solver response."""
        FakeBridge.events.append(
            f"solve:{self.index}:{request['name']}:{int(timeout)}"
        )
        response = FakeBridge.responses[self.index + self.solve_count]
        self.solve_count += 1
        return response

    def stop(self) -> None:
        """Record solver process stop."""
        FakeBridge.events.append(f"stop:{self.index}")


class FakeResponse:
    """Minimal requests response test double."""

    def __init__(self, status_code: int, text: str, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self.text = text
        self._payload = payload

    def json(self) -> dict[str, Any]:
        """Return configured JSON payload."""
        return self._payload


@pytest.fixture(autouse=True)
def reset_fake_bridge() -> None:
    """Reset fake bridge class state before each test."""
    FakeBridge.responses = []
    FakeBridge.events = []
    FakeBridge.created_count = 0
    FakeBridge.started_count = 0


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_load_env_file_reads_openrouter_key(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Env loader reads OPENROUTER_API_KEY from a simple .env file."""
    env_path = workspace_tmp / ".env"
    env_path.write_text("OPENROUTER_API_KEY=test-key\n", encoding="utf-8")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    load_env_file(env_path)

    assert os.environ["OPENROUTER_API_KEY"] == "test-key"


def test_load_env_file_does_not_override_existing_env(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Env loader preserves values already present in the environment."""
    env_path = workspace_tmp / ".env"
    env_path.write_text("OPENROUTER_API_KEY=env-file-key\n", encoding="utf-8")
    monkeypatch.setenv("OPENROUTER_API_KEY", "existing-key")

    load_env_file(env_path)

    assert os.environ["OPENROUTER_API_KEY"] == "existing-key"


def test_load_env_file_can_override_existing_env(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Env loader can replace existing values when override is enabled."""
    env_path = workspace_tmp / ".env"
    env_path.write_text("OPENROUTER_API_KEY=new-key\n", encoding="utf-8")
    monkeypatch.setenv("OPENROUTER_API_KEY", "old-key")

    load_env_file(env_path, override=True)

    assert os.environ["OPENROUTER_API_KEY"] == "new-key"


def test_load_env_file_ignores_comments_and_strips_quotes(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Env loader ignores comments and strips surrounding quotes."""
    env_path = workspace_tmp / ".env"
    env_path.write_text(
        "\n".join(
            [
                "# comment",
                "",
                'OPENROUTER_PROVIDER_ORDER="OpenAI"',
                "OPENROUTER_ALLOW_FALLBACKS='false'",
                "not-an-env-line",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENROUTER_PROVIDER_ORDER", raising=False)
    monkeypatch.delenv("OPENROUTER_ALLOW_FALLBACKS", raising=False)

    load_env_file(env_path)

    assert os.environ["OPENROUTER_PROVIDER_ORDER"] == "OpenAI"
    assert os.environ["OPENROUTER_ALLOW_FALLBACKS"] == "false"


def test_openrouter_provider_config_respects_require_parameters_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OpenRouter provider config preserves explicit false parameters."""
    monkeypatch.setenv("OPENROUTER_PROVIDER_ORDER", "OpenAI")
    monkeypatch.setenv("OPENROUTER_ALLOW_FALLBACKS", "false")
    monkeypatch.setenv("OPENROUTER_REQUIRE_PARAMETERS", "false")

    provider = openrouter_provider_config()

    assert provider == {
        "order": ["OpenAI"],
        "allow_fallbacks": False,
        "require_parameters": False,
    }


def test_call_openrouter_llm_adds_schema_only_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Strict JSON schema is attached only when the env flag is true."""
    captured_payloads: list[dict[str, Any]] = []

    def fake_post(*args: Any, **kwargs: Any) -> FakeResponse:
        captured_payloads.append(kwargs["json"])
        return FakeResponse(
            200,
            '{"choices":[]}',
            {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"action":"CHECK","amount":0,'
                                '"sizing_type":"none","confidence":"medium",'
                                '"reason":"ok","risk_flags":[]}'
                            )
                        }
                    }
                ]
            },
        )

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("OPENROUTER_USE_STRICT_JSON_SCHEMA", raising=False)
    monkeypatch.setattr("scripts.compare_solver_requests.requests.post", fake_post)

    call_openrouter_llm("prompt", "openai/gpt-5.4-mini", 30)

    assert "response_format" not in captured_payloads[-1]

    monkeypatch.setenv("OPENROUTER_USE_STRICT_JSON_SCHEMA", "true")
    call_openrouter_llm("prompt", "openai/gpt-5.4-mini", 30)

    assert captured_payloads[-1]["response_format"]["type"] == "json_schema"


def test_call_openrouter_llm_preserves_http_error_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP error details are retained in the diagnostic result."""

    def fake_post(*args: Any, **kwargs: Any) -> FakeResponse:
        return FakeResponse(
            401,
            '{"error":{"message":"No auth credentials found"}}',
            {},
        )

    monkeypatch.setenv("OPENROUTER_API_KEY", "bad-key")
    monkeypatch.setattr("scripts.compare_solver_requests.requests.post", fake_post)

    result = call_openrouter_llm("prompt", "openai/gpt-5.4-mini", 30)

    assert result["success"] is False
    assert result["status_code"] == 401
    assert "No auth credentials found" in result["response_body"]
    assert "HTTP 401" in result["error"]


def test_call_openrouter_llm_requests_post_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful requests.post response returns raw JSON content."""

    def fake_post(*args: Any, **kwargs: Any) -> FakeResponse:
        return FakeResponse(
            200,
            '{"choices":[]}',
            {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"action":"CHECK","amount":0,'
                                '"sizing_type":"none","confidence":"medium",'
                                '"reason":"ok","risk_flags":[]}'
                            )
                        }
                    }
                ]
            },
        )

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr("scripts.compare_solver_requests.requests.post", fake_post)

    result = call_openrouter_llm("prompt", "openai/gpt-5.4-mini", 30)

    assert result["success"] is True
    assert '"action":"CHECK"' in result["raw_content"]
    assert result["diagnostic_elapsed_ms"] is not None


def test_load_solver_request_supports_wrapped_and_raw_payloads(
    workspace_tmp: Path,
) -> None:
    """Request loader accepts both saved meta wrappers and raw request JSON."""
    wrapped = workspace_tmp / "wrapped.json"
    raw = workspace_tmp / "raw.json"
    _write_json(wrapped, {"meta": {"hand_id": 1}, "request": {"name": "wrapped"}})
    _write_json(raw, {"name": "raw"})

    assert load_solver_request(wrapped) == {"name": "wrapped"}
    assert load_solver_request(raw) == {"name": "raw"}


def test_extract_action_summary_prefers_average_strategy() -> None:
    """Average strategy is summarized into selected action and probabilities."""
    action, amount, probabilities = extract_action_summary(
        {
            "success": True,
            "node_strategy": {
                "average_strategy": {
                    "CALL": 0.25,
                    "RAISE 324": 0.75,
                }
            },
        }
    )

    assert action == "RAISE"
    assert amount == 324
    assert probabilities == {"CALL": 0.25, "RAISE 324": 0.75}


def test_parse_solver_action_handles_all_in_label() -> None:
    """Action parser preserves ALL_IN and extracts chip amount."""
    assert parse_solver_action("ALL-IN 2934") == ("ALL_IN", 2934)


def test_probability_summary_extracts_top_second_and_margin() -> None:
    """Probability summary reports top action, runner-up, and margin."""
    summary = probability_summary({"CHECK": 0.635, "BET 120": 0.365})

    assert summary == {
        "top_action": "CHECK",
        "top_probability": 0.635,
        "second_action": "BET",
        "second_probability": 0.365,
        "top_margin": 0.27,
    }


def test_compare_solver_requests_stops_primary_before_compare(
    workspace_tmp: Path,
) -> None:
    """All request variants run in clean separate processes."""
    primary_path = workspace_tmp / "hand_000005_req_000004_flop.json"
    compare_path = workspace_tmp / "hand_000005_req_000005_flop_compare_no_allin.json"
    out_dir = workspace_tmp / "out"
    _write_json(primary_path, {"request": {"name": "primary", "timeout_ms": 20000}})
    _write_json(compare_path, {"request": {"name": "compare"}})
    FakeBridge.responses = [
        {
            "success": True,
            "node_strategy": {"average_strategy": {"CALL": 0.8, "FOLD": 0.2}},
        },
        {
            "success": True,
            "node_strategy": {"average_strategy": {"CALL": 0.9, "FOLD": 0.1}},
        },
        {
            "success": True,
            "node_strategy": {"average_strategy": {"CALL": 0.7, "FOLD": 0.3}},
        },
        {
            "success": True,
            "node_strategy": {"average_strategy": {"CALL": 0.6, "FOLD": 0.4}},
        },
        {
            "success": True,
            "node_strategy": {"average_strategy": {"CALL": 0.55, "FOLD": 0.45}},
        },
    ]

    result = compare_solver_requests(
        primary_path,
        compare_path,
        timeout=30,
        out_dir=out_dir,
        bridge_factory=FakeBridge,
    )

    assert FakeBridge.events == [
        "create:0",
        "solve:0:primary:30",
        "stop:0",
        "create:1",
        "solve:1:compare:30",
        "stop:1",
        "create:2",
        "solve:2:primary:30",
        "stop:2",
        "create:3",
        "solve:3:primary:30",
        "stop:3",
        "create:4",
        "solve:4:primary:30",
        "stop:4",
    ]
    assert result["primary"]["action"] == "CALL"
    assert result["compare"]["action"] == "CALL"
    assert result["light"]["action"] == "CALL"
    assert result["middle"]["action"] == "CALL"
    assert result["fast_middle"]["action"] == "CALL"
    assert result["light"]["path"] == f"generated_light_probe_from:{primary_path}"
    assert result["middle"]["path"] == f"generated_middle_probe_from:{primary_path}"
    assert result["fast_middle"]["path"] == (
        f"generated_fast_middle_probe_from:{primary_path}"
    )
    assert result["summary"]["compare_action_match"] is True
    assert result["summary"]["compare_amount_match"] is True
    assert result["summary"]["light_action_match"] is True
    assert result["summary"]["light_amount_match"] is True
    assert result["summary"]["middle_action_match"] is True
    assert result["summary"]["middle_amount_match"] is True
    assert result["summary"]["fast_middle_action_match"] is True
    assert result["summary"]["fast_middle_amount_match"] is True
    assert result["summary"]["fast_middle_under_15s"] is True

    output_path = Path(result["output_path"])
    assert output_path == out_dir / "hand_000005_flop_compare_result.json"
    assert output_path.exists()
    saved = json.loads(output_path.read_text(encoding="utf-8"))
    assert saved["primary"]["path"] == str(primary_path)
    assert saved["compare"]["path"] == str(compare_path)
    assert saved["light"]["path"] == f"generated_light_probe_from:{primary_path}"
    assert saved["middle"]["path"] == f"generated_middle_probe_from:{primary_path}"
    assert saved["fast_middle"]["path"] == (
        f"generated_fast_middle_probe_from:{primary_path}"
    )


def test_batch_mode_discovers_primary_files_and_skips_variants(
    workspace_tmp: Path,
) -> None:
    """Batch discovery includes primary requests but excludes variants."""
    primary = workspace_tmp / "hand_000016_req_000009_flop.json"
    second_primary = workspace_tmp / "hand_000016_req_000011_flop.json"
    compare = workspace_tmp / "hand_000016_req_000010_flop_compare_no_allin.json"
    light = workspace_tmp / "hand_000016_req_000009_flop_light_probe.json"
    turn = workspace_tmp / "hand_000016_req_000012_turn.json"
    for path in (primary, second_primary, compare, light, turn):
        _write_json(path, {"request": {"name": path.stem}})

    discovered = discover_primary_request_files(workspace_tmp, "flop")

    assert discovered == [primary, second_primary]
    assert find_compare_request_for_primary(primary, workspace_tmp) == compare
    assert sample_id(primary) == "hand_000016_req_000009_flop"
    assert batch_result_filename(primary) == (
        "hand_000016_req_000009_flop_compare_result.json"
    )


def test_compare_solver_requests_batch_writes_items_and_summary(
    workspace_tmp: Path,
) -> None:
    """Batch mode runs each primary/compare pair and aggregates profiles."""
    batch_dir = workspace_tmp / "requests"
    out_dir = workspace_tmp / "batch_out"
    batch_dir.mkdir()
    primary = batch_dir / "hand_000016_req_000009_flop.json"
    compare = batch_dir / "hand_000016_req_000010_flop_compare_no_allin.json"
    skipped = batch_dir / "hand_000017_req_000001_flop.json"
    _write_json(primary, {"request": {"name": "primary"}})
    _write_json(compare, {"request": {"name": "compare"}})
    _write_json(skipped, {"request": {"name": "skipped"}})
    FakeBridge.responses = [
        {"success": True, "probabilities": {"CHECK": 1.0}},
        {"success": True, "probabilities": {"CHECK": 1.0}},
        {"success": True, "probabilities": {"BET 120": 1.0}},
        {"success": True, "probabilities": {"CHECK": 1.0}},
        {"success": False, "error": "timeout"},
    ]

    summary = compare_solver_requests_batch(
        batch_dir,
        phase="flop",
        timeout=30,
        out_dir=out_dir,
        bridge_factory=FakeBridge,
    )

    assert summary["total_primary_files"] == 2
    assert summary["compared"] == 1
    assert summary["skipped_missing_compare"] == 1
    assert summary["profiles"]["primary"]["success_count"] == 1
    assert summary["profiles"]["light"]["action_match_count"] == 0
    assert summary["profiles"]["light"]["check_to_bet_count"] == 1
    assert summary["profiles"]["fast_middle"]["error_count"] == 1
    item_path = (
        out_dir
        / "items"
        / "hand_000016_req_000009_flop_compare_result.json"
    )
    assert item_path.exists()
    summary_path = out_dir / "batch_summary.json"
    assert summary_path.exists()
    saved_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert saved_summary["items"][0]["sample_id"] == "hand_000016_req_000009_flop"
    saved_item = saved_summary["items"][0]
    assert saved_item["primary_top_action"] == "CHECK"
    assert saved_item["primary_top_probability"] == 1.0
    assert saved_item["primary_top_margin"] is None
    assert saved_item["light_clear_check_to_bet"] is False


def test_compare_solver_requests_uses_light_file_when_provided(
    workspace_tmp: Path,
) -> None:
    """Explicit --light-style path is used instead of generating a light request."""
    primary_path = workspace_tmp / "hand_000006_req_000007_flop.json"
    compare_path = workspace_tmp / "hand_000006_req_000008_flop_compare_no_allin.json"
    light_path = workspace_tmp / "hand_000006_req_000007_flop_light_probe.json"
    _write_json(primary_path, {"request": {"name": "primary"}})
    _write_json(compare_path, {"request": {"name": "compare"}})
    _write_json(light_path, {"request": {"name": "light_file"}})
    FakeBridge.responses = [
        {"success": True, "probabilities": {"CHECK": 1.0}},
        {"success": True, "probabilities": {"CHECK": 1.0}},
        {"success": True, "probabilities": {"BET 120": 1.0}},
        {"success": True, "probabilities": {"CHECK": 1.0}},
        {"success": True, "probabilities": {"CHECK": 1.0}},
    ]

    result = compare_solver_requests(
        primary_path,
        compare_path,
        light_path=light_path,
        timeout=30,
        out_dir=workspace_tmp / "out",
        bridge_factory=FakeBridge,
    )

    assert FakeBridge.events[7] == "solve:2:light_file:30"
    assert result["light"]["path"] == str(light_path)
    assert result["summary"]["light_action_match"] is False


def test_compare_solver_requests_uses_middle_file_when_provided(
    workspace_tmp: Path,
) -> None:
    """Explicit --middle-style path is used instead of generating a request."""
    primary_path = workspace_tmp / "hand_000006_req_000007_flop.json"
    compare_path = workspace_tmp / "hand_000006_req_000008_flop_compare_no_allin.json"
    middle_path = workspace_tmp / "hand_000006_req_000007_flop_middle_probe.json"
    _write_json(primary_path, {"request": {"name": "primary"}})
    _write_json(compare_path, {"request": {"name": "compare"}})
    _write_json(middle_path, {"request": {"name": "middle_file"}})
    FakeBridge.responses = [
        {"success": True, "probabilities": {"CHECK": 1.0}},
        {"success": True, "probabilities": {"CHECK": 1.0}},
        {"success": True, "probabilities": {"CHECK": 1.0}},
        {"success": True, "probabilities": {"BET 120": 1.0}},
        {"success": True, "probabilities": {"CHECK": 1.0}},
    ]

    result = compare_solver_requests(
        primary_path,
        compare_path,
        middle_path=middle_path,
        timeout=30,
        out_dir=workspace_tmp / "out",
        bridge_factory=FakeBridge,
    )

    assert FakeBridge.events[10] == "solve:3:middle_file:30"
    assert result["middle"]["path"] == str(middle_path)
    assert result["summary"]["middle_action_match"] is False


def test_compare_solver_requests_uses_fast_middle_file_when_provided(
    workspace_tmp: Path,
) -> None:
    """Explicit --fast-middle path is used instead of generating a request."""
    primary_path = workspace_tmp / "hand_000006_req_000007_flop.json"
    compare_path = workspace_tmp / "hand_000006_req_000008_flop_compare_no_allin.json"
    fast_middle_path = (
        workspace_tmp / "hand_000006_req_000007_flop_fast_middle_probe.json"
    )
    _write_json(primary_path, {"request": {"name": "primary"}})
    _write_json(compare_path, {"request": {"name": "compare"}})
    _write_json(fast_middle_path, {"request": {"name": "fast_middle_file"}})
    FakeBridge.responses = [
        {"success": True, "probabilities": {"CHECK": 1.0}},
        {"success": True, "probabilities": {"CHECK": 1.0}},
        {"success": True, "probabilities": {"CHECK": 1.0}},
        {"success": True, "probabilities": {"CHECK": 1.0}},
        {"success": True, "probabilities": {"BET 120": 1.0}},
    ]

    result = compare_solver_requests(
        primary_path,
        compare_path,
        fast_middle_path=fast_middle_path,
        timeout=30,
        out_dir=workspace_tmp / "out",
        bridge_factory=FakeBridge,
    )

    assert FakeBridge.events[13] == "solve:4:fast_middle_file:30"
    assert result["fast_middle"]["path"] == str(fast_middle_path)
    assert result["summary"]["fast_middle_action_match"] is False


def test_build_light_probe_request_overrides_solver_light_fields() -> None:
    """Generated light request applies deep_spr_light_probe-style settings."""
    primary = {
        "timeout_ms": 20000,
        "max_iterations": 300,
        "target_exploitability_pct": 0.5,
        "flop_bet_sizes_oop": "60%,a",
        "flop_bet_sizes_ip": "60%,a",
        "flop_raise_sizes_oop": "3x",
        "flop_raise_sizes_ip": "3x",
        "turn_bet_sizes_oop": "60%,a",
        "turn_bet_sizes_ip": "60%,a",
        "turn_raise_sizes_oop": "3x",
        "turn_raise_sizes_ip": "3x",
        "river_bet_sizes_oop": "60%,a",
    }

    light = build_light_probe_request(primary)

    assert light["timeout_ms"] == 5000
    assert light["max_iterations"] == 80
    assert light["target_exploitability_pct"] == 1.5
    assert light["flop_bet_sizes_oop"] == "50%"
    assert light["flop_bet_sizes_ip"] == "50%"
    assert light["flop_raise_sizes_oop"] == "2.5x"
    assert light["flop_raise_sizes_ip"] == "2.5x"
    assert light["turn_bet_sizes_oop"] == "50%"
    assert light["turn_bet_sizes_ip"] == "50%"
    assert light["turn_raise_sizes_oop"] == "2.5x"
    assert light["turn_raise_sizes_ip"] == "2.5x"
    assert light["river_bet_sizes_oop"] == "60%,a"
    assert primary["timeout_ms"] == 20000


def test_build_middle_probe_request_overrides_solver_middle_fields() -> None:
    """Generated middle request applies intermediate diagnostic settings."""
    primary = {
        "timeout_ms": 20000,
        "max_iterations": 300,
        "target_exploitability_pct": 0.6,
        "flop_bet_sizes_oop": "60%,a",
        "flop_bet_sizes_ip": "60%,a",
        "flop_raise_sizes_oop": "3x",
        "flop_raise_sizes_ip": "3x",
        "turn_bet_sizes_oop": "60%,a",
        "turn_bet_sizes_ip": "60%,a",
        "turn_raise_sizes_oop": "3x",
        "turn_raise_sizes_ip": "3x",
        "river_bet_sizes_oop": "60%,a",
    }

    middle = build_middle_probe_request(primary)

    assert middle["timeout_ms"] == 12000
    assert middle["max_iterations"] == 150
    assert middle["target_exploitability_pct"] == 1.0
    assert middle["flop_bet_sizes_oop"] == "60%"
    assert middle["flop_bet_sizes_ip"] == "60%"
    assert middle["flop_raise_sizes_oop"] == "2.5x"
    assert middle["flop_raise_sizes_ip"] == "2.5x"
    assert middle["turn_bet_sizes_oop"] == "60%"
    assert middle["turn_bet_sizes_ip"] == "60%"
    assert middle["turn_raise_sizes_oop"] == "2.5x"
    assert middle["turn_raise_sizes_ip"] == "2.5x"
    assert middle["river_bet_sizes_oop"] == "60%,a"
    assert primary["max_iterations"] == 300


def test_build_fast_middle_probe_request_overrides_solver_fields() -> None:
    """Generated fast-middle request applies the 15-second diagnostic profile."""
    primary = {
        "timeout_ms": 20000,
        "max_iterations": 300,
        "target_exploitability_pct": 0.6,
        "flop_bet_sizes_oop": "60%,a",
        "flop_bet_sizes_ip": "60%,a",
        "flop_raise_sizes_oop": "3x",
        "flop_raise_sizes_ip": "3x",
        "turn_bet_sizes_oop": "60%,a",
        "turn_bet_sizes_ip": "60%,a",
        "turn_raise_sizes_oop": "3x",
        "turn_raise_sizes_ip": "3x",
        "river_bet_sizes_oop": "60%,a",
    }

    fast_middle = build_fast_middle_probe_request(primary)

    assert fast_middle["timeout_ms"] == 15000
    assert fast_middle["max_iterations"] == 120
    assert fast_middle["target_exploitability_pct"] == 1.2
    assert fast_middle["flop_bet_sizes_oop"] == "60%"
    assert fast_middle["flop_bet_sizes_ip"] == "60%"
    assert fast_middle["flop_raise_sizes_oop"] == "2.5x"
    assert fast_middle["flop_raise_sizes_ip"] == "2.5x"
    assert fast_middle["turn_bet_sizes_oop"] == "60%"
    assert fast_middle["turn_bet_sizes_ip"] == "60%"
    assert fast_middle["turn_raise_sizes_oop"] == "2.5x"
    assert fast_middle["turn_raise_sizes_ip"] == "2.5x"
    assert fast_middle["river_bet_sizes_oop"] == "60%,a"
    assert primary["max_iterations"] == 300


def test_build_grid_probe_request_overrides_candidate_fields() -> None:
    """Generated grid request updates only the diagnostic search dimensions."""
    primary = {
        "timeout_ms": 20000,
        "max_iterations": 300,
        "target_exploitability_pct": 0.6,
        "flop_bet_sizes_oop": "60%,a",
        "flop_bet_sizes_ip": "60%,a",
        "turn_bet_sizes_oop": "60%,a",
        "turn_bet_sizes_ip": "60%,a",
    }

    grid = build_grid_probe_request(
        primary,
        max_iterations=180,
        target_exploitability_pct=0.9,
        bet_sizes="50%,60%",
    )

    assert grid["timeout_ms"] == 20000
    assert grid["max_iterations"] == 180
    assert grid["target_exploitability_pct"] == 0.9
    assert grid["flop_bet_sizes_oop"] == "50%,60%"
    assert grid["flop_bet_sizes_ip"] == "50%,60%"
    assert grid["turn_bet_sizes_oop"] == "50%,60%"
    assert grid["turn_bet_sizes_ip"] == "50%,60%"
    assert primary["max_iterations"] == 300
    assert grid_profile_id(
        max_iterations=180,
        target_exploitability_pct=0.9,
        bet_sizes="60%,a",
    ) == "iter180_target0_9_bets60_allin"


def test_build_single_size_request_applies_33_profile() -> None:
    """Single-size 33 profile applies one flop bet size and primary precision."""
    primary = {
        "timeout_ms": 20000,
        "max_iterations": 150,
        "target_exploitability_pct": 1.0,
        "flop_bet_sizes_oop": "60%,a",
        "flop_bet_sizes_ip": "60%,a",
        "flop_raise_sizes_oop": "3x",
        "flop_raise_sizes_ip": "3x",
        "turn_bet_sizes_oop": "60%,a",
    }

    request = build_single_size_request(primary, "single_33")

    assert request["flop_bet_sizes_oop"] == "33%"
    assert request["flop_bet_sizes_ip"] == "33%"
    assert request["flop_raise_sizes_oop"] == "2.5x"
    assert request["flop_raise_sizes_ip"] == "2.5x"
    assert request["max_iterations"] == 300
    assert request["target_exploitability_pct"] == 0.6
    assert request["timeout_ms"] == 30000
    assert request["turn_bet_sizes_oop"] == "60%,a"
    assert primary["flop_bet_sizes_oop"] == "60%,a"


def test_build_single_size_request_applies_allin_profile() -> None:
    """Single-size all-in profile applies all-in bet and raise sizes."""
    primary = {
        "flop_bet_sizes_oop": "60%,a",
        "flop_bet_sizes_ip": "60%,a",
        "flop_raise_sizes_oop": "3x",
        "flop_raise_sizes_ip": "3x",
    }

    request = build_single_size_request(primary, "single_allin")

    assert request["flop_bet_sizes_oop"] == "a"
    assert request["flop_bet_sizes_ip"] == "a"
    assert request["flop_raise_sizes_oop"] == "a"
    assert request["flop_raise_sizes_ip"] == "a"


def test_single_size_summary_counts_aggressive_actions() -> None:
    """Single-size summary counts BET/RAISE/ALL_IN as aggressive actions."""
    summary = build_single_size_summary(
        [
            {
                "sample_id": "sample_a",
                "profiles": [
                    {
                        "profile_id": "single_33",
                        "success": True,
                        "elapsed_ms": 12000,
                        "under_15s": True,
                        "action": "BET",
                        "aggressive_action": True,
                    },
                    {
                        "profile_id": "single_50",
                        "success": True,
                        "elapsed_ms": 16000,
                        "under_15s": False,
                        "action": "CHECK",
                        "aggressive_action": False,
                    },
                    {
                        "profile_id": "single_75",
                        "success": True,
                        "elapsed_ms": 14000,
                        "under_15s": True,
                        "action": "RAISE",
                        "aggressive_action": True,
                    },
                    {
                        "profile_id": "single_allin",
                        "success": True,
                        "elapsed_ms": 11000,
                        "under_15s": True,
                        "action": "CHECK",
                        "aggressive_action": False,
                    },
                ],
            }
        ]
    )

    assert summary["planned_runs"] == 4
    assert summary["success_count"] == 4
    assert summary["profile_summary"]["single_33"]["aggressive_action_count"] == 1
    assert summary["profile_summary"]["single_50"]["aggressive_action_count"] == 0
    assert summary["profile_summary"]["single_75"]["aggressive_action_count"] == 1
    assert summary["profile_summary"]["single_allin"]["aggressive_action_count"] == 0
    assert summary["sample_summary"][0]["aggressive_profiles"] == [
        "single_33",
        "single_75",
    ]
    assert summary["sample_summary"][0]["passive_profiles"] == [
        "single_50",
        "single_allin",
    ]
    assert summary["sample_summary"][0]["all_profiles_same_direction"] is False


def _single_size_item_from_actions(actions: dict[str, str]) -> dict[str, Any]:
    profiles = [
        {
            "profile_id": profile_id,
            "action": action,
            "aggressive_action": action in {"BET", "RAISE", "ALL_IN"},
        }
        for profile_id, action in actions.items()
    ]
    return {"sample_id": "sample_a", "profiles": profiles}


def test_build_sizing_teacher_small_only() -> None:
    """Sizing teacher labels 33/50 aggressive and 60/75 passive as small."""
    item = build_sizing_teacher_item(
        _single_size_item_from_actions(
            {
                "single_33": "BET",
                "single_50": "BET",
                "single_60": "CHECK",
                "single_75": "CHECK",
                "single_allin": "CHECK",
            }
        )
    )

    assert item["teacher_label"] == "small_only_aggressive"
    assert item["preferred_sizing_bucket"] == "small"
    assert item["allowed_sizing_types"] == ["bet_33", "bet_50"]
    assert item["allin_aggressive"] is False


def test_build_sizing_teacher_passive_all_standard() -> None:
    """Sizing teacher labels all passive standard sizes as no sizing."""
    item = build_sizing_teacher_item(
        _single_size_item_from_actions(
            {
                "single_33": "CHECK",
                "single_50": "CHECK",
                "single_60": "CHECK",
                "single_75": "CHECK",
                "single_allin": "CHECK",
            }
        )
    )

    assert item["teacher_label"] == "passive_all_standard"
    assert item["preferred_sizing_bucket"] == "none"
    assert item["allowed_sizing_types"] == []


def test_build_sizing_teacher_mixed_non_monotonic() -> None:
    """Sizing teacher keeps non-monotonic aggressive sizes as mixed."""
    item = build_sizing_teacher_item(
        _single_size_item_from_actions(
            {
                "single_33": "BET",
                "single_50": "CHECK",
                "single_60": "CHECK",
                "single_75": "BET",
                "single_allin": "CHECK",
            }
        )
    )

    assert item["teacher_label"] == "mixed_non_monotonic"
    assert item["preferred_sizing_bucket"] == "mixed"
    assert item["allowed_sizing_types"] == ["bet_33", "bet_75"]


def test_build_sizing_teacher_allin_aggressive() -> None:
    """Sizing teacher can include all-in when the all-in profile is aggressive."""
    item = build_sizing_teacher_item(
        _single_size_item_from_actions(
            {
                "single_33": "CHECK",
                "single_50": "CHECK",
                "single_60": "CHECK",
                "single_75": "CHECK",
                "single_allin": "ALL_IN",
            }
        )
    )

    assert item["teacher_label"] == "passive_all_standard"
    assert item["allin_aggressive"] is True
    assert item["allowed_sizing_types"] == ["all_in"]


def test_build_teacher_request_applies_standard_and_high_profiles() -> None:
    """Teacher request profiles increase iterations and candidate sizes."""
    primary = {
        "max_iterations": 300,
        "target_exploitability_pct": 0.6,
        "timeout_ms": 20000,
        "flop_bet_sizes_oop": "60%,a",
        "flop_raise_sizes_oop": "3x",
        "river_bet_sizes_ip": "60%,a",
    }

    standard = build_teacher_request(primary, "standard")
    high = build_teacher_request(primary, "high")

    assert standard["max_iterations"] == 500
    assert standard["target_exploitability_pct"] == 0.4
    assert standard["timeout_ms"] == 90000
    assert standard["flop_bet_sizes_oop"] == "33%,50%,60%,75%,a"
    assert standard["river_raise_sizes_ip"] == "2.5x"
    assert high["max_iterations"] == 800
    assert high["target_exploitability_pct"] == 0.3
    assert high["timeout_ms"] == 120000
    assert high["flop_bet_sizes_oop"] == "25%,33%,50%,60%,75%,a"
    assert high["turn_raise_sizes_oop"] == "2.5x,3.5x"
    assert teacher_request_config("standard")["bet_sizes"] == "33%,50%,60%,75%,a"
    assert primary["max_iterations"] == 300


def test_build_teacher_request_applies_narrow_profile() -> None:
    """Narrow teacher profile keeps primary bet sizes while increasing precision."""
    primary = {
        "max_iterations": 300,
        "target_exploitability_pct": 0.6,
        "timeout_ms": 20000,
        "flop_bet_sizes_oop": "60%,a",
        "flop_bet_sizes_ip": "60%,a",
        "flop_raise_sizes_oop": "3x",
        "flop_raise_sizes_ip": "3x",
        "turn_bet_sizes_oop": "60%,a",
        "turn_bet_sizes_ip": "60%,a",
        "turn_raise_sizes_oop": "3x",
        "turn_raise_sizes_ip": "3x",
    }

    narrow = build_teacher_request(primary, "narrow")

    assert narrow["max_iterations"] == 500
    assert narrow["target_exploitability_pct"] == 0.4
    assert narrow["timeout_ms"] == 180000
    assert narrow["flop_bet_sizes_oop"] == "60%,a"
    assert narrow["flop_bet_sizes_ip"] == "60%,a"
    assert narrow["flop_raise_sizes_oop"] == "2.5x"
    assert narrow["flop_raise_sizes_ip"] == "2.5x"
    assert narrow["turn_bet_sizes_oop"] == "60%,a"
    assert narrow["turn_bet_sizes_ip"] == "60%,a"
    assert narrow["turn_raise_sizes_oop"] == "2.5x"
    assert narrow["turn_raise_sizes_ip"] == "2.5x"
    assert teacher_request_config("narrow")["bet_sizes"] == "60%,a"
    assert primary["max_iterations"] == 300
    assert primary["timeout_ms"] == 20000


def test_build_teacher_request_applies_teacher_300_plus_profile() -> None:
    """Teacher 300 plus keeps primary precision while expanding bet sizes."""
    primary = {
        "max_iterations": 300,
        "target_exploitability_pct": 0.6,
        "timeout_ms": 20000,
        "flop_bet_sizes_oop": "60%,a",
        "flop_bet_sizes_ip": "60%,a",
        "flop_raise_sizes_oop": "3x",
        "flop_raise_sizes_ip": "3x",
        "turn_bet_sizes_oop": "60%,a",
        "turn_bet_sizes_ip": "60%,a",
        "turn_raise_sizes_oop": "3x",
        "turn_raise_sizes_ip": "3x",
    }

    teacher = build_teacher_request(primary, "teacher_300_plus")

    assert teacher["max_iterations"] == 300
    assert teacher["target_exploitability_pct"] == 0.6
    assert teacher["timeout_ms"] == 180000
    assert teacher["flop_bet_sizes_oop"] == "50%,60%,75%,a"
    assert teacher["flop_bet_sizes_ip"] == "50%,60%,75%,a"
    assert teacher["flop_raise_sizes_oop"] == "2.5x"
    assert teacher["flop_raise_sizes_ip"] == "2.5x"
    assert teacher["turn_bet_sizes_oop"] == "50%,60%,75%,a"
    assert teacher["turn_bet_sizes_ip"] == "50%,60%,75%,a"
    assert teacher["turn_raise_sizes_oop"] == "2.5x"
    assert teacher["turn_raise_sizes_ip"] == "2.5x"
    assert teacher_request_config("teacher_300_plus")["bet_sizes"] == (
        "50%,60%,75%,a"
    )
    assert primary["flop_bet_sizes_oop"] == "60%,a"
    assert primary["timeout_ms"] == 20000


def test_compare_solver_requests_teacher_writes_items_and_summary(
    workspace_tmp: Path,
) -> None:
    """Teacher mode writes high-precision result items and summary."""
    request_path = workspace_tmp / "hand_000004_req_000004_flop.json"
    out_dir = workspace_tmp / "teacher_out"
    _write_json(request_path, {"request": {"name": "teacher"}})
    FakeBridge.responses = [
        {
            "success": True,
            "diagnostic_elapsed_ms": 84000,
            "probabilities": {"CHECK": 0.72, "BET 120": 0.28},
        }
    ]

    summary = compare_solver_requests_teacher(
        teacher_path=request_path,
        teacher_dir=None,
        sample_ids=None,
        teacher_profile="standard",
        timeout=120,
        out_dir=out_dir,
        bridge_factory=FakeBridge,
    )

    assert summary["total_samples"] == 1
    assert summary["success_count"] == 1
    assert summary["avg_elapsed_ms"] == 84000
    item_path = (
        out_dir
        / "items"
        / "hand_000004_req_000004_flop_teacher_standard.json"
    )
    assert item_path.exists()
    item = json.loads(item_path.read_text(encoding="utf-8"))
    assert item["teacher_profile"] == "standard"
    assert item["request_config"]["max_iterations"] == 500
    assert item["top_action"] == "CHECK"
    assert item["top_margin"] == 0.44


def test_build_llm_flop_prompt_contains_primary_anchor() -> None:
    """LLM diagnostic prompt anchors decisions to the primary Solver result."""
    payload = {
        "meta": {"spr": 38.2, "hero_position": "BTN", "hero_is_ip": True},
        "request": {
            "board": "5h4h9h",
            "starting_pot": 232,
            "effective_stack": 8883,
            "actions_played": [],
        },
    }
    baseline = {
        "action": "CHECK",
        "amount": 0,
        "probabilities": {"CHECK": 0.63, "BET 139": 0.37},
    }

    prompt = build_llm_flop_prompt(payload, baseline, ["CHECK", "BET", "ALL_IN"])

    assert "Primary Solver is the anchor" in prompt
    assert "primary_solver_action" in prompt
    assert "CHECK" in prompt
    assert "BET_60" in prompt


def test_parse_llm_decision_json_valid() -> None:
    """LLM diagnostic JSON parser normalizes action and risk flags."""
    decision = parse_llm_decision_json(
        json.dumps(
            {
                "action": "all-in",
                "amount": 1200,
                "sizing_type": "all_in",
                "confidence": "medium",
                "reason": "Primary solver is close enough to support pressure.",
                "risk_flags": ["near_tie"],
            }
        )
    )

    assert decision["action"] == "ALL_IN"
    assert decision["amount"] == 1200
    assert decision["risk_flags"] == ["near_tie"]


def test_llm_diagnostic_summary_counts_dangerous_flip() -> None:
    """LLM diagnostic summary counts dangerous action flips."""
    summary = build_llm_diagnostic_summary(
        [
            {
                "sample_id": "hand_000001_req_000001_flop",
                "baseline_action": "CHECK",
                "llm_action": "BET",
                "llm_success": True,
                "llm_elapsed_ms": 2000,
                "action_match": False,
                "direction_match": False,
                "dangerous_flip": True,
                "clear_check_to_bet": True,
                "legal_action_valid": True,
                "llm_error": None,
                "under_15s": True,
            }
        ],
        "openai/gpt-5.4-mini",
    )

    assert summary["success_count"] == 1
    assert summary["dangerous_flip_count"] == 1
    assert summary["clear_check_to_bet_count"] == 1
    assert summary["legal_action_invalid_count"] == 0
    assert summary["under_15s_rate"] == 1.0


def test_compare_solver_requests_llm_writes_items_and_summary(
    workspace_tmp: Path,
) -> None:
    """LLM diagnostic mode writes baseline, LLM decision, and summary files."""
    request_path = workspace_tmp / "hand_000004_req_000004_flop.json"
    out_dir = workspace_tmp / "llm_out"
    _write_json(
        request_path,
        {
            "meta": {"spr": 38.2, "hero_position": "BTN"},
            "request": {
                "name": "baseline",
                "board": "5h4h9h",
                "starting_pot": 232,
                "effective_stack": 8883,
                "actions_played": [],
            },
        },
    )
    FakeBridge.responses = [
        {
            "success": True,
            "diagnostic_elapsed_ms": 21000,
            "probabilities": {"CHECK": 0.72, "BET 139": 0.28},
        }
    ]

    def fake_llm_caller(prompt: str, model: str, timeout: float) -> dict[str, Any]:
        assert "Primary Solver is the anchor" in prompt
        assert model == "openai/gpt-5.4-mini"
        assert int(timeout) == 30
        return {
            "success": True,
            "diagnostic_elapsed_ms": 1200,
            "raw_content": json.dumps(
                {
                    "action": "CHECK",
                    "amount": 0,
                    "sizing_type": "none",
                    "confidence": "medium",
                    "reason": "Primary solver is check-heavy.",
                    "risk_flags": [],
                }
            ),
        }

    summary = compare_solver_requests_llm(
        llm_path=request_path,
        llm_dir=None,
        sample_ids=None,
        llm_model="openai/gpt-5.4-mini",
        timeout=30,
        out_dir=out_dir,
        bridge_factory=FakeBridge,
        llm_caller=fake_llm_caller,
    )

    assert summary["total_samples"] == 1
    assert summary["success_count"] == 1
    assert summary["action_match_rate"] == 1.0
    item_path = out_dir / "items" / "hand_000004_req_000004_flop_llm.json"
    assert item_path.exists()
    item = json.loads(item_path.read_text(encoding="utf-8"))
    assert item["baseline_action"] == "CHECK"
    assert item["llm_action"] == "CHECK"
    assert item["legal_action_valid"] is True


def test_llm_prompt_includes_margin_class() -> None:
    """LLM diagnostic prompt includes primary_top_margin and primary_margin_class."""
    payload = {
        "meta": {"spr": 38.2, "hero_position": "BTN", "hero_is_ip": True},
        "request": {
            "board": "5h4h9h",
            "starting_pot": 232,
            "effective_stack": 8883,
            "actions_played": [],
        },
    }
    baseline = {
        "action": "CHECK",
        "amount": 0,
        "probabilities": {"CHECK": 0.63, "BET 139": 0.37},
    }

    prompt = build_llm_flop_prompt(payload, baseline, ["CHECK", "BET", "ALL_IN"])

    assert "primary_top_margin" in prompt
    assert "primary_margin_class" in prompt
    assert "primary_second_action" in prompt
    assert "primary_second_probability" in prompt
    assert "margin_class" in prompt
    assert "Margin interpretation rules" in prompt


def test_evaluate_llm_near_tie_high_confidence_flags_overstatement() -> None:
    """near_tie + confidence=high flags confidence_overstated and reason_overclaim."""
    baseline = {"action": "CHECK", "amount": 0}
    baseline_probability = {
        "top_action": "CHECK",
        "top_probability": 0.354,
        "second_action": "BET",
        "second_probability": 0.343,
        "top_margin": 0.011,
    }
    decision = {
        "action": "CHECK",
        "amount": 0,
        "confidence": "high",
        "reason": "Solver is clear that checking dominates here.",
    }
    llm_result = {"success": True, "elapsed_ms": 1200}

    result = evaluate_llm_decision(
        baseline, baseline_probability, decision, ["CHECK", "BET"], llm_result
    )

    assert result["primary_margin_class"] == "near_tie"
    assert result["confidence_overstated"] is True
    assert result["reason_overclaim"] is True


def test_evaluate_llm_near_tie_medium_confidence_no_overstatement() -> None:
    """near_tie + confidence=medium does NOT flag overstatement."""
    baseline = {"action": "CHECK", "amount": 0}
    baseline_probability = {
        "top_action": "CHECK",
        "top_probability": 0.354,
        "second_action": "BET",
        "second_probability": 0.343,
        "top_margin": 0.011,
    }
    decision = {
        "action": "CHECK",
        "amount": 0,
        "confidence": "medium",
        "reason": "Top solver action is CHECK by a small margin.",
    }
    llm_result = {"success": True, "elapsed_ms": 1200}

    result = evaluate_llm_decision(
        baseline, baseline_probability, decision, ["CHECK", "BET"], llm_result
    )

    assert result["primary_margin_class"] == "near_tie"
    assert result["confidence_overstated"] is False
    assert result["reason_overclaim"] is False


def test_reason_overclaim_ignores_negated_clear_language() -> None:
    """Negated clear/dominant language does not flag near-tie overclaim."""
    baseline = {"action": "FOLD", "amount": 0}
    baseline_probability = {
        "top_action": "FOLD",
        "top_probability": 0.354,
        "second_action": "CALL",
        "second_probability": 0.343,
        "top_margin": 0.011,
    }
    llm_result = {"success": True, "elapsed_ms": 1200}

    negated_decision = {
        "action": "FOLD",
        "amount": 0,
        "confidence": "medium",
        "reason": (
            "This is a close mixed node; fold is selected only because it is "
            "top, not because it clearly dominates."
        ),
    }
    negated_clear_or_dominant_decision = {
        "action": "FOLD",
        "amount": 0,
        "confidence": "medium",
        "reason": "This is not a clear or dominant solver result.",
    }
    positive_decision = {
        "action": "FOLD",
        "amount": 0,
        "confidence": "medium",
        "reason": "Fold clearly dominates this node.",
    }

    negated_result = evaluate_llm_decision(
        baseline, baseline_probability, negated_decision, ["FOLD", "CALL"], llm_result
    )
    negated_clear_or_dominant_result = evaluate_llm_decision(
        baseline,
        baseline_probability,
        negated_clear_or_dominant_decision,
        ["FOLD", "CALL"],
        llm_result,
    )
    positive_result = evaluate_llm_decision(
        baseline, baseline_probability, positive_decision, ["FOLD", "CALL"], llm_result
    )

    assert negated_result["reason_overclaim"] is False
    assert negated_clear_or_dominant_result["reason_overclaim"] is False
    assert positive_result["reason_overclaim"] is True


def test_build_teacher_summary_aggregates_teacher_results() -> None:
    """Teacher summary includes success/error counts and elapsed aggregates."""
    summary = build_teacher_summary(
        [
            {
                "sample_id": "a",
                "success": True,
                "elapsed_ms": 1000,
                "action": "CHECK",
                "amount": 0,
                "top_action": "CHECK",
                "top_probability": 0.7,
                "top_margin": 0.4,
                "error": None,
            },
            {
                "sample_id": "b",
                "success": False,
                "elapsed_ms": 3000,
                "action": None,
                "amount": None,
                "top_action": None,
                "top_probability": None,
                "top_margin": None,
                "error": "timeout",
            },
        ],
        "standard",
    )

    assert summary["total_samples"] == 2
    assert summary["success_count"] == 1
    assert summary["error_count"] == 1
    assert summary["avg_elapsed_ms"] == 2000
    assert summary["max_elapsed_ms"] == 3000
    assert summary["profile"] == "standard"


def test_grid_score_penalizes_dangerous_flips() -> None:
    """Grid score rewards speed/matches and penalizes dangerous action flips."""
    assert grid_score(
        under_15s=True,
        action_match=True,
        amount_match=True,
        primary_top_margin=0.30,
        dangerous_flip=False,
        clear_check_to_bet=False,
        call_or_raise_to_fold=False,
    ) == 8
    assert grid_score(
        under_15s=False,
        action_match=False,
        amount_match=False,
        primary_top_margin=0.30,
        dangerous_flip=True,
        clear_check_to_bet=True,
        call_or_raise_to_fold=False,
    ) == -12


def test_compare_solver_requests_grid_prunes_heavy_iterations(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Grid mode records pruned profiles after a slow candidate."""
    grid_dir = workspace_tmp / "grid"
    out_dir = workspace_tmp / "grid_out"
    grid_dir.mkdir()
    primary = grid_dir / "hand_000004_req_000004_flop.json"
    _write_json(primary, {"request": {"name": "primary", "max_iterations": 300}})
    monkeypatch.setattr("scripts.compare_solver_requests.GRID_MAX_ITERATIONS", (150, 180))
    monkeypatch.setattr("scripts.compare_solver_requests.GRID_TARGET_EXPLOITABILITY", (1.2,))
    monkeypatch.setattr("scripts.compare_solver_requests.GRID_BET_SIZES", ("60%",))
    FakeBridge.responses = [
        {
            "success": True,
            "diagnostic_elapsed_ms": 22000,
            "probabilities": {"CHECK": 0.8, "BET 120": 0.2},
        },
        {
            "success": True,
            "diagnostic_elapsed_ms": 21001,
            "probabilities": {"BET 120": 0.7, "CHECK": 0.3},
        },
    ]

    summary = compare_solver_requests_grid(
        grid_dir,
        phase="flop",
        sample_ids=["hand_000004_req_000004_flop"],
        timeout=30,
        out_dir=out_dir,
        bridge_factory=FakeBridge,
    )

    assert summary["total_samples"] == 1
    assert summary["total_planned_profiles"] == 2
    assert summary["executed_count"] == 1
    assert summary["skipped_by_pruning_count"] == 1
    profile = summary["profiles"]["iter150_target1_2_bets60"]
    assert profile["dangerous_flip_count"] == 1
    item_path = out_dir / "items" / "hand_000004_req_000004_flop_grid.json"
    item = json.loads(item_path.read_text(encoding="utf-8"))
    assert item["baseline_probability_summary"]["top_margin"] == 0.6
    assert item["results"][0]["dangerous_flip"] is True
    assert item["results"][1]["skipped_by_pruning"] is True


def test_repeatability_item_summary_detects_unstable_actions() -> None:
    """Repeatability item summary reports action and elapsed instability."""
    summary = repeatability_item_summary(
        "hand_000006_req_000007_flop",
        [
            {
                "action": "CHECK",
                "amount": 0,
                "elapsed_ms": 28000,
                "probabilities": {"CHECK": 0.6, "BET 120": 0.4},
            },
            {
                "action": "BET",
                "amount": 120,
                "elapsed_ms": 30000,
                "probabilities": {"BET 120": 0.55, "CHECK": 0.45},
            },
        ],
    )

    assert summary["action_stable"] is False
    assert summary["amount_stable"] is False
    assert summary["unstable"] is True
    assert summary["action_set"] == ["BET", "CHECK"]
    assert summary["amount_set"] == [0, 120]
    assert summary["elapsed_spread_ms"] == 2000
    assert summary["probability_top_action_set"] == ["BET", "CHECK"]
    assert summary["probability_top_margin_range"] == [0.1, 0.2]


def test_compare_solver_requests_repeat_writes_items_and_summary(
    workspace_tmp: Path,
) -> None:
    """Repeat mode runs the same request N times and writes summaries."""
    request_path = workspace_tmp / "hand_000006_req_000007_flop.json"
    out_dir = workspace_tmp / "repeat_out"
    _write_json(request_path, {"request": {"name": "repeat"}})
    FakeBridge.responses = [
        {
            "success": True,
            "diagnostic_elapsed_ms": 28000,
            "probabilities": {"CHECK": 0.6, "BET 120": 0.4},
        },
        {
            "success": True,
            "diagnostic_elapsed_ms": 28100,
            "probabilities": {"CHECK": 0.62, "BET 120": 0.38},
        },
    ]

    summary = compare_solver_requests_repeat(
        repeat_path=request_path,
        repeat_dir=None,
        sample_ids=None,
        repeat_count=2,
        timeout=30,
        out_dir=out_dir,
        bridge_factory=FakeBridge,
    )

    assert summary["total_samples"] == 1
    assert summary["unstable_sample_count"] == 0
    assert summary["items"][0]["action_stable"] is True
    item_path = out_dir / "items" / "hand_000006_req_000007_flop_repeat.json"
    assert item_path.exists()
    summary_path = out_dir / "repeatability_summary.json"
    assert summary_path.exists()


def test_build_repeatability_summary_lists_unstable_samples() -> None:
    """Repeatability summary aggregates unstable sample ids."""
    summary = build_repeatability_summary(
        [
            {
                "summary": {
                    "sample_id": "stable",
                    "unstable": False,
                    "elapsed_spread_ms": 100,
                }
            },
            {
                "summary": {
                    "sample_id": "unstable",
                    "unstable": True,
                    "elapsed_spread_ms": 300,
                }
            },
        ]
    )

    assert summary["total_samples"] == 2
    assert summary["unstable_sample_count"] == 1
    assert summary["unstable_samples"] == ["unstable"]
    assert summary["avg_elapsed_spread_ms"] == 200


def test_resident_item_summary_separates_start_and_solve_time() -> None:
    """Resident timing summary separates process start from solve time."""
    summary = resident_item_summary(
        "hand_000004_req_000004_flop",
        [
            {
                "start_ms": 1200,
                "solve_ms": 21000,
                "total_ms": 22200,
                "action": "CHECK",
                "amount": 0,
                "probabilities": {"CHECK": 0.7, "BET 120": 0.3},
            },
            {
                "start_ms": None,
                "solve_ms": 20800,
                "total_ms": 20800,
                "action": "CHECK",
                "amount": 0,
                "probabilities": {"CHECK": 0.72, "BET 120": 0.28},
            },
        ],
    )

    assert summary["start_ms"] == 1200
    assert summary["avg_resident_solve_ms"] == 20900
    assert summary["estimated_start_overhead_ms"] == 600
    assert summary["process_reuse_effective"] is False
    assert summary["action_stable"] is True


def test_compare_solver_requests_resident_reuses_one_bridge(
    workspace_tmp: Path,
) -> None:
    """Resident mode starts one bridge and reuses it for repeated solves."""
    request_path = workspace_tmp / "hand_000004_req_000004_flop.json"
    out_dir = workspace_tmp / "resident_out"
    _write_json(request_path, {"request": {"name": "resident"}})
    FakeBridge.responses = [
        {
            "success": True,
            "diagnostic_elapsed_ms": 21000,
            "probabilities": {"CHECK": 0.7, "BET 120": 0.3},
        },
        {
            "success": True,
            "diagnostic_elapsed_ms": 21100,
            "probabilities": {"CHECK": 0.72, "BET 120": 0.28},
        },
    ]

    summary = compare_solver_requests_resident(
        resident_path=request_path,
        resident_dir=None,
        sample_ids=None,
        repeat_count=2,
        timeout=30,
        out_dir=out_dir,
        bridge_factory=FakeBridge,
    )

    assert FakeBridge.created_count == 1
    assert FakeBridge.started_count == 1
    assert FakeBridge.events.count("stop:0") == 1
    assert FakeBridge.events == [
        "create:0",
        "start:0",
        "solve:0:resident:30",
        "solve:0:resident:30",
        "stop:0",
    ]
    assert summary["total_samples"] == 1
    assert summary["items"][0]["avg_resident_solve_ms"] == 21050
    item_path = out_dir / "items" / "hand_000004_req_000004_flop_resident.json"
    assert item_path.exists()
    summary_path = out_dir / "resident_timing_summary.json"
    assert summary_path.exists()


def test_build_resident_timing_summary_aggregates_items() -> None:
    """Resident timing summary aggregates solve timings and reuse effectiveness."""
    summary = build_resident_timing_summary(
        [
            {
                "summary": {
                    "sample_id": "a",
                    "avg_resident_solve_ms": 20000,
                    "process_reuse_effective": True,
                }
            },
            {
                "summary": {
                    "sample_id": "b",
                    "avg_resident_solve_ms": 22000,
                    "process_reuse_effective": False,
                }
            },
        ],
        start_ms=1200,
    )

    assert summary["total_samples"] == 2
    assert summary["start_ms"] == 1200
    assert summary["avg_resident_solve_ms"] == 21000
    assert summary["min_resident_solve_ms"] == 20000
    assert summary["max_resident_solve_ms"] == 22000
    assert summary["process_reuse_effective_count"] == 1


def test_result_filename_falls_back_to_timestamp(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unknown request filename still produces a result JSON filename."""

    class FakeDateTime:
        """Small datetime replacement for deterministic filenames."""

        @staticmethod
        def now() -> Any:
            class FakeNow:
                """Object exposing strftime."""

                @staticmethod
                def strftime(_format: str) -> str:
                    return "20260519_120000"

            return FakeNow()

    monkeypatch.setattr("scripts.compare_solver_requests.datetime", FakeDateTime)

    assert result_filename(Path("unknown.json")) == (
        "solver_compare_result_20260519_120000.json"
    )


def test_build_summary_calculates_speedup() -> None:
    """Summary includes action/amount matches and elapsed ratio."""
    summary = build_summary(
        {"action": "CALL", "amount": 324, "elapsed_ms": 20000},
        {"action": "CALL", "amount": 324, "elapsed_ms": 4000},
        {"action": "BET", "amount": 324, "elapsed_ms": 5000},
        {"action": "CALL", "amount": 120, "elapsed_ms": 10000},
        {"action": "CALL", "amount": 324, "elapsed_ms": 15000},
    )

    assert summary == {
        "compare_action_match": True,
        "compare_amount_match": True,
        "compare_speedup_ratio": 5.0,
        "light_action_match": False,
        "light_amount_match": True,
        "light_speedup_ratio": 4.0,
        "middle_action_match": True,
        "middle_amount_match": False,
        "middle_speedup_ratio": 2.0,
        "fast_middle_action_match": True,
        "fast_middle_amount_match": True,
        "fast_middle_speedup_ratio": 1.333,
        "fast_middle_under_15s": True,
    }


def test_build_batch_summary_aggregates_profile_metrics() -> None:
    """Batch summary reports under-15s rates and action flips."""
    summary = build_batch_summary(
        total_primary_files=2,
        skipped_missing_compare=["missing.json"],
        items=[
            {
                "primary_success": True,
                "primary_elapsed_ms": 20000,
                "primary_action": "CHECK",
                "primary_amount": 0,
                "primary_top_margin": 0.25,
                "light_success": True,
                "light_elapsed_ms": 10000,
                "light_action": "BET",
                "light_amount": 120,
                "light_action_match": False,
                "light_amount_match": False,
                "light_action_mismatch_near_tie": False,
                "light_dangerous_flip": True,
                "light_clear_check_to_bet": True,
                "light_call_or_raise_to_fold": False,
                "compare_success": True,
                "compare_elapsed_ms": 19000,
                "compare_action": "CHECK",
                "compare_amount": 0,
                "compare_action_match": True,
                "compare_amount_match": True,
                "compare_action_mismatch_near_tie": False,
                "compare_dangerous_flip": False,
                "compare_clear_check_to_bet": False,
                "compare_call_or_raise_to_fold": False,
                "middle_success": True,
                "middle_elapsed_ms": 14000,
                "middle_action": "CHECK",
                "middle_amount": 0,
                "middle_action_match": True,
                "middle_amount_match": True,
                "middle_action_mismatch_near_tie": False,
                "middle_dangerous_flip": False,
                "middle_clear_check_to_bet": False,
                "middle_call_or_raise_to_fold": False,
                "fast_middle_success": False,
                "fast_middle_elapsed_ms": 30000,
                "fast_middle_action": None,
                "fast_middle_amount": None,
                "fast_middle_action_match": False,
                "fast_middle_amount_match": False,
                "fast_middle_action_mismatch_near_tie": False,
                "fast_middle_dangerous_flip": True,
                "fast_middle_clear_check_to_bet": False,
                "fast_middle_call_or_raise_to_fold": False,
            }
        ],
    )

    assert summary["total_primary_files"] == 2
    assert summary["compared"] == 1
    assert summary["profiles"]["primary"]["under_15s_rate"] == 0.0
    assert summary["profiles"]["light"]["under_15s_rate"] == 1.0
    assert summary["profiles"]["light"]["action_match_rate"] == 0.0
    assert summary["profiles"]["light"]["check_to_bet_count"] == 1
    assert summary["profiles"]["light"]["dangerous_flip_count"] == 1
    assert summary["profiles"]["light"]["clear_check_to_bet_count"] == 1
    assert summary["profiles"]["fast_middle"]["error_count"] == 1
