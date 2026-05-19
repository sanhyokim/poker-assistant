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
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from solver.solver_bridge import PostflopSolverBridge

JsonDict = dict[str, Any]
BridgeFactory = Callable[[], PostflopSolverBridge]


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


def compare_solver_requests(
    primary_path: Path,
    compare_path: Path,
    *,
    light_path: Path | None = None,
    middle_path: Path | None = None,
    timeout: float,
    out_dir: Path,
    bridge_factory: BridgeFactory = PostflopSolverBridge,
) -> JsonDict:
    """Run primary, compare, light, and middle requests in separate processes.

    Args:
        primary_path: Primary request JSON path.
        compare_path: compare_no_allin request JSON path.
        light_path: Optional prebuilt light_probe request JSON path.
        middle_path: Optional prebuilt middle_probe request JSON path.
        timeout: Timeout seconds per request.
        out_dir: Directory where the comparison result JSON is saved.
        bridge_factory: Factory used by tests to inject a fake bridge.

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
    light_label = str(light_path) if light_path is not None else (
        f"generated_light_probe_from:{primary_path}"
    )
    middle_label = str(middle_path) if middle_path is not None else (
        f"generated_middle_probe_from:{primary_path}"
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
    result = {
        "primary": primary,
        "compare": compare,
        "light": light,
        "middle": middle,
        "summary": build_summary(primary, compare, light, middle),
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    output_path = out_dir / result_filename(primary_path)
    with output_path.open("w", encoding="utf-8") as file:
        json.dump(result, file, ensure_ascii=False, indent=2)
    result["output_path"] = str(output_path)
    return result


def result_filename(primary_path: Path) -> str:
    """Return a deterministic result filename when hand and phase are present."""
    match = re.search(r"hand_(\d+).*_(flop|turn|river)", primary_path.stem)
    if match:
        return f"hand_{match.group(1)}_{match.group(2)}_compare_result.json"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"solver_compare_result_{timestamp}.json"


def print_summary(result: JsonDict) -> None:
    """Print a compact comparison summary for manual CLI runs."""
    primary = result["primary"]
    compare = result["compare"]
    light = result["light"]
    middle = result["middle"]
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
        "SUMMARY: "
        f"compare_speedup_ratio={summary['compare_speedup_ratio']} "
        f"light_speedup_ratio={summary['light_speedup_ratio']} "
        f"middle_speedup_ratio={summary['middle_speedup_ratio']}"
    )
    print(f"RESULT_JSON: {result['output_path']}")


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
    parser.add_argument("--primary", required=True, type=Path)
    parser.add_argument("--compare", required=True, type=Path)
    parser.add_argument("--light", default=None, type=Path)
    parser.add_argument("--middle", default=None, type=Path)
    parser.add_argument("--timeout", default=30.0, type=float)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)

    result = compare_solver_requests(
        args.primary,
        args.compare,
        light_path=args.light,
        middle_path=args.middle,
        timeout=args.timeout,
        out_dir=args.out,
    )
    print_summary(result)
    return 0


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
