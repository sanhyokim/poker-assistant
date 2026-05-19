"""Tests for the temporary solver request comparison diagnostic script."""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from typing import Any

import pytest

from scripts.compare_solver_requests import (
    build_summary,
    compare_solver_requests,
    extract_action_summary,
    load_solver_request,
    parse_solver_action,
    result_filename,
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
    """Primary and compare requests run in clean separate solver processes."""
    primary_path = workspace_tmp / "hand_000005_req_000004_flop.json"
    compare_path = workspace_tmp / "hand_000005_req_000005_flop_compare_no_allin.json"
    out_dir = workspace_tmp / "out"
    _write_json(primary_path, {"request": {"name": "primary"}})
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
    ]
    assert result["primary"]["action"] == "CALL"
    assert result["compare"]["action"] == "CALL"
    assert result["summary"]["action_match"] is True
    assert result["summary"]["amount_match"] is True

    output_path = Path(result["output_path"])
    assert output_path == out_dir / "hand_000005_flop_compare_result.json"
    assert output_path.exists()
    saved = json.loads(output_path.read_text(encoding="utf-8"))
    assert saved["primary"]["path"] == str(primary_path)
    assert saved["compare"]["path"] == str(compare_path)


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
    )

    assert summary == {
        "action_match": True,
        "amount_match": True,
        "speedup_ratio": 5.0,
    }
