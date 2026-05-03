"""Run button and dealer recognition and compare with ground truth.

Usage:
    python scripts/analyze_buttons.py

Output:
    scripts/button_analysis_results.json
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

from recognition.button_recognizer import ButtonRecognizer  # noqa: E402
from recognition.dealer_recognizer import DealerRecognizer  # noqa: E402

logger = logging.getLogger(__name__)

PRIMARY_KEYS = [
    "cp_01",
    "cp_02",
    "cp_03",
    "cp_04",
    "cp_05",
    "cp_06",
    "cp_07",
    "cp_07b",
    "cp_08",
    "cp_09",
    "cp_10",
    "cp_11",
    "cp_12",
    "cp_13",
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


def compare_value(recognized: Any, expected: Any) -> dict[str, Any]:
    """Build a comparison object.

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


def all_matches(comparisons: dict[str, Any]) -> bool:
    """Return whether all comparison objects match.

    Args:
        comparisons: Comparison dictionary.

    Returns:
        True if all present comparison values match.
    """
    for value in comparisons.values():
        if value is None:
            continue
        if isinstance(value, dict) and "match" in value:
            if value["match"] is not True:
                return False
    return True


def analyze_buttons() -> dict[str, Any]:
    """Analyze primary screenshots with button and dealer recognizers.

    Returns:
        JSON-serializable analysis result.
    """
    config = load_yaml(PROJECT_ROOT / "config.yaml")
    profile = load_json(PROJECT_ROOT / "profiles" / "coinpoker_6max.json")
    ground_truth = load_json(
        PROJECT_ROOT / "tests" / "fixtures" / "ground_truth" / "coinpoker.json"
    )["screenshots"]
    screenshots_dir = (
        PROJECT_ROOT / "tests" / "fixtures" / "screenshots" / "coinpoker"
    )
    button_recognizer = ButtonRecognizer(profile=profile, config=config)
    dealer_recognizer = DealerRecognizer(profile=profile, config=config)
    results: list[dict[str, Any]] = []

    for key in PRIMARY_KEYS:
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

        recognized = {
            "is_my_turn": button_recognizer.detect_my_turn(img),
            "buttons": button_recognizer.classify_buttons(img),
            "dealer_seat": dealer_recognizer.detect_dealer_seat(img),
        }
        comparisons = {
            "is_my_turn": compare_value(
                recognized["is_my_turn"],
                expected.get("is_my_turn"),
            ),
            "buttons": compare_value(recognized["buttons"], expected.get("buttons")),
            "dealer_seat": (
                compare_value(recognized["dealer_seat"], expected.get("dealer_seat"))
                if "dealer_seat" in expected
                else None
            ),
        }
        item["recognized"] = recognized
        item["comparisons"] = comparisons
        item["match"] = all_matches(comparisons)
        results.append(item)

    return {
        "meta": {
            "description": "Button and dealer recognition analysis",
            "target_keys": PRIMARY_KEYS,
        },
        "results": results,
    }


def main() -> None:
    """Run analysis and write scripts/button_analysis_results.json."""
    logging.basicConfig(level=logging.INFO)
    output_path = PROJECT_ROOT / "scripts" / "button_analysis_results.json"
    results = analyze_buttons()
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"Wrote button analysis results to {output_path}")


if __name__ == "__main__":
    main()
