"""Unit tests for DiffDetector."""

from typing import Any

import cv2
import numpy as np
import pytest

from recognition.base_recognizer import BaseRecognizer
from recognition.diff_detector import DiffDetector


class CropHelper(BaseRecognizer):
    """Minimal BaseRecognizer subclass for crop_region tests."""

    def recognize(self, img: np.ndarray) -> dict[str, Any]:
        """Return an empty recognition result."""
        return {}


@pytest.fixture
def default_config() -> dict[str, dict[str, int]]:
    """Return default diff threshold config."""
    return {
        "recognition": {
            "diff_threshold_card": 500,
            "diff_threshold_number": 300,
            "diff_threshold_button": 200,
        }
    }


@pytest.fixture
def detector(default_config: dict[str, dict[str, int]]) -> DiffDetector:
    """Return a default DiffDetector instance."""
    return DiffDetector(default_config)


@pytest.fixture
def small_image() -> np.ndarray:
    """Return a small BGR image with all pixel values set to 100."""
    return np.full((10, 10, 3), 100, dtype=np.uint8)


@pytest.fixture
def small_image_slightly_different() -> np.ndarray:
    """Return an image with one pixel incremented by one in all channels."""
    img = np.full((10, 10, 3), 100, dtype=np.uint8)
    img[0, 0] = [101, 101, 101]
    return img


@pytest.fixture
def small_image_very_different() -> np.ndarray:
    """Return a small BGR image with all pixel values set to 200."""
    return np.full((10, 10, 3), 200, dtype=np.uint8)


class TestComputeDiff:
    """Tests for compute_diff."""

    def test_identical_images(
        self,
        detector: DiffDetector,
        small_image: np.ndarray,
    ) -> None:
        """Identical images have zero difference."""
        assert detector.compute_diff(small_image, small_image) == 0

    def test_slight_difference(
        self,
        detector: DiffDetector,
        small_image: np.ndarray,
        small_image_slightly_different: np.ndarray,
    ) -> None:
        """A one-pixel one-value change has difference 3."""
        diff = detector.compute_diff(small_image_slightly_different, small_image)
        assert diff == 3

    def test_large_difference(
        self,
        detector: DiffDetector,
        small_image: np.ndarray,
        small_image_very_different: np.ndarray,
    ) -> None:
        """A full-image 100-value change has the expected difference."""
        diff = detector.compute_diff(small_image_very_different, small_image)
        assert diff == 30000

    def test_shape_mismatch(self, detector: DiffDetector) -> None:
        """Shape mismatch returns the sentinel difference value."""
        img_a = np.zeros((10, 10, 3), dtype=np.uint8)
        img_b = np.zeros((20, 20, 3), dtype=np.uint8)
        assert detector.compute_diff(img_a, img_b) == 999_999_999

    def test_symmetry(
        self,
        detector: DiffDetector,
        small_image: np.ndarray,
        small_image_very_different: np.ndarray,
    ) -> None:
        """Difference is symmetric."""
        diff_ab = detector.compute_diff(small_image, small_image_very_different)
        diff_ba = detector.compute_diff(small_image_very_different, small_image)
        assert diff_ab == diff_ba


class TestGetRegionType:
    """Tests for get_region_type."""

    @pytest.mark.parametrize(
        "key,expected",
        [
            ("hero_card_1", "card"),
            ("hero_card_2", "card"),
            ("board_card_1", "card"),
            ("board_card_5", "card"),
            ("pot_display", "number"),
            ("hero_stack", "number"),
            ("hero_bet", "number"),
            ("player_stack_2", "number"),
            ("player_stack_6", "number"),
            ("player_bet_3", "number"),
            ("btn_fold", "button"),
            ("btn_call", "button"),
            ("btn_raise", "button"),
            ("dealer_btn_1", "button"),
            ("dealer_btn_6", "button"),
        ],
    )
    def test_known_region_keys(
        self,
        detector: DiffDetector,
        key: str,
        expected: str,
    ) -> None:
        """Known region keys map to expected region types."""
        assert detector.get_region_type(key) == expected

    def test_unknown_key_defaults_to_number(self, detector: DiffDetector) -> None:
        """Unknown keys default to number type."""
        assert detector.get_region_type("unknown_region") == "number"


class TestHasChanged:
    """Tests for has_changed."""

    def test_first_frame_always_changed(
        self,
        detector: DiffDetector,
        small_image: np.ndarray,
    ) -> None:
        """First frame is always treated as changed."""
        assert detector.has_changed("hero_card_1", small_image) is True

    def test_same_image_no_change(
        self,
        detector: DiffDetector,
        small_image: np.ndarray,
    ) -> None:
        """Same image on the second call is unchanged."""
        detector.has_changed("hero_card_1", small_image)
        assert detector.has_changed("hero_card_1", small_image) is False

    def test_different_image_changed(
        self,
        detector: DiffDetector,
        small_image: np.ndarray,
        small_image_very_different: np.ndarray,
    ) -> None:
        """Large image difference is treated as changed."""
        detector.has_changed("hero_card_1", small_image)
        assert detector.has_changed("hero_card_1", small_image_very_different) is True

    def test_slight_change_below_threshold(
        self,
        detector: DiffDetector,
        small_image: np.ndarray,
        small_image_slightly_different: np.ndarray,
    ) -> None:
        """Small change below the card threshold is unchanged."""
        detector.has_changed("hero_card_1", small_image)
        result = detector.has_changed(
            "hero_card_1",
            small_image_slightly_different,
        )
        assert result is False

    def test_independent_regions(
        self,
        detector: DiffDetector,
        small_image: np.ndarray,
    ) -> None:
        """Different region keys are cached independently."""
        detector.has_changed("hero_card_1", small_image)
        detector.has_changed("pot_display", small_image)
        assert detector.has_changed("hero_card_1", small_image) is False
        assert detector.has_changed("pot_display", small_image) is False

    def test_different_thresholds_per_type(self) -> None:
        """Different thresholds are applied per region type."""
        config = {
            "recognition": {
                "diff_threshold_card": 500,
                "diff_threshold_number": 300,
                "diff_threshold_button": 200,
            }
        }
        det = DiffDetector(config)
        base = np.full((5, 5, 3), 100, dtype=np.uint8)
        changed = np.full((5, 5, 3), 103, dtype=np.uint8)

        det.has_changed("btn_fold", base)
        assert det.has_changed("btn_fold", changed) is True

        det.has_changed("pot_display", base)
        assert det.has_changed("pot_display", changed) is False

    def test_updates_cache_after_check(
        self,
        detector: DiffDetector,
        small_image: np.ndarray,
        small_image_very_different: np.ndarray,
    ) -> None:
        """Cache updates after each check."""
        detector.has_changed("hero_card_1", small_image)
        detector.has_changed("hero_card_1", small_image_very_different)
        result = detector.has_changed("hero_card_1", small_image_very_different)
        assert result is False

    def test_cache_is_copy_not_reference(self, detector: DiffDetector) -> None:
        """Cached crops are defensive copies."""
        img = np.full((5, 5, 3), 100, dtype=np.uint8)
        detector.has_changed("hero_card_1", img)
        img[:] = 200
        img2 = np.full((5, 5, 3), 100, dtype=np.uint8)
        assert detector.has_changed("hero_card_1", img2) is False


class TestReset:
    """Tests for reset and reset_region."""

    def test_reset_clears_all(
        self,
        detector: DiffDetector,
        small_image: np.ndarray,
    ) -> None:
        """reset() clears all cached regions."""
        detector.has_changed("hero_card_1", small_image)
        detector.has_changed("pot_display", small_image)
        assert len(detector.get_cached_region_keys()) == 2

        detector.reset()
        assert len(detector.get_cached_region_keys()) == 0
        assert detector.has_changed("hero_card_1", small_image) is True

    def test_reset_region_clears_single(
        self,
        detector: DiffDetector,
        small_image: np.ndarray,
    ) -> None:
        """reset_region() clears only the requested region."""
        detector.has_changed("hero_card_1", small_image)
        detector.has_changed("pot_display", small_image)

        detector.reset_region("hero_card_1")
        assert "hero_card_1" not in detector.get_cached_region_keys()
        assert "pot_display" in detector.get_cached_region_keys()

    def test_reset_region_nonexistent_key(self, detector: DiffDetector) -> None:
        """reset_region() with an unknown key does not raise."""
        detector.reset_region("nonexistent_key")


class TestWithRealScreenshots:
    """Tests using real CoinPoker screenshots."""

    def test_same_screenshot_no_change(
        self,
        detector: DiffDetector,
        screenshots_dir: Any,
        profile: dict[str, Any],
    ) -> None:
        """Same screenshot crop sequence is unchanged on the second pass."""
        image_path = screenshots_dir / "cp_01_preflop_my_turnb.png"
        img = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if img is None:
            pytest.skip("cp_01 screenshot not available")

        test_keys = [
            "hero_card_1",
            "hero_card_2",
            "board_card_1",
            "board_card_2",
            "board_card_3",
            "pot_display",
            "hero_stack",
            "hero_bet",
            "btn_fold",
        ]
        helper = CropHelper(profile, {})

        for key in test_keys:
            crop = helper.crop_region(img, key)
            if crop is not None:
                detector.has_changed(key, crop)

        for key in test_keys:
            crop = helper.crop_region(img, key)
            if crop is not None:
                assert detector.has_changed(key, crop) is False, (
                    f"Region {key} should not have changed on same screenshot"
                )

    def test_different_screenshots_detect_change(
        self,
        detector: DiffDetector,
        screenshots_dir: Any,
        profile: dict[str, Any],
    ) -> None:
        """Different screenshots detect a changed hero card region."""
        img1 = cv2.imread(
            str(screenshots_dir / "cp_01_preflop_my_turnb.png"),
            cv2.IMREAD_COLOR,
        )
        img2 = cv2.imread(
            str(screenshots_dir / "cp_04_flop_not_my_turn.png"),
            cv2.IMREAD_COLOR,
        )
        if img1 is None or img2 is None:
            pytest.skip("cp_01 or cp_04 screenshot not available")

        helper = CropHelper(profile, {})
        crop1 = helper.crop_region(img1, "hero_card_1")
        crop2 = helper.crop_region(img2, "hero_card_1")
        if crop1 is None or crop2 is None:
            pytest.skip("hero_card_1 not in profile")

        detector.has_changed("hero_card_1", crop1)
        assert detector.has_changed("hero_card_1", crop2) is True


class TestConfigDefaults:
    """Tests for config defaults and overrides."""

    def test_empty_config(self) -> None:
        """Empty config initializes default thresholds."""
        det = DiffDetector({})
        assert det.thresholds["card"] == 500
        assert det.thresholds["number"] == 300
        assert det.thresholds["button"] == 200

    def test_partial_config(self) -> None:
        """Missing threshold values fall back to defaults."""
        config = {"recognition": {"diff_threshold_card": 1000}}
        det = DiffDetector(config)
        assert det.thresholds["card"] == 1000
        assert det.thresholds["number"] == 300
        assert det.thresholds["button"] == 200

    def test_custom_thresholds(self) -> None:
        """Custom thresholds are applied."""
        config = {
            "recognition": {
                "diff_threshold_card": 100,
                "diff_threshold_number": 50,
                "diff_threshold_button": 25,
            }
        }
        det = DiffDetector(config)
        assert det.thresholds["card"] == 100
        assert det.thresholds["number"] == 50
        assert det.thresholds["button"] == 25
