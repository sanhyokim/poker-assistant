"""Tests for opponent seat card detection."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from recognition.seat_card_detector import SeatCardDetector


@pytest.fixture
def default_profile() -> dict[str, dict[str, int]]:
    """Return a minimal profile with two seat card regions."""
    return {
        "seat_2_cards": {"x": 10, "y": 10, "w": 50, "h": 20},
        "seat_3_cards": {"x": 10, "y": 40, "w": 50, "h": 20},
    }


@pytest.fixture
def default_config() -> dict[str, dict[str, Any]]:
    """Return recognition thresholds for deterministic tests."""
    return {
        "recognition": {
            "fold_confirm_frames": 3,
            "card_edge_threshold": 30,
            "card_edge_density_min": 0.08,
            "card_gray_mean_min": 80.0,
            "card_gray_std_min": 20.0,
        },
    }


@pytest.fixture
def detector(
    default_profile: dict[str, dict[str, int]],
    default_config: dict[str, dict[str, Any]],
) -> SeatCardDetector:
    """Return a detector using the default test profile."""
    return SeatCardDetector(default_profile, default_config)


def test_detect_all_missing_profile_keys(
    default_config: dict[str, dict[str, Any]],
) -> None:
    """Seats without profile keys default to True."""
    detector = SeatCardDetector({}, default_config)
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)

    results = detector.detect_all(frame)

    assert all(results[seat] is True for seat in [2, 3, 4, 5, 6])


def test_detect_all_black_frame_no_cards(detector: SeatCardDetector) -> None:
    """Pure black configured regions have no edges and report no cards."""
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)

    results = detector.detect_all(frame)

    assert results[2] is False
    assert results[3] is False
    assert results[4] is True
    assert results[5] is True
    assert results[6] is True


def test_detect_card_with_edges(detector: SeatCardDetector) -> None:
    """A region with strong rectangular edges detects as card present."""
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    frame[10:30, 10:60] = 130
    frame[12:28, 15:55] = 255
    frame[14:26, 17:53] = 80

    results = detector.detect_all(frame)

    assert results[2] is True


def _crop_with_gray_mean(gray_mean: int) -> np.ndarray:
    """Create a BGR crop with a fixed grayscale mean."""
    return np.full((10, 10, 3), gray_mean, dtype=np.uint8)


def _crop_with_gray_values(low: int, high: int) -> np.ndarray:
    """Create a BGR crop with alternating grayscale values."""
    gray = np.full((10, 10), low, dtype=np.uint8)
    gray[:, ::2] = high
    return np.repeat(gray[:, :, None], 3, axis=2)


def _edge_mask_with_density(density: float) -> np.ndarray:
    """Create a 10x10 edge mask with an approximate nonzero density."""
    edges = np.zeros((10, 10), dtype=np.uint8)
    nonzero_count = int(round(density * edges.size))
    edges.flat[:nonzero_count] = 255
    return edges


def test_high_edge_density_low_gray_mean_reports_no_card(
    detector: SeatCardDetector,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """High edge density is not enough when gray mean is below threshold."""
    monkeypatch.setattr(
        "recognition.seat_card_detector.cv2.Canny",
        lambda *_args: _edge_mask_with_density(0.19),
    )

    assert detector._has_card(_crop_with_gray_values(10, 70), 2) is False


def test_high_edge_density_low_gray_std_reports_no_card(
    detector: SeatCardDetector,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """High edge density is not enough when gray std is below threshold."""
    monkeypatch.setattr(
        "recognition.seat_card_detector.cv2.Canny",
        lambda *_args: _edge_mask_with_density(0.27),
    )

    assert detector._has_card(_crop_with_gray_mean(130), 2) is False


def test_bright_textured_card_back_reports_card(
    detector: SeatCardDetector,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crop meeting density, mean, and std thresholds reports card present."""
    monkeypatch.setattr(
        "recognition.seat_card_detector.cv2.Canny",
        lambda *_args: _edge_mask_with_density(0.27),
    )

    assert detector._has_card(_crop_with_gray_values(80, 180), 2) is True


def test_bright_empty_seat(
    detector: SeatCardDetector,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bright crop with too few edges reports no card."""
    monkeypatch.setattr(
        "recognition.seat_card_detector.cv2.Canny",
        lambda *_args: _edge_mask_with_density(0.06),
    )

    assert detector._has_card(_crop_with_gray_mean(170), 2) is False


def test_borderline_gray_mean(
    detector: SeatCardDetector,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gray mean exactly at threshold can report card when other conditions pass."""
    monkeypatch.setattr(
        "recognition.seat_card_detector.cv2.Canny",
        lambda *_args: _edge_mask_with_density(0.10),
    )

    assert detector._has_card(_crop_with_gray_values(60, 100), 2) is True


def test_dark_empty_background_no_card(
    detector: SeatCardDetector,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dark empty backgrounds with too few edges report no card."""
    monkeypatch.setattr(
        "recognition.seat_card_detector.cv2.Canny",
        lambda *_args: _edge_mask_with_density(0.01),
    )

    assert detector._has_card(_crop_with_gray_mean(10), 2) is False


def test_reset_clears_state(detector: SeatCardDetector) -> None:
    """Reset clears all internal tracking state."""
    detector._no_card_streak[2] = 5
    detector._last_detection[2] = False

    detector.reset()

    assert len(detector._no_card_streak) == 0
    assert len(detector._last_detection) == 0


def test_crop_region_out_of_bounds(detector: SeatCardDetector) -> None:
    """Out-of-bounds regions return None."""
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    region = {"x": 90, "y": 90, "w": 50, "h": 50}

    crop = detector._crop_region(frame, region)

    assert crop is None


def test_crop_region_valid(detector: SeatCardDetector) -> None:
    """Valid regions return crops with the expected shape."""
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    region = {"x": 10, "y": 10, "w": 50, "h": 20}

    crop = detector._crop_region(frame, region)

    assert crop is not None
    assert crop.shape == (20, 50, 3)
