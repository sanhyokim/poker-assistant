"""Card recognition using HSV suit detection and EasyOCR rank detection."""

import logging
from collections.abc import Callable
from typing import Any, TypedDict

import cv2
import numpy as np

from recognition import get_reader
from recognition.base_recognizer import BaseRecognizer

logger = logging.getLogger(__name__)

HERO_CARD_KEYS = ("hero_card_1", "hero_card_2")
BOARD_CARD_KEYS = (
    "board_card_1",
    "board_card_2",
    "board_card_3",
    "board_card_4",
    "board_card_5",
)
HERO_CARD_MARGIN_PX = 3
VALID_RANKS = {"A", "2", "3", "4", "5", "6", "7", "8", "9", "T", "J", "Q", "K"}


class CardRecognitionResult(TypedDict):
    """Detailed card recognition result for analysis scripts."""

    region_key: str
    visible: bool
    card: str | None
    rank: str | None
    suit: str | None
    rank_confidence: float | None


def is_card_visible(card_img: np.ndarray, allow_fallback: bool = False) -> bool:
    """Return whether a card appears visible in a cropped card region.

    Dark, grayed, and blank card regions are treated as not visible. The
    decision is based on HSV value-channel mean and standard deviation.

    Args:
        card_img: BGR cropped card image.

    Returns:
        True if the card region appears visible.
    """
    _mean_v, _std_v, _white_ratio, visible = _card_visibility_metrics(
        card_img,
        allow_fallback=allow_fallback,
    )
    return visible


def _card_visibility_metrics(
    card_img: np.ndarray,
    allow_fallback: bool = False,
) -> tuple[float, float, float, bool]:
    if card_img.size == 0:
        return 0.0, 0.0, 0.0, False

    hsv = cv2.cvtColor(card_img, cv2.COLOR_BGR2HSV)
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]
    mean_v = float(np.mean(value))
    std_v = float(np.std(value))
    white_card_area = (saturation < 45) & (value > 170)
    white_ratio = float(np.count_nonzero(white_card_area)) / float(value.size)
    visible = mean_v >= 60.0 and std_v >= 15.0 and white_ratio >= 0.20
    if (
        allow_fallback
        and not visible
        and mean_v >= 30.0
        and std_v >= 8.0
        and white_ratio >= 0.15
    ):
        logger.debug(
            "Visibility fallback accepted: mean_v=%.1f, std_v=%.1f, "
            "white_ratio=%.2f",
            mean_v,
            std_v,
            white_ratio,
        )
        visible = True
    return mean_v, std_v, white_ratio, visible


def detect_suit(card_img: np.ndarray) -> str | None:
    """Detect card suit using four-color HSV thresholds.

    White card background is excluded, and red-purple seat background pixels
    are filtered before suit-color scoring.

    Args:
        card_img: BGR cropped card image.

    Returns:
        One of "h", "d", "c", "s", or None.
    """
    scores = _suit_hsv_counts(card_img)
    if not scores:
        return None

    suit, score = max(scores.items(), key=lambda item: item[1])
    if score <= 0:
        return None
    return suit


def _suit_hsv_counts(card_img: np.ndarray) -> dict[str, int]:
    if card_img.size == 0:
        return {}

    hsv = cv2.cvtColor(card_img, cv2.COLOR_BGR2HSV)
    hue = hsv[:, :, 0]
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]

    foreground = (saturation > 30) | (value < 200)
    seat_background = (hue >= 145) & (hue <= 180) & (value < 110)
    mask = foreground & ~seat_background
    if not np.any(mask):
        return {}

    heart = ((hue < 10) | (hue > 170)) & (saturation > 80) & mask
    diamond = (hue >= 95) & (hue <= 140) & (saturation > 70) & mask
    club = (hue >= 35) & (hue <= 85) & (saturation > 50) & mask
    spade = (saturation < 50) & (value < 150) & mask

    scores = {
        "h": int(np.count_nonzero(heart)),
        "d": int(np.count_nonzero(diamond)),
        "c": int(np.count_nonzero(club)),
        "s": int(np.count_nonzero(spade)),
    }
    return scores


def detect_rank(card_img: np.ndarray, card_width: int) -> str | None:
    """Detect card rank with EasyOCR.

    Args:
        card_img: BGR cropped card image.
        card_width: Width of the cropped card region.

    Returns:
        One of "A", "2", ..., "9", "T", "J", "Q", "K", or None.
    """
    rank, _confidence = _detect_rank_with_confidence(card_img, card_width, ["en"])
    return rank


def _normalize_rank(text: str) -> str | None:
    normalized = text.strip().upper().replace(" ", "")
    if normalized in {"10", "1O", "IO", "I0"}:
        return "T"
    if normalized == "0":
        return "Q"
    if normalized == "O":
        return "Q"
    if normalized == "I":
        return "J"
    if normalized in VALID_RANKS:
        return normalized
    return None


def _detect_rank_with_confidence(
    card_img: np.ndarray,
    card_width: int,
    languages: list[str],
    confidence_threshold: float = 0.4,
    region_key: str | None = None,
    failure_logger: Callable[
        [str, float, float, float, tuple[int, ...] | None],
        None,
    ]
    | None = None,
) -> tuple[str | None, float | None]:
    if card_img is None or card_img.size == 0:
        return None, 0.0

    try:
        card_height, card_actual_width = card_img.shape[:2]
        if card_actual_width < 50:
            # Hero cards are narrow; include the full width for two-character
            # ranks like T and more height for the upper stroke of 7.
            rank_region = card_img[0 : int(card_height * 0.65), 0:card_actual_width]
        else:
            rank_region = card_img[
                0 : card_height // 2,
                0 : int(card_actual_width * 2 / 3),
            ]
        if rank_region.size == 0:
            return None, 0.0

        gray_no_clahe = cv2.cvtColor(rank_region, cv2.COLOR_BGR2GRAY)
        gray_mean = float(np.mean(gray_no_clahe))
        gray_std = float(np.std(gray_no_clahe))
        gray = gray_no_clahe.copy()
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4))
        gray = clahe.apply(gray)
        gray = cv2.GaussianBlur(gray, (3, 3), 0)
        _threshold, binary = cv2.threshold(
            gray,
            0,
            255,
            cv2.THRESH_BINARY + cv2.THRESH_OTSU,
        )
        kernel = np.ones((2, 2), np.uint8)
        binary = cv2.dilate(binary, kernel, iterations=1)
        scale = 5 if card_width < 50 else 3

        reader = get_reader(languages)
        attempts = [
            ("otsu", binary),
            ("inverted", cv2.bitwise_not(binary)),
        ]
        adaptive = cv2.adaptiveThreshold(
            gray,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            11,
            2,
        )
        adaptive = cv2.dilate(adaptive, kernel, iterations=1)
        attempts.append(("adaptive", adaptive))

        gray_blurred = cv2.GaussianBlur(gray_no_clahe, (3, 3), 0)
        _fixed_threshold, binary_fixed = cv2.threshold(
            gray_blurred,
            128,
            255,
            cv2.THRESH_BINARY_INV,
        )
        binary_fixed = cv2.dilate(binary_fixed, kernel, iterations=1)
        attempts.append(("fixed_inverted", binary_fixed))

        _fixed_normal_threshold, binary_fixed_normal = cv2.threshold(
            gray_blurred,
            128,
            255,
            cv2.THRESH_BINARY,
        )
        binary_fixed_normal = cv2.dilate(binary_fixed_normal, kernel, iterations=1)
        attempts.append(("fixed_normal", binary_fixed_normal))

        dynamic_thresh = max(int(gray_mean * 0.4), 50)
        _dynamic_threshold, binary_dynamic = cv2.threshold(
            gray_no_clahe,
            dynamic_thresh,
            255,
            cv2.THRESH_BINARY_INV,
        )
        binary_dynamic = cv2.dilate(binary_dynamic, kernel, iterations=1)
        attempts.append(("dynamic_inverted", binary_dynamic))

        _dynamic_normal_threshold, binary_dynamic_normal = cv2.threshold(
            gray_no_clahe,
            dynamic_thresh,
            255,
            cv2.THRESH_BINARY,
        )
        binary_dynamic_normal = cv2.dilate(
            binary_dynamic_normal,
            kernel,
            iterations=1,
        )
        attempts.append(("dynamic_normal", binary_dynamic_normal))

        last_enlarged_shape: tuple[int, ...] | None = None
        binary_white_ratio = float(np.count_nonzero(binary == 255)) / float(binary.size)
        for attempt_name, attempt_img in attempts:
            enlarged = cv2.resize(
                attempt_img,
                None,
                fx=scale,
                fy=scale,
                interpolation=cv2.INTER_CUBIC,
            )
            last_enlarged_shape = tuple(enlarged.shape)
            if (
                enlarged is None
                or enlarged.size == 0
                or enlarged.shape[0] == 0
                or enlarged.shape[1] == 0
            ):
                logger.debug("Card rank OCR skipped: empty image")
                continue

            results = reader.readtext(
                enlarged,
                allowlist="0123456789AJQKT",
                text_threshold=0.3,
                low_text=0.2,
                min_size=5,
                detail=1,
            )
            rank, confidence = _best_rank_from_ocr_results(
                results,
                confidence_threshold,
                attempt_name,
            )
            if rank is not None:
                if attempt_name != "otsu":
                    logger.debug("Rank OCR succeeded with %s threshold", attempt_name)
                return rank, confidence

        no_binarization_threshold = max(confidence_threshold, 0.85)
        gray_raw = cv2.cvtColor(rank_region, cv2.COLOR_BGR2GRAY)
        blurred_for_sharp = cv2.GaussianBlur(gray_raw, (0, 0), 2.0)
        sharpened = cv2.addWeighted(gray_raw, 2.0, blurred_for_sharp, -1.0, 0)
        enlarged_sharp = cv2.resize(
            sharpened,
            None,
            fx=scale,
            fy=scale,
            interpolation=cv2.INTER_CUBIC,
        )
        results = reader.readtext(
            enlarged_sharp,
            allowlist="0123456789AJQKT",
            text_threshold=0.3,
            low_text=0.2,
            min_size=5,
            detail=1,
        )
        rank, confidence = _best_rank_from_ocr_results(
            results,
            no_binarization_threshold,
            "sharpened_gray",
        )
        if rank is not None:
            logger.debug("Rank OCR succeeded with sharpened_gray (no binarization)")
            return rank, confidence

        blurred_color = cv2.GaussianBlur(rank_region, (0, 0), 2.0)
        sharpened_color = cv2.addWeighted(rank_region, 2.0, blurred_color, -1.0, 0)
        enlarged_color = cv2.resize(
            sharpened_color,
            None,
            fx=scale,
            fy=scale,
            interpolation=cv2.INTER_CUBIC,
        )
        results = reader.readtext(
            enlarged_color,
            allowlist="0123456789AJQKT",
            text_threshold=0.3,
            low_text=0.2,
            min_size=5,
            detail=1,
        )
        rank, confidence = _best_rank_from_ocr_results(
            results,
            no_binarization_threshold,
            "sharpened_color",
        )
        if rank is not None:
            logger.debug("Rank OCR succeeded with sharpened_color (no binarization)")
            return rank, confidence

        fail_key = region_key or "unknown"
        if failure_logger is not None:
            failure_logger(
                fail_key,
                gray_mean,
                gray_std,
                binary_white_ratio,
                last_enlarged_shape,
            )
        else:
            logger.warning(
                "Rank OCR all attempts failed for %s: gray_mean=%.1f, "
                "gray_std=%.1f, binary_white_ratio=%.2f, enlarged_shape=%s",
                fail_key,
                gray_mean,
                gray_std,
                binary_white_ratio,
                last_enlarged_shape,
            )
    except Exception as exc:
        logger.debug("Card rank OCR failed: %s", exc)
        return None, 0.0

    return None, 0.0


def _best_rank_from_ocr_results(
    results: list[Any],
    confidence_threshold: float,
    attempt_name: str,
) -> tuple[str | None, float | None]:
    best_rank: str | None = None
    best_confidence: float | None = None
    if not results:
        logger.debug("Card rank OCR returned no candidates (%s)", attempt_name)
    for result in results:
        text = str(result[1])
        confidence = float(result[2])
        rank = _normalize_rank(text)
        logger.debug(
            "Card rank OCR candidate (%s): raw_text='%s', confidence=%.2f, "
            "normalized='%s'",
            attempt_name,
            text,
            confidence,
            rank,
        )
        if rank is None or confidence < confidence_threshold:
            continue
        if best_confidence is None or confidence > best_confidence:
            best_rank = rank
            best_confidence = confidence

    return best_rank, best_confidence


class CardRecognizer(BaseRecognizer):
    """Card recognition module.

    Public methods recognize hero cards, board cards, and board card counts.
    Card strings use rank plus lowercase suit, for example "Td" or "Ah".
    """

    def __init__(self, profile: dict[str, Any], config: dict[str, Any]) -> None:
        super().__init__(profile, config)
        ocr_config = config.get("ocr", {})
        self._languages: list[str] = list(ocr_config.get("languages", ["en"]))
        self._confidence_threshold = float(
            ocr_config.get("confidence_threshold", 0.4)
        )
        self._rank_fail_logged: dict[str, bool] = {}

    def recognize(self, img: np.ndarray) -> dict[str, list[str | None] | list[str]]:
        """Recognize hero and board cards from an image.

        Args:
            img: BGR source image.

        Returns:
            Dictionary with hero_cards and board_cards entries.
        """
        return {
            "hero_cards": self.recognize_hero_cards(img),
            "board_cards": self.recognize_board_cards(img),
        }

    def recognize_hero_cards(
        self,
        img: np.ndarray,
        log_info: bool = False,
    ) -> list[str | None]:
        """Recognize the two hero hole cards.

        Args:
            img: BGR source image.
            log_info: Whether to emit INFO-level diagnostics for live waiting
                state investigation.

        Returns:
            Two card strings or None values.
        """
        cards: list[str | None] = []
        for card_idx, key in enumerate(HERO_CARD_KEYS, start=1):
            crop = self.crop_region(img, key)
            if self._is_empty_crop(crop):
                logger.debug("Hero card %d crop empty: region=%s", card_idx, key)
                if log_info:
                    logger.info(
                        "Hero card %d: crop failed (region=%s)",
                        card_idx,
                        key,
                    )
                cards.append(None)
                continue
            card_img = self._apply_margin(crop)
            mean_v, std_v, white_ratio, visible = _card_visibility_metrics(
                card_img,
                allow_fallback=True,
            )
            logger.debug(
                "Hero card %d visibility: mean_v=%.1f, std_v=%.1f, "
                "white_ratio=%.2f, visible=%s",
                card_idx,
                mean_v,
                std_v,
                white_ratio,
                visible,
            )
            result = self._recognize_card_from_crop(key, crop, apply_margin=True)
            if log_info and not visible:
                logger.info(
                    "Hero card %d: not visible "
                    "(mean_v=%.1f, std_v=%.1f, white_ratio=%.2f)",
                    card_idx,
                    mean_v,
                    std_v,
                    white_ratio,
                )
            logger.debug(
                "Hero card %d suit detection: hsv_counts=%s, result=%s",
                card_idx,
                _suit_hsv_counts(card_img),
                result["suit"],
            )
            logger.debug(
                "Hero card %d rank OCR: confidence=%s, normalized='%s'",
                card_idx,
                result["rank_confidence"],
                result["rank"],
            )
            if log_info and visible:
                if result["card"] is None:
                    logger.info(
                        "Hero card %d: partial recognition "
                        "(suit=%s, rank=%s, confidence=%s)",
                        card_idx,
                        result["suit"],
                        result["rank"],
                        result["rank_confidence"],
                    )
                else:
                    logger.info("Hero card %d: OK (%s)", card_idx, result["card"])
            cards.append(result["card"])
        return cards

    def recognize_board_cards(self, img: np.ndarray) -> list[str]:
        """Recognize visible board cards.

        Args:
            img: BGR source image.

        Returns:
            List of recognized visible board card strings.
        """
        cards: list[str] = []
        for key in BOARD_CARD_KEYS:
            crop = self.crop_region(img, key)
            if self._is_empty_crop(crop):
                continue
            result = self._recognize_card_from_crop(key, crop, apply_margin=False)
            if result["card"] is not None:
                cards.append(result["card"])
        return cards

    def count_board_cards(self, img: np.ndarray) -> int:
        """Count visible board cards.

        Args:
            img: BGR source image.

        Returns:
            Number of visible board cards.
        """
        count = 0
        for key in BOARD_CARD_KEYS:
            crop = self.crop_region(img, key)
            if crop is not None and is_card_visible(crop):
                count += 1
        return count

    def analyze_cards(self, img: np.ndarray) -> dict[str, list[CardRecognitionResult]]:
        """Return detailed card recognition results for scripts.

        Args:
            img: BGR source image.

        Returns:
            Detailed hero and board card recognition results.
        """
        hero = [
            self._recognize_card_from_crop(
                key,
                self.crop_region(img, key),
                apply_margin=True,
            )
            for key in HERO_CARD_KEYS
        ]
        board = [
            self._recognize_card_from_crop(
                key,
                self.crop_region(img, key),
                apply_margin=False,
            )
            for key in BOARD_CARD_KEYS
        ]
        return {"hero": hero, "board": board}

    def _recognize_card_from_crop(
        self,
        region_key: str,
        crop: np.ndarray | None,
        apply_margin: bool,
    ) -> CardRecognitionResult:
        if self._is_empty_crop(crop):
            return self._empty_result(region_key, visible=False)

        card_img = self._apply_margin(crop) if apply_margin else crop
        if self._is_empty_crop(card_img):
            return self._empty_result(region_key, visible=False)

        visible = is_card_visible(card_img, allow_fallback=apply_margin)
        if not visible:
            return self._empty_result(region_key, visible=False)

        rank, confidence = self._recognize_rank(
            card_img,
            card_img.shape[1],
            region_key,
        )
        suit = detect_suit(card_img)
        card = f"{rank}{suit}" if rank is not None and suit is not None else None
        return {
            "region_key": region_key,
            "visible": True,
            "card": card,
            "rank": rank,
            "suit": suit,
            "rank_confidence": confidence,
        }

    def _recognize_rank(
        self,
        card_img: np.ndarray,
        card_width: int,
        card_key: str = "unknown",
    ) -> tuple[str | None, float | None]:
        rank, confidence = _detect_rank_with_confidence(
            card_img,
            card_width,
            self._languages,
            self._confidence_threshold,
            card_key,
            self._log_rank_ocr_failure_once,
        )
        if rank is not None:
            self._rank_fail_logged[card_key] = False
        return rank, confidence

    def _log_rank_ocr_failure_once(
        self,
        card_key: str,
        gray_mean: float,
        gray_std: float,
        binary_white_ratio: float,
        enlarged_shape: tuple[int, ...] | None,
    ) -> None:
        if self._rank_fail_logged.get(card_key, False):
            return
        logger.warning(
            "Rank OCR all attempts failed for %s "
            "(gray_mean=%.1f, gray_std=%.1f, binary_white_ratio=%.2f, "
            "enlarged_shape=%s)",
            card_key,
            gray_mean,
            gray_std,
            binary_white_ratio,
            enlarged_shape,
        )
        self._rank_fail_logged[card_key] = True

    def _apply_margin(self, card_img: np.ndarray) -> np.ndarray:
        if (
            card_img.shape[0] <= HERO_CARD_MARGIN_PX * 2
            or card_img.shape[1] <= HERO_CARD_MARGIN_PX * 2
        ):
            return card_img
        return card_img[
            HERO_CARD_MARGIN_PX:-HERO_CARD_MARGIN_PX,
            HERO_CARD_MARGIN_PX:-HERO_CARD_MARGIN_PX,
        ]

    def _is_empty_crop(self, crop: np.ndarray | None) -> bool:
        return (
            crop is None
            or crop.size == 0
            or crop.shape[0] == 0
            or crop.shape[1] == 0
        )

    def _empty_result(
        self,
        region_key: str,
        visible: bool,
    ) -> CardRecognitionResult:
        return {
            "region_key": region_key,
            "visible": visible,
            "card": None,
            "rank": None,
            "suit": None,
            "rank_confidence": None,
        }
