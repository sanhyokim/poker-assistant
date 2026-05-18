"""Tests for building postflop solver requests from GameState."""

from __future__ import annotations

from typing import Any

from core.game_state import ActionRecord, GameState, HeroState, PlayerState
from strategy.solver_request_builder import SolverRequestBuilder


TEST_CONFIG: dict[str, Any] = {
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
    "game": {
        "blind_bb": 100,
    },
}


def make_builder() -> SolverRequestBuilder:
    """Create a SolverRequestBuilder for tests."""
    return SolverRequestBuilder(TEST_CONFIG)


def make_state(
    phase: str = "flop",
    board: list[str] | None = None,
    hero_stack: int | None = 3000,
    opponent_stacks: list[int | None] | None = None,
) -> GameState:
    """Create a GameState with configurable active opponents.

    Args:
        phase: Game phase.
        board: Board cards.
        hero_stack: Hero current stack.
        opponent_stacks: Current stacks for active opponents from seat 2 upward.

    Returns:
        Configured GameState.
    """
    players = GameState.create_default_players()
    active_opponent_stacks = [5000] if opponent_stacks is None else opponent_stacks
    for index, stack in enumerate(active_opponent_stacks, start=2):
        players[str(index)] = PlayerState(
            stack=stack,
            is_seated=True,
            in_current_hand=True,
        )

    return GameState(
        phase=phase,
        hero=HeroState(stack=hero_stack),
        board=board or ["8c", "7d", "8d"],
        board_card_count=len(board or ["8c", "7d", "8d"]),
        pot=1200,
        players=players,
        active_player_count=1 + len(active_opponent_stacks),
    )


def test_can_use_solver_heads_up_flop() -> None:
    """Flop heads-up states can use the solver."""
    assert make_builder().can_use_solver(make_state(phase="flop"))


def test_can_use_solver_multiway() -> None:
    """Multiway postflop states cannot use the solver."""
    state = make_state(phase="flop", opponent_stacks=[5000, 4000])

    assert not make_builder().can_use_solver(state)


def test_can_use_solver_preflop() -> None:
    """Preflop states cannot use the postflop solver."""
    assert not make_builder().can_use_solver(make_state(phase="preflop"))


def test_can_use_solver_heads_up_turn() -> None:
    """Turn heads-up states can use the solver."""
    state = make_state(phase="turn", board=["8c", "7d", "8d", "Ah"])

    assert make_builder().can_use_solver(state)


def test_can_use_solver_heads_up_river() -> None:
    """River heads-up states can use the solver."""
    state = make_state(phase="river", board=["8c", "7d", "8d", "Ah", "2s"])

    assert make_builder().can_use_solver(state)


def test_effective_stack_hero_shorter() -> None:
    """Effective stack is hero stack when hero is shorter."""
    state = make_state(hero_stack=3000, opponent_stacks=[5000])

    assert make_builder().compute_effective_stack(state) == 3000


def test_effective_stack_opponent_shorter() -> None:
    """Effective stack is opponent stack when opponent is shorter."""
    state = make_state(hero_stack=5000, opponent_stacks=[2000])

    assert make_builder().compute_effective_stack(state) == 2000


def test_effective_stack_multiway_returns_none() -> None:
    """Effective stack is None for multiway states."""
    state = make_state(hero_stack=5000, opponent_stacks=[2000, 3000])

    assert make_builder().compute_effective_stack(state) is None


def test_effective_stack_no_opponents() -> None:
    """Effective stack is None when no opponent is active."""
    state = make_state(hero_stack=5000, opponent_stacks=[])

    assert make_builder().compute_effective_stack(state) is None


def test_build_request_flop() -> None:
    """Flop request contains every solver schema field."""
    builder = make_builder()
    state = make_state(
        phase="flop",
        board=["8c", "7d", "8d"],
        hero_stack=3000,
        opponent_stacks=[5000],
    )

    request = builder.build_request(state, "66+,A8s+,AJo+", "55+,KTs+", False)

    assert request == {
        "board": "8c7d8d",
        "turn": None,
        "river": None,
        "range_oop": "66+,A8s+,AJo+",
        "range_ip": "55+,KTs+",
        "starting_pot": 1200,
        "effective_stack": 3000,
        "flop_bet_sizes_oop": "60%,a",
        "flop_bet_sizes_ip": "60%,a",
        "flop_raise_sizes_oop": "2.5x",
        "flop_raise_sizes_ip": "2.5x",
        "turn_bet_sizes_oop": "60%,a",
        "turn_bet_sizes_ip": "60%,a",
        "turn_raise_sizes_oop": "2.5x",
        "turn_raise_sizes_ip": "2.5x",
        "river_bet_sizes_oop": "60%,a",
        "river_bet_sizes_ip": "60%,a",
        "river_raise_sizes_oop": "2.5x",
        "river_raise_sizes_ip": "2.5x",
        "rake_rate": 0.0,
        "rake_cap": 0.0,
        "add_allin_threshold": 1.5,
        "force_allin_threshold": 0.15,
        "merging_threshold": 0.1,
        "max_iterations": 200,
        "target_exploitability_pct": 0.5,
        "timeout_ms": 7000,
        "bunching": None,
        "actions_played": None,
    }


def test_build_request_turn() -> None:
    """Turn request includes the turn card and no river card."""
    state = make_state(phase="turn", board=["8c", "7d", "8d", "Ah"])

    request = make_builder().build_request(state, "AA", "KK", True)

    assert request is not None
    assert request["board"] == "8c7d8d"
    assert request["turn"] == "Ah"
    assert request["river"] is None


def test_build_request_river() -> None:
    """River request includes both turn and river cards."""
    state = make_state(phase="river", board=["8c", "7d", "8d", "Ah", "2s"])

    request = make_builder().build_request(state, "AA", "KK", True)

    assert request is not None
    assert request["board"] == "8c7d8d"
    assert request["turn"] == "Ah"
    assert request["river"] == "2s"


def test_build_request_multiway_returns_none() -> None:
    """build_request() returns None for multiway states."""
    state = make_state(opponent_stacks=[5000, 3000])

    assert make_builder().build_request(state, "AA", "KK", False) is None


def test_build_request_preflop_returns_none() -> None:
    """build_request() returns None for preflop states."""
    state = make_state(phase="preflop")

    assert make_builder().build_request(state, "AA", "KK", False) is None


def test_build_request_with_actions_played() -> None:
    """actions_played is passed through to the solver request."""
    request = make_builder().build_request(
        make_state(),
        "AA",
        "KK",
        False,
        actions_played=["Bet 200"],
    )

    assert request is not None
    assert request["actions_played"] == ["Bet 200"]


def test_build_request_with_street_start_pot() -> None:
    """street_start_pot overrides GameState.pot in the request."""
    request = make_builder().build_request(
        make_state(),
        "AA",
        "KK",
        False,
        street_start_pot=500,
    )

    assert request is not None
    assert request["starting_pot"] == 500


def test_build_request_with_street_start_effective_stack() -> None:
    """street_start_effective_stack overrides computed effective stack."""
    request = make_builder().build_request(
        make_state(),
        "AA",
        "KK",
        False,
        street_start_effective_stack=3000,
    )

    assert request is not None
    assert request["effective_stack"] == 3000


def test_build_request_without_new_params() -> None:
    """Omitting new params preserves existing pot and effective stack behavior."""
    state = make_state(hero_stack=3500, opponent_stacks=[4500])
    request = make_builder().build_request(state, "AA", "KK", False)

    assert request is not None
    assert request["starting_pot"] == state.pot
    assert request["effective_stack"] == 3500


def test_build_request_actions_played_none() -> None:
    """actions_played defaults to None for legacy solver requests."""
    request = make_builder().build_request(
        make_state(),
        "AA",
        "KK",
        False,
        actions_played=None,
    )

    assert request is not None
    assert request["actions_played"] is None


def test_board_to_flop_str() -> None:
    """_board_to_flop_str() concatenates the first three board cards."""
    assert SolverRequestBuilder._board_to_flop_str(["8c", "7d", "8d"]) == "8c7d8d"


def test_flop_deep_spr_extends_timeout() -> None:
    """Flop with deep SPR (>10) extends timeout and max_iterations."""
    builder = make_builder()
    state = make_state(
        phase="flop",
        hero_stack=10000,
        opponent_stacks=[10000],
    )
    state.pot = 500

    request = builder.build_request(state, "AA", "KK", False)

    assert request is not None
    assert request["timeout_ms"] == 20000
    assert request["max_iterations"] == 300


def test_is_deep_spr_for_flop_and_turn_only() -> None:
    """Deep-SPR predicate is explicit and limited to flop/turn."""
    builder = make_builder()

    assert builder.is_deep_spr("flop", 500, 6000)
    assert builder.is_deep_spr("turn", 500, 6000)
    assert not builder.is_deep_spr("river", 500, 6000)
    assert not builder.is_deep_spr("flop", 0, 6000)


def test_flop_shallow_spr_keeps_default_timeout() -> None:
    """Flop with shallow SPR (<=10) keeps default timeout and max_iterations."""
    builder = make_builder()
    state = make_state(
        phase="flop",
        hero_stack=500,
        opponent_stacks=[500],
    )
    state.pot = 500

    request = builder.build_request(state, "AA", "KK", False)

    assert request is not None
    assert request["timeout_ms"] == 7000
    assert request["max_iterations"] == 200


def test_turn_deep_spr_keeps_default_timeout() -> None:
    """Turn with deep SPR keeps default timeout (only flop applies)."""
    builder = make_builder()
    state = make_state(
        phase="turn",
        board=["8c", "7d", "8d", "Ah"],
        hero_stack=10000,
        opponent_stacks=[10000],
    )
    state.pot = 500

    request = builder.build_request(state, "AA", "KK", False)

    assert request is not None
    assert request["timeout_ms"] == 7000
    assert request["max_iterations"] == 200


def test_deep_spr_light_probe_overrides_solver_profile() -> None:
    """Light probe request uses comparison-only lightweight settings."""
    builder = make_builder()
    state = make_state(
        phase="flop",
        hero_stack=10000,
        opponent_stacks=[10000],
    )
    state.pot = 500

    request = builder.build_request(
        state,
        "AA",
        "KK",
        False,
        profile="deep_spr_light_probe",
    )

    assert request is not None
    assert request["timeout_ms"] == 5000
    assert request["max_iterations"] == 80
    assert request["target_exploitability_pct"] == 1.5
    assert request["flop_bet_sizes_oop"] == "50%"
    assert request["flop_bet_sizes_ip"] == "50%"
    assert request["flop_raise_sizes_oop"] == "2.5x"
    assert request["flop_raise_sizes_ip"] == "2.5x"


def test_light_probe_returns_none_for_non_deep_spr() -> None:
    """Light probe is not built for shallow SPR contexts."""
    builder = make_builder()
    state = make_state(
        phase="flop",
        hero_stack=500,
        opponent_stacks=[500],
    )
    state.pot = 500

    request = builder.build_request(
        state,
        "AA",
        "KK",
        False,
        profile="deep_spr_light_probe",
    )

    assert request is None


def test_turn_deep_spr_light_probe_is_available() -> None:
    """Turn deep-SPR can build a light probe without changing default request."""
    builder = make_builder()
    state = make_state(
        phase="turn",
        board=["8c", "7d", "8d", "Ah"],
        hero_stack=10000,
        opponent_stacks=[10000],
    )
    state.pot = 500

    request = builder.build_request(
        state,
        "AA",
        "KK",
        False,
        profile="deep_spr_light_probe",
    )

    assert request is not None
    assert request["timeout_ms"] == 5000
    assert request["max_iterations"] == 80


def test_default_deep_spr_flop_keeps_allin_bet_size_candidate() -> None:
    """Production deep-SPR flop request keeps the current 60%,a bet sizes."""
    builder = make_builder()
    state = make_state(
        phase="flop",
        hero_stack=10000,
        opponent_stacks=[10000],
    )
    state.pot = 500

    request = builder.build_request(state, "AA", "KK", False)

    assert request is not None
    assert request["flop_bet_sizes_oop"] == "60%,a"
    assert request["flop_bet_sizes_ip"] == "60%,a"


def test_compare_no_allin_request_changes_only_flop_bet_sizes() -> None:
    """Saved-only comparison request removes all-in from flop bet sizes."""
    builder = make_builder()
    state = make_state(
        phase="flop",
        hero_stack=10000,
        opponent_stacks=[10000],
    )
    state.pot = 500
    production = builder.build_request(state, "AA", "KK", False, actions_played=[])
    assert production is not None

    comparison = builder.build_deep_spr_flop_no_allin_comparison_request(
        state,
        production,
    )

    assert comparison is not None
    assert production["flop_bet_sizes_oop"] == "60%,a"
    assert comparison["flop_bet_sizes_oop"] == "60%"
    assert comparison["flop_bet_sizes_ip"] == "60%"
    assert comparison["turn_bet_sizes_oop"] == "60%,a"
    assert comparison["turn_bet_sizes_ip"] == "60%,a"
    assert comparison["river_bet_sizes_oop"] == "60%,a"
    assert comparison["river_bet_sizes_ip"] == "60%,a"


def test_compare_no_allin_request_requires_deep_spr_flop_root() -> None:
    """No-all-in comparison request is limited to deep-SPR flop root spots."""
    builder = make_builder()
    shallow = make_state(
        phase="flop",
        hero_stack=1000,
        opponent_stacks=[1000],
    )
    shallow.pot = 500
    shallow_request = builder.build_request(shallow, "AA", "KK", False)
    assert shallow_request is not None

    assert (
        builder.build_deep_spr_flop_no_allin_comparison_request(
            shallow,
            shallow_request,
        )
        is None
    )

    turn = make_state(
        phase="turn",
        board=["8c", "7d", "8d", "Ah"],
        hero_stack=10000,
        opponent_stacks=[10000],
    )
    turn.pot = 500
    turn_request = builder.build_request(turn, "AA", "KK", False)
    assert turn_request is not None
    assert (
        builder.build_deep_spr_flop_no_allin_comparison_request(
            turn,
            turn_request,
        )
        is None
    )

    flop = make_state(
        phase="flop",
        hero_stack=10000,
        opponent_stacks=[10000],
    )
    flop.pot = 500
    action_request = builder.build_request(
        flop,
        "AA",
        "KK",
        False,
        actions_played=["Bet 300"],
    )
    assert action_request is not None
    assert (
        builder.build_deep_spr_flop_no_allin_comparison_request(
            flop,
            action_request,
        )
        is None
    )


def test_request_unavailable_diagnostic_returns_facing_all_in() -> None:
    """Diagnostics include facing_all_in and key missing-input reason codes."""
    state = make_state(hero_stack=0, opponent_stacks=[5000])
    state.hero.bet = 100
    state.current_street_actions = [
        ActionRecord(seat=2, action="ALL_IN", amount=1000),
    ]

    diagnostics = make_builder().diagnose_request_unavailable(
        state,
        street_start_pot=None,
        street_start_effective_stack=None,
        actions_played=None,
        hero_is_ip=False,
    )

    assert "facing_all_in" in diagnostics["reason_codes"]
    assert "hero_stack_missing_or_zero" in diagnostics["reason_codes"]
    assert "street_start_pot_missing" in diagnostics["reason_codes"]
    assert diagnostics["actions_played"] == []
    assert diagnostics["actions_played_status"] == "empty"
    assert diagnostics["hero_is_ip"] is False


def test_request_unavailable_diagnostic_actions_played_ok() -> None:
    """Diagnostics expose actions_played and status when available."""
    state = make_state()

    diagnostics = make_builder().diagnose_request_unavailable(
        state,
        street_start_pot=500,
        street_start_effective_stack=3000,
        actions_played=["Bet 300"],
        hero_is_ip=True,
    )

    assert diagnostics["actions_played"] == ["Bet 300"]
    assert diagnostics["actions_played_status"] == "ok"
    assert diagnostics["hero_is_ip"] is True
