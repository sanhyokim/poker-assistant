"""Number recognizer unit tests.

Phase 4 acceptance criteria:
- Number recognition passes on all 14 primary test screenshots.
- Pot label colors are filtered so only numeric text is extracted.
- Empty regions return None.
"""

from collections.abc import Callable
from typing import Any

import cv2
import numpy as np
import pytest

from recognition.number_recognizer import NumberRecognizer

ImageLoader = Callable[[str], np.ndarray]

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


@pytest.fixture(scope="module")
def number_recognizer(
    profile: dict[str, Any],
    config: dict[str, Any],
) -> NumberRecognizer:
    """Return a shared NumberRecognizer instance."""
    return NumberRecognizer(profile, config)


@pytest.fixture(scope="module")
def screenshots(ground_truth: dict[str, Any]) -> dict[str, Any]:
    """Return screenshot ground truth entries."""
    return ground_truth["screenshots"]


@pytest.fixture(scope="module")
def load_image(screenshots_dir: Any) -> ImageLoader:
    """Return a helper that loads screenshots as BGR images.

    Args:
        screenshots_dir: Directory containing CoinPoker screenshots.

    Returns:
        Callable that loads one screenshot by filename.
    """

    def _load(filename: str) -> np.ndarray:
        path = screenshots_dir / filename
        img = cv2.imread(str(path), cv2.IMREAD_COLOR)
        assert img is not None, f"Image not found: {path}"
        return img

    return _load


@pytest.fixture(scope="module")
def number_results(
    number_recognizer: NumberRecognizer,
    screenshots: dict[str, Any],
    load_image: ImageLoader,
) -> dict[str, dict[str, Any]]:
    """Recognize all number fields for the primary screenshots once."""
    results: dict[str, dict[str, Any]] = {}
    for key in PRIMARY_KEYS:
        img = load_image(screenshots[key]["filename"])
        results[key] = number_recognizer.recognize_all(img)
    return results


class TestPotRecognition:
    """Pot recognition tests."""

    @pytest.mark.parametrize("key", PRIMARY_KEYS)
    def test_all_known_pots(
        self,
        key: str,
        number_results: dict[str, dict[str, Any]],
        screenshots: dict[str, Any],
    ) -> None:
        """All primary screenshots match ground truth pot values."""
        assert number_results[key]["pot"] == screenshots[key].get("pot")

    def test_cp01_pot(
        self,
        number_results: dict[str, dict[str, Any]],
        screenshots: dict[str, Any],
    ) -> None:
        """cp_01: pot=198."""
        assert number_results["cp_01"]["pot"] == screenshots["cp_01"]["pot"]

    def test_cp02_pot(
        self,
        number_results: dict[str, dict[str, Any]],
        screenshots: dict[str, Any],
    ) -> None:
        """cp_02: pot=12378."""
        assert number_results["cp_02"]["pot"] == screenshots["cp_02"]["pot"]

    def test_cp03_pot(
        self,
        number_results: dict[str, dict[str, Any]],
        screenshots: dict[str, Any],
    ) -> None:
        """cp_03: pot=348."""
        assert number_results["cp_03"]["pot"] == screenshots["cp_03"]["pot"]

    def test_cp07_pot(
        self,
        number_results: dict[str, dict[str, Any]],
        screenshots: dict[str, Any],
    ) -> None:
        """cp_07: pot=33232 on showdown screen."""
        assert number_results["cp_07"]["pot"] == screenshots["cp_07"]["pot"]

    def test_cp07b_pot(
        self,
        number_results: dict[str, dict[str, Any]],
        screenshots: dict[str, Any],
    ) -> None:
        """cp_07b: pot=165683."""
        assert number_results["cp_07b"]["pot"] == screenshots["cp_07b"]["pot"]

    def test_cp08_pot(
        self,
        number_results: dict[str, dict[str, Any]],
        screenshots: dict[str, Any],
    ) -> None:
        """cp_08: pot=7784."""
        assert number_results["cp_08"]["pot"] == screenshots["cp_08"]["pot"]

    def test_cp09_pot_none(
        self,
        number_results: dict[str, dict[str, Any]],
        screenshots: dict[str, Any],
    ) -> None:
        """cp_09: pot=None on waiting screen."""
        assert number_results["cp_09"]["pot"] == screenshots["cp_09"]["pot"]

    def test_cp13_pot(
        self,
        number_results: dict[str, dict[str, Any]],
        screenshots: dict[str, Any],
    ) -> None:
        """cp_13: pot=96 between hands."""
        assert number_results["cp_13"]["pot"] == screenshots["cp_13"]["pot"]


class TestHeroStackRecognition:
    """Hero stack recognition tests."""

    def test_cp01_hero_stack(
        self,
        number_results: dict[str, dict[str, Any]],
        screenshots: dict[str, Any],
    ) -> None:
        """cp_01: hero_stack=3802."""
        assert number_results["cp_01"]["hero_stack"] == screenshots["cp_01"]["hero_stack"]

    def test_cp02_hero_stack(
        self,
        number_results: dict[str, dict[str, Any]],
        screenshots: dict[str, Any],
    ) -> None:
        """cp_02: hero_stack=3686."""
        assert number_results["cp_02"]["hero_stack"] == screenshots["cp_02"]["hero_stack"]

    def test_cp08_hero_stack_zero(
        self,
        number_results: dict[str, dict[str, Any]],
        screenshots: dict[str, Any],
    ) -> None:
        """cp_08: hero_stack=0 after all-in."""
        assert number_results["cp_08"]["hero_stack"] == screenshots["cp_08"]["hero_stack"]


class TestHeroBetRecognition:
    """Hero bet recognition tests."""

    def test_cp01_hero_bet_none(
        self,
        number_results: dict[str, dict[str, Any]],
        screenshots: dict[str, Any],
    ) -> None:
        """cp_01: hero_bet=None."""
        assert number_results["cp_01"]["hero_bet"] == screenshots["cp_01"]["hero_bet"]

    def test_cp02_hero_bet(
        self,
        number_results: dict[str, dict[str, Any]],
        screenshots: dict[str, Any],
    ) -> None:
        """cp_02: hero_bet=100."""
        assert number_results["cp_02"]["hero_bet"] == screenshots["cp_02"]["hero_bet"]

    def test_cp08_hero_bet(
        self,
        number_results: dict[str, dict[str, Any]],
        screenshots: dict[str, Any],
    ) -> None:
        """cp_08: hero_bet=3752."""
        assert number_results["cp_08"]["hero_bet"] == screenshots["cp_08"]["hero_bet"]


class TestPlayerStacksRecognition:
    """Player stack recognition tests."""

    def test_cp01_player_stacks(
        self,
        number_results: dict[str, dict[str, Any]],
        screenshots: dict[str, Any],
    ) -> None:
        """cp_01: seat2=50292, seat4=19972, others=None."""
        expected = {
            seat: values["stack"]
            for seat, values in screenshots["cp_01"]["players"].items()
        }
        assert number_results["cp_01"]["player_stacks"] == expected

    def test_cp03_player_stacks(
        self,
        number_results: dict[str, dict[str, Any]],
        screenshots: dict[str, Any],
    ) -> None:
        """cp_03: seat2=14439, seat3=19890, others=None."""
        expected = {
            seat: values["stack"]
            for seat, values in screenshots["cp_03"]["players"].items()
        }
        assert number_results["cp_03"]["player_stacks"] == expected

    def test_cp08_full_table_stacks(
        self,
        number_results: dict[str, dict[str, Any]],
        screenshots: dict[str, Any],
    ) -> None:
        """cp_08: all five opponent seat stacks are recognized."""
        expected = {
            seat: values["stack"]
            for seat, values in screenshots["cp_08"]["players"].items()
        }
        assert number_results["cp_08"]["player_stacks"] == expected


class TestPlayerBetsRecognition:
    """Player bet recognition tests."""

    def test_cp01_player_bets(
        self,
        number_results: dict[str, dict[str, Any]],
        screenshots: dict[str, Any],
    ) -> None:
        """cp_01: seat2=100, seat4=50, others=None."""
        expected = {
            seat: values["bet"]
            for seat, values in screenshots["cp_01"]["players"].items()
        }
        assert number_results["cp_01"]["player_bets"] == expected

    def test_cp03_no_bets(
        self,
        number_results: dict[str, dict[str, Any]],
        screenshots: dict[str, Any],
    ) -> None:
        """cp_03: all bet fields are None after flop pot collection."""
        expected = {
            seat: values["bet"]
            for seat, values in screenshots["cp_03"]["players"].items()
        }
        assert number_results["cp_03"]["player_bets"] == expected

    def test_cp08_player_bets(
        self,
        number_results: dict[str, dict[str, Any]],
        screenshots: dict[str, Any],
    ) -> None:
        """cp_08: player bets match ground truth."""
        expected = {
            seat: values["bet"]
            for seat, values in screenshots["cp_08"]["players"].items()
        }
        assert number_results["cp_08"]["player_bets"] == expected


class TestEmptyRegions:
    """Empty region detection tests."""

    def test_empty_stack_returns_none(
        self,
        number_results: dict[str, dict[str, Any]],
    ) -> None:
        """Empty stack regions return None."""
        assert number_results["cp_01"]["player_stacks"]["3"] is None
        assert number_results["cp_01"]["player_stacks"]["5"] is None
        assert number_results["cp_01"]["player_stacks"]["6"] is None

    def test_empty_bet_returns_none(
        self,
        number_results: dict[str, dict[str, Any]],
    ) -> None:
        """Empty bet regions return None."""
        assert number_results["cp_03"]["player_bets"] == {
            "2": None,
            "3": None,
            "4": None,
            "5": None,
            "6": None,
        }


class TestRecognizeAll:
    """Batch recognition tests."""

    def test_recognize_all_returns_complete_dict(
        self,
        number_results: dict[str, dict[str, Any]],
    ) -> None:
        """recognize_all returns all expected top-level keys."""
        result = number_results["cp_01"]

        assert set(result) == {
            "pot",
            "hero_stack",
            "hero_bet",
            "player_stacks",
            "player_bets",
        }
        assert set(result["player_stacks"]) == {"2", "3", "4", "5", "6"}
        assert set(result["player_bets"]) == {"2", "3", "4", "5", "6"}
