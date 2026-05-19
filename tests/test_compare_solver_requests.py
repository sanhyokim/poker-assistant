"""Tests for the temporary solver request comparison diagnostic script."""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from typing import Any

import pytest

from scripts.compare_solver_requests import (
    batch_result_filename,
    build_batch_summary,
    build_summary,
    build_fast_middle_probe_request,
    build_light_probe_request,
    build_middle_probe_request,
    compare_solver_requests_batch,
    compare_solver_requests,
    discover_primary_request_files,
    extract_action_summary,
    find_compare_request_for_primary,
    load_solver_request,
    parse_solver_action,
    result_filename,
    sample_id,
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

    def __init__(self) -> None:
        self.index = FakeBridge.created_count
        FakeBridge.created_count += 1
        FakeBridge.events.append(f"create:{self.index}")

    def solve(self, request: dict[str, Any], timeout: float = 12.0) -> dict[str, Any]:
        """Return the next configured solver response."""
        FakeBridge.events.append(
            f"solve:{self.index}:{request['name']}:{int(timeout)}"
        )
        return FakeBridge.responses[self.index]

    def stop(self) -> None:
        """Record solver process stop."""
        FakeBridge.events.append(f"stop:{self.index}")


@pytest.fixture(autouse=True)
def reset_fake_bridge() -> None:
    """Reset fake bridge class state before each test."""
    FakeBridge.responses = []
    FakeBridge.events = []
    FakeBridge.created_count = 0


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


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
                "light_success": True,
                "light_elapsed_ms": 10000,
                "light_action": "BET",
                "light_amount": 120,
                "light_action_match": False,
                "light_amount_match": False,
                "compare_success": True,
                "compare_elapsed_ms": 19000,
                "compare_action": "CHECK",
                "compare_amount": 0,
                "compare_action_match": True,
                "compare_amount_match": True,
                "middle_success": True,
                "middle_elapsed_ms": 14000,
                "middle_action": "CHECK",
                "middle_amount": 0,
                "middle_action_match": True,
                "middle_amount_match": True,
                "fast_middle_success": False,
                "fast_middle_elapsed_ms": 30000,
                "fast_middle_action": None,
                "fast_middle_amount": None,
                "fast_middle_action_match": False,
                "fast_middle_amount_match": False,
            }
        ],
    )

    assert summary["total_primary_files"] == 2
    assert summary["compared"] == 1
    assert summary["profiles"]["primary"]["under_15s_rate"] == 0.0
    assert summary["profiles"]["light"]["under_15s_rate"] == 1.0
    assert summary["profiles"]["light"]["action_match_rate"] == 0.0
    assert summary["profiles"]["light"]["check_to_bet_count"] == 1
    assert summary["profiles"]["fast_middle"]["error_count"] == 1
