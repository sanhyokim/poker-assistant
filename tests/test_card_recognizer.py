"""Card recognizer unit tests.

Phase 3 acceptance criteria:
- 100% card recognition on active normal screens cp_01 and cp_03 through cp_06.
- 100% board card recognition on showdown screens cp_07 and cp_07b.
- Correct invisible hero-card handling on cp_07/cp_07b/cp_09/cp_10/cp_12/cp_13.
- EasyOCR Reader is managed as a singleton instance.
"""

from collections.abc import Callable
from typing import Any

import cv2
import numpy as np
import pytest

import recognition
import recognition.card_recognizer as card_recognizer_module
from recognition.card_recognizer import CardRecognizer

ImageLoader = Callable[[str], np.ndarray]


class FakeReader:
    """Fake EasyOCR Reader used for singleton tests."""

    def __init__(self, languages: list[str], gpu: bool) -> None:
        self.languages = languages
        self.gpu = gpu


class EmptyOcrReader:
    """Fake EasyOCR Reader that returns no OCR candidates."""

    def readtext(self, *_args: Any, **_kwargs: Any) -> list[Any]:
        """Return an empty candidate list for every OCR attempt."""
        return []


@pytest.fixture(scope="module")
def card_recognizer(
    profile: dict[str, Any],
    config: dict[str, Any],
) -> CardRecognizer:
    """Return a shared CardRecognizer instance for screenshot tests."""
    return CardRecognizer(profile, config)


@pytest.fixture(scope="module")
def load_image(screenshots_dir: Any) -> ImageLoader:
    """Return a helper that loads a screenshot by filename.

    Args:
        screenshots_dir: Directory containing CoinPoker screenshots.

    Returns:
        Callable that returns a BGR image for the requested filename.
    """

    def _load(filename: str) -> np.ndarray:
        path = screenshots_dir / filename
        img = cv2.imread(str(path), cv2.IMREAD_COLOR)
        assert img is not None, f"Image not found: {path}"
        return img

    return _load


@pytest.fixture(scope="module")
def screenshots(ground_truth: dict[str, Any]) -> dict[str, Any]:
    """Return screenshot ground truth entries."""
    return ground_truth["screenshots"]


class TestEasyOCRSingleton:
    """Test EasyOCR Reader singleton management."""

    def test_get_reader_returns_same_instance(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_reader() returns the same object on repeated calls."""
        recognition.reset_reader()
        monkeypatch.setattr(recognition.easyocr, "Reader", FakeReader)

        reader_1 = recognition.get_reader(["en"])
        reader_2 = recognition.get_reader(["en"])

        assert reader_1 is reader_2
        recognition.reset_reader()

    def test_reset_reader_clears_instance(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """reset_reader() causes get_reader() to create a new instance."""
        recognition.reset_reader()
        monkeypatch.setattr(recognition.easyocr, "Reader", FakeReader)

        reader_1 = recognition.get_reader(["en"])
        recognition.reset_reader()
        reader_2 = recognition.get_reader(["en"])

        assert reader_1 is not reader_2
        recognition.reset_reader()


class TestRankNormalization:
    """Rank OCR text normalization."""

    def test_normalize_rank_zero_to_queen(self) -> None:
        """A standalone 0 is normalized as a misread queen."""
        assert card_recognizer_module._normalize_rank("0") == "Q"

    def test_normalize_rank_ten_still_works(self) -> None:
        """Ten-like OCR variants are still normalized to T."""
        assert card_recognizer_module._normalize_rank("10") == "T"
        assert card_recognizer_module._normalize_rank("1O") == "T"
        assert card_recognizer_module._normalize_rank("IO") == "T"
        assert card_recognizer_module._normalize_rank("I0") == "T"

    def test_normalize_rank_o_to_queen(self) -> None:
        """A standalone O is normalized as a misread queen."""
        assert card_recognizer_module._normalize_rank("O") == "Q"


class TestActiveScreenCardRecognition:
    """Card recognition on active normal screens. 100% accuracy is required."""

    def test_cp01_preflop_hero_cards(
        self,
        card_recognizer: CardRecognizer,
        load_image: ImageLoader,
        screenshots: dict[str, Any],
    ) -> None:
        """cp_01: hero cards Td, 9c and zero board cards."""
        img = load_image(screenshots["cp_01"]["filename"])

        assert card_recognizer.recognize_hero_cards(img) == ["Td", "9c"]
        assert card_recognizer.count_board_cards(img) == 0

    def test_cp03_flop_all_cards(
        self,
        card_recognizer: CardRecognizer,
        load_image: ImageLoader,
        screenshots: dict[str, Any],
    ) -> None:
        """cp_03: hero 3s,3c and board 8c,7d,8d."""
        img = load_image(screenshots["cp_03"]["filename"])

        assert card_recognizer.recognize_hero_cards(img) == ["3s", "3c"]
        assert card_recognizer.recognize_board_cards(img) == ["8c", "7d", "8d"]
        assert card_recognizer.count_board_cards(img) == 3

    def test_cp04_flop_all_cards(
        self,
        card_recognizer: CardRecognizer,
        load_image: ImageLoader,
        screenshots: dict[str, Any],
    ) -> None:
        """cp_04: same flop cards as cp_03, with hero action buttons visible."""
        img = load_image(screenshots["cp_04"]["filename"])

        assert card_recognizer.recognize_hero_cards(img) == ["3s", "3c"]
        assert card_recognizer.recognize_board_cards(img) == ["8c", "7d", "8d"]
        assert card_recognizer.count_board_cards(img) == 3

    def test_cp05_turn_all_cards(
        self,
        card_recognizer: CardRecognizer,
        load_image: ImageLoader,
        screenshots: dict[str, Any],
    ) -> None:
        """cp_05: hero 3s,3c and board 8c,7d,8d,Ah."""
        img = load_image(screenshots["cp_05"]["filename"])

        assert card_recognizer.recognize_hero_cards(img) == ["3s", "3c"]
        assert card_recognizer.recognize_board_cards(img) == [
            "8c",
            "7d",
            "8d",
            "Ah",
        ]
        assert card_recognizer.count_board_cards(img) == 4

    def test_cp06_river_all_cards(
        self,
        card_recognizer: CardRecognizer,
        load_image: ImageLoader,
        screenshots: dict[str, Any],
    ) -> None:
        """cp_06: hero 3s,3c and board 8c,7d,8d,Ah,Jh."""
        img = load_image(screenshots["cp_06"]["filename"])

        assert card_recognizer.recognize_hero_cards(img) == ["3s", "3c"]
        assert card_recognizer.recognize_board_cards(img) == [
            "8c",
            "7d",
            "8d",
            "Ah",
            "Jh",
        ]
        assert card_recognizer.count_board_cards(img) == 5


class TestShowdownBoardRecognition:
    """Board card recognition on showdown screens."""

    def test_cp07_showdown_board(
        self,
        card_recognizer: CardRecognizer,
        load_image: ImageLoader,
        screenshots: dict[str, Any],
    ) -> None:
        """cp_07: board 4s,3d,5d,Js,8c."""
        img = load_image(screenshots["cp_07"]["filename"])

        assert card_recognizer.recognize_board_cards(img) == [
            "4s",
            "3d",
            "5d",
            "Js",
            "8c",
        ]
        assert card_recognizer.count_board_cards(img) == 5

    def test_cp07b_showdown_board(
        self,
        card_recognizer: CardRecognizer,
        load_image: ImageLoader,
        screenshots: dict[str, Any],
    ) -> None:
        """cp_07b: board Kh,8c,2d,4h,Ks."""
        img = load_image(screenshots["cp_07b"]["filename"])

        assert card_recognizer.recognize_board_cards(img) == [
            "Kh",
            "8c",
            "2d",
            "4h",
            "Ks",
        ]
        assert card_recognizer.count_board_cards(img) == 5


class TestHeroCardVisibility:
    """Hero card visibility handling for invisible hero-card screens."""

    def test_cp07_showdown_hero_not_visible(
        self,
        card_recognizer: CardRecognizer,
        load_image: ImageLoader,
        screenshots: dict[str, Any],
    ) -> None:
        """cp_07: showdown after hero folded returns invisible hero cards."""
        img = load_image(screenshots["cp_07"]["filename"])

        assert card_recognizer.recognize_hero_cards(img) == [None, None]

    def test_cp07b_showdown_hero_not_visible(
        self,
        card_recognizer: CardRecognizer,
        load_image: ImageLoader,
        screenshots: dict[str, Any],
    ) -> None:
        """cp_07b: showdown after hero folded returns invisible hero cards."""
        img = load_image(screenshots["cp_07b"]["filename"])

        assert card_recognizer.recognize_hero_cards(img) == [None, None]

    def test_cp09_player_away_hero_not_visible(
        self,
        card_recognizer: CardRecognizer,
        load_image: ImageLoader,
        screenshots: dict[str, Any],
    ) -> None:
        """cp_09: sit-out screen returns invisible hero cards and zero board."""
        img = load_image(screenshots["cp_09"]["filename"])

        assert card_recognizer.recognize_hero_cards(img) == [None, None]
        assert card_recognizer.count_board_cards(img) == 0

    def test_cp10_folded_hero_not_visible(
        self,
        card_recognizer: CardRecognizer,
        load_image: ImageLoader,
        screenshots: dict[str, Any],
    ) -> None:
        """cp_10: folded full-table screen returns invisible hero cards."""
        img = load_image(screenshots["cp_10"]["filename"])

        assert card_recognizer.recognize_hero_cards(img) == [None, None]

    def test_cp12_spectating_hero_not_visible(
        self,
        card_recognizer: CardRecognizer,
        load_image: ImageLoader,
        screenshots: dict[str, Any],
    ) -> None:
        """cp_12: folded spectating screen returns invisible hero cards."""
        img = load_image(screenshots["cp_12"]["filename"])

        assert card_recognizer.recognize_hero_cards(img) == [None, None]

    def test_cp13_between_hands_hero_not_visible(
        self,
        card_recognizer: CardRecognizer,
        load_image: ImageLoader,
        screenshots: dict[str, Any],
    ) -> None:
        """cp_13: between-hands screen returns invisible hero cards and zero board."""
        img = load_image(screenshots["cp_13"]["filename"])

        assert card_recognizer.recognize_hero_cards(img) == [None, None]
        assert card_recognizer.count_board_cards(img) == 0


class TestInactiveScreens:
    """Inactive screens should not crash even when recognition is incomplete."""

    def test_empty_rank_image_skips_ocr(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Empty rank images return None without creating an OCR reader."""
        reader_called = False

        def _fail_get_reader(_languages: list[str]) -> Any:
            nonlocal reader_called
            reader_called = True
            raise AssertionError("OCR reader should not be requested")

        monkeypatch.setattr(card_recognizer_module, "get_reader", _fail_get_reader)

        rank, confidence = card_recognizer_module._detect_rank_with_confidence(
            np.empty((0, 0, 3), dtype=np.uint8),
            0,
            ["en"],
        )

        assert rank is None
        assert confidence == 0.0
        assert reader_called is False

    def test_empty_profile_regions_return_empty_results(
        self,
        config: dict[str, Any],
    ) -> None:
        """Missing or empty card regions return empty card lists without OCR."""
        profile = {
            "hero_card_1": {"x": 0, "y": 0, "w": 0, "h": 0},
            "hero_card_2": {"x": 0, "y": 0, "w": 0, "h": 0},
            "board_card_1": {"x": 0, "y": 0, "w": 0, "h": 0},
            "board_card_2": {"x": 0, "y": 0, "w": 0, "h": 0},
            "board_card_3": {"x": 0, "y": 0, "w": 0, "h": 0},
            "board_card_4": {"x": 0, "y": 0, "w": 0, "h": 0},
            "board_card_5": {"x": 0, "y": 0, "w": 0, "h": 0},
        }
        recognizer = CardRecognizer(profile, config)
        img = np.zeros((10, 10, 3), dtype=np.uint8)

        assert recognizer.recognize_hero_cards(img) == [None, None]
        assert recognizer.recognize_board_cards(img) == []

    def test_cp02_preaction_no_crash(
        self,
        card_recognizer: CardRecognizer,
        load_image: ImageLoader,
        screenshots: dict[str, Any],
    ) -> None:
        """cp_02: pre-action dimmed screen does not raise during recognition."""
        img = load_image(screenshots["cp_02"]["filename"])

        hero_cards = card_recognizer.recognize_hero_cards(img)
        board_cards = card_recognizer.recognize_board_cards(img)

        assert len(hero_cards) == 2
        assert isinstance(board_cards, list)

    def test_cp08_allin_no_crash(
        self,
        card_recognizer: CardRecognizer,
        load_image: ImageLoader,
        screenshots: dict[str, Any],
    ) -> None:
        """cp_08: all-in screen does not raise during recognition."""
        img = load_image(screenshots["cp_08"]["filename"])

        hero_cards = card_recognizer.recognize_hero_cards(img)
        board_cards = card_recognizer.recognize_board_cards(img)

        assert len(hero_cards) == 2
        assert isinstance(board_cards, list)


class TestRankOcrFallbacks:
    """Rank OCR fallback behavior."""

    def test_rank_ocr_uses_sharpened_gray_after_binarization(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Sharpened grayscale OCR runs after all binary threshold fallbacks."""
        attempt_names: list[str] = []
        read_shapes: list[tuple[int, ...]] = []

        class ShapeReader:
            """Fake reader that records incoming OCR image shapes."""

            def readtext(self, image: np.ndarray, *_args: Any, **_kwargs: Any) -> list[Any]:
                """Record image shape and return no direct OCR candidates."""
                read_shapes.append(tuple(image.shape))
                return []

        def _capture_attempt(
            _results: list[Any],
            _confidence_threshold: float,
            attempt_name: str,
        ) -> tuple[str | None, float | None]:
            attempt_names.append(attempt_name)
            if attempt_name == "sharpened_gray":
                return "Q", 0.93
            return None, None

        monkeypatch.setattr(
            card_recognizer_module,
            "get_reader",
            lambda _languages: ShapeReader(),
        )
        monkeypatch.setattr(
            card_recognizer_module,
            "_best_rank_from_ocr_results",
            _capture_attempt,
        )
        card = np.full((80, 60, 3), 220, dtype=np.uint8)

        rank, confidence = card_recognizer_module._detect_rank_with_confidence(
            card,
            60,
            ["en"],
            region_key="hero_card_1",
        )

        assert rank == "Q"
        assert confidence == 0.93
        assert attempt_names == [
            "otsu",
            "inverted",
            "adaptive",
            "fixed_inverted",
            "fixed_normal",
            "dynamic_inverted",
            "dynamic_normal",
            "sharpened_gray",
        ]
        assert len(read_shapes[-1]) == 2

    def test_rank_ocr_uses_sharpened_color_after_binarization(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Sharpened color OCR runs after binary and grayscale sharp fallbacks."""
        attempt_names: list[str] = []
        read_shapes: list[tuple[int, ...]] = []

        class ShapeReader:
            """Fake reader that records incoming OCR image shapes."""

            def readtext(self, image: np.ndarray, *_args: Any, **_kwargs: Any) -> list[Any]:
                """Record image shape and return no direct OCR candidates."""
                read_shapes.append(tuple(image.shape))
                return []

        def _capture_attempt(
            _results: list[Any],
            _confidence_threshold: float,
            attempt_name: str,
        ) -> tuple[str | None, float | None]:
            attempt_names.append(attempt_name)
            if attempt_name == "sharpened_color":
                return "J", 0.89
            return None, None

        monkeypatch.setattr(
            card_recognizer_module,
            "get_reader",
            lambda _languages: ShapeReader(),
        )
        monkeypatch.setattr(
            card_recognizer_module,
            "_best_rank_from_ocr_results",
            _capture_attempt,
        )
        card = np.full((80, 60, 3), 220, dtype=np.uint8)

        rank, confidence = card_recognizer_module._detect_rank_with_confidence(
            card,
            60,
            ["en"],
            region_key="hero_card_1",
        )

        assert rank == "J"
        assert confidence == 0.89
        assert attempt_names == [
            "otsu",
            "inverted",
            "adaptive",
            "fixed_inverted",
            "fixed_normal",
            "dynamic_inverted",
            "dynamic_normal",
            "sharpened_gray",
            "sharpened_color",
        ]
        assert len(read_shapes[-2]) == 2
        assert len(read_shapes[-1]) == 3

    def test_rank_ocr_uses_fixed_threshold_fallbacks(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Rank OCR tries fixed-threshold fallbacks after the existing attempts."""
        attempt_names: list[str] = []

        def _capture_attempt(
            _results: list[Any],
            _confidence_threshold: float,
            attempt_name: str,
        ) -> tuple[str | None, float | None]:
            attempt_names.append(attempt_name)
            if attempt_name == "fixed_inverted":
                return "A", 0.91
            return None, None

        monkeypatch.setattr(
            card_recognizer_module,
            "get_reader",
            lambda _languages: EmptyOcrReader(),
        )
        monkeypatch.setattr(
            card_recognizer_module,
            "_best_rank_from_ocr_results",
            _capture_attempt,
        )
        bright_card = np.full((80, 60, 3), 230, dtype=np.uint8)
        cv2.putText(
            bright_card,
            "A",
            (4, 28),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (40, 40, 40),
            2,
            cv2.LINE_AA,
        )

        rank, confidence = card_recognizer_module._detect_rank_with_confidence(
            bright_card,
            60,
            ["en"],
            region_key="hero_card_1",
        )

        assert rank == "A"
        assert confidence == 0.91
        assert attempt_names == [
            "otsu",
            "inverted",
            "adaptive",
            "fixed_inverted",
        ]

    def test_rank_ocr_uses_dynamic_threshold_fallbacks(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Rank OCR tries dynamic low-threshold fallbacks after fixed thresholds."""
        attempt_names: list[str] = []
        threshold_values: list[float] = []
        real_threshold = cv2.threshold

        def _capture_threshold(
            src: np.ndarray,
            thresh: float,
            maxval: float,
            threshold_type: int,
        ) -> tuple[float, np.ndarray]:
            threshold_values.append(thresh)
            return real_threshold(src, thresh, maxval, threshold_type)

        def _capture_attempt(
            _results: list[Any],
            _confidence_threshold: float,
            attempt_name: str,
        ) -> tuple[str | None, float | None]:
            attempt_names.append(attempt_name)
            if attempt_name == "dynamic_inverted":
                return "K", 0.88
            return None, None

        monkeypatch.setattr(
            card_recognizer_module,
            "get_reader",
            lambda _languages: EmptyOcrReader(),
        )
        monkeypatch.setattr(
            card_recognizer_module,
            "_best_rank_from_ocr_results",
            _capture_attempt,
        )
        monkeypatch.setattr(card_recognizer_module.cv2, "threshold", _capture_threshold)
        bright_card = np.full((80, 60, 3), 220, dtype=np.uint8)
        cv2.putText(
            bright_card,
            "K",
            (4, 28),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (30, 30, 30),
            2,
            cv2.LINE_AA,
        )

        rank, confidence = card_recognizer_module._detect_rank_with_confidence(
            bright_card,
            93,
            ["en"],
            region_key="board_card_3",
        )

        assert rank == "K"
        assert confidence == 0.88
        assert attempt_names == [
            "otsu",
            "inverted",
            "adaptive",
            "fixed_inverted",
            "fixed_normal",
            "dynamic_inverted",
        ]
        rank_region_gray = cv2.cvtColor(bright_card[:40, :40], cv2.COLOR_BGR2GRAY)
        expected_dynamic_threshold = max(int(np.mean(rank_region_gray) * 0.4), 50)
        assert expected_dynamic_threshold in threshold_values

    def test_rank_ocr_failure_warning_is_suppressed_by_card_key(
        self,
        config: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Repeated rank OCR failures for the same card key log one warning."""
        recognizer = CardRecognizer({}, config)
        monkeypatch.setattr(
            card_recognizer_module,
            "get_reader",
            lambda _languages: EmptyOcrReader(),
        )
        card_img = np.full((80, 60, 3), 220, dtype=np.uint8)

        with caplog.at_level("WARNING", logger="recognition.card_recognizer"):
            recognizer._recognize_rank(card_img, 60, "hero_card_2")
            recognizer._recognize_rank(card_img, 60, "hero_card_2")
            recognizer._recognize_rank(card_img, 60, "board_card_1")

        messages = [
            record.getMessage()
            for record in caplog.records
            if "Rank OCR all attempts failed" in record.getMessage()
        ]
        assert len(messages) == 2
        assert "hero_card_2" in messages[0]
        assert "board_card_1" in messages[1]

    def test_hero_recognition_preserves_rank_failure_flags(
        self,
        config: dict[str, Any],
    ) -> None:
        """Hero recognition does not reset any rank-failure log flags."""
        recognizer = CardRecognizer({}, config)
        recognizer._rank_fail_logged = {
            "hero_card_1": True,
            "hero_card_2": True,
            "board_card_1": True,
        }
        img = np.zeros((10, 10, 3), dtype=np.uint8)

        assert recognizer.recognize_hero_cards(img) == [None, None]

        assert recognizer._rank_fail_logged["hero_card_1"] is True
        assert recognizer._rank_fail_logged["hero_card_2"] is True
        assert recognizer._rank_fail_logged["board_card_1"] is True

    def test_rank_ocr_success_resets_failure_key(
        self,
        config: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Successful rank OCR clears the previous failure suppression key."""
        recognizer = CardRecognizer({}, config)

        class AceReader:
            """Fake EasyOCR Reader that recognizes an ace."""

            def readtext(self, *_args: Any, **_kwargs: Any) -> list[Any]:
                """Return one high-confidence ace candidate."""
                return [([[0, 0], [1, 0], [1, 1], [0, 1]], "A", 0.95)]

        monkeypatch.setattr(
            card_recognizer_module,
            "get_reader",
            lambda _languages: AceReader(),
        )
        recognizer._rank_fail_logged["hero_card_1"] = True
        card_img = np.full((80, 60, 3), 220, dtype=np.uint8)

        rank, confidence = recognizer._recognize_rank(card_img, 60, "hero_card_1")

        assert rank == "A"
        assert confidence == 0.95
        assert recognizer._rank_fail_logged["hero_card_1"] is False


class TestBoardCardCount:
    """Board card count accuracy."""

    def test_preflop_zero_board(
        self,
        card_recognizer: CardRecognizer,
        load_image: ImageLoader,
        screenshots: dict[str, Any],
    ) -> None:
        """cp_01: preflop has zero board cards."""
        img = load_image(screenshots["cp_01"]["filename"])

        assert card_recognizer.count_board_cards(img) == 0

    def test_flop_three_board(
        self,
        card_recognizer: CardRecognizer,
        load_image: ImageLoader,
        screenshots: dict[str, Any],
    ) -> None:
        """cp_03: flop has three board cards."""
        img = load_image(screenshots["cp_03"]["filename"])

        assert card_recognizer.count_board_cards(img) == 3

    def test_turn_four_board(
        self,
        card_recognizer: CardRecognizer,
        load_image: ImageLoader,
        screenshots: dict[str, Any],
    ) -> None:
        """cp_05: turn has four board cards."""
        img = load_image(screenshots["cp_05"]["filename"])

        assert card_recognizer.count_board_cards(img) == 4

    def test_river_five_board(
        self,
        card_recognizer: CardRecognizer,
        load_image: ImageLoader,
        screenshots: dict[str, Any],
    ) -> None:
        """cp_06: river has five board cards."""
        img = load_image(screenshots["cp_06"]["filename"])

        assert card_recognizer.count_board_cards(img) == 5

    def test_showdown_five_board(
        self,
        card_recognizer: CardRecognizer,
        load_image: ImageLoader,
        screenshots: dict[str, Any],
    ) -> None:
        """cp_07: showdown has five board cards."""
        img = load_image(screenshots["cp_07"]["filename"])

        assert card_recognizer.count_board_cards(img) == 5

    def test_waiting_zero_board(
        self,
        card_recognizer: CardRecognizer,
        load_image: ImageLoader,
        screenshots: dict[str, Any],
    ) -> None:
        """cp_13: waiting between hands has zero board cards."""
        img = load_image(screenshots["cp_13"]["filename"])

        assert card_recognizer.count_board_cards(img) == 0
