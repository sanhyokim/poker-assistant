"""Tests for Deep CFR fallback routing."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from core.game_state import GameState, HeroState, PlayerState
from strategy.recommendation_engine import Recommendation, RecommendationEngine


def _make_game_state(phase: str, active_player_count: int) -> GameState:
    """Create a minimal postflop GameState."""
    players = {
        str(seat): PlayerState(
            stack=5000,
            bet=0,
            is_seated=True,
            in_current_hand=seat <= active_player_count,
        )
        for seat in range(2, 7)
    }
    board_counts = {"flop": 3, "turn": 4, "river": 5}
    return GameState(
        phase=phase,
        hand_id=42,
        hero=HeroState(
            seat=1,
            position="BTN",
            cards=["Ah", "Kd"],
            cards_visible=True,
            stack=5000,
            bet=0,
            is_my_turn=True,
            in_current_hand=True,
        ),
        board=["Tc", "7h", "2s", "4d", "9c"][: board_counts[phase]],
        board_card_count=board_counts[phase],
        pot=500,
        players=players,
        dealer_seat=1,
        active_player_count=active_player_count,
    )


def _make_recommendation(source: str, action: str = "CHECK") -> Recommendation:
    """Create a simple Recommendation."""
    return Recommendation(
        action=action,
        amount=0 if action in {"CHECK", "FOLD"} else 100,
        reason=f"{source} route",
        confidence="medium",
        strategy_source=source,
        action_probabilities={action.lower(): 1.0},
    )


def _make_bridge_failure() -> MagicMock:
    """Create an available bridge that fails inference by returning None."""
    bridge = MagicMock()
    bridge.available = True
    bridge.generate_recommendation.return_value = None
    return bridge


def _make_engine(
    *,
    llm_available: bool = True,
    multiway_available: bool = True,
) -> RecommendationEngine:
    """Create a RecommendationEngine with mocked dependencies."""
    return RecommendationEngine(
        config={"game": {"blind_bb": 100}, "deep_cfr": {"fallback_to_solver": True}},
        preflop_chart=MagicMock(),
        solver_bridge=MagicMock(),
        solver_request_builder=MagicMock(),
        llm_pipeline=MagicMock() if llm_available else None,
        multiway_engine=MagicMock() if multiway_available else None,
        deep_cfr_bridge=_make_bridge_failure(),
    )


def test_flop_hu_fallback_uses_llm() -> None:
    """Flop HU Deep CFR failure should use LLM, not solver."""
    engine = _make_engine()
    llm_rec = _make_recommendation("llm_multiway")

    with patch.object(
        engine, "_generate_postflop_multiway", return_value=llm_rec
    ) as multiway, patch.object(engine, "_generate_postflop_headsup") as headsup:
        rec = engine.generate(_make_game_state("flop", 2))

    assert rec.strategy_source == "deep_cfr_fallback_llm"
    multiway.assert_called_once()
    headsup.assert_not_called()


def test_flop_multiway_fallback_uses_llm() -> None:
    """Flop multiway Deep CFR failure should use LLM."""
    engine = _make_engine()
    llm_rec = _make_recommendation("llm_multiway")

    with patch.object(
        engine, "_generate_postflop_multiway", return_value=llm_rec
    ) as multiway:
        rec = engine.generate(_make_game_state("flop", 3))

    assert rec.strategy_source == "deep_cfr_fallback_llm"
    multiway.assert_called_once()


def test_flop_fallback_skips_when_llm_unavailable() -> None:
    """Flop fallback should skip when LLM fallback dependencies are missing."""
    engine = _make_engine(multiway_available=False)

    rec = engine.generate(_make_game_state("flop", 2))

    assert rec.strategy_source == "deep_cfr_skip"


def test_flop_fallback_skips_when_llm_raises() -> None:
    """Flop fallback should skip when LLM fallback raises."""
    engine = _make_engine()

    with patch.object(
        engine, "_generate_postflop_multiway", side_effect=RuntimeError("llm failed")
    ):
        rec = engine.generate(_make_game_state("flop", 2))

    assert rec.strategy_source == "deep_cfr_skip"


def test_turn_hu_fallback_uses_solver() -> None:
    """Turn HU Deep CFR failure should use solver."""
    engine = _make_engine()
    solver_rec = _make_recommendation("solver")

    with patch.object(
        engine, "_generate_postflop_headsup", return_value=solver_rec
    ) as headsup:
        rec = engine.generate(_make_game_state("turn", 2))

    assert rec.strategy_source == "solver"
    headsup.assert_called_once()


def test_river_hu_fallback_uses_solver() -> None:
    """River HU Deep CFR failure should use solver."""
    engine = _make_engine()
    solver_rec = _make_recommendation("solver")

    with patch.object(
        engine, "_generate_postflop_headsup", return_value=solver_rec
    ) as headsup:
        rec = engine.generate(_make_game_state("river", 2))

    assert rec.strategy_source == "solver"
    headsup.assert_called_once()


def test_turn_multiway_fallback_uses_llm() -> None:
    """Turn multiway Deep CFR failure should use LLM."""
    engine = _make_engine()
    llm_rec = _make_recommendation("llm_multiway")

    with patch.object(
        engine, "_generate_postflop_multiway", return_value=llm_rec
    ) as multiway:
        rec = engine.generate(_make_game_state("turn", 3))

    assert rec.strategy_source == "deep_cfr_fallback_llm"
    multiway.assert_called_once()


def test_river_multiway_fallback_uses_llm() -> None:
    """River multiway Deep CFR failure should use LLM."""
    engine = _make_engine()
    llm_rec = _make_recommendation("llm_multiway")

    with patch.object(
        engine, "_generate_postflop_multiway", return_value=llm_rec
    ) as multiway:
        rec = engine.generate(_make_game_state("river", 4))

    assert rec.strategy_source == "deep_cfr_fallback_llm"
    multiway.assert_called_once()


def test_turn_multiway_fallback_skips_when_llm_unavailable() -> None:
    """Turn multiway fallback should skip when LLM is unavailable."""
    engine = _make_engine(llm_available=False)

    rec = engine.generate(_make_game_state("turn", 3))

    assert rec.strategy_source == "deep_cfr_skip"


def test_deep_cfr_skip_action_value() -> None:
    """Deep CFR skip should return a non-strategic marker action."""
    engine = _make_engine()

    rec = engine._generate_deep_cfr_skip(_make_game_state("flop", 2), "test")

    assert rec.action == "DEEP_CFR_UNAVAILABLE"
    assert rec.strategy_source == "deep_cfr_skip"
    assert rec.confidence == "low"


def test_fallback_llm_sets_source() -> None:
    """Successful LLM fallback should mark its Deep CFR fallback source."""
    engine = _make_engine()
    llm_rec = _make_recommendation("llm_multiway")

    with patch.object(engine, "_generate_postflop_multiway", return_value=llm_rec):
        rec = engine._deep_cfr_llm_fallback(
            _make_game_state("flop", 2),
            opponent_stats=None,
        )

    assert rec.strategy_source == "deep_cfr_fallback_llm"
