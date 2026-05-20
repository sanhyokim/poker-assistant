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
from strategy.solver_request_builder import SolverRequestBuilder


TEST_CONFIG = {"game": {"blind_bb": 100}, "preflop_delta": {"sample_threshold_low": 50}}

DEEP_SPR_CONFIG = {
    "game": {"blind_bb": 100},
    "preflop_delta": {"sample_threshold_low": 50},
    "solver": {
        "deep_spr_threshold": 10.0,
        "deep_spr_light_probe_enabled": True,
    },
}

SOLVER_BUILDER_CONFIG = {
    "game": {"blind_bb": 100},
    "solver": {
        "max_iterations": 200,
        "target_exploitability_pct": 0.5,
        "timeout_ms": 7000,
        "deep_spr_threshold": 10.0,
        "deep_spr_light_timeout_ms": 5000,
        "deep_spr_light_max_iterations": 80,
        "deep_spr_light_target_exploitability_pct": 1.5,
        "deep_spr_light_bet_sizes": "50%",
        "deep_spr_light_raise_sizes": "2.5x",
        "default_bet_sizes": "60%,a",
        "default_raise_sizes": "2.5x",
        "add_allin_threshold": 1.5,
        "force_allin_threshold": 0.15,
        "merging_threshold": 0.1,
        "rake_rate": 0.0,
        "rake_cap": 0.0,
    },
}


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
    solver_request_builder._get_active_opponents.side_effect = (
        lambda state: [
            {"seat": int(seat), "stack": player.stack or 0}
            for seat, player in state.players.items()
            if player.in_current_hand
        ]
    )
    solver_request_builder.compute_effective_stack.side_effect = (
        lambda state: min(
            [state.hero.stack or 0]
            + [
                player.stack or 0
                for player in state.players.values()
                if player.in_current_hand
            ]
        )
    )
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


def test_parse_solver_strategy_prefers_node_strategy() -> None:
    """node_strategy is preferred over root_strategy when both are present."""
    engine = make_engine()
    state = make_state()
    solver_output = {
        "root_strategy": {
            "actions": ["Check", "Bet 100"],
            "average_strategy": {"Check": 1.0, "Bet 100": 0.0},
        },
        "node_strategy": {
            "actions": ["Call", "Fold"],
            "average_strategy": {"Call": 0.8, "Fold": 0.2},
        },
    }

    action, amount, probabilities = engine._parse_solver_strategy(
        solver_output,
        state,
    )

    assert action == "CALL"
    assert amount == 0
    assert probabilities["CALL"] == 0.8


def test_parse_solver_strategy_fallback_to_root() -> None:
    """root_strategy is used when node_strategy is absent."""
    engine = make_engine()
    state = make_state()
    solver_output = {
        "root_strategy": {
            "actions": ["Check", "Bet 100"],
            "average_strategy": {"Check": 0.1, "Bet 100": 0.9},
        },
    }

    action, amount, probabilities = engine._parse_solver_strategy(
        solver_output,
        state,
    )

    assert action == "BET"
    assert amount == 100
    assert probabilities["BET 100"] == 0.9


def test_detect_preflop_scenario_from_pot_size() -> None:
    """Preflop scenario is inferred from pot size when actions are unavailable."""
    engine = make_engine()
    state = make_state()

    state.pot = 4100
    assert engine._detect_preflop_scenario(state) == "4bet_pot"
    state.pot = 1500
    assert engine._detect_preflop_scenario(state) == "3bet_pot"
    state.pot = 600
    assert engine._detect_preflop_scenario(state) == "single_raised_pot"
    state.pot = 400
    assert engine._detect_preflop_scenario(state) == "limp_pot"


def test_baseline_range_scenario_selection() -> None:
    """Scenario-specific baseline ranges are selected by position."""
    engine = make_engine()
    engine._cached_baseline_ranges = {
        "single_raised_pot": {"OOP": "OOP_SRP", "IP": "IP_SRP"},
        "3bet_pot": {"OOP": "OOP_3BET", "IP": "IP_3BET"},
        "cbet_defend": {"OOP": "OOP_CBET", "IP": "IP_CBET"},
    }

    assert engine._baseline_range("OOP", "3bet_pot") == "OOP_3BET"
    assert engine._baseline_range("IP", "single_raised_pot") == "IP_SRP"
    assert engine._baseline_range("OOP", "missing") == "OOP_CBET"


def test_compute_street_start_pot() -> None:
    """Current street bets are subtracted from recognized pot."""
    engine = make_engine()
    state = make_state()
    state.pot = 1000
    state.hero.bet = 200
    state.players["2"].bet = 300

    assert engine._compute_street_start_pot(state) == 500


def test_build_actions_played_opponent_bet() -> None:
    """Opponent current bet becomes a solver Bet action."""
    engine = make_engine()
    state = make_state()
    state.players["2"].bet = 500

    assert engine._build_actions_played(state) == ["Bet 500"]


def test_build_actions_played_no_bets() -> None:
    """No current bets means hero acts first at the root node."""
    engine = make_engine()
    state = make_state()

    assert engine._build_actions_played(state) is None


def test_build_actions_played_from_current_street_actions() -> None:
    """Current street BET/RAISE records become ordered solver actions."""
    engine = make_engine()
    state = make_state()
    state.current_street_actions = [
        ActionRecord(seat=2, action="BET", amount=100),
        ActionRecord(seat=1, action="CALL", amount=100),
        ActionRecord(seat=2, action="RAISE", amount=300),
    ]

    actions, status, reason_codes = engine._build_actions_played_from_street_actions(
        state
    )

    assert actions == ["Bet 100", "Raise 300"]
    assert status == "ok"
    assert reason_codes == []


def test_build_actions_played_from_empty_street_is_empty_ok() -> None:
    """A root street with no actions is stable and buildable."""
    engine = make_engine()
    state = make_state()

    actions, status, reason_codes = engine._build_actions_played_from_street_actions(
        state
    )

    assert actions == []
    assert status == "empty_ok"
    assert reason_codes == []


def test_build_actions_played_missing_history_with_bets_is_unstable() -> None:
    """Visible bets without street history are not silently treated as root."""
    engine = make_engine()
    state = make_state()
    state.players["2"].bet = 200

    actions, status, reason_codes = engine._build_actions_played_from_street_actions(
        state
    )

    assert actions == []
    assert status == "unstable"
    assert reason_codes == ["street_actions_missing_with_bets"]


def test_build_actions_played_from_unsupported_street_action_is_unstable() -> None:
    """Unsupported action records block Solver launch instead of being hidden."""
    engine = make_engine()
    state = make_state()
    state.current_street_actions = [ActionRecord(seat=2, action="POST", amount=100)]

    actions, status, reason_codes = engine._build_actions_played_from_street_actions(
        state
    )

    assert actions == []
    assert status == "unstable"
    assert reason_codes == ["unsupported_action:POST"]


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


def test_preflop_bb_without_action_history_returns_non_displayable_deferred() -> None:
    """BB with no preflop action history returns an internal wait state."""
    engine = make_real_chart_engine()
    state = make_state(
        phase="preflop",
        active_player_count=6,
        hero_cards=["9h", "6h"],
    )
    state.hero.position = "BB"
    state.hero.bet = 100

    recommendation = engine.generate(state, preflop_actions=[])

    assert recommendation.action == "PREFLOP_ACTION_HISTORY_PENDING"
    assert recommendation.amount == 0
    assert recommendation.strategy_source == "preflop_deferred"
    assert "アクション履歴収集中" not in recommendation.reason
    assert recommendation.action_probabilities == {}


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


def test_multiway_call_zero_gets_effective_call_amount() -> None:
    """Multiway CALL fallback caps the computed call by Hero stack."""
    engine = make_engine()
    state = make_state(phase="flop", active_player_count=3)
    state.hero.stack = 5442
    state.hero.bet = 0
    state.players["2"].bet = 42976

    amount = engine._ensure_multiway_amount(0, "CALL", state)

    assert amount == 5442


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


def test_zero_cost_call_recommendation_converted_to_check(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """CALL recommendations become CHECK when Hero already matched max bet."""
    engine = make_engine()
    engine.preflop_chart.get_scenario.return_value = "BB_limp"
    engine.preflop_chart.get_recommendation.return_value = {
        "action": "call",
        "amount": 100,
        "confidence": "medium",
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
    assert recommendation.amount == 0
    assert recommendation.amount_bb is None
    assert recommendation.pot_percentage is None
    assert recommendation.preset_hint is None
    assert recommendation.raise_multiplier is None
    assert recommendation.raise_multiplier_label is None
    assert "CALL -> CHECK conversion: hero_bet(100) >= max_bet(100)" in caplog.text


def test_call_recommendation_kept_when_additional_cost_exists() -> None:
    """CALL recommendations remain CALL when Hero owes more chips."""
    engine = make_engine()
    engine.preflop_chart.get_scenario.return_value = "vs_raise"
    engine.preflop_chart.get_recommendation.return_value = {
        "action": "call",
        "amount": 300,
        "confidence": "medium",
        "source": "preflop_chart",
    }
    state = make_state(phase="preflop", active_player_count=6)
    state.buttons = ButtonState(call_or_check="call")
    state.hero.bet = 100
    state.players["2"].bet = 300

    recommendation = engine.generate(state)

    assert recommendation.action == "CALL"
    assert recommendation.amount == 300


@pytest.mark.parametrize("action_name", ["BET", "RAISE", "ALL_IN"])
def test_non_call_sized_recommendations_ignore_zero_cost_call_constraint(
    action_name: str,
) -> None:
    """BET/RAISE/ALL_IN recommendations are not affected by CALL constraints."""
    engine = make_engine()
    engine.preflop_chart.get_scenario.return_value = "sized_action"
    engine.preflop_chart.get_recommendation.return_value = {
        "action": action_name.lower(),
        "amount": 300,
        "confidence": "medium",
        "source": "preflop_chart",
    }
    state = make_state(phase="preflop", active_player_count=6)
    state.buttons = ButtonState(call_or_check="call")
    state.hero.bet = 100
    state.players["2"].bet = 100

    recommendation = engine.generate(state)

    assert recommendation.action == action_name


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


def test_stable_hu_postflop_input_calls_solver() -> None:
    """Stable HU postflop input reaches the Solver bridge."""
    engine = make_engine()
    state = make_state(phase="flop", active_player_count=2)
    engine.solver_request_builder.build_request.return_value = {
        "board": "Td7c2h",
        "timeout_ms": 12000,
        "effective_stack": 4000,
        "starting_pot": 600,
        "actions_played": [],
    }
    engine.solver_bridge.solve.return_value = {
        "success": True,
        "root_strategy": {
            "actions": ["Check", "Bet 120"],
            "average_strategy": {"Check": 0.25, "Bet 120": 0.75},
        },
    }

    recommendation = engine.generate(state, {"2": {"total_hands": 1}})

    assert recommendation.strategy_source == "solver"
    engine.solver_bridge.solve.assert_called_once()


def test_active_position_mismatch_blocks_solver_start(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """HU Solver is blocked when active seats and position lock disagree."""
    engine = make_engine()
    state = make_state(phase="flop", active_player_count=2)
    state.players["3"] = PlayerState(
        name="p3",
        stack=3000,
        bet=0,
        is_seated=True,
        in_current_hand=True,
    )

    with caplog.at_level(logging.INFO, logger="strategy.recommendation_engine"):
        recommendation = engine.generate(state, {"2": {"total_hands": 1}})

    assert recommendation.strategy_source == "solver_input_unstable"
    assert recommendation.action == "SOLVER_INPUT_UNSTABLE"
    engine.solver_bridge.solve.assert_not_called()
    assert "HU_SOLVER_POSITION_INPUT_CHECK" in caplog.text
    assert "active_position_mismatch" in caplog.text
    assert "HU_SOLVER_START_BLOCKED" in caplog.text


def test_unstable_actions_played_blocks_solver_start(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unconvertible street history blocks Solver launch without fallback action."""
    engine = make_engine()
    state = make_state(phase="flop", active_player_count=2)
    state.current_street_actions = [ActionRecord(seat=2, action="POST", amount=100)]

    with caplog.at_level(logging.INFO, logger="strategy.recommendation_engine"):
        recommendation = engine.generate(state, {"2": {"total_hands": 1}})

    assert recommendation.strategy_source == "solver_input_unstable"
    assert recommendation.action == "SOLVER_INPUT_UNSTABLE"
    engine.solver_bridge.solve.assert_not_called()
    assert "HU_SOLVER_ACTIONS_PLAYED_BUILD" in caplog.text
    assert "actions_played_unstable" in caplog.text


def test_headsup_solver_timeout_from_request(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """solve() is called with timeout derived from request timeout_ms."""
    engine = make_engine()
    state = make_state(phase="flop", active_player_count=2)
    engine.llm_pipeline.get_baseline_range.side_effect = ["OOP_RANGE", "IP_RANGE"]
    engine.solver_request_builder.build_request.return_value = {
        "board": "Td7c2h",
        "timeout_ms": 20000,
        "effective_stack": 4000,
        "starting_pot": 600,
        "actions_played": ["BET 300"],
    }
    engine.solver_bridge.solve.return_value = {
        "success": True,
        "root_strategy": {
            "actions": ["Check", "Bet 120"],
            "average_strategy": {"Check": 0.25, "Bet 120": 0.75},
        },
    }

    with caplog.at_level(logging.INFO, logger="strategy.recommendation_engine"):
        recommendation = engine.generate(
            state,
            {"2": {"vpip": 30, "total_hands": 49}},
        )

    assert recommendation.strategy_source == "solver"
    engine.solver_bridge.solve.assert_called_once()
    call_kwargs = engine.solver_bridge.solve.call_args[1]
    assert call_kwargs["timeout"] == 22.0
    assert "timeout_ms=20000" in caplog.text
    assert "bridge_timeout_sec=22.0" in caplog.text
    assert "HU solver success" in caplog.text
    assert "HU solver parse result" in caplog.text


def test_headsup_solver_timeout_default_when_missing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When timeout_ms is missing from request, default 12000ms → 14.0s."""
    engine = make_engine()
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

    with caplog.at_level(logging.INFO, logger="strategy.recommendation_engine"):
        recommendation = engine.generate(
            state,
            {"2": {"vpip": 30, "total_hands": 49}},
        )

    assert recommendation.strategy_source == "solver"
    engine.solver_bridge.solve.assert_called_once()
    call_kwargs = engine.solver_bridge.solve.call_args[1]
    assert call_kwargs["timeout"] == pytest.approx(14.0)
    assert call_kwargs["timeout"] >= 12.0
    assert "timeout_ms=12000" in caplog.text
    assert "bridge_timeout_sec=14.0" in caplog.text


def test_headsup_solver_failure_uses_correct_timeout(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Solver failure fallback still passes the correct bridge timeout."""
    engine = make_engine()
    state = make_state(phase="flop", active_player_count=2)
    engine.llm_pipeline.get_baseline_range.side_effect = ["OOP_RANGE", "IP_RANGE"]
    engine.solver_request_builder.build_request.return_value = {
        "board": "Td7c2h",
        "timeout_ms": 20000,
    }
    engine.solver_bridge.solve.return_value = {
        "success": False,
        "error": "Solver timeout (no response within 22.0s)",
    }

    with caplog.at_level(logging.INFO, logger="strategy.recommendation_engine"):
        recommendation = engine.generate(
            state,
            {"2": {"vpip": 30, "total_hands": 49}},
        )

    engine.solver_bridge.solve.assert_called_once()
    call_kwargs = engine.solver_bridge.solve.call_args[1]
    assert recommendation.strategy_source == "solver_timeout"
    assert recommendation.confidence == "low"
    assert recommendation.reason == "Solver timeout: no reliable solver result"
    assert call_kwargs["timeout"] == 22.0
    assert "timeout_ms=20000" in caplog.text
    assert "bridge_timeout_sec=22.0" in caplog.text
    assert "HU solver failed" in caplog.text
    assert "HU solver fallback reason=solver_failed" in caplog.text
    assert "Solver timeout" in caplog.text
    assert "error=Solver timeout" in caplog.text
    assert "HU_SOLVER_RESULT_DETAIL" in caplog.text


def test_deep_spr_primary_and_light_success_logs_compare(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Deep-SPR light probe is not run synchronously in the live path."""
    engine = make_engine(DEEP_SPR_CONFIG)
    state = make_state(phase="flop", active_player_count=2)
    state.hand_id = 31
    engine.llm_pipeline.get_baseline_range.side_effect = ["OOP_RANGE", "IP_RANGE"]
    primary_request = {
        "board": "Td7c2h",
        "timeout_ms": 20000,
        "effective_stack": 10000,
        "starting_pot": 500,
        "actions_played": [],
    }

    def build_request(*_args: object, **kwargs: object) -> dict[str, object]:
        assert kwargs.get("profile") != "deep_spr_light_probe"
        return primary_request

    engine.solver_request_builder.build_request.side_effect = build_request
    engine.solver_bridge.solve.return_value = {
        "success": True,
        "root_strategy": {
            "actions": ["Check", "Bet 120"],
            "average_strategy": {"Check": 0.25, "Bet 120": 0.75},
        },
    }

    with caplog.at_level(logging.INFO, logger="strategy.recommendation_engine"):
        recommendation = engine.generate(state, {"2": {"total_hands": 1}})

    assert recommendation.strategy_source == "solver"
    assert recommendation.action == "BET"
    assert recommendation.amount == 120
    assert engine.solver_bridge.solve.call_count == 1
    assert "DEEP_SPR_SOLVER_PRIMARY_RESULT" in caplog.text
    assert "DEEP_SPR_LIGHT_PROBE_SKIPPED" in caplog.text
    assert "reason=disabled_in_live_sync_path" in caplog.text
    assert "DEEP_SPR_LIGHT_SOLVER_RESULT" not in caplog.text
    assert "DEEP_SPR_SOLVER_COMPARE" not in caplog.text


def test_deep_spr_primary_timeout_light_success_logs_compare(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Primary timeout does not trigger a synchronous light probe."""
    engine = make_engine(DEEP_SPR_CONFIG)
    state = make_state(phase="flop", active_player_count=2)
    state.hand_id = 32
    engine.llm_pipeline.get_baseline_range.side_effect = ["OOP_RANGE", "IP_RANGE"]
    primary_request = {
        "board": "Td7c2h",
        "timeout_ms": 20000,
        "effective_stack": 10000,
        "starting_pot": 500,
        "actions_played": [],
    }

    def build_request(*_args: object, **kwargs: object) -> dict[str, object]:
        assert kwargs.get("profile") != "deep_spr_light_probe"
        return primary_request

    engine.solver_request_builder.build_request.side_effect = build_request
    engine.solver_bridge.solve.return_value = {"success": False, "error": "timeout"}

    with caplog.at_level(logging.INFO, logger="strategy.recommendation_engine"):
        recommendation = engine.generate(state, {"2": {"total_hands": 1}})

    assert recommendation.strategy_source == "solver_timeout"
    assert recommendation.action == "SOLVER_TIMEOUT"
    assert engine.solver_bridge.solve.call_count == 1
    assert "DEEP_SPR_LIGHT_PROBE_SKIPPED" in caplog.text
    assert "reason=disabled_in_live_sync_path" in caplog.text
    assert "DEEP_SPR_LIGHT_SOLVER_RESULT" not in caplog.text
    assert "comparison_type=primary_timeout_light_success" not in caplog.text


def test_deep_spr_light_probe_disabled_does_not_call_light_solver() -> None:
    """Light probe is skipped entirely when disabled."""
    config = {
        "game": {"blind_bb": 100},
        "preflop_delta": {"sample_threshold_low": 50},
        "solver": {
            "deep_spr_threshold": 10.0,
            "deep_spr_light_probe_enabled": False,
        },
    }
    engine = make_engine(config)
    state = make_state(phase="flop", active_player_count=2)
    engine.llm_pipeline.get_baseline_range.side_effect = ["OOP_RANGE", "IP_RANGE"]
    engine.solver_request_builder.build_request.return_value = {
        "board": "Td7c2h",
        "timeout_ms": 20000,
        "effective_stack": 10000,
        "starting_pot": 500,
        "actions_played": [],
    }
    engine.solver_bridge.solve.return_value = {
        "success": True,
        "root_strategy": {
            "actions": ["Check", "Bet 120"],
            "average_strategy": {"Check": 0.25, "Bet 120": 0.75},
        },
    }

    recommendation = engine.generate(state, {"2": {"total_hands": 1}})

    assert recommendation.strategy_source == "solver"
    assert engine.solver_bridge.solve.call_count == 1


def test_headsup_solver_unavailable_logs_fallback_reason(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Missing solver bridge logs the solver_unavailable reason."""
    engine = make_engine()
    engine.solver_bridge = None
    state = make_state(phase="flop", active_player_count=2)

    with caplog.at_level(logging.INFO, logger="strategy.recommendation_engine"):
        recommendation = engine.generate(state, {"2": {"total_hands": 1}})

    assert recommendation.strategy_source == "fallback"
    assert "HU solver fallback reason=solver_unavailable" in caplog.text
    assert "HU fallback entered" in caplog.text
    assert "reason=Solver unavailable" in caplog.text


def test_headsup_solver_request_unavailable_logs_fallback_reason(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Missing solver request logs request context before fallback."""
    engine = make_engine()
    state = make_state(phase="flop", active_player_count=2)
    state.hand_id = 8
    engine.solver_request_builder.build_request.return_value = None

    with caplog.at_level(logging.INFO, logger="strategy.recommendation_engine"):
        recommendation = engine.generate(state, {"2": {"total_hands": 1}})

    assert recommendation.strategy_source == "fallback"
    assert "HU solver fallback reason=request_unavailable" in caplog.text
    assert "hand_id=8" in caplog.text
    assert "reason=Solver request unavailable" in caplog.text
    assert "SOLVER_REQUEST_UNAVAILABLE_DETAIL" in caplog.text


def test_all_in_request_available_still_uses_solver() -> None:
    """Facing ALL-IN still tries and uses Solver when request can be built."""
    engine = make_engine()
    state = make_state(phase="flop", active_player_count=2)
    state.hero.bet = 100
    state.current_street_actions = [
        ActionRecord(seat=2, action="ALL_IN", amount=1000),
    ]
    engine.llm_pipeline.get_baseline_range.side_effect = ["OOP_RANGE", "IP_RANGE"]
    engine.solver_request_builder.build_request.return_value = {
        "board": "Td7c2h",
        "timeout_ms": 12000,
        "effective_stack": 5000,
        "starting_pot": 600,
        "actions_played": ["Bet 100", "Raise 1000"],
    }
    engine.solver_bridge.solve.return_value = {
        "success": True,
        "root_strategy": {
            "actions": ["Fold", "Call"],
            "average_strategy": {"Fold": 0.7, "Call": 0.3},
        },
    }

    recommendation = engine.generate(state, {"2": {"total_hands": 1}})

    assert recommendation.strategy_source == "solver"
    engine.solver_bridge.solve.assert_called_once()


def test_all_in_request_unavailable_uses_pot_odds_without_solver(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Facing ALL-IN uses math-only fallback only when request is unavailable."""
    engine = make_engine()
    state = make_state(phase="flop", active_player_count=2)
    state.hand_id = 33
    state.hero.bet = 100
    state.pot = 600
    state.current_street_actions = [
        ActionRecord(seat=2, action="ALL_IN", amount=1000),
    ]
    engine.solver_request_builder.build_request.return_value = None
    engine.solver_request_builder.diagnose_request_unavailable.return_value = {
        "reason_codes": ["facing_all_in", "effective_stack_missing"],
    }

    with caplog.at_level(logging.INFO, logger="strategy.recommendation_engine"):
        recommendation = engine.generate(state, {"2": {"total_hands": 1}})

    assert recommendation.strategy_source == "all_in_pot_odds"
    assert recommendation.action == "FOLD"
    assert "call_amount=900" in recommendation.reason
    assert "pot_after_call=1500" in recommendation.reason
    assert "必要勝率は約60%" in recommendation.reason
    engine.solver_bridge.solve.assert_not_called()
    assert "SOLVER_REQUEST_UNAVAILABLE_DETAIL" in caplog.text
    assert "HU_ALL_IN_DECISION_CONTEXT" in caplog.text


def test_headsup_solver_parse_exception_logs_fallback_reason(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Solver parse exceptions are logged with a parse_exception reason."""
    engine = make_engine()
    state = make_state(phase="flop", active_player_count=2)
    engine.solver_request_builder.build_request.return_value = {"board": "Td7c2h"}
    engine.solver_bridge.solve.return_value = {
        "success": True,
        "root_strategy": {"actions": ["Check"], "average_strategy": {"Check": 1.0}},
    }

    def raise_parse(_solver_output: dict, _state: GameState) -> tuple[str, int, dict]:
        raise ValueError("parse boom")

    monkeypatch.setattr(engine, "_parse_solver_strategy", raise_parse)

    with caplog.at_level(logging.INFO, logger="strategy.recommendation_engine"):
        recommendation = engine.generate(state, {"2": {"total_hands": 1}})

    assert recommendation.strategy_source == "fallback"
    assert "HU solver fallback reason=parse_exception" in caplog.text
    assert "parse boom" in caplog.text


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
    assert recommendation.reason.startswith("Enough hands to exploit.")
    assert "Solver: BET 120 80% / CHECK 20%" in recommendation.reason
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
    assert recommendation.reason.startswith("HU solver recommendation")
    assert "Solver: BET 120 80% / CHECK 20%" in recommendation.reason


def test_solver_recommendation_reason_includes_compact_mix() -> None:
    """Solver recommendations include top solver probabilities in the reason."""
    engine = make_engine()
    state = make_state(phase="flop", active_player_count=2)
    engine.llm_pipeline.get_baseline_range.side_effect = ["OOP_RANGE", "IP_RANGE"]
    engine.solver_request_builder.build_request.return_value = {"board": "Td7c2h"}
    engine.solver_bridge.solve.return_value = {
        "success": True,
        "root_strategy": {
            "actions": ["Fold", "Call", "All_in 900", "Raise 1800"],
            "average_strategy": {
                "Fold": 0.52,
                "Call": 0.31,
                "All_in 900": 0.17,
                "Raise 1800": 0.01,
            },
        },
    }

    recommendation = engine.generate(state, {"2": {"total_hands": 1}})

    assert recommendation.strategy_source == "solver"
    assert "Solver: FOLD 52% / CALL 31% / ALL-IN 900 17%" in recommendation.reason
    assert recommendation.action_probabilities == {
        "FOLD": 0.52,
        "CALL": 0.31,
        "ALL_IN 900": 0.17,
        "RAISE 1800": 0.01,
    }


def test_non_solver_recommendation_reason_is_not_modified() -> None:
    """Non-solver recommendation reasons are not mixed with solver probabilities."""
    recommendation = Recommendation(
        action="CHECK",
        reason="Fallback reason",
        strategy_source="fallback",
        action_probabilities={"CHECK": 1.0},
    )

    assert recommendation.reason == "Fallback reason"


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
    state.current_street_actions = [
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
    assert len(files) == 2
    debug_file = next(path for path in files if path.name.startswith("hand_000004_flop_"))
    request_file = next(
        path for path in files if path.name.startswith("hand_000004_req_")
    )
    debug_data = json.loads(debug_file.read_text(encoding="utf-8"))
    request_data = json.loads(request_file.read_text(encoding="utf-8"))
    assert request_data["meta"]["hand_id"] == 4
    assert request_data["meta"]["phase"] == "flop"
    assert request_data["meta"]["reason"] == "hu_postflop_solver"
    assert request_data["request"] == solver_request
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


def test_solver_request_json_saved_before_solve(workspace_tmp: Path) -> None:
    """The exact Solver request JSON is saved before invoking solve()."""
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
    state.hand_id = 9
    solver_request = {"board": "Td7c2h", "range_oop": "OOP_RANGE"}
    engine.llm_pipeline.get_baseline_range.side_effect = ["OOP_RANGE", "IP_RANGE"]
    engine.solver_request_builder.build_request.return_value = solver_request
    engine.solver_bridge.solve.return_value = {"success": False, "error": "timeout"}

    engine.generate(state, {"2": {"vpip": 30, "total_hands": 49}})

    request_files = [
        path
        for path in (workspace_tmp / "solver_io").rglob("*.json")
        if path.name.startswith("hand_000009_req_")
    ]
    assert len(request_files) == 1
    saved = json.loads(request_files[0].read_text(encoding="utf-8"))
    assert saved["meta"]["hand_id"] == 9
    assert saved["meta"]["reason"] == "hu_postflop_solver"
    assert saved["request"] == solver_request


def test_solver_request_json_meta_includes_range_and_action_context(
    workspace_tmp: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Solver request JSON meta captures range and action normalization context."""
    config = {
        **SOLVER_BUILDER_CONFIG,
        "preflop_delta": {"sample_threshold_low": 50},
        "debug": {
            "save_solver_io": True,
            "solver_io_dir": str(workspace_tmp / "solver_io"),
        },
    }
    engine = make_engine(config)
    engine.solver_request_builder = SolverRequestBuilder(config)
    state = make_state(phase="flop", active_player_count=2)
    state.hand_id = 10
    state.hero.position = "BB"
    state.hero.stack = 5000
    state.pot = 600
    state.players["2"].stack = 5000
    state.preflop_actions = [
        ActionRecord(seat=2, action="RAISE", amount=200),
        ActionRecord(seat=1, action="CALL", amount=100),
    ]
    engine.llm_pipeline.get_baseline_range.side_effect = ["OOP_RANGE", "IP_RANGE"]
    engine.solver_bridge.solve.return_value = {
        "success": False,
        "error": "timeout",
    }

    with caplog.at_level(logging.INFO):
        engine.generate(state, {"2": {"vpip": 30, "total_hands": 49}})

    request_file = next(
        path
        for path in (workspace_tmp / "solver_io").rglob("*.json")
        if path.name.startswith("hand_000010_req_")
        and "compare_no_allin" not in path.name
    )
    saved = json.loads(request_file.read_text(encoding="utf-8"))
    meta = saved["meta"]
    assert meta["hero_position"] == "BB"
    assert meta["hero_is_ip"] is False
    assert meta["active_seats"] == [1, 2]
    assert meta["range_source"] == "baseline"
    assert isinstance(meta["range_oop"], str)
    assert isinstance(meta["range_ip"], str)
    assert meta["range_oop"]
    assert meta["range_ip"]
    assert meta["actions_played_status"] == "empty_ok"
    assert meta["street_start_pot"] == 600
    assert meta["street_start_effective_stack"] == 5000
    assert meta["spr"] == pytest.approx(5000 / 600)
    assert meta["normalized_preflop_actions"][1]["action"] == "CALL"
    assert "HU_SOLVER_RANGE_CONTEXT" in caplog.text
    assert "PREFLOP_ACTION_NORMALIZATION_SUMMARY" in caplog.text


def test_save_solver_request_json_includes_hero_cards_and_call_context(
    workspace_tmp: Path,
) -> None:
    """Saved Solver request JSON includes hero cards and facing-bet context."""
    config = {
        "game": {"blind_bb": 100},
        "preflop_delta": {"sample_threshold_low": 50},
        "debug": {
            "save_solver_io": True,
            "solver_io_dir": str(workspace_tmp / "solver_io"),
        },
    }
    engine = make_engine(config)
    state = make_state(phase="flop", active_player_count=2, hero_cards=["Ah", "Kh"])
    state.hand_id = 21
    state.hero.position = "BB"
    state.hero.bet = 0
    state.players["2"].bet = 300
    solver_request = {"board": "Td7c2h", "effective_stack": 8883}

    path = engine._save_solver_request_json(state, solver_request, "unit_test")

    assert path is not None
    saved = json.loads(Path(path).read_text(encoding="utf-8"))
    meta = saved["meta"]
    assert meta["hero_cards"] == ["Ah", "Kh"]
    assert meta["facing_bet"] is True
    assert meta["call_amount"] == 300
    assert meta["raw_call_amount"] == 300
    assert meta["street"] == "flop"
    assert meta["heads_up"] is True
    assert meta["num_players"] == 2


def test_save_solver_request_json_includes_position_and_actions(
    workspace_tmp: Path,
) -> None:
    """Saved Solver request JSON includes position and action context."""
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
    state.hand_id = 22
    state.hero.position = "BTN"
    state.current_street_actions = [
        ActionRecord(seat=2, action="CHECK", amount=0),
    ]
    state.preflop_actions = [
        ActionRecord(seat=2, action="CALL", amount=100),
        ActionRecord(seat=1, action="CHECK", amount=0),
    ]

    path = engine._save_solver_request_json(state, {"board": "Td7c2h"}, "unit_test")

    assert path is not None
    meta = json.loads(Path(path).read_text(encoding="utf-8"))["meta"]
    assert meta["hero_position"] == "BTN"
    assert meta["hero_is_ip"] is True
    assert meta["current_street_actions"][0]["action"] == "CHECK"
    assert meta["preflop_actions"][0]["action"] == "CALL"


def test_solver_request_meta_incomplete_warns_when_hero_cards_missing(
    workspace_tmp: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Incomplete Solver request meta emits a warning but still saves."""
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
    state.hand_id = 23
    state.hero.cards = []

    with caplog.at_level(logging.WARNING):
        path = engine._save_solver_request_json(state, {"board": "Td7c2h"}, "unit_test")

    assert path is not None
    assert "SOLVER_REQUEST_META_INCOMPLETE" in caplog.text
    assert "hero_cards" in caplog.text


def test_deep_spr_flop_compare_no_allin_request_saved_not_solved(
    workspace_tmp: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Deep-SPR flop root saves a no-all-in comparison request only."""
    config = {
        **SOLVER_BUILDER_CONFIG,
        "preflop_delta": {"sample_threshold_low": 50},
        "debug": {
            "save_solver_io": True,
            "solver_io_dir": str(workspace_tmp / "solver_io"),
        },
    }
    engine = make_engine(config)
    engine.solver_request_builder = SolverRequestBuilder(config)
    state = make_state(phase="flop", active_player_count=2)
    state.hand_id = 11
    state.hero.stack = 10000
    state.pot = 500
    state.players["2"].stack = 10000
    engine.llm_pipeline.get_baseline_range.side_effect = ["OOP_RANGE", "IP_RANGE"]
    engine.solver_bridge.solve.return_value = {
        "success": False,
        "error": "timeout",
    }

    with caplog.at_level(logging.INFO):
        engine.generate(state, {"2": {"vpip": 30, "total_hands": 49}})

    compare_file = next(
        path
        for path in (workspace_tmp / "solver_io").rglob("*.json")
        if "compare_no_allin" in path.name
    )
    compare_data = json.loads(compare_file.read_text(encoding="utf-8"))
    assert compare_data["request"]["flop_bet_sizes_oop"] == "60%"
    assert compare_data["request"]["flop_bet_sizes_ip"] == "60%"
    solved_request = engine.solver_bridge.solve.call_args.args[0]
    assert solved_request["flop_bet_sizes_oop"] == "60%,a"
    assert solved_request["flop_bet_sizes_ip"] == "60%,a"
    assert solved_request is not compare_data["request"]
    assert "DEEP_SPR_FLOP_COMPARISON_REQUEST_SAVED" in caplog.text


@pytest.mark.parametrize(
    ("phase", "pot"),
    [("flop", 2000), ("turn", 500)],
)
def test_compare_no_allin_request_not_saved_for_ineligible_spots(
    workspace_tmp: Path,
    phase: str,
    pot: int,
) -> None:
    """Comparison request is saved only for deep-SPR flop root spots."""
    config = {
        **SOLVER_BUILDER_CONFIG,
        "preflop_delta": {"sample_threshold_low": 50},
        "debug": {
            "save_solver_io": True,
            "solver_io_dir": str(workspace_tmp / "solver_io"),
        },
    }
    engine = make_engine(config)
    engine.solver_request_builder = SolverRequestBuilder(config)
    state = make_state(phase=phase, active_player_count=2)
    state.hand_id = 12
    state.pot = pot
    state.hero.stack = 10000
    state.players["2"].stack = 10000
    if phase == "turn":
        state.board = ["Td", "7c", "2h", "As"]
        state.board_card_count = 4
    engine.llm_pipeline.get_baseline_range.side_effect = ["OOP_RANGE", "IP_RANGE"]
    engine.solver_bridge.solve.return_value = {
        "success": False,
        "error": "timeout",
    }

    engine.generate(state, {"2": {"vpip": 30, "total_hands": 49}})

    compare_files = [
        path
        for path in (workspace_tmp / "solver_io").rglob("*.json")
        if "compare_no_allin" in path.name
    ]
    assert compare_files == []


def test_reset_solver_process_delegates_to_bridge() -> None:
    """RecommendationEngine reset wrapper delegates to solver_bridge.reset_process."""
    engine = make_engine()
    engine.solver_bridge.reset_process.return_value = True

    assert engine.reset_solver_process("hero_turn_ended") is True
    engine.solver_bridge.reset_process.assert_called_once_with("hero_turn_ended")


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


def test_parse_solver_strategy_uses_hero_hand_row() -> None:
    """Diagnostic parser reports hand_strategy when hero hand row is used."""
    engine = make_engine()
    solver_output = {
        "root_strategy": {
            "actions": ["Check", "Bet 120"],
            "hands": ["AhKh", "QdQs"],
            "strategy_matrix": [[0.05, 0.95], [0.95, 0.05]],
            "average_strategy": {"Check": 0.9, "Bet 120": 0.1},
        }
    }

    result = engine._parse_solver_strategy_with_diagnostics(
        solver_output,
        make_state(hero_cards=["Ah", "Kh"]),
    )

    assert result["action"] == "BET"
    assert result["strategy_source_detail"] == "hand_strategy"
    assert result["matched_hand"] == "AhKh"
    assert result["matched_hand_index"] == 0


def test_parse_solver_strategy_falls_back_to_average_when_hero_hand_missing() -> None:
    """Diagnostic parser reports average fallback when hero hand row is absent."""
    engine = make_engine()
    solver_output = {
        "root_strategy": {
            "actions": ["Check", "Bet 120"],
            "hands": ["QdQs"],
            "strategy_matrix": [[0.1, 0.9]],
            "average_strategy": {"Check": 0.9, "Bet 120": 0.1},
        }
    }

    result = engine._parse_solver_strategy_with_diagnostics(
        solver_output,
        make_state(hero_cards=["Ah", "Kh"]),
    )

    assert result["action"] == "CHECK"
    assert result["strategy_source_detail"] == "average_strategy_fallback"
    assert "hero hand not found" in str(result["fallback_reason"])


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


def test_safety_guard_blocks_call_vs_large_bet() -> None:
    """Safety guard converts weak-hand CALL to FOLD facing a huge bet."""
    engine = make_engine()
    engine.preflop_chart.get_scenario.return_value = "vs_all_in"
    engine.preflop_chart.get_recommendation.return_value = {
        "action": "call",
        "amount": 9984,
        "confidence": "high",
        "source": "preflop_chart",
    }
    engine.preflop_chart.hand_to_generic.side_effect = PreflopChart.hand_to_generic
    state = make_state(
        phase="preflop",
        active_player_count=6,
        hero_cards=["8c", "6c"],
    )
    state.hero.stack = 10000
    state.players["2"].bet = 60000

    recommendation = engine.generate(state)

    assert recommendation.action == "FOLD"
    assert recommendation.amount == 0
    assert recommendation.strategy_source == "preflop_chart"


def test_safety_guard_allows_premium_call() -> None:
    """Safety guard keeps premium-hand CALL facing a huge bet."""
    engine = make_engine()
    engine.preflop_chart.get_scenario.return_value = "vs_all_in"
    engine.preflop_chart.get_recommendation.return_value = {
        "action": "call",
        "amount": 9984,
        "confidence": "high",
        "source": "preflop_chart",
    }
    engine.preflop_chart.hand_to_generic.side_effect = PreflopChart.hand_to_generic
    state = make_state(
        phase="preflop",
        active_player_count=6,
        hero_cards=["As", "Ac"],
    )
    state.hero.stack = 10000
    state.players["2"].bet = 60000

    recommendation = engine.generate(state)

    assert recommendation.action == "CALL"
    assert recommendation.strategy_source == "preflop_chart"
