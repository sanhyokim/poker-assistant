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
from core.game_loop import GameLoop, _AsyncRecommendationResult
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


class StaticFrameCapture:
    """Capture test double returning one static frame."""

    def __init__(self) -> None:
        self.frame = np.zeros((10, 10, 3), dtype=np.uint8)

    def get_frame(self) -> np.ndarray:
        """Return a dummy frame."""
        return self.frame


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
        "recognition": {"hero_card_confirm_frames": 1},
        "db": {"path": ":memory:"},
        "replay": {"base_dir": str(workspace_tmp / "replays")},
    }
    manager = HandManager(config, db_path=":memory:")
    manager._players_in_hand = {"1": True, "2": True}
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


def test_process_game_state_after_frame_uses_canonical_order(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Post-frame processing runs fold recovery and position lock before strategy."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    state = create_empty_game_state()
    calls: list[str] = []

    monkeypatch.setattr(
        loop._hand_manager,
        "process_frame",
        lambda game_state: calls.append("process_frame"),
    )
    monkeypatch.setattr(
        loop,
        "_recover_pending_hero_fold_badge",
        lambda game_state: calls.append("recover_pending_hero_fold_badge"),
    )
    monkeypatch.setattr(
        loop,
        "_sync_game_state_with_hand_manager",
        lambda game_state: calls.append("sync_game_state_with_hand_manager"),
    )
    monkeypatch.setattr(
        loop,
        "_update_hand_position_lock",
        lambda game_state: calls.append("update_hand_position_lock"),
    )
    monkeypatch.setattr(
        loop,
        "_handle_strategy",
        lambda game_state: calls.append("handle_strategy"),
    )

    loop.process_game_state_after_frame(state)

    assert calls == [
        "process_frame",
        "recover_pending_hero_fold_badge",
        "sync_game_state_with_hand_manager",
        "update_hand_position_lock",
        "handle_strategy",
    ]


def test_process_game_state_after_frame_filters_invalid_actions_before_manager(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Seat 0 actions are removed before HandManager receives a GameState."""
    caplog.set_level(logging.INFO, logger="core.game_loop")
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    state = create_empty_game_state()
    invalid_action = ActionRecord(
        seat=0,
        action="CHECK",
        amount=0,
        confidence="low",
    )
    valid_action = ActionRecord(
        seat=2,
        action="CALL",
        amount=100,
        confidence="high",
    )
    state.actions_since_last_frame = [invalid_action, valid_action]
    received_actions: list[ActionRecord] = []

    def process_frame(game_state: Any) -> None:
        received_actions.extend(game_state.actions_since_last_frame)

    monkeypatch.setattr(loop._hand_manager, "process_frame", process_frame)
    monkeypatch.setattr(loop, "_recover_pending_hero_fold_badge", lambda _state: None)
    monkeypatch.setattr(loop, "_sync_game_state_with_hand_manager", lambda _state: None)
    monkeypatch.setattr(loop, "_update_hand_position_lock", lambda _state: None)
    monkeypatch.setattr(loop, "_handle_strategy", lambda _state: None)

    loop.process_game_state_after_frame(state)

    assert state.actions_since_last_frame == [valid_action]
    assert received_actions == [valid_action]
    assert "Ignored invalid action before hand manager: seat=0" in caplog.text


def test_start_uses_canonical_post_frame_processing(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GameLoop.start delegates post-frame processing to the shared method."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    state = create_empty_game_state()
    calls: list[str] = []

    def process_one_frame_once() -> Any:
        calls.append("process_one_frame")
        return state

    def process_after_frame(game_state: Any) -> None:
        calls.append("process_game_state_after_frame")
        assert game_state is state
        loop.stop()

    monkeypatch.setattr(loop, "process_one_frame", process_one_frame_once)
    monkeypatch.setattr(loop, "process_game_state_after_frame", process_after_frame)
    monkeypatch.setattr("core.game_loop.time.sleep", lambda _seconds: None)

    loop.start()

    assert calls == ["process_one_frame", "process_game_state_after_frame"]


def test_stop_abandons_active_hand_before_close(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """stop() abandons an active hand before closing HandManager."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    hand_manager = MagicMock()
    hand_manager.abandon_current_hand.return_value = True
    loop._hand_manager = hand_manager

    loop.stop()

    hand_manager.abandon_current_hand.assert_called_once_with("user_stop")
    hand_manager.close.assert_called_once()


def test_capture_lost_stop_uses_capture_lost_reason(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Capture failure beyond reconnect limit stops with capture_lost reason."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._consecutive_capture_failures = int(
        loop._config.get("capture", {}).get("max_reconnect_attempts", 3)
    )
    loop.stop = MagicMock()  # type: ignore[method-assign]

    loop._handle_capture_failure()

    loop.stop.assert_called_once_with(reason="capture_lost")
    assert loop.capture_failed is True


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


def test_new_hand_suppressed_when_hero_cards_visible(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Active-hand NEW_HAND events are suppressed while hero cards are visible."""
    image_path = Path("tests/fixtures/screenshots/coinpoker/cp_01_preflop_my_turnb.png")
    loop = make_loop(workspace_tmp, monkeypatch, FileCapture(image_path))
    previous = create_empty_game_state()
    previous.pot = 1000
    loop._prev_state = previous
    loop._hand_manager._phase = "flop"
    loop._cached_hero_cards = ["Ah", "Kd"]
    loop._action_estimator = MagicMock()
    loop._action_estimator.estimate.return_value = {
        "game_event": "NEW_HAND",
        "actions": [],
        "filtered_pot": None,
    }
    loop._card_recognizer = MagicMock()
    loop._card_recognizer.recognize_board_cards.return_value = []
    loop._card_recognizer.count_board_cards.return_value = 0
    loop._card_recognizer.recognize_hero_cards.return_value = ["Ah", "Kd"]
    loop._fold_badge_detector = MagicMock()
    loop._fold_badge_detector.detect_all.return_value = {
        2: False,
        3: False,
        4: False,
        5: False,
        6: False,
    }

    with caplog.at_level(logging.INFO, logger="core.game_loop"):
        game_state = loop.process_one_frame()

    assert game_state is not None
    assert game_state.game_event is None
    assert "NEW_HAND suppressed: hero cards still visible" in caplog.text


def test_new_hand_allowed_when_hero_cards_missing(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Active-hand NEW_HAND events pass through when hero cards are missing."""
    image_path = Path("tests/fixtures/screenshots/coinpoker/cp_01_preflop_my_turnb.png")
    loop = make_loop(workspace_tmp, monkeypatch, FileCapture(image_path))
    previous = create_empty_game_state()
    previous.pot = 1000
    loop._prev_state = previous
    loop._hand_manager._phase = "flop"
    loop._cached_hero_cards = ["Ah", "Kd"]
    loop._action_estimator = MagicMock()
    loop._action_estimator.estimate.return_value = {
        "game_event": "NEW_HAND",
        "actions": [],
        "filtered_pot": None,
    }
    loop._card_recognizer = MagicMock()
    loop._card_recognizer.recognize_board_cards.return_value = []
    loop._card_recognizer.count_board_cards.return_value = 0
    loop._card_recognizer.recognize_hero_cards.return_value = [None, None]
    loop._fold_badge_detector = MagicMock()
    loop._fold_badge_detector.detect_all.return_value = {
        2: False,
        3: False,
        4: False,
        5: False,
        6: False,
    }

    game_state = loop.process_one_frame()

    assert game_state is not None
    assert game_state.game_event == "NEW_HAND"


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


def test_populate_players_clears_unseated_player_name(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unseated player names are cleared from GameState and cache."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._cached_player_names["3"] = "DepartedPlayer"
    game_state = create_empty_game_state()
    number_results = {
        "player_stacks": {"2": 4900, "3": None, "4": None, "5": None, "6": None},
        "player_bets": {"2": 0, "3": 0, "4": None, "5": None, "6": None},
    }
    player_names = {
        "2": "Alice",
        "3": "DepartedPlayer",
        "4": None,
        "5": None,
        "6": None,
    }

    loop._populate_players(game_state, number_results, player_names)

    assert game_state.players["3"].is_seated is False
    assert game_state.players["3"].name is None
    assert loop._cached_player_names["3"] is None


def test_stop_clears_cached_state(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """stop() clears cached live state for a clean UI/HUD stop."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._cached_player_names = {"2": "Alice"}
    loop._cached_hero_cards = ["Ah", "Kd"]
    loop._cached_hand_id = 12
    loop._previous_recommendation = Recommendation(action="BET", amount=100)
    loop._last_recommendation_log = "BET 100"
    loop._last_strategy_is_my_turn = True

    loop.stop()

    assert loop._cached_player_names == {}
    assert loop._cached_hero_cards is None
    assert loop._cached_hand_id is None
    assert loop._previous_recommendation is None
    assert loop._last_recommendation_log is None
    assert loop._last_strategy_is_my_turn is False


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


def test_fold_badge_detection_appends_fold_action(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fold badge detection generates a FOLD action."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "flop"
    loop._hand_manager._players_in_hand = {"1": True, "3": True}
    loop._prev_state = _state_with_player("3")
    game_state = _state_with_player("3")

    loop._process_fold_badge_detection(game_state, {3: True})

    assert game_state.actions_since_last_frame[-1] == ActionRecord(
        seat=3,
        action="FOLD",
        amount=0,
        confidence="high",
    )
    loop._hand_manager._add_actions(game_state.actions_since_last_frame)
    assert 3 not in loop._hand_manager.get_players_in_hand()


def test_fold_badge_detection_appends_hero_fold_and_clears_cache(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hero fold badge generates a FOLD action and clears cached hero cards."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "flop"
    loop._hand_manager._players_in_hand = {"1": True, "3": True}
    loop._cached_hero_cards = ["Ah", "Kd"]
    game_state = _state_with_player("3")

    loop._process_fold_badge_detection(game_state, {1: True, 3: True})

    assert game_state.actions_since_last_frame == [
        ActionRecord(
            seat=1,
            action="FOLD",
            amount=0,
            confidence="high",
        ),
        ActionRecord(
            seat=3,
            action="FOLD",
            amount=0,
            confidence="high",
        ),
    ]
    assert loop._cached_hero_cards is None


@pytest.mark.parametrize("action_name", ["CALL", "RAISE"])
def test_fold_badge_detection_ignores_hero_badge_with_non_fold_action(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    action_name: str,
) -> None:
    """Hero non-fold actions take precedence over same-frame hero fold badges."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "flop"
    loop._hand_manager._players_in_hand = {"1": True, "3": True}
    loop._cached_hero_cards = ["Ah", "Kd"]
    game_state = _state_with_player("3")
    hero_action = ActionRecord(
        seat=1,
        action=action_name,
        amount=100 if action_name != "CHECK" else 0,
        confidence="high",
    )
    game_state.actions_since_last_frame = [hero_action]

    with caplog.at_level(logging.INFO, logger="core.game_loop"):
        loop._process_fold_badge_detection(game_state, {1: True})

    assert game_state.actions_since_last_frame == [hero_action]
    assert loop._cached_hero_cards == ["Ah", "Kd"]
    assert (
        "Hero fold badge ignored because non-fold hero action was detected: "
        f"action={action_name}"
    ) in caplog.text
    assert loop._hero_fold_badge_ignored_for_hand is True
    assert loop._hero_fold_badge_ignored_reason == "non_fold_action"
    assert (
        "Hero fold badge ignore latched for hand: "
        f"reason=non_fold_action action={action_name}"
    ) in caplog.text
    assert "Hero FOLD detected via badge for seat 1" not in caplog.text


def test_fold_badge_detection_same_frame_check_sets_pending_recovery(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Same-frame Hero CHECK plus fold badge waits for boundary CHECK recording."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "flop"
    loop._hand_manager._players_in_hand = {"1": True, "3": True}
    loop._cached_hero_cards = ["Ah", "Kd"]
    game_state = _state_with_player("3")
    hero_check = ActionRecord(seat=1, action="CHECK", amount=0, confidence="high")
    game_state.actions_since_last_frame = [hero_check]

    with caplog.at_level(logging.INFO, logger="core.game_loop"):
        loop._process_fold_badge_detection(game_state, {1: True})

    assert game_state.actions_since_last_frame == [hero_check]
    assert loop._cached_hero_cards == ["Ah", "Kd"]
    assert loop._pending_hero_fold_badge_recovery is True
    assert loop._pending_hero_fold_badge_recovery_since is not None
    assert loop._hero_fold_badge_ignored_for_hand is False
    assert loop._hero_fold_badge_ignored_reason is None
    assert "Hero fold badge recovery pending: same-frame CHECK detected" in caplog.text
    assert "Hero fold badge ignore latched for hand" not in caplog.text


def test_pending_hero_fold_badge_recovery_replaces_check_after_hand_manager(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Pending same-frame recovery converts the recorded boundary CHECK to FOLD."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    start_state = create_empty_game_state()
    start_state.hero.cards = ["Ah", "Kd"]
    start_state.hero.cards_visible = True
    loop._hand_manager.process_frame(start_state)
    loop._hand_manager._players_in_hand = {"1": True, "3": True}
    loop._cached_hero_cards = ["Ah", "Kd"]
    game_state = _state_with_player("3")
    game_state.phase = "flop"
    hero_check = ActionRecord(seat=1, action="CHECK", amount=0, confidence="high")
    game_state.actions_since_last_frame = [hero_check]

    loop._process_fold_badge_detection(game_state, {1: True})
    loop._hand_manager._record_hero_action(
        ActionRecord(seat=1, action="CHECK", amount=0)
    )

    with caplog.at_level(logging.INFO, logger="core.game_loop"):
        loop._recover_pending_hero_fold_badge(game_state)

    current_street = loop._hand_manager.get_current_street_actions()
    assert current_street is not None
    assert current_street.actions == [
        ActionRecord(seat=1, action="FOLD", amount=0, confidence="high")
    ]
    assert loop._hand_manager.hero_folded is True
    assert 1 not in loop._hand_manager.get_players_in_hand()
    assert loop._cached_hero_cards is None
    assert loop._pending_hero_fold_badge_recovery is False
    assert loop._pending_hero_fold_badge_recovery_since is None
    assert (
        "Hero FOLD recovered from pending same-frame CHECK via fold badge"
        in caplog.text
    )


def test_pending_hero_fold_badge_recovery_expires_without_fold(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Expired pending recovery is cleared without folding Hero."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "flop"
    loop._hand_manager._players_in_hand = {"1": True}
    loop._pending_hero_fold_badge_recovery = True
    loop._pending_hero_fold_badge_recovery_since = time.monotonic() - 2.0
    game_state = create_empty_game_state()
    game_state.phase = "flop"

    with caplog.at_level(logging.INFO, logger="core.game_loop"):
        loop._recover_pending_hero_fold_badge(game_state)

    assert loop._pending_hero_fold_badge_recovery is False
    assert loop._pending_hero_fold_badge_recovery_since is None
    assert loop._hand_manager.hero_folded is False
    assert "Hero fold badge pending recovery expired" in caplog.text


def test_fold_badge_detection_recovers_recent_hero_check_as_fold(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A Hero fold badge can correct a very recent boundary CHECK to FOLD."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    start_state = create_empty_game_state()
    start_state.hero.cards = ["Ah", "Kd"]
    start_state.hero.cards_visible = True
    loop._hand_manager.process_frame(start_state)
    loop._hand_manager._players_in_hand = {"1": True, "3": True}
    loop._cached_hero_cards = ["Ah", "Kd"]
    loop._hand_manager._record_hero_action(
        ActionRecord(seat=1, action="CHECK", amount=0)
    )
    loop._hand_manager._last_hero_boundary_action_monotonic = time.monotonic() - 0.2
    game_state = _state_with_player("3")
    hero_check = ActionRecord(seat=1, action="CHECK", amount=0, confidence="high")
    game_state.actions_since_last_frame = [hero_check]

    with caplog.at_level(logging.INFO, logger="core.game_loop"):
        loop._process_fold_badge_detection(game_state, {1: True})

    current_street = loop._hand_manager.get_current_street_actions()
    assert current_street is not None
    assert current_street.actions == [
        ActionRecord(seat=1, action="FOLD", amount=0, confidence="high")
    ]
    assert loop._hand_manager.hero_folded is True
    assert 1 not in loop._hand_manager.get_players_in_hand()
    assert loop._cached_hero_cards is None
    assert loop._hero_fold_badge_ignored_for_hand is False
    assert game_state.actions_since_last_frame == [hero_check]
    assert "Hero FOLD recovered from CHECK via fold badge" in caplog.text


def test_fold_badge_detection_does_not_recover_check_during_obstruction(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Visual obstruction keeps Hero fold-badge recovery disabled."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    start_state = create_empty_game_state()
    start_state.hero.cards = ["Ah", "Kd"]
    start_state.hero.cards_visible = True
    loop._hand_manager.process_frame(start_state)
    loop._hand_manager._players_in_hand = {"1": True}
    loop._visual_obstruction_active = True
    loop._visual_obstruction_until = time.monotonic() + 10.0
    check = ActionRecord(seat=1, action="CHECK", amount=0)
    loop._hand_manager._record_hero_action(check)
    game_state = create_empty_game_state()
    game_state.actions_since_last_frame = [
        ActionRecord(seat=1, action="CHECK", amount=0, confidence="high")
    ]

    loop._process_fold_badge_detection(game_state, {1: True})

    current_street = loop._hand_manager.get_current_street_actions()
    assert current_street is not None
    assert current_street.actions == [check]
    assert loop._hand_manager.hero_folded is False
    assert loop._hero_fold_badge_ignored_for_hand is False
    assert loop._pending_hero_fold_badge_recovery is False


@pytest.mark.parametrize("action_name", ["CALL", "BET", "RAISE", "ALL_IN"])
def test_fold_badge_detection_keeps_non_check_hero_action_ignore(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
    action_name: str,
) -> None:
    """CALL/BET/RAISE/ALL_IN still ignore contradictory Hero fold badges."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "flop"
    loop._hand_manager._players_in_hand = {"1": True}
    loop._cached_hero_cards = ["Ah", "Kd"]
    game_state = create_empty_game_state()
    hero_action = ActionRecord(
        seat=1,
        action=action_name,
        amount=100,
        confidence="high",
    )
    game_state.actions_since_last_frame = [hero_action]

    loop._process_fold_badge_detection(game_state, {1: True})

    assert game_state.actions_since_last_frame == [hero_action]
    assert loop._hand_manager.hero_folded is False
    assert loop._cached_hero_cards == ["Ah", "Kd"]
    assert loop._hero_fold_badge_ignored_for_hand is True
    assert loop._pending_hero_fold_badge_recovery is False


def test_fold_badge_detection_latched_hero_ignore_survives_recent_guard(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A contradictory hero fold-badge latch is ignored after the time guard."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "flop"
    loop._hand_manager._players_in_hand = {"1": True}
    loop._cached_hero_cards = ["Ah", "Kd"]
    first_state = create_empty_game_state()
    first_state.actions_since_last_frame = [
        ActionRecord(seat=1, action="CHECK", amount=0, confidence="high")
    ]

    loop._process_fold_badge_detection(first_state, {1: True})
    assert loop._pending_hero_fold_badge_recovery is True
    loop._latch_hero_fold_badge_ignore("non_fold_action", "CHECK")

    loop._last_hero_non_fold_action_time = time.monotonic() - 2.0
    second_state = create_empty_game_state()

    with caplog.at_level(logging.DEBUG, logger="core.game_loop"):
        loop._process_fold_badge_detection(second_state, {1: True})

    assert second_state.actions_since_last_frame == []
    assert loop._cached_hero_cards == ["Ah", "Kd"]
    assert (
        "Hero fold badge ignored due to prior non-fold action in this hand: "
        "reason=non_fold_action"
    ) in caplog.text
    assert "Hero FOLD detected via badge for seat 1" not in caplog.text


def test_fold_badge_detection_opponent_badge_still_processed_with_hero_action(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Opponent fold badges are still processed when hero has a normal action."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "flop"
    loop._hand_manager._players_in_hand = {"1": True, "3": True}
    loop._cached_hero_cards = ["Ah", "Kd"]
    game_state = _state_with_player("3")
    hero_action = ActionRecord(
        seat=1,
        action="CHECK",
        amount=0,
        confidence="high",
    )
    game_state.actions_since_last_frame = [hero_action]

    loop._process_fold_badge_detection(game_state, {1: True, 3: True})

    assert game_state.actions_since_last_frame == [
        hero_action,
        ActionRecord(
            seat=3,
            action="FOLD",
            amount=0,
            confidence="high",
        ),
    ]
    assert loop._cached_hero_cards == ["Ah", "Kd"]


def test_fold_badge_detection_ignores_recent_hero_non_fold_action(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A recent hero non-fold action suppresses near-frame hero fold badges."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "flop"
    loop._hand_manager._players_in_hand = {"1": True}
    loop._cached_hero_cards = ["Ah", "Kd"]
    loop._last_hero_non_fold_action_time = time.monotonic()
    loop._last_hero_non_fold_action_name = "CHECK"
    game_state = create_empty_game_state()

    with caplog.at_level(logging.INFO, logger="core.game_loop"):
        loop._process_fold_badge_detection(game_state, {1: True})

    assert game_state.actions_since_last_frame == []
    assert loop._cached_hero_cards == ["Ah", "Kd"]
    assert loop._hero_fold_badge_ignored_for_hand is True
    assert loop._hero_fold_badge_ignored_reason == "recent_non_fold_action"
    assert (
        "Hero fold badge ignored because recent non-fold hero action was "
        "detected: action=CHECK"
    ) in caplog.text


def test_fold_badge_detection_latched_hero_ignore_still_processes_opponents(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hero ignore latch does not suppress opponent fold badges."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "flop"
    loop._hand_manager._players_in_hand = {"1": True, "3": True}
    loop._cached_hero_cards = ["Ah", "Kd"]
    loop._hero_fold_badge_ignored_for_hand = True
    loop._hero_fold_badge_ignored_reason = "non_fold_action"
    game_state = _state_with_player("3")

    loop._process_fold_badge_detection(game_state, {1: True, 3: True})

    assert game_state.actions_since_last_frame == [
        ActionRecord(seat=3, action="FOLD", amount=0, confidence="high")
    ]
    assert loop._cached_hero_cards == ["Ah", "Kd"]


def test_fold_badge_detection_ignores_hero_outside_hand(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hero fold badges outside active hand participation do not generate actions."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "flop"
    loop._hand_manager._players_in_hand = {"1": False, "3": True}
    loop._cached_hero_cards = ["Ah", "Kd"]
    game_state = _state_with_player("3")

    loop._process_fold_badge_detection(game_state, {1: True})

    assert game_state.actions_since_last_frame == []
    assert loop._cached_hero_cards == ["Ah", "Kd"]


def test_fold_badge_detection_hero_ignore_latch_clears_on_hand_start(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A new hand clears the hero fold-badge ignore latch."""
    loop = make_loop(workspace_tmp, monkeypatch, StaticFrameCapture())
    loop._hand_manager._phase = "preflop"
    loop._hand_manager._hand_just_started = True
    loop._hero_fold_badge_ignored_for_hand = True
    loop._hero_fold_badge_ignored_reason = "non_fold_action"
    loop._pending_hero_fold_badge_recovery = True
    loop._pending_hero_fold_badge_recovery_since = time.monotonic()

    state = loop.process_one_frame()

    assert state is not None
    assert loop._hero_fold_badge_ignored_for_hand is False
    assert loop._hero_fold_badge_ignored_reason is None
    assert loop._pending_hero_fold_badge_recovery is False
    assert loop._pending_hero_fold_badge_recovery_since is None


def test_pending_hero_fold_badge_recovery_clears_on_reset_and_stop(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reset and stop clear deferred Hero fold-badge recovery."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._pending_hero_fold_badge_recovery = True
    loop._pending_hero_fold_badge_recovery_since = time.monotonic()

    loop.reset()

    assert loop._pending_hero_fold_badge_recovery is False
    assert loop._pending_hero_fold_badge_recovery_since is None

    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._pending_hero_fold_badge_recovery = True
    loop._pending_hero_fold_badge_recovery_since = time.monotonic()

    loop.stop()

    assert loop._pending_hero_fold_badge_recovery is False
    assert loop._pending_hero_fold_badge_recovery_since is None


def test_fold_badge_detection_ignores_nonparticipants(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fold badges for seats outside the hand do not generate actions."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "flop"
    loop._hand_manager._players_in_hand = {"1": True, "3": False}
    loop._prev_state = _state_with_player("3")
    game_state = _state_with_player("3")

    loop._process_fold_badge_detection(game_state, {3: True})

    assert game_state.actions_since_last_frame == []


def test_fold_badge_detection_ignores_false_results(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Seats without fold badges do not generate actions."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "flop"
    loop._hand_manager._players_in_hand = {"1": True, "3": True}
    loop._prev_state = _state_with_player("3")
    game_state = _state_with_player("3")

    loop._process_fold_badge_detection(game_state, {3: False})

    assert game_state.actions_since_last_frame == []


def test_seat_card_detection_does_not_append_fold_after_confirm_frames(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Seat-card absence no longer generates FOLD actions by itself."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "flop"
    loop._hand_manager._players_in_hand = {"1": True, "2": True}
    game_state = _state_with_player("2")

    loop._process_seat_card_detection(game_state, {2: False})
    loop._process_seat_card_detection(game_state, {2: False})
    loop._process_seat_card_detection(game_state, {2: False})
    loop._process_seat_card_detection(game_state, {2: False})

    assert not any(
        action.seat == 2 and action.action.upper() == "FOLD"
        for action in game_state.actions_since_last_frame
    )
    assert 2 not in loop._seat_card_fold_latched


def test_seat_card_detection_waits_for_confirm_frames(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One or two no-card frames do not generate a FOLD action."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "flop"
    loop._hand_manager._players_in_hand = {"1": True, "2": True}
    game_state = _state_with_player("2")

    loop._process_seat_card_detection(game_state, {2: False})
    loop._process_seat_card_detection(game_state, {2: False})

    assert game_state.actions_since_last_frame == []
    assert loop._seat_no_card_streak[2] == 2


def test_seat_card_detection_visible_card_resets_streak(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A visible card resets the no-card streak before FOLD confirmation."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "flop"
    loop._hand_manager._players_in_hand = {"1": True, "2": True}
    game_state = _state_with_player("2")

    loop._process_seat_card_detection(game_state, {2: False})
    loop._process_seat_card_detection(game_state, {2: True})
    loop._process_seat_card_detection(game_state, {2: False})
    loop._process_seat_card_detection(game_state, {2: False})

    assert game_state.actions_since_last_frame == []
    assert loop._seat_no_card_streak[2] == 2


def test_seat_card_detection_does_not_latch_existing_frame_fold(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Seat-card detection does not interact with FOLD actions in the frame."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "flop"
    loop._hand_manager._players_in_hand = {"1": True, "2": True}
    game_state = _state_with_player("2")
    game_state.actions_since_last_frame.append(
        ActionRecord(seat=2, action="FOLD", amount=0, confidence="high")
    )
    loop._seat_no_card_streak[2] = 2

    loop._process_seat_card_detection(game_state, {2: False})

    assert game_state.actions_since_last_frame == [
        ActionRecord(seat=2, action="FOLD", amount=0, confidence="high")
    ]
    assert 2 not in loop._seat_card_fold_latched


def test_folded_seat_keeps_cards_visible_true(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Folded seats keep Cards=YES to show they were dealt cards."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "flop"
    loop._hand_manager._players_in_hand = {"1": True, "2": True, "3": False}
    loop._hand_manager._folded_seats = {"3"}
    game_state = create_empty_game_state()
    game_state.phase = "flop"
    game_state.players["3"].is_seated = True
    game_state.players["3"].in_current_hand = False
    game_state.players["3"].cards_visible = False

    loop._apply_seat_card_visibility(
        game_state,
        {2: True, 3: False, 4: False, 5: False, 6: False},
    )

    assert game_state.players["3"].cards_visible is True


def test_active_seat_detection_failure_preserves_cards_visible(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Active seats keep Cards=YES through temporary detector failures."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "flop"
    loop._hand_manager._players_in_hand = {"1": True, "2": True, "3": True}
    loop._hand_manager._folded_seats = set()
    game_state = create_empty_game_state()
    game_state.phase = "flop"
    game_state.players["3"].is_seated = True
    game_state.players["3"].in_current_hand = True
    game_state.players["3"].cards_visible = True

    loop._seat_card_confirmed = {3}

    loop._apply_seat_card_visibility(
        game_state,
        {2: True, 3: False, 4: False, 5: False, 6: False},
    )

    assert game_state.players["3"].cards_visible is True


def test_non_participant_shows_cards_no(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-participating seats still show Cards=NO."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "flop"
    loop._hand_manager._players_in_hand = {"1": True, "2": True}
    loop._hand_manager._folded_seats = set()
    game_state = create_empty_game_state()
    game_state.phase = "flop"
    game_state.players["3"].is_seated = True
    game_state.players["3"].in_current_hand = False
    game_state.players["3"].cards_visible = False

    loop._apply_seat_card_visibility(
        game_state,
        {2: True, 3: False, 4: False, 5: False, 6: False},
    )

    assert game_state.players["3"].cards_visible is False


def test_fold_badge_detector_runs_during_active_phase(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FoldBadgeDetector is called from process_one_frame during active phases."""
    image_path = Path("tests/fixtures/screenshots/coinpoker/cp_01_preflop_my_turnb.png")
    loop = make_loop(workspace_tmp, monkeypatch, FileCapture(image_path))
    loop._hand_manager._phase = "preflop"
    loop._hand_manager._players_in_hand = {"1": True, "2": True, "3": True}
    detector = MagicMock()
    detector.detect_all.return_value = {2: False, 3: False, 4: False, 5: False, 6: False}
    loop._fold_badge_detector = detector

    loop.process_one_frame()

    detector.detect_all.assert_called_once()


def test_fold_badge_latches_cleared_on_hand_start(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hand-start frames clear fold badge and seat-card state."""
    image_path = Path("tests/fixtures/screenshots/coinpoker/cp_01_preflop_my_turnb.png")
    loop = make_loop(workspace_tmp, monkeypatch, FileCapture(image_path))
    loop._hand_manager._phase = "preflop"
    loop._hand_manager._hand_id = 42
    loop._hand_manager._hand_just_started = True
    loop._hand_manager._hand_start_monotonic = time.monotonic()
    detector = MagicMock()
    detector.detect_all.return_value = {2: False, 3: False, 4: False, 5: False, 6: False}
    loop._fold_badge_detector = detector
    seat_detector = MagicMock()
    seat_detector.detect_all.return_value = {2: True, 3: True, 4: True, 5: True, 6: True}
    loop._seat_card_detector = seat_detector
    loop._seat_no_card_streak[2] = 2
    loop._seat_card_fold_latched.add(2)

    loop.process_one_frame()

    detector.reset.assert_called_once()
    detector.detect_all.assert_called_once()
    seat_detector.reset.assert_called_once()
    assert loop._seat_no_card_streak == {}
    assert loop._seat_card_fold_latched == set()


def test_seat_card_detector_skipped_during_hand_start_grace(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SeatCardDetector observation runs, but fold generation is skipped in grace."""
    image_path = Path("tests/fixtures/screenshots/coinpoker/cp_01_preflop_my_turnb.png")
    loop = make_loop(workspace_tmp, monkeypatch, FileCapture(image_path))
    loop._hand_manager._phase = "preflop"
    loop._hand_manager._players_in_hand = {"1": True, "2": True}
    loop._hand_manager._hand_start_monotonic = time.monotonic()
    loop._hand_start_grace_sec = 10.0
    loop._fold_badge_detector = MagicMock()
    loop._fold_badge_detector.detect_all.return_value = {2: False}
    loop._seat_card_detector = MagicMock()
    loop._seat_card_detector.detect_all.return_value = {2: False}

    loop.process_one_frame()

    loop._seat_card_detector.detect_all.assert_called_once()
    assert loop._seat_no_card_streak == {}


def test_revalidation_promotes_seat_with_cards(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Auto-revalidation promotes a seated out-of-hand seat with cards."""
    loop = make_loop(workspace_tmp, monkeypatch, StaticFrameCapture())
    loop._hand_manager._phase = "flop"
    loop._hand_manager._players_in_hand = {"1": True, "2": True, "3": False}
    loop._hand_manager._folded_seats = set()
    loop._seat_card_detector = MagicMock()
    loop._seat_card_detector.detect_all.return_value = {3: True}
    game_state = _state_with_player("3")
    game_state.active_player_count = 2
    game_state.players["3"].in_current_hand = False
    game_state.players["3"].cards_visible = False

    loop._revalidate_seat_cards_before_strategy(game_state)

    assert game_state.players["3"].in_current_hand is True
    assert game_state.players["3"].cards_visible is True
    assert game_state.active_player_count == 3
    assert 3 in loop._hand_manager.get_players_in_hand()
    assert "3" in loop._hand_manager._participated_seats


def test_revalidation_does_not_promote_folded_seat(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Auto-revalidation does not promote a folded seat."""
    loop = make_loop(workspace_tmp, monkeypatch, StaticFrameCapture())
    loop._hand_manager._phase = "flop"
    loop._hand_manager._players_in_hand = {"1": True, "2": True, "3": False}
    loop._hand_manager._folded_seats = {"3"}
    loop._seat_card_detector = MagicMock()
    loop._seat_card_detector.detect_all.return_value = {3: True}
    game_state = _state_with_player("3")
    game_state.active_player_count = 2
    game_state.players["3"].in_current_hand = False

    loop._revalidate_seat_cards_before_strategy(game_state)

    assert game_state.players["3"].in_current_hand is False
    assert game_state.active_player_count == 2


def test_revalidation_does_not_promote_empty_seat(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Auto-revalidation does not promote an unseated seat."""
    loop = make_loop(workspace_tmp, monkeypatch, StaticFrameCapture())
    loop._hand_manager._phase = "flop"
    loop._hand_manager._players_in_hand = {"1": True, "2": True, "3": False}
    loop._seat_card_detector = MagicMock()
    loop._seat_card_detector.detect_all.return_value = {3: True}
    game_state = create_empty_game_state()
    game_state.players["3"].is_seated = False
    game_state.players["3"].in_current_hand = False
    game_state.active_player_count = 2

    loop._revalidate_seat_cards_before_strategy(game_state)

    assert game_state.players["3"].in_current_hand is False
    assert game_state.active_player_count == 2


def test_revalidation_skipped_when_capture_fails(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Auto-revalidation is skipped when no frame is available."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "flop"
    loop._hand_manager._players_in_hand = {"1": True, "2": True, "3": False}
    loop._seat_card_detector = MagicMock()
    game_state = _state_with_player("3")
    game_state.active_player_count = 2
    game_state.players["3"].in_current_hand = False

    loop._revalidate_seat_cards_before_strategy(game_state)

    loop._seat_card_detector.detect_all.assert_not_called()
    assert game_state.players["3"].in_current_hand is False
    assert game_state.active_player_count == 2


def test_apply_seat_card_visibility_sets_player_cards_visible(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Seat-card observations update PlayerState.cards_visible for seated seats."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    state = create_empty_game_state()
    state.players["2"].is_seated = True
    state.players["3"].is_seated = False

    loop._apply_seat_card_visibility(state, {2: True, 3: True})

    assert state.players["2"].cards_visible is True
    assert state.players["3"].cards_visible is False


def test_visual_obstruction_detected_on_three_simultaneous_card_changes(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Three simultaneous seat-card changes activate visual obstruction guard."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._last_seat_card_states = {2: True, 3: True, 4: True}
    state = create_empty_game_state()
    for seat in ["2", "3", "4"]:
        state.players[seat].is_seated = True

    loop._apply_seat_card_visibility(state, {2: False, 3: False, 4: False})

    assert loop._is_visual_obstruction_active() is True


def test_visual_obstruction_freezes_cards_visible_true_to_false(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Visual obstruction keeps previously visible cards from dropping to NO."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._visual_obstruction_active = True
    loop._visual_obstruction_until = time.monotonic() + 10.0
    loop._prev_state = create_empty_game_state()
    loop._prev_state.players["2"].cards_visible = True
    state = create_empty_game_state()
    state.players["2"].is_seated = True

    loop._apply_seat_card_visibility(state, {2: False})

    assert state.players["2"].cards_visible is True


def test_visual_obstruction_allows_cards_visible_false_to_true(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Visual obstruction still allows cards visibility to recover to YES."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._visual_obstruction_active = True
    loop._visual_obstruction_until = time.monotonic() + 10.0
    loop._prev_state = create_empty_game_state()
    loop._prev_state.players["2"].cards_visible = False
    state = create_empty_game_state()
    state.players["2"].is_seated = True

    loop._apply_seat_card_visibility(state, {2: True})

    assert state.players["2"].cards_visible is True


def test_fold_badge_detection_ignored_during_visual_obstruction(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FoldBadge FOLD actions are not generated during visual obstruction."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._visual_obstruction_active = True
    loop._visual_obstruction_until = time.monotonic() + 10.0
    loop._hand_manager._phase = "flop"
    loop._hand_manager._players_in_hand = {"1": True, "2": True}
    game_state = _state_with_player("2")

    loop._process_fold_badge_detection(game_state, {2: True})

    assert game_state.actions_since_last_frame == []


def test_visual_obstruction_keeps_existing_player_name(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Blank name OCR does not overwrite cached names during obstruction."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._visual_obstruction_active = True
    loop._visual_obstruction_until = time.monotonic() + 10.0
    loop._cached_player_names["2"] = "Alice"
    state = create_empty_game_state()
    state.players["2"].is_seated = True
    state.players["2"].name = "-"

    loop._apply_seat_card_visibility(state, {2: True})

    assert state.players["2"].name == "Alice"


def test_new_hand_suppressed_during_visual_obstruction(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NEW_HAND events are suppressed while visual obstruction guard is active."""
    image_path = Path("tests/fixtures/screenshots/coinpoker/cp_01_preflop_my_turnb.png")
    loop = make_loop(workspace_tmp, monkeypatch, FileCapture(image_path))
    loop._hand_manager._phase = "flop"
    loop._prev_state = create_empty_game_state()
    loop._prev_state.pot = 3000
    loop._cached_hero_cards = ["Ah", "Kd"]
    loop._visual_obstruction_active = True
    loop._visual_obstruction_until = time.monotonic() + 10.0
    loop._action_estimator = MagicMock()
    loop._action_estimator.estimate.return_value = {
        "game_event": "NEW_HAND",
        "actions": [],
        "filtered_pot": None,
    }

    state = loop.process_one_frame()

    assert state is not None
    assert state.game_event is None


def test_table_visibility_fresh_dealer_makes_visible(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A freshly detected dealer button marks the table as visible."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    state = create_empty_game_state()

    loop._update_table_visibility(state, fresh_dealer_detected=True)

    assert state.table_visible is True


def test_table_visibility_hero_cards_make_visible(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Visible hero cards mark the table as visible."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    state = create_empty_game_state()
    state.hero.cards_visible = True

    loop._update_table_visibility(state, fresh_dealer_detected=False)

    assert state.table_visible is True


def test_table_visibility_board_cards_make_visible(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Visible board cards mark the table as visible."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    state = create_empty_game_state()
    state.board_card_count = 3

    loop._update_table_visibility(state, fresh_dealer_detected=False)

    assert state.table_visible is True


def test_table_visibility_single_seated_player_is_not_enough(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One stale seated OCR result does not mark the table as visible."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    state = create_empty_game_state()
    state.players["2"].is_seated = True

    loop._update_table_visibility(state, fresh_dealer_detected=False)

    assert state.table_visible is False


def test_table_visibility_pot_alone_is_not_enough(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pot OCR value alone does not mark the table as visible."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    state = create_empty_game_state()
    state.pot = 500

    loop._update_table_visibility(state, fresh_dealer_detected=False)

    assert state.table_visible is False


def test_table_visibility_inactive_confirm_frames_clear_visible_state(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The table is hidden after enough consecutive inactive frames."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    visible_state = create_empty_game_state()
    loop._update_table_visibility(visible_state, fresh_dealer_detected=True)
    assert visible_state.table_visible is True

    for _ in range(loop._table_inactive_confirm_frames):
        inactive_state = create_empty_game_state()
        loop._update_table_visibility(inactive_state, fresh_dealer_detected=False)

    assert inactive_state.table_visible is False


def test_table_invisible_confirm_abandons_active_hand_and_clears_strategy(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Confirmed table invisibility abandons an active hand without saving."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "flop"
    loop._hand_manager._hand_id = 5
    loop._hand_manager.abandon_current_hand = MagicMock(return_value=True)  # type: ignore[method-assign]
    hud_callback = MagicMock()
    loop._hud_callback = hud_callback
    loop._previous_recommendation = Recommendation(action="BET", amount=100)
    loop._previous_recommendation_context = {"hand_id": 5}
    loop._last_recommendation_log = (5, "flop", True)
    loop._last_strategy_is_my_turn = True
    loop._table_visible = True

    for _ in range(loop._table_inactive_confirm_frames):
        inactive_state = create_empty_game_state()
        loop._update_table_visibility(inactive_state, fresh_dealer_detected=False)

    loop._hand_manager.abandon_current_hand.assert_called_once_with("table_invisible")
    assert loop._previous_recommendation is None
    assert loop._previous_recommendation_context is None
    assert loop._last_recommendation_log is None
    assert loop._last_strategy_is_my_turn is False
    hud_callback.assert_called_with(None)


def test_clear_players_for_inactive_table_clears_stale_state(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inactive tables clear stale player and hero display state."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    state = create_empty_game_state()
    state.active_player_count = 3
    state.dealer_seat = 2
    state.hero.position = "BTN"
    state.hero.cards = ["Ah", "Kd"]
    state.hero.cards_visible = True
    state.hero.is_my_turn = True
    state.hero.in_current_hand = True
    state.players["2"] = PlayerState(
        name="Alice",
        stack=5000,
        bet=100,
        is_seated=True,
        cards_visible=True,
        in_current_hand=True,
    )

    loop._clear_players_for_inactive_table(state)

    assert state.active_player_count == 0
    assert state.dealer_seat is None
    assert state.hero.position is None
    assert state.hero.cards is None
    assert state.hero.cards_visible is False
    assert state.hero.is_my_turn is False
    assert state.hero.in_current_hand is False
    assert state.players["2"].is_seated is False
    assert state.players["2"].stack is None


def test_populate_position_skips_inactive_table(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Position calculation is skipped when table_visible is false."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    state = _make_seated_state(dealer_seat=1)
    state.table_visible = False
    calculate_mock = MagicMock()
    monkeypatch.setattr("core.game_loop.calculate_positions", calculate_mock)

    loop._populate_position(state)

    calculate_mock.assert_not_called()
    assert state.hero.position is None


def test_populate_position_skips_waiting_phase(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Position calculation is skipped during waiting phase."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    state = _make_seated_state(dealer_seat=1)
    state.phase = "waiting"
    calculate_mock = MagicMock()
    monkeypatch.setattr("core.game_loop.calculate_positions", calculate_mock)

    loop._populate_position(state)

    calculate_mock.assert_not_called()
    assert state.hero.position is None


def test_active_count_excludes_hero_when_not_in_hand(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Active count does not include hero after hero has folded."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "flop"
    loop._hand_manager._players_in_hand = {"1": False, "2": True, "3": True}
    loop._hand_manager._hero_folded = True
    state = create_empty_game_state()
    state.phase = "flop"
    state.table_visible = True
    state.active_player_count = 3

    loop._sync_game_state_with_hand_manager(state)

    assert state.active_player_count == 2
    assert state.hero.in_current_hand is False
    assert state.hero.has_folded is True


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
    game_state.phase = "preflop"
    game_state.table_visible = True
    game_state.dealer_seat = dealer_seat
    for player in game_state.players.values():
        player.is_seated = True
    return game_state


def _set_players_in_hand(loop: GameLoop, seats: set[int]) -> None:
    """Set HandManager players-in-hand state for position tests."""
    loop._hand_manager._players_in_hand = {str(seat): True for seat in seats}


def test_position_locked_during_hand(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Position lock is recalculated from the latest active hand state."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "preflop"
    loop._hand_manager._hand_id = 1
    loop._hand_manager._hand_just_started = True
    _set_players_in_hand(loop, {1, 2, 3, 4, 5, 6})
    first_state = _make_seated_state(dealer_seat=3)

    loop._update_hand_position_lock(first_state)

    assert first_state.hero.position == "BB"
    assert loop._hand_dealer_seat == 3

    loop._hand_manager._hand_just_started = False
    next_state = _make_seated_state(dealer_seat=4)
    loop._update_hand_position_lock(next_state)

    assert next_state.dealer_seat == 4
    assert next_state.hero.position == "UTG"


def test_position_updated_on_new_hand(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A new hand recomputes positions from the latest dealer seat."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "preflop"
    loop._hand_manager._hand_id = 1
    loop._hand_manager._hand_just_started = True
    _set_players_in_hand(loop, {1, 2, 3, 4, 5, 6})
    first_state = _make_seated_state(dealer_seat=3)
    loop._update_hand_position_lock(first_state)

    loop._hand_manager._phase = "waiting"
    loop._hand_manager._hand_just_started = False
    waiting_state = _make_seated_state(dealer_seat=4)
    loop._update_hand_position_lock(waiting_state)

    loop._hand_manager._phase = "preflop"
    loop._hand_manager._hand_id = 2
    loop._hand_manager._hand_just_started = True
    _set_players_in_hand(loop, {1, 2, 3, 4, 5, 6})
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
    loop._hand_manager._hand_id = 1
    loop._hand_manager._hand_just_started = True
    _set_players_in_hand(loop, {1, 2, 3, 4, 5, 6})
    active_state = _make_seated_state(dealer_seat=3)
    loop._update_hand_position_lock(active_state)

    loop._hand_manager._phase = "hand_end"
    loop._hand_manager._hand_just_started = False
    end_state = _make_seated_state(dealer_seat=3)
    loop._update_hand_position_lock(end_state)

    assert loop._hand_positions is None
    assert loop._hand_dealer_seat is None
    assert end_state.hero.position is None


def test_position_lock_dealer_three_full_ring_sets_hero_bb(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dealer seat 3 with all seats in hand makes hero BB, not BTN."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "preflop"
    loop._hand_manager._hand_id = 2
    loop._hand_manager._hand_just_started = True
    _set_players_in_hand(loop, {1, 2, 3, 4, 5, 6})
    state = _make_seated_state(dealer_seat=3)

    loop._update_hand_position_lock(state)

    assert loop._hand_positions is not None
    assert loop._hand_positions[3] == "BTN"
    assert state.hero.position != "BTN"
    assert state.hero.position == "BB"
    assert getattr(state.players["3"], "position") == "BTN"


def test_position_lock_update_logs_dealer_three_hero_bb(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Position lock update emits searchable INFO diagnostics."""
    caplog.set_level(logging.INFO, logger="core.game_loop")
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "preflop"
    loop._hand_manager._hand_id = 2
    loop._hand_manager._hand_just_started = True
    _set_players_in_hand(loop, {1, 2, 3, 4, 5, 6})
    state = _make_seated_state(dealer_seat=3)

    loop._update_hand_position_lock(state)

    assert "Position lock updated" in caplog.text
    assert "hand_id=2" in caplog.text
    assert "dealer=3" in caplog.text
    assert "active_seats=[1, 2, 3, 4, 5, 6]" in caplog.text
    assert "hero_position=BB" in caplog.text
    assert "dealer_source=game_state" in caplog.text


def test_position_lock_dealer_one_full_ring_sets_hero_btn(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dealer seat 1 with all seats in hand makes hero BTN."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "preflop"
    loop._hand_manager._hand_id = 4
    loop._hand_manager._hand_just_started = True
    _set_players_in_hand(loop, {1, 2, 3, 4, 5, 6})
    state = _make_seated_state(dealer_seat=1)

    loop._update_hand_position_lock(state)

    assert loop._hand_positions is not None
    assert loop._hand_positions[1] == "BTN"
    assert state.hero.position == "BTN"


def test_position_lock_update_logs_dealer_one_hero_btn(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Dealer seat 1 emits update log with hero BTN."""
    caplog.set_level(logging.INFO, logger="core.game_loop")
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "preflop"
    loop._hand_manager._hand_id = 4
    loop._hand_manager._hand_just_started = True
    _set_players_in_hand(loop, {1, 2, 3, 4, 5, 6})
    state = _make_seated_state(dealer_seat=1)

    loop._update_hand_position_lock(state)

    assert "Position lock updated" in caplog.text
    assert "dealer=1" in caplog.text
    assert "hero_position=BTN" in caplog.text


def test_position_lock_clears_stale_hero_btn_on_next_hand(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale hero BTN lock from the previous hand is not reused."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "preflop"
    loop._hand_manager._hand_id = 1
    loop._hand_manager._hand_just_started = True
    _set_players_in_hand(loop, {1, 2, 3, 4, 5, 6})
    first_state = _make_seated_state(dealer_seat=1)
    loop._update_hand_position_lock(first_state)
    assert first_state.hero.position == "BTN"

    loop._hand_manager._hand_id = 2
    loop._hand_manager._hand_just_started = True
    second_state = _make_seated_state(dealer_seat=3)
    loop._update_hand_position_lock(second_state)

    assert loop._hand_dealer_seat == 3
    assert loop._hand_positions is not None
    assert loop._hand_positions[3] == "BTN"
    assert second_state.hero.position == "BB"


def test_position_lock_uses_hand_manager_phase_when_state_is_waiting(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HandManager active phase updates position even if GameState is stale."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "preflop"
    loop._hand_manager._hand_id = 2
    loop._hand_manager._hand_just_started = True
    _set_players_in_hand(loop, {1, 2, 3, 4, 5, 6})
    state = _make_seated_state(dealer_seat=3)
    state.phase = "waiting"

    loop._update_hand_position_lock(state)

    assert state.hero.position == "BB"


def test_position_lock_logs_inactive_phase_skip(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Inactive phases emit a reasoned position lock skip log."""
    caplog.set_level(logging.INFO, logger="core.game_loop")
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "waiting"
    loop._hand_manager._hand_id = None
    state = _make_seated_state(dealer_seat=3)
    state.phase = "waiting"

    loop._update_hand_position_lock(state)

    assert "Position lock skipped: reason=inactive_phase" in caplog.text


def test_position_lock_logs_no_dealer_skip(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Missing all dealer sources emits a no_dealer skip log."""
    caplog.set_level(logging.INFO, logger="core.game_loop")
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "preflop"
    loop._hand_manager._hand_id = 2
    _set_players_in_hand(loop, {1, 2, 3, 4, 5, 6})
    loop._cached_dealer_seat = None
    loop._hand_dealer_seat = None
    state = _make_seated_state(dealer_seat=3)
    state.dealer_seat = None

    loop._update_hand_position_lock(state)

    assert "Position lock skipped: reason=no_dealer" in caplog.text
    assert "dealer_source=none" in caplog.text


def test_apply_locked_positions_logs_hero_position(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Applying an existing lock emits searchable INFO diagnostics."""
    caplog.set_level(logging.INFO, logger="core.game_loop")
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "preflop"
    loop._hand_manager._hand_id = 2
    loop._hand_position_hand_id = 2
    loop._hand_dealer_seat = 3
    loop._hand_positions = {3: "BTN", 2: "SB", 1: "BB"}
    state = _make_seated_state(dealer_seat=3)

    loop._apply_locked_positions(state)

    assert "Position lock applied" in caplog.text
    assert "hero_position=BB" in caplog.text


def test_strategy_sees_recalculated_hero_position(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Strategy handling receives the GameState after position recalculation."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "preflop"
    loop._hand_manager._hand_id = 2
    loop._hand_manager._hand_just_started = True
    _set_players_in_hand(loop, {1, 2, 3, 4, 5, 6})
    state = _make_seated_state(dealer_seat=3)
    seen_positions: list[str | None] = []

    def capture_strategy_position(game_state: Any) -> None:
        seen_positions.append(game_state.hero.position)

    monkeypatch.setattr(loop, "_handle_strategy", capture_strategy_position)

    loop._update_hand_position_lock(state)
    loop._handle_strategy(state)

    assert seen_positions == ["BB"]


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
    loop._hand_manager._players_in_hand = {"1": True, "2": True, "3": False}
    loop._name_recognizer.recognize_player_names = MagicMock(
        return_value={"2": "Alice", "3": "Bob", "4": None, "5": None, "6": None}
    )
    frame = np.zeros((20, 20, 3), dtype=np.uint8)

    first_state = loop._build_game_state(frame, time.time())
    second_state = loop._build_game_state(frame, time.time())

    assert loop._name_recognizer.recognize_player_names.call_count == 1
    assert first_state.players["2"].name == "Alice"
    assert second_state.players["2"].name == "Alice"
    assert first_state.players["3"].name is None


def test_player_names_recaptured_on_new_hand_id(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A new hand_id triggers a fresh player-name OCR capture."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "preflop"
    loop._hand_manager._players_in_hand = {"1": True, "2": True}
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


def test_player_name_ocr_failure_uses_previous_cache(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OCR failures for players in hand reuse the previous cached name."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "preflop"
    loop._hand_manager._hand_id = 9
    loop._hand_manager._players_in_hand = {"1": True, "2": True, "3": False}
    loop._cached_player_names = {"2": "PreviousAlice", "3": "PreviousBob"}
    loop._name_recognizer.recognize_player_names = MagicMock(
        return_value={"2": None, "3": "NewBob", "4": None, "5": None, "6": None}
    )
    frame = np.zeros((20, 20, 3), dtype=np.uint8)

    game_state = loop._build_game_state(frame, time.time())

    assert game_state.players["2"].name == "PreviousAlice"
    assert game_state.players["3"].name is None


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
    state.hero.in_current_hand = True

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
    state.table_visible = True
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
    state.table_visible = True
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
    state.hero.in_current_hand = True

    loop._handle_strategy(state)

    loop._recommendation_engine.generate.assert_called_once()
    assert loop.current_recommendation is not None
    assert loop.current_recommendation.action == "RAISE"


def test_waiting_hero_cards_one_frame_does_not_start_hand(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Waiting hero cards need a stable streak before hand start."""
    loop = make_loop(workspace_tmp, monkeypatch, StaticFrameCapture())
    loop._hero_card_confirm_frames = 2
    loop._card_recognizer.recognize_hero_cards = MagicMock(
        return_value=["Qd", "Ac"],
    )

    state = loop.process_one_frame()

    assert state is not None
    assert state.hero.cards is None
    assert state.hero.cards_visible is False
    assert state.hero_cards_unstable_reason == "hero_cards_waiting_unstable"
    assert loop._hand_manager.phase == "waiting"


def test_waiting_hero_cards_two_matching_frames_can_start_hand(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Matching waiting hero cards pass the stability gate on frame two."""
    loop = make_loop(workspace_tmp, monkeypatch, StaticFrameCapture())
    loop._hero_card_confirm_frames = 2
    loop._card_recognizer.recognize_hero_cards = MagicMock(
        return_value=["Qd", "Ac"],
    )

    first = loop.process_one_frame()
    second = loop.process_one_frame()

    assert first is not None
    assert second is not None
    assert first.hero.cards is None
    assert second.hero.cards == ["Qd", "Ac"]
    assert second.hero_cards_unstable_reason is None
    loop._hand_manager.process_frame(second)
    assert loop._hand_manager.phase == "preflop"


def test_waiting_hero_cards_change_resets_streak(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Changing waiting hero-card candidates restart the confirmation streak."""
    loop = make_loop(workspace_tmp, monkeypatch, StaticFrameCapture())
    loop._hero_card_confirm_frames = 2
    loop._card_recognizer.recognize_hero_cards = MagicMock(
        side_effect=[["Qd", "Ac"], ["Qd", "Kc"]],
    )

    first = loop.process_one_frame()
    second = loop.process_one_frame()

    assert first is not None
    assert second is not None
    assert second.hero.cards is None
    assert loop._hero_card_candidate == ["Qd", "Kc"]
    assert loop._hero_card_candidate_streak == 1
    assert loop._hand_manager.phase == "waiting"


def test_strategy_skipped_after_hero_fold(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recommendation generation stops once hero is no longer in the hand."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._recommendation_engine = MagicMock()
    loop._hand_manager._phase = "flop"
    loop._hand_manager._players_in_hand = {"1": False, "2": True}
    loop._previous_recommendation = Recommendation(action="BET", amount=100)
    state = create_empty_game_state()
    state.phase = "flop"
    state.hero.is_my_turn = True

    loop._handle_strategy(state)

    loop._recommendation_engine.generate.assert_not_called()
    assert loop.current_recommendation is None
    assert loop._last_strategy_is_my_turn is False


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
    state.hero.in_current_hand = True
    state.board = ["2h", "3d", "5c"]

    loop._handle_strategy(state)

    assert loop.current_recommendation is not None
    assert loop.current_recommendation.action == "CHECK"
    loop._recommendation_engine.generate.assert_called_once()


def test_strategy_deferred_during_pot_spike_hold(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pot spike hold prevents broken pot/action strategy requests."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._recommendation_engine = MagicMock()
    loop._previous_recommendation = Recommendation(action="BET", amount=120)
    loop._previous_recommendation_context = {"hand_id": 1}
    loop._pending_recommendation_active_id = 7
    loop._pending_recommendation_context = {"hand_id": 1}
    loop._start_async_postflop_recommendation = MagicMock()  # type: ignore[method-assign]
    computing_callback = MagicMock()
    loop._hud_computing_callback = computing_callback
    loop._hand_manager._phase = "turn"
    loop._hand_manager._players_in_hand = {"1": True, "2": True}

    state = create_empty_game_state()
    state.phase = "turn"
    state.hero.is_my_turn = True
    state.hero.in_current_hand = True
    state.active_player_count = 2
    state.board = ["2h", "3d", "5c", "9s"]
    state.pot = 314
    state.actions_since_last_frame = [
        ActionRecord(seat=5, action="BET", amount=13820),
    ]
    state.strategy_defer_reason = "pot_spike_hold"

    loop._handle_strategy(state)

    loop._recommendation_engine.generate.assert_not_called()
    loop._start_async_postflop_recommendation.assert_not_called()
    assert loop.current_recommendation is None
    assert loop._previous_recommendation_context is None
    assert loop._pending_recommendation_active_id is None
    computing_callback.assert_called_once_with("WAITING FOR STABLE POT...")


def test_async_recommendation_accepted_logs_details(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Async accepted logs include source, action, reason, and latency."""
    caplog.set_level(logging.INFO, logger="core.game_loop")
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    recommendation = Recommendation(
        action="BET",
        amount=120,
        reason="solver value bet",
        confidence="high",
        strategy_source="solver",
        latency_breakdown={"solver_ms": 12.0},
    )
    state = create_empty_game_state()
    state.hand_id = 3
    state.phase = "flop"
    monkeypatch.setattr(
        loop,
        "_is_recommendation_context_still_valid",
        lambda _ctx, _state: True,
    )
    loop._pending_recommendation_active_id = 9
    loop._pending_recommendation_context = {"hand_id": 3}
    loop._pending_recommendation_completed[9] = _AsyncRecommendationResult(
        request_id=9,
        recommendation=recommendation,
    )

    result = loop._poll_async_recommendation_result(state)

    assert result is recommendation
    assert "Async recommendation accepted: request_id=9" in caplog.text
    assert "hand_id=3" in caplog.text
    assert "phase=flop" in caplog.text
    assert "source=solver" in caplog.text
    assert "action=BET" in caplog.text
    assert "amount=120" in caplog.text
    assert "reason=solver value bet" in caplog.text
    assert "latency={'solver_ms': 12.0}" in caplog.text


def test_async_fallback_recommendation_accepted_logs_warning(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Accepted fallback async recommendations emit a warning with reason."""
    caplog.set_level(logging.INFO, logger="core.game_loop")
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    recommendation = Recommendation(
        action="CHECK",
        amount=0,
        reason="Solver request unavailable",
        confidence="low",
        strategy_source="fallback",
        latency_breakdown={"headsup_total_ms": 25.0},
    )
    state = create_empty_game_state()
    state.hand_id = 4
    state.phase = "turn"
    monkeypatch.setattr(
        loop,
        "_is_recommendation_context_still_valid",
        lambda _ctx, _state: True,
    )
    loop._pending_recommendation_active_id = 10
    loop._pending_recommendation_context = {"hand_id": 4}
    loop._pending_recommendation_completed[10] = _AsyncRecommendationResult(
        request_id=10,
        recommendation=recommendation,
    )

    result = loop._poll_async_recommendation_result(state)

    assert result is recommendation
    assert "Async recommendation accepted: request_id=10" in caplog.text
    assert "source=fallback" in caplog.text
    assert "Async fallback recommendation accepted: request_id=10" in caplog.text
    assert "reason=Solver request unavailable" in caplog.text
    assert "latency={'headsup_total_ms': 25.0}" in caplog.text


def test_active_hero_card_single_mismatch_does_not_abandon(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One active-hand hero-card mismatch is only a candidate."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "preflop"
    loop._hand_manager._hand_id = 6
    loop._cached_hero_cards = ["Qd", "Ac"]
    loop._card_recognizer.recognize_hero_cards = MagicMock(
        return_value=["Qd", "4c"],
    )
    state = create_empty_game_state()
    state.phase = "preflop"

    loop._validate_active_hero_cards(np.zeros((1, 1, 3), dtype=np.uint8), state)

    assert loop._hero_card_active_mismatch_streak == 1
    assert loop._hero_cards_invalid_for_hand is False
    assert loop._hand_manager.phase == "preflop"
    assert state.hero_cards_unstable_reason is None


def test_active_hero_card_confirmed_mismatch_abandons_and_skips_strategy(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two active-hand hero-card mismatches abandon and block strategy."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._recommendation_engine = MagicMock()
    loop._previous_recommendation = Recommendation(action="BET", amount=120)
    loop._pending_recommendation_active_id = 11
    loop._hud_computing_callback = MagicMock()
    loop._hand_manager._phase = "preflop"
    loop._hand_manager._hand_id = 6
    loop._cached_hero_cards = ["Qd", "Ac"]
    loop._hero_cards_recommendation_started_for_hand = True
    loop._card_recognizer.recognize_hero_cards = MagicMock(
        return_value=["Qd", "4c"],
    )
    state = create_empty_game_state()
    state.phase = "preflop"
    state.hand_id = 6
    state.hero.is_my_turn = True
    state.hero.in_current_hand = True

    frame = np.zeros((1, 1, 3), dtype=np.uint8)
    loop._validate_active_hero_cards(frame, state)
    loop._validate_active_hero_cards(frame, state)
    loop._handle_strategy(state)

    assert loop._hand_manager.phase == "waiting"
    assert loop._hero_cards_invalid_for_hand is True
    assert state.hero_cards_unstable_reason == "hero_cards_changed_after_recommendation"
    assert loop.current_recommendation is None
    assert loop._pending_recommendation_active_id is None
    loop._recommendation_engine.generate.assert_not_called()
    loop._hud_computing_callback.assert_called_with("HERO CARDS UNSTABLE")


def test_active_hero_card_mismatch_ignored_during_visual_obstruction(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Visual obstruction protection disables active hero-card mismatch checks."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "flop"
    loop._hand_manager._hand_id = 6
    loop._cached_hero_cards = ["Qd", "Ac"]
    loop._card_recognizer.recognize_hero_cards = MagicMock(
        return_value=["Qd", "4c"],
    )
    monkeypatch.setattr(loop, "_is_visual_obstruction_protected", lambda: True)
    state = create_empty_game_state()
    state.phase = "flop"

    loop._validate_active_hero_cards(np.zeros((1, 1, 3), dtype=np.uint8), state)

    assert loop._hero_card_active_mismatch_streak == 0
    assert loop._hero_cards_invalid_for_hand is False
    assert loop._hand_manager.phase == "flop"


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
    state_on.hero.in_current_hand = True
    state_on.board = ["2h", "3d", "5c"]

    loop._handle_strategy(state_on)
    assert loop.current_recommendation is not None

    state_off = create_empty_game_state()
    state_off.phase = "flop"
    state_off.hero.is_my_turn = False
    state_off.hero.in_current_hand = True
    state_off.board = ["2h", "3d", "5c"]

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
    state.hero.in_current_hand = True
    state.board = ["2h", "3d", "5c"]

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
    state.hero.in_current_hand = True
    state.board = ["2h", "3d", "5c"]

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
    state.hero.in_current_hand = True
    state.board = ["2h", "3d", "5c"]

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


def test_waiting_same_hero_cards_without_clear_does_not_start_hand(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Stale hero cards from the previous hand are suppressed in waiting."""
    image_path = Path("tests/fixtures/screenshots/coinpoker/cp_01_preflop_my_turnb.png")
    loop = make_loop(workspace_tmp, monkeypatch, FileCapture(image_path))
    loop._last_hand_manager_phase = "preflop"
    loop._hand_manager._phase = "waiting"
    loop._cached_hero_cards = ["Kd", "Qc"]
    loop._card_recognizer.recognize_hero_cards = MagicMock(
        return_value=["Kd", "Qc"]
    )

    with caplog.at_level(logging.INFO, logger="core.game_loop"):
        state = loop.process_one_frame()
        assert state is not None
        loop._hand_manager.process_frame(state)

    assert state.hero.cards is None
    assert loop._hand_manager.phase == "waiting"
    assert "suppressed as stale cards" in caplog.text


def test_waiting_same_hero_cards_after_clear_can_start_hand(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same hero cards can start a hand after a missing-card frame."""
    image_path = Path("tests/fixtures/screenshots/coinpoker/cp_01_preflop_my_turnb.png")
    loop = make_loop(workspace_tmp, monkeypatch, FileCapture(image_path))
    loop._hand_manager._phase = "waiting"
    loop._last_ended_hero_cards = ["Kd", "Qc"]
    loop._hero_cards_missing_since_hand_end = True
    loop._card_recognizer.recognize_hero_cards = MagicMock(
        return_value=["Kd", "Qc"]
    )

    state = loop.process_one_frame()
    assert state is not None
    loop._hand_manager.process_frame(state)

    assert state.hero.cards == ["Kd", "Qc"]
    assert loop._hand_manager.phase == "preflop"


def test_stale_suppression_timeout(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Stale hero-card suppression expires after the safety timeout."""
    image_path = Path("tests/fixtures/screenshots/coinpoker/cp_01_preflop_my_turnb.png")
    loop = make_loop(workspace_tmp, monkeypatch, FileCapture(image_path))
    loop._hand_manager._phase = "waiting"
    loop._last_hand_manager_phase = "waiting"
    loop._last_ended_hero_cards = ["Kd", "Qc"]
    loop._hero_cards_missing_since_hand_end = False
    loop._stale_suppression_start_time = time.monotonic() - 11.0
    loop._card_recognizer.recognize_hero_cards = MagicMock(
        return_value=["Kd", "Qc"]
    )

    with caplog.at_level(logging.INFO, logger="core.game_loop"):
        state = loop.process_one_frame()
        assert state is not None
        loop._hand_manager.process_frame(state)

    assert state.hero.cards == ["Kd", "Qc"]
    assert loop._hand_manager.phase == "preflop"
    assert "Stale card suppression timed out" in caplog.text


def test_stale_suppression_normal_clear(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing-card frame clears stale suppression through the normal path."""
    loop = make_loop(workspace_tmp, monkeypatch, StaticFrameCapture())
    loop._hand_manager._phase = "waiting"
    loop._last_hand_manager_phase = "waiting"
    loop._last_ended_hero_cards = ["Kd", "Qc"]
    loop._hero_cards_missing_since_hand_end = False
    loop._card_recognizer.recognize_hero_cards = MagicMock(
        side_effect=[[None, None], ["Kd", "Qc"]]
    )

    first_state = loop.process_one_frame()
    assert first_state is not None
    loop._hand_manager.process_frame(first_state)

    assert first_state.hero.cards is None
    assert loop._hero_cards_missing_since_hand_end is True
    assert loop._hand_manager.phase == "waiting"

    second_state = loop.process_one_frame()
    assert second_state is not None
    loop._hand_manager.process_frame(second_state)

    assert second_state.hero.cards == ["Kd", "Qc"]
    assert loop._hand_manager.phase == "preflop"


def test_waiting_different_hero_cards_can_start_hand(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Different hero cards are accepted as a new hand without a clear frame."""
    image_path = Path("tests/fixtures/screenshots/coinpoker/cp_01_preflop_my_turnb.png")
    loop = make_loop(workspace_tmp, monkeypatch, FileCapture(image_path))
    loop._hand_manager._phase = "waiting"
    loop._last_ended_hero_cards = ["Kd", "Qc"]
    loop._hero_cards_missing_since_hand_end = False
    loop._card_recognizer.recognize_hero_cards = MagicMock(
        return_value=["8d", "Td"]
    )

    state = loop.process_one_frame()
    assert state is not None
    loop._hand_manager.process_frame(state)

    assert state.hero.cards == ["8d", "Td"]
    assert loop._hand_manager.phase == "preflop"


def test_waiting_different_hero_cards_clear_card_wait_suppression(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Different cards clear stale suppression immediately after hand end."""
    image_path = Path("tests/fixtures/screenshots/coinpoker/cp_01_preflop_my_turnb.png")
    loop = make_loop(workspace_tmp, monkeypatch, FileCapture(image_path))
    loop._last_hand_manager_phase = "preflop"
    loop._hand_manager._phase = "waiting"
    loop._cached_hero_cards = ["As", "2s"]
    loop._card_recognizer.recognize_hero_cards = MagicMock(
        return_value=["7c", "6d"]
    )

    with caplog.at_level(logging.INFO, logger="core.game_loop"):
        state = loop.process_one_frame()
        assert state is not None
        loop._hand_manager.process_frame(state)

    assert state.hero.cards == ["7c", "6d"]
    assert loop._hand_manager.phase == "preflop"
    assert loop._waiting_for_card_clear is False
    assert loop._hero_cards_missing_since_hand_end is True
    assert (
        "Stale hero card suppression cleared: new hero cards differ from "
        "last ended hand current=['7c', '6d'] last=['As', '2s']"
    ) in caplog.text


def test_waiting_different_hero_cards_still_respect_pot_guard(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Different cards are not stale, but pot guard can still block hand start."""
    image_path = Path("tests/fixtures/screenshots/coinpoker/cp_01_preflop_my_turnb.png")
    loop = make_loop(workspace_tmp, monkeypatch, FileCapture(image_path))
    loop._last_hand_manager_phase = "preflop"
    loop._hand_manager._phase = "waiting"
    loop._cached_hero_cards = ["As", "2s"]
    loop._card_recognizer.recognize_hero_cards = MagicMock(
        return_value=["7c", "6d"]
    )
    loop._number_recognizer.recognize_all = MagicMock(
        return_value={
            "pot": 5000,
            "hero_stack": 5000,
            "hero_bet": 0,
            "player_stacks": {},
            "player_bets": {},
        }
    )

    with caplog.at_level(logging.INFO, logger="core.game_loop"):
        state = loop.process_one_frame()
        assert state is not None
        loop._hand_manager.process_frame(state)

    assert state.hero.cards is None
    assert loop._hand_manager.phase == "waiting"
    assert "Stale hero card suppression cleared" in caplog.text
    assert "New hand start suppressed: pot too large" in caplog.text


@pytest.mark.parametrize("board_count", [3, 5])
def test_recent_hand_end_suppresses_phase_fast_forward(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    board_count: int,
) -> None:
    """Previous-hand context makes visible board cards residual at hand start."""
    image_path = Path("tests/fixtures/screenshots/coinpoker/cp_01_preflop_my_turnb.png")
    loop = make_loop(workspace_tmp, monkeypatch, FileCapture(image_path))
    loop._last_hand_manager_phase = "waiting"
    loop._hand_manager._phase = "waiting"
    loop._last_ended_hero_cards = ["As", "2s"]
    loop._hero_cards_missing_since_hand_end = True
    loop._card_recognizer.recognize_hero_cards = MagicMock(
        return_value=["7c", "6d"]
    )
    board = ["Ah", "Kd", "Qs", "Jh", "Tc"][:board_count]
    loop._card_recognizer.recognize_board_cards = MagicMock(return_value=board)
    loop._card_recognizer.count_board_cards = MagicMock(return_value=board_count)

    with caplog.at_level(logging.INFO):
        state = loop.process_one_frame()
        assert state is not None
        loop._hand_manager.process_frame(state)

    assert state.suppress_phase_fast_forward is True
    assert loop._hand_manager.phase == "preflop"
    assert (
        "Phase fast-forward suppressed at hand start: "
        f"board_count={board_count} reason=recent_hand_end_or_stale_clear"
    ) in caplog.text


def test_stale_clear_suppresses_phase_fast_forward(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Stale suppression clear starts preflop despite residual flop cards."""
    image_path = Path("tests/fixtures/screenshots/coinpoker/cp_01_preflop_my_turnb.png")
    loop = make_loop(workspace_tmp, monkeypatch, FileCapture(image_path))
    loop._last_hand_manager_phase = "preflop"
    loop._hand_manager._phase = "waiting"
    loop._cached_hero_cards = ["As", "2s"]
    loop._card_recognizer.recognize_hero_cards = MagicMock(
        return_value=["7c", "6d"]
    )
    loop._card_recognizer.recognize_board_cards = MagicMock(
        return_value=["Ah", "Kd", "Qs"]
    )
    loop._card_recognizer.count_board_cards = MagicMock(return_value=3)

    with caplog.at_level(logging.INFO):
        state = loop.process_one_frame()
        assert state is not None
        loop._hand_manager.process_frame(state)

    assert state.suppress_phase_fast_forward is True
    assert loop._hand_manager.phase == "preflop"
    assert "Stale hero card suppression cleared" in caplog.text
    assert (
        "Phase fast-forward suppressed at hand start: "
        "board_count=3 reason=recent_hand_end_or_stale_clear"
    ) in caplog.text


def test_hand_start_clears_stale_card_suppression_state(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hand-start cleanup clears stale-card suppression bookkeeping."""
    loop = make_loop(workspace_tmp, monkeypatch, StaticFrameCapture())
    loop._hand_manager._phase = "preflop"
    loop._hand_manager._hand_just_started = True
    loop._waiting_for_card_clear = True
    loop._hero_cards_missing_since_hand_end = True
    loop._last_ended_hero_cards = ["As", "2s"]
    loop._stale_suppression_start_time = time.monotonic()
    loop._stale_suppression_bypassed = True

    state = loop.process_one_frame()

    assert state is not None
    assert loop._waiting_for_card_clear is False
    assert loop._hero_cards_missing_since_hand_end is False
    assert loop._last_ended_hero_cards is None
    assert loop._stale_suppression_start_time is None
    assert loop._stale_suppression_bypassed is False


def create_test_game_state(phase: str = "waiting") -> Any:
    """Create a test GameState with basic hero configuration."""
    state = create_empty_game_state()
    state.phase = phase
    if phase in {"preflop", "flop", "turn", "river"}:
        state.hero.cards = ["Ah", "Kd"]
    return state


@pytest.fixture
def game_loop_env(workspace_tmp: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[GameLoop, HandManager]:
    """Create GameLoop and HandManager for seat-card visibility tests."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    return loop, loop._hand_manager


def test_unconfirmed_seat_not_protected(game_loop_env: tuple[GameLoop, HandManager]) -> None:
    """Seat that was never stably detected with cards should not be protected."""
    gl, hm = game_loop_env
    hm._phase = "flop"
    hm._players_in_hand = {"1": True, "2": True, "3": True}
    hm._folded_seats = set()

    gs = create_test_game_state(phase="flop")
    gs.players["3"].is_seated = True
    gs.players["3"].in_current_hand = True
    gs.players["3"].cards_visible = False

    # Seat 3 was never confirmed (not in _seat_card_confirmed)
    gl._seat_card_confirmed = set()

    seat_card_results = {2: True, 3: False, 4: False, 5: False, 6: False}
    gl._apply_seat_card_visibility(gs, seat_card_results)

    # Unconfirmed seat should NOT be protected
    assert gs.players["3"].cards_visible is False


def test_confirmed_seat_is_protected(game_loop_env: tuple[GameLoop, HandManager]) -> None:
    """Seat that was confirmed with cards should be protected on detection failure."""
    gl, hm = game_loop_env
    hm._phase = "flop"
    hm._players_in_hand = {"1": True, "2": True, "3": True}
    hm._folded_seats = set()

    gs = create_test_game_state(phase="flop")
    gs.players["3"].is_seated = True
    gs.players["3"].in_current_hand = True
    gs.players["3"].cards_visible = True

    # Seat 3 was previously confirmed
    gl._seat_card_confirmed = {3}

    seat_card_results = {2: True, 3: False, 4: False, 5: False, 6: False}
    gl._apply_seat_card_visibility(gs, seat_card_results)

    # Confirmed seat should be protected
    assert gs.players["3"].cards_visible is True


def test_cards_invisible_forces_not_in_hand(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cards_visible=False, in_current_hand=True, not in players_in_hand -> NO."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "flop"
    # Seat 3 is NOT in players_in_hand, NOT participant, NOT confirmed
    loop._hand_manager._players_in_hand = {"1": True, "2": True}
    loop._hand_manager._folded_seats = set()
    loop._hand_manager._participated_seats = set()
    loop._hand_manager._participant_observed_seats = set()
    loop._seat_card_confirmed = set()
    state = create_empty_game_state()
    state.players["3"].is_seated = True
    state.players["3"].in_current_hand = True
    state.players["3"].cards_visible = False

    loop._apply_seat_card_visibility(state, {3: False})

    assert state.players["3"].in_current_hand is False


def test_cards_invisible_folded_stays_in_hand(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cards_visible=False, in_current_hand=True, folded=True -> stays True."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "flop"
    loop._hand_manager._players_in_hand = {"1": True, "2": True, "3": True}
    loop._hand_manager._folded_seats = {"3"}
    loop._seat_card_confirmed = set()
    state = create_empty_game_state()
    state.players["3"].is_seated = True
    state.players["3"].in_current_hand = True
    state.players["3"].cards_visible = False

    loop._apply_seat_card_visibility(state, {3: False})

    assert state.players["3"].in_current_hand is True


def test_cards_invisible_confirmed_stays_in_hand(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cards_visible=False, in_current_hand=True, confirmed -> stays True."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "flop"
    loop._hand_manager._players_in_hand = {"1": True, "2": True, "3": True}
    loop._hand_manager._folded_seats = set()
    loop._seat_card_confirmed = {3}
    state = create_empty_game_state()
    state.players["3"].is_seated = True
    state.players["3"].in_current_hand = True
    state.players["3"].cards_visible = False

    loop._apply_seat_card_visibility(state, {3: False})

    assert state.players["3"].in_current_hand is True


def test_current_street_actions_synced_from_hand_manager(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """current_street_actions is synced from HandManager to GameState."""
    from core.hand_manager import StreetActions

    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    hm = loop._hand_manager

    # Set up flop street actions in HandManager
    flop_actions = StreetActions(street="flop")
    flop_actions.actions = [
        ActionRecord(seat=4, action="BET", amount=300, confidence="high"),
        ActionRecord(seat=3, action="CALL", amount=300, confidence="high"),
        ActionRecord(seat=2, action="RAISE", amount=1600, confidence="high"),
    ]
    preflop_actions = StreetActions(street="preflop")
    preflop_actions.actions = [
        ActionRecord(seat=2, action="BLIND_SB", amount=50, confidence="high"),
        ActionRecord(seat=3, action="RAISE", amount=300, confidence="high"),
        ActionRecord(seat=1, action="CALL", amount=300, confidence="high"),
    ]
    hm._street_actions["preflop"] = preflop_actions
    hm._street_actions["flop"] = flop_actions
    hm._phase = "flop"

    state = create_empty_game_state()
    state.table_visible = True
    state.hero.in_current_hand = True
    state.hero.cards = ["Ah", "Kd"]

    loop._sync_game_state_with_hand_manager(state)

    assert len(state.current_street_actions) == 3
    assert state.current_street_actions[0].seat == 4
    assert state.current_street_actions[0].action == "BET"
    assert state.current_street_actions[0].amount == 300
    assert state.current_street_actions[1].seat == 3
    assert state.current_street_actions[1].action == "CALL"
    assert state.current_street_actions[1].amount == 300
    assert state.current_street_actions[2].seat == 2
    assert state.current_street_actions[2].action == "RAISE"
    assert state.current_street_actions[2].amount == 1600
    assert len(state.preflop_actions) == 2
    assert state.preflop_actions[0].seat == 3
    assert state.preflop_actions[0].action == "RAISE"
    assert state.preflop_actions[1].seat == 1
    assert state.preflop_actions[1].action == "CALL"


def test_current_street_actions_empty_when_no_street(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """current_street_actions is empty when HandManager has no current street."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    hm = loop._hand_manager
    hm._phase = "waiting"
    hm._street_actions.clear()

    state = create_empty_game_state()
    state.table_visible = True

    loop._sync_game_state_with_hand_manager(state)

    assert state.current_street_actions == []
    assert state.preflop_actions == []


# ---------------------------------------------------------------------------
# Phase 30-Fix41: Pot preservation during visual obstruction
# ---------------------------------------------------------------------------


def test_obstruction_active_pot_decrease_preserved(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Pot decrease is ignored while visual obstruction is active."""
    loop = make_loop(workspace_tmp, monkeypatch, StaticFrameCapture())
    loop._hand_manager._phase = "preflop"
    loop._prev_state = create_empty_game_state()
    loop._prev_state.pot = 314
    loop._visual_obstruction_active = True
    loop._visual_obstruction_until = time.monotonic() + 10.0
    loop._action_estimator = MagicMock()
    loop._action_estimator.estimate.return_value = {
        "game_event": None,
        "actions": [],
        "filtered_pot": 0,
    }

    with caplog.at_level(logging.INFO, logger="core.game_loop"):
        state = loop.process_one_frame()

    assert state is not None
    assert state.pot == 314
    assert "Pot decrease ignored during visual obstruction/recovery" in caplog.text


def test_process_one_frame_sets_strategy_defer_on_pot_spike_hold(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ActionEstimator pot-spike holds are carried to GameState strategy deferral."""
    loop = make_loop(workspace_tmp, monkeypatch, StaticFrameCapture())
    loop._hand_manager._phase = "turn"
    loop._prev_state = create_empty_game_state()
    loop._prev_state.pot = 314
    loop._action_estimator = MagicMock()
    loop._action_estimator.estimate.return_value = {
        "game_event": None,
        "actions": [ActionRecord(seat=5, action="BET", amount=13820)],
        "filtered_pot": 314,
        "pot_spike_hold": True,
    }

    state = loop.process_one_frame()

    assert state is not None
    assert state.pot == 314
    assert state.strategy_defer_reason == "pot_spike_hold"
    assert state.actions_since_last_frame == [
        ActionRecord(seat=5, action="BET", amount=13820),
    ]


def test_obstruction_recovery_pot_decrease_preserved(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Pot decrease is ignored during visual obstruction recovery window."""
    loop = make_loop(workspace_tmp, monkeypatch, StaticFrameCapture())
    loop._hand_manager._phase = "preflop"
    loop._prev_state = create_empty_game_state()
    loop._prev_state.pot = 314
    loop._visual_obstruction_active = False
    loop._visual_obstruction_until = 0.0
    loop._visual_obstruction_recovery_until = time.monotonic() + 10.0
    loop._action_estimator = MagicMock()
    loop._action_estimator.estimate.return_value = {
        "game_event": None,
        "actions": [],
        "filtered_pot": 0,
    }

    with caplog.at_level(logging.INFO, logger="core.game_loop"):
        state = loop.process_one_frame()

    assert state is not None
    assert state.pot == 314
    assert "Pot decrease ignored during visual obstruction/recovery" in caplog.text


def test_no_obstruction_pot_decrease_not_preserved(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pot decrease is NOT blocked when there is no visual obstruction."""
    loop = make_loop(workspace_tmp, monkeypatch, StaticFrameCapture())
    loop._hand_manager._phase = "preflop"
    loop._prev_state = create_empty_game_state()
    loop._prev_state.pot = 314
    loop._visual_obstruction_active = False
    loop._visual_obstruction_until = 0.0
    loop._visual_obstruction_recovery_until = 0.0
    loop._action_estimator = MagicMock()
    loop._action_estimator.estimate.return_value = {
        "game_event": None,
        "actions": [],
        "filtered_pot": 0,
    }

    state = loop.process_one_frame()

    assert state is not None
    assert state.pot == 0


# ---------------------------------------------------------------------------
# Phase 30-Fix32: Showdown guard tests
# ---------------------------------------------------------------------------


def _make_river_state_with_players(
    loop: GameLoop,
    players_in_hand: set[int],
) -> Any:
    """Create a river GameState with 5 board cards and given players."""
    state = create_empty_game_state()
    state.phase = "river"
    state.table_visible = True
    state.board = ["Ah", "Kh", "Qh", "Jh", "Th"]
    state.board_card_count = 5
    state.hero.cards = ["As", "Ks"]
    state.hero.in_current_hand = 1 in players_in_hand

    for seat in range(2, 7):
        if seat in players_in_hand:
            state.players[str(seat)].is_seated = True
            state.players[str(seat)].in_current_hand = True
            state.players[str(seat)].cards_visible = True
            state.players[str(seat)].stack = 5000

    return state


def test_showdown_guard_ignores_fold_badge_river_board5(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fold badge for opponent is ignored during river showdown with 5 board cards."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    hm = loop._hand_manager
    hm._phase = "river"
    hm._players_in_hand = {"1": True, "2": True, "3": True}
    hm._folded_seats = set()

    state = _make_river_state_with_players(loop, {1, 2, 3})
    state.actions_since_last_frame = []

    fold_results = {2: True, 3: False, 4: False, 5: False, 6: False}
    loop._process_fold_badge_detection(state, fold_results)

    # No FOLD action should have been added for seat 2
    fold_actions = [
        a for a in state.actions_since_last_frame if a.action == "FOLD"
    ]
    assert len(fold_actions) == 0, (
        f"Expected 0 FOLD actions during showdown guard, got {len(fold_actions)}"
    )


def test_showdown_guard_prevents_in_current_hand_drop_on_no_card(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Seat card NO_CARD does not drop active player during river showdown."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    hm = loop._hand_manager
    hm._phase = "river"
    hm._players_in_hand = {"1": True, "2": True, "3": True}
    hm._folded_seats = set()
    loop._seat_card_confirmed = set()

    state = _make_river_state_with_players(loop, {1, 2, 3})
    # Seat 2: not folded, not confirmed, NO_CARD detected
    state.players["2"].cards_visible = False

    seat_card_results = {2: False, 3: True, 4: False, 5: False, 6: False}
    loop._apply_seat_card_visibility(state, seat_card_results)

    # Seat 2 should remain in hand despite NO_CARD
    assert state.players["2"].in_current_hand is True, (
        "Seat 2 should remain in_current_hand during showdown guard"
    )


def test_fold_badge_still_effective_on_flop(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fold badge detection works normally on flop (no showdown guard interference)."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    hm = loop._hand_manager
    hm._phase = "flop"
    hm._players_in_hand = {"1": True, "2": True, "3": True}
    hm._folded_seats = set()

    state = create_empty_game_state()
    state.phase = "flop"
    state.table_visible = True
    state.board = ["Ah", "Kh", "Qh"]
    state.board_card_count = 3
    state.hero.cards = ["As", "Ks"]
    state.hero.in_current_hand = True
    state.players["2"].is_seated = True
    state.players["2"].in_current_hand = True
    state.actions_since_last_frame = []

    fold_results = {2: True, 3: False, 4: False, 5: False, 6: False}
    loop._process_fold_badge_detection(state, fold_results)

    # FOLD action should be generated normally on flop
    fold_actions = [
        a for a in state.actions_since_last_frame if a.action == "FOLD"
    ]
    assert len(fold_actions) == 1
    assert fold_actions[0].seat == 2


def test_hand_end_gets_empty_players_in_hand(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_players_in_hand() returns empty set for hand_end and waiting phases."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    hm = loop._hand_manager
    hm._players_in_hand = {"1": True, "2": True, "3": True}

    hm._phase = "hand_end"
    assert hm.get_players_in_hand() == set()

    hm._phase = "waiting"
    assert hm.get_players_in_hand() == set()

    hm._phase = "river"
    assert hm.get_players_in_hand() == {1, 2, 3}


# ---------------------------------------------------------------------------
# Phase 30-Fix33: New hand start guard tests
# ---------------------------------------------------------------------------


def test_new_hand_suppressed_when_board_visible_in_waiting(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """New hand is not started when board cards are still visible in waiting."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "waiting"

    state = create_empty_game_state()
    state.phase = "waiting"
    state.hero.cards = ["Th", "Ts"]
    state.hero.cards_visible = True
    state.board_card_count = 5
    state.pot = 0

    can_start = loop._can_start_new_hand_from_waiting(
        state, state.hero.cards
    )
    assert can_start is False


def test_new_hand_suppressed_when_same_hero_cards(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """New hand is not started when hero cards match last ended hand."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "waiting"
    loop._last_ended_hero_cards = ["Th", "Ts"]

    state = create_empty_game_state()
    state.phase = "waiting"
    state.hero.cards = ["Th", "Ts"]
    state.hero.cards_visible = True
    state.board_card_count = 0
    state.pot = 80

    can_start = loop._can_start_new_hand_from_waiting(
        state, state.hero.cards
    )
    assert can_start is False


def test_new_hand_suppressed_when_pot_too_large(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """New hand is not started when pot exceeds 10 BB during waiting."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "waiting"

    state = create_empty_game_state()
    state.phase = "waiting"
    state.hero.cards = ["7d", "Qd"]
    state.hero.cards_visible = True
    state.board_card_count = 0
    state.pot = 20336  # >> 10 BB (1000)

    can_start = loop._can_start_new_hand_from_waiting(
        state, state.hero.cards
    )
    assert can_start is False


def test_new_hand_allowed_with_clean_state(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Normal new hand with different cards, no board, and reasonable pot starts."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "waiting"
    loop._last_ended_hero_cards = ["Th", "Ts"]

    state = create_empty_game_state()
    state.phase = "waiting"
    state.hero.cards = ["7d", "Qd"]
    state.hero.cards_visible = True
    state.board_card_count = 0
    state.pot = 80

    can_start = loop._can_start_new_hand_from_waiting(
        state, state.hero.cards
    )
    assert can_start is True


# ---------------------------------------------------------------------------
# Phase 30-Fix35: Rejoin resilience tests
# ---------------------------------------------------------------------------


def test_rejoin_allowed_from_last_seat_card_state(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rejoin succeeds when the last known seat-card state was True."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager.rejoin_seat = MagicMock(return_value=True)

    loop._last_seat_card_states = {3: True}

    result = loop.request_rejoin_seat(3)

    assert result is True
    loop._hand_manager.rejoin_seat.assert_called_once_with(
        3,
        allow_folded_rejoin=True,
    )


def test_rejoin_allowed_from_confirmed_cache(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rejoin succeeds when the seat is in the confirmed cache."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager.rejoin_seat = MagicMock(return_value=True)

    loop._last_seat_card_states = {}
    loop._seat_card_confirmed = {3}

    result = loop.request_rejoin_seat(3)

    assert result is True
    loop._hand_manager.rejoin_seat.assert_called_once_with(
        3,
        allow_folded_rejoin=True,
    )


def test_rejoin_succeeds_on_retry(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rejoin retries up to 3 times; one positive detection succeeds."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager.rejoin_seat = MagicMock(return_value=True)
    loop._last_seat_card_states = {}
    loop._seat_card_confirmed = set()

    # First two attempts fail, third succeeds
    call_count = [0]

    def detect_all_side_effect(_frame: Any) -> dict[int, bool]:
        call_count[0] += 1
        if call_count[0] >= 3:
            return {3: True}
        return {3: False}

    frame_mock = object()
    capture_mock = MagicMock()
    capture_mock.get_frame.return_value = frame_mock
    loop._capture = capture_mock
    loop._seat_card_detector.detect_all = MagicMock(
        side_effect=detect_all_side_effect,
    )

    result = loop.request_rejoin_seat(3)

    assert result is True
    assert call_count[0] == 3
    loop._hand_manager.rejoin_seat.assert_called_once_with(
        3,
        allow_folded_rejoin=True,
    )


def test_rejoin_rejected_after_all_retries_fail(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rejoin is rejected when no card is detected after 3 retries."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._last_seat_card_states = {}
    loop._seat_card_confirmed = set()

    frame_mock = object()
    capture_mock = MagicMock()
    capture_mock.get_frame.return_value = frame_mock
    loop._capture = capture_mock
    loop._seat_card_detector.detect_all = MagicMock(
        return_value={3: False},
    )

    result = loop.request_rejoin_seat(3)

    assert result is False
    assert loop._seat_card_detector.detect_all.call_count == 3


def test_low_confidence_opponent_fold_ignored_for_recent_card(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Low-confidence opponent FOLD is ignored after recent card sighting."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "flop"
    loop._hand_manager._players_in_hand = {"1": True, "4": True}
    loop._last_seat_card_states = {4: True}
    state = create_empty_game_state()
    state.phase = "flop"
    state.players["4"].in_current_hand = True
    action = ActionRecord(seat=4, action="FOLD", amount=0, confidence="low")

    filtered = loop._filter_low_confidence_opponent_folds(state, [action])

    assert filtered == []


def test_low_confidence_opponent_fold_ignored_during_obstruction(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Visual obstruction/recovery suppresses weak opponent FOLD actions."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    monkeypatch.setattr(loop, "_is_visual_obstruction_protected", lambda: True)
    state = create_empty_game_state()
    action = ActionRecord(seat=5, action="FOLD", amount=0, confidence="low")

    filtered = loop._filter_low_confidence_opponent_folds(state, [action])

    assert filtered == []


def test_high_confidence_opponent_fold_is_preserved(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fold-badge high-confidence opponent FOLD remains actionable."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._last_seat_card_states = {4: True}
    state = create_empty_game_state()
    action = ActionRecord(seat=4, action="FOLD", amount=0, confidence="high")

    filtered = loop._filter_low_confidence_opponent_folds(state, [action])

    assert filtered == [action]


def test_low_confidence_hero_fold_is_preserved(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Opponent low-confidence filter does not apply to Hero FOLD."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    monkeypatch.setattr(loop, "_is_visual_obstruction_protected", lambda: True)
    state = create_empty_game_state()
    action = ActionRecord(seat=1, action="FOLD", amount=0, confidence="low")

    filtered = loop._filter_low_confidence_opponent_folds(state, [action])

    assert filtered == [action]


# ---------------------------------------------------------------------------
# Phase 30-Fix39: Visual obstruction recovery window & in_current_hand guards
# ---------------------------------------------------------------------------


def test_obstruction_active_keeps_in_current_hand(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Visual obstruction active: in_current_hand=True is preserved for active seat."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "flop"
    loop._hand_manager._players_in_hand = {"1": True, "2": True, "3": True}
    loop._hand_manager._folded_seats = set()
    loop._seat_card_confirmed = set()
    loop._visual_obstruction_active = True
    loop._visual_obstruction_until = time.monotonic() + 10.0

    state = create_empty_game_state()
    state.players["3"].is_seated = True
    state.players["3"].in_current_hand = True
    state.players["3"].cards_visible = False

    loop._apply_seat_card_visibility(state, {3: False})

    assert state.players["3"].in_current_hand is True


def test_recovery_window_keeps_in_current_hand(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recovery window after obstruction: in_current_hand=True is preserved."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "flop"
    loop._hand_manager._players_in_hand = {"1": True, "2": True, "3": True}
    loop._hand_manager._folded_seats = set()
    loop._seat_card_confirmed = set()
    loop._visual_obstruction_active = False
    loop._visual_obstruction_until = 0.0
    loop._visual_obstruction_recovery_until = time.monotonic() + 10.0

    state = create_empty_game_state()
    state.players["3"].is_seated = True
    state.players["3"].in_current_hand = True
    state.players["3"].cards_visible = False

    loop._apply_seat_card_visibility(state, {3: False})

    assert state.players["3"].in_current_hand is True


def test_players_in_hand_true_keeps_in_current_hand(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Seat in hand_manager.players_in_hand=True keeps in_current_hand despite NO_CARD."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "flop"
    loop._hand_manager._players_in_hand = {"1": True, "2": True, "3": True}
    loop._hand_manager._folded_seats = set()
    loop._seat_card_confirmed = set()

    state = create_empty_game_state()
    state.players["3"].is_seated = True
    state.players["3"].in_current_hand = True
    state.players["3"].cards_visible = False

    loop._apply_seat_card_visibility(state, {3: False})

    assert state.players["3"].in_current_hand is True


def test_participant_observed_keeps_in_current_hand(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Seat in participant_observed_seats keeps in_current_hand despite NO_CARD."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "flop"
    loop._hand_manager._players_in_hand = {"1": True, "2": True}
    loop._hand_manager._folded_seats = set()
    loop._seat_card_confirmed = set()
    loop._hand_manager._participant_observed_seats = {"3"}

    state = create_empty_game_state()
    state.players["3"].is_seated = True
    state.players["3"].in_current_hand = True
    state.players["3"].cards_visible = False

    loop._apply_seat_card_visibility(state, {3: False})

    assert state.players["3"].in_current_hand is True


def test_participated_seats_keeps_in_current_hand(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Seat in participated_seats keeps in_current_hand despite NO_CARD."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "flop"
    loop._hand_manager._players_in_hand = {"1": True, "2": True}
    loop._hand_manager._folded_seats = set()
    loop._seat_card_confirmed = set()
    loop._hand_manager._participated_seats = {"3"}

    state = create_empty_game_state()
    state.players["3"].is_seated = True
    state.players["3"].in_current_hand = True
    state.players["3"].cards_visible = False

    loop._apply_seat_card_visibility(state, {3: False})

    assert state.players["3"].in_current_hand is True


def test_obstruction_fold_badge_ignored(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FoldBadge is ignored during visual obstruction protection (active + recovery)."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._visual_obstruction_active = True
    loop._visual_obstruction_until = time.monotonic() + 10.0
    loop._hand_manager._phase = "flop"
    loop._hand_manager._players_in_hand = {"1": True, "2": True, "3": True}
    game_state = _state_with_player("3")

    loop._process_fold_badge_detection(game_state, {3: True})

    assert game_state.actions_since_last_frame == []


def test_recovery_window_fold_badge_ignored(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FoldBadge is ignored during recovery window after obstruction."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._visual_obstruction_active = False
    loop._visual_obstruction_until = 0.0
    loop._visual_obstruction_recovery_until = time.monotonic() + 10.0
    loop._hand_manager._phase = "flop"
    loop._hand_manager._players_in_hand = {"1": True, "2": True, "3": True}
    game_state = _state_with_player("3")

    loop._process_fold_badge_detection(game_state, {3: True})

    assert game_state.actions_since_last_frame == []


def test_normal_fold_badge_still_works(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Normal FoldBadge (no obstruction) still generates FOLD action."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "flop"
    loop._hand_manager._players_in_hand = {"1": True, "2": True, "3": True}
    game_state = _state_with_player("3")

    loop._process_fold_badge_detection(game_state, {3: True})

    assert len(game_state.actions_since_last_frame) == 1
    assert game_state.actions_since_last_frame[0].seat == 3
    assert game_state.actions_since_last_frame[0].action == "FOLD"


def test_hand_end_phase_no_force(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """hand_end phase: in_current_hand forcing is not applied."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "hand_end"
    loop._hand_manager._players_in_hand = {}
    loop._hand_manager._folded_seats = set()
    loop._seat_card_confirmed = set()

    state = create_empty_game_state()
    state.players["3"].is_seated = True
    state.players["3"].in_current_hand = True
    state.players["3"].cards_visible = False

    loop._apply_seat_card_visibility(state, {3: False})

    assert state.players["3"].in_current_hand is True


def test_waiting_phase_no_force(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """waiting phase: in_current_hand forcing is not applied."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "waiting"
    loop._hand_manager._players_in_hand = set()
    loop._hand_manager._folded_seats = set()
    loop._seat_card_confirmed = set()

    state = create_empty_game_state()
    state.players["3"].is_seated = True
    state.players["3"].in_current_hand = True
    state.players["3"].cards_visible = False

    loop._apply_seat_card_visibility(state, {3: False})

    assert state.players["3"].in_current_hand is True


def test_unprotected_no_card_still_forces_in_current_hand_false(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unprotected seat (not in hand, not participant, no obstruction) still forced NO."""
    loop = make_loop(workspace_tmp, monkeypatch, NoneCapture())
    loop._hand_manager._phase = "flop"
    loop._hand_manager._players_in_hand = {"1": True, "2": True}
    loop._hand_manager._folded_seats = set()
    loop._seat_card_confirmed = set()
    # Seat 3 is NOT in players_in_hand, NOT participant, NOT confirmed
    loop._hand_manager._participated_seats = set()
    loop._hand_manager._participant_observed_seats = set()

    state = create_empty_game_state()
    state.players["3"].is_seated = True
    state.players["3"].in_current_hand = True
    state.players["3"].cards_visible = False

    loop._apply_seat_card_visibility(state, {3: False})

    assert state.players["3"].in_current_hand is False
