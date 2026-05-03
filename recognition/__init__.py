"""Recognition package.

EasyOCR Reader singleton is managed here.
"""

import threading

import easyocr

_reader: easyocr.Reader | None = None
_lock = threading.Lock()


def get_reader(languages: list[str] | None = None) -> easyocr.Reader:
    """Return the EasyOCR Reader singleton instance.

    The reader is initialized in GPU mode on first use. Later calls return
    the same instance. If languages is omitted, English is used.

    Args:
        languages: EasyOCR language codes.

    Returns:
        Shared EasyOCR Reader instance.
    """
    global _reader
    if _reader is None:
        with _lock:
            if _reader is None:
                selected_languages = languages if languages is not None else ["en"]
                _reader = easyocr.Reader(selected_languages, gpu=True)
    return _reader


def reset_reader() -> None:
    """Reset the EasyOCR Reader singleton for tests."""
    global _reader
    with _lock:
        _reader = None
