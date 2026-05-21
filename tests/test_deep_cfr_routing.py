"""Tests for Deep CFR routing in RecommendationEngine."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from core.game_state import GameState, HeroState, PlayerState
from strategy.recommendation_engine import Recommendation, RecommendationEngine


def _make_game_state(phase: str = "flop", active_player_count: int = 2) -> GameState:
    """Create a minimal GameState for routing tests."""
    players = {
        str(seat): PlayerState(
            stack=5000,
            bet=0,
            is_seated=True,
            in_current_hand=seat <= active_player_count,
        )
        for seat in range(2, 7)
    }
    return GameState(
        phase=phase,
        hand_id=1,
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
        board=["Tc", "7h", "2s"] if phase != "preflop" else [],
        board_card_count=3 if phase != "preflop" else 0,
        pot=500,
        players=players,
        dealer_seat=1,
        active_player_count=active_player_count,
    )


def _make_recommendation(source: str, action: str = "CHECK") -> Recommendation:
    """Create a simple recommendation for mocked routes."""
    return Recommendation(
        action=action,
        amount=0 if action in {"CHECK", "FOLD"} else 100,
        reason=f"{source} route",
        confidence="medium",
        strategy_source=source,
        action_probabilities={action.lower(): 1.0},
    )


def _make_engine(deep_cfr_bridge: object | None = None) -> RecommendationEngine:
    """Create a RecommendationEngine with mocked dependencies."""
    return RecommendationEngine(
        config={"game": {"blind_bb": 100}, "deep_cfr": {"fallback_to_solver": True}},
        preflop_chart=MagicMock(),
        solver_bridge=MagicMock(),
        solver_request_builder=MagicMock(),
        llm_pipeline=None,
        multiway_engine=MagicMock(),
        deep_cfr_bridge=deep_cfr_bridge,
    )


def _make_bridge(
    available: bool = True,
    recommendation: Recommendation | None = None,
    side_effect: Exception | None = None,
) -> MagicMock:
    """Create a mocked Deep CFR bridge."""
    bridge = MagicMock()
    bridge.available = available
    if side_effect is not None:
        bridge.generate_recommendation.side_effect = side_effect
    else:
        bridge.generate_recommendation.return_value = recommendation
    return bridge


def test_postflop_routes_to_deep_cfr_when_available() -> None:
    """Available Deep CFR should be the first postflop route."""
    deep_cfr_rec = _make_recommendation("deep_cfr", action="BET")
    bridge = _make_bridge(recommendation=deep_cfr_rec)
    engine = _make_engine(bridge)

    rec = engine.generate(_make_game_state(active_player_count=2))

    assert rec.strategy_source == "deep_cfr"
    bridge.generate_recommendation.assert_called_once()


def test_postflop_falls_back_to_solver_when_deep_cfr_returns_none() -> None:
    """Deep CFR returning None should fall back to the legacy route."""
    bridge = _make_bridge(recommendation=None)
    engine = _make_engine(bridge)
    legacy_rec = _make_recommendation("solver")

    with patch.object(engine, "_postflop_legacy_route", return_value=legacy_rec) as route:
        rec = engine.generate(_make_game_state(active_player_count=2))

    assert rec.strategy_source == "solver"
    route.assert_called_once()


def test_postflop_falls_back_to_solver_when_deep_cfr_raises() -> None:
    """Deep CFR exceptions should fall back to the legacy route."""
    bridge = _make_bridge(side_effect=RuntimeError("boom"))
    engine = _make_engine(bridge)
    legacy_rec = _make_recommendation("solver")

    with patch.object(engine, "_postflop_legacy_route", return_value=legacy_rec) as route:
        rec = engine.generate(_make_game_state(active_player_count=2))

    assert rec.strategy_source == "solver"
    route.assert_called_once()


def test_postflop_uses_legacy_when_bridge_none() -> None:
    """Missing bridge should use the legacy route."""
    engine = _make_engine(deep_cfr_bridge=None)
    legacy_rec = _make_recommendation("solver")

    with patch.object(engine, "_postflop_legacy_route", return_value=legacy_rec) as route:
        rec = engine.generate(_make_game_state(active_player_count=2))

    assert rec.strategy_source == "solver"
    route.assert_called_once()


def test_postflop_uses_legacy_when_bridge_not_available() -> None:
    """Unavailable bridge should use the legacy route."""
    bridge = _make_bridge(available=False)
    engine = _make_engine(bridge)
    legacy_rec = _make_recommendation("solver")

    with patch.object(engine, "_postflop_legacy_route", return_value=legacy_rec) as route:
        rec = engine.generate(_make_game_state(active_player_count=2))

    assert rec.strategy_source == "solver"
    route.assert_called_once()


def test_preflop_not_affected_by_deep_cfr() -> None:
    """Preflop should not call Deep CFR even when the bridge is available."""
    bridge = _make_bridge(recommendation=_make_recommendation("deep_cfr"))
    engine = _make_engine(bridge)
    preflop_rec = _make_recommendation("preflop_chart")

    with patch.object(engine, "_generate_preflop", return_value=preflop_rec):
        rec = engine.generate(_make_game_state(phase="preflop", active_player_count=2))

    assert rec.strategy_source == "preflop_chart"
    bridge.generate_recommendation.assert_not_called()


def test_deep_cfr_latency_recorded() -> None:
    """Deep CFR success should add a deep_cfr_ms latency entry."""
    bridge = _make_bridge(recommendation=_make_recommendation("deep_cfr", action="BET"))
    engine = _make_engine(bridge)

    rec = engine.generate(_make_game_state(active_player_count=2))

    assert "deep_cfr_ms" in rec.latency_breakdown


def test_postflop_legacy_route_multiway() -> None:
    """Legacy route should call multiway generation for active >= 3."""
    engine = _make_engine()
    multiway_rec = _make_recommendation("llm_multiway")

    with patch.object(
        engine, "_generate_postflop_multiway", return_value=multiway_rec
    ) as multiway:
        rec = engine._postflop_legacy_route(
            _make_game_state(active_player_count=3),
            opponent_stats=None,
        )

    assert rec.strategy_source == "llm_multiway"
    multiway.assert_called_once()


def test_postflop_legacy_route_headsup() -> None:
    """Legacy route should call heads-up solver generation for active == 2."""
    engine = _make_engine()
    solver_rec = _make_recommendation("solver")

    with patch.object(
        engine, "_generate_postflop_headsup", return_value=solver_rec
    ) as headsup:
        rec = engine._postflop_legacy_route(
            _make_game_state(active_player_count=2),
            opponent_stats=None,
        )

    assert rec.strategy_source == "solver"
    headsup.assert_called_once()
