"""Abstract base class for frame capture sources."""

import abc

import numpy as np


class BaseCapture(abc.ABC):
    """Abstract base class for all capture implementations.

    Subclasses must implement get_frame() to provide BGR numpy arrays
    of shape (1080, 1920, 3) representing captured frames.
    """

    @abc.abstractmethod
    def get_frame(self) -> np.ndarray | None:
        """Capture and return a single frame.

        Returns:
            BGR numpy.ndarray of shape (height, width, 3),
            or None if capture failed.
        """
        ...

    @abc.abstractmethod
    def is_open(self) -> bool:
        """Check if the capture source is currently open and available.

        Returns:
            True if capture source is ready for get_frame() calls.
        """
        ...

    @abc.abstractmethod
    def release(self) -> None:
        """Release the capture source and free resources."""
        ...

    def reconnect(self) -> bool:
        """Attempt to reconnect the capture source.

        Returns:
            True if reconnection succeeded. The default implementation returns
            False for capture sources that do not support reconnection.
        """
        return False
