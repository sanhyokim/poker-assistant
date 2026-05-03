"""Run card recognition on primary test screenshots and write JSON results.

Usage:
    python scripts/analyze_screenshots.py

Output:
    scripts/analysis_results.json
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

from recognition.card_recognizer import CardRecognizer  # noqa: E402

logger = logging.getLogger(__name__)

PRIMARY_IMAGES = [
    "cp_01_preflop_my_turnb.png",
    "cp_02_preflop_not_my_turn.png",
    "cp_03_flop_my_turn.png",
    "cp_04_flop_not_my_turn.png",
    "cp_05_turn.png",
    "cp_06_river.png",
    "cp_07_showdown.png",
    "cp_07_showdownb.png",
    "cp_08_allin.png",
    "cp_09_player_away.png",
    "cp_10_full_table_6players.png",
    "cp_11_timebank_countdown.png",
    "cp_12_folded_spectating.png",
    "cp_13_between_hands.png",
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


def analyze_screenshots() -> dict[str, Any]:
    """Analyze primary screenshots with CardRecognizer.

    Returns:
        Analysis result dictionary suitable for JSON output.
    """
    config = load_yaml(PROJECT_ROOT / "config.yaml")
    profile = load_json(PROJECT_ROOT / "profiles" / "coinpoker_6max.json")
    screenshots_dir = (
        PROJECT_ROOT / "tests" / "fixtures" / "screenshots" / "coinpoker"
    )
    recognizer = CardRecognizer(profile=profile, config=config)
    results: list[dict[str, Any]] = []

    for filename in PRIMARY_IMAGES:
        image_path = screenshots_dir / filename
        item: dict[str, Any] = {
            "filename": filename,
            "exists": image_path.exists(),
            "hero_cards": [],
            "board_cards": [],
            "board_card_count": 0,
            "details": None,
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

        details = recognizer.analyze_cards(img)
        item["hero_cards"] = recognizer.recognize_hero_cards(img)
        item["board_cards"] = recognizer.recognize_board_cards(img)
        item["board_card_count"] = recognizer.count_board_cards(img)
        item["details"] = details
        results.append(item)

    return {
        "meta": {
            "description": "Card recognition analysis for primary screenshots",
            "image_count": len(PRIMARY_IMAGES),
        },
        "results": results,
    }


def main() -> None:
    """Run screenshot analysis and write scripts/analysis_results.json."""
    logging.basicConfig(level=logging.INFO)
    output_path = PROJECT_ROOT / "scripts" / "analysis_results.json"
    results = analyze_screenshots()
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"Wrote analysis results to {output_path}")


if __name__ == "__main__":
    main()
