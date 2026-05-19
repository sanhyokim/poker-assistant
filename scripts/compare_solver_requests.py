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
from urllib import error as urllib_error
from urllib import request as urllib_request

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from solver.solver_bridge import PostflopSolverBridge

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


def load_env_file(env_path: Path | None = None) -> None:
    """Load simple KEY=VALUE lines from .env without overriding env vars."""
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
        "primary_solver_probabilities": baseline.get("probabilities", {}),
    }
    return (
        "You are evaluating a heads-up no-limit hold'em flop decision.\n"
        "Primary Solver is the anchor. Do not make a large deviation from "
        "primary Solver unless the board texture strongly justifies it.\n"
        "If primary top_margin >= 0.20, match primary action direction.\n"
        "If primary top_margin <= 0.10, small sizing variation is allowed.\n"
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
        }
    return {
        "success": True,
        "elapsed_ms": elapsed_ms,
        "decision": decision,
        "error": None,
    }


def call_openrouter_llm(prompt: str, model: str, timeout: float) -> JsonDict:
    """Call OpenRouter chat completions for diagnostics only."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        return {"success": False, "error": "OPENROUTER_API_KEY missing"}
    request_body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 180,
        "reasoning": {"effort": "none"},
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "hu_flop_decision",
                "strict": True,
                "schema": llm_decision_schema(),
            },
        },
    }
    data = json.dumps(request_body).encode("utf-8")
    http_request = urllib_request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib_request.urlopen(http_request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib_error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {"success": False, "error": str(exc)}
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        return {"success": False, "error": f"Invalid OpenRouter response: {exc}"}
    return {"success": True, "raw_content": content}


def llm_decision_schema() -> JsonDict:
    """Return the JSON schema for LLM diagnostic decisions."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "action",
            "amount",
            "sizing_type",
            "confidence",
            "reason",
            "risk_flags",
        ],
        "properties": {
            "action": {"type": "string", "enum": sorted(LLM_ALLOWED_ACTIONS)},
            "amount": {"type": "integer", "minimum": 0},
            "sizing_type": {"type": "string"},
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
        }
    baseline_action = baseline.get("action")
    llm_action = decision.get("action")
    flags = _mismatch_flags(
        baseline_action,
        llm_action,
        _optional_float(baseline_probability.get("top_margin")),
    )
    action_match = baseline_action == llm_action
    amount_match = baseline.get("amount") == decision.get("amount")
    legal_action_valid = llm_action in set(legal_actions)
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
                "error": item["llm_error"],
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
    for item in summary["items"]:
        print(
            f"{item['sample_id']}: "
            f"success={item['llm_success']} "
            f"baseline_action={item['baseline_action']} "
            f"llm_action={item['llm_action']} "
            f"dangerous_flip={item['dangerous_flip']}"
        )
    print(f"RESULT_JSON: {summary['output_path']}")


def main(argv: list[str] | None = None) -> int:
    """Run the comparison CLI.

    Args:
        argv: Optional argument list for tests.

    Returns:
        Process exit code.
    """
    load_env_file()
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
    parser.add_argument("--llm-model", default=None)
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
