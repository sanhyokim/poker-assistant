"""Run number recognition on test screenshots and compare with ground truth.

Usage:
    python scripts/analyze_numbers.py

Output:
    scripts/number_analysis_results.json
"""

import json
import logging
import pathlib
import sys
from typing import Any

import cv2
import yaml

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from recognition.number_recognizer import NumberRecognizer  # noqa: E402

logger = logging.getLogger(__name__)

TARGET_KEYS = [
    "cp_01",
    "cp_02",
    "cp_03",
    "cp_04",
    "cp_05",
    "cp_06",
    "cp_07",
    "cp_07b",
    "cp_08",
]


def load_json(path: pathlib.Path) -> dict[str, Any]:
    """Load a JSON file.

    Args:
        path: JSON file path.

    Returns:
        Parsed JSON dictionary.
    """
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_yaml(path: pathlib.Path) -> dict[str, Any]:
    """Load a YAML file.

    Args:
        path: YAML file path.

    Returns:
        Parsed YAML dictionary.
    """
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def compare_value(recognized: int | None, expected: int | None) -> dict[str, Any]:
    """Build a comparison object for one scalar number field.

    Args:
        recognized: Recognized value.
        expected: Expected ground-truth value.

    Returns:
        Comparison dictionary.
    """
    return {
        "recognized": recognized,
        "expected": expected,
        "match": recognized == expected,
    }


def compare_optional_value(
    recognized: int | None,
    expected_source: dict[str, Any],
    key: str,
) -> dict[str, Any] | None:
    """Compare a scalar field only when ground truth provides the key.

    Args:
        recognized: Recognized value.
        expected_source: Ground-truth screenshot entry.
        key: Field name to compare.

    Returns:
        Comparison dictionary, or None when the field is not measured.
    """
    if key not in expected_source:
        return None
    return compare_value(recognized, expected_source.get(key))


def compare_seat_values(
    recognized: dict[str, int | None],
    expected_players: dict[str, dict[str, int | None]] | None,
    value_key: str,
) -> dict[str, Any]:
    """Compare recognized seat values with ground-truth player entries.

    Args:
        recognized: Recognized values by seat.
        expected_players: Ground-truth players dictionary.
        value_key: Either stack or bet.

    Returns:
        Per-seat comparison dictionary.
    """
    comparisons: dict[str, Any] = {}
    if expected_players is None:
        return comparisons
    for seat, value in recognized.items():
        if seat not in expected_players or value_key not in expected_players[seat]:
            continue
        expected = expected_players[seat].get(value_key)
        comparisons[seat] = compare_value(value, expected)
    return comparisons


def all_matches(comparisons: dict[str, Any]) -> bool:
    """Return whether all nested comparison objects match.

    Args:
        comparisons: Comparison dictionary.

    Returns:
        True if every comparison has match=True.
    """
    for value in comparisons.values():
        if value is None:
            continue
        if isinstance(value, dict) and "match" in value:
            if value["match"] is not True:
                return False
        elif isinstance(value, dict) and not all_matches(value):
            return False
    return True


def analyze_numbers() -> dict[str, Any]:
    """Analyze configured screenshots with NumberRecognizer.

    Returns:
        JSON-serializable number analysis results.
    """
    config = load_yaml(PROJECT_ROOT / "config.yaml")
    profile = load_json(PROJECT_ROOT / "profiles" / "coinpoker_6max.json")
    ground_truth = load_json(
        PROJECT_ROOT / "tests" / "fixtures" / "ground_truth" / "coinpoker.json"
    )["screenshots"]
    screenshots_dir = (
        PROJECT_ROOT / "tests" / "fixtures" / "screenshots" / "coinpoker"
    )
    recognizer = NumberRecognizer(profile=profile, config=config)
    results: list[dict[str, Any]] = []

    for key in TARGET_KEYS:
        expected = ground_truth[key]
        image_path = screenshots_dir / expected["filename"]
        item: dict[str, Any] = {
            "key": key,
            "filename": expected["filename"],
            "exists": image_path.exists(),
            "recognized": None,
            "comparisons": None,
            "match": False,
        }
        if not image_path.exists():
            logger.warning("Screenshot not found: %s", image_path)
            results.append(item)
            continue

        img = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if img is None:
            logger.warning("Failed to read screenshot: %s", image_path)
            results.append(item)
            continue

        recognized = recognizer.recognize_all(img)
        comparisons = {
            "pot": compare_optional_value(recognized["pot"], expected, "pot"),
            "hero_stack": compare_optional_value(
                recognized["hero_stack"],
                expected,
                "hero_stack",
            ),
            "hero_bet": compare_optional_value(
                recognized["hero_bet"],
                expected,
                "hero_bet",
            ),
            "player_stacks": compare_seat_values(
                recognized["player_stacks"],
                expected.get("players"),
                "stack",
            ),
            "player_bets": compare_seat_values(
                recognized["player_bets"],
                expected.get("players"),
                "bet",
            ),
        }
        item["recognized"] = recognized
        item["comparisons"] = comparisons
        item["match"] = all_matches(comparisons)
        results.append(item)

    return {
        "meta": {
            "description": "Number recognition analysis for CoinPoker screenshots",
            "target_keys": TARGET_KEYS,
        },
        "results": results,
    }


def main() -> None:
    """Run number analysis and write scripts/number_analysis_results.json."""
    logging.basicConfig(level=logging.INFO)
    output_path = PROJECT_ROOT / "scripts" / "number_analysis_results.json"
    results = analyze_numbers()
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"Wrote number analysis results to {output_path}")


if __name__ == "__main__":
    main()
