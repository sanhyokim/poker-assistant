"""Detect whether opponent seats currently have hole cards.

Each configured opponent card region is analyzed for back-card edges. The
detector is intentionally conservative: missing or invalid profile regions are
treated as card-present so later hand-state logic does not incorrectly mark a
player as folded.
"""

from __future__ import annotations

import logging
from typing import Any

import cv2
import numpy as np


logger = logging.getLogger(__name__)


class SeatCardDetector:
    """Detect card presence for opponent seats.

    Args:
        profile: Coordinate profile containing ``seat_X_cards`` regions.
        config: Parsed application config. Uses the ``recognition`` section.
    """

    SEAT_KEYS = {
        2: "seat_2_cards",
        3: "seat_3_cards",
        4: "seat_4_cards",
        5: "seat_5_cards",
        6: "seat_6_cards",
    }

    def __init__(self, profile: dict[str, Any], config: dict[str, Any]) -> None:
        """Initialize detector thresholds and per-seat state."""
        self._profile = profile
        recognition_config = config.get("recognition", {})
        self._fold_confirm_frames = int(
            recognition_config.get("fold_confirm_frames", 3)
        )
        self._card_edge_threshold = int(
            recognition_config.get("card_edge_threshold", 30)
        )
        self._card_edge_density_min = float(
            recognition_config.get("card_edge_density_min", 0.08)
        )
        self._no_card_streak: dict[int, int] = {}
        self._last_detection: dict[int, bool] = {}

    def detect_all(self, frame: np.ndarray) -> dict[int, bool]:
        """Detect card presence for all opponent seats.

        Args:
            frame: BGR frame image.

        Returns:
            Mapping of seat number to card presence. Seats without configured
            regions return True as a fail-safe default.
        """
        results: dict[int, bool] = {}
        for seat, key in self.SEAT_KEYS.items():
            region = self._profile.get(key)
            if region is None:
                results[seat] = True
                continue
            crop = self._crop_region(frame, region)
            if crop is None or crop.size == 0:
                results[seat] = True
                continue
            results[seat] = self._has_card(crop, seat)
        return results

    def reset(self) -> None:
        """Clear internal per-seat tracking state."""
        self._no_card_streak.clear()
        self._last_detection.clear()
        logger.debug("SeatCardDetector: internal state reset")

    def _has_card(self, crop: np.ndarray, seat: int) -> bool:
        """Return whether a cropped card region contains card-like edges."""
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(
            gray,
            self._card_edge_threshold,
            self._card_edge_threshold * 3,
        )
        total_pixels = edges.shape[0] * edges.shape[1]
        if total_pixels == 0:
            return True

        edge_density = float(np.count_nonzero(edges)) / total_pixels
        has_card = edge_density >= self._card_edge_density_min
        logger.debug(
            "Seat %d card detection: edge_density=%.4f, threshold=%.4f, "
            "has_card=%s",
            seat,
            edge_density,
            self._card_edge_density_min,
            has_card,
        )
        return has_card

    @staticmethod
    def _crop_region(
        frame: np.ndarray,
        region: dict[str, int],
    ) -> np.ndarray | None:
        """Crop a configured region from a frame.

        Args:
            frame: BGR frame image.
            region: Region dictionary with x, y, w, and h keys.

        Returns:
            Cropped image, or None when the region is invalid.
        """
        x = region.get("x", 0)
        y = region.get("y", 0)
        w = region.get("w", 0)
        h = region.get("h", 0)
        if x < 0 or y < 0 or w <= 0 or h <= 0:
            return None
        if y + h > frame.shape[0] or x + w > frame.shape[1]:
            return None
        return frame[y : y + h, x : x + w]
