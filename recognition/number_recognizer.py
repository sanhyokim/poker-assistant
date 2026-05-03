"""Number recognition for pot, stack, and bet amounts."""

import logging
import re
from typing import Any, TypedDict

import cv2
import numpy as np

from recognition import get_reader
from recognition.base_recognizer import BaseRecognizer

logger = logging.getLogger(__name__)

PLAYER_SEATS = ("2", "3", "4", "5", "6")
NUMBER_ALLOWLIST = "0123456789.,$ USDTCHP"


class NumberRecognitionResult(TypedDict):
    """Aggregated number recognition result."""

    pot: int | None
    hero_stack: int | None
    hero_bet: int | None
    player_stacks: dict[str, int | None]
    player_bets: dict[str, int | None]


class NumberRecognizer(BaseRecognizer):
    """Recognize pot, stack, and bet numbers from table screenshots.

    Args:
        profile: Coordinate profile dictionary.
        config: Full application config dictionary.
    """

    def __init__(self, profile: dict[str, Any], config: dict[str, Any]) -> None:
        super().__init__(profile, config)
        self._languages: list[str] = list(config.get("ocr", {}).get("languages", ["en"]))
        self._confidence_threshold = float(
            config.get("ocr", {}).get("confidence_threshold", 0.4)
        )
        self._empty_region_std = float(
            config.get("action_estimation", {}).get("empty_region_std", 8)
        )

    def recognize(self, img: np.ndarray) -> NumberRecognitionResult:
        """Recognize all number fields from an image.

        Args:
            img: BGR source image.

        Returns:
            Aggregated number recognition result.
        """
        return self.recognize_all(img)

    def recognize_pot(self, img: np.ndarray) -> int | None:
        """Recognize the pot display value.

        Args:
            img: BGR source image.

        Returns:
            Pot amount, or None if unavailable.
        """
        crop = self.crop_region(img, "pot_display")
        if crop is None:
            return None
        return self._ocr_number(crop, is_pot=True)

    def recognize_hero_stack(self, img: np.ndarray) -> int | None:
        """Recognize the hero stack value.

        Args:
            img: BGR source image.

        Returns:
            Hero stack amount, or None if unavailable.
        """
        crop = self.crop_region(img, "hero_stack")
        if crop is None:
            return None
        return self._ocr_stack_number(crop)

    def recognize_hero_bet(self, img: np.ndarray) -> int | None:
        """Recognize the hero bet value.

        Args:
            img: BGR source image.

        Returns:
            Hero bet amount, or None if unavailable.
        """
        crop = self.crop_region(img, "hero_bet")
        if crop is None:
            return None
        return self._ocr_number(crop)

    def recognize_player_stacks(self, img: np.ndarray) -> dict[str, int | None]:
        """Recognize player stack values for seats 2 through 6.

        Args:
            img: BGR source image.

        Returns:
            Mapping of seat number strings to stack values or None.
        """
        return {
            seat: self._recognize_stack_region(img, f"player_stack_{seat}")
            for seat in PLAYER_SEATS
        }

    def recognize_player_bets(self, img: np.ndarray) -> dict[str, int | None]:
        """Recognize player bet values for seats 2 through 6.

        Args:
            img: BGR source image.

        Returns:
            Mapping of seat number strings to bet values or None.
        """
        return {
            seat: self._recognize_region(img, f"player_bet_{seat}")
            for seat in PLAYER_SEATS
        }

    def recognize_all(self, img: np.ndarray) -> NumberRecognitionResult:
        """Recognize all configured number regions.

        Args:
            img: BGR source image.

        Returns:
            Pot, hero stack/bet, player stacks, and player bets.
        """
        return {
            "pot": self.recognize_pot(img),
            "hero_stack": self.recognize_hero_stack(img),
            "hero_bet": self.recognize_hero_bet(img),
            "player_stacks": self.recognize_player_stacks(img),
            "player_bets": self.recognize_player_bets(img),
        }

    def _recognize_region(self, img: np.ndarray, region_key: str) -> int | None:
        crop = self.crop_region(img, region_key)
        if crop is None:
            return None
        return self._ocr_number(crop)

    def _recognize_stack_region(
        self,
        img: np.ndarray,
        region_key: str,
    ) -> int | None:
        crop = self.crop_region(img, region_key)
        if crop is None:
            return None
        return self._ocr_stack_number(crop)

    def _ocr_stack_number(self, crop: np.ndarray) -> int | None:
        value = self._ocr_number(crop)
        if value is not None:
            return value
        if self._looks_like_zero_stack(crop):
            return 0
        return None

    def _preprocess_pot_color(self, crop: np.ndarray) -> np.ndarray:
        """Remove pot label colors and keep yellow/white number pixels.

        Args:
            crop: BGR pot-display crop.

        Returns:
            BGR image with non-number colors blacked out.
        """
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        h, s, v = cv2.split(hsv)
        yellow_mask = (h >= 15) & (h <= 40) & (s > 60) & (v > 120)
        white_mask = (s < 80) & (v > 180)
        keep_mask = yellow_mask | white_mask
        result = crop.copy()
        result[~keep_mask] = 0
        return result

    def _is_empty_region(self, crop: np.ndarray) -> bool:
        """Return whether a number region appears empty.

        Args:
            crop: BGR number-region crop.

        Returns:
            True if grayscale standard deviation is below the configured
            empty-region threshold.
        """
        if crop.size == 0:
            return True
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        return float(np.std(gray)) < self._empty_region_std

    def _ocr_number(self, crop: np.ndarray, is_pot: bool = False) -> int | None:
        """OCR a number crop and return an integer value.

        Args:
            crop: BGR number-region crop.
            is_pot: Whether to apply pot-specific color filtering.

        Returns:
            Recognized integer value, or None on empty region or failure.
        """
        if crop.size == 0 or self._is_empty_region(crop):
            return None

        try:
            prepared = self._preprocess_pot_color(crop) if is_pot else crop
            if self._is_empty_region(prepared):
                return None

            gray = cv2.cvtColor(prepared, cv2.COLOR_BGR2GRAY)
            _threshold, binary = cv2.threshold(
                gray,
                0,
                255,
                cv2.THRESH_BINARY + cv2.THRESH_OTSU,
            )
            enlarged = cv2.resize(
                binary,
                None,
                fx=2,
                fy=2,
                interpolation=cv2.INTER_CUBIC,
            )
            reader = get_reader(self._languages)
            results = reader.readtext(
                enlarged,
                allowlist=NUMBER_ALLOWLIST,
                detail=1,
            )
        except Exception:
            logger.exception("Number OCR failed")
            return None

        return self._clean_number_tokens(results)

    def _clean_number_tokens(self, results: list[Any]) -> int | None:
        """Clean EasyOCR tokens and convert them to an integer.

        Args:
            results: EasyOCR readtext results.

        Returns:
            Parsed integer, or None if no usable numeric token remains.
        """
        tokens: list[tuple[float, float, str]] = []
        for result in results:
            try:
                bbox = result[0]
                text = str(result[1])
                confidence = float(result[2])
            except (IndexError, TypeError, ValueError):
                continue
            if confidence < self._confidence_threshold:
                continue

            cleaned = self._clean_token(text)
            if not cleaned:
                continue
            x_position = self._token_x_position(bbox)
            y_position = self._token_y_position(bbox)
            tokens.append((x_position, y_position, cleaned))

        if not tokens:
            return None

        tokens.sort(key=lambda item: item[0])
        if len(tokens) > 1:
            min_y = min(y_position for _x, y_position, _token in tokens)
            tokens = [
                (x_position, y_position, token)
                for x_position, y_position, token in tokens
                if y_position <= min_y + 20.0
            ]
        digit_tokens = [token for _x, _y, token in tokens]
        if not digit_tokens:
            return None

        number_text = "".join(digit_tokens)
        try:
            return int(number_text)
        except ValueError:
            logger.debug("Number OCR produced non-integer text: %s", number_text)
            return None

    def _clean_token(self, text: str) -> str:
        normalized = text.upper()
        for value in ("USDT", "USD", "CHP"):
            normalized = normalized.replace(value, "")
        normalized = normalized.replace("$", "")
        normalized = normalized.replace(",", "")
        normalized = normalized.replace(".", "")
        normalized = normalized.replace(" ", "")
        return re.sub(r"\D", "", normalized)

    def _token_x_position(self, bbox: Any) -> float:
        try:
            return float(min(point[0] for point in bbox))
        except (TypeError, ValueError, IndexError):
            return 0.0

    def _token_y_position(self, bbox: Any) -> float:
        try:
            return float(min(point[1] for point in bbox))
        except (TypeError, ValueError, IndexError):
            return 0.0

    def _looks_like_zero_stack(self, crop: np.ndarray) -> bool:
        if crop.size == 0 or self._is_empty_region(crop):
            return False
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        h, s, v = cv2.split(hsv)
        yellow_mask = (h >= 15) & (h <= 40) & (s > 60) & (v > 120)
        white_mask = (s < 80) & (v > 180)
        yellow_ratio = float(np.count_nonzero(yellow_mask)) / float(h.size)
        white_ratio = float(np.count_nonzero(white_mask)) / float(h.size)
        return yellow_ratio > 0.02 and white_ratio < 0.03
