"""Level 1 regression tests and Level 3 E2E latency tests.

Phase 6 acceptance checks:
- Level 1: card recognition 100%, number recognition 95%+, button detection 100%.
- Level 3: E2E latency P95 <= 7 seconds.
"""

import os
import statistics
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest


PRIMARY_KEYS = [
    "cp_01",
    "cp_02",
    "cp_03",
    "cp_04",
    "cp_05",
    "cp_06",
    "cp_07",
    "cp_07b",
    "cp_08",
    "cp_09",
    "cp_10",
    "cp_11",
    "cp_12",
    "cp_13",
]
CARD_HERO_KEYS = ["cp_01", "cp_03", "cp_04", "cp_05", "cp_06"]
CARD_BOARD_KEYS = ["cp_01", "cp_03", "cp_04", "cp_05", "cp_06", "cp_07", "cp_07b"]


def _load_frame(screenshots_dir: Path, filename: str) -> np.ndarray:
    """Load a test screenshot as a BGR frame."""
    frame = cv2.imread(str(screenshots_dir / filename), cv2.IMREAD_COLOR)
    assert frame is not None, f"Image not found: {filename}"
    return frame


def _primary_frames(
    ground_truth: dict[str, Any],
    screenshots_dir: Path,
) -> list[tuple[str, dict[str, Any], np.ndarray]]:
    """Return loaded primary screenshots with ground-truth metadata."""
    screenshots = ground_truth["screenshots"]
    frames: list[tuple[str, dict[str, Any], np.ndarray]] = []
    for key in PRIMARY_KEYS:
        expected = screenshots[key]
        frames.append((key, expected, _load_frame(screenshots_dir, expected["filename"])))
    return frames


def _percentile(values: list[float], percentile: float) -> float:
    """Return a nearest-rank percentile value for non-empty samples."""
    if not values:
        raise ValueError("percentile requires at least one value")
    sorted_values = sorted(values)
    index = int(round((len(sorted_values) - 1) * percentile))
    return sorted_values[min(max(index, 0), len(sorted_values) - 1)]


def _print_latency_stats(title: str, latencies: list[float]) -> tuple[float, float, float]:
    """Print latency statistics and return p50, p95, p99."""
    p50 = statistics.median(latencies)
    p95 = _percentile(latencies, 0.95)
    p99 = _percentile(latencies, 0.99)
    mean = statistics.mean(latencies)

    print(f"\n=== {title} ===")
    print(f"  Samples: {len(latencies)}")
    print(f"  Mean:    {mean * 1000:.1f}ms")
    print(f"  P50:     {p50 * 1000:.1f}ms")
    print(f"  P95:     {p95 * 1000:.1f}ms")
    print(f"  P99:     {p99 * 1000:.1f}ms")
    print(f"  Min:     {min(latencies) * 1000:.1f}ms")
    print(f"  Max:     {max(latencies) * 1000:.1f}ms")
    return p50, p95, p99


class TestLevel1Regression:
    """Level 1: static-image regression checks for recognition accuracy."""

    def test_card_recognition_accuracy(
        self,
        config: dict[str, Any],
        profile: dict[str, Any],
        ground_truth: dict[str, Any],
        screenshots_dir: Path,
    ) -> None:
        """Verify card recognition accuracy is 100% on measured fields."""
        from recognition.card_recognizer import CardRecognizer

        recognizer = CardRecognizer(profile, config)
        total = 0
        correct = 0

        frames_by_key = {
            key: (expected, frame)
            for key, expected, frame in _primary_frames(ground_truth, screenshots_dir)
        }

        for key in CARD_HERO_KEYS:
            expected, frame = frames_by_key[key]
            actual_hero = recognizer.recognize_hero_cards(frame)
            for index, expected_card in enumerate(expected["hero_cards"]):
                total += 1
                if index < len(actual_hero) and actual_hero[index] == expected_card:
                    correct += 1

        for key in CARD_BOARD_KEYS:
            expected, frame = frames_by_key[key]
            expected_board = expected.get("board_cards")
            if expected_board:
                actual_board = recognizer.recognize_board_cards(frame)
                for index, expected_card in enumerate(expected_board):
                    total += 1
                    if index < len(actual_board) and actual_board[index] == expected_card:
                        correct += 1
            else:
                total += 1
                if recognizer.count_board_cards(frame) == 0:
                    correct += 1

        accuracy = correct / total if total > 0 else 0.0
        assert accuracy >= 1.0, (
            f"Card recognition accuracy {accuracy:.1%} ({correct}/{total}) "
            "is below 100% threshold"
        )

    def test_number_recognition_accuracy(
        self,
        config: dict[str, Any],
        profile: dict[str, Any],
        ground_truth: dict[str, Any],
        screenshots_dir: Path,
    ) -> None:
        """Verify number recognition accuracy is at least 95%."""
        from recognition.number_recognizer import NumberRecognizer

        recognizer = NumberRecognizer(profile, config)
        total = 0
        correct = 0

        for _key, expected, frame in _primary_frames(ground_truth, screenshots_dir):
            actual = recognizer.recognize_all(frame)

            for field_name in ("pot", "hero_stack", "hero_bet"):
                if field_name in expected:
                    total += 1
                    if actual[field_name] == expected[field_name]:
                        correct += 1

            for seat, values in expected.get("players", {}).items():
                if "stack" in values:
                    total += 1
                    if actual["player_stacks"].get(seat) == values["stack"]:
                        correct += 1
                if "bet" in values:
                    total += 1
                    if actual["player_bets"].get(seat) == values["bet"]:
                        correct += 1

        accuracy = correct / total if total > 0 else 0.0
        assert accuracy >= 0.95, (
            f"Number recognition accuracy {accuracy:.1%} ({correct}/{total}) "
            "is below 95% threshold"
        )

    def test_button_detection_accuracy(
        self,
        config: dict[str, Any],
        profile: dict[str, Any],
        ground_truth: dict[str, Any],
        screenshots_dir: Path,
    ) -> None:
        """Verify hero-turn and button classification accuracy is 100%."""
        from recognition.button_recognizer import ButtonRecognizer

        recognizer = ButtonRecognizer(profile, config)
        total = 0
        correct = 0

        for _key, expected, frame in _primary_frames(ground_truth, screenshots_dir):
            actual_turn = recognizer.detect_my_turn(frame)
            total += 1
            if actual_turn == expected["is_my_turn"]:
                correct += 1

            expected_buttons = expected.get("buttons")
            if expected_buttons is not None:
                actual_buttons = recognizer.classify_buttons(frame)
                total += 1
                if actual_buttons == expected_buttons:
                    correct += 1

        accuracy = correct / total if total > 0 else 0.0
        assert accuracy >= 1.0, (
            f"Button detection accuracy {accuracy:.1%} ({correct}/{total}) "
            "is below 100% threshold"
        )


class TestLevel3Latency:
    """Level 3: E2E latency measurement with mocked strategy delay."""

    def test_recognition_pipeline_latency(
        self,
        config: dict[str, Any],
        profile: dict[str, Any],
        ground_truth: dict[str, Any],
        screenshots_dir: Path,
    ) -> None:
        """Measure capture-equivalent recognition pipeline latency."""
        from recognition.button_recognizer import ButtonRecognizer
        from recognition.card_recognizer import CardRecognizer
        from recognition.dealer_recognizer import DealerRecognizer
        from recognition.number_recognizer import NumberRecognizer

        card_recognizer = CardRecognizer(profile, config)
        number_recognizer = NumberRecognizer(profile, config)
        button_recognizer = ButtonRecognizer(profile, config)
        dealer_recognizer = DealerRecognizer(profile, config)
        frames = _primary_frames(ground_truth, screenshots_dir)
        latencies: list[float] = []

        first_frame = frames[0][2]
        card_recognizer.recognize_hero_cards(first_frame)
        card_recognizer.recognize_board_cards(first_frame)
        number_recognizer.recognize_all(first_frame)
        button_recognizer.detect_my_turn(first_frame)
        dealer_recognizer.detect_dealer_seat(first_frame)

        for _key, _expected, frame in frames:
            start = time.perf_counter()

            card_recognizer.recognize_hero_cards(frame)
            card_recognizer.recognize_board_cards(frame)
            card_recognizer.count_board_cards(frame)
            number_recognizer.recognize_all(frame)
            button_recognizer.detect_my_turn(frame)
            button_recognizer.classify_buttons(frame)
            dealer_recognizer.detect_dealer_seat(frame)

            latencies.append(time.perf_counter() - start)

        _p50, p95, _p99 = _print_latency_stats(
            "Recognition Pipeline Latency",
            latencies,
        )
        assert p95 <= 0.5, (
            f"Recognition pipeline P95 latency {p95 * 1000:.1f}ms exceeds 500ms"
        )

    def test_e2e_pipeline_latency_with_mock_strategy(
        self,
        config: dict[str, Any],
        profile: dict[str, Any],
        ground_truth: dict[str, Any],
        screenshots_dir: Path,
    ) -> None:
        """Measure recognition plus mocked strategy decision latency."""
        from core.game_state import create_empty_game_state
        from recognition.button_recognizer import ButtonRecognizer
        from recognition.card_recognizer import CardRecognizer
        from recognition.dealer_recognizer import DealerRecognizer
        from recognition.number_recognizer import NumberRecognizer

        card_recognizer = CardRecognizer(profile, config)
        number_recognizer = NumberRecognizer(profile, config)
        button_recognizer = ButtonRecognizer(profile, config)
        dealer_recognizer = DealerRecognizer(profile, config)
        frames = _primary_frames(ground_truth, screenshots_dir)
        latencies: list[float] = []

        first_frame = frames[0][2]
        card_recognizer.recognize_hero_cards(first_frame)
        card_recognizer.recognize_board_cards(first_frame)
        number_recognizer.recognize_all(first_frame)
        button_recognizer.detect_my_turn(first_frame)
        dealer_recognizer.detect_dealer_seat(first_frame)

        for _key, _expected, frame in frames:
            start = time.perf_counter()

            hero_cards = card_recognizer.recognize_hero_cards(frame)
            board_cards = card_recognizer.recognize_board_cards(frame)
            board_count = card_recognizer.count_board_cards(frame)
            numbers = number_recognizer.recognize_all(frame)
            is_my_turn = button_recognizer.detect_my_turn(frame)
            if is_my_turn:
                button_recognizer.classify_buttons(frame)
            dealer_seat = dealer_recognizer.detect_dealer_seat(frame)

            game_state = create_empty_game_state()
            game_state.hero.cards = hero_cards
            game_state.hero.is_my_turn = is_my_turn
            game_state.board = board_cards
            game_state.board_card_count = board_count
            game_state.pot = numbers["pot"] or 0
            game_state.dealer_seat = dealer_seat

            if is_my_turn and board_count >= 3:
                time.sleep(1.0)
                if board_count == 3:
                    time.sleep(3.5)
                else:
                    time.sleep(0.03)

            latencies.append(time.perf_counter() - start)

        _p50, p95, _p99 = _print_latency_stats(
            "E2E Pipeline Latency (Mock Strategy)",
            latencies,
        )
        assert p95 <= 7.0, (
            f"E2E pipeline P95 latency {p95 * 1000:.1f}ms exceeds 7000ms"
        )

    def test_diff_detection_skip_rate(
        self,
        config: dict[str, Any],
        profile: dict[str, Any],
        ground_truth: dict[str, Any],
        screenshots_dir: Path,
    ) -> None:
        """Verify repeated identical crops produce at least 60% skip rate."""
        from recognition.diff_detector import DiffDetector

        diff_detector = DiffDetector(config)
        first_key = PRIMARY_KEYS[0]
        filename = ground_truth["screenshots"][first_key]["filename"]
        frame = _load_frame(screenshots_dir, filename)

        pot_region = profile.get("pot_display")
        if pot_region is None:
            pytest.skip("pot_display not in profile")

        x = pot_region["x"]
        y = pot_region["y"]
        width = pot_region["w"]
        height = pot_region["h"]
        crop = frame[y : y + height, x : x + width]

        total_checks = 10
        skipped = 0
        for _ in range(total_checks):
            changed = diff_detector.has_changed("pot_display", crop)
            if not changed:
                skipped += 1

        skip_rate = skipped / total_checks
        print("\n=== Diff Detection Skip Rate ===")
        print(f"  Total checks: {total_checks}")
        print(f"  Skipped: {skipped}")
        print(f"  Skip rate: {skip_rate:.0%}")

        assert skip_rate >= 0.6, (
            f"Diff detection skip rate {skip_rate:.0%} is below 60%"
        )
