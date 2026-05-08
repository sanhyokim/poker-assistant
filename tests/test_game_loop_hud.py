"""Tests for GameLoop HUD callback integration."""

import logging
import shutil
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from core.game_loop import GameLoop
from core.game_state import ButtonState, GameState, create_empty_game_state
from core.hand_manager import HandManager
from strategy.recommendation_engine import Recommendation


@pytest.fixture
def workspace_tmp() -> Path:
    """Return a workspace-local temporary directory."""
    path = Path(".test_tmp") / f"game_loop_hud_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


class NoneCapture:
    """Capture test double returning no frames."""

    def get_frame(self) -> None:
        """Return no frame."""
        return None

    def is_open(self) -> bool:
        """Return open state."""
        return True

    def release(self) -> None:
        """Release no resources."""
        return None

    def reconnect(self) -> bool:
        """Report failed reconnection."""
        return False


class FakeCardRecognizer:
    """Card recognizer test double."""

    def __init__(self, _profile: dict[str, Any], _config: dict[str, Any]) -> None:
        pass


class FakeNumberRecognizer:
    """Number recognizer test double."""

    def __init__(self, _profile: dict[str, Any], _config: dict[str, Any]) -> None:
        pass


class FakeButtonRecognizer:
    """Button recognizer test double."""

    def __init__(self, _profile: dict[str, Any], _config: dict[str, Any]) -> None:
        pass


class FakeDealerRecognizer:
    """Dealer recognizer test double."""

    def __init__(self, _profile: dict[str, Any], _config: dict[str, Any]) -> None:
        pass


class FakeNameRecognizer:
    """Name recognizer test double."""

    def __init__(self, _profile: dict[str, Any], _config: dict[str, Any]) -> None:
        pass


class FakeActionEstimator:
    """Action estimator test double."""

    def __init__(self, _config: dict[str, Any]) -> None:
        pass


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
    hud_callback: Any = None,
    hud_computing_callback: Any = None,
) -> GameLoop:
    """Create a GameLoop with HUD callback test doubles."""
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
        enable_strategy=False,
        hud_callback=hud_callback,
        hud_computing_callback=hud_computing_callback,
    )


def _recommendation(action: str = "BET") -> Recommendation:
    """Return a test recommendation."""
    return Recommendation(action=action, amount=120, strategy_source="solver")


def _state(
    phase: str = "flop",
    is_my_turn: bool = False,
    game_event: str | None = None,
) -> GameState:
    """Return a strategy-ready GameState."""
    state = create_empty_game_state()
    state.phase = phase
    state.hero.is_my_turn = is_my_turn
    state.game_event = game_event
    state.active_player_count = 2
    return state


def test_hud_callback_called_for_preflop_recommendation(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preflop recommendation generation notifies the HUD."""
    hud_callback = MagicMock()
    loop = _make_loop(workspace_tmp, monkeypatch, hud_callback=hud_callback)
    recommendation = _recommendation("RAISE")
    loop._recommendation_engine = MagicMock()
    loop._recommendation_engine.generate.return_value = recommendation
    loop._hand_manager._phase = "preflop"

    loop._handle_strategy(_state(phase="preflop", is_my_turn=True))

    hud_callback.assert_called_once_with(recommendation)


def test_hud_computing_callback_called_on_postflop_hero_turn(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Postflop hero turn notifies computing state before calculation."""
    hud_computing_callback = MagicMock()
    loop = _make_loop(
        workspace_tmp,
        monkeypatch,
        hud_computing_callback=hud_computing_callback,
    )
    loop._recommendation_engine = MagicMock()
    loop._recommendation_engine.generate.return_value = _recommendation()
    loop._hand_manager._phase = "flop"

    loop._handle_strategy(_state(is_my_turn=True))

    hud_computing_callback.assert_called_once()


def test_hud_notified_when_continued_turn_uses_cached_recommendation(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cached recommendation is sent on continued hero turn."""
    hud_callback = MagicMock()
    loop = _make_loop(workspace_tmp, monkeypatch, hud_callback=hud_callback)
    recommendation = _recommendation()
    loop._recommendation_engine = MagicMock()
    loop._recommendation_engine.apply_action_constraints.return_value = recommendation
    loop._previous_recommendation = recommendation
    loop._last_strategy_is_my_turn = True
    loop._hand_manager._phase = "flop"

    loop._handle_strategy(_state(is_my_turn=True))

    hud_callback.assert_called_once_with(recommendation)
    loop._recommendation_engine.generate.assert_not_called()


def test_postflop_recommendation_generated_once_per_turn(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Postflop recommendation is generated once, then reused on continued turn."""
    loop = _make_loop(workspace_tmp, monkeypatch)
    recommendation = _recommendation("FOLD")
    loop._recommendation_engine = MagicMock()
    loop._recommendation_engine.generate.return_value = recommendation
    loop._recommendation_engine.apply_action_constraints.return_value = recommendation
    loop._hand_manager._phase = "flop"

    loop._handle_strategy(_state(is_my_turn=True))
    loop._handle_strategy(_state(is_my_turn=True))

    assert loop._recommendation_engine.generate.call_count == 1
    assert loop.current_recommendation is recommendation


def test_recommendation_log_formats_raise_multiplier() -> None:
    """Recommendation logs prefer raise multiplier labels for RAISE."""
    recommendation = Recommendation(
        action="RAISE",
        amount=300,
        amount_bb=3.0,
        strategy_source="preflop_chart",
        raise_multiplier=3.0,
        raise_multiplier_label="3.0X",
    )

    assert (
        GameLoop._format_recommendation_log(recommendation)
        == "RAISE 300 (3.0BB) [3.0X] (source=preflop_chart)"
    )


def test_recommendation_log_formats_bet_pot_hint() -> None:
    """Recommendation logs keep pot-size labels for BET."""
    recommendation = Recommendation(
        action="BET",
        amount=825,
        amount_bb=8.2,
        strategy_source="solver",
        pot_percentage=33.0,
        preset_hint="33%",
    )

    assert (
        GameLoop._format_recommendation_log(recommendation)
        == "BET 825 (8.2BB) [33%pot] (source=solver)"
    )


def test_preflop_cached_fold_constraints_reapplied_when_check_available(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A cached preflop FOLD is constrained again when the check button appears."""
    hud_callback = MagicMock()
    loop = _make_loop(workspace_tmp, monkeypatch, hud_callback=hud_callback)
    check_recommendation = _recommendation("CHECK")
    loop._recommendation_engine = MagicMock()
    loop._recommendation_engine.apply_action_constraints.return_value = (
        check_recommendation
    )
    fold_recommendation = _recommendation("FOLD")
    loop._previous_recommendation = fold_recommendation
    loop._last_strategy_is_my_turn = True
    loop._last_strategy_phase = "preflop"
    loop._hand_manager._phase = "preflop"
    state = _state(phase="preflop", is_my_turn=True)
    state.buttons = ButtonState(call_or_check="check")

    with caplog.at_level(logging.INFO, logger="core.game_loop"):
        loop._handle_strategy(state)

    loop._recommendation_engine.generate.assert_not_called()
    loop._recommendation_engine.apply_action_constraints.assert_called_once_with(
        fold_recommendation,
        state,
    )
    hud_callback.assert_called_once_with(check_recommendation)
    assert (
        "Cached recommendation updated by button constraints: FOLD -> CHECK"
        in caplog.text
    )


def test_recommendation_change_is_logged(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Action changes within the same hand are logged."""
    loop = _make_loop(workspace_tmp, monkeypatch)
    loop._previous_recommendation = _recommendation("FOLD")

    with caplog.at_level(logging.INFO, logger="core.game_loop"):
        loop._log_recommendation_change(_recommendation("RAISE"))

    assert (
        "Recommendation changed: FOLD -> RAISE "
        "(opponent action changed the scenario)"
    ) in caplog.text


def test_previous_recommendation_resets_on_preflop_transition(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Entering preflop clears previous recommendation change tracking."""
    hud_callback = MagicMock()
    loop = _make_loop(workspace_tmp, monkeypatch, hud_callback=hud_callback)
    loop._previous_recommendation = _recommendation("FOLD")
    loop._last_strategy_phase = "waiting"
    loop._recommendation_engine = MagicMock()
    loop._recommendation_engine.generate.return_value = _recommendation("RAISE")
    loop._hand_manager._phase = "preflop"

    with caplog.at_level(logging.INFO, logger="core.game_loop"):
        loop._handle_strategy(_state(phase="preflop", is_my_turn=True))

    assert "Recommendation changed" not in caplog.text


def test_hud_notified_for_synchronous_postflop_fallback(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Synchronous postflop recommendation generation notifies the HUD."""
    hud_callback = MagicMock()
    loop = _make_loop(workspace_tmp, monkeypatch, hud_callback=hud_callback)
    recommendation = _recommendation("CHECK")
    loop._recommendation_engine = MagicMock()
    loop._recommendation_engine.generate.return_value = recommendation
    loop._hand_manager._phase = "flop"

    loop._handle_strategy(_state(is_my_turn=True))

    hud_callback.assert_called_once_with(recommendation)


def test_hud_callback_none_on_hand_end(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """hand_end phase clears the HUD to waiting state."""
    hud_callback = MagicMock()
    loop = _make_loop(workspace_tmp, monkeypatch, hud_callback=hud_callback)
    loop._recommendation_engine = MagicMock()
    loop._previous_recommendation = _recommendation()
    loop._hand_manager._phase = "hand_end"

    loop._handle_strategy(_state(phase="river"))

    hud_callback.assert_called_once_with(None)
    assert loop.current_recommendation is None


def test_missing_hud_callbacks_are_safe(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default None HUD callbacks do not raise."""
    loop = _make_loop(workspace_tmp, monkeypatch)

    loop._notify_hud(_recommendation())
    loop._notify_hud_computing()


def test_hud_callback_exception_is_logged(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """HUD callback exceptions are logged without escaping."""
    hud_callback = MagicMock(side_effect=RuntimeError("boom"))
    loop = _make_loop(workspace_tmp, monkeypatch, hud_callback=hud_callback)

    with caplog.at_level(logging.WARNING):
        loop._notify_hud(_recommendation())

    assert "HUD callback failed" in caplog.text


def test_hud_computing_callback_exception_is_logged(
    workspace_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """HUD computing callback exceptions are logged without escaping."""
    computing_callback = MagicMock(side_effect=RuntimeError("boom"))
    loop = _make_loop(
        workspace_tmp,
        monkeypatch,
        hud_computing_callback=computing_callback,
    )

    with caplog.at_level(logging.WARNING):
        loop._notify_hud_computing()

    assert "HUD computing callback failed" in caplog.text
