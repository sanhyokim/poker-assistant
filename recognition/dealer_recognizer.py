"""Dealer button detection using red and white pixel scoring."""

import logging
from typing import Any

import cv2
import numpy as np

from recognition.base_recognizer import BaseRecognizer

DEALER_SCORE_THRESHOLD = 0.05
logger = logging.getLogger(__name__)


class DealerRecognizer(BaseRecognizer):
    """Detect the dealer button seat.

    Args:
        profile: Coordinate profile dictionary.
        config: Full application config dictionary.
    """

    def __init__(self, profile: dict[str, Any], config: dict[str, Any]) -> None:
        super().__init__(profile, config)

    def recognize(self, img: np.ndarray) -> int | None:
        """Detect dealer seat from an image.

        Args:
            img: BGR source image.

        Returns:
            Seat number, or None when no dealer button is detected.
        """
        return self.detect_dealer_seat(img)

    def detect_dealer_seat(self, img: np.ndarray) -> int | None:
        """Detect dealer button seat by red and white pixel scoring.

        Args:
            img: BGR source image.

        Returns:
            Seat number from 1 to 6, or None if no score exceeds threshold.
        """
        best_seat: int | None = None
        best_score = 0.0
        scores: dict[int, float] = {}
        for seat in range(1, 7):
            crop = self.crop_region(img, f"dealer_btn_{seat}")
            if crop is None:
                continue
            score = self._score_dealer_button(crop)
            scores[seat] = score
            if score > best_score:
                best_score = score
                best_seat = seat

        if best_score > DEALER_SCORE_THRESHOLD:
            logger.debug(
                "Dealer detection scores: %s -> selected seat=%s "
                "(threshold=%.2f)",
                {seat: round(score, 4) for seat, score in scores.items()},
                best_seat,
                DEALER_SCORE_THRESHOLD,
            )
            return best_seat
        logger.debug(
            "Dealer detection scores: %s -> selected seat=None "
            "(threshold=%.2f)",
            {seat: round(score, 4) for seat, score in scores.items()},
            DEALER_SCORE_THRESHOLD,
        )
        return None

    def _score_dealer_button(self, crop: np.ndarray) -> float:
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        h, s, v = cv2.split(hsv)
        red_mask = ((h < 15) | (h > 160)) & (s > 80) & (v > 80)
        white_mask = (s < 40) & (v > 200)
        pixel_count = float(h.size)
        red_ratio = float(np.count_nonzero(red_mask)) / pixel_count
        white_ratio = float(np.count_nonzero(white_mask)) / pixel_count
        return red_ratio * 0.7 + white_ratio * 0.3
