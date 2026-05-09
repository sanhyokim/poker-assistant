"""Tests for the unified recommendation engine."""

from __future__ import annotations

import json
import logging
import shutil
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.game_state import ActionRecord, ButtonState, GameState, HeroState, PlayerState
from strategy.preflop_chart import PreflopChart
from strategy.recommendation_engine import Recommendation, RecommendationEngine


TEST_CONFIG = {"game": {"blind_bb": 100}, "preflop_delta": {"sample_threshold_low": 50}}


@pytest.fixture
def workspace_tmp() -> Path:
    """Return a workspace-local temporary directory."""
    path = Path(".test_tmp") / f"recommendation_engine_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def make_engine(config: dict | None = None) -> RecommendationEngine:
    """Create a RecommendationEngine with mocked dependencies."""
    preflop_chart = MagicMock()
    solver_bridge = MagicMock()
    solver_bridge.disabled = False
    solver_request_builder = MagicMock()
    llm_pipeline = MagicMock()
    multiway_engine = MagicMock()
    return RecommendationEngine(
        config or TEST_CONFIG,
        preflop_chart,
        solver_bridge,
        solver_request_builder,
        llm_pipeline,
        multiway_engine,
    )


def make_real_chart_engine() -> RecommendationEngine:
    """Create a RecommendationEngine with the real preflop chart."""
    return RecommendationEngine(
        TEST_CONFIG,
        PreflopChart(),
        None,
        MagicMock(),
        None,
        MagicMock(),
    )


def make_state(
    phase: str = "flop",
    active_player_count: int = 2,
    hero_cards: list[str] | None = None,
) -> GameState:
    """Create a GameState for recommendation tests."""
    players = GameState.create_default_players()
    players["2"] = PlayerState(
        name="p2",
        stack=4000,
        bet=0,
        is_seated=True,
        in_current_hand=True,
    )
    if active_player_count >= 3:
        players["3"] = PlayerState(
            name="p3",
            stack=3000,
            bet=0,
            is_seated=True,
            in_current_hand=True,
        )

    return GameState(
        phase=phase,
        hero=HeroState(
            position="BTN",
            cards=hero_cards or ["Ah", "As"],
            stack=5000,
            bet=0,
            is_my_turn=True,
        ),
        board=["Td", "7c", "2h"],
        board_card_count=3,
        pot=600,
        players=players,
        active_player_count=active_player_count,
    )


def test_generate_preflop_uses_chart() -> None:
    """Preflop GameState returns a PreflopChart-based recommendation."""
    engine = make_engine()
    engine.preflop_chart.get_scenario.return_value = "RFI"
    engine.preflop_chart.get_recommendation.return_value = {
        "action": "raise",
        "confidence": "high",
        "source": "preflop_chart",
        "range": "77+",
    }
    state = make_state(phase="preflop", active_player_count=6)

    recommendation = engine.generate(state)

    assert recommendation.action == "RAISE"
    assert recommendation.amount == 300
    assert recommendation.confidence == "high"
    assert recommendation.strategy_source == "preflop_chart"
    assert "preflop_chart_ms" in recommendation.latency_breakdown


def test_generate_preflop_uses_chart_amount() -> None:
    """Preflop recommendation amount comes from the chart result when present."""
    engine = make_engine()
    engine.preflop_chart.get_scenario.return_value = "vs_3bet"
    engine.preflop_chart.get_recommendation.return_value = {
        "action": "4bet",
        "amount": 1200,
        "confidence": "high",
        "source": "preflop_chart",
        "range": "QQ+",
    }
    state = make_state(phase="preflop", active_player_count=6)
    state.players["2"].bet = 400

    recommendation = engine.generate(state)

    assert recommendation.action == "RAISE"
    assert recommendation.amount == 1200
    engine.preflop_chart.get_recommendation.assert_called_once()
    call_kwargs = engine.preflop_chart.get_recommendation.call_args.kwargs
    assert call_kwargs["current_max_bet"] == 400
    assert call_kwargs["blind_bb"] == 100


def test_generate_preflop_delta_policy_can_change_action() -> None:
    """Preflop delta policy can adjust the chart action via probabilities."""
    engine = make_engine()
    engine.preflop_chart.get_scenario.return_value = "RFI"
    engine.preflop_chart.get_recommendation.return_value = {
        "action": "raise",
        "amount": 300,
        "confidence": "high",
        "source": "preflop_chart",
        "range": "77+",
    }
    engine.delta_policy = MagicMock()
    engine.delta_policy.should_apply.return_value = True
    engine.delta_policy.apply.return_value = {"raise": 0.35, "call": 0.0, "fold": 0.65}
    state = make_state(phase="preflop", active_player_count=6)

    recommendation = engine.generate(state, opponent_stats={"2": {"total_hands": 80}})

    assert recommendation.action == "FOLD"
    assert recommendation.amount == 0
    assert recommendation.action_probabilities["FOLD"] == pytest.approx(0.65)
    engine.delta_policy.apply.assert_called_once()


def test_generate_preflop_delta_policy_skipped_without_stats() -> None:
    """Delta policy is not consulted when no opponent stats are available."""
    engine = make_engine()
    engine.preflop_chart.get_scenario.return_value = "RFI"
    engine.preflop_chart.get_recommendation.return_value = {
        "action": "raise",
        "amount": 300,
        "confidence": "high",
        "source": "preflop_chart",
        "range": "77+",
    }
    engine.delta_policy = MagicMock()
    state = make_state(phase="preflop", active_player_count=6)

    recommendation = engine.generate(state)

    assert recommendation.action == "RAISE"
    engine.delta_policy.should_apply.assert_not_called()
    engine.delta_policy.apply.assert_not_called()


def test_generate_preflop_uses_cumulative_limp_history_for_bb_check() -> None:
    """BB limp pots use cumulative history even when the current frame has no action."""
    engine = make_real_chart_engine()
    state = make_state(
        phase="preflop",
        active_player_count=6,
        hero_cards=["9h", "6h"],
    )
    state.hero.position = "BB"
    state.hero.bet = 100
    state.dealer_seat = 3
    state.actions_since_last_frame = []
    for player in state.players.values():
        player.is_seated = True
        player.in_current_hand = True
    state.players["4"].bet = 100

    recommendation = engine.generate(
        state,
        preflop_actions=[ActionRecord(seat=4, action="CALL", amount=100)],
    )

    assert recommendation.action == "CHECK"
    assert recommendation.amount == 0
    assert recommendation.strategy_source == "preflop_chart"


def test_generate_preflop_bb_vs_limp_raises_premium_hand() -> None:
    """BB premium hands raise versus limpers."""
    engine = make_real_chart_engine()
    state = make_state(
        phase="preflop",
        active_player_count=6,
        hero_cards=["Ah", "Kd"],
    )
    state.hero.position = "BB"
    state.hero.bet = 100
    state.dealer_seat = 3
    for player in state.players.values():
        player.is_seated = True
        player.in_current_hand = True
    state.players["4"].bet = 100

    recommendation = engine.generate(
        state,
        preflop_actions=[ActionRecord(seat=4, action="CALL", amount=100)],
    )

    assert recommendation.action == "RAISE"
    assert recommendation.amount == 300
    assert recommendation.strategy_source == "preflop_chart"


def test_bb_empty_actions_returns_deferred() -> None:
    """BB with no preflop action history defers recommendation."""
    engine = make_real_chart_engine()
    state = make_state(
        phase="preflop",
        active_player_count=6,
        hero_cards=["9h", "6h"],
    )
    state.hero.position = "BB"
    state.hero.bet = 100

    recommendation = engine.generate(state, preflop_actions=[])

    assert recommendation.action == "CHECK"
    assert recommendation.amount == 0
    assert recommendation.strategy_source == "deferred"


def test_bb_with_actions_returns_chart() -> None:
    """BB with action history uses the normal chart path."""
    engine = make_real_chart_engine()
    state = make_state(
        phase="preflop",
        active_player_count=6,
        hero_cards=["Ah", "Kd"],
    )
    state.hero.position = "BB"
    state.hero.bet = 100

    recommendation = engine.generate(
        state,
        preflop_actions=[
            {
                "seat": 2,
                "action": "RAISE",
                "amount": 300,
                "position": "BTN",
            }
        ],
    )

    assert recommendation.strategy_source == "preflop_chart"


def test_non_bb_empty_actions_returns_rfi() -> None:
    """Non-BB empty history uses the RFI chart scenario."""
    engine = make_real_chart_engine()
    state = make_state(
        phase="preflop",
        active_player_count=6,
        hero_cards=["Ah", "Kd"],
    )
    state.hero.position = "UTG"

    recommendation = engine.generate(state, preflop_actions=[])

    assert recommendation.strategy_source == "preflop_chart"
    assert recommendation.action == "RAISE"


def test_generate_preflop_sb_vs_btn_raise_uses_added_chart() -> None:
    """SB can resolve BTN raises through the added SB chart scenario."""
    engine = make_real_chart_engine()
    state = make_state(
        phase="preflop",
        active_player_count=6,
        hero_cards=["As", "Ac"],
    )
    state.hero.position = "SB"
    state.dealer_seat = 2
    for player in state.players.values():
        player.is_seated = True
        player.in_current_hand = True
    state.players["2"].bet = 300

    recommendation = engine.generate(
        state,
        preflop_actions=[ActionRecord(seat=2, action="RAISE", amount=300)],
    )

    assert recommendation.action == "RAISE"
    assert recommendation.amount == 900
    assert recommendation.strategy_source == "preflop_chart"


def test_raise_amount_capped_to_allin() -> None:
    """RAISE amount at or above hero stack is converted to ALL_IN."""
    engine = make_engine()
    engine.preflop_chart.get_scenario.return_value = "RFI"
    engine.preflop_chart.get_recommendation.return_value = {
        "action": "raise",
        "amount": 5000,
        "confidence": "high",
        "source": "preflop_chart",
    }
    state = make_state(phase="preflop", active_player_count=6)
    state.hero.stack = 1000

    recommendation = engine.generate(state)

    assert recommendation.action == "ALL_IN"
    assert recommendation.amount == 1000
    assert recommendation.confidence == "high"
    assert recommendation.strategy_source == "preflop_chart"


def test_headsup_uses_solver_path() -> None:
    """Two active postflop players use the solver path."""
    engine = make_engine()
    engine.llm_pipeline = None
    engine.solver_request_builder.build_request.return_value = {"request": True}
    engine.solver_bridge.solve.return_value = {
        "success": True,
        "root_strategy": {
            "actions": ["CHECK"],
            "average_strategy": {"CHECK": 1.0},
        },
    }
    state = make_state(phase="flop", active_player_count=2)

    recommendation = engine.generate(state)

    assert recommendation.strategy_source == "solver"
    engine.solver_bridge.solve.assert_called_once()


def test_engine_is_raise_action_includes_all_in() -> None:
    """ALL_IN is treated as a raise-like action by scenario helpers."""
    assert RecommendationEngine._is_raise_action("ALL_IN")
    assert RecommendationEngine._is_raise_action("all-in")


def test_preflop_scenario_opponent_all_in_returns_vs_all_in() -> None:
    """Opponent all-in resolves to the dedicated all-in scenario."""
    engine = make_real_chart_engine()
    state = make_state(phase="preflop", hero_cards=["8c", "6c"])
    state.hero.position = "BB"
    history = [
        {"seat": 5, "position": "MP", "action": "ALL_IN", "amount": 9868},
    ]

    assert engine._get_preflop_scenario(state, history) == "vs_all_in"


def test_vs_all_in_folds_weak_hands() -> None:
    """Weak hands fold against an opponent all-in."""
    engine = make_real_chart_engine()
    state = make_state(
        phase="preflop",
        active_player_count=3,
        hero_cards=["8c", "6c"],
    )
    state.hero.position = "BB"
    state.hero.stack = 9672
    state.players["2"].bet = 9984

    recommendation = engine.generate(
        state,
        preflop_actions=[
            {"seat": 2, "position": "SB", "action": "ALL_IN", "amount": 9984},
        ],
    )

    assert recommendation.action == "FOLD"
    assert recommendation.strategy_source == "preflop_chart"


def test_vs_all_in_calls_premium() -> None:
    """Premium hands call against an opponent all-in."""
    engine = make_real_chart_engine()
    state = make_state(
        phase="preflop",
        active_player_count=3,
        hero_cards=["As", "Ac"],
    )
    state.hero.position = "BB"
    state.hero.stack = 12000
    state.players["2"].bet = 9984

    recommendation = engine.generate(
        state,
        preflop_actions=[
            {"seat": 2, "position": "SB", "action": "ALL_IN", "amount": 9984},
        ],
    )

    assert recommendation.action == "CALL"
    assert recommendation.amount == 9984
    assert recommendation.strategy_source == "preflop_chart"


def test_safety_guard_blocks_weak_all_in() -> None:
    """Safety guard converts weak stack-off recommendations to FOLD."""
    engine = make_engine()
    engine.preflop_chart.get_scenario.return_value = "RFI"
    engine.preflop_chart.get_recommendation.return_value = {
        "action": "raise",
        "amount": 30000,
        "confidence": "high",
        "source": "preflop_chart",
    }
    engine.preflop_chart.hand_to_generic.side_effect = PreflopChart.hand_to_generic
    state = make_state(
        phase="preflop",
        active_player_count=6,
        hero_cards=["8c", "6c"],
    )
    state.hero.stack = 9672
    state.players["2"].bet = 9984

    recommendation = engine.generate(state)

    assert recommendation.action == "FOLD"
    assert recommendation.amount == 0
    assert recommendation.strategy_source == "preflop_chart"


def test_safety_guard_allows_premium_all_in() -> None:
    """Safety guard keeps premium stack-off recommendations."""
    engine = make_engine()
    engine.preflop_chart.get_scenario.return_value = "RFI"
    engine.preflop_chart.get_recommendation.return_value = {
        "action": "raise",
        "amount": 30000,
        "confidence": "high",
        "source": "preflop_chart",
    }
    engine.preflop_chart.hand_to_generic.side_effect = PreflopChart.hand_to_generic
    state = make_state(
        phase="preflop",
        active_player_count=6,
        hero_cards=["As", "Ac"],
    )
    state.hero.stack = 9672
    state.players["2"].bet = 9984

    recommendation = engine.generate(state)

    assert recommendation.action == "ALL_IN"
    assert recommendation.amount == 9672
    assert recommendation.strategy_source == "preflop_chart"


def test_raise_amount_within_stack_unchanged() -> None:
    """RAISE amount below hero stack remains a RAISE."""
    engine = make_engine()
    engine.preflop_chart.get_scenario.return_value = "RFI"
    engine.preflop_chart.get_recommendation.return_value = {
        "action": "raise",
        "amount": 1500,
        "confidence": "high",
        "source": "preflop_chart",
    }
    state = make_state(phase="preflop", active_player_count=6)
    state.hero.stack = 5000

    recommendation = engine.generate(state)

    assert recommendation.action == "RAISE"
    assert recommendation.amount == 1500


def test_recommendation_enriched_with_pot_percentage_and_bb() -> None:
    """BET recommendations include pot percentage, BB amount, and preset hint."""
    engine = make_engine()
    state = make_state(phase="flop", active_player_count=3)
    state.pot = 2500
    engine.multiway_engine.evaluate.return_value = {
        "action": "bet",
        "size": 825,
        "reasoning": "Bet one third pot.",
    }

    recommendation = engine.generate(state)

    assert recommendation.action == "BET"
    assert recommendation.amount == 825
    assert recommendation.amount_bb == 8.2
    assert recommendation.pot_percentage == 33.0
    assert recommendation.preset_hint == "33%"


def test_multiway_bet_zero_gets_default_amount() -> None:
    """Multiway BET with null size defaults to 60 percent pot."""
    engine = make_engine()
    state = make_state(phase="flop", active_player_count=3)
    state.pot = 1000
    engine.multiway_engine.evaluate.return_value = {
        "action": "bet",
        "size": None,
        "reasoning": "Bet with default size.",
    }

    recommendation = engine.generate(state)

    assert recommendation.action == "BET"
    assert recommendation.amount == 600


def test_multiway_call_zero_gets_call_amount() -> None:
    """Multiway CALL with null size defaults to max_bet minus hero_bet."""
    engine = make_engine()
    state = make_state(phase="flop", active_player_count=3)
    state.hero.bet = 0
    state.players["2"].bet = 300
    engine.multiway_engine.evaluate.return_value = {
        "action": "call",
        "size": None,
        "reasoning": "Call the bet.",
    }

    recommendation = engine.generate(state)

    assert recommendation.action == "CALL"
    assert recommendation.amount == 300


def test_multiway_check_zero_stays_zero() -> None:
    """Multiway CHECK with null size keeps amount at zero."""
    engine = make_engine()
    state = make_state(phase="flop", active_player_count=3)
    engine.multiway_engine.evaluate.return_value = {
        "action": "check",
        "size": None,
        "reasoning": "Check back.",
    }

    recommendation = engine.generate(state)

    assert recommendation.action == "CHECK"
    assert recommendation.amount == 0


def test_multiway_bet_with_valid_size_unchanged() -> None:
    """Multiway BET with a valid positive size keeps the LLM amount."""
    engine = make_engine()
    state = make_state(phase="flop", active_player_count=3)
    engine.multiway_engine.evaluate.return_value = {
        "action": "bet",
        "size": 500,
        "reasoning": "Specific bet size.",
    }

    recommendation = engine.generate(state)

    assert recommendation.action == "BET"
    assert recommendation.amount == 500


def test_fold_converted_to_check_when_check_button_available(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """FOLD recommendations become CHECK when the visible button allows checking."""
    engine = make_engine()
    engine.preflop_chart.get_scenario.return_value = "BB_check"
    engine.preflop_chart.get_recommendation.return_value = {
        "action": "fold",
        "amount": 0,
        "confidence": "low",
        "source": "preflop_chart",
    }
    state = make_state(phase="preflop", active_player_count=6)
    state.buttons = ButtonState(call_or_check="check")

    with caplog.at_level(logging.DEBUG, logger="strategy.recommendation_engine"):
        recommendation = engine.generate(state)

    assert recommendation.action == "CHECK"
    assert recommendation.amount == 0
    assert recommendation.reason == "チェック可能（ベットなし）"
    assert recommendation.amount_bb is None
    assert recommendation.pot_percentage is None
    assert recommendation.preset_hint is None
    assert recommendation.raise_multiplier is None
    assert recommendation.raise_multiplier_label is None
    assert "Action constraints check: rec.action=FOLD" in caplog.text
    assert "call_or_check=check" in caplog.text
    assert "FOLD -> CHECK conversion: check button available" in caplog.text


def test_preflop_chart_fallback_fold_converted_to_check() -> None:
    """preflop_chart_fallback FOLD also becomes CHECK when checking is available."""
    engine = make_engine()
    engine.preflop_chart.get_scenario.return_value = "unknown"
    engine.preflop_chart.get_recommendation.return_value = {
        "action": "fold",
        "amount": 0,
        "confidence": "low",
        "source": "preflop_chart_fallback",
        "reason": "該当シナリオなし",
    }
    state = make_state(phase="preflop", active_player_count=6)
    state.buttons = ButtonState(call_or_check="check")

    recommendation = engine.generate(state)

    assert recommendation.action == "CHECK"
    assert recommendation.strategy_source == "preflop_chart_fallback"


def test_fold_stays_fold_when_call_button_visible() -> None:
    """FOLD recommendations are preserved when checking is unavailable."""
    engine = make_engine()
    engine.preflop_chart.get_scenario.return_value = "vs_raise"
    engine.preflop_chart.get_recommendation.return_value = {
        "action": "fold",
        "amount": 0,
        "confidence": "low",
        "source": "preflop_chart",
    }
    state = make_state(phase="preflop", active_player_count=6)
    state.buttons = ButtonState(call_or_check="call")

    recommendation = engine.generate(state)

    assert recommendation.action == "FOLD"


def test_bb_limp_call_button_converted_to_check(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """BB limp spots convert FOLD to CHECK when hero has no additional cost."""
    engine = make_engine()
    engine.preflop_chart.get_scenario.return_value = "BB_limp"
    engine.preflop_chart.get_recommendation.return_value = {
        "action": "fold",
        "amount": 0,
        "confidence": "low",
        "source": "preflop_chart",
    }
    state = make_state(phase="preflop", active_player_count=6)
    state.buttons = ButtonState(call_or_check="call")
    state.hero.bet = 100
    state.players["2"].bet = 100
    state.players["3"].bet = 100

    with caplog.at_level(logging.INFO, logger="strategy.recommendation_engine"):
        recommendation = engine.generate(state)

    assert recommendation.action == "CHECK"
    assert "FOLD -> CHECK conversion: hero_bet(100) >= max_bet(100)" in caplog.text


def test_fold_stays_fold_when_hero_owes_more_chips() -> None:
    """FOLD remains valid when opponent max bet is greater than hero bet."""
    engine = make_engine()
    engine.preflop_chart.get_scenario.return_value = "vs_raise"
    engine.preflop_chart.get_recommendation.return_value = {
        "action": "fold",
        "amount": 0,
        "confidence": "low",
        "source": "preflop_chart",
    }
    state = make_state(phase="preflop", active_player_count=6)
    state.buttons = ButtonState(call_or_check="call")
    state.hero.bet = 100
    state.players["2"].bet = 300

    recommendation = engine.generate(state)

    assert recommendation.action == "FOLD"


def test_fold_stays_fold_without_button_state() -> None:
    """FOLD recommendations are preserved when button state is unavailable."""
    engine = make_engine()
    engine.preflop_chart.get_scenario.return_value = "unknown_buttons"
    engine.preflop_chart.get_recommendation.return_value = {
        "action": "fold",
        "amount": 0,
        "confidence": "low",
        "source": "preflop_chart",
    }
    state = make_state(phase="preflop", active_player_count=6)
    state.buttons = None

    recommendation = engine.generate(state)

    assert recommendation.action == "FOLD"


def test_constraint_check_to_fold_when_call_required() -> None:
    """CHECK recommendations become FOLD when a call is required."""
    engine = make_engine()
    state = make_state(phase="flop", active_player_count=2)
    state.buttons = ButtonState(call_or_check="call")
    state.hero.bet = 0
    state.players["2"].bet = 200
    recommendation = engine.apply_action_constraints(
        Recommendation(
            action="CHECK",
            amount=0,
            reason="Pot control.",
            confidence="medium",
            strategy_source="llm_multiway",
            action_probabilities={"CHECK": 1.0},
        ),
        state,
    )

    assert recommendation.action == "FOLD"
    assert recommendation.confidence == "low"
    assert "チェック不可" in recommendation.reason
    assert recommendation.amount_bb is None
    assert recommendation.pot_percentage is None


def test_constraint_check_stays_when_check_available() -> None:
    """CHECK recommendations remain CHECK when the button is check."""
    engine = make_engine()
    state = make_state(phase="flop", active_player_count=2)
    state.buttons = ButtonState(call_or_check="check")
    state.hero.bet = 0
    state.players["2"].bet = 0
    recommendation = Recommendation(
        action="CHECK",
        amount=0,
        reason="Pot control.",
        confidence="medium",
        strategy_source="llm_multiway",
    )

    result = engine.apply_action_constraints(recommendation, state)

    assert result is recommendation
    assert result.action == "CHECK"


def test_constraint_check_stays_when_bets_equal() -> None:
    """CHECK recommendations remain CHECK when hero has matched the max bet."""
    engine = make_engine()
    state = make_state(phase="preflop", active_player_count=6)
    state.buttons = ButtonState(call_or_check="call")
    state.hero.bet = 100
    state.players["2"].bet = 100
    recommendation = Recommendation(
        action="CHECK",
        amount=0,
        reason="No additional cost.",
        confidence="medium",
        strategy_source="llm_multiway",
    )

    result = engine.apply_action_constraints(recommendation, state)

    assert result is recommendation
    assert result.action == "CHECK"


def test_get_max_opponent_bet() -> None:
    """Max opponent bet ignores hero and returns the highest player bet."""
    engine = make_engine()
    state = make_state(phase="flop", active_player_count=4)
    state.hero.bet = 1000
    state.players["2"].bet = 200
    state.players["3"].bet = 500
    state.players["4"] = PlayerState(bet=0)

    assert engine._get_max_opponent_bet(state) == 500


def test_get_max_opponent_bet_all_zero() -> None:
    """Max opponent bet returns zero when all player bets are zero."""
    engine = make_engine()
    state = make_state(phase="flop", active_player_count=3)
    for player in state.players.values():
        player.bet = 0

    assert engine._get_max_opponent_bet(state) == 0


def test_preflop_raise_multiplier_uses_big_blind() -> None:
    """Preflop RAISE multiplier is based on the big blind."""
    engine = make_engine()
    engine.preflop_chart.get_scenario.return_value = "RFI"
    engine.preflop_chart.get_recommendation.return_value = {
        "action": "raise",
        "amount": 300,
        "confidence": "high",
        "source": "preflop_chart",
    }
    state = make_state(phase="preflop", active_player_count=6)

    recommendation = engine.generate(state)

    assert recommendation.raise_multiplier == 3.0
    assert recommendation.raise_multiplier_label == "3.0X"


def test_postflop_raise_multiplier_uses_current_max_bet() -> None:
    """Postflop RAISE multiplier is based on the current maximum bet."""
    engine = make_engine()
    state = make_state(phase="flop", active_player_count=3)
    state.players["2"].bet = 200
    engine.multiway_engine.evaluate.return_value = {
        "action": "raise",
        "size": 600,
        "reasoning": "Raise over lead.",
    }

    recommendation = engine.generate(state)

    assert recommendation.action == "RAISE"
    assert recommendation.raise_multiplier == 3.0
    assert recommendation.raise_multiplier_label == "3.0X"


def test_postflop_raise_multiplier_handles_zero_max_bet() -> None:
    """Postflop RAISE multiplier is empty when no max bet exists."""
    engine = make_engine()
    state = make_state(phase="flop", active_player_count=3)
    engine.multiway_engine.evaluate.return_value = {
        "action": "raise",
        "size": 600,
        "reasoning": "Raise label should not divide by zero.",
    }

    recommendation = engine.generate(state)

    assert recommendation.action == "RAISE"
    assert recommendation.raise_multiplier is None
    assert recommendation.raise_multiplier_label is None


def test_recommendation_enrichment_handles_zero_pot() -> None:
    """pot=0 avoids percentage division while preserving BB amount."""
    engine = make_engine()
    engine.preflop_chart.get_scenario.return_value = "RFI"
    engine.preflop_chart.get_recommendation.return_value = {
        "action": "raise",
        "amount": 300,
        "confidence": "high",
        "source": "preflop_chart",
    }
    state = make_state(phase="preflop", active_player_count=6)
    state.pot = 0

    recommendation = engine.generate(state)

    assert recommendation.amount_bb == 3.0
    assert recommendation.pot_percentage is None
    assert recommendation.preset_hint is None
    assert recommendation.raise_multiplier == 3.0
    assert recommendation.raise_multiplier_label == "3.0X"


def test_fold_recommendation_has_no_size_metadata() -> None:
    """FOLD recommendations leave display size metadata empty."""
    engine = make_engine()
    engine.preflop_chart.get_scenario.return_value = "RFI"
    engine.preflop_chart.get_recommendation.return_value = {
        "action": "fold",
        "amount": 0,
        "confidence": "low",
        "source": "preflop_chart",
    }
    state = make_state(phase="preflop", active_player_count=6)

    recommendation = engine.generate(state)

    assert recommendation.action == "FOLD"
    assert recommendation.amount_bb is None
    assert recommendation.pot_percentage is None
    assert recommendation.preset_hint is None
    assert recommendation.raise_multiplier is None
    assert recommendation.raise_multiplier_label is None


def test_find_nearest_preset_returns_exact_percentage_when_not_close() -> None:
    """Preset hint falls back to exact percentage outside the preset tolerance."""
    assert RecommendationEngine._find_nearest_preset(62.0) == "62%"


def test_generate_postflop_headsup_solver_success() -> None:
    """Heads-up postflop uses solver output and returns high confidence."""
    engine = make_engine()
    state = make_state(phase="flop", active_player_count=2)
    engine.llm_pipeline.get_baseline_range.side_effect = ["OOP_RANGE", "IP_RANGE"]
    engine.solver_request_builder.build_request.return_value = {"board": "Td7c2h"}
    engine.solver_bridge.solve.return_value = {
        "success": True,
        "exploitability": 1.25,
        "root_strategy": {
            "actions": ["Check", "Bet 120"],
            "hands": [],
            "strategy_matrix": [],
            "average_strategy": {"Check": 0.25, "Bet 120": 0.75},
        },
    }
    engine.llm_pipeline.generate_reason.return_value = "高頻度ベット推奨"

    recommendation = engine.generate(state, {"2": {"vpip": 30, "total_hands": 49}})

    assert recommendation.action == "BET"
    assert recommendation.amount == 120
    assert recommendation.confidence == "high"
    assert recommendation.strategy_source == "solver"
    assert recommendation.solver_exploitability == 1.25
    assert recommendation.action_probabilities == {"CHECK": 0.25, "BET 120": 0.75}
    assert "solver_ms" in recommendation.latency_breakdown
    assert recommendation.latency_breakdown["range_estimation_ms"] == 0.0
    assert recommendation.latency_breakdown["exploit_adjustment_ms"] == 0.0
    assert recommendation.latency_breakdown["reason_generation_ms"] == 0.0
    engine.llm_pipeline.estimate_ranges.assert_not_called()
    engine.llm_pipeline.suggest_exploit.assert_not_called()
    engine.llm_pipeline.generate_reason.assert_not_called()


def test_generate_postflop_headsup_uses_exploit_only_with_usable_stats() -> None:
    """Heads-up postflop calls only suggest_exploit when stats meet threshold."""
    engine = make_engine()
    state = make_state(phase="flop", active_player_count=2)
    engine.llm_pipeline.get_baseline_range.side_effect = ["OOP_RANGE", "IP_RANGE"]
    engine.solver_request_builder.build_request.return_value = {"board": "Td7c2h"}
    engine.solver_bridge.solve.return_value = {
        "success": True,
        "root_strategy": {
            "actions": ["Check", "Bet 120"],
            "average_strategy": {"Check": 0.2, "Bet 120": 0.8},
        },
    }
    engine.llm_pipeline.suggest_exploit.return_value = {
        "adjusted_action": "call",
        "adjusted_size": None,
        "reasoning": "Enough hands to exploit.",
    }

    recommendation = engine.generate(state, {"2": {"vpip": 30, "total_hands": 50}})

    assert recommendation.action == "CALL"
    assert recommendation.reason == "Enough hands to exploit."
    engine.llm_pipeline.estimate_ranges.assert_not_called()
    engine.llm_pipeline.suggest_exploit.assert_called_once()
    engine.llm_pipeline.generate_reason.assert_not_called()


def test_generate_postflop_headsup_exploit_failure_returns_solver() -> None:
    """Heads-up postflop keeps solver output when exploit LLM fails."""
    engine = make_engine()
    state = make_state(phase="flop", active_player_count=2)
    engine.llm_pipeline.get_baseline_range.side_effect = ["OOP_RANGE", "IP_RANGE"]
    engine.solver_request_builder.build_request.return_value = {"board": "Td7c2h"}
    engine.solver_bridge.solve.return_value = {
        "success": True,
        "root_strategy": {
            "actions": ["Check", "Bet 120"],
            "average_strategy": {"Check": 0.2, "Bet 120": 0.8},
        },
    }
    engine.llm_pipeline.suggest_exploit.side_effect = RuntimeError("boom")

    recommendation = engine.generate(state, {"2": {"vpip": 30, "total_hands": 100}})

    assert recommendation.action == "BET"
    assert recommendation.amount == 120
    assert recommendation.reason == "HU solver recommendation"
    engine.llm_pipeline.estimate_ranges.assert_not_called()
    engine.llm_pipeline.suggest_exploit.assert_called_once()
    engine.llm_pipeline.generate_reason.assert_not_called()


def test_generate_postflop_headsup_uses_configured_stats_threshold() -> None:
    """Heads-up exploit threshold is read from config."""
    config = {"game": {"blind_bb": 100}, "preflop_delta": {"sample_threshold_low": 80}}
    engine = make_engine(config)
    state = make_state(phase="flop", active_player_count=2)
    engine.llm_pipeline.get_baseline_range.side_effect = [
        "OOP_RANGE",
        "IP_RANGE",
        "OOP_RANGE",
        "IP_RANGE",
    ]
    engine.solver_request_builder.build_request.return_value = {"board": "Td7c2h"}
    engine.solver_bridge.solve.return_value = {
        "success": True,
        "root_strategy": {
            "actions": ["Check", "Bet 120"],
            "average_strategy": {"Check": 0.2, "Bet 120": 0.8},
        },
    }
    engine.llm_pipeline.suggest_exploit.return_value = {
        "adjusted_action": "call",
        "reasoning": "Configured threshold met.",
    }

    below = engine.generate(state, {"2": {"vpip": 30, "total_hands": 79}})
    assert below.action == "BET"
    engine.llm_pipeline.suggest_exploit.assert_not_called()

    at_threshold = engine.generate(state, {"2": {"vpip": 30, "total_hands": 80}})
    assert at_threshold.action == "CALL"
    engine.llm_pipeline.suggest_exploit.assert_called_once()
    engine.llm_pipeline.estimate_ranges.assert_not_called()
    engine.llm_pipeline.generate_reason.assert_not_called()


def test_solver_debug_json_saved_when_enabled(workspace_tmp: Path) -> None:
    """Solver debug JSON is saved after successful HU solver recommendation."""
    config = {
        "game": {"blind_bb": 100},
        "preflop_delta": {"sample_threshold_low": 50},
        "debug": {
            "save_solver_io": True,
            "solver_io_dir": str(workspace_tmp / "solver_io"),
        },
    }
    engine = make_engine(config)
    state = make_state(phase="flop", active_player_count=2)
    state.hand_id = 4
    state.hero.bet = 50
    state.players["2"].bet = 200
    state.players["3"].in_current_hand = False
    state.actions_since_last_frame = [
        ActionRecord(seat=2, action="BET", amount=200, confidence="high")
    ]
    solver_request = {"board": "Td7c2h", "range_oop": "OOP_RANGE"}
    solver_output = {
        "success": True,
        "exploitability": 1.25,
        "root_strategy": {
            "actions": ["Check", "Bet 120"],
            "average_strategy": {"Check": 0.25, "Bet 120": 0.75},
        },
    }
    engine.llm_pipeline.get_baseline_range.side_effect = ["OOP_RANGE", "IP_RANGE"]
    engine.solver_request_builder.build_request.return_value = solver_request
    engine.solver_bridge.solve.return_value = solver_output

    recommendation = engine.generate(state, {"2": {"vpip": 30, "total_hands": 49}})

    files = list((workspace_tmp / "solver_io").rglob("*.json"))
    assert len(files) == 1
    debug_data = json.loads(files[0].read_text(encoding="utf-8"))
    assert files[0].name.startswith("hand_000004_flop_")
    assert debug_data["hand_id"] == 4
    assert debug_data["phase"] == "flop"
    assert debug_data["hero_cards"] == ["Ah", "As"]
    assert debug_data["board"] == ["Td", "7c", "2h"]
    assert debug_data["call_amount"] == 150
    assert debug_data["solver_request"] == solver_request
    assert debug_data["solver_output"] == solver_output
    assert debug_data["recommendation"]["action"] == recommendation.action
    assert debug_data["recommendation"]["amount"] == recommendation.amount
    assert "latency" in debug_data


def test_solver_debug_json_not_saved_when_disabled(workspace_tmp: Path) -> None:
    """Solver debug JSON is not saved when debug.save_solver_io is false."""
    config = {
        "game": {"blind_bb": 100},
        "preflop_delta": {"sample_threshold_low": 50},
        "debug": {
            "save_solver_io": False,
            "solver_io_dir": str(workspace_tmp / "solver_io"),
        },
    }
    engine = make_engine(config)
    state = make_state(phase="flop", active_player_count=2)
    engine.llm_pipeline.get_baseline_range.side_effect = ["OOP_RANGE", "IP_RANGE"]
    engine.solver_request_builder.build_request.return_value = {"board": "Td7c2h"}
    engine.solver_bridge.solve.return_value = {
        "success": True,
        "root_strategy": {
            "actions": ["Check", "Bet 120"],
            "average_strategy": {"Check": 0.25, "Bet 120": 0.75},
        },
    }

    engine.generate(state, {"2": {"vpip": 30, "total_hands": 49}})

    assert list(workspace_tmp.rglob("*.json")) == []


def test_solver_debug_save_failure_does_not_block_recommendation(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Solver debug save failures do not block recommendations."""
    config = {
        "game": {"blind_bb": 100},
        "preflop_delta": {"sample_threshold_low": 50},
        "debug": {
            "save_solver_io": True,
            "solver_io_dir": str(workspace_tmp / "solver_io"),
        },
    }
    engine = make_engine(config)
    state = make_state(phase="flop", active_player_count=2)
    engine.llm_pipeline.get_baseline_range.side_effect = ["OOP_RANGE", "IP_RANGE"]
    engine.solver_request_builder.build_request.return_value = {"board": "Td7c2h"}
    engine.solver_bridge.solve.return_value = {
        "success": True,
        "root_strategy": {
            "actions": ["Check", "Bet 120"],
            "average_strategy": {"Check": 0.25, "Bet 120": 0.75},
        },
    }

    def fail_open(*_args: object, **_kwargs: object) -> None:
        raise OSError("blocked")

    monkeypatch.setattr("builtins.open", fail_open)
    with caplog.at_level(logging.WARNING):
        recommendation = engine.generate(
            state,
            {"2": {"vpip": 30, "total_hands": 49}},
        )

    assert recommendation.action == "BET"
    assert "Solver debug save failed" in caplog.text


def test_generate_postflop_multiway_uses_multiway_engine() -> None:
    """Multiway postflop uses MultiwayEngine and returns medium confidence."""
    engine = make_engine()
    state = make_state(phase="flop", active_player_count=3)
    engine.multiway_engine.evaluate.return_value = {
        "action": "check",
        "size": None,
        "reasoning": "Pot control.",
        "equity": 0.45,
        "source": "multiway_engine",
    }

    recommendation = engine.generate(state, {"2": {"vpip": 30}, "3": {"vpip": 22}})

    assert recommendation.action == "CHECK"
    assert recommendation.confidence == "medium"
    assert recommendation.strategy_source == "llm_multiway"
    assert "multiway_ms" in recommendation.latency_breakdown


def test_solver_disabled_falls_back_to_llm() -> None:
    """Disabled solver falls back to the LLM heads-up path when available."""
    engine = make_engine()
    engine.solver_bridge.disabled = True
    engine.llm_pipeline.suggest_exploit.return_value = {
        "adjusted_action": "call",
        "adjusted_size": None,
        "reasoning": "Solver disabled; call is acceptable.",
    }

    recommendation = engine.generate(make_state(), {"2": {"vpip": 30, "total_hands": 50}})

    assert recommendation.action == "CALL"
    assert recommendation.confidence == "medium"
    assert recommendation.strategy_source == "llm_headsup_fallback"


def test_all_modules_failure_returns_fallback() -> None:
    """Unexpected dependency errors return a low-confidence fallback."""
    engine = make_engine()
    engine.solver_request_builder.build_request.side_effect = RuntimeError("boom")
    engine.llm_pipeline.suggest_exploit.side_effect = RuntimeError("boom")

    recommendation = engine.generate(make_state(), {"2": {"vpip": 30}})

    assert recommendation.action == "CHECK"
    assert recommendation.confidence == "low"
    assert recommendation.strategy_source == "fallback"


def test_parse_solver_strategy_average_strategy() -> None:
    """Solver average_strategy is parsed into action, amount, and probabilities."""
    engine = make_engine()
    solver_output = {
        "root_strategy": {
            "actions": ["Check", "Bet 120", "AllIn 900"],
            "average_strategy": {"Check": 0.2, "Bet 120": 0.3, "AllIn 900": 0.5},
        }
    }

    action, amount, probabilities = engine._parse_solver_strategy(
        solver_output,
        make_state(),
    )

    assert action == "ALL_IN"
    assert amount == 900
    assert probabilities == {"CHECK": 0.2, "BET 120": 0.3, "ALL_IN 900": 0.5}


def test_parse_solver_strategy_hand_specific() -> None:
    """Hand-specific strategy_matrix is preferred when hero hand exists."""
    engine = make_engine()
    solver_output = {
        "root_strategy": {
            "actions": ["Check", "Bet 120"],
            "hands": ["AhAs"],
            "strategy_matrix": [[0.1, 0.9]],
            "average_strategy": {"Check": 0.8, "Bet 120": 0.2},
        }
    }

    action, amount, probabilities = engine._parse_solver_strategy(
        solver_output,
        make_state(hero_cards=["Ah", "As"]),
    )

    assert action == "BET"
    assert amount == 120
    assert probabilities == {"CHECK": 0.1, "BET 120": 0.9}


def test_parse_solver_strategy_call_amount() -> None:
    """Call action amount uses the current maximum bet."""
    engine = make_engine()
    state = make_state()
    state.players["2"].bet = 250
    solver_output = {
        "root_strategy": {
            "actions": ["Call", "Fold"],
            "average_strategy": {"Call": 0.7, "Fold": 0.3},
        }
    }

    action, amount, probabilities = engine._parse_solver_strategy(solver_output, state)

    assert action == "CALL"
    assert amount == 250
    assert probabilities == {"CALL": 0.7, "FOLD": 0.3}
