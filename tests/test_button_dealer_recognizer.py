"""Tests for button and dealer recognizers."""

from collections.abc import Callable
from typing import Any

import cv2
import numpy as np
import pytest

from recognition.button_recognizer import ButtonRecognizer
from recognition.dealer_recognizer import DealerRecognizer

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
def button_recognizer(
    profile: dict[str, Any],
    config: dict[str, Any],
) -> ButtonRecognizer:
    """Return a shared ButtonRecognizer instance."""
    return ButtonRecognizer(profile, config)


@pytest.fixture(scope="module")
def dealer_recognizer(
    profile: dict[str, Any],
    config: dict[str, Any],
) -> DealerRecognizer:
    """Return a shared DealerRecognizer instance."""
    return DealerRecognizer(profile, config)


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


class TestButtonRecognizer:
    """Button recognizer tests."""

    @pytest.mark.parametrize("key", PRIMARY_KEYS)
    def test_detect_my_turn_matches_ground_truth(
        self,
        key: str,
        button_recognizer: ButtonRecognizer,
        screenshots: dict[str, Any],
        load_image: ImageLoader,
    ) -> None:
        """detect_my_turn() matches ground truth on primary screenshots."""
        img = load_image(screenshots[key]["filename"])

        assert button_recognizer.detect_my_turn(img) == screenshots[key]["is_my_turn"]

    @pytest.mark.parametrize("key", ["cp_01", "cp_04", "cp_05", "cp_11"])
    def test_classify_buttons_matches_ground_truth(
        self,
        key: str,
        button_recognizer: ButtonRecognizer,
        screenshots: dict[str, Any],
        load_image: ImageLoader,
    ) -> None:
        """classify_buttons() matches ground truth on hero-turn screenshots."""
        img = load_image(screenshots[key]["filename"])

        assert button_recognizer.classify_buttons(img) == screenshots[key]["buttons"]

    def test_classify_buttons_returns_none_when_not_my_turn(
        self,
        button_recognizer: ButtonRecognizer,
        screenshots: dict[str, Any],
        load_image: ImageLoader,
    ) -> None:
        """classify_buttons() returns None on a non-turn screenshot."""
        img = load_image(screenshots["cp_03"]["filename"])

        assert button_recognizer.classify_buttons(img) is None


class TestDealerRecognizer:
    """Dealer recognizer tests."""

    @pytest.mark.parametrize("key", ["cp_01", "cp_02", "cp_03", "cp_04", "cp_05", "cp_06"])
    def test_detect_dealer_seat_matches_ground_truth(
        self,
        key: str,
        dealer_recognizer: DealerRecognizer,
        screenshots: dict[str, Any],
        load_image: ImageLoader,
    ) -> None:
        """detect_dealer_seat() matches ground truth for measured seats."""
        img = load_image(screenshots[key]["filename"])

        assert dealer_recognizer.detect_dealer_seat(img) == screenshots[key]["dealer_seat"]

    def test_dealer_seat_5_and_6_regions_are_measured(
        self,
        dealer_recognizer: DealerRecognizer,
        screenshots: dict[str, Any],
        load_image: ImageLoader,
    ) -> None:
        """dealer_btn_5/6 are present in the profile and crop safely."""
        img = load_image(screenshots["cp_01"]["filename"])

        assert dealer_recognizer.crop_region(img, "dealer_btn_5") is not None
        assert dealer_recognizer.crop_region(img, "dealer_btn_6") is not None
        assert dealer_recognizer.detect_dealer_seat(img) == 1
