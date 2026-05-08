"""Tests for GameLoop recommendation replay persistence."""

from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

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
    loop._last_strategy_phase = None
    loop._last_strategy_is_my_turn = False
    loop._hud_callback = MagicMock()
    loop._hud_computing_callback = None
    return loop


def _state(phase: str = "preflop", is_my_turn: bool = True) -> GameState:
    """Return a strategy-ready GameState."""
    state = create_empty_game_state()
    state.phase = phase
    state.hero.is_my_turn = is_my_turn
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
