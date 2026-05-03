"""Integration tests for 167 CoinPoker auto-capture screenshots."""

import json
import shutil
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest

from core.game_loop import GameLoop
from core.game_state import GameState
from core.hand_manager import HandManager

SCREENSHOT_DIR = Path("tests/fixtures/screenshots/coinpoker")
AUTO_FILE_COUNT = 167
AUTO_FILES = sorted(SCREENSHOT_DIR.glob("auto_*.png"))
HAS_AUTO_FILES = len(AUTO_FILES) >= AUTO_FILE_COUNT


class FakeCapture:
    """Capture source that returns a fixed list of auto screenshots."""

    def __init__(self, file_paths: list[Path]) -> None:
        self._files = file_paths
        self._index = 0

    def get_frame(self) -> np.ndarray | None:
        """Return the next screenshot as a BGR image."""
        if self._index >= len(self._files):
            return None
        img = cv2.imread(str(self._files[self._index]), cv2.IMREAD_COLOR)
        self._index += 1
        return img

    def is_open(self) -> bool:
        """Return whether unread screenshots remain."""
        return self._index < len(self._files)

    def release(self) -> None:
        """Release no resources."""
        return None


def load_config() -> dict[str, Any]:
    """Return integration-test config."""
    return {
        "capture": {"method": "file", "polling_interval_sec": 0.0},
        "game": {"blind_bb": 100, "blind_sb": 50, "table_size": 6},
        "ocr": {"languages": ["en"], "confidence_threshold": 0.4},
        "recognition": {
            "diff_threshold_card": 500,
            "diff_threshold_number": 300,
            "diff_threshold_button": 200,
            "fold_confirm_frames": 3,
            "pot_spike_ratio": 2.0,
            "pot_spike_confirm_frames": 2,
        },
        "action_estimation": {
            "new_hand_pot_ratio": 0.3,
            "new_hand_min_pot_bb": 2,
            "raise_threshold": 1.1,
            "empty_region_std": 8,
        },
        "db": {"path": ":memory:"},
        "replay": {"base_dir": "tests/tmp_replays", "retention_days": 30},
    }


def load_profile() -> dict[str, Any]:
    """Load the CoinPoker coordinate profile."""
    with open("profiles/coinpoker_6max.json", "r", encoding="utf-8") as f:
        return json.load(f)


def _run_all_frames() -> tuple[GameLoop, HandManager, list[GameState]]:
    """Run all auto screenshots through GameLoop and HandManager."""
    config = load_config()
    profile = load_profile()
    capture = FakeCapture(AUTO_FILES[:AUTO_FILE_COUNT])
    hand_manager = HandManager(config, db_path=":memory:")
    game_loop = GameLoop(
        capture=capture,
        config=config,
        profile=profile,
        hand_manager=hand_manager,
        enable_strategy=False,
    )

    states: list[GameState] = []
    try:
        for _index in range(AUTO_FILE_COUNT):
            state = game_loop.process_one_frame()
            if state is None:
                break
            hand_manager.process_frame(state)
            states.append(state)
    except Exception:
        hand_manager.close()
        raise

    return game_loop, hand_manager, states


@pytest.fixture(scope="module")
def integration_result() -> tuple[GameLoop, HandManager, list[GameState]]:
    """Run the 167-frame integration flow once for this module."""
    shutil.rmtree("tests/tmp_replays", ignore_errors=True)
    result = _run_all_frames()
    yield result
    result[1].close()
    shutil.rmtree("tests/tmp_replays", ignore_errors=True)


@pytest.mark.skipif(
    not HAS_AUTO_FILES,
    reason=f"Need {AUTO_FILE_COUNT} auto_*.png files in {SCREENSHOT_DIR}",
)
class TestGameLoopIntegration:
    """Integration checks over the 167 auto-capture screenshot sequence."""

    def test_all_frames_no_crash(
        self,
        integration_result: tuple[GameLoop, HandManager, list[GameState]],
    ) -> None:
        """At least 100 frames are processed into GameState objects."""
        _game_loop, _hand_manager, states = integration_result
        assert len(states) >= 100

    def test_no_exceptions(self) -> None:
        """Processing all frames raises no uncaught exceptions."""
        game_loop, hand_manager, states = _run_all_frames()
        try:
            assert len(states) >= 100
        finally:
            hand_manager.close()
            game_loop.reset()

    def test_pot_recognized(
        self,
        integration_result: tuple[GameLoop, HandManager, list[GameState]],
    ) -> None:
        """At least one frame has a positive pot value."""
        _game_loop, _hand_manager, states = integration_result
        pots = [state.pot for state in states if state.pot > 0]
        assert len(pots) > 0

    def test_dealer_seat_recognized(
        self,
        integration_result: tuple[GameLoop, HandManager, list[GameState]],
    ) -> None:
        """At least one frame has a recognized dealer seat."""
        _game_loop, _hand_manager, states = integration_result
        dealers = [state.dealer_seat for state in states if state.dealer_seat is not None]
        assert len(dealers) > 0

    def test_hero_cards_detected(
        self,
        integration_result: tuple[GameLoop, HandManager, list[GameState]],
    ) -> None:
        """At least one frame has hero-card recognition output."""
        _game_loop, _hand_manager, states = integration_result
        hero_frames = [
            state
            for state in states
            if (
                state.hero.cards is not None
                and len(state.hero.cards) == 2
                and any(card is not None for card in state.hero.cards)
            )
        ]
        assert len(hero_frames) > 0

    def test_phase_transitions(
        self,
        integration_result: tuple[GameLoop, HandManager, list[GameState]],
    ) -> None:
        """Phase transitions require two fully recognized hero cards."""
        _game_loop, hand_manager, states = integration_result
        phases = {state.phase for state in states}
        phases.add(hand_manager.phase)
        complete_hero_frames = [
            state
            for state in states
            if (
                state.hero.cards is not None
                and len(state.hero.cards) == 2
                and all(card is not None for card in state.hero.cards)
            )
        ]

        if complete_hero_frames:
            assert len(phases - {"waiting"}) > 0
        else:
            assert phases == {"waiting"}

    def test_actions_detected(
        self,
        integration_result: tuple[GameLoop, HandManager, list[GameState]],
    ) -> None:
        """At least one action is detected or accumulated by HandManager."""
        _game_loop, hand_manager, states = integration_result
        total_frame_actions = sum(len(state.actions_since_last_frame) for state in states)
        total_hand_actions = len(hand_manager.get_all_actions())
        assert total_frame_actions + total_hand_actions > 0

    def test_board_cards_detected(
        self,
        integration_result: tuple[GameLoop, HandManager, list[GameState]],
    ) -> None:
        """At least one frame has visible board cards."""
        _game_loop, _hand_manager, states = integration_result
        board_frames = [state for state in states if state.board_card_count > 0]
        assert len(board_frames) > 0

    def test_summary_report(
        self,
        integration_result: tuple[GameLoop, HandManager, list[GameState]],
    ) -> None:
        """Print a recognition summary for manual inspection."""
        _game_loop, hand_manager, states = integration_result
        pot_count = sum(1 for state in states if state.pot > 0)
        dealer_count = sum(1 for state in states if state.dealer_seat is not None)
        hero_card_count = sum(
            1
            for state in states
            if (
                state.hero.cards is not None
                and len(state.hero.cards) == 2
                and any(card is not None for card in state.hero.cards)
            )
        )
        board_count = sum(1 for state in states if state.board_card_count > 0)
        my_turn_count = sum(1 for state in states if state.hero.is_my_turn)
        action_count = sum(len(state.actions_since_last_frame) for state in states)
        phases = {state.phase for state in states}
        phases.add(hand_manager.phase)
        events = [state.game_event for state in states if state.game_event is not None]

        report = (
            "\n=== 167-Frame Integration Test Summary ===\n"
            f"Total frames processed: {len(states)}\n"
            f"Final phase: {hand_manager.phase}\n"
            f"Final hand_id: {hand_manager.hand_id}\n\n"
            "Recognition counts:\n"
            f"  Pot recognized: {pot_count}/{len(states)} frames\n"
            f"  Dealer seat recognized: {dealer_count}/{len(states)} frames\n"
            f"  Hero cards recognized: {hero_card_count}/{len(states)} frames\n"
            f"  Board cards present: {board_count}/{len(states)} frames\n"
            f"  My turn (is_my_turn=True): {my_turn_count}/{len(states)} frames\n"
            f"  Total actions detected: {action_count}\n\n"
            f"Phases seen: {phases}\n"
            f"Game events: {events}\n"
            "=======================================\n"
        )
        print(report)
        assert len(states) > 0

    def test_frame_by_frame_detail(
        self,
        integration_result: tuple[GameLoop, HandManager, list[GameState]],
    ) -> None:
        """Print all 167 frame states for diagnosis."""
        _game_loop, _hand_manager, states = integration_result
        print("\n=== Frame-by-Frame Detail ===")
        for index, state in enumerate(states, start=1):
            actions = ""
            if state.actions_since_last_frame:
                actions = ", ".join(
                    f"{action.seat}:{action.action}({action.amount})"
                    for action in state.actions_since_last_frame
                )

            print(
                f"F{index:03d} | phase={state.phase:10s} | "
                f"cards={state.hero.cards} | "
                f"board={state.board_card_count} | "
                f"pot={state.pot} | "
                f"dealer={state.dealer_seat} | "
                f"turn={state.hero.is_my_turn} | "
                f"event={state.game_event} | "
                f"actions=[{actions}] | "
                f"stack={state.hero.stack}"
            )
        print("=== End Detail ===")
        assert len(states) > 0
