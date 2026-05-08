"""Capture screenshots from the capture card and save them as PNG files.

Usage:
    python scripts/capture_screenshot.py
    python scripts/capture_screenshot.py --device 0 --output screenshots/capture.png
    python scripts/capture_screenshot.py --continuous --interval 0.5
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from datetime import datetime

import cv2


logger = logging.getLogger(__name__)

FRAME_WIDTH = 1920
FRAME_HEIGHT = 1080
FRAME_FPS = 60
WARMUP_FRAMES = 5


def _open_capture(device_index: int) -> cv2.VideoCapture:
    """Open and configure the capture device.

    Args:
        device_index: Capture device index.

    Returns:
        Configured OpenCV VideoCapture instance.
    """
    cap = cv2.VideoCapture(device_index, cv2.CAP_MSMF)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
    cap.set(cv2.CAP_PROP_FPS, FRAME_FPS)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    return cap


def _discard_warmup_frames(cap: cv2.VideoCapture) -> None:
    """Discard initial frames while the capture device warms up.

    Args:
        cap: Open capture device.
    """
    for _ in range(WARMUP_FRAMES):
        cap.read()


def capture_single(device_index: int, output_path: str) -> bool:
    """Capture a single frame and save it to a PNG file.

    Args:
        device_index: Capture device index.
        output_path: Output PNG path.

    Returns:
        True if the frame was captured and saved.
    """
    cap = _open_capture(device_index)
    if not cap.isOpened():
        logger.error("Cannot open capture device %d", device_index)
        return False

    try:
        _discard_warmup_frames(cap)
        ret, frame = cap.read()
    finally:
        cap.release()

    if not ret or frame is None:
        logger.error("Failed to capture frame")
        return False

    logger.info("Captured frame: %dx%d", frame.shape[1], frame.shape[0])
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    if not cv2.imwrite(output_path, frame):
        logger.error("Failed to save frame: %s", output_path)
        return False

    logger.info("Saved: %s", output_path)
    return True


def capture_continuous(device_index: int, output_dir: str, interval: float) -> None:
    """Capture frames continuously at the given interval.

    Press Ctrl+C to stop.

    Args:
        device_index: Capture device index.
        output_dir: Output directory for PNG files.
        interval: Seconds between captures.
    """
    cap = _open_capture(device_index)
    if not cap.isOpened():
        logger.error("Cannot open capture device %d", device_index)
        return

    os.makedirs(output_dir, exist_ok=True)
    _discard_warmup_frames(cap)

    count = 0
    logger.info(
        "Continuous capture started (interval=%.3fs). Press Ctrl+C to stop.",
        interval,
    )
    try:
        while True:
            ret, frame = cap.read()
            if not ret or frame is None:
                logger.warning("Frame capture failed, retrying")
                time.sleep(0.1)
                continue

            count += 1
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            filepath = os.path.join(output_dir, f"capture_{timestamp}.png")
            if cv2.imwrite(filepath, frame):
                logger.info("[%d] Saved: %s", count, filepath)
            else:
                logger.error("[%d] Failed to save: %s", count, filepath)

            time.sleep(interval)
    except KeyboardInterrupt:
        logger.info("Stopped. Total frames captured: %d", count)
    finally:
        cap.release()


def _build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser.

    Returns:
        Configured argument parser.
    """
    parser = argparse.ArgumentParser(
        description="Capture screenshots from capture card",
    )
    parser.add_argument(
        "--device",
        type=int,
        default=0,
        help="Capture device index (default: 0)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help=(
            "Output file path for single capture "
            "(default: screenshots/capture_TIMESTAMP.png)"
        ),
    )
    parser.add_argument(
        "--continuous",
        action="store_true",
        help="Continuous capture mode",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.5,
        help="Interval between captures in continuous mode (default: 0.5s)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="screenshots",
        help="Output directory for continuous mode (default: screenshots/)",
    )
    return parser


def main() -> None:
    """Run the screenshot capture CLI."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = _build_parser()
    args = parser.parse_args()

    if args.continuous:
        capture_continuous(args.device, args.output_dir, args.interval)
        return

    output_path = args.output
    if output_path is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = f"screenshots/capture_{timestamp}.png"
    capture_single(args.device, output_path)


if __name__ == "__main__":
    main()
