"""Screen capture using mss for development purposes.

Captures the primary monitor screen and converts to BGR numpy array.
"""

import logging
from typing import Any

import cv2
import mss
import numpy as np

from capture.base_capture import BaseCapture

logger = logging.getLogger(__name__)


class MssCapture(BaseCapture):
    """Capture source using mss screen capture.

    Captures the primary monitor. Suitable for development when
    CoinPoker is running on the same machine without a capture card.

    Args:
        monitor_index: Monitor number. 1 is usually the primary monitor.
    """

    def __init__(self, monitor_index: int = 1) -> None:
        self._monitor_index = monitor_index
        self._sct: Any | None = None

        try:
            self._sct = mss.mss()
            logger.info("MssCapture opened monitor %d", monitor_index)
        except Exception:
            logger.exception("MssCapture: exception initializing mss")
            self._sct = None

    def get_frame(self) -> np.ndarray | None:
        """Capture a screenshot of the monitor.

        Returns:
            BGR numpy.ndarray, or None if capture failed.
        """
        if self._sct is None:
            return None

        try:
            monitor = self._sct.monitors[self._monitor_index]
            screenshot = self._sct.grab(monitor)
            frame = np.array(screenshot, dtype=np.uint8)
            frame_bgr = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
            return frame_bgr
        except Exception:
            logger.exception("MssCapture: failed to capture screen")
            return None

    def is_open(self) -> bool:
        """Check if mss is initialized.

        Returns:
            True if mss is ready for capture.
        """
        return self._sct is not None

    def release(self) -> None:
        """Release mss resources."""
        if self._sct is not None:
            self._sct.close()
            self._sct = None
            logger.info("MssCapture released")
