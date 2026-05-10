"""Button recognition for hero turn detection and button classification."""

from typing import Any, TypedDict

import cv2
import numpy as np

from recognition.base_recognizer import BaseRecognizer

BET_REGION_KEYS = (
    "hero_bet",
    "player_bet_2",
    "player_bet_3",
    "player_bet_4",
    "player_bet_5",
    "player_bet_6",
)


class ButtonState(TypedDict):
    """Recognized action button state."""

    fold: bool
    call_or_check: str
    raise_or_bet: str | None


class ButtonRecognizer(BaseRecognizer):
    """Recognize hero turn state and action button types.

    Args:
        profile: Coordinate profile dictionary.
        config: Full application config dictionary.
    """

    def __init__(self, profile: dict[str, Any], config: dict[str, Any]) -> None:
        super().__init__(profile, config)

    def recognize(self, img: np.ndarray) -> dict[str, Any]:
        """Recognize turn state and buttons from an image.

        Args:
            img: BGR source image.

        Returns:
            Dictionary containing is_my_turn and buttons.
        """
        return {
            "is_my_turn": self.detect_my_turn(img),
            "buttons": self.classify_buttons(img),
        }

    def detect_my_turn(self, img: np.ndarray) -> bool:
        """Detect whether it is hero's turn from action button colors.

        Args:
            img: BGR source image.

        Returns:
            True if fold red and call/check green buttons are visible.
        """
        fold_crop = self.crop_region(img, "btn_fold")
        if fold_crop is None:
            return False

        fold_h, fold_s, fold_v = self._mean_hsv(fold_crop)
        fold_is_red = (
            (fold_h > 155.0 or fold_h < 10.0)
            and fold_s > 150.0
            and fold_v > 140.0
        )
        if not fold_is_red:
            return False

        call_crop = self.crop_region(img, "btn_call_check")
        if call_crop is None:
            return True

        call_h, call_s, call_v = self._mean_hsv(call_crop)
        return 35.0 <= call_h <= 90.0 and call_s > 150.0 and call_v > 100.0

    def classify_buttons(self, img: np.ndarray) -> ButtonState | None:
        """Classify action button types when it is hero's turn.

        Args:
            img: BGR source image.

        Returns:
            Button state dictionary, or None when it is not hero's turn.
        """
        if not self.detect_my_turn(img):
            return None

        has_active_bets = self._has_active_bets(img)
        raise_crop = self.crop_region(img, "btn_raise_bet")
        raise_or_bet: str | None = None
        if raise_crop is not None and self._is_orange_button(raise_crop):
            raise_or_bet = "raise" if has_active_bets else "bet"

        return {
            "fold": True,
            "call_or_check": "call" if has_active_bets else "check",
            "raise_or_bet": raise_or_bet,
        }

    def _has_active_bets(self, img: np.ndarray) -> bool:
        """Return whether an active bet appears on the table.

        Args:
            img: BGR source image.

        Returns:
            True if any bet region has enough grayscale variation and mean.
        """
        for region_key in BET_REGION_KEYS:
            crop = self.crop_region(img, region_key)
            if crop is None:
                continue
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            if float(np.std(gray)) > 25.0 and float(np.mean(gray)) > 40.0:
                return True
        return False

    def _is_orange_button(self, crop: np.ndarray) -> bool:
        mean_h, mean_s, mean_v = self._mean_hsv(crop)
        return 10.0 <= mean_h <= 35.0 and mean_s > 150.0 and mean_v > 150.0

    def _mean_hsv(self, crop: np.ndarray) -> tuple[float, float, float]:
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        return (
            float(np.mean(hsv[:, :, 0])),
            float(np.mean(hsv[:, :, 1])),
            float(np.mean(hsv[:, :, 2])),
        )
