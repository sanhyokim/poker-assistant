"""Capture package.

Provides a factory function to create the appropriate capture source
based on config.yaml settings.
"""

import logging
from typing import Any

from capture.base_capture import BaseCapture
from capture.card_capture import CardCapture
from capture.file_capture import FileCapture
from capture.mss_capture import MssCapture

logger = logging.getLogger(__name__)


def create_capture(config: dict[str, Any]) -> BaseCapture:
    """Create a capture instance based on configuration.

    Args:
        config: Full config dictionary parsed from config.yaml. Must contain
            a capture section with a method key. Supported methods are
            capture_card, mss, and file.

    Returns:
        A BaseCapture subclass instance.

    Raises:
        ValueError: If capture method is unknown or file_path is missing.
    """
    capture_config = config.get("capture", {})
    method = capture_config.get("method", "capture_card")

    if method == "capture_card":
        return CardCapture(
            device_index=capture_config.get("device_index", 0),
            width=capture_config.get("width", 1920),
            height=capture_config.get("height", 1080),
            fps=capture_config.get("fps", 60),
        )
    if method == "mss":
        return MssCapture(
            monitor_index=capture_config.get("monitor_index", 1),
        )
    if method == "file":
        file_path = capture_config.get("file_path", "")
        if not file_path:
            logger.error(
                "create_capture: 'file' method requires "
                "'capture.file_path' in config"
            )
            raise ValueError(
                "capture.file_path is required for 'file' method"
            )
        return FileCapture(path=file_path)

    raise ValueError(f"Unknown capture method: '{method}'")
