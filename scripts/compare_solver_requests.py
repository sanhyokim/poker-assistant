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
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from solver.solver_bridge import PostflopSolverBridge

JsonDict = dict[str, Any]
BridgeFactory = Callable[[], PostflopSolverBridge]
VARIANT_SUFFIXES = (
    "_compare_no_allin",
    "_light_probe",
    "_middle_probe",
    "_fast_middle_probe",
)
PROFILE_NAMES = ("primary", "compare", "light", "middle", "fast_middle")


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
    primary_action = item.get("primary_action")
    variant_action = item.get(f"{profile}_action")
    primary_margin = _optional_float(item.get("primary_top_margin"))
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
        f"{profile}_action_mismatch_near_tie": near_tie_mismatch,
        f"{profile}_dangerous_flip": dangerous_flip,
        f"{profile}_clear_check_to_bet": clear_check_to_bet,
        f"{profile}_call_or_raise_to_fold": call_or_raise_to_fold,
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


def main(argv: list[str] | None = None) -> int:
    """Run the comparison CLI.

    Args:
        argv: Optional argument list for tests.

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(
        description="Compare primary and compare_no_allin solver request JSON files."
    )
    parser.add_argument("--primary", default=None, type=Path)
    parser.add_argument("--compare", default=None, type=Path)
    parser.add_argument("--light", default=None, type=Path)
    parser.add_argument("--middle", default=None, type=Path)
    parser.add_argument("--fast-middle", default=None, type=Path)
    parser.add_argument("--batch-dir", default=None, type=Path)
    parser.add_argument("--phase", default="flop")
    parser.add_argument("--timeout", default=30.0, type=float)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)

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
