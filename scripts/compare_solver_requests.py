"""Temporary diagnostic tool for comparing Solver request variants.

This script is NOT part of the production recommendation path.
It is used manually to compare deep-SPR flop primary requests against
compare_no_allin requests.

Do not call this script from GameLoop, RecommendationEngine, or any live
recommendation path. After the Solver speed investigation is complete,
remove it or keep it explicitly as a diagnostics-only tool.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any, Callable

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from solver.solver_bridge import PostflopSolverBridge
from strategy.llm_pipeline import LLMPipeline

JsonDict = dict[str, Any]
BridgeFactory = Callable[[], PostflopSolverBridge]
LLMCaller = Callable[[str, str, float], JsonDict]
VARIANT_SUFFIXES = (
    "_compare_no_allin",
    "_light_probe",
    "_middle_probe",
    "_fast_middle_probe",
)
PROFILE_NAMES = ("primary", "compare", "light", "middle", "fast_middle")
GRID_MAX_ITERATIONS = (150, 180, 200, 230, 250, 280, 300)
GRID_TARGET_EXPLOITABILITY = (1.2, 1.0, 0.9, 0.8, 0.7, 0.6)
GRID_BET_SIZES = ("60%,a", "60%", "50%,60%", "33%,60%")
DEFAULT_LLM_MODEL = "openai/gpt-5.4-mini"
LLM_ALLOWED_ACTIONS = {"CHECK", "BET", "RAISE", "CALL", "FOLD", "ALL_IN"}
LLM_ALLOWED_SIZING_TYPES = {
    "none",
    "bet_33",
    "bet_50",
    "bet_60",
    "bet_75",
    "raise_33",
    "raise_50",
    "raise_60",
    "raise_75",
    "all_in",
}
TEACHER_PROFILES: dict[str, JsonDict] = {
    "standard": {
        "max_iterations": 500,
        "target_exploitability_pct": 0.4,
        "timeout_ms": 90000,
        "bet_sizes": "33%,50%,60%,75%,a",
        "raise_sizes": "2.5x",
    },
    "narrow": {
        "max_iterations": 500,
        "target_exploitability_pct": 0.4,
        "timeout_ms": 180000,
        "bet_sizes": "60%,a",
        "raise_sizes": "2.5x",
    },
    "teacher_300_plus": {
        "max_iterations": 300,
        "target_exploitability_pct": 0.6,
        "timeout_ms": 180000,
        "bet_sizes": "50%,60%,75%,a",
        "raise_sizes": "2.5x",
    },
    "high": {
        "max_iterations": 800,
        "target_exploitability_pct": 0.3,
        "timeout_ms": 120000,
        "bet_sizes": "25%,33%,50%,60%,75%,a",
        "raise_sizes": "2.5x,3.5x",
    },
}
SINGLE_SIZE_PROFILES: dict[str, JsonDict] = {
    "single_33": {"bet_size": "33%", "raise_size": "2.5x"},
    "single_50": {"bet_size": "50%", "raise_size": "2.5x"},
    "single_60": {"bet_size": "60%", "raise_size": "2.5x"},
    "single_75": {"bet_size": "75%", "raise_size": "2.5x"},
    "single_allin": {"bet_size": "a", "raise_size": "a"},
}
STANDARD_SINGLE_SIZE_PROFILES = ("single_33", "single_50", "single_60", "single_75")
SIZING_TYPE_BY_PROFILE = {
    "single_33": "bet_33",
    "single_50": "bet_50",
    "single_60": "bet_60",
    "single_75": "bet_75",
    "single_allin": "all_in",
}


def load_env_file(env_path: Path | None = None, *, override: bool = False) -> None:
    """Load simple KEY=VALUE lines from .env.

    If override=True, values from .env replace existing process env vars.
    If override=False, existing process env vars are preserved.
    """
    path = env_path or (REPO_ROOT / ".env")
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue

        quoted = (
            (value.startswith('"') and value.endswith('"'))
            or (value.startswith("'") and value.endswith("'"))
        )
        if quoted:
            value = value[1:-1]

        if override:
            os.environ[key] = value
        else:
            os.environ.setdefault(key, value)


def load_solver_request(path: Path) -> JsonDict:
    """Load a solver request JSON file with optional metadata wrapping.

    Args:
        path: Path to a JSON request file.

    Returns:
        Solver request dictionary to send to the CLI.
    """
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if isinstance(payload, dict) and isinstance(payload.get("request"), dict):
        return dict(payload["request"])
    if not isinstance(payload, dict):
        raise ValueError(f"Solver request JSON must be an object: {path}")
    return dict(payload)


def load_solver_payload(path: Path) -> JsonDict:
    """Load a solver JSON file while preserving optional metadata."""
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise ValueError(f"Solver request JSON must be an object: {path}")
    if isinstance(payload.get("request"), dict):
        return {"meta": dict(payload.get("meta") or {}), "request": dict(payload["request"])}
    return {"meta": {}, "request": dict(payload)}


def run_solver_request(
    path: Path,
    *,
    timeout: float,
    bridge_factory: BridgeFactory = PostflopSolverBridge,
) -> JsonDict:
    """Run one solver request and return a normalized diagnostic result.

    Args:
        path: Request JSON path.
        timeout: Timeout seconds for the solver bridge.
        bridge_factory: Factory used by tests to inject a fake bridge.

    Returns:
        Normalized result dictionary containing timing and parsed action fields.
    """
    request = load_solver_request(path)
    return run_solver_request_payload(
        request,
        path_label=str(path),
        timeout=timeout,
        bridge_factory=bridge_factory,
    )


def run_solver_request_payload(
    request: JsonDict,
    *,
    path_label: str,
    timeout: float,
    bridge_factory: BridgeFactory = PostflopSolverBridge,
) -> JsonDict:
    """Run one request payload and return a normalized diagnostic result.

    Args:
        request: Solver request dictionary.
        path_label: Source label recorded in the result JSON.
        timeout: Timeout seconds for the solver bridge.
        bridge_factory: Factory used by tests to inject a fake bridge.

    Returns:
        Normalized result dictionary containing timing and parsed action fields.
    """
    bridge = bridge_factory()
    started_at = time.perf_counter()
    raw_result: JsonDict
    try:
        raw_result = bridge.solve(request, timeout=timeout)
    except Exception as exc:
        raw_result = {"success": False, "error": str(exc)}
    finally:
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        bridge.stop()
    diagnostic_elapsed = _optional_int(raw_result.get("diagnostic_elapsed_ms"))
    if diagnostic_elapsed is not None:
        elapsed_ms = diagnostic_elapsed

    action, amount, probabilities = extract_action_summary(raw_result)
    return {
        "path": path_label,
        "success": bool(raw_result.get("success")),
        "elapsed_ms": elapsed_ms,
        "action": action,
        "amount": amount,
        "probabilities": probabilities,
        "error": raw_result.get("error"),
    }


def build_light_probe_request(primary_request: JsonDict) -> JsonDict:
    """Return a light-probe request derived from a primary Solver request.

    Args:
        primary_request: Original production Solver request dictionary.

    Returns:
        New request dictionary using deep_spr_light_probe-style parameters.
    """
    light_request = dict(primary_request)
    light_request["timeout_ms"] = 5000
    light_request["max_iterations"] = 80
    light_request["target_exploitability_pct"] = 1.5

    for street in ("flop", "turn"):
        light_request[f"{street}_bet_sizes_oop"] = "50%"
        light_request[f"{street}_bet_sizes_ip"] = "50%"
        light_request[f"{street}_raise_sizes_oop"] = "2.5x"
        light_request[f"{street}_raise_sizes_ip"] = "2.5x"

    return light_request


def build_middle_probe_request(primary_request: JsonDict) -> JsonDict:
    """Return a middle-probe request derived from a primary Solver request.

    Args:
        primary_request: Original production Solver request dictionary.

    Returns:
        New request dictionary using an intermediate diagnostic profile.
    """
    middle_request = dict(primary_request)
    middle_request["timeout_ms"] = 12000
    middle_request["max_iterations"] = 150
    middle_request["target_exploitability_pct"] = 1.0

    for street in ("flop", "turn"):
        middle_request[f"{street}_bet_sizes_oop"] = "60%"
        middle_request[f"{street}_bet_sizes_ip"] = "60%"
        middle_request[f"{street}_raise_sizes_oop"] = "2.5x"
        middle_request[f"{street}_raise_sizes_ip"] = "2.5x"

    return middle_request


def build_fast_middle_probe_request(primary_request: JsonDict) -> JsonDict:
    """Return a fast-middle probe request derived from a primary request.

    Args:
        primary_request: Original production Solver request dictionary.

    Returns:
        New request dictionary targeting a 15-second diagnostic profile.
    """
    fast_middle_request = dict(primary_request)
    fast_middle_request["timeout_ms"] = 15000
    fast_middle_request["max_iterations"] = 120
    fast_middle_request["target_exploitability_pct"] = 1.2

    for street in ("flop", "turn"):
        fast_middle_request[f"{street}_bet_sizes_oop"] = "60%"
        fast_middle_request[f"{street}_bet_sizes_ip"] = "60%"
        fast_middle_request[f"{street}_raise_sizes_oop"] = "2.5x"
        fast_middle_request[f"{street}_raise_sizes_ip"] = "2.5x"

    return fast_middle_request


def build_grid_probe_request(
    primary_request: JsonDict,
    *,
    max_iterations: int,
    target_exploitability_pct: float,
    bet_sizes: str,
) -> JsonDict:
    """Return a grid-search request derived from a primary Solver request."""
    grid_request = dict(primary_request)
    grid_request["max_iterations"] = max_iterations
    grid_request["target_exploitability_pct"] = target_exploitability_pct
    for street in ("flop", "turn"):
        grid_request[f"{street}_bet_sizes_oop"] = bet_sizes
        grid_request[f"{street}_bet_sizes_ip"] = bet_sizes
    return grid_request


def build_teacher_request(primary_request: JsonDict, profile: str) -> JsonDict:
    """Return a high-precision teacher request derived from a primary request."""
    if profile not in TEACHER_PROFILES:
        raise ValueError(f"Unknown teacher profile: {profile}")
    config = TEACHER_PROFILES[profile]
    teacher_request = dict(primary_request)
    teacher_request["max_iterations"] = int(config["max_iterations"])
    teacher_request["target_exploitability_pct"] = float(
        config["target_exploitability_pct"]
    )
    teacher_request["timeout_ms"] = int(config["timeout_ms"])
    for street in ("flop", "turn", "river"):
        teacher_request[f"{street}_bet_sizes_oop"] = str(config["bet_sizes"])
        teacher_request[f"{street}_bet_sizes_ip"] = str(config["bet_sizes"])
        teacher_request[f"{street}_raise_sizes_oop"] = str(config["raise_sizes"])
        teacher_request[f"{street}_raise_sizes_ip"] = str(config["raise_sizes"])
    return teacher_request


def build_single_size_request(primary_request: JsonDict, profile: str) -> JsonDict:
    """Return a flop single-size diagnostic request derived from a primary request."""
    if profile not in SINGLE_SIZE_PROFILES:
        raise ValueError(f"Unknown single-size profile: {profile}")
    config = SINGLE_SIZE_PROFILES[profile]
    single_size_request = dict(primary_request)
    single_size_request["max_iterations"] = 300
    single_size_request["target_exploitability_pct"] = 0.6
    single_size_request["timeout_ms"] = 30000
    single_size_request["flop_bet_sizes_oop"] = str(config["bet_size"])
    single_size_request["flop_bet_sizes_ip"] = str(config["bet_size"])
    single_size_request["flop_raise_sizes_oop"] = str(config["raise_size"])
    single_size_request["flop_raise_sizes_ip"] = str(config["raise_size"])
    return single_size_request


def teacher_request_config(profile: str) -> JsonDict:
    """Return public teacher request config for output JSON."""
    if profile not in TEACHER_PROFILES:
        raise ValueError(f"Unknown teacher profile: {profile}")
    return dict(TEACHER_PROFILES[profile])


def grid_profile_id(
    *,
    max_iterations: int,
    target_exploitability_pct: float,
    bet_sizes: str,
) -> str:
    """Return a compact grid profile id."""
    bet_label = (
        bet_sizes.replace("%", "")
        .replace(",", "_")
        .replace("a", "allin")
    )
    target_label = str(target_exploitability_pct).replace(".", "_")
    return f"iter{max_iterations}_target{target_label}_bets{bet_label}"


def extract_action_summary(result: JsonDict) -> tuple[str | None, int | None, JsonDict]:
    """Extract action, amount, and probabilities from a solver response.

    Args:
        result: Raw solver response dictionary.

    Returns:
        Tuple of action, amount, and normalized probability mapping.
    """
    probabilities = _extract_probabilities(result)
    if probabilities:
        selected_action = max(probabilities.items(), key=lambda item: item[1])[0]
        action, amount = parse_solver_action(selected_action)
        return action, amount, probabilities

    action = result.get("action") or result.get("selected_action")
    amount = result.get("amount") or result.get("selected_amount")
    parsed_amount = _optional_int(amount)
    if action is None:
        return None, parsed_amount, {}
    parsed_action, action_amount = parse_solver_action(str(action))
    return parsed_action, parsed_amount if parsed_amount is not None else action_amount, {}


def parse_solver_action(action_text: str) -> tuple[str, int]:
    """Parse a display action and optional chip amount from solver output.

    Args:
        action_text: Solver action label such as ``CALL`` or ``BET 120``.

    Returns:
        Uppercase action and parsed amount, or zero when no amount is present.
    """
    cleaned = action_text.replace("-", "_").strip()
    parts = cleaned.split()
    action = parts[0].upper() if parts else "CHECK"
    amount = 0
    if len(parts) >= 2:
        amount = _optional_int(parts[1]) or 0
    return action, amount


def build_summary(
    primary: JsonDict,
    compare: JsonDict,
    light: JsonDict | None = None,
    middle: JsonDict | None = None,
    fast_middle: JsonDict | None = None,
) -> JsonDict:
    """Build comparison summary fields for normalized solver results."""
    compare_summary = _variant_summary(primary, compare)
    light_summary = _variant_summary(primary, light) if light is not None else {
        "action_match": None,
        "amount_match": None,
        "speedup_ratio": None,
    }
    middle_summary = _variant_summary(primary, middle) if middle is not None else {
        "action_match": None,
        "amount_match": None,
        "speedup_ratio": None,
    }
    fast_middle_summary = (
        _variant_summary(primary, fast_middle)
        if fast_middle is not None
        else {"action_match": None, "amount_match": None, "speedup_ratio": None}
    )
    return {
        "compare_action_match": compare_summary["action_match"],
        "compare_amount_match": compare_summary["amount_match"],
        "compare_speedup_ratio": compare_summary["speedup_ratio"],
        "light_action_match": light_summary["action_match"],
        "light_amount_match": light_summary["amount_match"],
        "light_speedup_ratio": light_summary["speedup_ratio"],
        "middle_action_match": middle_summary["action_match"],
        "middle_amount_match": middle_summary["amount_match"],
        "middle_speedup_ratio": middle_summary["speedup_ratio"],
        "fast_middle_action_match": fast_middle_summary["action_match"],
        "fast_middle_amount_match": fast_middle_summary["amount_match"],
        "fast_middle_speedup_ratio": fast_middle_summary["speedup_ratio"],
        "fast_middle_under_15s": _under_15s(fast_middle),
    }


def _variant_summary(primary: JsonDict, variant: JsonDict | None) -> JsonDict:
    """Return match and speedup summary for one variant against primary."""
    if variant is None:
        return {"action_match": None, "amount_match": None, "speedup_ratio": None}
    primary_elapsed = _optional_float(primary.get("elapsed_ms"))
    variant_elapsed = _optional_float(variant.get("elapsed_ms"))
    speedup_ratio = None
    if primary_elapsed is not None and variant_elapsed and variant_elapsed > 0:
        speedup_ratio = round(primary_elapsed / variant_elapsed, 3)

    return {
        "action_match": primary.get("action") == variant.get("action"),
        "amount_match": primary.get("amount") == variant.get("amount"),
        "speedup_ratio": speedup_ratio,
    }


def _under_15s(result: JsonDict | None) -> bool | None:
    """Return whether a result completed within the 15-second target."""
    if result is None:
        return None
    elapsed_ms = _optional_float(result.get("elapsed_ms"))
    if elapsed_ms is None:
        return None
    return elapsed_ms <= 15000


def compare_solver_requests(
    primary_path: Path,
    compare_path: Path,
    *,
    light_path: Path | None = None,
    middle_path: Path | None = None,
    fast_middle_path: Path | None = None,
    timeout: float,
    out_dir: Path,
    bridge_factory: BridgeFactory = PostflopSolverBridge,
    result_name: str | None = None,
) -> JsonDict:
    """Run all diagnostic request variants in separate Solver processes.

    Args:
        primary_path: Primary request JSON path.
        compare_path: compare_no_allin request JSON path.
        light_path: Optional prebuilt light_probe request JSON path.
        middle_path: Optional prebuilt middle_probe request JSON path.
        fast_middle_path: Optional prebuilt fast_middle_probe request JSON path.
        timeout: Timeout seconds per request.
        out_dir: Directory where the comparison result JSON is saved.
        bridge_factory: Factory used by tests to inject a fake bridge.
        result_name: Optional output filename override for batch mode.

    Returns:
        Full comparison result dictionary.
    """
    primary_request = load_solver_request(primary_path)
    compare_request = load_solver_request(compare_path)
    light_request = (
        load_solver_request(light_path)
        if light_path is not None
        else build_light_probe_request(primary_request)
    )
    middle_request = (
        load_solver_request(middle_path)
        if middle_path is not None
        else build_middle_probe_request(primary_request)
    )
    fast_middle_request = (
        load_solver_request(fast_middle_path)
        if fast_middle_path is not None
        else build_fast_middle_probe_request(primary_request)
    )
    light_label = str(light_path) if light_path is not None else (
        f"generated_light_probe_from:{primary_path}"
    )
    middle_label = str(middle_path) if middle_path is not None else (
        f"generated_middle_probe_from:{primary_path}"
    )
    fast_middle_label = str(fast_middle_path) if fast_middle_path is not None else (
        f"generated_fast_middle_probe_from:{primary_path}"
    )

    primary = run_solver_request_payload(
        primary_request,
        path_label=str(primary_path),
        timeout=timeout,
        bridge_factory=bridge_factory,
    )
    compare = run_solver_request_payload(
        compare_request,
        path_label=str(compare_path),
        timeout=timeout,
        bridge_factory=bridge_factory,
    )
    light = run_solver_request_payload(
        light_request,
        path_label=light_label,
        timeout=timeout,
        bridge_factory=bridge_factory,
    )
    middle = run_solver_request_payload(
        middle_request,
        path_label=middle_label,
        timeout=timeout,
        bridge_factory=bridge_factory,
    )
    fast_middle = run_solver_request_payload(
        fast_middle_request,
        path_label=fast_middle_label,
        timeout=timeout,
        bridge_factory=bridge_factory,
    )
    result = {
        "primary": primary,
        "compare": compare,
        "light": light,
        "middle": middle,
        "fast_middle": fast_middle,
        "summary": build_summary(primary, compare, light, middle, fast_middle),
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    output_path = out_dir / (result_name or result_filename(primary_path))
    with output_path.open("w", encoding="utf-8") as file:
        json.dump(result, file, ensure_ascii=False, indent=2)
    result["output_path"] = str(output_path)
    return result


def compare_solver_requests_batch(
    batch_dir: Path,
    *,
    phase: str,
    timeout: float,
    out_dir: Path,
    bridge_factory: BridgeFactory = PostflopSolverBridge,
) -> JsonDict:
    """Compare all primary Solver requests in a directory.

    Args:
        batch_dir: Directory containing saved Solver request JSON files.
        phase: Street phase to match, usually ``flop``.
        timeout: Timeout seconds per request.
        out_dir: Directory where item and summary result JSON files are saved.
        bridge_factory: Factory used by tests to inject a fake bridge.

    Returns:
        Batch summary dictionary.
    """
    primary_paths = discover_primary_request_files(batch_dir, phase)
    items_dir = out_dir / "items"
    items: list[JsonDict] = []
    skipped_missing_compare: list[str] = []

    print(
        "BATCH DISCOVERY: "
        f"batch_dir={batch_dir} phase={phase} primary_files={len(primary_paths)}"
    )
    for primary_path in primary_paths:
        compare_path = find_compare_request_for_primary(primary_path, batch_dir)
        if compare_path is None:
            skipped_missing_compare.append(str(primary_path))
            continue

        result = compare_solver_requests(
            primary_path,
            compare_path,
            timeout=timeout,
            out_dir=items_dir,
            bridge_factory=bridge_factory,
            result_name=batch_result_filename(primary_path),
        )
        items.append(_batch_item_summary(primary_path, result))

    summary = build_batch_summary(
        total_primary_files=len(primary_paths),
        skipped_missing_compare=skipped_missing_compare,
        items=items,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "batch_summary.json"
    summary["output_path"] = str(summary_path)
    with summary_path.open("w", encoding="utf-8") as file:
        json.dump(summary, file, ensure_ascii=False, indent=2)
    return summary


def compare_solver_requests_grid(
    grid_dir: Path,
    *,
    phase: str,
    sample_ids: list[str] | None,
    timeout: float,
    out_dir: Path,
    bridge_factory: BridgeFactory = PostflopSolverBridge,
) -> JsonDict:
    """Run a diagnostic grid search against primary deep-SPR flop requests."""
    primary_paths = discover_primary_request_files(grid_dir, phase)
    if sample_ids:
        selected = set(sample_ids)
        primary_paths = [
            path for path in primary_paths if sample_id(path) in selected
        ]

    items_dir = out_dir / "items"
    items_dir.mkdir(parents=True, exist_ok=True)
    all_results: list[JsonDict] = []
    planned_profiles = len(grid_profile_configs())
    print(
        "GRID DISCOVERY: "
        f"grid_dir={grid_dir} phase={phase} samples={len(primary_paths)} "
        f"profiles_per_sample={planned_profiles}"
    )

    for primary_path in primary_paths:
        item = _run_grid_for_sample(
            primary_path,
            timeout=timeout,
            bridge_factory=bridge_factory,
        )
        all_results.extend(item["results"])
        output_path = items_dir / f"{sample_id(primary_path)}_grid.json"
        with output_path.open("w", encoding="utf-8") as file:
            json.dump(item, file, ensure_ascii=False, indent=2)

    summary = build_grid_summary(
        total_samples=len(primary_paths),
        total_planned_profiles=len(primary_paths) * planned_profiles,
        results=all_results,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "grid_summary.json"
    summary["output_path"] = str(summary_path)
    with summary_path.open("w", encoding="utf-8") as file:
        json.dump(summary, file, ensure_ascii=False, indent=2)
    return summary


def compare_solver_requests_repeat(
    *,
    repeat_path: Path | None,
    repeat_dir: Path | None,
    sample_ids: list[str] | None,
    repeat_count: int,
    timeout: float,
    out_dir: Path,
    bridge_factory: BridgeFactory = PostflopSolverBridge,
) -> JsonDict:
    """Run identical Solver requests multiple times for repeatability checks."""
    request_paths = discover_repeat_request_files(repeat_path, repeat_dir, sample_ids)
    items_dir = out_dir / "items"
    items_dir.mkdir(parents=True, exist_ok=True)
    items: list[JsonDict] = []

    print(
        "REPEAT DISCOVERY: "
        f"samples={len(request_paths)} repeat_count={repeat_count}"
    )
    for request_path in request_paths:
        item = _run_repeat_for_sample(
            request_path,
            repeat_count=repeat_count,
            timeout=timeout,
            bridge_factory=bridge_factory,
        )
        items.append(item)
        output_path = items_dir / f"{sample_id(request_path)}_repeat.json"
        with output_path.open("w", encoding="utf-8") as file:
            json.dump(item, file, ensure_ascii=False, indent=2)

    summary = build_repeatability_summary(items)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "repeatability_summary.json"
    summary["output_path"] = str(summary_path)
    with summary_path.open("w", encoding="utf-8") as file:
        json.dump(summary, file, ensure_ascii=False, indent=2)
    return summary


def compare_solver_requests_resident(
    *,
    resident_path: Path | None,
    resident_dir: Path | None,
    sample_ids: list[str] | None,
    repeat_count: int,
    timeout: float,
    out_dir: Path,
    bridge_factory: BridgeFactory = PostflopSolverBridge,
) -> JsonDict:
    """Run Solver requests through one resident bridge process."""
    request_paths = discover_repeat_request_files(
        resident_path,
        resident_dir,
        sample_ids,
    )
    items_dir = out_dir / "items"
    items_dir.mkdir(parents=True, exist_ok=True)
    bridge = bridge_factory()
    start_started = time.perf_counter()
    try:
        bridge.start()
    except Exception:
        bridge.stop()
        raise
    start_ms = int((time.perf_counter() - start_started) * 1000)
    items: list[JsonDict] = []
    try:
        print(
            "RESIDENT DISCOVERY: "
            f"samples={len(request_paths)} repeat_count={repeat_count}"
        )
        first_run = True
        for request_path in request_paths:
            item = _run_resident_for_sample(
                request_path,
                repeat_count=repeat_count,
                timeout=timeout,
                bridge=bridge,
                initial_start_ms=start_ms if first_run else None,
            )
            first_run = False
            items.append(item)
            output_path = items_dir / f"{sample_id(request_path)}_resident.json"
            with output_path.open("w", encoding="utf-8") as file:
                json.dump(item, file, ensure_ascii=False, indent=2)
    finally:
        bridge.stop()

    summary = build_resident_timing_summary(items, start_ms)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "resident_timing_summary.json"
    summary["output_path"] = str(summary_path)
    with summary_path.open("w", encoding="utf-8") as file:
        json.dump(summary, file, ensure_ascii=False, indent=2)
    return summary


def compare_solver_requests_teacher(
    *,
    teacher_path: Path | None,
    teacher_dir: Path | None,
    sample_ids: list[str] | None,
    teacher_profile: str,
    timeout: float,
    out_dir: Path,
    bridge_factory: BridgeFactory = PostflopSolverBridge,
) -> JsonDict:
    """Create high-precision teacher Solver results for selected requests."""
    request_paths = discover_repeat_request_files(
        teacher_path,
        teacher_dir,
        sample_ids,
    )
    items_dir = out_dir / "items"
    items_dir.mkdir(parents=True, exist_ok=True)
    items: list[JsonDict] = []
    print(
        "TEACHER DISCOVERY: "
        f"samples={len(request_paths)} profile={teacher_profile}"
    )
    for request_path in request_paths:
        item = _run_teacher_for_sample(
            request_path,
            teacher_profile=teacher_profile,
            timeout=timeout,
            bridge_factory=bridge_factory,
        )
        items.append(item)
        output_path = items_dir / f"{sample_id(request_path)}_teacher_{teacher_profile}.json"
        with output_path.open("w", encoding="utf-8") as file:
            json.dump(item, file, ensure_ascii=False, indent=2)

    summary = build_teacher_summary(items, teacher_profile)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "teacher_summary.json"
    summary["output_path"] = str(summary_path)
    with summary_path.open("w", encoding="utf-8") as file:
        json.dump(summary, file, ensure_ascii=False, indent=2)
    return summary


def _run_teacher_for_sample(
    request_path: Path,
    *,
    teacher_profile: str,
    timeout: float,
    bridge_factory: BridgeFactory,
) -> JsonDict:
    """Run one teacher request and return a saved item dictionary."""
    primary_request = load_solver_request(request_path)
    teacher_request = build_teacher_request(primary_request, teacher_profile)
    result = run_solver_request_payload(
        teacher_request,
        path_label=f"generated_teacher_{teacher_profile}_from:{request_path}",
        timeout=timeout,
        bridge_factory=bridge_factory,
    )
    probabilities = result.get("probabilities")
    if not isinstance(probabilities, dict):
        probabilities = {}
    probability_info = probability_summary(probabilities)
    action = result.get("action")
    amount = result.get("amount")
    return {
        "sample_id": sample_id(request_path),
        "teacher_profile": teacher_profile,
        "source_request": str(request_path),
        "request_config": teacher_request_config(teacher_profile),
        "success": result["success"],
        "elapsed_ms": result["elapsed_ms"],
        "action": action,
        "amount": amount,
        "probabilities": probabilities,
        "top_action": probability_info["top_action"],
        "top_probability": probability_info["top_probability"],
        "second_action": probability_info["second_action"],
        "second_probability": probability_info["second_probability"],
        "top_margin": probability_info["top_margin"],
        "error": result["error"],
        "teacher_action": action,
        "teacher_amount": amount,
    }


def build_teacher_summary(items: list[JsonDict], profile: str) -> JsonDict:
    """Aggregate teacher Solver item results."""
    elapsed_values = [
        int(item["elapsed_ms"])
        for item in items
        if item.get("elapsed_ms") is not None
    ]
    success_count = sum(1 for item in items if item.get("success") is True)
    error_count = sum(1 for item in items if item.get("success") is False)
    return {
        "total_samples": len(items),
        "success_count": success_count,
        "error_count": error_count,
        "avg_elapsed_ms": _average(elapsed_values),
        "max_elapsed_ms": max(elapsed_values) if elapsed_values else None,
        "profile": profile,
        "items": [
            {
                "sample_id": item["sample_id"],
                "success": item["success"],
                "elapsed_ms": item["elapsed_ms"],
                "action": item["action"],
                "amount": item["amount"],
                "top_action": item["top_action"],
                "top_probability": item["top_probability"],
                "top_margin": item["top_margin"],
                "error": item["error"],
            }
            for item in items
        ],
    }


def compare_solver_requests_single_size(
    *,
    single_size_path: Path | None,
    single_size_dir: Path | None,
    phase: str,
    sample_ids: list[str] | None,
    timeout: float,
    out_dir: Path,
    bridge_factory: BridgeFactory = PostflopSolverBridge,
) -> JsonDict:
    """Run single-size Solver diagnostics for selected HU flop requests."""
    request_paths = discover_single_size_request_files(
        single_size_path,
        single_size_dir,
        phase,
        sample_ids,
    )
    items_dir = out_dir / "items"
    items_dir.mkdir(parents=True, exist_ok=True)
    items: list[JsonDict] = []
    print(
        "SINGLE-SIZE DISCOVERY: "
        f"samples={len(request_paths)} profiles={len(SINGLE_SIZE_PROFILES)}"
    )
    for request_path in request_paths:
        item = _run_single_size_for_sample(
            request_path,
            timeout=timeout,
            bridge_factory=bridge_factory,
        )
        items.append(item)
        output_path = items_dir / f"{sample_id(request_path)}_single_size.json"
        with output_path.open("w", encoding="utf-8") as file:
            json.dump(item, file, ensure_ascii=False, indent=2)

    summary = build_single_size_summary(items)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "single_size_summary.json"
    summary["output_path"] = str(summary_path)
    with summary_path.open("w", encoding="utf-8") as file:
        json.dump(summary, file, ensure_ascii=False, indent=2)
    return summary


def discover_single_size_request_files(
    single_size_path: Path | None,
    single_size_dir: Path | None,
    phase: str,
    sample_ids: list[str] | None,
) -> list[Path]:
    """Return request files for single-size mode."""
    if single_size_path is not None:
        return [single_size_path]
    if single_size_dir is None:
        return []
    paths = discover_primary_request_files(single_size_dir, phase)
    if not sample_ids:
        return paths
    selected = set(sample_ids)
    return [path for path in paths if sample_id(path) in selected]


def _run_single_size_for_sample(
    request_path: Path,
    *,
    timeout: float,
    bridge_factory: BridgeFactory,
) -> JsonDict:
    """Run all single-size profiles for one request."""
    primary_request = load_solver_request(request_path)
    profiles: list[JsonDict] = []
    for profile_id, config in SINGLE_SIZE_PROFILES.items():
        request = build_single_size_request(primary_request, profile_id)
        result = run_solver_request_payload(
            request,
            path_label=f"generated_single_size_{profile_id}_from:{request_path}",
            timeout=timeout,
            bridge_factory=bridge_factory,
        )
        probabilities = result.get("probabilities")
        if not isinstance(probabilities, dict):
            probabilities = {}
        probability_info = probability_summary(probabilities)
        action = result.get("action")
        profiles.append(
            {
                "profile_id": profile_id,
                "bet_size": config["bet_size"],
                "success": result["success"],
                "elapsed_ms": result["elapsed_ms"],
                "under_15s": _under_15s(result),
                "action": action,
                "amount": result.get("amount"),
                "probabilities": probabilities,
                "top_action": probability_info["top_action"],
                "top_probability": probability_info["top_probability"],
                "second_action": probability_info["second_action"],
                "second_probability": probability_info["second_probability"],
                "top_margin": probability_info["top_margin"],
                "aggressive_action": _is_aggressive_action(action),
                "error": result["error"],
            }
        )
    return {
        "sample_id": sample_id(request_path),
        "source_request": str(request_path),
        "profiles": profiles,
    }


def build_single_size_summary(items: list[JsonDict]) -> JsonDict:
    """Aggregate single-size Solver diagnostics across samples and profiles."""
    profile_rows: dict[str, list[JsonDict]] = {
        profile_id: [] for profile_id in SINGLE_SIZE_PROFILES
    }
    for item in items:
        for row in item.get("profiles", []):
            profile_id = row.get("profile_id")
            if isinstance(profile_id, str):
                profile_rows.setdefault(profile_id, []).append(row)

    planned_runs = sum(len(rows) for rows in profile_rows.values())
    success_count = sum(
        1 for rows in profile_rows.values() for row in rows if row.get("success") is True
    )
    error_count = sum(
        1 for rows in profile_rows.values() for row in rows if row.get("success") is False
    )
    return {
        "total_samples": len(items),
        "profile_count": len(SINGLE_SIZE_PROFILES),
        "planned_runs": planned_runs,
        "success_count": success_count,
        "error_count": error_count,
        "profile_summary": {
            profile_id: _single_size_profile_summary(rows)
            for profile_id, rows in profile_rows.items()
        },
        "sample_summary": [_single_size_sample_summary(item) for item in items],
    }


def _single_size_profile_summary(rows: list[JsonDict]) -> JsonDict:
    """Aggregate single-size rows for one profile."""
    elapsed_values = [
        int(row["elapsed_ms"])
        for row in rows
        if row.get("elapsed_ms") is not None
    ]
    return {
        "success_count": sum(1 for row in rows if row.get("success") is True),
        "error_count": sum(1 for row in rows if row.get("success") is False),
        "under_15s_rate": _bool_rate(rows, "under_15s"),
        "aggressive_action_count": sum(
            1 for row in rows if row.get("aggressive_action") is True
        ),
        "check_count": sum(1 for row in rows if row.get("action") == "CHECK"),
        "call_count": sum(1 for row in rows if row.get("action") == "CALL"),
        "fold_count": sum(1 for row in rows if row.get("action") == "FOLD"),
        "bet_count": sum(1 for row in rows if row.get("action") == "BET"),
        "raise_count": sum(1 for row in rows if row.get("action") == "RAISE"),
        "all_in_count": sum(1 for row in rows if row.get("action") == "ALL_IN"),
        "avg_elapsed_ms": _average(elapsed_values),
    }


def _single_size_sample_summary(item: JsonDict) -> JsonDict:
    """Summarize aggressive/passive profile directions for one sample."""
    aggressive_profiles = [
        row["profile_id"]
        for row in item.get("profiles", [])
        if row.get("success") is True and row.get("aggressive_action") is True
    ]
    passive_profiles = [
        row["profile_id"]
        for row in item.get("profiles", [])
        if row.get("success") is True and row.get("aggressive_action") is False
    ]
    error_profiles = [
        row["profile_id"]
        for row in item.get("profiles", [])
        if row.get("success") is False
    ]
    successful_count = len(aggressive_profiles) + len(passive_profiles)
    all_profiles_same_direction = not error_profiles and successful_count > 0 and (
        len(aggressive_profiles) == successful_count
        or len(passive_profiles) == successful_count
    )
    return {
        "sample_id": item["sample_id"],
        "aggressive_profiles": aggressive_profiles,
        "passive_profiles": passive_profiles,
        "error_profiles": error_profiles,
        "all_profiles_same_direction": all_profiles_same_direction,
    }


def _is_aggressive_action(action: object) -> bool:
    """Return whether an action is BET / RAISE / ALL_IN."""
    return action in {"BET", "RAISE", "ALL_IN"}


def compare_solver_requests_sizing_teacher(
    *,
    sizing_teacher_path: Path | None,
    sizing_teacher_dir: Path | None,
    out_dir: Path,
) -> JsonDict:
    """Build sizing teacher labels from single-size diagnostic items."""
    item_paths = discover_sizing_teacher_item_files(
        sizing_teacher_path,
        sizing_teacher_dir,
    )
    items_dir = out_dir / "items"
    items_dir.mkdir(parents=True, exist_ok=True)
    items: list[JsonDict] = []
    print(f"SIZING TEACHER DISCOVERY: items={len(item_paths)}")
    for item_path in item_paths:
        source_item = load_json_object(item_path)
        teacher_item = build_sizing_teacher_item(source_item, source_path=item_path)
        items.append(teacher_item)
        output_path = items_dir / f"{teacher_item['sample_id']}_sizing_teacher.json"
        with output_path.open("w", encoding="utf-8") as file:
            json.dump(teacher_item, file, ensure_ascii=False, indent=2)

    summary = build_sizing_teacher_summary(items)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "sizing_teacher_summary.json"
    summary["output_path"] = str(summary_path)
    with summary_path.open("w", encoding="utf-8") as file:
        json.dump(summary, file, ensure_ascii=False, indent=2)
    return summary


def discover_sizing_teacher_item_files(
    sizing_teacher_path: Path | None,
    sizing_teacher_dir: Path | None,
) -> list[Path]:
    """Return single-size item files for sizing teacher mode."""
    if sizing_teacher_path is not None:
        return [sizing_teacher_path]
    if sizing_teacher_dir is None:
        return []
    items_dir = sizing_teacher_dir / "items"
    search_dir = items_dir if items_dir.exists() else sizing_teacher_dir
    return sorted(search_dir.glob("*_single_size.json"))


def load_json_object(path: Path) -> JsonDict:
    """Load a JSON object from disk."""
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise ValueError(f"JSON must be an object: {path}")
    return payload


def build_sizing_teacher_item(
    single_size_item: JsonDict,
    *,
    source_path: Path | None = None,
) -> JsonDict:
    """Build one sizing teacher label from a single-size diagnostic item."""
    profile_actions = _profile_actions(single_size_item)
    aggressive_profiles = [
        profile
        for profile in SINGLE_SIZE_PROFILES
        if _is_aggressive_action(profile_actions.get(profile))
    ]
    passive_profiles = [
        profile
        for profile in SINGLE_SIZE_PROFILES
        if profile_actions.get(profile) in {"CHECK", "CALL", "FOLD"}
    ]
    standard_aggressive = [
        profile
        for profile in STANDARD_SINGLE_SIZE_PROFILES
        if profile in aggressive_profiles
    ]
    allin_aggressive = "single_allin" in aggressive_profiles
    teacher_label = _sizing_teacher_label(standard_aggressive, profile_actions)
    allowed_sizing_types = [
        SIZING_TYPE_BY_PROFILE[profile] for profile in standard_aggressive
    ]
    if allin_aggressive:
        allowed_sizing_types.append(SIZING_TYPE_BY_PROFILE["single_allin"])
    return {
        "sample_id": str(single_size_item.get("sample_id", "unknown")),
        "source_item": str(source_path) if source_path is not None else None,
        "profile_actions": profile_actions,
        "aggressive_profiles": aggressive_profiles,
        "passive_profiles": passive_profiles,
        "teacher_label": teacher_label,
        "preferred_sizing_bucket": _preferred_sizing_bucket(teacher_label),
        "allowed_sizing_types": allowed_sizing_types,
        "allin_aggressive": allin_aggressive,
    }


def build_sizing_teacher_summary(items: list[JsonDict]) -> JsonDict:
    """Aggregate sizing teacher labels."""
    label_counts: dict[str, int] = {}
    allowed_counts: dict[str, int] = {}
    for item in items:
        label = str(item.get("teacher_label", "unknown"))
        label_counts[label] = label_counts.get(label, 0) + 1
        for sizing_type in item.get("allowed_sizing_types", []):
            sizing_text = str(sizing_type)
            allowed_counts[sizing_text] = allowed_counts.get(sizing_text, 0) + 1
    return {
        "total_samples": len(items),
        "label_counts": label_counts,
        "allin_aggressive_count": sum(
            1 for item in items if item.get("allin_aggressive") is True
        ),
        "allowed_sizing_type_counts": allowed_counts,
        "items": [
            {
                "sample_id": item["sample_id"],
                "teacher_label": item["teacher_label"],
                "preferred_sizing_bucket": item["preferred_sizing_bucket"],
                "allowed_sizing_types": item["allowed_sizing_types"],
                "allin_aggressive": item["allin_aggressive"],
            }
            for item in items
        ],
    }


def _profile_actions(single_size_item: JsonDict) -> dict[str, str | None]:
    """Return action by single-size profile id."""
    actions: dict[str, str | None] = {
        profile_id: None for profile_id in SINGLE_SIZE_PROFILES
    }
    for profile in single_size_item.get("profiles", []):
        if not isinstance(profile, dict):
            continue
        profile_id = profile.get("profile_id")
        if isinstance(profile_id, str) and profile_id in actions:
            action = profile.get("action")
            actions[profile_id] = str(action).upper() if action is not None else None
    return actions


def _sizing_teacher_label(
    standard_aggressive: list[str],
    profile_actions: dict[str, str | None],
) -> str:
    """Return a teacher label from standard single-size aggressive profiles."""
    if any(profile_actions.get(profile) is None for profile in SINGLE_SIZE_PROFILES):
        return "unknown"
    aggressive_set = set(standard_aggressive)
    if aggressive_set == {"single_33", "single_50"}:
        return "small_only_aggressive"
    if aggressive_set == {"single_33"}:
        return "tiny_only_aggressive"
    if aggressive_set == set(STANDARD_SINGLE_SIZE_PROFILES):
        return "all_standard_aggressive"
    if aggressive_set == {"single_33", "single_50", "single_60"}:
        return "medium_or_small_aggressive"
    if not aggressive_set:
        return "passive_all_standard"
    return "mixed_non_monotonic"


def _preferred_sizing_bucket(teacher_label: str) -> str:
    """Map a teacher label to a compact sizing bucket."""
    buckets = {
        "small_only_aggressive": "small",
        "tiny_only_aggressive": "tiny",
        "all_standard_aggressive": "broad",
        "medium_or_small_aggressive": "small_to_medium",
        "passive_all_standard": "none",
        "mixed_non_monotonic": "mixed",
    }
    return buckets.get(teacher_label, "unknown")


def compare_solver_requests_llm_sizing(
    *,
    llm_sizing_path: Path | None,
    llm_sizing_dir: Path | None,
    sizing_teacher_path: Path | None,
    sizing_teacher_dir: Path | None,
    phase: str,
    sample_ids: list[str] | None,
    llm_model: str | None,
    timeout: float,
    out_dir: Path,
    bridge_factory: BridgeFactory = PostflopSolverBridge,
    llm_caller: LLMCaller | None = None,
) -> JsonDict:
    """Run LLM sizing diagnostics against sizing teacher labels."""
    request_paths = discover_llm_sizing_request_files(
        llm_sizing_path,
        llm_sizing_dir,
        phase,
        sample_ids,
    )
    teacher_items = load_sizing_teacher_items(sizing_teacher_path, sizing_teacher_dir)
    items_dir = out_dir / "items"
    items_dir.mkdir(parents=True, exist_ok=True)
    items: list[JsonDict] = []
    model = llm_model or os.getenv("LLM_MODEL_DEFAULT") or DEFAULT_LLM_MODEL
    caller = llm_caller or call_openrouter_llm
    print(f"LLM SIZING DISCOVERY: samples={len(request_paths)} model={model}")
    for request_path in request_paths:
        current_sample_id = sample_id(request_path)
        teacher_item = teacher_items.get(current_sample_id)
        if teacher_item is None:
            item = _missing_sizing_teacher_item(request_path, model)
        else:
            item = _run_llm_sizing_for_sample(
                request_path,
                teacher_item=teacher_item,
                llm_model=model,
                timeout=timeout,
                bridge_factory=bridge_factory,
                llm_caller=caller,
            )
        items.append(item)
        output_path = items_dir / f"{current_sample_id}_llm_sizing.json"
        with output_path.open("w", encoding="utf-8") as file:
            json.dump(item, file, ensure_ascii=False, indent=2)

    summary = build_llm_sizing_summary(items, model)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "llm_sizing_summary.json"
    summary["output_path"] = str(summary_path)
    with summary_path.open("w", encoding="utf-8") as file:
        json.dump(summary, file, ensure_ascii=False, indent=2)
    return summary


def discover_llm_sizing_request_files(
    llm_sizing_path: Path | None,
    llm_sizing_dir: Path | None,
    phase: str,
    sample_ids: list[str] | None,
) -> list[Path]:
    """Return request files for LLM sizing mode."""
    if llm_sizing_path is not None:
        return [llm_sizing_path]
    if llm_sizing_dir is None:
        return []
    paths = discover_primary_request_files(llm_sizing_dir, phase)
    if not sample_ids:
        return paths
    selected = set(sample_ids)
    return [path for path in paths if sample_id(path) in selected]


def load_sizing_teacher_items(
    sizing_teacher_path: Path | None,
    sizing_teacher_dir: Path | None,
) -> dict[str, JsonDict]:
    """Load sizing teacher items keyed by sample id."""
    paths = discover_named_json_files(
        sizing_teacher_path,
        sizing_teacher_dir,
        "*_sizing_teacher.json",
    )
    items: dict[str, JsonDict] = {}
    for path in paths:
        item = load_json_object(path)
        sample_id_text = item.get("sample_id")
        if isinstance(sample_id_text, str):
            items[sample_id_text] = item
    return items


def discover_named_json_files(
    item_path: Path | None,
    item_dir: Path | None,
    pattern: str,
) -> list[Path]:
    """Return JSON files from an optional single path or directory."""
    if item_path is not None:
        return [item_path]
    if item_dir is None:
        return []
    items_dir = item_dir / "items"
    search_dir = items_dir if items_dir.exists() else item_dir
    return sorted(search_dir.glob(pattern))


def _missing_sizing_teacher_item(request_path: Path, model: str) -> JsonDict:
    """Return an error item when sizing teacher data is missing."""
    return {
        "sample_id": sample_id(request_path),
        "source_request": str(request_path),
        "llm_model": model,
        "llm_success": False,
        "llm_elapsed_ms": 0,
        "llm_error": "Sizing teacher item missing",
        "teacher_label": "unknown",
        "allowed_sizing_types": [],
        "allin_aggressive": False,
        "llm_action": None,
        "llm_sizing_type": None,
        "llm_sizing_bucket": None,
        "sizing_allowed_match": False,
        "allin_violation": False,
        "passive_teacher_aggressive_violation": False,
        "sizing_type_valid": False,
        "teacher_alignment": False,
    }


def _run_llm_sizing_for_sample(
    request_path: Path,
    *,
    teacher_item: JsonDict,
    llm_model: str,
    timeout: float,
    bridge_factory: BridgeFactory,
    llm_caller: LLMCaller,
) -> JsonDict:
    """Run one LLM sizing diagnostic sample."""
    payload = load_solver_payload(request_path)
    primary_request = dict(payload["request"])
    baseline = run_solver_request_payload(
        primary_request,
        path_label=str(request_path),
        timeout=timeout,
        bridge_factory=bridge_factory,
    )
    legal_actions = legal_actions_for_solver_request(primary_request)
    prompt = build_llm_sizing_prompt(payload, baseline, legal_actions, teacher_item)
    llm_result = run_llm_diagnostic_request(
        prompt,
        model=llm_model,
        timeout=timeout,
        llm_caller=llm_caller,
    )
    probabilities = baseline.get("probabilities")
    if not isinstance(probabilities, dict):
        probabilities = {}
    probability_info = probability_summary(probabilities)
    decision = llm_result.get("decision")
    if not isinstance(decision, dict):
        decision = {}
    evaluation = evaluate_llm_sizing_decision(teacher_item, decision, llm_result)
    return {
        "sample_id": sample_id(request_path),
        "source_request": str(request_path),
        "source_teacher": teacher_item.get("source_item"),
        "llm_model": llm_model,
        "baseline_success": baseline["success"],
        "baseline_elapsed_ms": baseline["elapsed_ms"],
        "baseline_action": baseline["action"],
        "baseline_amount": baseline["amount"],
        "baseline_probabilities": probabilities,
        "baseline_top_action": probability_info["top_action"],
        "baseline_top_probability": probability_info["top_probability"],
        "baseline_second_action": probability_info["second_action"],
        "baseline_second_probability": probability_info["second_probability"],
        "baseline_top_margin": probability_info["top_margin"],
        "primary_margin_class": _margin_class(probability_info["top_margin"]),
        "teacher_label": teacher_item.get("teacher_label"),
        "preferred_sizing_bucket": teacher_item.get("preferred_sizing_bucket"),
        "allowed_sizing_types": teacher_item.get("allowed_sizing_types", []),
        "allin_aggressive": teacher_item.get("allin_aggressive", False),
        "profile_actions": teacher_item.get("profile_actions", {}),
        "legal_actions": legal_actions,
        "llm_success": llm_result["success"],
        "llm_elapsed_ms": llm_result["elapsed_ms"],
        "llm_action": decision.get("action"),
        "llm_amount": decision.get("amount"),
        "llm_sizing_type": decision.get("sizing_type"),
        "llm_sizing_bucket": decision.get("sizing_bucket"),
        "llm_confidence": decision.get("confidence"),
        "llm_reason": decision.get("reason"),
        "llm_risk_flags": decision.get("risk_flags", []),
        "llm_error": llm_result.get("error"),
        "llm_status_code": llm_result.get("status_code"),
        "llm_response_body": llm_result.get("response_body"),
        "prompt": prompt,
        **evaluation,
    }


def build_llm_sizing_prompt(
    payload: JsonDict,
    baseline: JsonDict,
    legal_actions: list[str],
    teacher_item: JsonDict,
) -> str:
    """Build a diagnostics-only LLM sizing prompt."""
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    request = payload.get("request") if isinstance(payload.get("request"), dict) else {}
    probabilities = baseline.get("probabilities")
    if not isinstance(probabilities, dict):
        probabilities = {}
    prob_info = probability_summary(probabilities)
    context = {
        "task": "HU flop sizing teacher diagnostic",
        "board": request.get("board"),
        "hero_cards": meta.get("hero_cards"),
        "pot": request.get("starting_pot"),
        "starting_pot": request.get("starting_pot"),
        "effective_stack": request.get("effective_stack"),
        "spr": meta.get("spr"),
        "hero_position": meta.get("hero_position"),
        "hero_is_ip": meta.get("hero_is_ip"),
        "actions_played": request.get("actions_played", []),
        "legal_actions": legal_actions,
        "primary_solver_action": baseline.get("action"),
        "primary_solver_probabilities": probabilities,
        "primary_top_margin": prob_info["top_margin"],
        "primary_margin_class": _margin_class(prob_info["top_margin"]),
        "teacher_label": teacher_item.get("teacher_label"),
        "preferred_sizing_bucket": teacher_item.get("preferred_sizing_bucket"),
        "allowed_sizing_types": teacher_item.get("allowed_sizing_types", []),
        "allin_aggressive": teacher_item.get("allin_aggressive", False),
        "profile_actions": teacher_item.get("profile_actions", {}),
    }
    return (
        "You are choosing a HU flop sizing bucket based on single-size "
        "solver teacher data.\n\n"
        "Rules:\n"
        "- For this sizing diagnostic, the single-size solver teacher data "
        "is the primary anchor.\n"
        "- The primary 60% solver action is reference information only.\n"
        "- Do not follow primary CHECK/CALL/FOLD if the teacher data indicates "
        "allowed aggressive sizing.\n"
        "- Choose sizing_type only from allowed_sizing_types.\n"
        "- If allowed_sizing_types is not empty:\n"
        "  - You must choose one sizing_type from allowed_sizing_types.\n"
        "  - Do not choose sizing_type=\"none\".\n"
        "  - Do not choose CHECK/CALL/FOLD solely because the primary solver "
        "action was passive.\n"
        "  - Choose the action direction consistent with the sizing_type:\n"
        "    - bet_33 / bet_50 / bet_60 / bet_75 => BET\n"
        "    - raise_33 / raise_50 / raise_60 / raise_75 => RAISE\n"
        "    - all_in => ALL_IN\n"
        "- If allowed_sizing_types is empty, choose action from the primary "
        "solver direction and sizing_type=\"none\".\n"
        "- If allowed_sizing_types is empty, do not invent BET/RAISE/ALL_IN.\n"
        "- Do not choose all_in unless allin_aggressive=true.\n"
        "- If teacher_label is passive_all_standard, allowed_sizing_types is "
        "empty and sizing_type must be none.\n"
        "- If teacher_label is tiny_only_aggressive, allowed_sizing_types "
        "should contain only bet_33 or equivalent raise_33; choose the 33 bucket.\n"
        "- If teacher_label is small_only_aggressive, choose from 33 or 50 "
        "bucket; prefer 33 on wet/dynamic/high-SPR boards unless reason supports 50.\n"
        "- If teacher_label is medium_or_small_aggressive, prefer bet_33 / "
        "bet_50 / bet_60.\n"
        "- If teacher_label is all_standard_aggressive, choose the sizing "
        "that best fits board texture / SPR / position; do not choose none.\n"
        "- If teacher_label is mixed_non_monotonic, choose only from "
        "allowed_sizing_types, explain why the chosen allowed bucket is more "
        "suitable than the other allowed bucket, and do not choose none.\n"
        "- Output JSON only.\n"
        "Allowed JSON keys: action, amount, sizing_type, sizing_bucket, "
        "confidence, reason, risk_flags.\n"
        "Allowed sizing_type values: none, bet_33, bet_50, bet_60, bet_75, "
        "raise_33, raise_50, raise_60, raise_75, all_in.\n"
        f"Context JSON:\n{json.dumps(context, ensure_ascii=False, indent=2)}"
    )


def evaluate_llm_sizing_decision(
    teacher_item: JsonDict,
    decision: JsonDict,
    llm_result: JsonDict | None = None,
) -> JsonDict:
    """Evaluate one LLM sizing decision against a teacher label."""
    sizing_type = str(decision.get("sizing_type", "")).lower()
    llm_action = str(decision.get("action", "")).upper()
    allowed_sizing_types = [
        str(value).lower() for value in teacher_item.get("allowed_sizing_types", [])
    ]
    teacher_label = str(teacher_item.get("teacher_label", "unknown"))
    allin_aggressive = bool(teacher_item.get("allin_aggressive", False))
    sizing_type_valid = sizing_type in LLM_ALLOWED_SIZING_TYPES
    sizing_allowed_match = _sizing_allowed_match(sizing_type, allowed_sizing_types)
    allin_violation = sizing_type == "all_in" and not allin_aggressive
    passive_teacher_aggressive_violation = (
        teacher_label == "passive_all_standard"
        and llm_action in {"BET", "RAISE", "ALL_IN"}
    )
    teacher_alignment = (
        sizing_allowed_match
        and not allin_violation
        and not passive_teacher_aggressive_violation
        and sizing_type_valid
    )
    return {
        "sizing_allowed_match": sizing_allowed_match,
        "allin_violation": allin_violation,
        "passive_teacher_aggressive_violation": passive_teacher_aggressive_violation,
        "sizing_type_valid": sizing_type_valid,
        "teacher_alignment": teacher_alignment,
        "under_15s": _under_15s(llm_result) if llm_result is not None else None,
    }


def build_llm_sizing_summary(items: list[JsonDict], model: str) -> JsonDict:
    """Aggregate LLM sizing diagnostic results."""
    success_items = [item for item in items if item.get("llm_success") is True]
    label_summary: dict[str, JsonDict] = {}
    for item in items:
        label = str(item.get("teacher_label", "unknown"))
        stats = label_summary.setdefault(label, {"total": 0, "teacher_alignment_count": 0})
        stats["total"] += 1
        if item.get("teacher_alignment") is True:
            stats["teacher_alignment_count"] += 1
    return {
        "total_samples": len(items),
        "success_count": len(success_items),
        "error_count": sum(1 for item in items if item.get("llm_success") is False),
        "under_15s_rate": _bool_rate(items, "under_15s"),
        "teacher_alignment_rate": _bool_rate(success_items, "teacher_alignment"),
        "sizing_allowed_match_count": sum(
            1 for item in items if item.get("sizing_allowed_match") is True
        ),
        "allin_violation_count": sum(
            1 for item in items if item.get("allin_violation") is True
        ),
        "passive_teacher_aggressive_violation_count": sum(
            1
            for item in items
            if item.get("passive_teacher_aggressive_violation") is True
        ),
        "sizing_type_invalid_count": sum(
            1 for item in items if item.get("sizing_type_valid") is False
        ),
        "label_summary": label_summary,
        "model": model,
        "items": [
            {
                "sample_id": item["sample_id"],
                "teacher_label": item.get("teacher_label"),
                "allowed_sizing_types": item.get("allowed_sizing_types", []),
                "llm_success": item.get("llm_success"),
                "llm_action": item.get("llm_action"),
                "llm_sizing_type": item.get("llm_sizing_type"),
                "llm_sizing_bucket": item.get("llm_sizing_bucket"),
                "sizing_allowed_match": item.get("sizing_allowed_match"),
                "allin_violation": item.get("allin_violation"),
                "passive_teacher_aggressive_violation": item.get(
                    "passive_teacher_aggressive_violation"
                ),
                "sizing_type_valid": item.get("sizing_type_valid"),
                "teacher_alignment": item.get("teacher_alignment"),
                "error": item.get("llm_error"),
            }
            for item in items
        ],
    }


def _sizing_allowed_match(sizing_type: str, allowed_sizing_types: list[str]) -> bool:
    """Return whether sizing type matches teacher allowed buckets."""
    if not allowed_sizing_types:
        return sizing_type == "none"
    if sizing_type in allowed_sizing_types:
        return True
    sizing_bucket = _sizing_bucket_token(sizing_type)
    return any(_sizing_bucket_token(allowed) == sizing_bucket for allowed in allowed_sizing_types)


def _sizing_bucket_token(sizing_type: str) -> str:
    """Normalize bet_N and raise_N into the same bucket token."""
    text = sizing_type.lower()
    if text.startswith("bet_") or text.startswith("raise_"):
        return text.split("_", 1)[1]
    return text


def compare_solver_requests_llm_blind(
    *,
    llm_blind_path: Path | None,
    llm_blind_dir: Path | None,
    sizing_teacher_path: Path | None,
    sizing_teacher_dir: Path | None,
    phase: str,
    sample_ids: list[str] | None,
    llm_model: str | None,
    timeout: float,
    out_dir: Path,
    blind_profile: str = "baseline",
    bridge_factory: BridgeFactory = PostflopSolverBridge,
    llm_caller: LLMCaller | None = None,
) -> JsonDict:
    """Run blind LLM diagnostics and compare against Solver/teacher data."""
    request_paths = discover_llm_sizing_request_files(
        llm_blind_path,
        llm_blind_dir,
        phase,
        sample_ids,
    )
    teacher_items = load_sizing_teacher_items(sizing_teacher_path, sizing_teacher_dir)
    items_dir = out_dir / "items"
    items_dir.mkdir(parents=True, exist_ok=True)
    items: list[JsonDict] = []
    model = llm_model or os.getenv("LLM_MODEL_DEFAULT") or DEFAULT_LLM_MODEL
    caller = llm_caller or call_openrouter_llm
    print(
        f"BLIND LLM DISCOVERY: samples={len(request_paths)} "
        f"model={model} profile={blind_profile}"
    )
    for request_path in request_paths:
        current_sample_id = sample_id(request_path)
        teacher_item = teacher_items.get(current_sample_id)
        if teacher_item is None:
            item = _missing_blind_teacher_item(request_path, model, blind_profile)
        else:
            item = _run_llm_blind_for_sample(
                request_path,
                teacher_item=teacher_item,
                llm_model=model,
                timeout=timeout,
                blind_profile=blind_profile,
                bridge_factory=bridge_factory,
                llm_caller=caller,
            )
        items.append(item)
        output_path = items_dir / f"{current_sample_id}_blind_llm.json"
        with output_path.open("w", encoding="utf-8") as file:
            json.dump(item, file, ensure_ascii=False, indent=2)

    summary = build_llm_blind_summary(items, model, blind_profile)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "llm_blind_summary.json"
    summary["output_path"] = str(summary_path)
    with summary_path.open("w", encoding="utf-8") as file:
        json.dump(summary, file, ensure_ascii=False, indent=2)
    return summary


def compare_solver_requests_llm_blind_repeat(
    *,
    llm_blind_repeat_path: Path | None,
    llm_blind_repeat_dir: Path | None,
    sizing_teacher_path: Path | None,
    sizing_teacher_dir: Path | None,
    phase: str,
    sample_ids: list[str] | None,
    llm_model: str | None,
    timeout: float,
    blind_profile: str,
    repeat_count: int,
    out_dir: Path,
    bridge_factory: BridgeFactory = PostflopSolverBridge,
    llm_caller: LLMCaller | None = None,
) -> JsonDict:
    """Run blind LLM diagnostics repeatedly for stability checks."""
    request_paths = discover_llm_sizing_request_files(
        llm_blind_repeat_path,
        llm_blind_repeat_dir,
        phase,
        sample_ids,
    )
    teacher_items = load_sizing_teacher_items(sizing_teacher_path, sizing_teacher_dir)
    items_dir = out_dir / "items"
    items_dir.mkdir(parents=True, exist_ok=True)
    items: list[JsonDict] = []
    model = llm_model or os.getenv("LLM_MODEL_DEFAULT") or DEFAULT_LLM_MODEL
    caller = llm_caller or call_openrouter_llm
    print(
        "BLIND LLM REPEAT DISCOVERY: "
        f"samples={len(request_paths)} repeat_count={repeat_count} "
        f"model={model} profile={blind_profile}"
    )
    for request_path in request_paths:
        current_sample_id = sample_id(request_path)
        teacher_item = teacher_items.get(current_sample_id)
        if teacher_item is None:
            item = _missing_blind_repeat_teacher_item(
                request_path,
                model,
                blind_profile,
                repeat_count,
            )
        else:
            item = _run_llm_blind_repeat_for_sample(
                request_path,
                teacher_item=teacher_item,
                llm_model=model,
                timeout=timeout,
                blind_profile=blind_profile,
                repeat_count=repeat_count,
                bridge_factory=bridge_factory,
                llm_caller=caller,
            )
        items.append(item)
        output_path = items_dir / f"{current_sample_id}_blind_repeat.json"
        with output_path.open("w", encoding="utf-8") as file:
            json.dump(item, file, ensure_ascii=False, indent=2)

    summary = build_llm_blind_repeat_summary(items, blind_profile, repeat_count)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "llm_blind_repeat_summary.json"
    summary["output_path"] = str(summary_path)
    with summary_path.open("w", encoding="utf-8") as file:
        json.dump(summary, file, ensure_ascii=False, indent=2)
    return summary


def _missing_blind_teacher_item(
    request_path: Path, model: str, blind_profile: str
) -> JsonDict:
    """Return an error item when blind teacher data is missing."""
    return {
        "sample_id": sample_id(request_path),
        "source_request": str(request_path),
        "llm_model": model,
        "blind_profile": blind_profile,
        "llm_success": False,
        "llm_elapsed_ms": 0,
        "llm_error": "Sizing teacher item missing",
        "baseline_action": None,
        "teacher_label": "unknown",
        "allowed_sizing_types": [],
        "llm_action": None,
        "llm_sizing_type": None,
        "blind_action_match": False,
        "blind_direction_match": False,
        "blind_sizing_allowed_match": False,
        "blind_allin_violation": False,
        "blind_passive_teacher_aggressive_violation": False,
        "blind_teacher_alignment": False,
        "legal_action_valid": None,
        "under_15s": False,
    }


def _missing_blind_repeat_teacher_item(
    request_path: Path,
    model: str,
    blind_profile: str,
    repeat_count: int,
) -> JsonDict:
    """Return an error repeat item when blind teacher data is missing."""
    return {
        "sample_id": sample_id(request_path),
        "source_request": str(request_path),
        "llm_model": model,
        "blind_profile": blind_profile,
        "repeat_count": repeat_count,
        "baseline_action": None,
        "teacher_label": "unknown",
        "allowed_sizing_types": [],
        "runs": [],
        "action_values": [],
        "sizing_type_values": [],
        "teacher_alignment_count": 0,
        "teacher_alignment_rate": 0.0,
        "action_stable": False,
        "sizing_type_stable": False,
        "teacher_alignment_stable": False,
        "allin_violation_count": 0,
        "passive_teacher_aggressive_violation_count": 0,
        "legal_action_invalid_count": 0,
        "error_count": repeat_count,
        "llm_error": "Sizing teacher item missing",
    }


def _run_llm_blind_for_sample(
    request_path: Path,
    *,
    teacher_item: JsonDict,
    llm_model: str,
    timeout: float,
    blind_profile: str,
    bridge_factory: BridgeFactory,
    llm_caller: LLMCaller,
) -> JsonDict:
    """Run one blind LLM decision and evaluate it after the fact."""
    payload = load_solver_payload(request_path)
    primary_request = dict(payload["request"])
    baseline = run_solver_request_payload(
        primary_request,
        path_label=str(request_path),
        timeout=timeout,
        bridge_factory=bridge_factory,
    )
    legal_actions = legal_actions_for_solver_request(primary_request)
    prompt = build_blind_llm_prompt(
        payload,
        legal_actions,
        blind_profile=blind_profile,
    )
    llm_result = run_llm_diagnostic_request(
        prompt,
        model=llm_model,
        timeout=timeout,
        llm_caller=llm_caller,
    )
    decision = llm_result.get("decision")
    if not isinstance(decision, dict):
        decision = {}
    evaluation = evaluate_blind_llm_decision(
        baseline,
        teacher_item,
        decision,
        legal_actions,
        llm_result,
    )
    return {
        "sample_id": sample_id(request_path),
        "source_request": str(request_path),
        "source_teacher": teacher_item.get("source_item"),
        "llm_model": llm_model,
        "blind_profile": blind_profile,
        "llm_success": llm_result["success"],
        "llm_elapsed_ms": llm_result["elapsed_ms"],
        "llm_action": decision.get("action"),
        "llm_amount": decision.get("amount"),
        "llm_sizing_type": decision.get("sizing_type"),
        "llm_confidence": decision.get("confidence"),
        "llm_reason": decision.get("reason"),
        "llm_risk_flags": decision.get("risk_flags", []),
        "llm_error": llm_result.get("error"),
        "llm_status_code": llm_result.get("status_code"),
        "llm_response_body": llm_result.get("response_body"),
        "baseline_action": baseline.get("action"),
        "baseline_amount": baseline.get("amount"),
        "teacher_label": teacher_item.get("teacher_label"),
        "allowed_sizing_types": teacher_item.get("allowed_sizing_types", []),
        "allin_aggressive": teacher_item.get("allin_aggressive", False),
        "prompt": prompt,
        **evaluation,
    }


def _run_llm_blind_repeat_for_sample(
    request_path: Path,
    *,
    teacher_item: JsonDict,
    llm_model: str,
    timeout: float,
    blind_profile: str,
    repeat_count: int,
    bridge_factory: BridgeFactory,
    llm_caller: LLMCaller,
) -> JsonDict:
    """Run one blind LLM prompt repeatedly and summarize stability."""
    payload = load_solver_payload(request_path)
    primary_request = dict(payload["request"])
    baseline = run_solver_request_payload(
        primary_request,
        path_label=str(request_path),
        timeout=timeout,
        bridge_factory=bridge_factory,
    )
    legal_actions = legal_actions_for_solver_request(primary_request)
    prompt = build_blind_llm_prompt(
        payload,
        legal_actions,
        blind_profile=blind_profile,
    )
    runs: list[JsonDict] = []
    for run_index in range(1, repeat_count + 1):
        llm_result = run_llm_diagnostic_request(
            prompt,
            model=llm_model,
            timeout=timeout,
            llm_caller=llm_caller,
        )
        decision = llm_result.get("decision")
        if not isinstance(decision, dict):
            decision = {}
        evaluation = evaluate_blind_llm_decision(
            baseline,
            teacher_item,
            decision,
            legal_actions,
            llm_result,
        )
        runs.append(
            {
                "run_index": run_index,
                "success": llm_result["success"],
                "elapsed_ms": llm_result["elapsed_ms"],
                "llm_action": decision.get("action"),
                "llm_amount": decision.get("amount"),
                "llm_sizing_type": decision.get("sizing_type"),
                "llm_confidence": decision.get("confidence"),
                "llm_reason": decision.get("reason"),
                "llm_error": llm_result.get("error"),
                "llm_status_code": llm_result.get("status_code"),
                **evaluation,
            }
        )

    summary = blind_repeat_item_summary(
        sample_id(request_path),
        runs,
        repeat_count=repeat_count,
    )
    return {
        "sample_id": sample_id(request_path),
        "source_request": str(request_path),
        "source_teacher": teacher_item.get("source_item"),
        "llm_model": llm_model,
        "blind_profile": blind_profile,
        "repeat_count": repeat_count,
        "baseline_action": baseline.get("action"),
        "baseline_amount": baseline.get("amount"),
        "teacher_label": teacher_item.get("teacher_label"),
        "allowed_sizing_types": teacher_item.get("allowed_sizing_types", []),
        "allin_aggressive": teacher_item.get("allin_aggressive", False),
        "runs": runs,
        **summary,
    }


def build_blind_llm_prompt(
    payload: JsonDict,
    legal_actions: list[str],
    *,
    blind_profile: str = "baseline",
) -> str:
    """Build a blind HU flop prompt using only visible game context."""
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    request = payload.get("request") if isinstance(payload.get("request"), dict) else {}
    context = {
        "board": request.get("board"),
        "hero_cards": meta.get("hero_cards"),
        "pot": request.get("starting_pot"),
        "starting_pot": request.get("starting_pot"),
        "effective_stack": request.get("effective_stack"),
        "spr": meta.get("spr"),
        "hero_position": meta.get("hero_position"),
        "hero_is_ip": meta.get("hero_is_ip"),
        "actions_played": request.get("actions_played", []),
        "legal_actions": legal_actions,
    }
    guidance = ""
    if blind_profile == "guided":
        guidance = (
            "\nHU flop strategy guidance:\n"
            "- This is a heads-up flop decision with deep SPR.\n"
            "- Do not choose ALL_IN on the flop unless there is an exceptional "
            "reason. In deep-SPR HU flop spots, ALL_IN should be extremely rare.\n"
            "- Do not overuse CHECK just because it is safe.\n"
            "- Consider small bets, especially 33% pot, when:\n"
            "  - hero is in position,\n"
            "  - board is dry or favorable to the bettor,\n"
            "  - a small continuation bet can realize fold equity,\n"
            "  - betting small applies pressure without overcommitting deep stacks.\n"
            "- On dynamic or wet boards, small or medium bets may be preferred "
            "over large bets when protection/value is needed but SPR is deep.\n"
            "- Avoid large 75% sizing unless board texture and value/protection "
            "logic strongly support it.\n"
            "- When facing a bet, do not raise too large by default. If raising "
            "is reasonable, prefer small-to-medium raise sizing unless there is "
            "a strong value or denial reason.\n"
            "- If the spot is close or uncertain, prefer the lower-risk legal "
            "action, but do not ignore natural small-bet opportunities.\n"
            "\nSizing options:\n"
            "- If betting first:\n"
            "  - bet_33 = small stab / small c-bet\n"
            "  - bet_50 = medium bet\n"
            "  - bet_60 = larger medium bet\n"
            "  - bet_75 = large polar/protection bet\n"
            "- If raising over a bet, raise_33 / raise_50 / raise_60 / raise_75 "
            "represent increasing pressure relative to pot/context.\n"
            "- all_in should almost never be used in deep-SPR flop spots.\n"
            "- If choosing CHECK/CALL/FOLD, sizing_type must be \"none\".\n"
        )
    elif blind_profile != "baseline":
        raise ValueError(f"Unsupported blind_profile: {blind_profile}")

    return (
        "You are making a heads-up no-limit hold'em flop decision.\n"
        "Use only the visible game context.\n"
        "Do not assume hidden solver output.\n"
        "Choose a legal action and sizing.\n"
        f"{guidance}"
        "Return JSON only.\n"
        "Allowed actions: CHECK, BET, RAISE, CALL, FOLD, ALL_IN.\n"
        "Allowed sizing_type values: none, bet_33, bet_50, bet_60, bet_75, "
        "raise_33, raise_50, raise_60, raise_75, all_in.\n"
        "Allowed JSON keys: action, amount, sizing_type, sizing_bucket, "
        "confidence, reason, risk_flags.\n"
        f"Visible context JSON:\n{json.dumps(context, ensure_ascii=False, indent=2)}"
    )


def evaluate_blind_llm_decision(
    baseline: JsonDict,
    teacher_item: JsonDict,
    decision: JsonDict,
    legal_actions: list[str],
    llm_result: JsonDict | None = None,
) -> JsonDict:
    """Evaluate a blind LLM decision against hidden Solver/teacher references."""
    llm_action = str(decision.get("action", "")).upper()
    sizing_eval = evaluate_llm_sizing_decision(teacher_item, decision, llm_result)
    legal_action_valid = llm_action in set(legal_actions)
    baseline_action = baseline.get("action")
    blind_action_match = llm_action == baseline_action
    blind_direction_match = _action_direction(llm_action) == _action_direction(
        baseline_action
    )
    blind_teacher_alignment = (
        sizing_eval["sizing_allowed_match"]
        and not sizing_eval["allin_violation"]
        and not sizing_eval["passive_teacher_aggressive_violation"]
        and legal_action_valid
    )
    return {
        "blind_action_match": blind_action_match,
        "blind_direction_match": blind_direction_match,
        "blind_teacher_alignment": blind_teacher_alignment,
        "blind_sizing_allowed_match": sizing_eval["sizing_allowed_match"],
        "blind_allin_violation": sizing_eval["allin_violation"],
        "blind_passive_teacher_aggressive_violation": sizing_eval[
            "passive_teacher_aggressive_violation"
        ],
        "legal_action_valid": legal_action_valid,
        "under_15s": sizing_eval["under_15s"],
    }


def build_llm_blind_summary(
    items: list[JsonDict], model: str, blind_profile: str = "baseline"
) -> JsonDict:
    """Aggregate blind LLM diagnostic results."""
    success_items = [item for item in items if item.get("llm_success") is True]
    return {
        "blind_profile": blind_profile,
        "total_samples": len(items),
        "success_count": len(success_items),
        "error_count": sum(1 for item in items if item.get("llm_success") is False),
        "under_15s_rate": _bool_rate(items, "under_15s"),
        "blind_action_match_rate": _bool_rate(success_items, "blind_action_match"),
        "blind_direction_match_rate": _bool_rate(
            success_items, "blind_direction_match"
        ),
        "blind_teacher_alignment_rate": _bool_rate(
            success_items, "blind_teacher_alignment"
        ),
        "blind_sizing_allowed_match_count": sum(
            1 for item in items if item.get("blind_sizing_allowed_match") is True
        ),
        "blind_allin_violation_count": sum(
            1 for item in items if item.get("blind_allin_violation") is True
        ),
        "blind_passive_teacher_aggressive_violation_count": sum(
            1
            for item in items
            if item.get("blind_passive_teacher_aggressive_violation") is True
        ),
        "legal_action_invalid_count": sum(
            1 for item in items if item.get("legal_action_valid") is False
        ),
        "model": model,
        "items": [
            {
                "sample_id": item["sample_id"],
                "blind_profile": item.get("blind_profile"),
                "baseline_action": item.get("baseline_action"),
                "teacher_label": item.get("teacher_label"),
                "allowed_sizing_types": item.get("allowed_sizing_types", []),
                "llm_success": item.get("llm_success"),
                "llm_action": item.get("llm_action"),
                "llm_sizing_type": item.get("llm_sizing_type"),
                "blind_action_match": item.get("blind_action_match"),
                "blind_direction_match": item.get("blind_direction_match"),
                "blind_teacher_alignment": item.get("blind_teacher_alignment"),
                "blind_sizing_allowed_match": item.get("blind_sizing_allowed_match"),
                "blind_allin_violation": item.get("blind_allin_violation"),
                "blind_passive_teacher_aggressive_violation": item.get(
                    "blind_passive_teacher_aggressive_violation"
                ),
                "legal_action_valid": item.get("legal_action_valid"),
                "error": item.get("llm_error"),
            }
            for item in items
        ],
    }


def blind_repeat_item_summary(
    sample_id_value: str,
    runs: list[JsonDict],
    *,
    repeat_count: int,
) -> JsonDict:
    """Aggregate repeated blind LLM runs for one sample."""
    success_runs = [run for run in runs if run.get("success") is True]
    action_values = sorted(
        {
            str(run.get("llm_action"))
            for run in success_runs
            if run.get("llm_action") is not None
        }
    )
    sizing_type_values = sorted(
        {
            str(run.get("llm_sizing_type"))
            for run in success_runs
            if run.get("llm_sizing_type") is not None
        }
    )
    alignment_values = {
        bool(run.get("blind_teacher_alignment")) for run in success_runs
    }
    teacher_alignment_count = sum(
        1 for run in success_runs if run.get("blind_teacher_alignment") is True
    )
    allin_violation_count = sum(
        1 for run in runs if run.get("blind_allin_violation") is True
    )
    passive_violation_count = sum(
        1
        for run in runs
        if run.get("blind_passive_teacher_aggressive_violation") is True
    )
    return {
        "sample_id": sample_id_value,
        "repeat_count": repeat_count,
        "success_count": len(success_runs),
        "error_count": sum(1 for run in runs if run.get("success") is False),
        "action_values": action_values,
        "sizing_type_values": sizing_type_values,
        "teacher_alignment_count": teacher_alignment_count,
        "teacher_alignment_rate": _rate(teacher_alignment_count, len(success_runs)),
        "action_stable": len(action_values) <= 1 and bool(success_runs),
        "sizing_type_stable": len(sizing_type_values) <= 1 and bool(success_runs),
        "teacher_alignment_stable": len(alignment_values) <= 1 and bool(success_runs),
        "allin_violation_count": allin_violation_count,
        "passive_teacher_aggressive_violation_count": passive_violation_count,
        "legal_action_invalid_count": sum(
            1 for run in runs if run.get("legal_action_valid") is False
        ),
    }


def build_llm_blind_repeat_summary(
    items: list[JsonDict],
    blind_profile: str,
    repeat_count: int,
) -> JsonDict:
    """Aggregate blind LLM repeatability diagnostics."""
    runs = [run for item in items for run in item.get("runs", [])]
    success_runs = [run for run in runs if run.get("success") is True]
    unstable_samples = [
        {
            "sample_id": item["sample_id"],
            "action_stable": item.get("action_stable"),
            "sizing_type_stable": item.get("sizing_type_stable"),
            "teacher_alignment_stable": item.get("teacher_alignment_stable"),
            "allin_violation_count": item.get("allin_violation_count", 0),
            "passive_teacher_aggressive_violation_count": item.get(
                "passive_teacher_aggressive_violation_count", 0
            ),
            "action_values": item.get("action_values", []),
            "sizing_type_values": item.get("sizing_type_values", []),
            "teacher_alignment_rate": item.get("teacher_alignment_rate"),
        }
        for item in items
        if (
            item.get("action_stable") is False
            or item.get("sizing_type_stable") is False
            or item.get("teacher_alignment_stable") is False
            or item.get("allin_violation_count", 0) > 0
            or item.get("passive_teacher_aggressive_violation_count", 0) > 0
        )
    ]
    return {
        "blind_profile": blind_profile,
        "total_samples": len(items),
        "repeat_count": repeat_count,
        "planned_runs": len(items) * repeat_count,
        "success_count": len(success_runs),
        "error_count": sum(1 for run in runs if run.get("success") is False)
        + sum(int(item.get("error_count", 0)) for item in items if not item.get("runs")),
        "under_15s_rate": _bool_rate(runs, "under_15s"),
        "overall_blind_action_match_rate": _bool_rate(
            success_runs,
            "blind_action_match",
        ),
        "overall_blind_direction_match_rate": _bool_rate(
            success_runs,
            "blind_direction_match",
        ),
        "overall_blind_teacher_alignment_rate": _bool_rate(
            success_runs,
            "blind_teacher_alignment",
        ),
        "action_stable_sample_count": sum(
            1 for item in items if item.get("action_stable") is True
        ),
        "sizing_type_stable_sample_count": sum(
            1 for item in items if item.get("sizing_type_stable") is True
        ),
        "teacher_alignment_stable_sample_count": sum(
            1 for item in items if item.get("teacher_alignment_stable") is True
        ),
        "allin_violation_count": sum(
            int(item.get("allin_violation_count", 0)) for item in items
        ),
        "passive_teacher_aggressive_violation_count": sum(
            int(item.get("passive_teacher_aggressive_violation_count", 0))
            for item in items
        ),
        "legal_action_invalid_count": sum(
            int(item.get("legal_action_invalid_count", 0)) for item in items
        ),
        "unstable_samples": unstable_samples,
        "items": [
            {
                "sample_id": item["sample_id"],
                "action_values": item.get("action_values", []),
                "sizing_type_values": item.get("sizing_type_values", []),
                "teacher_alignment_rate": item.get("teacher_alignment_rate"),
                "action_stable": item.get("action_stable"),
                "sizing_type_stable": item.get("sizing_type_stable"),
                "teacher_alignment_stable": item.get("teacher_alignment_stable"),
                "allin_violation_count": item.get("allin_violation_count", 0),
                "passive_teacher_aggressive_violation_count": item.get(
                    "passive_teacher_aggressive_violation_count", 0
                ),
            }
            for item in items
        ],
    }


def _action_direction(action: object) -> str:
    """Return aggressive/passive/unknown direction for an action."""
    if action in {"BET", "RAISE", "ALL_IN"}:
        return "aggressive"
    if action in {"CHECK", "CALL", "FOLD"}:
        return "passive"
    return "unknown"


def compare_solver_requests_llm(
    *,
    llm_path: Path | None,
    llm_dir: Path | None,
    sample_ids: list[str] | None,
    llm_model: str | None,
    timeout: float,
    out_dir: Path,
    bridge_factory: BridgeFactory = PostflopSolverBridge,
    llm_caller: LLMCaller | None = None,
) -> JsonDict:
    """Run LLM diagnostic decisions against primary Solver baselines."""
    request_paths = discover_repeat_request_files(llm_path, llm_dir, sample_ids)
    items_dir = out_dir / "items"
    items_dir.mkdir(parents=True, exist_ok=True)
    items: list[JsonDict] = []
    model = llm_model or os.getenv("LLM_MODEL_DEFAULT") or DEFAULT_LLM_MODEL
    caller = llm_caller or call_openrouter_llm
    print(f"LLM DISCOVERY: samples={len(request_paths)} model={model}")
    for request_path in request_paths:
        item = _run_llm_for_sample(
            request_path,
            llm_model=model,
            timeout=timeout,
            bridge_factory=bridge_factory,
            llm_caller=caller,
        )
        items.append(item)
        output_path = items_dir / f"{sample_id(request_path)}_llm.json"
        with output_path.open("w", encoding="utf-8") as file:
            json.dump(item, file, ensure_ascii=False, indent=2)

    summary = build_llm_diagnostic_summary(items, model)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "llm_summary.json"
    summary["output_path"] = str(summary_path)
    with summary_path.open("w", encoding="utf-8") as file:
        json.dump(summary, file, ensure_ascii=False, indent=2)
    return summary


def _run_llm_for_sample(
    request_path: Path,
    *,
    llm_model: str,
    timeout: float,
    bridge_factory: BridgeFactory,
    llm_caller: LLMCaller,
) -> JsonDict:
    """Run one primary baseline and one LLM diagnostic decision."""
    payload = load_solver_payload(request_path)
    primary_request = dict(payload["request"])
    baseline = run_solver_request_payload(
        primary_request,
        path_label=str(request_path),
        timeout=timeout,
        bridge_factory=bridge_factory,
    )
    legal_actions = legal_actions_for_solver_request(primary_request)
    prompt = build_llm_flop_prompt(payload, baseline, legal_actions)
    llm_result = run_llm_diagnostic_request(
        prompt,
        model=llm_model,
        timeout=timeout,
        llm_caller=llm_caller,
    )
    probabilities = baseline.get("probabilities")
    if not isinstance(probabilities, dict):
        probabilities = {}
    probability_info = probability_summary(probabilities)
    decision = llm_result.get("decision")
    if not isinstance(decision, dict):
        decision = {}
    evaluation = evaluate_llm_decision(
        baseline,
        probability_info,
        decision,
        legal_actions,
        llm_result,
    )
    return {
        "sample_id": sample_id(request_path),
        "source_request": str(request_path),
        "llm_model": llm_model,
        "baseline_success": baseline["success"],
        "baseline_elapsed_ms": baseline["elapsed_ms"],
        "baseline_action": baseline["action"],
        "baseline_amount": baseline["amount"],
        "baseline_probabilities": probabilities,
        "baseline_top_action": probability_info["top_action"],
        "baseline_top_probability": probability_info["top_probability"],
        "baseline_second_action": probability_info["second_action"],
        "baseline_second_probability": probability_info["second_probability"],
        "baseline_top_margin": probability_info["top_margin"],
        "legal_actions": legal_actions,
        "llm_success": llm_result["success"],
        "llm_elapsed_ms": llm_result["elapsed_ms"],
        "llm_action": decision.get("action"),
        "llm_amount": decision.get("amount"),
        "llm_sizing_type": decision.get("sizing_type"),
        "llm_confidence": decision.get("confidence"),
        "llm_reason": decision.get("reason"),
        "llm_risk_flags": decision.get("risk_flags", []),
        "llm_error": llm_result.get("error"),
        "llm_status_code": llm_result.get("status_code"),
        "llm_response_body": llm_result.get("response_body"),
        "prompt": prompt,
        **evaluation,
    }


def build_llm_flop_prompt(
    payload: JsonDict,
    baseline: JsonDict,
    legal_actions: list[str],
) -> str:
    """Build a diagnostics-only HU deep-SPR flop LLM prompt."""
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    request = payload.get("request") if isinstance(payload.get("request"), dict) else {}
    probabilities = baseline.get("probabilities")
    if not isinstance(probabilities, dict):
        probabilities = {}
    prob_info = probability_summary(probabilities)
    context = {
        "task": "HU deep-SPR flop decision diagnostic",
        "board": request.get("board"),
        "hero_cards": meta.get("hero_cards"),
        "pot": request.get("starting_pot"),
        "starting_pot": request.get("starting_pot"),
        "effective_stack": request.get("effective_stack"),
        "spr": meta.get("spr"),
        "hero_position": meta.get("hero_position"),
        "hero_is_ip": meta.get("hero_is_ip"),
        "actions_played": request.get("actions_played", []),
        "legal_actions": legal_actions,
        "candidate_actions": [
            "CHECK",
            "BET_33",
            "BET_50",
            "BET_60",
            "BET_75",
            "ALL_IN",
        ],
        "primary_solver_action": baseline.get("action"),
        "primary_solver_amount": baseline.get("amount"),
        "primary_solver_probabilities": probabilities,
        "primary_top_action": prob_info["top_action"],
        "primary_top_probability": prob_info["top_probability"],
        "primary_second_action": prob_info["second_action"],
        "primary_second_probability": prob_info["second_probability"],
        "primary_top_margin": prob_info["top_margin"],
        "primary_margin_class": _margin_class(prob_info["top_margin"]),
    }
    return (
        "You are evaluating a heads-up no-limit hold'em flop decision.\n"
        "Primary Solver is the anchor. Do not make a large deviation from "
        "primary Solver unless the board texture strongly justifies it.\n"
        "Margin interpretation rules:\n"
        "- If primary top_margin >= 0.20:\n"
        "  - Treat the primary solver action as clear.\n"
        "  - Match the primary action direction.\n"
        "  - confidence may be high.\n"
        "- If 0.10 < primary top_margin < 0.20:\n"
        "  - Treat the spot as moderately mixed.\n"
        "  - Prefer matching the primary action.\n"
        "  - confidence should be medium.\n"
        "- If primary top_margin <= 0.10:\n"
        "  - Treat the spot as near-tie / highly mixed.\n"
        "  - Do not describe the solver action as clear, strong, dominant, or obvious.\n"
        "  - confidence must be low or medium, never high.\n"
        "  - Explain that the chosen action is selected because it is the top "
        "solver action, not because it dominates.\n"
        "Do not output illegal actions. Output JSON only.\n"
        "Allowed JSON keys: action, amount, sizing_type, confidence, reason, "
        "risk_flags.\n"
        f"Context JSON:\n{json.dumps(context, ensure_ascii=False, indent=2)}"
    )


def run_llm_diagnostic_request(
    prompt: str,
    *,
    model: str,
    timeout: float,
    llm_caller: LLMCaller,
) -> JsonDict:
    """Call an LLM diagnostic function and normalize the result."""
    started_at = time.perf_counter()
    try:
        raw_result = llm_caller(prompt, model, timeout)
    except Exception as exc:
        raw_result = {"success": False, "error": str(exc)}
    elapsed_ms = int((time.perf_counter() - started_at) * 1000)
    diagnostic_elapsed = _optional_int(raw_result.get("diagnostic_elapsed_ms"))
    if diagnostic_elapsed is not None:
        elapsed_ms = diagnostic_elapsed
    if raw_result.get("success") is not True:
        return {
            "success": False,
            "elapsed_ms": elapsed_ms,
            "decision": None,
            "error": raw_result.get("error") or "LLM request failed",
            "status_code": raw_result.get("status_code"),
            "response_body": raw_result.get("response_body"),
        }
    try:
        decision = raw_result.get("decision")
        if not isinstance(decision, dict):
            decision = parse_llm_decision_json(str(raw_result.get("raw_content", "")))
    except ValueError as exc:
        return {
            "success": False,
            "elapsed_ms": elapsed_ms,
            "decision": None,
            "error": str(exc),
            "status_code": raw_result.get("status_code"),
            "response_body": raw_result.get("response_body"),
        }
    return {
        "success": True,
        "elapsed_ms": elapsed_ms,
        "decision": decision,
        "error": None,
        "status_code": raw_result.get("status_code"),
        "response_body": raw_result.get("response_body"),
    }


def call_openrouter_llm(prompt: str, model: str, timeout: float) -> JsonDict:
    """Call OpenRouter chat completions for diagnostics only."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        return {"success": False, "error": "OPENROUTER_API_KEY missing"}
    provider = openrouter_provider_config()
    payload: JsonDict = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 180,
        "temperature": 0.1,
        "reasoning": {"effort": "none"},
    }
    if provider is not None:
        payload["provider"] = provider
    if os.getenv("OPENROUTER_USE_STRICT_JSON_SCHEMA", "").lower() == "true":
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "hu_flop_decision",
                "strict": True,
                "schema": llm_decision_schema(),
            },
        }

    started_at = time.perf_counter()
    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=(5, timeout),
        )
    except requests.RequestException as exc:
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        return {
            "success": False,
            "diagnostic_elapsed_ms": elapsed_ms,
            "error": str(exc),
        }
    elapsed_ms = int((time.perf_counter() - started_at) * 1000)
    if response.status_code >= 400:
        return {
            "success": False,
            "diagnostic_elapsed_ms": elapsed_ms,
            "status_code": response.status_code,
            "response_body": response.text[:1000],
            "error": f"HTTP {response.status_code}: {response.text[:500]}",
        }
    try:
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        return {
            "success": False,
            "diagnostic_elapsed_ms": elapsed_ms,
            "status_code": response.status_code,
            "response_body": response.text[:1000],
            "error": f"Invalid OpenRouter response: {exc}",
        }
    if not isinstance(content, str) or not content.strip():
        return {
            "success": False,
            "diagnostic_elapsed_ms": elapsed_ms,
            "status_code": response.status_code,
            "response_body": response.text[:1000],
            "error": "OpenRouter response content was empty",
        }
    return {
        "success": True,
        "diagnostic_elapsed_ms": elapsed_ms,
        "status_code": response.status_code,
        "response_body": response.text[:1000],
        "raw_content": content,
    }


def openrouter_provider_config() -> dict[str, Any] | None:
    """Return OpenRouter provider config compatible with LLMPipeline."""
    return LLMPipeline.openrouter_provider_config()


def llm_decision_schema() -> JsonDict:
    """Return the JSON schema for LLM diagnostic decisions."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "action",
            "amount",
            "sizing_type",
            "sizing_bucket",
            "confidence",
            "reason",
            "risk_flags",
        ],
        "properties": {
            "action": {"type": "string", "enum": sorted(LLM_ALLOWED_ACTIONS)},
            "amount": {"type": "integer", "minimum": 0},
            "sizing_type": {"type": "string"},
            "sizing_bucket": {"type": "string"},
            "confidence": {
                "type": "string",
                "enum": ["low", "medium", "high"],
            },
            "reason": {"type": "string"},
            "risk_flags": {"type": "array", "items": {"type": "string"}},
        },
    }


def parse_llm_decision_json(content: str) -> JsonDict:
    """Parse and validate a JSON-only LLM diagnostic response."""
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"```$", "", text).strip()
    try:
        decision = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid LLM JSON: {exc}") from exc
    if not isinstance(decision, dict):
        raise ValueError("LLM decision must be a JSON object")
    action = str(decision.get("action", "")).upper().replace("-", "_")
    amount = _optional_int(decision.get("amount")) or 0
    risk_flags = decision.get("risk_flags", [])
    if not isinstance(risk_flags, list):
        risk_flags = [str(risk_flags)]
    return {
        "action": action,
        "amount": amount,
        "sizing_type": str(decision.get("sizing_type", "unknown")),
        "sizing_bucket": str(decision.get("sizing_bucket", "unknown")),
        "confidence": str(decision.get("confidence", "low")).lower(),
        "reason": str(decision.get("reason", "")),
        "risk_flags": [str(flag) for flag in risk_flags],
    }


def legal_actions_for_solver_request(request: JsonDict) -> list[str]:
    """Return legal action labels for a diagnostic request."""
    actions_played = request.get("actions_played") or []
    if actions_played == []:
        return ["CHECK", "BET", "ALL_IN"]
    return ["CHECK", "BET", "RAISE", "CALL", "FOLD", "ALL_IN"]


def evaluate_llm_decision(
    baseline: JsonDict,
    baseline_probability: JsonDict,
    decision: JsonDict,
    legal_actions: list[str],
    llm_result: JsonDict,
) -> JsonDict:
    """Evaluate one LLM decision against the primary Solver baseline."""
    if llm_result.get("success") is not True:
        return {
            "action_match": False,
            "amount_match": False,
            "direction_match": False,
            "near_tie_mismatch": False,
            "dangerous_flip": False,
            "clear_check_to_bet": False,
            "call_or_raise_to_fold": False,
            "legal_action_valid": None,
            "under_15s": False,
            "primary_margin_class": "unknown",
            "confidence_overstated": False,
            "reason_overclaim": False,
        }
    baseline_action = baseline.get("action")
    llm_action = decision.get("action")
    top_margin_raw = baseline_probability.get("top_margin")
    margin = _optional_float(top_margin_raw) if top_margin_raw is not None else None
    flags = _mismatch_flags(baseline_action, llm_action, margin)
    action_match = baseline_action == llm_action
    amount_match = baseline.get("amount") == decision.get("amount")
    legal_action_valid = llm_action in set(legal_actions)
    margin_class = _margin_class(margin)
    llm_confidence = decision.get("confidence", "")
    llm_reason = decision.get("reason", "")
    confidence_overstated = (
        margin_class == "near_tie"
        and isinstance(llm_confidence, str)
        and llm_confidence.lower() == "high"
    )
    _overclaim_words = [
        "clear", "strongly prefers", "dominant", "obvious",
        "明確", "強い", "優勢",
    ]
    reason_overclaim = (
        margin_class == "near_tie"
        and isinstance(llm_reason, str)
        and _reason_overclaims_near_tie(llm_reason)
    )
    return {
        "action_match": action_match,
        "amount_match": amount_match,
        "direction_match": action_match,
        "near_tie_mismatch": flags["action_mismatch_near_tie"],
        "dangerous_flip": flags["dangerous_flip"],
        "clear_check_to_bet": flags["clear_check_to_bet"],
        "call_or_raise_to_fold": flags["call_or_raise_to_fold"],
        "legal_action_valid": legal_action_valid,
        "under_15s": _under_15s({"elapsed_ms": llm_result.get("elapsed_ms")}),
        "primary_margin_class": margin_class,
        "confidence_overstated": confidence_overstated,
        "reason_overclaim": reason_overclaim,
    }


def build_llm_diagnostic_summary(items: list[JsonDict], model: str) -> JsonDict:
    """Aggregate LLM diagnostic item results."""
    success_items = [item for item in items if item.get("llm_success") is True]
    elapsed_values = [
        int(item["llm_elapsed_ms"])
        for item in items
        if item.get("llm_elapsed_ms") is not None
    ]
    total = len(items)
    return {
        "total_samples": total,
        "success_count": len(success_items),
        "error_count": sum(1 for item in items if item.get("llm_success") is False),
        "avg_llm_elapsed_ms": _average(elapsed_values),
        "under_15s_rate": _bool_rate(items, "under_15s"),
        "action_match_rate": _bool_rate(success_items, "action_match"),
        "direction_match_rate": _bool_rate(success_items, "direction_match"),
        "dangerous_flip_count": sum(
            1 for item in items if item.get("dangerous_flip") is True
        ),
        "clear_check_to_bet_count": sum(
            1 for item in items if item.get("clear_check_to_bet") is True
        ),
        "legal_action_invalid_count": sum(
            1 for item in items if item.get("legal_action_valid") is False
        ),
        "confidence_overstated_count": sum(
            1 for item in items if item.get("confidence_overstated") is True
        ),
        "reason_overclaim_count": sum(
            1 for item in items if item.get("reason_overclaim") is True
        ),
        "model": model,
        "items": [
            {
                "sample_id": item["sample_id"],
                "baseline_action": item["baseline_action"],
                "llm_action": item["llm_action"],
                "llm_success": item["llm_success"],
                "llm_elapsed_ms": item["llm_elapsed_ms"],
                "action_match": item["action_match"],
                "dangerous_flip": item["dangerous_flip"],
                "legal_action_valid": item["legal_action_valid"],
                "confidence_overstated": item.get("confidence_overstated", False),
                "reason_overclaim": item.get("reason_overclaim", False),
                "primary_margin_class": item.get("primary_margin_class", "unknown"),
                "error": item["llm_error"],
                "status_code": item.get("llm_status_code"),
            }
            for item in items
        ],
    }


def _run_resident_for_sample(
    request_path: Path,
    *,
    repeat_count: int,
    timeout: float,
    bridge: PostflopSolverBridge,
    initial_start_ms: int | None,
) -> JsonDict:
    """Run one request repeatedly through an already-started bridge."""
    request = load_solver_request(request_path)
    runs: list[JsonDict] = []
    for run_index in range(1, repeat_count + 1):
        solve_started = time.perf_counter()
        raw_result: JsonDict
        try:
            raw_result = bridge.solve(request, timeout=timeout)
        except Exception as exc:
            raw_result = {"success": False, "error": str(exc)}
        solve_ms = int((time.perf_counter() - solve_started) * 1000)
        diagnostic_elapsed = _optional_int(raw_result.get("diagnostic_elapsed_ms"))
        if diagnostic_elapsed is not None:
            solve_ms = diagnostic_elapsed
        action, amount, probabilities = extract_action_summary(raw_result)
        start_ms = initial_start_ms if run_index == 1 else None
        runs.append(
            {
                "run": run_index,
                "start_ms": start_ms,
                "solve_ms": solve_ms,
                "total_ms": solve_ms + (start_ms or 0),
                "success": bool(raw_result.get("success")),
                "action": action,
                "amount": amount,
                "probabilities": probabilities,
                "error": raw_result.get("error"),
            }
        )

    summary = resident_item_summary(sample_id(request_path), runs)
    return {
        "sample_id": sample_id(request_path),
        "path": str(request_path),
        "repeat_count": repeat_count,
        "runs": runs,
        "summary": summary,
    }


def resident_item_summary(sample_id_value: str, runs: list[JsonDict]) -> JsonDict:
    """Aggregate resident timing runs for one request."""
    repeat_runs = [
        {
            "action": run.get("action"),
            "amount": run.get("amount"),
            "elapsed_ms": run.get("solve_ms"),
            "probabilities": run.get("probabilities"),
        }
        for run in runs
    ]
    repeat_summary = repeatability_item_summary(sample_id_value, repeat_runs)
    solve_values = [
        int(run["solve_ms"])
        for run in runs
        if run.get("solve_ms") is not None
    ]
    total_values = [
        int(run["total_ms"])
        for run in runs
        if run.get("total_ms") is not None
    ]
    start_values = [
        int(run["start_ms"])
        for run in runs
        if run.get("start_ms") is not None
    ]
    avg_total = _average(total_values)
    avg_solve = _average(solve_values)
    estimated_start_overhead = None
    if avg_total is not None and avg_solve is not None:
        estimated_start_overhead = avg_total - avg_solve
    repeat_summary.update(
        {
            "start_ms": start_values[0] if start_values else None,
            "avg_resident_solve_ms": avg_solve,
            "min_resident_solve_ms": min(solve_values) if solve_values else None,
            "max_resident_solve_ms": max(solve_values) if solve_values else None,
            "avg_total_ms": avg_total,
            "estimated_start_overhead_ms": estimated_start_overhead,
            "process_reuse_effective": (
                estimated_start_overhead is not None
                and estimated_start_overhead >= 2000
            ),
        }
    )
    return repeat_summary


def build_resident_timing_summary(items: list[JsonDict], start_ms: int) -> JsonDict:
    """Aggregate resident timing summaries."""
    summaries = [item["summary"] for item in items]
    solve_values = [
        int(summary["avg_resident_solve_ms"])
        for summary in summaries
        if summary.get("avg_resident_solve_ms") is not None
    ]
    effective_count = sum(
        1 for summary in summaries if summary.get("process_reuse_effective") is True
    )
    return {
        "total_samples": len(items),
        "start_ms": start_ms,
        "avg_resident_solve_ms": _average(solve_values),
        "min_resident_solve_ms": min(solve_values) if solve_values else None,
        "max_resident_solve_ms": max(solve_values) if solve_values else None,
        "process_reuse_effective_count": effective_count,
        "items": summaries,
    }


def discover_repeat_request_files(
    repeat_path: Path | None,
    repeat_dir: Path | None,
    sample_ids: list[str] | None,
) -> list[Path]:
    """Return request files for repeatability mode."""
    if repeat_path is not None:
        return [repeat_path]
    if repeat_dir is None:
        return []
    paths = discover_primary_request_files(repeat_dir, "flop")
    if not sample_ids:
        return paths
    selected = set(sample_ids)
    return [path for path in paths if sample_id(path) in selected]


def _run_repeat_for_sample(
    request_path: Path,
    *,
    repeat_count: int,
    timeout: float,
    bridge_factory: BridgeFactory,
) -> JsonDict:
    """Run the same request repeatedly and summarize the sample."""
    request = load_solver_request(request_path)
    runs: list[JsonDict] = []
    for run_index in range(1, repeat_count + 1):
        result = run_solver_request_payload(
            request,
            path_label=str(request_path),
            timeout=timeout,
            bridge_factory=bridge_factory,
        )
        runs.append(
            {
                "run": run_index,
                "success": result["success"],
                "elapsed_ms": result["elapsed_ms"],
                "action": result["action"],
                "amount": result["amount"],
                "probabilities": result["probabilities"],
                "error": result["error"],
            }
        )

    summary = repeatability_item_summary(sample_id(request_path), runs)
    if summary["unstable"]:
        print(
            "SOLVER_REPEATABILITY_UNSTABLE: "
            f"sample_id={summary['sample_id']} "
            f"action_set={summary['action_set']} amount_set={summary['amount_set']}"
        )
    return {
        "sample_id": sample_id(request_path),
        "path": str(request_path),
        "repeat_count": repeat_count,
        "runs": runs,
        "summary": summary,
    }


def repeatability_item_summary(sample_id_value: str, runs: list[JsonDict]) -> JsonDict:
    """Aggregate repeated run results for one request."""
    actions = sorted({run.get("action") for run in runs if run.get("action") is not None})
    amounts = sorted({run.get("amount") for run in runs if run.get("amount") is not None})
    elapsed_values = [
        int(run["elapsed_ms"])
        for run in runs
        if run.get("elapsed_ms") is not None
    ]
    top_margins = [
        summary["top_margin"]
        for summary in (
            probability_summary(_probabilities_from_run(run)) for run in runs
        )
        if summary["top_margin"] is not None
    ]
    top_actions = sorted(
        {
            summary["top_action"]
            for summary in (
                probability_summary(_probabilities_from_run(run)) for run in runs
            )
            if summary["top_action"] is not None
        }
    )
    action_stable = len(actions) <= 1
    amount_stable = len(amounts) <= 1
    return {
        "sample_id": sample_id_value,
        "run_count": len(runs),
        "action_set": actions,
        "amount_set": amounts,
        "action_stable": action_stable,
        "amount_stable": amount_stable,
        "unstable": not action_stable,
        "avg_elapsed_ms": _average(elapsed_values),
        "min_elapsed_ms": min(elapsed_values) if elapsed_values else None,
        "max_elapsed_ms": max(elapsed_values) if elapsed_values else None,
        "elapsed_spread_ms": (
            max(elapsed_values) - min(elapsed_values) if elapsed_values else None
        ),
        "probability_top_action_set": top_actions,
        "probability_top_margin_range": (
            [round(min(top_margins), 3), round(max(top_margins), 3)]
            if top_margins
            else None
        ),
    }


def build_repeatability_summary(items: list[JsonDict]) -> JsonDict:
    """Aggregate repeatability item summaries."""
    summaries = [item["summary"] for item in items]
    unstable_samples = [
        summary["sample_id"] for summary in summaries if summary["unstable"]
    ]
    elapsed_spreads = [
        int(summary["elapsed_spread_ms"])
        for summary in summaries
        if summary.get("elapsed_spread_ms") is not None
    ]
    return {
        "total_samples": len(items),
        "unstable_sample_count": len(unstable_samples),
        "unstable_samples": unstable_samples,
        "avg_elapsed_spread_ms": _average(elapsed_spreads),
        "items": summaries,
    }


def _probabilities_from_run(run: JsonDict) -> dict[str, float]:
    probabilities = run.get("probabilities")
    if not isinstance(probabilities, dict):
        return {}
    return {str(action): float(probability) for action, probability in probabilities.items()}


def grid_profile_configs() -> list[JsonDict]:
    """Return grid profile configurations in fastest-to-slowest order."""
    profiles: list[JsonDict] = []
    for bet_sizes in GRID_BET_SIZES:
        for target in GRID_TARGET_EXPLOITABILITY:
            for max_iterations in GRID_MAX_ITERATIONS:
                profiles.append(
                    {
                        "profile_id": grid_profile_id(
                            max_iterations=max_iterations,
                            target_exploitability_pct=target,
                            bet_sizes=bet_sizes,
                        ),
                        "max_iterations": max_iterations,
                        "target_exploitability_pct": target,
                        "bet_sizes": bet_sizes,
                    }
                )
    return profiles


def _run_grid_for_sample(
    primary_path: Path,
    *,
    timeout: float,
    bridge_factory: BridgeFactory,
) -> JsonDict:
    """Run baseline and grid profiles for one sample."""
    primary_request = load_solver_request(primary_path)
    baseline = run_solver_request_payload(
        primary_request,
        path_label=str(primary_path),
        timeout=timeout,
        bridge_factory=bridge_factory,
    )
    baseline_probabilities = baseline.get("probabilities")
    if not isinstance(baseline_probabilities, dict):
        baseline_probabilities = {}
    baseline_summary = probability_summary(baseline_probabilities)
    results: list[JsonDict] = []

    for bet_sizes in GRID_BET_SIZES:
        skip_stricter_targets = False
        consecutive_slow_targets = 0
        for target in GRID_TARGET_EXPLOITABILITY:
            target_slow = False
            if skip_stricter_targets:
                for max_iterations in GRID_MAX_ITERATIONS:
                    config = _grid_config(max_iterations, target, bet_sizes)
                    results.append(
                        _skipped_grid_result(
                            primary_path,
                            baseline,
                            baseline_summary,
                            config,
                            "slower_target_pruned",
                        )
                    )
                continue

            prune_heavier_iterations = False
            for max_iterations in GRID_MAX_ITERATIONS:
                config = _grid_config(max_iterations, target, bet_sizes)
                if prune_heavier_iterations:
                    results.append(
                        _skipped_grid_result(
                            primary_path,
                            baseline,
                            baseline_summary,
                            config,
                            "heavier_iterations_pruned",
                        )
                    )
                    continue

                request = build_grid_probe_request(
                    primary_request,
                    max_iterations=int(config["max_iterations"]),
                    target_exploitability_pct=float(
                        config["target_exploitability_pct"]
                    ),
                    bet_sizes=str(config["bet_sizes"]),
                )
                result = run_solver_request_payload(
                    request,
                    path_label=f"generated_grid_probe_from:{primary_path}",
                    timeout=timeout,
                    bridge_factory=bridge_factory,
                )
                grid_result = _grid_result_summary(
                    primary_path,
                    baseline,
                    baseline_summary,
                    config,
                    result,
                )
                results.append(grid_result)
                elapsed_ms = _optional_int(result.get("elapsed_ms"))
                if elapsed_ms is not None and elapsed_ms > 20000:
                    target_slow = True
                    prune_heavier_iterations = True

            if target >= 0.9 and target_slow:
                consecutive_slow_targets += 1
            else:
                consecutive_slow_targets = 0
            if target == 0.9 and consecutive_slow_targets >= 2:
                skip_stricter_targets = True

    return {
        "sample_id": sample_id(primary_path),
        "primary_path": str(primary_path),
        "baseline": baseline,
        "baseline_probability_summary": baseline_summary,
        "results": results,
    }


def _grid_config(
    max_iterations: int,
    target_exploitability_pct: float,
    bet_sizes: str,
) -> JsonDict:
    """Return a normalized grid config dictionary."""
    return {
        "profile_id": grid_profile_id(
            max_iterations=max_iterations,
            target_exploitability_pct=target_exploitability_pct,
            bet_sizes=bet_sizes,
        ),
        "max_iterations": max_iterations,
        "target_exploitability_pct": target_exploitability_pct,
        "bet_sizes": bet_sizes,
    }


def _grid_result_summary(
    primary_path: Path,
    baseline: JsonDict,
    baseline_summary: JsonDict,
    config: JsonDict,
    result: JsonDict,
) -> JsonDict:
    """Return one executed grid result row."""
    action_match = baseline.get("action") == result.get("action")
    amount_match = baseline.get("amount") == result.get("amount")
    flags = _mismatch_flags(
        baseline.get("action"),
        result.get("action"),
        _optional_float(baseline_summary.get("top_margin")),
    )
    probabilities = result.get("probabilities")
    if not isinstance(probabilities, dict):
        probabilities = {}
    row: JsonDict = {
        "sample_id": sample_id(primary_path),
        **config,
        "skipped_by_pruning": False,
        "success": result["success"],
        "elapsed_ms": result["elapsed_ms"],
        "under_15s": _under_15s(result),
        "action": result["action"],
        "amount": result["amount"],
        "action_match": action_match,
        "amount_match": amount_match,
        "dangerous_flip": flags["dangerous_flip"],
        "clear_check_to_bet": flags["clear_check_to_bet"],
        "call_or_raise_to_fold": flags["call_or_raise_to_fold"],
        "near_tie_mismatch": flags["action_mismatch_near_tie"],
        "score": grid_score(
            under_15s=_under_15s(result),
            action_match=action_match,
            amount_match=amount_match,
            primary_top_margin=_optional_float(baseline_summary.get("top_margin")),
            dangerous_flip=flags["dangerous_flip"],
            clear_check_to_bet=flags["clear_check_to_bet"],
            call_or_raise_to_fold=flags["call_or_raise_to_fold"],
        ),
        "probabilities": probabilities,
        "error": result["error"],
        "baseline_action": baseline.get("action"),
        "baseline_amount": baseline.get("amount"),
        "baseline_probabilities": baseline.get("probabilities"),
        "baseline_top_action": baseline_summary.get("top_action"),
        "baseline_top_probability": baseline_summary.get("top_probability"),
        "baseline_second_action": baseline_summary.get("second_action"),
        "baseline_second_probability": baseline_summary.get("second_probability"),
        "baseline_top_margin": baseline_summary.get("top_margin"),
    }
    return row


def _skipped_grid_result(
    primary_path: Path,
    baseline: JsonDict,
    baseline_summary: JsonDict,
    config: JsonDict,
    reason: str,
) -> JsonDict:
    """Return a skipped grid result row."""
    return {
        "sample_id": sample_id(primary_path),
        **config,
        "skipped_by_pruning": True,
        "skip_reason": reason,
        "success": None,
        "elapsed_ms": None,
        "under_15s": None,
        "action": None,
        "amount": None,
        "action_match": None,
        "amount_match": None,
        "dangerous_flip": None,
        "clear_check_to_bet": None,
        "call_or_raise_to_fold": None,
        "near_tie_mismatch": None,
        "score": None,
        "probabilities": {},
        "error": None,
        "baseline_action": baseline.get("action"),
        "baseline_amount": baseline.get("amount"),
        "baseline_probabilities": baseline.get("probabilities"),
        "baseline_top_action": baseline_summary.get("top_action"),
        "baseline_top_probability": baseline_summary.get("top_probability"),
        "baseline_second_action": baseline_summary.get("second_action"),
        "baseline_second_probability": baseline_summary.get("second_probability"),
        "baseline_top_margin": baseline_summary.get("top_margin"),
    }


def grid_score(
    *,
    under_15s: bool | None,
    action_match: bool,
    amount_match: bool,
    primary_top_margin: float | None,
    dangerous_flip: bool,
    clear_check_to_bet: bool,
    call_or_raise_to_fold: bool,
) -> int:
    """Score one grid result for diagnostics."""
    score = 0
    if under_15s:
        score += 3
    if action_match:
        score += 4
    if amount_match:
        score += 1
    if not action_match and primary_top_margin is not None and primary_top_margin <= 0.10:
        score += 1
    if dangerous_flip:
        score -= 5
    if clear_check_to_bet:
        score -= 7
    if call_or_raise_to_fold:
        score -= 10
    return score


def build_grid_summary(
    *,
    total_samples: int,
    total_planned_profiles: int,
    results: list[JsonDict],
) -> JsonDict:
    """Aggregate all grid result rows."""
    executed = [row for row in results if not row.get("skipped_by_pruning")]
    skipped_count = len(results) - len(executed)
    profiles: dict[str, JsonDict] = {}
    for profile_id in sorted({str(row["profile_id"]) for row in results}):
        rows = [row for row in results if row["profile_id"] == profile_id]
        profiles[profile_id] = _grid_profile_summary(rows)

    top_profiles_by_score = sorted(
        profiles.values(),
        key=lambda row: (
            _optional_float(row.get("avg_score")) or -999.0,
            _optional_float(row.get("under_15s_rate")) or 0.0,
        ),
        reverse=True,
    )[:10]
    top_profiles_under_15s = sorted(
        profiles.values(),
        key=lambda row: (
            _optional_float(row.get("under_15s_rate")) or 0.0,
            _optional_float(row.get("action_match_rate")) or 0.0,
            -int(row.get("dangerous_flip_count") or 0),
        ),
        reverse=True,
    )[:10]
    return {
        "total_samples": total_samples,
        "total_planned_profiles": total_planned_profiles,
        "executed_count": len(executed),
        "skipped_by_pruning_count": skipped_count,
        "top_profiles_by_score": top_profiles_by_score,
        "top_profiles_under_15s": top_profiles_under_15s,
        "profiles": profiles,
    }


def _grid_profile_summary(rows: list[JsonDict]) -> JsonDict:
    """Aggregate a single grid profile across samples."""
    executed = [row for row in rows if not row.get("skipped_by_pruning")]
    elapsed_values = [
        int(row["elapsed_ms"])
        for row in executed
        if row.get("elapsed_ms") is not None
    ]
    scores = [
        int(row["score"])
        for row in executed
        if row.get("score") is not None
    ]
    profile_id = str(rows[0]["profile_id"]) if rows else ""
    return {
        "profile_id": profile_id,
        "planned_count": len(rows),
        "executed_count": len(executed),
        "skipped_by_pruning_count": len(rows) - len(executed),
        "success_count": sum(1 for row in executed if row.get("success") is True),
        "error_count": sum(1 for row in executed if row.get("success") is False),
        "avg_elapsed_ms": _average(elapsed_values),
        "under_15s_rate": _rate(
            sum(1 for value in elapsed_values if value <= 15000),
            len(elapsed_values),
        ),
        "action_match_rate": _rate(
            sum(1 for row in executed if row.get("action_match") is True),
            len(executed),
        ),
        "amount_match_rate": _rate(
            sum(1 for row in executed if row.get("amount_match") is True),
            len(executed),
        ),
        "dangerous_flip_count": sum(
            1 for row in executed if row.get("dangerous_flip") is True
        ),
        "clear_check_to_bet_count": sum(
            1 for row in executed if row.get("clear_check_to_bet") is True
        ),
        "call_or_raise_to_fold_count": sum(
            1 for row in executed if row.get("call_or_raise_to_fold") is True
        ),
        "avg_score": _average(scores),
    }


def discover_primary_request_files(batch_dir: Path, phase: str) -> list[Path]:
    """Return primary request files for a phase, excluding diagnostic variants."""
    pattern = f"hand_*_req_*_{phase}.json"
    return sorted(
        path
        for path in batch_dir.glob(pattern)
        if not any(path.stem.endswith(suffix) for suffix in VARIANT_SUFFIXES)
    )


def find_compare_request_for_primary(primary_path: Path, batch_dir: Path) -> Path | None:
    """Find the nearest compare_no_allin request for a primary request."""
    parsed = parse_request_filename(primary_path)
    if parsed is None:
        return None
    hand_id, request_id, phase = parsed
    candidates = sorted(
        path
        for path in batch_dir.glob(f"hand_{hand_id}_req_*_{phase}_compare_no_allin.json")
        if parse_request_filename(path) is not None
    )
    if not candidates:
        return None

    later_candidates = [
        path
        for path in candidates
        if (parse_request_filename(path) or ("", -1, ""))[1] > request_id
    ]
    if later_candidates:
        return min(
            later_candidates,
            key=lambda path: (parse_request_filename(path) or ("", 0, ""))[1],
        )
    return candidates[0]


def parse_request_filename(path: Path) -> tuple[str, int, str] | None:
    """Parse hand id, request id, and phase from a solver request filename."""
    match = re.search(r"hand_(\d+)_req_(\d+)_(flop|turn|river)", path.stem)
    if match is None:
        return None
    return match.group(1), int(match.group(2)), match.group(3)


def build_batch_summary(
    *,
    total_primary_files: int,
    skipped_missing_compare: list[str],
    items: list[JsonDict],
) -> JsonDict:
    """Aggregate item-level comparison results into profile statistics."""
    compared = len(items)
    return {
        "total_primary_files": total_primary_files,
        "compared": compared,
        "skipped_missing_compare": len(skipped_missing_compare),
        "skipped_missing_compare_files": skipped_missing_compare,
        "profiles": {
            profile: _batch_profile_summary(items, profile)
            for profile in PROFILE_NAMES
        },
        "items": items,
    }


def _batch_profile_summary(items: list[JsonDict], profile: str) -> JsonDict:
    """Aggregate one profile's elapsed, success, and primary-match statistics."""
    elapsed_values = [
        int(item[f"{profile}_elapsed_ms"])
        for item in items
        if item.get(f"{profile}_elapsed_ms") is not None
    ]
    success_count = sum(1 for item in items if item.get(f"{profile}_success") is True)
    error_count = sum(1 for item in items if item.get(f"{profile}_success") is False)
    under_15s_count = sum(1 for value in elapsed_values if value <= 15000)
    summary: JsonDict = {
        "success_count": success_count,
        "error_count": error_count,
        "avg_elapsed_ms": _average(elapsed_values),
        "median_elapsed_ms": int(median(elapsed_values)) if elapsed_values else None,
        "under_15s_count": under_15s_count,
        "under_15s_rate": _rate(under_15s_count, len(elapsed_values)),
    }
    if profile != "primary":
        action_match_count = sum(
            1 for item in items if item.get(f"{profile}_action_match") is True
        )
        amount_match_count = sum(
            1 for item in items if item.get(f"{profile}_amount_match") is True
        )
        check_to_bet_count = sum(
            1
            for item in items
            if item.get("primary_action") == "CHECK"
            and item.get(f"{profile}_action") == "BET"
        )
        bet_to_check_count = sum(
            1
            for item in items
            if item.get("primary_action") == "BET"
            and item.get(f"{profile}_action") == "CHECK"
        )
        action_flip_count = sum(
            1
            for item in items
            if item.get("primary_action") != item.get(f"{profile}_action")
        )
        near_tie_mismatch_count = sum(
            1
            for item in items
            if item.get(f"{profile}_action_mismatch_near_tie") is True
        )
        dangerous_flip_count = sum(
            1
            for item in items
            if item.get(f"{profile}_dangerous_flip") is True
        )
        clear_check_to_bet_count = sum(
            1
            for item in items
            if item.get(f"{profile}_clear_check_to_bet") is True
        )
        call_or_raise_to_fold_count = sum(
            1
            for item in items
            if item.get(f"{profile}_call_or_raise_to_fold") is True
        )
        summary.update(
            {
                "action_match_count": action_match_count,
                "action_match_rate": _rate(action_match_count, len(items)),
                "amount_match_count": amount_match_count,
                "amount_match_rate": _rate(amount_match_count, len(items)),
                "action_flip_count": action_flip_count,
                "check_to_bet_count": check_to_bet_count,
                "bet_to_check_count": bet_to_check_count,
                "near_tie_mismatch_count": near_tie_mismatch_count,
                "dangerous_flip_count": dangerous_flip_count,
                "clear_check_to_bet_count": clear_check_to_bet_count,
                "call_or_raise_to_fold_count": call_or_raise_to_fold_count,
            }
        )
    return summary


def _batch_item_summary(primary_path: Path, result: JsonDict) -> JsonDict:
    """Return a compact summary row for one batch item."""
    item: JsonDict = {"sample_id": sample_id(primary_path)}
    for profile in PROFILE_NAMES:
        profile_result = result[profile]
        item[f"{profile}_success"] = profile_result["success"]
        item[f"{profile}_elapsed_ms"] = profile_result["elapsed_ms"]
        item[f"{profile}_action"] = profile_result["action"]
        item[f"{profile}_amount"] = profile_result["amount"]
        item[f"{profile}_error"] = profile_result["error"]
        probabilities = profile_result.get("probabilities")
        if not isinstance(probabilities, dict):
            probabilities = {}
        for key, value in probability_summary(probabilities).items():
            item[f"{profile}_{key}"] = value
    for profile in PROFILE_NAMES:
        if profile == "primary":
            continue
        item[f"{profile}_action_match"] = (
            item[f"{profile}_action"] == item["primary_action"]
        )
        item[f"{profile}_amount_match"] = (
            item[f"{profile}_amount"] == item["primary_amount"]
        )
        item[f"{profile}_under_15s"] = _under_15s(result[profile])
        item.update(_variant_margin_flags(item, profile))
    return item


def probability_summary(probabilities: dict[str, float]) -> JsonDict:
    """Return top/second action and margin from a probability mapping."""
    if not probabilities:
        return {
            "top_action": None,
            "top_probability": None,
            "second_action": None,
            "second_probability": None,
            "top_margin": None,
        }
    ranked = sorted(probabilities.items(), key=lambda item: item[1], reverse=True)
    top_action, top_probability = ranked[0]
    second_action = None
    second_probability = None
    if len(ranked) >= 2:
        second_action, second_probability = ranked[1]
    margin = None
    if second_probability is not None:
        margin = round(float(top_probability) - float(second_probability), 3)
    return {
        "top_action": parse_solver_action(str(top_action))[0],
        "top_probability": round(float(top_probability), 3),
        "second_action": (
            parse_solver_action(str(second_action))[0]
            if second_action is not None
            else None
        ),
        "second_probability": (
            round(float(second_probability), 3)
            if second_probability is not None
            else None
        ),
        "top_margin": margin,
    }


def _variant_margin_flags(item: JsonDict, profile: str) -> JsonDict:
    """Return mismatch severity flags for one variant against primary."""
    flags = _mismatch_flags(
        item.get("primary_action"),
        item.get(f"{profile}_action"),
        _optional_float(item.get("primary_top_margin")),
    )
    return {
        f"{profile}_action_mismatch_near_tie": flags["action_mismatch_near_tie"],
        f"{profile}_dangerous_flip": flags["dangerous_flip"],
        f"{profile}_clear_check_to_bet": flags["clear_check_to_bet"],
        f"{profile}_call_or_raise_to_fold": flags["call_or_raise_to_fold"],
    }


def _mismatch_flags(
    primary_action: object,
    variant_action: object,
    primary_margin: float | None,
) -> JsonDict:
    """Return mismatch severity flags for two actions."""
    action_mismatch = primary_action != variant_action
    near_tie_mismatch = (
        action_mismatch and primary_margin is not None and primary_margin <= 0.10
    )
    dangerous_flip = (
        action_mismatch and primary_margin is not None and primary_margin >= 0.20
    )
    clear_check_to_bet = (
        dangerous_flip and primary_action == "CHECK" and variant_action == "BET"
    )
    call_or_raise_to_fold = (
        primary_action in {"CALL", "RAISE"} and variant_action == "FOLD"
    )
    return {
        "action_mismatch_near_tie": near_tie_mismatch,
        "dangerous_flip": dangerous_flip,
        "clear_check_to_bet": clear_check_to_bet,
        "call_or_raise_to_fold": call_or_raise_to_fold,
    }


def _margin_class(top_margin: float | None) -> str:
    """Classify top_margin into clear / moderate / near_tie / unknown."""
    if top_margin is None:
        return "unknown"
    if top_margin >= 0.20:
        return "clear"
    if top_margin > 0.10:
        return "moderate"
    return "near_tie"


def _reason_overclaims_near_tie(reason: str) -> bool:
    """Return whether a near-tie explanation uses unqualified overclaim language."""
    sanitized = reason.lower()
    negated_patterns = (
        r"\bnot\s+because\s+it\s+clearly\s+dominates?\b",
        r"\bnot\s+because\s+\w+\s+clearly\s+dominates?\b",
        r"\bnot\s+a\s+clear\s+or\s+dominant\b",
        r"\bnot\s+clear\s+or\s+dominant\b",
        r"\bdoes\s+not\s+clearly\s+dominates?\b",
        r"\bnot\s+clearly\s+dominates?\b",
        r"\bnot\s+clear\b",
        r"\bnot\s+clearly\b",
        r"\bdoes\s+not\s+clearly\b",
        r"\bnot\s+dominant\b",
        r"\bdoes\s+not\s+dominate\b",
        r"\bno\s+action\s+is\s+dominant\b",
        r"\bno\s+dominant\b",
        r"\bnot\s+obvious\b",
        r"\bnot\s+strong\b",
        r"\bdoes\s+not\s+strongly\s+prefer\b",
        r"\brather\s+than\s+treating\s+it\s+as\s+dominant\b",
        r"\brather\s+than\s+treating\s+\w+\s+as\s+dominant\b",
    )
    for pattern in negated_patterns:
        sanitized = re.sub(pattern, " ", sanitized)

    overclaim_words = ("clear", "strongly prefers", "dominant", "obvious")
    return any(word in sanitized for word in overclaim_words)


def sample_id(primary_path: Path) -> str:
    """Return a stable sample id including hand, request id, and phase."""
    parsed = parse_request_filename(primary_path)
    if parsed is None:
        return primary_path.stem
    hand_id, request_id, phase = parsed
    return f"hand_{hand_id}_req_{request_id:06d}_{phase}"


def result_filename(primary_path: Path) -> str:
    """Return a deterministic result filename when hand and phase are present."""
    match = re.search(r"hand_(\d+).*_(flop|turn|river)", primary_path.stem)
    if match:
        return f"hand_{match.group(1)}_{match.group(2)}_compare_result.json"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"solver_compare_result_{timestamp}.json"


def batch_result_filename(primary_path: Path) -> str:
    """Return a batch item filename including request id."""
    return f"{sample_id(primary_path)}_compare_result.json"


def print_summary(result: JsonDict) -> None:
    """Print a compact comparison summary for manual CLI runs."""
    primary = result["primary"]
    compare = result["compare"]
    light = result["light"]
    middle = result["middle"]
    fast_middle = result["fast_middle"]
    summary = result["summary"]
    print(
        "PRIMARY: "
        f"success={primary['success']} elapsed_ms={primary['elapsed_ms']} "
        f"action={primary['action']} amount={primary['amount']}"
    )
    print(
        "COMPARE: "
        f"success={compare['success']} elapsed_ms={compare['elapsed_ms']} "
        f"action={compare['action']} amount={compare['amount']}"
    )
    print(
        "LIGHT: "
        f"success={light['success']} elapsed_ms={light['elapsed_ms']} "
        f"action={light['action']} amount={light['amount']}"
    )
    print(
        "MIDDLE: "
        f"success={middle['success']} elapsed_ms={middle['elapsed_ms']} "
        f"action={middle['action']} amount={middle['amount']}"
    )
    print(
        "FAST_MIDDLE: "
        f"success={fast_middle['success']} "
        f"elapsed_ms={fast_middle['elapsed_ms']} "
        f"action={fast_middle['action']} amount={fast_middle['amount']}"
    )
    print(
        "SUMMARY: "
        f"compare_speedup_ratio={summary['compare_speedup_ratio']} "
        f"light_speedup_ratio={summary['light_speedup_ratio']} "
        f"middle_speedup_ratio={summary['middle_speedup_ratio']} "
        f"fast_middle_speedup_ratio={summary['fast_middle_speedup_ratio']}"
    )
    print(f"RESULT_JSON: {result['output_path']}")


def print_batch_summary(summary: JsonDict) -> None:
    """Print compact aggregate statistics for a batch run."""
    print("BATCH SUMMARY")
    print(f"total_primary_files={summary['total_primary_files']}")
    print(f"compared={summary['compared']}")
    print(f"skipped_missing_compare={summary['skipped_missing_compare']}")
    print("")
    profiles = summary["profiles"]
    for label, profile in (
        ("PRIMARY", "primary"),
        ("COMPARE", "compare"),
        ("LIGHT", "light"),
        ("MIDDLE", "middle"),
        ("FAST_MIDDLE", "fast_middle"),
    ):
        stats = profiles[profile]
        line = (
            f"{label}: avg={stats['avg_elapsed_ms']}ms "
            f"median={stats['median_elapsed_ms']}ms "
            f"under_15s={_percent(stats['under_15s_rate'])}"
        )
        if profile != "primary":
            line += (
                f" action_match={_percent(stats['action_match_rate'])}"
                f" amount_match={_percent(stats['amount_match_rate'])}"
                f" check_to_bet={stats['check_to_bet_count']}"
                f" near_tie_mismatch={stats['near_tie_mismatch_count']}"
                f" dangerous_flip={stats['dangerous_flip_count']}"
                f" clear_check_to_bet={stats['clear_check_to_bet_count']}"
                f" call_or_raise_to_fold={stats['call_or_raise_to_fold_count']}"
                f" errors={stats['error_count']}"
            )
        else:
            line += f" errors={stats['error_count']}"
        print(line)
    print(f"RESULT_JSON: {summary['output_path']}")


def print_grid_summary(summary: JsonDict) -> None:
    """Print compact aggregate statistics for a grid run."""
    print("GRID SUMMARY")
    print(f"samples={summary['total_samples']}")
    print(f"planned={summary['total_planned_profiles']}")
    print(f"executed={summary['executed_count']}")
    print(f"skipped_by_pruning={summary['skipped_by_pruning_count']}")
    print("")
    print("TOP PROFILES")
    for index, profile in enumerate(summary["top_profiles_by_score"], start=1):
        print(
            f"{index}. {profile['profile_id']} "
            f"score={profile['avg_score']} "
            f"under_15s_rate={_percent(profile['under_15s_rate'])} "
            f"action_match_rate={_percent(profile['action_match_rate'])} "
            f"dangerous_flip={profile['dangerous_flip_count']}"
        )
    print(f"RESULT_JSON: {summary['output_path']}")


def print_repeatability_summary(summary: JsonDict) -> None:
    """Print compact aggregate statistics for a repeatability run."""
    print("REPEATABILITY SUMMARY")
    print(f"samples={summary['total_samples']}")
    print(f"unstable_sample_count={summary['unstable_sample_count']}")
    print(f"avg_elapsed_spread_ms={summary['avg_elapsed_spread_ms']}")
    for item in summary["items"]:
        print(
            f"{item['sample_id']}: "
            f"action_stable={item['action_stable']} "
            f"amount_stable={item['amount_stable']} "
            f"actions={item['action_set']} "
            f"amounts={item['amount_set']} "
            f"elapsed_spread_ms={item['elapsed_spread_ms']}"
        )
    print(f"RESULT_JSON: {summary['output_path']}")


def print_resident_timing_summary(summary: JsonDict) -> None:
    """Print compact aggregate statistics for resident timing runs."""
    print("RESIDENT TIMING SUMMARY")
    print(f"samples={summary['total_samples']}")
    print(f"start_ms={summary['start_ms']}")
    print(f"avg_resident_solve_ms={summary['avg_resident_solve_ms']}")
    print(f"process_reuse_effective_count={summary['process_reuse_effective_count']}")
    for item in summary["items"]:
        print(
            f"{item['sample_id']}: "
            f"avg_solve_ms={item['avg_resident_solve_ms']} "
            f"start_ms={item['start_ms']} "
            f"action_stable={item['action_stable']} "
            f"amount_stable={item['amount_stable']} "
            f"process_reuse_effective={item['process_reuse_effective']}"
        )
    print(f"RESULT_JSON: {summary['output_path']}")


def print_teacher_summary(summary: JsonDict) -> None:
    """Print compact aggregate statistics for teacher runs."""
    print("TEACHER SUMMARY")
    print(f"samples={summary['total_samples']}")
    print(f"profile={summary['profile']}")
    print(f"success_count={summary['success_count']}")
    print(f"error_count={summary['error_count']}")
    print(f"avg_elapsed_ms={summary['avg_elapsed_ms']}")
    print(f"max_elapsed_ms={summary['max_elapsed_ms']}")
    for item in summary["items"]:
        print(
            f"{item['sample_id']}: "
            f"success={item['success']} elapsed_ms={item['elapsed_ms']} "
            f"action={item['action']} amount={item['amount']} "
            f"top_margin={item['top_margin']}"
        )
    print(f"RESULT_JSON: {summary['output_path']}")


def print_single_size_summary(summary: JsonDict) -> None:
    """Print compact aggregate statistics for single-size diagnostics."""
    print("SINGLE-SIZE SUMMARY")
    print(f"samples={summary['total_samples']}")
    print(f"profile_count={summary['profile_count']}")
    print(f"planned_runs={summary['planned_runs']}")
    print(f"success_count={summary['success_count']}")
    print(f"error_count={summary['error_count']}")
    for profile_id, stats in summary["profile_summary"].items():
        print(
            f"{profile_id}: "
            f"aggressive={stats['aggressive_action_count']} "
            f"under_15s={_percent(stats['under_15s_rate'])} "
            f"avg_elapsed_ms={stats['avg_elapsed_ms']} "
            f"bet={stats['bet_count']} raise={stats['raise_count']} "
            f"all_in={stats['all_in_count']} check={stats['check_count']} "
            f"call={stats['call_count']} fold={stats['fold_count']}"
        )
    print(f"RESULT_JSON: {summary['output_path']}")


def print_sizing_teacher_summary(summary: JsonDict) -> None:
    """Print compact aggregate statistics for sizing teacher labels."""
    print("SIZING TEACHER SUMMARY")
    print(f"samples={summary['total_samples']}")
    print(f"allin_aggressive_count={summary['allin_aggressive_count']}")
    for label, count in summary["label_counts"].items():
        print(f"{label}={count}")
    print(f"RESULT_JSON: {summary['output_path']}")


def print_llm_sizing_summary(summary: JsonDict) -> None:
    """Print compact aggregate statistics for LLM sizing diagnostics."""
    print("LLM SIZING SUMMARY")
    print(f"samples={summary['total_samples']}")
    print(f"model={summary['model']}")
    print(f"success_count={summary['success_count']}")
    print(f"error_count={summary['error_count']}")
    print(f"under_15s_rate={_percent(summary['under_15s_rate'])}")
    print(f"teacher_alignment_rate={_percent(summary['teacher_alignment_rate'])}")
    print(f"sizing_allowed_match_count={summary['sizing_allowed_match_count']}")
    print(f"allin_violation_count={summary['allin_violation_count']}")
    print(
        "passive_teacher_aggressive_violation_count="
        f"{summary['passive_teacher_aggressive_violation_count']}"
    )
    print(f"sizing_type_invalid_count={summary['sizing_type_invalid_count']}")
    for label, stats in summary["label_summary"].items():
        print(
            f"{label}: total={stats['total']} "
            f"alignment={stats['teacher_alignment_count']}"
        )
    print(f"RESULT_JSON: {summary['output_path']}")


def print_llm_blind_summary(summary: JsonDict) -> None:
    """Print compact aggregate statistics for blind LLM diagnostics."""
    print("BLIND LLM SUMMARY")
    print(f"samples={summary['total_samples']}")
    print(f"model={summary['model']}")
    print(f"blind_profile={summary['blind_profile']}")
    print(f"success_count={summary['success_count']}")
    print(f"error_count={summary['error_count']}")
    print(f"under_15s_rate={_percent(summary['under_15s_rate'])}")
    print(f"blind_action_match_rate={_percent(summary['blind_action_match_rate'])}")
    print(
        f"blind_direction_match_rate="
        f"{_percent(summary['blind_direction_match_rate'])}"
    )
    print(
        f"blind_teacher_alignment_rate="
        f"{_percent(summary['blind_teacher_alignment_rate'])}"
    )
    print(
        "blind_sizing_allowed_match_count="
        f"{summary['blind_sizing_allowed_match_count']}"
    )
    print(f"blind_allin_violation_count={summary['blind_allin_violation_count']}")
    print(
        "blind_passive_teacher_aggressive_violation_count="
        f"{summary['blind_passive_teacher_aggressive_violation_count']}"
    )
    print(f"legal_action_invalid_count={summary['legal_action_invalid_count']}")
    print(f"RESULT_JSON: {summary['output_path']}")


def print_llm_blind_repeat_summary(summary: JsonDict) -> None:
    """Print compact aggregate statistics for blind LLM repeatability."""
    print("BLIND LLM REPEAT SUMMARY")
    print(f"blind_profile={summary['blind_profile']}")
    print(f"samples={summary['total_samples']}")
    print(f"repeat_count={summary['repeat_count']}")
    print(f"planned_runs={summary['planned_runs']}")
    print(f"success_count={summary['success_count']}")
    print(f"error_count={summary['error_count']}")
    print(f"under_15s_rate={_percent(summary['under_15s_rate'])}")
    print(
        "overall_blind_action_match_rate="
        f"{_percent(summary['overall_blind_action_match_rate'])}"
    )
    print(
        "overall_blind_direction_match_rate="
        f"{_percent(summary['overall_blind_direction_match_rate'])}"
    )
    print(
        "overall_blind_teacher_alignment_rate="
        f"{_percent(summary['overall_blind_teacher_alignment_rate'])}"
    )
    print(f"action_stable_sample_count={summary['action_stable_sample_count']}")
    print(
        "sizing_type_stable_sample_count="
        f"{summary['sizing_type_stable_sample_count']}"
    )
    print(
        "teacher_alignment_stable_sample_count="
        f"{summary['teacher_alignment_stable_sample_count']}"
    )
    print(f"allin_violation_count={summary['allin_violation_count']}")
    print(
        "passive_teacher_aggressive_violation_count="
        f"{summary['passive_teacher_aggressive_violation_count']}"
    )
    print(f"legal_action_invalid_count={summary['legal_action_invalid_count']}")
    print(f"unstable_sample_count={len(summary['unstable_samples'])}")
    print(f"RESULT_JSON: {summary['output_path']}")


def print_llm_summary(summary: JsonDict) -> None:
    """Print compact aggregate statistics for LLM diagnostics."""
    print("LLM SUMMARY")
    print(f"samples={summary['total_samples']}")
    print(f"model={summary['model']}")
    print(f"success_count={summary['success_count']}")
    print(f"error_count={summary['error_count']}")
    print(f"avg_llm_elapsed_ms={summary['avg_llm_elapsed_ms']}")
    print(f"under_15s_rate={_percent(summary['under_15s_rate'])}")
    print(f"action_match_rate={_percent(summary['action_match_rate'])}")
    print(f"direction_match_rate={_percent(summary['direction_match_rate'])}")
    print(f"dangerous_flip_count={summary['dangerous_flip_count']}")
    print(f"legal_action_invalid_count={summary['legal_action_invalid_count']}")
    print(f"confidence_overstated_count={summary['confidence_overstated_count']}")
    print(f"reason_overclaim_count={summary['reason_overclaim_count']}")
    for item in summary["items"]:
        print(
            f"{item['sample_id']}: "
            f"success={item['llm_success']} "
            f"baseline_action={item['baseline_action']} "
            f"llm_action={item['llm_action']} "
            f"dangerous_flip={item['dangerous_flip']} "
            f"confidence_overstated={item.get('confidence_overstated', False)} "
            f"reason_overclaim={item.get('reason_overclaim', False)}"
        )
    print(f"RESULT_JSON: {summary['output_path']}")


def main(argv: list[str] | None = None) -> int:
    """Run the comparison CLI.

    Args:
        argv: Optional argument list for tests.

    Returns:
        Process exit code.
    """
    load_env_file(override=True)
    parser = argparse.ArgumentParser(
        description="Compare primary and compare_no_allin solver request JSON files."
    )
    parser.add_argument("--primary", default=None, type=Path)
    parser.add_argument("--compare", default=None, type=Path)
    parser.add_argument("--light", default=None, type=Path)
    parser.add_argument("--middle", default=None, type=Path)
    parser.add_argument("--fast-middle", default=None, type=Path)
    parser.add_argument("--batch-dir", default=None, type=Path)
    parser.add_argument("--grid-dir", default=None, type=Path)
    parser.add_argument("--repeat-path", default=None, type=Path)
    parser.add_argument("--repeat-dir", default=None, type=Path)
    parser.add_argument("--resident-path", default=None, type=Path)
    parser.add_argument("--resident-dir", default=None, type=Path)
    parser.add_argument("--teacher-path", default=None, type=Path)
    parser.add_argument("--teacher-dir", default=None, type=Path)
    parser.add_argument("--teacher-profile", default="standard")
    parser.add_argument("--llm-path", default=None, type=Path)
    parser.add_argument("--llm-dir", default=None, type=Path)
    parser.add_argument("--llm-sizing-path", default=None, type=Path)
    parser.add_argument("--llm-sizing-dir", default=None, type=Path)
    parser.add_argument("--llm-blind-path", default=None, type=Path)
    parser.add_argument("--llm-blind-dir", default=None, type=Path)
    parser.add_argument("--llm-blind-repeat-path", default=None, type=Path)
    parser.add_argument("--llm-blind-repeat-dir", default=None, type=Path)
    parser.add_argument(
        "--blind-profile",
        choices=["baseline", "guided"],
        default="baseline",
    )
    parser.add_argument("--llm-model", default=None)
    parser.add_argument("--single-size-path", default=None, type=Path)
    parser.add_argument("--single-size-dir", default=None, type=Path)
    parser.add_argument("--sizing-teacher-path", default=None, type=Path)
    parser.add_argument("--sizing-teacher-dir", default=None, type=Path)
    parser.add_argument("--repeat-count", default=5, type=int)
    parser.add_argument("--phase", default="flop")
    parser.add_argument("--sample-ids", default=None)
    parser.add_argument("--timeout", default=30.0, type=float)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)

    sample_ids = None
    if args.sample_ids:
        sample_ids = [
            sample_id_text.strip()
            for sample_id_text in args.sample_ids.split(",")
            if sample_id_text.strip()
        ]

    if args.repeat_path is not None or args.repeat_dir is not None:
        result = compare_solver_requests_repeat(
            repeat_path=args.repeat_path,
            repeat_dir=args.repeat_dir,
            sample_ids=sample_ids,
            repeat_count=args.repeat_count,
            timeout=args.timeout,
            out_dir=args.out,
        )
        print_repeatability_summary(result)
        return 0

    if args.teacher_path is not None or args.teacher_dir is not None:
        result = compare_solver_requests_teacher(
            teacher_path=args.teacher_path,
            teacher_dir=args.teacher_dir,
            sample_ids=sample_ids,
            teacher_profile=args.teacher_profile,
            timeout=args.timeout,
            out_dir=args.out,
        )
        print_teacher_summary(result)
        return 0

    if args.llm_path is not None or args.llm_dir is not None:
        result = compare_solver_requests_llm(
            llm_path=args.llm_path,
            llm_dir=args.llm_dir,
            sample_ids=sample_ids,
            llm_model=args.llm_model,
            timeout=args.timeout,
            out_dir=args.out,
        )
        print_llm_summary(result)
        return 0

    if args.llm_sizing_path is not None or args.llm_sizing_dir is not None:
        result = compare_solver_requests_llm_sizing(
            llm_sizing_path=args.llm_sizing_path,
            llm_sizing_dir=args.llm_sizing_dir,
            sizing_teacher_path=args.sizing_teacher_path,
            sizing_teacher_dir=args.sizing_teacher_dir,
            phase=args.phase,
            sample_ids=sample_ids,
            llm_model=args.llm_model,
            timeout=args.timeout,
            out_dir=args.out,
        )
        print_llm_sizing_summary(result)
        return 0

    if (
        args.llm_blind_repeat_path is not None
        or args.llm_blind_repeat_dir is not None
    ):
        result = compare_solver_requests_llm_blind_repeat(
            llm_blind_repeat_path=args.llm_blind_repeat_path,
            llm_blind_repeat_dir=args.llm_blind_repeat_dir,
            sizing_teacher_path=args.sizing_teacher_path,
            sizing_teacher_dir=args.sizing_teacher_dir,
            phase=args.phase,
            sample_ids=sample_ids,
            llm_model=args.llm_model,
            timeout=args.timeout,
            blind_profile=args.blind_profile,
            repeat_count=args.repeat_count,
            out_dir=args.out,
        )
        print_llm_blind_repeat_summary(result)
        return 0

    if args.llm_blind_path is not None or args.llm_blind_dir is not None:
        result = compare_solver_requests_llm_blind(
            llm_blind_path=args.llm_blind_path,
            llm_blind_dir=args.llm_blind_dir,
            sizing_teacher_path=args.sizing_teacher_path,
            sizing_teacher_dir=args.sizing_teacher_dir,
            phase=args.phase,
            sample_ids=sample_ids,
            llm_model=args.llm_model,
            timeout=args.timeout,
            out_dir=args.out,
            blind_profile=args.blind_profile,
        )
        print_llm_blind_summary(result)
        return 0

    if args.single_size_path is not None or args.single_size_dir is not None:
        result = compare_solver_requests_single_size(
            single_size_path=args.single_size_path,
            single_size_dir=args.single_size_dir,
            phase=args.phase,
            sample_ids=sample_ids,
            timeout=args.timeout,
            out_dir=args.out,
        )
        print_single_size_summary(result)
        return 0

    if args.sizing_teacher_path is not None or args.sizing_teacher_dir is not None:
        result = compare_solver_requests_sizing_teacher(
            sizing_teacher_path=args.sizing_teacher_path,
            sizing_teacher_dir=args.sizing_teacher_dir,
            out_dir=args.out,
        )
        print_sizing_teacher_summary(result)
        return 0

    if args.resident_path is not None or args.resident_dir is not None:
        result = compare_solver_requests_resident(
            resident_path=args.resident_path,
            resident_dir=args.resident_dir,
            sample_ids=sample_ids,
            repeat_count=args.repeat_count,
            timeout=args.timeout,
            out_dir=args.out,
        )
        print_resident_timing_summary(result)
        return 0

    if args.grid_dir is not None:
        result = compare_solver_requests_grid(
            args.grid_dir,
            phase=args.phase,
            sample_ids=sample_ids,
            timeout=args.timeout,
            out_dir=args.out,
        )
        print_grid_summary(result)
        return 0

    if args.batch_dir is not None:
        result = compare_solver_requests_batch(
            args.batch_dir,
            phase=args.phase,
            timeout=args.timeout,
            out_dir=args.out,
        )
        print_batch_summary(result)
        return 0

    if args.primary is None or args.compare is None:
        parser.error("--primary and --compare are required unless --batch-dir is used")

    result = compare_solver_requests(
        args.primary,
        args.compare,
        light_path=args.light,
        middle_path=args.middle,
        fast_middle_path=args.fast_middle,
        timeout=args.timeout,
        out_dir=args.out,
    )
    print_summary(result)
    return 0


def _average(values: list[int]) -> int | None:
    if not values:
        return None
    return int(sum(values) / len(values))


def _bool_rate(items: list[JsonDict], key: str) -> float | None:
    if not items:
        return None
    return _rate(sum(1 for item in items if item.get(key) is True), len(items))


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 3)


def _percent(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.1f}%"


def _extract_probabilities(result: JsonDict) -> JsonDict:
    probabilities = result.get("probabilities")
    if isinstance(probabilities, dict):
        return {
            str(action): float(probability)
            for action, probability in probabilities.items()
        }

    strategy = result.get("node_strategy") or result.get("root_strategy")
    if not isinstance(strategy, dict):
        return {}
    average_strategy = strategy.get("average_strategy")
    if isinstance(average_strategy, dict):
        return {
            str(action): float(probability)
            for action, probability in average_strategy.items()
        }
    return {}


def _optional_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _optional_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
