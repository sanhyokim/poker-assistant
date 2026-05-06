"""Tests for the synchronous GameLoop skeleton."""

import logging
import shutil
import time
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest

from capture.file_capture import FileCapture
from core.game_loop import GameLoop
from core.game_state import ActionRecord, PlayerState, create_empty_game_state
from core.hand_manager import HandManager
from strategy.recommendation_engine import Recommendation


@pytest.fixture
def workspace_tmp() -> Path:
    """Return a workspace-local temporary directory."""
    path = Path(".test_tmp") / f"game_loop_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


class FakeCardRecognizer:
    """Card recognizer test double."""

    def __init__(self, _profile: dict[str, Any], _config: dict[str, Any]) -> None:
        pass

    def recognize_hero_cards(
        self,
        _frame: Any,
        log_info: bool = False,
    ) -> list[str]:
        """Return fixed hero cards."""
        _ = log_info
        return ["Ah", "Kd"]

    def recognize_board_cards(self, _frame: Any) -> list[str]:
        """Return no board cards."""
        return []

    def count_board_cards(self, _frame: Any) -> int:
        """Return fixed board card count."""
        return 0


class FakeNumberRecognizer:
    """Number recognizer test double."""

    def __init__(self, _profile: dict[str, Any], _config: dict[str, Any]) -> None:
        pass

    def recognize_all(self, _frame: Any) -> dict[str, Any]:
        """Return fixed number recognition results."""
        return {
            "pot": 150,
            "hero_stack": 5000,
            "hero_bet": 0,
            "player_stacks": {"2": 4900, "3": 4800, "4": None, "5": None, "6": None},
            "player_bets": {"2": 50, "3": 100, "4": None, "5": None, "6": None},
        }


class FakeButtonRecognizer:
    """Button recognizer test double."""

    def __init__(self, _profile: dict[str, Any], _config: dict[str, Any]) -> None:
        pass

    def detect_my_turn(self, _frame: Any) -> bool:
        """Return fixed turn state."""
        return True

    def classify_buttons(self, _frame: Any) -> dict[str, Any]:
        """Return fixed button classification."""
        return {"fold": True, "call_or_check": "call", "raise_or_bet": "raise"}


class FakeDealerRecognizer:
    """Dealer recognizer test double."""

    def __init__(self, _profile: dict[str, Any], _config: dict[str, Any]) -> None:
        pass

    def detect_dealer_seat(self, _frame: Any) -> int:
        """Return fixed dealer seat."""
        return 1


class FakeNameRecognizer:
    """Name recognizer test double."""

    def __init__(self, _profile: dict[str, Any], _config: dict[str, Any]) -> None:
        pass

    def recognize_player_names(self, _frame: Any) -> dict[str, str | None]:
        """Return fixed player names."""
        return {"2": "Alice", "3": "Bob", "4": None, "5": None, "6": None}


class FakeActionEstimator:
    """Action estimator test double."""

    def __init__(self, _config: dict[str, Any]) -> None:
        self.reset_called = False

    def estimate(self, _previous: Any, _current: Any) -> dict[str, Any]:
        """Return one deterministic action on second frame onward."""
        return {
            "game_event": None,
            "actions": [ActionRecord(seat=2, action="CALL", amount=100)],
        }

    def reset(self) -> None:
        """Mark reset call."""
        self.reset_called = True


class NoneCapture:
    """Capture test double returning None."""

    def __init__(self) -> None:
        self.release_called = False
        self.reconnect_calls = 0

    def get_frame(self) -> None:
        """Return no frame."""
        return None

    def is_open(self) -> bool:
        """Return open state."""
        return True

    def release(self) -> None:
        """Release no resources."""
        self.release_called = True
        return None

    def reconnect(self) -> bool:
        """Record reconnect attempts and fail."""
        self.reconnect_calls += 1
        return False


def install_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install fake recognizers into core.game_loop."""
    monkeypatch.setattr("core.game_loop.CardRecognizer", FakeCardRecognizer)
    monkeypatch.setattr("core.game_loop.NumberRecognizer", FakeNumberRecognizer)
    monkeypatch.setattr("core.game_loop.ButtonRecognizer", FakeButtonRecognizer)
    monkeypatch.setattr("core.game_loop.DealerRecognizer", FakeDealerRecognizer)
    monkeypatch.setattr("core.game_loop.NameRecognizer", FakeNameRecognizer)
    monkeypatch.setattr("core.game_loop.ActionEstimator", FakeActionEstimator)


def make_loop(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
    capture: Any,
) -> GameLoop:
    """Create a GameLoop with fake recognizers."""
    install_fakes(monkeypatch)
    config = {
        "capture": {"polling_interval_sec": 0.5},
        "game": {"blind_sb": 50, "blind_bb": 100},
        "db": {"path": ":memory:"},
        "replay": {"base_dir": str(workspace_tmp / "replays")},
    }
    manager = HandManager(config, db_path=":memory:")
    return GameLoop(capture, config, {}, manager, enable_strategy=False)


def test_game_loop_instantiates(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GameLoop can be instantiated with file capture and dependencies."""
    image_path = Path("tests/fixtures/screenshots/coinpoker/cp_01_preflop_my_turnb.png")
    loop = make_loop(workspace_tmp, monkeypatch, FileCapture(image_path))

    assert loop._frame_number == 0
    assert loop._prev_state is None


def test_process_one_frame_returns_game_state(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """process_one_frame builds a GameState from one file-capture image."""
    image_path = Path("tests/fixtures/screenshots/coinpoker/cp_01_preflop_my_turnb.png")
    loop = make_loop(workspace_tmp, monkeypatch, FileCapture(image_path))

    game_state = loop.process_one_frame()

    assert game_state is not None
    assert game_state.hero.cards == ["Ah", "Kd"]
    assert game_state.hero.stack == 5000
    assert game_state.players["2"].name == "Alice"
    assert game_state.players["2"].stack == 4900
    assert game_state.board_card_count == 0
    assert game_state.pot == 150
    assert game_state.dealer_seat == 1


def test_populate_players_uses_hand_manager_participants(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Folded seats are not counted, while all-in seats remain in hand."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "flop"
    loop._hand_manager._players_in_hand = {"1": True, "2": True, "3": False}
    game_state = create_empty_game_state()
    number_results = {
        "player_stacks": {"2": 0, "3": 4800, "4": None, "5": None, "6": None},
        "player_bets": {"2": 5000, "3": 0, "4": None, "5": None, "6": None},
    }
    player_names = {"2": "Alice", "3": "Bob", "4": None, "5": None, "6": None}

    loop._populate_players(game_state, number_results, player_names)

    assert game_state.players["2"].in_current_hand is True
    assert game_state.players["3"].in_current_hand is False
    assert game_state.active_player_count == 2


def test_populate_players_waiting_counts_zero(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Waiting phase has no active hand participants."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "waiting"
    loop._hand_manager._players_in_hand = {}
    game_state = create_empty_game_state()
    number_results = {
        "player_stacks": {"2": 4900, "3": 4800, "4": None, "5": None, "6": None},
        "player_bets": {"2": 0, "3": 0, "4": None, "5": None, "6": None},
    }
    player_names = {"2": "Alice", "3": "Bob", "4": None, "5": None, "6": None}

    loop._populate_players(game_state, number_results, player_names)

    assert game_state.active_player_count == 0
    assert game_state.players["2"].is_seated is True
    assert game_state.players["2"].in_current_hand is False


def _state_with_player(seat: str, stack: int = 5000, bet: int = 0) -> Any:
    """Create a GameState with one configured opponent player."""
    game_state = create_empty_game_state()
    game_state.players[seat] = PlayerState(
        stack=stack,
        bet=bet,
        is_seated=True,
        in_current_hand=True,
    )
    return game_state


def test_seat_card_fold_detection_after_confirm_frames(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Seat card disappearance for N frames generates a FOLD action."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "flop"
    loop._hand_manager._players_in_hand = {"1": True, "3": True}
    loop._prev_state = _state_with_player("3")
    game_state = _state_with_player("3")

    for _ in range(loop._fold_confirm_frames - 1):
        loop._process_seat_card_detection(game_state, {3: False})

    assert game_state.actions_since_last_frame == []

    loop._process_seat_card_detection(game_state, {3: False})

    assert game_state.actions_since_last_frame[-1] == ActionRecord(
        seat=3,
        action="FOLD",
        amount=0,
        confidence="high",
    )
    loop._hand_manager._add_actions(game_state.actions_since_last_frame)
    assert 3 not in loop._hand_manager.get_players_in_hand()


def test_seat_card_reappearance_resets_streak(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Card reappearance after early misses resets the no-card streak."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "flop"
    loop._hand_manager._players_in_hand = {"1": True, "3": True}
    loop._prev_state = _state_with_player("3")
    game_state = _state_with_player("3")

    loop._process_seat_card_detection(game_state, {3: False})
    loop._process_seat_card_detection(game_state, {3: True})
    loop._process_seat_card_detection(game_state, {3: False})

    assert game_state.actions_since_last_frame == []
    assert loop._seat_card_no_card_streak[3] == 1


def test_seat_card_with_recent_action_skips_fold(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Seats with recent bet or stack changes are not marked as folded."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "flop"
    loop._hand_manager._players_in_hand = {"1": True, "3": True}
    loop._prev_state = _state_with_player("3", stack=5000, bet=0)
    game_state = _state_with_player("3", stack=4900, bet=100)

    for _ in range(loop._fold_confirm_frames):
        loop._process_seat_card_detection(game_state, {3: False})

    assert game_state.actions_since_last_frame == []
    assert 3 not in loop._seat_card_no_card_streak


def test_seat_card_detector_runs_during_active_phase(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SeatCardDetector is called from process_one_frame during active phases."""
    image_path = Path("tests/fixtures/screenshots/coinpoker/cp_01_preflop_my_turnb.png")
    loop = make_loop(workspace_tmp, monkeypatch, FileCapture(image_path))
    loop._hand_manager._phase = "preflop"
    loop._hand_manager._players_in_hand = {"1": True, "2": True, "3": True}
    detector = MagicMock()
    detector.detect_all.return_value = {2: True, 3: True, 4: True, 5: True, 6: True}
    loop._seat_card_detector = detector

    loop.process_one_frame()

    detector.detect_all.assert_called_once()


def test_hero_card_missing_with_state_change_no_hand_end(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hero card disappearance with stack change does not end the hand."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    manager = loop._hand_manager
    manager._phase = "flop"
    manager._seen_hero_cards_this_hand = True
    manager._hero_card_missing_count = 4
    manager._turn_start_state = create_empty_game_state()
    manager._turn_start_state.hero.cards = ["Ah", "Kd"]
    manager._turn_start_state.hero.stack = 5000
    manager._turn_start_state.hero.bet = 0
    game_state = create_empty_game_state()
    game_state.hero.cards = None
    game_state.hero.stack = 4900
    game_state.hero.bet = 100

    assert manager._check_hand_end_conditions(game_state) is False
    assert manager._hero_card_missing_count == 0


def _make_seated_state(dealer_seat: int) -> Any:
    """Create a GameState with all seats occupied for position tests."""
    game_state = create_empty_game_state()
    game_state.dealer_seat = dealer_seat
    for player in game_state.players.values():
        player.is_seated = True
    return game_state


def test_position_locked_during_hand(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dealer changes during a hand do not change locked hero position."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "preflop"
    loop._hand_manager._hand_just_started = True
    first_state = _make_seated_state(dealer_seat=3)

    loop._update_hand_position_lock(first_state)

    assert first_state.hero.position == "BB"
    assert loop._hand_dealer_seat == 3

    loop._hand_manager._hand_just_started = False
    next_state = _make_seated_state(dealer_seat=4)
    loop._populate_position(next_state)

    assert next_state.dealer_seat == 3
    assert next_state.hero.position == "BB"


def test_position_updated_on_new_hand(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A new hand recomputes positions from the latest dealer seat."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "preflop"
    loop._hand_manager._hand_just_started = True
    first_state = _make_seated_state(dealer_seat=3)
    loop._update_hand_position_lock(first_state)

    loop._hand_manager._phase = "waiting"
    loop._hand_manager._hand_just_started = False
    waiting_state = _make_seated_state(dealer_seat=4)
    loop._update_hand_position_lock(waiting_state)

    loop._hand_manager._phase = "preflop"
    loop._hand_manager._hand_just_started = True
    second_state = _make_seated_state(dealer_seat=4)
    loop._update_hand_position_lock(second_state)

    assert loop._hand_dealer_seat == 4
    assert second_state.hero.position == "UTG"


def test_position_cleared_on_hand_end(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hand-end phases clear the locked position cache."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "preflop"
    loop._hand_manager._hand_just_started = True
    active_state = _make_seated_state(dealer_seat=3)
    loop._update_hand_position_lock(active_state)

    loop._hand_manager._phase = "hand_end"
    loop._hand_manager._hand_just_started = False
    end_state = _make_seated_state(dealer_seat=3)
    loop._update_hand_position_lock(end_state)

    assert loop._hand_positions is None
    assert loop._hand_dealer_seat is None
    assert end_state.hero.position is None


def test_process_one_frame_none_capture_returns_none(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """process_one_frame returns None when capture returns None."""
    capture = NoneCapture()
    loop = make_loop(workspace_tmp, monkeypatch, capture)

    assert loop.process_one_frame() is None
    assert capture.reconnect_calls == 1


def test_reset_clears_internal_state(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """reset clears frame counter, previous state, and hero card cache."""
    image_path = Path("tests/fixtures/screenshots/coinpoker/cp_01_preflop_my_turnb.png")
    loop = make_loop(workspace_tmp, monkeypatch, FileCapture(image_path))
    assert loop.process_one_frame() is not None
    loop._cached_hero_cards = ["Ah", "Kd"]

    loop.reset()

    assert loop._frame_number == 0
    assert loop._prev_state is None
    assert loop._cached_hero_cards is None
    assert loop._cached_hand_id is None


def test_waiting_frame_does_not_use_stale_hero_card_cache(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Waiting frames force fresh hero-card recognition instead of stale cache."""
    image_path = Path("tests/fixtures/screenshots/coinpoker/cp_01_preflop_my_turnb.png")
    loop = make_loop(workspace_tmp, monkeypatch, FileCapture(image_path))
    loop._cached_hero_cards = ["Kc", "2d"]
    loop._partial_hero_cards = ["Kc", None]
    loop._cached_hand_id = 1
    loop._hand_manager._phase = "waiting"
    loop._hand_manager._hand_id = None

    game_state = loop.process_one_frame()

    assert game_state is not None
    assert game_state.hero.cards == ["Ah", "Kd"]
    assert loop._cached_hero_cards is None
    assert loop._partial_hero_cards is None


def test_waiting_after_hand_end_ignores_residual_cards_until_clear(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After hand_end->waiting, visible residual cards are ignored until clear."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._last_hand_manager_phase = "hand_end"
    loop._hand_manager._phase = "waiting"
    loop._card_recognizer.recognize_hero_cards = MagicMock(
        side_effect=[["Ah", "Kd"], [None, None], ["Qs", "7h"]],
    )
    frame = np.zeros((20, 20, 3), dtype=np.uint8)

    residual_state = loop._build_game_state(frame, time.time())
    cleared_state = loop._build_game_state(frame, time.time())
    new_cards_state = loop._build_game_state(frame, time.time())

    assert residual_state.hero.cards is None
    assert cleared_state.hero.cards is None
    assert new_cards_state.hero.cards == ["Qs", "7h"]


def test_initial_waiting_accepts_visible_cards_without_clear_wait(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Initial waiting state accepts cards immediately."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "waiting"
    loop._card_recognizer.recognize_hero_cards = MagicMock(return_value=["Ah", "Kd"])
    frame = np.zeros((20, 20, 3), dtype=np.uint8)

    game_state = loop._build_game_state(frame, time.time())

    assert game_state.hero.cards == ["Ah", "Kd"]
    assert loop._waiting_for_card_clear is False


def test_player_names_cached_during_hand(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Player-name OCR runs once per hand and cached names are reused."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "preflop"
    loop._hand_manager._hand_id = 7
    loop._name_recognizer.recognize_player_names = MagicMock(
        return_value={"2": "Alice", "3": "Bob", "4": None, "5": None, "6": None}
    )
    frame = np.zeros((20, 20, 3), dtype=np.uint8)

    first_state = loop._build_game_state(frame, time.time())
    second_state = loop._build_game_state(frame, time.time())

    assert loop._name_recognizer.recognize_player_names.call_count == 1
    assert first_state.players["2"].name == "Alice"
    assert second_state.players["2"].name == "Alice"


def test_player_names_recaptured_on_new_hand_id(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A new hand_id triggers a fresh player-name OCR capture."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "preflop"
    loop._name_recognizer.recognize_player_names = MagicMock(
        side_effect=[
            {"2": "Alice", "3": None, "4": None, "5": None, "6": None},
            {"2": "Carol", "3": None, "4": None, "5": None, "6": None},
        ]
    )
    frame = np.zeros((20, 20, 3), dtype=np.uint8)

    loop._hand_manager._hand_id = 7
    first_state = loop._build_game_state(frame, time.time())
    loop._hand_manager._hand_id = 8
    second_state = loop._build_game_state(frame, time.time())

    assert loop._name_recognizer.recognize_player_names.call_count == 2
    assert first_state.players["2"].name == "Alice"
    assert second_state.players["2"].name == "Carol"


def test_waiting_hero_card_failure_log_is_suppressed(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Repeated waiting hero-card failures emit INFO once per result pattern."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "waiting"
    loop._card_recognizer.recognize_hero_cards = MagicMock(
        side_effect=[[None, None], [None, None], ["Ah", None]],
    )
    frame = np.zeros((20, 20, 3), dtype=np.uint8)

    with caplog.at_level("INFO", logger="core.game_loop"):
        loop._build_game_state(frame, time.time())
        loop._build_game_state(frame, time.time())
        loop._build_game_state(frame, time.time())

    messages = [record.getMessage() for record in caplog.records]
    failure_messages = [
        message
        for message in messages
        if message.startswith("Waiting: hero card recognition failed")
    ]
    assert failure_messages == [
        "Waiting: hero card recognition failed - result=[None, None]",
        "Waiting: hero card recognition failed - result=['Ah', None]",
    ]


def test_stop_sets_running_false(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """stop sets _running to False."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._running = True

    loop.stop()

    assert loop._running is False


def test_stop_closes_hand_manager(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """stop closes the HandManager DB connection."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    close_mock = MagicMock()
    loop._hand_manager.close = close_mock  # type: ignore[method-assign]

    loop.stop()

    close_mock.assert_called_once()


def test_stop_releases_capture(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """stop releases the capture source."""
    capture = NoneCapture()
    loop = make_loop(workspace_tmp, monkeypatch, capture)

    loop.stop()

    assert capture.release_called is True


def test_process_one_frame_reconnect_success_resets_failure_count(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful reconnect resets capture failure count."""

    class ReconnectCapture(NoneCapture):
        """Capture that fails a frame but reconnects successfully."""

        def reconnect(self) -> bool:
            """Record reconnect attempts and succeed."""
            self.reconnect_calls += 1
            return True

    capture = ReconnectCapture()
    loop = make_loop(workspace_tmp, monkeypatch, capture)

    assert loop.process_one_frame() is None
    assert loop._consecutive_capture_failures == 0
    assert loop.capture_failed is False


def test_process_one_frame_stops_after_reconnect_limit(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Capture failure beyond reconnect limit marks failure and stops loop."""
    capture = NoneCapture()
    loop = make_loop(workspace_tmp, monkeypatch, capture)
    loop._running = True
    loop._config["capture"]["max_reconnect_attempts"] = 1

    assert loop.process_one_frame() is None
    assert loop.process_one_frame() is None

    assert loop.capture_failed is True
    assert loop._running is False
    assert capture.reconnect_calls == 1


def test_strategy_preflop_immediate_computation(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preflop hero turn computes recommendation synchronously."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._recommendation_engine = MagicMock()
    loop._recommendation_engine.generate.return_value = Recommendation(
        action="RAISE",
        amount=300,
        strategy_source="preflop_chart",
    )
    loop._hand_manager._phase = "preflop"
    state = create_empty_game_state()
    state.phase = "preflop"
    state.hero.is_my_turn = True

    loop._handle_strategy(state)

    assert loop.current_recommendation is not None
    assert loop.current_recommendation.action == "RAISE"


def test_game_state_phase_synced_after_hand_manager(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GameState phase and hand ID are synced after HandManager updates."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    state = create_empty_game_state()
    state.phase = "waiting"
    state.hero.cards = ["Ah", "Kd"]
    state.dealer_seat = 1
    state.players["2"].stack = 4900
    state.players["2"].bet = 50
    state.players["2"].is_seated = True
    state.players["2"].in_current_hand = True
    state.players["3"].stack = 4800
    state.players["3"].bet = 100
    state.players["3"].is_seated = True
    state.players["3"].in_current_hand = True

    loop._hand_manager.process_frame(state)
    assert state.phase == "waiting"

    loop._sync_game_state_with_hand_manager(state)

    assert state.phase == "preflop"
    assert state.hand_id == loop._hand_manager.hand_id
    assert state.active_player_count == 3


def test_active_player_count_synced_after_fold(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FOLD-updated HandManager participants are reflected in active count."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    state = create_empty_game_state()
    state.phase = "flop"
    state.active_player_count = 5
    loop._hand_manager._phase = "flop"
    loop._hand_manager._players_in_hand = {
        "1": True,
        "2": False,
        "3": False,
        "4": False,
        "5": True,
    }

    loop._sync_game_state_with_hand_manager(state)

    assert state.active_player_count == 2


def test_skip_recommendation_on_hand_start_frame(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preflop recommendation is skipped on the frame a hand starts."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._recommendation_engine = MagicMock()
    loop._recommendation_engine.generate.return_value = Recommendation(
        action="RAISE",
        amount=300,
        strategy_source="preflop_chart",
    )
    loop._hand_manager._phase = "preflop"
    loop._hand_manager._hand_just_started = True
    state = create_empty_game_state()
    state.phase = "preflop"
    state.hero.is_my_turn = True

    loop._handle_strategy(state)

    loop._recommendation_engine.generate.assert_not_called()
    assert loop.current_recommendation is None


def test_recommendation_generated_on_next_frame(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preflop recommendation resumes after the hand-start frame."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._recommendation_engine = MagicMock()
    loop._recommendation_engine.generate.return_value = Recommendation(
        action="RAISE",
        amount=300,
        strategy_source="preflop_chart",
    )
    loop._hand_manager._phase = "preflop"
    loop._hand_manager._hand_just_started = False
    state = create_empty_game_state()
    state.phase = "preflop"
    state.hero.is_my_turn = True

    loop._handle_strategy(state)

    loop._recommendation_engine.generate.assert_called_once()
    assert loop.current_recommendation is not None
    assert loop.current_recommendation.action == "RAISE"


def test_preflop_sync_notifies_hud_computing(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preflop synchronous recommendation shows computing before result."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    computing_callback = MagicMock()
    loop._hud_computing_callback = computing_callback
    loop._recommendation_engine = MagicMock()
    loop._recommendation_engine.generate.return_value = Recommendation(
        action="RAISE",
        amount=300,
        strategy_source="preflop_chart",
    )
    loop._hand_manager._phase = "preflop"
    loop._hand_manager._hand_just_started = False
    state = create_empty_game_state()
    state.phase = "preflop"
    state.hero.is_my_turn = True

    loop._handle_strategy(state)

    computing_callback.assert_called_once()


def test_generate_recommendation_passes_opponent_stats(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_generate_recommendation passes DB stats to the recommendation engine."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._recommendation_engine = MagicMock()
    loop._recommendation_engine.generate.return_value = Recommendation(
        action="FOLD",
        strategy_source="preflop_chart",
    )
    stats = {"2": {"player_name": "Alice", "vpip": 28.0}}
    loop._hand_manager.get_opponent_stats = MagicMock(return_value=stats)  # type: ignore[method-assign]
    state = create_empty_game_state()

    result = loop._generate_recommendation(state)

    assert result.action == "FOLD"
    loop._recommendation_engine.generate.assert_called_once()
    assert loop._recommendation_engine.generate.call_args.kwargs["opponent_stats"] == stats


def test_generate_recommendation_continues_on_stats_error(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stats lookup errors do not block recommendation generation."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._recommendation_engine = MagicMock()
    loop._recommendation_engine.generate.return_value = Recommendation(
        action="CHECK",
        strategy_source="fallback",
    )
    loop._hand_manager.get_opponent_stats = MagicMock(  # type: ignore[method-assign]
        side_effect=RuntimeError("db unavailable")
    )
    state = create_empty_game_state()

    result = loop._generate_recommendation(state)

    assert result.action == "CHECK"
    assert loop._recommendation_engine.generate.call_args.kwargs["opponent_stats"] == {}


def test_strategy_new_street_syncs_phase(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NEW_STREET syncs GameState phase from HandManager."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._recommendation_engine = MagicMock()
    loop._recommendation_engine.generate.return_value = Recommendation(
        action="BET",
        amount=120,
        strategy_source="solver",
    )
    loop._hand_manager._phase = "flop"
    state = create_empty_game_state()
    state.phase = "preflop"
    state.game_event = "NEW_STREET"
    state.hero.is_my_turn = False

    loop._handle_strategy(state)

    assert state.phase == "flop"
    loop._recommendation_engine.generate.assert_not_called()


def test_strategy_postflop_hero_turn_computes_synchronously(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hero turn on postflop computes recommendation synchronously."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._recommendation_engine = MagicMock()
    loop._recommendation_engine.generate.return_value = Recommendation(
        action="CHECK",
        strategy_source="fallback",
    )
    loop._hand_manager._phase = "flop"
    state = create_empty_game_state()
    state.phase = "flop"
    state.hero.is_my_turn = True

    loop._handle_strategy(state)

    assert loop.current_recommendation is not None
    assert loop.current_recommendation.action == "CHECK"
    loop._recommendation_engine.generate.assert_called_once()


def test_strategy_recommendation_cleared_on_turn_end(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recommendation clears when hero turn transitions from True to False."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._recommendation_engine = MagicMock()
    loop._recommendation_engine.generate.return_value = Recommendation(
        action="CHECK",
        strategy_source="fallback",
    )
    loop._hand_manager._phase = "flop"
    state_on = create_empty_game_state()
    state_on.phase = "flop"
    state_on.hero.is_my_turn = True

    loop._handle_strategy(state_on)
    assert loop.current_recommendation is not None

    state_off = create_empty_game_state()
    state_off.phase = "flop"
    state_off.hero.is_my_turn = False

    loop._handle_strategy(state_off)
    assert loop.current_recommendation is None


def test_strategy_continued_turn_reuses_recommendation(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Continued hero turn reuses cached recommendation without regenerating."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._recommendation_engine = MagicMock()
    loop._recommendation_engine.generate.return_value = Recommendation(
        action="BET",
        amount=120,
        strategy_source="solver",
    )
    loop._recommendation_engine.apply_action_constraints.return_value = Recommendation(
        action="BET",
        amount=120,
        strategy_source="solver",
    )
    loop._hand_manager._phase = "flop"
    state = create_empty_game_state()
    state.phase = "flop"
    state.hero.is_my_turn = True

    loop._handle_strategy(state)
    loop._handle_strategy(state)

    assert loop._recommendation_engine.generate.call_count == 1
    assert loop.current_recommendation is not None
    assert loop.current_recommendation.action == "BET"


def test_sync_postflop_preflop_result_uses_fallback(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Synchronous postflop calculation replaces preflop-chart results."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._recommendation_engine = MagicMock()
    loop._recommendation_engine.generate.return_value = Recommendation(
        action="RAISE",
        amount=300,
        strategy_source="preflop_chart",
    )
    loop._recommendation_engine._generate_fallback.return_value = Recommendation(
        action="CHECK",
        strategy_source="fallback",
    )
    loop._hand_manager._phase = "flop"
    state = create_empty_game_state()
    state.phase = "flop"
    state.hero.is_my_turn = True

    loop._handle_strategy(state)

    assert loop.current_recommendation is not None
    assert loop.current_recommendation.strategy_source == "fallback"
    loop._recommendation_engine._generate_fallback.assert_called_once_with(
        state,
        "Preflop result in postflop",
    )


def test_strategy_decision_point_logs_info_only_on_turn_start(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Strategy decision point is INFO once, then DEBUG while turn remains active."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._recommendation_engine = MagicMock()
    loop._recommendation_engine.generate.return_value = Recommendation(
        action="CHECK",
        strategy_source="fallback",
    )
    loop._hand_manager._phase = "flop"
    state = create_empty_game_state()
    state.phase = "flop"
    state.hero.is_my_turn = True

    with caplog.at_level(logging.INFO, logger="core.game_loop"):
        loop._handle_strategy(state)
        loop._handle_strategy(state)

    messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == "core.game_loop"
        and record.levelno == logging.INFO
        and "Strategy decision point" in record.getMessage()
    ]
    assert len(messages) == 1


def test_hero_card_cache_clears_on_hand_id_change(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hero card cache is cleared when HandManager hand_id changes."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._cached_hero_cards = ["Ah", "Kd"]
    loop._partial_hero_cards = ["Ah", None]
    loop._cached_hand_id = 1
    loop._hand_manager._hand_id = 2
    loop._hand_manager._phase = "preflop"
    state = create_empty_game_state()
    state.hero.cards = None

    loop._manage_hero_card_cache(state)

    assert loop._cached_hero_cards is None
    assert loop._partial_hero_cards is None
    assert loop._cached_hand_id == 2
    assert state.hero.cards is None


def test_hero_card_cache_does_not_carry_to_next_hand(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A new hand_id uses the current frame cards instead of old cached cards."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._cached_hero_cards = ["Ah", "Kd"]
    loop._cached_hand_id = 1
    loop._hand_manager._hand_id = 2
    loop._hand_manager._phase = "preflop"
    state = create_empty_game_state()
    state.hero.cards = ["Qs", "Qc"]

    loop._manage_hero_card_cache(state)

    assert loop._cached_hero_cards == ["Qs", "Qc"]
    assert loop._cached_hand_id == 2
    assert state.hero.cards == ["Qs", "Qc"]
