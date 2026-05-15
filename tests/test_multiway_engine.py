"""Tests for the multiway decision engine."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from core.game_state import ActionRecord, GameState, HeroState, PlayerState
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


def test_calculate_equity_with_range() -> None:
    """Range-based equity calculation returns a bounded float."""
    engine = make_engine()
    engine.mc_samples = 200

    equity = engine.calculate_equity(
        ["Ah", "As"],
        ["Td", "7c", "2h"],
        1,
        "77+,ATs+",
    )

    assert 0.0 <= equity <= 1.0


def test_calculate_equity_without_range() -> None:
    """Equity calculation without a range keeps random-opponent behavior."""
    engine = make_engine()
    engine.mc_samples = 200

    equity = engine.calculate_equity(
        ["Ah", "As"],
        ["Td", "7c", "2h"],
        1,
        None,
    )

    assert 0.0 <= equity <= 1.0


def test_hand_matches_range_pair_plus() -> None:
    """Pair plus notation includes higher pairs."""
    assert MultiwayEngine._hand_matches_range("TT", "77+")


def test_hand_matches_range_suited_plus() -> None:
    """Suited plus notation includes higher kickers."""
    assert MultiwayEngine._hand_matches_range("ATs", "A9s+")


def test_hand_matches_range_no_match() -> None:
    """Hands outside the simplified range return False."""
    assert not MultiwayEngine._hand_matches_range("72o", "77+,ATs+")


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
    assert elapsed_ms < 150


# ---------------------------------------------------------------------------
# Phase 30-Fix30: Multiway stability tests
# ---------------------------------------------------------------------------


def make_hand9_state() -> GameState:
    """Create a Hand 9 reproduction: Kh Ks on 9d 5d Jh flop, 3-way.

    Seat5 BET 498, Seat2 CALL 498. Hero BTN with Kh Ks facing 498 call.
    Pot = 1992.
    """
    players = GameState.create_default_players()
    players["2"] = PlayerState(
        name="Seat2",
        stack=5000,
        bet=498,
        is_seated=True,
        in_current_hand=True,
        cards_visible=True,
    )
    players["3"] = PlayerState(
        name="Seat3",
        stack=0,
        bet=0,
        is_seated=False,
        in_current_hand=False,
    )
    players["4"] = PlayerState(
        name="Seat4",
        stack=0,
        bet=0,
        is_seated=False,
        in_current_hand=False,
    )
    players["5"] = PlayerState(
        name="Seat5",
        stack=5000,
        bet=498,
        is_seated=True,
        in_current_hand=True,
        cards_visible=True,
    )
    players["6"] = PlayerState(
        name="Seat6",
        stack=0,
        bet=0,
        is_seated=False,
        in_current_hand=False,
    )

    full_street_actions = [
        ActionRecord(seat=5, action="BET", amount=498, confidence="high"),
        ActionRecord(seat=2, action="CALL", amount=498, confidence="high"),
    ]

    return GameState(
        phase="flop",
        hero=HeroState(
            seat=1,
            position="BTN",
            cards=["Kh", "Ks"],
            stack=10000,
            bet=0,
            is_my_turn=True,
        ),
        board=["9d", "5d", "Jh"],
        board_card_count=3,
        pot=1992,
        players=players,
        dealer_seat=1,
        active_player_count=3,
        current_street_actions=full_street_actions,
    )


def test_hand9_fold_guard_overrides_llm_fold_to_call() -> None:
    """Hand 9: LLM returns FOLD, but equity 47.35% vs required 20% triggers guard."""
    state = make_hand9_state()
    engine = make_engine()
    # Mock equity to known Hand 9 value
    engine.calculate_equity = MagicMock(return_value=0.4735)  # type: ignore[method-assign]
    engine.llm.decide_multiway.return_value = {
        "action": "fold",
        "size": None,
        "confidence": "medium",
        "reasoning": "Multiway conservative fold",
        "raw_response": '{"action": "fold"}',
    }

    result = engine.evaluate(state, [{"total_hands": 50, "vpip": 30}, {"total_hands": 50, "vpip": 25}])

    normalized_action = result["action"].lower()
    assert normalized_action == "call", (
        f"Expected CALL, got {result['action']}. guard_applied={result.get('guard_applied')}"
    )
    assert result.get("guard_applied") is True
    # Size should be call_amount = 498
    size = int(result.get("size") or 0)
    assert size == 498, f"Expected call size 498, got {size}"


def test_fold_guard_does_not_override_when_equity_insufficient() -> None:
    """When equity < required_equity + margin, LLM FOLD is preserved."""
    state = make_hand9_state()
    engine = make_engine()
    engine.calculate_equity = MagicMock(return_value=0.30)  # type: ignore[method-assign]
    engine.llm.decide_multiway.return_value = {
        "action": "fold",
        "size": None,
        "confidence": "medium",
        "reasoning": "Insufficient equity",
        "raw_response": '{"action": "fold"}',
    }

    result = engine.evaluate(state, [{"total_hands": 50, "vpip": 30}])

    assert result["action"].lower() == "fold"
    assert result.get("guard_applied") is not True


def test_llm_prompt_includes_pot_odds_fields() -> None:
    """LLM prompt must include Call Amount, Required Equity, Facing Bet, etc."""
    state = make_hand9_state()
    engine = make_engine()
    engine.calculate_equity = MagicMock(return_value=0.4735)  # type: ignore[method-assign]
    engine.llm.decide_multiway.return_value = {
        "action": "call",
        "size": 498,
        "confidence": "medium",
        "reasoning": "Good pot odds",
        "raw_response": "",
    }

    engine.evaluate(state, [{"total_hands": 50, "vpip": 30}])

    call_kwargs = engine.llm.decide_multiway.call_args.kwargs
    assert call_kwargs["call_amount"] == 498
    assert call_kwargs["facing_bet"] == 498
    assert call_kwargs["pot_after_call"] == 2490  # 1992 + 498
    assert abs(call_kwargs["required_equity"] - 0.20) < 0.01  # 498/2490 ≈ 0.20


def test_call_amount_is_capped_by_hero_stack() -> None:
    """Multiway call metrics use Hero's effective all-in call amount."""
    state = make_state()
    state.hero.stack = 5442
    state.hero.bet = 0
    state.pot = 13360
    state.active_player_count = 3
    state.players["5"].bet = 42976

    metrics = MultiwayEngine._compute_metrics(state)

    assert metrics["raw_call_amount"] == 42976
    assert metrics["call_amount"] == 5442
    assert metrics["effective_call_amount"] == 5442
    assert metrics["hero_stack"] == 5442
    assert metrics["pot_after_call"] == 18802
    assert metrics["required_equity"] == 5442 / 18802
    assert metrics["hero_call_is_all_in"] is True
    assert metrics["spr"] == pytest.approx(5442 / 13360)


def test_num_opponents_uses_active_player_count() -> None:
    """Equity opponent count follows active players, not stats list length."""
    state = make_state()
    state.active_player_count = 3
    opponent_stats_list = [
        {"total_hands": 50},
        {"total_hands": 40},
        {"total_hands": 30},
        {"total_hands": 20},
        {"total_hands": 10},
    ]

    assert MultiwayEngine._num_opponents(state, opponent_stats_list) == 2


def test_full_street_action_history_passed_to_llm() -> None:
    """BET and CALL from separate frames must both reach Multiway LLM."""
    state = make_hand9_state()
    engine = make_engine()
    engine.calculate_equity = MagicMock(return_value=0.5)  # type: ignore[method-assign]
    engine.llm.decide_multiway.return_value = {
        "action": "call",
        "size": 498,
        "confidence": "medium",
        "reasoning": "",
        "raw_response": "",
    }

    engine.evaluate(state, [])

    call_kwargs = engine.llm.decide_multiway.call_args.kwargs
    actions = call_kwargs.get("current_street_actions")
    assert actions is not None, "current_street_actions must not be None"
    actions_list = list(actions)
    assert len(actions_list) == 2
    # Seat5 BET 498 and Seat2 CALL 498 both present
    action_summary = [
        (a.seat, a.action, a.amount) for a in actions_list
    ]
    assert (5, "BET", 498) in action_summary
    assert (2, "CALL", 498) in action_summary


def test_multiway_guard_only_triggers_on_fold_with_bet() -> None:
    """FOLD guard only activates when LLM action is fold AND call_amount > 0.

    Existing normal cases (CHECK, CALL, BET) should not be affected.
    """
    state = make_hand9_state()
    engine = make_engine()
    engine.calculate_equity = MagicMock(return_value=0.47)  # type: ignore[method-assign]

    # Case 1: LLM returns CALL — guard must not trigger
    engine.llm.decide_multiway.return_value = {
        "action": "call",
        "size": 498,
        "confidence": "medium",
        "reasoning": "",
        "raw_response": "",
    }
    result = engine.evaluate(state, [])
    assert result["action"].lower() == "call"
    assert result.get("guard_applied") is not True

    # Case 2: LLM returns BET — guard must not trigger
    engine.llm.decide_multiway.return_value = {
        "action": "bet",
        "size": "60%",
        "confidence": "medium",
        "reasoning": "",
        "raw_response": "",
    }
    result = engine.evaluate(state, [])
    assert result["action"].lower() == "bet"
    assert result.get("guard_applied") is not True

    # Case 3: call_amount == 0 scenario — guard must not trigger even on FOLD
    no_bet_state = make_hand9_state()
    # Reset bets so call_amount = 0
    no_bet_state.players["2"].bet = 0
    no_bet_state.players["5"].bet = 0
    no_bet_state.current_street_actions = []
    engine.llm.decide_multiway.return_value = {
        "action": "fold",
        "size": None,
        "confidence": "medium",
        "reasoning": "",
        "raw_response": "",
    }
    result = engine.evaluate(no_bet_state, [])
    # When no bet to face, FOLD should remain FOLD (fallthrough to heuristic)
    # Or it could get converted by action constraints later
    assert result.get("guard_applied") is not True


def test_cumulative_actions_passed_to_llm_three_actions() -> None:
    """BET/CALL/RAISE cumulative actions reach the LLM via current_street_actions."""
    engine = make_engine()
    engine.calculate_equity = MagicMock(return_value=0.5)  # type: ignore[method-assign]
    engine.llm.decide_multiway.return_value = {
        "action": "call",
        "size": 1600,
        "confidence": "medium",
        "reasoning": "",
        "raw_response": "",
    }

    state = make_state()
    state.current_street_actions = [
        ActionRecord(seat=4, action="BET", amount=300, confidence="high"),
        ActionRecord(seat=3, action="CALL", amount=300, confidence="high"),
        ActionRecord(seat=2, action="RAISE", amount=1600, confidence="high"),
    ]
    state.players["5"] = PlayerState(
        name="p5", stack=4000, bet=1600, is_seated=True, in_current_hand=True,
    )

    engine.evaluate(state, [])

    call_kwargs = engine.llm.decide_multiway.call_args.kwargs
    actions = call_kwargs.get("current_street_actions")
    assert actions is not None
    actions_list = list(actions)
    assert len(actions_list) == 3

    action_summary = [(a.seat, a.action, a.amount) for a in actions_list]
    assert (4, "BET", 300) in action_summary
    assert (3, "CALL", 300) in action_summary
    assert (2, "RAISE", 1600) in action_summary


def test_llm_uses_current_street_actions_not_actions_since_last_frame() -> None:
    """LLM receives all current_street_actions, not just the latest frame actions."""
    engine = make_engine()
    engine.calculate_equity = MagicMock(return_value=0.5)  # type: ignore[method-assign]
    engine.llm.decide_multiway.return_value = {
        "action": "call",
        "size": 1600,
        "confidence": "medium",
        "reasoning": "",
        "raw_response": "",
    }

    state = make_state()
    # actions_since_last_frame has only 1 action (latest frame)
    state.actions_since_last_frame = [
        ActionRecord(seat=2, action="RAISE", amount=1600, confidence="high"),
    ]
    # current_street_actions has all 3 cumulative actions
    state.current_street_actions = [
        ActionRecord(seat=4, action="BET", amount=300, confidence="high"),
        ActionRecord(seat=3, action="CALL", amount=300, confidence="high"),
        ActionRecord(seat=2, action="RAISE", amount=1600, confidence="high"),
    ]
    state.players["5"] = PlayerState(
        name="p5", stack=4000, bet=1600, is_seated=True, in_current_hand=True,
    )

    engine.evaluate(state, [])

    call_kwargs = engine.llm.decide_multiway.call_args.kwargs
    actions = call_kwargs.get("current_street_actions")
    assert actions is not None
    actions_list = list(actions)

    # Should have all 3 actions, not just the 1 from actions_since_last_frame
    assert len(actions_list) == 3, (
        f"Expected 3 cumulative actions, got {len(actions_list)}. "
        "LLM should receive current_street_actions (accumulated), "
        "not just actions_since_last_frame (per-frame)."
    )

    action_summary = [(a.seat, a.action, a.amount) for a in actions_list]
    assert (4, "BET", 300) in action_summary
    assert (3, "CALL", 300) in action_summary
    assert (2, "RAISE", 1600) in action_summary
