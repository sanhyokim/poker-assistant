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
        "path": str(path),
        "success": bool(raw_result.get("success")),
        "elapsed_ms": elapsed_ms,
        "action": action,
        "amount": amount,
        "probabilities": probabilities,
        "error": raw_result.get("error"),
    }


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


def build_summary(primary: JsonDict, compare: JsonDict) -> JsonDict:
    """Build comparison summary fields for two normalized solver results."""
    primary_elapsed = _optional_float(primary.get("elapsed_ms"))
    compare_elapsed = _optional_float(compare.get("elapsed_ms"))
    speedup_ratio = None
    if primary_elapsed is not None and compare_elapsed and compare_elapsed > 0:
        speedup_ratio = round(primary_elapsed / compare_elapsed, 3)

    return {
        "action_match": primary.get("action") == compare.get("action"),
        "amount_match": primary.get("amount") == compare.get("amount"),
        "speedup_ratio": speedup_ratio,
    }


def compare_solver_requests(
    primary_path: Path,
    compare_path: Path,
    *,
    timeout: float,
    out_dir: Path,
    bridge_factory: BridgeFactory = PostflopSolverBridge,
) -> JsonDict:
    """Run primary and compare requests in separate solver processes.

    Args:
        primary_path: Primary request JSON path.
        compare_path: compare_no_allin request JSON path.
        timeout: Timeout seconds per request.
        out_dir: Directory where the comparison result JSON is saved.
        bridge_factory: Factory used by tests to inject a fake bridge.

    Returns:
        Full comparison result dictionary.
    """
    primary = run_solver_request(
        primary_path,
        timeout=timeout,
        bridge_factory=bridge_factory,
    )
    compare = run_solver_request(
        compare_path,
        timeout=timeout,
        bridge_factory=bridge_factory,
    )
    result = {
        "primary": primary,
        "compare": compare,
        "summary": build_summary(primary, compare),
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
        "SUMMARY: "
        f"action_match={summary['action_match']} "
        f"amount_match={summary['amount_match']} "
        f"speedup_ratio={summary['speedup_ratio']}"
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
    parser.add_argument("--timeout", default=30.0, type=float)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)

    result = compare_solver_requests(
        args.primary,
        args.compare,
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
