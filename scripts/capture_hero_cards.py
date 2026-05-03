"""Capture diagnostic images for hero-card rank recognition.

This script captures one frame from the capture card and saves:
1. The full frame.
2. Raw hero_card_1 / hero_card_2 crops.
3. Margin-applied hero-card crops.
4. Rank-region crops.
5. Rank-region enlarged images.
6. OTSU, fixed-threshold, dynamic-threshold, and sharpened debug images.

Usage:
1. Open CoinPoker with hero cards dealt.
2. Run: python scripts/capture_hero_cards.py
3. Inspect images under scripts/debug_captures/<timestamp>/.
4. Run multiple times for difficult ranks such as Q, 7, T, and K.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parents[1]
PROFILE_PATH = ROOT_DIR / "profiles" / "coinpoker_6max.json"
OUTPUT_ROOT = ROOT_DIR / "scripts" / "debug_captures"
HERO_CARD_KEYS = ("hero_card_1", "hero_card_2")
HERO_CARD_MARGIN_PX = 3


def load_profile() -> dict[str, Any]:
    """Load the CoinPoker coordinate profile."""
    with PROFILE_PATH.open("r", encoding="utf-8") as file:
        return json.load(file)


def capture_frame() -> tuple[np.ndarray | None, str]:
    """Capture a single frame from the configured capture card.

    Returns:
        Tuple containing the captured BGR frame, or None on failure, and a
        human-readable capture format summary.
    """
    capture = cv2.VideoCapture(0, cv2.CAP_MSMF)
    try:
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
        capture.set(cv2.CAP_PROP_FPS, 60)
        capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))

        ok, frame = capture.read()
        fourcc_int = int(capture.get(cv2.CAP_PROP_FOURCC))
        fourcc = "".join(chr((fourcc_int >> (8 * index)) & 0xFF) for index in range(4))
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = capture.get(cv2.CAP_PROP_FPS)
        summary = f"{fourcc}, {width}x{height}@{fps}fps"
        return frame if ok else None, summary
    finally:
        capture.release()


def save_hero_card_debug_images(
    frame: np.ndarray,
    profile: dict[str, Any],
    output_dir: Path,
) -> None:
    """Save hero-card crop and preprocessing diagnostics."""
    cv2.imwrite(str(output_dir / "full_frame.png"), frame)
    logger.info("Full frame: %s", frame.shape)

    for key in HERO_CARD_KEYS:
        region = profile[key]
        x = int(region["x"])
        y = int(region["y"])
        width = int(region["w"])
        height = int(region["h"])

        crop_raw = frame[y : y + height, x : x + width]
        cv2.imwrite(str(output_dir / f"{key}_raw.png"), crop_raw)
        logger.info("%s raw: %s (w=%d, h=%d)", key, crop_raw.shape, width, height)

        margin = HERO_CARD_MARGIN_PX
        crop_margin = frame[
            y + margin : y + height - margin,
            x + margin : x + width - margin,
        ]
        cv2.imwrite(str(output_dir / f"{key}_margin3.png"), crop_margin)
        logger.info("%s margin3: %s", key, crop_margin.shape)

        card_height, card_width = crop_margin.shape[:2]
        rank_region = crop_margin[0 : card_height // 2, 0 : int(card_width * 2 / 3)]
        cv2.imwrite(str(output_dir / f"{key}_rank_region.png"), rank_region)

        gray = cv2.cvtColor(rank_region, cv2.COLOR_BGR2GRAY)
        logger.info(
            "%s rank region: shape=%s, gray_mean=%.1f, gray_std=%.1f, "
            "gray_min=%d, gray_max=%d",
            key,
            rank_region.shape,
            float(np.mean(gray)),
            float(np.std(gray)),
            int(np.min(gray)),
            int(np.max(gray)),
        )

        scale = 5 if width < 50 else 3
        enlarged = cv2.resize(
            rank_region,
            None,
            fx=scale,
            fy=scale,
            interpolation=cv2.INTER_CUBIC,
        )
        cv2.imwrite(str(output_dir / f"{key}_rank_enlarged_x{scale}.png"), enlarged)
        logger.info("%s enlarged x%d: %s", key, scale, enlarged.shape)

        save_preprocessing_debug_images(key, gray, scale, output_dir)


def save_preprocessing_debug_images(
    key: str,
    gray: np.ndarray,
    scale: int,
    output_dir: Path,
) -> None:
    """Save rank-region preprocessing variants used for OCR debugging."""
    _otsu_threshold, otsu = cv2.threshold(
        gray,
        0,
        255,
        cv2.THRESH_BINARY + cv2.THRESH_OTSU,
    )
    cv2.imwrite(str(output_dir / f"{key}_binary_otsu.png"), otsu)

    _fixed_threshold, fixed128 = cv2.threshold(
        gray,
        128,
        255,
        cv2.THRESH_BINARY_INV,
    )
    cv2.imwrite(str(output_dir / f"{key}_binary_fixed128_inv.png"), fixed128)

    dynamic_thresh = max(int(float(np.mean(gray)) * 0.4), 50)
    _dynamic_threshold, dynamic = cv2.threshold(
        gray,
        dynamic_thresh,
        255,
        cv2.THRESH_BINARY_INV,
    )
    cv2.imwrite(
        str(output_dir / f"{key}_binary_dynamic{dynamic_thresh}_inv.png"),
        dynamic,
    )

    blurred = cv2.GaussianBlur(gray, (0, 0), 2.0)
    sharpened = cv2.addWeighted(gray, 2.0, blurred, -1.0, 0)
    enlarged_sharp = cv2.resize(
        sharpened,
        None,
        fx=scale,
        fy=scale,
        interpolation=cv2.INTER_CUBIC,
    )
    cv2.imwrite(str(output_dir / f"{key}_sharpened_gray_x{scale}.png"), enlarged_sharp)


def main() -> int:
    """Capture and save one set of hero-card diagnostic images."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    profile = load_profile()
    frame, capture_summary = capture_frame()
    logger.info("Capture format: %s", capture_summary)
    if frame is None:
        logger.error("ERROR: Failed to capture frame")
        return 1

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = OUTPUT_ROOT / timestamp
    output_dir.mkdir(parents=True, exist_ok=True)

    save_hero_card_debug_images(frame, profile, output_dir)
    logger.info("")
    logger.info("All files saved to: %s", output_dir)
    logger.info("Compare the rank_region and binary images to understand OCR failures.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
