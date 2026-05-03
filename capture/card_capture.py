"""Capture card input via OpenCV VideoCapture.

Reads frames from an HDMI capture card connected via USB3.0,
configured for 1920x1080@60fps MJPG.
"""

import logging
import time

import cv2
import numpy as np

from capture.base_capture import BaseCapture

logger = logging.getLogger(__name__)


class CardCapture(BaseCapture):
    """Capture source using HDMI capture card via OpenCV.

    Args:
        device_index: Video device index.
        width: Frame width in pixels.
        height: Frame height in pixels.
        fps: Target frames per second.
    """

    def __init__(
        self,
        device_index: int = 0,
        width: int = 1920,
        height: int = 1080,
        fps: int = 60,
    ) -> None:
        self._device_index = device_index
        self._width = width
        self._height = height
        self._fps = fps
        self._cap: cv2.VideoCapture | None = None

        self._open_device()

    def _open_device(self) -> bool:
        """Open the configured capture card device."""
        try:
            self._cap = cv2.VideoCapture(self._device_index, cv2.CAP_MSMF)
            if not self._cap.isOpened():
                logger.error(
                    "CardCapture: failed to open device %d", self._device_index
                )
                self._cap = None
                return False

            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
            self._cap.set(cv2.CAP_PROP_FPS, self._fps)
            self._cap.set(
                cv2.CAP_PROP_FOURCC,
                cv2.VideoWriter_fourcc(*"MJPG"),
            )
            logger.info(
                "CardCapture opened device %d (%dx%d@%dfps)",
                self._device_index,
                self._width,
                self._height,
                self._fps,
            )
            return True
        except Exception:
            logger.exception(
                "CardCapture: exception opening device %d", self._device_index
            )
            self._cap = None
            return False

    def get_frame(self) -> np.ndarray | None:
        """Capture a single frame from the capture card.

        Returns:
            BGR numpy.ndarray of shape (height, width, 3),
            or None if capture failed.
        """
        if self._cap is None or not self._cap.isOpened():
            return None

        ret, frame = self._cap.read()
        if not ret or frame is None:
            logger.warning("CardCapture: failed to read frame")
            return None

        return frame

    def is_open(self) -> bool:
        """Check if the capture card device is open.

        Returns:
            True if device is open and ready.
        """
        return self._cap is not None and self._cap.isOpened()

    def release(self) -> None:
        """Release the capture card device."""
        if self._cap is not None:
            self._cap.release()
            self._cap = None
            logger.info("CardCapture released device %d", self._device_index)

    def reconnect(self) -> bool:
        """Attempt to reconnect the capture card device.

        Returns:
            True when the device was reopened successfully.
        """
        logger.info("Attempting capture device reconnection")
        try:
            self.release()
            time.sleep(1.0)
            return self._open_device()
        except Exception:
            logger.exception("Capture device reconnection error")
            self._cap = None
            return False
