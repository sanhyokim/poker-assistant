"""Tests for the multiway decision engine."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from core.game_state import GameState, HeroState, PlayerState
from strategy.llm_pipeline import LLMPipeline
from strategy.multiway_engine import MultiwayEngine


TEST_CONFIG = {"game": {"blind_bb": 100}, "preflop_delta": {"sample_threshold_low": 50}}


def make_engine(config: dict | None = None) -> MultiwayEngine:
    """Create a MultiwayEngine with a mocked LLM pipeline."""
    llm = MagicMock(spec=LLMPipeline)
    engine = MultiwayEngine(llm, config or TEST_CONFIG)
    engine.mc_samples = 2000
    return engine


def make_state(
    hero_cards: list[str] | None = None,
    board: list[str] | None = None,
) -> GameState:
    """Create a flop GameState with three active players."""
    players = GameState.create_default_players()
    players["2"] = PlayerState(
        name="p2",
        stack=4000,
        is_seated=True,
        in_current_hand=True,
    )
    players["3"] = PlayerState(
        name="p3",
        stack=3000,
        is_seated=True,
        in_current_hand=True,
    )
    return GameState(
        phase="flop",
        hero=HeroState(
            seat=1,
            position="BTN",
            cards=hero_cards or ["Ah", "Kh"],
            stack=5000,
            bet=0,
            is_my_turn=True,
        ),
        board=board or ["Th", "7d", "2c"],
        board_card_count=len(board or ["Th", "7d", "2c"]),
        pot=600,
        players=players,
        dealer_seat=1,
        active_player_count=3,
    )


def test_calculate_equity_basic() -> None:
    """AA against one random opponent has high equity."""
    engine = make_engine()

    equity = engine.calculate_equity(["Ah", "As"], ["Td", "7c", "2h"], 1)

    assert 0.7 < equity < 1.0


def test_calculate_equity_weak_hand() -> None:
    """72o against one random opponent has low equity on a neutral flop."""
    engine = make_engine()

    equity = engine.calculate_equity(["7h", "2c"], ["Kd", "9s", "4h"], 1)

    assert equity < 0.4


def test_calculate_equity_multiway_3() -> None:
    """Equity is lower against two opponents than against one opponent."""
    engine = make_engine()

    heads_up = engine.calculate_equity(["Ah", "As"], ["Td", "7c", "2h"], 1)
    multiway = engine.calculate_equity(["Ah", "As"], ["Td", "7c", "2h"], 2)

    assert multiway < heads_up


def test_calculate_equity_full_board() -> None:
    """Equity calculation works on a complete river board."""
    engine = make_engine()

    equity = engine.calculate_equity(
        ["Ah", "As"],
        ["Td", "7c", "2h", "3s", "4d"],
        1,
    )

    assert 0.0 <= equity <= 1.0


def test_calculate_equity_invalid_cards() -> None:
    """Invalid card strings return neutral equity without raising."""
    engine = make_engine()

    assert engine.calculate_equity(["bad", "As"], ["Td", "7c", "2h"], 1) == 0.5


def test_evaluate_with_llm_success() -> None:
    """evaluate() returns the LLM action when LLM succeeds."""
    engine = make_engine()
    engine.llm.decide_multiway.return_value = {
        "action": "bet",
        "size": "60%",
        "confidence": "medium",
        "reasoning": "Strong draw with high equity",
    }

    result = engine.evaluate(make_state(), [{"vpip": 30}, {"vpip": 22}])

    assert result["action"] == "bet"
    assert result["size"] == "60%"
    assert result["source"] == "multiway_engine"
    assert 0.0 <= result["equity"] <= 1.0


def test_evaluate_llm_failure_heuristic() -> None:
    """LLM failure returns the heuristic fallback."""
    engine = make_engine()
    engine.llm.decide_multiway.return_value = None

    result = engine.evaluate(make_state(), [{"vpip": 30}, {"vpip": 22}])

    assert result["source"] == "multiway_heuristic_fallback"
    assert result["confidence"] == "medium"


def test_evaluate_high_equity_heuristic() -> None:
    """High equity fallback recommends betting."""
    engine = make_engine()
    engine.llm.decide_multiway.return_value = None
    engine.calculate_equity = MagicMock(return_value=0.7)  # type: ignore[method-assign]

    result = engine.evaluate(make_state(), [{"vpip": 30}, {"vpip": 22}])

    assert result["action"] == "bet"
    assert result["size"] == "60%"


def test_evaluate_mid_equity_heuristic() -> None:
    """Medium equity fallback recommends checking."""
    engine = make_engine()
    engine.llm.decide_multiway.return_value = None
    engine.calculate_equity = MagicMock(return_value=0.5)  # type: ignore[method-assign]

    result = engine.evaluate(make_state(), [{"vpip": 30}, {"vpip": 22}])

    assert result["action"] == "check"
    assert result["size"] is None


def test_evaluate_low_equity_heuristic() -> None:
    """Low equity fallback recommends folding."""
    engine = make_engine()
    engine.llm.decide_multiway.return_value = None
    engine.calculate_equity = MagicMock(return_value=0.3)  # type: ignore[method-assign]

    result = engine.evaluate(make_state(), [{"vpip": 30}, {"vpip": 22}])

    assert result["action"] == "fold"
    assert result["size"] is None


def test_format_opponent_profiles_with_stats() -> None:
    """Opponent stats are converted into anonymized prompt-safe profiles."""
    profiles = make_engine()._format_opponent_profiles(
        [
            {
                "player_name": "villain",
                "long_term_style": "LAG",
                "total_hands": 50,
                "vpip": 35,
                "pfr": 25,
                "freshness_note": "fresh",
            }
        ]
    )

    assert profiles == [
        {
            "identifier": "seat_2",
            "player": "seat_2",
            "style": "LAG",
            "vpip": 35,
            "pfr": 25,
            "notes": "fresh",
        }
    ]
    assert "villain" not in str(profiles)


def test_format_opponent_profiles_none() -> None:
    """Missing opponent stats are excluded from LLM profiles."""
    profiles = make_engine()._format_opponent_profiles([None])

    assert profiles == []


def test_format_opponent_profiles_filters_low_sample_stats() -> None:
    """Opponent stats below the sample threshold are excluded from profiles."""
    profiles = make_engine()._format_opponent_profiles(
        [
            {"player_name": "low", "total_hands": 49, "vpip": 40, "pfr": 20},
            {"player_name": "usable", "total_hands": 50, "vpip": 30, "pfr": 18},
        ]
    )

    assert len(profiles) == 1
    assert profiles[0]["identifier"] == "seat_3"
    assert profiles[0]["vpip"] == 30


def test_format_opponent_profiles_uses_configured_threshold() -> None:
    """Multiway opponent profile threshold is read from config."""
    config = {"game": {"blind_bb": 100}, "preflop_delta": {"sample_threshold_low": 80}}
    profiles = make_engine(config)._format_opponent_profiles(
        [
            {"player_name": "below", "total_hands": 79, "vpip": 40, "pfr": 20},
            {"player_name": "at", "total_hands": 80, "vpip": 30, "pfr": 18},
        ]
    )

    assert len(profiles) == 1
    assert profiles[0]["identifier"] == "seat_3"


def test_multiway_no_player_name_in_llm_input() -> None:
    """Multiway LLM input uses seat identifiers instead of player names."""
    engine = make_engine()
    engine.llm.decide_multiway.return_value = {
        "action": "check",
        "size": None,
        "confidence": "medium",
        "reasoning": "Pot control",
    }

    engine.evaluate(
        make_state(),
        [
            {"player_name": "SecretOne", "total_hands": 50, "vpip": 30},
            {"name": "SecretTwo", "total_hands": 50, "vpip": 22},
        ],
    )

    profiles = engine.llm.decide_multiway.call_args.kwargs["opponent_profiles"]
    assert "SecretOne" not in str(profiles)
    assert "SecretTwo" not in str(profiles)
    assert profiles[0]["identifier"] == "seat_2"
    assert profiles[1]["identifier"] == "seat_3"


def test_evaluate_returns_medium_confidence() -> None:
    """evaluate() returns medium confidence for multiway decisions."""
    engine = make_engine()
    engine.llm.decide_multiway.return_value = {
        "action": "check",
        "size": None,
        "confidence": "low",
        "reasoning": "model confidence ignored",
    }

    result = engine.evaluate(make_state(), [{"vpip": 30}, {"vpip": 22}])

    assert result["confidence"] == "medium"


def test_evaluate_continues_without_usable_opponent_stats() -> None:
    """Multiway evaluation continues with empty profiles when stats are weak."""
    engine = make_engine()
    engine.llm.decide_multiway.return_value = {
        "action": "check",
        "size": None,
        "confidence": "medium",
        "reasoning": "No usable opponent stats",
    }

    result = engine.evaluate(
        make_state(),
        [
            {"player_name": "LowOne", "total_hands": 10, "vpip": 30},
            None,
        ],
    )

    profiles = engine.llm.decide_multiway.call_args.kwargs["opponent_profiles"]
    assert profiles == []
    assert result["action"] == "check"


def test_equity_calculation_time() -> None:
    """Equity calculation completes quickly enough for polling."""
    engine = make_engine()
    engine.mc_samples = 10000
    started_at = time.perf_counter()

    equity = engine.calculate_equity(["Ah", "As"], ["Td", "7c", "2h"], 1)
    elapsed_ms = (time.perf_counter() - started_at) * 1000

    assert 0.0 <= equity <= 1.0
    assert elapsed_ms < 100
