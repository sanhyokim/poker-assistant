"""File-based capture for testing and development.

Reads image files from a directory or a single file path,
returning them as BGR numpy arrays via the BaseCapture interface.
"""

import logging
import pathlib

import cv2
import numpy as np

from capture.base_capture import BaseCapture

logger = logging.getLogger(__name__)


class FileCapture(BaseCapture):
    """Capture source that reads from image files.

    Supports two modes:
    - Single file: returns the same image on every get_frame() call.
    - Directory: iterates through sorted PNG files sequentially.
      After the last file, returns None (no looping).

    Args:
        path: Path to a single image file or a directory of images.
    """

    def __init__(self, path: str | pathlib.Path) -> None:
        self._path = pathlib.Path(path)
        self._files: list[pathlib.Path] = []
        self._index: int = 0
        self._opened: bool = False

        if self._path.is_file():
            self._files = [self._path]
            self._opened = True
            logger.info("FileCapture opened single file: %s", self._path)
        elif self._path.is_dir():
            self._files = sorted(self._path.glob("*.png"))
            if not self._files:
                logger.warning(
                    "FileCapture: no PNG files found in %s", self._path
                )
            else:
                self._opened = True
                logger.info(
                    "FileCapture opened directory: %s (%d files)",
                    self._path,
                    len(self._files),
                )
        else:
            logger.error("FileCapture: path does not exist: %s", self._path)

    def get_frame(self) -> np.ndarray | None:
        """Read and return the next image file as a BGR numpy array.

        Returns:
            BGR numpy.ndarray, or None if no more files or read error.
        """
        if not self._opened or self._index >= len(self._files):
            return None

        file_path = self._files[self._index]
        img = cv2.imread(str(file_path), cv2.IMREAD_COLOR)

        if img is None:
            logger.warning("FileCapture: failed to read %s", file_path)
            self._index += 1
            return None

        self._index += 1
        logger.debug(
            "FileCapture: read frame %d/%d from %s",
            self._index,
            len(self._files),
            file_path.name,
        )
        return img

    def is_open(self) -> bool:
        """Check if capture source is open and has remaining files.

        Returns:
            True if there are files remaining to read.
        """
        return self._opened and self._index < len(self._files)

    def release(self) -> None:
        """Release the file capture source."""
        self._opened = False
        self._files = []
        self._index = 0
        logger.info("FileCapture released")

    def reset(self) -> None:
        """Reset the file index to re-read from the beginning.

        Useful for re-running the same sequence of images.
        """
        self._index = 0
        if self._files:
            self._opened = True
        logger.debug("FileCapture reset to beginning")
