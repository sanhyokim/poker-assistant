"""Player-name OCR recognition."""

import logging
from typing import Any

import cv2
import numpy as np

from recognition import get_reader
from recognition.base_recognizer import BaseRecognizer

logger = logging.getLogger(__name__)


class NameRecognizer(BaseRecognizer):
    """Recognize opponent player names for seats 2 through 6.

    Args:
        profile: Coordinate profile dictionary.
        config: Full application config dictionary.
    """

    def __init__(self, profile: dict[str, Any], config: dict[str, Any]) -> None:
        super().__init__(profile, config)
        ocr_config = config.get("ocr", {})
        self._reader = get_reader(list(ocr_config.get("languages", ["en"])))
        self._confidence_threshold = float(
            ocr_config.get("confidence_threshold", 0.4)
        )
        self._empty_region_std = float(
            config.get("action_estimation", {}).get("empty_region_std", 8)
        )

    def recognize(self, img: np.ndarray) -> dict[str, str | None]:
        """Recognize all configured player names.

        Args:
            img: BGR source image.

        Returns:
            Seat string to player name mapping.
        """
        return self.recognize_player_names(img)

    def recognize_player_names(self, img: np.ndarray) -> dict[str, str | None]:
        """Recognize player names for seats 2 through 6.

        Args:
            img: BGR 1920x1080 frame image.

        Returns:
            Mapping like {"2": "player", "3": None, ...}.
        """
        return {
            str(seat): self._recognize_single_name(img, f"player_name_{seat}")
            for seat in range(2, 7)
        }

    def _recognize_single_name(
        self,
        img: np.ndarray,
        region_key: str,
    ) -> str | None:
        crop = self.crop_region(img, region_key)
        if crop is None:
            return None

        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        if float(np.std(gray)) < self._empty_region_std:
            return None

        try:
            results = self._reader.readtext(
                crop,
                text_threshold=0.3,
                low_text=0.2,
            )
        except Exception as exc:
            logger.warning("Name OCR failed for %s: %s", region_key, exc)
            return None

        if not results:
            return None

        try:
            best_result = max(results, key=lambda result: float(result[2]))
            text = str(best_result[1]).strip()
            confidence = float(best_result[2])
        except (IndexError, TypeError, ValueError):
            logger.debug("Name OCR returned malformed result for %s", region_key)
            return None

        if confidence < self._confidence_threshold:
            logger.debug(
                "Name OCR low confidence for %s: '%s' (%.2f)",
                region_key,
                text,
                confidence,
            )
            return None

        if not text:
            return None

        text = self._clean_player_name(text)
        if not text:
            return None

        logger.debug(
            "Name recognized for %s: '%s' (%.2f)",
            region_key,
            text,
            confidence,
        )
        return text

    def _clean_player_name(self, raw_name: str) -> str:
        """Clean noisy OCR prefixes from a player name.

        Args:
            raw_name: Raw OCR output.

        Returns:
            Cleaned player name, or the original value if cleaning would empty it.
        """
        cleaned = raw_name.lstrip("~-_.!@#$%^&*()+=[]{}|\\/<>,;:'\"` ")
        if not cleaned:
            return raw_name
        return cleaned
