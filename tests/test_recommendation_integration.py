"""Phase 17 integration tests for recommendations and background calculation."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from core.game_loop import GameLoop
from core.game_state import GameState, HeroState, PlayerState
from core.hand_manager import HandManager
from strategy.recommendation_engine import Recommendation


class NoneCapture:
    """Capture test double that returns no frame."""

    def get_frame(self) -> None:
        """Return no frame."""
        return None

    def is_open(self) -> bool:
        """Return open state."""
        return True

    def release(self) -> None:
        """Release no resources."""
        return None


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
        return ["Ah", "As"]

    def recognize_board_cards(self, _frame: Any) -> list[str]:
        """Return fixed board cards."""
        return ["Td", "7c", "2h"]

    def count_board_cards(self, _frame: Any) -> int:
        """Return fixed board card count."""
        return 3


class FakeNumberRecognizer:
    """Number recognizer test double."""

    def __init__(self, _profile: dict[str, Any], _config: dict[str, Any]) -> None:
        pass

    def recognize_all(self, _frame: Any) -> dict[str, Any]:
        """Return fixed number recognition results."""
        return {
            "pot": 200,
            "hero_stack": 900,
            "hero_bet": 0,
            "player_stacks": {"2": 800, "3": None, "4": None, "5": None, "6": None},
            "player_bets": {"2": 0, "3": None, "4": None, "5": None, "6": None},
        }


class FakeButtonRecognizer:
    """Button recognizer test double."""

    def __init__(self, _profile: dict[str, Any], _config: dict[str, Any]) -> None:
        pass

    def detect_my_turn(self, _frame: Any) -> bool:
        """Return fixed hero turn state."""
        return True

    def classify_buttons(self, _frame: Any) -> dict[str, Any]:
        """Return fixed button state."""
        return {"fold": True, "call_or_check": "check", "raise_or_bet": "bet"}


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
        return {"2": "p2", "3": None, "4": None, "5": None, "6": None}


class FakeActionEstimator:
    """Action estimator test double."""

    def __init__(self, _config: dict[str, Any]) -> None:
        pass

    def estimate(self, _previous: GameState, _current: GameState) -> dict[str, Any]:
        """Return no actions."""
        return {"game_event": None, "actions": []}

    def reset(self) -> None:
        """Reset no state."""
        return None


@pytest.fixture
def workspace_tmp() -> Path:
    """Return a workspace-local temporary directory."""
    path = Path(".test_tmp") / f"recommendation_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _install_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install fake recognizers into core.game_loop."""
    monkeypatch.setattr("core.game_loop.CardRecognizer", FakeCardRecognizer)
    monkeypatch.setattr("core.game_loop.NumberRecognizer", FakeNumberRecognizer)
    monkeypatch.setattr("core.game_loop.ButtonRecognizer", FakeButtonRecognizer)
    monkeypatch.setattr("core.game_loop.DealerRecognizer", FakeDealerRecognizer)
    monkeypatch.setattr("core.game_loop.NameRecognizer", FakeNameRecognizer)
    monkeypatch.setattr("core.game_loop.ActionEstimator", FakeActionEstimator)


def _make_loop(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
    enable_strategy: bool = False,
) -> GameLoop:
    """Create a GameLoop with fake recognition dependencies."""
    _install_fakes(monkeypatch)
    config = {
        "capture": {"polling_interval_sec": 0.0},
        "game": {"blind_sb": 50, "blind_bb": 100},
        "db": {"path": ":memory:"},
        "replay": {"base_dir": str(workspace_tmp / "replays")},
    }
    manager = HandManager(config, db_path=":memory:")
    manager._players_in_hand = {"1": True, "2": True}
    return GameLoop(
        NoneCapture(),
        config,
        {},
        manager,
        enable_strategy=enable_strategy,
    )


def _make_game_state(
    phase: str = "flop",
    is_my_turn: bool = False,
    hero_position: str = "BTN",
    hero_cards: list[str] | None = None,
    board: list[str] | None = None,
    board_card_count: int = 3,
    active_player_count: int = 2,
    game_event: str | None = None,
    pot: int = 200,
    hero_stack: int = 900,
    hero_bet: int = 0,
) -> GameState:
    """Create a test GameState."""
    players = GameState.create_default_players()
    players["2"] = PlayerState(
        name="p2",
        stack=800,
        bet=0,
        is_seated=True,
        in_current_hand=True,
    )
    if active_player_count >= 3:
        players["3"] = PlayerState(
            name="p3",
            stack=700,
            bet=0,
            is_seated=True,
            in_current_hand=True,
        )

    return GameState(
        phase=phase,
        hero=HeroState(
            position=hero_position,
            cards=hero_cards or ["Ah", "As"],
            stack=hero_stack,
            bet=hero_bet,
            is_my_turn=is_my_turn,
            in_current_hand=True,
        ),
        board=board or ["Td", "7c", "2h"],
        board_card_count=board_card_count,
        pot=pot,
        players=players,
        active_player_count=active_player_count,
        game_event=game_event,
    )


def _make_mock_recommendation_engine(
    recommendation: Recommendation | None = None,
    delay_event: threading.Event | None = None,
    started_event: threading.Event | None = None,
) -> MagicMock:
    """Create a RecommendationEngine mock with optional blocking generate()."""
    recommendation = recommendation or Recommendation(
        action="BET",
        amount=120,
        confidence="high",
        strategy_source="solver",
    )
    engine = MagicMock()
    engine.solver_bridge = None

    def generate(_state: GameState) -> Recommendation:
        if started_event is not None:
            started_event.set()
        if delay_event is not None:
            delay_event.wait(timeout=2.0)
        return recommendation

    engine.generate.side_effect = generate
    return engine


def _cleanup_loop(loop: GameLoop) -> None:
    """Clean up loop resources."""
    loop._hand_manager.close()


class TestRecommendationIntegration:
    """RecommendationEngine + GameLoop integration tests."""

    def test_preflop_immediate_recommendation(
        self,
        workspace_tmp: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Preflop hero turn generates an immediate recommendation."""
        loop = _make_loop(workspace_tmp, monkeypatch)
        try:
            recommendation = Recommendation(
                action="RAISE",
                amount=300,
                confidence="high",
                strategy_source="preflop_chart",
            )
            loop._recommendation_engine = _make_mock_recommendation_engine(
                recommendation,
            )
            loop._hand_manager._phase = "preflop"

            loop._handle_strategy(_make_game_state("preflop", is_my_turn=True))

            assert loop.current_recommendation is recommendation
            assert loop.current_recommendation.strategy_source == "preflop_chart"
        finally:
            _cleanup_loop(loop)

    def test_new_street_does_not_trigger_computation(
        self,
        workspace_tmp: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """NEW_STREET syncs phase but does not generate recommendation."""
        loop = _make_loop(workspace_tmp, monkeypatch)
        try:
            loop._recommendation_engine = _make_mock_recommendation_engine()
            loop._hand_manager._phase = "flop"

            loop._handle_strategy(_make_game_state(game_event="NEW_STREET"))

            loop._recommendation_engine.generate.assert_not_called()
        finally:
            _cleanup_loop(loop)

    def test_postflop_hero_turn_computes_synchronously(
        self,
        workspace_tmp: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Hero turn on postflop computes recommendation synchronously."""
        loop = _make_loop(workspace_tmp, monkeypatch)
        try:
            recommendation = Recommendation(action="BET", amount=120)
            engine = _make_mock_recommendation_engine(recommendation)
            loop._recommendation_engine = engine
            loop._hand_manager._phase = "flop"

            loop._handle_strategy(_make_game_state(is_my_turn=True))

            assert loop.current_recommendation is recommendation
            assert engine.generate.call_count == 1
        finally:
            _cleanup_loop(loop)

    def test_continued_turn_reuses_recommendation(
        self,
        workspace_tmp: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Continued hero turn reuses cached recommendation."""
        loop = _make_loop(workspace_tmp, monkeypatch)
        try:
            recommendation = Recommendation(action="CHECK", amount=0)
            engine = _make_mock_recommendation_engine(recommendation)
            engine.apply_action_constraints.return_value = recommendation
            loop._recommendation_engine = engine
            loop._hand_manager._phase = "flop"

            loop._handle_strategy(_make_game_state(is_my_turn=True))
            loop._handle_strategy(_make_game_state(is_my_turn=True))

            assert loop.current_recommendation is recommendation
            assert engine.generate.call_count == 1
        finally:
            _cleanup_loop(loop)

    def test_recommendation_cleared_after_turn_ends(
        self,
        workspace_tmp: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Pending recommendation clears when hero turn ends."""
        loop = _make_loop(workspace_tmp, monkeypatch)
        try:
            loop._recommendation_engine = _make_mock_recommendation_engine(
                Recommendation(action="CHECK"),
            )
            loop._hand_manager._phase = "flop"

            loop._handle_strategy(_make_game_state(is_my_turn=True))
            assert loop.current_recommendation is not None
            loop._handle_strategy(_make_game_state(is_my_turn=False))

            assert loop.current_recommendation is None
        finally:
            _cleanup_loop(loop)

    def test_headsup_vs_multiway_routing(
        self,
        workspace_tmp: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """RecommendationEngine routes heads-up and multiway by player count."""
        loop = _make_loop(workspace_tmp, monkeypatch)
        try:
            engine = MagicMock()
            engine.solver_bridge = None

            def generate(state: GameState) -> Recommendation:
                if state.active_player_count == 2:
                    return Recommendation(
                        action="BET",
                        confidence="high",
                        strategy_source="solver",
                    )
                return Recommendation(
                    action="CHECK",
                    confidence="medium",
                    strategy_source="llm_multiway",
                )

            engine.generate.side_effect = generate
            loop._recommendation_engine = engine
            loop._hand_manager._phase = "flop"

            loop._handle_strategy(_make_game_state(active_player_count=2, is_my_turn=True))
            heads_up = loop.current_recommendation
            loop._previous_recommendation = None
            loop._last_strategy_is_my_turn = False
            loop._handle_strategy(_make_game_state(active_player_count=3, is_my_turn=True))
            multiway = loop.current_recommendation

            assert heads_up is not None
            assert heads_up.confidence == "high"
            assert multiway is not None
            assert multiway.confidence == "medium"
        finally:
            _cleanup_loop(loop)

    def test_stop_cleanup(
        self,
        workspace_tmp: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """stop() stops the solver bridge cleanly."""
        loop = _make_loop(workspace_tmp, monkeypatch)
        solver_bridge = MagicMock()
        try:
            engine = _make_mock_recommendation_engine()
            engine.solver_bridge = solver_bridge
            loop._recommendation_engine = engine

            loop.stop()

            solver_bridge.stop.assert_called_once()
        finally:
            _cleanup_loop(loop)

    def test_no_strategy_during_hand_end_or_waiting(
        self,
        workspace_tmp: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Strategy generation is skipped during waiting and hand_end."""
        loop = _make_loop(workspace_tmp, monkeypatch)
        try:
            engine = _make_mock_recommendation_engine()
            loop._recommendation_engine = engine

            loop._hand_manager._phase = "waiting"
            loop._handle_strategy(_make_game_state(phase="waiting", is_my_turn=True))
            loop._hand_manager._phase = "hand_end"
            loop._handle_strategy(_make_game_state(phase="hand_end", is_my_turn=True))

            engine.generate.assert_not_called()
            assert loop.current_recommendation is None
        finally:
            _cleanup_loop(loop)

    def test_strategy_modules_init_failure_graceful(
        self,
        workspace_tmp: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Strategy init failures do not prevent GameLoop frame processing."""
        _install_fakes(monkeypatch)
        config = {
            "capture": {"polling_interval_sec": 0.0},
            "game": {"blind_sb": 50, "blind_bb": 100},
            "db": {"path": ":memory:"},
            "replay": {"base_dir": str(workspace_tmp / "replays")},
        }
        manager = HandManager(config, db_path=":memory:")
        fake_frame = object()
        capture = MagicMock()
        capture.get_frame.return_value = fake_frame

        with (
            patch("strategy.preflop_chart.PreflopChart", side_effect=RuntimeError),
            patch("solver.solver_bridge.PostflopSolverBridge", side_effect=RuntimeError),
            patch(
                "strategy.solver_request_builder.SolverRequestBuilder",
                side_effect=RuntimeError,
            ),
            patch("strategy.llm_pipeline.LLMPipeline", side_effect=RuntimeError),
            patch("strategy.multiway_engine.MultiwayEngine", side_effect=RuntimeError),
        ):
            loop = GameLoop(capture, config, {}, manager, enable_strategy=True)

        try:
            state = loop.process_one_frame()

            assert state is not None
            assert state.hero.cards == ["Ah", "As"]
            assert loop._recommendation_engine is not None
            assert loop._recommendation_engine.preflop_chart is None
            assert loop._recommendation_engine.solver_bridge is None
        finally:
            loop.stop()
            manager.close()
