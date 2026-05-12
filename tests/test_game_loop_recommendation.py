"""Tests for GameLoop recommendation replay persistence."""

import logging
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest

from core.game_loop import GameLoop
from core.game_state import ActionRecord, GameState, create_empty_game_state
from core.hand_manager import HandManager, StreetActions
from strategy.recommendation_engine import Recommendation


def _make_loop(hand_manager: Any, recommendation_engine: Any = None) -> GameLoop:
    """Create a minimally initialized GameLoop for strategy tests."""
    hand_manager.get_players_in_hand.return_value = {1, 2}
    loop = GameLoop.__new__(GameLoop)
    loop._recommendation_engine = recommendation_engine
    loop._hand_manager = hand_manager
    loop._last_recommendation_log = None
    loop._previous_recommendation = None
    loop._previous_recommendation_context = None
    loop._last_strategy_phase = None
    loop._last_strategy_is_my_turn = False
    loop._hud_callback = MagicMock()
    loop._hud_computing_callback = None
    return loop


def _make_loop_for_postflop(
    hand_manager: Any,
    recommendation_engine: Any = None,
) -> GameLoop:
    """Create a GameLoop pre-populated for postflop strategy tests."""
    loop = _make_loop(hand_manager, recommendation_engine)
    loop._save_recommendation_to_hand_manager = MagicMock()
    loop._save_human_action_to_hand_manager = MagicMock()
    loop._notify_hud = MagicMock()
    loop._notify_hud_computing = MagicMock()
    loop._log_recommendation = MagicMock()
    loop._log_recommendation_change = MagicMock()
    loop._revalidate_seat_cards_before_strategy = MagicMock()
    loop._get_opponent_stats_for_strategy = MagicMock(return_value={})
    loop._recommendation_generate_accepts_keywords = MagicMock(return_value=True)
    loop._get_preflop_actions_for_strategy = MagicMock(return_value=[])
    loop._guard_postflop_recommendation_source = MagicMock(
        side_effect=lambda rec, gs, ph, ctx: rec
    )
    loop._apply_action_constraints_to_recommendation = MagicMock(return_value=False)
    return loop


def _state(phase: str = "preflop", is_my_turn: bool = True) -> GameState:
    """Return a strategy-ready GameState."""
    state = create_empty_game_state()
    state.phase = phase
    state.hero.is_my_turn = is_my_turn
    state.hero.in_current_hand = True
    state.active_player_count = 2
    return state


def test_handle_strategy_saves_generated_recommendation_to_hand_manager() -> None:
    """Generated recommendations are stored for replay output."""
    hand_manager = MagicMock()
    hand_manager.phase = "preflop"
    recommendation = Recommendation(
        action="RAISE",
        amount=300,
        strategy_source="preflop_chart",
        latency_breakdown={"preflop_chart_ms": 1.5},
    )
    recommendation_engine = MagicMock()
    recommendation_engine.generate.return_value = recommendation
    loop = _make_loop(hand_manager, recommendation_engine)

    loop._handle_strategy(_state())

    hand_manager.set_recommendation.assert_called_once()
    _, kwargs = hand_manager.set_recommendation.call_args
    assert kwargs["recommendation"] == "RAISE 300"
    assert kwargs["time_to_recommend_ms"] > 0
    assert kwargs["latency_breakdown"] == {"preflop_chart_ms": 1.5}


def test_handle_strategy_saves_fold_recommendation_without_amount() -> None:
    """Zero-amount recommendations use action-only replay text."""
    hand_manager = MagicMock()
    hand_manager.phase = "preflop"
    recommendation = Recommendation(action="FOLD", amount=0, strategy_source="preflop_chart")
    recommendation_engine = MagicMock()
    recommendation_engine.generate.return_value = recommendation
    loop = _make_loop(hand_manager, recommendation_engine)

    loop._handle_strategy(_state())

    _, kwargs = hand_manager.set_recommendation.call_args
    assert kwargs["recommendation"] == "FOLD"
    assert isinstance(kwargs["latency_breakdown"], dict)


def test_handle_strategy_saves_hero_action_to_hand_manager() -> None:
    """Explicit hero actions on GameState are passed to HandManager."""
    hand_manager = MagicMock()
    hand_manager.phase = "flop"
    loop = _make_loop(hand_manager)
    state = _state(phase="flop", is_my_turn=False)
    state.hero_action = ActionRecord(seat=1, action="CALL", amount=200)

    loop._handle_strategy(state)

    hand_manager.set_human_action.assert_called_once_with("CALL 200")


def test_hand_manager_set_human_action_marks_followed_recommendation() -> None:
    """Human action matching the recommended action marks the street as followed."""
    manager = HandManager({"game": {}, "db": {"path": ":memory:"}}, db_path=":memory:")
    manager._phase = "flop"
    manager._street_actions = {"flop": StreetActions(street="flop")}

    manager.set_recommendation(
        "CALL 200",
        time_to_recommend_ms=12.3,
        latency_breakdown={"solver_ms": 10.0, "total_ms": 12.3},
    )
    manager.set_human_action("CALL 200")

    street = manager.get_current_street_actions()
    assert street is not None
    assert street.recommendation == "CALL 200"
    assert street.human_action == "CALL 200"
    assert street.followed_recommendation is True


def test_replay_json_includes_recommendation_fields() -> None:
    """Replay JSON includes recommendation metadata captured on a street."""
    manager = HandManager({"game": {}, "db": {"path": ":memory:"}}, db_path=":memory:")
    manager._hand_id = 1
    manager._hero_cards = ["Ah", "Kd"]
    manager._street_actions = {"preflop": StreetActions(street="preflop")}
    manager._phase = "preflop"
    manager.set_recommendation(
        "RAISE 300",
        time_to_recommend_ms=8.5,
        latency_breakdown={"preflop_chart_ms": 2.0, "total_ms": 8.5},
    )
    manager.set_human_action("RAISE 300")

    replay = manager._build_replay_json(datetime.now(timezone.utc))
    preflop = replay["streets"]["preflop"]

    assert preflop["recommendation"] == "RAISE 300"
    assert preflop["human_action"] == "RAISE 300"
    assert preflop["followed_recommendation"] is True
    assert preflop["time_to_recommend_ms"] == 8.5
    assert preflop["latency_breakdown"] == {
        "preflop_chart_ms": 2.0,
        "total_ms": 8.5,
    }


# ---------------------------------------------------------------------------
# Snapshot and freshness check unit tests
# ---------------------------------------------------------------------------


def test_build_recommendation_context_snapshot_captures_decision_point() -> None:
    """Snapshot captures the key fields of the current decision point."""
    state = create_empty_game_state()
    state.phase = "flop"
    state.hand_id = 42
    state.board = ["2h", "3d", "5c"]
    state.pot = 1500
    state.active_player_count = 2
    state.hero.is_my_turn = True
    state.hero.in_current_hand = True
    state.current_street_actions = [
        ActionRecord(seat=2, action="CHECK", amount=0),
    ]

    snapshot = GameLoop._build_recommendation_context_snapshot(state)

    assert snapshot["hand_id"] == 42
    assert snapshot["phase"] == "flop"
    assert snapshot["board"] == ("2h", "3d", "5c")
    assert snapshot["board_count"] == 3
    assert snapshot["pot"] == 1500
    assert snapshot["active_player_count"] == 2
    assert snapshot["current_street_actions_count"] == 1
    assert snapshot["hero_is_my_turn"] is True
    assert snapshot["hero_in_current_hand"] is True


def test_build_snapshot_handles_none_board() -> None:
    """Snapshot handles board=None gracefully."""
    state = create_empty_game_state()
    state.board = None  # type: ignore[assignment]

    snapshot = GameLoop._build_recommendation_context_snapshot(state)

    assert snapshot["board"] == ()
    assert snapshot["board_count"] == 0


def test_is_recommendation_context_still_valid_returns_true_when_unchanged() -> None:
    """Context is valid when nothing changed since the snapshot."""
    state = create_empty_game_state()
    state.phase = "flop"
    state.hand_id = 1
    state.board = ["2h", "3d", "5c"]
    state.active_player_count = 2
    state.hero.is_my_turn = True
    state.hero.in_current_hand = True

    snapshot = GameLoop._build_recommendation_context_snapshot(state)

    assert GameLoop._is_recommendation_context_still_valid(snapshot, state) is True


def test_is_recommendation_context_still_valid_phase_changed() -> None:
    """Context is invalid when phase changed."""
    state = create_empty_game_state()
    state.phase = "flop"
    state.board = ["2h", "3d", "5c"]
    state.hero.is_my_turn = True
    state.hero.in_current_hand = True

    snapshot = GameLoop._build_recommendation_context_snapshot(state)
    state.phase = "turn"

    assert GameLoop._is_recommendation_context_still_valid(snapshot, state) is False


def test_is_recommendation_context_still_valid_hand_id_changed() -> None:
    """Context is invalid when hand_id changed."""
    state = create_empty_game_state()
    state.phase = "flop"
    state.hand_id = 1
    state.board = ["2h", "3d", "5c"]
    state.hero.is_my_turn = True
    state.hero.in_current_hand = True

    snapshot = GameLoop._build_recommendation_context_snapshot(state)
    state.hand_id = 2

    assert GameLoop._is_recommendation_context_still_valid(snapshot, state) is False


def test_is_recommendation_context_still_valid_board_count_changed() -> None:
    """Context is invalid when board card count changed (new street)."""
    state = create_empty_game_state()
    state.phase = "flop"
    state.board = ["2h", "3d", "5c"]
    state.hero.is_my_turn = True
    state.hero.in_current_hand = True

    snapshot = GameLoop._build_recommendation_context_snapshot(state)
    state.board = ["2h", "3d", "5c", "7s"]

    assert GameLoop._is_recommendation_context_still_valid(snapshot, state) is False


def test_is_recommendation_context_still_valid_hero_not_my_turn() -> None:
    """Context is invalid when hero is no longer to act."""
    state = create_empty_game_state()
    state.phase = "flop"
    state.board = ["2h", "3d", "5c"]
    state.hero.is_my_turn = True
    state.hero.in_current_hand = True

    snapshot = GameLoop._build_recommendation_context_snapshot(state)
    state.hero.is_my_turn = False

    assert GameLoop._is_recommendation_context_still_valid(snapshot, state) is False


def test_is_recommendation_context_still_valid_hero_not_in_hand() -> None:
    """Context is invalid when hero is no longer in the hand."""
    state = create_empty_game_state()
    state.phase = "flop"
    state.board = ["2h", "3d", "5c"]
    state.hero.is_my_turn = True
    state.hero.in_current_hand = True

    snapshot = GameLoop._build_recommendation_context_snapshot(state)
    state.hero.in_current_hand = False

    assert GameLoop._is_recommendation_context_still_valid(snapshot, state) is False


def test_is_recommendation_context_still_valid_waiting_phase() -> None:
    """Context is invalid when phase transitions to waiting."""
    state = create_empty_game_state()
    state.phase = "flop"
    state.board = ["2h", "3d", "5c"]
    state.hero.is_my_turn = True
    state.hero.in_current_hand = True

    snapshot = GameLoop._build_recommendation_context_snapshot(state)
    state.phase = "waiting"

    assert GameLoop._is_recommendation_context_still_valid(snapshot, state) is False


def test_is_recommendation_context_still_valid_actions_count_changed() -> None:
    """Context is invalid when street actions changed (opponent acted)."""
    state = create_empty_game_state()
    state.phase = "flop"
    state.board = ["2h", "3d", "5c"]
    state.hero.is_my_turn = True
    state.hero.in_current_hand = True
    state.current_street_actions = [
        ActionRecord(seat=2, action="CHECK", amount=0),
    ]

    snapshot = GameLoop._build_recommendation_context_snapshot(state)
    state.current_street_actions = [
        ActionRecord(seat=2, action="CHECK", amount=0),
        ActionRecord(seat=3, action="BET", amount=500),
    ]

    assert GameLoop._is_recommendation_context_still_valid(snapshot, state) is False


def test_pot_change_does_not_invalidate_context() -> None:
    """Pot changes alone do not invalidate the context (OCR noise tolerance)."""
    state = create_empty_game_state()
    state.phase = "flop"
    state.board = ["2h", "3d", "5c"]
    state.pot = 1000
    state.hero.is_my_turn = True
    state.hero.in_current_hand = True

    snapshot = GameLoop._build_recommendation_context_snapshot(state)
    state.pot = 1200  # Pot changed by opponent bet, but not in actions yet

    # Pot is not checked, so context is still valid
    # (other fields like actions_count will catch real changes)
    assert GameLoop._is_recommendation_context_still_valid(snapshot, state) is True


# ---------------------------------------------------------------------------
# Integration tests: _handle_strategy stale-discard and mismatch guard
# ---------------------------------------------------------------------------


def test_stale_postflop_recommendation_discarded_after_generation() -> None:
    """Recommendation is not saved/HUD-displayed when context changed during solve."""
    hand_manager = MagicMock()
    hand_manager.phase = "flop"
    hand_manager.get_players_in_hand.return_value = {1, 2}

    recommendation = Recommendation(
        action="CHECK", amount=0, strategy_source="fallback"
    )
    recommendation_engine = MagicMock()
    recommendation_engine.generate.return_value = recommendation

    loop = _make_loop_for_postflop(hand_manager, recommendation_engine)
    # Simulate context change during solver (freshness returns False)
    loop._is_recommendation_context_still_valid = MagicMock(return_value=False)
    loop._build_recommendation_context_snapshot = MagicMock(
        return_value={"phase": "flop", "board_count": 3}
    )

    state = _state(phase="flop", is_my_turn=True)
    state.board = ["2h", "3d", "5c"]

    loop._handle_strategy(state)

    # Must NOT save recommendation
    loop._save_recommendation_to_hand_manager.assert_not_called()
    # Must NOT update HUD with recommendation
    loop._notify_hud.assert_not_called()
    # Must NOT update _previous_recommendation
    assert loop._previous_recommendation is None
    assert loop._previous_recommendation_context is None


def test_stale_recommendation_logged_on_discard(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Stale recommendation discard emits INFO log."""
    hand_manager = MagicMock()
    hand_manager.phase = "flop"
    hand_manager.get_players_in_hand.return_value = {1, 2}

    recommendation = Recommendation(
        action="CHECK", amount=0, strategy_source="fallback"
    )
    recommendation_engine = MagicMock()
    recommendation_engine.generate.return_value = recommendation

    loop = _make_loop_for_postflop(hand_manager, recommendation_engine)
    loop._is_recommendation_context_still_valid = MagicMock(return_value=False)
    loop._build_recommendation_context_snapshot = MagicMock(
        return_value={"phase": "flop", "board_count": 3}
    )

    state = _state(phase="flop", is_my_turn=True)
    state.board = ["2h", "3d", "5c"]

    with caplog.at_level(logging.INFO, logger="core.game_loop"):
        loop._handle_strategy(state)

    assert "Stale recommendation discarded" in caplog.text


def test_strategy_skipped_on_phase_board_count_mismatch() -> None:
    """Strategy is skipped when phase implies N board cards but count differs."""
    hand_manager = MagicMock()
    hand_manager.phase = "flop"
    hand_manager.get_players_in_hand.return_value = {1, 2}

    recommendation_engine = MagicMock()
    loop = _make_loop_for_postflop(hand_manager, recommendation_engine)

    state = _state(phase="flop", is_my_turn=True)
    state.board = ["2h", "3d", "5c", "7s", "9d"]  # 5 board cards on flop!

    loop._handle_strategy(state)

    # Must NOT call generate
    recommendation_engine.generate.assert_not_called()
    # Must NOT save recommendation
    loop._save_recommendation_to_hand_manager.assert_not_called()


def test_phase_board_mismatch_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Phase/board_count mismatch emits WARNING log."""
    hand_manager = MagicMock()
    hand_manager.phase = "flop"
    hand_manager.get_players_in_hand.return_value = {1, 2}

    recommendation_engine = MagicMock()
    loop = _make_loop_for_postflop(hand_manager, recommendation_engine)

    state = _state(phase="flop", is_my_turn=True)
    state.board = ["2h", "3d", "5c", "7s", "9d"]

    with caplog.at_level(logging.WARNING, logger="core.game_loop"):
        loop._handle_strategy(state)

    assert "phase/board_count mismatch" in caplog.text


def test_strategy_skipped_turn_board_count_3() -> None:
    """Strategy is skipped when turn phase has only 3 board cards."""
    hand_manager = MagicMock()
    hand_manager.phase = "turn"
    hand_manager.get_players_in_hand.return_value = {1, 2}

    recommendation_engine = MagicMock()
    loop = _make_loop_for_postflop(hand_manager, recommendation_engine)

    state = _state(phase="turn", is_my_turn=True)
    state.board = ["2h", "3d", "5c"]  # 3 cards, turn expects 4

    loop._handle_strategy(state)

    recommendation_engine.generate.assert_not_called()
    loop._save_recommendation_to_hand_manager.assert_not_called()


def test_strategy_skipped_river_board_count_4() -> None:
    """Strategy is skipped when river phase has only 4 board cards."""
    hand_manager = MagicMock()
    hand_manager.phase = "river"
    hand_manager.get_players_in_hand.return_value = {1, 2}

    recommendation_engine = MagicMock()
    loop = _make_loop_for_postflop(hand_manager, recommendation_engine)

    state = _state(phase="river", is_my_turn=True)
    state.board = ["2h", "3d", "5c", "7s"]  # 4 cards, river expects 5

    loop._handle_strategy(state)

    recommendation_engine.generate.assert_not_called()
    loop._save_recommendation_to_hand_manager.assert_not_called()


def test_valid_turn_recommendation_proceeds() -> None:
    """Turn with 4 board cards proceeds to generate recommendation."""
    hand_manager = MagicMock()
    hand_manager.phase = "turn"
    hand_manager.get_players_in_hand.return_value = {1, 2}

    recommendation = Recommendation(
        action="BET", amount=700, strategy_source="solver"
    )
    recommendation_engine = MagicMock()
    recommendation_engine.generate.return_value = recommendation

    loop = _make_loop_for_postflop(hand_manager, recommendation_engine)
    loop._is_recommendation_context_still_valid = MagicMock(return_value=True)
    loop._build_recommendation_context_snapshot = MagicMock(
        return_value={"phase": "turn", "board_count": 4}
    )

    state = _state(phase="turn", is_my_turn=True)
    state.board = ["2h", "3d", "5c", "7s"]

    loop._handle_strategy(state)

    loop._save_recommendation_to_hand_manager.assert_called_once()
    assert loop._previous_recommendation is recommendation


def test_valid_river_recommendation_proceeds() -> None:
    """River with 5 board cards proceeds to generate recommendation."""
    hand_manager = MagicMock()
    hand_manager.phase = "river"
    hand_manager.get_players_in_hand.return_value = {1, 2}

    recommendation = Recommendation(
        action="BET", amount=1000, strategy_source="solver"
    )
    recommendation_engine = MagicMock()
    recommendation_engine.generate.return_value = recommendation

    loop = _make_loop_for_postflop(hand_manager, recommendation_engine)
    loop._is_recommendation_context_still_valid = MagicMock(return_value=True)
    loop._build_recommendation_context_snapshot = MagicMock(
        return_value={"phase": "river", "board_count": 5}
    )

    state = _state(phase="river", is_my_turn=True)
    state.board = ["2h", "3d", "5c", "7s", "9d"]

    loop._handle_strategy(state)

    loop._save_recommendation_to_hand_manager.assert_called_once()
    assert loop._previous_recommendation is recommendation


def test_valid_postflop_recommendation_saved_normally() -> None:
    """Valid recommendation goes through normal save/HUD/previous flow."""
    hand_manager = MagicMock()
    hand_manager.phase = "flop"
    hand_manager.get_players_in_hand.return_value = {1, 2}

    recommendation = Recommendation(
        action="BET", amount=500, strategy_source="solver"
    )
    recommendation_engine = MagicMock()
    recommendation_engine.generate.return_value = recommendation

    loop = _make_loop_for_postflop(hand_manager, recommendation_engine)
    # Freshness returns True (context unchanged)
    loop._is_recommendation_context_still_valid = MagicMock(return_value=True)
    loop._build_recommendation_context_snapshot = MagicMock(
        return_value={"phase": "flop", "board_count": 3}
    )

    state = _state(phase="flop", is_my_turn=True)
    state.board = ["2h", "3d", "5c"]

    loop._handle_strategy(state)

    # Must save recommendation
    loop._save_recommendation_to_hand_manager.assert_called_once()
    # Must update _previous_recommendation
    assert loop._previous_recommendation is recommendation
    # Must update context snapshot
    assert loop._previous_recommendation_context is not None


def test_cached_recommendation_discarded_when_context_changed() -> None:
    """Cached recommendation is cleared when game context no longer valid."""
    hand_manager = MagicMock()
    hand_manager.phase = "turn"
    hand_manager.get_players_in_hand.return_value = {1, 2}

    recommendation_engine = MagicMock()
    # The cached freshness guard will discard, then fall to first-time calc
    recommendation_engine.generate.return_value = Recommendation(
        action="BET", amount=500, strategy_source="solver"
    )

    loop = _make_loop_for_postflop(hand_manager, recommendation_engine)
    loop._is_recommendation_context_still_valid = MagicMock(return_value=False)
    loop._build_recommendation_context_snapshot = MagicMock(
        return_value={"phase": "turn", "board_count": 4}
    )

    # Set up a stale cached recommendation (saved during flop)
    loop._last_strategy_is_my_turn = True
    loop._previous_recommendation = Recommendation(
        action="CHECK", amount=0, strategy_source="fallback"
    )
    loop._previous_recommendation_context = {
        "phase": "flop",
        "board_count": 3,
    }

    state = _state(phase="turn", is_my_turn=True)
    state.board = ["2h", "3d", "5c", "7s"]

    loop._handle_strategy(state)

    # Cached recommendation should be cleared before constraint re-apply
    # The _is_recommendation_context_still_valid returns False,
    # so the first-time path is entered instead.
    # Verify generate was called (first-time path)
    recommendation_engine.generate.assert_called_once()

    # (The stale CHECK is not applied as FOLD via constraints on new street)


def test_cached_recommendation_invalid_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Cached recommendation discard logs INFO."""
    hand_manager = MagicMock()
    hand_manager.phase = "turn"
    hand_manager.get_players_in_hand.return_value = {1, 2}

    recommendation_engine = MagicMock()
    recommendation_engine.generate.return_value = Recommendation(
        action="BET", amount=500, strategy_source="solver"
    )

    loop = _make_loop_for_postflop(hand_manager, recommendation_engine)
    loop._is_recommendation_context_still_valid = MagicMock(return_value=False)
    loop._build_recommendation_context_snapshot = MagicMock(
        return_value={"phase": "turn", "board_count": 4}
    )

    loop._last_strategy_is_my_turn = True
    loop._previous_recommendation = Recommendation(
        action="CHECK", amount=0, strategy_source="fallback"
    )
    loop._previous_recommendation_context = {"phase": "flop", "board_count": 3}

    state = _state(phase="turn", is_my_turn=True)
    state.board = ["2h", "3d", "5c", "7s"]

    with caplog.at_level(logging.INFO, logger="core.game_loop"):
        loop._handle_strategy(state)

    assert "Cached recommendation discarded" in caplog.text
