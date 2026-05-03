"""Abstract base class for recognizers."""

from abc import ABC, abstractmethod
from typing import Any

import numpy as np


class BaseRecognizer(ABC):
    """Abstract base class for recognition modules.

    Args:
        profile: Coordinate profile dictionary.
        config: Full application config dictionary.
    """

    def __init__(self, profile: dict[str, Any], config: dict[str, Any]) -> None:
        self.profile = profile
        self.config = config

    def crop_region(self, img: np.ndarray, region_key: str) -> np.ndarray | None:
        """Crop an image region using the coordinate profile.

        Coordinate entries must use the form {"x": int, "y": int, "w": int,
        "h": int}.

        Args:
            img: BGR source image.
            region_key: Coordinate profile key.

        Returns:
            Cropped BGR image, or None if the region is missing or empty.
        """
        region = self.profile.get(region_key)
        if region is None:
            return None

        x = int(region["x"])
        y = int(region["y"])
        w = int(region["w"])
        h = int(region["h"])
        crop = img[y : y + h, x : x + w]
        if crop.size == 0:
            return None
        return crop

    @abstractmethod
    def recognize(self, img: np.ndarray) -> Any:
        """Recognize information from an image.

        Args:
            img: BGR source image.

        Returns:
            Recognized result in a subclass-specific format.
        """
        ...
