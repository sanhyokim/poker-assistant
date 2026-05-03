"""Tests for player-name recognition."""

from typing import Any

import numpy as np

from recognition.name_recognizer import NameRecognizer


class DummyReader:
    """Small EasyOCR reader stub for tests."""

    def __init__(self, results: list[Any] | None = None) -> None:
        self.results = results or []

    def readtext(self, _img: np.ndarray, **_kwargs: Any) -> list[Any]:
        """Return configured OCR results."""
        return self.results


def test_recognize_player_names_returns_all_seat_keys(
    monkeypatch: Any,
) -> None:
    """recognize_player_names returns keys for seats 2 through 6."""
    monkeypatch.setattr(
        "recognition.name_recognizer.get_reader",
        lambda _languages: DummyReader([([], "PlayerOne", 0.95)]),
    )
    profile = {
        f"player_name_{seat}": {"x": 0, "y": 0, "w": 10, "h": 10}
        for seat in range(2, 7)
    }
    config = {"ocr": {"languages": ["en"], "confidence_threshold": 0.4}}
    recognizer = NameRecognizer(profile, config)
    img = np.zeros((20, 20, 3), dtype=np.uint8)
    img[::2, ::2] = 255

    names = recognizer.recognize_player_names(img)

    assert set(names.keys()) == {"2", "3", "4", "5", "6"}
    assert all(name == "PlayerOne" for name in names.values())


def test_empty_region_returns_none(monkeypatch: Any) -> None:
    """A visually empty region returns None."""
    monkeypatch.setattr(
        "recognition.name_recognizer.get_reader",
        lambda _languages: DummyReader([([], "PlayerOne", 0.95)]),
    )
    profile = {"player_name_2": {"x": 0, "y": 0, "w": 10, "h": 10}}
    recognizer = NameRecognizer(profile, {"action_estimation": {"empty_region_std": 8}})
    img = np.zeros((20, 20, 3), dtype=np.uint8)

    assert recognizer._recognize_single_name(img, "player_name_2") is None


def test_missing_profile_key_returns_none(monkeypatch: Any) -> None:
    """A missing coordinate key returns None."""
    monkeypatch.setattr(
        "recognition.name_recognizer.get_reader",
        lambda _languages: DummyReader([([], "PlayerOne", 0.95)]),
    )
    recognizer = NameRecognizer({}, {})
    img = np.full((20, 20, 3), 120, dtype=np.uint8)

    assert recognizer._recognize_single_name(img, "player_name_2") is None


def test_low_confidence_returns_none(monkeypatch: Any) -> None:
    """Low-confidence OCR results are ignored."""
    monkeypatch.setattr(
        "recognition.name_recognizer.get_reader",
        lambda _languages: DummyReader([([], "PlayerOne", 0.2)]),
    )
    profile = {"player_name_2": {"x": 0, "y": 0, "w": 10, "h": 10}}
    recognizer = NameRecognizer(
        profile,
        {"ocr": {"confidence_threshold": 0.4}},
    )
    img = np.full((20, 20, 3), 120, dtype=np.uint8)

    assert recognizer._recognize_single_name(img, "player_name_2") is None


def test_clean_player_name_removes_leading_noise(monkeypatch: Any) -> None:
    """Leading OCR noise is removed from recognized player names."""
    monkeypatch.setattr(
        "recognition.name_recognizer.get_reader",
        lambda _languages: DummyReader([([], "~-.Player..", 0.95)]),
    )
    profile = {"player_name_2": {"x": 0, "y": 0, "w": 10, "h": 10}}
    recognizer = NameRecognizer(
        profile,
        {"ocr": {"confidence_threshold": 0.4}},
    )
    img = np.full((20, 20, 3), 120, dtype=np.uint8)
    img[::2, ::2] = 255

    assert recognizer._recognize_single_name(img, "player_name_2") == "Player.."


def test_clean_player_name_keeps_original_when_empty(monkeypatch: Any) -> None:
    """Names made only of symbols are not converted to an empty string."""
    monkeypatch.setattr(
        "recognition.name_recognizer.get_reader",
        lambda _languages: DummyReader([([], "~~~", 0.95)]),
    )
    profile = {"player_name_2": {"x": 0, "y": 0, "w": 10, "h": 10}}
    recognizer = NameRecognizer(
        profile,
        {"ocr": {"confidence_threshold": 0.4}},
    )
    img = np.full((20, 20, 3), 120, dtype=np.uint8)
    img[::2, ::2] = 255

    assert recognizer._recognize_single_name(img, "player_name_2") == "~~~"
