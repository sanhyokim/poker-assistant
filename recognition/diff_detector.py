"""Frame-difference detector for OCR optimization.

Compares current and previous cropped regions and reports whether the
pixel-difference sum exceeds the configured threshold.
"""

import logging

import numpy as np

logger = logging.getLogger(__name__)


class DiffDetector:
    """Frame-difference detector.

    Region type specific thresholds are used to decide whether OCR should run.

    Attributes:
        thresholds: Difference thresholds by region type.
    """

    _REGION_TYPE_MAP: dict[str, str] = {
        "hero_card": "card",
        "board_card": "card",
        "pot": "number",
        "hero_stack": "number",
        "hero_bet": "number",
        "player_stack": "number",
        "player_bet": "number",
        "btn_fold": "button",
        "btn_call": "button",
        "btn_raise": "button",
        "dealer_btn": "button",
    }

    def __init__(self, config: dict) -> None:
        """Initialize a DiffDetector.

        Args:
            config: Full config dictionary.
        """
        recognition_config = config.get("recognition", {})
        self.thresholds: dict[str, int] = {
            "card": recognition_config.get("diff_threshold_card", 500),
            "number": recognition_config.get("diff_threshold_number", 300),
            "button": recognition_config.get("diff_threshold_button", 200),
        }
        self._prev_crops: dict[str, np.ndarray] = {}
        logger.info(
            "DiffDetector initialized with thresholds: %s",
            self.thresholds,
        )

    def get_region_type(self, region_key: str) -> str:
        """Return the region type for a coordinate profile key.

        Args:
            region_key: Coordinate profile key.

        Returns:
            One of card, number, or button. Unknown keys default to number.
        """
        for prefix, region_type in self._REGION_TYPE_MAP.items():
            if region_key.startswith(prefix):
                return region_type
        return "number"

    def compute_diff(self, curr_crop: np.ndarray, prev_crop: np.ndarray) -> int:
        """Compute the pixel-difference sum between two crops.

        Args:
            curr_crop: Current BGR crop.
            prev_crop: Previous BGR crop.

        Returns:
            Pixel-difference sum, or a large sentinel on shape mismatch.
        """
        if curr_crop.shape != prev_crop.shape:
            logger.debug(
                "Crop shape mismatch: curr=%s, prev=%s",
                curr_crop.shape,
                prev_crop.shape,
            )
            return 999_999_999

        diff = int(
            np.sum(
                np.abs(
                    curr_crop.astype(np.int16)
                    - prev_crop.astype(np.int16)
                )
            )
        )
        return diff

    def has_changed(self, region_key: str, curr_crop: np.ndarray) -> bool:
        """Return whether a region changed since the previous frame.

        The first frame for a region is always treated as changed. The current
        crop is saved as a defensive copy after each call.

        Args:
            region_key: Coordinate profile key.
            curr_crop: Current BGR crop.

        Returns:
            True when OCR should run; False when a previous value can be reused.
        """
        region_type = self.get_region_type(region_key)
        threshold = self.thresholds.get(region_type, 300)
        prev_crop = self._prev_crops.get(region_key)

        if prev_crop is None:
            self._prev_crops[region_key] = curr_crop.copy()
            logger.debug(
                "DiffDetector: %s - first frame, marked as changed",
                region_key,
            )
            return True

        diff = self.compute_diff(curr_crop, prev_crop)
        changed = diff >= threshold
        self._prev_crops[region_key] = curr_crop.copy()

        if changed:
            logger.debug(
                "DiffDetector: %s - CHANGED (diff=%d, threshold=%d, type=%s)",
                region_key,
                diff,
                threshold,
                region_type,
            )
        else:
            logger.debug(
                "DiffDetector: %s - no change (diff=%d, threshold=%d, type=%s)",
                region_key,
                diff,
                threshold,
                region_type,
            )

        return changed

    def reset(self) -> None:
        """Clear all cached previous crops."""
        self._prev_crops.clear()
        logger.info("DiffDetector: all cached crops cleared")

    def reset_region(self, region_key: str) -> None:
        """Clear one cached region.

        Args:
            region_key: Coordinate profile key to clear.
        """
        if region_key in self._prev_crops:
            del self._prev_crops[region_key]
            logger.debug(
                "DiffDetector: cached crop cleared for %s",
                region_key,
            )

    def get_cached_region_keys(self) -> list[str]:
        """Return cached region keys.

        Returns:
            List of region keys currently cached.
        """
        return list(self._prev_crops.keys())
