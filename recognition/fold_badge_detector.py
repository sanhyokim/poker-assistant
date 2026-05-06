"""Fold badge detection using template matching."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from recognition.base_recognizer import BaseRecognizer


logger = logging.getLogger(__name__)


class FoldBadgeDetector(BaseRecognizer):
    """Detect opponent fold badges using template matching.

    Once a seat is detected as folded, it remains folded until ``reset`` is
    called at a hand boundary.

    Args:
        profile: Coordinate profile dictionary.
        config: Full application config dictionary.
        template_path: Path to fold badge template image.
    """

    SEAT_KEYS = {
        2: "action_badge_2",
        3: "action_badge_3",
        4: "action_badge_4",
        5: "action_badge_5",
        6: "action_badge_6",
    }

    def __init__(
        self,
        profile: dict[str, Any],
        config: dict[str, Any],
        template_path: str = "recognition/templates/fold_badge_ja.png",
    ) -> None:
        """Initialize detector threshold, template, and latches."""
        super().__init__(profile, config)
        self._threshold = float(
            config.get("recognition", {}).get("fold_badge_threshold", 0.8),
        )
        self._template = self._load_template(template_path)
        self._folded_seats: set[int] = set()

    def detect_all(self, frame: np.ndarray) -> dict[int, bool]:
        """Detect fold badges for all opponent seats.

        Args:
            frame: BGR frame image.

        Returns:
            Mapping of seat number to fold status.
        """
        results: dict[int, bool] = {}
        for seat, key in self.SEAT_KEYS.items():
            if seat in self._folded_seats:
                results[seat] = True
                continue

            is_folded = self._detect_single(frame, seat, key)
            if is_folded:
                self._folded_seats.add(seat)
                logger.info(
                    "Fold badge detected for seat %d (latched for hand)",
                    seat,
                )
            results[seat] = is_folded
        return results

    def reset(self) -> None:
        """Clear all fold latches."""
        if self._folded_seats:
            logger.debug("Fold badge latches cleared: %s", self._folded_seats)
        self._folded_seats.clear()

    @property
    def folded_seats(self) -> set[int]:
        """Return seats currently latched as folded."""
        return set(self._folded_seats)

    def recognize(self, img: np.ndarray) -> dict[int, bool]:
        """Detect fold badges using the BaseRecognizer interface."""
        return self.detect_all(img)

    def _load_template(self, path: str) -> np.ndarray | None:
        """Load the fold badge template image."""
        template_path = Path(path)
        template = cv2.imread(str(template_path))
        if template is None:
            logger.error("Failed to load fold badge template: %s", path)
            return None
        logger.info(
            "Fold badge template loaded: %s (%dx%d)",
            path,
            template.shape[1],
            template.shape[0],
        )
        return template

    def _detect_single(self, frame: np.ndarray, seat: int, key: str) -> bool:
        """Return whether one seat's badge region matches the fold template."""
        if self._template is None:
            return False

        crop = self.crop_region(frame, key)
        if crop is None:
            return False

        height, width = crop.shape[:2]
        if height <= 0 or width <= 0:
            return False

        template_resized = cv2.resize(self._template, (width, height))
        result = cv2.matchTemplate(
            crop,
            template_resized,
            cv2.TM_CCOEFF_NORMED,
        )
        _, max_value, _, _ = cv2.minMaxLoc(result)
        visual_match = self._looks_like_fold_badge(crop)
        logger.debug(
            "Seat %d fold badge match: %.3f (threshold: %.3f, visual=%s)",
            seat,
            max_value,
            self._threshold,
            visual_match,
        )
        return max_value >= self._threshold or visual_match

    @staticmethod
    def _looks_like_fold_badge(crop: np.ndarray) -> bool:
        """Return whether a crop has the dark teal fold-badge appearance."""
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        teal_mask = (
            (hsv[:, :, 0] >= 70)
            & (hsv[:, :, 0] <= 100)
            & (hsv[:, :, 1] > 30)
            & (hsv[:, :, 2] > 20)
        )
        dark_mask = gray < 45
        return (
            float(np.count_nonzero(teal_mask)) / teal_mask.size >= 0.75
            and float(np.count_nonzero(dark_mask)) / dark_mask.size >= 0.90
        )
