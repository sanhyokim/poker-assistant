"""Tests for fold badge template detection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest

from recognition.fold_badge_detector import FoldBadgeDetector


FIXTURE_DIR = Path("tests/fixtures/screenshots/coinpoker")
PROFILE_PATH = Path("profiles/coinpoker_6max.json")
TEMPLATE_PATH = Path("recognition/templates/fold_badge_ja.png")

FOLD_BADGE_GROUND_TRUTH = {
    "auto_0045.png": {2: False, 3: True, 4: False, 5: True, 6: True},
    "auto_0146.png": {2: True, 3: False, 4: False, 5: False, 6: True},
    "auto_0017.png": {2: False, 3: True, 4: False, 5: False, 6: False},
    "cp_12_folded_spectating.png": {
        2: False,
        3: False,
        4: True,
        5: False,
        6: False,
    },
}


@pytest.fixture
def profile() -> dict[str, Any]:
    """Return the CoinPoker coordinate profile."""
    return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def config() -> dict[str, Any]:
    """Return fold badge detector config."""
    return {"recognition": {"fold_badge_threshold": 0.8}}


@pytest.fixture
def detector(profile: dict[str, Any], config: dict[str, Any]) -> FoldBadgeDetector:
    """Return a detector using the real template."""
    return FoldBadgeDetector(profile, config, template_path=str(TEMPLATE_PATH))


def _load_frame(image_name: str) -> np.ndarray:
    """Load a fixture frame."""
    frame = cv2.imread(str(FIXTURE_DIR / image_name))
    assert frame is not None, f"fixture not found: {image_name}"
    return frame


@pytest.mark.parametrize(
    ("image_name", "expected"),
    FOLD_BADGE_GROUND_TRUTH.items(),
)
def test_fold_badge_fixture_detection(
    detector: FoldBadgeDetector,
    image_name: str,
    expected: dict[int, bool],
) -> None:
    """Fixture frames match expected fold badge states."""
    detector.reset()
    result = detector.detect_all(_load_frame(image_name))

    assert result == expected


def test_fold_badge_latches_after_detection(
    detector: FoldBadgeDetector,
) -> None:
    """A detected fold remains latched until reset."""
    folded_frame = _load_frame("auto_0017.png")
    blank_frame = np.zeros_like(folded_frame)

    first = detector.detect_all(folded_frame)
    second = detector.detect_all(blank_frame)

    assert first[3] is True
    assert second[3] is True
    assert 3 in detector.folded_seats


def test_fold_badge_reset_clears_latches(
    detector: FoldBadgeDetector,
) -> None:
    """Reset clears latched fold seats."""
    detector.detect_all(_load_frame("auto_0017.png"))

    detector.reset()

    assert detector.folded_seats == set()


def test_missing_template_returns_false(
    profile: dict[str, Any],
    config: dict[str, Any],
) -> None:
    """Missing template fails closed without raising."""
    detector = FoldBadgeDetector(
        profile,
        config,
        template_path="recognition/templates/missing_fold_badge.png",
    )

    result = detector.detect_all(_load_frame("auto_0045.png"))

    assert result == {2: False, 3: False, 4: False, 5: False, 6: False}
